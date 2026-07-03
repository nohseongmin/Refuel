"""Refuel phone sync - ntfy 릴레이로 폰에 상태/알림 전송 (선택 기능, 기본 OFF).

- 서버 불필요: ntfy.sh(오픈소스 푸시 릴레이)의 비밀 토픽으로 POST만 한다.
- 나가는 데이터 = 토큰 수·시각·에이전트명뿐 (코드/프롬프트 없음).
- 토픽은 최초 1회 랜덤 생성(secrets) → 사실상 비밀 채널.
  상태: <topic>-s (무음, 폰 대시보드가 폴링) / 알림: <topic>-a (푸시)
"""
import json
import logging
import secrets
import threading
import urllib.request
from datetime import datetime

from . import core

log = logging.getLogger("refuel")

_last_post = {"ts": 0.0, "sig": None}
HEARTBEAT_SEC = 600          # 변화 없어도 10분마다 상태 갱신(ntfy.sh 일일 한도 고려)


def enabled():
    return bool(core.CONFIG.get("sync_enabled"))


def server():
    return (core.CONFIG.get("sync_server") or "https://ntfy.sh").rstrip("/")


def topic():
    """비밀 토픽(최초 1회 생성 후 config에 고정)."""
    t = core.CONFIG.get("sync_topic")
    if not t:
        t = "refuel-" + secrets.token_urlsafe(24).replace("_", "").replace("-", "")[:28]
        core.CONFIG["sync_topic"] = t
        core.save_config()
    return t


def pair_url():
    """폰이 QR로 여는 대시보드 URL."""
    base = core.CONFIG.get("sync_app_url") or "https://nohseongmin.github.io/Refuel/"
    sv = server()
    extra = "" if sv == "https://ntfy.sh" else f"&sv={sv}"
    return f"{base}#t={topic()}{extra}"


def _post_json(payload):
    """ntfy JSON publish (UTF-8 제목/본문 안전)."""
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
            log.warning("sync 전송 실패: %s", e)
    threading.Thread(target=run, daemon=True).start()


def post_alert(title, msg):
    """알림을 폰 푸시 토픽으로 (비동기)."""
    if not enabled():
        return
    _fire({"topic": topic() + "-a", "title": title, "message": msg,
           "priority": 4, "tags": ["zap"]})


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
        })
    return {"v": 1, "ts": int(datetime.now().timestamp()), "agents": agents}


def post_state(state):
    """상태를 무음 토픽으로. 의미 변화 또는 하트비트 주기에만 전송."""
    if not enabled():
        return
    now = datetime.now().timestamp()
    sig_src = [(a["id"],
                (a["block"]["start"].isoformat() if a["block"] else None),
                int((a["block"]["ratio"] or 0) * 10) if a["block"] else -1)
               for a in state.get("agents", [])]
    sig = json.dumps(sig_src, default=str)
    if sig == _last_post["sig"] and (now - _last_post["ts"]) < HEARTBEAT_SEC:
        return
    _last_post.update(ts=now, sig=sig)
    _fire({"topic": topic() + "-s", "message": json.dumps(_compact(state)),
           "priority": 1})
