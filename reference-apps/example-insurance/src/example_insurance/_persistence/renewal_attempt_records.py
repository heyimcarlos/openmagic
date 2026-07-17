"""Private transaction operations for renewal Attempt results and recovery."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from openmagic_runtime.kernel.work import ClaimedAttempt, DispositionRequired, KernelWork
from psycopg import Connection

from example_insurance._persistence.renewal_workflow_records import (
    WorkflowIdentity,
    expired_workflow_instances,
    lock_workflow_after_instance,
)


@dataclass(frozen=True)
class AcceptedRenewalAttempt:
    workflow: WorkflowIdentity
    disposition: DispositionRequired


def accept_renewal_attempt_result(
    connection: Connection[tuple[Any, ...]],
    *,
    attempt: ClaimedAttempt,
    worker_id: str,
    observation: dict[str, Any],
) -> AcceptedRenewalAttempt:
    disposition = KernelWork(connection).accept_result(
        attempt,
        worker_id=worker_id,
        observation=observation,
    )
    workflow = lock_workflow_after_instance(connection, disposition.instance_id)
    return AcceptedRenewalAttempt(workflow, disposition)


def recover_expired_renewal_attempt(
    connection: Connection[tuple[Any, ...]],
) -> AcceptedRenewalAttempt | None:
    work = KernelWork(connection)
    for instance_id in expired_workflow_instances(connection):
        disposition = work.recover_expired(instance_id)
        if disposition is not None:
            workflow = lock_workflow_after_instance(connection, disposition.instance_id)
            return AcceptedRenewalAttempt(workflow, disposition)
    return None


__all__ = [
    "AcceptedRenewalAttempt",
    "accept_renewal_attempt_result",
    "recover_expired_renewal_attempt",
]
