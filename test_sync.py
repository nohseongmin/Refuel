"""Regression checks for phone status publication and reset alert scheduling."""
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from refuel import sync


def test_post_state_token_changes():
    start = datetime(2026, 1, 1, 10, tzinfo=timezone.utc)
    for has_block, field in ((True, "block"), (True, "today_tokens"),
                             (True, "week_tokens"), (False, "today_tokens"),
                             (False, "week_tokens")):
        agent = {"id": "codex", "name": "Codex", "today_tokens": 100,
                 "week_tokens": 100,
                 "block": {"start": start, "reset_at": start + timedelta(hours=5),
                           "tokens": 100, "ratio": 0.15} if has_block else None}
        state = {"agents": [agent]}
        with patch.dict(sync.core.CONFIG, {"sync_enabled": True, "sync_topic": "test"}), \
                patch.dict(sync._last_post, {"ts": 0.0, "sig": None}), \
                patch.object(sync, "_HAVE_AES", True), \
                patch.object(sync, "datetime") as clock, \
                patch.object(sync, "_encrypt", return_value="encrypted") as encrypt, \
                patch.object(sync, "_fire") as fire:
            clock.now.return_value = start
            sync.post_state(state)
            sync.post_state(state)
            assert fire.call_count == 1

            if field == "block":
                agent["block"]["tokens"] += 1
            else:
                agent[field] += 1
            sync.post_state(state)
            assert fire.call_count == 2, (has_block, field)
            payload = encrypt.call_args.args[0]["agents"][0]
            assert payload["today"] == agent["today_tokens"]
            assert payload["week"] == agent["week_tokens"]
            if has_block:
                assert payload["block"]["tok"] == agent["block"]["tokens"]

            sync.post_state(state)
            assert fire.call_count == 2
            clock.now.return_value = start + timedelta(seconds=sync.HEARTBEAT_SEC)
            sync.post_state(state)
            assert fire.call_count == 3


def test_rotate_reschedules_current_window():
    start = datetime(2026, 1, 1, 10, tzinfo=timezone.utc)
    reset = start + timedelta(hours=5)
    with patch.dict(sync.core.CONFIG, {"sync_enabled": True, "sync_topic": "old-topic",
                                      "sync_key": "old-key", "sync_scheduled": {}}), \
            patch.dict(sync._last_post, {"ts": 0.0, "sig": None}), \
            patch.object(sync.core, "save_config"), \
            patch.object(sync, "datetime") as clock, \
            patch.object(sync.secrets, "token_urlsafe", return_value="rotated"), \
            patch.object(sync.secrets, "token_hex", return_value="0" * 32), \
            patch.object(sync, "_fire") as fire:
        clock.now.return_value = start
        sync.schedule_refill("codex", "Codex", start, reset)
        sync.schedule_refill("codex", "Codex", start, reset)
        assert fire.call_count == 1
        assert fire.call_args.args[0]["topic"] == "old-topic-a"

        sync.rotate()
        assert sync.core.CONFIG["sync_scheduled"] == {}
        sync.schedule_refill("codex", "Codex", start, reset)
        sync.schedule_refill("codex", "Codex", start, reset)
        assert fire.call_count == 2
        assert fire.call_args.args[0]["topic"] == "refuel-rotated-a"
        assert fire.call_args.args[0]["delay"] == str(int(reset.timestamp()))


if __name__ == "__main__":
    test_post_state_token_changes()
    test_rotate_reschedules_current_window()
