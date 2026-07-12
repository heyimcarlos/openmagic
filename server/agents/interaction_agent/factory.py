"""Application composition for workflow and controlled legacy interaction modes."""

from __future__ import annotations

from functools import lru_cache
from uuid import UUID

from server.config import Settings, get_settings
from server.workflows import (
    StaticWorkflowAuthority,
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


def create_interaction_runtime(settings: Settings | None = None) -> InteractionAgentRuntime:
    settings = settings or get_settings()
    if settings.interaction_mode == "legacy":
        return InteractionAgentRuntime(toolbox=LegacyInteractionToolbox(), settings=settings)
    database_url = _required(settings.database_url, "OPENMAGIC_DATABASE_URL")
    cursor_secret = _required(
        settings.workflow_cursor_secret,
        "OPENMAGIC_WORKFLOW_CURSOR_SECRET",
    )
    broker_party_id = UUID(
        _required(settings.workflow_broker_party_id, "OPENMAGIC_WORKFLOW_BROKER_PARTY_ID")
    )
    organization_party_id = UUID(
        _required(
            settings.workflow_organization_party_id,
            "OPENMAGIC_WORKFLOW_ORGANIZATION_PARTY_ID",
        )
    )
    toolbox = _workflow_toolbox(database_url, cursor_secret)

    def context_factory(cause_id: str) -> InteractionToolContext:
        return InteractionToolContext(
            actor_party_id=broker_party_id,
            organization_party_id=organization_party_id,
            cause_id=cause_id,
        )

    return InteractionAgentRuntime(
        toolbox=toolbox,
        tool_context_factory=context_factory,
        system_prompt_builder=build_workflow_system_prompt,
        message_builder=prepare_workflow_message,
        settings=settings,
    )


@lru_cache(maxsize=4)
def _workflow_toolbox(database_url: str, cursor_secret: str) -> WorkflowInteractionToolbox:
    database = WorkflowDatabase(database_url)
    retrieval = WorkflowRetrieval(database=database, cursor_secret=cursor_secret.encode())
    control_plane = WorkflowControlPlane(
        database=database,
        registry=default_workflow_registry(),
        authority=StaticWorkflowAuthority(grants=set()),
    )
    return WorkflowInteractionToolbox(retrieval=retrieval, control_plane=control_plane)


def _required(value: str | None, variable: str) -> str:
    if not value:
        raise ValueError(f"{variable} is required for workflow interaction mode")
    return value
