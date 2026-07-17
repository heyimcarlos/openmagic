"""Public transaction-scoped read side for deterministic verification evidence."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from psycopg import Connection

from example_insurance._persistence.transaction_modes import set_repeatable_read_only
from example_insurance._persistence.verification_evidence_records import (
    ApplicationEventEvidence,
    VerificationApplicationEvidence,
    load_verification_application_evidence,
)


class VerificationEvidenceReader:
    """Observe one connected application and runtime chain from one database snapshot."""

    def __init__(self, connection: Connection[tuple[Any, ...]]) -> None:
        set_repeatable_read_only(connection)
        self._connection = connection

    def accepted_challenge(self, challenge_id: UUID) -> VerificationApplicationEvidence:
        return load_verification_application_evidence(self._connection, challenge_id)


__all__ = [
    "ApplicationEventEvidence",
    "VerificationApplicationEvidence",
    "VerificationEvidenceReader",
]
