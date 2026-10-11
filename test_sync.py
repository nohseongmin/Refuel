"""Regression checks for phone status publishing."""
from datetime import datetime, timezone
from unittest.mock import patch

from refuel import core, sync


def test_post_state_publishes_changed_token_count():
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    state = {"agents": [{
        "id": "claude-code", "name": "Claude Code", "today_tokens": 10,
        "week_tokens": 10,
        "block": {"start": start, "reset_at": start, "tokens": 10, "ratio": None},
        "weekly": {}, "daily": [], "streak": None,
    }]}
    sent = []
    with patch.dict(core.CONFIG, {"sync_enabled": True}), \
            patch.dict(sync._last_post, {"ts": 0.0, "sig": None}), \
            patch.object(sync, "_HAVE_AES", True), \
            patch.object(sync, "topic", return_value="test-topic"), \
            patch.object(sync, "_encrypt", side_effect=lambda payload: payload), \
            patch.object(sync, "_fire", side_effect=sent.append):
        sync.post_state(state)
        sync.post_state(state)
        state["agents"][0]["block"]["tokens"] = 11
        state["agents"][0]["today_tokens"] = 11
        sync.post_state(state)

    assert [post["message"]["agents"][0]["block"]["tok"] for post in sent] == [10, 11]


if __name__ == "__main__":
    test_post_state_publishes_changed_token_count()
