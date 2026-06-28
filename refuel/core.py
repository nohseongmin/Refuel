"""Refuel core - Claude Code 로그 파싱 및 5시간 윈도우 사용량 계산.

GUI/패키징과 분리된 순수 로직. 외부 의존성 없음(표준 라이브러리만).
"""
import json
import glob
import os
from pathlib import Path
from datetime import datetime, timedelta, timezone

# ---------------- 설정 ----------------
LOG_DIR = Path.home() / ".claude" / "projects"
SESSION_WINDOW = timedelta(hours=5)     # Claude 구독 5시간 롤링 윈도우
BLOCK_TOKEN_LIMIT = None                # 플랜 한도(토큰). None이면 %게이지/사용량경고 비활성
WARN_RATIO = 0.8                        # 한도 대비 경고 임계
RESET_SOON_MIN = 30                     # 리셋 임박 알림(분)

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
                    "model": msg.get("model", "unknown"),
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
    """5시간 윈도우 블록으로 분할 (gap > 5h 또는 시작+5h 초과 시 새 블록)."""
    blocks, cur = [], None
    for e in events:
        new = cur is None
        if not new and ((e["ts"] - cur["last"] > SESSION_WINDOW) or
                        (e["ts"] >= cur["start"] + SESSION_WINDOW)):
            blocks.append(cur)
            new = True
        if new:
            start = e["ts"].replace(minute=0, second=0, microsecond=0)
            cur = {"start": start, "last": e["ts"], "tokens": 0, "events": 0,
                   "inp": 0, "out": 0, "cache": 0}
        cur["tokens"] += e["total"]
        cur["inp"] += e["inp"]
        cur["out"] += e["out"]
        cur["cache"] += e["cache"]
        cur["events"] += 1
        cur["last"] = e["ts"]
    if cur:
        blocks.append(cur)
    return blocks


def build_state():
    """현재 사용량/윈도우 상태를 dict로 반환. datetime은 객체 그대로 둠(GUI에서 포맷)."""
    events = _scan()
    now = datetime.now(timezone.utc)
    today = datetime.now().astimezone().date()
    week_ago = now - timedelta(days=7)

    today_tok = week_tok = 0
    by_model = {}
    for e in events:
        if e["ts"].astimezone().date() == today:
            today_tok += e["total"]
        if e["ts"] >= week_ago:
            week_tok += e["total"]
        if e["total"]:
            by_model[e["model"]] = by_model.get(e["model"], 0) + e["total"]

    state = {
        "today_tokens": today_tok,
        "week_tokens": week_tok,
        "by_model": by_model,
        "total_events": len(events),
        "limit": BLOCK_TOKEN_LIMIT,
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
                "ratio": (b["tokens"] / BLOCK_TOKEN_LIMIT) if BLOCK_TOKEN_LIMIT else None,
            }
    return state
