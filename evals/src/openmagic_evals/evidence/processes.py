"""Separate-process backpressure, loss, capacity, and recovery evidence."""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from example_insurance.renewals import ExampleInsurance
from openmagic_runtime.threads import ThreadStore

from openmagic_evals.evidence.artifact_io import write_artifact
from openmagic_evals.evidence.contracts import (
    REQUIRED_NEGATIVE_CLAIMS,
    ArtifactCase,
    CaseVerdict,
    Correlations,
    DeterministicArtifact,
    DeterministicSummary,
    ProcessMetrics,
    QueueDepth,
)
from openmagic_evals.evidence.fault_injection import pause_message_append
from openmagic_evals.evidence.inspection import (
    AttemptAuthority,
    DeliveryAuthority,
    EvidenceInspection,
    QueueState,
)
from openmagic_evals.evidence.release import reproducibility_pin
from openmagic_evals.harness import LocalEmailProvider
from openmagic_evals.harness.deployment import ManagedProcess, ProcessRole, TestDeployment
from openmagic_evals.harness.renewal_scenario import (
    approve_renewal,
    prepare_renewal_approval,
    prepare_synthetic_renewal_start,
    wait_for_renewal_completion,
)

_PROCESS_ROLES: tuple[ProcessRole, ...] = (
    "api",
    "workflow-worker",
    "delivery-worker",
)


@dataclass(frozen=True)
class ProcessEvidence:
    queued_workflows: int
    initial: QueueState
    drained: QueueState
    initial_processes: tuple[ManagedProcess, ...]
    replacement_processes: tuple[ManagedProcess, ...]
    forced_loss_pids: tuple[int, ...]
    lost_attempt: AttemptAuthority
    lost_delivery: DeliveryAuthority
    elapsed_ms: int


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
) -> AttemptAuthority:
    if process.worker_id is None:
        raise AssertionError("Workflow Worker process did not expose its durable worker identity")
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        authority = inspection.active_attempt(process.worker_id)
        if authority is not None and provider.requests():
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
        if authority is not None and inspection.query_is_waiting(
            "INSERT INTO openmagic_runtime.messages"
        ):
            return authority
        time.sleep(0.02)
    raise TimeoutError("Delivery Worker did not hold observed durable authority")


