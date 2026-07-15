"""Typed email provider seam for the Example Insurance renewal effect."""

from __future__ import annotations

import json
from dataclasses import dataclass
from http.client import RemoteDisconnected
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen
from uuid import UUID, uuid5

import psycopg
from openmagic_runtime.commands import CommandReceipt
from openmagic_runtime.evidence import content_fingerprint
from openmagic_runtime.execution import (
    AttemptExecution,
    AttemptObservation,
    CancellationToken,
)

from example_insurance.renewal_command_records import load_committed_dispatch_permit

_EFFECT_NAMESPACE = UUID("cbd8665d-e683-4c30-aab1-cdce90440e9d")


@dataclass(frozen=True)
class RenewalEmailEffect:
    recipient_email: str
    subject: str
    body: str

    def __post_init__(self) -> None:
        if not self.recipient_email.strip() or "@" not in self.recipient_email:
            raise ValueError("Renewal email recipient must be a non-empty email address")
        if not self.subject.strip() or not self.body.strip():
            raise ValueError("Renewal email subject and body must be non-empty")


@dataclass(frozen=True)
class RenewalApprovalPresentation:
    workflow_id: UUID
    wait_id: UUID
    draft_id: UUID
    message_id: UUID
    thread_sequence: int
    message_fingerprint: str
    presentation_fingerprint: str
    proposed_effect: RenewalEmailEffect


@dataclass(frozen=True)
class ExternalEffectPermit:
    logical_effect_id: UUID
    step_id: UUID
    attempt_id: UUID
    provider_idempotency_key: str
    effect_fingerprint: str
    effect: RenewalEmailEffect

    def __post_init__(self) -> None:
        if self.logical_effect_id != logical_effect_id(self.step_id):
            raise ValueError("External Effect permit has a non-canonical effect identity")
        if self.provider_idempotency_key != str(self.logical_effect_id):
            raise ValueError("External Effect permit has a non-canonical idempotency identity")
        if self.effect_fingerprint != content_fingerprint(self.effect):
            raise ValueError("External Effect permit input differs from its fingerprint")


def logical_effect_id(step_id: UUID) -> UUID:
    return uuid5(_EFFECT_NAMESPACE, str(step_id))


def permit_from_record(payload: dict[str, Any]) -> ExternalEffectPermit:
    effect = dict(payload["effect"])
    return ExternalEffectPermit(
        logical_effect_id=UUID(str(payload["logical_effect_id"])),
        step_id=UUID(str(payload["step_id"])),
        attempt_id=UUID(str(payload["attempt_id"])),
        provider_idempotency_key=str(payload["provider_idempotency_key"]),
        effect_fingerprint=str(payload["effect_fingerprint"]),
        effect=RenewalEmailEffect(
            recipient_email=str(effect["recipient_email"]),
            subject=str(effect["subject"]),
            body=str(effect["body"]),
        ),
    )


def committed_permit_execution_input(
    receipt: CommandReceipt[ExternalEffectPermit],
) -> dict[str, Any]:
    return {
        "authorization_command_id": str(receipt.command_id),
        "authorization_result_digest": receipt.result_digest,
    }


def _permit_bound_input(
    execution: AttemptExecution,
    *,
    database_url: str,
) -> tuple[UUID, str, RenewalEmailEffect]:
    expected_fields = {
        "authorization_command_id",
        "authorization_result_digest",
    }
    if set(execution.input) != expected_fields:
        raise RuntimeError("Email provider execution requires exact permit-bound input")
    try:
        command_id = UUID(str(execution.input["authorization_command_id"]))
    except ValueError as error:
        raise RuntimeError("Email provider permit identities are invalid") from error
    requested_digest = str(execution.input["authorization_result_digest"])
    with psycopg.connect(database_url) as connection, connection.transaction():
        connection.execute("SET TRANSACTION READ ONLY")
        receipt = load_committed_dispatch_permit(
            connection,
            command_id=command_id,
            result_digest=requested_digest,
        )
    permit = permit_from_record(receipt.result)
    if (
        permit.step_id != execution.step_id
        or permit.attempt_id != execution.attempt_id
        or permit.logical_effect_id != logical_effect_id(execution.step_id)
    ):
        raise RuntimeError("Email provider execution conflicts with its committed permit")
    return permit.logical_effect_id, permit.provider_idempotency_key, permit.effect


