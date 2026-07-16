"""Separate-process backpressure, loss, capacity, and recovery experiment."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from urllib.request import Request, urlopen
from uuid import UUID, uuid5

from example_insurance.renewals import (
    ExampleInsurance,
    StartRenewalOutreach,
    StartRenewalOutreachInput,
)
from openmagic_api.renewals import StartRenewalRequest, StartRenewalResponse
from openmagic_playground import ManagedProcess, PlaygroundDeployment
from openmagic_playground.deployment_observation import observe_postgres
from openmagic_playground.renewal_observation import decode_renewal_projection
from openmagic_runtime.commands import Actor, Cause
from pydantic import JsonValue

from openmagic_evals.evidence.contracts import (
    AgentCorrelations,
    ApplicationCorrelations,
    Correlations,
    ProcessContract,
    RuntimeCorrelations,
    SanitizedObservation,
    canonical_digest,
    merge_correlations,
)
from openmagic_evals.evidence.fault_injection import lock_message_append
from openmagic_evals.evidence.inspection import (
    AttemptAuthority,
    DeliveryAuthority,
    EvidenceInspection,
    QueueState,
)
from openmagic_evals.evidence.pins import PostgresDeploymentPin
from openmagic_evals.harness import LocalEmailProvider
from openmagic_evals.harness.renewal_scenario import approve_renewal, wait_for_renewal_completion

PROCESS_CONTRACT = ProcessContract(
    scenario_version="process.loss-backpressure-recovery.v1",
    queued_workflows=12,
    initial_capacity={"api": 1, "workflow-worker": 1, "delivery-worker": 1},
    burst_capacity={"api": 2, "workflow-worker": 3, "delivery-worker": 2},
    provider_behavior="slow_success",
    provider_delay_seconds=3,
    forced_loss_points=(
        "api-readiness",
        "workflow-worker-provider-io",
        "delivery-worker-message-lock",
    ),
    queue_predicates=(
        "pending-steps-equal-workflow-denominator",
        "pending-steps-and-deliveries-drain-to-zero",
    ),
    recovery_timeout_seconds=30,
)


@dataclass(frozen=True)
class ProcessEvidence:
    queued_workflows: int
    initial: QueueState
    drained: QueueState
    initial_processes: tuple[ManagedProcess, ...]
    replacement_processes: tuple[ManagedProcess, ...]
    forced_loss_pids: tuple[int, int, int]
    lost_attempt: AttemptAuthority
    lost_delivery: DeliveryAuthority
    workload_correlations: Correlations
    workload_observations: tuple[SanitizedObservation, ...]
    api_observations: tuple[SanitizedObservation, SanitizedObservation]
    claim_latency_ms: int
    recovery_times_ms: tuple[int, int, int]
    lock_wait_lower_bound_ms: int
    observed_throughput_per_second: float
    elapsed_ms: int
    postgres_deployment: PostgresDeploymentPin


@dataclass(frozen=True)
class _ApiPoolPhase:
    initial_processes: tuple[ManagedProcess, ...]
    capacity_process: ManagedProcess
    replacement_processes: tuple[ManagedProcess, ...]
    operation: StartRenewalRequest
    lost_pid: int
    observations: tuple[SanitizedObservation, SanitizedObservation]
    recovery_ms: int


@dataclass(frozen=True)
class _WorkflowLossPhase:
    lost_process: ManagedProcess
    replacement_processes: tuple[ManagedProcess, ...]
    lost_pid: int
    authority: AttemptAuthority
    recovery_ms: int


@dataclass(frozen=True)
class _WorkflowThroughputPhase:
    processes: tuple[ManagedProcess, ...]
    workflow_ids: tuple[UUID, ...]
    initial_queue: QueueState
    claim_latency_ms: int
    throughput_per_second: float


@dataclass(frozen=True)
class _DeliveryLossPhase:
    lost_process: ManagedProcess
    replacement_processes: tuple[ManagedProcess, ...]
    lost_pid: int
    authority: DeliveryAuthority
    drained_queue: QueueState
    recovery_ms: int
    lock_wait_lower_bound_ms: int


def _api_database_observation(
    process: ManagedProcess, *, document_update: dict[str, JsonValue] | None = None
) -> SanitizedObservation:
    with urlopen(process.health_url, timeout=2) as response:
        payload = json.load(response)
    if payload.get("role") != "api" or payload.get("status") != "ready":
        raise AssertionError("API did not reconstruct its readiness from PostgreSQL")
    document: dict[str, JsonValue] = {
        "role": "api",
        "status": "ready",
        "postgresql_authority_reconstructed": True,
    }
    if document_update is not None:
        document.update(document_update)
    return SanitizedObservation(
        document=document,
        digest=canonical_digest(document),
    )


_PROCESS_RENEWAL_NAMESPACE = UUID("64d438ed-3420-44ea-a8bc-464fa9080fab")


def _renewal_request(seed: int) -> StartRenewalRequest:
    def identity(role: str) -> UUID:
        return uuid5(_PROCESS_RENEWAL_NAMESPACE, f"{seed}:{role}")

    return StartRenewalRequest(
        command_id=identity("command"),
        workflow_id=identity("workflow"),
        thread_id=identity("thread"),
        policy_id=identity("policy"),
        actor_id=identity("actor"),
        cause_id=identity("cause"),
        policy_number=f"OM-PROCESS-{seed}",
        policyholder_name=f"Synthetic Process Party {seed}",
        policyholder_email=f"process-{seed}@example.test",
        renewal_date="2028-12-31",
        expiring_premium_cents=100_000 + seed,
    )


def _submit_via_api(process: ManagedProcess, request: StartRenewalRequest) -> StartRenewalResponse:
    target = process.health_url.removesuffix("/health") + "/renewals"
    http_request = Request(
        target,
        data=request.model_dump_json().encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(http_request, timeout=5) as response:
        return StartRenewalResponse.model_validate_json(response.read())


def _application_command(request: StartRenewalRequest) -> StartRenewalOutreach:
    return StartRenewalOutreach(
        command_id=request.command_id,
        actor=Actor("party", str(request.actor_id)),
        cause=Cause("message", str(request.cause_id)),
        input=StartRenewalOutreachInput(
            workflow_id=request.workflow_id,
            thread_id=request.thread_id,
            policy_id=request.policy_id,
            policy_number=request.policy_number,
            policyholder_name=request.policyholder_name,
            policyholder_email=request.policyholder_email,
            renewal_date=request.renewal_date,
            expiring_premium_cents=request.expiring_premium_cents,
        ),
    )


def _verify_workload_outcome(
    application: ExampleInsurance, workflow_id: UUID
) -> tuple[Correlations, SanitizedObservation]:
    projection = decode_renewal_projection(application.renewal_evidence_json(workflow_id))
    outcomes = projection.outcomes
    values = projection.correlations
    valid = (
        outcomes.workflow_lifecycle == "active"
        and outcomes.instance_state == "open"
        and outcomes.approval_wait_state == "unsatisfied"
        and outcomes.external_email_effect_count == 0
        and outcomes.attempt_states
        and set(outcomes.attempt_states) == {"completed"}
        and outcomes.delivery_states == ("delivered",)
        and len(outcomes.delivery_attempt_states) == 1
        and outcomes.delivery_attempt_states[0][-1] == "succeeded"
        and set(outcomes.delivery_attempt_states[0]).issubset({"abandoned", "succeeded"})
        and len(values.message_ids) == 1
    )
    if not valid:
        diagnostic = {
            "approval_wait_state": outcomes.approval_wait_state,
            "attempt_states": outcomes.attempt_states,
            "delivery_attempt_states": outcomes.delivery_attempt_states,
            "delivery_states": outcomes.delivery_states,
            "external_email_effect_count": outcomes.external_email_effect_count,
            "instance_state": outcomes.instance_state,
            "message_count": len(values.message_ids),
            "workflow_lifecycle": outcomes.workflow_lifecycle,
        }
        raise AssertionError(
            "backpressure workload did not reach its exact safe durable outcome: "
            f"{json.dumps(diagnostic, sort_keys=True)}"
        )
    correlations = Correlations(
        runtime=RuntimeCorrelations(
            command_ids=(values.command_id,),
            workflow_ids=(values.workflow_id,),
            instance_ids=(values.instance_id,),
            step_ids=values.step_ids,
            attempt_ids=values.attempt_ids,
            wait_ids=outcomes.approval_wait_ids,
        ),
        application=ApplicationCorrelations(
            thread_ids=(values.thread_id,),
            message_ids=values.message_ids,
            domain_event_ids=values.domain_event_ids,
            delivery_ids=values.delivery_ids,
        ),
        agent=AgentCorrelations(agent_run_ids=values.agent_run_ids),
    )
    document = {
        "workflow_id": str(workflow_id),
        "workflow_lifecycle": outcomes.workflow_lifecycle,
        "instance_state": outcomes.instance_state,
        "approval_wait_state": outcomes.approval_wait_state,
        "attempt_states": list(outcomes.attempt_states),
        "delivery_states": list(outcomes.delivery_states),
        "delivery_attempt_states": [list(states) for states in outcomes.delivery_attempt_states],
        "external_email_effect_count": outcomes.external_email_effect_count,
        "message_count": len(values.message_ids),
    }
    return correlations, SanitizedObservation(
        document=document,
        digest=canonical_digest(document),
    )


def _wait_for(
    inspection: EvidenceInspection,
    predicate: Callable[[QueueState], bool],
    timeout: float = 30.0,
) -> QueueState:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        observation = inspection.queue_state()
        if predicate(observation):
            return observation
        time.sleep(0.02)
    raise TimeoutError("process pools did not durably drain within the evidence bound")


def _wait_attempt(
    inspection: EvidenceInspection,
    process: ManagedProcess,
    provider: LocalEmailProvider,
    provider_request_baseline: int,
) -> AttemptAuthority:
    if process.worker_id is None:
        raise AssertionError("Workflow Worker process did not expose its durable worker identity")
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        authority = inspection.active_attempt(process.worker_id)
        if authority is not None and provider.request_count() > provider_request_baseline:
            return authority
        time.sleep(0.02)
    raise TimeoutError("Workflow Worker did not hold observed durable authority")


def _wait_delivery(
    inspection: EvidenceInspection,
    process: ManagedProcess,
) -> DeliveryAuthority:
    if process.worker_id is None:
        raise AssertionError("Delivery Worker process did not expose its durable worker identity")
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        authority = inspection.active_delivery(process.worker_id)
        if authority is not None and inspection.query_is_lock_waiting("openmagic_runtime.messages"):
            return authority
        time.sleep(0.02)
    raise TimeoutError("Delivery Worker did not hold observed durable authority")


def _exercise_api_pool(
    deployment: PlaygroundDeployment,
    contract: ProcessContract,
) -> _ApiPoolPhase:
    initial_processes = deployment.processes
    deployment.drain_role("workflow-worker")
    deployment.drain_role("delivery-worker")
    initial_api = next(process for process in initial_processes if process.role == "api")
    capacity_api = deployment.scale_role("api", capacity=contract.burst_capacity["api"])[0]
    operation = _renewal_request(-1)
    initial_receipt = _submit_via_api(initial_api, operation)
    capacity_receipt = _submit_via_api(capacity_api, operation)
    if initial_receipt != capacity_receipt:
        raise AssertionError("independent API capacity did not replay one durable Command")
    initial_observation = _api_database_observation(
        initial_api,
        document_update={
            "burst_capacity": contract.burst_capacity["api"],
            "durable_operation": "renewal-submission",
            "independent_processes_exercised": 2,
            "exercised_process_ids": [initial_api.pid, capacity_api.pid],
            "replay_identity_preserved": True,
        },
    )

    recovery_started = time.monotonic()
    lost_api = deployment.terminate_role("api")
    gracefully_drained = deployment.drain_role("api")
    if len(gracefully_drained) != 1 or any(
        process.role == "api" for process in deployment.processes
    ):
        raise AssertionError("API pool did not drain to zero independent capacity")
    replacements = deployment.scale_role("api", capacity=contract.burst_capacity["api"])
    replacement_receipt = _submit_via_api(replacements[0], operation)
    recovery_ms = round((time.monotonic() - recovery_started) * 1000)
    if replacement_receipt != initial_receipt or {process.pid for process in replacements} & {
        initial_api.pid,
        capacity_api.pid,
    }:
        raise AssertionError("API restart did not recover durable state in fresh interpreters")
    replacement_observation = _api_database_observation(
        replacements[0],
        document_update={
            "drained_capacity": contract.burst_capacity["api"],
            "drained_process_ids": [lost_api.pid, gracefully_drained[0].pid],
            "restarted_capacity": len(replacements),
            "restarted_process_ids": [process.pid for process in replacements],
            "durable_recovery": True,
            "replay_identity_preserved": True,
        },
    )
    return _ApiPoolPhase(
        initial_processes=initial_processes,
        capacity_process=capacity_api,
        replacement_processes=replacements,
        operation=operation,
        lost_pid=lost_api.pid,
        observations=(initial_observation, replacement_observation),
        recovery_ms=recovery_ms,
    )


def _exercise_workflow_loss(
    *,
    application: ExampleInsurance,
    deployment: PlaygroundDeployment,
    inspection: EvidenceInspection,
    provider: LocalEmailProvider,
    provider_request_baseline: int,
    operation: StartRenewalRequest,
) -> _WorkflowLossPhase:
    effect_command = _application_command(operation)
    application.run_workflow_worker_once(worker_id="api-operation-facts")
    application.run_workflow_worker_once(worker_id="api-operation-draft")
    application.run_delivery_worker_once(worker_id="api-operation-delivery")
    approve_renewal(application, effect_command, effect_command.actor)
    lost_process = deployment.scale_role("workflow-worker", capacity=1)[0]
    authority = _wait_attempt(
        inspection,
        lost_process,
        provider,
        provider_request_baseline,
    )
    lost = deployment.terminate_role("workflow-worker")
    if lost.pid != lost_process.pid:
        raise AssertionError("Workflow loss did not target the observed authority holder")
    recovery_started = time.monotonic()
    time.sleep(3.2)
    replacements = deployment.scale_role("workflow-worker", capacity=1)
    wait_for_renewal_completion(application, effect_command.input.workflow_id)
    recovery_ms = round((time.monotonic() - recovery_started) * 1000)
    deployment.drain_role("workflow-worker")
    return _WorkflowLossPhase(
        lost_process=lost_process,
        replacement_processes=replacements,
        lost_pid=lost.pid,
        authority=authority,
        recovery_ms=recovery_ms,
    )


def _exercise_workflow_throughput(
    *,
    deployment: PlaygroundDeployment,
    inspection: EvidenceInspection,
    api_processes: tuple[ManagedProcess, ...],
    contract: ProcessContract,
) -> _WorkflowThroughputPhase:
    workflow_ids: list[UUID] = []
    for seed in range(contract.queued_workflows):
        request = _renewal_request(seed)
        receipt = _submit_via_api(api_processes[seed % len(api_processes)], request)
        if receipt.workflow_id != request.workflow_id:
            raise AssertionError("API returned a different durable Workflow identity")
        workflow_ids.append(request.workflow_id)
    initial = inspection.queue_state()
    if initial.pending_steps != contract.queued_workflows:
        raise AssertionError("queued Workflow count did not match pending Step depth")

    started_at = time.monotonic()
    processes = deployment.scale_role(
        "workflow-worker", capacity=contract.burst_capacity["workflow-worker"]
    )
    first_claim = _wait_for(
        inspection,
        lambda value: value.pending_steps < contract.queued_workflows,
    )
    if first_claim.pending_steps >= contract.queued_workflows:
        raise AssertionError("Workflow pool did not claim queued work")
    claim_latency_ms = round((time.monotonic() - started_at) * 1000)
    _wait_for(
        inspection,
        lambda value: (
            value.pending_steps == 0 and value.pending_deliveries == contract.queued_workflows
        ),
        timeout=contract.recovery_timeout_seconds,
    )
    deployment.drain_role("workflow-worker")
    drain_seconds = time.monotonic() - started_at
    return _WorkflowThroughputPhase(
        processes=processes,
        workflow_ids=tuple(workflow_ids),
        initial_queue=initial,
        claim_latency_ms=claim_latency_ms,
        throughput_per_second=contract.queued_workflows / drain_seconds,
    )


def _exercise_delivery_loss(
    *,
    deployment: PlaygroundDeployment,
    inspection: EvidenceInspection,
    contract: ProcessContract,
) -> _DeliveryLossPhase:
    with lock_message_append(deployment.database_url):
        lost_process = deployment.scale_role("delivery-worker", capacity=1)[0]
        authority = _wait_delivery(inspection, lost_process)
        lock_wait_deadline = time.monotonic() + 5
        while time.monotonic() < lock_wait_deadline and not inspection.query_is_lock_waiting(
            "openmagic_runtime.messages"
        ):
            time.sleep(0.01)
        if time.monotonic() >= lock_wait_deadline:
            raise AssertionError("Delivery Worker did not enter the observed lock wait")
        observed_lock_wait_started = time.monotonic()
        time.sleep(0.25)
        if not inspection.query_is_lock_waiting("openmagic_runtime.messages"):
            raise AssertionError("Delivery Worker did not remain in the observed lock wait")
        lock_wait_lower_bound_ms = round((time.monotonic() - observed_lock_wait_started) * 1000)
        lost = deployment.terminate_role("delivery-worker")
        if lost.pid != lost_process.pid:
            raise AssertionError("Delivery loss did not target the observed authority holder")
    recovery_started = time.monotonic()
    time.sleep(1.1)
    replacements = deployment.scale_role(
        "delivery-worker", capacity=contract.burst_capacity["delivery-worker"]
    )
    drained = _wait_for(
        inspection,
        lambda value: value.pending_steps == 0 and value.pending_deliveries == 0,
        timeout=contract.recovery_timeout_seconds,
    )
    return _DeliveryLossPhase(
        lost_process=lost_process,
        replacement_processes=replacements,
        lost_pid=lost.pid,
        authority=authority,
        drained_queue=drained,
        recovery_ms=round((time.monotonic() - recovery_started) * 1000),
        lock_wait_lower_bound_ms=lock_wait_lower_bound_ms,
    )


def run_process_evidence(
    *,
    working_directory: Path,
    contract: ProcessContract = PROCESS_CONTRACT,
) -> ProcessEvidence:
    if contract.queued_workflows <= 3:
        raise ValueError("backpressure evidence requires more work than initial Worker capacity")
    started_at = time.monotonic()
    provider = LocalEmailProvider(working_directory=working_directory / "provider")
    deployment = PlaygroundDeployment(
        working_directory=working_directory / "deployment",
        role_capacities=contract.initial_capacity,
        email_provider_url=provider.url,
    )
    with provider, deployment:
        provider.configure(
            behaviors=(contract.provider_behavior,),
            reconciliation="unchanged",
            delay_seconds=contract.provider_delay_seconds,
        )
        provider_request_baseline = provider.request_count()
        api = _exercise_api_pool(deployment, contract)
        application = ExampleInsurance(
            database_url=deployment.database_url,
            email_provider_url=provider.url,
        )
        application.prepare()
        inspection = EvidenceInspection(deployment.database_url)

        workflow_loss = _exercise_workflow_loss(
            application=application,
            deployment=deployment,
            inspection=inspection,
            provider=provider,
            provider_request_baseline=provider_request_baseline,
            operation=api.operation,
        )
        throughput = _exercise_workflow_throughput(
            deployment=deployment,
            inspection=inspection,
            api_processes=api.replacement_processes,
            contract=contract,
        )
        delivery_loss = _exercise_delivery_loss(
            deployment=deployment,
            inspection=inspection,
            contract=contract,
        )
        workload_observations = tuple(
            _verify_workload_outcome(application, workflow_id)
            for workflow_id in throughput.workflow_ids
        )
        replacements = (
            api.capacity_process,
            *api.replacement_processes,
            *throughput.processes,
            workflow_loss.lost_process,
            *workflow_loss.replacement_processes,
            delivery_loss.lost_process,
            *delivery_loss.replacement_processes,
        )
        return ProcessEvidence(
            queued_workflows=contract.queued_workflows,
            initial=throughput.initial_queue,
            drained=delivery_loss.drained_queue,
            initial_processes=api.initial_processes,
            replacement_processes=replacements,
            forced_loss_pids=(api.lost_pid, workflow_loss.lost_pid, delivery_loss.lost_pid),
            lost_attempt=workflow_loss.authority,
            lost_delivery=delivery_loss.authority,
            workload_correlations=merge_correlations(
                correlations for correlations, _digest in workload_observations
            ),
            workload_observations=tuple(
                observation for _correlations, observation in workload_observations
            ),
            api_observations=api.observations,
            claim_latency_ms=throughput.claim_latency_ms,
            recovery_times_ms=(
                api.recovery_ms,
                workflow_loss.recovery_ms,
                delivery_loss.recovery_ms,
            ),
            lock_wait_lower_bound_ms=delivery_loss.lock_wait_lower_bound_ms,
            observed_throughput_per_second=throughput.throughput_per_second,
            elapsed_ms=round((time.monotonic() - started_at) * 1000),
            postgres_deployment=PostgresDeploymentPin.model_validate(
                observe_postgres(deployment.database_url)
            ),
        )


__all__ = [
    "PROCESS_CONTRACT",
    "ProcessEvidence",
    "run_process_evidence",
]
