"""Pure projection of process experiments into canonical evidence artifacts."""

from __future__ import annotations

import statistics

from openmagic_playground import ProcessRole

from openmagic_evals.evidence.contracts import (
    REQUIRED_NEGATIVE_CLAIMS,
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
    canonical_digest,
    merge_correlations,
)
from openmagic_evals.evidence.pins import ReproducibilityPin
from openmagic_evals.evidence.process_experiment import ProcessEvidence

_PROCESS_ROLES: tuple[ProcessRole, ...] = (
    "api",
    "workflow-worker",
    "delivery-worker",
)


def _distribution(values: tuple[int, ...]) -> DistributionSummary:
    return DistributionSummary(
        count=len(values),
        mean=statistics.mean(values),
        median=statistics.median(values),
        sample_standard_deviation=statistics.stdev(values) if len(values) > 1 else 0.0,
        minimum=min(values),
        maximum=max(values),
    )


def _observation(report: ProcessEvidence) -> ProcessObservation:
    return ProcessObservation(
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
            instance_definition=report.lost_attempt.instance_definition,
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


def _metrics(report: ProcessEvidence) -> ProcessMetrics:
    return ProcessMetrics(
        queued_workflows=report.queued_workflows,
        initial_queue=QueueDepth(
            pending_steps=report.initial.pending_steps,
            pending_deliveries=report.initial.pending_deliveries,
        ),
        drained_queue=QueueDepth(
            pending_steps=report.drained.pending_steps,
            pending_deliveries=report.drained.pending_deliveries,
        ),
        initial_capacity={
            role: sum(process.role == role for process in report.initial_processes)
            for role in _PROCESS_ROLES
        },
        started_processes={
            role: sum(process.role == role for process in report.replacement_processes)
            for role in _PROCESS_ROLES
        },
        forced_losses={"api": 1, "workflow-worker": 1, "delivery-worker": 1},
        fresh_interpreters=True,
        postgresql_only_reconstruction=True,
        elapsed_ms=report.elapsed_ms,
        claim_latency_ms=_distribution((report.claim_latency_ms,)),
        recovery_time_ms=_distribution(report.recovery_times_ms),
        lock_wait_lower_bound_ms=_distribution((report.lock_wait_lower_bound_ms,)),
        observed_throughput_per_second=report.observed_throughput_per_second,
    )


def _correlations(report: ProcessEvidence) -> Correlations:
    process_ids = tuple(
        dict.fromkeys(
            (
                *(process.pid for process in report.initial_processes),
                *(process.pid for process in report.replacement_processes),
                *report.forced_loss_pids,
            )
        )
    )
    return merge_correlations(
        (
            report.workload_correlations,
            Correlations(
                runtime=RuntimeCorrelations(
                    instance_ids=(report.lost_attempt.instance_id,),
                    instance_definitions=(report.lost_attempt.instance_definition,),
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


def project_process_artifact(
    *,
    report: ProcessEvidence,
    contract: ProcessContract,
    reproducibility: ReproducibilityPin,
) -> ProcessArtifact:
    """Project one completed experiment without performing I/O."""

    observation = _observation(report)
    metrics = _metrics(report)
    correlations = _correlations(report)
    proof_document = {
        "contract": contract.model_dump(mode="json"),
        "metrics": metrics.model_dump(mode="json"),
        "observation": observation.model_dump(mode="json"),
        "correlations": correlations.model_dump(mode="json"),
    }
    case = ProcessCase(
        case_id="process.loss-backpressure-recovery",
        case_schema_version=1,
        expected_trials=1,
        observed_trials=1,
        seeds=(0,),
        correlations=correlations,
        observation_digests=(canonical_digest(proof_document),),
        verdict=CaseVerdict(status="passed", invariant_violations=()),
        process_metrics=metrics,
        process_contract=contract,
        process_observation=observation,
    )
    return ProcessArtifact(
        reproducibility=reproducibility,
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


__all__ = ["project_process_artifact"]
