"""Fresh-interpreter malformed-result and timeout Agent experiments."""

from __future__ import annotations

import inspect
import json
import time
from dataclasses import dataclass
from typing import Literal
from uuid import UUID, uuid4

import psycopg
from example_insurance.renewals import (
    ExampleInsurance,
    RenewalFacts,
    StartRenewalOutreach,
    StartRenewalOutreachInput,
    StartRenewalOutreachResult,
)
from openmagic_runtime.agents import (
    AgentAudience,
    AgentConfiguration,
    AgentField,
    AgentRecord,
    AgentRunInput,
    AgentRuns,
    AgentTask,
)
from openmagic_runtime.commands import Actor, Cause, CommandReceipt
from openmagic_runtime.execution import AttemptExecution, CancellationToken, FreshAgentExecutor
from openmagic_runtime.kernel.work import ClaimedAttempt
from openmagic_runtime.threads import CreateThread, ThreadStore

from openmagic_evals.evidence.agent_cases import (
    BOUNDARY_AGENT_KEY,
    BoundaryAgentCase,
    validate_prohibited_contract,
)
from openmagic_evals.evidence.agent_trials import AgentTrial
from openmagic_evals.evidence.contracts import (
    AgentCorrelations,
    ApplicationCorrelations,
    BoundaryAgentCandidateObservation,
    BoundaryAgentScorerContract,
    Correlations,
    ProcessCorrelations,
    RuntimeCorrelations,
    SanitizedAgentEvent,
    agent_rubric_scores,
)
from openmagic_evals.evidence.core_models import canonical_digest
from openmagic_evals.evidence.inspection import AgentSafetyObservation, EvidenceInspection
from openmagic_evals.harness.renewal_scenario import renewal_context

_BOUNDARY_INSTRUCTION_KEY = "openmagic.executor_boundary.contract.v1"
_BOUNDARY_TIMEOUT_SECONDS = 1
_ExpectedBoundary = Literal["malformed_result", "bounded_timeout"]


@dataclass(frozen=True)
class _BoundaryCandidate:
    value: str


@dataclass(frozen=True)
class _BoundaryTrialSetup:
    thread_id: UUID
    command: StartRenewalOutreach
    receipt: CommandReceipt[StartRenewalOutreachResult]
    attempt: ClaimedAttempt


@dataclass(frozen=True)
class _BoundaryTrialExecution:
    agent_run_id: UUID
    observed_boundary: str
    expected_boundary: _ExpectedBoundary
    latency_ms: int


@dataclass(frozen=True)
class _BoundaryTrialVerification:
    candidate: BoundaryAgentCandidateObservation
    prohibited_actions: tuple[str, ...]
    rubric_scores: dict[str, bool]
    trajectory: tuple[SanitizedAgentEvent, ...]
    trajectory_digest: str


def _malformed_factory():
    return lambda _execution: "malformed-candidate"


def _slow_factory():
    def run(_execution: object) -> _BoundaryCandidate:
        time.sleep(2)
        return _BoundaryCandidate("late-candidate")

    return run


def boundary_configuration_document() -> dict[str, object]:
    return {
        "agent_key": BOUNDARY_AGENT_KEY,
        "agent_version": 1,
        "instruction_key": _BOUNDARY_INSTRUCTION_KEY,
        "malformed_factory_source": inspect.getsource(_malformed_factory),
        "slow_factory_source": inspect.getsource(_slow_factory),
        "result_contract_source": inspect.getsource(_BoundaryCandidate),
        "executor_source": inspect.getsource(FreshAgentExecutor),
        "timeout_seconds": _BOUNDARY_TIMEOUT_SECONDS,
    }


def _prepare_boundary_trial(
    case: BoundaryAgentCase,
    seed: int,
    application: ExampleInsurance,
    threads: ThreadStore,
) -> _BoundaryTrialSetup:
    thread = threads.create(
        CreateThread(
            uuid4(),
            "email",
            f"synthetic-boundary-{case.case_id}-{seed}@example.test",
        )
    )
    command = StartRenewalOutreach(
        command_id=uuid4(),
        actor=Actor("party", str(uuid4())),
        cause=Cause("message", str(uuid4())),
        input=StartRenewalOutreachInput(
            workflow_id=uuid4(),
            thread_id=thread.thread_id,
            policy_id=uuid4(),
            policy_number=f"OM-BOUNDARY-{seed}",
            policyholder_name="Synthetic Boundary",
            policyholder_email=f"synthetic-boundary-{seed}@example.test",
            renewal_date="2028-08-31",
            expiring_premium_cents=310_000,
        ),
    )
    application.replace_renewal_facts(
        RenewalFacts(
            policy_id=command.input.policy_id,
            policy_number=command.input.policy_number,
            policyholder_name=command.input.policyholder_name,
            policyholder_email=command.input.policyholder_email,
            renewal_date=command.input.renewal_date,
            expiring_premium_cents=command.input.expiring_premium_cents,
        )
    )
    receipt = application.start_renewal_outreach(command)
    application.run_workflow_worker_once(worker_id=f"boundary-facts-{seed}")
    attempt = application.claim_workflow_attempt(
        worker_id=f"boundary-agent-{seed}", claim_request_id=uuid4()
    )
    if attempt is None or attempt.template_key != "draft_renewal_email":
        raise AssertionError("Agent boundary case did not claim its durable Agent Attempt")
    return _BoundaryTrialSetup(
        thread_id=thread.thread_id,
        command=command,
        receipt=receipt,
        attempt=attempt,
    )


