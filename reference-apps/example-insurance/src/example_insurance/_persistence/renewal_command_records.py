"""Private read models for committed renewal Command receipts."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from openmagic_runtime.commands import read_committed_command_result
from openmagic_runtime.evidence import content_fingerprint
from psycopg import Connection

from example_insurance.renewal_effect_types import ExternalEffectPermit, permit_from_record


def load_committed_dispatch_permit(
    connection: Connection[tuple[Any, ...]],
    *,
    command_id: UUID,
    result_digest: str,
) -> ExternalEffectPermit:
    committed = read_committed_command_result(connection, command_id)
    if committed is None:
        raise RuntimeError("Email provider execution lacks its committed permit receipt")
    if (
        committed.command_type != "renewal.authorize_email_dispatch"
        or committed.schema_version != 1
        or committed.result_digest != result_digest
        or content_fingerprint(committed.result) != result_digest
    ):
        raise RuntimeError("Email provider execution lacks its committed permit receipt")
    try:
        return permit_from_record(committed.result)
    except (KeyError, TypeError, ValueError) as error:
        raise RuntimeError("Email provider execution lacks its committed permit receipt") from error


__all__ = ["load_committed_dispatch_permit"]
