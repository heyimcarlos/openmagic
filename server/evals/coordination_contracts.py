"""Immutable contracts and corpus for the paired coordination evaluation."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

CoordinationProfile = Literal["legacy", "workflow"]
CoordinationOutcome = Literal["delegated", "proposed", "clarified", "no_match", "failed"]
WorkflowOutcome = Literal["proposed", "clarified", "no_match"]
ScenarioPhase = Literal["paired", "recovery"]


class _EvidenceModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CoordinationDiagnostics(_EvidenceModel):
    """Soft trajectory measurements that never decide V0 correctness."""

    model_calls: int = Field(ge=0)
    tool_calls: tuple[str, ...]
    search_calls: int = Field(ge=0)
    packet_reads: int = Field(ge=0)
    max_context_bytes: int = Field(ge=0)
    approximate_context_tokens: int = Field(ge=0)
    model_duration_ms: float = Field(ge=0)
    local_tool_duration_ms: float = Field(ge=0)


class CoordinationTrial(_EvidenceModel):
    """One bounded observation for one scenario and Interaction profile."""

    scenario_id: str = Field(pattern=r"^[a-z][a-z0-9-]{0,63}$")
    profile: CoordinationProfile
    model: str = Field(min_length=1, max_length=255)
    application_build: str = Field(min_length=1, max_length=255)
    outcome: CoordinationOutcome
    correctness: bool | None
    response_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    selected_workflow_id: UUID | None = None
    mutated_workflow_ids: tuple[UUID, ...] = ()
    diagnostics: CoordinationDiagnostics

    @model_validator(mode="after")
    def keep_verdict_on_workflow_trials(self) -> CoordinationTrial:
        if self.profile == "legacy" and self.correctness is not None:
            raise ValueError("Legacy correctness is diagnostic and must be null")
        if self.profile == "workflow" and self.correctness is None:
            raise ValueError("Workflow trials require a correctness verdict")
        return self


class CoordinationReport(_EvidenceModel):
    """One paired evidence report with a strict V0 verdict."""

    schema_version: Literal[1] = 1
    run_id: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}$")
    generated_at: datetime
    v0_passed: bool
    workflow_trials: int = Field(ge=1)
    baseline_trials: int = Field(ge=1)
    trials: tuple[CoordinationTrial, ...]


class CoordinationScenario(_EvidenceModel):
    """One synthetic Broker request and its required Workflow outcome."""

    scenario_id: str = Field(pattern=r"^[a-z][a-z0-9-]{0,63}$")
    request: str = Field(min_length=1, max_length=2_000)
    expected_outcome: WorkflowOutcome
    expected_workflow_id: UUID | None = None
    expected_workflow_jobs: int = Field(ge=0, le=20)
    phase: ScenarioPhase = "paired"
    irrelevant_legacy_agents: tuple[str, ...] = ()

    @model_validator(mode="after")
    def require_target_only_for_proposal(self) -> CoordinationScenario:
        proposes = self.expected_outcome == "proposed"
        if proposes != (self.expected_workflow_id is not None):
            raise ValueError("Only a proposed outcome has an expected Workflow")
        if proposes != (self.expected_workflow_jobs > 0):
            raise ValueError("Only a proposed outcome creates Workflow Jobs")
        return self


TARGET_RENEWAL_WORKFLOW_ID = UUID("40000000-0000-0000-0000-000000000001")

RENEWAL_COORDINATION_SCENARIOS: tuple[CoordinationScenario, ...] = (
    CoordinationScenario(
        scenario_id="unique-renewal",
        request="Prepare John Smith's 2026 renewal email at Acme Brokerage.",
        expected_outcome="proposed",
        expected_workflow_id=TARGET_RENEWAL_WORKFLOW_ID,
        expected_workflow_jobs=2,
    ),
    CoordinationScenario(
        scenario_id="ambiguous-renewal",
        request="Prepare John's renewal email.",
        expected_outcome="clarified",
        expected_workflow_jobs=0,
    ),
    CoordinationScenario(
        scenario_id="missing-renewal",
        request="Prepare Zelda Zephyr's renewal email.",
        expected_outcome="no_match",
        expected_workflow_jobs=0,
    ),
    CoordinationScenario(
        scenario_id="authorization-distractor",
        request="Prepare John Smith's urgent 2026 renewal email at Acme Brokerage.",
        expected_outcome="proposed",
        expected_workflow_id=TARGET_RENEWAL_WORKFLOW_ID,
        expected_workflow_jobs=2,
    ),
    CoordinationScenario(
        scenario_id="irrelevant-context",
        request="Prepare John Smith's 2026 renewal email at Acme Brokerage.",
        expected_outcome="proposed",
        expected_workflow_id=TARGET_RENEWAL_WORKFLOW_ID,
        expected_workflow_jobs=2,
        irrelevant_legacy_agents=(
            "Calendar cleanup",
            "Conference travel",
            "Expense reconciliation",
            "Flight search",
            "GitHub issue triage",
            "Hotel booking",
            "Invoice followup",
            "Meal planning",
            "Meeting notes",
            "Package tracking",
            "Team survey",
            "Weather research",
        ),
    ),
    CoordinationScenario(
        scenario_id="duplicate-cause-renewal",
        request="Prepare John Smith's 2026 renewal email at Acme Brokerage.",
        expected_outcome="proposed",
        expected_workflow_id=TARGET_RENEWAL_WORKFLOW_ID,
        expected_workflow_jobs=2,
        phase="recovery",
    ),
)


__all__ = [
    "RENEWAL_COORDINATION_SCENARIOS",
    "CoordinationDiagnostics",
    "CoordinationOutcome",
    "CoordinationProfile",
    "CoordinationReport",
    "CoordinationScenario",
    "CoordinationTrial",
]
