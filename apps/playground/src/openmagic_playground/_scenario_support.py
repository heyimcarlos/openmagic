"""Shared deterministic setup for synthetic playground scenarios."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from uuid import UUID, uuid5

from example_insurance.migrations import apply_migrations
from example_insurance.renewal_definition import RENEWAL_DEFINITION
from example_insurance.renewals import (
    ApproveRenewalDraft,
    ApproveRenewalDraftInput,
    ExampleInsurance,
    RenewalFacts,
    StartRenewalOutreach,
    StartRenewalOutreachInput,
)
from openmagic_runtime.commands import Actor, Cause
from openmagic_runtime.delivery import DeliveryAcknowledgement
from openmagic_runtime.processes import finish_owned_cleanup
from openmagic_runtime.threads import CreateThread, ThreadStore
from testcontainers.postgres import PostgresContainer

from openmagic_playground.deployment import POSTGRES_IMAGE
from openmagic_playground.renewal_observation import RenewalProjection, decode_renewal_projection
from openmagic_playground.reset import mark_synthetic_deployment
from openmagic_playground.responses import (
    PlaygroundAgentCorrelations,
    PlaygroundApplicationCorrelations,
    PlaygroundCorrelations,
    PlaygroundInstanceDefinitionCorrelation,
    PlaygroundProcessCorrelations,
    PlaygroundProviderCorrelations,
    PlaygroundRuntimeCorrelations,
    SafeRenewalBoundaryObservation,
)

_DEMO_NAMESPACE = UUID("d21783e3-7912-45d6-b3b2-289549e5d3e5")


@dataclass(frozen=True)
class RenewalFixture:
    application: ExampleInsurance
    threads: ThreadStore
    renewal: StartRenewalOutreach
    actor: Actor
    initial_delivery: DeliveryAcknowledgement
    worker_ids: tuple[str, ...]


@dataclass(frozen=True)
class RenewalApprovalPhase:
    command_id: UUID
    approval_grant_id: UUID


@dataclass(frozen=True)
class SafeRenewalPhase:
    correlations: PlaygroundCorrelations
    observation: SafeRenewalBoundaryObservation


def scenario_id(scenario: str, role: str) -> UUID:
    return uuid5(_DEMO_NAMESPACE, f"{scenario}:{role}")


@contextmanager
def scenario_database(scenario: str) -> Iterator[str]:
    container = PostgresContainer(
        POSTGRES_IMAGE,
        username="openmagic",
        password="openmagic",
        dbname=f"openmagic_playground_{scenario}_{scenario_id(scenario, 'database').hex}",
        driver=None,
    )
    try:
        container.start()
        database_url = container.get_connection_url(driver=None)
    except BaseException as startup_error:
        try:
            container.stop()
        except BaseException as cleanup_error:
            raise BaseExceptionGroup(
                "playground database startup and cleanup failed",
                [startup_error, cleanup_error],
            ) from startup_error
        raise
    try:
        apply_migrations(database_url)
        mark_synthetic_deployment(database_url)
        yield database_url
    except BaseException as execution_error:
        finish_owned_cleanup(
            container.stop,
            execution_error=execution_error,
            message="playground database execution and cleanup failed",
        )
        raise
    else:
        finish_owned_cleanup(
            container.stop,
            execution_error=None,
            message="playground database cleanup failed",
        )


def create_renewal_fixture(
    application: ExampleInsurance,
    threads: ThreadStore,
    scenario: str,
) -> RenewalFixture:
    thread_id = scenario_id(scenario, "thread")
    actor = Actor("party", str(scenario_id(scenario, "actor")))
    threads.create(CreateThread(thread_id, "email", f"{scenario}@example.test"))
    renewal = StartRenewalOutreach(
        command_id=scenario_id(scenario, "command"),
        actor=actor,
        cause=Cause("message", str(scenario_id(scenario, "cause"))),
        input=StartRenewalOutreachInput(
            workflow_id=scenario_id(scenario, "workflow"),
            thread_id=thread_id,
            policy_id=scenario_id(scenario, "policy"),
            policy_number="OM-SYNTHETIC-71",
            policyholder_name="Synthetic Playground Party",
            policyholder_email=f"{scenario}@example.test",
            renewal_date="2028-12-31",
            expiring_premium_cents=171_000,
        ),
    )
    application.replace_renewal_facts(
        RenewalFacts(
            policy_id=renewal.input.policy_id,
            policy_number=renewal.input.policy_number,
            policyholder_name=renewal.input.policyholder_name,
            policyholder_email=renewal.input.policyholder_email,
            renewal_date=renewal.input.renewal_date,
            expiring_premium_cents=renewal.input.expiring_premium_cents,
        )
    )
    application.start_renewal_outreach(renewal)
    worker_ids = (f"{scenario}-facts", f"{scenario}-draft", f"{scenario}-delivery")
    application.run_workflow_worker_once(worker_id=worker_ids[0])
    application.run_workflow_worker_once(worker_id=worker_ids[1])
    initial_delivery = application.run_delivery_worker_once(worker_id=worker_ids[2])
    if initial_delivery is None:
        raise AssertionError("synthetic renewal did not deliver its initial draft")
    return RenewalFixture(application, threads, renewal, actor, initial_delivery, worker_ids)


def approve_renewal(fixture: RenewalFixture, scenario: str) -> RenewalApprovalPhase:
    presentation = fixture.application.renewal_approval_presentation(
        fixture.renewal.input.workflow_id
    )
    command_id = scenario_id(scenario, "approval-command")
    receipt = fixture.application.approve_renewal_draft(
        ApproveRenewalDraft(
            command_id=command_id,
            actor=fixture.actor,
            cause=Cause("message", str(scenario_id(scenario, "approval-cause"))),
            input=ApproveRenewalDraftInput(
                workflow_id=fixture.renewal.input.workflow_id,
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
    approval_grant_id = receipt.result.approval_grant_id
    if approval_grant_id is None:
        raise AssertionError("synthetic renewal lacks approval authority")
    return RenewalApprovalPhase(command_id, approval_grant_id)


def observe_safe_renewal(fixture: RenewalFixture) -> SafeRenewalPhase:
    projection = decode_renewal_projection(
        fixture.application.renewal_evidence_json(fixture.renewal.input.workflow_id)
    )
    values = projection.correlations
    outcomes = projection.outcomes
    messages = fixture.threads.read(fixture.renewal.input.thread_id).messages
    if (
        outcomes.approval_wait_state != "unsatisfied"
        or outcomes.external_email_effect_count != 0
        or len(messages) != 1
    ):
        raise AssertionError("synthetic playground renewal left its safe approval boundary")
    return SafeRenewalPhase(
        correlations=PlaygroundCorrelations(
            runtime=PlaygroundRuntimeCorrelations(
                command_ids=(values.command_id,),
                workflow_ids=(values.workflow_id,),
                instance_ids=(values.instance_id,),
                instance_definitions=(
                    PlaygroundInstanceDefinitionCorrelation(
                        instance_id=values.instance_id,
                        definition_key=RENEWAL_DEFINITION.identity.key,
                        definition_version=RENEWAL_DEFINITION.identity.version,
                    ),
                ),
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
        observation=SafeRenewalBoundaryObservation(
            approval_wait_state="unsatisfied",
            external_email_effect_count=0,
            instance_state="open",
            message_count=1,
            workflow_lifecycle="active",
        ),
    )


def projection_correlations(
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
            instance_definitions=(
                PlaygroundInstanceDefinitionCorrelation(
                    instance_id=values.instance_id,
                    definition_key=RENEWAL_DEFINITION.identity.key,
                    definition_version=RENEWAL_DEFINITION.identity.version,
                ),
            ),
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


__all__: list[str] = []
