"""Typed Workflow catalog used by the local backpressure playground."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from server.workflows import (
    CLAIM_INTAKE_REVIEW_KIND,
    DRAFT_RENEWAL_EMAIL_KIND,
    GMAIL_SEND_EMAIL_KIND,
    INSURANCE_TASK_KIND,
    POLICY_COVERAGE_REVIEW_KIND,
    RENEWAL_OUTREACH_KIND,
    WorkflowJobProposal,
    WorkflowProposal,
)

DemoWorkflowScenario = Literal["renewal", "claim", "policy"]
DemoWorkflowSelection = Literal["mixed", "renewal", "claim", "policy"]
MIXED_WORKFLOW_SCENARIOS: tuple[DemoWorkflowScenario, ...] = (
    "renewal",
    "claim",
    "policy",
)


def build_demo_workflow_proposal(
    scenario: DemoWorkflowScenario,
    *,
    index: int,
    request_id: UUID,
    broker_email: str,
    policyholder_email: str,
) -> WorkflowProposal:
    """Build one closed, typed workload without bypassing registry validation."""

    suffix = request_id.hex[:8]
    if scenario == "renewal":
        return WorkflowProposal(
            kind=RENEWAL_OUTREACH_KIND,
            objective=f"Prepare renewal outreach {suffix}",
            input={"renewal_period": "2026"},
            jobs=(
                WorkflowJobProposal(
                    key="draft",
                    kind=DRAFT_RENEWAL_EMAIL_KIND,
                    input={
                        "recipient_name": f"Demo Policyholder {index}",
                        "renewal_period": "2026",
                    },
                ),
                WorkflowJobProposal(
                    key="send",
                    kind=GMAIL_SEND_EMAIL_KIND,
                    input={
                        "sender_mailbox": broker_email,
                        "to": [policyholder_email],
                        "subject": {"job_output": "draft", "field": "subject"},
                        "body": {"job_output": "draft", "field": "body"},
                    },
                    depends_on=("draft",),
                ),
            ),
        )
    if scenario == "claim":
        reference = f"CLM-{suffix.upper()}"
        return WorkflowProposal(
            kind=CLAIM_INTAKE_REVIEW_KIND,
            objective=f"Review first notice of loss {reference}",
            input={
                "claim_reference": reference,
                "claimant_name": f"Demo Claimant {index}",
            },
            jobs=(
                WorkflowJobProposal(
                    key="extract_facts",
                    kind=INSURANCE_TASK_KIND,
                    input={
                        "task_type": "extract_claim_facts",
                        "subject": reference,
                        "context": "A reported vehicle incident needs a bounded fact summary.",
                    },
                ),
                WorkflowJobProposal(
                    key="triage",
                    kind=INSURANCE_TASK_KIND,
                    input={
                        "task_type": "triage_claim",
                        "subject": reference,
                        "context": (
                            "Identify the appropriate next review queue without deciding coverage."
                        ),
                    },
                    depends_on=("extract_facts",),
                ),
            ),
        )
    reference = f"POL-{suffix.upper()}"
    return WorkflowProposal(
        kind=POLICY_COVERAGE_REVIEW_KIND,
        objective=f"Review policy questions for {reference}",
        input={
            "policy_reference": reference,
            "review_focus": "Summarize open coverage questions without making a coverage decision.",
        },
        jobs=(
            WorkflowJobProposal(
                key="review_coverage",
                kind=INSURANCE_TASK_KIND,
                input={
                    "task_type": "review_policy_coverage",
                    "subject": reference,
                    "context": "List the policy facts a licensed reviewer should verify next.",
                },
            ),
        ),
    )


__all__ = [
    "MIXED_WORKFLOW_SCENARIOS",
    "DemoWorkflowScenario",
    "DemoWorkflowSelection",
    "build_demo_workflow_proposal",
]
