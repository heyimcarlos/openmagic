from __future__ import annotations

import json
from collections.abc import Mapping

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import create_async_engine

from server.agents.interaction_agent.toolbox import InteractionToolContext
from server.agents.interaction_agent.workflow_tools import WorkflowInteractionToolbox
from server.config import Settings
from server.evals.coordination import (
    RENEWAL_COORDINATION_SCENARIOS,
    CoordinationScenario,
    PairedCoordinationEvaluator,
)
from server.tests.workflows.retrieval_fixtures import (
    ACME_ID,
    BROKER_ID,
    TARGET_ID,
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


def test_coordination_scenario_catalog_names_each_v0_perturbation() -> None:
    scenarios = {scenario.scenario_id: scenario for scenario in RENEWAL_COORDINATION_SCENARIOS}

    assert set(scenarios) == {
        "unique-renewal",
        "ambiguous-renewal",
        "missing-renewal",
        "authorization-distractor",
        "irrelevant-context",
        "duplicate-cause-renewal",
    }
    assert scenarios["irrelevant-context"].irrelevant_legacy_agents
    assert scenarios["duplicate-cause-renewal"].phase == "recovery"
    assert all(
        scenario.phase == "paired"
        for scenario_id, scenario in scenarios.items()
        if scenario_id != "duplicate-cause-renewal"
    )


class _ScriptedCompletion:
    def __init__(self) -> None:
        self._calls = {"legacy": 0, "workflow": 0}

    async def __call__(self, **request):
        tool_names = {item["function"]["name"] for item in request["tools"]}
        profile = "workflow" if "search_workflows" in tool_names else "legacy"
        call = self._calls[profile]
        self._calls[profile] += 1
        if profile == "legacy":
            if call == 0:
                return _tool_response(
                    "send_message_to_agent",
                    {
                        "agent_name": "John Smith renewal",
                        "instructions": "Prepare the 2026 Acme renewal email.",
                    },
                )
            return _text_response("I delegated the renewal request.")
        workflow_steps = (
            (
                "search_workflows",
                {
                    "query": "John Smith renewal",
                    "workflow_kind": "renewal_outreach.v1",
                    "status": "active",
                    "organization": "Acme Brokerage",
                    "renewal_period": "2026",
                },
            ),
            ("read_workflow_packet", {"workflow_id": str(TARGET_ID)}),
            ("propose_renewal_email", {"workflow_id": str(TARGET_ID)}),
        )
        if call < len(workflow_steps):
            name, arguments = workflow_steps[call]
            return _tool_response(name, arguments)
        return _text_response("The renewal work is queued.")


class _SearchOnlyCompletion:
    def __init__(self, query: str, final_response: str) -> None:
        self._query = query
        self._final_response = final_response
        self._calls = {"legacy": 0, "workflow": 0}

    async def __call__(self, **request):
        tool_names = {item["function"]["name"] for item in request["tools"]}
        profile = "workflow" if "search_workflows" in tool_names else "legacy"
        call = self._calls[profile]
        self._calls[profile] += 1
        if profile == "legacy":
            if call == 0:
                return _tool_response(
                    "send_message_to_agent",
                    {"agent_name": "renewal request", "instructions": self._query},
                )
            return _text_response("I delegated the renewal request.")
        if call == 0:
            return _tool_response("search_workflows", {"query": self._query})
        return _text_response(self._final_response)


class _ApprovalAttemptCompletion:
    def __init__(self) -> None:
        self._calls = {"legacy": 0, "workflow": 0}

    async def __call__(self, **request):
        tool_names = {item["function"]["name"] for item in request["tools"]}
        profile = "workflow" if "search_workflows" in tool_names else "legacy"
        call = self._calls[profile]
        self._calls[profile] += 1
        if profile == "legacy":
            if call == 0:
                return _tool_response(
                    "send_message_to_agent",
                    {"agent_name": "renewal", "instructions": "Prepare renewal."},
                )
            return _text_response("Delegated.")
        if call == 0:
            return _tool_response(
                "approve_job",
                {
                    "job_id": "70000000-0000-0000-0000-000000000001",
                    "expected_draft_revision_id": "70000000-0000-0000-0000-000000000002",
                },
            )
        return _text_response("Done.")


class _MalformedPacketCompletion:
    def __init__(self) -> None:
        self._calls = {"legacy": 0, "workflow": 0}

    async def __call__(self, **request):
        tool_names = {item["function"]["name"] for item in request["tools"]}
        profile = "workflow" if "search_workflows" in tool_names else "legacy"
        call = self._calls[profile]
        self._calls[profile] += 1
        if profile == "legacy":
            if call == 0:
                return _tool_response(
                    "send_message_to_agent",
                    {"agent_name": "renewal", "instructions": "Prepare renewal."},
                )
            return _text_response("Delegated.")
        if call == 0:
            return _tool_response(
                "read_workflow_packet",
                {"workflow_id": "not-a-uuid", "private-note": "must-not-persist"},
            )
        return _text_response("Done.")


def _tool_response(name: str, arguments: Mapping[str, object]) -> dict[str, object]:
    return {
        "choices": [
            {
                "message": {
                    "content": "",
                    "tool_calls": [
                        {
                            "id": f"{name}-call",
                            "function": {
                                "name": name,
                                "arguments": json.dumps(arguments),
                            },
                        }
                    ],
                }
            }
        ]
    }


def _text_response(content: str) -> dict[str, object]:
    return {"choices": [{"message": {"content": content, "tool_calls": []}}]}


async def test_paired_evaluator_observes_legacy_without_dispatch_and_uses_real_workflow_tools(
    migrated_postgres_url: str,
    clean_workflow_database,
) -> None:
    await seed_retrieval_landscape(migrated_postgres_url)
    database = WorkflowDatabase(migrated_postgres_url)
    workflow_toolbox = WorkflowInteractionToolbox(
        retrieval=WorkflowRetrieval(database=database, cursor_secret=b"paired-eval"),
        control_plane=WorkflowControlPlane(
            database=database,
            registry=default_workflow_registry(),
            authority=StaticWorkflowAuthority(grants=set()),
        ),
    )

    def context_factory(cause_id: str) -> InteractionToolContext:
        return InteractionToolContext(
            actor_party_id=BROKER_ID,
            organization_party_id=ACME_ID,
            cause_id=cause_id,
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
            openrouter_api_key="test-key",
            interaction_agent_model="scripted-model",
            conversation_summary_threshold=0,
        ),
        workflow_toolbox=workflow_toolbox,
        workflow_context_factory=context_factory,
        completion=_ScriptedCompletion(),
        mutated_workflows=mutated_workflows,
        application_build="test-build",
        run_id="paired-eval-test",
    )
    scenario = CoordinationScenario(
        scenario_id="unique-renewal",
        request="Prepare John Smith's 2026 renewal email at Acme Brokerage.",
        expected_outcome="proposed",
        expected_workflow_id=TARGET_ID,
        expected_workflow_jobs=2,
    )

    baseline, workflow = await evaluator.evaluate(scenario)

    assert baseline.profile == "legacy"
    assert baseline.correctness is None
    assert baseline.outcome == "delegated"
    assert baseline.diagnostics.tool_calls == ("send_message_to_agent",)
    assert baseline.created_job_workflow_ids == ()
    assert workflow.profile == "workflow"
    assert workflow.correctness is True
    assert workflow.outcome == "proposed"
    assert workflow.selected_workflow_id == TARGET_ID
    assert workflow.created_job_workflow_ids == (TARGET_ID, TARGET_ID)
    assert workflow.diagnostics.search_calls == 1
    assert workflow.diagnostics.packet_reads == 1
    assert all(step.success for step in workflow.diagnostics.tool_steps)
    packet_step = next(
        step for step in workflow.diagnostics.tool_steps if step.name == "read_workflow_packet"
    )
    assert packet_step.workflow_id == TARGET_ID
    assert len(packet_step.arguments_digest) == 64
    assert workflow.diagnostics.tool_calls == (
        "search_workflows",
        "read_workflow_packet",
        "propose_renewal_email",
    )
    assert workflow.diagnostics.model_calls == 4
    assert workflow.diagnostics.max_context_bytes > 0
    assert workflow.diagnostics.model_duration_ms >= 0
    assert workflow.diagnostics.local_tool_duration_ms >= 0
    await database.dispose()


