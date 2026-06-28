"""Refuel core - 에이전트 로그 자동탐지/파싱, 5시간 윈도우 계산, 설정 관리.

GUI/패키징과 분리된 순수 로직. 외부 의존성 없음(표준 라이브러리만).

멀티 에이전트는 '자동 발견' 구조: 각 에이전트의 표준 로그 위치 후보를 앱이 알고 있고,
존재하는 것만 스캔한다. 사용자가 경로를 지정하지 않는다.
"""
import json
import glob
import os
from pathlib import Path
from datetime import datetime, timedelta, timezone

# ---------------- 경로 ----------------
CONFIG_DIR = Path.home() / ".refuel"
CONFIG_PATH = CONFIG_DIR / "config.json"
SESSION_WINDOW = timedelta(hours=5)     # Claude 구독 5시간 롤링 윈도우

# ---------------- 설정 ----------------
DEFAULTS = {
    "warn_ratio": 0.8,           # 추정 한도 대비 경고 임계
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


# ---------------- 에이전트 로그 위치 자동탐지 ----------------
def _existing(paths):
    out, seen = [], set()
    for c in paths:
        try:
            s = str(c)
            if c.exists() and s not in seen:
                seen.add(s)
                out.append(c)
        except OSError:
            pass
    return out


def claude_dirs():
    """Claude Code 로그 디렉터리 후보. CLAUDE_CONFIG_DIR(여러 개 가능) 우선."""
    cands = []
    env = os.environ.get("CLAUDE_CONFIG_DIR")
    if env:
        for part in env.split(os.pathsep):
            if part.strip():
                cands.append(Path(part.strip()) / "projects")
    cands += [
        Path.home() / ".claude" / "projects",
        Path.home() / ".config" / "claude" / "projects",
    ]
    return _existing(cands)


def codex_dirs():
    """Codex CLI 세션 로그 후보 (실험적). CODEX_HOME 우선."""
    cands = []
    env = os.environ.get("CODEX_HOME")
    if env:
        cands.append(Path(env) / "sessions")
    cands.append(Path.home() / ".codex" / "sessions")
    return _existing(cands)


# ---------------- 파싱 ----------------
_cache = {}  # path -> ((mtime, size), [events])


def _parse_claude_file(path, agent):
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
                    "ts": dt, "agent": agent,
                    "inp": inp, "out": out, "cache": cc + cr,
                    "total": inp + out + cc + cr,
                    "id": msg.get("id") or obj.get("uuid"),
                })
    except Exception:
        pass
    return events


def _parse_codex_file(path, agent):
    """Codex CLI rollout JSONL의 last_token_usage(턴별 증분)만 합산. 실험적·미검증."""
    events = []
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                if "token" not in line:
                    continue
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                payload = obj.get("payload") if isinstance(obj.get("payload"), dict) else obj
                info = payload.get("info") if isinstance(payload.get("info"), dict) else None
                usage = info.get("last_token_usage") if isinstance(info, dict) else None
                if not isinstance(usage, dict):
                    continue
                ts = obj.get("timestamp") or payload.get("timestamp")
                if not ts:
                    continue
                try:
                    dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
                except Exception:
                    continue
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                inp = usage.get("input_tokens", 0) or 0
                out = usage.get("output_tokens", 0) or 0
                cached = usage.get("cached_input_tokens", 0) or 0
                events.append({
                    "ts": dt, "agent": agent,
                    "inp": inp, "out": out, "cache": cached,
                    "total": inp + out + cached, "id": None,
                })
    except Exception:
        pass
    return events


# 에이전트 레지스트리: 새 에이전트는 (dirs, glob, parser) 만 추가하면 자동 발견됨.
AGENTS = {
    "claude-code": {"name": "Claude Code", "dirs": claude_dirs, "glob": "**/*.jsonl",
                    "parser": _parse_claude_file},
    "codex": {"name": "Codex (실험)", "dirs": codex_dirs, "glob": "**/*.jsonl",
              "parser": _parse_codex_file},
}


