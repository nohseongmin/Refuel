"""Refuel core - 에이전트 로그 자동탐지/파싱, 5시간 윈도우 계산, 설정/히스토리.

순수 로직(GUI 분리). 외부 의존성 없음(표준 라이브러리만).
로컬 전용: 로그 읽기만 하고 네트워크 호출 없음. ~/.refuel 에 설정·히스토리·로그 저장.
"""
import json
import glob
import os
import sqlite3
import logging
from pathlib import Path
from datetime import datetime, timedelta, timezone, date

# ---------------- 경로 ----------------
CONFIG_DIR = Path.home() / ".refuel"
CONFIG_PATH = CONFIG_DIR / "config.json"
DB_PATH = CONFIG_DIR / "history.db"
LOG_PATH = CONFIG_DIR / "refuel.log"
SESSION_WINDOW = timedelta(hours=5)     # Claude 구독 5시간 롤링 윈도우
BURN_LOOKBACK_MIN = 30                  # 소진 예측: 소모속도 측정 구간(분)
BURN_MIN_ELAPSED_MIN = 5                # 소진 예측: 최소 관측 시간(분) - 미달 시 예측 안 함
SESSION_WINDOW_SEC = int(SESSION_WINDOW.total_seconds())   # 18000 — UI 진행바 계산용(윈도우와 항상 일치)
SECONDS_PER_DAY = 86400
WEEKLY_WINDOW_SEC = 7 * SECONDS_PER_DAY  # 주간 윈도우 길이(초)
SORT_LAST = 10 ** 9                      # 활성 블록 없는 에이전트를 정렬 맨 뒤로 보내는 센티넬

log = logging.getLogger("refuel")


def setup_logging():
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        h = logging.FileHandler(LOG_PATH, encoding="utf-8")
        h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        log.addHandler(h)
        log.setLevel(logging.INFO)
    except Exception:
        pass


# ---------------- 설정 ----------------
DEFAULTS = {
    "warn_ratio": 0.8,
    "reset_soon_min": 30,
    "weekly_reset_dow": 0,
    "weekly_reset_hour": 9,
    "minimize_to_tray": True,
    "autostart": False,
    "accent": "#46e08a",
    "sync_enabled": False,       # 폰 연동(베타): ntfy 릴레이로 상태/알림 전송
    "sync_topic": "",            # 최초 활성화 시 랜덤 생성
    "sync_key": "",              # E2E 암호화 키(hex, QR로만 전달)
    "check_updates": True,       # GitHub 릴리스 새 버전 확인(읽기 전용, 하루 1회)
    "sync_scheduled": {},        # 에이전트별 마지막 예약 블록(중복 예약 방지)
    "sync_server": "https://ntfy.sh",
    "sync_app_url": "https://nohseongmin.github.io/Refuel/",
    "consented": "",             # 동의한 면책조항 버전(빈 값이면 최초 실행 시 동의 요구)
}
CONFIG = dict(DEFAULTS)

# 면책조항 버전 — 내용이 바뀌면 올려서 재동의를 받는다.
DISCLAIMER_VERSION = "1"
DISCLAIMER_TEXT = (
    "Refuel은 비공식 도구이며 Anthropic·OpenAI·Cursor 등 어떤 회사와도 무관합니다.\n\n"
    "• 표시되는 한도·리셋 시각·소진 예측은 로컬 로그를 바탕으로 한 추정치이며, "
    "정확성을 보장하지 않습니다. 참고용으로만 사용하세요.\n\n"
    "• '폰 연동'을 켠 경우에 한해, 토큰 사용량 수치·시각·에이전트 이름이 암호화되어 "
    "중계 서비스(ntfy)를 거쳐 본인 기기로 전송됩니다. 코드·프롬프트·API 키는 전송되지 "
    "않으며, 개발자는 그 데이터에 접근할 수 없습니다. 폰 연동은 기본 꺼져 있고 언제든 끌 수 있습니다.\n\n"
    "• 이 소프트웨어는 '있는 그대로' 제공되며 어떠한 보증도 하지 않습니다. "
    "사용으로 발생하는 결과에 대한 책임은 사용자 본인에게 있습니다.\n\n"
    "• 전체 소스코드는 github.com/nohseongmin/Refuel 에서 확인할 수 있습니다."
)


