"""Pure approval Policy facts."""

from __future__ import annotations

from openmagic_runtime.commands import Actor

from example_insurance.renewal_approval_policy import (
    ApprovalDecisionAuthority,
    ApprovalDecisionFacts,
    RequestedApprovalPresentation,
)
from example_insurance.renewal_commands import (
    ApproveRenewalDraftInput,
    RequestRenewalRevisionInput,
)


def decision_facts(
    authority: ApprovalDecisionAuthority,
    actor: Actor,
    value: ApproveRenewalDraftInput | RequestRenewalRevisionInput,
) -> ApprovalDecisionFacts:
    return ApprovalDecisionFacts(
        lifecycle=authority.lifecycle,
        actor_matches=authority.authorized_actor_kind == actor.kind
        and authority.authorized_actor_id == actor.identifier,
        authority_revoked=authority.authority_revoked,
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
        durable=authority.durable,
    )


__all__ = ["ApprovalDecisionAuthority", "decision_facts"]
