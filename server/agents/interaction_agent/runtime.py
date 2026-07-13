"""Interaction Agent Runtime - handles LLM calls for user and agent turns."""

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol
from uuid import UUID, uuid4

from ...config import Settings, get_settings
from ...logging_config import logger
from ...openrouter_client import request_chat_completion
from ...services.conversation import get_conversation_log, get_working_memory_log
from ...workflows import (
    InteractionActivityAction,
    InteractionActivityStatus,
    InteractionActivityStore,
    ProtectedOperation,
)
from .agent import build_system_prompt, prepare_message_with_history
from .toolbox import InteractionToolbox, InteractionToolContext, ToolResult


class _ConversationState(Protocol):
    def load_transcript(self) -> str: ...

    def record_user_message(self, message: str, *, cause_id: str | None = None) -> None: ...

    def record_agent_message(self, message: str) -> None: ...

    def record_reply(self, message: str, *, cause_id: str | None = None) -> None: ...

    def record_reply_once(
        self,
        delivery_id: str,
        message: str,
        *,
        cause_id: str | None = None,
    ) -> bool: ...


class _WorkingMemoryState(Protocol):
    def render_transcript(self) -> str: ...


Completion = Callable[..., Awaitable[dict[str, Any]]]


@dataclass
class InteractionResult:
    """Result from the interaction agent."""

    success: bool
    response: str
    error: str | None = None
    execution_agents_used: int = 0


@dataclass
class _ToolCall:
    """Parsed tool invocation from an LLM response."""

    identifier: str | None
    name: str
    arguments: dict[str, Any]


@dataclass
class _LoopSummary:
    """Aggregate information produced by the interaction loop."""

    last_assistant_text: str = ""
    user_messages: list[str] = field(default_factory=list)
    tool_names: list[str] = field(default_factory=list)
    execution_agents: set[str] = field(default_factory=set)


