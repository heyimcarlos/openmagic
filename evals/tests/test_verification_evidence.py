from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import psycopg
from example_insurance.renewals import (
    ExampleInsurance,
    SubmitVerificationCode,
    SubmitVerificationCodeInput,
)
from openmagic_evals.evidence.case_recording import record_complete_durable_chain
from openmagic_evals.evidence.inspection import EvidenceInspection
from openmagic_evals.harness import (
    LocalEmailProvider,
    issue_verification_challenge,
    renewal_context,
)
from openmagic_runtime.commands import Cause
from openmagic_runtime.evidence import RuntimeEvidenceReader
from openmagic_runtime.kernel.inspection import KernelInspection


def test_agent_and_deterministic_workflows_share_runtime_attempt_evidence(
    tmp_path: Path,
) -> None:
    with LocalEmailProvider(working_directory=tmp_path / "provider") as provider:
        provider.configure(behaviors=("success",))
        context = renewal_context(
            verification_code_secret=b"issue-70-reuse-evidence",
        )
        with context as (database_url, application, threads):
            scenario = issue_verification_challenge(application, threads)
            renewal = scenario.renewal
            required = scenario.challenge_receipt
            assert required.result.verification_instance_id is not None
            assert required.result.challenge_id is not None
            assert scenario.code is not None
            renewal_instance_id = application.start_renewal_outreach(renewal).result.instance_id
            accepted = application.submit_verification_code(
                SubmitVerificationCode(
                    command_id=uuid4(),
                    actor=scenario.actor,
                    cause=Cause("message", str(uuid4())),
                    input=SubmitVerificationCodeInput(
                        challenge_id=required.result.challenge_id,
                        protected_command_id=scenario.protected_command.command_id,
                        workflow_id=renewal.input.workflow_id,
                        thread_id=renewal.input.thread_id,
                        purpose="renewal.read_approved_details",
                        code=scenario.code,
                    ),
                )
            )
            assert accepted.result.session_id is not None
            effect_application = ExampleInsurance(
                database_url=database_url,
                verification_code_secret=b"issue-70-reuse-evidence",
                email_provider_url=provider.url,
            )
            effect_application.prepare()
            assert effect_application.run_workflow_worker_once(worker_id="trace-email") is not None
            with psycopg.connect(database_url) as connection, connection.transaction():
                reader = RuntimeEvidenceReader(connection)
                agent_workflow = reader.instance(renewal_instance_id)
                deterministic_workflow = reader.instance(required.result.verification_instance_id)

            assert agent_workflow.attempts
            assert agent_workflow.agent_runs
            assert deterministic_workflow.attempts
            assert deterministic_workflow.agent_runs == ()
            assert {attempt.state for attempt in deterministic_workflow.attempts} == {"completed"}
            assert (
                KernelInspection(database_url=database_url)
                .snapshot(required.result.verification_instance_id)
                .definition_key
                == "example_insurance.verification_delivery"
            )
            provider_request_id = str(provider.requests()[0]["provider_request_id"])
            chain = EvidenceInspection(database_url).durable_chain(
                renewal_workflow_id=renewal.input.workflow_id,
                challenge_id=required.result.challenge_id,
                provider_request_id=provider_request_id,
                worker_id="trace-email",
            )
            assert chain.external_effect_ids
            assert chain.provider_request_ids == (provider_request_id,)
            assert chain.worker_ids == ("trace-email",)
            assert "approval-grant-to-external-effect" in chain.relationship_checks
            assert (
                "external-effect-to-attempt-worker-and-provider-request"
                in chain.relationship_checks
            )
            record_complete_durable_chain(
                application=application,
                database_url=database_url,
                renewal_workflow_id=renewal.input.workflow_id,
                challenge_id=required.result.challenge_id,
                provider_request_id=provider_request_id,
                worker_id="trace-email",
                process_id=provider.pid,
            )
