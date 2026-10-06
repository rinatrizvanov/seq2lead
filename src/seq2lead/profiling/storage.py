"""Storage measurement, separating live bytes from allocated-but-dead ones.

This exists because the first M1 profile was wrong. Repeated delete-and-reload of
the same release left the relation carrying six generations of dead tuples, and
`pg_total_relation_size` counts allocated pages whether or not anything lives in
them — so the "bytes per row" figure was ~2.9x the truth. Autovacuum had already
marked the space reusable, which is why the dead-tuple count looked clean while
the file size did not shrink.

So: always report the breakdown, never a single number, and say whether the
relation was compacted before measuring.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

import psycopg

if TYPE_CHECKING:
    from seq2lead.config import DbConfig

SIZE_SQL = """
SELECT pg_relation_size(c.oid, 'main')                        AS heap_main,
       COALESCE(pg_total_relation_size(c.reltoastrelid), 0)   AS toast,
       pg_indexes_size(c.oid)                                 AS indexes,
       pg_total_relation_size(c.oid)                          AS total
FROM pg_class c
WHERE c.relname = %s AND c.relkind = 'r'
"""

TUPLE_SQL = """
SELECT n_live_tup, n_dead_tup, n_tup_ins, n_tup_del, n_tup_upd
FROM pg_stat_user_tables WHERE relname = %s
"""


@dataclass
class StorageProfile:
    relation: str
    rows: int
    heap_main: int
    toast: int
    indexes: int
    total: int
    live_tuples: int
    dead_tuples: int
    tuples_inserted: int
    tuples_deleted: int
    compacted: bool
    vacuum_seconds: float = 0.0
    total_before_compaction: int | None = None

    def per_row(self, value: int) -> float:
        return value / self.rows if self.rows else 0.0

    @property
    def reclaimed(self) -> int | None:
        if self.total_before_compaction is None:
            return None
        return self.total_before_compaction - self.total


def measure(conn: psycopg.Connection, relation: str, *, compacted: bool) -> StorageProfile:
    """Read the size breakdown for `relation`. Does not modify anything."""
    conn.execute("ANALYZE " + relation)
    sizes = conn.execute(SIZE_SQL, (relation,)).fetchone()
    if sizes is None:
        raise LookupError(f"No such table: {relation}")
    tuples = conn.execute(TUPLE_SQL, (relation,)).fetchone() or (0, 0, 0, 0, 0)
    rows_row = conn.execute(f"SELECT count(*) FROM {relation}").fetchone()  # noqa: S608
    rows = int(rows_row[0]) if rows_row else 0

    return StorageProfile(
        relation=relation,
        rows=rows,
        heap_main=int(sizes[0]),
        toast=int(sizes[1]),
        indexes=int(sizes[2]),
        total=int(sizes[3]),
        live_tuples=int(tuples[0] or 0),
        dead_tuples=int(tuples[1] or 0),
        tuples_inserted=int(tuples[2] or 0),
        tuples_deleted=int(tuples[3] or 0),
        compacted=compacted,
    )


def compact_and_measure(cfg: DbConfig, relation: str) -> StorageProfile:
    """`VACUUM FULL` the relation, then measure it.

    VACUUM FULL cannot run inside a transaction block, so this opens its own
    autocommit connection. It rewrites the table, which is what makes the result
    a live-size measurement rather than an allocation measurement.
    """
    conn = psycopg.connect(**cfg.connect_kwargs, autocommit=True)
    try:
        before = measure(conn, relation, compacted=False)
        t0 = time.perf_counter()
        conn.execute(f"VACUUM (FULL, ANALYZE) {relation}")  # noqa: S608
        elapsed = time.perf_counter() - t0
        after = measure(conn, relation, compacted=True)
    finally:
        conn.close()

    after.vacuum_seconds = elapsed
    after.total_before_compaction = before.total
    return after
