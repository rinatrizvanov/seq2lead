"""Connection and transaction behaviour.

The transaction tests matter more than they look: every ingest path will rely on
`transaction()` to make a failed load leave no trace, and that guarantee is worth
pinning before there is any ingest code to get it wrong.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

import psycopg
import pytest
from psycopg import sql

from seq2lead.config import DbConfig
from seq2lead.db import DatabaseUnreachable, connect, server_version, transaction

if TYPE_CHECKING:
    from collections.abc import Iterator


def test_unreachable_error_carries_the_remedy() -> None:
    """A failure here should tell the reader how to fix it, not just that it broke."""
    nowhere = DbConfig(host="127.0.0.1", port=1, dbname="x", user="x", password="x")
    with pytest.raises(DatabaseUnreachable) as excinfo:
        server_version(nowhere)
    assert "docker compose" in str(excinfo.value)


@pytest.fixture
def scratch_table() -> Iterator[sql.Identifier]:
    """A uniquely named table, dropped afterwards whatever the test did."""
    name = f"seq2lead_test_{uuid.uuid4().hex[:12]}"
    identifier = sql.Identifier(name)
    try:
        yield identifier
    finally:
        with transaction() as conn:
            conn.execute(sql.SQL("DROP TABLE IF EXISTS {}").format(identifier))


def _create(conn: psycopg.Connection, table: sql.Identifier) -> None:
    conn.execute(sql.SQL("CREATE TABLE {} (id int PRIMARY KEY, note text)").format(table))


def _count(table: sql.Identifier) -> int:
    with connect() as conn, conn.cursor() as cur:
        cur.execute(sql.SQL("SELECT count(*) FROM {}").format(table))
        row = cur.fetchone()
    assert row is not None
    return int(row[0])


@pytest.mark.requires_db
def test_server_version_reports_postgres_16() -> None:
    """The M0 acceptance check: the scaffold can reach its own database."""
    try:
        version_string = server_version()
    except DatabaseUnreachable as exc:
        pytest.fail(str(exc))
    assert "PostgreSQL" in version_string
    assert version_string.split()[1].startswith("16."), version_string


@pytest.mark.requires_db
def test_connect_closes_the_connection() -> None:
    with connect() as conn:
        assert not conn.closed
    assert conn.closed


@pytest.mark.requires_db
def test_transaction_commits_on_success(scratch_table: sql.Identifier) -> None:
    with transaction() as conn:
        _create(conn, scratch_table)
        conn.execute(sql.SQL("INSERT INTO {} VALUES (1, 'kept')").format(scratch_table))

    # A separate connection proves the write was committed, not merely buffered.
    with connect() as conn, conn.cursor() as cur:
        cur.execute(sql.SQL("SELECT note FROM {} WHERE id = 1").format(scratch_table))
        row = cur.fetchone()
    assert row is not None
    assert row[0] == "kept"


@pytest.mark.requires_db
def test_transaction_rolls_back_when_the_body_raises(scratch_table: sql.Identifier) -> None:
    with transaction() as conn:
        _create(conn, scratch_table)

    with pytest.raises(RuntimeError, match="ingest blew up"), transaction() as conn:
        conn.execute(sql.SQL("INSERT INTO {} VALUES (1, 'doomed')").format(scratch_table))
        raise RuntimeError("ingest blew up")

    assert _count(scratch_table) == 0


@pytest.mark.requires_db
def test_transaction_rolls_back_on_a_database_error(scratch_table: sql.Identifier) -> None:
    """A constraint violation mid-batch must undo the rows written before it."""
    with transaction() as conn:
        _create(conn, scratch_table)
        conn.execute(sql.SQL("INSERT INTO {} VALUES (1, 'first')").format(scratch_table))

    with pytest.raises(psycopg.errors.UniqueViolation), transaction() as conn:
        conn.execute(sql.SQL("INSERT INTO {} VALUES (2, 'second')").format(scratch_table))
        conn.execute(sql.SQL("INSERT INTO {} VALUES (1, 'duplicate')").format(scratch_table))

    assert _count(scratch_table) == 1  # 'second' is gone too


@pytest.mark.requires_db
def test_transaction_closes_the_connection_after_rollback(
    scratch_table: sql.Identifier,
) -> None:
    with transaction() as conn:
        _create(conn, scratch_table)

    captured: list[psycopg.Connection] = []
    with pytest.raises(RuntimeError), transaction() as conn:
        captured.append(conn)
        raise RuntimeError("boom")
    assert captured[0].closed
