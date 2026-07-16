from __future__ import annotations

import json
import time
from dataclasses import replace
from threading import Barrier
from uuid import UUID, uuid4

import pytest
from example_insurance.renewals import (
    ApproveRenewalDraft,
    ApproveRenewalDraftInput,
    ApproveRenewalDraftResult,
    ExampleInsurance,
    RenewalEmailEffect,
    RenewalFacts,
    RequestRenewalRevision,
    RequestRenewalRevisionInput,
    RequestRenewalRevisionResult,
    StartRenewalOutreach,
    StartRenewalOutreachInput,
)
from openmagic_evals.evidence.case_recording import record_renewal_case
from openmagic_evals.harness import prepare_renewal_approval, renewal_context
from openmagic_runtime.commands import Actor, Cause, CommandReceipt
from openmagic_runtime.evidence import content_fingerprint
from openmagic_runtime.kernel.inspection import KernelInspection
from openmagic_runtime.threads import CreateThread


def wait_for_race(barrier: Barrier, delay: float) -> None:
    barrier.wait()
    time.sleep(delay)


def race_approval(
    barrier: Barrier,
    delay: float,
    application: ExampleInsurance,
    command: ApproveRenewalDraft,
) -> CommandReceipt[ApproveRenewalDraftResult]:
    wait_for_race(barrier, delay)
    return application.approve_renewal_draft(command)


def race_revision(
    barrier: Barrier,
    delay: float,
    application: ExampleInsurance,
    command: RequestRenewalRevision,
) -> CommandReceipt[RequestRenewalRevisionResult]:
    wait_for_race(barrier, delay)
    return application.request_renewal_revision(command)


def test_approval_presentation_requires_acknowledged_delivery() -> None:
    with renewal_context() as (database_url, application, threads):
        command, actor = prepare_renewal_approval(application, threads, deliver=False)
        started = application.start_renewal_outreach(command)
        snapshot = KernelInspection(database_url=database_url).snapshot(started.result.instance_id)
        delivery = application.claim_delivery_attempt(
            worker_id="unacknowledged-delivery",
            claim_request_id=uuid4(),
        )
        assert delivery is not None
        value = dict(delivery.content_descriptor["input"])
        effect = RenewalEmailEffect(
            recipient_email=str(value["recipient_email"]),
            subject=str(value["subject"]),
            body=str(value["body"]),
        )
        before_delivery = application.approve_renewal_draft(
            ApproveRenewalDraft(
                command_id=uuid4(),
                actor=actor,
                cause=Cause(kind="message", identifier=str(uuid4())),
                input=ApproveRenewalDraftInput(
                    workflow_id=command.input.workflow_id,
                    wait_id=snapshot.waits[-1].wait_id,
                    draft_id=UUID(str(value["draft_id"])),
                    message_id=uuid4(),
                    thread_sequence=delivery.context_through_sequence + 1,
                    message_fingerprint=content_fingerprint(f"{effect.subject}\n\n{effect.body}"),
                    presentation_fingerprint=str(value["presentation_fingerprint"]),
                    proposed_effect=effect,
                ),
            )
        )

        with pytest.raises(KeyError, match="presentation"):
            application.renewal_approval_presentation(command.input.workflow_id)
        assert before_delivery.result.outcome == "stale_presentation"
        record_renewal_case(
            case_id="wait.one-shot",
            scenario_id="early-input-rejected",
            application=application,
            database_url=database_url,
            workflow_id=command.input.workflow_id,
            document={
                "outcome": before_delivery.result.outcome,
                "wait_state": snapshot.waits[-1].state,
            },
        )


