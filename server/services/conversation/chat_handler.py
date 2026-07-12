import asyncio

from fastapi import status
from fastapi.responses import JSONResponse, PlainTextResponse

from ...agents.interaction_agent.factory import create_interaction_runtime
from ...logging_config import logger
from ...models import ChatMessage, ChatRequest
from ...utils import error_response

_BACKGROUND_TASKS: set[asyncio.Task[None]] = set()


# Extract the most recent user message from the chat request payload
def _extract_latest_user_message(payload: ChatRequest) -> ChatMessage | None:
    for message in reversed(payload.messages):
        if message.role.lower().strip() == "user" and message.content.strip():
            return message
    return None


# Process incoming chat requests by routing them to the interaction agent runtime
async def handle_chat_request(payload: ChatRequest) -> PlainTextResponse | JSONResponse:
    """Handle a chat request using the InteractionAgentRuntime."""

    # Extract user message
    user_message = _extract_latest_user_message(payload)
    if user_message is None:
        return error_response("Missing user message", status_code=status.HTTP_400_BAD_REQUEST)

    user_content = user_message.content.strip()  # Already checked in _extract_latest_user_message

    logger.info("chat request", extra={"message_length": len(user_content)})

    try:
        runtime = create_interaction_runtime()
    except ValueError as ve:
        # Missing API key error
        logger.error("configuration error", extra={"error": str(ve)})
        return error_response(str(ve), status_code=status.HTTP_400_BAD_REQUEST)

    async def _run_interaction() -> None:
        try:
            await runtime.execute(user_message=user_content)
        except Exception as exc:  # pragma: no cover - defensive
            logger.error("chat task failed", extra={"error": str(exc)})

    task = asyncio.create_task(_run_interaction())
    _BACKGROUND_TASKS.add(task)
    task.add_done_callback(_BACKGROUND_TASKS.discard)

    return PlainTextResponse("", status_code=status.HTTP_202_ACCEPTED)
