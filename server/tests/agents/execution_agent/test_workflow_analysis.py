from __future__ import annotations

import json
from uuid import uuid4

import pytest

from server.agents.execution_agent import workflow_analysis
from server.agents.execution_agent.workflow_analysis import FreshInsuranceWorkExecutionAgent
from server.config import Settings


async def test_fresh_insurance_work_runtime_publishes_bounded_result(
    monkeypatch: pytest.MonkeyPatch,
):
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
                                    "name": "publish_work_result",
                                    "arguments": json.dumps(
                                        {
                                            "title": "Claim facts extracted",
                                            "summary": "A reviewer should verify the incident date.",
                                        }
                                    ),
                                }
                            }
                        ]
                    }
                }
            ]
        }

    monkeypatch.setattr(workflow_analysis, "request_chat_completion", fake_completion)
    runtime = FreshInsuranceWorkExecutionAgent(
        uuid4(),
        Settings(openrouter_api_key="test-key"),
    )
    execution_input: dict[str, object] = {
        "task_type": "extract_claim_facts",
        "subject": "CLM-123",
        "context": "A reported incident needs review.",
    }

    result = await runtime.execute(execution_input)

    assert result.outcome == "succeeded"
    assert result.data == {
        "title": "Claim facts extracted",
        "summary": "A reviewer should verify the incident date.",
    }
    assert captured["messages"] == [
        {"role": "user", "content": json.dumps(execution_input, sort_keys=True)}
    ]
    with pytest.raises(RuntimeError):
        await runtime.execute(execution_input)


async def test_invalid_insurance_work_output_is_a_typed_failure(
    monkeypatch: pytest.MonkeyPatch,
):
    async def fake_completion(**_kwargs):
        return {
            "choices": [
                {
                    "message": {
                        "tool_calls": [
                            {
                                "function": {
                                    "name": "publish_work_result",
                                    "arguments": json.dumps({"title": "Missing summary"}),
                                }
                            }
                        ]
                    }
                }
            ]
        }

    monkeypatch.setattr(workflow_analysis, "request_chat_completion", fake_completion)
    runtime = FreshInsuranceWorkExecutionAgent(
        uuid4(),
        Settings(openrouter_api_key="test-key"),
    )

    result = await runtime.execute(
        {
            "task_type": "review_policy_coverage",
            "subject": "POL-123",
            "context": "Review open policy questions.",
        }
    )

    assert result.outcome == "failed"
    assert result.error == {"code": "invalid_work_output", "validation_errors": 1}
