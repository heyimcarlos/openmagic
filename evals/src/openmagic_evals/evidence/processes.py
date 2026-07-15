"""Separate-process backpressure, loss, capacity, and recovery evidence."""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

import psycopg
from example_insurance.renewals import ExampleInsurance
from openmagic_runtime.threads import ThreadStore

from openmagic_evals.evidence.contracts import (
    REQUIRED_NEGATIVE_CLAIMS,
    ArtifactCase,
    CaseVerdict,
    Correlations,
    DeterministicArtifact,
    DeterministicSummary,
    ProcessMetrics,
    QueueDepth,
    canonical_artifact_json,
    parse_artifact,
)
from openmagic_evals.evidence.redaction import audit_redaction
from openmagic_evals.evidence.release import reproducibility_pin
from openmagic_evals.harness.deployment import ManagedProcess, ProcessRole, TestDeployment
from openmagic_evals.harness.renewal_scenario import prepare_synthetic_renewal_start

_PROCESS_ROLES: tuple[ProcessRole, ...] = (
    "api",
    "workflow-worker",
    "delivery-worker",
)


@dataclass(frozen=True)
class QueueObservation:
    pending_steps: int
    pending_deliveries: int


@dataclass(frozen=True)
class ProcessEvidence:
    queued_workflows: int
    initial: QueueObservation
    drained: QueueObservation
    initial_processes: tuple[ManagedProcess, ...]
    replacement_processes: tuple[ManagedProcess, ...]
    forced_loss_pids: tuple[int, ...]
    elapsed_ms: int


def _queues(database_url: str) -> QueueObservation:
    with psycopg.connect(database_url) as connection:
        row = connection.execute(
            "SELECT "
            "(SELECT count(*) FROM openmagic_runtime.steps WHERE state = 'pending'), "
            "(SELECT count(*) FROM openmagic_runtime.deliveries WHERE status = 'pending')"
        ).fetchone()
    if row is None:
        raise RuntimeError("PostgreSQL did not return queue observations")
    return QueueObservation(pending_steps=int(row[0]), pending_deliveries=int(row[1]))


def _wait_for(
    database_url: str,
    predicate: Callable[[QueueObservation], bool],
    timeout: float = 30.0,
) -> QueueObservation:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        observation = _queues(database_url)
        if predicate(observation):
            return observation
        time.sleep(0.02)
    raise TimeoutError("process pools did not durably drain within the evidence bound")


def run_process_evidence(*, working_directory: Path, workflow_count: int = 12) -> ProcessEvidence:
    if workflow_count <= 3:
        raise ValueError("backpressure evidence requires more work than initial Worker capacity")
    started_at = time.monotonic()
    with TestDeployment(
        working_directory=working_directory,
        role_capacities={"api": 1, "workflow-worker": 1, "delivery-worker": 1},
    ) as deployment:
        initial_processes = deployment.processes
        deployment.drain_role("workflow-worker")
        deployment.drain_role("delivery-worker")
        application = ExampleInsurance(database_url=deployment.database_url)
        application.prepare()
        threads = ThreadStore(database_url=deployment.database_url)
        for seed in range(workflow_count):
            application.start_renewal_outreach(
                prepare_synthetic_renewal_start(application, threads, seed)
            )
        initial = _queues(deployment.database_url)
        if initial.pending_steps != workflow_count:
            raise AssertionError("queued Workflow count did not match pending Step depth")

        workflow_started = deployment.scale_role("workflow-worker", capacity=3)
        lost_workflow = deployment.terminate_role("workflow-worker")
        workflow_replacement = deployment.scale_role("workflow-worker", capacity=3)
        _wait_for(
            deployment.database_url,
            lambda value: value.pending_steps == 0 and value.pending_deliveries == workflow_count,
        )

        delivery_started = deployment.scale_role("delivery-worker", capacity=2)
        lost_delivery = deployment.terminate_role("delivery-worker")
        delivery_replacement = deployment.scale_role("delivery-worker", capacity=2)
        drained = _wait_for(
            deployment.database_url,
            lambda value: value.pending_steps == 0 and value.pending_deliveries == 0,
        )
        replacements = (
            *workflow_started,
            *workflow_replacement,
            *delivery_started,
            *delivery_replacement,
        )
        return ProcessEvidence(
            queued_workflows=workflow_count,
            initial=initial,
            drained=drained,
            initial_processes=initial_processes,
            replacement_processes=replacements,
            forced_loss_pids=(lost_workflow.pid, lost_delivery.pid),
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
            json.dumps(observation, sort_keys=True, separators=(",", ":")).encode()
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
        correlations=Correlations(process_ids=process_ids),
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
        ),
        limitations=(
            "Tested one synthetic 12-Workflow queue on one PostgreSQL deployment.",
            "Process evidence does not establish production availability or fleet scale.",
        ),
        negative_claims=REQUIRED_NEGATIVE_CLAIMS,
    )
    document = canonical_artifact_json(artifact)
    parse_artifact(document)
    redaction = audit_redaction(json.loads(document))
    if not redaction.passed:
        raise RuntimeError("process evidence failed its redaction audit")
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(document, encoding="utf-8")
    temporary.replace(output)
    return artifact


__all__ = [
    "ProcessEvidence",
    "QueueObservation",
    "run_process_evidence",
    "run_process_release",
]
