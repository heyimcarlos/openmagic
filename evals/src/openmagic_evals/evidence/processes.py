"""Composition entry points for process-loss and backpressure evidence."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from openmagic_evals.evidence.artifact_io import write_artifact
from openmagic_evals.evidence.contracts import ProcessArtifact, canonical_digest
from openmagic_evals.evidence.deadline import bounded_evidence
from openmagic_evals.evidence.process_experiment import (
    PROCESS_CONTRACT,
    ProcessEvidence,
    run_process_evidence,
)
from openmagic_evals.evidence.process_projection import project_process_artifact
from openmagic_evals.evidence.reproducibility import reproducibility_pin


@bounded_evidence
def run_process_release(
    *,
    repository_root: Path,
    working_directory: Path,
    output: Path,
    timeout_seconds: int = 120,
) -> ProcessArtifact:
    """Execute, project, and write one canonical process evidence artifact."""

    command = (
        "openmagic-evidence",
        "processes",
        "--repository-root",
        str(repository_root.resolve()),
        "--working-directory",
        str(working_directory.resolve()),
        "--output",
        str(output.resolve()),
        "--timeout-seconds",
        str(timeout_seconds),
    )
    started_at = datetime.now(UTC)
    report = run_process_evidence(
        working_directory=working_directory,
        contract=PROCESS_CONTRACT,
    )
    finished_at = datetime.now(UTC)
    artifact = project_process_artifact(
        report=report,
        contract=PROCESS_CONTRACT,
        reproducibility=reproducibility_pin(
            repository_root.resolve(),
            command=command,
            started_at=started_at,
            finished_at=finished_at,
            timeout_seconds=timeout_seconds,
            case_corpus_digest=canonical_digest(PROCESS_CONTRACT.model_dump(mode="json")),
            postgres_deployments=(report.postgres_deployment,),
        ),
    )
    write_artifact(output, artifact)
    return artifact


__all__ = [
    "ProcessEvidence",
    "run_process_evidence",
    "run_process_release",
]
