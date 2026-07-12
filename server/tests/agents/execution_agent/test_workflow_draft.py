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
