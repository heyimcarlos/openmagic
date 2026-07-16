"""Composition entry point for sealed Agent quality evidence."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from openmagic_evals.evidence.agent_cases import DEVELOPMENT_CASES, AgentCase
from openmagic_evals.evidence.agent_corpus_phase import load_verified_held_out_corpus
from openmagic_evals.evidence.agent_experiment import (
    capture_agent_configuration_phase,
    execute_agent_phase,
)
from openmagic_evals.evidence.agent_projection import (
    AgentExperimentResult,
    agent_corpus_digest,
    evaluate_trials,
    project_agent_quality_artifact,
)
from openmagic_evals.evidence.agent_trials import AgentTrial
from openmagic_evals.evidence.artifact_io import write_artifact
from openmagic_evals.evidence.contracts import AgentQualityArtifact
from openmagic_evals.evidence.deadline import bounded_evidence
from openmagic_evals.evidence.postgres_provenance import (
    load_postgres_deployments,
    record_postgres_deployments,
)
from openmagic_evals.evidence.reproducibility import reproducibility_pin


def load_sealed_held_out_cases(repository_root: Path) -> tuple[AgentCase, ...]:
    return load_verified_held_out_corpus(repository_root).cases


@bounded_evidence
def run_local_agent_quality(
    *,
    repository_root: Path,
    output: Path,
    timeout_seconds: int = 300,
) -> AgentQualityArtifact:
    """Execute typed phases, project their artifact, and write it canonically."""

    command = (
        "openmagic-evidence",
        "agent-quality",
        "--repository-root",
        str(repository_root.resolve()),
        "--output",
        str(output.resolve()),
        "--timeout-seconds",
        str(timeout_seconds),
    )
    started_at = datetime.now(UTC)
    configuration = capture_agent_configuration_phase()
    with TemporaryDirectory(prefix="openmagic-agent-postgres-") as deployment_directory:
        deployment_path = Path(deployment_directory)
        with record_postgres_deployments(deployment_path):
            development = execute_agent_phase(DEVELOPMENT_CASES)
            seal = load_verified_held_out_corpus(repository_root.resolve())
            held_out = execute_agent_phase(seal.cases)
        postgres_deployments = load_postgres_deployments(deployment_path)
    finished_at = datetime.now(UTC)
    phases = (development, held_out)
    artifact = project_agent_quality_artifact(
        development=development,
        held_out=held_out,
        configuration=configuration,
        seal=seal,
        reproducibility=reproducibility_pin(
            repository_root.resolve(),
            command=command,
            started_at=started_at,
            finished_at=finished_at,
            timeout_seconds=timeout_seconds,
            case_corpus_digest=agent_corpus_digest(phases),
            postgres_deployments=postgres_deployments,
        ),
    )
    write_artifact(output, artifact)
    return artifact


__all__ = [
    "AgentCase",
    "AgentExperimentResult",
    "AgentTrial",
    "evaluate_trials",
    "load_sealed_held_out_cases",
    "run_local_agent_quality",
]
