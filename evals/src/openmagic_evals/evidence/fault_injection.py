"""Explicit PostgreSQL fault windows for separate-process evidence."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

import psycopg
from psycopg import sql


@contextmanager
def pause_message_append(database_url: str, *, seconds: int = 10) -> Iterator[None]:
    if seconds <= 0:
        raise ValueError("fault-window duration must be positive")
    with psycopg.connect(database_url) as connection, connection.transaction():
        connection.execute(
            sql.SQL(
                "CREATE FUNCTION openmagic_runtime.pause_evidence_message_append() "
                "RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN PERFORM pg_sleep({}); "
                "RETURN NEW; END $$"
            ).format(sql.Literal(seconds))
        )
        connection.execute(
            "CREATE TRIGGER pause_evidence_message_append BEFORE INSERT ON "
            "openmagic_runtime.messages FOR EACH ROW EXECUTE FUNCTION "
            "openmagic_runtime.pause_evidence_message_append()"
        )
    try:
        yield
    finally:
        with psycopg.connect(database_url) as connection, connection.transaction():
            connection.execute(
                "DROP TRIGGER IF EXISTS pause_evidence_message_append ON openmagic_runtime.messages"
            )
            connection.execute(
                "DROP FUNCTION IF EXISTS openmagic_runtime.pause_evidence_message_append()"
            )


__all__ = ["pause_message_append"]
