"""Opt-in real-model comparison of legacy and Workflow coordination."""

from __future__ import annotations

import os
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import create_async_engine

from server.agents.interaction_agent.toolbox import InteractionToolContext
from server.agents.interaction_agent.workflow_tools import WorkflowInteractionToolbox
from server.config import Settings
from server.evals.coordination import (
    RENEWAL_COORDINATION_SCENARIOS,
    PairedCoordinationEvaluator,
    build_coordination_report,
    write_coordination_report,
)
from server.openrouter_client import request_chat_completion
from server.tests.workflows.retrieval_fixtures import (
    ACME_ID,
    BROKER_ID,
    seed_retrieval_landscape,
)
from server.workflows import (
    StaticWorkflowAuthority,
    WorkflowControlPlane,
    WorkflowDatabase,
    WorkflowRetrieval,
    default_workflow_registry,
)
from server.workflows.models import WorkflowJobRow


@pytest.mark.skipif(
    os.getenv("OPENMAGIC_RUN_PAIRED_COORDINATION_EVAL") != "1",
    reason="credentialed paired coordination evaluation is opt-in",
)
@pytest.mark.timeout(300)
async def test_real_model_compares_both_profiles_without_external_execution(
    migrated_postgres_url: str,
    clean_workflow_database,
    record_property: Callable[[str, object], None],
) -> None:
    api_key = _required("OPENROUTER_API_KEY")
    application_build = _required("OPENMAGIC_EVAL_APPLICATION_BUILD")
    model = os.getenv("OPENMAGIC_PAIRED_EVAL_MODEL", Settings().interaction_agent_model)
    output_directory = Path(
        os.getenv("OPENMAGIC_PAIRED_EVAL_OUTPUT_DIR", "/tmp/openmagic-paired-eval")
    )
    run_id = f"paired-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}"
    trials = []

    for scenario in RENEWAL_COORDINATION_SCENARIOS:
        if scenario.phase != "paired":
            continue
        await _reset_landscape(migrated_postgres_url)
        database = WorkflowDatabase(migrated_postgres_url)
        toolbox = WorkflowInteractionToolbox(
            retrieval=WorkflowRetrieval(
                database=database,
                cursor_secret=f"{run_id}:{scenario.scenario_id}".encode(),
            ),
            control_plane=WorkflowControlPlane(
                database=database,
                registry=default_workflow_registry(),
                authority=StaticWorkflowAuthority(grants=set()),
            ),
        )

        async def mutated_workflows() -> tuple:
            engine = create_async_engine(migrated_postgres_url)
            async with engine.connect() as connection:
                workflow_ids = (
                    await connection.scalars(
                        sa.select(WorkflowJobRow.workflow_id).order_by(WorkflowJobRow.id)
                    )
                ).all()
            await engine.dispose()
            return tuple(workflow_ids)

        evaluator = PairedCoordinationEvaluator(
            settings=Settings(
                openrouter_api_key=api_key,
                interaction_agent_model=model,
                conversation_summary_threshold=0,
            ),
            workflow_toolbox=toolbox,
            workflow_context_factory=lambda cause_id: InteractionToolContext(
                actor_party_id=BROKER_ID,
                organization_party_id=ACME_ID,
                cause_id=cause_id,
            ),
            completion=request_chat_completion,
            mutated_workflows=mutated_workflows,
            application_build=application_build,
            run_id=run_id,
        )
        try:
            trials.extend(await evaluator.evaluate(scenario))
        finally:
            await database.dispose()

    report = build_coordination_report(
        run_id=run_id,
        generated_at=datetime.now(UTC),
        trials=tuple(trials),
    )
    json_path, markdown_path = write_coordination_report(report, output_directory)
    record_property("paired_eval_json", str(json_path))
    record_property("paired_eval_markdown", str(markdown_path))
    record_property("workflow_trials", report.workflow_trials)
    record_property("baseline_trials", report.baseline_trials)

    assert report.v0_passed is True


async def _reset_landscape(database_url: str) -> None:
    engine = create_async_engine(database_url)
    async with engine.begin() as connection:
        await connection.execute(
            sa.text(
                "TRUNCATE notifications, workflow_events, interaction_causes, "
                "workflow_job_runs, workflow_job_dependencies, workflow_jobs, "
                "workflow_participant_roles, workflow_participants, "
                "organization_memberships, party_identifiers, workflows, parties CASCADE"
            )
        )
    await engine.dispose()
    await seed_retrieval_landscape(database_url)


def _required(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Paired coordination evaluation requires {name}")
    return value
