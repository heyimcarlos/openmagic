"""Derived supported-claim report with mandatory negative claims."""

from __future__ import annotations

import hashlib
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
    parse_artifact,
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


def _digest(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _common_reproducibility_pin(artifact: Artifact) -> tuple[object, ...]:
    pin = artifact.reproducibility
    return (
        pin.build,
        pin.suite_version,
        pin.postgres_version,
        pin.postgres_image,
        pin.postgres_configuration,
        pin.postgres_configuration_digest,
        pin.migration_heads,
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
            or not any(case.correlations.model_dump(mode="python").values())
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


def write_claim_report(
    *,
    deterministic_path: Path,
    surface_path: Path,
    output: Path,
    agent_path: Path | None = None,
    live_path: Path | None = None,
    playground_path: Path | None = None,
    process_path: Path | None = None,
    race_path: Path | None = None,
    renewal_demo_path: Path | None = None,
    verification_demo_path: Path | None = None,
) -> None:
    deterministic = parse_artifact(deterministic_path.read_bytes())
    if not isinstance(deterministic, DeterministicArtifact):
        raise TypeError("claim report requires a deterministic release artifact")
    if not deterministic.summary.strict_pass:
        raise ValueError("claim report cannot publish supported claims from a failed release gate")
    _validate_release_matrix(deterministic)
    related: list[tuple[str, Path, Artifact]] = [
        ("deterministic", deterministic_path, deterministic)
    ]
    surface = parse_artifact(surface_path.read_bytes())
    if not isinstance(surface, SurfaceAuditArtifact) or not surface.summary.strict_pass:
        raise TypeError("claim report requires a passing surface and cold-schema artifact")
    related.append(("surface-audit", surface_path, surface))
    if agent_path is not None:
        agent = parse_artifact(agent_path.read_bytes())
        if not isinstance(agent, AgentQualityArtifact):
            raise TypeError("Agent artifact has the wrong lane")
        related.append(("agent-quality", agent_path, agent))
    if live_path is not None:
        live = parse_artifact(live_path.read_bytes())
        if not isinstance(live, LiveSmokeArtifact):
            raise TypeError("live artifact has the wrong lane")
        related.append(("live-smoke", live_path, live))
    if playground_path is not None:
        playground = parse_artifact(playground_path.read_bytes())
        if not isinstance(playground, PlaygroundArtifact):
            raise TypeError("playground artifact has the wrong lane")
        related.append(("playground", playground_path, playground))
    if process_path is not None:
        process = parse_artifact(process_path.read_bytes())
        if not isinstance(process, ProcessArtifact):
            raise TypeError("process artifact has the wrong lane")
        related.append(("processes", process_path, process))
    if race_path is not None:
        race = parse_artifact(race_path.read_bytes())
        if not isinstance(race, RaceArtifact):
            raise TypeError("race artifact has the wrong lane")
        related.append(("races", race_path, race))
    for name, path, expected_case in (
        ("renewal-demo", renewal_demo_path, "demo.renewal-complete"),
        (
            "verification-demo",
            verification_demo_path,
            "demo.deterministic-verification",
        ),
    ):
        if path is None:
            continue
        demo = parse_artifact(path.read_bytes())
        if not isinstance(demo, PlaygroundArtifact) or {case.case_id for case in demo.cases} != {
            expected_case
        }:
            raise TypeError(f"{name} artifact has the wrong demonstration contract")
        related.append((name, path, demo))

    expected_pin = _common_reproducibility_pin(deterministic)
    if any(_common_reproducibility_pin(artifact) != expected_pin for _, _, artifact in related):
        raise ValueError("claim report artifacts do not share one reproducibility pin")
    if any(
        installation_kind != "wheel"
        for _, _, artifact in related
        for installation_kind in artifact.reproducibility.build.installation_kinds.values()
    ):
        raise ValueError("claim report requires every evidence product from clean wheel installs")

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


__all__ = ["write_claim_report"]
