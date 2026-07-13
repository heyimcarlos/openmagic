from __future__ import annotations

import hashlib
import re
import threading
from collections.abc import Iterator
from dataclasses import dataclass
from fcntl import LOCK_EX, LOCK_UN, flock
from html import escape, unescape
from pathlib import Path
from typing import Protocol

from ...config import get_settings
from ...logging_config import logger
from ...models import ChatMessage
from ...utils.timezones import now_in_user_timezone

_DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
_CONVERSATION_LOG_PATH = _DATA_DIR / "conversation" / "poke_conversation.log"


class TranscriptFormatter(Protocol):
    def __call__(
        self, tag: str, timestamp: str, payload: str
    ) -> str:  # pragma: no cover - typing protocol
        ...


class WorkingMemoryLog(Protocol):
    def append_entry(self, tag: str, payload: str, timestamp: str) -> None: ...

    def clear(self) -> None: ...


def _encode_payload(payload: str) -> str:
    normalized = payload.replace("\r\n", "\n").replace("\r", "\n")
    collapsed = normalized.replace("\n", "\\n")
    return escape(collapsed, quote=False)


def _decode_payload(payload: str) -> str:
    return unescape(payload).replace("\\n", "\n")


def _encode_attribute(value: str) -> str:
    """Keep one attribute on one transcript line while preserving its exact value."""

    return (
        escape(value, quote=True)
        .replace("\r", "&#13;")
        .replace("\n", "&#10;")
        .replace("\t", "&#9;")
    )


def _bounded_message_id(base_id: str, occurrence: int) -> str:
    candidate = base_id if occurrence == 0 else f"{base_id}:{occurrence + 1}"
    if len(candidate) <= 255:
        return candidate
    digest = hashlib.sha256(candidate.encode()).hexdigest()
    return f"message:{digest}"


def _default_formatter(tag: str, timestamp: str, payload: str) -> str:
    encoded = _encode_payload(payload)
    return f'<{tag} timestamp="{timestamp}">{encoded}</{tag}>\n'


def _resolve_working_memory_log() -> WorkingMemoryLog:
    from .summarization import get_working_memory_log

    return get_working_memory_log()


_ATTR_PATTERN = re.compile(r"(\w+)\s*=\s*\"([^\"]*)\"")


@dataclass(frozen=True)
class ConversationEntry:
    tag: str
    timestamp: str
    payload: str
    cause_id: str | None = None


@dataclass(frozen=True)
class CorrelatedChatMessage:
    message: ChatMessage
    cause_id: str | None


