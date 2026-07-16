"""Explicit PostgreSQL lock windows for separate-process evidence."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

import psycopg


@contextmanager
def lock_message_append(database_url: str) -> Iterator[None]:
    """Hold the Message table lock while a separate Delivery Worker blocks."""

    with psycopg.connect(database_url) as connection, connection.transaction():
        connection.execute("LOCK TABLE openmagic_runtime.messages IN ACCESS EXCLUSIVE MODE")
        yield


__all__ = ["lock_message_append"]
