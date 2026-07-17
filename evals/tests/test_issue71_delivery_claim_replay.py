from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from openmagic_evals.harness import prepare_synthetic_renewal_start, renewal_context
from openmagic_runtime.delivery import ClaimedDelivery


def test_delivery_claim_replay_binds_worker_and_serializes_concurrent_replay() -> None:
    with renewal_context() as (_database_url, application, threads):
        command = prepare_synthetic_renewal_start(application, threads, 71_001)
        application.start_renewal_outreach(command)
        application.run_workflow_worker_once(worker_id="delivery-replay-facts")
        application.run_workflow_worker_once(worker_id="delivery-replay-draft")
        claim_request_id = uuid4()

        def claim() -> ClaimedDelivery | None:
            return application.claim_delivery_attempt(
                worker_id="delivery-replay-worker",
                claim_request_id=claim_request_id,
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            claims = tuple(executor.map(lambda _index: claim(), range(2)))

        assert claims[0] is not None
        assert claims[0] == claims[1]
        with pytest.raises(ValueError, match="conflicting input"):
            application.claim_delivery_attempt(
                worker_id="different-delivery-worker",
                claim_request_id=claim_request_id,
            )

        exact_replay = application.claim_delivery_attempt(
            worker_id="delivery-replay-worker",
            claim_request_id=claim_request_id,
        )
        assert exact_replay == claims[0]
