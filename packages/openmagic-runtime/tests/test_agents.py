from __future__ import annotations

from dataclasses import replace
from uuid import uuid4

import pytest
from openmagic_runtime.agents import (
    AgentAudience,
    AgentConfiguration,
    AgentField,
    AgentRecord,
    AgentRunInput,
    AgentTask,
)


def _input() -> AgentRunInput:
    return AgentRunInput(
        configuration=AgentConfiguration("example.agent", 1, "example.instructions.v1"),
        task=AgentTask(
            "example.task",
            1,
            AgentRecord("example.task_input", 1, (AgentField("value", "exact"),)),
        ),
        thread_id=uuid4(),
        context_through_sequence=0,
        domain_event_context=(),
        audience_context=AgentAudience("workflow_role", "broker"),
        locale="en-CA",
    )


def test_agent_run_input_is_deeply_immutable_and_typed() -> None:
    input = _input()

    assert input.configuration == AgentConfiguration("example.agent", 1, "example.instructions.v1")
    assert input.task.input.value("value") == "exact"
    with pytest.raises(AttributeError):
        input.task.input.fields.append(  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]
            AgentField("other", "value")
        )


def test_agent_contracts_reject_boolean_versions_and_non_string_identifiers() -> None:
    with pytest.raises(ValueError, match="configuration identity"):
        AgentConfiguration("example.agent", True, "example.instructions.v1")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="record schema identity"):
        AgentRecord("example.task_input", True, ())  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="immutable scalar"):
        AgentField(True, "value")  # type: ignore[arg-type]  # ty: ignore[invalid-argument-type]
    with pytest.raises(ValueError, match="Run Input"):
        replace(_input(), context_through_sequence=True)  # type: ignore[arg-type]
