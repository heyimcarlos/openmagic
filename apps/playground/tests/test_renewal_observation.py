from __future__ import annotations

import json
from collections.abc import Callable
from copy import deepcopy
from typing import cast
from uuid import UUID

import pytest
from openmagic_playground.renewal_observation import (
    RenewalProjectionDecodeError,
    decode_renewal_projection,
)

WORKFLOW_ID = "8bd180e8-b7a4-4f99-abce-b2c905c3d18d"


def _projection_document() -> dict[str, object]:
    return {
        "schema_version": "openmagic.evidence.v1",
        "scenario": "renewal_drafting",
        "correlations": {
            "command_id": "8d628223-6906-4b3f-a156-8548a0ae9e9c",
            "workflow_id": WORKFLOW_ID,
            "instance_id": "e0ca458e-3e51-4d08-a7dc-c56560677c4b",
            "thread_id": "e2da93ac-fcca-4026-8ca6-b0342e650152",
            "step_ids": [],
            "attempt_ids": [],
            "agent_run_ids": [],
            "domain_event_ids": [],
            "delivery_ids": [],
            "message_ids": [],
            "draft_agent_run_ids": [],
            "decision_ids": [],
            "signal_ids": [],
            "approval_grant_ids": [],
            "logical_effect_ids": [],
            "effect_evidence_ids": [],
        },
        "outcomes": {
            "workflow_lifecycle": "active",
            "instance_state": "open",
            "step_states": {},
            "attempt_states": [],
            "agent_run_states": [],
            "delivery_attempt_states": [],
            "approval_wait_id": None,
            "approval_wait_state": None,
            "approval_wait_ids": [],
            "approval_wait_states": [],
            "delivery_states": [],
            "domain_events": [
                {
                    "event_id": "de5cff77-c58e-4390-b985-a66b8ea9914d",
                    "event_type": "renewal.outreach.started",
                    "actor": {"kind": "party", "identifier": "party-71"},
                    "cause": {"kind": "message", "identifier": "message-71"},
                }
            ],
            "external_email_effect_count": 0,
            "external_effect_certainties": [],
            "effect_evidence": [],
            "decisions": [],
            "approval_grants": [],
            "external_effects": [],
            "completion_event_count": 0,
        },
        "invariant_violations": [],
        "redacted": True,
    }


def _add_top_level_field(document: dict[str, object]) -> None:
    document["unexpected"] = "drift"


def _add_correlation_field(document: dict[str, object]) -> None:
    correlations = cast(dict[str, object], document["correlations"])
    assert isinstance(correlations, dict)
    correlations["unexpected"] = "drift"


def _remove_outcome_field(document: dict[str, object]) -> None:
    outcomes = cast(dict[str, object], document["outcomes"])
    assert isinstance(outcomes, dict)
    outcomes.pop("instance_state")


def _replace_redaction_type(document: dict[str, object]) -> None:
    document["redacted"] = "true"


def _drift_nested_lineage(document: dict[str, object]) -> None:
    outcomes = cast(dict[str, object], document["outcomes"])
    assert isinstance(outcomes, dict)
    events = cast(list[object], outcomes["domain_events"])
    assert isinstance(events, list)
    event = cast(dict[str, object], events[0])
    assert isinstance(event, dict)
    actor = cast(dict[str, object], event["actor"])
    assert isinstance(actor, dict)
    actor["authority"] = "forbidden"


def test_decoder_returns_typed_versioned_projection() -> None:
    projection = decode_renewal_projection(json.dumps(_projection_document()))

    assert projection.schema_version == "openmagic.evidence.v1"
    assert projection.correlations.workflow_id == UUID(WORKFLOW_ID)
    assert projection.outcomes.approval_wait_state is None
    assert projection.invariant_violations == ()
    assert projection.redacted is True


@pytest.mark.parametrize(
    "mutation",
    [
        _add_top_level_field,
        _add_correlation_field,
        _remove_outcome_field,
        _replace_redaction_type,
        _drift_nested_lineage,
    ],
)
def test_decoder_rejects_malformed_or_drifted_projection(
    mutation: Callable[[dict[str, object]], None],
) -> None:
    document = deepcopy(_projection_document())
    mutation(document)

    with pytest.raises(RenewalProjectionDecodeError):
        decode_renewal_projection(json.dumps(document))
