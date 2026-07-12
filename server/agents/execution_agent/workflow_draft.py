"""History-free Execution Agent for one claimed Draft Run."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from typing import Any
from uuid import UUID

from pydantic import ValidationError

from server.config import Settings, get_settings
from server.openrouter_client import request_chat_completion
from server.workflows import DraftRenewalEmailOutput, RunResult

_SYSTEM_PROMPT = """You draft one concise insurance renewal email.
Use only the supplied recipient name and renewal period. Do not claim coverage,
pricing, or policy facts that were not supplied. Call publish_draft exactly once
with the complete subject and body. You have no conversation history."""

_PUBLISH_DRAFT_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "publish_draft",
        "description": "Publish the complete renewal email draft.",
        "parameters": DraftRenewalEmailOutput.model_json_schema(),
    },
}


class FreshDraftExecutionAgent:
    """A single-use LLM runtime with no inherited logs or broad tools."""

    def __init__(self, runtime_instance_id: UUID, settings: Settings) -> None:
        self.runtime_instance_id = runtime_instance_id
        self._settings = settings
        self._used = False

    async def execute(self, execution_input: dict[str, object]) -> RunResult:
        if self._used:
            raise RuntimeError("A Draft execution runtime may execute only once")
        self._used = True
        try:
            response = await request_chat_completion(
                model=self._settings.execution_agent_model,
                messages=[
                    {
                        "role": "user",
                        "content": json.dumps(execution_input, sort_keys=True),
                    }
                ],
                system=_SYSTEM_PROMPT,
                api_key=self._settings.openrouter_api_key,
                tools=[_PUBLISH_DRAFT_TOOL],
            )
            draft = self._extract_draft(response)
        except ValidationError as exc:
            return RunResult(
                outcome="failed",
                evidence=({"type": "agent_output_rejected"},),
                error={"code": "invalid_draft_output", "validation_errors": exc.error_count()},
            )
        except (AttributeError, IndexError, KeyError, TypeError, ValueError):
            return RunResult(
                outcome="failed",
                evidence=({"type": "agent_output_rejected"},),
                error={"code": "invalid_draft_output"},
            )
        except Exception:
            return RunResult(
                outcome="failed",
                evidence=({"type": "executor_failed"},),
                error={"code": "executor_unavailable"},
            )
        return RunResult(
            outcome="succeeded",
            data=draft.model_dump(mode="json"),
            evidence=({"type": "agent_output_validated"},),
        )

    @staticmethod
    def _extract_draft(response: dict[str, Any]) -> DraftRenewalEmailOutput:
        message = response["choices"][0]["message"]
        calls = message.get("tool_calls") or []
        publish_calls = [
            call for call in calls if call.get("function", {}).get("name") == "publish_draft"
        ]
        if len(publish_calls) != 1:
            raise ValueError("Execution Agent must call publish_draft exactly once")
        arguments = publish_calls[0]["function"].get("arguments")
        if isinstance(arguments, str):
            arguments = json.loads(arguments)
        return DraftRenewalEmailOutput.model_validate(arguments)


class FreshDraftExecutionAgentFactory:
    """Create a new disposable runtime for every Draft Run."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()

    @asynccontextmanager
    async def create(self, runtime_instance_id: UUID):
        runtime = FreshDraftExecutionAgent(runtime_instance_id, self._settings)
        try:
            yield runtime
        finally:
            del runtime
