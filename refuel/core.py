"""Refuel core: agent log discovery and parsing, 5-hour window maths, config and history.

Pure logic with no GUI and no third-party dependencies, standard library only.
Local only: it reads logs and makes no network calls. Config, history and logs live
in ~/.refuel.
"""
import json
import glob
import os
import re
import sqlite3
import logging
from collections import namedtuple
from pathlib import Path
from datetime import datetime, timedelta, timezone, date

# ---------------- Paths ----------------
CONFIG_DIR = Path.home() / ".refuel"
CONFIG_PATH = CONFIG_DIR / "config.json"
DB_PATH = CONFIG_DIR / "history.db"
LOG_PATH = CONFIG_DIR / "refuel.log"
SESSION_WINDOW = timedelta(hours=5)     # Claude subscription rolling 5-hour window
SESSION_WINDOW_SEC = int(SESSION_WINDOW.total_seconds())   # 18000, drives the progress bar and always matches the window
WEEKLY_WINDOW_SEC = 7 * 24 * 3600        # length of the weekly window in seconds
GRASS_DAYS = 112                         # 16 weeks of grass, sized to fit the phone card
GRASS_LEVELS = 5                         # shading steps from faint to full accent
SORT_LAST = 10 ** 9                      # sentinel sorting agents without an active block to the end

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


# ---------------- Settings ----------------
DEFAULTS = {
    "warn_ratio": 0.8,
    "reset_soon_min": 30,
    "weekly_reset_dow": 0,
    "weekly_reset_hour": 9,
    "minimize_to_tray": True,
    "autostart": False,
    "accent": "#46e08a",
    "sync_enabled": False,       # phone sync (beta): relays status and alerts through ntfy
    "sync_topic": "",            # generated randomly the first time it is enabled
    "sync_key": "",              # end-to-end encryption key in hex, shared only through the QR
    "check_updates": True,       # checks GitHub releases once a day, read-only
    "sync_scheduled": {},        # last scheduled block per agent, prevents duplicate pushes
    "sync_server": "https://ntfy.sh",
    "sync_app_url": "https://nohseongmin.github.io/Refuel/",
    "consented": "",             # accepted disclaimer version; empty means the dialog appears on first run
}
CONFIG = dict(DEFAULTS)