def _scan():
    """등록된 모든 에이전트의 로그를 자동 발견·파싱(파일별 mtime 캐시) 후 id 로 중복 제거."""
    merged = {}
    detected = {}
    for agent_id, spec in AGENTS.items():
        for d in spec["dirs"]():
            for p in glob.glob(str(d / spec["glob"]), recursive=True):
                try:
                    st = os.stat(p)
                except OSError:
                    continue
                key = (st.st_mtime, st.st_size)
                cached = _cache.get(p)
                if cached and cached[0] == key:
                    evs = cached[1]
                else:
                    evs = spec["parser"](p, agent_id)
                    _cache[p] = (key, evs)
                if evs:
                    detected[agent_id] = spec["name"]
                for e in evs:
                    merged[e["id"] or id(e)] = e
    return sorted(merged.values(), key=lambda e: e["ts"]), detected


def _compute_blocks(events):
    """5시간 윈도우 블록으로 분할. 블록 시작 = 첫 메시지 시각(정시 내림 안 함)."""
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
    days_since = (now_local.weekday() - dow) % 7
    cand = (now_local - timedelta(days=days_since)).replace(
        hour=hour % 24, minute=0, second=0, microsecond=0)
    if cand > now_local:
        cand -= timedelta(days=7)
    return cand


def _agent_breakdown(events, now, now_local, today, wk_reset, wk_next):
    """한 에이전트의 이벤트들로 사용량/윈도우/일별/추정한도 계산."""
    today_tok = week_tok = 0
    daily = {}
    for e in events:
        d = e["ts"].astimezone().date()
        if d == today:
            today_tok += e["total"]
        if e["ts"].astimezone() >= wk_reset:
            week_tok += e["total"]
        if 0 <= (today - d).days < 7:
            daily[d] = daily.get(d, 0) + e["total"]
    daily_list = [(today - timedelta(days=i), daily.get(today - timedelta(days=i), 0))
                  for i in range(6, -1, -1)]

    blocks = _compute_blocks(events)
    ceiling = 0
    block = None
    if blocks:
        last = blocks[-1]
        end = last["start"] + SESSION_WINDOW
        is_active = now < end and (now - last["last"]) < SESSION_WINDOW
        completed = blocks[:-1] if is_active else blocks
        ceiling = max((b["tokens"] for b in completed), default=0)
        if is_active:
            block = {
                "tokens": last["tokens"], "inp": last["inp"], "out": last["out"],
                "cache": last["cache"], "events": last["events"],
                "start": last["start"].astimezone(),
                "reset_at": end.astimezone(),
                "remaining_sec": max(0, int((end - now).total_seconds())),
                "ratio": (last["tokens"] / ceiling) if ceiling else None,
            }
    return {
        "today_tokens": today_tok,
        "week_tokens": week_tok,
        "daily": daily_list,
        "weekly_reset": wk_next,
        "weekly_remaining_sec": max(0, int((wk_next - now_local).total_seconds())),
        "ceiling_est": ceiling or None,
        "events_n": len(events),
        "block": block,
    }


def build_state():
    """에이전트별로 분리된 상태를 반환. datetime은 로컬 aware 객체."""
    events, detected = _scan()
    now = datetime.now(timezone.utc)
    now_local = now.astimezone()
    today = now_local.date()
    wk_reset = _last_weekly_reset(now_local, CONFIG["weekly_reset_dow"], CONFIG["weekly_reset_hour"])
    wk_next = wk_reset + timedelta(days=7)

    grouped = {}
    for e in events:
        grouped.setdefault(e["agent"], []).append(e)
    for aid in detected:
        grouped.setdefault(aid, [])

    agents = []
    for aid, evs in grouped.items():
        bd = _agent_breakdown(evs, now, now_local, today, wk_reset, wk_next)
        bd["id"] = aid
        bd["name"] = AGENTS.get(aid, {}).get("name", aid)
        agents.append(bd)

    def urgency(a):
        b = a["block"]
        return b["remaining_sec"] if b else 10 ** 9
    agents.sort(key=urgency)

    return {"agents": agents, "total_events": len(events)}
