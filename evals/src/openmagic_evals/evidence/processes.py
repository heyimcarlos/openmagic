"""Separate-process backpressure, loss, capacity, and recovery evidence."""

from __future__ import annotations

import json
import statistics
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.request import Request, urlopen
from uuid import UUID, uuid5

from example_insurance.renewals import (
    ExampleInsurance,
    StartRenewalOutreach,
    StartRenewalOutreachInput,
)
from openmagic_api.renewals import StartRenewalRequest, StartRenewalResponse
from openmagic_playground import ManagedProcess, PlaygroundDeployment, ProcessRole
from openmagic_playground.deployment_observation import observe_postgres
from openmagic_runtime.commands import Actor, Cause
from pydantic import JsonValue

from openmagic_evals.evidence.artifact_io import write_artifact
from openmagic_evals.evidence.contracts import (
    REQUIRED_NEGATIVE_CLAIMS,
    AgentCorrelations,
    ApplicationCorrelations,
    AttemptAuthorityEvidence,
    CaseVerdict,
    Correlations,
    DeliveryAuthorityEvidence,
    DeterministicSummary,
    DistributionSummary,
    ForcedProcessLoss,
    ProcessArtifact,
    ProcessCase,
    ProcessContract,
    ProcessCorrelations,
    ProcessIdentityEvidence,
    ProcessMetrics,
    ProcessObservation,
    QueueDepth,
    RuntimeCorrelations,
    SanitizedObservation,
    canonical_digest,
    merge_correlations,
)
from openmagic_evals.evidence.deadline import bounded_evidence
from openmagic_evals.evidence.fault_injection import lock_message_append
from openmagic_evals.evidence.inspection import (
    AttemptAuthority,
    DeliveryAuthority,
    EvidenceInspection,
    QueueState,
)
from openmagic_evals.evidence.pins import PostgresDeploymentPin
from openmagic_evals.evidence.reproducibility import reproducibility_pin
from openmagic_evals.harness import LocalEmailProvider
from openmagic_evals.harness.renewal_scenario import approve_renewal, wait_for_renewal_completion