def _read_json(response: Any) -> dict[str, Any]:
    payload = json.load(response)
    if not isinstance(payload, dict):
        raise RuntimeError("Email provider returned a non-object response")
    return payload


def _classification(payload: dict[str, Any]) -> tuple[str, str]:
    classification = payload.get("classification")
    request_id = payload.get("provider_request_id")
    if classification not in {"applied", "not_applied", "uncertain"}:
        raise RuntimeError("Email provider returned an unsupported classification")
    if not isinstance(request_id, str) or not request_id:
        raise RuntimeError("Email provider omitted its request identity")
    return classification, request_id


class EmailProviderExecutor:
    def __init__(
        self,
        *,
        database_url: str,
        provider_url: str,
        timeout_seconds: float = 3.0,
    ) -> None:
        self._database_url = database_url
        self._provider_url = provider_url.rstrip("/")
        self._timeout_seconds = timeout_seconds

    def execute(
        self, execution: AttemptExecution, cancellation: CancellationToken
    ) -> AttemptObservation:
        if cancellation.cancelled:
            raise RuntimeError("Attempt execution was cancelled")
        effect_id, provider_key, effect = _permit_bound_input(
            execution,
            database_url=self._database_url,
        )
        request = Request(
            f"{self._provider_url}/dispatch",
            data=json.dumps(
                {
                    "logical_effect_id": str(effect_id),
                    "idempotency_key": provider_key,
                    "recipient_email": effect.recipient_email,
                    "subject": effect.subject,
                    "body": effect.body,
                }
            ).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=self._timeout_seconds) as response:
                classification, request_id = _classification(_read_json(response))
        except HTTPError as error:
            with error:
                classification, request_id = _classification(_read_json(error))
        except (OSError, RemoteDisconnected, TimeoutError, URLError):
            classification = "uncertain"
            request_id = str(effect_id)
        if cancellation.cancelled:
            raise RuntimeError("Attempt execution was cancelled")
        return AttemptObservation(
            value={
                "classification": classification,
                "provider_request_id": request_id,
            }
        )


class EmailReconciliationExecutor:
    def __init__(self, *, provider_url: str, timeout_seconds: float = 3.0) -> None:
        self._provider_url = provider_url.rstrip("/")
        self._timeout_seconds = timeout_seconds

    def execute(
        self, execution: AttemptExecution, cancellation: CancellationToken
    ) -> AttemptObservation:
        if cancellation.cancelled:
            raise RuntimeError("Attempt execution was cancelled")
        effect_id = str(execution.input["logical_effect_id"])
        try:
            with urlopen(
                f"{self._provider_url}/effects/{quote(effect_id, safe='')}",
                timeout=self._timeout_seconds,
            ) as response:
                classification, request_id = _classification(_read_json(response))
        except HTTPError as error:
            if error.code == 404:
                classification = "not_applied"
                request_id = effect_id
            else:
                with error:
                    classification, request_id = _classification(_read_json(error))
        except (OSError, RemoteDisconnected, TimeoutError, URLError):
            classification = "uncertain"
            request_id = effect_id
        if cancellation.cancelled:
            raise RuntimeError("Attempt execution was cancelled")
        return AttemptObservation(
            value={
                "classification": classification,
                "provider_request_id": request_id,
            }
        )


__all__ = [
    "EmailProviderExecutor",
    "EmailReconciliationExecutor",
    "ExternalEffectPermit",
    "RenewalApprovalPresentation",
    "RenewalEmailEffect",
    "committed_permit_execution_input",
    "logical_effect_id",
    "permit_from_record",
]
