from __future__ import annotations

import time
from uuid import uuid4

import psycopg
import pytest
from example_insurance.renewals import CancelRenewalOutreach, CancelRenewalOutreachInput
from openmagic_evals.harness import prepare_synthetic_renewal_start, renewal_context
from openmagic_runtime.commands import Cause
from openmagic_runtime.kernel.work import KernelWork, StaleAuthority


def test_lease_authority_boundaries_use_database_time_without_a_grace_period() -> None:
    with renewal_context() as (database_url, application, threads):
        command = prepare_synthetic_renewal_start(application, threads, 10_001)
        application.start_renewal_outreach(command)
        claim = application.claim_workflow_attempt(
            worker_id="lease-boundary",
            claim_request_id=uuid4(),
        )
        assert claim is not None

        with psycopg.connect(database_url) as connection, connection.transaction():
            connection.execute(
                "SELECT pg_sleep(GREATEST(EXTRACT(EPOCH FROM "
                "(lease_expires_at - clock_timestamp())) - 0.05, 0)) "
                "FROM openmagic_runtime.attempts WHERE attempt_id = %s",
                (claim.attempt_id,),
            )
            authority = KernelWork(connection).execution_authority(
                claim,
                worker_id="lease-boundary",
            )
            assert authority.directive == "execute"

        with psycopg.connect(database_url) as connection, connection.transaction():
            connection.execute(
                "SELECT pg_sleep(GREATEST(EXTRACT(EPOCH FROM "
                "(lease_expires_at - clock_timestamp())), 0)) "
                "FROM openmagic_runtime.attempts WHERE attempt_id = %s",
                (claim.attempt_id,),
            )
            with pytest.raises(StaleAuthority, match="stale"):
                KernelWork(connection).execution_authority(
                    claim,
                    worker_id="lease-boundary",
                )

        time.sleep(0.05)
        with (
            psycopg.connect(database_url) as connection,
            connection.transaction(),
            pytest.raises(StaleAuthority, match="stale"),
        ):
            KernelWork(connection).execution_authority(claim, worker_id="lease-boundary")

        assert application.recover_expired_workflow_attempt()
        with (
            psycopg.connect(database_url) as connection,
            connection.transaction(),
            pytest.raises(StaleAuthority, match="stale"),
        ):
            KernelWork(connection).execution_authority(claim, worker_id="lease-boundary")

        cancelled = application.cancel_renewal_outreach(
            CancelRenewalOutreach(
                command_id=uuid4(),
                actor=command.actor,
                cause=Cause("command", str(uuid4())),
                input=CancelRenewalOutreachInput(command.input.workflow_id),
            )
        )
        assert cancelled.result.outcome == "cancelled"

        closure_command = prepare_synthetic_renewal_start(application, threads, 10_002)
        application.start_renewal_outreach(closure_command)
        closure_claim = application.claim_workflow_attempt(
            worker_id="closure-boundary",
            claim_request_id=uuid4(),
        )
        assert closure_claim is not None
        closed = application.cancel_renewal_outreach(
            CancelRenewalOutreach(
                command_id=uuid4(),
                actor=closure_command.actor,
                cause=Cause("command", str(uuid4())),
                input=CancelRenewalOutreachInput(closure_command.input.workflow_id),
            )
        )
        assert closed.result.outcome == "cancelled"
        with (
            psycopg.connect(database_url) as connection,
            connection.transaction(),
            pytest.raises(StaleAuthority, match="stale"),
        ):
            KernelWork(connection).execution_authority(
                closure_claim,
                worker_id="closure-boundary",
            )
