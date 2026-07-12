"""Prompt and context renderer for the durable Workflow interaction profile."""

from __future__ import annotations

from pathlib import Path

_PROMPT_PATH = Path(__file__).with_name("workflow_system_prompt.md")
WORKFLOW_SYSTEM_PROMPT = _PROMPT_PATH.read_text(encoding="utf-8").strip()


def build_workflow_system_prompt() -> str:
    return WORKFLOW_SYSTEM_PROMPT


def prepare_workflow_message(
    latest_text: str,
    transcript: str,
    message_type: str = "user",
) -> list[dict[str, str]]:
    history = transcript.strip() or "None"
    tag = "new_agent_message" if message_type == "agent" else "new_user_message"
    content = (
        f"<conversation_history>\n{history}\n</conversation_history>\n\n"
        f"<{tag}>\n{latest_text.strip()}\n</{tag}>"
    )
    return [{"role": "user", "content": content}]
