"""Read models for committed renewal Command receipts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from openmagic_runtime.evidence import content_fingerprint
from psycopg import Connection


@dataclass(frozen=True)
class CommittedDispatchPermit:
    command_id: UUID
    result_digest: str
    result: dict[str, Any]


def load_committed_dispatch_permit(
    connection: Connection[tuple[Any, ...]],
    *,
    command_id: UUID,
    result_digest: str,
) -> CommittedDispatchPermit:
    row = connection.execute(
        "SELECT command_type, schema_version, result_digest, result "
        "FROM openmagic_runtime.command_receipts WHERE command_id = %s",
        (command_id,),
    ).fetchone()
    if row is None:
        raise RuntimeError("Email provider execution lacks its committed permit receipt")
    command_type, schema_version, durable_digest, result = row
    result_record = dict(result)
    if (
        str(command_type) != "renewal.authorize_email_dispatch"
        or int(schema_version) != 1
        or str(durable_digest) != result_digest
        or content_fingerprint(result_record) != result_digest
    ):
        raise RuntimeError("Email provider execution lacks its committed permit receipt")
    return CommittedDispatchPermit(command_id, result_digest, result_record)


__all__ = ["CommittedDispatchPermit", "load_committed_dispatch_permit"]
