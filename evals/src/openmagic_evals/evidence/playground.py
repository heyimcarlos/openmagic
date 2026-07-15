"""Synthetic playground verification kept outside correctness evidence."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from example_insurance.reset import reset_synthetic_deployment

from openmagic_evals.evidence.audit import audit_cold_schema
from openmagic_evals.evidence.contracts import (
    ArtifactCase,
    CaseVerdict,
    Correlations,
    PlaygroundArtifact,
    PlaygroundSummary,
    canonical_artifact_json,
    parse_artifact,
)
from openmagic_evals.evidence.redaction import audit_redaction
from openmagic_evals.evidence.release import reproducibility_pin
from openmagic_evals.harness import LocalEmailProvider, TestDeployment


def _digest(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def verify_playground(
    *,
    repository_root: Path,
    working_directory: Path,
    output: Path,
    timeout_seconds: int = 120,
) -> PlaygroundArtifact:
    command = (
        "openmagic-evidence",
        "playground",
        "--working-directory",
        str(working_directory.resolve()),
        "--output",
        str(output.resolve()),
    )
    started_at = datetime.now(UTC)
    with (
        LocalEmailProvider(working_directory=working_directory / "provider") as provider,
        TestDeployment(
            working_directory=working_directory / "deployment",
            email_provider_url=provider.url,
        ) as deployment,
    ):
        original = deployment.processes
        deployment.drain_role("delivery-worker")
        deployment.drain_role("workflow-worker")
        deployment.drain_role("api")
        reset_synthetic_deployment(deployment.database_url)
        restarted = (
            *deployment.scale_role("api", capacity=1),
            *deployment.scale_role("workflow-worker", capacity=1),
            *deployment.scale_role("delivery-worker", capacity=1),
        )
        schema = audit_cold_schema(deployment.database_url)
        if not schema.passed:
            raise AssertionError(schema.violations)
        original_pids = tuple(process.pid for process in original)
        restarted_pids = tuple(process.pid for process in restarted)
        if set(original_pids) & set(restarted_pids):
            raise AssertionError("playground restart did not use fresh interpreters")
        process_ids = (*original_pids, *restarted_pids, provider.pid)
    finished_at = datetime.now(UTC)
    artifact = PlaygroundArtifact(
        reproducibility=reproducibility_pin(
            repository_root.resolve(),
            command=command,
            started_at=started_at,
            finished_at=finished_at,
            timeout_seconds=timeout_seconds,
            case_corpus_digest=_digest("issue-71.playground.v1"),
        ),
        cases=(
            ArtifactCase(
                case_id="playground.synthetic-reset-and-process-control",
                case_schema_version=1,
                expected_trials=1,
                observed_trials=1,
                seeds=(0,),
                correlations=Correlations(process_ids=process_ids),
                observation_digests=(
                    _digest(
                        {
                            "original_process_count": len(original_pids),
                            "restarted_process_count": len(restarted_pids),
                            "schema_passed": schema.passed,
                        }
                    ),
                ),
                verdict=CaseVerdict(status="passed", invariant_violations=()),
            ),
        ),
        summary=PlaygroundSummary(
            synthetic_data_only=True,
            effects_enabled_by_default=False,
            local_provider=True,
            reset_verified=True,
            process_controls_verified=True,
            contributes_to_correctness=False,
        ),
        limitations=(
            "The playground is a local synthetic demonstration.",
            "Playground success does not contribute to deterministic correctness.",
        ),
    )
    document = canonical_artifact_json(artifact)
    parse_artifact(document)
    audit_redaction(json.loads(document))
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(document, encoding="utf-8")
    return artifact


__all__ = ["verify_playground"]
