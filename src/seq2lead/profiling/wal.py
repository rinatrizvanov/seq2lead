"""WAL and checkpoint accounting around a bulk load.

A 3.2M-row COPY inside one transaction is a large write-ahead-log burst, and the
open question from M1 was whether that burst forces requested checkpoints and
hurts. Guessing is pointless, so measure it: snapshot the cluster's WAL and
checkpoint counters either side of the load and report the delta.

`pg_stat_wal` and `pg_stat_bgwriter` are cluster-wide, so a concurrent workload
would pollute the numbers. On a dedicated development database they are clean;
the report says so rather than implying isolation we do not have.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import psycopg


@dataclass
class WalSnapshot:
    lsn: str
    wal_records: int
    wal_bytes: int
    wal_fpi: int
    checkpoints_timed: int
    checkpoints_requested: int
    buffers_checkpoint: int


@dataclass
class WalDelta:
    wal_bytes: int
    wal_records: int
    wal_fpi: int
    checkpoints_timed: int
    checkpoints_requested: int
    buffers_checkpoint: int

    @property
    def checkpoints_total(self) -> int:
        return self.checkpoints_timed + self.checkpoints_requested


def _scalar(conn: psycopg.Connection, sql: str) -> object:
    row = conn.execute(sql).fetchone()
    return row[0] if row else None


def snapshot(conn: psycopg.Connection) -> WalSnapshot:
    lsn = str(_scalar(conn, "SELECT pg_current_wal_lsn()"))
    wal = conn.execute("SELECT wal_records, wal_bytes, wal_fpi FROM pg_stat_wal").fetchone() or (
        0,
        0,
        0,
    )
    # pg_stat_bgwriter carries the checkpoint counters in PostgreSQL 16; they move
    # to pg_stat_checkpointer in 17, so fall back rather than fail on a newer server.
    try:
        bg = conn.execute(
            "SELECT checkpoints_timed, checkpoints_req, buffers_checkpoint FROM pg_stat_bgwriter"
        ).fetchone() or (0, 0, 0)
    except Exception:  # noqa: BLE001 - counter location is version-dependent
        conn.rollback()
        bg = conn.execute(
            "SELECT num_timed, num_requested, buffers_written FROM pg_stat_checkpointer"
        ).fetchone() or (0, 0, 0)

    return WalSnapshot(
        lsn=lsn,
        wal_records=int(wal[0] or 0),
        wal_bytes=int(wal[1] or 0),
        wal_fpi=int(wal[2] or 0),
        checkpoints_timed=int(bg[0] or 0),
        checkpoints_requested=int(bg[1] or 0),
        buffers_checkpoint=int(bg[2] or 0),
    )


def delta(conn: psycopg.Connection, before: WalSnapshot, after: WalSnapshot) -> WalDelta:
    """Difference two snapshots. LSN distance is authoritative for WAL volume."""
    lsn_bytes = _scalar(conn, f"SELECT pg_wal_lsn_diff('{after.lsn}', '{before.lsn}')::bigint")
    return WalDelta(
        wal_bytes=int(lsn_bytes or 0),
        wal_records=after.wal_records - before.wal_records,
        wal_fpi=after.wal_fpi - before.wal_fpi,
        checkpoints_timed=after.checkpoints_timed - before.checkpoints_timed,
        checkpoints_requested=after.checkpoints_requested - before.checkpoints_requested,
        buffers_checkpoint=after.buffers_checkpoint - before.buffers_checkpoint,
    )