def test_exact_approval_satisfies_one_wait_and_materializes_the_fenced_email_step() -> None:
    with renewal_context() as (database_url, application, threads):
        thread = threads.create(CreateThread(uuid4(), "email", "broker-approval"))
        actor = Actor(kind="party", identifier=str(uuid4()))
        start = StartRenewalOutreach(
            command_id=uuid4(),
            actor=actor,
            cause=Cause(kind="message", identifier=str(uuid4())),
            input=StartRenewalOutreachInput(
                workflow_id=uuid4(),
                thread_id=thread.thread_id,
                policy_id=uuid4(),
                policy_number="OM-69",
                policyholder_name="Avery Chen",
                policyholder_email="avery@example.test",
                renewal_date="2027-12-31",
                expiring_premium_cents=250_000,
            ),
        )
        application.replace_renewal_facts(
            RenewalFacts(
                policy_id=start.input.policy_id,
                policy_number=start.input.policy_number,
                policyholder_name=start.input.policyholder_name,
                policyholder_email=start.input.policyholder_email,
                renewal_date=start.input.renewal_date,
                expiring_premium_cents=start.input.expiring_premium_cents,
            )
        )
        started = application.start_renewal_outreach(start)
        application.run_workflow_worker_once(worker_id="facts")
        application.run_workflow_worker_once(worker_id="draft")
        application.run_delivery_worker_once(worker_id="delivery")
        presentation = application.renewal_approval_presentation(start.input.workflow_id)
        command = ApproveRenewalDraft(
            command_id=uuid4(),
            actor=actor,
            cause=Cause(kind="message", identifier=str(uuid4())),
            input=ApproveRenewalDraftInput(
                workflow_id=start.input.workflow_id,
                wait_id=presentation.wait_id,
                draft_id=presentation.draft_id,
                message_id=presentation.message_id,
                thread_sequence=presentation.thread_sequence,
                message_fingerprint=presentation.message_fingerprint,
                presentation_fingerprint=presentation.presentation_fingerprint,
                proposed_effect=presentation.proposed_effect,
            ),
        )

        receipt = application.approve_renewal_draft(command)
        replay = application.approve_renewal_draft(command)
        snapshot = KernelInspection(database_url=database_url).snapshot(started.result.instance_id)

        assert replay == receipt
        assert receipt.result.outcome == "approved"
        assert receipt.result.wait_id == presentation.wait_id
        assert receipt.result.effect_step_id is not None
        assert [(wait.template_key, wait.state) for wait in snapshot.waits] == [
            ("renewal_draft_approval", "satisfied")
        ]
        assert [(step.template_key, step.state) for step in snapshot.steps][-1] == (
            "send_renewal_email",
            "pending",
        )
        record_renewal_case(
            case_id="wait.one-shot",
            scenario_id="exact-one-shot",
            application=application,
            database_url=database_url,
            workflow_id=start.input.workflow_id,
            document={
                "replayed_value_identically": replay == receipt,
                "wait_state": snapshot.waits[0].state,
                "effect_step_id": str(receipt.result.effect_step_id),
            },
            worker_ids=("facts", "draft", "delivery"),
        )
        record_renewal_case(
            case_id="domain-event.atomic-correlation",
            scenario_id="approval",
            application=application,
            database_url=database_url,
            workflow_id=start.input.workflow_id,
            document={"approval_outcome": receipt.result.outcome},
        )


def test_approval_rejects_wrong_delivered_message_identity() -> None:
    with renewal_context() as (_, application, threads):
        command, actor = prepare_renewal_approval(application, threads)
        presentation = application.renewal_approval_presentation(command.input.workflow_id)
        exact_input = ApproveRenewalDraftInput(
            workflow_id=command.input.workflow_id,
            wait_id=presentation.wait_id,
            draft_id=presentation.draft_id,
            message_id=presentation.message_id,
            thread_sequence=presentation.thread_sequence,
            message_fingerprint=presentation.message_fingerprint,
            presentation_fingerprint=presentation.presentation_fingerprint,
            proposed_effect=presentation.proposed_effect,
        )

        mismatches = (
            replace(exact_input, message_id=uuid4()),
            replace(exact_input, thread_sequence=exact_input.thread_sequence + 1),
            replace(exact_input, message_fingerprint="wrong-message-content"),
        )
        outcomes = tuple(
            application.approve_renewal_draft(
                ApproveRenewalDraft(
                    command_id=uuid4(),
                    actor=actor,
                    cause=Cause(kind="message", identifier=str(uuid4())),
                    input=value,
                )
            ).result.outcome
            for value in mismatches
        )

        assert outcomes == ("stale_presentation",) * 3


