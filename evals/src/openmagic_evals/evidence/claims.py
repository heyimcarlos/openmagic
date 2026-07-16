"""Derived supported-claim report with mandatory negative claims."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from openmagic_evals.evidence.contracts import (
    REQUIRED_NEGATIVE_CLAIMS,
    AgentQualityArtifact,
    Artifact,
    DeterministicArtifact,
    LiveSmokeArtifact,
    PlaygroundArtifact,
    ProcessArtifact,
    RaceArtifact,
    RaceCase,
    SurfaceAuditArtifact,
    has_correlations,
    parse_artifact,
)
from openmagic_evals.evidence.demos import (
    _RENEWAL_DEMONSTRATION_CASE_ID,
    _VERIFICATION_DEMONSTRATION_CASE_ID,
)
from openmagic_evals.evidence.matrix import (
    DETERMINISTIC_RELEASE_MATRIX,
    cardinality_one_races,
    select_pytest_results,
)

_SUPPORTED_CLAIMS = (
    "The tested single-PostgreSQL kernel preserved the pinned Definition, transaction, replay, race, lease, recovery, and retry contracts.",
    "The pinned source, installed wheel module and export surfaces, and cold schemas matched their exact allowlists.",
    "Deterministic and Agent Executors used the same tested Step and Attempt interface.",
    "Application Policy retained authority, completion, retry-safety, and External Effect decisions in the tested cases.",
    "The tested Domain Event and Delivery path recovered to at most one Message in one exact Thread.",
)


@dataclass(frozen=True)
class EvidencePackagePaths:
    """One complete issue 71 evidence package, with no omittable lanes."""

    deterministic: Path
    surface_audit: Path
    agent_quality: Path
    live_smoke: Path
    playground: Path
    processes: Path
    races: Path
    renewal_demo: Path
    verification_demo: Path


@dataclass(frozen=True)
class _ArtifactRequirement:
    name: str
    path: Path
    artifact_type: type[Artifact]
    demonstration_case_id: str | None = None


def _digest(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _common_reproducibility_pin(artifact: Artifact) -> tuple[object, ...]:
    pin = artifact.reproducibility
    return (
        pin.build,
        pin.suite_version,
        pin.definition_digests,
        pin.sandbox_digest,
    )


def _validate_release_matrix(artifact: DeterministicArtifact) -> None:
    cases = {case.case_id: case for case in artifact.cases}
    if len(cases) != len(artifact.cases):
        raise ValueError("claim report requires unique deterministic case identities")
    release_contracts = {case.case_id: case for case in DETERMINISTIC_RELEASE_MATRIX}
    race_contracts = {case.case_id: case for case in cardinality_one_races()}
    if set(cases) != set(release_contracts) | set(race_contracts):
        raise ValueError("claim report requires the complete deterministic release matrix")
    for case_id, contract in release_contracts.items():
        case = cases[case_id]
        if isinstance(case, RaceCase):
            raise ValueError(f"claim report deterministic proof is incomplete: {case_id}")
        selected_results = select_pytest_results(case.test_results, contract.pytest_nodes)
        scenario_ids = tuple(scenario.scenario_id for scenario in case.scenarios)
        if (
            case.verdict.status != "passed"
            or case.expected_trials != 1
            or case.observed_trials != 1
            or not case.observation_digests
            or not has_correlations(case.correlations)
            or scenario_ids != tuple(sorted(contract.required_scenarios))
            or selected_results != case.test_results
            or any(node not in selected_results for node in contract.pytest_nodes)
            or any(result.get("status") != "passed" for result in selected_results.values())
        ):
            raise ValueError(f"claim report deterministic proof is incomplete: {case_id}")
    for case_id, contract in race_contracts.items():
        case = cases[case_id]
        if (
            not isinstance(case, RaceCase)
            or case.verdict.status != "passed"
            or case.expected_trials != 100
            or case.observed_trials != 100
            or case.seeds != contract.seeds
            or len(case.race_trials) != 100
            or any(
                trial.constraint_rows != 1
                or not trial.overlap_barrier_observed
                or tuple(sorted(trial.public_outcomes))
                != tuple(sorted(contract.expected_public_outcomes))
                for trial in case.race_trials
            )
        ):
            raise ValueError(f"claim report race proof is incomplete: {case_id}")


def _requirements(package: EvidencePackagePaths) -> tuple[_ArtifactRequirement, ...]:
    return (
        _ArtifactRequirement("deterministic", package.deterministic, DeterministicArtifact),
        _ArtifactRequirement("surface-audit", package.surface_audit, SurfaceAuditArtifact),
        _ArtifactRequirement("agent-quality", package.agent_quality, AgentQualityArtifact),
        _ArtifactRequirement("live-smoke", package.live_smoke, LiveSmokeArtifact),
        _ArtifactRequirement("playground", package.playground, PlaygroundArtifact),
        _ArtifactRequirement("processes", package.processes, ProcessArtifact),
        _ArtifactRequirement("races", package.races, RaceArtifact),
        _ArtifactRequirement(
            "renewal-demo",
            package.renewal_demo,
            PlaygroundArtifact,
            _RENEWAL_DEMONSTRATION_CASE_ID,
        ),
        _ArtifactRequirement(
            "verification-demo",
            package.verification_demo,
            PlaygroundArtifact,
            _VERIFICATION_DEMONSTRATION_CASE_ID,
        ),
    )


def _load_required_artifacts(
    package: EvidencePackagePaths,
) -> tuple[tuple[str, Path, Artifact], ...]:
    loaded: list[tuple[str, Path, Artifact]] = []
    for requirement in _requirements(package):
        artifact = parse_artifact(requirement.path.read_bytes())
        if not isinstance(artifact, requirement.artifact_type):
            raise TypeError(f"{requirement.name} artifact has the wrong evidence lane")
        if requirement.demonstration_case_id is not None and _demonstration_case_ids(artifact) != {
            requirement.demonstration_case_id
        }:
            raise TypeError(f"{requirement.name} artifact has the wrong demonstration contract")
        loaded.append((requirement.name, requirement.path, artifact))
    return tuple(loaded)


def _demonstration_case_ids(artifact: Artifact) -> set[str]:
    if not isinstance(artifact, PlaygroundArtifact):
        return set()
    return {case.case_id for case in artifact.cases}


def _validate_common_reproducibility(
    related: tuple[tuple[str, Path, Artifact], ...],
) -> None:
    expected_pin = _common_reproducibility_pin(related[0][2])
    if any(_common_reproducibility_pin(artifact) != expected_pin for _, _, artifact in related):
        raise ValueError("claim report artifacts do not share one reproducibility pin")
    if any(
        installation_kind != "wheel"
        for _, _, artifact in related
        for installation_kind in artifact.reproducibility.build.installation_kinds.values()
    ):
        raise ValueError("claim report requires every evidence product from clean wheel installs")


def write_claim_report(*, package: EvidencePackagePaths, output: Path) -> None:
    related = _load_required_artifacts(package)
    deterministic = related[0][2]
    if not isinstance(deterministic, DeterministicArtifact):
        raise TypeError("claim report requires a deterministic release artifact")
    if not deterministic.summary.strict_pass:
        raise ValueError("claim report cannot publish supported claims from a failed release gate")
    _validate_release_matrix(deterministic)
    surface = related[1][2]
    if not isinstance(surface, SurfaceAuditArtifact) or not surface.summary.strict_pass:
        raise TypeError("claim report requires a passing surface and cold-schema artifact")
    _validate_common_reproducibility(related)

    lines = [
        "# OpenMagic tested claim report",
        "",
        f"Build: `{deterministic.reproducibility.build.git_sha}`",
        "",
        "## May claim",
        "",
        *(f"- {claim}" for claim in _SUPPORTED_CLAIMS),
        "",
        "## May not claim",
        "",
        *(f"- {claim}" for claim in REQUIRED_NEGATIVE_CLAIMS),
        "",
        "Agent quality, provider availability, and playground behavior are separate evidence products. They cannot turn a failed deterministic gate into a pass.",
        "",
        "## Evidence artifacts",
        "",
        *(f"- `{name}`: `{path}` (`{_digest(path)}`)" for name, path, _artifact in related),
        "",
        "## Residual limitations",
        "",
        *(f"- {limitation}" for limitation in deterministic.limitations),
        "",
    ]
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(lines), encoding="utf-8")


__all__ = ["EvidencePackagePaths", "write_claim_report"]
