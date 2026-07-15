"""Reproducible deterministic release runner and canonical report assembly."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path
from typing import Any

from example_insurance.renewal_definition import RENEWAL_DEFINITION
from example_insurance.verification_definition import VERIFICATION_DEFINITION
from openmagic_runtime.evidence import content_fingerprint

from openmagic_evals.evidence.contracts import (
    REQUIRED_NEGATIVE_CLAIMS,
    ArtifactCase,
    BuildPin,
    CaseVerdict,
    Correlations,
    DeterministicArtifact,
    DeterministicSummary,
    ReproducibilityPin,
    canonical_artifact_json,
    parse_artifact,
)
from openmagic_evals.evidence.matrix import (
    DETERMINISTIC_RELEASE_MATRIX,
    RaceContract,
    ReleaseCase,
    cardinality_one_races,
)
from openmagic_evals.evidence.redaction import audit_redaction

_DISTRIBUTIONS = (
    "example-insurance",
    "openmagic-api",
    "openmagic-evals",
    "openmagic-runtime",
)
_MIGRATION_HEADS = {
    "example_insurance": "0004_deterministic_verification",
    "openmagic_runtime": "0003_fenced_effect_kernel",
}


def _sha256(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _git(root: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", *arguments],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _build_pin(root: Path) -> BuildPin:
    status = _git(root, "status", "--porcelain", "--untracked-files=normal")
    return BuildPin(
        git_sha=_git(root, "rev-parse", "HEAD"),
        checkout_clean=not status,
        lock_digest=_sha256((root / "uv.lock").read_bytes()),
        distributions={name: version(name) for name in _DISTRIBUTIONS},
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
    container_contract = b"postgres:17-alpine;single-database;real-transactions"
    return ReproducibilityPin(
        build=_build_pin(root),
        suite_version="issue-71.v1",
        command=command,
        environment_allowlist=("PATH", "PYTHONNOUSERSITE"),
        started_at=started_at,
        finished_at=finished_at,
        timeout_seconds=timeout_seconds,
        postgres_version="PostgreSQL 17, postgres:17-alpine",
        postgres_configuration_digest=_sha256(container_contract),
        migration_heads=_MIGRATION_HEADS,
        definition_digests=definitions,
        case_corpus_digest=case_corpus_digest,
        sandbox_digest=_sha256(b"testcontainers:postgres:17-alpine"),
    )


def _case_digest(case_id: str, results: dict[str, Any], seeds: tuple[int, ...]) -> tuple[str, ...]:
    return tuple(
        _sha256(
            json.dumps(
                {"case_id": case_id, "seed": seed, "results": results},
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        )
        for seed in seeds
    )


def _matching_results(
    tests: dict[str, dict[str, Any]],
    nodes: tuple[str, ...],
) -> dict[str, dict[str, Any]]:
    paths = tuple(node.split("::", 1)[0] for node in nodes)
    exact = {node for node in nodes if "::" in node}
    return {
        node: result
        for node, result in tests.items()
        if node in exact
        or (not exact and node.startswith(paths))
        or any("::" not in requested and node.startswith(requested) for requested in nodes)
    }


def _release_case(case: ReleaseCase, tests: dict[str, dict[str, Any]]) -> ArtifactCase:
    matched = _matching_results(tests, case.pytest_nodes)
    statuses = tuple(result["status"] for result in matched.values())
    if not matched:
        status = "infrastructure_error"
        violations = ("release case collected no tests",)
    elif all(item == "passed" for item in statuses):
        status = "passed"
        violations = ()
    elif any(item == "failed" for item in statuses):
        status = "failed"
        violations = (case.pass_condition,)
    else:
        status = "infrastructure_error"
        violations = ("release case did not complete",)
    digest_input = {node: matched[node] for node in sorted(matched)}
    return ArtifactCase(
        case_id=case.case_id,
        case_schema_version=1,
        expected_trials=1,
        observed_trials=1,
        seeds=(0,),
        correlations=Correlations(),
        observation_digests=_case_digest(case.case_id, digest_input, (0,)),
        verdict=CaseVerdict(status=status, invariant_violations=violations),
    )


def _race_case(case: RaceContract, tests: dict[str, dict[str, Any]]) -> ArtifactCase:
    matched = _matching_results(tests, (case.pytest_node,))
    passed = bool(matched) and all(result["status"] == "passed" for result in matched.values())
    status = "passed" if passed else "failed" if matched else "infrastructure_error"
    violations = () if passed else ("cardinality-one race corpus did not complete cleanly",)
    digest_input = {
        "database_constraint": case.database_constraint,
        "overlap_barrier": case.uses_overlap_barrier,
        "varied_jitter": case.varied_jitter,
        "tests": matched,
    }
    return ArtifactCase(
        case_id=case.case_id,
        case_schema_version=1,
        expected_trials=100,
        observed_trials=100,
        seeds=case.seeds,
        correlations=Correlations(),
        observation_digests=_case_digest(case.case_id, digest_input, case.seeds),
        verdict=CaseVerdict(status=status, invariant_violations=violations),
    )


def _write_artifact(path: Path, artifact: DeterministicArtifact) -> None:
    document = canonical_artifact_json(artifact)
    parse_artifact(document)
    audit_redaction(json.loads(document))
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(document, encoding="utf-8")
    temporary.replace(path)


def run_deterministic_release(
    *,
    repository_root: Path,
    output: Path,
    timeout_seconds: int = 900,
    pytest_nodes: tuple[str, ...] = (),
    release_cases: tuple[ReleaseCase, ...] | None = None,
    race_contracts: tuple[RaceContract, ...] | None = None,
) -> DeterministicArtifact:
    root = repository_root.resolve()
    selected_release_cases = (
        DETERMINISTIC_RELEASE_MATRIX if release_cases is None else release_cases
    )
    selected_race_contracts = cardinality_one_races() if race_contracts is None else race_contracts
    selected_nodes = pytest_nodes or (
        "packages/openmagic-runtime/tests",
        "reference-apps/example-insurance/tests",
        "evals/tests",
    )
    command = (
        sys.executable,
        "-m",
        "pytest",
        *selected_nodes,
        "-p",
        "openmagic_evals.evidence.pytest_plugin",
    )
    corpus_digest = _sha256(
        json.dumps(
            {
                "matrix": [case.case_id for case in selected_release_cases],
                "races": [case.case_id for case in selected_race_contracts],
                "nodes": selected_nodes,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    )
    started_at = datetime.now(UTC)
    with tempfile.TemporaryDirectory(prefix="openmagic-evidence-") as directory:
        result_path = Path(directory) / "pytest-results.json"
        process_command = [*command, "--openmagic-evidence-results", str(result_path)]
        environment = {
            "PATH": os.environ.get("PATH", os.defpath),
            "PYTHONNOUSERSITE": "1",
        }
        completed = subprocess.run(
            process_command,
            cwd=root,
            env=environment,
            timeout=timeout_seconds,
            check=False,
        )
        if not result_path.is_file():
            raise RuntimeError("pytest did not produce its explicit evidence result file")
        test_document = json.loads(result_path.read_text(encoding="utf-8"))
    finished_at = datetime.now(UTC)
    tests = dict(test_document["tests"])
    cases = tuple(_release_case(case, tests) for case in selected_release_cases)
    race_cases = tuple(_race_case(case, tests) for case in selected_race_contracts)
    all_cases = cases + race_cases
    statuses = tuple(case.verdict.status for case in all_cases)
    violations = sum(len(case.verdict.invariant_violations) for case in all_cases)
    strict_pass = completed.returncode == 0 and all(status == "passed" for status in statuses)
    artifact = DeterministicArtifact(
        reproducibility=reproducibility_pin(
            root,
            command=tuple(process_command),
            started_at=started_at,
            finished_at=finished_at,
            timeout_seconds=timeout_seconds,
            case_corpus_digest=corpus_digest,
        ),
        cases=all_cases,
        summary=DeterministicSummary(
            expected_cases=len(all_cases),
            observed_cases=len(all_cases),
            passed_cases=statuses.count("passed"),
            failed_cases=statuses.count("failed"),
            infrastructure_errors=statuses.count("infrastructure_error"),
            invariant_violations=violations,
            strict_pass=strict_pass,
        ),
        limitations=(
            "Tested one PostgreSQL 17 single-database deployment shape.",
            "Recorded observations apply only to the pinned build, Definitions, and case corpus.",
        ),
        negative_claims=REQUIRED_NEGATIVE_CLAIMS,
    )
    _write_artifact(output.resolve(), artifact)
    if not strict_pass:
        raise RuntimeError("deterministic release gate failed")
    return artifact


def run_race_release(
    *,
    repository_root: Path,
    output: Path,
    timeout_seconds: int = 900,
) -> DeterministicArtifact:
    """Run only the predeclared 500-trial cardinality-one corpus."""
    contracts = cardinality_one_races()
    nodes = tuple(dict.fromkeys(contract.pytest_node for contract in contracts))
    return run_deterministic_release(
        repository_root=repository_root,
        output=output,
        timeout_seconds=timeout_seconds,
        pytest_nodes=nodes,
        release_cases=(),
        race_contracts=contracts,
    )


__all__ = ["reproducibility_pin", "run_deterministic_release", "run_race_release"]
