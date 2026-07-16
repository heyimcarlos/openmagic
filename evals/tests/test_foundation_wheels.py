from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]


def _run(command: list[str], *, environment: dict[str, str] | None = None) -> None:
    subprocess.run(
        command,
        cwd=ROOT,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )


@pytest.mark.integration
def test_built_wheels_install_and_boot_in_clean_environments(tmp_path) -> None:
    distributions = (
        ("openmagic-runtime", "openmagic_runtime", "openmagic_runtime"),
        ("example-insurance", "example_insurance", "example_insurance"),
        ("openmagic-api", "openmagic_api", "openmagic_api"),
        ("openmagic-evals", "openmagic_evals", "openmagic_evals"),
    )
    wheel_directory = tmp_path / "wheels"
    wheel_directory.mkdir()

    for package, _, _ in distributions:
        _run(
            [
                "uv",
                "build",
                "--package",
                package,
                "--wheel",
                "--out-dir",
                str(wheel_directory),
            ]
        )

    environments: dict[str, Path] = {}
    for package, wheel_prefix, import_name in distributions:
        environment = tmp_path / package
        _run(["uv", "venv", "--python", "3.13", str(environment)])
        wheel = next(wheel_directory.glob(f"{wheel_prefix}-*.whl"))
        python = environment / "bin/python"
        _run(
            [
                "uv",
                "pip",
                "install",
                "--python",
                str(python),
                "--find-links",
                str(wheel_directory),
                str(wheel),
            ]
        )
        _run([str(python), "-c", f"import {import_name}"])
        environments[package] = environment

    clean_evals = environments["openmagic-evals"]
    _run([str(clean_evals / "bin/openmagic-evidence"), "audit-installed"])
    work_directory = tmp_path / "deployment"
    child_environment = {
        "PATH": os.defpath,
        "PYTHONNOUSERSITE": "1",
        "WORK_DIRECTORY": str(work_directory),
    }
    _run(
        [
            str(clean_evals / "bin/python"),
            "-c",
            (
                "import os\n"
                "from pathlib import Path\n"
                "from openmagic_evals.harness import DeploymentVerifier, TestDeployment\n"
                "with TestDeployment(working_directory=Path(os.environ['WORK_DIRECTORY'])) "
                "as deployment:\n"
                "    verdict = DeploymentVerifier(deployment).verify_boot()\n"
                "    assert verdict.passed, verdict.violations\n"
            ),
        ],
        environment=child_environment,
    )
    evidence_command = clean_evals / "bin/openmagic-evidence"
    _run(
        [
            str(evidence_command),
            "demo-renewal",
            "--repository-root",
            str(ROOT),
            "--working-directory",
            str(tmp_path / "wheel-renewal-demo"),
            "--output",
            str(tmp_path / "wheel-renewal-demo.json"),
        ]
    )
    renewal_artifact = json.loads(
        (tmp_path / "wheel-renewal-demo.json").read_text(encoding="utf-8")
    )
    _run(
        [
            str(evidence_command),
            "demo-verification",
            "--repository-root",
            str(ROOT),
            "--output",
            str(tmp_path / "wheel-verification-demo.json"),
        ]
    )
    verification_artifact = json.loads(
        (tmp_path / "wheel-verification-demo.json").read_text(encoding="utf-8")
    )
    source_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    for artifact in (renewal_artifact, verification_artifact):
        assert artifact["reproducibility"]["command"][0] == "openmagic-evidence"
        assert artifact["reproducibility"]["build"]["checkout_clean"] is True
        assert artifact["reproducibility"]["build"]["git_sha"] == source_sha
        assert set(artifact["reproducibility"]["build"]["installation_kinds"].values()) == {"wheel"}
        assert (
            artifact["reproducibility"]["build"]["source_distribution_digests"]
            == artifact["reproducibility"]["build"]["distribution_digests"]
        )
        assert set(artifact["reproducibility"]["build"]["distribution_digests"]) == {
            package for package, _, _ in distributions
        }
        wheel_archives = artifact["reproducibility"]["build"]["wheel_archives"]
        assert set(wheel_archives) == {package for package, _, _ in distributions}
        assert all(pin["filename"].endswith(".whl") for pin in wheel_archives.values())
        assert all(pin["archive_digest"].startswith("sha256:") for pin in wheel_archives.values())
