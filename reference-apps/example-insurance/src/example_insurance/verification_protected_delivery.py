"""Authorized protected renewal Delivery creation and Command resolution."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from openmagic_runtime.commands import Actor, Cause
from openmagic_runtime.delivery import DeliveryControl
from psycopg import Connection

from example_insurance._persistence.renewal_records import record_event
from example_insurance._persistence.renewal_workflow_records import protected_renewal_details
from example_insurance._persistence.verification_challenge_records import (
    record_authorized_command,
    resolve_protected_command,
)
from example_insurance.verification_policy import VERIFICATION_DELIVERY_RETRY_POLICY


@dataclass(frozen=True)
class ProtectedDeliveryContext:
    protected_command_id: UUID
    party_id: UUID
    workflow_id: UUID
    thread_id: UUID
    purpose: str
    approval_grant_id: UUID


class ProtectedRenewalDeliveryControl:
    def create_for_new_command(
        self,
        connection: Connection[tuple[Any, ...]],
        context: ProtectedDeliveryContext,
    ) -> UUID:
        delivery_id = self._create(connection, context)
        record_authorized_command(
            connection,
            protected_command_id=context.protected_command_id,
            party_id=context.party_id,
            workflow_id=context.workflow_id,
            thread_id=context.thread_id,
            purpose=context.purpose,
            approval_grant_id=context.approval_grant_id,
            delivery_id=delivery_id,
        )
        return delivery_id

    def resume_waiting_command(
        self,
        connection: Connection[tuple[Any, ...]],
        context: ProtectedDeliveryContext,
    ) -> UUID:
        delivery_id = self._create(connection, context)
        resolve_protected_command(
            connection,
            protected_command_id=context.protected_command_id,
            outcome="authorized",
            delivery_id=delivery_id,
        )
        return delivery_id

    @staticmethod
    def _create(
        connection: Connection[tuple[Any, ...]],
        context: ProtectedDeliveryContext,
    ) -> UUID:
        details = protected_renewal_details(connection, context.workflow_id)
        event_id = record_event(
            connection,
            event_type="renewal.protected_details.authorized",
            workflow_id=context.workflow_id,
            actor=Actor("system", "verification-policy"),
            cause=Cause("command", str(context.protected_command_id)),
            payload={
                "protected_command_id": str(context.protected_command_id),
                "approval_grant_id": str(context.approval_grant_id),
            },
        )
        intent = DeliveryControl(connection).create(
            domain_event_id=event_id,
            thread_id=context.thread_id,
            audience={"kind": "party", "identifier": str(context.party_id)},
            message_author={"kind": "system", "identifier": "example-insurance"},
            content_descriptor={
                "template_key": "example_insurance.renewal_protected_details.v1",
                "template_version": 1,
                "locale": "en-CA",
                "input": {
                    "protected_command_id": str(context.protected_command_id),
                    "purpose": context.purpose,
                    "policy_number": details.policy_number,
                    "policyholder_name": details.policyholder_name,
                    "renewal_date": details.renewal_date,
                },
            },
            message_content=(
                f"Approved renewal details for policy {details.policy_number}: "
                f"{details.policyholder_name}, renewal date {details.renewal_date}."
            ),
            retry_policy=VERIFICATION_DELIVERY_RETRY_POLICY,
        )
        return intent.delivery_id


__all__ = [
    "ProtectedDeliveryContext",
    "ProtectedRenewalDeliveryControl",
]
