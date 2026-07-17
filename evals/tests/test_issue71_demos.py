from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from evidence_repository import prepare_clean_evidence_repository
from openmagic_evals.evidence.contracts import parse_artifact
from openmagic_evals.evidence.demos import (
    _RENEWAL_DEMONSTRATION_CASE_ID,
    _VERIFICATION_DEMONSTRATION_CASE_ID,
    run_renewal_demo,
    run_verification_demo,
)


def test_public_demonstrations_reproduce_from_reused_working_directory(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    prepare_clean_evidence_repository(repository)
    working_directory = tmp_path / "renewal"

    first = run_renewal_demo(
        repository_root=repository,
        working_directory=working_directory,
        execute_approved_local_effect=True,
        output=tmp_path / "renewal-first.json",
    )
    second = run_renewal_demo(
        repository_root=repository,
        working_directory=working_directory,
        execute_approved_local_effect=True,
        output=tmp_path / "renewal-second.json",
    )
    verification = run_verification_demo(
        repository_root=repository,
        output=tmp_path / "verification.json",
    )

    assert first.cases[0].verdict.status == "passed"
    assert first.cases[0].case_id == _RENEWAL_DEMONSTRATION_CASE_ID
    observation = first.cases[0].scenarios[0].observation
    assert observation["approval_wait_state"] == "satisfied"
    assert observation["external_email_effect_count"] == 1
    assert observation["external_effect_certainties"] == ["applied"]
    assert observation["instance_state"] == "closed"
    assert observation["workflow_lifecycle"] == "completed"
    assert observation["approved_local_execution"] is True
    assert first.cases[0].correlations.application.external_effect_ids
    assert first.cases[0].correlations.process.worker_ids == ("playground-email",)
    assert first.cases[0].correlations.process.process_ids
    assert first.cases[0].correlations.provider.provider_request_ids
    assert second.cases[0].verdict.status == "passed"
    assert verification.cases[0].verdict.status == "passed"
    assert verification.cases[0].case_id == _VERIFICATION_DEMONSTRATION_CASE_ID
    verification_correlations = verification.cases[0].correlations
    assert len(verification_correlations.runtime.workflow_ids) == 2
    assert len(verification_correlations.runtime.instance_ids) == 2
    assert {
        item.definition_key for item in verification_correlations.runtime.instance_definitions
    } == {
        "example_insurance.renewal_outreach",
        "example_insurance.verification_delivery",
    }
    assert verification_correlations.runtime.step_ids
    assert verification_correlations.runtime.attempt_ids
    assert verification_correlations.runtime.trace_event_ids
    assert len(verification_correlations.runtime.command_ids) >= 5
    assert len(verification_correlations.application.message_ids) >= 3
    assert len(verification_correlations.application.domain_event_ids) >= 3
    assert len(verification_correlations.application.delivery_ids) >= 3
    assert len(verification_correlations.application.delivery_attempt_ids) >= 3
    assert verification_correlations.application.approval_grant_ids
    assert verification_correlations.application.verification_challenge_ids
    assert verification_correlations.application.verification_session_ids
    assert verification_correlations.process.worker_ids


def test_renewal_demo_cli_records_its_explicit_timeout(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    prepare_clean_evidence_repository(repository)
    output = tmp_path / "renewal-cli.json"

    subprocess.run(
        [
            sys.executable,
            "-m",
            "openmagic_evals.evidence",
            "demo-renewal",
            "--repository-root",
            str(repository),
            "--working-directory",
            str(tmp_path / "renewal-cli"),
            "--execute-approved-local-effect",
            "--output",
            str(output),
            "--timeout-seconds",
            "37",
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    artifact = parse_artifact(output.read_text(encoding="utf-8"))
    assert artifact.reproducibility.timeout_seconds == 37
    assert artifact.reproducibility.command[-1] == "37"
