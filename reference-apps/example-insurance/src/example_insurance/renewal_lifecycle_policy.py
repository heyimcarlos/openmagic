"""Application Policy for renewal authority and lifecycle changes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

WorkflowLifecycle = Literal["active", "cancelled", "completed"]


def workflow_lifecycle(value: object) -> WorkflowLifecycle:
    if value == "active":
        return "active"
    if value == "cancelled":
        return "cancelled"
    if value == "completed":
        return "completed"
    raise RuntimeError("Renewal Workflow has an invalid lifecycle")


@dataclass(frozen=True)
class CancellationFacts:
    lifecycle: WorkflowLifecycle
    actor_authorized: bool
    dispatch_boundary_crossed: bool


class RenewalLifecyclePolicy:
    @staticmethod
    def authorizes_revocation(*, actor_kind: str, actor_id: str) -> bool:
        return actor_kind == "system" and actor_id == "authority-administrator"

    @staticmethod
    def actor_can_cancel(
        *,
        actor_kind: str,
        actor_id: str,
        authorized_actor_kind: str,
        authorized_actor_id: str,
    ) -> bool:
        return (actor_kind == authorized_actor_kind and actor_id == authorized_actor_id) or (
            actor_kind == "system" and actor_id == "workflow-administrator"
        )

    @staticmethod
    def cancellation_outcome(
        facts: CancellationFacts,
    ) -> Literal[
        "unauthorized",
        "already_completed",
        "already_cancelled",
        "too_late",
        "cancelled",
    ]:
        if not facts.actor_authorized:
            return "unauthorized"
        if facts.lifecycle == "completed":
            return "already_completed"
        if facts.lifecycle == "cancelled":
            return "already_cancelled"
        if facts.dispatch_boundary_crossed:
            return "too_late"
        return "cancelled"


__all__ = [
    "CancellationFacts",
    "RenewalLifecyclePolicy",
    "WorkflowLifecycle",
    "workflow_lifecycle",
]