def has_consented():
    return CONFIG.get("consented") == DISCLAIMER_VERSION


def set_consented():
    CONFIG["consented"] = DISCLAIMER_VERSION
    save_config()


def load_config():
    CONFIG.update(DEFAULTS)
    try:
        if CONFIG_PATH.exists():
            CONFIG.update(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
    except Exception as e:
        log.warning("config 로드 실패: %s", e)
    return CONFIG


def save_config():
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(json.dumps(CONFIG, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        log.warning("config 저장 실패: %s", e)


# ---------------- 히스토리(SQLite) ----------------
def _db():
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH, timeout=3)
    con.execute("CREATE TABLE IF NOT EXISTS daily("
                "agent TEXT, day TEXT, tokens INTEGER, inp INTEGER, out INTEGER, cache INTEGER,"
                "PRIMARY KEY(agent, day))")
    return con


def _persist_daily(agent, rows):
    """rows: {date: (tok, inp, out, cache)}. 같은 날은 더 큰 값으로 갱신(단조 증가)."""
    try:
        con = _db()
        con.executemany(
            "INSERT INTO daily(agent,day,tokens,inp,out,cache) VALUES(?,?,?,?,?,?) "
            "ON CONFLICT(agent,day) DO UPDATE SET tokens=max(tokens,excluded.tokens), "
            "inp=max(inp,excluded.inp), out=max(out,excluded.out), cache=max(cache,excluded.cache)",
            [(agent, d.isoformat(), t, i, o, c) for d, (t, i, o, c) in rows.items()])
        con.commit()
        con.close()
    except Exception as e:
        log.warning("history 저장 실패: %s", e)


def _history_daily(agent):
    try:
        con = _db()
        cur = con.execute("SELECT day, tokens FROM daily WHERE agent=?", (agent,))
        m = {row[0]: row[1] for row in cur.fetchall()}
        con.close()
        return m
    except Exception as e:
        log.warning("history 로드 실패: %s", e)
        return {}


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
    cands = []
    env = os.environ.get("CLAUDE_CONFIG_DIR")
    if env:
        for part in env.split(os.pathsep):
            if part.strip():
                cands.append(Path(part.strip()) / "projects")
    cands += [Path.home() / ".claude" / "projects",
              Path.home() / ".config" / "claude" / "projects"]
    return _existing(cands)


def codex_dirs():
    cands = []
    env = os.environ.get("CODEX_HOME")
    if env:
        cands.append(Path(env) / "sessions")
    cands.append(Path.home() / ".codex" / "sessions")
    return _existing(cands)


# ---------------- 파싱 ----------------
_cache = {}  # path -> ((mtime, size), [events])


def _parse_iso_utc(ts):
    """ISO8601 문자열 → UTC-aware datetime. 파싱 실패/빈값이면 None. tz 없으면 UTC로 간주."""
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


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
                dt = _parse_iso_utc(obj.get("timestamp"))
                if dt is None:
                    continue
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
    except Exception as e:
        log.warning("parse 실패 %s: %s", path, e)
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
                dt = _parse_iso_utc(obj.get("timestamp") or payload.get("timestamp"))
                if dt is None:
                    continue
                inp = usage.get("input_tokens", 0) or 0
                out = usage.get("output_tokens", 0) or 0
                cached = usage.get("cached_input_tokens", 0) or 0
                events.append({
                    "ts": dt, "agent": agent,
                    "inp": inp, "out": out, "cache": cached,
                    "total": inp + out + cached, "id": None,
                })
    except Exception as e:
        log.warning("codex parse 실패 %s: %s", path, e)
    return events


# 에이전트 레지스트리: 새 에이전트는 (dirs, glob, parser) 만 추가하면 자동 발견됨.
AGENTS = {
    "claude-code": {"name": "Claude Code", "dirs": claude_dirs, "glob": "**/*.jsonl",
                    "parser": _parse_claude_file},
    "codex": {"name": "Codex (실험)", "dirs": codex_dirs, "glob": "**/*.jsonl",
              "parser": _parse_codex_file},
}


def _scan():
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
    """5시간 윈도우 블록 분할. 블록 시작 = 첫 메시지 시각(정시 내림 안 함)."""
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


def _weekly_ceiling(agent, dow, today):
    """과거 '완료된 주'들의 총 토큰 최댓값 = 주간 한도 추정(히스토리 기반)."""
    hist = _history_daily(agent)
    if not hist:
        return 0
    buckets = {}
    for day_iso, tok in hist.items():
        try:
            d = date.fromisoformat(day_iso)
        except Exception:
            continue
        ws = d - timedelta(days=(d.weekday() - dow) % 7)
        buckets[ws] = buckets.get(ws, 0) + tok
    cur_ws = today - timedelta(days=(today.weekday() - dow) % 7)
    return max((v for k, v in buckets.items() if k < cur_ws), default=0)


def _agent_breakdown(events, agent, now, now_local, today, wk_reset, wk_next):
    today_tok = week_tok = 0
    daily = {}  # date -> [tok, inp, out, cache]
    for e in events:
        d = e["ts"].astimezone().date()
        if d == today:
            today_tok += e["total"]
        if e["ts"].astimezone() >= wk_reset:
            week_tok += e["total"]
        if 0 <= (today - d).days < 7:
            r = daily.setdefault(d, [0, 0, 0, 0])
            r[0] += e["total"]; r[1] += e["inp"]; r[2] += e["out"]; r[3] += e["cache"]

    if daily:
        _persist_daily(agent, {d: tuple(r) for d, r in daily.items()})
    hist = _history_daily(agent)
    daily_list = []
    for i in range(6, -1, -1):
        d = today - timedelta(days=i)
        daily_list.append((d, max(daily.get(d, [0])[0], hist.get(d.isoformat(), 0))))

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
            remaining = max(0, int((end - now).total_seconds()))
            block = {
                "tokens": last["tokens"], "inp": last["inp"], "out": last["out"],
                "cache": last["cache"], "events": last["events"],
                "start": last["start"].astimezone(),
                "reset_at": end.astimezone(),
                "remaining_sec": remaining,
                "ratio": (last["tokens"] / ceiling) if ceiling else None,
                "rate_min": 0, "eta": None,
            }
            # 소진 예측: 최근 30분 소모 속도로 리셋 전 한도 도달 여부 추정
            lookback = timedelta(minutes=BURN_LOOKBACK_MIN)
            win_start = max(last["start"], now - lookback)
            elapsed_min = (now - win_start).total_seconds() / 60
            if elapsed_min >= BURN_MIN_ELAPSED_MIN:
                recent = sum(e["total"] for e in events
                             if e["ts"] >= win_start and e["ts"] <= now)
                rate = recent / elapsed_min
                block["rate_min"] = int(rate)
                if ceiling and rate > 0 and last["tokens"] < ceiling:
                    eta_sec = (ceiling - last["tokens"]) / rate * 60
                    if eta_sec < remaining:
                        block["eta"] = (now + timedelta(seconds=eta_sec)).astimezone()
    wk_ceiling = _weekly_ceiling(agent, CONFIG["weekly_reset_dow"], today)
    weekly = {
        "tokens": week_tok,
        "reset_at": wk_next,
        "remaining_sec": max(0, int((wk_next - now_local).total_seconds())),
        "ceiling_est": wk_ceiling or None,
        "ratio": (week_tok / wk_ceiling) if wk_ceiling else None,
    }
    return {
        "today_tokens": today_tok,
        "week_tokens": week_tok,
        "daily": daily_list,
        "weekly": weekly,
        "weekly_reset": wk_next,
        "weekly_remaining_sec": weekly["remaining_sec"],
        "ceiling_est": ceiling or None,
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
        bd = _agent_breakdown(evs, aid, now, now_local, today, wk_reset, wk_next)
        bd["id"] = aid
        bd["name"] = AGENTS.get(aid, {}).get("name", aid)
        agents.append(bd)
    agents.sort(key=lambda a: a["block"]["remaining_sec"] if a["block"] else SORT_LAST)

    return {"agents": agents, "total_events": len(events)}
