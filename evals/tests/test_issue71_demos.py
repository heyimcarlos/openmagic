from __future__ import annotations

import subprocess
from pathlib import Path

from openmagic_evals.evidence.demos import run_renewal_demo, run_verification_demo


def _clean_repository(path: Path) -> None:
    path.mkdir()
    (path / "uv.lock").write_text("synthetic lock\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.test"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=path, check=True)
    subprocess.run(["git", "add", "uv.lock"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-qm", "test fixture"], cwd=path, check=True)


def test_public_demonstrations_reproduce_from_reused_working_directory(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    _clean_repository(repository)
    working_directory = tmp_path / "renewal"

    first = run_renewal_demo(
        repository_root=repository,
        working_directory=working_directory,
        output=tmp_path / "renewal-first.json",
    )
    second = run_renewal_demo(
        repository_root=repository,
        working_directory=working_directory,
        output=tmp_path / "renewal-second.json",
    )
    verification = run_verification_demo(
        repository_root=repository,
        output=tmp_path / "verification.json",
    )

    assert first.cases[0].verdict.status == "passed"
    assert second.cases[0].verdict.status == "passed"
    assert verification.cases[0].verdict.status == "passed"
