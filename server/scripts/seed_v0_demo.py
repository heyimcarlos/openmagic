"""Seed the explicit trusted identity and renewal Workflow used by the V0 demo."""

from __future__ import annotations

import asyncio
from uuid import UUID

from server.config import get_settings
from server.workflows.demo_seed import seed_v0_demo


def _required(value: str | None, variable: str) -> str:
    if not value:
        raise ValueError(f"{variable} is required to seed the V0 demo")
    return value


async def _seed() -> None:
    settings = get_settings()
    workflow_id = await seed_v0_demo(
        _required(settings.database_url, "OPENMAGIC_DATABASE_URL"),
        broker_party_id=UUID(
            _required(settings.workflow_broker_party_id, "OPENMAGIC_WORKFLOW_BROKER_PARTY_ID")
        ),
        organization_party_id=UUID(
            _required(
                settings.workflow_organization_party_id,
                "OPENMAGIC_WORKFLOW_ORGANIZATION_PARTY_ID",
            )
        ),
    )
    print(f"Seeded V0 Workflow {workflow_id}")


if __name__ == "__main__":
    asyncio.run(_seed())
