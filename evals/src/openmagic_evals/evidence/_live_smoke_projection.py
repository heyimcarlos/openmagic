"""Pure canonical projection for redacted live-provider attempt results."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from openmagic_evals.evidence._live_provider_attempt import LiveProviderAttempt
from openmagic_evals.evidence.contracts import (
    ArtifactCase,
    AvailabilitySummary,
    CaseVerdict,
    Correlations,
    DeterministicScenarioEvidence,
    LiveProviderPin,
    LiveSmokeArtifact,
    ProviderCorrelations,
    ReproducibilityPin,
    deterministic_observation_digest,
)


def digest(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode()).hexdigest()


def provider_configuration_digest(*, provider: str, model: str, endpoint: str) -> str:
    return digest(
        json.dumps(
            {"endpoint": endpoint, "model": model, "provider": provider},
            sort_keys=True,
            separators=(",", ":"),
        )
    )


@dataclass(frozen=True)
class LiveSmokeConfiguration:
    repository_root: Path
    output: Path
    provider: str
    model: str
    endpoint: str
    expected_configuration_digest: str | None
    synthetic_case_id: str
    credential_file: Path | None
    allow_live: bool
    timeout_seconds: int

    def __post_init__(self) -> None:
        if self.timeout_seconds <= 0:
            raise ValueError("live smoke timeout must be positive")
        if (
            self.expected_configuration_digest is not None
            and self.expected_configuration_digest != self.configuration_digest
        ):
            raise ValueError("live configuration digest does not match provider configuration")

    @property
    def configuration_digest(self) -> str:
        return provider_configuration_digest(
            provider=self.provider,
            model=self.model,
            endpoint=self.endpoint,
        )

    @property
    def command(self) -> tuple[str, ...]:
        parts = [
            "openmagic-evidence",
            "live-smoke",
            "--repository-root",
            str(self.repository_root.resolve()),
            "--output",
            str(self.output.resolve()),
            "--provider",
            self.provider,
            "--model",
            self.model,
            "--endpoint",
            self.endpoint,
            "--synthetic-case-id",
            self.synthetic_case_id,
            "--timeout-seconds",
            str(self.timeout_seconds),
        ]
        if self.expected_configuration_digest is not None:
            parts.extend(("--configuration-digest", self.expected_configuration_digest))
        if self.credential_file is not None:
            parts.extend(("--credential-file", str(self.credential_file.resolve())))
        if self.allow_live:
            parts.append("--allow-live")
        return tuple(parts)


def project_live_smoke(
    *,
    configuration: LiveSmokeConfiguration,
    attempt: LiveProviderAttempt,
    reproducibility: ReproducibilityPin,
) -> LiveSmokeArtifact:
    """Project a secret-free attempt result into its versioned evidence contract."""

    status = (
        "passed"
        if attempt.available
        else "unavailable"
        if not attempt.attempted
        else "infrastructure_error"
    )
    correlations = Correlations(
        provider=ProviderCorrelations(provider_request_ids=attempt.provider_request_ids)
    )
    scenarios = (
        DeterministicScenarioEvidence(
            scenario_id=configuration.synthetic_case_id,
            correlations=correlations,
            observation=attempt.observation,
            observation_digest=digest(
                json.dumps(attempt.observation, sort_keys=True, separators=(",", ":"))
            ),
        ),
    )
    return LiveSmokeArtifact(
        reproducibility=reproducibility,
        provider_configuration=LiveProviderPin(
            provider=configuration.provider,
            model=configuration.model,
            endpoint_digest=digest(configuration.endpoint),
            configuration_digest=configuration.configuration_digest,
            synthetic_case_id=configuration.synthetic_case_id,
            reversible=True,
        ),
        cases=(
            ArtifactCase(
                case_id=configuration.synthetic_case_id,
                case_schema_version=1,
                expected_trials=1,
                observed_trials=1,
                seeds=(0,),
                correlations=correlations,
                observation_digests=(deterministic_observation_digest(scenarios, {}),),
                scenarios=scenarios,
                test_results={},
                verdict=CaseVerdict(status=status, invariant_violations=()),
            ),
        ),
        summary=AvailabilitySummary(
            attempted=attempt.attempted,
            available=attempt.available,
            reversible=True,
        ),
        limitations=(
            "This report measures one pinned provider endpoint at one moment.",
            "Provider availability cannot determine deterministic correctness.",
        ),
    )


__all__: list[str] = []
