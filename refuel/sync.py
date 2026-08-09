"""Refuel phone sync: relays status and alerts to a phone through ntfy.
Opt-in and off by default.

No server of our own is needed. It only POSTs to a secret topic on ntfy.sh, an
open-source push relay. What leaves the machine is token counts, timestamps and
agent names, never code or prompts.

The topic is generated once from secrets, which makes it effectively private.
Status goes to <topic>-s, polled silently by the phone dashboard, and alerts go to
<topic>-a as pushes.

The status payload is end-to-end encrypted with AES-GCM. The key travels only in the
QR fragment, so the relay sees ciphertext alone, and the GCM tag blocks forged status
injection. Alert text stays plaintext so the ntfy app can display it, which is
harmless because it says no more than "refueled". Key and topic can be reissued with
rotate().
"""
import base64
import json
import logging
import secrets
import threading
import urllib.request
from datetime import datetime

from . import core

log = logging.getLogger("refuel")

try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    _HAVE_AES = True
except Exception:
    _HAVE_AES = False

_last_post = {"ts": 0.0, "sig": None}
HEARTBEAT_SEC = 600          # refresh every 10 minutes even without changes, mindful of ntfy.sh daily limits


def enabled():
    return bool(core.CONFIG.get("sync_enabled"))


def server():
    return (core.CONFIG.get("sync_server") or "https://ntfy.sh").rstrip("/")


def topic():
    """Secret topic, generated once and then fixed in config."""
    t = core.CONFIG.get("sync_topic")
    if not t:
        t = "refuel-" + secrets.token_urlsafe(24).replace("_", "").replace("-", "")[:28]
        core.CONFIG["sync_topic"] = t
        core.save_config()
    return t


def key():
    """End-to-end encryption key, 128-bit hex, generated once and then fixed in config."""
    k = core.CONFIG.get("sync_key")
    if not k:
        k = secrets.token_hex(16)
        core.CONFIG["sync_key"] = k
        core.save_config()
    return k


def rotate():
    """Reissues topic and key, invalidating every existing pairing and subscription."""
    core.CONFIG["sync_topic"] = ""
    core.CONFIG["sync_key"] = ""
    core.save_config()
    _last_post.update(ts=0.0, sig=None)
    return topic(), key()


def _encrypt(obj):
    """JSON → 'enc1:' + b64(nonce12 + AESGCM ciphertext)."""
    n = secrets.token_bytes(12)
    ct = AESGCM(bytes.fromhex(key())).encrypt(n, json.dumps(obj).encode("utf-8"), None)
    return "enc1:" + base64.b64encode(n + ct).decode()


def pair_url():
    """Dashboard URL the phone opens from the QR. Topic and key sit in the # fragment,
    which browsers never send to a server."""
    base = core.CONFIG.get("sync_app_url") or "https://nohseongmin.github.io/Refuel/"
    sv = server()
    extra = "" if sv == "https://ntfy.sh" else f"&sv={sv}"
    return f"{base}#t={topic()}&k={key()}{extra}"


def _post_json(payload):
    """ntfy JSON publish, which handles UTF-8 titles and bodies safely."""
    req = urllib.request.Request(
        server(), data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=10) as r:
        r.read()


def _fire(payload):
    def run():
        try:
            _post_json(payload)
        except Exception as e:
            log.warning("sync upload failed: %s", e)
    threading.Thread(target=run, daemon=True).start()


LIVE_TAG = "refuel-live"   # marks a live alert; the phone app shows only these


def post_alert(title, msg):
    """Sends an alert that just happened to the phone push topic, asynchronously.

    The phone polls this topic and shows only messages carrying LIVE_TAG. Pre-scheduled
    pushes from schedule_refill deliberately omit the tag, because the phone already
    schedules reset alerts locally and would otherwise ring twice.
    """
    if not enabled():
        return
    _fire({"topic": topic() + "-a", "title": title, "message": msg,
           "priority": 4, "tags": ["zap", LIVE_TAG]})


# Two resets within this many seconds count as the same window, so a reschedule never
# becomes a duplicate push.
SCHEDULE_TOL = 15 * 60


def schedule_refill(agent_id, name, block_start, reset_at):
    """Pre-schedules the refuel push on ntfy for the reset time, so it arrives with the
    PC off.

    An ntfy schedule cannot be cancelled, so exactly one is sent per window. Anything
    within 15 minutes of a stored reset counts as the same window and is not
    rescheduled, which holds it to one push even when the block start shifts slightly
    between scans or versions.
    """
    if not enabled():
        return
    reset_epoch = int(reset_at.timestamp())
    if reset_epoch - int(datetime.now().timestamp()) < 60:   # imminent resets are left to the live path
        return
    sched = core.CONFIG.get("sync_scheduled") or {}
    prev = sched.get(agent_id)
    if isinstance(prev, str):
        # The older format stored the block start as a string. Treat it as already
        # scheduled and migrate quietly, so nothing goes out twice.
        sched[agent_id] = reset_epoch
        core.CONFIG["sync_scheduled"] = sched
        core.save_config()
        log.info("migrated legacy schedule format (not rescheduling): %s", name)
        return
    if isinstance(prev, (int, float)) and abs(reset_epoch - prev) < SCHEDULE_TOL:
        return   # already scheduled for this window
    sched[agent_id] = reset_epoch
    core.CONFIG["sync_scheduled"] = sched
    core.save_config()
    _fire({"topic": topic() + "-a", "title": f"{name} refueled",
           "message": "Your 5-hour usage limit has reset.",
           "priority": 4, "delay": str(reset_epoch)})
    log.info("refuel push scheduled: %s @ %s", name, reset_at.strftime("%H:%M"))


def _epoch(dt):
    try:
        return int(dt.timestamp())
    except Exception:
        return None


def _compact(state):
    agents = []
    for a in state.get("agents", []):
        b = a.get("block")
        wk = a.get("weekly") or {}
        agents.append({
            "id": a["id"], "name": a["name"],
            "today": a.get("today_tokens", 0), "week": a.get("week_tokens", 0),
            "block": ({"reset": _epoch(b["reset_at"]), "tok": b["tokens"],
                       "ratio": b["ratio"]} if b else None),
            "wk": {"reset": _epoch(wk.get("reset_at")), "ratio": wk.get("ratio")},
            "daily": [[d.isoformat(), v] for d, v in a.get("daily", [])],
            "streak": a.get("streak"),
        })
    # accent travels along so the phone matches the PC theme; it derives the shades itself
    return {"v": 1, "ts": int(datetime.now().timestamp()),
            "accent": core.CONFIG.get("accent"), "agents": agents}


def post_state(state):
    """Publishes status to the silent topic, only on a meaningful change or the heartbeat."""
    if not enabled():
        return
    now = datetime.now().timestamp()
    sig_src = [(a["id"],
                (a["block"]["start"].isoformat() if a["block"] else None),
                int((a["block"]["ratio"] or 0) * 10) if a["block"] else -1)
               for a in state.get("agents", [])]
    # Count a colour change as a change too, or the phone theme lags until the next heartbeat.
    sig = json.dumps([core.CONFIG.get("accent"), sig_src], default=str)
    if sig == _last_post["sig"] and (now - _last_post["ts"]) < HEARTBEAT_SEC:
        return
    if not _HAVE_AES:
        log.warning("cryptography module missing - status upload stopped (never sent in plaintext)")
        return
    _last_post.update(ts=now, sig=sig)
    _fire({"topic": topic() + "-s", "message": _encrypt(_compact(state)),
           "priority": 1})
