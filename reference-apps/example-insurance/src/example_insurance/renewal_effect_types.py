"""Typed renewal email effect identities and authorization permits."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid5

from openmagic_runtime.evidence import content_fingerprint

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
    if set(payload) != {
        "logical_effect_id",
        "step_id",
        "attempt_id",
        "provider_idempotency_key",
        "effect_fingerprint",
        "effect",
    }:
        raise ValueError("External Effect permit record has unexpected fields")
    effect = payload["effect"]
    if not isinstance(effect, dict) or set(effect) != {
        "recipient_email",
        "subject",
        "body",
    }:
        raise ValueError("External Effect permit record has invalid provider input")
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


__all__ = [
    "ExternalEffectPermit",
    "RenewalApprovalPresentation",
    "RenewalEmailEffect",
    "logical_effect_id",
    "permit_from_record",
]