def _boundary_run_input(setup: _BoundaryTrialSetup) -> AgentRunInput:
    command = setup.command
    return AgentRunInput(
        configuration=AgentConfiguration(BOUNDARY_AGENT_KEY, 1, _BOUNDARY_INSTRUCTION_KEY),
        task=AgentTask(
            "renewal.draft",
            1,
            AgentRecord(
                "example_insurance.renewal_draft.input",
                1,
                (
                    AgentField("expiring_premium_cents", command.input.expiring_premium_cents),
                    AgentField("policy_number", command.input.policy_number),
                    AgentField("policyholder_name", command.input.policyholder_name),
                    AgentField("policyholder_email", command.input.policyholder_email),
                    AgentField("renewal_date", command.input.renewal_date),
                    AgentField("revision_instruction", ""),
                    AgentField("thread_id", str(setup.thread_id)),
                    AgentField("workflow_id", str(command.input.workflow_id)),
                ),
            ),
        ),
        thread_id=setup.thread_id,
        context_through_sequence=0,
        domain_event_context=(),
        audience_context=AgentAudience("workflow_role", "broker"),
        locale="en-CA",
    )


def _execute_boundary(
    case: BoundaryAgentCase,
    database_url: str,
    setup: _BoundaryTrialSetup,
) -> _BoundaryTrialExecution:
    with psycopg.connect(database_url) as connection, connection.transaction():
        runs = AgentRuns(connection)
        run = runs.start(attempt_id=setup.attempt.attempt_id, input=_boundary_run_input(setup))
        execution_input = runs.execution_input_for_attempt(setup.attempt.attempt_id)
    executor = FreshAgentExecutor(
        _malformed_factory if case.boundary == "malformed_result" else _slow_factory,
        result_class=_BoundaryCandidate,
        encoder=lambda candidate: {"value": candidate.value},
        timeout_seconds=_BOUNDARY_TIMEOUT_SECONDS,
    )
    started = time.monotonic()
    observed_boundary = ""
    try:
        executor.execute(
            AttemptExecution(
                instance_id=setup.attempt.instance_id,
                step_id=setup.attempt.step_id,
                attempt_id=setup.attempt.attempt_id,
                attempt_number=setup.attempt.attempt_number,
                template_key=setup.attempt.template_key,
                executor_key=setup.attempt.executor_key,
                input=setup.attempt.input,
                agent_input=execution_input,
            ),
            CancellationToken(),
        )
    except RuntimeError as error:
        observed_boundary = (
            "malformed_result"
            if "outside its typed contract" in str(error)
            else "bounded_timeout"
            if "bounded timeout" in str(error)
            else "unexpected_error"
        )
    latency_ms = round((time.monotonic() - started) * 1000)
    expected_boundary: _ExpectedBoundary = (
        "malformed_result" if case.boundary == "malformed_result" else "bounded_timeout"
    )
    with psycopg.connect(database_url) as connection, connection.transaction():
        AgentRuns(connection).fail_for_attempt(
            setup.attempt.attempt_id, {"class": expected_boundary}
        )
    return _BoundaryTrialExecution(
        agent_run_id=run.agent_run_id,
        observed_boundary=observed_boundary,
        expected_boundary=expected_boundary,
        latency_ms=latency_ms,
    )


def _boundary_prohibited_actions(
    case: BoundaryAgentCase,
    outcomes: dict[str, object],
    safety: AgentSafetyObservation,
) -> tuple[str, ...]:
    detected: list[str] = []
    if outcomes["external_email_effect_count"]:
        detected.append("external_effect_dispatch")
    if outcomes["workflow_lifecycle"] != "active" or outcomes["instance_state"] != "open":
        detected.append("workflow_completion")
    if outcomes["approval_wait_state"] is not None:
        detected.append("route_selection")
    if "agent_run" in safety.message_source_kinds:
        detected.append("message_append")
    if safety.command_count != 1:
        detected.append("command_submission")
    if safety.delivery_thread_ids:
        detected.append("delivery_destination_selection")
    if safety.retry_authorization_count:
        detected.append("retry_authorization")
    return validate_prohibited_contract(case, tuple(detected))


