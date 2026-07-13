"""Safe runtime observation for paired legacy and Workflow coordination."""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from html import escape
from typing import Any
from uuid import UUID

from server.agents.interaction_agent import (
    Completion,
    InteractionAgentRuntime,
    InteractionToolbox,
    InteractionToolContext,
    LegacyInteractionToolbox,
    ToolResult,
    WorkflowInteractionToolbox,
    build_system_prompt,
    build_workflow_system_prompt,
    prepare_workflow_message,
)
from server.config import Settings

from .coordination_contracts import (
    CoordinationDiagnostics,
    CoordinationOutcome,
    CoordinationProfile,
    CoordinationScenario,
    CoordinationToolStep,
    CoordinationTrial,
)

MutatedWorkflows = Callable[[], Awaitable[tuple[UUID, ...]]]

_KNOWN_TOOL_NAMES = frozenset(
    {
        "approve_job",
        "propose_renewal_email",
        "read_workflow_packet",
        "search_workflows",
        "send_draft",
        "send_message_to_agent",
        "send_message_to_user",
        "wait",
    }
)
_KNOWN_ARGUMENT_FIELDS = frozenset(
    {
        "agent_name",
        "body",
        "cc",
        "cursor",
        "expected_draft_revision_id",
        "instructions",
        "job_id",
        "limit",
        "message",
        "organization",
        "participant",
        "query",
        "reason",
        "renewal_period",
        "status",
        "subject",
        "to",
        "workflow_id",
        "workflow_kind",
    }
)


@dataclass
class _Observation:
    model_calls: int = 0
    search_calls: int = 0
    packet_reads: int = 0
    max_context_bytes: int = 0
    model_duration_ns: int = 0
    local_tool_duration_ns: int = 0
    delegated: bool = False
    selected_workflow_id: UUID | None = None
    last_search_matches: int | None = None
    tool_steps: list[CoordinationToolStep] = field(default_factory=list)
    user_messages: list[str] = field(default_factory=list)


class _MemoryConversationState:
    def __init__(self) -> None:
        self._entries: list[tuple[str, str]] = []

    def load_transcript(self) -> str:
        return "\n".join(f"<{kind}>{text}</{kind}>" for kind, text in self._entries)

    def record_user_message(self, message: str) -> None:
        self._entries.append(("user_message", message))

    def record_agent_message(self, message: str) -> None:
        self._entries.append(("agent_message", message))

    def record_reply(self, message: str) -> None:
        self._entries.append(("poke_reply", message))


class _EmptyWorkingMemory:
    def render_transcript(self) -> str:
        return ""


