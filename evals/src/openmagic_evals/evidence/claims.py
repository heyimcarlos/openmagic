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
    parse_artifact,
)
from openmagic_evals.evidence.matrix import DETERMINISTIC_RELEASE_MATRIX, cardinality_one_races

_SUPPORTED_CLAIMS = (
    "The tested single-PostgreSQL kernel preserved the pinned Definition, transaction, replay, race, lease, recovery, and retry contracts.",
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


def write_claim_report(
    *,
    deterministic_path: Path,
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
    expected_release_cases = {case.case_id for case in DETERMINISTIC_RELEASE_MATRIX} | {
        race.case_id for race in cardinality_one_races()
    }
    if {case.case_id for case in deterministic.cases} != expected_release_cases:
        raise ValueError("claim report requires the complete deterministic release matrix")
    related: list[tuple[str, Path, Artifact]] = [
        ("deterministic", deterministic_path, deterministic)
    ]
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
