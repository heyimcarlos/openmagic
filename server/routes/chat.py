from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import JSONResponse, Response

from ..config import get_settings
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
) -> Response:
    return await handle_chat_request(payload)


@router.get("/history", response_model=ChatHistoryResponse)
# Retrieve the conversation history from the log
def chat_history(
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
    return ChatHistoryResponse(messages=log.to_chat_messages())


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
