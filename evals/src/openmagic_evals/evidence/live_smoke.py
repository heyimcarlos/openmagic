"""Opt-in live provider availability evidence, separate from correctness."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from openmagic_evals.evidence.artifact_io import write_artifact
from openmagic_evals.evidence.contracts import (
    ArtifactCase,
    AvailabilitySummary,
    CaseVerdict,
    Correlations,
    DeterministicScenarioEvidence,
    LiveProviderPin,
    LiveSmokeArtifact,
    ProviderCorrelations,
    deterministic_observation_digest,
)
from openmagic_evals.evidence.deadline import bounded_evidence
from openmagic_evals.evidence.reproducibility import reproducibility_pin


def _digest(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode()).hexdigest()


def _document_digest(value: dict[str, object]) -> str:
    return _digest(json.dumps(value, sort_keys=True, separators=(",", ":")))


def provider_configuration_digest(*, provider: str, model: str, endpoint: str) -> str:
    return _digest(
        json.dumps(
            {"endpoint": endpoint, "model": model, "provider": provider},
            sort_keys=True,
            separators=(",", ":"),
        )
    )


def _contains_marker(value: object, marker: str) -> bool:
    if isinstance(value, dict):
        return any(_contains_marker(item, marker) for item in value.values())
    if isinstance(value, list):
        return any(_contains_marker(item, marker) for item in value)
    return isinstance(value, str) and marker in value


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
    command_parts = [
        "openmagic-evidence",
        "live-smoke",
        "--repository-root",
        str(repository_root.resolve()),
        "--output",
        str(output.resolve()),
        "--provider",
        provider,
        "--model",
        model,
        "--endpoint",
        endpoint,
        "--synthetic-case-id",
        synthetic_case_id,
        "--timeout-seconds",
        str(timeout_seconds),
    ]
    actual_configuration_digest = provider_configuration_digest(
        provider=provider, model=model, endpoint=endpoint
    )
    if configuration_digest is not None:
        if configuration_digest != actual_configuration_digest:
            raise ValueError("live configuration digest does not match provider configuration")
        command_parts.extend(("--configuration-digest", configuration_digest))
    if credential_file is not None:
        command_parts.extend(("--credential-file", str(credential_file.resolve())))
    if allow_live:
        command_parts.append("--allow-live")
    command = tuple(command_parts)
    started_at = datetime.now(UTC)
    if allow_live and credential_file is None:
        raise ValueError("live smoke requires an explicit credential file")
    attempted = allow_live and credential_file is not None
    available = False
    provider_request_ids: tuple[str, ...] = ()
    observation: dict[str, object] = {"attempted": attempted, "available": False}
    if attempted:
        if provider != "openai-responses":
            raise ValueError("live smoke supports only the pinned openai-responses contract")
        endpoint_parts = urlsplit(endpoint)
        official_endpoint = endpoint == "https://api.openai.com/v1/responses"
        local_contract_endpoint = (
            endpoint_parts.scheme == "http"
            and endpoint_parts.hostname in {"127.0.0.1", "::1"}
            and endpoint_parts.path == "/v1/responses"
        )
        if not official_endpoint and not local_contract_endpoint:
            raise ValueError("live credential endpoint is outside the provider allowlist")
        mode = credential_file.stat().st_mode & 0o777
        if mode & 0o077:
            raise ValueError("live credential file must not be accessible by group or other")
        credential = credential_file.read_text(encoding="utf-8").strip()
        if not credential:
            raise ValueError("live credential file is empty")
        marker = "OPENMAGIC_SYNTHETIC_SMOKE_OK"
        payload = {
            "model": model,
            "input": f"Return exactly {marker}",
            "metadata": {"openmagic_case_id": synthetic_case_id},
            "store": False,
        }
        request = Request(
            endpoint,
            data=json.dumps(payload, sort_keys=True, separators=(",", ":")).encode(),
            headers={
                "Authorization": f"Bearer {credential}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=timeout_seconds) as response:
                response_document = json.load(response)
                marker_verified = _contains_marker(response_document, marker)
                available = 200 <= response.status < 300 and marker_verified
                request_id = response.headers.get("x-request-id")
                provider_request_ids = (request_id,) if request_id else ()
                observation = {
                    "attempted": True,
                    "available": available,
                    "marker_verified": marker_verified,
                    "status_code": response.status,
                }
        except (OSError, URLError, ValueError, json.JSONDecodeError):
            available = False
            observation = {
                "attempted": True,
                "available": False,
                "marker_verified": False,
            }
    finished_at = datetime.now(UTC)
    status = "passed" if available else "unavailable" if not attempted else "infrastructure_error"
    correlations = Correlations(
        provider=ProviderCorrelations(provider_request_ids=provider_request_ids)
    )
    scenarios = (
        DeterministicScenarioEvidence(
            scenario_id=synthetic_case_id,
            correlations=correlations,
            observation=observation,
            observation_digest=_document_digest(observation),
        ),
    )
    artifact = LiveSmokeArtifact(
        reproducibility=reproducibility_pin(
            repository_root.resolve(),
            command=command,
            started_at=started_at,
            finished_at=finished_at,
            timeout_seconds=timeout_seconds,
            postgres_deployments=(),
            case_corpus_digest=_digest(synthetic_case_id),
        ),
        provider_configuration=LiveProviderPin(
            provider=provider,
            model=model,
            endpoint_digest=_digest(endpoint),
            configuration_digest=actual_configuration_digest,
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
                correlations=correlations,
                observation_digests=(deterministic_observation_digest(scenarios, {}),),
                scenarios=scenarios,
                test_results={},
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
    write_artifact(output, artifact)
    return artifact


__all__ = ["provider_configuration_digest", "run_live_smoke"]