class InteractionAgentRuntime:
    """Manages the interaction agent's request processing."""

    MAX_TOOL_ITERATIONS = 8

    # Initialize interaction agent runtime with settings and service dependencies
    def __init__(
        self,
        *,
        toolbox: InteractionToolbox,
        tool_context_factory: Callable[[str], InteractionToolContext] | None = None,
        system_prompt_builder: Callable[[], str] = build_system_prompt,
        message_builder: Callable[..., list[dict[str, str]]] = prepare_message_with_history,
        interaction_cause_recorder: Callable[[InteractionToolContext, str], Awaitable[None]]
        | None = None,
        activity_store: InteractionActivityStore | None = None,
        completion: Completion | None = None,
        conversation_state: _ConversationState | None = None,
        working_memory_state: _WorkingMemoryState | None = None,
        settings: Settings | None = None,
    ) -> None:
        settings = settings or get_settings()
        self.api_key = settings.openrouter_api_key
        self.model = settings.interaction_agent_model
        self.settings = settings
        self.conversation_log = conversation_state or get_conversation_log()
        self.working_memory_log = working_memory_state or get_working_memory_log()
        self.toolbox = toolbox
        self.tool_schemas = list(self.toolbox.schemas)
        self._tool_context_factory = tool_context_factory or self._legacy_tool_context
        self._system_prompt_builder = system_prompt_builder
        self._message_builder = message_builder
        self._interaction_cause_recorder = interaction_cause_recorder
        self._activity_store = activity_store
        self._completion = completion

        if not self.api_key:
            raise ValueError(
                "OpenRouter API key not configured. Set OPENROUTER_API_KEY environment variable."
            )

    # Main entry point for processing user messages through the LLM interaction loop
    async def execute(
        self,
        user_message: str,
        *,
        cause_id: str | None = None,
    ) -> InteractionResult:
        """Handle a user-authored message."""

        try:
            transcript_before = self._load_conversation_transcript()
            system_prompt = self._system_prompt_builder()
            messages = self._message_builder(user_message, transcript_before, message_type="user")
            if self._interaction_cause_recorder is not None and cause_id is None:
                raise ValueError("Authenticated interaction Cause ID is required")
            tool_context = self._tool_context_factory(cause_id or f"message-{uuid4()}")
            if self._interaction_cause_recorder is not None:
                await self._interaction_cause_recorder(tool_context, user_message)
            self.conversation_log.record_user_message(user_message, cause_id=tool_context.cause_id)

            logger.info("Processing user message through interaction agent")
            summary = await self._run_interaction_loop(system_prompt, messages, tool_context)

            final_response = self._finalize_response(summary)

            if final_response and not summary.user_messages:
                self.conversation_log.record_reply(final_response, cause_id=tool_context.cause_id)

            return InteractionResult(
                success=True,
                response=final_response,
                execution_agents_used=len(summary.execution_agents),
            )

        except Exception as exc:
            logger.error("Interaction agent failed", extra={"error": str(exc)})
            return InteractionResult(
                success=False,
                response="",
                error=str(exc),
            )

    # Handle incoming messages from execution agents and generate appropriate responses
    async def handle_agent_message(self, agent_message: str) -> InteractionResult:
        """Process a status update emitted by an execution agent."""

        try:
            transcript_before = self._load_conversation_transcript()
            self.conversation_log.record_agent_message(agent_message)

            system_prompt = self._system_prompt_builder()
            messages = self._message_builder(agent_message, transcript_before, message_type="agent")
            tool_context = self._tool_context_factory(f"agent-message-{uuid4()}")

            logger.info("Processing execution agent results")
            summary = await self._run_interaction_loop(system_prompt, messages, tool_context)

            final_response = self._finalize_response(summary)

            if final_response and not summary.user_messages:
                self.conversation_log.record_reply(final_response)

            return InteractionResult(
                success=True,
                response=final_response,
                execution_agents_used=len(summary.execution_agents),
            )

        except Exception as exc:
            logger.error("Interaction agent (agent message) failed", extra={"error": str(exc)})
            return InteractionResult(
                success=False,
                response="",
                error=str(exc),
            )

    async def execute_fresh_notification(
        self,
        notification_message: str,
        tool_context: InteractionToolContext,
    ) -> InteractionResult:
        """Handle one Notification without reading or recording prior prompt history."""

        try:
            system_prompt = self._system_prompt_builder()
            messages = self._message_builder(notification_message, "", message_type="agent")
            summary = await self._run_interaction_loop(system_prompt, messages, tool_context)
            if not summary.user_messages:
                raise RuntimeError("Notification Interaction Agent did not present its request")
            return InteractionResult(success=True, response=summary.user_messages[-1])
        except Exception as exc:
            logger.error(
                "Fresh Notification Interaction Agent failed",
                extra={"error_type": type(exc).__name__},
            )
            return InteractionResult(success=False, response="", error=type(exc).__name__)

    async def execute_verified_resume(
        self,
        *,
        notification_id: UUID,
        operation_cause_id: str,
        operation_cause_type: Literal["message", "ui_action"] = "message",
        challenge_id: UUID,
        workflow_id: UUID,
        operation: ProtectedOperation,
    ) -> InteractionResult:
        """Run the exact stored operation, then let a fresh turn present its result."""

        try:
            tool_context = self._tool_context_factory(operation_cause_id)
            tool_context.cause_type = operation_cause_type
            tool_context.verification_challenge_id = challenge_id
            tool_context.trusted_workflow_id = workflow_id
            tool_context.delivery_id = str(notification_id)
            result = await self._execute_tool(
                _ToolCall(
                    identifier=None,
                    name=operation.name,
                    arguments=operation.arguments,
                ),
                tool_context,
            )
            if not result.success:
                recovery = (
                    "Verification succeeded, but I can no longer complete that request because "
                    "your access or the Workflow state changed. Please start the request again."
                )
                self.conversation_log.record_reply_once(
                    str(notification_id),
                    recovery,
                    cause_id=operation_cause_id,
                )
                error_code = (
                    result.payload.get("code")
                    if isinstance(result.payload, dict)
                    else "protected_operation_rejected"
                )
                return InteractionResult(
                    success=False,
                    response=recovery,
                    error=str(error_code),
                )
            original_request_reader = getattr(
                self.conversation_log,
                "user_message_for_cause",
                None,
            )
            original_request = (
                original_request_reader(operation_cause_id)
                if callable(original_request_reader)
                else None
            )
            resumed_message = self._safe_json_dump(
                {
                    "verification": "succeeded",
                    "challenge_id": str(challenge_id),
                    "operation": operation.model_dump(mode="json"),
                    "result": result.payload,
                    "original_request": original_request,
                    "instruction": (
                        "Continue the original request from this verified result. "
                        "If the user asked to prepare a renewal email, propose it now."
                    ),
                }
            )
            messages = self._message_builder(
                resumed_message,
                "",
                message_type="agent",
            )
            summary = await self._run_interaction_loop(
                (
                    "Continue one verified user request from a freshly loaded Workflow Packet. "
                    "Use propose_renewal_email when the original request asks to prepare or draft "
                    "the renewal email. You may then send one short user-facing update. Never "
                    "approve or send the email. Do not mention Workflows, Jobs, Runs, packets, "
                    "the Control Plane, or provider internals unless the user explicitly asks "
                    "about the architecture."
                ),
                messages,
                tool_context,
                tool_schemas=self._schemas_named(
                    "propose_renewal_email",
                    "send_message_to_user",
                    "wait",
                ),
            )
            final_response = self._finalize_response(summary)
            if not final_response:
                raise RuntimeError("Verified continuation returned no response")
            self.conversation_log.record_reply_once(
                str(notification_id),
                final_response,
                cause_id=operation_cause_id,
            )
            return InteractionResult(success=True, response=final_response)
        except Exception as exc:
            logger.error(
                "Verified operation resumption failed",
                extra={"error_type": type(exc).__name__},
            )
            return InteractionResult(success=False, response="", error=type(exc).__name__)

    # Core interaction loop that handles LLM calls and tool executions until completion
    async def _run_interaction_loop(
        self,
        system_prompt: str,
        messages: list[dict[str, Any]],
        tool_context: InteractionToolContext,
        *,
        tool_schemas: tuple[dict[str, Any], ...] | None = None,
    ) -> _LoopSummary:
        """Iteratively query the LLM until it issues a final response."""

        summary = _LoopSummary()

        for _iteration in range(self.MAX_TOOL_ITERATIONS):
            if tool_schemas is None:
                response = await self._make_llm_call(system_prompt, messages)
            else:
                response = await self._make_llm_call(
                    system_prompt,
                    messages,
                    tool_schemas=tool_schemas,
                )
            assistant_message = self._extract_assistant_message(response)

            assistant_content = (assistant_message.get("content") or "").strip()
            if assistant_content:
                summary.last_assistant_text = assistant_content

            raw_tool_calls = assistant_message.get("tool_calls") or []
            parsed_tool_calls = self._parse_tool_calls(raw_tool_calls)

            assistant_entry: dict[str, Any] = {
                "role": "assistant",
                "content": assistant_message.get("content", "") or "",
            }
            if raw_tool_calls:
                assistant_entry["tool_calls"] = raw_tool_calls
            messages.append(assistant_entry)

            if not parsed_tool_calls:
                break

            for tool_call in parsed_tool_calls:
                summary.tool_names.append(tool_call.name)

                if tool_call.name == "send_message_to_agent":
                    agent_name = tool_call.arguments.get("agent_name")
                    if isinstance(agent_name, str) and agent_name:
                        summary.execution_agents.add(agent_name)

                result = await self._execute_tool(tool_call, tool_context)

                if result.user_message:
                    summary.user_messages.append(result.user_message)

                tool_message = {
                    "role": "tool",
                    "tool_call_id": tool_call.identifier or tool_call.name,
                    "content": self._format_tool_result(tool_call, result),
                }
                messages.append(tool_message)
        else:
            raise RuntimeError("Reached tool iteration limit without final response")

        if not summary.user_messages and not summary.last_assistant_text:
            logger.warning("Interaction loop exited without assistant content")

        return summary

    def _schemas_named(self, *names: str) -> tuple[dict[str, Any], ...]:
        allowed = set(names)
        return tuple(
            schema
            for schema in self.tool_schemas
            if schema.get("function", {}).get("name") in allowed
        )

    # Load conversation history, preferring summarized version if available
    def _load_conversation_transcript(self) -> str:
        if self.settings.summarization_enabled:
            rendered = self.working_memory_log.render_transcript()
            if rendered.strip():
                return rendered
        return self.conversation_log.load_transcript()

    # Execute API call to OpenRouter with system prompt, messages, and tool schemas
    async def _make_llm_call(
        self,
        system_prompt: str,
        messages: list[dict[str, Any]],
        *,
        tool_schemas: tuple[dict[str, Any], ...] | None = None,
    ) -> dict[str, Any]:
        """Make an LLM call via OpenRouter."""

        logger.debug(
            "Interaction agent calling LLM",
            extra={
                "model": self.model,
                "tools": len(self.tool_schemas if tool_schemas is None else tool_schemas),
            },
        )
        completion = self._completion or request_chat_completion
        return await completion(
            model=self.model,
            messages=messages,
            system=system_prompt,
            api_key=self.api_key,
            tools=self.tool_schemas if tool_schemas is None else list(tool_schemas),
        )

    # Extract the assistant's message from the OpenRouter API response structure
    def _extract_assistant_message(self, response: dict[str, Any]) -> dict[str, Any]:
        """Return the assistant message from the raw response payload."""

        choice = (response.get("choices") or [{}])[0]
        message = choice.get("message")
        if not isinstance(message, dict):
            raise RuntimeError("LLM response did not include an assistant message")
        return message

    # Convert raw LLM tool calls into structured _ToolCall objects with validation
    def _parse_tool_calls(self, raw_tool_calls: list[dict[str, Any]]) -> list[_ToolCall]:
        """Normalize tool call payloads from the LLM."""

        parsed: list[_ToolCall] = []
        for raw in raw_tool_calls:
            function_block = raw.get("function") or {}
            name = function_block.get("name")
            if not isinstance(name, str) or not name:
                logger.warning("Skipping tool call without name", extra={"tool": raw})
                continue

            arguments, error = self._parse_tool_arguments(function_block.get("arguments"))
            if error:
                logger.warning("Tool call arguments invalid", extra={"tool": name, "error": error})
                parsed.append(
                    _ToolCall(
                        identifier=raw.get("id"),
                        name=name,
                        arguments={"__invalid_arguments__": error},
                    )
                )
                continue

            parsed.append(_ToolCall(identifier=raw.get("id"), name=name, arguments=arguments))

        return parsed

    # Parse and validate tool arguments from various formats (dict, JSON string, etc.)
    def _parse_tool_arguments(self, raw_arguments: Any) -> tuple[dict[str, Any], str | None]:
        """Convert tool arguments into a dictionary, reporting errors."""

        if raw_arguments is None:
            return {}, None

        if isinstance(raw_arguments, dict):
            return raw_arguments, None

        if isinstance(raw_arguments, str):
            if not raw_arguments.strip():
                return {}, None
            try:
                parsed = json.loads(raw_arguments)
            except json.JSONDecodeError as exc:
                return {}, f"invalid json: {exc}"
            if isinstance(parsed, dict):
                return parsed, None
            return {}, "decoded arguments were not an object"

        return {}, f"unsupported argument type: {type(raw_arguments).__name__}"

    # Execute tool calls with error handling and logging, returning standardized results
    async def _execute_tool(
        self,
        tool_call: _ToolCall,
        context: InteractionToolContext,
    ) -> ToolResult:
        """Execute a tool call and convert low-level errors into structured results."""

        activity_id = await self._start_activity(tool_call, context)
        if "__invalid_arguments__" in tool_call.arguments:
            error = tool_call.arguments["__invalid_arguments__"]
            self._log_tool_invocation(tool_call, stage="rejected", detail={"error": error})
            result = ToolResult(success=False, payload={"error": error})
            await self._finish_activity(activity_id, tool_call, result, context)
            return result

        try:
            self._log_tool_invocation(tool_call, stage="start")
            result = await self.toolbox.invoke(tool_call.name, tool_call.arguments, context)
        except Exception as exc:  # pragma: no cover - defensive
            logger.error(
                "Tool execution crashed",
                extra={"tool": tool_call.name, "error_type": type(exc).__name__},
            )
            self._log_tool_invocation(
                tool_call,
                stage="error",
                detail={"error_type": type(exc).__name__},
            )
            result = ToolResult(success=False, payload={"code": "internal_error"})
            await self._finish_activity(activity_id, tool_call, result, context)
            return result

        if not isinstance(result, ToolResult):
            logger.warning(
                "Tool did not return ToolResult; coercing",
                extra={"tool": tool_call.name},
            )
            wrapped = ToolResult(success=True, payload=result)
            self._log_tool_invocation(tool_call, stage="done", result=wrapped)
            await self._finish_activity(activity_id, tool_call, wrapped, context)
            return wrapped

        status = "success" if result.success else "error"
        logger.debug(
            "Tool executed",
            extra={
                "tool": tool_call.name,
                "status": status,
            },
        )
        self._log_tool_invocation(tool_call, stage="done", result=result)
        await self._finish_activity(activity_id, tool_call, result, context)
        return result

    async def _start_activity(
        self,
        tool_call: _ToolCall,
        context: InteractionToolContext,
    ) -> UUID | None:
        activity_store = getattr(self, "_activity_store", None)
        if activity_store is None:
            return None
        try:
            action = InteractionActivityAction(tool_call.name)
        except ValueError:
            return None
        try:
            receipt = await activity_store.start(cause_id=context.cause_id, action=action)
        except Exception as exc:  # pragma: no cover - telemetry must remain non-blocking
            logger.warning(
                "Interaction activity receipt start failed",
                extra={"tool": tool_call.name, "error_type": type(exc).__name__},
            )
            return None
        return receipt.id

    async def _finish_activity(
        self,
        receipt_id: UUID | None,
        tool_call: _ToolCall,
        result: ToolResult,
        context: InteractionToolContext,
    ) -> None:
        activity_store = getattr(self, "_activity_store", None)
        if receipt_id is None or activity_store is None:
            return
        action = InteractionActivityAction(tool_call.name)
        workflow_id = self._trusted_activity_workflow_id(action, result, context)
        try:
            await activity_store.finish(
                receipt_id,
                status=(
                    InteractionActivityStatus.SUCCEEDED
                    if result.success
                    else InteractionActivityStatus.FAILED
                ),
                workflow_id=workflow_id,
            )
        except Exception as exc:  # pragma: no cover - telemetry must remain non-blocking
            logger.warning(
                "Interaction activity receipt finish failed",
                extra={"error_type": type(exc).__name__},
            )

    @staticmethod
    def _trusted_activity_workflow_id(
        action: InteractionActivityAction,
        result: ToolResult,
        context: InteractionToolContext,
    ) -> UUID | None:
        if not result.success or action is InteractionActivityAction.SEARCH_WORKFLOWS:
            return None
        if action is InteractionActivityAction.READ_WORKFLOW_PACKET:
            return (
                context.loaded_packet.workflow.workflow_id
                if context.loaded_packet is not None
                else None
            )
        if context.loaded_packet is not None:
            return context.loaded_packet.workflow.workflow_id
        return context.trusted_workflow_id or context.resolved_workflow_id

    # Format tool execution results into JSON for LLM consumption
    def _format_tool_result(self, tool_call: _ToolCall, result: ToolResult) -> str:
        """Render a tool execution result back to the LLM."""

        payload: dict[str, Any] = {
            "tool": tool_call.name,
            "status": "success" if result.success else "error",
            "arguments": {
                key: value
                for key, value in tool_call.arguments.items()
                if key != "__invalid_arguments__"
            },
        }

        if result.payload is not None:
            key = "result" if result.success else "error"
            payload[key] = result.payload

        return self._safe_json_dump(payload)

    # Safely serialize objects to JSON with fallback to string representation
    def _safe_json_dump(self, payload: Any) -> str:
        """Serialize payload to JSON, falling back to repr on failure."""

        try:
            return json.dumps(payload, default=str)
        except TypeError:
            return repr(payload)

    # Log tool execution stages (start, done, error) with structured metadata
    def _log_tool_invocation(
        self,
        tool_call: _ToolCall,
        *,
        stage: str,
        result: ToolResult | None = None,
        detail: dict[str, Any] | None = None,
    ) -> None:
        """Emit structured logs for tool lifecycle events."""

        cleaned_args = {
            key: value
            for key, value in tool_call.arguments.items()
            if key in {"workflow_id", "cursor", "limit"}
        }

        log_payload: dict[str, Any] = {
            "tool": tool_call.name,
            "stage": stage,
            "arguments": cleaned_args,
        }

        if result is not None:
            log_payload["success"] = result.success
            if isinstance(result.payload, dict):
                log_payload["result"] = {
                    key: result.payload[key]
                    for key in ("workflow_id", "status", "total_matches", "has_more")
                    if key in result.payload
                }

        if detail:
            log_payload.update(detail)

        if stage == "done":
            logger.info(f"Tool '{tool_call.name}' completed")
        elif stage in {"error", "rejected"}:
            logger.warning(f"Tool '{tool_call.name}' {stage}")
        else:
            logger.debug(f"Tool '{tool_call.name}' {stage}")

    # Determine final user-facing response from interaction loop summary
    def _finalize_response(self, summary: _LoopSummary) -> str:
        """Decide what text should be exposed to the user as the final reply."""

        if summary.user_messages:
            return summary.user_messages[-1]

        return summary.last_assistant_text

    @staticmethod
    def _legacy_tool_context(cause_id: str) -> InteractionToolContext:
        return InteractionToolContext(
            actor_party_id=UUID(int=0),
            organization_party_id=UUID(int=0),
            cause_id=cause_id,
        )
