from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import create_async_engine

from server.agents.interaction_agent.factory import create_interaction_runtime
from server.agents.interaction_agent.runtime import InteractionAgentRuntime
from server.agents.interaction_agent.toolbox import InteractionToolContext, ToolResult
from server.agents.interaction_agent.tools import LegacyInteractionToolbox
from server.agents.interaction_agent.workflow_agent import (
    build_workflow_system_prompt,
    prepare_workflow_message,
)
from server.agents.interaction_agent.workflow_tools import WorkflowInteractionToolbox
from server.config import Settings
from server.tests.workflows.retrieval_fixtures import (
    ACME_ID,
    BROKER_ID,
    JOHN_ACME_ID,
    SAME_NAME_ID,
    TARGET_ID,
    seed_retrieval_landscape,
)
from server.workflows import (
    DeterministicVerificationEmailSender,
    InteractionActivityAction,
    InteractionActivityStatus,
    InteractionActivityStore,
    ProtectedOperation,
    StaticWorkflowAuthority,
    StepUpVerification,
    SubmitVerificationCodeCommand,
    VerificationEmailExecutionHandler,
    WorkflowControlPlane,
    WorkflowDatabase,
    WorkflowInspectionContext,
    WorkflowRetrieval,
    WorkflowWorker,
    default_workflow_registry,
)


@pytest.fixture
async def workflow_toolbox(migrated_postgres_url: str, clean_workflow_database):
    await seed_retrieval_landscape(migrated_postgres_url)
    database = WorkflowDatabase(migrated_postgres_url)
    retrieval = WorkflowRetrieval(database=database, cursor_secret=b"workflow-tool-test")
    control_plane = WorkflowControlPlane(
        database=database,
        registry=default_workflow_registry(),
        authority=StaticWorkflowAuthority(grants=set()),
    )
    yield WorkflowInteractionToolbox(retrieval=retrieval, control_plane=control_plane)
    await database.dispose()


async def _deliver_verification_code(
    database: WorkflowDatabase,
    control_plane: WorkflowControlPlane,
    verification: StepUpVerification,
) -> str:
    sender = DeterministicVerificationEmailSender()
    worker = WorkflowWorker(
        control_plane=control_plane,
        executors={},
        deterministic_handlers={
            "composio_verification_email": VerificationEmailExecutionHandler(
                verification=verification,
                sender=sender,
            )
        },
        worker_id="verification-email-worker",
        application_build="workflow-tool-test",
    )
    assert await worker.run_once() is not None
    return sender.deliveries[0].code


