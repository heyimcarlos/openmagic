"""Versioned synthetic Agent cases and independent outcome scoring."""

from __future__ import annotations

import inspect
import json
import subprocess
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import UUID, uuid4

import example_insurance.renewal_attempts as renewal_attempts_module
import example_insurance.renewals as renewals_module
from example_insurance.renewals import (
    ExampleInsurance,
    RenewalFacts,
    RequestRenewalRevision,
    RequestRenewalRevisionInput,
    StartRenewalOutreach,
    StartRenewalOutreachInput,
)
from openmagic_runtime.commands import Actor, Cause
from openmagic_runtime.threads import AppendMessage, CreateThread

from openmagic_evals.evidence.agent_boundary_trials import (
    boundary_configuration_document,
    execute_boundary_trial,
)
from openmagic_evals.evidence.agent_cases import (
    BOUNDARY_AGENT_KEY,
    DEVELOPMENT_CASES,
    RENEWAL_AGENT_KEY,
    AgentCase,
    BoundaryAgentCase,
    RenewalAgentCase,
    validate_prohibited_contract,
)
from openmagic_evals.evidence.agent_trials import AgentTrial
from openmagic_evals.evidence.artifact_io import write_artifact
from openmagic_evals.evidence.contracts import (
    AgentCaseEvidence,
    AgentConfigurationPin,
    AgentCorpusPin,
    AgentCorrelations,
    AgentQualityArtifact,
    AgentQualitySummary,
    AgentScorerContract,
    AgentTrialEvidence,
    ApplicationCorrelations,
    BoundaryAgentScorerContract,
    CaseVerdict,
    Correlations,
    DistributionSummary,
    ProcessCorrelations,
    RenewalAgentCandidateObservation,
    RenewalAgentScorerContract,
    RuntimeCorrelations,
    SanitizedAgentEvent,
    agent_rubric_scores,
    aggregate_agent_trials,
    merge_correlations,
)
from openmagic_evals.evidence.core_models import canonical_digest
from openmagic_evals.evidence.deadline import bounded_evidence
from openmagic_evals.evidence.inspection import EvidenceInspection
from openmagic_evals.evidence.postgres_provenance import (
    load_postgres_deployments,
    record_postgres_deployments,
)
from openmagic_evals.evidence.reproducibility import reproducibility_pin
from openmagic_evals.harness.renewal_scenario import renewal_context


@dataclass(frozen=True)
class AgentExperimentResult:
    expected_trials: int
    observed_trials: int
    passed_trials: int
    prohibited_actions: int
    pass_rate: float
    wilson_lower: float
    wilson_upper: float
    threshold_passed: bool
    latency: DistributionSummary


def evaluate_trials(
    cases: tuple[AgentCase, ...],
    trials: tuple[AgentTrial, ...],
) -> AgentExperimentResult:
    expected_trials = sum(case.predeclared_trials for case in cases)
    if len(trials) != expected_trials:
        raise ValueError("Agent experiment is missing trials from its denominator")
    case_by_id = {case.case_id: case for case in cases}
    if len(case_by_id) != len(cases):
        raise ValueError("Agent case identities must be unique")
    seen: set[tuple[str, int]] = set()
    for trial in trials:
        case = case_by_id.get(trial.case_id)
        if case is None:
            raise ValueError("Agent trial references an unknown case")
        identity = (trial.case_id, trial.seed)
        if identity in seen or trial.seed not in range(case.predeclared_trials):
            raise ValueError("Agent trial seed is duplicated or outside the predeclared corpus")
        seen.add(identity)
    for case in cases:
        case_trials = tuple(trial for trial in trials if trial.case_id == case.case_id)
        if len(case_trials) != case.predeclared_trials:
            raise ValueError("Agent case does not have its complete trial denominator")

    aggregate = aggregate_agent_trials(trials)
    thresholds_pass = all(
        sum(trial.outcome_passed for trial in trials if trial.case_id == case.case_id)
        / case.predeclared_trials
        >= case.pass_threshold
        for case in cases
    )
    return AgentExperimentResult(
        expected_trials=expected_trials,
        observed_trials=aggregate.observed_trials,
        passed_trials=aggregate.passed_trials,
        prohibited_actions=aggregate.prohibited_actions,
        pass_rate=aggregate.pass_rate,
        wilson_lower=aggregate.wilson_lower,
        wilson_upper=aggregate.wilson_upper,
        threshold_passed=thresholds_pass and aggregate.prohibited_actions == 0,
        latency=aggregate.latency_ms,
    )


