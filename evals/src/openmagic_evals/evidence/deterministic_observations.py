"""Typed public-API observations for deterministic release cases and demonstrations."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4

from example_insurance.migrations import apply_migrations
from example_insurance.renewals import (
    ExampleInsurance,
    SubmitVerificationCode,
    SubmitVerificationCodeInput,
)
from openmagic_runtime.commands import Cause
from openmagic_runtime.threads import ThreadStore

from openmagic_evals.evidence.contracts import Correlations, merge_correlations
from openmagic_evals.evidence.inspection import EvidenceInspection
from openmagic_evals.harness import (
    LocalEmailProvider,
    approve_renewal,
    issue_verification_challenge,
    prepare_renewal_approval,
    renewal_context,
)
from openmagic_evals.harness._postgres import postgres_container


def _digest(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _ids(value: object) -> tuple[UUID, ...]:
    return tuple(UUID(str(item)) for item in value) if isinstance(value, list) else ()


@dataclass(frozen=True)
class DeterministicObservation:
    correlations: Correlations
    document: dict[str, object]

    @property
    def digest(self) -> str:
        return _digest(self.document)


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
        evidence = json.loads(application.renewal_evidence_json(command.input.workflow_id))
        values = evidence["correlations"]
        outcomes = evidence["outcomes"]
        trace_event_ids, delivery_attempt_ids = EvidenceInspection(database_url).renewal_demo_ids(
            UUID(values["instance_id"])
        )
        correlations = Correlations(
            command_ids=(UUID(values["command_id"]),),
            workflow_ids=(UUID(values["workflow_id"]),),
            instance_ids=(UUID(values["instance_id"]),),
            step_ids=_ids(values["step_ids"]),
            attempt_ids=_ids(values["attempt_ids"]),
            wait_ids=_ids(outcomes["approval_wait_ids"]),
            signal_ids=_ids(values["signal_ids"]),
            trace_event_ids=trace_event_ids,
            thread_ids=(UUID(values["thread_id"]),),
            message_ids=_ids(values["message_ids"]),
            agent_run_ids=_ids(values["agent_run_ids"]),
            domain_event_ids=_ids(values["domain_event_ids"]),
            delivery_ids=_ids(values["delivery_ids"]),
            delivery_attempt_ids=delivery_attempt_ids,
            external_effect_ids=_ids(values["logical_effect_ids"]),
            approval_grant_ids=_ids(values["approval_grant_ids"]),
            worker_ids=("synthetic-email",),
            process_ids=(provider.pid,),
            provider_request_ids=tuple(
                str(item["provider_request_id"])
                for item in outcomes["effect_evidence"]
                if item["provider_request_id"] is not None
            ),
        )
        document = {
            "workflow_lifecycle": outcomes["workflow_lifecycle"],
            "instance_state": outcomes["instance_state"],
            "completion_event_count": outcomes["completion_event_count"],
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
            command_ids=(scenario.protected_command.command_id, receipt.command_id),
            workflow_ids=(scenario.renewal.input.workflow_id, verification.workflow_id),
            instance_ids=(verification.instance_id,),
            step_ids=tuple(step_id for step_id, _ in verification.step_attempt_ids),
            attempt_ids=tuple(attempt_id for _, attempt_id in verification.step_attempt_ids),
            thread_ids=(scenario.renewal.input.thread_id, scenario.identifier_thread_id),
            verification_challenge_ids=(challenge_id,),
            verification_session_ids=(verification.session_id,),
            worker_ids=("synthetic-protected-delivery",),
        )
        document = {
            "verification_outcome": receipt.result.verification_outcome,
            "protected_outcome": receipt.result.protected_outcome,
            "session_count": 1,
        }
    return DeterministicObservation(correlations=correlations, document=document)


def release_observations(working_directory: Path) -> dict[str, DeterministicObservation]:
    """Build family-specific projections without consuming demonstration artifacts."""

    renewal = collect_renewal_observation(working_directory / "renewal")
    verification = collect_verification_observation()
    combined = DeterministicObservation(
        correlations=merge_correlations((renewal.correlations, verification.correlations)),
        document={
            "renewal": renewal.document,
            "verification": verification.document,
        },
    )
    families = {
        "acknowledgement": renewal,
        "completion": renewal,
        "definition": combined,
        "domain_event": renewal,
        "exact_thread_delivery": renewal,
        "external_effect": renewal,
        "executor": combined,
        "lease": renewal,
        "recovery": renewal,
        "replay": verification,
        "retry": renewal,
        "route": renewal,
        "signal": renewal,
        "transaction": renewal,
        "trace_completeness": combined,
        "wait": renewal,
    }
    return families


__all__ = [
    "DeterministicObservation",
    "collect_renewal_observation",
    "collect_verification_observation",
    "release_observations",
]
