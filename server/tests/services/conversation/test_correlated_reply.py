from __future__ import annotations

from pathlib import Path

from server.services.conversation.log import ConversationLog


class _WorkingMemory:
    def __init__(self) -> None:
        self.entries: list[tuple[str, str, str]] = []

    def append_entry(self, kind: str, content: str, timestamp: str) -> None:
        self.entries.append((kind, content, timestamp))


def test_correlated_reply_is_idempotent_across_log_instances(tmp_path: Path):
    path = tmp_path / "conversation.log"
    first = ConversationLog(path)
    first._working_memory_log = _WorkingMemory()
    second = ConversationLog(path)
    second._working_memory_log = _WorkingMemory()

    assert first.record_reply_once("notification-1", "Exact draft") is True
    assert second.record_reply_once("notification-1", "Exact draft") is False

    assert [message.content for message in second.to_chat_messages()] == ["Exact draft"]
