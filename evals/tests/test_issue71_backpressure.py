from __future__ import annotations

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
    initial_pids = {process.pid for process in report.initial_processes}
    assert all(process.pid not in initial_pids for process in report.replacement_processes)
