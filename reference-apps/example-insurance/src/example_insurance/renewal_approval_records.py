"""Canonical transaction-bound approval presentation snapshot."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from typing import Any
from uuid import UUID

from openmagic_runtime.commands import StateConflict
from openmagic_runtime.delivery import (
    DeliveryPresentation,
    lock_delivery_presentation,
    read_delivery_presentation,
)
from openmagic_runtime.evidence import content_fingerprint
from openmagic_runtime.kernel.records import RuntimeWait, lock_wait, waits_for_instance
from psycopg import Connection
from psycopg.rows import dict_row

from example_insurance.renewal_approval_policy import (
    DeliveredApprovalPresentation,
    DurableApprovalPresentation,
    message_source_kind,
)
from example_insurance.renewal_effect_types import (
    RenewalApprovalPresentation,
    RenewalEmailEffect,
)
from example_insurance.renewal_lifecycle_policy import (
    WorkflowLifecycle,
    workflow_lifecycle,
)
from example_insurance.renewal_workflow_records import lock_instance_for_workflow


@dataclass(frozen=True)
class ApprovalWorkflow:
    workflow_id: UUID
    instance_id: UUID
    thread_id: UUID
    lifecycle: WorkflowLifecycle
    authorized_actor_kind: str
    authorized_actor_id: str
    authority_revoked: bool
    policy_number: str
    policyholder_name: str
    policyholder_email: str
    renewal_date: str
    expiring_premium_cents: int

    @classmethod
    def decode(cls, record: Mapping[str, Any]) -> ApprovalWorkflow:
        renewal_date = record["renewal_date"]
        if not isinstance(renewal_date, date):
            raise RuntimeError("Renewal Workflow date has an invalid type")
        return cls(
            workflow_id=UUID(str(record["workflow_id"])),
            instance_id=UUID(str(record["instance_id"])),
            thread_id=UUID(str(record["thread_id"])),
            lifecycle=workflow_lifecycle(record["lifecycle"]),
            authorized_actor_kind=str(record["authorized_actor_kind"]),
            authorized_actor_id=str(record["authorized_actor_id"]),
            authority_revoked=bool(record["authority_revoked"]),
            policy_number=str(record["policy_number"]),
            policyholder_name=str(record["policyholder_name"]),
            policyholder_email=str(record["policyholder_email"]),
            renewal_date=renewal_date.isoformat(),
            expiring_premium_cents=int(record["expiring_premium_cents"]),
        )


@dataclass(frozen=True)
class ApprovalDraft:
    draft_id: UUID
    event_id: UUID
    presentation_fingerprint: str
    effect: RenewalEmailEffect

    @classmethod
    def decode(cls, record: Mapping[str, Any]) -> ApprovalDraft:
        return cls(
            draft_id=UUID(str(record["draft_id"])),
            event_id=UUID(str(record["event_id"])),
            presentation_fingerprint=str(record["presentation_fingerprint"]),
            effect=RenewalEmailEffect(
                recipient_email=str(record["policyholder_email"]),
                subject=str(record["subject"]),
                body=str(record["body"]),
            ),
        )


@dataclass(frozen=True)
class ApprovalSnapshot:
    workflow: ApprovalWorkflow
    draft: ApprovalDraft
    wait: RuntimeWait
    delivery: DeliveryPresentation | None

    def _delivered_presentation(self) -> DeliveredApprovalPresentation | None:
        delivery = self.delivery
        if delivery is None or delivery.delivered_message_id is None or delivery.message is None:
            return None
        message = delivery.message
        return DeliveredApprovalPresentation(
            delivery_id=delivery.delivery_id,
            delivery_thread_id=delivery.thread_id,
            status=delivery.status,
            acknowledged=delivery.acknowledged,
            delivered_message_id=delivery.delivered_message_id,
            message_id=message.message_id,
            message_thread_id=message.thread_id,
            sequence=message.sequence,
            content_fingerprint=content_fingerprint(message.content),
            source_kind=message_source_kind(message.source_kind),
            source_id=message.source_id,
        )

    def durable_presentation(self) -> DurableApprovalPresentation:
        expected_wait_input = {
            "workflow_id": str(self.workflow.workflow_id),
            "draft_id": str(self.draft.draft_id),
            "presentation_fingerprint": self.draft.presentation_fingerprint,
            "recipient_email": self.draft.effect.recipient_email,
            "subject": self.draft.effect.subject,
            "body": self.draft.effect.body,
        }
        return DurableApprovalPresentation(
            workflow_id=self.workflow.workflow_id,
            thread_id=self.workflow.thread_id,
            wait_id=self.wait.wait_id,
            draft_id=self.draft.draft_id,
            wait_state=self.wait.state,
            wait_input_matches=self.wait.input == expected_wait_input,
            presentation_fingerprint=self.draft.presentation_fingerprint,
            effect=self.draft.effect,
            delivery=self._delivered_presentation(),
        )

    def presentation(self) -> RenewalApprovalPresentation:
        durable = self.durable_presentation()
        identity = durable.identity()
        if durable.wait_state != "unsatisfied" or identity is None:
            raise KeyError(f"Renewal approval presentation not found: {self.workflow.workflow_id}")
        return RenewalApprovalPresentation(
            workflow_id=identity.workflow_id,
            wait_id=identity.wait_id,
            draft_id=identity.draft_id,
            message_id=identity.message.message_id,
            thread_sequence=identity.message.thread_sequence,
            message_fingerprint=identity.message.content_fingerprint,
            presentation_fingerprint=identity.presentation_fingerprint,
            proposed_effect=identity.effect,
        )


def _read_workflow(
    connection: Connection[tuple[Any, ...]], workflow_id: UUID
) -> ApprovalWorkflow | None:
    with connection.cursor(row_factory=dict_row) as cursor:
        record = cursor.execute(
            "SELECT r.workflow_id, r.instance_id, r.thread_id, r.lifecycle, "
            "r.authorized_actor_kind, r.authorized_actor_id, "
            "r.authority_revoked_at IS NOT NULL AS authority_revoked, r.policy_number, "
            "r.policyholder_name, f.policyholder_email, r.renewal_date, "
            "r.expiring_premium_cents FROM example_insurance.renewal_workflows r "
            "JOIN example_insurance.policy_renewal_facts f ON f.policy_id = r.policy_id "
            "WHERE r.workflow_id = %s",
            (workflow_id,),
        ).fetchone()
    return ApprovalWorkflow.decode(record) if record is not None else None


def _lock_workflow(
    connection: Connection[tuple[Any, ...]], workflow_id: UUID
) -> ApprovalWorkflow | None:
    with connection.cursor(row_factory=dict_row) as cursor:
        record = cursor.execute(
            "SELECT r.workflow_id, r.instance_id, r.thread_id, r.lifecycle, "
            "r.authorized_actor_kind, r.authorized_actor_id, "
            "r.authority_revoked_at IS NOT NULL AS authority_revoked, r.policy_number, "
            "r.policyholder_name, f.policyholder_email, r.renewal_date, "
            "r.expiring_premium_cents FROM example_insurance.renewal_workflows r "
            "JOIN example_insurance.policy_renewal_facts f ON f.policy_id = r.policy_id "
            "WHERE r.workflow_id = %s FOR UPDATE OF r",
            (workflow_id,),
        ).fetchone()
    return ApprovalWorkflow.decode(record) if record is not None else None


def _read_latest_draft(
    connection: Connection[tuple[Any, ...]], workflow_id: UUID
) -> ApprovalDraft | None:
    with connection.cursor(row_factory=dict_row) as cursor:
        record = cursor.execute(
            "SELECT d.draft_id, d.presentation_fingerprint, d.policyholder_email, "
            "d.subject, d.body, d.ready_event_id AS event_id "
            "FROM example_insurance.renewal_drafts d "
            "WHERE d.workflow_id = %s AND d.ready_event_id IS NOT NULL "
            "ORDER BY d.created_at DESC, d.draft_id DESC LIMIT 1",
            (workflow_id,),
        ).fetchone()
    return ApprovalDraft.decode(record) if record is not None else None


def _lock_exact_draft(
    connection: Connection[tuple[Any, ...]],
    *,
    workflow_id: UUID,
    draft_id: UUID,
) -> ApprovalDraft | None:
    with connection.cursor(row_factory=dict_row) as cursor:
        record = cursor.execute(
            "SELECT d.draft_id, d.presentation_fingerprint, d.policyholder_email, "
            "d.subject, d.body, d.ready_event_id AS event_id "
            "FROM example_insurance.renewal_drafts d "
            "WHERE d.workflow_id = %s AND d.draft_id = %s "
            "AND d.ready_event_id IS NOT NULL FOR UPDATE OF d",
            (workflow_id, draft_id),
        ).fetchone()
    return ApprovalDraft.decode(record) if record is not None else None


def _assemble_snapshot(
    *,
    workflow: ApprovalWorkflow | None,
    draft: ApprovalDraft | None,
    wait: RuntimeWait | None,
    delivery: DeliveryPresentation | None,
) -> ApprovalSnapshot:
    if workflow is None or draft is None or wait is None:
        raise StateConflict("Exact Workflow, Wait, or Draft does not exist")
    return ApprovalSnapshot(workflow, draft, wait, delivery)


def load_approval_presentation_snapshot(
    connection: Connection[tuple[Any, ...]], workflow_id: UUID
) -> ApprovalSnapshot:
    try:
        workflow = _read_workflow(connection, workflow_id)
        if workflow is None:
            raise StateConflict("Exact Workflow does not exist")
        draft = _read_latest_draft(connection, workflow_id)
        waits = tuple(
            wait
            for wait in waits_for_instance(connection, workflow.instance_id)
            if draft is not None
            and wait.template_key == "renewal_draft_approval"
            and wait.input.get("draft_id") == str(draft.draft_id)
        )
        wait = waits[-1] if waits else None
        delivery = (
            read_delivery_presentation(
                connection,
                domain_event_id=draft.event_id,
                thread_id=workflow.thread_id,
            )
            if draft is not None
            else None
        )
        return _assemble_snapshot(
            workflow=workflow,
            draft=draft,
            wait=wait,
            delivery=delivery,
        )
    except StateConflict:
        raise KeyError(f"Renewal approval presentation not found: {workflow_id}") from None


def lock_approval_decision_snapshot(
    connection: Connection[tuple[Any, ...]],
    *,
    workflow_id: UUID,
    draft_id: UUID,
    wait_id: UUID,
) -> ApprovalSnapshot:
    identity = lock_instance_for_workflow(connection, workflow_id)
    if identity is None:
        raise StateConflict("Exact Workflow does not exist")
    workflow = _lock_workflow(connection, workflow_id)
    if workflow is None or workflow.instance_id != identity.instance_id:
        raise StateConflict("Exact Workflow does not exist")
    draft = _lock_exact_draft(
        connection,
        workflow_id=workflow_id,
        draft_id=draft_id,
    )
    wait = lock_wait(
        connection,
        instance_id=identity.instance_id,
        wait_id=wait_id,
    )
    delivery = (
        lock_delivery_presentation(
            connection,
            domain_event_id=draft.event_id,
            thread_id=workflow.thread_id,
        )
        if draft is not None
        else None
    )
    return _assemble_snapshot(
        workflow=workflow,
        draft=draft,
        wait=wait,
        delivery=delivery,
    )


__all__ = [
    "ApprovalDraft",
    "ApprovalSnapshot",
    "ApprovalWorkflow",
    "load_approval_presentation_snapshot",
    "lock_approval_decision_snapshot",
]
