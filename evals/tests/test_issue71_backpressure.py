from __future__ import annotations

from collections import Counter

from openmagic_evals.evidence.processes import run_process_evidence


def test_separate_process_pools_drain_backpressure_after_forced_loss(tmp_path) -> None:
    report = run_process_evidence(working_directory=tmp_path / "backpressure")

    assert report.queued_workflows == 12
    assert report.initial.pending_steps == 12
    assert report.initial.pending_deliveries == 0
    assert report.drained.pending_steps == 0
    assert report.drained.pending_deliveries == 0
    assert len(report.forced_loss_pids) == 2
    assert len(set(report.forced_loss_pids)) == 2
    assert report.lost_attempt.worker_id
    assert report.lost_attempt.attempt_id
    assert report.lost_delivery.worker_id
    assert report.lost_delivery.delivery_attempt_id
    assert len(report.workload_correlations.workflow_ids) == report.queued_workflows
    assert len(report.workload_correlations.message_ids) == report.queued_workflows
    assert len(report.workload_observation_digests) == report.queued_workflows
    assert report.claim_latency_ms >= 0
    assert all(value > 0 for value in report.recovery_times_ms)
    assert report.lock_wait_ms >= 0
    assert report.observed_throughput_per_second > 0
    assert len(report.api_observation_digests) == 2
    initial_pids = {process.pid for process in report.initial_processes}
    assert all(process.pid not in initial_pids for process in report.replacement_processes)
    replacements = Counter(process.role for process in report.replacement_processes)
    assert replacements["api"] == 1
    assert replacements["workflow-worker"] >= 2
    assert replacements["delivery-worker"] >= 2
