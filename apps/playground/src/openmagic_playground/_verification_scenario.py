"""Deterministic protected-renewal verification demonstration."""

from __future__ import annotations

import re
from uuid import UUID

import psycopg
from example_insurance.renewal_evidence import read_renewal_evidence
from example_insurance.renewals import (
    ExampleInsurance,
    ProvisionVerificationAuthority,
    ProvisionVerificationAuthorityInput,
    RequestProtectedRenewalDetails,
    RequestProtectedRenewalDetailsInput,
    SubmitVerificationCode,
    SubmitVerificationCodeInput,
)
from example_insurance.verification_evidence import VerificationEvidenceReader
from openmagic_runtime.commands import Actor, Cause
from openmagic_runtime.evidence import RuntimeEvidenceReader
from openmagic_runtime.threads import CreateThread, ThreadStore

from openmagic_playground._scenario_support import (
    approve_renewal,
    create_renewal_fixture,
    scenario_database,
    scenario_id,
)
from openmagic_playground._verification_observation import (
    VerificationChallengePhase,
    VerificationCompletionPhase,
    verification_correlations,
    verification_durable_chain,
)
from openmagic_playground.deployment_observation import observe_postgres
from openmagic_playground.renewal_observation import decode_renewal_projection
from openmagic_playground.responses import PostgresDeploymentObservation
from openmagic_playground.verification_response import (
    VerificationDemonstrationObservation,
    VerificationDemonstrationResponse,
)


def _issue_verification_challenge(database_url: str) -> VerificationChallengePhase:
    application = ExampleInsurance(
        database_url=database_url,
        verification_code_secret=b"synthetic-playground-verification",
    )
    application.prepare()
    threads = ThreadStore(database_url=database_url)
    fixture = create_renewal_fixture(application, threads, "verification")
    approval = approve_renewal(fixture, "verification")
    identifier_thread_id = scenario_id("verification", "identifier-thread")
    email = "verification-identifier@example.test"
    threads.create(CreateThread(identifier_thread_id, "email", email))
    provision_command_id = scenario_id("verification", "provision-command")
    application.provision_verification_authority(
        ProvisionVerificationAuthority(
            command_id=provision_command_id,
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
            approval_grant_id=approval.approval_grant_id,
        ),
    )
    receipt = application.request_protected_renewal_details(protected)
    challenge_id = receipt.result.challenge_id
    verification_workflow_id = receipt.result.verification_workflow_id
    verification_instance_id = receipt.result.verification_instance_id
    if challenge_id is None or verification_workflow_id is None or verification_instance_id is None:
        raise AssertionError("verification demonstration did not issue a Challenge")
    return VerificationChallengePhase(
        fixture=fixture,
        protected_command=protected,
        challenge_id=challenge_id,
        identifier_thread_id=identifier_thread_id,
        approval_grant_id=approval.approval_grant_id,
        approval_command_id=approval.command_id,
        provision_command_id=provision_command_id,
        verification_workflow_id=verification_workflow_id,
        verification_instance_id=verification_instance_id,
    )


def _complete_verification(
    database_url: str, phase: VerificationChallengePhase
) -> VerificationCompletionPhase:
    application = phase.fixture.application
    application.run_workflow_worker_once(worker_id="verification-playground")
    challenge_delivery = application.run_delivery_worker_once(
        worker_id="verification-playground-delivery"
    )
    if challenge_delivery is None:
        raise AssertionError("verification demonstration did not deliver its Challenge")
    content = phase.fixture.threads.read(phase.identifier_thread_id).messages[-1].content
    match = re.search(r"\b(\d{6})\b", content)
    if match is None:
        raise AssertionError("verification demonstration did not deliver its code")
    submit_command_id = scenario_id("verification", "submit-command")
    receipt = application.submit_verification_code(
        SubmitVerificationCode(
            command_id=submit_command_id,
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
    session_id = receipt.result.session_id
    authorized_delivery_id = receipt.result.authorized_delivery_id
    if session_id is None or authorized_delivery_id is None:
        raise AssertionError("verification demonstration omitted its durable authorization IDs")
    authorized_delivery = application.run_delivery_worker_once(
        worker_id="verification-playground-delivery"
    )
    if authorized_delivery is None or authorized_delivery.delivery_id != authorized_delivery_id:
        raise AssertionError("verification demonstration did not deliver its protected result")
    with psycopg.connect(database_url) as connection, connection.transaction():
        application_evidence = VerificationEvidenceReader(connection).accepted_challenge(
            phase.challenge_id
        )
        renewal_projection = decode_renewal_projection(
            read_renewal_evidence(
                connection,
                phase.fixture.renewal.input.workflow_id,
            ).to_json()
        )
        reader = RuntimeEvidenceReader(connection)
        renewal_runtime = reader.instance(renewal_projection.correlations.instance_id)
        initial_delivery_evidence = reader.delivery(phase.fixture.initial_delivery.delivery_id)
    if (
        application_evidence.challenge_delivery.delivery_id != challenge_delivery.delivery_id
        or application_evidence.authorized_delivery.delivery_id != authorized_delivery.delivery_id
        or application_evidence.session_id != session_id
        or application_evidence.submit_command_id != submit_command_id
    ):
        raise AssertionError("verification receipts disagree with the durable evidence snapshot")
    return VerificationCompletionPhase(
        verification_outcome="verified",
        protected_outcome="authorized",
        application=application_evidence,
        renewal_runtime=renewal_runtime,
        initial_delivery=initial_delivery_evidence,
        renewal_projection=renewal_projection,
    )


def run_verification_scenario() -> VerificationDemonstrationResponse:
    with scenario_database("verification") as database_url:
        challenge = _issue_verification_challenge(database_url)
        completion = _complete_verification(database_url, challenge)
        durable_chain = verification_durable_chain(challenge, completion)
        return VerificationDemonstrationResponse(
            durable_chain=durable_chain,
            correlations=verification_correlations(challenge, completion, durable_chain),
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
