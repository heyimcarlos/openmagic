from __future__ import annotations

import pickle
import time
from uuid import uuid4

import psycopg
import pytest
from example_insurance.migrations import apply_migrations
from example_insurance.renewals import ExampleInsurance
from openmagic_evals.harness import (
    LocalEmailProvider,
    approve_renewal,
    prepare_renewal_approval,
)
from openmagic_evals.harness._postgres import postgres_container
from openmagic_runtime.kernel.control import KernelControl
from openmagic_runtime.kernel.transitions import GuardCurrentAttempt
from openmagic_runtime.threads import ThreadStore


def test_current_attempt_guard_rejects_expired_abandoned_and_superseded_authority(
    tmp_path,
) -> None:
    with (
        LocalEmailProvider(working_directory=tmp_path / "provider") as provider,
        postgres_container(database_name=f"openmagic_test_{uuid4().hex}") as postgres,
    ):
        database_url = postgres.get_connection_url(driver=None)
        apply_migrations(database_url)
        application = ExampleInsurance(
            database_url=database_url,
            email_provider_url=provider.url,
        )
        application.prepare()
        command, actor = prepare_renewal_approval(
            application,
            ThreadStore(database_url=database_url),
        )
        approve_renewal(application, command, actor)
        original = application.claim_workflow_attempt(
            worker_id="email",
            claim_request_id=uuid4(),
        )
        assert original is not None
        request = GuardCurrentAttempt(
            instance_id=original.instance_id,
            step_id=original.step_id,
            attempt_id=original.attempt_id,
            attempt_number=original.attempt_number,
        )
        with psycopg.connect(database_url) as connection:
            with connection.transaction():
                guard = KernelControl(connection).guard_current_attempt(request)
                guard.require_usable()
                with pytest.raises(TypeError, match="cannot be serialized"):
                    pickle.dumps(guard)
            with (
                connection.transaction(),
                pytest.raises(RuntimeError, match="earlier transaction"),
            ):
                guard.require_usable()
        with pytest.raises(RuntimeError, match="no longer transaction-scoped"):
            guard.require_usable()

        time.sleep(1.1)
        assert application.recover_expired_workflow_attempt()
        replacement = application.claim_workflow_attempt(
            worker_id="replacement",
            claim_request_id=uuid4(),
        )
        assert replacement is not None
        assert replacement.attempt_number == original.attempt_number + 1
        with psycopg.connect(database_url) as connection, connection.transaction():
            control = KernelControl(connection)
            with pytest.raises(RuntimeError, match="not current"):
                control.guard_current_attempt(request)
            control.guard_current_attempt(
                GuardCurrentAttempt(
                    instance_id=replacement.instance_id,
                    step_id=replacement.step_id,
                    attempt_id=replacement.attempt_id,
                    attempt_number=replacement.attempt_number,
                )
            ).require_usable()
