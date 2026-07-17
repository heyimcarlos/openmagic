from __future__ import annotations

import json
from uuid import UUID, uuid4

import psycopg
import pytest
from example_insurance.renewals import CancelRenewalOutreach, CancelRenewalOutreachInput
from openmagic_evals.evidence.case_recording import record_renewal_case
from openmagic_evals.harness import prepare_synthetic_renewal_start, renewal_context
from openmagic_runtime.commands import Cause
from openmagic_runtime.evidence import RuntimeEvidenceReader
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
            before = RuntimeEvidenceReader(connection).attempt_lease(claim.attempt_id)
            authority = KernelWork(connection).execution_authority(
                claim,
                worker_id="lease-boundary",
            )
            assert authority.directive == "execute"
            assert before.lease_valid
            assert before.checked_at < before.lease_expires_at
            before_remaining_ms = (
                before.lease_expires_at - before.checked_at
            ).total_seconds() * 1000
            assert 0 < before_remaining_ms <= 75
        with psycopg.connect(database_url) as connection, connection.transaction():
            connection.execute(
                "SELECT pg_sleep(GREATEST(EXTRACT(EPOCH FROM "
                "(lease_expires_at - clock_timestamp())), 0)) "
                "FROM openmagic_runtime.attempts WHERE attempt_id = %s",
                (claim.attempt_id,),
            )
            boundary = RuntimeEvidenceReader(connection).attempt_lease(claim.attempt_id)
            with pytest.raises(StaleAuthority, match="stale"):
                KernelWork(connection).execution_authority(
                    claim,
                    worker_id="lease-boundary",
                )
            assert not boundary.lease_valid
            assert boundary.checked_at >= boundary.lease_expires_at
            expiry_overshoot_ms = (
                boundary.checked_at - boundary.lease_expires_at
            ).total_seconds() * 1000
            assert 0 <= expiry_overshoot_ms <= 25
        record_renewal_case(
            case_id="lease.authoritative-time",
            scenario_id="before-expiry",
            application=application,
            database_url=database_url,
            workflow_id=command.input.workflow_id,
            document={
                "directive": authority.directive,
                "attempt_id": str(claim.attempt_id),
                "database_checked_at": before.checked_at.isoformat(),
                "lease_expires_at": before.lease_expires_at.isoformat(),
                "milliseconds_before_expiry": before_remaining_ms,
            },
            worker_ids=("lease-boundary",),
        )
        record_renewal_case(
            case_id="lease.authoritative-time",
            scenario_id="at-expiry",
            application=application,
            database_url=database_url,
            workflow_id=command.input.workflow_id,
            document={
                "rejected": True,
                "attempt_id": str(claim.attempt_id),
                "database_checked_at": boundary.checked_at.isoformat(),
                "lease_expires_at": boundary.lease_expires_at.isoformat(),
                "database_time_relation": "at-or-after-expiry",
                "expiry_overshoot_ms": expiry_overshoot_ms,
            },
            worker_ids=("lease-boundary",),
        )

        with psycopg.connect(database_url) as connection, connection.transaction():
            connection.execute(
                "SELECT pg_sleep(GREATEST(EXTRACT(EPOCH FROM "
                "(lease_expires_at + interval '50 milliseconds' - clock_timestamp())), 0)) "
                "FROM openmagic_runtime.attempts WHERE attempt_id = %s",
                (claim.attempt_id,),
            )
            after = RuntimeEvidenceReader(connection).attempt_lease(claim.attempt_id)
            with pytest.raises(StaleAuthority, match="stale"):
                KernelWork(connection).execution_authority(
                    claim,
                    worker_id="lease-boundary",
                )
            assert not after.lease_valid
            milliseconds_after_expiry = (
                after.checked_at - after.lease_expires_at
            ).total_seconds() * 1000
            assert milliseconds_after_expiry >= 50
        record_renewal_case(
            case_id="lease.authoritative-time",
            scenario_id="after-expiry",
            application=application,
            database_url=database_url,
            workflow_id=command.input.workflow_id,
            document={
                "rejected": True,
                "attempt_id": str(claim.attempt_id),
                "database_checked_at": after.checked_at.isoformat(),
                "lease_expires_at": after.lease_expires_at.isoformat(),
                "milliseconds_after_expiry": milliseconds_after_expiry,
            },
            worker_ids=("lease-boundary",),
        )

        assert application.recover_expired_workflow_attempt()
        with (
            psycopg.connect(database_url) as connection,
            connection.transaction(),
            pytest.raises(StaleAuthority, match="stale"),
        ):
            KernelWork(connection).execution_authority(claim, worker_id="lease-boundary")
        record_renewal_case(
            case_id="lease.authoritative-time",
            scenario_id="after-abandonment",
            application=application,
            database_url=database_url,
            workflow_id=command.input.workflow_id,
            document={"rejected": True, "attempt_id": str(claim.attempt_id)},
            worker_ids=("lease-boundary",),
        )

        cancelled = application.cancel_renewal_outreach(
            CancelRenewalOutreach(
                command_id=uuid4(),
                actor=command.actor,
                cause=Cause("command", str(uuid4())),
                input=CancelRenewalOutreachInput(command.input.workflow_id),
            )
        )
        assert cancelled.result.outcome == "cancelled"
        cancellation_evidence = json.loads(
            application.renewal_evidence_json(command.input.workflow_id)
        )
        cancellation_event = next(
            event
            for event in cancellation_evidence["outcomes"]["domain_events"]
            if event["event_type"] == "renewal.outreach.cancelled"
        )
        assert cancellation_event["cause"] == {
            "kind": "command",
            "identifier": str(cancelled.command_id),
        }
        record_renewal_case(
            case_id="domain-event.atomic-correlation",
            scenario_id="cancellation",
            application=application,
            database_url=database_url,
            workflow_id=command.input.workflow_id,
            document={
                "outcome": cancelled.result.outcome,
                "event_id": cancellation_event["event_id"],
                "event_type": cancellation_event["event_type"],
                "source_command_id": str(cancelled.command_id),
            },
            additional_command_ids=(cancelled.command_id,),
            domain_event_ids=(UUID(cancellation_event["event_id"]),),
        )

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
        record_renewal_case(
            case_id="lease.authoritative-time",
            scenario_id="after-instance-close",
            application=application,
            database_url=database_url,
            workflow_id=closure_command.input.workflow_id,
            document={
                "rejected": True,
                "attempt_id": str(closure_claim.attempt_id),
                "closure_outcome": closed.result.outcome,
            },
            worker_ids=("closure-boundary",),
        )
