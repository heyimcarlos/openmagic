from __future__ import annotations

from pathlib import Path

from evidence_repository import prepare_clean_evidence_repository
from openmagic_evals.evidence.playground import verify_playground


def test_playground_exercises_disabled_effects_process_restart_and_safe_reset(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    prepare_clean_evidence_repository(repository)

    artifact = verify_playground(
        repository_root=repository,
        working_directory=tmp_path / "playground",
        output=tmp_path / "playground.json",
    )

    assert artifact.summary.synthetic_data_only
    assert not artifact.summary.effects_enabled_by_default
    assert artifact.summary.reset_verified
    assert artifact.summary.repeated_run_verified
    assert artifact.summary.intentional_failure_verified
    assert artifact.summary.disconnected_provider_verified
    assert artifact.summary.process_controls_verified
    assert not artifact.summary.contributes_to_correctness
    assert artifact.cases[0].verdict.status == "passed"
    assert len(artifact.cases[0].correlations.process.process_ids) == 7
    assert {scenario.scenario_id for scenario in artifact.cases[0].scenarios} == {
        "safe-reset",
        "repeated-run",
        "intentional-failure",
        "disconnected-provider",
    }
