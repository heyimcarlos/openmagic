"""History-free Execution Agent for one bounded insurance analysis Run."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from typing import Any
from uuid import UUID

from pydantic import ValidationError

from server.config import Settings, get_settings
from server.openrouter_client import request_chat_completion
from server.workflows import InsuranceTaskOutput, RunResult

_SYSTEM_PROMPT = """You complete one bounded insurance analysis task.
Use only the supplied subject and context. Do not invent policy, coverage,
liability, or claim facts. Call publish_work_result exactly once with a concise
title and summary. You have no conversation history."""

_PUBLISH_RESULT_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "publish_work_result",
        "description": "Publish the complete result of this bounded insurance task.",
        "parameters": InsuranceTaskOutput.model_json_schema(),
    },
}


class FreshInsuranceWorkExecutionAgent:
    """A single-use LLM runtime with no inherited interaction context."""

    def __init__(self, runtime_instance_id: UUID, settings: Settings) -> None:
        self.runtime_instance_id = runtime_instance_id
        self._settings = settings
        self._used = False

    async def execute(self, execution_input: dict[str, object]) -> RunResult:
        if self._used:
            raise RuntimeError("An insurance work runtime may execute only once")
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
                tools=[_PUBLISH_RESULT_TOOL],
            )
            result = self._extract_result(response)
        except ValidationError as exc:
            return RunResult(
                outcome="failed",
                evidence=({"type": "agent_output_rejected"},),
                error={"code": "invalid_work_output", "validation_errors": exc.error_count()},
            )
        except (AttributeError, IndexError, KeyError, TypeError, ValueError):
            return RunResult(
                outcome="failed",
                evidence=({"type": "agent_output_rejected"},),
                error={"code": "invalid_work_output"},
            )
        except Exception:
            return RunResult(
                outcome="failed",
                evidence=({"type": "executor_failed"},),
                error={"code": "executor_unavailable"},
            )
        return RunResult(
            outcome="succeeded",
            data=result.model_dump(mode="json"),
            evidence=({"type": "agent_output_validated"},),
        )

    @staticmethod
    def _extract_result(response: dict[str, Any]) -> InsuranceTaskOutput:
        message = response["choices"][0]["message"]
        calls = message.get("tool_calls") or []
        publish_calls = [
            call
            for call in calls
            if call.get("function", {}).get("name") == "publish_work_result"
        ]
        if len(publish_calls) != 1:
            raise ValueError("Execution Agent must call publish_work_result exactly once")
        arguments = publish_calls[0]["function"].get("arguments")
        if isinstance(arguments, str):
            arguments = json.loads(arguments)
        return InsuranceTaskOutput.model_validate(arguments)


class FreshInsuranceWorkExecutionAgentFactory:
    """Create a new disposable runtime for every insurance work Run."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()

    @asynccontextmanager
    async def create(self, runtime_instance_id: UUID):
        runtime = FreshInsuranceWorkExecutionAgent(runtime_instance_id, self._settings)
        try:
            yield runtime
        finally:
            del runtime


__all__ = ["FreshInsuranceWorkExecutionAgentFactory"]
