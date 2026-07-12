"""Workflow authority seam used by deterministic Control Plane commands."""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from .contracts import WorkflowCommandContext

AuthorityGrant = tuple[UUID, UUID, str]


class WorkflowAuthority(Protocol):
    """Resolve current Party authority outside model-controlled input."""

    async def can_create_workflow(
        self,
        context: WorkflowCommandContext,
        workflow_kind: str,
    ) -> bool: ...


@dataclass(frozen=True, init=False)
class StaticWorkflowAuthority:
    """V0 seeded authority directory, replaceable by durable Party records."""

    grants: frozenset[AuthorityGrant]

    def __init__(self, grants: Collection[AuthorityGrant]) -> None:
        object.__setattr__(self, "grants", frozenset(grants))

    async def can_create_workflow(
        self,
        context: WorkflowCommandContext,
        workflow_kind: str,
    ) -> bool:
        grant = (context.actor_party_id, context.organization_party_id, workflow_kind)
        return grant in self.grants
