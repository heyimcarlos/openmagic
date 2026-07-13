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

from server.agents.interaction_agent.agent import build_system_prompt
from server.agents.interaction_agent.runtime import Completion, InteractionAgentRuntime
from server.agents.interaction_agent.toolbox import (
    InteractionToolbox,
    InteractionToolContext,
    ToolResult,
)
from server.agents.interaction_agent.tools import TOOL_SCHEMAS
from server.agents.interaction_agent.workflow_agent import (
    build_workflow_system_prompt,
    prepare_workflow_message,
)
from server.agents.interaction_agent.workflow_tools import WorkflowInteractionToolbox
from server.config import Settings

from .coordination_contracts import (
    CoordinationDiagnostics,
    CoordinationOutcome,
    CoordinationProfile,
    CoordinationScenario,
    CoordinationTrial,
)

MutatedWorkflows = Callable[[], Awaitable[tuple[UUID, ...]]]


@dataclass
class _Observation:
    model_calls: int = 0
    tool_calls: list[str] = field(default_factory=list)
    search_calls: int = 0
    packet_reads: int = 0
    max_context_bytes: int = 0
    model_duration_ns: int = 0
    local_tool_duration_ns: int = 0
    delegated: bool = False
    selected_workflow_id: UUID | None = None
    last_search_matches: int | None = None


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
        return tuple(TOOL_SCHEMAS)

    async def invoke(
        self,
        name: str,
        arguments: dict[str, Any],
        context: InteractionToolContext,
    ) -> ToolResult:
        started = time.perf_counter_ns()
        self._observation.tool_calls.append(name)
        if name == "search_workflows":
            self._observation.search_calls += 1
        elif name == "read_workflow_packet":
            self._observation.packet_reads += 1
        try:
            result = await self._invoke_safely(name, arguments, context)
        finally:
            self._observation.local_tool_duration_ns += time.perf_counter_ns() - started
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
        if self._delegate is None:
            return ToolResult(success=False, payload={"code": "unknown_tool"})
        return await self._delegate.invoke(name, arguments, context)

    def _record_result(self, name: str, result: ToolResult) -> None:
        if name == "send_message_to_agent" and result.success:
            self._observation.delegated = True
        if name == "search_workflows" and result.success and isinstance(result.payload, dict):
            matches = result.payload.get("total_matches")
            if isinstance(matches, int):
                self._observation.last_search_matches = matches
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
        mutations = _new_items(before, await self._mutated_workflows())
        outcome = _outcome(result.success, observation)
        correctness = (
            None
            if profile == "legacy"
            else _workflow_correctness(scenario, outcome, observation, mutations)
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
            mutated_workflow_ids=mutations,
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
        tool_calls=tuple(observation.tool_calls),
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


def _outcome(success: bool, observation: _Observation) -> CoordinationOutcome:
    if not success:
        return "failed"
    if observation.selected_workflow_id is not None:
        return "proposed"
    if observation.delegated:
        return "delegated"
    if observation.last_search_matches == 0:
        return "no_match"
    if observation.last_search_matches is not None:
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
        and len(mutations) == scenario.expected_workflow_jobs
        and all(workflow_id == scenario.expected_workflow_id for workflow_id in mutations)
    )


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
