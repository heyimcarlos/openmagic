"""Shared build and environment pins for every enterprise evidence lane."""

from __future__ import annotations

import base64
import csv
import hashlib
import io
import zipfile
from datetime import datetime
from importlib.metadata import distribution, version
from importlib.util import find_spec
from pathlib import Path
from typing import Literal
from urllib.parse import unquote, urlparse

from example_insurance.renewal_definition import RENEWAL_DEFINITION
from example_insurance.verification_definition import VERIFICATION_DEFINITION
from openmagic_runtime.evidence import content_fingerprint
from pydantic import JsonValue, TypeAdapter

from openmagic_evals.evidence._execution_config import (
    fixed_executable_path,
    fixed_execution_environment,
)
from openmagic_evals.evidence._owned_command import capture_owned_command
from openmagic_evals.evidence.contracts import (
    BuildPin,
    ReproducibilityPin,
    WheelArchivePin,
    canonical_digest,
)
from openmagic_evals.evidence.pins import (
    REQUIRED_EXECUTABLES,
    EnvironmentVariablePin,
    ExecutablePin,
    PostgresDeploymentPin,
)
from openmagic_evals.evidence.race_definitions import (
    SIGNAL_RELEASE_DEFINITION,
    evidence_race_definitions,
)
from openmagic_evals.harness._postgres import POSTGRES_IMAGE

_DISTRIBUTIONS = (
    "example-insurance",
    "openmagic-api",
    "openmagic-evals",
    "openmagic-playground",
    "openmagic-runtime",
)
_DISTRIBUTION_PACKAGES = {
    "example-insurance": "example_insurance",
    "openmagic-api": "openmagic_api",
    "openmagic-evals": "openmagic_evals",
    "openmagic-playground": "openmagic_playground",
    "openmagic-runtime": "openmagic_runtime",
}
_DISTRIBUTION_SOURCE_ROOTS = {
    "example-insurance": Path("reference-apps/example-insurance/src/example_insurance"),
    "openmagic-api": Path("apps/api/src/openmagic_api"),
    "openmagic-evals": Path("evals/src/openmagic_evals"),
    "openmagic-playground": Path("apps/playground/src/openmagic_playground"),
    "openmagic-runtime": Path("packages/openmagic-runtime/src/openmagic_runtime"),
}


