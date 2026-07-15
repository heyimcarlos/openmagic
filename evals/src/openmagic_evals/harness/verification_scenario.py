"""Public-seam setup for Example Insurance verification scenarios."""

from __future__ import annotations

import re
from dataclasses import dataclass
from uuid import UUID, uuid4

from example_insurance.renewals import (
    ApproveRenewalDraft,
    ApproveRenewalDraftInput,
    ExampleInsurance,
    ProvisionVerificationAuthority,
    ProvisionVerificationAuthorityInput,
    RequestProtectedRenewalDetails,
    RequestProtectedRenewalDetailsInput,
    RequestProtectedRenewalDetailsResult,
    StartRenewalOutreach,
)
from openmagic_runtime.commands import Actor, Cause, CommandReceipt
from openmagic_runtime.threads import CreateThread, ThreadStore

from openmagic_evals.harness.renewal_scenario import prepare_renewal_approval


@dataclass(frozen=True)
class VerificationScenario:
    renewal: StartRenewalOutreach
    actor: Actor
    protected_command: RequestProtectedRenewalDetails
    challenge_receipt: CommandReceipt[RequestProtectedRenewalDetailsResult]
    identifier_thread_id: UUID
    organization_party_id: UUID
    code: str | None


def issue_verification_challenge(
    application: ExampleInsurance,
    threads: ThreadStore,
    *,
    run_workflow: bool = True,
    deliver: bool = True,
    actor: Actor | None = None,
    protected_thread_id: UUID | None = None,
    identifier_thread_id: UUID | None = None,
    organization_party_id: UUID | None = None,
) -> VerificationScenario:
    renewal, actor = prepare_renewal_approval(
        application,
        threads,
        actor=actor,
        thread_id=protected_thread_id,
    )
    presentation = application.renewal_approval_presentation(renewal.input.workflow_id)
    approval = application.approve_renewal_draft(
        ApproveRenewalDraft(
            command_id=uuid4(),
            actor=actor,
            cause=Cause("message", str(uuid4())),
            input=ApproveRenewalDraftInput(
                workflow_id=renewal.input.workflow_id,
                wait_id=presentation.wait_id,
                draft_id=presentation.draft_id,
                message_id=presentation.message_id,
                thread_sequence=presentation.thread_sequence,
                message_fingerprint=presentation.message_fingerprint,
                presentation_fingerprint=presentation.presentation_fingerprint,
                proposed_effect=presentation.proposed_effect,
            ),
        )
    )
    if approval.result.approval_grant_id is None:
        raise AssertionError("Verification setup requires exact approval authority")
    party_id = UUID(actor.identifier)
    email = f"broker-{party_id}@example.test"
    if identifier_thread_id is None:
        identifier_thread = threads.create(CreateThread(uuid4(), "email", email))
        identifier_thread_id = identifier_thread.thread_id
    if organization_party_id is None:
        organization_party_id = uuid4()
    application.provision_verification_authority(
        ProvisionVerificationAuthority(
            command_id=uuid4(),
            actor=Actor("system", "fixture-authority"),
            cause=Cause("command", str(uuid4())),
            input=ProvisionVerificationAuthorityInput(
                party_id=party_id,
                organization_party_id=organization_party_id,
                workflow_id=renewal.input.workflow_id,
                email=email,
                delivery_thread_id=identifier_thread_id,
            ),
        )
    )
    protected = RequestProtectedRenewalDetails(
        command_id=uuid4(),
        actor=actor,
        cause=Cause("message", str(uuid4())),
        input=RequestProtectedRenewalDetailsInput(
            workflow_id=renewal.input.workflow_id,
            thread_id=renewal.input.thread_id,
            purpose="renewal.read_approved_details",
            approval_grant_id=approval.result.approval_grant_id,
        ),
    )
    required = application.request_protected_renewal_details(protected)
    if required.result.challenge_id is None:
        raise AssertionError("Verification setup did not create a Challenge")
    code: str | None = None
    if run_workflow:
        application.run_workflow_worker_once(worker_id="verification-worker")
        if deliver:
            application.run_delivery_worker_once(worker_id="verification-delivery")
            message = threads.read(identifier_thread_id).messages[-1].content
            code_match = re.search(r"\b(\d{6})\b", message)
            if code_match is None:
                raise AssertionError("Verification Delivery did not contain a code")
            code = code_match.group(1)
    return VerificationScenario(
        renewal=renewal,
        actor=actor,
        protected_command=protected,
        challenge_receipt=required,
        identifier_thread_id=identifier_thread_id,
        organization_party_id=organization_party_id,
        code=code,
    )


__all__ = [
    "VerificationScenario",
    "issue_verification_challenge",
]
