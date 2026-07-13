from functools import lru_cache
from typing import TYPE_CHECKING
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import JSONResponse, Response

from ..config import Settings, get_settings
from ..logging_config import logger
from ..models import (
    ChatApprovalCommand,
    ChatApprovalResponse,
    ChatHistoryClearResponse,
    ChatHistoryResponse,
    ChatLatestTelemetryResponse,
    ChatRequest,
)
from ..services import get_conversation_log, get_trigger_service, handle_chat_request
from ..services.conversation import (
    WorkflowTelemetryProjector,
    get_conversation_session,
)
from ..workflows import (
    InteractionActivityStore,
    WorkflowDatabase,
    WorkflowInspectionContext,
    WorkflowRetrieval,
    default_workflow_registry,
    find_sms_party,
    sms_interaction_id,
)

if TYPE_CHECKING:
    from ..agents.interaction_agent import WorkflowInteractionToolbox

router = APIRouter(prefix="/chat", tags=["chat"])
_LATEST_TELEMETRY_CAUSE_LIMIT = 20


@router.post(
    "/send", response_class=JSONResponse, summary="Submit a chat message and receive a completion"
)
# Handle incoming chat messages and route them to the interaction agent
async def chat_send(
    payload: ChatRequest,
) -> Response:
    return await handle_chat_request(payload)


@router.get("/history", response_model=ChatHistoryResponse)
# Retrieve the conversation history from the log
async def chat_history(
    sender_phone: str | None = Query(default=None, min_length=8, max_length=32),
) -> ChatHistoryResponse:
    settings = get_settings()
    if settings.interaction_mode == "workflow" and sender_phone is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="SMS sender phone is required",
        )
    log = (
        get_conversation_session(sms_interaction_id(sender_phone)).log
        if sender_phone
        else get_conversation_log()
    )
    correlated = log.to_correlated_chat_messages()
    messages = [entry.message for entry in correlated]
    if settings.interaction_mode != "workflow" or sender_phone is None:
        return ChatHistoryResponse(messages=messages)
    latest_reply_index_by_cause: dict[str, int] = {}
    for index, entry in enumerate(correlated):
        if entry.message.role == "assistant" and entry.cause_id is not None:
            latest_reply_index_by_cause[entry.cause_id] = index
    cause_ids = list(latest_reply_index_by_cause)
    if not cause_ids:
        return ChatHistoryResponse(messages=messages)
    try:
        if not settings.database_url or not settings.workflow_cursor_secret:
            raise ValueError("Workflow telemetry database configuration is incomplete")
        database, projector = _workflow_telemetry_services(
            settings.database_url,
            settings.workflow_cursor_secret,
        )
        party = await find_sms_party(database, sender_phone)
        if party is None:
            return ChatHistoryResponse(messages=messages)
        telemetry = await projector.project(
            actor_party_id=party.party_id,
            cause_ids=cause_ids,
        )
    except Exception as exc:  # pragma: no cover - history must remain available
        logger.warning(
            "Workflow chat telemetry projection failed",
            extra={"error_type": type(exc).__name__},
        )
        return ChatHistoryResponse(messages=messages)

    projected_messages = []
    for index, entry in enumerate(correlated):
        cause_id = entry.cause_id
        should_attach = (
            entry.message.role == "assistant"
            and cause_id is not None
            and cause_id in telemetry
            and latest_reply_index_by_cause.get(cause_id) == index
        )
        if should_attach:
            assert cause_id is not None
            projected_messages.append(
                entry.message.model_copy(update={"telemetry": telemetry[cause_id]})
            )
        else:
            projected_messages.append(entry.message)
    return ChatHistoryResponse(messages=projected_messages)


@router.get("/telemetry/latest", response_model=ChatLatestTelemetryResponse)
async def latest_chat_telemetry(
    sender_phone: str = Query(min_length=8, max_length=32),
) -> ChatLatestTelemetryResponse:
    """Project the latest bounded Workflow activity for the cockpit."""

    settings = get_settings()
    if settings.interaction_mode != "workflow":
        return ChatLatestTelemetryResponse()
    log = get_conversation_session(sms_interaction_id(sender_phone)).log
    cause_ids: list[str] = []
    for entry in reversed(list(log.iter_correlated_entries())):
        if entry.tag != "poke_reply" or entry.cause_id is None:
            continue
        if entry.cause_id not in cause_ids:
            cause_ids.append(entry.cause_id)
        if len(cause_ids) == _LATEST_TELEMETRY_CAUSE_LIMIT:
            break
    if not cause_ids:
        return ChatLatestTelemetryResponse()
    if not settings.database_url or not settings.workflow_cursor_secret:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Workflow telemetry is unavailable",
        )
    try:
        database, projector = _workflow_telemetry_services(
            settings.database_url,
            settings.workflow_cursor_secret,
        )
        party = await find_sms_party(database, sender_phone)
        if party is None:
            return ChatLatestTelemetryResponse()
        telemetry_by_cause = await projector.project(
            actor_party_id=party.party_id,
            cause_ids=cause_ids,
        )
    except Exception as exc:
        logger.warning(
            "Latest Workflow telemetry projection failed",
            extra={"error_type": type(exc).__name__},
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Workflow telemetry is unavailable",
        ) from exc
    telemetry = next(
        (telemetry_by_cause[cause_id] for cause_id in cause_ids if cause_id in telemetry_by_cause),
        None,
    )
    return ChatLatestTelemetryResponse(telemetry=telemetry)


