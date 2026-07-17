"""Typed verification phases and durable-chain projection."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from example_insurance.renewals import RequestProtectedRenewalDetails
from example_insurance.verification_evidence import (
    ApplicationEventEvidence,
    VerificationApplicationEvidence,
)
from openmagic_runtime.evidence import RuntimeDeliveryEvidence, RuntimeInstanceEvidence

from openmagic_playground._scenario_support import RenewalFixture, projection_correlations
from openmagic_playground.renewal_observation import RenewalProjection
from openmagic_playground.responses import (
    PlaygroundCorrelations,
    PlaygroundInstanceDefinitionCorrelation,
)
from openmagic_playground.verification_response import (
    VerificationCommandChain,
    VerificationDeliveryChain,
    VerificationDurableChain,
    VerificationInstanceChain,
)


@dataclass(frozen=True)
class VerificationChallengePhase:
    fixture: RenewalFixture
    protected_command: RequestProtectedRenewalDetails
    challenge_id: UUID
    identifier_thread_id: UUID
    approval_grant_id: UUID
    approval_command_id: UUID
    provision_command_id: UUID
    verification_workflow_id: UUID
    verification_instance_id: UUID


@dataclass(frozen=True)
class VerificationCompletionPhase:
    verification_outcome: Literal["verified"]
    protected_outcome: Literal["authorized"]
    application: VerificationApplicationEvidence
    renewal_runtime: RuntimeInstanceEvidence
    initial_delivery: RuntimeDeliveryEvidence
    renewal_projection: RenewalProjection


def _projection_event(
    projection: RenewalProjection,
    event_id: UUID,
) -> ApplicationEventEvidence:
    matches = tuple(item for item in projection.outcomes.domain_events if item.event_id == event_id)
    if len(matches) != 1:
        raise AssertionError("renewal projection did not retain one exact application Domain Event")
    event = matches[0]
    if event.cause.kind == "command":
        source_kind: Literal["command", "attempt"] = "command"
    elif event.cause.kind == "attempt":
        source_kind = "attempt"
    else:
        raise AssertionError("renewal Domain Event has an unsupported causal source")
    try:
        source_id = UUID(event.cause.identifier)
    except ValueError as error:
        raise AssertionError("renewal Domain Event has a malformed causal identity") from error
    return ApplicationEventEvidence(
        event_id=event.event_id,
        source_kind=source_kind,
        source_id=source_id,
    )


def _delivery_chain(
    evidence: RuntimeDeliveryEvidence,
    event: ApplicationEventEvidence,
) -> VerificationDeliveryChain:
    if evidence.domain_event_id != event.event_id:
        raise AssertionError("Delivery evidence is unrelated to its application Domain Event")
    successful_id = evidence.successful_attempt_id
    message_id = evidence.delivered_message_id
    matches = tuple(
        attempt for attempt in evidence.attempts if attempt.delivery_attempt_id == successful_id
    )
    if successful_id is None or message_id is None or len(matches) != 1:
        raise AssertionError("Delivery evidence omitted its successful Attempt or Message")
    successful = matches[0]
    if successful.state != "succeeded":
        raise AssertionError("Delivery evidence successful Attempt is not durable succeeded state")
    return VerificationDeliveryChain(
        source_kind=event.source_kind,
        source_id=event.source_id,
        domain_event_id=event.event_id,
        delivery_id=evidence.delivery_id,
        delivery_attempt_id=successful.delivery_attempt_id,
        thread_id=evidence.thread_id,
        message_id=message_id,
        worker_id=successful.worker_id,
    )


def _instance_chain(
    workflow_id: UUID, runtime: RuntimeInstanceEvidence
) -> VerificationInstanceChain:
    return VerificationInstanceChain(
        workflow_id=workflow_id,
        instance_id=runtime.instance_id,
        definition=PlaygroundInstanceDefinitionCorrelation(
            instance_id=runtime.instance_id,
            definition_key=runtime.definition_key,
            definition_version=runtime.definition_version,
        ),
        step_ids=tuple(item.step_id for item in runtime.steps),
        attempt_ids=tuple(item.attempt_id for item in runtime.attempts),
        trace_event_ids=runtime.trace_event_ids,
        worker_ids=tuple(dict.fromkeys(item.worker_id for item in runtime.attempts)),
    )


def _approval_command(projection: RenewalProjection, approval_grant_id: UUID) -> UUID:
    grants = tuple(
        item
        for item in projection.outcomes.approval_grants
        if item.approval_grant_id == approval_grant_id
    )
    if len(grants) != 1:
        raise AssertionError("verification evidence omitted its exact Approval Grant")
    decisions = tuple(
        item for item in projection.outcomes.decisions if item.decision_id == grants[0].decision_id
    )
    if len(decisions) != 1:
        raise AssertionError("verification evidence omitted its exact approval Command")
    return decisions[0].command_id


def verification_durable_chain(
    challenge: VerificationChallengePhase,
    completion: VerificationCompletionPhase,
) -> VerificationDurableChain:
    application = completion.application
    renewal_values = completion.renewal_projection.correlations
    if (
        application.challenge_id != challenge.challenge_id
        or application.protected_command_id != challenge.protected_command.command_id
        or application.verification_workflow_id != challenge.verification_workflow_id
        or application.verification_instance_id != challenge.verification_instance_id
        or application.protected_workflow_id != renewal_values.workflow_id
        or application.protected_thread_id != renewal_values.thread_id
        or completion.renewal_runtime.instance_id != renewal_values.instance_id
        or application.verification_runtime.instance_id != application.verification_instance_id
    ):
        raise AssertionError(
            "public verification results disagree with the durable application chain"
        )
    initial_event = _projection_event(
        completion.renewal_projection,
        completion.initial_delivery.domain_event_id,
    )
    return VerificationDurableChain(
        commands=VerificationCommandChain(
            renewal_start_id=renewal_values.command_id,
            approval_id=_approval_command(
                completion.renewal_projection,
                application.approval_grant_id,
            ),
            protected_request_id=application.protected_command_id,
            verification_submission_id=application.submit_command_id,
        ),
        renewal=_instance_chain(renewal_values.workflow_id, completion.renewal_runtime),
        verification=_instance_chain(
            application.verification_workflow_id,
            application.verification_runtime,
        ),
        protected_thread_id=application.protected_thread_id,
        identifier_thread_id=application.identifier_thread_id,
        approval_grant_id=application.approval_grant_id,
        challenge_id=application.challenge_id,
        session_id=application.session_id,
        initial_delivery=_delivery_chain(completion.initial_delivery, initial_event),
        challenge_delivery=_delivery_chain(
            application.challenge_delivery,
            application.challenge_event,
        ),
        authorized_delivery=_delivery_chain(
            application.authorized_delivery,
            application.authorized_event,
        ),
    )


def verification_correlations(
    challenge: VerificationChallengePhase,
    completion: VerificationCompletionPhase,
    chain: VerificationDurableChain,
) -> PlaygroundCorrelations:
    renewal = projection_correlations(completion.renewal_projection)
    chain_correlations = chain.correlations()
    with_provision = chain_correlations.model_copy(
        update={
            "runtime": chain_correlations.runtime.model_copy(
                update={
                    "command_ids": (
                        *chain_correlations.runtime.command_ids,
                        challenge.provision_command_id,
                    )
                }
            )
        }
    )
    return PlaygroundCorrelations.merge((with_provision, renewal))


__all__ = [
    "VerificationChallengePhase",
    "VerificationCompletionPhase",
    "verification_correlations",
    "verification_durable_chain",
]
