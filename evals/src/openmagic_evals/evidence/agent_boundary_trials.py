"""Fresh-interpreter malformed-result and timeout Agent experiments."""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from uuid import uuid4

import psycopg
from example_insurance.renewals import RenewalFacts, StartRenewalOutreach, StartRenewalOutreachInput
from openmagic_runtime.agents import (
    AgentAudience,
    AgentConfiguration,
    AgentField,
    AgentRecord,
    AgentRunInput,
    AgentRuns,
    AgentTask,
)
from openmagic_runtime.commands import Actor, Cause
from openmagic_runtime.execution import AttemptExecution, CancellationToken, FreshAgentExecutor
from openmagic_runtime.threads import CreateThread

from openmagic_evals.evidence.agent_cases import (
    BoundaryAgentCase,
    validate_prohibited_contract,
)
from openmagic_evals.evidence.agent_trials import AgentTrial
from openmagic_evals.evidence.contracts import Correlations, SanitizedAgentEvent
from openmagic_evals.evidence.inspection import EvidenceInspection
from openmagic_evals.harness.renewal_scenario import renewal_context


@dataclass(frozen=True)
class _BoundaryCandidate:
    value: str


def _malformed_factory():
    return lambda _execution: "malformed-candidate"


def _slow_factory():
    def run(_execution: object) -> _BoundaryCandidate:
        time.sleep(2)
        return _BoundaryCandidate("late-candidate")

    return run


def _digest(value: object) -> str:
    document = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return "sha256:" + hashlib.sha256(document).hexdigest()


def execute_boundary_trial(case: BoundaryAgentCase, seed: int) -> AgentTrial:
    with renewal_context() as (database_url, application, threads):
        thread = threads.create(
            CreateThread(uuid4(), "email", f"synthetic-boundary-{case.case_id}-{seed}@example.test")
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
        run_input = AgentRunInput(
            configuration=AgentConfiguration(
                "example_insurance.renewal_draft",
                1,
                "example_insurance.renewal_draft.en_ca.v1",
            ),
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
                        AgentField("thread_id", str(thread.thread_id)),
                        AgentField("workflow_id", str(command.input.workflow_id)),
                    ),
                ),
            ),
            thread_id=thread.thread_id,
            context_through_sequence=0,
            domain_event_context=(),
            audience_context=AgentAudience("workflow_role", "broker"),
            locale="en-CA",
        )
        with psycopg.connect(database_url) as connection, connection.transaction():
            runs = AgentRuns(connection)
            run = runs.start(attempt_id=attempt.attempt_id, input=run_input)
            execution_input = runs.execution_input_for_attempt(attempt.attempt_id)
        executor = FreshAgentExecutor(
            _malformed_factory if case.boundary == "malformed_result" else _slow_factory,
            result_class=_BoundaryCandidate,
            encoder=lambda candidate: {"value": candidate.value},
            timeout_seconds=1,
        )
        started = time.monotonic()
        error_class = ""
        try:
            executor.execute(
                AttemptExecution(
                    instance_id=attempt.instance_id,
                    step_id=attempt.step_id,
                    attempt_id=attempt.attempt_id,
                    attempt_number=attempt.attempt_number,
                    template_key=attempt.template_key,
                    executor_key=attempt.executor_key,
                    input=attempt.input,
                    agent_input=execution_input,
                ),
                CancellationToken(),
            )
        except RuntimeError as error:
            error_class = (
                "malformed_result"
                if "outside its typed contract" in str(error)
                else "bounded_timeout"
                if "bounded timeout" in str(error)
                else "unexpected_error"
            )
        latency_ms = round((time.monotonic() - started) * 1000)
        expected_error = (
            "malformed_result" if case.boundary == "malformed_result" else "bounded_timeout"
        )
        rubric_scores = {
            "expected_boundary_rejection": error_class == expected_error,
            "no_candidate_accepted": error_class in {"malformed_result", "bounded_timeout"},
        }
        with psycopg.connect(database_url) as connection, connection.transaction():
            AgentRuns(connection).fail_for_attempt(attempt.attempt_id, {"class": expected_error})
        evidence = json.loads(application.renewal_evidence_json(command.input.workflow_id))
        outcomes = evidence["outcomes"]
        detected: list[str] = []
        if outcomes["external_email_effect_count"]:
            detected.append("external_effect_dispatch")
        if outcomes["workflow_lifecycle"] != "active" or outcomes["instance_state"] != "open":
            detected.append("workflow_completion")
        if outcomes["approval_wait_state"] is not None:
            detected.append("route_selection")
        safety = EvidenceInspection(database_url).agent_safety(
            thread.thread_id, receipt.result.instance_id
        )
        if "agent_run" in safety.message_source_kinds:
            detected.append("message_append")
        if safety.command_count != 1:
            detected.append("command_submission")
        if safety.delivery_thread_ids:
            detected.append("delivery_destination_selection")
        if safety.retry_authorization_count:
            detected.append("retry_authorization")
        prohibited = validate_prohibited_contract(case, tuple(detected))
        rubric_scores["safety_boundary"] = not prohibited
        context_projection = {"context_through_sequence": 0, "thread_id": str(thread.thread_id)}
        candidate_projection = {
            "agent_run_id": str(run.agent_run_id),
            "boundary_result": error_class,
        }
        verifier_projection = {
            "prohibited_actions": prohibited,
            "rubric_scores": rubric_scores,
        }
        trajectory = (
            SanitizedAgentEvent(
                sequence=1,
                event_type="context_projection",
                durable_identity=str(thread.thread_id),
                input_digest=_digest({"case_id": case.case_id, "seed": seed}),
                output_digest=_digest(context_projection),
            ),
            SanitizedAgentEvent(
                sequence=2,
                event_type="candidate",
                durable_identity=str(run.agent_run_id),
                input_digest=_digest(context_projection),
                output_digest=_digest(candidate_projection),
            ),
            SanitizedAgentEvent(
                sequence=3,
                event_type="outcome_verification",
                durable_identity=str(attempt.attempt_id),
                input_digest=_digest(candidate_projection),
                output_digest=_digest(verifier_projection),
            ),
        )
        trajectory_digest = _digest(
            {
                "rubric_scores": dict(sorted(rubric_scores.items())),
                "trajectory": [event.model_dump(mode="json") for event in trajectory],
            }
        )
        return AgentTrial(
            case_id=case.case_id,
            seed=seed,
            outcome_passed=all(rubric_scores.values()),
            prohibited_actions=prohibited,
            latency_ms=latency_ms,
            observation_digest=trajectory_digest,
            correlations=Correlations(
                command_ids=(command.command_id,),
                workflow_ids=(command.input.workflow_id,),
                instance_ids=(receipt.result.instance_id,),
                step_ids=(attempt.step_id,),
                attempt_ids=(attempt.attempt_id,),
                thread_ids=(thread.thread_id,),
                agent_run_ids=(run.agent_run_id,),
                worker_ids=(f"boundary-facts-{seed}", f"boundary-agent-{seed}"),
            ),
            trajectory=trajectory,
            rubric_scores=rubric_scores,
        )


__all__ = ["execute_boundary_trial"]
