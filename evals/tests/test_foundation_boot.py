from __future__ import annotations

import os
import subprocess
import sys
from stat import S_IMODE

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
        assert S_IMODE((tmp_path / "verification-code-secret").stat().st_mode) == 0o600


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


def test_delivery_worker_process_rejects_verification_secret_capability() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            "from example_insurance.workers import delivery_worker_main; delivery_worker_main()",
            "--database-url",
            "postgresql://unused:unused@127.0.0.1:1/unused",
            "--host",
            "127.0.0.1",
            "--port",
            "1",
            "--worker-id",
            "delivery-with-secret",
            "--verification-code-secret-file",
            "/not/read/by/delivery-worker",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 2
    assert "Delivery Worker does not accept --verification-code-secret-file" in completed.stderr
