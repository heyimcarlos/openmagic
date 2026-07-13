"""Per-interaction conversation state for the simulated SMS channel."""

from __future__ import annotations

import hashlib
import threading
from dataclasses import dataclass
from pathlib import Path

from .log import ConversationLog

_DEFAULT_SESSION_ROOT = (
    Path(__file__).resolve().parent.parent.parent / "data" / "conversation" / "sms"
)


class SessionWorkingMemory:
    """No-op summarization state that keeps one SMS transcript isolated."""

    def append_entry(self, tag: str, payload: str, timestamp: str) -> None:
        del tag, payload, timestamp

    def clear(self) -> None:
        return None

    def render_transcript(self) -> str:
        return ""


@dataclass(frozen=True)
class ConversationSession:
    log: ConversationLog
    working_memory: SessionWorkingMemory


class ConversationSessionStore:
    """Resolve stable, filesystem-safe conversation state by interaction ID."""

    def __init__(self, root: Path = _DEFAULT_SESSION_ROOT) -> None:
        self._root = root
        self._sessions: dict[str, ConversationSession] = {}
        self._lock = threading.Lock()

    def get(self, interaction_id: str) -> ConversationSession:
        normalized = interaction_id.strip()
        if not normalized:
            raise ValueError("interaction_id is required")
        with self._lock:
            existing = self._sessions.get(normalized)
            if existing is not None:
                return existing
            digest = hashlib.sha256(normalized.encode()).hexdigest()
            memory = SessionWorkingMemory()
            session = ConversationSession(
                log=ConversationLog(
                    self._root / f"{digest}.log",
                    working_memory_log=memory,
                ),
                working_memory=memory,
            )
            self._sessions[normalized] = session
            return session


_conversation_sessions = ConversationSessionStore()


def get_conversation_session(interaction_id: str) -> ConversationSession:
    return _conversation_sessions.get(interaction_id)


__all__ = [
    "ConversationSession",
    "ConversationSessionStore",
    "SessionWorkingMemory",
    "get_conversation_session",
]
