"""Interaction agent module."""

from .agent import (
    build_system_prompt,
    prepare_message_with_history,
)
from .factory import create_interaction_runtime
from .runtime import Completion, InteractionAgentRuntime, InteractionResult
from .toolbox import InteractionToolbox, InteractionToolContext, ToolResult
from .tools import LegacyInteractionToolbox
from .workflow_agent import build_workflow_system_prompt, prepare_workflow_message
from .workflow_tools import WorkflowInteractionToolbox

__all__ = [
    "Completion",
    "InteractionAgentRuntime",
    "InteractionResult",
    "InteractionToolContext",
    "InteractionToolbox",
    "LegacyInteractionToolbox",
    "ToolResult",
    "WorkflowInteractionToolbox",
    "build_system_prompt",
    "build_workflow_system_prompt",
    "create_interaction_runtime",
    "prepare_message_with_history",
    "prepare_workflow_message",
]
