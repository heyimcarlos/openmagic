from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from uuid import UUID

import psycopg
import pytest
from example_insurance.renewals import ExampleInsurance
from openmagic_evals.evidence.case_recording import record_renewal_case
from openmagic_evals.harness import (
    LocalEmailProvider,
    PlaygroundDeployment,
    approve_renewal,
    prepare_renewal_approval,
    wait_for_database_fault_window,
    wait_for_renewal_completion,
)
from openmagic_runtime.threads import ThreadStore


@pytest.mark.integration
def test_fresh_process_recovers_after_fence_commit_before_provider_io(tmp_path) -> None:
    provider = LocalEmailProvider(working_directory=tmp_path / "provider")
    deployment = PlaygroundDeployment(
        working_directory=tmp_path / "deployment",
        email_provider_url=provider.url,
    )
    with provider, deployment:
        provider.configure(behaviors=("success",))
        deployment.terminate_role("workflow-worker")
        deployment.terminate_role("delivery-worker")
        application = ExampleInsurance(
            database_url=deployment.database_url,
            email_provider_url=provider.url,
        )
        application.prepare()
        command, actor = prepare_renewal_approval(
            application,
            ThreadStore(database_url=deployment.database_url),
        )
        approve_renewal(application, command, actor)

        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "openmagic_evals.harness.fence_once",
                "--database-url",
                deployment.database_url,
                "--email-provider-url",
                provider.url,
                "--worker-id",
                "fence-only-process",
            ],
            cwd=tmp_path,
            env={"PATH": os.defpath, "PYTHONNOUSERSITE": "1", "PYTHONUNBUFFERED": "1"},
            capture_output=True,
            check=True,
        )
        fenced = json.loads(completed.stdout)

        assert UUID(fenced["attempt_id"])
        assert provider.requests() == ()
        time.sleep(1.1)
        recovery_process = deployment.restart_role("workflow-worker")
        evidence = wait_for_renewal_completion(application, command.input.workflow_id)

        assert recovery_process.pid > 0
        assert evidence["outcomes"]["external_effect_certainties"] == ["applied"]
        assert len(provider.reconciliations()) >= 1
        assert len(provider.requests()) == 1
        record_renewal_case(
            case_id="external-effect.fenced-uncertainty",
            scenario_id="after-dispatch-record-before-io",
            application=application,
            database_url=deployment.database_url,
            workflow_id=command.input.workflow_id,
            document={
                "fenced_attempt_id": fenced["attempt_id"],
                "requests_before_recovery": 0,
                "requests_after_recovery": len(provider.requests()),
            },
            process_ids=(recovery_process.pid,),
        )


@pytest.mark.integration
def test_fresh_process_loss_before_fence_allows_only_safe_retry(tmp_path) -> None:
    provider = LocalEmailProvider(working_directory=tmp_path / "provider")
    deployment = PlaygroundDeployment(
        working_directory=tmp_path / "deployment",
        email_provider_url=provider.url,
    )
    with provider, deployment:
        provider.configure(behaviors=("success",))
        deployment.terminate_role("workflow-worker")
        deployment.terminate_role("delivery-worker")
        application = ExampleInsurance(
            database_url=deployment.database_url,
            email_provider_url=provider.url,
        )
        application.prepare()
        command, actor = prepare_renewal_approval(
            application,
            ThreadStore(database_url=deployment.database_url),
        )
        approve_renewal(application, command, actor)
        with psycopg.connect(deployment.database_url) as connection, connection.transaction():
            connection.execute(
                "CREATE FUNCTION example_insurance.pause_effect_fence() RETURNS trigger "
                "LANGUAGE plpgsql AS $$ BEGIN PERFORM pg_sleep(10); RETURN NEW; END $$"
            )
            connection.execute(
                "CREATE TRIGGER pause_effect_fence BEFORE INSERT ON "
                "example_insurance.external_effects FOR EACH ROW EXECUTE FUNCTION "
                "example_insurance.pause_effect_fence()"
            )

        lost = deployment.restart_role("workflow-worker")
        wait_for_database_fault_window(
            deployment.database_url,
            "INSERT INTO example_insurance.external_effects",
        )
        deployment.terminate_role("workflow-worker")
        with psycopg.connect(deployment.database_url) as connection, connection.transaction():
            connection.execute(
                "DROP TRIGGER pause_effect_fence ON example_insurance.external_effects"
            )
            connection.execute("DROP FUNCTION example_insurance.pause_effect_fence()")
        time.sleep(1.1)
        recovered = deployment.restart_role("workflow-worker")
        evidence = wait_for_renewal_completion(application, command.input.workflow_id)

        assert recovered.pid != lost.pid
        assert evidence["outcomes"]["attempt_states"].count("abandoned") == 1
        assert len(provider.requests()) == 1
        record_renewal_case(
            case_id="external-effect.fenced-uncertainty",
            scenario_id="before-dispatch-record",
            application=application,
            database_url=deployment.database_url,
            workflow_id=command.input.workflow_id,
            document={
                "lost_process_id": lost.pid,
                "recovered_process_id": recovered.pid,
                "provider_requests": len(provider.requests()),
            },
            process_ids=(lost.pid, recovered.pid),
        )