def sha256(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _execution_pins() -> dict[str, ExecutablePin]:
    return {
        name: ExecutablePin(
            path=str(path),
            content_digest=sha256(path.read_bytes()),
        )
        for name in sorted(REQUIRED_EXECUTABLES)
        for path in (fixed_executable_path(name),)
    }


def _environment_pins() -> dict[str, EnvironmentVariablePin]:
    return {
        name: EnvironmentVariablePin(value=value, digest=canonical_digest(value))
        for name, value in fixed_execution_environment().items()
    }


def _git(root: Path, *arguments: str) -> str:
    completed = capture_owned_command(
        (str(fixed_executable_path("git")), *arguments),
        working_directory=root,
        environment=fixed_execution_environment(),
        timeout_seconds=30,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"pinned Git command failed: {completed.stderr.strip()}")
    return completed.stdout.strip()


def _package_digest(package_root: Path) -> str:
    content = hashlib.sha256()
    for path in sorted(package_root.rglob("*")):
        relative = path.relative_to(package_root.parent)
        if not path.is_file() or any(
            part == "__pycache__" or part.startswith(".") for part in relative.parts
        ):
            continue
        content.update(relative.as_posix().encode())
        content.update(b"\0")
        content.update(path.read_bytes())
        content.update(b"\0")
    return "sha256:" + content.hexdigest()


def _distribution_digest(name: str) -> str:
    package_name = _DISTRIBUTION_PACKAGES[name]
    package_spec = find_spec(package_name)
    if package_spec is None or package_spec.origin is None:
        raise RuntimeError(f"installed distribution package is unavailable: {package_name}")
    return _package_digest(Path(package_spec.origin).parent)


def _direct_url(name: str) -> dict[str, JsonValue] | None:
    item = distribution(name)
    direct_url = next(
        (
            Path(str(item.locate_file(file)))
            for file in (item.files or ())
            if Path(str(file)).name == "direct_url.json"
        ),
        None,
    )
    if direct_url is None or not direct_url.is_file():
        return None
    return TypeAdapter(dict[str, JsonValue]).validate_json(direct_url.read_text(encoding="utf-8"))


def _installation_kind(name: str) -> Literal["wheel", "editable"]:
    document = _direct_url(name)
    if document is None:
        return "wheel"
    directory = document.get("dir_info")
    return (
        "editable" if isinstance(directory, dict) and directory.get("editable") is True else "wheel"
    )


def _member_digest(archive: zipfile.ZipFile, names: tuple[str, ...]) -> str:
    content = hashlib.sha256()
    for name in names:
        content.update(name.encode())
        content.update(b"\0")
        content.update(archive.read(name))
        content.update(b"\0")
    return "sha256:" + content.hexdigest()


def _wheel_archive_pin(name: str) -> WheelArchivePin:
    installed = distribution(name)
    document = _direct_url(name)
    if document is None:
        raise RuntimeError(f"wheel installation has no local archive provenance: {name}")
    url = document.get("url")
    if not isinstance(url, str):
        raise RuntimeError(f"wheel installation has no local archive provenance: {name}")
    parsed = urlparse(url)
    if parsed.scheme != "file" or parsed.netloc not in {"", "localhost"}:
        raise RuntimeError(f"wheel installation provenance is not a local file: {name}")
    wheel = Path(unquote(parsed.path))
    if not wheel.is_file() or wheel.suffix != ".whl":
        raise RuntimeError(f"wheel installation archive is unavailable: {name}")
    archive_digest = sha256(wheel.read_bytes())
    archive_info = document.get("archive_info")
    archive_hash = archive_info.get("hash") if isinstance(archive_info, dict) else None
    if isinstance(archive_hash, str):
        expected = archive_hash.replace("sha256=", "sha256:", 1)
        if expected != archive_digest:
            raise RuntimeError(f"wheel archive differs from its installer provenance: {name}")
    with zipfile.ZipFile(wheel) as archive:
        member_names = tuple(item.filename for item in archive.infolist() if not item.is_dir())
        if len(set(member_names)) != len(member_names):
            raise RuntimeError(f"wheel contains duplicate archive members: {name}")
        members = tuple(sorted(member_names))
        records = tuple(member for member in members if member.endswith(".dist-info/RECORD"))
        if len(records) != 1:
            raise RuntimeError(f"wheel must contain exactly one RECORD: {name}")
        record_name = records[0]
        dist_info = record_name.rsplit("/", 1)[0]
        required_metadata = (f"{dist_info}/METADATA", f"{dist_info}/WHEEL")
        if any(members.count(member) != 1 for member in required_metadata):
            raise RuntimeError(f"wheel must contain exact METADATA and WHEEL members: {name}")
        record_bytes = archive.read(record_name)
        record_rows = tuple(csv.reader(io.StringIO(record_bytes.decode("utf-8"))))
        if any(len(row) != 3 or not row[0] for row in record_rows):
            raise RuntimeError(f"wheel RECORD contains a malformed row: {name}")
        record_paths = tuple(row[0] for row in record_rows)
        if len(set(record_paths)) != len(record_paths):
            raise RuntimeError(f"wheel RECORD contains duplicate paths: {name}")
        rows = {row[0]: (row[1], row[2]) for row in record_rows}
        if set(rows) != set(members):
            raise RuntimeError(f"wheel RECORD does not enumerate every archive member: {name}")
        for member in members:
            digest, size = rows[member]
            if member == record_name:
                if digest or size:
                    raise RuntimeError(f"wheel RECORD must leave its own digest empty: {name}")
                continue
            data = archive.read(member)
            encoded = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
            if digest != f"sha256={encoded}" or size != str(len(data)):
                raise RuntimeError(f"wheel RECORD hash or size mismatch: {name}:{member}")
            installed_path = Path(str(installed.locate_file(member)))
            if not installed_path.is_file() or installed_path.read_bytes() != data:
                raise RuntimeError(
                    f"installed distribution differs from pinned wheel: {name}:{member}"
                )
        metadata = tuple(
            member for member in members if ".dist-info/" in member and member != record_name
        )
        if not metadata:
            raise RuntimeError(f"wheel contains no distribution metadata: {name}")
        return WheelArchivePin(
            filename=wheel.name,
            archive_digest=archive_digest,
            record_digest=sha256(record_bytes),
            metadata_digest=_member_digest(archive, metadata),
        )


def build_pin(root: Path) -> BuildPin:
    status = _git(root, "status", "--porcelain", "--untracked-files=normal")
    installed_digests = {name: _distribution_digest(name) for name in _DISTRIBUTIONS}
    installation_kinds = {name: _installation_kind(name) for name in _DISTRIBUTIONS}
    return BuildPin(
        git_sha=_git(root, "rev-parse", "HEAD"),
        checkout_clean=not status,
        lock_digest=sha256((root / "uv.lock").read_bytes()),
        distributions={name: version(name) for name in _DISTRIBUTIONS},
        distribution_digests=installed_digests,
        source_distribution_digests={
            name: _package_digest(root / _DISTRIBUTION_SOURCE_ROOTS[name])
            for name in _DISTRIBUTIONS
        },
        installation_kinds=installation_kinds,
        wheel_archives={
            name: _wheel_archive_pin(name)
            for name, kind in installation_kinds.items()
            if kind == "wheel"
        },
    )


def reproducibility_pin(
    root: Path,
    *,
    command: tuple[str, ...],
    started_at: datetime,
    finished_at: datetime,
    timeout_seconds: int,
    case_corpus_digest: str,
    postgres_deployments: tuple[PostgresDeploymentPin, ...],
    postgres_provenance: Literal["required", "not_applicable"] = "required",
) -> ReproducibilityPin:
    definitions = _pinned_definition_digests()
    return ReproducibilityPin(
        build=build_pin(root),
        suite_version="issue-71.v1",
        command=command,
        environment=_environment_pins(),
        executables=_execution_pins(),
        started_at=started_at,
        finished_at=finished_at,
        timeout_seconds=timeout_seconds,
        postgres_provenance=postgres_provenance,
        postgres_deployments=postgres_deployments,
        definition_digests=definitions,
        case_corpus_digest=case_corpus_digest,
        sandbox_digest=sha256(POSTGRES_IMAGE.encode()),
    )


def _pinned_definition_digests() -> dict[str, str]:
    """Return every exact Definition identity observable in release evidence."""

    return {
        f"{definition.identity.key}:{definition.identity.version}": "sha256:"
        + content_fingerprint(definition)
        for definition in (
            RENEWAL_DEFINITION,
            VERIFICATION_DEFINITION,
            SIGNAL_RELEASE_DEFINITION,
            *evidence_race_definitions(),
        )
    }


__all__ = [
    "build_pin",
    "fixed_executable_path",
    "fixed_execution_environment",
    "reproducibility_pin",
    "sha256",
]
