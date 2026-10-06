"""DDL for the immutable raw layer.

M1 covers provenance (`source_release`), the record store (`raw_measurement`) and
the reconciliation ledger (`ingest_exclusion`). The curated layer arrives at M3
and is always rebuildable from these.

**What "raw" means here, precisely.** The database stores a *parsed projection*
of the source file, not its original bytes:

* Each loaded line is split on TAB and stored as a JSONB map of its non-empty
  fields. A field is omitted only when it is exactly the empty string; a field
  containing whitespace is preserved as-is.
* `source_release.column_names` holds the full ordered header, so any loaded row
  reconstructs exactly as ``"\\t".join(payload.get(c, "") for c in header)``.
  `seq2lead.ingest.bindingdb.reconstruct_line` implements this and the test suite
  asserts it round-trips against the real file.
* Lines that cannot be decoded as UTF-8, or whose field count disagrees with the
  header, are not loaded. They are quarantined in `ingest_exclusion` with their
  **original bytes** intact and untruncated.
* The archive's SHA-256 and URL are recorded, so the original bytes remain
  obtainable even though they are not themselves in the database.

Storage note: BindingDB's TSV is 640 columns wide because a 12-column block is
repeated for up to 50 target chains, and only ~30 fields per row are populated.
Storing all 640 keys would inflate the table roughly twentyfold for no added
information.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import psycopg

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS source_release (
    id              SERIAL PRIMARY KEY,
    source_name     TEXT        NOT NULL,
    version         TEXT        NOT NULL,
    subset          TEXT        NOT NULL,
    url             TEXT        NOT NULL,
    archive_member  TEXT        NOT NULL,
    sha256          TEXT        NOT NULL,
    md5             TEXT        NOT NULL,
    md5_verified    BOOLEAN     NOT NULL,
    sha256_pinned   BOOLEAN     NOT NULL,
    archive_bytes   BIGINT      NOT NULL,
    member_bytes    BIGINT      NOT NULL,
    license         TEXT        NOT NULL,
    downloaded_at   TIMESTAMPTZ NOT NULL,
    ingested_at     TIMESTAMPTZ,
    column_names    JSONB       NOT NULL,
    data_lines      BIGINT      NOT NULL DEFAULT 0,
    rows_loaded     BIGINT      NOT NULL DEFAULT 0,
    rows_excluded   BIGINT      NOT NULL DEFAULT 0,
    field_total     BIGINT      NOT NULL DEFAULT 0,
    -- Timings belong to the release, so that re-running the profile on an
    -- already-loaded release reports the real load rather than re-loading it.
    copy_seconds      DOUBLE PRECISION,
    exclusion_seconds DOUBLE PRECISION,
    index_seconds     JSONB,
    total_seconds     DOUBLE PRECISION,
    UNIQUE (source_name, version, subset)
);

COMMENT ON COLUMN source_release.column_names IS
    'The full ordered TSV header. Keys absent from raw_measurement.payload are empty strings.';
COMMENT ON COLUMN source_release.sha256_pinned IS
    'True when the archive digest matched an expected value frozen in the source manifest.';
COMMENT ON COLUMN source_release.field_total IS
    'Sum of populated fields over all loaded rows, counted during ingest so that '
    'mean fields/row never needs a full-table scan.';

CREATE TABLE IF NOT EXISTS raw_measurement (
    id                BIGSERIAL PRIMARY KEY,
    source_release_id INTEGER NOT NULL REFERENCES source_release(id) ON DELETE RESTRICT,
    line_no           BIGINT  NOT NULL,
    payload           JSONB   NOT NULL,
    UNIQUE (source_release_id, line_no)
);

CREATE TABLE IF NOT EXISTS ingest_exclusion (
    id                BIGSERIAL PRIMARY KEY,
    source_release_id INTEGER NOT NULL REFERENCES source_release(id) ON DELETE RESTRICT,
    line_no           BIGINT  NOT NULL,
    rule_code         TEXT    NOT NULL,
    detail            TEXT    NOT NULL,
    raw_bytes         BYTEA   NOT NULL,
    byte_length       BIGINT  NOT NULL
);

COMMENT ON TABLE ingest_exclusion IS
    'Every input line not loaded, with its original bytes preserved untruncated and '
    'undecoded. loaded + excluded must equal the data lines in the source file.';
"""

# Indexes built after a bulk load. Dropped first, so COPY is not slowed by index
# maintenance and the rebuild time is a real measurement rather than a no-op.
#
# **GIN reassessment for M2.** The pilot built a `jsonb_path_ops` GIN over the whole
# payload. That is not carried to the full release. Its size scales with distinct
# key/value pairs rather than row count, and at 3.2M rows x ~30 populated fields it
# would be very large — while no query M3 actually issues needs it. M3 reads the
# measurement table sequentially to build the curated layer, and its one keyed
# lookup is the assay join, which the expression index below serves. An index is
# built when a named query needs it, not because the pilot had one.
INDEX_SQL: dict[str, str] = {
    "raw_measurement_reactant_set_idx": (
        "CREATE INDEX IF NOT EXISTS raw_measurement_reactant_set_idx "
        "ON raw_measurement ((payload ->> 'BindingDB Reactant_set_id'))"
    ),
}

# Deliberately not built. Kept here so the decision is visible and reversible, and
# so `drop_indexes` removes it from any database that predates the reassessment.
OPTIONAL_INDEX_SQL: dict[str, str] = {
    "raw_measurement_payload_gin": (
        "CREATE INDEX IF NOT EXISTS raw_measurement_payload_gin "
        "ON raw_measurement USING GIN (payload jsonb_path_ops)"
    ),
}

# Join paths M3 needs across the auxiliary tables:
#   raw_measurement 'BindingDB Reactant_set_id' -> raw_record 'REACTANT_SET_ID'
#   raw_record 'ENTRYID_ASSAYID' -> raw_record ('ENTRYID','ASSAYID')  [assay text]
AUX_INDEX_SQL: dict[str, str] = {
    "raw_record_reactant_set_idx": (
        "CREATE INDEX IF NOT EXISTS raw_record_reactant_set_idx "
        "ON raw_record ((payload ->> 'REACTANT_SET_ID'))"
    ),
    "raw_record_entry_assay_idx": (
        "CREATE INDEX IF NOT EXISTS raw_record_entry_assay_idx "
        "ON raw_record ((payload ->> 'ENTRYID'), (payload ->> 'ASSAYID'))"
    ),
}


def create_schema(conn: psycopg.Connection) -> None:
    """Create the raw-layer tables. Idempotent."""
    conn.execute(SCHEMA_SQL)


def drop_indexes(conn: psycopg.Connection) -> None:
    """Drop post-load indexes so a rebuild is timed honestly.

    Includes `OPTIONAL_INDEX_SQL`, so a database carrying the pilot's GIN index has
    it removed rather than silently kept and rebuilt at full-release scale.
    """
    for name in (*INDEX_SQL, *OPTIONAL_INDEX_SQL):
        conn.execute(f"DROP INDEX IF EXISTS {name}")