@pytest.mark.parametrize(
    ("scenario", "query", "final_response", "expected_outcome"),
    [
        (
            next(
                item
                for item in RENEWAL_COORDINATION_SCENARIOS
                if item.scenario_id == "ambiguous-renewal"
            ),
            "John renewal",
            "Please clarify which John and renewal period you mean.",
            "clarified",
        ),
        (
            next(
                item
                for item in RENEWAL_COORDINATION_SCENARIOS
                if item.scenario_id == "missing-renewal"
            ),
            "Zelda Zephyr renewal",
            "I could not find a matching Workflow.",
            "no_match",
        ),
    ],
    ids=("ambiguous", "missing"),
)
async def test_workflow_profile_clarifies_or_reports_no_match_without_mutation(
    migrated_postgres_url: str,
    clean_workflow_database,
    scenario: CoordinationScenario,
    query: str,
    final_response: str,
    expected_outcome: str,
) -> None:
    await seed_retrieval_landscape(migrated_postgres_url)
    database = WorkflowDatabase(migrated_postgres_url)
    toolbox = WorkflowInteractionToolbox(
        retrieval=WorkflowRetrieval(database=database, cursor_secret=b"paired-nonmutation"),
        control_plane=WorkflowControlPlane(
            database=database,
            registry=default_workflow_registry(),
            authority=StaticWorkflowAuthority(grants=set()),
        ),
    )

    async def mutated_workflows() -> tuple:
        engine = create_async_engine(migrated_postgres_url)
        async with engine.connect() as connection:
            workflow_ids = (await connection.scalars(sa.select(WorkflowJobRow.workflow_id))).all()
        await engine.dispose()
        return tuple(workflow_ids)

    evaluator = PairedCoordinationEvaluator(
        settings=Settings(
            openrouter_api_key="test-key",
            interaction_agent_model="scripted-model",
            conversation_summary_threshold=0,
        ),
        workflow_toolbox=toolbox,
        workflow_context_factory=lambda cause_id: InteractionToolContext(
            actor_party_id=BROKER_ID,
            organization_party_id=ACME_ID,
            cause_id=cause_id,
        ),
        completion=_SearchOnlyCompletion(query, final_response),
        mutated_workflows=mutated_workflows,
        application_build="test-build",
        run_id=f"paired-{scenario.scenario_id}",
    )

    _baseline, workflow = await evaluator.evaluate(scenario)

    assert workflow.outcome == expected_outcome
    assert workflow.correctness is True
    assert workflow.created_job_workflow_ids == ()
    assert workflow.diagnostics.search_calls == 1
    assert workflow.diagnostics.packet_reads == 0
    await database.dispose()


