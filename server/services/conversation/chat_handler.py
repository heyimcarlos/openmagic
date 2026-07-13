import asyncio
import re

from fastapi import status
from fastapi.responses import JSONResponse, PlainTextResponse

from ...agents.interaction_agent import create_interaction_runtime, get_step_up_verification
from ...config import get_settings
from ...logging_config import logger
from ...models import ChatMessage, ChatRequest
from ...utils import error_response
from ...workflows import (
    SubmitVerificationCodeCommand,
    WorkflowDatabase,
    resolve_sms_party,
    sms_interaction_id,
)
from .sessions import get_conversation_session

_BACKGROUND_TASKS: set[asyncio.Task[None]] = set()
_VERIFICATION_CODE = re.compile(r"(?<!\d)(\d{3})[\s-]?(\d{3})(?!\d)")


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
    settings = get_settings()
    if settings.interaction_mode == "workflow" and user_message.id is None:
        return error_response(
            "Authenticated interaction Cause ID is required",
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    if settings.interaction_mode == "workflow" and payload.interaction is None:
        return error_response(
            "Inbound SMS interaction envelope is required",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    logger.info("chat request", extra={"message_length": len(user_content)})

    try:
        if settings.interaction_mode == "workflow":
            assert payload.interaction is not None
            if not settings.database_url:
                raise ValueError("OPENMAGIC_DATABASE_URL is required for workflow interaction mode")
            identity_database = WorkflowDatabase(settings.database_url)
            try:
                party = await resolve_sms_party(
                    identity_database,
                    payload.interaction.sender_phone,
                )
            finally:
                await identity_database.dispose()
            interaction_id = sms_interaction_id(payload.interaction.sender_phone)
            session = get_conversation_session(interaction_id)
            runtime = create_interaction_runtime(
                settings,
                actor_party_id=party.party_id,
                interaction_id=interaction_id,
                conversation_state=session.log,
                working_memory_state=session.working_memory,
            )
        else:
            session = None
            runtime = create_interaction_runtime(settings)
    except ValueError as ve:
        # Missing API key error
        logger.error("configuration error", extra={"error": str(ve)})
        return error_response(str(ve), status_code=status.HTTP_400_BAD_REQUEST)

    async def _run_interaction() -> None:
        try:
            verification_codes = [
                "".join(match) for match in _VERIFICATION_CODE.findall(user_content)
            ]
            if (
                settings.interaction_mode == "workflow"
                and payload.interaction is not None
                and len(verification_codes) == 1
            ):
                verified = await get_step_up_verification(settings).submit_code(
                    SubmitVerificationCodeCommand(
                        actor_party_id=party.party_id,
                        interaction_id=interaction_id,
                        cause_id=user_message.id or "",
                        code=verification_codes[0],
                    )
                )
                assert session is not None
                session.log.record_user_message(
                    "[Verification code submitted]",
                    cause_id=user_message.id,
                )
                if verified.status == "verified" and verified.challenge_id is not None:
                    return
                if verified.status == "no_active_challenge":
                    session.log.record_reply(
                        "There is no active verification request for this conversation.",
                        cause_id=user_message.id,
                    )
                    return
                failure_messages = {
                    "invalid_code": "That verification code is not valid. Please try again.",
                    "attempts_exhausted": (
                        "Too many incorrect codes. Ask me to try the protected request again."
                    ),
                    "expired": "That verification code expired. Ask me to try the request again.",
                    "verification_unavailable": (
                        "Verification is no longer available for that on-file email."
                    ),
                }
                session.log.record_reply(
                    failure_messages[verified.status],
                    cause_id=user_message.id,
                )
                return
            safe_user_content = _VERIFICATION_CODE.sub(
                "[six-digit value redacted]",
                user_content,
            )
            await runtime.execute(user_message=safe_user_content, cause_id=user_message.id)
        except Exception as exc:  # pragma: no cover - defensive
            logger.error("chat task failed", extra={"error": str(exc)})

    task = asyncio.create_task(_run_interaction())
    _BACKGROUND_TASKS.add(task)
    task.add_done_callback(_BACKGROUND_TASKS.discard)

    return PlainTextResponse("", status_code=status.HTTP_202_ACCEPTED)