def _tool_call(name: str, arguments: dict[str, object]) -> dict[str, object]:
    return {
        "choices": [
            {
                "message": {
                    "content": "",
                    "tool_calls": [
                        {
                            "id": name,
                            "type": "function",
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


def test_workflow_tool_surface_omits_delegation_and_execution_configuration(
    workflow_toolbox: WorkflowInteractionToolbox,
):
    encoded = json.dumps(workflow_toolbox.schemas)

    assert "search_workflows" in encoded
    assert "read_workflow_packet" in encoded
    assert "propose_renewal_email" in encoded
    for forbidden in (
        "send_message_to_agent",
        "agent_name",
        "actor_party_id",
        "organization_party_id",
        "executor",
        "handler",
        "prompt",
        "max_attempts",
        "sender_mailbox",
        "recipient_email",
    ):
        assert forbidden not in encoded
    proposal_schema = next(
        schema["function"]["parameters"]
        for schema in workflow_toolbox.schemas
        if schema["function"]["name"] == "propose_renewal_email"
    )
    assert "status" not in proposal_schema["properties"]


def test_runtime_factory_defaults_to_workflow_mode_and_keeps_legacy_explicit(
    migrated_postgres_url: str,
):
    common: dict[str, Any] = {
        "openrouter_api_key": "test-key",
        "database_url": migrated_postgres_url,
        "workflow_cursor_secret": "workflow-factory-test",
        "workflow_broker_party_id": str(BROKER_ID),
        "workflow_organization_party_id": str(ACME_ID),
        "verification_code_secret": "workflow-factory-verification-secret",
        "composio_api_key": "test-composio-key",
        "workflow_composio_user_id": "test-composio-user",
    }

    workflow_runtime = create_interaction_runtime(Settings(**common))
    legacy_runtime = create_interaction_runtime(Settings(**common, interaction_mode="legacy"))

    assert isinstance(workflow_runtime.toolbox, WorkflowInteractionToolbox)
    assert isinstance(legacy_runtime.toolbox, LegacyInteractionToolbox)
    assert all(
        schema["function"]["name"] != "send_message_to_agent"
        for schema in workflow_runtime.tool_schemas
    )
    assert any(
        schema["function"]["name"] == "send_message_to_agent"
        for schema in legacy_runtime.tool_schemas
    )


async def test_workflow_tools_search_read_one_packet_then_propose(
    workflow_toolbox: WorkflowInteractionToolbox,
):
    context = InteractionToolContext(
        actor_party_id=BROKER_ID,
        organization_party_id=ACME_ID,
        cause_id="message-issue-18",
    )
    await workflow_toolbox.record_interaction_cause(context, "Prepare the selected renewal.")

    search = await workflow_toolbox.invoke(
        "search_workflows",
        {
            "query": "John Smith renewal",
            "workflow_kind": "renewal_outreach.v1",
            "status": "active",
            "organization": "Acme Brokerage",
            "renewal_period": "2026",
        },
        context,
    )
    assert search.success is True
    assert search.payload["results"][0]["workflow_id"] == str(TARGET_ID)

    packet = await workflow_toolbox.invoke(
        "read_workflow_packet",
        {"workflow_id": str(TARGET_ID)},
        context,
    )
    assert packet.success is True
    assert context.loaded_packet is not None
    assert context.loaded_packet.workflow.workflow_id == TARGET_ID

    proposal = await workflow_toolbox.invoke(
        "propose_renewal_email",
        {"workflow_id": str(TARGET_ID)},
        context,
    )
    assert proposal.success is True
    assert proposal.payload["workflow_id"] == str(TARGET_ID)
    assert len(proposal.payload["job_ids"]) == 2


async def test_protected_packet_read_returns_verification_required_from_actual_tool(
    migrated_postgres_url: str,
    clean_workflow_database,
):
    await seed_retrieval_landscape(migrated_postgres_url)
    database = WorkflowDatabase(migrated_postgres_url)
    retrieval = WorkflowRetrieval(database=database, cursor_secret=b"protected-tool-test")
    control_plane = WorkflowControlPlane(
        database=database,
        registry=default_workflow_registry(),
        authority=StaticWorkflowAuthority(grants=set()),
    )
    verification = StepUpVerification(
        database=database,
        code_secret=b"protected-tool-verification-secret",
    )
    toolbox = WorkflowInteractionToolbox(
        retrieval=retrieval,
        control_plane=control_plane,
        verification=verification,
    )
    context = InteractionToolContext(
        actor_party_id=JOHN_ACME_ID,
        organization_party_id=ACME_ID,
        cause_id="private-packet-message",
        interaction_id="sms-policyholder-demo",
    )
    await toolbox.record_interaction_cause(context, "Show me my renewal details.")
    search = await toolbox.invoke(
        "search_workflows",
        {
            "query": "John Smith renewal",
            "workflow_kind": "renewal_outreach.v1",
            "status": "active",
            "organization": "Acme Brokerage",
            "renewal_period": "2026",
        },
        context,
    )
    assert search.success is True
    assert search.payload["results"][0]["workflow_id"] == str(TARGET_ID)
    assert search.payload["total_matches"] == 1
    assert search.payload["facets"]

    packet = await toolbox.invoke(
        "read_workflow_packet",
        {"workflow_id": str(TARGET_ID)},
        context,
    )

    assert packet.success is False
    assert packet.payload["code"] == "verification_required"
    assert packet.payload["delivery_method"] == "email_code"
    assert packet.payload["destination"] == "j***@example.com"
    assert packet.payload["challenge_id"]
    assert context.loaded_packet is None
    await database.dispose()


async def test_verified_session_covers_later_sensitive_write(
    migrated_postgres_url: str,
    clean_workflow_database,
):
    await seed_retrieval_landscape(migrated_postgres_url)
    database = WorkflowDatabase(migrated_postgres_url)
    control_plane = WorkflowControlPlane(
        database=database,
        registry=default_workflow_registry(),
        authority=StaticWorkflowAuthority(grants=set()),
    )
    verification = StepUpVerification(
        database=database,
        code_secret=b"protected-write-verification-secret",
    )
    toolbox = WorkflowInteractionToolbox(
        retrieval=WorkflowRetrieval(
            database=database,
            cursor_secret=b"protected-write-test",
        ),
        control_plane=control_plane,
        verification=verification,
    )
    context = InteractionToolContext(
        actor_party_id=BROKER_ID,
        organization_party_id=ACME_ID,
        cause_id="propose-renewal-message",
        interaction_id="sms-broker-demo",
    )
    await toolbox.record_interaction_cause(context, "Prepare John's renewal email.")
    await toolbox.invoke(
        "search_workflows",
        {
            "query": "John Smith renewal",
            "workflow_kind": "renewal_outreach.v1",
            "status": "active",
            "organization": "Acme Brokerage",
            "renewal_period": "2026",
        },
        context,
    )
    read = await toolbox.invoke(
        "read_workflow_packet",
        {"workflow_id": str(TARGET_ID)},
        context,
    )
    assert read.success is False
    assert read.payload["purpose"] == "sensitive_read"

    code = await _deliver_verification_code(database, control_plane, verification)
    verified = await verification.submit_code(
        SubmitVerificationCodeCommand(
            actor_party_id=BROKER_ID,
            interaction_id="sms-broker-demo",
            cause_id="read-verification-message",
            code=code,
        )
    )
    assert verified.status == "verified"

    context.cause_id = "propose-after-read-verification"
    await toolbox.record_interaction_cause(context, "Prepare the email now.")
    loaded = await toolbox.invoke(
        "read_workflow_packet",
        {"workflow_id": str(TARGET_ID)},
        context,
    )
    assert loaded.success is True
    proposed = await toolbox.invoke(
        "propose_renewal_email",
        {"workflow_id": str(TARGET_ID)},
        context,
    )

    assert proposed.success is True
    await database.dispose()


async def test_verified_resume_uses_session_for_follow_on_proposal_with_actual_tools(
    migrated_postgres_url: str,
    clean_workflow_database,
):
    await seed_retrieval_landscape(migrated_postgres_url)
    database = WorkflowDatabase(migrated_postgres_url)
    retrieval = WorkflowRetrieval(database=database, cursor_secret=b"verified-resume-test")
    control_plane = WorkflowControlPlane(
        database=database,
        registry=default_workflow_registry(),
        authority=StaticWorkflowAuthority(grants=set()),
    )
    verification = StepUpVerification(
        database=database,
        code_secret=b"verified-resume-secret",
    )
    toolbox = WorkflowInteractionToolbox(
        retrieval=retrieval,
        control_plane=control_plane,
        verification=verification,
    )
    initial = InteractionToolContext(
        actor_party_id=BROKER_ID,
        organization_party_id=ACME_ID,
        cause_id="draft-request",
        interaction_id="sms-broker-demo",
    )
    await toolbox.record_interaction_cause(
        initial,
        "Prepare John Smith's renewal email and show it to me before sending.",
    )
    search = await toolbox.invoke(
        "search_workflows",
        {
            "query": "John Smith renewal",
            "workflow_kind": "renewal_outreach.v1",
            "status": "active",
            "organization": "Acme Brokerage",
            "renewal_period": "2026",
        },
        initial,
    )
    assert "renewal_details" not in json.dumps(search.payload)
    required = await toolbox.invoke(
        "read_workflow_packet",
        {"workflow_id": str(TARGET_ID)},
        initial,
    )
    assert required.payload["code"] == "verification_required"
    code = await _deliver_verification_code(database, control_plane, verification)
    verified = await verification.submit_code(
        SubmitVerificationCodeCommand(
            actor_party_id=BROKER_ID,
            interaction_id="sms-broker-demo",
            cause_id="verification-code-message",
            code=code,
        )
    )
    assert verified.challenge_id is not None
    assert verified.workflow_id is not None
    assert verified.request_cause_id == "draft-request"
    assert verified.operation is not None

    class Conversation:
        def __init__(self) -> None:
            self.replies: list[str] = []

        def load_transcript(self) -> str:
            return ""

        def user_message_for_cause(self, cause_id: str) -> str | None:
            assert cause_id == "draft-request"
            return "Prepare John Smith's renewal email and show it to me before sending."

        def record_user_message(self, message: str, *, cause_id: str | None = None) -> None:
            del message, cause_id
            return None

        def record_agent_message(self, message: str) -> None:
            del message
            return None

        def record_reply(self, message: str, *, cause_id: str | None = None) -> None:
            self.replies.append(message)

        def record_reply_once(
            self,
            delivery_id: str,
            message: str,
            *,
            cause_id: str | None = None,
        ) -> bool:
            del delivery_id, cause_id
            if message in self.replies:
                return False
            self.replies.append(message)
            return True

        def record_wait(self, _reason: str) -> None:
            return None

    class WorkingMemory:
        def render_transcript(self) -> str:
            return ""

    contexts: list[InteractionToolContext] = []

    def context_factory(cause_id: str) -> InteractionToolContext:
        context = InteractionToolContext(
            actor_party_id=BROKER_ID,
            organization_party_id=ACME_ID,
            cause_id=cause_id,
            interaction_id="sms-broker-demo",
        )
        contexts.append(context)
        return context

    completion_calls = 0

    async def completion(**kwargs):
        nonlocal completion_calls
        completion_calls += 1
        assert str(TARGET_ID) in json.dumps(kwargs["messages"])
        assert "$1,284" in json.dumps(kwargs["messages"])
        assert "Prepare John Smith's renewal email" in json.dumps(kwargs["messages"])
        assert {schema["function"]["name"] for schema in kwargs["tools"]} == {
            "propose_renewal_email",
            "send_message_to_user",
            "wait",
        }
        if completion_calls == 1:
            return _tool_call(
                "propose_renewal_email",
                {"workflow_id": str(TARGET_ID)},
            )
        return {
            "choices": [
                {
                    "message": {
                        "content": (
                            "I'm preparing it now. I'll show you the exact email before anything "
                            "is sent."
                        )
                    }
                }
            ]
        }

    conversation = Conversation()
    runtime = InteractionAgentRuntime(
        toolbox=toolbox,
        tool_context_factory=context_factory,
        system_prompt_builder=build_workflow_system_prompt,
        message_builder=prepare_workflow_message,
        completion=completion,
        conversation_state=conversation,
        working_memory_state=WorkingMemory(),
        settings=Settings(openrouter_api_key="test-key", conversation_summary_threshold=0),
    )

    result = await runtime.execute_verified_resume(
        notification_id=uuid4(),
        operation_cause_id=verified.request_cause_id,
        challenge_id=verified.challenge_id,
        workflow_id=verified.workflow_id,
        operation=verified.operation,
    )

    assert result.success is True
    assert result.response.startswith("I'm preparing it now")
    assert contexts[0].cause_id == "draft-request"
    assert contexts[0].trusted_workflow_id == TARGET_ID
    assert contexts[0].loaded_packet is not None
    assert contexts[0].loaded_packet.workflow.workflow_id == TARGET_ID
    assert contexts[0].verification_challenge_id is None
    updated = await retrieval.read_workflow_packet(
        WorkflowInspectionContext(actor_party_id=BROKER_ID),
        TARGET_ID,
    )
    assert len(updated.jobs) == 2
    await database.dispose()


async def test_verified_resume_can_continue_a_draft_request_without_search_or_approval_tools():
    tool_names = ("read_workflow_packet", "propose_renewal_email", "send_message_to_user", "wait")
    invoked: list[str] = []

    class Toolbox:
        @property
        def schemas(self):
            return tuple(
                {
                    "type": "function",
                    "function": {
                        "name": name,
                        "description": name,
                        "parameters": {"type": "object", "properties": {}},
                    },
                }
                for name in tool_names
            )

        async def invoke(self, name, arguments, context):
            del arguments, context
            invoked.append(name)
            return ToolResult(success=True, payload={"workflow_id": str(TARGET_ID)})

    class Conversation:
        def __init__(self):
            self.replies: list[str] = []

        def user_message_for_cause(self, cause_id):
            assert cause_id == "draft-request"
            return "Prepare John Smith's renewal email and show it to me before sending."

        def record_reply_once(self, delivery_id, message, *, cause_id=None):
            del delivery_id
            assert cause_id == "draft-request"
            self.replies.append(message)
            return True

        def load_transcript(self):
            return ""

        def record_user_message(self, *_args, **_kwargs):
            return None

        def record_agent_message(self, *_args, **_kwargs):
            return None

        def record_reply(self, *_args, **_kwargs):
            return None

    calls = 0

    async def completion(**kwargs):
        nonlocal calls
        calls += 1
        names = {schema["function"]["name"] for schema in kwargs["tools"]}
        assert names == {"propose_renewal_email", "send_message_to_user", "wait"}
        assert "Prepare John Smith's renewal email" in json.dumps(kwargs["messages"])
        if calls == 1:
            return {
                "choices": [
                    {
                        "message": {
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": "proposal",
                                    "type": "function",
                                    "function": {
                                        "name": "propose_renewal_email",
                                        "arguments": json.dumps({"workflow_id": str(TARGET_ID)}),
                                    },
                                }
                            ],
                        }
                    }
                ]
            }
        return {
            "choices": [
                {
                    "message": {
                        "content": "I'm preparing it now. I'll show you the exact email before anything is sent."
                    }
                }
            ]
        }

    conversation = Conversation()
    runtime = InteractionAgentRuntime(
        toolbox=Toolbox(),
        tool_context_factory=lambda cause_id: InteractionToolContext(
            actor_party_id=BROKER_ID,
            organization_party_id=ACME_ID,
            cause_id=cause_id,
        ),
        completion=completion,
        conversation_state=conversation,
        settings=Settings(openrouter_api_key="test-key"),
    )

    result = await runtime.execute_verified_resume(
        notification_id=uuid4(),
        operation_cause_id="draft-request",
        challenge_id=uuid4(),
        workflow_id=TARGET_ID,
        operation=ProtectedOperation(
            name="read_workflow_packet",
            arguments={"workflow_id": str(TARGET_ID)},
        ),
    )

    assert result.success is True
    assert invoked == ["read_workflow_packet", "propose_renewal_email"]
    assert result.response.startswith("I'm preparing it now")


async def test_verified_resume_explains_revalidation_failure_to_the_user():
    class RejectingToolbox:
        @property
        def schemas(self):
            return ()

        async def invoke(self, name, arguments, context):
            del name, arguments, context
            return ToolResult(success=False, payload={"code": "stale_approval_target"})

    class Conversation:
        def __init__(self) -> None:
            self.replies: list[str] = []

        def load_transcript(self) -> str:
            return ""

        def record_user_message(self, message: str, *, cause_id: str | None = None) -> None:
            del message, cause_id
            return None

        def record_agent_message(self, message: str) -> None:
            del message
            return None

        def record_reply(self, message: str, *, cause_id: str | None = None) -> None:
            self.replies.append(message)

        def record_reply_once(
            self,
            delivery_id: str,
            message: str,
            *,
            cause_id: str | None = None,
        ) -> bool:
            del delivery_id, cause_id
            if message in self.replies:
                return False
            self.replies.append(message)
            return True

    class WorkingMemory:
        def render_transcript(self) -> str:
            return ""

    async def completion(**_kwargs):
        raise AssertionError("Rejected continuation must not invoke the model")

    conversation = Conversation()
    runtime = InteractionAgentRuntime(
        toolbox=RejectingToolbox(),
        tool_context_factory=lambda cause_id: InteractionToolContext(
            actor_party_id=JOHN_ACME_ID,
            organization_party_id=ACME_ID,
            cause_id=cause_id,
            interaction_id="sms-policyholder-demo",
        ),
        system_prompt_builder=lambda: "unused",
        message_builder=lambda *_args, **_kwargs: [],
        completion=completion,
        conversation_state=conversation,
        working_memory_state=WorkingMemory(),
        settings=Settings(openrouter_api_key="test-key"),
    )

    result = await runtime.execute_verified_resume(
        notification_id=uuid4(),
        operation_cause_id="original-private-request",
        challenge_id=UUID("70000000-0000-0000-0000-000000000001"),
        workflow_id=TARGET_ID,
        operation=ProtectedOperation(
            name="approve_job",
            arguments={
                "job_id": "60000000-0000-0000-0000-000000000001",
                "expected_draft_revision_id": "60000000-0000-0000-0000-000000000002",
            },
        ),
    )

    assert result.success is False
    assert result.error == "stale_approval_target"
    assert "Please start the request again" in result.response
    assert "Workflow" not in result.response
    assert conversation.replies == [result.response]


async def test_proposal_requires_packet_in_same_interaction_turn(
    workflow_toolbox: WorkflowInteractionToolbox,
):
    context = InteractionToolContext(
        actor_party_id=BROKER_ID,
        organization_party_id=ACME_ID,
        cause_id="message-without-packet",
    )

    result = await workflow_toolbox.invoke(
        "propose_renewal_email",
        {"workflow_id": str(TARGET_ID)},
        context,
    )

    assert result.success is False
    assert result.payload == {"code": "workflow_packet_required"}


async def test_one_interaction_turn_cannot_load_two_workflow_packets(
    workflow_toolbox: WorkflowInteractionToolbox,
):
    context = InteractionToolContext(
        actor_party_id=BROKER_ID,
        organization_party_id=ACME_ID,
        cause_id="message-one-packet",
    )
    await workflow_toolbox.invoke(
        "search_workflows",
        {
            "query": "John Smith renewal",
            "workflow_kind": "renewal_outreach.v1",
            "status": "active",
            "organization": "Acme Brokerage",
            "renewal_period": "2026",
        },
        context,
    )
    first = await workflow_toolbox.invoke(
        "read_workflow_packet",
        {"workflow_id": str(TARGET_ID)},
        context,
    )
    await workflow_toolbox.invoke(
        "search_workflows",
        {
            "query": "John Smith renewal",
            "workflow_kind": "renewal_outreach.v1",
            "status": "active",
            "organization": "Northwind Brokerage",
            "renewal_period": "2026",
        },
        context,
    )
    second = await workflow_toolbox.invoke(
        "read_workflow_packet",
        {"workflow_id": str(SAME_NAME_ID)},
        context,
    )

    assert first.success is True
    assert second.success is False
    assert second.payload == {"code": "workflow_packet_already_selected"}


async def test_selected_authorized_workflow_derives_its_organization_context(
    workflow_toolbox: WorkflowInteractionToolbox,
):
    context = InteractionToolContext(
        actor_party_id=BROKER_ID,
        organization_party_id=ACME_ID,
        cause_id="message-cross-organization",
    )
    await workflow_toolbox.record_interaction_cause(context, "Prepare the Northwind renewal.")
    search = await workflow_toolbox.invoke(
        "search_workflows",
        {
            "query": "John Smith renewal",
            "workflow_kind": "renewal_outreach.v1",
            "status": "active",
            "organization": "Northwind Brokerage",
            "renewal_period": "2026",
        },
        context,
    )
    packet = await workflow_toolbox.invoke(
        "read_workflow_packet",
        {"workflow_id": str(SAME_NAME_ID)},
        context,
    )
    proposal = await workflow_toolbox.invoke(
        "propose_renewal_email",
        {"workflow_id": str(SAME_NAME_ID)},
        context,
    )

    assert search.payload["total_matches"] == 1
    assert packet.success is True
    assert proposal.success is True


async def test_unexpected_tool_errors_are_redacted_before_model_delivery():
    from server.agents.interaction_agent import runtime as runtime_module

    secret = "postgresql://user:secret@db/private?token=abc"

    class CrashingToolbox:
        @property
        def schemas(self):
            return ()

        async def invoke(self, name, arguments, context):
            raise RuntimeError(secret)

    runtime = object.__new__(InteractionAgentRuntime)
    runtime.toolbox = CrashingToolbox()
    result = await runtime._execute_tool(
        runtime_module._ToolCall(identifier="call", name="crash", arguments={}),
        InteractionToolContext(
            actor_party_id=BROKER_ID,
            organization_party_id=ACME_ID,
            cause_id="message-error-redaction",
        ),
    )

    assert result.payload == {"code": "internal_error"}
    assert secret not in json.dumps(result.payload)


@pytest.mark.parametrize("fail_on_start", [True, False])
async def test_activity_receipt_failures_do_not_change_tool_results(fail_on_start: bool):
    from server.agents.interaction_agent import runtime as runtime_module

    class SuccessfulToolbox:
        async def invoke(self, name, arguments, context):
            return ToolResult(success=True, payload={"ok": True})

    class FailingActivityStore:
        async def start(self, **kwargs):
            if fail_on_start:
                raise RuntimeError("receipt start failed")
            return SimpleNamespace(id=uuid4())

        async def finish(self, *args, **kwargs):
            raise RuntimeError("receipt finish failed")

    runtime = object.__new__(InteractionAgentRuntime)
    runtime.toolbox = SuccessfulToolbox()
    runtime._activity_store = FailingActivityStore()

    result = await runtime._execute_tool(
        runtime_module._ToolCall(
            identifier="call",
            name="search_workflows",
            arguments={},
        ),
        InteractionToolContext(
            actor_party_id=BROKER_ID,
            organization_party_id=ACME_ID,
            cause_id="message-receipt-failure",
        ),
    )

    assert result == ToolResult(success=True, payload={"ok": True})


async def test_ambiguous_search_cannot_read_or_propose_the_first_candidate(
    workflow_toolbox: WorkflowInteractionToolbox,
    migrated_postgres_url: str,
):
    context = InteractionToolContext(
        actor_party_id=BROKER_ID,
        organization_party_id=ACME_ID,
        cause_id="message-ambiguous-selection",
    )
    await workflow_toolbox.record_interaction_cause(context, "Prepare John's renewal.")
    search = await workflow_toolbox.invoke(
        "search_workflows",
        {"query": "John renewal"},
        context,
    )
    assert search.success is True
    assert search.payload["total_matches"] > 1

    packet = await workflow_toolbox.invoke(
        "read_workflow_packet",
        {"workflow_id": str(TARGET_ID)},
        context,
    )
    proposal = await workflow_toolbox.invoke(
        "propose_renewal_email",
        {"workflow_id": str(TARGET_ID)},
        context,
    )

    assert packet.success is False
    assert packet.payload == {"code": "workflow_resolution_required"}
    assert proposal.success is False
    assert proposal.payload == {"code": "workflow_packet_required"}
    engine = create_async_engine(migrated_postgres_url)
    async with engine.connect() as connection:
        job_count = await connection.scalar(sa.text("SELECT count(*) FROM workflow_jobs"))
    await engine.dispose()
    assert job_count == 0

    refined = await workflow_toolbox.invoke(
        "search_workflows",
        {
            "query": "John Smith renewal",
            "workflow_kind": "renewal_outreach.v1",
            "status": "active",
            "organization": "Acme Brokerage",
            "renewal_period": "2026",
        },
        context,
    )
    selected_packet = await workflow_toolbox.invoke(
        "read_workflow_packet",
        {"workflow_id": str(TARGET_ID)},
        context,
    )
    accepted = await workflow_toolbox.invoke(
        "propose_renewal_email",
        {"workflow_id": str(TARGET_ID)},
        context,
    )

    assert refined.payload["total_matches"] == 1
    assert selected_packet.success is True
    assert accepted.success is True
    engine = create_async_engine(migrated_postgres_url)
    async with engine.connect() as connection:
        accepted_job_count = await connection.scalar(sa.text("SELECT count(*) FROM workflow_jobs"))
    await engine.dispose()
    assert accepted_job_count == 2


async def test_scripted_workflow_runtime_loads_one_packet_and_never_delegates(
    workflow_toolbox: WorkflowInteractionToolbox,
    migrated_postgres_url: str,
    monkeypatch: pytest.MonkeyPatch,
):
    from server.agents.interaction_agent import runtime as runtime_module

    class FakeConversationLog:
        def __init__(self) -> None:
            self.replies: list[str] = []

        def load_transcript(self) -> str:
            return ""

        def record_user_message(self, message: str, *, cause_id: str | None = None) -> None:
            self.user_message = message

        def record_agent_message(self, message: str) -> None:
            self.agent_message = message

        def record_reply(self, message: str, *, cause_id: str | None = None) -> None:
            self.replies.append(message)

    class FakeWorkingMemory:
        def render_transcript(self) -> str:
            return ""

    responses = [
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
        (
            "propose_renewal_email",
            {
                "workflow_id": str(TARGET_ID),
            },
        ),
        None,
    ]
    calls: list[dict] = []

    async def scripted_completion(**kwargs):
        calls.append(kwargs)
        scripted = responses[len(calls) - 1]
        if scripted is None:
            message = {"content": "The renewal work is queued.", "tool_calls": []}
        else:
            name, arguments = scripted
            message = {
                "content": "",
                "tool_calls": [
                    {
                        "id": f"call-{len(calls)}",
                        "function": {"name": name, "arguments": json.dumps(arguments)},
                    }
                ],
            }
        return {"choices": [{"message": message}]}

    log = FakeConversationLog()
    monkeypatch.setattr(
        runtime_module,
        "get_settings",
        lambda: SimpleNamespace(
            openrouter_api_key="test-key",
            interaction_agent_model="scripted-model",
            summarization_enabled=False,
        ),
    )
    monkeypatch.setattr(runtime_module, "get_conversation_log", lambda: log)
    monkeypatch.setattr(runtime_module, "get_working_memory_log", FakeWorkingMemory)
    monkeypatch.setattr(runtime_module, "request_chat_completion", scripted_completion)
    contexts: list[InteractionToolContext] = []

    def context_factory(cause_id: str) -> InteractionToolContext:
        context = InteractionToolContext(
            actor_party_id=BROKER_ID,
            organization_party_id=ACME_ID,
            cause_id=cause_id,
        )
        contexts.append(context)
        return context

    activity_database = WorkflowDatabase(migrated_postgres_url)
    activity_store = InteractionActivityStore(activity_database)
    runtime = InteractionAgentRuntime(
        toolbox=workflow_toolbox,
        tool_context_factory=context_factory,
        system_prompt_builder=build_workflow_system_prompt,
        message_builder=prepare_workflow_message,
        interaction_cause_recorder=workflow_toolbox.record_interaction_cause,
        activity_store=activity_store,
    )

    result = await runtime.execute(
        "Prepare John Smith's 2026 renewal email at Acme.",
        cause_id="authenticated-message-1",
    )

    assert result.success is True
    assert result.execution_agents_used == 0
    assert result.response == "The renewal work is queued."
    assert len(contexts) == 1
    assert contexts[0].cause_id == "authenticated-message-1"
    assert contexts[0].loaded_packet is not None
    assert contexts[0].loaded_packet.workflow.workflow_id == TARGET_ID
    assert len(calls) == 4
    assert all("send_message_to_agent" not in json.dumps(call["tools"]) for call in calls)
    assert "<active_agents>" not in calls[0]["messages"][0]["content"]
    receipts = await activity_store.list_for_actor_causes(
        actor_party_id=BROKER_ID,
        cause_ids=["authenticated-message-1"],
    )
    assert [(receipt.action, receipt.status, receipt.workflow_id) for receipt in receipts] == [
        (
            InteractionActivityAction.SEARCH_WORKFLOWS,
            InteractionActivityStatus.SUCCEEDED,
            None,
        ),
        (
            InteractionActivityAction.READ_WORKFLOW_PACKET,
            InteractionActivityStatus.SUCCEEDED,
            TARGET_ID,
        ),
        (
            InteractionActivityAction.PROPOSE_RENEWAL_EMAIL,
            InteractionActivityStatus.SUCCEEDED,
            TARGET_ID,
        ),
    ]
    await activity_database.dispose()


@pytest.mark.parametrize(
    ("query", "response"),
    [
        (
            "John renewal",
            "I found more than one John renewal. Which organization do you mean?",
        ),
        (
            "Zelda Zephyr renewal",
            "I could not find an authorized renewal Workflow for Zelda Zephyr.",
        ),
    ],
)
async def test_ambiguous_or_missing_search_asks_without_loading_or_mutating(
    workflow_toolbox: WorkflowInteractionToolbox,
    migrated_postgres_url: str,
    monkeypatch: pytest.MonkeyPatch,
    query: str,
    response: str,
):
    from server.agents.interaction_agent import runtime as runtime_module

    class FakeConversationLog:
        def load_transcript(self) -> str:
            return ""

        def record_user_message(self, message: str, *, cause_id: str | None = None) -> None:
            pass

        def record_reply(self, message: str, *, cause_id: str | None = None) -> None:
            pass

    class FakeWorkingMemory:
        def render_transcript(self) -> str:
            return ""

    calls = 0

    async def scripted_completion(**kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            message = {
                "content": "",
                "tool_calls": [
                    {
                        "id": "search-call",
                        "function": {
                            "name": "search_workflows",
                            "arguments": json.dumps({"query": query}),
                        },
                    }
                ],
            }
        else:
            message = {"content": response, "tool_calls": []}
        return {"choices": [{"message": message}]}

    log = FakeConversationLog()
    monkeypatch.setattr(
        runtime_module,
        "get_settings",
        lambda: SimpleNamespace(
            openrouter_api_key="test-key",
            interaction_agent_model="scripted-model",
            summarization_enabled=False,
        ),
    )
    monkeypatch.setattr(runtime_module, "get_conversation_log", lambda: log)
    monkeypatch.setattr(runtime_module, "get_working_memory_log", FakeWorkingMemory)
    monkeypatch.setattr(runtime_module, "request_chat_completion", scripted_completion)
    contexts: list[InteractionToolContext] = []

    def context_factory(cause_id: str) -> InteractionToolContext:
        context = InteractionToolContext(
            actor_party_id=BROKER_ID,
            organization_party_id=ACME_ID,
            cause_id=cause_id,
        )
        contexts.append(context)
        return context

    runtime = InteractionAgentRuntime(
        toolbox=workflow_toolbox,
        tool_context_factory=context_factory,
        system_prompt_builder=build_workflow_system_prompt,
        message_builder=prepare_workflow_message,
    )

    result = await runtime.execute(f"Prepare {query}.")

    assert result.success is True
    assert result.response == response
    assert len(contexts) == 1
    assert contexts[0].loaded_packet is None
    engine = create_async_engine(migrated_postgres_url)
    async with engine.connect() as connection:
        job_count = await connection.scalar(sa.text("SELECT count(*) FROM workflow_jobs"))
    await engine.dispose()
    assert job_count == 0
