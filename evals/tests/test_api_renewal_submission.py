from __future__ import annotations

import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from uuid import UUID, uuid4

import psycopg
import pytest
from example_insurance.migrations import apply_migrations
from example_insurance.renewal_commands import StartRenewalOutreach, StartRenewalOutreachInput
from example_insurance.renewal_submission import (
    RenewalSubmission,
    RenewalSubmissionApplication,
)
from example_insurance.renewals import ExampleInsurance, RenewalFacts
from openmagic_api.renewals import StartRenewalRequest, submit_renewal
from openmagic_evals.harness._postgres import postgres_container
from openmagic_runtime.commands import Actor, Cause, IdempotencyConflict, StateConflict
from openmagic_runtime.threads import CreateThread, ThreadStore
from psycopg.conninfo import make_conninfo


def _submission() -> RenewalSubmission:
    thread_id = uuid4()
    policy_id = uuid4()
    facts = RenewalFacts(
        policy_id=policy_id,
        policy_number="API-ATOMIC-001",
        policyholder_name="Morgan Hale",
        policyholder_email="morgan@example.test",
        renewal_date="2027-03-01",
        expiring_premium_cents=182_500,
    )
    return RenewalSubmission(
        thread=CreateThread(
            thread_id=thread_id,
            channel_kind="email",
            channel_reference=facts.policyholder_email,
        ),
        facts=facts,
        command=StartRenewalOutreach(
            command_id=uuid4(),
            actor=Actor("party", str(uuid4())),
            cause=Cause("message", str(uuid4())),
            input=StartRenewalOutreachInput(
                workflow_id=uuid4(),
                thread_id=thread_id,
                policy_id=policy_id,
                policy_number=facts.policy_number,
                policyholder_name=facts.policyholder_name,
                policyholder_email=facts.policyholder_email,
                renewal_date=facts.renewal_date,
                expiring_premium_cents=facts.expiring_premium_cents,
            ),
        ),
    )


def _facts_revision(database_url: str, policy_id: UUID) -> tuple[int, str]:
    with psycopg.connect(database_url) as connection, connection.transaction():
        row = connection.execute(
            "SELECT revision, policyholder_name "
            "FROM example_insurance.policy_renewal_facts WHERE policy_id = %s",
            (policy_id,),
        ).fetchone()
    assert row is not None
    return int(row[0]), str(row[1])


def _api_request(submission: RenewalSubmission) -> StartRenewalRequest:
    command = submission.command
    value = command.input
    return StartRenewalRequest(
        command_id=command.command_id,
        workflow_id=value.workflow_id,
        thread_id=value.thread_id,
        policy_id=value.policy_id,
        actor_id=UUID(command.actor.identifier),
        cause_id=UUID(command.cause.identifier),
        policy_number=value.policy_number,
        policyholder_name=value.policyholder_name,
        policyholder_email=value.policyholder_email,
        renewal_date=value.renewal_date,
        expiring_premium_cents=value.expiring_premium_cents,
    )


def test_api_exact_replay_does_not_change_provisioned_authority() -> None:
    with postgres_container(database_name=f"openmagic_test_{uuid4().hex}") as postgres:
        database_url = postgres.get_connection_url(driver=None)
        apply_migrations(database_url)
        submission = _submission()
        request = _api_request(submission)

        response = submit_renewal(database_url=database_url, request=request)
        replay = submit_renewal(database_url=database_url, request=request)

        assert replay == response
        assert _facts_revision(database_url, submission.facts.policy_id) == (1, "Morgan Hale")
        thread = ThreadStore(database_url=database_url).read(submission.thread.thread_id)
        assert thread.channel_reference == "morgan@example.test"


def test_conflicting_replay_rolls_back_provisioning_changes() -> None:
    with postgres_container(database_name=f"openmagic_test_{uuid4().hex}") as postgres:
        database_url = postgres.get_connection_url(driver=None)
        apply_migrations(database_url)
        application = RenewalSubmissionApplication(database_url=database_url)
        application.prepare()
        submission = _submission()
        application.provision_and_start_renewal(submission)
        conflicting_facts = replace(submission.facts, policyholder_name="Changed Name")
        conflicting = replace(
            submission,
            facts=conflicting_facts,
            command=replace(
                submission.command,
                input=replace(
                    submission.command.input,
                    policyholder_name=conflicting_facts.policyholder_name,
                ),
            ),
        )

        with pytest.raises(IdempotencyConflict):
            application.provision_and_start_renewal(conflicting)

        assert _facts_revision(database_url, submission.facts.policy_id) == (1, "Morgan Hale")


