from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from openmagic_runtime._delivery_contracts import ClaimDelivery, ClaimedDelivery
from openmagic_runtime._persistence.delivery_records import (
    DeliveredMessage,
    RuntimeDeliveryAttemptEvidence,
)
from openmagic_runtime._persistence.delivery_work_models import (
    ClaimedDeliveryRecord,
    DeliveryAttemptAuthorityRecord,
    DeliveryRecord,
    retry_policy,
)
from openmagic_runtime.kernel._persistence.work_authority import AttemptAuthorityRecord
from openmagic_runtime.kernel._persistence.work_claims import (
    ClaimCandidateRecord,
    decode_claimed_attempt_receipt,
)
from openmagic_runtime.kernel._work_contracts import ClaimedAttempt, ClaimWork


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
        RuntimeDeliveryAttemptEvidence.decode(
            {
                "delivery_attempt_id": uuid4(),
                "worker_id": 1,
                "state": "running",
            }
        )


def test_public_claim_contracts_reject_invalid_authority_shapes() -> None:
    with pytest.raises(ValueError, match="worker"):
        ClaimWork(uuid4(), "", ("executor",))
    with pytest.raises(ValueError, match="executor"):
        ClaimWork(uuid4(), "worker", ())
    with pytest.raises(ValueError, match="worker"):
        ClaimDelivery(uuid4(), "")
    with pytest.raises(ValueError, match="positive"):
        ClaimedAttempt(uuid4(), uuid4(), uuid4(), 0, "step", "executor", 1, {})
    with pytest.raises(ValueError, match="positive"):
        ClaimedDelivery(uuid4(), uuid4(), 0, uuid4(), {"kind": "draft"}, 0)


def test_attempt_claim_records_reject_coerced_database_and_replay_values() -> None:
    with pytest.raises(RuntimeError, match="invalid durable representation"):
        ClaimCandidateRecord.decode({"step_id": str(uuid4()), "template_key": "draft", "input": {}})
    with pytest.raises(RuntimeError, match="invalid durable representation"):
        decode_claimed_attempt_receipt(
            {
                "instance_id": str(uuid4()).upper(),
                "step_id": str(uuid4()),
                "attempt_id": str(uuid4()),
                "attempt_number": True,
                "template_key": "draft",
                "executor_key": "agent",
                "lease_seconds": 30,
                "input": {},
            }
        )


def test_delivery_claim_record_rejects_boolean_attempt_number() -> None:
    with pytest.raises(RuntimeError, match="invalid durable representation"):
        ClaimedDeliveryRecord.decode(
            {
                "delivery_id": uuid4(),
                "attempt_number": True,
                "thread_id": uuid4(),
                "content_descriptor": {"kind": "renewal"},
                "context_through_sequence": 0,
            }
        )
