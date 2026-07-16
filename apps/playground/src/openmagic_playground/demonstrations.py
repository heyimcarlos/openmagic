"""Executable synthetic demonstrations owned by the playground application."""

from __future__ import annotations

import re
import socket
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid5

from example_insurance.renewals import (
    ApproveRenewalDraft,
    ApproveRenewalDraftInput,
    ExampleInsurance,
    ProvisionVerificationAuthority,
    ProvisionVerificationAuthorityInput,
    RenewalFacts,
    RequestProtectedRenewalDetails,
    RequestProtectedRenewalDetailsInput,
    StartRenewalOutreach,
    StartRenewalOutreachInput,
    SubmitVerificationCode,
    SubmitVerificationCodeInput,
)
from openmagic_runtime.commands import Actor, Cause
from openmagic_runtime.threads import CreateThread, ThreadStore
from testcontainers.postgres import PostgresContainer

from openmagic_playground.deployment import POSTGRES_IMAGE, PlaygroundDeployment
from openmagic_playground.deployment_observation import observe_postgres
from openmagic_playground.renewal_observation import RenewalProjection, decode_renewal_projection
from openmagic_playground.reset import mark_synthetic_deployment
from openmagic_playground.responses import (
    ControlExerciseResponse,
    ExercisedControls,
    FailureScenarioObservation,
    PlaygroundAgentCorrelations,
    PlaygroundApplicationCorrelations,
    PlaygroundCorrelations,
    PlaygroundProcessCorrelations,
    PlaygroundProviderCorrelations,
    PlaygroundRuntimeCorrelations,
    PlaygroundScenarioCoverage,
    PostgresDeploymentObservation,
    RenewalDemonstrationObservation,
    RenewalDemonstrationResponse,
    SafeRenewalBoundaryObservation,
    VerificationDemonstrationObservation,
    VerificationDemonstrationResponse,
)
from openmagic_playground.synthetic_provider import SyntheticEmailProvider

_DEMO_NAMESPACE = UUID("d21783e3-7912-45d6-b3b2-289549e5d3e5")


def _id(scenario: str, role: str) -> UUID:
    return uuid5(_DEMO_NAMESPACE, f"{scenario}:{role}")


def _database(scenario: str) -> tuple[PostgresContainer, str]:
    container = PostgresContainer(
        POSTGRES_IMAGE,
        username="openmagic",
        password="openmagic",
        dbname=f"openmagic_playground_{scenario}_{_id(scenario, 'database').hex}",
        driver=None,
    )
    try:
        container.start()
        return container, container.get_connection_url(driver=None)
    except BaseException as startup_error:
        try:
            container.stop()
        except BaseException as cleanup_error:
            raise BaseExceptionGroup(
                "playground database startup and cleanup failed",
                [startup_error, cleanup_error],
            ) from startup_error
        raise


def _renewal_fixture(
    application: ExampleInsurance, threads: ThreadStore, scenario: str
) -> tuple[StartRenewalOutreach, Actor]:
    thread_id = _id(scenario, "thread")
    actor = Actor("party", str(_id(scenario, "actor")))
    threads.create(CreateThread(thread_id, "email", f"{scenario}@example.test"))
    command = StartRenewalOutreach(
        command_id=_id(scenario, "command"),
        actor=actor,
        cause=Cause("message", str(_id(scenario, "cause"))),
        input=StartRenewalOutreachInput(
            workflow_id=_id(scenario, "workflow"),
            thread_id=thread_id,
            policy_id=_id(scenario, "policy"),
            policy_number="OM-SYNTHETIC-71",
            policyholder_name="Synthetic Playground Party",
            policyholder_email=f"{scenario}@example.test",
            renewal_date="2028-12-31",
            expiring_premium_cents=171_000,
        ),
    )
    application.replace_renewal_facts(
        RenewalFacts(
            policy_id=command.input.policy_id,
            policy_number=command.input.policy_number,
            policyholder_name=command.input.policyholder_name,
            policyholder_email=command.input.policyholder_email,
            renewal_date=command.input.renewal_date,
            expiring_premium_cents=command.input.expiring_premium_cents,
        )
    )
    application.start_renewal_outreach(command)
    application.run_workflow_worker_once(worker_id=f"{scenario}-facts")
    application.run_workflow_worker_once(worker_id=f"{scenario}-draft")
    application.run_delivery_worker_once(worker_id=f"{scenario}-delivery")
    return command, actor