_PROCESS_ROLES: tuple[ProcessRole, ...] = (
    "api",
    "workflow-worker",
    "delivery-worker",
)
_PROCESS_CONTRACT = ProcessContract(
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


def _distribution(values: tuple[int, ...]) -> DistributionSummary:
    return DistributionSummary(
        count=len(values),
        mean=statistics.mean(values),
        median=statistics.median(values),
        sample_standard_deviation=statistics.stdev(values) if len(values) > 1 else 0.0,
        minimum=min(values),
        maximum=max(values),
    )


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


def _uuid_tuple(values: object) -> tuple[UUID, ...]:
    return tuple(UUID(str(value)) for value in values) if isinstance(values, list) else ()


def _verify_workload_outcome(
    application: ExampleInsurance, workflow_id: UUID
) -> tuple[Correlations, SanitizedObservation]:
    evidence = json.loads(application.renewal_evidence_json(workflow_id))
    outcomes = evidence["outcomes"]
    values = evidence["correlations"]
    valid = (
        outcomes["workflow_lifecycle"] == "active"
        and outcomes["instance_state"] == "open"
        and outcomes["approval_wait_state"] == "unsatisfied"
        and outcomes["external_email_effect_count"] == 0
        and outcomes["attempt_states"]
        and set(outcomes["attempt_states"]) == {"completed"}
        and outcomes["delivery_states"] == ["delivered"]
        and len(outcomes["delivery_attempt_states"]) == 1
        and outcomes["delivery_attempt_states"][0][-1] == "succeeded"
        and set(outcomes["delivery_attempt_states"][0]).issubset({"abandoned", "succeeded"})
        and len(values["message_ids"]) == 1
    )
    if not valid:
        diagnostic = {
            "approval_wait_state": outcomes["approval_wait_state"],
            "attempt_states": outcomes["attempt_states"],
            "delivery_attempt_states": outcomes["delivery_attempt_states"],
            "delivery_states": outcomes["delivery_states"],
            "external_email_effect_count": outcomes["external_email_effect_count"],
            "instance_state": outcomes["instance_state"],
            "message_count": len(values["message_ids"]),
            "workflow_lifecycle": outcomes["workflow_lifecycle"],
        }
        raise AssertionError(
            "backpressure workload did not reach its exact safe durable outcome: "
            f"{json.dumps(diagnostic, sort_keys=True)}"
        )
    correlations = Correlations(
        runtime=RuntimeCorrelations(
            command_ids=(UUID(values["command_id"]),),
            workflow_ids=(UUID(values["workflow_id"]),),
            instance_ids=(UUID(values["instance_id"]),),
            step_ids=_uuid_tuple(values["step_ids"]),
            attempt_ids=_uuid_tuple(values["attempt_ids"]),
            wait_ids=_uuid_tuple(outcomes["approval_wait_ids"]),
        ),
        application=ApplicationCorrelations(
            thread_ids=(UUID(values["thread_id"]),),
            message_ids=_uuid_tuple(values["message_ids"]),
            domain_event_ids=_uuid_tuple(values["domain_event_ids"]),
            delivery_ids=_uuid_tuple(values["delivery_ids"]),
        ),
        agent=AgentCorrelations(agent_run_ids=_uuid_tuple(values["agent_run_ids"])),
    )
    document = {
        "workflow_id": str(workflow_id),
        "workflow_lifecycle": outcomes["workflow_lifecycle"],
        "instance_state": outcomes["instance_state"],
        "approval_wait_state": outcomes["approval_wait_state"],
        "attempt_states": outcomes["attempt_states"],
        "delivery_states": outcomes["delivery_states"],
        "delivery_attempt_states": outcomes["delivery_attempt_states"],
        "external_email_effect_count": outcomes["external_email_effect_count"],
        "message_count": len(values["message_ids"]),
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


def run_process_evidence(
    *,
    working_directory: Path,
    contract: ProcessContract = _PROCESS_CONTRACT,
) -> ProcessEvidence:
    workflow_count = contract.queued_workflows
    if workflow_count <= 3:
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
        initial_processes = deployment.processes
        deployment.drain_role("workflow-worker")
        deployment.drain_role("delivery-worker")
        initial_api = next(process for process in initial_processes if process.role == "api")
        capacity_api = deployment.scale_role("api", capacity=contract.burst_capacity["api"])[0]
        api_operation = _renewal_request(-1)
        initial_receipt = _submit_via_api(initial_api, api_operation)
        capacity_receipt = _submit_via_api(capacity_api, api_operation)
        if initial_receipt != capacity_receipt:
            raise AssertionError("independent API capacity did not replay one durable Command")
        initial_api_observation = _api_database_observation(
            initial_api,
            document_update={
                "burst_capacity": contract.burst_capacity["api"],
                "durable_operation": "renewal-submission",
                "independent_processes_exercised": 2,
                "exercised_process_ids": [initial_api.pid, capacity_api.pid],
                "replay_identity_preserved": True,
            },
        )
        api_recovery_started = time.monotonic()
        lost_api = deployment.terminate_role("api")
        gracefully_drained_api = deployment.drain_role("api")
        if len(gracefully_drained_api) != 1 or any(
            process.role == "api" for process in deployment.processes
        ):
            raise AssertionError("API pool did not drain to zero independent capacity")
        api_replacements = deployment.scale_role("api", capacity=contract.burst_capacity["api"])
        api_replacement = api_replacements[0]
        recovery_receipt = _submit_via_api(api_replacement, api_operation)
        api_recovery_ms = round((time.monotonic() - api_recovery_started) * 1000)
        if recovery_receipt != initial_receipt or {process.pid for process in api_replacements} & {
            initial_api.pid,
            capacity_api.pid,
        }:
            raise AssertionError("API restart did not recover durable state in fresh interpreters")
        replacement_api_observation = _api_database_observation(
            api_replacement,
            document_update={
                "drained_capacity": contract.burst_capacity["api"],
                "drained_process_ids": [lost_api.pid, gracefully_drained_api[0].pid],
                "restarted_capacity": len(api_replacements),
                "restarted_process_ids": [process.pid for process in api_replacements],
                "durable_recovery": True,
                "replay_identity_preserved": True,
            },
        )
        application = ExampleInsurance(
            database_url=deployment.database_url,
            email_provider_url=provider.url,
        )
        application.prepare()
        inspection = EvidenceInspection(deployment.database_url)

        effect_command = _application_command(api_operation)
        application.run_workflow_worker_once(worker_id="api-operation-facts")
        application.run_workflow_worker_once(worker_id="api-operation-draft")
        application.run_delivery_worker_once(worker_id="api-operation-delivery")
        approve_renewal(application, effect_command, effect_command.actor)
        lost_workflow_process = deployment.scale_role("workflow-worker", capacity=1)[0]
        lost_attempt = _wait_attempt(
            inspection,
            lost_workflow_process,
            provider,
            provider_request_baseline,
        )
        lost_workflow = deployment.terminate_role("workflow-worker")
        if lost_workflow.pid != lost_workflow_process.pid:
            raise AssertionError("Workflow loss did not target the observed authority holder")
        workflow_recovery_started = time.monotonic()
        time.sleep(3.2)
        workflow_replacement = deployment.scale_role("workflow-worker", capacity=1)
        wait_for_renewal_completion(application, effect_command.input.workflow_id)
        workflow_recovery_ms = round((time.monotonic() - workflow_recovery_started) * 1000)
        deployment.drain_role("workflow-worker")

        workload_ids: list[UUID] = []
        for seed in range(workflow_count):
            request = _renewal_request(seed)
            receipt = _submit_via_api(api_replacements[seed % len(api_replacements)], request)
            if receipt.workflow_id != request.workflow_id:
                raise AssertionError("API returned a different durable Workflow identity")
            workload_ids.append(request.workflow_id)
        initial = inspection.queue_state()
        if initial.pending_steps != workflow_count:
            raise AssertionError("queued Workflow count did not match pending Step depth")

        throughput_started = time.monotonic()
        workflow_started = deployment.scale_role(
            "workflow-worker", capacity=contract.burst_capacity["workflow-worker"]
        )
        first_claim = _wait_for(inspection, lambda value: value.pending_steps < workflow_count)
        if first_claim.pending_steps >= workflow_count:
            raise AssertionError("Workflow pool did not claim queued work")
        claim_latency_ms = round((time.monotonic() - throughput_started) * 1000)
        _wait_for(
            inspection,
            lambda value: value.pending_steps == 0 and value.pending_deliveries == workflow_count,
            timeout=contract.recovery_timeout_seconds,
        )
        deployment.drain_role("workflow-worker")
        workflow_drain_seconds = time.monotonic() - throughput_started

        with lock_message_append(deployment.database_url):
            lost_delivery_process = deployment.scale_role("delivery-worker", capacity=1)[0]
            lost_delivery_authority = _wait_delivery(inspection, lost_delivery_process)
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
            lost_delivery = deployment.terminate_role("delivery-worker")
            if lost_delivery.pid != lost_delivery_process.pid:
                raise AssertionError("Delivery loss did not target the observed authority holder")
        delivery_recovery_started = time.monotonic()
        time.sleep(1.1)
        delivery_replacement = deployment.scale_role(
            "delivery-worker", capacity=contract.burst_capacity["delivery-worker"]
        )
        drained = _wait_for(
            inspection,
            lambda value: value.pending_steps == 0 and value.pending_deliveries == 0,
            timeout=contract.recovery_timeout_seconds,
        )
        delivery_recovery_ms = round((time.monotonic() - delivery_recovery_started) * 1000)
        workload_observations = tuple(
            _verify_workload_outcome(application, workflow_id) for workflow_id in workload_ids
        )
        replacements = (
            capacity_api,
            *api_replacements,
            *workflow_started,
            lost_workflow_process,
            *workflow_replacement,
            lost_delivery_process,
            *delivery_replacement,
        )
        return ProcessEvidence(
            queued_workflows=workflow_count,
            initial=initial,
            drained=drained,
            initial_processes=initial_processes,
            replacement_processes=replacements,
            forced_loss_pids=(lost_api.pid, lost_workflow.pid, lost_delivery.pid),
            lost_attempt=lost_attempt,
            lost_delivery=lost_delivery_authority,
            workload_correlations=merge_correlations(
                correlations for correlations, _digest in workload_observations
            ),
            workload_observations=tuple(
                observation for _correlations, observation in workload_observations
            ),
            api_observations=(
                initial_api_observation,
                replacement_api_observation,
            ),
            claim_latency_ms=claim_latency_ms,
            recovery_times_ms=(api_recovery_ms, workflow_recovery_ms, delivery_recovery_ms),
            lock_wait_lower_bound_ms=lock_wait_lower_bound_ms,
            observed_throughput_per_second=workflow_count / workflow_drain_seconds,
            elapsed_ms=round((time.monotonic() - started_at) * 1000),
            postgres_deployment=PostgresDeploymentPin.model_validate(
                observe_postgres(deployment.database_url)
            ),
        )


@bounded_evidence
def run_process_release(
    *,
    repository_root: Path,
    working_directory: Path,
    output: Path,
    timeout_seconds: int = 120,
) -> ProcessArtifact:
    """Record one canonical process-loss and backpressure evidence artifact."""
    command = (
        "openmagic-evidence",
        "processes",
        "--repository-root",
        str(repository_root.resolve()),
        "--working-directory",
        str(working_directory.resolve()),
        "--output",
        str(output.resolve()),
        "--timeout-seconds",
        str(timeout_seconds),
    )
    started_at = datetime.now(UTC)
    report = run_process_evidence(working_directory=working_directory, contract=_PROCESS_CONTRACT)
    finished_at = datetime.now(UTC)
    process_ids = tuple(
        dict.fromkeys(
            (
                *(process.pid for process in report.initial_processes),
                *(process.pid for process in report.replacement_processes),
                *report.forced_loss_pids,
            )
        )
    )
    initial_capacity = {
        role: sum(process.role == role for process in report.initial_processes)
        for role in _PROCESS_ROLES
    }
    started_processes = {
        role: sum(process.role == role for process in report.replacement_processes)
        for role in _PROCESS_ROLES
    }
    process_observation = ProcessObservation(
        initial_processes=tuple(
            ProcessIdentityEvidence(role=item.role, pid=item.pid, worker_id=item.worker_id)
            for item in report.initial_processes
        ),
        replacement_processes=tuple(
            ProcessIdentityEvidence(role=item.role, pid=item.pid, worker_id=item.worker_id)
            for item in report.replacement_processes
        ),
        forced_losses=(
            ForcedProcessLoss(role="api", pid=report.forced_loss_pids[0]),
            ForcedProcessLoss(role="workflow-worker", pid=report.forced_loss_pids[1]),
            ForcedProcessLoss(role="delivery-worker", pid=report.forced_loss_pids[2]),
        ),
        lost_attempt=AttemptAuthorityEvidence(
            instance_id=report.lost_attempt.instance_id,
            step_id=report.lost_attempt.step_id,
            attempt_id=report.lost_attempt.attempt_id,
            worker_id=report.lost_attempt.worker_id,
        ),
        lost_delivery=DeliveryAuthorityEvidence(
            delivery_id=report.lost_delivery.delivery_id,
            delivery_attempt_id=report.lost_delivery.delivery_attempt_id,
            thread_id=report.lost_delivery.thread_id,
            worker_id=report.lost_delivery.worker_id,
        ),
        workload_correlations=report.workload_correlations,
        workload_observations=report.workload_observations,
        api_observations=report.api_observations,
    )
    process_metrics = ProcessMetrics(
        queued_workflows=report.queued_workflows,
        initial_queue=QueueDepth(
            pending_steps=report.initial.pending_steps,
            pending_deliveries=report.initial.pending_deliveries,
        ),
        drained_queue=QueueDepth(
            pending_steps=report.drained.pending_steps,
            pending_deliveries=report.drained.pending_deliveries,
        ),
        initial_capacity=initial_capacity,
        started_processes=started_processes,
        forced_losses={"api": 1, "workflow-worker": 1, "delivery-worker": 1},
        fresh_interpreters=True,
        postgresql_only_reconstruction=True,
        elapsed_ms=report.elapsed_ms,
        claim_latency_ms=_distribution((report.claim_latency_ms,)),
        recovery_time_ms=_distribution(report.recovery_times_ms),
        lock_wait_lower_bound_ms=_distribution((report.lock_wait_lower_bound_ms,)),
        observed_throughput_per_second=report.observed_throughput_per_second,
    )
    case_correlations = merge_correlations(
        (
            report.workload_correlations,
            Correlations(
                runtime=RuntimeCorrelations(
                    instance_ids=(report.lost_attempt.instance_id,),
                    step_ids=(report.lost_attempt.step_id,),
                    attempt_ids=(report.lost_attempt.attempt_id,),
                ),
                application=ApplicationCorrelations(
                    thread_ids=(report.lost_delivery.thread_id,),
                    delivery_ids=(report.lost_delivery.delivery_id,),
                    delivery_attempt_ids=(report.lost_delivery.delivery_attempt_id,),
                ),
                process=ProcessCorrelations(
                    worker_ids=tuple(
                        process.worker_id
                        for process in (*report.initial_processes, *report.replacement_processes)
                        if process.worker_id is not None
                    ),
                    process_ids=process_ids,
                ),
            ),
        )
    )
    proof_document = {
        "contract": _PROCESS_CONTRACT.model_dump(mode="json"),
        "metrics": process_metrics.model_dump(mode="json"),
        "observation": process_observation.model_dump(mode="json"),
        "correlations": case_correlations.model_dump(mode="json"),
    }
    case = ProcessCase(
        case_id="process.loss-backpressure-recovery",
        case_schema_version=1,
        expected_trials=1,
        observed_trials=1,
        seeds=(0,),
        correlations=case_correlations,
        observation_digests=(canonical_digest(proof_document),),
        verdict=CaseVerdict(status="passed", invariant_violations=()),
        process_metrics=process_metrics,
        process_contract=_PROCESS_CONTRACT,
        process_observation=process_observation,
    )
    corpus_digest = canonical_digest(_PROCESS_CONTRACT.model_dump(mode="json"))
    artifact = ProcessArtifact(
        reproducibility=reproducibility_pin(
            repository_root.resolve(),
            command=command,
            started_at=started_at,
            finished_at=finished_at,
            timeout_seconds=timeout_seconds,
            case_corpus_digest=corpus_digest,
            postgres_deployments=(report.postgres_deployment,),
        ),
        cases=(case,),
        summary=DeterministicSummary(
            expected_cases=1,
            observed_cases=1,
            passed_cases=1,
            failed_cases=0,
            infrastructure_errors=0,
            invariant_violations=0,
            strict_pass=True,
            runner_exit_code=0,
        ),
        limitations=(
            "Tested one synthetic 12-Workflow queue on one PostgreSQL deployment.",
            "Process evidence does not establish production availability or fleet scale.",
        ),
        negative_claims=REQUIRED_NEGATIVE_CLAIMS,
    )
    write_artifact(output, artifact)
    return artifact


__all__ = [
    "ProcessEvidence",
    "run_process_evidence",
    "run_process_release",
]
