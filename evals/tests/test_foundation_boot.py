from __future__ import annotations

import os

from openmagic_evals.harness import DeploymentVerifier, TestDeployment


def test_clean_slate_deployment_boots_installed_processes(tmp_path) -> None:
    (tmp_path / ".env").write_text(
        "OPENMAGIC_DATABASE_URL=postgresql://ambient:ambient@127.0.0.1:1/wrong\n"
        "OPENMAGIC_PROCESS_ROLE=ambient\n",
        encoding="utf-8",
    )

    with TestDeployment(working_directory=tmp_path) as deployment:
        verdict = DeploymentVerifier(deployment).verify_boot()

        assert verdict.passed, verdict.violations
        assert {process.role for process in deployment.processes} == {
            "api",
            "workflow-worker",
            "delivery-worker",
        }
        assert len({process.pid for process in deployment.processes}) == 3
        assert all(process.pid != os.getpid() for process in deployment.processes)


def test_known_bad_control_is_rejected_by_independent_verifier(tmp_path) -> None:
    with TestDeployment(working_directory=tmp_path) as deployment:
        verdict = DeploymentVerifier(deployment).verify_boot(
            required_schemas=(
                "openmagic_runtime",
                "example_insurance",
                "known_bad_control",
            )
        )

        assert not verdict.passed
        assert verdict.violations == ("required schema is missing: known_bad_control",)
