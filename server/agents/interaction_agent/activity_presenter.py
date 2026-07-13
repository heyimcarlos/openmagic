"""Application-authored summaries for visible Interaction Agent tool activity."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from server.workflows import InteractionActivityAction, InteractionActivityPresentation

from .toolbox import ToolResult

_FAILURE_SUMMARIES = {
    "workflow_resolution_required": "select one Workflow before reading its packet",
    "workflow_packet_required": "load the selected Workflow Packet before changing it",
    "workflow_packet_already_selected": "only one Workflow Packet may be selected per turn",
    "stale_approval_target": "the approval target changed or is no longer waiting",
    "verification_required": "identity verification is required",
    "verification_in_progress": "identity verification is still in progress",
    "verification_unavailable": "identity verification is unavailable",
    "verification_context_required": "this request cannot be verified safely",
    "invalid_arguments": "the tool input was invalid",
    "unknown_tool": "the requested tool is not registered",
    "state_change_already_committed": "this request was already applied",
    "internal_error": "the tool failed safely",
}


def present_activity_input(
    action: InteractionActivityAction,
    arguments: Mapping[str, Any],
) -> str | None:
    """Return a bounded summary of the safe, model-selected tool arguments."""

    if action is InteractionActivityAction.SEARCH_WORKFLOWS:
        parts: list[str] = []
        query = _display_text(arguments.get("query"), 100)
        if query:
            parts.append(f'query "{query}"')
        for key, label in (
            ("workflow_kind", "kind"),
            ("status", "status"),
            ("participant", "participant"),
            ("organization", "organization"),
            ("renewal_period", "renewal period"),
        ):
            value = _display_text(arguments.get(key), 80)
            if value:
                parts.append(f"{label} {value}")
        return " · ".join(parts) or "all authorized Workflows"

    if action is InteractionActivityAction.READ_WORKFLOW_PACKET:
        return "selected Workflow"

    if action is InteractionActivityAction.PROPOSE_WORKFLOW:
        return _objective_input(arguments)

    if action in {
        InteractionActivityAction.PROPOSE_WORKFLOW_WORK,
        InteractionActivityAction.REVISE_WORKFLOW_WORK,
    }:
        operation = arguments.get("operation")
        if isinstance(operation, Mapping):
            objective = _display_text(operation.get("objective"), 160)
            if objective:
                return objective
        return "selected Workflow"

    if action is InteractionActivityAction.APPROVE_JOB:
        return "exact presented effect"

    return None


def present_activity_result(
    action: InteractionActivityAction,
    result: ToolResult,
) -> InteractionActivityPresentation:
    """Convert a tool result into bounded, non-sensitive display context."""

    if not result.success:
        code = _result_code(result.payload)
        detail = _FAILURE_SUMMARIES.get(code, "the tool failed safely")
        return InteractionActivityPresentation(summary=f"Failed: {detail}")

    payload = result.payload if isinstance(result.payload, Mapping) else {}
    if action is InteractionActivityAction.SEARCH_WORKFLOWS:
        return _present_search_result(payload)
    if action is InteractionActivityAction.READ_WORKFLOW_PACKET:
        return _present_packet_result(payload)
    if action is InteractionActivityAction.PROPOSE_WORKFLOW:
        return InteractionActivityPresentation(summary="Created a typed Workflow graph")
    if action is InteractionActivityAction.PROPOSE_WORKFLOW_WORK:
        return InteractionActivityPresentation(summary="Added typed work to the Workflow")
    if action is InteractionActivityAction.REVISE_WORKFLOW_WORK:
        return InteractionActivityPresentation(summary="Created a replacement draft revision")
    if action is InteractionActivityAction.APPROVE_JOB:
        return InteractionActivityPresentation(summary="Approved the exact presented effect")
    return InteractionActivityPresentation(summary="Tool completed")


def _objective_input(arguments: Mapping[str, Any]) -> str:
    objective = _display_text(arguments.get("objective"), 180)
    return objective or "new typed Workflow"


def _present_search_result(payload: Mapping[str, Any]) -> InteractionActivityPresentation:
    results = payload.get("results")
    visible = results[:5] if isinstance(results, list) else []
    total = payload.get("total_matches")
    total_matches = total if isinstance(total, int) and total >= 0 else len(visible)
    summary = f"{total_matches} authorized matches, showing {len(visible)}"

    items: list[str] = []
    for candidate in visible:
        if not isinstance(candidate, Mapping):
            continue
        parts = [
            _display_text(candidate.get("objective"), 120),
            _display_text(candidate.get("status"), 30),
            _display_text(candidate.get("organization"), 60),
        ]
        reasons = candidate.get("match_reasons")
        if isinstance(reasons, Sequence) and not isinstance(reasons, (str, bytes)):
            reason_text = ", ".join(
                reason for value in reasons[:3] if (reason := _display_text(value, 60)) is not None
            )
            parts.append(reason_text or None)
        item = " · ".join(part for part in parts if part)
        if item:
            items.append(item[:255])
    if payload.get("has_more") is True and len(items) < 8:
        items.append("More authorized matches are available")
    return InteractionActivityPresentation(summary=summary, items=tuple(items))


def _present_packet_result(payload: Mapping[str, Any]) -> InteractionActivityPresentation:
    workflow = payload.get("workflow")
    jobs = payload.get("jobs")
    if not isinstance(workflow, Mapping):
        return InteractionActivityPresentation(summary="Loaded bounded Workflow Packet")
    job_count = len(jobs) if isinstance(jobs, list) else 0
    parts = [
        _display_text(workflow.get("objective"), 120),
        _display_text(workflow.get("status"), 30),
        _display_text(workflow.get("organization"), 60),
        f"{job_count} Jobs",
    ]
    item = " · ".join(part for part in parts if part)[:255]
    return InteractionActivityPresentation(
        summary="Loaded bounded Workflow Packet",
        items=(item,) if item else (),
    )


def _result_code(payload: Any) -> str | None:
    if not isinstance(payload, Mapping):
        return None
    value = payload.get("code")
    return value if isinstance(value, str) else None


def _display_text(value: Any, limit: int) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = " ".join(value.split())
    if not normalized:
        return None
    return normalized[:limit]


__all__ = ["present_activity_input", "present_activity_result"]
