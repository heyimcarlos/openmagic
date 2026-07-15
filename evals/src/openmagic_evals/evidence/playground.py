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
    PlaygroundArtifact,
    PlaygroundSummary,
)
from openmagic_evals.evidence.release import reproducibility_pin
from openmagic_evals.harness import (
    LocalEmailProvider,
    TestDeployment,
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
        provider_pid = provider.pid
        application = ExampleInsurance(database_url=deployment.database_url)
        application.prepare()
        threads = ThreadStore(database_url=deployment.database_url)
        first_observation, first_correlations = _run_fixture(application, threads, seed=71)
        if provider.requests():
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
        process_ids = (*original_pids, *restarted_pids, provider_pid)
    finished_at = datetime.now(UTC)
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
                correlations=Correlations(
                    command_ids=(
                        *first_correlations.command_ids,
                        *second_correlations.command_ids,
                    ),
                    workflow_ids=(
                        *first_correlations.workflow_ids,
                        *second_correlations.workflow_ids,
                    ),
                    instance_ids=(
                        *first_correlations.instance_ids,
                        *second_correlations.instance_ids,
                    ),
                    step_ids=(*first_correlations.step_ids, *second_correlations.step_ids),
                    attempt_ids=(
                        *first_correlations.attempt_ids,
                        *second_correlations.attempt_ids,
                    ),
                    wait_ids=(*first_correlations.wait_ids, *second_correlations.wait_ids),
                    thread_ids=(
                        *first_correlations.thread_ids,
                        *second_correlations.thread_ids,
                    ),
                    message_ids=(
                        *first_correlations.message_ids,
                        *second_correlations.message_ids,
                    ),
                    agent_run_ids=(
                        *first_correlations.agent_run_ids,
                        *second_correlations.agent_run_ids,
                    ),
                    domain_event_ids=(
                        *first_correlations.domain_event_ids,
                        *second_correlations.domain_event_ids,
                    ),
                    delivery_ids=(
                        *first_correlations.delivery_ids,
                        *second_correlations.delivery_ids,
                    ),
                    process_ids=process_ids,
                ),
                observation_digests=(
                    _digest(
                        {
                            "original_process_count": len(original_pids),
                            "restarted_process_count": len(restarted_pids),
                            "schema_passed": schema.passed,
                            "fixture": first_observation,
                            "fixture_reproduced_after_reset": True,
                            "provider_disconnection_tolerated": True,
                            "provider_request_count": 0,
                        }
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
