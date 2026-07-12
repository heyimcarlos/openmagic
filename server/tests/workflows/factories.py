from __future__ import annotations

from uuid import UUID

from server.workflows import (
    DRAFT_RENEWAL_EMAIL_KIND,
    GMAIL_SEND_EMAIL_KIND,
    RENEWAL_OUTREACH_KIND,
    CreateWorkflowCommand,
    WorkflowCommandContext,
    WorkflowJobProposal,
    WorkflowProposal,
)

BROKER_ID = UUID("11111111-1111-1111-1111-111111111111")
ORGANIZATION_ID = UUID("22222222-2222-2222-2222-222222222222")


def renewal_proposal() -> WorkflowProposal:
    return WorkflowProposal(
        kind=RENEWAL_OUTREACH_KIND,
        objective="2026 renewal outreach for John Smith",
        input={"renewal_period": "2026"},
        jobs=(
            WorkflowJobProposal(
                key="draft",
                kind=DRAFT_RENEWAL_EMAIL_KIND,
                input={
                    "recipient_name": "John Smith",
                    "renewal_period": "2026",
                },
            ),
            WorkflowJobProposal(
                key="send",
                kind=GMAIL_SEND_EMAIL_KIND,
                input={
                    "sender_mailbox": "broker@acme.example",
                    "to": ["john@example.com"],
                    "subject": {"job_output": "draft", "field": "subject"},
                    "body": {"job_output": "draft", "field": "body"},
                },
                depends_on=("draft",),
            ),
        ),
    )


def create_command(proposal: WorkflowProposal | None = None) -> CreateWorkflowCommand:
    return CreateWorkflowCommand(
        context=WorkflowCommandContext(
            actor_party_id=BROKER_ID,
            organization_party_id=ORGANIZATION_ID,
            cause_type="message",
            cause_id="message-renewal-request",
        ),
        proposal=proposal or renewal_proposal(),
    )
