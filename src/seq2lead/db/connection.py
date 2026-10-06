"""PostgreSQL connection and transaction helpers.

Transaction behaviour is settled here, before any ingestion code exists, so that
every later write path inherits it rather than inventing its own: work inside
`transaction()` commits on success, rolls back on any exception, and the
connection is closed either way. A partial load must never be mistakable for a
complete one.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import TYPE_CHECKING

import psycopg

from seq2lead.config import DbConfig

if TYPE_CHECKING:
    from collections.abc import Iterator


class DatabaseUnreachable(RuntimeError):
    """Raised when Postgres cannot be reached, with a remedy in the message."""


_REMEDY = (
    "Start it with:  docker compose --env-file .env -f docker/compose.yml up -d\n"
    "Then check:     docker compose --env-file .env -f docker/compose.yml ps"
)


@contextmanager
def connect(cfg: DbConfig | None = None) -> Iterator[psycopg.Connection]:
    """Yield a connection and always close it.

    This helper does **not** commit. Reads are fine here; anything that writes
    should use `transaction()` so the commit/rollback decision is explicit.
    """
    cfg = cfg or DbConfig.from_env()
    try:
        conn = psycopg.connect(**cfg.connect_kwargs, connect_timeout=5)
    except psycopg.OperationalError as exc:
        raise DatabaseUnreachable(
            f"Cannot reach PostgreSQL at {cfg.describe()}.\n{_REMEDY}\n\nDriver said: {exc}"
        ) from exc
    # `search_path` is set per connection, not per session variable, so a test
    # schema cannot leak into a later connection. `public` stays on the path
    # because extensions such as pgcrypto live there; the migrations create every
    # table in the leading schema, so those shadow the corpus completely.
    if cfg.schema != "public":
        with conn.cursor() as cur:
            cur.execute(f'SET search_path TO "{cfg.schema}", public')
        conn.commit()
    try:
        yield conn
    finally:
        conn.close()


@contextmanager
def transaction(cfg: DbConfig | None = None) -> Iterator[psycopg.Connection]:
    """Yield a connection whose work commits on success and rolls back on failure.

    `BaseException` rather than `Exception`: a KeyboardInterrupt part-way through
    an ingest must also roll back, not leave half a release loaded.
    """
    with connect(cfg) as conn:
        try:
            yield conn
        except BaseException:
            conn.rollback()
            raise
        else:
            conn.commit()


def server_version(cfg: DbConfig | None = None) -> str:
    """Return the server's version string. The M0 smoke check."""
    with connect(cfg) as conn, conn.cursor() as cur:
        cur.execute("SELECT version()")
        row = cur.fetchone()
    if row is None:  # pragma: no cover - SELECT version() always returns a row
        raise DatabaseUnreachable("Connected, but SELECT version() returned no rows.")
    return str(row[0])