# Bump this when the disclaimer text changes materially, to ask for agreement again.
DISCLAIMER_VERSION = "1"
DISCLAIMER_TEXT = (
    "Refuel is an unofficial tool and is not affiliated with Anthropic, OpenAI, "
    "Cursor, or any other company.\n\n"
    "• Limits and reset times shown here are estimates derived from your local logs. "
    "They are not guaranteed to be accurate — treat them as a rough guide.\n\n"
    "• Only if you turn on 'Phone sync': token counts, timestamps and agent names are "
    "encrypted and relayed to your own device through ntfy. Your code, prompts and API "
    "keys are never sent, and the developer cannot read that data. Phone sync is off by "
    "default and can be turned off at any time.\n\n"
    "• This software is provided 'as is', without warranty of any kind. You are "
    "responsible for how you use it.\n\n"
    "• Full source code: github.com/nohseongmin/Refuel"
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
        log.warning("config load failed: %s", e)
    return CONFIG


def save_config():
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(json.dumps(CONFIG, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        log.warning("config save failed: %s", e)


# ---------------- History (SQLite) ----------------
def _db():
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH, timeout=3)
    con.execute("CREATE TABLE IF NOT EXISTS daily("
                "agent TEXT, day TEXT, tokens INTEGER, inp INTEGER, out INTEGER, cache INTEGER,"
                "PRIMARY KEY(agent, day))")
    return con


def _persist_daily(agent, rows):
    """rows: {date: (tok, inp, out, cache)}. A repeated day keeps the larger value, so
    totals only ever grow."""
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
        log.warning("history save failed: %s", e)


def _history_daily(agent):
    try:
        con = _db()
        cur = con.execute("SELECT day, tokens FROM daily WHERE agent=?", (agent,))
        m = {row[0]: row[1] for row in cur.fetchall()}
        con.close()
        return m
    except Exception as e:
        log.warning("history load failed: %s", e)
        return {}


# ---------------- Agent log discovery ----------------
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


# ---------------- Parsing ----------------
_cache = {}  # path -> ((mtime, size), [events])


def _parse_iso_utc(ts):
    """ISO 8601 string to a UTC-aware datetime. None if empty or unparseable.
    A missing timezone is treated as UTC."""
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


_TS_RE = re.compile(r'"timestamp"\s*:\s*"([^"]+)"')


def _event(ts, agent, inp=0, out=0, cache=0, eid=None):
    """One event. The rule that total is the sum of the three counts lives only here."""
    return {"ts": ts, "agent": agent, "inp": inp, "out": out, "cache": cache,
            "total": inp + out + cache, "id": eid}


def _parse_claude_file(path, agent):
    events = []
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                if '"usage"' not in line:
                    # The 5-hour window starts the moment you send the message. Anchoring on the
                    # assistant reply pushes the reset estimate later the slower the reply is,
                    # so user messages are recorded as zero-token activity to pin the start.
                    if '"type":"user"' in line:
                        m = _TS_RE.search(line)
                        dt = _parse_iso_utc(m.group(1)) if m else None
                        if dt is not None:
                            events.append(_event(dt, agent))
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
                cache = ((usage.get("cache_creation_input_tokens", 0) or 0) +
                         (usage.get("cache_read_input_tokens", 0) or 0))
                events.append(_event(dt, agent,
                                     usage.get("input_tokens", 0) or 0,
                                     usage.get("output_tokens", 0) or 0,
                                     cache, msg.get("id") or obj.get("uuid")))
    except Exception as e:
        log.warning("parse failed %s: %s", path, e)
    return events


def _parse_codex_file(path, agent):
    """Sums only last_token_usage, the per-turn delta, from Codex CLI rollout JSONL.
    Experimental and unverified."""
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
                events.append(_event(dt, agent,
                                     usage.get("input_tokens", 0) or 0,
                                     usage.get("output_tokens", 0) or 0,
                                     usage.get("cached_input_tokens", 0) or 0))
    except Exception as e:
        log.warning("codex parse failed %s: %s", path, e)
    return events


# Agent registry. A new agent needs only dirs, glob and parser to be discovered.
AGENTS = {
    "claude-code": {"name": "Claude Code", "dirs": claude_dirs, "glob": "**/*.jsonl",
                    "parser": _parse_claude_file},
    "codex": {"name": "Codex", "dirs": codex_dirs, "glob": "**/*.jsonl",
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
    """Splits events into 5-hour window blocks. A block starts at its first message,
    with no rounding to the hour."""
    blocks, cur = [], None
    for e in events:
        new = cur is None
        if not new and ((e["ts"] - cur["last"] > SESSION_WINDOW) or
                        (e["ts"] >= cur["start"] + SESSION_WINDOW)):
            blocks.append(cur)
            new = True
        if new:
            cur = {"start": e["ts"].replace(microsecond=0), "last": e["ts"],
                   "tokens": 0, "inp": 0, "out": 0, "cache": 0}
        cur["tokens"] += e["total"]
        for k in ("inp", "out", "cache"):
            cur[k] += e[k]
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
    """Largest total across past completed weeks, used as the weekly limit estimate."""
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


def _day_level(tokens, full):
    """Shade of one grass square. 0 means unused, 1 to 5 scale with usage.

    The estimated 5-hour limit counts as 100% and is split into five steps. A day that
    goes past the limit is 5. Without an estimate there is nothing to judge against,
    so it stays at 1.
    """
    if tokens <= 0:
        return 0
    if not full:
        return 1
    return min(GRASS_LEVELS, -(-tokens * GRASS_LEVELS // full))   # ceiling division


def _streak(levels):
    """levels runs oldest to today. Returns the current and best streak.

    Today may simply not have started yet, so an empty today counts back from
    yesterday. Otherwise a streak would look broken halfway through the day.
    """
    best = run = 0
    for lv in levels:
        run = run + 1 if lv else 0
        best = max(best, run)
    if levels and not levels[-1]:
        run = 0
        for lv in reversed(levels[:-1]):
            if not lv:
                break
            run += 1
    return run, best


_Clock = namedtuple("_Clock", "utc local today wk_reset wk_next")


def _now_clock():
    """Computes now once so every agent shares the same reference time."""
    utc = datetime.now(timezone.utc)
    local = utc.astimezone()
    wk_reset = _last_weekly_reset(local, CONFIG["weekly_reset_dow"], CONFIG["weekly_reset_hour"])
    return _Clock(utc, local, local.date(), wk_reset, wk_reset + timedelta(days=7))


def _agent_breakdown(events, agent, clock):
    now, today = clock.utc, clock.today
    today_tok = week_tok = 0
    daily = {}  # date -> [tok, inp, out, cache]
    for e in events:
        d = e["ts"].astimezone().date()
        if d == today:
            today_tok += e["total"]
        if e["ts"].astimezone() >= clock.wk_reset:
            week_tok += e["total"]
        if d <= today:      # collect every day in the logs so the grass fills in, excluding future dates
            r = daily.setdefault(d, [0, 0, 0, 0])
            r[0] += e["total"]; r[1] += e["inp"]; r[2] += e["out"]; r[3] += e["cache"]

    if daily:
        _persist_daily(agent, {d: tuple(r) for d, r in daily.items()})
    hist = _history_daily(agent)

    def day_tokens(d):
        """Usage for a day: the larger of this scan and stored history, so the grass
        survives log cleanup."""
        return max(daily.get(d, [0])[0], hist.get(d.isoformat(), 0))

    daily_list = [(today - timedelta(days=i), day_tokens(today - timedelta(days=i)))
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
                "cache": last["cache"],
                "start": last["start"].astimezone(),
                "reset_at": end.astimezone(),
                "remaining_sec": max(0, int((end - now).total_seconds())),
                "ratio": (last["tokens"] / ceiling) if ceiling else None,
            }
    # Grass is GRASS_DAYS of shading ending today, packed one character per day such as
    # "0112...". A string rather than an array of dates cuts the phone payload tenfold.
    grass_from = today - timedelta(days=GRASS_DAYS - 1)
    levels = [_day_level(day_tokens(grass_from + timedelta(days=i)), ceiling)
              for i in range(GRASS_DAYS)]
    cur_streak, best_streak = _streak(levels)

    wk_ceiling = _weekly_ceiling(agent, CONFIG["weekly_reset_dow"], today)
    weekly = {
        "tokens": week_tok,
        "reset_at": clock.wk_next,
        "remaining_sec": max(0, int((clock.wk_next - clock.local).total_seconds())),
        "ceiling_est": wk_ceiling or None,
        "ratio": (week_tok / wk_ceiling) if wk_ceiling else None,
    }
    return {
        "today_tokens": today_tok,
        "week_tokens": week_tok,
        "daily": daily_list,
        "weekly": weekly,
        "block": block,
        "streak": {"current": cur_streak, "best": best_streak,
                   "from": grass_from.isoformat(),
                   "levels": "".join(str(v) for v in levels)},
    }


def build_state():
    """Returns state split per agent. All datetimes are local and timezone-aware."""
    events, detected = _scan()
    clock = _now_clock()

    grouped = {aid: [] for aid in detected}
    for e in events:
        grouped.setdefault(e["agent"], []).append(e)

    agents = []
    for aid, evs in grouped.items():
        bd = _agent_breakdown(evs, aid, clock)
        bd["id"] = aid
        bd["name"] = AGENTS.get(aid, {}).get("name", aid)
        agents.append(bd)
    agents.sort(key=lambda a: a["block"]["remaining_sec"] if a["block"] else SORT_LAST)

    return {"agents": agents, "total_events": len(events)}
