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
    initial_pids = {process.pid for process in report.initial_processes}
    assert all(process.pid not in initial_pids for process in report.replacement_processes)
    replacements = Counter(process.role for process in report.replacement_processes)
    assert replacements["api"] == 1
    assert replacements["workflow-worker"] >= 2
    assert replacements["delivery-worker"] >= 2