def test_revision_creates_another_bounded_draft_and_exact_approval_wait() -> None:
    with renewal_context() as (database_url, application, threads):
        command, actor = prepare_renewal_approval(application, threads)
        presentation = application.renewal_approval_presentation(command.input.workflow_id)
        revision = RequestRenewalRevision(
            command_id=uuid4(),
            actor=actor,
            cause=Cause(kind="message", identifier=str(uuid4())),
            input=RequestRenewalRevisionInput(
                workflow_id=command.input.workflow_id,
                wait_id=presentation.wait_id,
                draft_id=presentation.draft_id,
                message_id=presentation.message_id,
                thread_sequence=presentation.thread_sequence,
                message_fingerprint=presentation.message_fingerprint,
                presentation_fingerprint=presentation.presentation_fingerprint,
                proposed_effect=presentation.proposed_effect,
                revision_instruction="Use a warmer opening.",
            ),
        )

        receipt = application.request_renewal_revision(revision)
        replay = application.request_renewal_revision(revision)
        draft_result = application.run_workflow_worker_once(worker_id="revision")
        assert draft_result is not None
        before_delivery = application.approve_renewal_draft(
            ApproveRenewalDraft(
                command_id=uuid4(),
                actor=actor,
                cause=Cause(kind="message", identifier=str(uuid4())),
                input=ApproveRenewalDraftInput(
                    workflow_id=command.input.workflow_id,
                    wait_id=draft_result.waits["approval"],
                    draft_id=presentation.draft_id,
                    message_id=presentation.message_id,
                    thread_sequence=presentation.thread_sequence,
                    message_fingerprint=presentation.message_fingerprint,
                    presentation_fingerprint=presentation.presentation_fingerprint,
                    proposed_effect=presentation.proposed_effect,
                ),
            )
        )
        delivery_result = application.run_delivery_worker_once(worker_id="revision-delivery")
        revised = application.renewal_approval_presentation(command.input.workflow_id)
        snapshot = KernelInspection(database_url=database_url).snapshot(
            application.start_renewal_outreach(command).result.instance_id
        )
        evidence = json.loads(application.renewal_evidence_json(command.input.workflow_id))

        assert replay == receipt
        assert receipt.result.outcome == "revision_requested"
        assert before_delivery.result.outcome == "stale_presentation"
        assert delivery_result is not None
        assert draft_result.template_key == "draft_renewal_email"
        assert revised.wait_id != presentation.wait_id
        assert revised.draft_id != presentation.draft_id
        assert "Requested revision: Use a warmer opening." in revised.proposed_effect.body
        assert [step.template_key for step in snapshot.steps].count("draft_renewal_email") == 2
        assert [wait.state for wait in snapshot.waits] == ["satisfied", "unsatisfied"]
        step_states = evidence["outcomes"]["step_states"]
        assert set(step_states) == set(evidence["correlations"]["step_ids"])
        assert [item["template_key"] for item in step_states.values()].count(
            "draft_renewal_email"
        ) == 2
        record_renewal_case(
            case_id="domain-event.atomic-correlation",
            scenario_id="revision",
            application=application,
            database_url=database_url,
            workflow_id=command.input.workflow_id,
            document={
                "revision_outcome": receipt.result.outcome,
                "draft_count": 2,
                "wait_states": [wait.state for wait in snapshot.waits],
            },
            worker_ids=("revision", "revision-delivery"),
        )

        cross_wired = application.approve_renewal_draft(
            ApproveRenewalDraft(
                command_id=uuid4(),
                actor=actor,
                cause=Cause(kind="message", identifier=str(uuid4())),
                input=ApproveRenewalDraftInput(
                    workflow_id=command.input.workflow_id,
                    wait_id=revised.wait_id,
                    draft_id=presentation.draft_id,
                    message_id=presentation.message_id,
                    thread_sequence=presentation.thread_sequence,
                    message_fingerprint=presentation.message_fingerprint,
                    presentation_fingerprint=presentation.presentation_fingerprint,
                    proposed_effect=presentation.proposed_effect,
                ),
            )
        )
        assert cross_wired.result.outcome == "stale_presentation"
