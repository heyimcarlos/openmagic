"""Reproducible deterministic release runner and canonical report assembly."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from importlib.metadata import distribution, version
from importlib.util import find_spec
from pathlib import Path
from typing import Any

import psycopg
from example_insurance.migrations import apply_migrations
from example_insurance.renewal_definition import RENEWAL_DEFINITION
from example_insurance.verification_definition import VERIFICATION_DEFINITION
from openmagic_runtime.evidence import content_fingerprint

from openmagic_evals.evidence.artifact_io import write_artifact
from openmagic_evals.evidence.case_recording import (
    RecordedCaseObservation,
    load_case_observations,
    merge_case_observations,
)
from openmagic_evals.evidence.contracts import (
    REQUIRED_NEGATIVE_CLAIMS,
    ArtifactCase,
    BuildPin,
    CaseVerdict,
    DeterministicArtifact,
    DeterministicSummary,
    RaceArtifact,
    RaceCase,
    RaceTrialEvidence,
    ReproducibilityPin,
    merge_correlations,
)
from openmagic_evals.evidence.deadline import bounded_evidence
from openmagic_evals.evidence.deterministic_observations import DeterministicObservation
from openmagic_evals.evidence.matrix import (
    DETERMINISTIC_RELEASE_MATRIX,
    RaceContract,
    ReleaseCase,
    cardinality_one_races,
)
from openmagic_evals.evidence.race_models import RaceCorpus
from openmagic_evals.evidence.race_transitions import transition_race_definitions
from openmagic_evals.evidence.races import run_all_races
from openmagic_evals.harness._postgres import POSTGRES_IMAGE, postgres_container

_DISTRIBUTIONS = (
    "example-insurance",
    "openmagic-api",
    "openmagic-evals",
    "openmagic-runtime",
)
_DISTRIBUTION_PACKAGES = {
    "example-insurance": "example_insurance",
    "openmagic-api": "openmagic_api",
    "openmagic-evals": "openmagic_evals",
    "openmagic-runtime": "openmagic_runtime",
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
        distribution_digests={name: _distribution_digest(root, name) for name in _DISTRIBUTIONS},
    )


def _distribution_digest(_root: Path, name: str) -> str:
    item = distribution(name)
    content = hashlib.sha256()
    for file in sorted(item.files or (), key=str):
        relative = Path(str(file))
        if not any(part.endswith(".dist-info") for part in relative.parts) or relative.name not in {
            "METADATA",
            "WHEEL",
            "entry_points.txt",
            "top_level.txt",
        }:
            continue
        path = Path(str(item.locate_file(file)))
        if not path.is_file():
            continue
        content.update(relative.as_posix().encode())
        content.update(b"\0")
        content.update(path.read_bytes())
        content.update(b"\0")
    package_name = _DISTRIBUTION_PACKAGES[name]
    package_spec = find_spec(package_name)
    if package_spec is None or package_spec.origin is None:
        raise RuntimeError(f"installed distribution package is unavailable: {package_name}")
    package_root = Path(package_spec.origin).parent
    for path in sorted(package_root.rglob("*")):
        if not path.is_file() or any(
            part == "__pycache__" or part.startswith(".") for part in path.parts
        ):
            continue
        relative = path.relative_to(package_root.parent)
        content.update(relative.as_posix().encode())
        content.update(b"\0")
        content.update(path.read_bytes())
        content.update(b"\0")
    return "sha256:" + content.hexdigest()


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
    definitions.update(
        {
            f"{definition.identity.key}:{definition.identity.version}": "sha256:"
            + content_fingerprint(definition)
            for definition in transition_race_definitions()
        }
    )
    with postgres_container(database_name="openmagic_test_evidence_pin") as postgres:
        database_url = postgres.get_connection_url(driver=None)
        apply_migrations(database_url)
        with psycopg.connect(database_url) as connection:
            row = connection.execute(
                "SELECT current_setting('server_version'), "
                "current_setting('transaction_isolation'), "
                "current_setting('synchronous_commit'), "
                "current_setting('TimeZone'), "
                "current_setting('max_connections')"
            ).fetchone()
            application_head = connection.execute(
                "SELECT version FROM example_insurance.migration_history "
                "ORDER BY version DESC LIMIT 1"
            ).fetchone()
            runtime_head = connection.execute(
                "SELECT version FROM openmagic_runtime.migration_history "
                "ORDER BY version DESC LIMIT 1"
            ).fetchone()
    if row is None:
        raise RuntimeError("PostgreSQL did not return its observed configuration")
    if application_head is None or runtime_head is None:
        raise RuntimeError("PostgreSQL did not return its observed migration heads")
    migration_heads = {
        "example_insurance": str(application_head[0]),
        "openmagic_runtime": str(runtime_head[0]),
    }
    postgres_configuration = {
        "max_connections": str(row[4]),
        "synchronous_commit": str(row[2]),
        "timezone": str(row[3]),
        "transaction_isolation": str(row[1]),
    }
    configuration_document = json.dumps(
        postgres_configuration, sort_keys=True, separators=(",", ":")
    ).encode()
    return ReproducibilityPin(
        build=_build_pin(root),
        suite_version="issue-71.v1",
        command=command,
        environment_allowlist=("PATH", "PYTHONNOUSERSITE"),
        started_at=started_at,
        finished_at=finished_at,
        timeout_seconds=timeout_seconds,
        postgres_version=str(row[0]),
        postgres_image=POSTGRES_IMAGE,
        postgres_configuration=postgres_configuration,
        postgres_configuration_digest=_sha256(configuration_document),
        migration_heads=migration_heads,
        definition_digests=definitions,
        case_corpus_digest=case_corpus_digest,
        sandbox_digest=_sha256(POSTGRES_IMAGE.encode()),
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


def _release_case(
    case: ReleaseCase,
    tests: dict[str, dict[str, Any]],
    observation: DeterministicObservation,
) -> ArtifactCase:
    matched = _matching_results(tests, case.pytest_nodes)
    statuses = tuple(result["status"] for result in matched.values())
    missing_nodes = tuple(
        node
        for node in case.pytest_nodes
        if ("::" in node and node not in matched)
        or ("::" not in node and not any(item.startswith(node) for item in matched))
    )
    if missing_nodes:
        status = "infrastructure_error"
        violations = ("release case omitted a predeclared pytest node",)
    elif not matched:
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
    digest_input = {
        "durable_observation": observation.document,
        "tests": {node: matched[node] for node in sorted(matched)},
    }
    return ArtifactCase(
        case_id=case.case_id,
        case_schema_version=1,
        expected_trials=1,
        observed_trials=1,
        seeds=(0,),
        correlations=observation.correlations,
        observation_digests=_case_digest(case.case_id, digest_input, (0,)),
        verdict=CaseVerdict(status=status, invariant_violations=violations),
    )


def _exact_observation(
    case: ReleaseCase,
    recorded: dict[str, tuple[RecordedCaseObservation, ...]],
) -> DeterministicObservation:
    observations = recorded.get(case.case_id, ())
    observed_scenarios = tuple(item.scenario_id for item in observations)
    if observed_scenarios != tuple(sorted(case.required_scenarios)):
        raise RuntimeError(
            f"deterministic case {case.case_id} recorded {observed_scenarios!r}, "
            f"expected {tuple(sorted(case.required_scenarios))!r}"
        )
    correlations, document = merge_case_observations(observations)
    return DeterministicObservation(correlations=correlations, document=document)


def _trace_completeness_case(
    case: ReleaseCase,
    tests: dict[str, dict[str, Any]],
    observation: DeterministicObservation,
) -> ArtifactCase:
    contract = _release_case(case, tests, observation)
    correlations = observation.correlations
    required_identity_groups = (
        correlations.command_ids,
        correlations.workflow_ids,
        correlations.instance_ids,
        correlations.step_ids,
        correlations.attempt_ids,
        correlations.wait_ids,
        correlations.signal_ids,
        correlations.trace_event_ids,
        correlations.thread_ids,
        correlations.message_ids,
        correlations.agent_run_ids,
        correlations.domain_event_ids,
        correlations.delivery_ids,
        correlations.delivery_attempt_ids,
        correlations.external_effect_ids,
        correlations.approval_grant_ids,
        correlations.verification_challenge_ids,
        correlations.verification_session_ids,
        correlations.worker_ids,
        correlations.process_ids,
        correlations.provider_request_ids,
    )
    if not all(required_identity_groups):
        raise AssertionError("trace completeness omitted an accepted durable identity")
    return contract


def _race_case(case: RaceContract, corpus: RaceCorpus) -> RaceCase:
    if (
        corpus.case_id != case.case_id
        or corpus.database_constraint != case.database_constraint
        or corpus.uses_overlap_barrier != case.uses_overlap_barrier
        or corpus.varied_jitter != case.varied_jitter
        or tuple(sorted(corpus.expected_public_outcomes))
        != tuple(sorted(case.expected_public_outcomes))
    ):
        raise ValueError(f"race corpus metadata differs from its contract: {case.case_id}")
    if tuple(result.seed for result in corpus.results) != case.seeds:
        raise ValueError(f"race corpus is missing its predeclared seeds: {case.case_id}")
    passed = all(
        result.constraint_rows == 1
        and tuple(sorted(result.public_outcomes)) == tuple(sorted(corpus.expected_public_outcomes))
        for result in corpus.results
    )
    violations = () if passed else ("cardinality-one constraint disagreed with public outcomes",)
    trials = tuple(
        RaceTrialEvidence(
            seed=result.seed,
            jitter_microseconds=result.jitter_microseconds,
            public_outcomes=result.public_outcomes,
            constraint_rows=result.constraint_rows,
            correlations=result.correlations,
            observation_digest=result.observation_digest,
            contender_process_ids=result.contender_process_ids,
            overlap_barrier_observed=result.overlap_barrier_observed,
        )
        for result in corpus.results
    )
    return RaceCase(
        case_id=case.case_id,
        case_schema_version=1,
        expected_trials=100,
        observed_trials=len(trials),
        seeds=case.seeds,
        correlations=merge_correlations(result.correlations for result in corpus.results),
        observation_digests=tuple(result.observation_digest for result in corpus.results),
        race_trials=trials,
        verdict=CaseVerdict(
            status="passed" if passed else "failed",
            invariant_violations=violations,
        ),
    )


@bounded_evidence
def run_deterministic_release(
    *,
    repository_root: Path,
    output: Path,
    timeout_seconds: int = 1800,
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
    process_command_base = (
        sys.executable,
        "-m",
        "pytest",
        *selected_nodes,
        "-p",
        "openmagic_evals.evidence.pytest_plugin",
    )
    public_command = (
        "openmagic-evidence",
        "deterministic",
        "--repository-root",
        str(root),
        "--output",
        str(output.resolve()),
        "--timeout-seconds",
        str(timeout_seconds),
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
        observation_directory = Path(directory) / "case-observations"
        process_command = [
            *process_command_base,
            "--openmagic-evidence-results",
            str(result_path),
        ]
        environment = {
            "OPENMAGIC_EVIDENCE_OBSERVATION_DIRECTORY": str(observation_directory),
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
        recorded = load_case_observations(observation_directory)
    tests = dict(test_document["tests"])
    case_observations = {
        case.case_id: _exact_observation(case, recorded)
        for case in selected_release_cases
        if case.case_id not in {"release.complete-suite", "trace.complete-durable-chain"}
    }
    all_correlations = merge_correlations(
        observation.correlations for observation in case_observations.values()
    )
    all_document: dict[str, object] = {
        case_id: observation.document for case_id, observation in sorted(case_observations.items())
    }
    for aggregate_case_id, scenario_id in (
        ("release.complete-suite", "all-predeclared-cases"),
        ("trace.complete-durable-chain", "all-accepted-scenarios"),
    ):
        if not any(case.case_id == aggregate_case_id for case in selected_release_cases):
            continue
        aggregate = RecordedCaseObservation(
            case_id=aggregate_case_id,
            scenario_id=scenario_id,
            correlations=all_correlations,
            document=all_document,
        )
        recorded[aggregate_case_id] = (aggregate,)
        aggregate_case = next(
            case for case in selected_release_cases if case.case_id == aggregate_case_id
        )
        case_observations[aggregate_case_id] = _exact_observation(aggregate_case, recorded)
    cases = tuple(
        _trace_completeness_case(case, tests, case_observations[case.case_id])
        if case.family == "trace_completeness"
        else _release_case(case, tests, case_observations[case.case_id])
        for case in selected_release_cases
    )
    corpora = {corpus.case_id: corpus for corpus in run_all_races()}
    race_cases = tuple(_race_case(case, corpora[case.case_id]) for case in selected_race_contracts)
    finished_at = datetime.now(UTC)
    all_cases = cases + race_cases
    statuses = tuple(case.verdict.status for case in all_cases)
    violations = sum(len(case.verdict.invariant_violations) for case in all_cases)
    strict_pass = completed.returncode == 0 and all(status == "passed" for status in statuses)
    artifact = DeterministicArtifact(
        reproducibility=reproducibility_pin(
            root,
            command=public_command,
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
            runner_exit_code=completed.returncode,
        ),
        limitations=(
            "Tested one PostgreSQL 17 single-database deployment shape.",
            "Recorded observations apply only to the pinned build, Definitions, and case corpus.",
        ),
        negative_claims=REQUIRED_NEGATIVE_CLAIMS,
    )
    write_artifact(output.resolve(), artifact)
    if not strict_pass:
        raise RuntimeError("deterministic release gate failed")
    return artifact


@bounded_evidence
def run_race_release(
    *,
    repository_root: Path,
    output: Path,
    timeout_seconds: int = 900,
) -> RaceArtifact:
    """Run only the predeclared 700-trial cardinality-one corpus."""
    root = repository_root.resolve()
    contracts = cardinality_one_races()
    command = (
        "openmagic-evidence",
        "races",
        "--repository-root",
        str(root),
        "--output",
        str(output.resolve()),
        "--timeout-seconds",
        str(timeout_seconds),
    )
    corpus_digest = _sha256(
        json.dumps(
            {"races": [case.case_id for case in contracts], "seeds": list(range(100))},
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    )
    started_at = datetime.now(UTC)
    corpora = {corpus.case_id: corpus for corpus in run_all_races()}
    cases = tuple(_race_case(contract, corpora[contract.case_id]) for contract in contracts)
    finished_at = datetime.now(UTC)
    statuses = tuple(case.verdict.status for case in cases)
    violations = sum(len(case.verdict.invariant_violations) for case in cases)
    strict_pass = all(status == "passed" for status in statuses) and violations == 0
    artifact = RaceArtifact(
        reproducibility=reproducibility_pin(
            root,
            command=command,
            started_at=started_at,
            finished_at=finished_at,
            timeout_seconds=timeout_seconds,
            case_corpus_digest=corpus_digest,
        ),
        cases=cases,
        summary=DeterministicSummary(
            expected_cases=len(cases),
            observed_cases=len(cases),
            passed_cases=statuses.count("passed"),
            failed_cases=statuses.count("failed"),
            infrastructure_errors=0,
            invariant_violations=violations,
            strict_pass=strict_pass,
            runner_exit_code=0 if strict_pass else 1,
        ),
        limitations=(
            "Race results apply to the pinned single-PostgreSQL deployment shape.",
            "The corpus proves only the seven accepted cardinality-one invariants.",
        ),
        negative_claims=REQUIRED_NEGATIVE_CLAIMS,
    )
    write_artifact(output.resolve(), artifact)
    if not strict_pass:
        raise RuntimeError("cardinality-one race gate failed")
    return artifact


__all__ = ["reproducibility_pin", "run_deterministic_release", "run_race_release"]
