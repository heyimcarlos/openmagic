"""Versioned responses emitted by the installed playground process boundary."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, PositiveInt


class _ResponseModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class PlaygroundCorrelations(_ResponseModel):
    command_ids: tuple[UUID, ...] = ()
    workflow_ids: tuple[UUID, ...] = Field(min_length=1)
    instance_ids: tuple[UUID, ...] = ()
    step_ids: tuple[UUID, ...] = ()
    attempt_ids: tuple[UUID, ...] = ()
    wait_ids: tuple[UUID, ...] = ()
    signal_ids: tuple[UUID, ...] = ()
    trace_event_ids: tuple[UUID, ...] = ()
    thread_ids: tuple[UUID, ...] = ()
    message_ids: tuple[UUID, ...] = ()
    agent_run_ids: tuple[UUID, ...] = ()
    domain_event_ids: tuple[UUID, ...] = ()
    delivery_ids: tuple[UUID, ...] = ()
    delivery_attempt_ids: tuple[UUID, ...] = ()
    external_effect_ids: tuple[UUID, ...] = ()
    approval_grant_ids: tuple[UUID, ...] = ()
    verification_challenge_ids: tuple[UUID, ...] = ()
    verification_session_ids: tuple[UUID, ...] = ()


class PostgresDeploymentObservation(_ResponseModel):
    deployment_id: str
    postgres_version: str
    postgres_image: str
    postgres_configuration: dict[str, str]
    postgres_configuration_digest: str
    migration_heads: dict[str, str | None]


class RenewalDemonstrationObservation(_ResponseModel):
    approval_wait_state: Literal["unsatisfied"]
    external_email_effect_count: Literal[0]
    instance_state: Literal["open"]
    message_count: Literal[1]
    workflow_lifecycle: Literal["active"]


class VerificationDemonstrationObservation(_ResponseModel):
    verification_outcome: Literal["verified"]
    protected_outcome: Literal["authorized"]
    session_count: Literal[1]


class RenewalDemonstrationResponse(_ResponseModel):
    response_schema_version: Literal[1] = 1
    response_type: Literal["demonstration"] = "demonstration"
    demonstration: Literal["renewal"] = "renewal"
    correlations: PlaygroundCorrelations
    observation: RenewalDemonstrationObservation
    postgres_deployments: tuple[PostgresDeploymentObservation, ...] = Field(min_length=1)


class VerificationDemonstrationResponse(_ResponseModel):
    response_schema_version: Literal[1] = 1
    response_type: Literal["demonstration"] = "demonstration"
    demonstration: Literal["verification"] = "verification"
    correlations: PlaygroundCorrelations
    observation: VerificationDemonstrationObservation
    postgres_deployments: tuple[PostgresDeploymentObservation, ...] = Field(min_length=1)


class ExercisedControls(_ResponseModel):
    start: PositiveInt
    drain: PositiveInt
    reset: Literal[True]
    restart: PositiveInt
    stop: Literal[True]


class ControlExerciseResponse(_ResponseModel):
    response_schema_version: Literal[1] = 1
    response_type: Literal["control-exercise"] = "control-exercise"
    controls: ExercisedControls
    correlations: PlaygroundCorrelations
    fixture: RenewalDemonstrationObservation
    original_process_ids: tuple[PositiveInt, ...] = Field(min_length=1)
    restarted_process_ids: tuple[PositiveInt, ...] = Field(min_length=1)
    postgres_deployments: tuple[PostgresDeploymentObservation, ...] = Field(min_length=1)


__all__ = [
    "ControlExerciseResponse",
    "ExercisedControls",
    "PlaygroundCorrelations",
    "PostgresDeploymentObservation",
    "RenewalDemonstrationObservation",
    "RenewalDemonstrationResponse",
    "VerificationDemonstrationObservation",
    "VerificationDemonstrationResponse",
]
