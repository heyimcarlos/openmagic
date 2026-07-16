"""Shared build and environment pins for every enterprise evidence lane."""

from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import datetime
from importlib.metadata import distribution, version
from importlib.util import find_spec
from pathlib import Path

import psycopg
from example_insurance.migrations import apply_migrations
from example_insurance.renewal_definition import RENEWAL_DEFINITION
from example_insurance.verification_definition import VERIFICATION_DEFINITION
from openmagic_runtime.evidence import content_fingerprint

from openmagic_evals.evidence.contracts import BuildPin, ReproducibilityPin
from openmagic_evals.evidence.race_transitions import transition_race_definitions
from openmagic_evals.harness._postgres import POSTGRES_IMAGE, postgres_container

_DISTRIBUTIONS = (
    "example-insurance",
    "openmagic-api",
    "openmagic-evals",
    "openmagic-runtime",
)
_DISTRIBUTION_PACKAGES = {
    "example-insurance": "example_insurance",
    "openmagic-api": "openmagic_api",
    "openmagic-evals": "openmagic_evals",
    "openmagic-runtime": "openmagic_runtime",
}


def sha256(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _git(root: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", *arguments], cwd=root, check=True, capture_output=True, text=True
    )
    return completed.stdout.strip()


def _distribution_digest(name: str) -> str:
    item = distribution(name)
    content = hashlib.sha256()
    for file in sorted(item.files or (), key=str):
        relative = Path(str(file))
        is_selected_metadata = any(
            part.endswith(".dist-info") for part in relative.parts
        ) and relative.name in {"METADATA", "WHEEL", "entry_points.txt", "top_level.txt"}
        if not is_selected_metadata:
            continue
        path = Path(str(item.locate_file(file)))
        if path.is_file():
            content.update(relative.as_posix().encode())
            content.update(b"\0")
            content.update(path.read_bytes())
            content.update(b"\0")
    package_name = _DISTRIBUTION_PACKAGES[name]
    package_spec = find_spec(package_name)
    if package_spec is None or package_spec.origin is None:
        raise RuntimeError(f"installed distribution package is unavailable: {package_name}")
    package_root = Path(package_spec.origin).parent
    for path in sorted(package_root.rglob("*")):
        if not path.is_file() or any(
            part == "__pycache__" or part.startswith(".") for part in path.parts
        ):
            continue
        relative = path.relative_to(package_root.parent)
        content.update(relative.as_posix().encode())
        content.update(b"\0")
        content.update(path.read_bytes())
        content.update(b"\0")
    return "sha256:" + content.hexdigest()


def build_pin(root: Path) -> BuildPin:
    status = _git(root, "status", "--porcelain", "--untracked-files=normal")
    return BuildPin(
        git_sha=_git(root, "rev-parse", "HEAD"),
        checkout_clean=not status,
        lock_digest=sha256((root / "uv.lock").read_bytes()),
        distributions={name: version(name) for name in _DISTRIBUTIONS},
        distribution_digests={name: _distribution_digest(name) for name in _DISTRIBUTIONS},
    )


def reproducibility_pin(
    root: Path,
    *,
    command: tuple[str, ...],
    started_at: datetime,
    finished_at: datetime,
    timeout_seconds: int,
    case_corpus_digest: str,
) -> ReproducibilityPin:
    definitions = {
        "example_insurance.renewal_outreach:2": "sha256:" + content_fingerprint(RENEWAL_DEFINITION),
        "example_insurance.verification_delivery:1": "sha256:"
        + content_fingerprint(VERIFICATION_DEFINITION),
    }
    definitions.update(
        {
            f"{definition.identity.key}:{definition.identity.version}": "sha256:"
            + content_fingerprint(definition)
            for definition in transition_race_definitions()
        }
    )
    with postgres_container(database_name="openmagic_test_evidence_pin") as postgres:
        database_url = postgres.get_connection_url(driver=None)
        apply_migrations(database_url)
        with psycopg.connect(database_url) as connection:
            row = connection.execute(
                "SELECT current_setting('server_version'), "
                "current_setting('transaction_isolation'), "
                "current_setting('synchronous_commit'), "
                "current_setting('TimeZone'), "
                "current_setting('max_connections')"
            ).fetchone()
            application_head = connection.execute(
                "SELECT version FROM example_insurance.migration_history "
                "ORDER BY version DESC LIMIT 1"
            ).fetchone()
            runtime_head = connection.execute(
                "SELECT version FROM openmagic_runtime.migration_history "
                "ORDER BY version DESC LIMIT 1"
            ).fetchone()
    if row is None:
        raise RuntimeError("PostgreSQL did not return its observed configuration")
    if application_head is None or runtime_head is None:
        raise RuntimeError("PostgreSQL did not return its observed migration heads")
    postgres_configuration = {
        "max_connections": str(row[4]),
        "synchronous_commit": str(row[2]),
        "timezone": str(row[3]),
        "transaction_isolation": str(row[1]),
    }
    configuration_document = json.dumps(
        postgres_configuration, sort_keys=True, separators=(",", ":")
    ).encode()
    return ReproducibilityPin(
        build=build_pin(root),
        suite_version="issue-71.v1",
        command=command,
        environment_allowlist=("PATH", "PYTHONNOUSERSITE"),
        started_at=started_at,
        finished_at=finished_at,
        timeout_seconds=timeout_seconds,
        postgres_version=str(row[0]),
        postgres_image=POSTGRES_IMAGE,
        postgres_configuration=postgres_configuration,
        postgres_configuration_digest=sha256(configuration_document),
        migration_heads={
            "example_insurance": str(application_head[0]),
            "openmagic_runtime": str(runtime_head[0]),
        },
        definition_digests=definitions,
        case_corpus_digest=case_corpus_digest,
        sandbox_digest=sha256(POSTGRES_IMAGE.encode()),
    )


__all__ = ["build_pin", "reproducibility_pin", "sha256"]
