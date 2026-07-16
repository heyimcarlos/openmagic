"""Pure approval Policy facts."""

from __future__ import annotations

from openmagic_runtime.commands import Actor

from example_insurance._persistence.renewal_approval_records import ApprovalSnapshot
from example_insurance.renewal_approval_policy import (
    ApprovalDecisionFacts,
    RequestedApprovalPresentation,
)
from example_insurance.renewal_commands import (
    ApproveRenewalDraftInput,
    RequestRenewalRevisionInput,
)


def decision_facts(
    snapshot: ApprovalSnapshot,
    actor: Actor,
    value: ApproveRenewalDraftInput | RequestRenewalRevisionInput,
) -> ApprovalDecisionFacts:
    workflow = snapshot.workflow
    return ApprovalDecisionFacts(
        lifecycle=workflow.lifecycle,
        actor_matches=workflow.authorized_actor_kind == actor.kind
        and workflow.authorized_actor_id == actor.identifier,
        authority_revoked=workflow.authority_revoked,
        requested=RequestedApprovalPresentation(
            workflow_id=value.workflow_id,
            wait_id=value.wait_id,
            draft_id=value.draft_id,
            message_id=value.message_id,
            thread_sequence=value.thread_sequence,
            message_fingerprint=value.message_fingerprint,
            presentation_fingerprint=value.presentation_fingerprint,
            effect=value.proposed_effect,
        ),
        durable=snapshot.durable_presentation(),
    )


__all__ = ["decision_facts"]
