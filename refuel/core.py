"""Refuel core - Claude Code 로그 파싱, 5시간 윈도우 계산, 설정 관리.

GUI/패키징과 분리된 순수 로직. 외부 의존성 없음(표준 라이브러리만).
"""
import json
import glob
import os
from pathlib import Path
from datetime import datetime, timedelta, timezone

# ---------------- 경로 ----------------
LOG_DIR = Path.home() / ".claude" / "projects"
CONFIG_DIR = Path.home() / ".refuel"
CONFIG_PATH = CONFIG_DIR / "config.json"

SESSION_WINDOW = timedelta(hours=5)     # Claude 구독 5시간 롤링 윈도우

# ---------------- 설정 ----------------
DEFAULTS = {
    "block_token_limit": None,   # 플랜 한도(토큰). None이면 %게이지/사용량경고 비활성
    "warn_ratio": 0.8,           # 한도 대비 경고 임계
    "reset_soon_min": 30,        # 리셋 임박 알림(분)
    "weekly_reset_dow": 0,       # 주간 리셋 요일 (0=월 ... 6=일)
    "weekly_reset_hour": 9,      # 주간 리셋 시각(시)
    "minimize_to_tray": True,    # 창 닫으면 트레이로
    "autostart": False,          # 윈도우 시작 시 자동 실행
    "accent": "#46e08a",         # 강조 색상
}

CONFIG = dict(DEFAULTS)


def load_config():
    CONFIG.update(DEFAULTS)
    try:
        if CONFIG_PATH.exists():
            CONFIG.update(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
    except Exception:
        pass
    return CONFIG


def save_config():
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(json.dumps(CONFIG, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        print("config 저장 실패:", e)


# ---------------- 로그 파싱 ----------------
_cache = {}  # path -> ((mtime, size), [events])


def _parse_file(path):
    events = []
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                if '"usage"' not in line:
                    continue
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                msg = obj.get("message") or {}
                usage = msg.get("usage")
                if not isinstance(usage, dict):
                    continue
                ts = obj.get("timestamp")
                if not ts:
                    continue
                try:
                    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                except Exception:
                    continue
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                inp = usage.get("input_tokens", 0) or 0
                out = usage.get("output_tokens", 0) or 0
                cc = usage.get("cache_creation_input_tokens", 0) or 0
                cr = usage.get("cache_read_input_tokens", 0) or 0
                events.append({
                    "ts": dt,
                    "inp": inp, "out": out, "cache": cc + cr,
                    "total": inp + out + cc + cr,
                    "id": msg.get("id") or obj.get("uuid"),
                })
    except Exception:
        pass
    return events


def _scan():
    """모든 JSONL 파싱(파일별 mtime 캐시) 후 message.id 로 중복 제거."""
    files = glob.glob(str(LOG_DIR / "**" / "*.jsonl"), recursive=True)
    merged = {}
    for p in files:
        try:
            st = os.stat(p)
        except OSError:
            continue
        key = (st.st_mtime, st.st_size)
        cached = _cache.get(p)
        if cached and cached[0] == key:
            evs = cached[1]
        else:
            evs = _parse_file(p)
            _cache[p] = (key, evs)
        for e in evs:
            merged[e["id"] or id(e)] = e
    return sorted(merged.values(), key=lambda e: e["ts"])


def _compute_blocks(events):
    """5시간 윈도우 블록으로 분할 (gap > 5h 또는 시작+5h 초과 시 새 블록).

    블록 시작 = 첫 메시지 시각 그대로(정시 내림 안 함) → 리셋 = 첫 메시지 +5h.
    """
    blocks, cur = [], None
    for e in events:
        new = cur is None
        if not new and ((e["ts"] - cur["last"] > SESSION_WINDOW) or
                        (e["ts"] >= cur["start"] + SESSION_WINDOW)):
            blocks.append(cur)
            new = True
        if new:
            cur = {"start": e["ts"].replace(microsecond=0), "last": e["ts"],
                   "tokens": 0, "events": 0, "inp": 0, "out": 0, "cache": 0}
        cur["tokens"] += e["total"]
        cur["inp"] += e["inp"]
        cur["out"] += e["out"]
        cur["cache"] += e["cache"]
        cur["events"] += 1
        cur["last"] = e["ts"]
    if cur:
        blocks.append(cur)
    return blocks


def _last_weekly_reset(now_local, dow, hour):
    """now 기준 직전 주간 리셋 시각(로컬, aware)."""
    days_since = (now_local.weekday() - dow) % 7
    cand = (now_local - timedelta(days=days_since)).replace(
        hour=hour % 24, minute=0, second=0, microsecond=0)
    if cand > now_local:
        cand -= timedelta(days=7)
    return cand


def build_state():
    """현재 사용량/윈도우 상태를 dict로 반환. datetime은 로컬 aware 객체."""
    events = _scan()
    now = datetime.now(timezone.utc)
    now_local = now.astimezone()
    today = now_local.date()

    wk_reset = _last_weekly_reset(now_local, CONFIG["weekly_reset_dow"], CONFIG["weekly_reset_hour"])
    wk_next = wk_reset + timedelta(days=7)

    today_tok = week_tok = 0
    daily = {}  # date -> tokens (최근 7일)
    for e in events:
        d = e["ts"].astimezone().date()
        if d == today:
            today_tok += e["total"]
        if e["ts"].astimezone() >= wk_reset:
            week_tok += e["total"]
        if (today - d).days < 7 and d <= today:
            daily[d] = daily.get(d, 0) + e["total"]

    daily_list = []
    for i in range(6, -1, -1):
        d = today - timedelta(days=i)
        daily_list.append((d, daily.get(d, 0)))

    limit = CONFIG["block_token_limit"]
    state = {
        "today_tokens": today_tok,
        "week_tokens": week_tok,
        "daily": daily_list,
        "weekly_reset": wk_next,
        "weekly_remaining_sec": max(0, int((wk_next - now_local).total_seconds())),
        "total_events": len(events),
        "limit": limit,
        "block": None,
    }

    blocks = _compute_blocks(events)
    if blocks:
        b = blocks[-1]
        end = b["start"] + SESSION_WINDOW
        if now < end and (now - b["last"]) < SESSION_WINDOW:
            state["block"] = {
                "tokens": b["tokens"],
                "inp": b["inp"], "out": b["out"], "cache": b["cache"],
                "events": b["events"],
                "start": b["start"].astimezone(),
                "reset_at": end.astimezone(),
                "remaining_sec": max(0, int((end - now).total_seconds())),
                "ratio": (b["tokens"] / limit) if limit else None,
            }
    return state
