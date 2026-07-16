"""Synthetic playground verification kept outside correctness evidence."""

from __future__ import annotations

import hashlib
import json
import time
from datetime import UTC, datetime
from pathlib import Path

from example_insurance.renewals import ExampleInsurance
from example_insurance.reset import reset_synthetic_deployment
from openmagic_runtime.threads import ThreadStore

from openmagic_evals.evidence.artifact_io import write_artifact
from openmagic_evals.evidence.audit import audit_cold_schema
from openmagic_evals.evidence.contracts import (
    ArtifactCase,
    CaseVerdict,
    Correlations,
    DeterministicScenarioEvidence,
    PlaygroundArtifact,
    PlaygroundSummary,
    merge_correlations,
)
from openmagic_evals.evidence.deadline import bounded_evidence
from openmagic_evals.evidence.reproducibility import reproducibility_pin
from openmagic_evals.harness import (
    LocalEmailProvider,
    TestDeployment,
    approve_renewal,
    prepare_renewal_approval,
    prepare_synthetic_renewal_start,
)


def _digest(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _run_fixture(
    application: ExampleInsurance,
    threads: ThreadStore,
    *,
    seed: int,
) -> tuple[dict[str, object], Correlations]:
    command = prepare_synthetic_renewal_start(application, threads, seed)
    receipt = application.start_renewal_outreach(command)
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        thread = threads.read(command.input.thread_id)
        evidence = json.loads(application.renewal_evidence_json(command.input.workflow_id))
        outcomes = evidence["outcomes"]
        if len(thread.messages) == 1 and outcomes["approval_wait_state"] == "unsatisfied":
            values = evidence["correlations"]
            observation = {
                "approval_wait_state": outcomes["approval_wait_state"],
                "external_email_effect_count": outcomes["external_email_effect_count"],
                "instance_state": outcomes["instance_state"],
                "message_count": len(thread.messages),
                "workflow_lifecycle": outcomes["workflow_lifecycle"],
            }
            correlations = Correlations(
                command_ids=(command.command_id,),
                workflow_ids=(command.input.workflow_id,),
                instance_ids=(receipt.result.instance_id,),
                step_ids=tuple(values["step_ids"]),
                attempt_ids=tuple(values["attempt_ids"]),
                wait_ids=tuple(outcomes["approval_wait_ids"]),
                thread_ids=(command.input.thread_id,),
                message_ids=(thread.messages[0].message_id,),
                agent_run_ids=tuple(values["agent_run_ids"]),
                domain_event_ids=tuple(values["domain_event_ids"]),
                delivery_ids=tuple(values["delivery_ids"]),
            )
            return observation, correlations
        time.sleep(0.02)
    raise TimeoutError("playground fixture did not reach its safe approval Wait")


@bounded_evidence
def verify_playground(
    *,
    repository_root: Path,
    working_directory: Path,
    output: Path,
    timeout_seconds: int = 120,
) -> PlaygroundArtifact:
    command = (
        "openmagic-evidence",
        "playground",
        "--repository-root",
        str(repository_root.resolve()),
        "--working-directory",
        str(working_directory.resolve()),
        "--output",
        str(output.resolve()),
        "--timeout-seconds",
        str(timeout_seconds),
    )
    started_at = datetime.now(UTC)
    with (
        LocalEmailProvider(working_directory=working_directory / "provider") as provider,
        TestDeployment(working_directory=working_directory / "deployment") as deployment,
    ):
        provider.configure(behaviors=("success",))
        provider_request_baseline = provider.request_count()
        provider_pid = provider.pid
        application = ExampleInsurance(database_url=deployment.database_url)
        application.prepare()
        threads = ThreadStore(database_url=deployment.database_url)
        first_observation, first_correlations = _run_fixture(application, threads, seed=71)
        if provider.request_count() != provider_request_baseline:
            raise AssertionError("playground dispatched an effect while effects were disabled")
        original = deployment.processes
        deployment.drain_role("delivery-worker")
        deployment.drain_role("workflow-worker")
        deployment.drain_role("api")
        reset_synthetic_deployment(deployment.database_url)
        restarted = (
            *deployment.scale_role("api", capacity=1),
            *deployment.scale_role("workflow-worker", capacity=1),
            *deployment.scale_role("delivery-worker", capacity=1),
        )
        schema = audit_cold_schema(deployment.database_url)
        if not schema.passed:
            raise AssertionError(schema.violations)
        original_pids = tuple(process.pid for process in original)
        restarted_pids = tuple(process.pid for process in restarted)
        if set(original_pids) & set(restarted_pids):
            raise AssertionError("playground restart did not use fresh interpreters")
        provider.stop()
        application = ExampleInsurance(database_url=deployment.database_url)
        application.prepare()
        threads = ThreadStore(database_url=deployment.database_url)
        second_observation, second_correlations = _run_fixture(application, threads, seed=71)
        if first_observation != second_observation:
            raise AssertionError("playground reset did not reproduce its deterministic fixture")
        if second_observation["external_email_effect_count"] != 0:
            raise AssertionError("playground fixture enabled an External Effect")
        deployment.drain_role("delivery-worker")
        deployment.drain_role("workflow-worker")
        failure_application = ExampleInsurance(
            database_url=deployment.database_url,
            email_provider_url=provider.url,
        )
        failure_application.prepare()
        failure_threads = ThreadStore(database_url=deployment.database_url)
        failure_command, failure_actor = prepare_renewal_approval(
            failure_application, failure_threads
        )
        approve_renewal(failure_application, failure_command, failure_actor)
        failure_application.run_workflow_worker_once(worker_id="playground-disconnected-provider")
        failure_evidence = json.loads(
            failure_application.renewal_evidence_json(failure_command.input.workflow_id)
        )
        failure_outcomes = failure_evidence["outcomes"]
        if (
            failure_outcomes["workflow_lifecycle"] != "active"
            or failure_outcomes["completion_event_count"] != 0
            or failure_outcomes["external_effect_certainties"] != ["uncertain"]
        ):
            raise AssertionError(
                "disconnected provider did not remain an explicit safe failure: "
                f"{json.dumps(failure_outcomes, sort_keys=True)}"
            )
        failure_observation = {
            "completion_event_count": failure_outcomes["completion_event_count"],
            "external_effect_certainties": failure_outcomes["external_effect_certainties"],
            "workflow_lifecycle": failure_outcomes["workflow_lifecycle"],
        }
        process_ids = (*original_pids, *restarted_pids, provider_pid)
    finished_at = datetime.now(UTC)
    case_correlations = merge_correlations((first_correlations, second_correlations)).model_copy(
        update={"process_ids": process_ids}
    )
    case_observation = {
        "original_process_count": len(original_pids),
        "restarted_process_count": len(restarted_pids),
        "schema_passed": schema.passed,
        "fixture": first_observation,
        "fixture_reproduced_after_reset": True,
        "provider_disconnection_tolerated": True,
        "provider_request_count": 0,
        "intentional_failure": failure_observation,
    }
    artifact = PlaygroundArtifact(
        reproducibility=reproducibility_pin(
            repository_root.resolve(),
            command=command,
            started_at=started_at,
            finished_at=finished_at,
            timeout_seconds=timeout_seconds,
            case_corpus_digest=_digest("issue-71.playground.v1"),
        ),
        cases=(
            ArtifactCase(
                case_id="playground.synthetic-reset-and-process-control",
                case_schema_version=1,
                expected_trials=1,
                observed_trials=1,
                seeds=(0,),
                correlations=case_correlations,
                observation_digests=(_digest(case_observation),),
                scenarios=(
                    DeterministicScenarioEvidence(
                        scenario_id="synthetic-reset-and-process-control",
                        correlations=case_correlations,
                        observation=case_observation,
                        observation_digest=_digest(case_observation),
                    ),
                ),
                verdict=CaseVerdict(status="passed", invariant_violations=()),
            ),
        ),
        summary=PlaygroundSummary(
            synthetic_data_only=True,
            effects_enabled_by_default=False,
            local_provider=True,
            reset_verified=True,
            process_controls_verified=True,
            contributes_to_correctness=False,
        ),
        limitations=(
            "The playground is a local synthetic demonstration.",
            "Playground success does not contribute to deterministic correctness.",
        ),
    )
    write_artifact(output, artifact)
    return artifact


__all__ = ["verify_playground"]