class ConversationLog:
    """Append-only conversation log persisted to disk for the interaction agent."""

    def __init__(
        self,
        path: Path,
        formatter: TranscriptFormatter = _default_formatter,
        working_memory_log: WorkingMemoryLog | None = None,
    ):
        self._path = path
        self._formatter = formatter
        self._lock = threading.Lock()
        self._ensure_directory()
        self._working_memory_log = working_memory_log or _resolve_working_memory_log()

    def _ensure_directory(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("conversation log directory creation failed", extra={"error": str(exc)})

    def _append(self, tag: str, payload: str, *, cause_id: str | None = None) -> str:
        timestamp = now_in_user_timezone("%Y-%m-%d %H:%M:%S")
        assert isinstance(timestamp, str)
        entry = self._formatter(tag, timestamp, str(payload))
        if cause_id is not None:
            open_end = entry.find(">")
            if open_end == -1:
                raise ValueError("conversation formatter must produce an opening tag")
            attribute = f' cause_id="{_encode_attribute(cause_id)}"'
            entry = f"{entry[:open_end]}{attribute}{entry[open_end:]}"
        with self._lock:
            try:
                with self._path.open("a", encoding="utf-8") as handle:
                    handle.write(entry)
            except Exception as exc:  # pragma: no cover - defensive
                logger.error(
                    "conversation log append failed",
                    extra={"error": str(exc), "tag": tag, "path": str(self._path)},
                )
                raise
        self._notify_summarization()
        return timestamp

    def _parse_line(self, line: str) -> ConversationEntry | None:
        stripped = line.strip()
        if not stripped.startswith("<") or "</" not in stripped:
            return None
        open_end = stripped.find(">")
        if open_end == -1:
            return None
        open_tag_content = stripped[1:open_end]
        if " " in open_tag_content:
            tag, attr_string = open_tag_content.split(" ", 1)
        else:
            tag, attr_string = open_tag_content, ""
        close_start = stripped.rfind("</")
        close_end = stripped.rfind(">")
        if close_start == -1 or close_end == -1:
            return None
        closing_tag = stripped[close_start + 2 : close_end]
        if closing_tag != tag:
            return None
        payload = stripped[open_end + 1 : close_start]
        attributes: dict[str, str] = {
            match.group(1): match.group(2) for match in _ATTR_PATTERN.finditer(attr_string)
        }
        timestamp = unescape(attributes.get("timestamp", ""))
        encoded_cause_id = attributes.get("cause_id")
        return ConversationEntry(
            tag=tag,
            timestamp=timestamp,
            payload=_decode_payload(payload),
            cause_id=unescape(encoded_cause_id) if encoded_cause_id is not None else None,
        )

    def iter_correlated_entries(self) -> Iterator[ConversationEntry]:
        """Read transcript entries while preserving optional durable Cause correlation."""

        with self._lock:
            try:
                lines = self._path.read_text(encoding="utf-8").splitlines()
            except FileNotFoundError:
                lines = []
            except Exception as exc:  # pragma: no cover - defensive
                logger.error(
                    "conversation log read failed",
                    extra={"error": str(exc), "path": str(self._path)},
                )
                raise
        for line in lines:
            item = self._parse_line(line)
            if item is not None:
                yield item

    def iter_entries(self) -> Iterator[tuple[str, str, str]]:
        for entry in self.iter_correlated_entries():
            yield entry.tag, entry.timestamp, entry.payload

    def load_transcript(self) -> str:
        parts: list[str] = []
        for tag, timestamp, payload in self.iter_entries():
            safe_payload = escape(payload, quote=False)
            if timestamp:
                parts.append(f'<{tag} timestamp="{timestamp}">{safe_payload}</{tag}>')
            else:
                parts.append(f"<{tag}>{safe_payload}</{tag}>")
        return "\n".join(parts)

    def record_user_message(self, content: str, *, cause_id: str | None = None) -> None:
        timestamp = self._append("user_message", content, cause_id=cause_id)
        self._working_memory_log.append_entry("user_message", content, timestamp)

    def record_agent_message(self, content: str) -> None:
        timestamp = self._append("agent_message", content)
        self._working_memory_log.append_entry("agent_message", content, timestamp)

    def record_reply(self, content: str, *, cause_id: str | None = None) -> None:
        timestamp = self._append("poke_reply", content, cause_id=cause_id)
        self._working_memory_log.append_entry("poke_reply", content, timestamp)

    def record_reply_once(
        self,
        delivery_id: str,
        content: str,
        *,
        cause_id: str | None = None,
    ) -> bool:
        """Append one correlated reply at most once across process restarts."""

        marker = f'delivery_id="{_encode_attribute(delivery_id)}"'
        timestamp = now_in_user_timezone("%Y-%m-%d %H:%M:%S")
        assert isinstance(timestamp, str)
        cause = f' cause_id="{_encode_attribute(cause_id)}"' if cause_id is not None else ""
        entry = (
            f'<poke_reply timestamp="{timestamp}" {marker}{cause}>'
            f"{_encode_payload(content)}</poke_reply>\n"
        )
        with self._lock:
            lock_path = self._path.with_suffix(f"{self._path.suffix}.lock")
            with lock_path.open("a", encoding="utf-8") as lock_file:
                flock(lock_file.fileno(), LOCK_EX)
                try:
                    existing = self._path.read_text(encoding="utf-8") if self._path.exists() else ""
                    if marker in existing:
                        return False
                    with self._path.open("a", encoding="utf-8") as handle:
                        handle.write(entry)
                except Exception as exc:  # pragma: no cover - defensive
                    logger.error(
                        "correlated conversation reply failed",
                        extra={"error": str(exc), "path": str(self._path)},
                    )
                    raise
                finally:
                    flock(lock_file.fileno(), LOCK_UN)
        self._working_memory_log.append_entry("poke_reply", content, timestamp)
        self._notify_summarization()
        return True

    def record_wait(self, reason: str) -> None:
        """Record a wait marker that should not reach the user-facing chat history."""
        timestamp = self._append("wait", reason)
        self._working_memory_log.append_entry("wait", reason, timestamp)

    def _notify_summarization(self) -> None:
        settings = get_settings()
        if not settings.summarization_enabled:
            return

        try:
            from .summarization import schedule_summarization
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug(
                "summarization scheduler unavailable",
                extra={"error": str(exc)},
            )
            return

        try:
            schedule_summarization()
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning(
                "failed to schedule summarization",
                extra={"error": str(exc)},
            )

    def to_chat_messages(self) -> list[ChatMessage]:
        return [entry.message for entry in self.to_correlated_chat_messages()]

    def to_correlated_chat_messages(self) -> list[CorrelatedChatMessage]:
        """Project visible chat messages with stable IDs and their trusted Cause IDs."""

        messages: list[CorrelatedChatMessage] = []
        id_occurrences: dict[str, int] = {}
        for index, entry in enumerate(self.iter_correlated_entries()):
            if entry.tag == "wait":
                continue
            if entry.tag not in {"user_message", "poke_reply"}:
                continue
            role = "user" if entry.tag == "user_message" else "assistant"
            if entry.cause_id is None:
                base_id = f"legacy:{index}"
            elif role == "user":
                base_id = entry.cause_id
            else:
                base_id = f"reply:{entry.cause_id}"
            occurrence = id_occurrences.get(base_id, 0)
            id_occurrences[base_id] = occurrence + 1
            message_id = _bounded_message_id(base_id, occurrence)
            messages.append(
                CorrelatedChatMessage(
                    message=ChatMessage(
                        id=message_id,
                        role=role,
                        content=entry.payload,
                        timestamp=entry.timestamp or None,
                    ),
                    cause_id=entry.cause_id,
                )
            )
        return messages

    def clear(self) -> None:
        with self._lock:
            try:
                if self._path.exists():
                    self._path.unlink()
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning(
                    "conversation log clear failed",
                    extra={"error": str(exc), "path": str(self._path)},
                )
            finally:
                self._ensure_directory()
        try:
            self._working_memory_log.clear()
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug(
                "working memory clear skipped",
                extra={"error": str(exc)},
            )


_conversation_log = ConversationLog(_CONVERSATION_LOG_PATH)


def get_conversation_log() -> ConversationLog:
    return _conversation_log


__all__ = [
    "ConversationEntry",
    "ConversationLog",
    "CorrelatedChatMessage",
    "get_conversation_log",
]
