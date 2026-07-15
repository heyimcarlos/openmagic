"""Opt-in live provider availability evidence, separate from correctness."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

from openmagic_evals.evidence.contracts import (
    ArtifactCase,
    AvailabilitySummary,
    CaseVerdict,
    Correlations,
    LiveProviderPin,
    LiveSmokeArtifact,
    canonical_artifact_json,
    parse_artifact,
)
from openmagic_evals.evidence.redaction import audit_redaction
from openmagic_evals.evidence.release import reproducibility_pin


def _digest(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode()).hexdigest()


def run_live_smoke(
    *,
    repository_root: Path,
    output: Path,
    provider: str,
    model: str,
    endpoint: str,
    configuration_digest: str,
    synthetic_case_id: str,
    credential_file: Path | None,
    allow_live: bool,
    timeout_seconds: int = 10,
) -> LiveSmokeArtifact:
    command = (
        "openmagic-evidence",
        "live-smoke",
        "--provider",
        provider,
        "--model",
        model,
        "--synthetic-case-id",
        synthetic_case_id,
    )
    started_at = datetime.now(UTC)
    attempted = allow_live and credential_file is not None
    available = False
    observation = {"attempted": attempted, "available": False}
    if attempted:
        mode = credential_file.stat().st_mode & 0o777
        if mode & 0o077:
            raise ValueError("live credential file must not be accessible by group or other")
        credential = credential_file.read_text(encoding="utf-8").strip()
        if not credential:
            raise ValueError("live credential file is empty")
        request = Request(endpoint, headers={"Authorization": f"Bearer {credential}"})
        try:
            with urlopen(request, timeout=timeout_seconds) as response:
                available = 200 <= response.status < 300
        except (OSError, URLError):
            available = False
        observation = {"attempted": True, "available": available}
    finished_at = datetime.now(UTC)
    status = "passed" if available else "unavailable" if not attempted else "infrastructure_error"
    artifact = LiveSmokeArtifact(
        reproducibility=reproducibility_pin(
            repository_root.resolve(),
            command=command,
            started_at=started_at,
            finished_at=finished_at,
            timeout_seconds=timeout_seconds,
            case_corpus_digest=_digest(synthetic_case_id),
        ),
        provider_configuration=LiveProviderPin(
            provider=provider,
            model=model,
            endpoint_digest=_digest(endpoint),
            configuration_digest=configuration_digest,
            synthetic_case_id=synthetic_case_id,
            reversible=True,
        ),
        cases=(
            ArtifactCase(
                case_id=synthetic_case_id,
                case_schema_version=1,
                expected_trials=1,
                observed_trials=1,
                seeds=(0,),
                correlations=Correlations(),
                observation_digests=(_digest(json.dumps(observation, sort_keys=True)),),
                verdict=CaseVerdict(status=status, invariant_violations=()),
            ),
        ),
        summary=AvailabilitySummary(
            attempted=attempted,
            available=available,
            reversible=True,
        ),
        limitations=(
            "This report measures one pinned provider endpoint at one moment.",
            "Provider availability cannot determine deterministic correctness.",
        ),
    )
    document = canonical_artifact_json(artifact)
    parse_artifact(document)
    audit_redaction(json.loads(document))
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(document, encoding="utf-8")
    return artifact


__all__ = ["run_live_smoke"]
