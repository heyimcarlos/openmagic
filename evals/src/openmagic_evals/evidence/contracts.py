"""Canonical serialization facade for private enterprise evidence artifacts."""

from __future__ import annotations

import json
from typing import Annotated

from pydantic import Field, TypeAdapter

from openmagic_evals.evidence.agent_aggregation import (
    AgentAggregate,
    AgentCaseSummary,
    AgentConfigurationSummary,
    AgentQualitySummary,
    AgentSplitSummary,
    aggregate_agent_trials,
    summarize_agent_cases,
    summarize_agent_configurations,
    summarize_agent_quality,
)
from openmagic_evals.evidence.agent_artifact import (
    AgentConfigurationPin,
    AgentCorpusPin,
    AgentQualityArtifact,
)
from openmagic_evals.evidence.agent_scoring import (
    AgentCandidateObservation,
    AgentScorerContract,
    BoundaryAgentCandidateObservation,
    BoundaryAgentScorerContract,
    RenewalAgentCandidateObservation,
    RenewalAgentScorerContract,
    agent_rubric_scores,
)
from openmagic_evals.evidence.agent_trial_models import (
    AgentCaseEvidence,
    AgentTrialEvidence,
    SanitizedAgentEvent,
)
from openmagic_evals.evidence.availability_models import (
    AvailabilitySummary,
    LiveProviderPin,
    LiveSmokeArtifact,
)
from openmagic_evals.evidence.core_models import (
    AgentCorrelations,
    ApplicationCorrelations,
    CaseVerdict,
    Correlations,
    DistributionSummary,
    InstanceDefinitionCorrelation,
    ProcessCorrelations,
    ProviderCorrelations,
    RuntimeCorrelations,
    SanitizedObservation,
    canonical_digest,
    has_correlations,
    merge_correlations,
    validate_correlated_definitions,
)
from openmagic_evals.evidence.deterministic_models import (
    REQUIRED_NEGATIVE_CLAIMS,
    SCHEMA_VERSION,
    ArtifactCase,
    DeterministicArtifact,
    DeterministicScenarioEvidence,
    DeterministicSummary,
    deterministic_observation_digest,
)
from openmagic_evals.evidence.pins import (
    BuildPin,
    EnvironmentVariablePin,
    ExecutablePin,
    PostgresDeploymentPin,
    ReproducibilityPin,
    WheelArchivePin,
)
from openmagic_evals.evidence.playground_models import PlaygroundArtifact, PlaygroundSummary
from openmagic_evals.evidence.process_models import (
    AttemptAuthorityEvidence,
    DeliveryAuthorityEvidence,
    ForcedProcessLoss,
    ProcessArtifact,
    ProcessCase,
    ProcessContract,
    ProcessIdentityEvidence,
    ProcessMetrics,
    ProcessObservation,
    QueueDepth,
)
from openmagic_evals.evidence.race_models import (
    RaceArtifact,
    RaceCase,
    RaceTrialEvidence,
    race_trial_digest,
)
from openmagic_evals.evidence.surface_models import (
    ColdSchemaEvidence,
    InstalledSurfaceEvidence,
    RepositorySurfaceEvidence,
    SurfaceAuditArtifact,
    SurfaceAuditSummary,
)

Artifact = Annotated[
    DeterministicArtifact
    | RaceArtifact
    | ProcessArtifact
    | AgentQualityArtifact
    | LiveSmokeArtifact
    | PlaygroundArtifact
    | SurfaceAuditArtifact,
    Field(discriminator="artifact_kind"),
]
_ARTIFACT_ADAPTER = TypeAdapter(Artifact)


def parse_artifact(document: str | bytes) -> Artifact:
    return _ARTIFACT_ADAPTER.validate_json(document)


def canonical_artifact_json(artifact: Artifact) -> str:
    value = artifact.model_dump(mode="json")
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n"


def artifact_json_schema() -> dict[str, object]:
    return _ARTIFACT_ADAPTER.json_schema()


__all__ = [
    "REQUIRED_NEGATIVE_CLAIMS",
    "SCHEMA_VERSION",
    "AgentAggregate",
    "AgentCandidateObservation",
    "AgentCaseEvidence",
    "AgentCaseSummary",
    "AgentConfigurationPin",
    "AgentConfigurationSummary",
    "AgentCorpusPin",
    "AgentCorrelations",
    "AgentQualityArtifact",
    "AgentQualitySummary",
    "AgentScorerContract",
    "AgentSplitSummary",
    "AgentTrialEvidence",
    "ApplicationCorrelations",
    "Artifact",
    "ArtifactCase",
    "AttemptAuthorityEvidence",
    "AvailabilitySummary",
    "BoundaryAgentCandidateObservation",
    "BoundaryAgentScorerContract",
    "BuildPin",
    "CaseVerdict",
    "ColdSchemaEvidence",
    "Correlations",
    "DeliveryAuthorityEvidence",
    "DeterministicArtifact",
    "DeterministicScenarioEvidence",
    "DeterministicSummary",
    "DistributionSummary",
    "EnvironmentVariablePin",
    "ExecutablePin",
    "ForcedProcessLoss",
    "InstalledSurfaceEvidence",
    "InstanceDefinitionCorrelation",
    "LiveProviderPin",
    "LiveSmokeArtifact",
    "PlaygroundArtifact",
    "PlaygroundSummary",
    "PostgresDeploymentPin",
    "ProcessArtifact",
    "ProcessCase",
    "ProcessContract",
    "ProcessCorrelations",
    "ProcessIdentityEvidence",
    "ProcessMetrics",
    "ProcessObservation",
    "ProviderCorrelations",
    "QueueDepth",
    "RaceArtifact",
    "RaceCase",
    "RaceTrialEvidence",
    "RenewalAgentCandidateObservation",
    "RenewalAgentScorerContract",
    "RepositorySurfaceEvidence",
    "ReproducibilityPin",
    "RuntimeCorrelations",
    "SanitizedAgentEvent",
    "SanitizedObservation",
    "SurfaceAuditArtifact",
    "SurfaceAuditSummary",
    "WheelArchivePin",
    "agent_rubric_scores",
    "aggregate_agent_trials",
    "artifact_json_schema",
    "canonical_artifact_json",
    "canonical_digest",
    "deterministic_observation_digest",
    "has_correlations",
    "merge_correlations",
    "parse_artifact",
    "race_trial_digest",
    "summarize_agent_cases",
    "summarize_agent_configurations",
    "summarize_agent_quality",
    "validate_correlated_definitions",
]
