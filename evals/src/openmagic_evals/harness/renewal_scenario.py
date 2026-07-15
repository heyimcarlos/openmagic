"""Public scenario helpers for installed renewal evaluations."""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from uuid import UUID, uuid4

import psycopg
from example_insurance.migrations import apply_migrations
from example_insurance.renewals import (
    ApproveRenewalDraft,
    ApproveRenewalDraftInput,
    ExampleInsurance,
    RenewalFacts,
    StartRenewalOutreach,
    StartRenewalOutreachInput,
)
from openmagic_runtime.commands import Actor, Cause
from openmagic_runtime.threads import CreateThread, ThreadStore

from openmagic_evals.harness._postgres import postgres_container


@contextmanager
def renewal_context(
    *,
    verification_code_secret: bytes | None = None,
    challenge_ttl_seconds: int = 600,
    session_ttl_seconds: int = 900,
) -> Iterator[tuple[str, ExampleInsurance, ThreadStore]]:
    """Provide a migrated PostgreSQL renewal application through public interfaces."""
    with postgres_container(database_name=f"openmagic_test_{uuid4().hex}") as postgres:
        database_url = postgres.get_connection_url(driver=None)
        apply_migrations(database_url)
        application = ExampleInsurance(
            database_url=database_url,
            verification_code_secret=verification_code_secret,
            challenge_ttl_seconds=challenge_ttl_seconds,
            session_ttl_seconds=session_ttl_seconds,
        )
        application.prepare()
        yield database_url, application, ThreadStore(database_url=database_url)


def prepare_renewal_approval(
    application: ExampleInsurance,
    threads: ThreadStore,
    *,
    deliver: bool = True,
    actor: Actor | None = None,
    thread_id: UUID | None = None,
) -> tuple[StartRenewalOutreach, Actor]:
    """Start a renewal and advance it to its durable approval Wait."""
    if thread_id is None:
        thread = threads.create(CreateThread(uuid4(), "email", f"broker-{uuid4()}"))
        thread_id = thread.thread_id
    if actor is None:
        actor = Actor(kind="party", identifier=str(uuid4()))
    command = StartRenewalOutreach(
        command_id=uuid4(),
        actor=actor,
        cause=Cause(kind="message", identifier=str(uuid4())),
        input=StartRenewalOutreachInput(
            workflow_id=uuid4(),
            thread_id=thread_id,
            policy_id=uuid4(),
            policy_number="OM-69",
            policyholder_name="Avery Chen",
            policyholder_email="avery@example.test",
            renewal_date="2027-12-31",
            expiring_premium_cents=250_000,
        ),
    )
    application.replace_renewal_facts(
        RenewalFacts(
            policy_id=command.input.policy_id,
            policy_number=command.input.policy_number,
            policyholder_name=command.input.policyholder_name,
            policyholder_email=command.input.policyholder_email,
            renewal_date=command.input.renewal_date,
            expiring_premium_cents=command.input.expiring_premium_cents,
        )
    )
    application.start_renewal_outreach(command)
    application.run_workflow_worker_once(worker_id="facts")
    application.run_workflow_worker_once(worker_id="draft")
    if deliver:
        application.run_delivery_worker_once(worker_id="delivery")
    return command, actor


def approve_renewal(
    application: ExampleInsurance,
    command: StartRenewalOutreach,
    actor: Actor,
) -> UUID:
    """Approve the currently presented draft and return its effect Step ID."""
    presentation = application.renewal_approval_presentation(command.input.workflow_id)
    receipt = application.approve_renewal_draft(
        ApproveRenewalDraft(
            command_id=uuid4(),
            actor=actor,
            cause=Cause(kind="message", identifier=str(uuid4())),
            input=ApproveRenewalDraftInput(
                workflow_id=command.input.workflow_id,
                wait_id=presentation.wait_id,
                draft_id=presentation.draft_id,
                message_id=presentation.message_id,
                thread_sequence=presentation.thread_sequence,
                message_fingerprint=presentation.message_fingerprint,
                presentation_fingerprint=presentation.presentation_fingerprint,
                proposed_effect=presentation.proposed_effect,
            ),
        )
    )
    if receipt.result.effect_step_id is None:
        raise AssertionError("Approval did not create an effect Step")
    return receipt.result.effect_step_id


def wait_for_renewal_completion(
    application: ExampleInsurance,
    workflow_id: UUID,
) -> dict[str, Any]:
    """Wait for the deployed workers to complete a renewal Workflow."""
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        evidence = json.loads(application.renewal_evidence_json(workflow_id))
        if evidence["outcomes"]["workflow_lifecycle"] == "completed":
            return evidence
        time.sleep(0.02)
    raise AssertionError("Renewal Workflow did not complete")


def wait_for_database_fault_window(database_url: str, query_prefix: str) -> None:
    """Wait until a fresh process blocks inside an injected PostgreSQL fault window."""
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        with psycopg.connect(database_url) as connection:
            active = connection.execute(
                "SELECT 1 FROM pg_stat_activity WHERE datname = current_database() "
                "AND state = 'active' AND query LIKE %s AND wait_event = 'PgSleep'",
                (f"{query_prefix}%",),
            ).fetchone()
        if active is not None:
            return
        time.sleep(0.01)
    raise AssertionError(f"Fresh Worker did not reach fault window: {query_prefix}")


__all__ = [
    "approve_renewal",
    "prepare_renewal_approval",
    "renewal_context",
    "wait_for_database_fault_window",
    "wait_for_renewal_completion",
]
