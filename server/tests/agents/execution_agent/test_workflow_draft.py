from __future__ import annotations

import json
from uuid import uuid4

import pytest

from server.agents.execution_agent import workflow_draft
from server.agents.execution_agent.workflow_draft import FreshDraftExecutionAgent
from server.config import Settings


async def test_fresh_draft_runtime_sends_only_bounded_input(monkeypatch: pytest.MonkeyPatch):
    captured: dict[str, object] = {}

    async def fake_completion(**kwargs):
        captured.update(kwargs)
        return {
            "choices": [
                {
                    "message": {
                        "tool_calls": [
                            {
                                "function": {
                                    "name": "publish_draft",
                                    "arguments": json.dumps(
                                        {
                                            "subject": "Your 2026 policy renewal",
                                            "body": "Hello John, let's review your renewal.",
                                        }
                                    ),
                                }
                            }
                        ]
                    }
                }
            ]
        }

    monkeypatch.setattr(workflow_draft, "request_chat_completion", fake_completion)
    runtime = FreshDraftExecutionAgent(
        uuid4(),
        Settings(openrouter_api_key="test-key"),
    )
    execution_input = {"recipient_name": "John Smith", "renewal_period": "2026"}

    result = await runtime.execute(execution_input)

    assert result.outcome == "succeeded"
    assert captured["messages"] == [
        {"role": "user", "content": json.dumps(execution_input, sort_keys=True)}
    ]
    with pytest.raises(RuntimeError):
        await runtime.execute(execution_input)


@pytest.mark.parametrize(
    "response",
    [
        {"choices": []},
        {"choices": [{"message": None}]},
        {"choices": [{"message": {"tool_calls": [None]}}]},
    ],
)
async def test_malformed_provider_response_becomes_typed_failure(
    monkeypatch: pytest.MonkeyPatch,
    response: dict[str, object],
):
    async def fake_completion(**_kwargs):
        return response

    monkeypatch.setattr(workflow_draft, "request_chat_completion", fake_completion)
    runtime = FreshDraftExecutionAgent(uuid4(), Settings(openrouter_api_key="test-key"))

    result = await runtime.execute({"recipient_name": "John", "renewal_period": "2026"})

    assert result.outcome == "failed"
    assert result.error == {"code": "invalid_draft_output"}


async def test_transport_failure_becomes_retryable_typed_failure(monkeypatch: pytest.MonkeyPatch):
    async def failed_completion(**_kwargs):
        raise OSError("provider unavailable")

    monkeypatch.setattr(workflow_draft, "request_chat_completion", failed_completion)
    runtime = FreshDraftExecutionAgent(uuid4(), Settings(openrouter_api_key="test-key"))

    result = await runtime.execute({"recipient_name": "John", "renewal_period": "2026"})

    assert result.outcome == "failed"
    assert result.error == {"code": "executor_unavailable"}
