"""Typed email provider seam for the Example Insurance renewal effect."""

from __future__ import annotations

import json
from http.client import RemoteDisconnected
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen
from uuid import UUID

import psycopg
from openmagic_runtime.commands import CommandReceipt
from openmagic_runtime.execution import (
    AttemptExecution,
    AttemptObservation,
    CancellationToken,
)

from example_insurance.renewal_command_records import load_committed_dispatch_permit
from example_insurance.renewal_effect_types import ExternalEffectPermit, logical_effect_id


def committed_permit_execution_input(
    receipt: CommandReceipt[ExternalEffectPermit],
) -> dict[str, Any]:
    return {
        "authorization_command_id": str(receipt.command_id),
        "authorization_result_digest": receipt.result_digest,
    }


def _permit_reference(execution: AttemptExecution) -> tuple[UUID, str]:
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
    return command_id, str(execution.input["authorization_result_digest"])


def _validate_permit(execution: AttemptExecution, permit: ExternalEffectPermit) -> None:
    if (
        permit.step_id != execution.step_id
        or permit.attempt_id != execution.attempt_id
        or permit.logical_effect_id != logical_effect_id(execution.step_id)
    ):
        raise RuntimeError("Email provider execution conflicts with its committed permit")


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


class EmailProviderClient:
    def __init__(self, *, provider_url: str, timeout_seconds: float = 3.0) -> None:
        self._provider_url = provider_url.rstrip("/")
        self._timeout_seconds = timeout_seconds

    def execute(
        self, permit: ExternalEffectPermit, cancellation: CancellationToken
    ) -> AttemptObservation:
        if cancellation.cancelled:
            raise RuntimeError("Attempt execution was cancelled")
        request = Request(
            f"{self._provider_url}/dispatch",
            data=json.dumps(
                {
                    "logical_effect_id": str(permit.logical_effect_id),
                    "idempotency_key": permit.provider_idempotency_key,
                    "recipient_email": permit.effect.recipient_email,
                    "subject": permit.effect.subject,
                    "body": permit.effect.body,
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
            request_id = str(permit.logical_effect_id)
        if cancellation.cancelled:
            raise RuntimeError("Attempt execution was cancelled")
        return AttemptObservation(
            value={
                "classification": classification,
                "provider_request_id": request_id,
            }
        )


class AuthorizedEmailEffectExecutor:
    def __init__(self, *, database_url: str, client: EmailProviderClient) -> None:
        self._database_url = database_url
        self._client = client

    def execute(
        self, execution: AttemptExecution, cancellation: CancellationToken
    ) -> AttemptObservation:
        command_id, result_digest = _permit_reference(execution)
        with psycopg.connect(self._database_url) as connection, connection.transaction():
            connection.execute("SET TRANSACTION READ ONLY")
            permit = load_committed_dispatch_permit(
                connection,
                command_id=command_id,
                result_digest=result_digest,
            )
        _validate_permit(execution, permit)
        return self._client.execute(permit, cancellation)


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
    "AuthorizedEmailEffectExecutor",
    "EmailProviderClient",
    "EmailReconciliationExecutor",
    "committed_permit_execution_input",
]
