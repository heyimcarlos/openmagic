from __future__ import annotations

import subprocess
from pathlib import Path

from openmagic_evals.evidence.playground import verify_playground


def _clean_repository(path: Path) -> None:
    path.mkdir()
    (path / "uv.lock").write_text("synthetic lock\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.test"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=path, check=True)
    subprocess.run(["git", "add", "uv.lock"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-qm", "test fixture"], cwd=path, check=True)


def test_playground_exercises_disabled_effects_process_restart_and_safe_reset(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    _clean_repository(repository)

    artifact = verify_playground(
        repository_root=repository,
        working_directory=tmp_path / "playground",
        output=tmp_path / "playground.json",
    )

    assert artifact.summary.synthetic_data_only
    assert not artifact.summary.effects_enabled_by_default
    assert artifact.summary.reset_verified
    assert artifact.summary.process_controls_verified
    assert not artifact.summary.contributes_to_correctness
    assert artifact.cases[0].verdict.status == "passed"
    assert len(artifact.cases[0].correlations.process_ids) == 7
