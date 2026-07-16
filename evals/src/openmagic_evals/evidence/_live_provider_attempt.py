"""Secret-owning transport phase for one explicitly authorized live smoke attempt."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


@dataclass(frozen=True)
class LiveProviderAttemptRequest:
    provider: str
    model: str
    endpoint: str
    synthetic_case_id: str
    credential_file: Path | None
    allow_live: bool
    timeout_seconds: int


@dataclass(frozen=True)
class LiveProviderAttempt:
    attempted: bool
    available: bool
    provider_request_ids: tuple[str, ...]
    observation: dict[str, object]


def _contains_marker(value: object, marker: str) -> bool:
    if isinstance(value, dict):
        return any(_contains_marker(item, marker) for item in value.values())
    if isinstance(value, list):
        return any(_contains_marker(item, marker) for item in value)
    return isinstance(value, str) and marker in value


def _authorized_endpoint(endpoint: str) -> bool:
    endpoint_parts = urlsplit(endpoint)
    return endpoint == "https://api.openai.com/v1/responses" or (
        endpoint_parts.scheme == "http"
        and endpoint_parts.hostname in {"127.0.0.1", "::1"}
        and endpoint_parts.path == "/v1/responses"
    )


def _credential(path: Path) -> str:
    mode = path.stat().st_mode & 0o777
    if mode & 0o077:
        raise ValueError("live credential file must not be accessible by group or other")
    credential = path.read_text(encoding="utf-8").strip()
    if not credential:
        raise ValueError("live credential file is empty")
    return credential


def execute_live_provider_attempt(request: LiveProviderAttemptRequest) -> LiveProviderAttempt:
    """Execute one reversible synthetic request without returning credential material."""

    if request.allow_live and request.credential_file is None:
        raise ValueError("live smoke requires an explicit credential file")
    if not request.allow_live:
        return LiveProviderAttempt(
            attempted=False,
            available=False,
            provider_request_ids=(),
            observation={"attempted": False, "available": False},
        )
    if request.provider != "openai-responses":
        raise ValueError("live smoke supports only the pinned openai-responses contract")
    if not _authorized_endpoint(request.endpoint):
        raise ValueError("live credential endpoint is outside the provider allowlist")
    credential_file = request.credential_file
    if credential_file is None:
        raise AssertionError("authorized live attempt lost its credential-file identity")
    credential = _credential(credential_file)
    marker = "OPENMAGIC_SYNTHETIC_SMOKE_OK"
    payload = {
        "model": request.model,
        "input": f"Return exactly {marker}",
        "metadata": {"openmagic_case_id": request.synthetic_case_id},
        "store": False,
    }
    http_request = Request(
        request.endpoint,
        data=json.dumps(payload, sort_keys=True, separators=(",", ":")).encode(),
        headers={
            "Authorization": f"Bearer {credential}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urlopen(http_request, timeout=request.timeout_seconds) as response:
            response_document = json.load(response)
            marker_verified = _contains_marker(response_document, marker)
            available = 200 <= response.status < 300 and marker_verified
            request_id = response.headers.get("x-request-id")
            return LiveProviderAttempt(
                attempted=True,
                available=available,
                provider_request_ids=(request_id,) if request_id else (),
                observation={
                    "attempted": True,
                    "available": available,
                    "marker_verified": marker_verified,
                    "status_code": response.status,
                },
            )
    except (OSError, URLError, ValueError, json.JSONDecodeError):
        return LiveProviderAttempt(
            attempted=True,
            available=False,
            provider_request_ids=(),
            observation={
                "attempted": True,
                "available": False,
                "marker_verified": False,
            },
        )


__all__: list[str] = []