def _renewal_agent_configuration_documents() -> tuple[dict[str, object], dict[str, object]]:
    factory = renewals_module._draft_agent_factory
    candidate = renewals_module.RenewalDraftCandidate
    instruction = {
        "agent_key": RENEWAL_AGENT_KEY,
        "agent_version": 1,
        "instruction_key": "example_insurance.renewal_draft.en_ca.v1",
        "factory_source": inspect.getsource(factory),
        "result_contract_source": inspect.getsource(candidate),
        "executor_registration_source": inspect.getsource(ExampleInsurance.__init__),
    }
    tool_schema = {
        "durable_input_construction_source": inspect.getsource(
            renewal_attempts_module.prepare_workflow_attempt
        ),
        "execution_input_type": "openmagic_runtime.agents.AgentExecutionInput",
        "result_type": "example_insurance.renewals.RenewalDraftCandidate",
    }
    return instruction, tool_schema


def _uuid_values(values: object) -> tuple[UUID, ...]:
    if not isinstance(values, list):
        return ()
    return tuple(UUID(str(value)) for value in values)


def _execute_renewal_agent_trial(case: RenewalAgentCase, seed: int) -> AgentTrial:
    with renewal_context() as (database_url, application, threads):
        thread = threads.create(
            CreateThread(uuid4(), "email", f"synthetic-agent-{case.case_id}-{seed}@example.test")
        )
        if case.prior_thread_context is not None:
            threads.append(
                AppendMessage(
                    thread_id=thread.thread_id,
                    author_kind="party",
                    author_id="synthetic-broker",
                    source_kind="channel",
                    source_id=uuid4(),
                    content=case.prior_thread_context,
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
                policy_number=case.policy_number,
                policyholder_name=case.policyholder_name,
                policyholder_email=f"synthetic-{seed}@example.test",
                renewal_date=case.renewal_date,
                expiring_premium_cents=case.premium_cents,
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
        started = time.monotonic()
        receipt = application.start_renewal_outreach(command)
        application.run_workflow_worker_once(worker_id=f"agent-facts-{seed}")
        application.run_workflow_worker_once(worker_id=f"agent-draft-{seed}")
        application.run_delivery_worker_once(worker_id=f"agent-delivery-{seed}")
        worker_ids = [
            f"agent-facts-{seed}",
            f"agent-draft-{seed}",
            f"agent-delivery-{seed}",
        ]
        if case.scenario == "revision":
            presentation = application.renewal_approval_presentation(command.input.workflow_id)
            application.request_renewal_revision(
                RequestRenewalRevision(
                    command_id=uuid4(),
                    actor=command.actor,
                    cause=Cause("message", str(uuid4())),
                    input=RequestRenewalRevisionInput(
                        workflow_id=command.input.workflow_id,
                        wait_id=presentation.wait_id,
                        draft_id=presentation.draft_id,
                        message_id=presentation.message_id,
                        thread_sequence=presentation.thread_sequence,
                        message_fingerprint=presentation.message_fingerprint,
                        presentation_fingerprint=presentation.presentation_fingerprint,
                        proposed_effect=presentation.proposed_effect,
                        revision_instruction="Use a warmer opening.",
                    ),
                )
            )
            application.run_workflow_worker_once(worker_id=f"agent-revision-{seed}")
            application.run_delivery_worker_once(worker_id=f"agent-revision-delivery-{seed}")
            worker_ids.extend((f"agent-revision-{seed}", f"agent-revision-delivery-{seed}"))
        latency_ms = round((time.monotonic() - started) * 1000)
        message = threads.read(thread.thread_id).messages[-1]
        evidence = json.loads(application.renewal_evidence_json(command.input.workflow_id))
        detected: list[str] = []
        outcomes = evidence["outcomes"]
        if outcomes["external_email_effect_count"]:
            detected.append("external_effect_dispatch")
        if outcomes["workflow_lifecycle"] != "active" or outcomes["instance_state"] != "open":
            detected.append("workflow_completion")
        if outcomes["approval_wait_state"] != "unsatisfied":
            detected.append("route_selection")
        safety = EvidenceInspection(database_url).agent_safety(
            thread.thread_id, receipt.result.instance_id
        )
        if "agent_run" in safety.message_source_kinds:
            detected.append("message_append")
        expected_command_count = 2 if case.scenario == "revision" else 1
        if safety.command_count != expected_command_count:
            detected.append("command_submission")
        if not safety.delivery_thread_ids or set(safety.delivery_thread_ids) != {thread.thread_id}:
            detected.append("delivery_destination_selection")
        if safety.retry_authorization_count != 0:
            detected.append("retry_authorization")
        prohibited = validate_prohibited_contract(case, tuple(detected))
        subject, body = message.content.split("\n\n", 1)
        scorer_contract = _agent_scorer_contract(case)
        candidate_observation = RenewalAgentCandidateObservation(subject=subject, body=body)
        rubric_scores = agent_rubric_scores(
            scorer_contract,
            candidate_observation,
            prohibited,
        )
        outcome_passed = all(rubric_scores.values())
        correlations = evidence["correlations"]
        agent_run_ids = _uuid_values(correlations["agent_run_ids"])
        message_ids = _uuid_values(correlations["message_ids"])
        context_projection = {
            "context_through_sequence": len(threads.read(thread.thread_id).messages)
            - len(message_ids),
            "thread_id": str(thread.thread_id),
        }
        candidate_projection = {
            "agent_run_id": str(agent_run_ids[-1]),
            "candidate_digest": canonical_digest(candidate_observation.model_dump(mode="json")),
        }
        verification_projection = {
            "message_id": str(message.message_id),
            "prohibited_actions": prohibited,
            "rubric_scores": rubric_scores,
        }
        trajectory = (
            SanitizedAgentEvent(
                sequence=1,
                event_type="context_projection",
                durable_identity=str(thread.thread_id),
                input_digest=canonical_digest(
                    {"case_id": case.case_id, "seed": seed, "split": case.split}
                ),
                output_digest=canonical_digest(context_projection),
            ),
            SanitizedAgentEvent(
                sequence=2,
                event_type="candidate",
                durable_identity=str(agent_run_ids[-1]),
                input_digest=canonical_digest(context_projection),
                output_digest=canonical_digest(candidate_projection),
            ),
            SanitizedAgentEvent(
                sequence=3,
                event_type="outcome_verification",
                durable_identity=str(message.message_id),
                input_digest=canonical_digest(candidate_projection),
                output_digest=canonical_digest(verification_projection),
            ),
        )
        trajectory_digest = canonical_digest(
            {
                "candidate_observation": candidate_observation.model_dump(mode="json"),
                "rubric_scores": dict(sorted(rubric_scores.items())),
                "trajectory": [event.model_dump(mode="json") for event in trajectory],
            }
        )
        return AgentTrial(
            case_id=case.case_id,
            seed=seed,
            outcome_passed=outcome_passed,
            prohibited_actions=prohibited,
            latency_ms=latency_ms,
            observation_digest=trajectory_digest,
            correlations=Correlations(
                runtime=RuntimeCorrelations(
                    command_ids=(command.command_id,),
                    workflow_ids=(command.input.workflow_id,),
                    instance_ids=(receipt.result.instance_id,),
                    step_ids=_uuid_values(correlations["step_ids"]),
                    attempt_ids=_uuid_values(correlations["attempt_ids"]),
                    wait_ids=_uuid_values(outcomes["approval_wait_ids"]),
                ),
                application=ApplicationCorrelations(
                    thread_ids=(thread.thread_id,),
                    message_ids=message_ids,
                    domain_event_ids=_uuid_values(correlations["domain_event_ids"]),
                    delivery_ids=_uuid_values(correlations["delivery_ids"]),
                    delivery_attempt_ids=safety.delivery_attempt_ids,
                ),
                agent=AgentCorrelations(agent_run_ids=agent_run_ids),
                process=ProcessCorrelations(worker_ids=tuple(worker_ids)),
            ),
            trajectory=trajectory,
            candidate_observation=candidate_observation,
            rubric_scores=rubric_scores,
        )


def _execute_agent_trial(case: AgentCase, seed: int) -> AgentTrial:
    if isinstance(case, BoundaryAgentCase):
        return execute_boundary_trial(case, seed)
    return _execute_renewal_agent_trial(case, seed)


def load_sealed_held_out_cases(repository_root: Path) -> tuple[AgentCase, ...]:
    from openmagic_evals.evidence.sealed_holdout import (
        HELD_OUT_CASES,
        HELD_OUT_CORPUS_DIGEST,
        HELD_OUT_SEALED_AT_COMMIT,
        TUNING_LOCKED_PATHS,
    )

    actual_digest = canonical_digest([asdict(case) for case in HELD_OUT_CASES])
    if actual_digest != HELD_OUT_CORPUS_DIGEST:
        raise RuntimeError("held-out Agent corpus differs from its predeclared seal")
    ancestor = subprocess.run(
        ["git", "merge-base", "--is-ancestor", HELD_OUT_SEALED_AT_COMMIT, "HEAD"],
        cwd=repository_root,
        check=False,
        capture_output=True,
    )
    unchanged = subprocess.run(
        ["git", "diff", "--quiet", HELD_OUT_SEALED_AT_COMMIT, "--", *TUNING_LOCKED_PATHS],
        cwd=repository_root,
        check=False,
        capture_output=True,
    )
    if ancestor.returncode != 0 or unchanged.returncode != 0:
        raise RuntimeError("Agent implementation changed after the held-out corpus was sealed")
    return HELD_OUT_CASES


def _merge_correlations(trials: tuple[AgentTrial, ...]) -> Correlations:
    return merge_correlations(trial.correlations for trial in trials)


def _agent_scorer_contract(case: AgentCase) -> AgentScorerContract:
    if isinstance(case, RenewalAgentCase):
        return RenewalAgentScorerContract(
            expected_subject=case.expected_subject,
            required_body_fragments=case.required_body_fragments,
            forbidden_body_fragments=case.forbidden_body_fragments,
        )
    expected_boundary = (
        "malformed_result" if case.boundary == "malformed_result" else "bounded_timeout"
    )
    return BoundaryAgentScorerContract(expected_boundary=expected_boundary)


@bounded_evidence
def run_local_agent_quality(
    *,
    repository_root: Path,
    output: Path,
    timeout_seconds: int = 300,
) -> AgentQualityArtifact:
    command = (
        "openmagic-evidence",
        "agent-quality",
        "--repository-root",
        str(repository_root.resolve()),
        "--output",
        str(output.resolve()),
        "--timeout-seconds",
        str(timeout_seconds),
    )
    started_at = datetime.now(UTC)
    with TemporaryDirectory(prefix="openmagic-agent-postgres-") as deployment_directory:
        with record_postgres_deployments(Path(deployment_directory)):
            development_trials = tuple(
                _execute_agent_trial(case, seed)
                for case in DEVELOPMENT_CASES
                for seed in range(case.predeclared_trials)
            )
            held_out_cases = load_sealed_held_out_cases(repository_root.resolve())
            held_out_trials = tuple(
                _execute_agent_trial(case, seed)
                for case in held_out_cases
                for seed in range(case.predeclared_trials)
            )
        postgres_deployments = load_postgres_deployments(Path(deployment_directory))
    cases = DEVELOPMENT_CASES + held_out_cases
    trials = development_trials + held_out_trials
    from openmagic_evals.evidence.sealed_holdout import (
        HELD_OUT_CORPUS_DIGEST,
        HELD_OUT_CORPUS_VERSION,
        HELD_OUT_SEALED_AT_COMMIT,
        TUNING_LOCKED_PATHS,
    )

    finished_at = datetime.now(UTC)
    result = evaluate_trials(cases, trials)

    def artifact_case(case: AgentCase) -> AgentCaseEvidence:
        case_trials = tuple(trial for trial in trials if trial.case_id == case.case_id)
        passed_trials = sum(trial.outcome_passed for trial in case_trials)
        prohibited_actions = sum(len(trial.prohibited_actions) for trial in case_trials)
        threshold_passed = (
            passed_trials / case.predeclared_trials >= case.pass_threshold
            and prohibited_actions == 0
        )
        return AgentCaseEvidence(
            case_id=case.case_id,
            case_schema_version=case.case_schema_version,
            configuration_key=case.configuration_key,
            split=case.split,
            prohibited_action_contract=case.prohibited_actions,
            scorer_contract=_agent_scorer_contract(case),
            expected_trials=case.predeclared_trials,
            observed_trials=case.predeclared_trials,
            seeds=tuple(range(case.predeclared_trials)),
            correlations=_merge_correlations(case_trials),
            observation_digests=tuple(trial.observation_digest for trial in case_trials),
            agent_trials=tuple(
                AgentTrialEvidence(
                    seed=trial.seed,
                    outcome_passed=trial.outcome_passed,
                    prohibited_actions=trial.prohibited_actions,
                    latency_ms=trial.latency_ms,
                    trajectory_digest=trial.observation_digest,
                    correlations=trial.correlations,
                    trajectory=trial.trajectory,
                    candidate_observation=trial.candidate_observation,
                    rubric_scores=trial.rubric_scores,
                )
                for trial in case_trials
            ),
            pass_threshold=case.pass_threshold,
            passed_trials=passed_trials,
            prohibited_actions=prohibited_actions,
            verdict=CaseVerdict(
                status="passed" if threshold_passed else "failed",
                invariant_violations=()
                if threshold_passed
                else ("Agent case missed its predeclared quality or safety threshold",),
            ),
        )

    artifact_cases = tuple(artifact_case(case) for case in cases)
    corpus_digest = canonical_digest([asdict(case) for case in cases])
    renewal_instruction, renewal_tool_schema = _renewal_agent_configuration_documents()
    boundary_configuration = boundary_configuration_document()
    artifact = AgentQualityArtifact(
        reproducibility=reproducibility_pin(
            repository_root.resolve(),
            command=command,
            started_at=started_at,
            finished_at=finished_at,
            timeout_seconds=timeout_seconds,
            case_corpus_digest=corpus_digest,
            postgres_deployments=postgres_deployments,
        ),
        corpus=AgentCorpusPin(
            development_cases_digest=canonical_digest([asdict(case) for case in DEVELOPMENT_CASES]),
            held_out_corpus_version=HELD_OUT_CORPUS_VERSION,
            held_out_cases_digest=HELD_OUT_CORPUS_DIGEST,
            held_out_sealed_at_commit=HELD_OUT_SEALED_AT_COMMIT,
            tuning_locked_paths=TUNING_LOCKED_PATHS,
            execution_phases=("development", "held_out"),
            tuning_unchanged_after_seal=True,
        ),
        agent_configurations=(
            AgentConfigurationPin(
                agent_key=RENEWAL_AGENT_KEY,
                agent_version=1,
                instruction_digest=canonical_digest(renewal_instruction),
                tool_schema_digest=canonical_digest(renewal_tool_schema),
                provider="openmagic-fresh-interpreter",
                model="deterministic-reference-agent-v1",
                reasoning="deterministic",
                temperature=0.0,
            ),
            AgentConfigurationPin(
                agent_key=BOUNDARY_AGENT_KEY,
                agent_version=1,
                instruction_digest=canonical_digest(boundary_configuration),
                tool_schema_digest=canonical_digest(
                    {
                        "input": "openmagic_runtime.agents.AgentRunInput",
                        "output": "openmagic_evals.evidence.agent_boundary_trials._BoundaryCandidate",
                        "timeout_seconds": boundary_configuration["timeout_seconds"],
                    }
                ),
                provider="openmagic-fresh-interpreter",
                model="deterministic-boundary-harness-v1",
                reasoning="none",
                temperature=0.0,
            ),
        ),
        cases=artifact_cases,
        summary=AgentQualitySummary(
            development_cases=len(DEVELOPMENT_CASES),
            held_out_cases=len(held_out_cases),
            expected_trials=result.expected_trials,
            observed_trials=result.observed_trials,
            passed_trials=result.passed_trials,
            prohibited_actions=result.prohibited_actions,
            threshold_passed=result.threshold_passed,
            pass_rate=result.pass_rate,
            wilson_lower=result.wilson_lower,
            wilson_upper=result.wilson_upper,
            latency_ms=result.latency,
        ),
        limitations=(
            "The report measures only the two explicitly pinned local configurations.",
            "The sealed held-out corpus has 15 trials and does not imply model-agnostic quality.",
        ),
    )
    write_artifact(output, artifact)
    if not result.threshold_passed:
        raise RuntimeError("Agent quality experiment missed its predeclared threshold")
    return artifact


__all__ = [
    "AgentCase",
    "AgentExperimentResult",
    "AgentTrial",
    "evaluate_trials",
    "load_sealed_held_out_cases",
    "run_local_agent_quality",
]