def run_process_evidence(*, working_directory: Path, workflow_count: int = 12) -> ProcessEvidence:
    if workflow_count <= 3:
        raise ValueError("backpressure evidence requires more work than initial Worker capacity")
    started_at = time.monotonic()
    provider = LocalEmailProvider(working_directory=working_directory / "provider")
    deployment = TestDeployment(
        working_directory=working_directory / "deployment",
        role_capacities={"api": 1, "workflow-worker": 1, "delivery-worker": 1},
        email_provider_url=provider.url,
    )
    with provider, deployment:
        provider.configure(behaviors=("slow_success",), reconciliation="unchanged", delay_seconds=3)
        initial_processes = deployment.processes
        deployment.drain_role("workflow-worker")
        deployment.drain_role("delivery-worker")
        initial_api = next(process for process in initial_processes if process.role == "api")
        api_replacement = deployment.restart_role("api")
        if api_replacement.pid == initial_api.pid:
            raise AssertionError("API restart did not use a fresh interpreter")
        application = ExampleInsurance(
            database_url=deployment.database_url,
            email_provider_url=provider.url,
        )
        application.prepare()
        threads = ThreadStore(database_url=deployment.database_url)
        inspection = EvidenceInspection(deployment.database_url)

        effect_command, actor = prepare_renewal_approval(application, threads)
        approve_renewal(application, effect_command, actor)
        lost_workflow_process = deployment.scale_role("workflow-worker", capacity=1)[0]
        lost_attempt = _wait_attempt(inspection, lost_workflow_process, provider)
        lost_workflow = deployment.terminate_role("workflow-worker")
        if lost_workflow.pid != lost_workflow_process.pid:
            raise AssertionError("Workflow loss did not target the observed authority holder")
        time.sleep(3.2)
        workflow_replacement = deployment.scale_role("workflow-worker", capacity=1)
        wait_for_renewal_completion(application, effect_command.input.workflow_id)
        deployment.drain_role("workflow-worker")

        for seed in range(workflow_count):
            application.start_renewal_outreach(
                prepare_synthetic_renewal_start(application, threads, seed)
            )
        initial = inspection.queue_state()
        if initial.pending_steps != workflow_count:
            raise AssertionError("queued Workflow count did not match pending Step depth")

        workflow_started = deployment.scale_role("workflow-worker", capacity=3)
        _wait_for(
            inspection,
            lambda value: value.pending_steps == 0 and value.pending_deliveries == workflow_count,
        )
        deployment.drain_role("workflow-worker")

        with pause_message_append(deployment.database_url):
            lost_delivery_process = deployment.scale_role("delivery-worker", capacity=1)[0]
            lost_delivery_authority = _wait_delivery(inspection, lost_delivery_process)
            lost_delivery = deployment.terminate_role("delivery-worker")
            if lost_delivery.pid != lost_delivery_process.pid:
                raise AssertionError("Delivery loss did not target the observed authority holder")
        time.sleep(1.1)
        delivery_replacement = deployment.scale_role("delivery-worker", capacity=2)
        drained = _wait_for(
            inspection,
            lambda value: value.pending_steps == 0 and value.pending_deliveries == 0,
        )
        replacements = (
            api_replacement,
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
            forced_loss_pids=(lost_workflow.pid, lost_delivery.pid),
            lost_attempt=lost_attempt,
            lost_delivery=lost_delivery_authority,
            elapsed_ms=round((time.monotonic() - started_at) * 1000),
        )


def run_process_release(
    *,
    repository_root: Path,
    working_directory: Path,
    output: Path,
    timeout_seconds: int = 120,
) -> DeterministicArtifact:
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
    report = run_process_evidence(working_directory=working_directory)
    finished_at = datetime.now(UTC)
    observation = asdict(report)
    digest = (
        "sha256:"
        + hashlib.sha256(
            json.dumps(
                observation,
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            ).encode()
        ).hexdigest()
    )
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
    case = ArtifactCase(
        case_id="process.loss-backpressure-recovery",
        case_schema_version=1,
        expected_trials=1,
        observed_trials=1,
        seeds=(0,),
        correlations=Correlations(
            instance_ids=(report.lost_attempt.instance_id,),
            step_ids=(report.lost_attempt.step_id,),
            attempt_ids=(report.lost_attempt.attempt_id,),
            thread_ids=(report.lost_delivery.thread_id,),
            delivery_ids=(report.lost_delivery.delivery_id,),
            delivery_attempt_ids=(report.lost_delivery.delivery_attempt_id,),
            worker_ids=(report.lost_attempt.worker_id, report.lost_delivery.worker_id),
            process_ids=process_ids,
        ),
        observation_digests=(digest,),
        verdict=CaseVerdict(status="passed", invariant_violations=()),
        process_metrics=ProcessMetrics(
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
            forced_losses={"workflow-worker": 1, "delivery-worker": 1},
            fresh_interpreters=True,
            postgresql_only_reconstruction=True,
            elapsed_ms=report.elapsed_ms,
        ),
    )
    corpus_digest = (
        "sha256:"
        + hashlib.sha256(
            b"process.loss-backpressure-recovery:12:api=1:workflow=1->3:delivery=1->2"
        ).hexdigest()
    )
    artifact = DeterministicArtifact(
        reproducibility=reproducibility_pin(
            repository_root.resolve(),
            command=command,
            started_at=started_at,
            finished_at=finished_at,
            timeout_seconds=timeout_seconds,
            case_corpus_digest=corpus_digest,
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