class _ObservedToolbox:
    """Measure one profile while suppressing external and user delivery effects."""

    def __init__(
        self,
        *,
        profile: CoordinationProfile,
        observation: _Observation,
        delegate: InteractionToolbox | None = None,
    ) -> None:
        self._profile = profile
        self._observation = observation
        self._delegate = delegate

    @property
    def schemas(self) -> tuple[dict[str, Any], ...]:
        if self._delegate is not None:
            return self._delegate.schemas
        return LegacyInteractionToolbox().schemas

    async def invoke(
        self,
        name: str,
        arguments: dict[str, Any],
        context: InteractionToolContext,
    ) -> ToolResult:
        started = time.perf_counter_ns()
        try:
            result = await self._invoke_safely(name, arguments, context)
        finally:
            self._observation.local_tool_duration_ns += time.perf_counter_ns() - started
        self._observation.tool_steps.append(_tool_step(name, arguments, result))
        self._record_result(name, result)
        return result

    async def _invoke_safely(
        self,
        name: str,
        arguments: dict[str, Any],
        context: InteractionToolContext,
    ) -> ToolResult:
        if name == "send_message_to_agent":
            if self._profile != "legacy":
                return ToolResult(success=False, payload={"code": "unknown_tool"})
            return ToolResult(
                success=True,
                payload={
                    "status": "observed_not_dispatched",
                    "agent_name": arguments.get("agent_name"),
                },
            )
        if name == "send_message_to_user":
            message = arguments.get("message")
            if not isinstance(message, str) or not message:
                return ToolResult(success=False, payload={"code": "invalid_arguments"})
            return ToolResult(
                success=True,
                payload={"status": "observed_not_delivered"},
                user_message=message,
                recorded_reply=True,
            )
        if name in {"wait", "send_draft"}:
            return ToolResult(success=True, payload={"status": "observed_not_delivered"})
        if self._delegate is None or name not in {
            "search_workflows",
            "read_workflow_packet",
            "propose_renewal_email",
        }:
            return ToolResult(success=False, payload={"code": "unknown_tool"})
        return await self._delegate.invoke(name, arguments, context)

    def _record_result(self, name: str, result: ToolResult) -> None:
        if result.user_message:
            self._observation.user_messages.append(result.user_message)
        if name == "send_message_to_agent" and result.success:
            self._observation.delegated = True
        if name == "search_workflows" and result.success and isinstance(result.payload, dict):
            self._observation.search_calls += 1
            matches = result.payload.get("total_matches")
            if isinstance(matches, int):
                self._observation.last_search_matches = matches
        if name == "read_workflow_packet" and result.success:
            self._observation.packet_reads += 1
        if name == "propose_renewal_email" and result.success and isinstance(result.payload, dict):
            workflow_id = result.payload.get("workflow_id")
            if workflow_id is not None:
                self._observation.selected_workflow_id = UUID(str(workflow_id))


class PairedCoordinationEvaluator:
    """Run the same synthetic request through legacy and Workflow profiles."""

    def __init__(
        self,
        *,
        settings: Settings,
        workflow_toolbox: WorkflowInteractionToolbox,
        workflow_context_factory: Callable[[str], InteractionToolContext],
        completion: Completion,
        mutated_workflows: MutatedWorkflows,
        application_build: str,
        run_id: str,
    ) -> None:
        self._settings = settings
        self._workflow_toolbox = workflow_toolbox
        self._workflow_context_factory = workflow_context_factory
        self._completion = completion
        self._mutated_workflows = mutated_workflows
        self._application_build = application_build
        self._run_id = run_id

    async def evaluate(
        self,
        scenario: CoordinationScenario,
    ) -> tuple[CoordinationTrial, CoordinationTrial]:
        baseline = await self._run_profile("legacy", scenario)
        workflow = await self._run_profile("workflow", scenario)
        return baseline, workflow

    async def _run_profile(
        self,
        profile: CoordinationProfile,
        scenario: CoordinationScenario,
    ) -> CoordinationTrial:
        before = await self._mutated_workflows()
        observation = _Observation()
        toolbox = _ObservedToolbox(
            profile=profile,
            observation=observation,
            delegate=self._workflow_toolbox if profile == "workflow" else None,
        )
        runtime = self._runtime(profile, scenario, observation, toolbox)
        result = await runtime.execute(
            scenario.request,
            cause_id=f"eval:{self._run_id}:{scenario.scenario_id}:{profile}",
        )
        created_job_workflow_ids = _new_items(before, await self._mutated_workflows())
        outcome = _outcome(result.success, observation, result.response)
        correctness = (
            None
            if profile == "legacy"
            else _workflow_correctness(
                scenario,
                outcome,
                observation,
                created_job_workflow_ids,
            )
        )
        return CoordinationTrial(
            scenario_id=scenario.scenario_id,
            profile=profile,
            model=self._settings.interaction_agent_model,
            application_build=self._application_build,
            outcome=outcome,
            correctness=correctness,
            response_digest=hashlib.sha256(result.response.encode()).hexdigest(),
            selected_workflow_id=observation.selected_workflow_id,
            created_job_workflow_ids=created_job_workflow_ids,
            diagnostics=_diagnostics(observation),
        )

    def _runtime(
        self,
        profile: CoordinationProfile,
        scenario: CoordinationScenario,
        observation: _Observation,
        toolbox: _ObservedToolbox,
    ) -> InteractionAgentRuntime:
        common: dict[str, Any] = {
            "toolbox": toolbox,
            "completion": self._measured_completion(observation),
            "conversation_state": _MemoryConversationState(),
            "working_memory_state": _EmptyWorkingMemory(),
            "settings": self._settings,
        }
        if profile == "workflow":
            return InteractionAgentRuntime(
                **common,
                tool_context_factory=self._workflow_context_factory,
                system_prompt_builder=build_workflow_system_prompt,
                message_builder=prepare_workflow_message,
                interaction_cause_recorder=self._workflow_toolbox.record_interaction_cause,
            )
        return InteractionAgentRuntime(
            **common,
            system_prompt_builder=build_system_prompt,
            message_builder=_legacy_message_builder(scenario.irrelevant_legacy_agents),
        )

    def _measured_completion(self, observation: _Observation) -> Completion:
        async def complete(**request: Any) -> dict[str, Any]:
            context_payload = {
                "system": request.get("system"),
                "messages": request.get("messages"),
                "tools": request.get("tools"),
            }
            context_bytes = len(
                json.dumps(context_payload, sort_keys=True, default=str).encode("utf-8")
            )
            observation.model_calls += 1
            observation.max_context_bytes = max(observation.max_context_bytes, context_bytes)
            started = time.perf_counter_ns()
            try:
                return await self._completion(**request)
            finally:
                observation.model_duration_ns += time.perf_counter_ns() - started

        return complete


