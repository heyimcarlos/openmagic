"""Exact build and execution provenance contracts for evidence artifacts."""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Literal

from openmagic_runtime.evidence import POSTGRES_EVIDENCE_CONFIGURATION_KEYS
from pydantic import BaseModel, ConfigDict, Field, model_validator

from openmagic_evals.evidence._execution_config import (
    CONFIGURED_EXECUTABLES,
    FIXED_ENVIRONMENT,
)
from openmagic_evals.evidence.core_models import canonical_digest

_SHA256 = re.compile(r"sha256:[0-9a-f]{64}")
_GIT_SHA = re.compile(r"[0-9a-f]{40}")
_DEFINITION_DIGEST_KEY = re.compile(r"[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+:[1-9][0-9]*")
REQUIRED_EXECUTABLES = CONFIGURED_EXECUTABLES


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


class EnvironmentVariablePin(_PinModel):
    value: str
    digest: str

    @model_validator(mode="after")
    def validate_value(self) -> EnvironmentVariablePin:
        _require_digest(self.digest, "environment value digest")
        if self.digest != canonical_digest(self.value):
            raise ValueError("environment value digest does not match its pinned value")
        return self


class ExecutablePin(_PinModel):
    path: str
    content_digest: str

    @model_validator(mode="after")
    def validate_executable(self) -> ExecutablePin:
        if not Path(self.path).is_absolute():
            raise ValueError("executable pin requires an absolute path")
        _require_digest(self.content_digest, "executable content digest")
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


class PostgresDeploymentPin(_PinModel):
    """Observed provenance for one exact PostgreSQL deployment used by a lane."""

    deployment_id: str
    postgres_version: str
    postgres_image: str
    postgres_configuration: dict[str, str]
    postgres_configuration_digest: str
    migration_heads: dict[str, str | None]

    @model_validator(mode="after")
    def validate_deployment(self) -> PostgresDeploymentPin:
        _require_digest(self.deployment_id, "deployment_id")
        if "@sha256:" not in self.postgres_image or not self.postgres_configuration:
            raise ValueError("PostgreSQL image and observed configuration must be pinned")
        if set(self.postgres_configuration) != POSTGRES_EVIDENCE_CONFIGURATION_KEYS:
            raise ValueError("PostgreSQL provenance requires every named configuration value")
        _require_digest(self.postgres_configuration_digest, "postgres_configuration_digest")
        if self.postgres_configuration_digest != canonical_digest(self.postgres_configuration):
            raise ValueError("PostgreSQL configuration digest does not match its document")
        if set(self.migration_heads) != {"example_insurance", "openmagic_runtime"}:
            raise ValueError("both owned migration heads must be observed")
        return self


class ReproducibilityPin(_PinModel):
    build: BuildPin
    suite_version: str
    command: tuple[str, ...]
    environment: dict[str, EnvironmentVariablePin]
    executables: dict[str, ExecutablePin]
    started_at: datetime
    finished_at: datetime
    timeout_seconds: int = Field(gt=0)
    postgres_provenance: Literal["required", "not_applicable"] = "required"
    postgres_deployments: tuple[PostgresDeploymentPin, ...]
    definition_digests: dict[str, str]
    case_corpus_digest: str | None = None
    sandbox_digest: str | None = None

    @model_validator(mode="after")
    def validate_reproducibility(self) -> ReproducibilityPin:
        if not self.suite_version or not self.command:
            raise ValueError("suite version and exact command are required")
        if set(self.environment) != set(FIXED_ENVIRONMENT):
            raise ValueError("evidence environment must pin the complete fixed environment")
        if set(self.executables) != REQUIRED_EXECUTABLES:
            raise ValueError("evidence executables must pin every configured helper")
        if self.finished_at < self.started_at:
            raise ValueError("finished_at cannot precede started_at")
        deployment_ids = tuple(item.deployment_id for item in self.postgres_deployments)
        if len(deployment_ids) != len(set(deployment_ids)):
            raise ValueError("PostgreSQL deployment provenance must be unique")
        if self.postgres_provenance == "required":
            if not self.postgres_deployments:
                raise ValueError("PostgreSQL-backed evidence requires exact deployment provenance")
            if any(None in item.migration_heads.values() for item in self.postgres_deployments):
                raise ValueError("PostgreSQL-backed evidence requires concrete migration heads")
        elif self.postgres_deployments:
            raise ValueError("non-PostgreSQL evidence cannot claim PostgreSQL deployments")
        if self.case_corpus_digest is not None:
            _require_digest(self.case_corpus_digest, "case_corpus_digest")
        if self.sandbox_digest is not None:
            _require_digest(self.sandbox_digest, "sandbox_digest")
        if not self.definition_digests:
            raise ValueError("Definition digests are required")
        if any(_DEFINITION_DIGEST_KEY.fullmatch(key) is None for key in self.definition_digests):
            raise ValueError("Definition digest keys must pin a stable key and positive version")
        for digest in self.definition_digests.values():
            _require_digest(digest, "Definition digest")
        return self


__all__ = [
    "REQUIRED_EXECUTABLES",
    "BuildPin",
    "EnvironmentVariablePin",
    "ExecutablePin",
    "PostgresDeploymentPin",
    "ReproducibilityPin",
    "WheelArchivePin",
]
