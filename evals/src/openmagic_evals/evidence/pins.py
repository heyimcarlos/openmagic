"""Exact build and execution provenance contracts for evidence artifacts."""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

_SHA256 = re.compile(r"sha256:[0-9a-f]{64}")
_GIT_SHA = re.compile(r"[0-9a-f]{40}")


class _PinModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def _require_digest(value: str, field: str) -> None:
    if _SHA256.fullmatch(value) is None:
        raise ValueError(f"{field} must be a lowercase SHA-256 digest")


class WheelArchivePin(_PinModel):
    filename: str
    archive_digest: str
    record_digest: str
    metadata_digest: str

    @model_validator(mode="after")
    def validate_wheel(self) -> WheelArchivePin:
        if Path(self.filename).name != self.filename or not self.filename.endswith(".whl"):
            raise ValueError("wheel archive pin must store one basename only")
        _require_digest(self.archive_digest, "wheel archive digest")
        _require_digest(self.record_digest, "wheel RECORD digest")
        _require_digest(self.metadata_digest, "wheel metadata digest")
        return self


class BuildPin(_PinModel):
    git_sha: str
    checkout_clean: bool
    lock_digest: str
    distributions: dict[str, str]
    distribution_digests: dict[str, str]
    source_distribution_digests: dict[str, str]
    installation_kinds: dict[str, Literal["wheel", "editable"]]
    wheel_archives: dict[str, WheelArchivePin]

    @model_validator(mode="after")
    def validate_build(self) -> BuildPin:
        if _GIT_SHA.fullmatch(self.git_sha) is None:
            raise ValueError("git_sha must be a full lowercase Git SHA")
        if not self.checkout_clean:
            raise ValueError("admissible evidence requires a clean checkout")
        _require_digest(self.lock_digest, "lock_digest")
        if not self.distributions:
            raise ValueError("distribution versions must be pinned")
        if set(self.distribution_digests) != set(self.distributions):
            raise ValueError("every installed distribution must have one content digest")
        if self.source_distribution_digests != self.distribution_digests:
            raise ValueError("installed distribution contents must match the pinned source tree")
        if set(self.installation_kinds) != set(self.distributions):
            raise ValueError("every installed distribution must declare its installation kind")
        wheel_distributions = {
            name for name, kind in self.installation_kinds.items() if kind == "wheel"
        }
        if set(self.wheel_archives) != wheel_distributions:
            raise ValueError("every wheel installation must pin its exact archive and metadata")
        for digest in self.distribution_digests.values():
            _require_digest(digest, "distribution digest")
        return self


class ReproducibilityPin(_PinModel):
    build: BuildPin
    suite_version: str
    command: tuple[str, ...]
    environment_allowlist: tuple[str, ...]
    started_at: datetime
    finished_at: datetime
    timeout_seconds: int = Field(gt=0)
    postgres_version: str
    postgres_image: str
    postgres_configuration: dict[str, str]
    postgres_configuration_digest: str
    migration_heads: dict[str, str]
    definition_digests: dict[str, str]
    case_corpus_digest: str | None = None
    sandbox_digest: str | None = None

    @model_validator(mode="after")
    def validate_reproducibility(self) -> ReproducibilityPin:
        if not self.suite_version or not self.command:
            raise ValueError("suite version and exact command are required")
        if self.finished_at < self.started_at:
            raise ValueError("finished_at cannot precede started_at")
        if "@sha256:" not in self.postgres_image or not self.postgres_configuration:
            raise ValueError("PostgreSQL image and observed configuration must be pinned")
        _require_digest(self.postgres_configuration_digest, "postgres_configuration_digest")
        if self.case_corpus_digest is not None:
            _require_digest(self.case_corpus_digest, "case_corpus_digest")
        if self.sandbox_digest is not None:
            _require_digest(self.sandbox_digest, "sandbox_digest")
        if not self.migration_heads or not self.definition_digests:
            raise ValueError("migration heads and Definition digests are required")
        return self


__all__ = ["BuildPin", "ReproducibilityPin", "WheelArchivePin"]