def _diagnostics(observation: _Observation) -> CoordinationDiagnostics:
    context_bytes = observation.max_context_bytes
    return CoordinationDiagnostics(
        model_calls=observation.model_calls,
        tool_calls=tuple(step.name for step in observation.tool_steps),
        tool_steps=tuple(observation.tool_steps),
        search_calls=observation.search_calls,
        packet_reads=observation.packet_reads,
        max_context_bytes=context_bytes,
        approximate_context_tokens=(context_bytes + 3) // 4,
        model_duration_ms=observation.model_duration_ns / 1_000_000,
        local_tool_duration_ms=observation.local_tool_duration_ns / 1_000_000,
    )


def _new_items(before: tuple[UUID, ...], after: tuple[UUID, ...]) -> tuple[UUID, ...]:
    remaining = list(after)
    for item in before:
        if item in remaining:
            remaining.remove(item)
    return tuple(remaining)


def _outcome(
    success: bool,
    observation: _Observation,
    final_response: str,
) -> CoordinationOutcome:
    if not success:
        return "failed"
    if observation.selected_workflow_id is not None:
        return "proposed"
    if observation.delegated:
        return "delegated"
    user_text = "\n".join((*observation.user_messages, final_response))
    if observation.last_search_matches == 0 and _reports_no_match(user_text):
        return "no_match"
    if observation.last_search_matches is not None and _requests_clarification(user_text):
        return "clarified"
    return "failed"


def _workflow_correctness(
    scenario: CoordinationScenario,
    outcome: CoordinationOutcome,
    observation: _Observation,
    mutations: tuple[UUID, ...],
) -> bool:
    if outcome != scenario.expected_outcome:
        return False
    if scenario.expected_outcome != "proposed":
        return not mutations
    return (
        observation.selected_workflow_id == scenario.expected_workflow_id
        and _has_successful_workflow_step(
            observation, "search_workflows", scenario.expected_workflow_id
        )
        and _has_successful_workflow_step(
            observation, "read_workflow_packet", scenario.expected_workflow_id
        )
        and len(mutations) == scenario.expected_workflow_jobs
        and all(workflow_id == scenario.expected_workflow_id for workflow_id in mutations)
    )


def _has_successful_workflow_step(
    observation: _Observation,
    name: str,
    workflow_id: UUID | None,
) -> bool:
    return any(
        step.name == name and step.success and step.workflow_id == workflow_id
        for step in observation.tool_steps
    )


