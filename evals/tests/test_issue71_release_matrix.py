from __future__ import annotations

import time
from dataclasses import dataclass, replace
from functools import partial
from pathlib import Path
from uuid import uuid4

import pytest
from example_insurance.renewal_definition import RENEWAL_DEFINITION
from example_insurance.verification_definition import VERIFICATION_DEFINITION
from openmagic_evals.evidence.case_recording import (
    load_case_observations,
    record_case_observation,
    record_renewal_case,
)
from openmagic_evals.evidence.contracts import Correlations, DeterministicScenarioEvidence
from openmagic_evals.evidence.deterministic_observations import collect_renewal_observation
from openmagic_evals.evidence.matrix import (
    DETERMINISTIC_RELEASE_MATRIX,
    REQUIRED_EVIDENCE_FAMILIES,
    cardinality_one_races,
)
from openmagic_evals.evidence.release import (
    _ExactCaseObservation,
    _race_corpus_digest,
    _release_case,
    _release_corpus_digest,
)
from openmagic_evals.harness import prepare_synthetic_renewal_start, renewal_context
from openmagic_runtime.agents import (
    AgentAudience,
    AgentConfiguration,
    AgentExecutionInput,
    AgentField,
    AgentRecord,
    AgentRunInput,
    AgentTask,
)
from openmagic_runtime.execution import AttemptExecution, CancellationToken, FreshAgentExecutor
from openmagic_runtime.kernel.definitions import DefinitionCatalog
from openmagic_runtime.threads import ThreadContext


@dataclass(frozen=True)
class _BoundaryCandidate:
    value: str


def _boundary_candidate_factory():
    return lambda execution: _BoundaryCandidate(str(execution.run_input.task.input.value("value")))


def _boundary_failure_factory():
    def fail(_execution: AgentExecutionInput) -> _BoundaryCandidate:
        raise ValueError("synthetic typed failure")

    return fail


def _boundary_malformed_factory():
    return lambda _execution: "not-a-typed-candidate"


def _boundary_slow_factory(marker: Path):
    def run(_execution: AgentExecutionInput) -> _BoundaryCandidate:
        time.sleep(1.5)
        marker.write_text("late", encoding="utf-8")
        return _BoundaryCandidate("late")

    return run


def _boundary_execution() -> AttemptExecution:
    attempt_id = uuid4()
    thread_id = uuid4()
    run_input = AgentRunInput(
        configuration=AgentConfiguration("test.agent", 1, "test.agent.instructions.v1"),
        task=AgentTask(
            "test.task",
            1,
            AgentRecord("test.task.input", 1, (AgentField("value", "candidate"),)),
        ),
        thread_id=thread_id,
        context_through_sequence=0,
        domain_event_context=(),
        audience_context=AgentAudience("test", "recipient"),
        locale="en-CA",
    )
    return AttemptExecution(
        instance_id=uuid4(),
        step_id=uuid4(),
        attempt_id=attempt_id,
        attempt_number=1,
        template_key="test_agent",
        executor_key="test.agent.v1",
        input={"value": "candidate"},
        agent_input=AgentExecutionInput(
            agent_run_id=uuid4(),
            attempt_id=attempt_id,
            run_input=run_input,
            thread_context=ThreadContext(thread_id, 0, ()),
        ),
    )


def test_executor_case_records_typed_boundary_scenarios(tmp_path: Path) -> None:
    happy = FreshAgentExecutor(
        _boundary_candidate_factory,
        result_class=_BoundaryCandidate,
        encoder=lambda candidate: {"value": candidate.value},
        timeout_seconds=1,
    ).execute(_boundary_execution(), CancellationToken())
    assert happy.value == {"value": "candidate"}
    record_case_observation(
        case_id="executor.typed-malformed-timeout",
        scenario_id="happy",
        correlations=Correlations(),
        document={"accepted_value": happy.value},
    )

    scenarios = (
        ("typed-failure", _boundary_failure_factory, "synthetic typed failure"),
        ("malformed", _boundary_malformed_factory, "outside its typed contract"),
    )
    for scenario_id, factory, match in scenarios:
        executor = FreshAgentExecutor(
            factory,
            result_class=_BoundaryCandidate,
            encoder=lambda candidate: {"value": candidate.value},
            timeout_seconds=1,
        )
        with pytest.raises((RuntimeError, ValueError), match=match):
            executor.execute(_boundary_execution(), CancellationToken())
        record_case_observation(
            case_id="executor.typed-malformed-timeout",
            scenario_id=scenario_id,
            correlations=Correlations(),
            document={"candidate_accepted": False, "error_match": match},
        )

    marker = tmp_path / "late-agent-result"
    timeout_executor = FreshAgentExecutor(
        partial(_boundary_slow_factory, marker),
        result_class=_BoundaryCandidate,
        encoder=lambda candidate: {"value": candidate.value},
        timeout_seconds=1,
    )
    with pytest.raises(RuntimeError, match="bounded timeout"):
        timeout_executor.execute(_boundary_execution(), CancellationToken())
    time.sleep(0.7)
    assert not marker.exists()
    record_case_observation(
        case_id="executor.typed-malformed-timeout",
        scenario_id="timeout",
        correlations=Correlations(),
        document={"candidate_accepted": False, "late_side_effect": marker.exists()},
    )


