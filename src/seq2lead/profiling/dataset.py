"""Read-only summaries of a loaded release.

These are descriptive only. Nothing here decides a curation rule. M4 builds provisional Ki endpoint
tables against a predeclared threshold; M5 tests assay comparability and decides
whether pooling Ki across assays is defensible; M6 builds the splits.

`mean_fields_per_row` is *not* computed here. It is counted during ingest and
stored on `source_release.field_total`: the previous implementation ran a
`LATERAL jsonb_object_keys` over every row, which is tolerable at 27k rows and
not at 3.2M.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import psycopg

SEQ_KEY = "BindingDB Target Chain Sequence 1"
KI_KEY = "Ki (nM)"
CHAINS_KEY = "Number of Protein Chains in Target (>1 implies a multichain complex)"


@dataclass
class DatasetStats:
    rows: int
    distinct_sequences: int
    distinct_ligands: int
    distinct_target_names: int
    single_chain_rows: int
    ki_present: int
    ki_exact: int
    ki_censored_gt: int
    ki_censored_lt: int
    populated_columns: int
    total_columns: int
    mean_fields_per_row: float
    publication_date_present: int
    bindingdb_date_present: int
    ph_present: int
    temp_present: int
    exclusions_by_rule: dict[str, int]


def _scalar(conn: psycopg.Connection, sql: str, params: tuple[object, ...]) -> int:
    row = conn.execute(sql, params).fetchone()
    return int(row[0]) if row and row[0] is not None else 0


def collect(conn: psycopg.Connection, release_id: int) -> DatasetStats:
    p = (release_id,)
    base = "FROM raw_measurement WHERE source_release_id = %s"

    release = conn.execute(
        "SELECT jsonb_array_length(column_names), rows_loaded, field_total "
        "FROM source_release WHERE id = %s",
        p,
    ).fetchone()
    total_columns = int(release[0]) if release else 0
    rows_loaded = int(release[1]) if release else 0
    field_total = int(release[2]) if release else 0

    populated = _scalar(
        conn,
        f"SELECT count(DISTINCT k) FROM (SELECT jsonb_object_keys(payload) AS k {base}) t",
        p,
    )

    def key_present(key: str) -> int:
        return _scalar(conn, f"SELECT count(*) {base} AND payload ? %s", (release_id, key))

    def ki_like(pattern: str) -> int:
        return _scalar(
            conn,
            f"SELECT count(*) {base} AND payload ->> %s LIKE %s",
            (release_id, KI_KEY, pattern),
        )

    rules = conn.execute(
        "SELECT rule_code, count(*) FROM ingest_exclusion "
        "WHERE source_release_id = %s GROUP BY rule_code ORDER BY rule_code",
        p,
    ).fetchall()

    return DatasetStats(
        rows=_scalar(conn, f"SELECT count(*) {base}", p),
        distinct_sequences=_scalar(
            conn, f"SELECT count(DISTINCT payload ->> %s) {base}", (SEQ_KEY, release_id)
        ),
        distinct_ligands=_scalar(
            conn,
            f"SELECT count(DISTINCT payload ->> %s) {base}",
            ("Ligand InChI Key", release_id),
        ),
        distinct_target_names=_scalar(
            conn, f"SELECT count(DISTINCT payload ->> %s) {base}", ("Target Name", release_id)
        ),
        single_chain_rows=_scalar(
            conn,
            f"SELECT count(*) {base} AND payload ->> %s = '1'",
            (release_id, CHAINS_KEY),
        ),
        ki_present=key_present(KI_KEY),
        ki_exact=_scalar(
            conn,
            f"SELECT count(*) {base} AND payload ->> %s !~ '^[<>~]'",
            (release_id, KI_KEY),
        ),
        ki_censored_gt=ki_like(">%"),
        ki_censored_lt=ki_like("<%"),
        populated_columns=populated,
        total_columns=total_columns,
        mean_fields_per_row=field_total / rows_loaded if rows_loaded else 0.0,
        publication_date_present=key_present("Date of publication"),
        bindingdb_date_present=key_present("Date in BindingDB"),
        ph_present=key_present("pH"),
        temp_present=key_present("Temp (C)"),
        exclusions_by_rule={str(r[0]): int(r[1]) for r in rules},
    )


def sample_sequences(conn: psycopg.Connection, release_id: int, n: int) -> list[str]:
    """Distinct chain-1 sequences, evenly spaced across the length distribution.

    Deterministic, and it covers the short and long tails rather than sampling
    the dense middle, which is where a throughput estimate would flatter itself.
    """
    rows = conn.execute(
        "SELECT DISTINCT payload ->> %s AS seq FROM raw_measurement "
        "WHERE source_release_id = %s AND payload ? %s",
        (SEQ_KEY, release_id, SEQ_KEY),
    ).fetchall()
    sequences = sorted({r[0] for r in rows if r[0]}, key=lambda s: (len(s), s))
    if not sequences or n >= len(sequences):
        return sequences
    step = (len(sequences) - 1) / (n - 1) if n > 1 else 1
    return [sequences[round(i * step)] for i in range(n)]


def sequence_length_stats(conn: psycopg.Connection, release_id: int) -> dict[str, int]:
    """Length distribution of distinct chain-1 sequences, for the M7 truncation item."""
    rows = conn.execute(
        "SELECT DISTINCT payload ->> %s AS seq FROM raw_measurement "
        "WHERE source_release_id = %s AND payload ? %s",
        (SEQ_KEY, release_id, SEQ_KEY),
    ).fetchall()
    lengths = sorted(len(r[0]) for r in rows if r[0])
    if not lengths:
        return {}
    return {
        "n": len(lengths),
        "min": lengths[0],
        "median": lengths[len(lengths) // 2],
        "max": lengths[-1],
        "over_1022": sum(1 for length in lengths if length > 1022),
    }
