"""Reproducible deterministic release runner and canonical report assembly."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from openmagic_evals.evidence.artifact_io import write_artifact
from openmagic_evals.evidence.case_recording import (
    RecordedCaseObservation,
    load_case_observations,
    merge_case_observations,
)
from openmagic_evals.evidence.contracts import (
    REQUIRED_NEGATIVE_CLAIMS,
    ArtifactCase,
    CaseVerdict,
    Correlations,
    DeterministicArtifact,
    DeterministicScenarioEvidence,
    DeterministicSummary,
    RaceArtifact,
    RaceCase,
    RaceTrialEvidence,
    deterministic_observation_digest,
    merge_correlations,
)
from openmagic_evals.evidence.deadline import bounded_evidence
from openmagic_evals.evidence.matrix import (
    DETERMINISTIC_RELEASE_MATRIX,
    RaceContract,
    ReleaseCase,
    cardinality_one_races,
)
from openmagic_evals.evidence.race_models import RaceCorpus
from openmagic_evals.evidence.races import run_all_races
from openmagic_evals.evidence.reproducibility import reproducibility_pin, sha256


def _sha256(value: bytes) -> str:
    return sha256(value)


def _release_corpus_digest(
    release_cases: tuple[ReleaseCase, ...],
    race_contracts: tuple[RaceContract, ...],
    pytest_nodes: tuple[str, ...],
) -> str:
    return _sha256(
        json.dumps(
            {
                "matrix": [asdict(case) for case in release_cases],
                "races": [asdict(case) for case in race_contracts],
                "nodes": pytest_nodes,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    )


def _race_corpus_digest(contracts: tuple[RaceContract, ...]) -> str:
    return _sha256(
        json.dumps(
            {"races": [asdict(case) for case in contracts]},
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    )


@dataclass(frozen=True)
class _ExactCaseObservation:
    correlations: Correlations
    document: dict[str, object]
    scenarios: tuple[DeterministicScenarioEvidence, ...]


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
    observation: _ExactCaseObservation,
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
    test_results = {node: matched[node] for node in sorted(matched)}
    return ArtifactCase(
        case_id=case.case_id,
        case_schema_version=1,
        expected_trials=1,
        observed_trials=1,
        seeds=(0,),
        correlations=observation.correlations,
        observation_digests=(
            deterministic_observation_digest(observation.scenarios, test_results),
        ),
        scenarios=observation.scenarios,
        test_results=test_results,
        verdict=CaseVerdict(status=status, invariant_violations=violations),
    )


def _exact_observation(
    case: ReleaseCase,
    recorded: dict[str, tuple[RecordedCaseObservation, ...]],
) -> _ExactCaseObservation:
    observations = recorded.get(case.case_id, ())
    observed_scenarios = tuple(item.scenario_id for item in observations)
    if observed_scenarios != tuple(sorted(case.required_scenarios)):
        raise RuntimeError(
            f"deterministic case {case.case_id} recorded {observed_scenarios!r}, "
            f"expected {tuple(sorted(case.required_scenarios))!r}"
        )
    correlations, document = merge_case_observations(observations)
    scenarios = tuple(
        DeterministicScenarioEvidence(
            scenario_id=item.scenario_id,
            correlations=item.correlations,
            observation=item.document,
            observation_digest=_sha256(
                json.dumps(
                    item.document,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode()
            ),
        )
        for item in observations
    )
    return _ExactCaseObservation(
        correlations=correlations,
        document=document,
        scenarios=scenarios,
    )


def _trace_completeness_case(
    case: ReleaseCase,
    tests: dict[str, dict[str, Any]],
    observation: _ExactCaseObservation,
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
            observation=result.observation,
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
    corpus_digest = _release_corpus_digest(
        selected_release_cases, selected_race_contracts, selected_nodes
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
    corpus_digest = _race_corpus_digest(contracts)
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


__all__ = ["run_deterministic_release", "run_race_release"]