@pytest.mark.integration
def test_fresh_process_loss_during_reconciliation_preserves_uncertainty(tmp_path) -> None:
    provider = LocalEmailProvider(working_directory=tmp_path / "provider")
    deployment = PlaygroundDeployment(
        working_directory=tmp_path / "deployment",
        email_provider_url=provider.url,
    )
    with provider, deployment:
        provider.configure(
            behaviors=("response_loss_after_success",),
            reconciliation="slow_applied",
            delay_seconds=3,
        )
        deployment.terminate_role("workflow-worker")
        deployment.terminate_role("delivery-worker")
        application = ExampleInsurance(
            database_url=deployment.database_url,
            email_provider_url=provider.url,
        )
        application.prepare()
        command, actor = prepare_renewal_approval(
            application,
            ThreadStore(database_url=deployment.database_url),
        )
        approve_renewal(application, command, actor)

        lost = deployment.restart_role("workflow-worker")
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline and not provider.reconciliations():
            time.sleep(0.02)
        assert provider.reconciliations()
        deployment.terminate_role("workflow-worker")
        time.sleep(3.2)
        provider.configure(behaviors=("success",), reconciliation="unchanged")
        recovered = deployment.restart_role("workflow-worker")
        evidence = wait_for_renewal_completion(application, command.input.workflow_id)

        assert recovered.pid != lost.pid
        assert len(provider.requests()) == 1
        assert evidence["outcomes"]["completion_event_count"] == 1
        assert any(
            item["classification"] == "uncertain"
            for item in evidence["outcomes"]["effect_evidence"]
        )
        record_renewal_case(
            case_id="recovery.fresh-process",
            scenario_id="during-recovery",
            application=application,
            database_url=deployment.database_url,
            workflow_id=command.input.workflow_id,
            document={
                "lost_process_id": lost.pid,
                "recovered_process_id": recovered.pid,
                "completion_event_count": evidence["outcomes"]["completion_event_count"],
                "uncertain_observation_preserved": True,
            },
            process_ids=(lost.pid, recovered.pid),
        )


@pytest.mark.integration
def test_fresh_process_loss_during_provider_io_reconciles_without_redispatch(tmp_path) -> None:
    provider = LocalEmailProvider(working_directory=tmp_path / "provider")
    deployment = PlaygroundDeployment(
        working_directory=tmp_path / "deployment",
        email_provider_url=provider.url,
    )
    with provider, deployment:
        provider.configure(
            behaviors=("slow_success",),
            reconciliation="unchanged",
            delay_seconds=3,
        )
        deployment.terminate_role("workflow-worker")
        deployment.terminate_role("delivery-worker")
        application = ExampleInsurance(
            database_url=deployment.database_url,
            email_provider_url=provider.url,
        )
        application.prepare()
        command, actor = prepare_renewal_approval(
            application,
            ThreadStore(database_url=deployment.database_url),
        )
        approve_renewal(application, command, actor)

        lost = deployment.restart_role("workflow-worker")
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline and not provider.requests():
            time.sleep(0.02)
        assert provider.requests()
        deployment.terminate_role("workflow-worker")
        time.sleep(3.2)
        recovered = deployment.restart_role("workflow-worker")
        evidence = wait_for_renewal_completion(application, command.input.workflow_id)

        assert recovered.pid != lost.pid
        assert len(provider.requests()) == 1
        assert len(provider.reconciliations()) >= 1
        assert evidence["outcomes"]["external_effect_certainties"] == ["applied"]
        record_renewal_case(
            case_id="recovery.fresh-process",
            scenario_id="during-provider-io",
            application=application,
            database_url=deployment.database_url,
            workflow_id=command.input.workflow_id,
            document={
                "lost_process_id": lost.pid,
                "recovered_process_id": recovered.pid,
                "provider_requests": len(provider.requests()),
                "reconciliations": len(provider.reconciliations()),
            },
            process_ids=(lost.pid, recovered.pid),
        )


