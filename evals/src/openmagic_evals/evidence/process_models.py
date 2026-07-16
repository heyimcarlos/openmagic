"""Canonical contracts for process loss, backpressure, and recovery evidence."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from openmagic_evals.evidence.core_models import (
    ArtifactCaseBase,
    DistributionSummary,
    EvidenceModel,
    SanitizedObservation,
    canonical_digest,
)

ProcessRole = Literal["api", "workflow-worker", "delivery-worker"]
PROCESS_ROLES: tuple[ProcessRole, ...] = ("api", "workflow-worker", "delivery-worker")


class QueueDepth(EvidenceModel):
    pending_steps: int = Field(ge=0)
    pending_deliveries: int = Field(ge=0)


class ProcessMetrics(EvidenceModel):
    queued_workflows: int = Field(gt=0)
    initial_queue: QueueDepth
    drained_queue: QueueDepth
    initial_capacity: dict[ProcessRole, int]
    started_processes: dict[ProcessRole, int]
    forced_losses: dict[ProcessRole, int]
    fresh_interpreters: Literal[True]
    postgresql_only_reconstruction: Literal[True]
    elapsed_ms: int = Field(ge=0)
    claim_latency_ms: DistributionSummary
    recovery_time_ms: DistributionSummary
    lock_wait_lower_bound_ms: DistributionSummary
    observed_throughput_per_second: float = Field(gt=0)

    @model_validator(mode="after")
    def validate_process_evidence(self) -> ProcessMetrics:
        roles = set(PROCESS_ROLES)
        if set(self.initial_capacity) != roles or set(self.started_processes) != roles:
            raise ValueError("process evidence must report every independent role")
        if any(self.initial_capacity[role] < 1 for role in PROCESS_ROLES):
            raise ValueError("process evidence requires positive initial capacity for every role")
        if any(self.started_processes[role] < 1 for role in PROCESS_ROLES):
            raise ValueError("process evidence must restart every role in a fresh interpreter")
        if set(self.forced_losses) != roles or any(
            value < 1 for value in self.forced_losses.values()
        ):
            raise ValueError("process evidence must report forced loss for every process role")
        if self.initial_queue.pending_steps != self.queued_workflows:
            raise ValueError("initial Step queue must match the submitted Workflow denominator")
        if self.drained_queue.pending_steps or self.drained_queue.pending_deliveries:
            raise ValueError("process evidence must finish with both durable queues drained")
        if (
            self.claim_latency_ms.count != 1
            or self.recovery_time_ms.count != sum(self.forced_losses.values())
            or self.lock_wait_lower_bound_ms.count != 1
        ):
            raise ValueError("process timing distributions must retain their exact denominators")
        return self


class ProcessIdentityEvidence(EvidenceModel):
    role: ProcessRole
    pid: int = Field(gt=0)
    worker_id: str | None

    @model_validator(mode="after")
    def validate_role_identity(self) -> ProcessIdentityEvidence:
        if (self.role == "api") != (self.worker_id is None):
            raise ValueError("only Worker process identities may carry worker IDs")
        return self


class ForcedProcessLoss(EvidenceModel):
    role: ProcessRole
    pid: int = Field(gt=0)


class AttemptAuthorityEvidence(EvidenceModel):
    instance_id: UUID
    step_id: UUID
    attempt_id: UUID
    worker_id: str


class DeliveryAuthorityEvidence(EvidenceModel):
    delivery_id: UUID
    delivery_attempt_id: UUID
    thread_id: UUID
    worker_id: str


class ProcessObservation(EvidenceModel):
    initial_processes: tuple[ProcessIdentityEvidence, ...]
    replacement_processes: tuple[ProcessIdentityEvidence, ...]
    forced_losses: tuple[ForcedProcessLoss, ForcedProcessLoss, ForcedProcessLoss]
    lost_attempt: AttemptAuthorityEvidence
    lost_delivery: DeliveryAuthorityEvidence
    workload_observations: tuple[SanitizedObservation, ...] = Field(min_length=1)
    api_observations: tuple[SanitizedObservation, SanitizedObservation]

    @model_validator(mode="after")
    def validate_process_observation(self) -> ProcessObservation:
        all_processes = (*self.initial_processes, *self.replacement_processes)
        pids = tuple(item.pid for item in all_processes)
        worker_ids = tuple(item.worker_id for item in all_processes if item.worker_id is not None)
        if len(set(pids)) != len(pids):
            raise ValueError("every observed process must have a unique interpreter identity")
        if len(set(worker_ids)) != len(worker_ids):
            raise ValueError("every observed Worker must have a unique worker identity")
        if {loss.role for loss in self.forced_losses} != set(PROCESS_ROLES):
            raise ValueError("process evidence requires one forced loss for every role")
        if len({loss.pid for loss in self.forced_losses}) != len(PROCESS_ROLES):
            raise ValueError("process evidence requires one distinct forced-loss PID per role")
        return self


class ProcessContract(EvidenceModel):
    scenario_version: Literal["process.loss-backpressure-recovery.v1"]
    queued_workflows: int = Field(gt=3)
    initial_capacity: dict[ProcessRole, int]
    burst_capacity: dict[ProcessRole, int]
    provider_behavior: Literal["slow_success"]
    provider_delay_seconds: int = Field(gt=0)
    forced_loss_points: tuple[
        Literal["api-readiness"],
        Literal["workflow-worker-provider-io"],
        Literal["delivery-worker-message-lock"],
    ]
    queue_predicates: tuple[
        Literal["pending-steps-equal-workflow-denominator"],
        Literal["pending-steps-and-deliveries-drain-to-zero"],
    ]
    recovery_timeout_seconds: int = Field(gt=0)

    @model_validator(mode="after")
    def validate_process_contract(self) -> ProcessContract:
        roles = set(PROCESS_ROLES)
        if set(self.initial_capacity) != roles or set(self.burst_capacity) != roles:
            raise ValueError("process contract must pin capacity for all process roles")
        if any(value < 1 for value in self.initial_capacity.values()):
            raise ValueError("process contract initial capacities must be positive")
        if any(self.burst_capacity[role] < self.initial_capacity[role] for role in PROCESS_ROLES):
            raise ValueError("process contract burst capacity cannot shrink a role")
        return self


class ProcessCase(ArtifactCaseBase):
    case_kind: Literal["process"] = "process"
    process_metrics: ProcessMetrics
    process_contract: ProcessContract
    process_observation: ProcessObservation

    @model_validator(mode="after")
    def validate_process_case(self) -> ProcessCase:
        document = {
            "contract": self.process_contract.model_dump(mode="json"),
            "metrics": self.process_metrics.model_dump(mode="json"),
            "observation": self.process_observation.model_dump(mode="json"),
            "correlations": self.correlations.model_dump(mode="json"),
        }
        if self.observation_digests != (canonical_digest(document),):
            raise ValueError("process case digest must derive from its complete canonical proof")
        if (
            len(self.process_observation.workload_observations)
            != self.process_metrics.queued_workflows
        ):
            raise ValueError("process observation must retain every queued Workflow result")
        if (
            self.process_contract.queued_workflows != self.process_metrics.queued_workflows
            or self.process_contract.initial_capacity != self.process_metrics.initial_capacity
        ):
            raise ValueError("process contract must match the executed process metrics")
        initial_counts = {
            role: sum(item.role == role for item in self.process_observation.initial_processes)
            for role in PROCESS_ROLES
        }
        started_counts = {
            role: sum(item.role == role for item in self.process_observation.replacement_processes)
            for role in PROCESS_ROLES
        }
        if (
            initial_counts != self.process_metrics.initial_capacity
            or started_counts != self.process_metrics.started_processes
            or any(
                started_counts[role] < self.process_contract.burst_capacity[role]
                for role in PROCESS_ROLES
            )
        ):
            raise ValueError("process identities must match measured and contracted capacities")
        processes_by_pid = {
            item.pid: item
            for item in (
                *self.process_observation.initial_processes,
                *self.process_observation.replacement_processes,
            )
        }
        forced_by_role = {loss.role: loss for loss in self.process_observation.forced_losses}
        if any(
            loss.pid not in processes_by_pid or processes_by_pid[loss.pid].role != role
            for role, loss in forced_by_role.items()
        ):
            raise ValueError("forced process losses must identify observed processes of each role")
        observed_loss_counts = {
            role: sum(loss.role == role for loss in self.process_observation.forced_losses)
            for role in PROCESS_ROLES
        }
        if self.process_metrics.forced_losses != observed_loss_counts:
            raise ValueError("process loss metrics must derive from recorded loss identities")
        forced_workflow = processes_by_pid[forced_by_role["workflow-worker"].pid]
        forced_delivery = processes_by_pid[forced_by_role["delivery-worker"].pid]
        if self.process_observation.lost_attempt.worker_id != forced_workflow.worker_id:
            raise ValueError("lost Attempt authority must identify the forced Workflow Worker")
        if self.process_observation.lost_delivery.worker_id != forced_delivery.worker_id:
            raise ValueError("lost Delivery authority must identify the forced Delivery Worker")
        return self
