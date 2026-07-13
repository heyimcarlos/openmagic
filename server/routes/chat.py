import secrets
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Query, status
from fastapi.responses import JSONResponse, Response

from ..config import Settings, get_settings
from ..models import ChatHistoryClearResponse, ChatHistoryResponse, ChatRequest
from ..services import get_conversation_log, get_trigger_service, handle_chat_request
from ..services.conversation import get_conversation_session
from ..workflows import sms_interaction_id

router = APIRouter(prefix="/chat", tags=["chat"])


@router.post(
    "/send", response_class=JSONResponse, summary="Submit a chat message and receive a completion"
)
# Handle incoming chat messages and route them to the interaction agent
async def chat_send(
    payload: ChatRequest,
    authorization: Annotated[str | None, Header()] = None,
) -> Response:
    _require_workflow_interaction(get_settings(), authorization)
    return await handle_chat_request(payload)


def _require_workflow_interaction(
    settings: Settings,
    authorization: str | None,
) -> None:
    if settings.interaction_mode != "workflow":
        return
    expected = settings.workflow_interaction_token
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Workflow interaction authentication is not configured",
        )
    scheme, _, supplied = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not secrets.compare_digest(supplied, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Workflow interaction authentication failed",
        )


@router.get("/history", response_model=ChatHistoryResponse)
# Retrieve the conversation history from the log
def chat_history(
    sender_phone: str | None = Query(default=None, min_length=8, max_length=32),
    authorization: Annotated[str | None, Header()] = None,
) -> ChatHistoryResponse:
    settings = get_settings()
    _require_workflow_interaction(settings, authorization)
    if settings.interaction_mode == "workflow" and sender_phone is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="SMS sender phone is required",
        )
    log = get_conversation_session(sms_interaction_id(sender_phone)).log if sender_phone else get_conversation_log()
    return ChatHistoryResponse(messages=log.to_chat_messages())


@router.delete("/history", response_model=ChatHistoryClearResponse)
def clear_history(
    sender_phone: str | None = Query(default=None, min_length=8, max_length=32),
    authorization: Annotated[str | None, Header()] = None,
) -> ChatHistoryClearResponse:
    from ..services import get_agent_roster, get_execution_agent_logs

    settings = get_settings()
    _require_workflow_interaction(settings, authorization)
    if settings.interaction_mode == "workflow" and sender_phone is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="SMS sender phone is required",
        )
    log = get_conversation_session(sms_interaction_id(sender_phone)).log if sender_phone else get_conversation_log()
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
