"""Workflow authority seam used by deterministic Control Plane commands."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Collection
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from .contracts import WorkflowCommandContext
from .models import WorkflowRow

AuthorityGrant = tuple[UUID, UUID, str]
CurrentBrokerAuthority = Callable[
    [AsyncSession, WorkflowCommandContext, WorkflowRow], Awaitable[bool]
]


@dataclass(frozen=True)
class WorkflowAuthorizationScope:
    """Trusted V0 creation scope recovered from Workflow Event evidence."""

    actor_party_id: UUID
    organization_party_id: UUID


class WorkflowAuthority(Protocol):
    """Resolve current Party authority outside model-controlled input."""

    async def can_create_workflow(
        self,
        context: WorkflowCommandContext,
        workflow_kind: str,
    ) -> bool: ...

    async def can_read_workflow(
        self,
        context: WorkflowCommandContext,
        workflow_id: UUID,
        workflow_kind: str,
        scope: WorkflowAuthorizationScope,
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

    async def can_read_workflow(
        self,
        context: WorkflowCommandContext,
        workflow_id: UUID,
        workflow_kind: str,
        scope: WorkflowAuthorizationScope,
    ) -> bool:
        del workflow_id
        grant = (context.actor_party_id, context.organization_party_id, workflow_kind)
        return (
            grant in self.grants
            and context.actor_party_id == scope.actor_party_id
            and context.organization_party_id == scope.organization_party_id
        )