@pytest.mark.integration
def test_completion_event_and_instance_closure_recover_atomically(tmp_path) -> None:
    provider = LocalEmailProvider(working_directory=tmp_path / "provider")
    deployment = PlaygroundDeployment(
        working_directory=tmp_path / "deployment",
        email_provider_url=provider.url,
    )
    with provider, deployment:
        provider.configure(behaviors=("success",))
        deployment.terminate_role("workflow-worker")
        deployment.terminate_role("delivery-worker")
        application = ExampleInsurance(
            database_url=deployment.database_url,
            email_provider_url=provider.url,
        )
        application.prepare()
        command, actor = prepare_renewal_approval(
            application,
            ThreadStore(database_url=deployment.database_url),
        )
        approve_renewal(application, command, actor)
        with psycopg.connect(deployment.database_url) as connection, connection.transaction():
            connection.execute(
                "CREATE FUNCTION example_insurance.pause_completion() RETURNS trigger "
                "LANGUAGE plpgsql AS $$ BEGIN IF NEW.lifecycle = 'completed' THEN "
                "PERFORM pg_sleep(10); END IF; RETURN NEW; END $$"
            )
            connection.execute(
                "CREATE TRIGGER pause_completion BEFORE UPDATE ON "
                "example_insurance.renewal_workflows FOR EACH ROW EXECUTE FUNCTION "
                "example_insurance.pause_completion()"
            )

        lost = deployment.restart_role("workflow-worker")
        wait_for_database_fault_window(
            deployment.database_url,
            "UPDATE example_insurance.renewal_workflows SET lifecycle = 'completed'",
        )
        deployment.terminate_role("workflow-worker")
        with psycopg.connect(deployment.database_url) as connection, connection.transaction():
            connection.execute(
                "DROP TRIGGER pause_completion ON example_insurance.renewal_workflows"
            )
            connection.execute("DROP FUNCTION example_insurance.pause_completion()")
        time.sleep(1.1)
        recovered = deployment.restart_role("workflow-worker")
        evidence = wait_for_renewal_completion(application, command.input.workflow_id)
        deployment.terminate_role("workflow-worker")
        after_commit = deployment.restart_role("workflow-worker")
        replayed_evidence = json.loads(application.renewal_evidence_json(command.input.workflow_id))

        assert recovered.pid != lost.pid
        assert after_commit.pid != recovered.pid
        assert evidence == replayed_evidence
        assert evidence["outcomes"]["workflow_lifecycle"] == "completed"
        assert evidence["outcomes"]["instance_state"] == "closed"
        assert evidence["outcomes"]["completion_event_count"] == 1
        assert len(provider.requests()) == 1
        record_renewal_case(
            case_id="completion.evidence-backed",
            scenario_id="completion-loss-recovery",
            application=application,
            database_url=deployment.database_url,
            workflow_id=command.input.workflow_id,
            document={
                "lost_process_id": lost.pid,
                "recovered_process_id": recovered.pid,
                "after_commit_process_id": after_commit.pid,
                "completion_event_count": evidence["outcomes"]["completion_event_count"],
                "instance_state": evidence["outcomes"]["instance_state"],
            },
            process_ids=(lost.pid, recovered.pid, after_commit.pid),
        )
        record_renewal_case(
            case_id="recovery.fresh-process",
            scenario_id="after-accepted-result",
            application=application,
            database_url=deployment.database_url,
            workflow_id=command.input.workflow_id,
            document={
                "lost_process_id": lost.pid,
                "recovered_process_id": recovered.pid,
                "after_commit_process_id": after_commit.pid,
                "completion_event_count": evidence["outcomes"]["completion_event_count"],
                "replayed_value_identically": evidence == replayed_evidence,
            },
            process_ids=(lost.pid, recovered.pid, after_commit.pid),
        )