@router.post("/approval", response_model=ChatApprovalResponse)
async def approve_exact_email(payload: ChatApprovalCommand) -> ChatApprovalResponse:
    """Apply one direct approval UI action through the deterministic boundary."""

    from ..agents.interaction_agent import InteractionToolContext

    settings = get_settings()
    if settings.interaction_mode != "workflow":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    required = (
        settings.database_url,
        settings.workflow_cursor_secret,
        settings.workflow_organization_party_id,
        settings.verification_code_secret,
    )
    if not all(required):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Exact approval is unavailable",
        )
    assert settings.database_url is not None
    assert settings.workflow_cursor_secret is not None
    assert settings.workflow_organization_party_id is not None
    database, _projector = _workflow_telemetry_services(
        settings.database_url,
        settings.workflow_cursor_secret,
    )
    party = await find_sms_party(database, payload.sender_phone)
    if party is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This chat is not authorized to approve that email.",
        )
    context = InteractionToolContext(
        actor_party_id=party.party_id,
        organization_party_id=UUID(settings.workflow_organization_party_id),
        cause_id=payload.cause_id,
        cause_type="ui_action",
        interaction_id=sms_interaction_id(payload.sender_phone),
        trusted_workflow_id=payload.workflow_id,
    )
    try:
        context.loaded_packet = await get_workflow_retrieval(settings).read_workflow_packet(
            WorkflowInspectionContext(actor_party_id=party.party_id),
            payload.workflow_id,
        )
        toolbox = get_workflow_interaction_toolbox(settings)
        await toolbox.record_interaction_cause(
            context,
            f"Approve exact email for Job {payload.job_id}",
        )
        result = await toolbox.invoke(
            "approve_job",
            {
                "job_id": str(payload.job_id),
                "expected_draft_revision_id": str(payload.expected_draft_revision_id),
            },
            context,
        )
        code = result.payload.get("code") if isinstance(result.payload, dict) else None
        if not result.success and code in {"verification_required", "verification_in_progress"}:
            return ChatApprovalResponse(
                status="verification_required",
                masked_destination=(
                    result.payload.get("destination")
                    if isinstance(result.payload.get("destination"), str)
                    else None
                ),
            )
        if not result.success:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "This approval is no longer available. Refresh the conversation and review "
                    "the latest email."
                ),
            )
        return ChatApprovalResponse(status="approved", job_id=payload.job_id)
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning(
            "Exact email approval rejected",
            extra={"error_type": type(exc).__name__},
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "This approval is no longer available. Refresh the conversation and review "
                "the latest email."
            ),
        ) from exc


@lru_cache(maxsize=4)
def _workflow_telemetry_services(
    database_url: str,
    cursor_secret: str,
) -> tuple[WorkflowDatabase, WorkflowTelemetryProjector]:
    database = WorkflowDatabase(database_url)
    activity_store = InteractionActivityStore(database)
    return database, WorkflowTelemetryProjector(
        retrieval=WorkflowRetrieval(database=database, cursor_secret=cursor_secret.encode()),
        activity_store=activity_store,
        registry=default_workflow_registry(),
    )


def get_workflow_interaction_toolbox(settings: Settings) -> "WorkflowInteractionToolbox":
    """Load the public interaction boundary lazily to avoid application import cycles."""

    from ..agents.interaction_agent import get_workflow_interaction_toolbox as resolve

    return resolve(settings)


def get_workflow_retrieval(settings: Settings) -> WorkflowRetrieval:
    """Load the public packet reader lazily to avoid application import cycles."""

    from ..agents.interaction_agent import get_workflow_retrieval as resolve

    return resolve(settings)


@router.delete("/history", response_model=ChatHistoryClearResponse)
def clear_history(
    sender_phone: str | None = Query(default=None, min_length=8, max_length=32),
) -> ChatHistoryClearResponse:
    from ..services import get_agent_roster, get_execution_agent_logs

    settings = get_settings()
    if settings.interaction_mode == "workflow" and sender_phone is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="SMS sender phone is required",
        )
    log = (
        get_conversation_session(sms_interaction_id(sender_phone)).log
        if sender_phone
        else get_conversation_log()
    )
    log.clear()

    if sender_phone is not None:
        return ChatHistoryClearResponse()

    # Clear execution agent logs
    execution_logs = get_execution_agent_logs()
    execution_logs.clear_all()

    # Clear agent roster
    roster = get_agent_roster()
    roster.clear()

    # Clear stored triggers
    trigger_service = get_trigger_service()
    trigger_service.clear_all()

    return ChatHistoryClearResponse()


__all__ = ["router"]
