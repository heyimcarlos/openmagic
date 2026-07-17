"""Versioned typed response for the protected verification demonstration."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from openmagic_playground.responses import (
    PlaygroundApplicationCorrelations,
    PlaygroundCorrelations,
    PlaygroundInstanceDefinitionCorrelation,
    PlaygroundProcessCorrelations,
    PlaygroundRuntimeCorrelations,
    PostgresDeploymentObservation,
    _ResponseModel,
    _unique,
)


class VerificationDemonstrationObservation(_ResponseModel):
    verification_outcome: Literal["verified"]
    protected_outcome: Literal["authorized"]
    session_count: Literal[1]


class VerificationCommandChain(_ResponseModel):
    renewal_start_id: UUID
    approval_id: UUID
    protected_request_id: UUID
    verification_submission_id: UUID

    @property
    def values(self) -> tuple[UUID, ...]:
        return (
            self.renewal_start_id,
            self.approval_id,
            self.protected_request_id,
            self.verification_submission_id,
        )

    @model_validator(mode="after")
    def unique_commands(self) -> VerificationCommandChain:
        if len(set(self.values)) != len(self.values):
            raise ValueError("verification chain Command identities must be distinct")
        return self


class VerificationInstanceChain(_ResponseModel):
    workflow_id: UUID
    instance_id: UUID
    definition: PlaygroundInstanceDefinitionCorrelation
    step_ids: tuple[UUID, ...] = Field(min_length=1)
    attempt_ids: tuple[UUID, ...] = Field(min_length=1)
    trace_event_ids: tuple[UUID, ...] = Field(min_length=1)
    worker_ids: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def bind_definition(self) -> VerificationInstanceChain:
        if self.definition.instance_id != self.instance_id:
            raise ValueError("verification chain Definition must identify its exact Instance")
        if any(not worker.strip() for worker in self.worker_ids):
            raise ValueError("verification chain Worker identities must be non-empty")
        for identities, label in (
            (self.step_ids, "Step"),
            (self.attempt_ids, "Attempt"),
            (self.trace_event_ids, "Trace Event"),
            (self.worker_ids, "Worker"),
        ):
            if len(identities) != len(set(identities)):
                raise ValueError(f"verification chain {label} identities must be distinct")
        return self


class VerificationDeliveryChain(_ResponseModel):
    source_kind: Literal["command", "attempt"]
    source_id: UUID
    domain_event_id: UUID
    delivery_id: UUID
    delivery_attempt_id: UUID
    thread_id: UUID
    message_id: UUID
    worker_id: str = Field(min_length=1)


class VerificationDurableChain(_ResponseModel):
    commands: VerificationCommandChain
    renewal: VerificationInstanceChain
    verification: VerificationInstanceChain
    protected_thread_id: UUID
    identifier_thread_id: UUID
    approval_grant_id: UUID
    challenge_id: UUID
    session_id: UUID
    initial_delivery: VerificationDeliveryChain
    challenge_delivery: VerificationDeliveryChain
    authorized_delivery: VerificationDeliveryChain

    @model_validator(mode="after")
    def validate_links(self) -> VerificationDurableChain:
        if self.renewal.workflow_id == self.verification.workflow_id or (
            self.renewal.instance_id == self.verification.instance_id
        ):
            raise ValueError("verification chain requires distinct durable Workflows and Instances")
        deliveries = (
            self.initial_delivery,
            self.challenge_delivery,
            self.authorized_delivery,
        )
        if (
            self.initial_delivery.source_kind != "attempt"
            or self.initial_delivery.source_id not in self.renewal.attempt_ids
            or self.challenge_delivery.source_kind != "attempt"
            or self.challenge_delivery.source_id not in self.verification.attempt_ids
            or self.authorized_delivery.source_kind != "command"
            or self.authorized_delivery.source_id != self.commands.protected_request_id
        ):
            raise ValueError("verification Deliveries must retain their durable causal sources")
        if tuple(item.thread_id for item in deliveries) != (
            self.protected_thread_id,
            self.identifier_thread_id,
            self.protected_thread_id,
        ):
            raise ValueError("verification Deliveries must retain their exact Threads")
        for values, label in (
            (tuple(item.domain_event_id for item in deliveries), "Domain Event"),
            (tuple(item.delivery_id for item in deliveries), "Delivery"),
            (tuple(item.delivery_attempt_id for item in deliveries), "Delivery Attempt"),
            (tuple(item.message_id for item in deliveries), "Message"),
        ):
            if len(set(values)) != len(values):
                raise ValueError(f"verification chain {label} identities must be distinct")
        return self

    def correlations(self) -> PlaygroundCorrelations:
        deliveries = (
            self.initial_delivery,
            self.challenge_delivery,
            self.authorized_delivery,
        )
        return PlaygroundCorrelations(
            runtime=PlaygroundRuntimeCorrelations(
                command_ids=self.commands.values,
                workflow_ids=(self.renewal.workflow_id, self.verification.workflow_id),
                instance_ids=(self.renewal.instance_id, self.verification.instance_id),
                instance_definitions=(self.renewal.definition, self.verification.definition),
                step_ids=_unique((*self.renewal.step_ids, *self.verification.step_ids)),
                attempt_ids=_unique((*self.renewal.attempt_ids, *self.verification.attempt_ids)),
                trace_event_ids=_unique(
                    (*self.renewal.trace_event_ids, *self.verification.trace_event_ids)
                ),
            ),
            application=PlaygroundApplicationCorrelations(
                thread_ids=(self.protected_thread_id, self.identifier_thread_id),
                message_ids=tuple(item.message_id for item in deliveries),
                domain_event_ids=tuple(item.domain_event_id for item in deliveries),
                delivery_ids=tuple(item.delivery_id for item in deliveries),
                delivery_attempt_ids=tuple(item.delivery_attempt_id for item in deliveries),
                approval_grant_ids=(self.approval_grant_id,),
                verification_challenge_ids=(self.challenge_id,),
                verification_session_ids=(self.session_id,),
            ),
            process=PlaygroundProcessCorrelations(
                worker_ids=_unique(
                    (
                        *self.renewal.worker_ids,
                        *self.verification.worker_ids,
                        *(item.worker_id for item in deliveries),
                    )
                )
            ),
        )


class VerificationDemonstrationResponse(_ResponseModel):
    response_schema_version: Literal[1] = 1
    response_type: Literal["demonstration"] = "demonstration"
    demonstration: Literal["verification"] = "verification"
    durable_chain: VerificationDurableChain
    correlations: PlaygroundCorrelations
    observation: VerificationDemonstrationObservation
    postgres_deployments: tuple[PostgresDeploymentObservation, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def retain_complete_durable_chain(self) -> VerificationDemonstrationResponse:
        complete = PlaygroundCorrelations.merge(
            (self.correlations, self.durable_chain.correlations())
        )
        if complete != self.correlations:
            raise ValueError("verification correlations omit part of their durable chain")
        return self


__all__ = [
    "VerificationCommandChain",
    "VerificationDeliveryChain",
    "VerificationDemonstrationObservation",
    "VerificationDemonstrationResponse",
    "VerificationDurableChain",
    "VerificationInstanceChain",
]
