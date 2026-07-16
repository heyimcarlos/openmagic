"""Derived repository, installed-package, and cold-schema surface evidence."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from openmagic_evals.evidence.pins import ReproducibilityPin
from openmagic_evals.evidence.release_models import SCHEMA_VERSION


class _SurfaceModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class RepositorySurfaceEvidence(_SurfaceModel):
    audited_distributions: tuple[str, ...]
    production_dependency_edges: tuple[str, ...]
    private_persistence_packages: tuple[str, ...]
    violations: tuple[str, ...]
    passed: bool

    @model_validator(mode="after")
    def validate_verdict(self) -> RepositorySurfaceEvidence:
        if self.passed != (not self.violations):
            raise ValueError("repository surface verdict must derive from recorded violations")
        return self


class InstalledSurfaceEvidence(_SurfaceModel):
    distributions: dict[str, str]
    production_dependency_edges: tuple[str, ...]
    private_persistence_packages: tuple[str, ...]
    audited_files: int = Field(gt=0)
    violations: tuple[str, ...]
    passed: bool

    @model_validator(mode="after")
    def validate_verdict(self) -> InstalledSurfaceEvidence:
        if self.passed != (not self.violations):
            raise ValueError("installed surface verdict must derive from recorded violations")
        return self


class ColdSchemaEvidence(_SurfaceModel):
    schemas: tuple[str, ...]
    tables: dict[str, tuple[str, ...]]
    migration_heads: dict[str, str]
    legacy_relations: tuple[str, ...]
    violations: tuple[str, ...]
    passed: bool

    @model_validator(mode="after")
    def validate_verdict(self) -> ColdSchemaEvidence:
        if self.passed != (not self.violations and not self.legacy_relations):
            raise ValueError("cold schema verdict must reject violations and legacy relations")
        return self


class SurfaceAuditSummary(_SurfaceModel):
    repository_passed: bool
    installed_surface_passed: bool
    cold_schema_passed: bool
    strict_pass: bool

    @model_validator(mode="after")
    def validate_summary(self) -> SurfaceAuditSummary:
        if self.strict_pass != (
            self.repository_passed and self.installed_surface_passed and self.cold_schema_passed
        ):
            raise ValueError("surface audit strict verdict must derive from every audit")
        return self


class SurfaceAuditArtifact(_SurfaceModel):
    schema_version: Literal["openmagic.enterprise-evidence.v1"] = SCHEMA_VERSION
    artifact_kind: Literal["surface_audit"] = "surface_audit"
    lane: Literal["installable_surface"] = "installable_surface"
    reproducibility: ReproducibilityPin
    repository: RepositorySurfaceEvidence
    installed: InstalledSurfaceEvidence
    cold_schema: ColdSchemaEvidence
    summary: SurfaceAuditSummary
    limitations: tuple[str, ...]

    @model_validator(mode="after")
    def validate_surface_audit(self) -> SurfaceAuditArtifact:
        if self.summary.repository_passed != self.repository.passed:
            raise ValueError("repository audit summary contradicts its evidence")
        if self.summary.installed_surface_passed != self.installed.passed:
            raise ValueError("installed audit summary contradicts its evidence")
        if self.summary.cold_schema_passed != self.cold_schema.passed:
            raise ValueError("cold schema summary contradicts its evidence")
        return self


__all__ = [
    "ColdSchemaEvidence",
    "InstalledSurfaceEvidence",
    "RepositorySurfaceEvidence",
    "SurfaceAuditArtifact",
    "SurfaceAuditSummary",
]
