"""Application composition for workflow and controlled legacy interaction modes."""

from __future__ import annotations

from functools import lru_cache
from typing import Any
from uuid import UUID

from server.config import Settings, get_settings
from server.workflows import (
    StaticWorkflowAuthority,
    StepUpVerification,
    WorkflowControlPlane,
    WorkflowDatabase,
    WorkflowRetrieval,
    default_workflow_registry,
)

from .runtime import InteractionAgentRuntime
from .toolbox import InteractionToolContext
from .tools import LegacyInteractionToolbox
from .workflow_agent import build_workflow_system_prompt, prepare_workflow_message
from .workflow_tools import WorkflowInteractionToolbox


def create_interaction_runtime(
    settings: Settings | None = None,
    *,
    actor_party_id: UUID | None = None,
    interaction_id: str | None = None,
    conversation_state: Any | None = None,
    working_memory_state: Any | None = None,
) -> InteractionAgentRuntime:
    settings = settings or get_settings()
    if settings.interaction_mode == "legacy":
        return InteractionAgentRuntime(toolbox=LegacyInteractionToolbox(), settings=settings)
    database_url = _required(settings.database_url, "OPENMAGIC_DATABASE_URL")
    cursor_secret = _required(
        settings.workflow_cursor_secret,
        "OPENMAGIC_WORKFLOW_CURSOR_SECRET",
    )
    resolved_actor_party_id = actor_party_id or UUID(
        _required(settings.workflow_broker_party_id, "OPENMAGIC_WORKFLOW_BROKER_PARTY_ID")
    )
    organization_party_id = UUID(
        _required(
            settings.workflow_organization_party_id,
            "OPENMAGIC_WORKFLOW_ORGANIZATION_PARTY_ID",
        )
    )
    verification_secret = _required(
        settings.verification_code_secret,
        "OPENMAGIC_VERIFICATION_CODE_SECRET",
    )
    delivery_available = bool(settings.composio_api_key and settings.workflow_composio_user_id)
    toolbox = _workflow_toolbox(
        database_url,
        cursor_secret,
        verification_secret,
        delivery_available,
    )

    def context_factory(cause_id: str) -> InteractionToolContext:
        return InteractionToolContext(
            actor_party_id=resolved_actor_party_id,
            organization_party_id=organization_party_id,
            cause_id=cause_id,
            interaction_id=interaction_id or "trusted-browser-default",
            conversation=conversation_state,
        )

    return InteractionAgentRuntime(
        toolbox=toolbox,
        tool_context_factory=context_factory,
        system_prompt_builder=build_workflow_system_prompt,
        message_builder=prepare_workflow_message,
        interaction_cause_recorder=toolbox.record_interaction_cause,
        conversation_state=conversation_state,
        working_memory_state=working_memory_state,
        settings=settings,
    )


@lru_cache(maxsize=8)
def _workflow_toolbox(
    database_url: str,
    cursor_secret: str,
    verification_secret: str,
    delivery_available: bool,
) -> WorkflowInteractionToolbox:
    database = WorkflowDatabase(database_url)
    retrieval = WorkflowRetrieval(database=database, cursor_secret=cursor_secret.encode())
    control_plane = WorkflowControlPlane(
        database=database,
        registry=default_workflow_registry(),
        authority=StaticWorkflowAuthority(grants=set()),
    )
    return WorkflowInteractionToolbox(
        retrieval=retrieval,
        control_plane=control_plane,
        verification=_step_up_verification(
            database_url,
            verification_secret,
            delivery_available,
        ),
    )


@lru_cache(maxsize=4)
def _step_up_verification(
    database_url: str,
    verification_secret: str,
    delivery_available: bool,
) -> StepUpVerification:
    return StepUpVerification(
        database=WorkflowDatabase(database_url),
        code_secret=verification_secret.encode(),
        delivery_available=delivery_available,
    )


def get_step_up_verification(settings: Settings | None = None) -> StepUpVerification:
    settings = settings or get_settings()
    database_url = _required(settings.database_url, "OPENMAGIC_DATABASE_URL")
    verification_secret = _required(
        settings.verification_code_secret,
        "OPENMAGIC_VERIFICATION_CODE_SECRET",
    )
    return _step_up_verification(
        database_url,
        verification_secret,
        bool(settings.composio_api_key and settings.workflow_composio_user_id),
    )


def _required(value: str | None, variable: str) -> str:
    if not value:
        raise ValueError(f"{variable} is required for workflow interaction mode")
    return value
