from __future__ import annotations

import psycopg
from openmagic_evals.harness import (
    issue_verification_challenge,
    renewal_context,
)
from openmagic_runtime.evidence import RuntimeEvidenceReader
from openmagic_runtime.kernel.inspection import KernelInspection


def test_agent_and_deterministic_workflows_share_runtime_attempt_evidence() -> None:
    with renewal_context(verification_code_secret=b"issue-70-reuse-evidence") as (
        database_url,
        application,
        threads,
    ):
        scenario = issue_verification_challenge(application, threads)
        renewal = scenario.renewal
        required = scenario.challenge_receipt
        assert required.result.verification_instance_id is not None
        renewal_instance_id = application.start_renewal_outreach(renewal).result.instance_id
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
