from __future__ import annotations

from collections import Counter

from openmagic_evals.harness import PlaygroundDeployment


def test_process_pools_have_independent_capacity_drain_loss_and_fresh_restart(tmp_path) -> None:
    with PlaygroundDeployment(
        working_directory=tmp_path / "deployment",
        role_capacities={"api": 2, "workflow-worker": 2, "delivery-worker": 3},
    ) as deployment:
        initial = Counter(process.role for process in deployment.processes)
        assert initial == {"api": 2, "workflow-worker": 2, "delivery-worker": 3}
        initial_pids = {process.pid for process in deployment.processes}

        lost = deployment.terminate_role("workflow-worker")
        assert lost.pid not in {process.pid for process in deployment.processes}
        restarted = deployment.scale_role("workflow-worker", capacity=2)[0]
        assert restarted.pid not in initial_pids

        drained = deployment.drain_role("delivery-worker")
        assert len(drained) == 3
        assert all(process.role == "delivery-worker" for process in drained)
        assert Counter(process.role for process in deployment.processes) == {
            "api": 2,
            "workflow-worker": 2,
        }

        replacement = deployment.scale_role("delivery-worker", capacity=2)
        assert len(replacement) == 2
        assert all(process.pid not in initial_pids for process in replacement)
        assert Counter(process.role for process in deployment.processes) == {
            "api": 2,
            "workflow-worker": 2,
            "delivery-worker": 2,
        }