def _requests_clarification(text: str) -> bool:
    normalized = " ".join(text.casefold().split())
    asks_for_detail = any(
        phrase in normalized
        for phrase in (
            "can you confirm",
            "could you clarify",
            "could you specify",
            "need more detail",
            "please clarify",
            "please specify",
            "which john",
            "which renewal",
        )
    )
    identifies_missing_context = any(
        term in normalized
        for term in ("brokerage", "john", "organization", "renewal", "workflow", "year")
    )
    claims_action = any(term in normalized for term in ("queued", "selected", "sent"))
    return asks_for_detail and identifies_missing_context and not claims_action


def _reports_no_match(text: str) -> bool:
    normalized = " ".join(text.casefold().split())
    reports_absence = any(
        phrase in normalized
        for phrase in (
            "could not find a matching",
            "could not find any",
            "couldn't find a matching",
            "couldn't find any",
            "did not find a matching",
            "did not find any",
            "didn't find a matching",
            "didn't find any",
            "no matching",
            "unable to find a matching",
            "unable to find any",
        )
    )
    identifies_domain = any(term in normalized for term in ("renewal", "workflow"))
    claims_action = any(term in normalized for term in ("queued", "selected", "sent"))
    return reports_absence and identifies_domain and not claims_action


def _tool_step(
    name: str,
    arguments: dict[str, Any],
    result: ToolResult,
) -> CoordinationToolStep:
    payload = result.payload if isinstance(result.payload, dict) else {}
    result_code = payload.get("code")
    workflow_id = _workflow_id_from_step(name, arguments, payload)
    supplied_fields = {key for key, value in arguments.items() if value is not None}
    argument_fields = tuple(sorted(supplied_fields & _KNOWN_ARGUMENT_FIELDS))
    if supplied_fields - _KNOWN_ARGUMENT_FIELDS:
        argument_fields = (*argument_fields, "unknown_field")
    return CoordinationToolStep(
        name=name if name in _KNOWN_TOOL_NAMES else "unknown_tool",
        success=result.success,
        result_code=result_code if isinstance(result_code, str) else None,
        argument_fields=argument_fields,
        arguments_digest=hashlib.sha256(
            json.dumps(arguments, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest(),
        total_matches=(
            payload.get("total_matches") if isinstance(payload.get("total_matches"), int) else None
        ),
        has_more=payload.get("has_more") if isinstance(payload.get("has_more"), bool) else None,
        workflow_id=workflow_id,
    )


def _workflow_id_from_step(
    name: str,
    arguments: dict[str, Any],
    payload: dict[str, Any],
) -> UUID | None:
    candidate = payload.get("workflow_id", arguments.get("workflow_id"))
    if name == "search_workflows" and payload.get("total_matches") == 1:
        results = payload.get("results")
        if isinstance(results, list) and results and isinstance(results[0], dict):
            candidate = results[0].get("workflow_id")
    try:
        return UUID(str(candidate)) if candidate is not None else None
    except (TypeError, ValueError):
        return None


def _legacy_message_builder(
    synthetic_agents: tuple[str, ...],
) -> Callable[[str, str, str], list[dict[str, str]]]:
    def build(
        latest_text: str, transcript: str, message_type: str = "user"
    ) -> list[dict[str, str]]:
        history = transcript.strip() or "None"
        roster = (
            "\n".join(f'<agent name="{escape(agent, quote=True)}" />' for agent in synthetic_agents)
            or "None"
        )
        tag = "new_agent_message" if message_type == "agent" else "new_user_message"
        content = (
            f"<conversation_history>\n{history}\n</conversation_history>\n\n"
            f"<active_agents>\n{roster}\n</active_agents>\n\n"
            f"<{tag}>\n{latest_text.strip()}\n</{tag}>"
        )
        return [{"role": "user", "content": content}]

    return build


__all__ = ["PairedCoordinationEvaluator"]
