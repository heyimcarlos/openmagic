"""Executable synthetic demonstrations owned by the playground application."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
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
from openmagic_playground.reset import mark_synthetic_deployment

_DEMO_NAMESPACE = UUID("d21783e3-7912-45d6-b3b2-289549e5d3e5")


def _id(scenario: str, role: str) -> UUID:
    return uuid5(_DEMO_NAMESPACE, f"{scenario}:{role}")


@dataclass(frozen=True)
class DemonstrationResult:
    demonstration: str
    correlations: dict[str, list[str | int]]
    observation: dict[str, object]
    postgres_deployments: tuple[dict[str, object], ...]

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def _database(scenario: str) -> tuple[PostgresContainer, str]:
    container = PostgresContainer(
        POSTGRES_IMAGE,
        username="openmagic",
        password="openmagic",
        dbname=f"openmagic_playground_{scenario}_{_id(scenario, 'database').hex}",
        driver=None,
    )
    container.start()
    return container, container.get_connection_url(driver=None)


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


def _renewal_result(
    application: ExampleInsurance, threads: ThreadStore, command: StartRenewalOutreach
) -> DemonstrationResult:
    evidence = json.loads(application.renewal_evidence_json(command.input.workflow_id))
    values = evidence["correlations"]
    outcomes = evidence["outcomes"]
    messages = threads.read(command.input.thread_id).messages
    if (
        outcomes["approval_wait_state"] != "unsatisfied"
        or outcomes["external_email_effect_count"] != 0
        or len(messages) != 1
    ):
        raise AssertionError("synthetic playground renewal left its safe approval boundary")
    return DemonstrationResult(
        demonstration="renewal",
        correlations={
            "command_ids": [values["command_id"]],
            "workflow_ids": [values["workflow_id"]],
            "instance_ids": [values["instance_id"]],
            "step_ids": values["step_ids"],
            "attempt_ids": values["attempt_ids"],
            "wait_ids": outcomes["approval_wait_ids"],
            "thread_ids": [values["thread_id"]],
            "message_ids": values["message_ids"],
            "agent_run_ids": values["agent_run_ids"],
            "domain_event_ids": values["domain_event_ids"],
            "delivery_ids": values["delivery_ids"],
        },
        observation={
            "approval_wait_state": outcomes["approval_wait_state"],
            "external_email_effect_count": outcomes["external_email_effect_count"],
            "instance_state": outcomes["instance_state"],
            "message_count": len(messages),
            "workflow_lifecycle": outcomes["workflow_lifecycle"],
        },
        postgres_deployments=(),
    )


def run_renewal_demonstration() -> DemonstrationResult:
    """Run the effects-disabled renewal demonstration in an isolated database."""

    container, database_url = _database("renewal")
    try:
        from example_insurance.migrations import apply_migrations

        apply_migrations(database_url)
        mark_synthetic_deployment(database_url)
        application = ExampleInsurance(database_url=database_url)
        application.prepare()
        threads = ThreadStore(database_url=database_url)
        command, _actor = _renewal_fixture(application, threads, "renewal")
        result = _renewal_result(application, threads, command)
        return DemonstrationResult(
            demonstration=result.demonstration,
            correlations=result.correlations,
            observation=result.observation,
            postgres_deployments=(observe_postgres(database_url),),
        )
    finally:
        container.stop()


def run_verification_demonstration() -> DemonstrationResult:
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
        return DemonstrationResult(
            demonstration="verification",
            correlations={
                "command_ids": [str(protected.command_id), str(receipt.command_id)],
                "workflow_ids": [str(renewal.input.workflow_id)],
                "thread_ids": [str(renewal.input.thread_id), str(identifier_thread_id)],
                "verification_challenge_ids": [str(challenge_id)],
            },
            observation={
                "verification_outcome": receipt.result.verification_outcome,
                "protected_outcome": receipt.result.protected_outcome,
                "session_count": 1,
            },
            postgres_deployments=(observe_postgres(database_url),),
        )
    finally:
        container.stop()


def exercise_process_controls(*, working_directory: Path) -> dict[str, object]:
    """Exercise start, drain, reset, restart, and stop on one safe deployment."""

    deployment = PlaygroundDeployment(working_directory=working_directory)
    original = deployment.start()
    try:
        drained = deployment.drain()
        first_application = ExampleInsurance(database_url=deployment.database_url)
        first_application.prepare()
        first_threads = ThreadStore(database_url=deployment.database_url)
        first = _renewal_result(
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
        second = _renewal_result(
            second_application,
            second_threads,
            _renewal_fixture(
                second_application,
                second_threads,
                "control",
            )[0],
        )
        restarted = tuple(
            process
            for role in ("api", "workflow-worker", "delivery-worker")
            for process in deployment.scale_role(role, capacity=1)
        )
        if first.observation != second.observation:
            raise AssertionError("playground reset did not reproduce its deterministic fixture")
        if {item.pid for item in original} & {item.pid for item in restarted}:
            raise AssertionError("playground restart did not use fresh interpreters")
        result = {
            "controls": {
                "start": len(original),
                "drain": len(drained),
                "reset": True,
                "restart": len(restarted),
                "stop": True,
            },
            "correlations": first.correlations,
            "fixture": first.observation,
            "original_process_ids": [item.pid for item in original],
            "restarted_process_ids": [item.pid for item in restarted],
            "postgres_deployments": [observe_postgres(deployment.database_url)],
        }
    finally:
        deployment.stop()
    return result


__all__ = [
    "DemonstrationResult",
    "exercise_process_controls",
    "run_renewal_demonstration",
    "run_verification_demonstration",
]