def test_concurrent_exact_submissions_commit_one_authority_revision() -> None:
    with postgres_container(database_name=f"openmagic_test_{uuid4().hex}") as postgres:
        database_url = postgres.get_connection_url(driver=None)
        apply_migrations(database_url)
        application = RenewalSubmissionApplication(database_url=database_url)
        application.prepare()
        submission = _submission()

        with ThreadPoolExecutor(max_workers=8) as executor:
            receipts = tuple(
                executor.map(
                    lambda _: application.provision_and_start_renewal(submission),
                    range(8),
                )
            )

        assert all(receipt == receipts[0] for receipt in receipts)
        assert _facts_revision(database_url, submission.facts.policy_id) == (1, "Morgan Hale")


def test_concurrent_threads_cannot_claim_one_channel_reference() -> None:
    with postgres_container(database_name=f"openmagic_test_{uuid4().hex}") as postgres:
        database_url = postgres.get_connection_url(driver=None)
        apply_migrations(database_url)
        application = RenewalSubmissionApplication(database_url=database_url)
        application.prepare()
        submissions = (_submission(), _submission())

        def submit(submission: RenewalSubmission) -> str:
            try:
                application.provision_and_start_renewal(submission)
            except StateConflict:
                return "channel_conflict"
            return "committed"

        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = tuple(executor.map(submit, submissions))

        assert sorted(outcomes) == ["channel_conflict", "committed"]
        with psycopg.connect(database_url) as connection, connection.transaction():
            durable_counts = connection.execute(
                "SELECT "
                "(SELECT count(*) FROM openmagic_runtime.threads "
                "WHERE channel_kind = 'email' AND channel_reference = 'morgan@example.test'), "
                "(SELECT count(*) FROM example_insurance.policy_renewal_facts "
                "WHERE policy_id = ANY(%s)), "
                "(SELECT count(*) FROM openmagic_runtime.command_receipts "
                "WHERE command_id = ANY(%s))",
                (
                    [submission.facts.policy_id for submission in submissions],
                    [submission.command.command_id for submission in submissions],
                ),
            ).fetchone()
        assert durable_counts == (1, 1, 1)


def test_process_loss_rolls_back_thread_facts_and_command_together() -> None:
    with postgres_container(database_name=f"openmagic_test_{uuid4().hex}") as postgres:
        database_url = postgres.get_connection_url(driver=None)
        apply_migrations(database_url)
        application = ExampleInsurance(database_url=database_url)
        application.prepare()
        submission = _submission()
        application.replace_renewal_facts(
            replace(submission.facts, policyholder_name="Original Name")
        )
        process_url = make_conninfo(
            database_url,
            application_name="atomic_submission_loss",
        )

        with psycopg.connect(database_url) as lock_connection, lock_connection.transaction():
            lock_connection.execute(
                "SELECT policy_id FROM example_insurance.policy_renewal_facts "
                "WHERE policy_id = %s FOR UPDATE",
                (submission.facts.policy_id,),
            )
            process = subprocess.Popen(
                (
                    sys.executable,
                    str(
                        __file__.replace(
                            "test_api_renewal_submission.py",
                            "support/issue71_atomic_api_process.py",
                        )
                    ),
                    process_url,
                    _api_request(submission).model_dump_json(),
                ),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                exit_code = process.poll()
                if exit_code is not None:
                    pytest.fail(
                        "Fresh submission process exited before its PostgreSQL lock barrier: "
                        f"{exit_code}"
                    )
                lock_connection.execute("SELECT pg_stat_clear_snapshot()")
                blocked = lock_connection.execute(
                    "SELECT EXISTS ("
                    "SELECT 1 FROM pg_stat_activity "
                    "WHERE pid <> pg_backend_pid() "
                    "AND query LIKE 'INSERT INTO example_insurance.policy_renewal_facts%' "
                    "AND wait_event_type = 'Lock')"
                ).fetchone()
                if blocked is not None and bool(blocked[0]):
                    break
                time.sleep(0.02)
            else:
                lock_connection.execute("SELECT pg_stat_clear_snapshot()")
                activities = lock_connection.execute(
                    "SELECT application_name, state, wait_event_type, wait_event, left(query, 120) "
                    "FROM pg_stat_activity WHERE pid <> pg_backend_pid()"
                ).fetchall()
                process.kill()
                process.wait(timeout=5)
                pytest.fail(
                    "Fresh submission process did not reach its PostgreSQL lock barrier: "
                    f"{activities!r}"
                )
            process.kill()
            process.wait(timeout=5)
            assert process.returncode is not None

        assert _facts_revision(database_url, submission.facts.policy_id) == (1, "Original Name")
        with pytest.raises(KeyError):
            ThreadStore(database_url=database_url).read(submission.thread.thread_id)
        with psycopg.connect(database_url) as connection, connection.transaction():
            receipt = connection.execute(
                "SELECT 1 FROM openmagic_runtime.command_receipts WHERE command_id = %s",
                (submission.command.command_id,),
            ).fetchone()
        assert receipt is None
