"""Versioned responses emitted by the installed playground process boundary."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Literal, TypeVar
from uuid import UUID

from openmagic_runtime.evidence import content_fingerprint
from pydantic import BaseModel, ConfigDict, Field, PositiveInt, model_validator

CorrelationValue = TypeVar("CorrelationValue")


class _ResponseModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def _unique(values: Iterable[CorrelationValue]) -> tuple[CorrelationValue, ...]:
    return tuple(dict.fromkeys(values))


class PlaygroundInstanceDefinitionCorrelation(_ResponseModel):
    instance_id: UUID
    definition_key: str = Field(min_length=1)
    definition_version: PositiveInt


class PlaygroundRuntimeCorrelations(_ResponseModel):
    command_ids: tuple[UUID, ...] = ()
    workflow_ids: tuple[UUID, ...] = Field(min_length=1)
    instance_ids: tuple[UUID, ...] = ()
    step_ids: tuple[UUID, ...] = ()
    attempt_ids: tuple[UUID, ...] = ()
    wait_ids: tuple[UUID, ...] = ()
    signal_ids: tuple[UUID, ...] = ()
    trace_event_ids: tuple[UUID, ...] = ()
    instance_definitions: tuple[PlaygroundInstanceDefinitionCorrelation, ...] = ()

    @model_validator(mode="after")
    def retain_instance_definitions(self) -> PlaygroundRuntimeCorrelations:
        mapped_ids = tuple(item.instance_id for item in self.instance_definitions)
        if len(self.instance_ids) != len(set(self.instance_ids)):
            raise ValueError("playground Instance identities must be unique")
        if len(mapped_ids) != len(set(mapped_ids)):
            raise ValueError("a playground Instance can retain only one Definition identity")
        if set(mapped_ids) != set(self.instance_ids):
            raise ValueError("every playground Instance must retain its Definition identity")
        return self


class PlaygroundApplicationCorrelations(_ResponseModel):
    thread_ids: tuple[UUID, ...] = ()
    message_ids: tuple[UUID, ...] = ()
    domain_event_ids: tuple[UUID, ...] = ()
    delivery_ids: tuple[UUID, ...] = ()
    delivery_attempt_ids: tuple[UUID, ...] = ()
    external_effect_ids: tuple[UUID, ...] = ()
    approval_grant_ids: tuple[UUID, ...] = ()
    verification_challenge_ids: tuple[UUID, ...] = ()
    verification_session_ids: tuple[UUID, ...] = ()


class PlaygroundAgentCorrelations(_ResponseModel):
    agent_run_ids: tuple[UUID, ...] = ()


class PlaygroundProcessCorrelations(_ResponseModel):
    worker_ids: tuple[str, ...] = ()
    process_ids: tuple[PositiveInt, ...] = ()


class PlaygroundProviderCorrelations(_ResponseModel):
    provider_request_ids: tuple[str, ...] = ()


class PlaygroundCorrelations(_ResponseModel):
    runtime: PlaygroundRuntimeCorrelations
    application: PlaygroundApplicationCorrelations = Field(
        default_factory=PlaygroundApplicationCorrelations
    )
    agent: PlaygroundAgentCorrelations = Field(default_factory=PlaygroundAgentCorrelations)
    process: PlaygroundProcessCorrelations = Field(default_factory=PlaygroundProcessCorrelations)
    provider: PlaygroundProviderCorrelations = Field(default_factory=PlaygroundProviderCorrelations)

    @classmethod
    def merge(cls, values: Iterable[PlaygroundCorrelations]) -> PlaygroundCorrelations:
        items = tuple(values)
        return cls(
            runtime=PlaygroundRuntimeCorrelations(
                command_ids=_unique(value for item in items for value in item.runtime.command_ids),
                workflow_ids=_unique(
                    value for item in items for value in item.runtime.workflow_ids
                ),
                instance_ids=_unique(
                    value for item in items for value in item.runtime.instance_ids
                ),
                step_ids=_unique(value for item in items for value in item.runtime.step_ids),
                attempt_ids=_unique(value for item in items for value in item.runtime.attempt_ids),
                wait_ids=_unique(value for item in items for value in item.runtime.wait_ids),
                signal_ids=_unique(value for item in items for value in item.runtime.signal_ids),
                trace_event_ids=_unique(
                    value for item in items for value in item.runtime.trace_event_ids
                ),
                instance_definitions=_unique(
                    value for item in items for value in item.runtime.instance_definitions
                ),
            ),
            application=PlaygroundApplicationCorrelations(
                thread_ids=_unique(
                    value for item in items for value in item.application.thread_ids
                ),
                message_ids=_unique(
                    value for item in items for value in item.application.message_ids
                ),
                domain_event_ids=_unique(
                    value for item in items for value in item.application.domain_event_ids
                ),
                delivery_ids=_unique(
                    value for item in items for value in item.application.delivery_ids
                ),
                delivery_attempt_ids=_unique(
                    value for item in items for value in item.application.delivery_attempt_ids
                ),
                external_effect_ids=_unique(
                    value for item in items for value in item.application.external_effect_ids
                ),
                approval_grant_ids=_unique(
                    value for item in items for value in item.application.approval_grant_ids
                ),
                verification_challenge_ids=_unique(
                    value for item in items for value in item.application.verification_challenge_ids
                ),
                verification_session_ids=_unique(
                    value for item in items for value in item.application.verification_session_ids
                ),
            ),
            agent=PlaygroundAgentCorrelations(
                agent_run_ids=_unique(value for item in items for value in item.agent.agent_run_ids)
            ),
            process=PlaygroundProcessCorrelations(
                worker_ids=_unique(value for item in items for value in item.process.worker_ids),
                process_ids=_unique(value for item in items for value in item.process.process_ids),
            ),
            provider=PlaygroundProviderCorrelations(
                provider_request_ids=_unique(
                    value for item in items for value in item.provider.provider_request_ids
                )
            ),
        )


class PostgresDeploymentObservation(_ResponseModel):
    deployment_id: str
    postgres_version: str
    postgres_image: str
    postgres_configuration: dict[str, str]
    postgres_configuration_digest: str
    migration_heads: dict[str, str | None]

    @model_validator(mode="after")
    def validate_postgres(self) -> PostgresDeploymentObservation:
        if self.postgres_configuration_digest != "sha256:" + content_fingerprint(
            self.postgres_configuration
        ):
            raise ValueError("PostgreSQL configuration digest does not match its document")
        if set(self.migration_heads) != {"example_insurance", "openmagic_runtime"} or any(
            value is None for value in self.migration_heads.values()
        ):
            raise ValueError("playground PostgreSQL provenance requires concrete migration heads")
        return self


class SafeRenewalBoundaryObservation(_ResponseModel):
    approval_wait_state: Literal["unsatisfied"]
    external_email_effect_count: Literal[0]
    instance_state: Literal["open"]
    message_count: Literal[1]
    workflow_lifecycle: Literal["active"]


class RenewalDemonstrationObservation(_ResponseModel):
    approval_wait_state: Literal["satisfied"]
    external_email_effect_count: Literal[1]
    external_effect_certainties: tuple[Literal["applied"], ...] = Field(min_length=1)
    instance_state: Literal["closed"]
    message_count: Literal[1]
    workflow_lifecycle: Literal["completed"]
    completion_event_count: Literal[1]
    provider_request_count: Literal[1]
    approved_local_execution: Literal[True]


class FailureScenarioObservation(_ResponseModel):
    scenario: Literal["intentional-failure", "disconnected-provider"]
    external_effect_certainty: Literal["not_applied", "uncertain"]
    instance_state: Literal["open"]
    workflow_lifecycle: Literal["active"]
    provider_connected: bool


class PlaygroundScenarioCoverage(_ResponseModel):
    reset_reproduced: Literal[True]
    repeated_run_reproduced: Literal[True]
    intentional_failure: FailureScenarioObservation
    disconnected_provider: FailureScenarioObservation


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
    fixture: SafeRenewalBoundaryObservation
    scenario_coverage: PlaygroundScenarioCoverage
    original_process_ids: tuple[PositiveInt, ...] = Field(min_length=1)
    restarted_process_ids: tuple[PositiveInt, ...] = Field(min_length=1)
    postgres_deployments: tuple[PostgresDeploymentObservation, ...] = Field(min_length=1)


__all__ = [
    "ControlExerciseResponse",
    "ExercisedControls",
    "FailureScenarioObservation",
    "PlaygroundAgentCorrelations",
    "PlaygroundApplicationCorrelations",
    "PlaygroundCorrelations",
    "PlaygroundInstanceDefinitionCorrelation",
    "PlaygroundProcessCorrelations",
    "PlaygroundProviderCorrelations",
    "PlaygroundRuntimeCorrelations",
    "PlaygroundScenarioCoverage",
    "PostgresDeploymentObservation",
    "RenewalDemonstrationObservation",
    "RenewalDemonstrationResponse",
    "SafeRenewalBoundaryObservation",
    "VerificationDemonstrationObservation",
    "VerificationDemonstrationResponse",
]
