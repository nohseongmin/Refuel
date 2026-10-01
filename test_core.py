"""Regression checks for agent log parsing and user-message window anchors."""
import json
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from refuel.core import _compute_blocks, _parse_claude_file, _parse_codex_file


def test_claude_user_message_whitespace():
    messages = [
        {"type": "user", "timestamp": "2026-01-01T10:00:00Z"},
        {"type": "assistant", "timestamp": "2026-01-01T10:05:00Z",
         "message": {"id": "reply", "usage": {"input_tokens": 10, "output_tokens": 5}}},
    ]
    start = datetime(2026, 1, 1, 10, tzinfo=timezone.utc)
    with TemporaryDirectory() as directory:
        path = Path(directory) / "session.jsonl"
        for separators in ((",", ":"), (", ", ": "), (",", " \t:\t ")):
            path.write_text("\n".join(json.dumps(message, separators=separators)
                                      for message in messages), encoding="utf-8")
            events = _parse_claude_file(path, "claude-code")
            assert len(events) == 2, separators
            assert events[0]["ts"] == start, separators
            assert events[0]["total"] == 0, separators
            blocks = _compute_blocks(events)
            assert len(blocks) == 1, separators
            assert blocks[0]["start"] == start, separators
            assert blocks[0]["tokens"] == 15, separators


def test_claude_user_message_containing_usage():
    messages = [
        {"type": "user", "timestamp": "2026-01-01T10:00:00Z",
         "message": {"content": [{"usage": "quoted prompt field"}]}},
        {"type": "assistant", "timestamp": "2026-01-01T10:05:00Z",
         "message": {"id": "reply", "usage": {"input_tokens": 10, "output_tokens": 5}}},
    ]
    start = datetime(2026, 1, 1, 10, tzinfo=timezone.utc)
    with TemporaryDirectory() as directory:
        path = Path(directory) / "session.jsonl"
        path.write_text("\n".join(json.dumps(message) for message in messages), encoding="utf-8")
        events = _parse_claude_file(path, "claude-code")
        assert len(events) == 2
        assert events[0]["ts"] == start
        assert _compute_blocks(events)[0]["start"] == start


def test_claude_invalid_record_structure():
    reply = {"type": "assistant", "timestamp": "2026-01-01T10:05:00Z",
             "message": {"id": "reply", "usage": {"input_tokens": 10, "output_tokens": 5}}}
    invalid_records = [
        ["usage"],
        {"message": ["usage"]},
        {"message": "usage"},
        {"message": 1, "usage": {}},
    ]
    with TemporaryDirectory() as directory:
        path = Path(directory) / "session.jsonl"
        for record in invalid_records:
            path.write_text("\n".join(json.dumps(message) for message in (record, reply)),
                            encoding="utf-8")
            events = _parse_claude_file(path, "claude-code")
            assert len(events) == 1, record
            assert events[0]["id"] == "reply", record
            assert events[0]["total"] == 15, record


def test_malformed_token_counts_do_not_truncate_log():
    claude_messages = [
        {"type": "assistant", "timestamp": "2026-01-01T10:00:00Z",
         "message": {"usage": {"input_tokens": "invalid", "output_tokens": -1,
                                "cache_read_input_tokens": True}}},
        {"type": "assistant", "timestamp": "2026-01-01T10:05:00Z",
         "message": {"id": "valid", "usage": {"input_tokens": 10, "output_tokens": 5}}},
    ]
    codex_messages = [
        {"timestamp": "2026-01-01T10:00:00Z",
         "payload": {"info": {"last_token_usage": {"input_tokens": "invalid"}}}},
        {"timestamp": "2026-01-01T10:05:00Z",
         "payload": {"info": {"last_token_usage": {"output_tokens": 5}}}},
    ]
    with TemporaryDirectory() as directory:
        path = Path(directory) / "session.jsonl"
        path.write_text("\n".join(json.dumps(message) for message in claude_messages),
                        encoding="utf-8")
        claude_events = _parse_claude_file(path, "claude-code")
        assert [event["total"] for event in claude_events] == [0, 15]
        assert claude_events[1]["id"] == "valid"

        path.write_text("\n".join(json.dumps(message) for message in codex_messages),
                        encoding="utf-8")
        codex_events = _parse_codex_file(path, "codex")
        assert [event["total"] for event in codex_events] == [0, 5]


if __name__ == "__main__":
    test_claude_user_message_whitespace()
    test_claude_user_message_containing_usage()
    test_claude_invalid_record_structure()
    test_malformed_token_counts_do_not_truncate_log()
