"""A throwaway schema for tests that write fixtures.

Fixture-writing tests used to ingest synthetic releases straight into the
curated corpus. Cleanup was best-effort and provenance-blind -- curation inserts
compounds and targets with no foreign key back to a release -- so two test
molecules and two test proteins survived into the production tables and were
embedded by ESM-2 as though they were data.

An id high-water mark plugged that hole. This removes it: a fixture that writes
into its own schema cannot touch the corpus at all, whatever its teardown does
or fails to do.

`public` stays on the search path because extensions such as pgcrypto live
there, so `is_fully_shadowed()` exists to prove the isolation rather than assume
it: every table the migrations create must resolve to the test schema, not fall
through.
"""

from __future__ import annotations

import os
import re
from contextlib import contextmanager
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator

SCHEMA_ENV = "SEQ2LEAD_DB_SCHEMA"
_SAFE_NAME = re.compile(r"^[a-z_][a-z0-9_]{0,48}$")

#: Schema holding the corpus. Its table list is the authoritative inventory of
#: what a test schema must shadow -- a hand-maintained list drifts, and the first
#: one did: it named 19 tables where the database had 31, so a query against
#: `pair_label_support`, `target_cluster` or any of the other ten would have
#: fallen straight through to the corpus.
CORPUS_SCHEMA = "public"


class IsolationError(RuntimeError):
    """The test schema is not actually isolating anything."""


def _tables_in(conn, schema: str) -> set[str]:
    rows = conn.execute(
        "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = %s AND c.relkind = 'r'",
        (schema,),
    ).fetchall()
    return {str(r[0]) for r in rows}


def corpus_tables(conn) -> set[str]:
    """Every table in the corpus schema. The inventory nothing may miss."""
    return _tables_in(conn, CORPUS_SCHEMA)


def is_fully_shadowed(conn, schema: str) -> list[str]:
    """Corpus tables that do *not* resolve to `schema`. Empty means real isolation.

    Derived by comparing against the corpus schema rather than a literal list, so
    a migration that adds a table cannot quietly fall outside the check.
    """
    expected = corpus_tables(conn)
    if not expected:
        raise IsolationError(
            f"the {CORPUS_SCHEMA!r} schema has no tables, so there is no inventory to "
            "check a test schema against. Run the migrations first."
        )
    return sorted(expected - _tables_in(conn, schema))


@contextmanager
def isolated_schema(name: str = "seq2lead_test") -> Iterator[str]:
    """Create a schema, migrate into it, point the process at it, then drop it.

    The environment variable is set and restored around the block, so anything
    that opens a connection inside it -- including code that takes no config
    argument -- lands in the test schema.
    """
    if not _SAFE_NAME.match(name):
        raise IsolationError(
            f"unsafe schema name {name!r}: lowercase letters, digits and underscores only"
        )
    from seq2lead.db import transaction
    from seq2lead.db.migrations import apply_pending
    from seq2lead.db.schema import create_schema

    previous = os.environ.get(SCHEMA_ENV)
    # Created from a public-schema connection, before the redirect is in place.
    with transaction() as conn:
        conn.execute(f'DROP SCHEMA IF EXISTS "{name}" CASCADE')
        conn.execute(f'CREATE SCHEMA "{name}"')

    os.environ[SCHEMA_ENV] = name
    try:
        with transaction() as conn:
            create_schema(conn)
            apply_pending(conn)
        with transaction() as conn:
            unshadowed = is_fully_shadowed(conn, name)
            if unshadowed:
                raise IsolationError(
                    f"schema {name!r} does not shadow {len(unshadowed)} tables "
                    f"({unshadowed[:5]}), so a query would reach the real corpus. "
                    "Refusing to run fixtures against it."
                )
        yield name
    finally:
        if previous is None:
            os.environ.pop(SCHEMA_ENV, None)
        else:
            os.environ[SCHEMA_ENV] = previous
        with transaction() as conn:
            conn.execute(f'DROP SCHEMA IF EXISTS "{name}" CASCADE')


@contextmanager
def corpus_connection() -> Iterator:
    """A connection to the real corpus, regardless of any active test schema.

    A handful of tests assert properties of the curated corpus itself -- that a
    superseded endpoint is refused, that a pilot release round-trips. Those are
    not fixture-writing tests and must not be isolated, or they silently skip
    against an empty schema and stop testing anything.
    """
    from dataclasses import replace

    from seq2lead.config import DbConfig
    from seq2lead.db import connect

    with connect(replace(DbConfig.from_env(), schema="public")) as conn:
        yield conn
