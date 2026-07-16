from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from openmagic_runtime._persistence.delivery_records import (
    DeliveredMessage,
    RuntimeDeliveryEvidence,
)
from openmagic_runtime._persistence.delivery_work_models import (
    DeliveryAttemptAuthorityRecord,
    DeliveryRecord,
    retry_policy,
)
from openmagic_runtime.kernel._persistence.work_authority import AttemptAuthorityRecord


def test_delivery_retry_policy_rejects_coerced_durable_values() -> None:
    with pytest.raises(RuntimeError, match="invalid durable representation"):
        retry_policy(
            {
                "version": "1",
                "max_attempts": 2,
                "delays_seconds": [0],
                "lease_seconds": 30,
                "retryable_failure_classes": ["transient"],
                "terminal_failure_classes": ["terminal"],
            }
        )


def test_delivery_attempt_authority_rejects_open_state_and_boolean_coercion() -> None:
    record = {"state": "running", "worker_id": "worker", "lease_valid": "false"}

    with pytest.raises(RuntimeError, match="invalid durable representation"):
        DeliveryAttemptAuthorityRecord.decode(record)

    record["state"] = "unknown"
    record["lease_valid"] = True
    with pytest.raises(RuntimeError, match="invalid state"):
        DeliveryAttemptAuthorityRecord.decode(record)


def test_completed_attempt_authority_requires_accepted_result() -> None:
    now = datetime.now(UTC)

    with pytest.raises(RuntimeError, match="missing its accepted result"):
        AttemptAuthorityRecord.decode(
            {
                "state": "completed",
                "worker_id": "worker",
                "lease_valid": False,
                "deadline_valid": True,
                "observation": None,
                "observation_digest": None,
                "instance_id": uuid4(),
                "step_id": uuid4(),
                "attempt_number": 1,
                "template_key": "draft",
                "input": {},
                "lease_expires_at": now,
                "checked_at": now,
            }
        )


def test_attempt_authority_rejects_scalar_and_boolean_coercion() -> None:
    now = datetime.now(UTC)
    record = {
        "state": "leased",
        "worker_id": 7,
        "lease_valid": "false",
        "deadline_valid": "false",
        "observation": None,
        "observation_digest": None,
        "instance_id": uuid4(),
        "step_id": uuid4(),
        "attempt_number": 1,
        "template_key": 9,
        "input": {},
        "lease_expires_at": now,
        "checked_at": now,
    }

    with pytest.raises(RuntimeError, match="invalid durable representation"):
        AttemptAuthorityRecord.decode(record)


def test_delivery_record_requires_exact_nonempty_message_author() -> None:
    with pytest.raises(RuntimeError, match="Message author"):
        DeliveryRecord.decode(
            {
                "thread_id": uuid4(),
                "status": "pending",
                "successful_attempt_id": None,
                "message_author": {"kind": "agent"},
                "message_content": "Review renewal",
            }
        )


def test_delivery_read_records_reject_scalar_coercion() -> None:
    with pytest.raises(RuntimeError, match="invalid durable representation"):
        DeliveredMessage.decode(
            {
                "message_id": uuid4(),
                "thread_id": uuid4(),
                "sequence": "1",
                "content": "Review renewal",
                "source_kind": "delivery",
                "source_id": uuid4(),
            }
        )

    with pytest.raises(RuntimeError, match="invalid durable representation"):
        RuntimeDeliveryEvidence.decode(
            {
                "delivery_id": uuid4(),
                "status": "pending",
                "delivered_message_id": None,
                "attempt_states": ("running",),
            }
        )
