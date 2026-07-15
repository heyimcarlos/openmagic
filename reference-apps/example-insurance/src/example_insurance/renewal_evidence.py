"""Read-only evidence projection for the renewal drafting scenario."""

from __future__ import annotations

from typing import Any
from uuid import UUID

import psycopg
from openmagic_runtime.evidence import EvidenceRecord, RuntimeEvidenceReader


class RenewalEvidenceProjector:
    def __init__(self, *, database_url: str) -> None:
        self._database_url = database_url

    def to_json(self, workflow_id: UUID) -> str:
        with psycopg.connect(self._database_url) as connection, connection.transaction():
            connection.execute("SET TRANSACTION READ ONLY")
            workflow = connection.execute(
                "SELECT start_command_id, instance_id, thread_id, lifecycle FROM "
                "example_insurance.renewal_workflows WHERE workflow_id = %s",
                (workflow_id,),
            ).fetchone()
            if workflow is None:
                raise KeyError(f"Renewal Workflow not found: {workflow_id}")
            runtime = RuntimeEvidenceReader(connection).instance(UUID(str(workflow[1])))
            event = connection.execute(
                "SELECT event_id FROM example_insurance.domain_events "
                "WHERE workflow_id = %s AND event_type = 'renewal.draft.ready'",
                (workflow_id,),
            ).fetchone()
            draft = connection.execute(
                "SELECT agent_run_id FROM example_insurance.renewal_drafts WHERE workflow_id = %s",
                (workflow_id,),
            ).fetchone()
            delivery = (
                RuntimeEvidenceReader(connection).delivery(UUID(str(event[0])))
                if event is not None
                else None
            )
            effect_events = connection.execute(
                "SELECT count(*) FROM example_insurance.domain_events WHERE workflow_id = %s "
                "AND event_type LIKE 'external_effect.%%'",
                (workflow_id,),
            ).fetchone()
        correlations: dict[str, Any] = {
            "command_id": str(workflow[0]),
            "workflow_id": str(workflow_id),
            "instance_id": str(workflow[1]),
            "thread_id": str(workflow[2]),
            "step_ids": [str(step.step_id) for step in runtime.steps],
            "attempt_ids": [str(attempt_id) for attempt_id, _ in runtime.attempts],
            "agent_run_ids": [str(run_id) for run_id, _, _ in runtime.agent_runs],
        }
        if event is not None and delivery is not None and draft is not None:
            correlations.update(
                {
                    "domain_event_id": str(event[0]),
                    "delivery_id": str(delivery.delivery_id),
                    "message_id": (
                        str(delivery.delivered_message_id)
                        if delivery.delivered_message_id is not None
                        else None
                    ),
                    "agent_run_id": str(draft[0]),
                }
            )
        approval_waits = tuple(
            wait for wait in runtime.waits if wait.template_key == "renewal_draft_approval"
        )
        if len(approval_waits) > 1:
            raise RuntimeError("Renewal has more than one approval Wait")
        approval_wait = approval_waits[0] if approval_waits else None
        return EvidenceRecord(
            schema_version="openmagic.evidence.v1",
            scenario="renewal_drafting",
            correlations=correlations,
            outcomes={
                "workflow_lifecycle": str(workflow[3]),
                "step_states": {step.template_key: step.state for step in runtime.steps},
                "attempt_states": [state for _, state in runtime.attempts],
                "agent_run_states": [state for _, _, state in runtime.agent_runs],
                "delivery_attempt_states": (
                    list(delivery.attempt_states) if delivery is not None else []
                ),
                "approval_wait_id": (
                    str(approval_wait.wait_id) if approval_wait is not None else None
                ),
                "approval_wait_state": (approval_wait.state if approval_wait is not None else None),
                "delivery_state": delivery.status if delivery is not None else None,
                "external_email_effect_count": int(effect_events[0]) if effect_events else 0,
            },
            invariant_violations=(),
            redacted=True,
        ).to_json()


__all__ = ["RenewalEvidenceProjector"]