def _safe_renewal_result(
    application: ExampleInsurance,
    threads: ThreadStore,
    command: StartRenewalOutreach,
) -> tuple[PlaygroundCorrelations, SafeRenewalBoundaryObservation]:
    projection = decode_renewal_projection(
        application.renewal_evidence_json(command.input.workflow_id)
    )
    values = projection.correlations
    outcomes = projection.outcomes
    messages = threads.read(command.input.thread_id).messages
    if (
        outcomes.approval_wait_state != "unsatisfied"
        or outcomes.external_email_effect_count != 0
        or len(messages) != 1
    ):
        raise AssertionError("synthetic playground renewal left its safe approval boundary")
    return (
        PlaygroundCorrelations(
            runtime=PlaygroundRuntimeCorrelations(
                command_ids=(values.command_id,),
                workflow_ids=(values.workflow_id,),
                instance_ids=(values.instance_id,),
                step_ids=values.step_ids,
                attempt_ids=values.attempt_ids,
                wait_ids=outcomes.approval_wait_ids,
            ),
            application=PlaygroundApplicationCorrelations(
                thread_ids=(values.thread_id,),
                message_ids=values.message_ids,
                domain_event_ids=values.domain_event_ids,
                delivery_ids=values.delivery_ids,
            ),
            agent=PlaygroundAgentCorrelations(agent_run_ids=values.agent_run_ids),
        ),
        SafeRenewalBoundaryObservation(
            approval_wait_state="unsatisfied",
            external_email_effect_count=0,
            instance_state="open",
            message_count=1,
            workflow_lifecycle="active",
        ),
    )