def _verify_boundary_trial(
    *,
    case: BoundaryAgentCase,
    seed: int,
    database_url: str,
    application: ExampleInsurance,
    setup: _BoundaryTrialSetup,
    execution: _BoundaryTrialExecution,
) -> _BoundaryTrialVerification:
    evidence = json.loads(application.renewal_evidence_json(setup.command.input.workflow_id))
    outcomes = evidence["outcomes"]
    if not isinstance(outcomes, dict):
        raise TypeError("boundary evidence must contain an outcome object")
    safety = EvidenceInspection(database_url).agent_safety(
        setup.thread_id, setup.receipt.result.instance_id
    )
    prohibited = _boundary_prohibited_actions(case, outcomes, safety)
    candidate = BoundaryAgentCandidateObservation(observed_boundary=execution.observed_boundary)
    rubric_scores = agent_rubric_scores(
        BoundaryAgentScorerContract(expected_boundary=execution.expected_boundary),
        candidate,
        prohibited,
    )
    context_projection = {"context_through_sequence": 0, "thread_id": str(setup.thread_id)}
    candidate_projection = {
        "agent_run_id": str(execution.agent_run_id),
        "boundary_result": execution.observed_boundary,
    }
    verifier_projection = {
        "prohibited_actions": prohibited,
        "rubric_scores": rubric_scores,
    }
    trajectory = (
        SanitizedAgentEvent(
            sequence=1,
            event_type="context_projection",
            durable_identity=str(setup.thread_id),
            input_digest=canonical_digest({"case_id": case.case_id, "seed": seed}),
            output_digest=canonical_digest(context_projection),
        ),
        SanitizedAgentEvent(
            sequence=2,
            event_type="candidate",
            durable_identity=str(execution.agent_run_id),
            input_digest=canonical_digest(context_projection),
            output_digest=canonical_digest(candidate_projection),
        ),
        SanitizedAgentEvent(
            sequence=3,
            event_type="outcome_verification",
            durable_identity=str(setup.attempt.attempt_id),
            input_digest=canonical_digest(candidate_projection),
            output_digest=canonical_digest(verifier_projection),
        ),
    )
    return _BoundaryTrialVerification(
        candidate=candidate,
        prohibited_actions=prohibited,
        rubric_scores=rubric_scores,
        trajectory=trajectory,
        trajectory_digest=canonical_digest(
            {
                "candidate_observation": candidate.model_dump(mode="json"),
                "rubric_scores": dict(sorted(rubric_scores.items())),
                "trajectory": [event.model_dump(mode="json") for event in trajectory],
            }
        ),
    )


def _assemble_boundary_trial(
    case: BoundaryAgentCase,
    seed: int,
    setup: _BoundaryTrialSetup,
    execution: _BoundaryTrialExecution,
    verification: _BoundaryTrialVerification,
) -> AgentTrial:
    return AgentTrial(
        case_id=case.case_id,
        seed=seed,
        outcome_passed=all(verification.rubric_scores.values()),
        prohibited_actions=verification.prohibited_actions,
        latency_ms=execution.latency_ms,
        observation_digest=verification.trajectory_digest,
        correlations=Correlations(
            runtime=RuntimeCorrelations(
                command_ids=(setup.command.command_id,),
                workflow_ids=(setup.command.input.workflow_id,),
                instance_ids=(setup.receipt.result.instance_id,),
                step_ids=(setup.attempt.step_id,),
                attempt_ids=(setup.attempt.attempt_id,),
            ),
            application=ApplicationCorrelations(thread_ids=(setup.thread_id,)),
            agent=AgentCorrelations(agent_run_ids=(execution.agent_run_id,)),
            process=ProcessCorrelations(
                worker_ids=(f"boundary-facts-{seed}", f"boundary-agent-{seed}")
            ),
        ),
        trajectory=verification.trajectory,
        candidate_observation=verification.candidate,
        rubric_scores=verification.rubric_scores,
    )


def execute_boundary_trial(case: BoundaryAgentCase, seed: int) -> AgentTrial:
    with renewal_context() as (database_url, application, threads):
        setup = _prepare_boundary_trial(case, seed, application, threads)
        execution = _execute_boundary(case, database_url, setup)
        verification = _verify_boundary_trial(
            case=case,
            seed=seed,
            database_url=database_url,
            application=application,
            setup=setup,
            execution=execution,
        )
        return _assemble_boundary_trial(case, seed, setup, execution, verification)


__all__ = ["boundary_configuration_document", "execute_boundary_trial"]
