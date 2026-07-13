from functools import lru_cache

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import JSONResponse, Response

from ..config import get_settings
from ..logging_config import logger
from ..models import (
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
    WorkflowRetrieval,
    default_workflow_registry,
    find_sms_party,
    sms_interaction_id,
)

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
