"""Typed public-API observations for deterministic release cases and demonstrations."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from example_insurance.migrations import apply_migrations
from example_insurance.renewals import (
    ExampleInsurance,
    SubmitVerificationCode,
    SubmitVerificationCodeInput,
)
from openmagic_playground.renewal_observation import decode_renewal_projection
from openmagic_runtime.commands import Cause
from openmagic_runtime.threads import ThreadStore

from openmagic_evals.evidence.contracts import (
    AgentCorrelations,
    ApplicationCorrelations,
    Correlations,
    ProcessCorrelations,
    ProviderCorrelations,
    RuntimeCorrelations,
)
from openmagic_evals.evidence.core_models import canonical_digest
from openmagic_evals.evidence.inspection import EvidenceInspection
from openmagic_evals.harness import (
    LocalEmailProvider,
    approve_renewal,
    issue_verification_challenge,
    prepare_renewal_approval,
    renewal_context,
)
from openmagic_evals.harness._postgres import postgres_container


@dataclass(frozen=True)
class DeterministicObservation:
    correlations: Correlations
    document: dict[str, object]

    @property
    def digest(self) -> str:
        return canonical_digest(self.document)


def collect_renewal_observation(working_directory: Path) -> DeterministicObservation:
    """Observe one complete synthetic renewal through public application interfaces."""

    with (
        LocalEmailProvider(working_directory=working_directory / "provider") as provider,
        postgres_container(database_name=f"openmagic_test_{uuid4().hex}") as postgres,
    ):
        provider.configure(behaviors=("success",))
        provider_request_baseline = provider.request_count()
        database_url = postgres.get_connection_url(driver=None)
        apply_migrations(database_url)
        application = ExampleInsurance(database_url=database_url, email_provider_url=provider.url)
        application.prepare()
        command, actor = prepare_renewal_approval(
            application,
            ThreadStore(database_url=database_url),
        )
        approve_renewal(application, command, actor)
        result = application.run_workflow_worker_once(worker_id="synthetic-email")
        if result is None:
            raise AssertionError("renewal observation did not execute its local effect")
        projection = decode_renewal_projection(
            application.renewal_evidence_json(command.input.workflow_id)
        )
        values = projection.correlations
        outcomes = projection.outcomes
        trace_event_ids, delivery_attempt_ids = EvidenceInspection(database_url).renewal_demo_ids(
            values.instance_id
        )
        correlations = Correlations(
            runtime=RuntimeCorrelations(
                command_ids=(values.command_id,),
                workflow_ids=(values.workflow_id,),
                instance_ids=(values.instance_id,),
                step_ids=values.step_ids,
                attempt_ids=values.attempt_ids,
                wait_ids=outcomes.approval_wait_ids,
                signal_ids=values.signal_ids,
                trace_event_ids=trace_event_ids,
            ),
            application=ApplicationCorrelations(
                thread_ids=(values.thread_id,),
                message_ids=values.message_ids,
                domain_event_ids=values.domain_event_ids,
                delivery_ids=values.delivery_ids,
                delivery_attempt_ids=delivery_attempt_ids,
                external_effect_ids=values.logical_effect_ids,
                approval_grant_ids=values.approval_grant_ids,
            ),
            agent=AgentCorrelations(agent_run_ids=values.agent_run_ids),
            process=ProcessCorrelations(
                worker_ids=("synthetic-email",), process_ids=(provider.pid,)
            ),
            provider=ProviderCorrelations(
                provider_request_ids=tuple(
                    item.provider_request_id
                    for item in outcomes.effect_evidence
                    if item.provider_request_id is not None
                )
            ),
        )
        document = {
            "workflow_lifecycle": outcomes.workflow_lifecycle,
            "instance_state": outcomes.instance_state,
            "completion_event_count": outcomes.completion_event_count,
            "provider_request_count": provider.request_count() - provider_request_baseline,
        }
    if document != {
        "workflow_lifecycle": "completed",
        "instance_state": "closed",
        "completion_event_count": 1,
        "provider_request_count": 1,
    }:
        raise AssertionError("renewal observation did not reach its accepted terminal outcome")
    return DeterministicObservation(correlations=correlations, document=document)


def collect_verification_observation() -> DeterministicObservation:
    """Observe deterministic verification reuse through public application interfaces."""

    with renewal_context(verification_code_secret=b"synthetic-demo-verification") as (
        database_url,
        application,
        threads,
    ):
        scenario = issue_verification_challenge(application, threads)
        challenge_id = scenario.challenge_receipt.result.challenge_id
        if challenge_id is None or scenario.code is None:
            raise AssertionError("verification observation did not issue a Challenge")
        receipt = application.submit_verification_code(
            SubmitVerificationCode(
                command_id=uuid4(),
                actor=scenario.actor,
                cause=Cause("message", str(uuid4())),
                input=SubmitVerificationCodeInput(
                    challenge_id=challenge_id,
                    protected_command_id=scenario.protected_command.command_id,
                    workflow_id=scenario.renewal.input.workflow_id,
                    thread_id=scenario.renewal.input.thread_id,
                    purpose="renewal.read_approved_details",
                    code=scenario.code,
                ),
            )
        )
        application.run_delivery_worker_once(worker_id="synthetic-protected-delivery")
        verification = EvidenceInspection(database_url).verification_demo(challenge_id)
        if verification is None or receipt.result.verification_outcome != "verified":
            raise AssertionError("verification observation did not verify")
        correlations = Correlations(
            runtime=RuntimeCorrelations(
                command_ids=(scenario.protected_command.command_id, receipt.command_id),
                workflow_ids=(scenario.renewal.input.workflow_id, verification.workflow_id),
                instance_ids=(verification.instance_id,),
                step_ids=tuple(step_id for step_id, _ in verification.step_attempt_ids),
                attempt_ids=tuple(attempt_id for _, attempt_id in verification.step_attempt_ids),
            ),
            application=ApplicationCorrelations(
                thread_ids=(scenario.renewal.input.thread_id, scenario.identifier_thread_id),
                verification_challenge_ids=(challenge_id,),
                verification_session_ids=(verification.session_id,),
            ),
            process=ProcessCorrelations(worker_ids=("synthetic-protected-delivery",)),
        )
        document = {
            "verification_outcome": receipt.result.verification_outcome,
            "protected_outcome": receipt.result.protected_outcome,
            "session_count": 1,
        }
    return DeterministicObservation(correlations=correlations, document=document)


__all__ = [
    "DeterministicObservation",
    "collect_renewal_observation",
    "collect_verification_observation",
]