def _approve_renewal(
    application: ExampleInsurance,
    renewal: StartRenewalOutreach,
    actor: Actor,
    scenario: str,
) -> None:
    presentation = application.renewal_approval_presentation(renewal.input.workflow_id)
    application.approve_renewal_draft(
        ApproveRenewalDraft(
            command_id=_id(scenario, "approval-command"),
            actor=actor,
            cause=Cause("message", str(_id(scenario, "approval-cause"))),
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


def _correlations(
    projection: RenewalProjection,
    *,
    worker_ids: tuple[str, ...] = (),
    process_ids: tuple[int, ...] = (),
    provider_request_ids: tuple[str, ...] = (),
) -> PlaygroundCorrelations:
    values = projection.correlations
    outcomes = projection.outcomes
    return PlaygroundCorrelations(
        runtime=PlaygroundRuntimeCorrelations(
            command_ids=(values.command_id,),
            workflow_ids=(values.workflow_id,),
            instance_ids=(values.instance_id,),
            step_ids=values.step_ids,
            attempt_ids=values.attempt_ids,
            wait_ids=outcomes.approval_wait_ids,
            signal_ids=values.signal_ids,
        ),
        application=PlaygroundApplicationCorrelations(
            thread_ids=(values.thread_id,),
            message_ids=values.message_ids,
            domain_event_ids=values.domain_event_ids,
            delivery_ids=values.delivery_ids,
            external_effect_ids=values.logical_effect_ids,
            approval_grant_ids=values.approval_grant_ids,
        ),
        agent=PlaygroundAgentCorrelations(agent_run_ids=values.agent_run_ids),
        process=PlaygroundProcessCorrelations(
            worker_ids=worker_ids,
            process_ids=process_ids,
        ),
        provider=PlaygroundProviderCorrelations(provider_request_ids=provider_request_ids),
    )


def run_renewal_demonstration(
    *,
    working_directory: Path,
    execute_approved_local_effect: Literal[True],
) -> RenewalDemonstrationResponse:
    """Run one explicitly approved effect through an owned local provider."""

    if execute_approved_local_effect is not True:
        raise ValueError("renewal demo requires explicit approved local effect execution")
    with SyntheticEmailProvider(
        working_directory=working_directory / "provider",
        behavior="success",
    ) as provider:
        container, database_url = _database("renewal")
        try:
            from example_insurance.migrations import apply_migrations

            apply_migrations(database_url)
            mark_synthetic_deployment(database_url)
            application = ExampleInsurance(
                database_url=database_url,
                email_provider_url=provider.url,
            )
            application.prepare()
            threads = ThreadStore(database_url=database_url)
            command, actor = _renewal_fixture(application, threads, "renewal")
            _approve_renewal(application, command, actor, "renewal")
            completed = application.run_workflow_worker_once(worker_id="playground-email")
            if completed is None:
                raise AssertionError("approved local effect was not attempted")
            projection = decode_renewal_projection(
                application.renewal_evidence_json(command.input.workflow_id)
            )
            outcomes = projection.outcomes
            requests = provider.requests()
            if (
                outcomes.approval_wait_state != "satisfied"
                or outcomes.external_email_effect_count != 1
                or outcomes.external_effect_certainties != ("applied",)
                or outcomes.instance_state != "closed"
                or outcomes.workflow_lifecycle != "completed"
                or outcomes.completion_event_count != 1
                or len(requests) != 1
            ):
                raise AssertionError("synthetic renewal did not complete its approved local effect")
            return RenewalDemonstrationResponse(
                correlations=_correlations(
                    projection,
                    worker_ids=("playground-email",),
                    process_ids=(provider.pid,),
                    provider_request_ids=(requests[0].provider_request_id,),
                ),
                observation=RenewalDemonstrationObservation(
                    approval_wait_state="satisfied",
                    external_email_effect_count=1,
                    external_effect_certainties=("applied",),
                    instance_state="closed",
                    message_count=1,
                    workflow_lifecycle="completed",
                    completion_event_count=1,
                    provider_request_count=1,
                    approved_local_execution=execute_approved_local_effect,
                ),
                postgres_deployments=(
                    PostgresDeploymentObservation.model_validate(observe_postgres(database_url)),
                ),
            )
        finally:
            container.stop()


def run_verification_demonstration() -> VerificationDemonstrationResponse:
    """Run deterministic verification without an external provider."""

    container, database_url = _database("verification")
    try:
        from example_insurance.migrations import apply_migrations

        apply_migrations(database_url)
        mark_synthetic_deployment(database_url)
        application = ExampleInsurance(
            database_url=database_url,
            verification_code_secret=b"synthetic-playground-verification",
        )
        application.prepare()
        threads = ThreadStore(database_url=database_url)
        renewal, actor = _renewal_fixture(application, threads, "verification")
        presentation = application.renewal_approval_presentation(renewal.input.workflow_id)
        approval = application.approve_renewal_draft(
            ApproveRenewalDraft(
                command_id=_id("verification", "approval-command"),
                actor=actor,
                cause=Cause("message", str(_id("verification", "approval-cause"))),
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
            raise AssertionError("verification demonstration lacks approval authority")
        identifier_thread_id = _id("verification", "identifier-thread")
        email = "verification-identifier@example.test"
        threads.create(CreateThread(identifier_thread_id, "email", email))
        application.provision_verification_authority(
            ProvisionVerificationAuthority(
                command_id=_id("verification", "provision-command"),
                actor=Actor("system", "synthetic-playground"),
                cause=Cause("command", str(_id("verification", "provision-cause"))),
                input=ProvisionVerificationAuthorityInput(
                    party_id=UUID(actor.identifier),
                    organization_party_id=_id("verification", "organization"),
                    workflow_id=renewal.input.workflow_id,
                    email=email,
                    delivery_thread_id=identifier_thread_id,
                ),
            )
        )
        protected = RequestProtectedRenewalDetails(
            command_id=_id("verification", "protected-command"),
            actor=actor,
            cause=Cause("message", str(_id("verification", "protected-cause"))),
            input=RequestProtectedRenewalDetailsInput(
                workflow_id=renewal.input.workflow_id,
                thread_id=renewal.input.thread_id,
                purpose="renewal.read_approved_details",
                approval_grant_id=approval.result.approval_grant_id,
            ),
        )
        challenge = application.request_protected_renewal_details(protected)
        challenge_id = challenge.result.challenge_id
        if challenge_id is None:
            raise AssertionError("verification demonstration did not issue a Challenge")
        application.run_workflow_worker_once(worker_id="verification-playground")
        application.run_delivery_worker_once(worker_id="verification-playground-delivery")
        content = threads.read(identifier_thread_id).messages[-1].content
        match = re.search(r"\b(\d{6})\b", content)
        if match is None:
            raise AssertionError("verification demonstration did not deliver its code")
        receipt = application.submit_verification_code(
            SubmitVerificationCode(
                command_id=_id("verification", "submit-command"),
                actor=actor,
                cause=Cause("message", str(_id("verification", "submit-cause"))),
                input=SubmitVerificationCodeInput(
                    challenge_id=challenge_id,
                    protected_command_id=protected.command_id,
                    workflow_id=renewal.input.workflow_id,
                    thread_id=renewal.input.thread_id,
                    purpose="renewal.read_approved_details",
                    code=match.group(1),
                ),
            )
        )
        if receipt.result.verification_outcome != "verified":
            raise AssertionError("verification demonstration did not verify")
        if receipt.result.protected_outcome != "authorized":
            raise AssertionError("verification demonstration was not authorized")
        return VerificationDemonstrationResponse(
            correlations=PlaygroundCorrelations(
                runtime=PlaygroundRuntimeCorrelations(
                    command_ids=(protected.command_id, receipt.command_id),
                    workflow_ids=(renewal.input.workflow_id,),
                ),
                application=PlaygroundApplicationCorrelations(
                    thread_ids=(renewal.input.thread_id, identifier_thread_id),
                    verification_challenge_ids=(challenge_id,),
                ),
            ),
            observation=VerificationDemonstrationObservation(
                verification_outcome=receipt.result.verification_outcome,
                protected_outcome=receipt.result.protected_outcome,
                session_count=1,
            ),
            postgres_deployments=(
                PostgresDeploymentObservation.model_validate(observe_postgres(database_url)),
            ),
        )
    finally:
        container.stop()


def _failure_scenario(
    *,
    database_url: str,
    scenario: Literal["intentional-failure", "disconnected-provider"],
    provider_url: str,
    provider_connected: bool,
    provider_process_id: int | None = None,
) -> tuple[FailureScenarioObservation, PlaygroundCorrelations]:
    application = ExampleInsurance(database_url=database_url, email_provider_url=provider_url)
    application.prepare()
    threads = ThreadStore(database_url=database_url)
    renewal, actor = _renewal_fixture(application, threads, scenario)
    _approve_renewal(application, renewal, actor, scenario)
    result = application.run_workflow_worker_once(worker_id=f"{scenario}-email")
    if result is None:
        raise AssertionError(f"{scenario} did not exercise its provider boundary")
    projection = decode_renewal_projection(
        application.renewal_evidence_json(renewal.input.workflow_id)
    )
    outcomes = projection.outcomes
    certainties = outcomes.external_effect_certainties
    expected = "not_applied" if provider_connected else "uncertain"
    if (
        certainties != (expected,)
        or outcomes.instance_state != "open"
        or outcomes.workflow_lifecycle != "active"
    ):
        raise AssertionError(f"{scenario} did not retain its explicit incomplete state")
    provider_request_ids = tuple(
        item.provider_request_id
        for item in outcomes.effect_evidence
        if item.provider_request_id is not None
    )
    return (
        FailureScenarioObservation(
            scenario=scenario,
            external_effect_certainty=expected,
            instance_state="open",
            workflow_lifecycle="active",
            provider_connected=provider_connected,
        ),
        _correlations(
            projection,
            worker_ids=(f"{scenario}-email",),
            process_ids=(provider_process_id,) if provider_process_id is not None else (),
            provider_request_ids=provider_request_ids,
        ),
    )


def exercise_process_controls(*, working_directory: Path) -> ControlExerciseResponse:
    """Exercise controls and all accepted synthetic playground scenarios."""

    deployment = PlaygroundDeployment(working_directory=working_directory)
    original = deployment.start()
    try:
        drained = deployment.drain()
        first_application = ExampleInsurance(database_url=deployment.database_url)
        first_application.prepare()
        first_threads = ThreadStore(database_url=deployment.database_url)
        first_correlations, first_observation = _safe_renewal_result(
            first_application,
            first_threads,
            _renewal_fixture(
                first_application,
                first_threads,
                "control",
            )[0],
        )
        deployment.reset()
        second_application = ExampleInsurance(database_url=deployment.database_url)
        second_application.prepare()
        second_threads = ThreadStore(database_url=deployment.database_url)
        second_correlations, second_observation = _safe_renewal_result(
            second_application,
            second_threads,
            _renewal_fixture(
                second_application,
                second_threads,
                "control",
            )[0],
        )
        with SyntheticEmailProvider(
            working_directory=working_directory / "intentional-failure-provider",
            behavior="not_applied",
        ) as provider:
            intentional_failure, intentional_correlations = _failure_scenario(
                database_url=deployment.database_url,
                scenario="intentional-failure",
                provider_url=provider.url,
                provider_connected=True,
                provider_process_id=provider.pid,
            )
        deployment.reset()
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as disconnected:
            disconnected.bind(("127.0.0.1", 0))
            disconnected_port = int(disconnected.getsockname()[1])
            disconnected_failure, disconnected_correlations = _failure_scenario(
                database_url=deployment.database_url,
                scenario="disconnected-provider",
                provider_url=f"http://127.0.0.1:{disconnected_port}",
                provider_connected=False,
            )
        restarted = tuple(
            process
            for role in ("api", "workflow-worker", "delivery-worker")
            for process in deployment.scale_role(role, capacity=1)
        )
        if first_observation != second_observation:
            raise AssertionError("playground reset did not reproduce its deterministic fixture")
        if {item.pid for item in original} & {item.pid for item in restarted}:
            raise AssertionError("playground restart did not use fresh interpreters")
        result = ControlExerciseResponse(
            controls=ExercisedControls(
                start=len(original),
                drain=len(drained),
                reset=True,
                restart=len(restarted),
                stop=True,
            ),
            correlations=PlaygroundCorrelations.merge(
                (
                    first_correlations,
                    second_correlations,
                    intentional_correlations,
                    disconnected_correlations,
                )
            ),
            fixture=first_observation,
            scenario_coverage=PlaygroundScenarioCoverage(
                reset_reproduced=True,
                repeated_run_reproduced=True,
                intentional_failure=intentional_failure,
                disconnected_provider=disconnected_failure,
            ),
            original_process_ids=tuple(item.pid for item in original),
            restarted_process_ids=tuple(item.pid for item in restarted),
            postgres_deployments=(
                PostgresDeploymentObservation.model_validate(
                    observe_postgres(deployment.database_url)
                ),
            ),
        )
    finally:
        deployment.stop()
    return result


__all__ = [
    "exercise_process_controls",
    "run_renewal_demonstration",
    "run_verification_demonstration",
]
