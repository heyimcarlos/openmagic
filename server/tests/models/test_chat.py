from __future__ import annotations

import pytest
from pydantic import ValidationError

from server.models import ChatMessage


def test_history_message_carries_sanitized_workflow_telemetry():
    message = ChatMessage.model_validate(
        {
            "role": "assistant",
            "content": "I started the renewal.",
            "telemetry": {
                "activity_summary": "Found context, advanced 1 Workflow",
                "activity": [
                    {
                        "id": "search",
                        "label": "Searched authorized Workflows",
                        "status": "succeeded",
                    }
                ],
                "workflows": [
                    {
                        "id": "renewal",
                        "title": "John Smith renewal outreach",
                        "status_label": "Waiting for approval",
                        "stages": [
                            {
                                "id": "approval",
                                "kind": "checkpoint",
                                "label": "Exact approval",
                                "status": "waiting",
                            }
                        ],
                    }
                ],
                "reasoning": "must not cross the API contract",
            },
        }
    )

    payload = message.model_dump()
    assert payload["telemetry"] == {
        "activity_summary": "Found context, advanced 1 Workflow",
        "activity": [
            {"id": "search", "label": "Searched authorized Workflows", "status": "succeeded"}
        ],
        "workflows": [
            {
                "id": "renewal",
                "title": "John Smith renewal outreach",
                "status_label": "Waiting for approval",
                "stages": [
                    {
                        "id": "approval",
                        "kind": "checkpoint",
                        "label": "Exact approval",
                        "status": "waiting",
                    }
                ],
            }
        ],
    }


@pytest.mark.parametrize(
    ("kind", "status"),
    [("job", "unavailable"), ("checkpoint", "succeeded")],
)
def test_workflow_stage_rejects_a_status_from_another_row_kind(kind: str, status: str):
    with pytest.raises(ValidationError):
        ChatMessage.model_validate(
            {
                "role": "assistant",
                "content": "I started the renewal.",
                "telemetry": {
                    "activity_summary": "Advanced 1 Workflow",
                    "activity": [],
                    "workflows": [
                        {
                            "id": "renewal",
                            "title": "Renewal outreach",
                            "status_label": "In progress",
                            "stages": [
                                {
                                    "id": "stage",
                                    "kind": kind,
                                    "label": "Renewal step",
                                    "status": status,
                                }
                            ],
                        }
                    ],
                },
            }
        )
