"""PostgreSQL connection and transaction lifecycle for Workflow commands."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import sqlalchemy as sa
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


class WorkflowDatabase:
    """Create isolated async sessions without exposing transaction state globally."""

    def __init__(self, url: str) -> None:
        parsed_url = make_url(url)
        if parsed_url.get_backend_name() != "postgresql":
            raise ValueError("The Workflow protocol requires PostgreSQL")
        self._engine: AsyncEngine = create_async_engine(url, pool_pre_ping=True)
        self._sessions = async_sessionmaker(self._engine, expire_on_commit=False)

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[AsyncSession]:
        async with self._sessions() as session, session.begin():
            yield session

    @asynccontextmanager
    async def read_transaction(self) -> AsyncIterator[AsyncSession]:
        """Read one aggregate from a stable PostgreSQL snapshot."""

        async with self._sessions() as session, session.begin():
            await session.execute(sa.text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ"))
            yield session

    async def dispose(self) -> None:
        await self._engine.dispose()
