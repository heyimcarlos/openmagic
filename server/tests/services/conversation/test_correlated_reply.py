from __future__ import annotations

from multiprocessing import get_context
from pathlib import Path

from server.services.conversation.log import ConversationLog


class _WorkingMemory:
    def __init__(self) -> None:
        self.entries: list[tuple[str, str, str]] = []

    def append_entry(self, kind: str, content: str, timestamp: str) -> None:
        self.entries.append((kind, content, timestamp))


def _concurrent_reply(path: str, barrier, results) -> None:
    log = ConversationLog(Path(path))
    log._working_memory_log = _WorkingMemory()
    barrier.wait()
    results.put(log.record_reply_once("notification-1", "Exact draft"))


def test_correlated_reply_is_idempotent_across_log_instances(tmp_path: Path):
    path = tmp_path / "conversation.log"
    first = ConversationLog(path)
    first._working_memory_log = _WorkingMemory()
    second = ConversationLog(path)
    second._working_memory_log = _WorkingMemory()

    assert first.record_reply_once("notification-1", "Exact draft") is True
    assert second.record_reply_once("notification-1", "Exact draft") is False

    assert [message.content for message in second.to_chat_messages()] == ["Exact draft"]


def test_correlated_reply_is_atomic_across_processes(tmp_path: Path):
    path = tmp_path / "conversation.log"
    context = get_context("fork")
    barrier = context.Barrier(4)
    results = context.Queue()
    processes = [
        context.Process(target=_concurrent_reply, args=(str(path), barrier, results))
        for _ in range(4)
    ]

    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=5)
        assert process.exitcode == 0

    assert sorted(results.get(timeout=1) for _ in processes) == [False, False, False, True]
    assert [message.content for message in ConversationLog(path).to_chat_messages()] == [
        "Exact draft"
    ]
