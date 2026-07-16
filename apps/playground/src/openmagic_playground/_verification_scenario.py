"""Deterministic protected-renewal verification demonstration."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from example_insurance.renewals import (
    ExampleInsurance,
    ProvisionVerificationAuthority,
    ProvisionVerificationAuthorityInput,
    RequestProtectedRenewalDetails,
    RequestProtectedRenewalDetailsInput,
    SubmitVerificationCode,
    SubmitVerificationCodeInput,
)
from openmagic_runtime.commands import Actor, Cause
from openmagic_runtime.threads import CreateThread, ThreadStore

from openmagic_playground._scenario_support import (
    RenewalFixture,
    approve_renewal,
    create_renewal_fixture,
    scenario_database,
    scenario_id,
)
from openmagic_playground.deployment_observation import observe_postgres
from openmagic_playground.responses import (
    PlaygroundApplicationCorrelations,
    PlaygroundCorrelations,
    PlaygroundRuntimeCorrelations,
    PostgresDeploymentObservation,
    VerificationDemonstrationObservation,
    VerificationDemonstrationResponse,
)


@dataclass(frozen=True)
class VerificationChallengePhase:
    fixture: RenewalFixture
    protected_command: RequestProtectedRenewalDetails
    challenge_id: UUID
    identifier_thread_id: UUID


@dataclass(frozen=True)
class VerificationCompletionPhase:
    verification_outcome: Literal["verified"]
    protected_outcome: Literal["authorized"]


def _issue_verification_challenge(database_url: str) -> VerificationChallengePhase:
    application = ExampleInsurance(
        database_url=database_url,
        verification_code_secret=b"synthetic-playground-verification",
    )
    application.prepare()
    threads = ThreadStore(database_url=database_url)
    fixture = create_renewal_fixture(application, threads, "verification")
    approval_grant_id = approve_renewal(fixture, "verification")
    identifier_thread_id = scenario_id("verification", "identifier-thread")
    email = "verification-identifier@example.test"
    threads.create(CreateThread(identifier_thread_id, "email", email))
    application.provision_verification_authority(
        ProvisionVerificationAuthority(
            command_id=scenario_id("verification", "provision-command"),
            actor=Actor("system", "synthetic-playground"),
            cause=Cause("command", str(scenario_id("verification", "provision-cause"))),
            input=ProvisionVerificationAuthorityInput(
                party_id=UUID(fixture.actor.identifier),
                organization_party_id=scenario_id("verification", "organization"),
                workflow_id=fixture.renewal.input.workflow_id,
                email=email,
                delivery_thread_id=identifier_thread_id,
            ),
        )
    )
    protected = RequestProtectedRenewalDetails(
        command_id=scenario_id("verification", "protected-command"),
        actor=fixture.actor,
        cause=Cause("message", str(scenario_id("verification", "protected-cause"))),
        input=RequestProtectedRenewalDetailsInput(
            workflow_id=fixture.renewal.input.workflow_id,
            thread_id=fixture.renewal.input.thread_id,
            purpose="renewal.read_approved_details",
            approval_grant_id=approval_grant_id,
        ),
    )
    receipt = application.request_protected_renewal_details(protected)
    challenge_id = receipt.result.challenge_id
    if challenge_id is None:
        raise AssertionError("verification demonstration did not issue a Challenge")
    return VerificationChallengePhase(
        fixture=fixture,
        protected_command=protected,
        challenge_id=challenge_id,
        identifier_thread_id=identifier_thread_id,
    )


def _complete_verification(phase: VerificationChallengePhase) -> VerificationCompletionPhase:
    application = phase.fixture.application
    application.run_workflow_worker_once(worker_id="verification-playground")
    application.run_delivery_worker_once(worker_id="verification-playground-delivery")
    content = phase.fixture.threads.read(phase.identifier_thread_id).messages[-1].content
    match = re.search(r"\b(\d{6})\b", content)
    if match is None:
        raise AssertionError("verification demonstration did not deliver its code")
    receipt = application.submit_verification_code(
        SubmitVerificationCode(
            command_id=scenario_id("verification", "submit-command"),
            actor=phase.fixture.actor,
            cause=Cause("message", str(scenario_id("verification", "submit-cause"))),
            input=SubmitVerificationCodeInput(
                challenge_id=phase.challenge_id,
                protected_command_id=phase.protected_command.command_id,
                workflow_id=phase.fixture.renewal.input.workflow_id,
                thread_id=phase.fixture.renewal.input.thread_id,
                purpose="renewal.read_approved_details",
                code=match.group(1),
            ),
        )
    )
    if receipt.result.verification_outcome != "verified":
        raise AssertionError("verification demonstration did not verify")
    if receipt.result.protected_outcome != "authorized":
        raise AssertionError("verification demonstration was not authorized")
    return VerificationCompletionPhase(
        verification_outcome="verified",
        protected_outcome="authorized",
    )


def run_verification_scenario() -> VerificationDemonstrationResponse:
    with scenario_database("verification") as database_url:
        challenge = _issue_verification_challenge(database_url)
        completion = _complete_verification(challenge)
        return VerificationDemonstrationResponse(
            correlations=PlaygroundCorrelations(
                runtime=PlaygroundRuntimeCorrelations(
                    command_ids=(
                        challenge.protected_command.command_id,
                        scenario_id("verification", "submit-command"),
                    ),
                    workflow_ids=(challenge.fixture.renewal.input.workflow_id,),
                ),
                application=PlaygroundApplicationCorrelations(
                    thread_ids=(
                        challenge.fixture.renewal.input.thread_id,
                        challenge.identifier_thread_id,
                    ),
                    verification_challenge_ids=(challenge.challenge_id,),
                ),
            ),
            observation=VerificationDemonstrationObservation(
                verification_outcome=completion.verification_outcome,
                protected_outcome=completion.protected_outcome,
                session_count=1,
            ),
            postgres_deployments=(
                PostgresDeploymentObservation.model_validate(observe_postgres(database_url)),
            ),
        )


__all__: list[str] = []
