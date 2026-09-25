"""Regression checks for Claude log parsing and user-message window anchors."""
import json
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from refuel.core import _compute_blocks, _parse_claude_file


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


if __name__ == "__main__":
    test_claude_user_message_whitespace()
    test_claude_invalid_record_structure()