def test_definition_case_records_closed_installed_manifests() -> None:
    with renewal_context() as (database_url, application, threads):
        command = prepare_synthetic_renewal_start(application, threads, 71)
        application.start_renewal_outreach(command)
        catalog = DefinitionCatalog(database_url=database_url)
        digests = {
            "renewal": catalog.register(RENEWAL_DEFINITION),
            "verification": catalog.register(VERIFICATION_DEFINITION),
        }
        renewal_routes = tuple(route.key for route in RENEWAL_DEFINITION.routes)
        verification_routes = tuple(route.key for route in VERIFICATION_DEFINITION.routes)

        assert set(renewal_routes) == {
            "approve_email",
            "await_approval",
            "draft_after_facts",
            "reconcile_email",
            "revise_email",
            "start",
        }
        assert set(verification_routes) == {"start"}
        record_renewal_case(
            case_id="definition.closed-readiness",
            scenario_id="closed-manifests",
            application=application,
            database_url=database_url,
            workflow_id=command.input.workflow_id,
            document={
                "definition_digests": digests,
                "renewal_routes": renewal_routes,
                "verification_routes": verification_routes,
            },
        )


def test_release_matrix_covers_every_accepted_deterministic_family() -> None:
    assert {
        "acknowledgement",
        "completion",
        "definition",
        "domain_event",
        "exact_thread_delivery",
        "external_effect",
        "executor",
        "lease",
        "recovery",
        "replay",
        "retry",
        "route",
        "signal",
        "transaction",
        "trace_completeness",
        "wait",
    } == REQUIRED_EVIDENCE_FAMILIES
    assert {case.family for case in DETERMINISTIC_RELEASE_MATRIX} == REQUIRED_EVIDENCE_FAMILIES
    assert len({case.case_id for case in DETERMINISTIC_RELEASE_MATRIX}) == len(
        DETERMINISTIC_RELEASE_MATRIX
    )
    assert all(case.pytest_nodes for case in DETERMINISTIC_RELEASE_MATRIX)
    assert all(case.pass_condition for case in DETERMINISTIC_RELEASE_MATRIX)


def test_every_cardinality_one_race_declares_barrier_and_100_varied_jitter_seeds() -> None:
    races = cardinality_one_races()

    assert {race.case_id for race in races} == {
        "race.command-receipt",
        "race.delivery-claim",
        "race.attempt-result",
        "race.route-activation",
        "race.step-claim",
        "race.wait-signal",
        "race.verification-submission",
    }
    assert all(race.uses_overlap_barrier for race in races)
    assert all(race.seeds == tuple(range(100)) for race in races)
    assert all(race.varied_jitter for race in races)
    assert all(race.database_constraint for race in races)


def test_case_recording_rejects_duplicate_case_and_scenario_emissions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENMAGIC_EVIDENCE_OBSERVATION_DIRECTORY", str(tmp_path))
    values = {
        "case_id": "release.test",
        "scenario_id": "exact-scenario",
        "correlations": Correlations(command_ids=(uuid4(),)),
        "document": {"observed": True},
    }

    record_case_observation(**values)
    record_case_observation(**values)

    with pytest.raises(ValueError, match="duplicate deterministic observation"):
        load_case_observations(tmp_path)


def test_corpus_pins_change_when_full_case_contracts_change() -> None:
    release_cases = (DETERMINISTIC_RELEASE_MATRIX[0],)
    race_contracts = (cardinality_one_races()[0],)
    release_digest = _release_corpus_digest(release_cases, race_contracts, ("evals/tests",))
    race_digest = _race_corpus_digest(race_contracts)

    changed_release = (replace(release_cases[0], pass_condition="different invariant"),)
    changed_race = (replace(race_contracts[0], database_constraint="different constraint"),)

    assert (
        _release_corpus_digest(changed_release, race_contracts, ("evals/tests",)) != release_digest
    )
    assert _race_corpus_digest(changed_race) != race_digest


def test_release_case_fails_closed_when_one_exact_node_is_missing(tmp_path: Path) -> None:
    case = DETERMINISTIC_RELEASE_MATRIX[1]
    observation = collect_renewal_observation(tmp_path)
    tests = {case.pytest_nodes[0]: {"status": "passed"}}
    exact = _ExactCaseObservation(
        correlations=observation.correlations,
        document=observation.document,
        scenarios=(
            DeterministicScenarioEvidence(
                scenario_id=case.required_scenarios[0],
                correlations=observation.correlations,
                observation=observation.document,
                observation_digest=observation.digest,
            ),
        ),
    )

    artifact_case = _release_case(case, tests, exact)

    assert artifact_case.verdict.status == "infrastructure_error"