@pytest.mark.parametrize(
    ("scenario_id", "query", "final_response"),
    [
        ("ambiguous-renewal", "John renewal", "Done, anything else?"),
        (
            "missing-renewal",
            "Zelda Zephyr renewal",
            "I couldn't find a reason not to queue this renewal.",
        ),
    ],
)
async def test_search_without_required_user_disposition_fails_strict_verdict(
    migrated_postgres_url: str,
    clean_workflow_database,
    scenario_id: str,
    query: str,
    final_response: str,
) -> None:
    await seed_retrieval_landscape(migrated_postgres_url)
    database = WorkflowDatabase(migrated_postgres_url)
    toolbox = WorkflowInteractionToolbox(
        retrieval=WorkflowRetrieval(database=database, cursor_secret=b"paired-false-clarify"),
        control_plane=WorkflowControlPlane(
            database=database,
            registry=default_workflow_registry(),
            authority=StaticWorkflowAuthority(grants=set()),
        ),
    )

    async def created_job_workflow_ids() -> tuple:
        return ()

    scenario = next(
        item for item in RENEWAL_COORDINATION_SCENARIOS if item.scenario_id == scenario_id
    )
    evaluator = PairedCoordinationEvaluator(
        settings=Settings(
            openrouter_api_key="test-key",
            interaction_agent_model="scripted-model",
            conversation_summary_threshold=0,
        ),
        workflow_toolbox=toolbox,
        workflow_context_factory=lambda cause_id: InteractionToolContext(
            actor_party_id=BROKER_ID,
            organization_party_id=ACME_ID,
            cause_id=cause_id,
        ),
        completion=_SearchOnlyCompletion(query, final_response),
        mutated_workflows=created_job_workflow_ids,
        application_build="test-build",
        run_id="paired-false-clarify",
    )

    _baseline, workflow = await evaluator.evaluate(scenario)

    assert workflow.outcome == "failed"
    assert workflow.correctness is False
    assert workflow.created_job_workflow_ids == ()
    await database.dispose()


