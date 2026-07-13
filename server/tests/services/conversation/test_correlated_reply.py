from __future__ import annotations

from multiprocessing import get_context
from pathlib import Path

from server.services.conversation.log import ConversationLog


class _WorkingMemory:
    def __init__(self) -> None:
        self.entries: list[tuple[str, str, str]] = []

    def append_entry(self, tag: str, payload: str, timestamp: str) -> None:
        self.entries.append((tag, payload, timestamp))

    def clear(self) -> None:
        self.entries.clear()


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


def test_conversation_entries_round_trip_cause_ids_and_stable_message_ids(tmp_path: Path):
    path = tmp_path / "conversation.log"
    log = ConversationLog(path)
    log._working_memory_log = _WorkingMemory()

    cause_id = 'message<&"-1\ncontinued\r\t'
    log.record_user_message("Draft John's renewal", cause_id=cause_id)
    log.record_reply("The draft is ready", cause_id=cause_id)

    messages = log.to_chat_messages()
    assert [(message.id, message.role, message.content) for message in messages] == [
        (cause_id, "user", "Draft John's renewal"),
        (f"reply:{cause_id}", "assistant", "The draft is ready"),
    ]
    assert [entry.cause_id for entry in log.iter_correlated_entries()] == [cause_id, cause_id]
    assert len(path.read_text(encoding="utf-8").splitlines()) == 2


def test_legacy_conversation_entries_receive_deterministic_unique_ids(tmp_path: Path):
    path = tmp_path / "conversation.log"
    path.write_text(
        '<user_message timestamp="2026-07-13 01:00:00">Hello</user_message>\n'
        '<poke_reply timestamp="2026-07-13 01:00:01">Hi</poke_reply>\n',
        encoding="utf-8",
    )
    log = ConversationLog(path)
    log._working_memory_log = _WorkingMemory()

    messages = log.to_chat_messages()

    assert [message.id for message in messages] == ["legacy:0", "legacy:1"]
    assert [entry.cause_id for entry in log.iter_correlated_entries()] == [None, None]


def test_reply_id_is_bounded_when_cause_uses_its_full_input_budget(tmp_path: Path):
    path = tmp_path / "conversation.log"
    log = ConversationLog(path)
    log._working_memory_log = _WorkingMemory()
    cause_id = "c" * 255
    log.record_user_message("Request", cause_id=cause_id)
    log.record_reply("Reply", cause_id=cause_id)

    user, reply = log.to_chat_messages()

    assert user.id == cause_id
    assert reply.id is not None
    assert reply.id.startswith("message:")
    assert len(reply.id) <= 255


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
