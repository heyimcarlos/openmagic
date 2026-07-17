"""Opt-in live provider availability evidence, separate from correctness."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from openmagic_evals.evidence._live_provider_attempt import (
    LiveProviderAttemptRequest,
    execute_live_provider_attempt,
)
from openmagic_evals.evidence._live_smoke_projection import (
    LiveSmokeConfiguration,
    digest,
    project_live_smoke,
    provider_configuration_digest,
)
from openmagic_evals.evidence.artifact_io import write_artifact
from openmagic_evals.evidence.contracts import LiveSmokeArtifact
from openmagic_evals.evidence.deadline import bounded_evidence
from openmagic_evals.evidence.reproducibility import reproducibility_pin


@bounded_evidence
def run_live_smoke(
    *,
    repository_root: Path,
    output: Path,
    provider: str,
    model: str,
    endpoint: str,
    configuration_digest: str | None,
    synthetic_case_id: str,
    credential_file: Path | None,
    allow_live: bool,
    timeout_seconds: int = 10,
) -> LiveSmokeArtifact:
    configuration = LiveSmokeConfiguration(
        repository_root=repository_root,
        output=output,
        provider=provider,
        model=model,
        endpoint=endpoint,
        expected_configuration_digest=configuration_digest,
        synthetic_case_id=synthetic_case_id,
        credential_file=credential_file,
        allow_live=allow_live,
        timeout_seconds=timeout_seconds,
    )
    started_at = datetime.now(UTC)
    attempt = execute_live_provider_attempt(
        LiveProviderAttemptRequest(
            provider=provider,
            model=model,
            endpoint=endpoint,
            synthetic_case_id=synthetic_case_id,
            credential_file=credential_file,
            allow_live=allow_live,
            timeout_seconds=timeout_seconds,
        )
    )
    finished_at = datetime.now(UTC)
    artifact = project_live_smoke(
        configuration=configuration,
        attempt=attempt,
        reproducibility=reproducibility_pin(
            repository_root.resolve(),
            command=configuration.command,
            started_at=started_at,
            finished_at=finished_at,
            timeout_seconds=timeout_seconds,
            postgres_deployments=(),
            postgres_provenance="not_applicable",
            case_corpus_digest=digest(synthetic_case_id),
        ),
    )
    write_artifact(output, artifact)
    return artifact


__all__ = ["provider_configuration_digest", "run_live_smoke"]