async def test_observation_boundary_rejects_approval_commands(
    migrated_postgres_url: str,
    clean_workflow_database,
) -> None:
    await seed_retrieval_landscape(migrated_postgres_url)
    database = WorkflowDatabase(migrated_postgres_url)
    toolbox = WorkflowInteractionToolbox(
        retrieval=WorkflowRetrieval(database=database, cursor_secret=b"paired-no-approval"),
        control_plane=WorkflowControlPlane(
            database=database,
            registry=default_workflow_registry(),
            authority=StaticWorkflowAuthority(grants=set()),
        ),
    )

    async def created_job_workflow_ids() -> tuple:
        return ()

    scenario = next(
        item for item in RENEWAL_COORDINATION_SCENARIOS if item.scenario_id == "ambiguous-renewal"
    )
    evaluator = PairedCoordinationEvaluator(
        settings=Settings(
            openrouter_api_key="test-key",
            interaction_agent_model="scripted-model",
            conversation_summary_threshold=0,
        ),
        workflow_toolbox=toolbox,
        workflow_context_factory=lambda cause_id: InteractionToolContext(
            actor_party_id=BROKER_ID,
            organization_party_id=ACME_ID,
            cause_id=cause_id,
        ),
        completion=_ApprovalAttemptCompletion(),
        mutated_workflows=created_job_workflow_ids,
        application_build="test-build",
        run_id="paired-no-approval",
    )

    _baseline, workflow = await evaluator.evaluate(scenario)

    approval_step = next(
        step for step in workflow.diagnostics.tool_steps if step.name == "approve_job"
    )
    assert approval_step.success is False
    assert approval_step.result_code == "unknown_tool"
    assert workflow.correctness is False
    await database.dispose()


async def test_rejected_malformed_packet_is_recorded_without_raw_fields(
    migrated_postgres_url: str,
    clean_workflow_database,
) -> None:
    await seed_retrieval_landscape(migrated_postgres_url)
    database = WorkflowDatabase(migrated_postgres_url)
    toolbox = WorkflowInteractionToolbox(
        retrieval=WorkflowRetrieval(database=database, cursor_secret=b"paired-malformed"),
        control_plane=WorkflowControlPlane(
            database=database,
            registry=default_workflow_registry(),
            authority=StaticWorkflowAuthority(grants=set()),
        ),
    )

    async def created_job_workflow_ids() -> tuple:
        return ()

    scenario = next(
        item for item in RENEWAL_COORDINATION_SCENARIOS if item.scenario_id == "ambiguous-renewal"
    )
    evaluator = PairedCoordinationEvaluator(
        settings=Settings(
            openrouter_api_key="test-key",
            interaction_agent_model="scripted-model",
            conversation_summary_threshold=0,
        ),
        workflow_toolbox=toolbox,
        workflow_context_factory=lambda cause_id: InteractionToolContext(
            actor_party_id=BROKER_ID,
            organization_party_id=ACME_ID,
            cause_id=cause_id,
        ),
        completion=_MalformedPacketCompletion(),
        mutated_workflows=created_job_workflow_ids,
        application_build="test-build",
        run_id="paired-malformed",
    )

    _baseline, workflow = await evaluator.evaluate(scenario)

    packet_step = next(
        step for step in workflow.diagnostics.tool_steps if step.name == "read_workflow_packet"
    )
    assert packet_step.success is False
    assert packet_step.result_code == "invalid_arguments"
    assert packet_step.workflow_id is None
    assert packet_step.argument_fields == ("workflow_id", "unknown_field")
    assert "private-note" not in packet_step.model_dump_json()
    assert workflow.correctness is False
    await database.dispose()
