"""Pinned synthetic renewal and verification demonstrations."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from openmagic_evals.evidence.artifact_io import write_artifact
from openmagic_evals.evidence.contracts import (
    ArtifactCase,
    CaseVerdict,
    Correlations,
    PlaygroundArtifact,
    PlaygroundSummary,
)
from openmagic_evals.evidence.deadline import bounded_evidence
from openmagic_evals.evidence.deterministic_observations import (
    collect_renewal_observation,
    collect_verification_observation,
)
from openmagic_evals.evidence.release import reproducibility_pin


def _digest(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _ids(value: object) -> tuple[UUID, ...]:
    return tuple(UUID(str(item)) for item in value) if isinstance(value, list) else ()


def _write(path: Path, artifact: PlaygroundArtifact) -> PlaygroundArtifact:
    write_artifact(path, artifact)
    return artifact


def _demo_artifact(
    *,
    repository_root: Path,
    output: Path,
    command: tuple[str, ...],
    case_id: str,
    started_at: datetime,
    correlations: Correlations,
    observation: dict[str, object],
    process_controls: bool,
    timeout_seconds: int,
) -> PlaygroundArtifact:
    finished_at = datetime.now(UTC)
    return _write(
        output,
        PlaygroundArtifact(
            reproducibility=reproducibility_pin(
                repository_root.resolve(),
                command=command,
                started_at=started_at,
                finished_at=finished_at,
                timeout_seconds=timeout_seconds,
                case_corpus_digest=_digest(case_id),
            ),
            cases=(
                ArtifactCase(
                    case_id=case_id,
                    case_schema_version=1,
                    expected_trials=1,
                    observed_trials=1,
                    seeds=(0,),
                    correlations=correlations,
                    observation_digests=(_digest(observation),),
                    verdict=CaseVerdict(status="passed", invariant_violations=()),
                ),
            ),
            summary=PlaygroundSummary(
                synthetic_data_only=True,
                effects_enabled_by_default=False,
                local_provider=True,
                reset_verified=False,
                process_controls_verified=process_controls,
                contributes_to_correctness=False,
            ),
            limitations=(
                "This is a synthetic demonstration and not correctness evidence.",
                "The result applies only to the pinned local provider and build.",
            ),
        ),
    )


@bounded_evidence
def run_renewal_demo(
    *,
    repository_root: Path,
    working_directory: Path,
    output: Path,
    timeout_seconds: int = 120,
) -> PlaygroundArtifact:
    started_at = datetime.now(UTC)
    command_line = (
        "openmagic-evidence",
        "demo-renewal",
        "--repository-root",
        str(repository_root.resolve()),
        "--working-directory",
        str(working_directory.resolve()),
        "--output",
        str(output.resolve()),
        "--timeout-seconds",
        str(timeout_seconds),
    )
    result = collect_renewal_observation(working_directory)
    return _demo_artifact(
        repository_root=repository_root,
        output=output,
        command=command_line,
        case_id="demo.renewal-complete",
        started_at=started_at,
        correlations=result.correlations,
        observation=result.document,
        process_controls=False,
        timeout_seconds=timeout_seconds,
    )


@bounded_evidence
def run_verification_demo(
    *, repository_root: Path, output: Path, timeout_seconds: int = 120
) -> PlaygroundArtifact:
    started_at = datetime.now(UTC)
    command_line = (
        "openmagic-evidence",
        "demo-verification",
        "--repository-root",
        str(repository_root.resolve()),
        "--output",
        str(output.resolve()),
        "--timeout-seconds",
        str(timeout_seconds),
    )
    result = collect_verification_observation()
    return _demo_artifact(
        repository_root=repository_root,
        output=output,
        command=command_line,
        case_id="demo.deterministic-verification",
        started_at=started_at,
        correlations=result.correlations,
        observation=result.document,
        process_controls=False,
        timeout_seconds=timeout_seconds,
    )


__all__ = ["run_renewal_demo", "run_verification_demo"]
