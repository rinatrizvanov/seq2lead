"""Explicit, ordered schema migrations.

`CREATE TABLE IF NOT EXISTS` does not migrate an existing table: it silently does
nothing when the table is present but has the wrong columns. That is how the M1
schema change was handled, and it is why this module exists.

**How the pilot database actually reached the current schema — stated plainly.**
The M1 revision added columns (`sha256_pinned`, `field_total`, the timing
columns), changed `ingest_exclusion.raw_line TEXT` to `raw_bytes BYTEA`, and
changed the foreign keys from `ON DELETE CASCADE` to `ON DELETE RESTRICT`.
`create_schema` could not apply any of that to the existing tables. The tables
were therefore **dropped and rebuilt, and the pilot was re-ingested from the
pinned archive**. That was safe because the raw layer is reproducible from a
checksummed artifact — but it did not preserve identity: the pilot's release id
moved from 33 to 12 and its raw row ids were reassigned.

From this point the ledger below is the only sanctioned route, so that never
happens silently again. Migration 0001 is a **baseline**: on a database that
already carries these tables it is recorded as applied without running, and on an
empty database it creates them. Later migrations must be `ALTER`-based and
identity-preserving; a change that cannot be expressed that way needs an explicit
rebuild plan written down before it runs.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from seq2lead.db.m3_schema import M3_SQL
from seq2lead.db.m4_schema import M4_SQL
from seq2lead.db.m6_schema import M6_SQL
from seq2lead.db.schema import SCHEMA_SQL

if TYPE_CHECKING:
    import psycopg

LEDGER_SQL = """
CREATE TABLE IF NOT EXISTS schema_migration (
    version     INTEGER     PRIMARY KEY,
    name        TEXT        NOT NULL,
    applied_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    baselined   BOOLEAN     NOT NULL DEFAULT FALSE
);
COMMENT ON COLUMN schema_migration.baselined IS
    'True when the migration was recorded against a pre-existing schema rather than run.';
"""

M2_SQL = """
-- Auxiliary raw records: the assay-description and reactant-set mapping tables,
-- which are plain TSVs rather than the 640-column measurement file.
CREATE TABLE IF NOT EXISTS raw_record (
    id                BIGSERIAL PRIMARY KEY,
    source_release_id INTEGER NOT NULL REFERENCES source_release(id) ON DELETE RESTRICT,
    line_no           BIGINT  NOT NULL,
    payload           JSONB   NOT NULL,
    UNIQUE (source_release_id, line_no)
);

-- FASTA target sequences. Kept apart from raw_record because a FASTA record is a
-- header plus a sequence, not a set of named fields.
CREATE TABLE IF NOT EXISTS raw_sequence (
    id                BIGSERIAL PRIMARY KEY,
    source_release_id INTEGER NOT NULL REFERENCES source_release(id) ON DELETE RESTRICT,
    ordinal           BIGINT  NOT NULL,
    description       TEXT    NOT NULL,
    sequence          TEXT    NOT NULL,
    length            INTEGER NOT NULL,
    UNIQUE (source_release_id, ordinal)
);
"""


WAL_SQL = """
-- WAL and checkpoint cost belongs to the load that caused it. M2 measured this
-- live but had nowhere to put it, so a regenerated report could not show the
-- original load's figures. Recording it makes the measurement durable.
ALTER TABLE source_release ADD COLUMN IF NOT EXISTS wal_bytes BIGINT;
ALTER TABLE source_release ADD COLUMN IF NOT EXISTS wal_records BIGINT;
ALTER TABLE source_release ADD COLUMN IF NOT EXISTS wal_fpi BIGINT;
ALTER TABLE source_release ADD COLUMN IF NOT EXISTS checkpoints_timed INTEGER;
ALTER TABLE source_release ADD COLUMN IF NOT EXISTS checkpoints_requested INTEGER;
"""


RELATION_SQL = """
-- Inclusive and exclusive bounds are not interchangeable: at an activity
-- threshold, '>' is decisive where '>=' is ambiguous, because '>=' admits the
-- endpoint and the endpoint is on the active side. The original operator is kept
-- for provenance and the inclusivity is made explicit rather than inferred.
ALTER TABLE activity ADD COLUMN IF NOT EXISTS relation_raw TEXT;
ALTER TABLE activity ADD COLUMN IF NOT EXISTS bound_inclusive BOOLEAN;
"""


IDENTITY_SQL = """
-- M3 audit found BindingDB's 'Ligand InChI Key' to be stereo-insensitive: the
-- second block is UHFFFAOYSA (no stereo layer) even for structures that differ
-- only in stereochemistry. 64,486 keys carried 2-8 distinct SMILES, and RDKit
-- standardizes those to genuinely different parents. Keying the cache on that
-- identifier made one raw row silently inherit another row's compound.
--
-- The structure itself is now the key. BindingDB's identifier is retained as
-- provenance, not as identity.
DROP TABLE IF EXISTS compound_source;
CREATE TABLE compound_source (
    source_smiles_sha256 TEXT   NOT NULL,
    standardizer_version TEXT   NOT NULL,
    source_smiles        TEXT   NOT NULL,
    source_inchikey      TEXT   NOT NULL,
    compound_id          BIGINT NOT NULL REFERENCES compound(id) ON DELETE RESTRICT,
    PRIMARY KEY (source_smiles_sha256, standardizer_version)
);
CREATE INDEX IF NOT EXISTS compound_source_inchikey_idx ON compound_source(source_inchikey);
CREATE INDEX IF NOT EXISTS compound_source_compound_idx ON compound_source(compound_id);

-- Which structure THIS row carried, so the row-to-compound mapping is explicit
-- rather than inferred from a shared identifier.
ALTER TABLE activity ADD COLUMN IF NOT EXISTS source_structure_sha256 TEXT;

-- One sequence can carry several organism annotations (1,863 do) and several
-- UniProt ids (22 do). Recording all of them, and declining to elect one.
CREATE TABLE IF NOT EXISTS target_organism (
    target_id BIGINT NOT NULL REFERENCES target(id) ON DELETE RESTRICT,
    organism  TEXT   NOT NULL,
    n_rows    BIGINT NOT NULL DEFAULT 0,
    PRIMARY KEY (target_id, organism)
);
ALTER TABLE target ADD COLUMN IF NOT EXISTS n_organisms INTEGER;
ALTER TABLE target ADD COLUMN IF NOT EXISTS n_uniprot_ids INTEGER;

COMMENT ON COLUMN target.organism IS
    'Populated only when the sequence carries exactly one organism annotation. '
    'NULL means the annotations conflict; see target_organism for all of them.';
"""


EVAL_FLAG_SQL = """
-- Downstream code must not have to join pair_label to discover that a pair is
-- unusable. An explicit flag on each table says so directly.
ALTER TABLE pair_regression ADD COLUMN IF NOT EXISTS excluded_from_eval BOOLEAN NOT NULL
    DEFAULT FALSE;
ALTER TABLE pair_label      ADD COLUMN IF NOT EXISTS excluded_from_eval BOOLEAN NOT NULL
    DEFAULT FALSE;

COMMENT ON COLUMN pair_regression.excluded_from_eval IS
    'True means keep this pair out of validation AND test. Statistics are retained '
    'for audit and for the M5 assay-variance analysis; see eval_exclusion_reason.';
COMMENT ON COLUMN pair_label.excluded_from_eval IS
    'True means keep this pair out of validation AND test. Training use is a separate '
    'decision that M6 makes; see eval_exclusion_reason.';

-- Endpoint versions are immutable, so a corrected build is a new version and the
-- old one is marked rather than edited.
ALTER TABLE endpoint_version ADD COLUMN IF NOT EXISTS superseded_by TEXT;
ALTER TABLE endpoint_version ADD COLUMN IF NOT EXISTS superseded_reason TEXT;
"""


CLUSTER_THRESHOLD_SQL = """
-- REAL is float4, and comparing it to a float8 parameter (0.40) never matches,
-- so every cold_protein lookup silently found nothing. NUMERIC compares exactly.
ALTER TABLE target_cluster ALTER COLUMN threshold TYPE NUMERIC(4,3);
"""


TEMPORAL_SQL = """
-- The temporal split previously joined GLOBAL M4 labels, whose medians and
-- interval logic summarise every measurement of a pair including ones dated
-- after the cut. That is leakage of exactly the kind the split exists to avoid.
--
-- Each temporal partition now gets its own endpoint_version, built from that
-- partition's activities alone, and this table names it.
CREATE TABLE IF NOT EXISTS split_partition_endpoint (
    split_id    INTEGER NOT NULL REFERENCES split_version(id) ON DELETE CASCADE,
    partition   TEXT    NOT NULL,
    endpoint_id INTEGER NOT NULL REFERENCES endpoint_version(id) ON DELETE RESTRICT,
    PRIMARY KEY (split_id, partition)
);

-- label_reversal has no tuning set by construction, so it is marked as what it
-- is rather than pretending otherwise.
ALTER TABLE split_version ADD COLUMN IF NOT EXISTS protocol TEXT NOT NULL DEFAULT 'trainable';
ALTER TABLE split_version ADD COLUMN IF NOT EXISTS superseded_by TEXT;
ALTER TABLE split_version ADD COLUMN IF NOT EXISTS superseded_reason TEXT;

COMMENT ON COLUMN split_version.protocol IS
    'trainable = may inform model selection. diagnostic_frozen = run only after '
    'model choices are frozen elsewhere; it has no tuning set.';

-- A pair is new or recurrent *relative to the history available to the partition
-- that scores it*, not relative to the whole table.
ALTER TABLE split_pair_assignment ADD COLUMN IF NOT EXISTS history_partitions TEXT;

COMMENT ON COLUMN split_pair_assignment.history_partitions IS
    'Which earlier partitions this pair was already measured in. Empty means new.';
"""


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    sql: str
    # A baseline migration may be recorded as applied against an existing schema.
    baseline_probe: str | None = None


M7_SQL = """
-- Content-addressed feature caches. A cache is identified by the *spec* that
-- produced it, so a changed model revision, pooling rule or length policy
-- yields a different identity and cannot silently reuse the old vectors.
CREATE TABLE IF NOT EXISTS feature_version (
    id              SERIAL PRIMARY KEY,
    name            TEXT NOT NULL UNIQUE,
    kind            TEXT NOT NULL,          -- ecfp4 | esm2 | activity
    entity          TEXT NOT NULL,          -- compound | target | pair
    spec_sha256     TEXT NOT NULL,          -- identity: hash of the spec below
    params          JSONB NOT NULL,
    dim             INTEGER,
    n_entities      INTEGER,
    storage_path    TEXT,
    storage_sha256  TEXT,                   -- identity of the bytes on disk
    split_id        INTEGER REFERENCES split_version(id) ON DELETE RESTRICT,
    builder_version TEXT NOT NULL,
    seconds         DOUBLE PRECISION,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    superseded_by   TEXT,
    superseded_reason TEXT
);
CREATE INDEX IF NOT EXISTS feature_version_spec_idx ON feature_version (spec_sha256);

-- Per-entity notes attached to a cache: which targets were embedded outside the
-- model's training window, which compounds failed to parse, and so on.
CREATE TABLE IF NOT EXISTS feature_entity_flag (
    feature_id INTEGER NOT NULL REFERENCES feature_version(id) ON DELETE CASCADE,
    entity_id  INTEGER NOT NULL,
    flag       TEXT    NOT NULL,
    detail     JSONB,
    PRIMARY KEY (feature_id, entity_id, flag)
);

-- Evaluation strata that attach to a target rather than a pair, so a score can
-- be reported separately for, e.g., held-out targets with a near-identical
-- training homolog.
CREATE TABLE IF NOT EXISTS split_target_stratum (
    split_id  INTEGER NOT NULL REFERENCES split_version(id) ON DELETE CASCADE,
    target_id INTEGER NOT NULL REFERENCES target(id) ON DELETE RESTRICT,
    stratum   TEXT    NOT NULL,
    detail    JSONB,
    PRIMARY KEY (split_id, target_id, stratum)
);
"""


M7_V2_SQL = """
-- `digest()` for the compound content hash in the input manifest. Idempotent,
-- and needed on a fresh database; this cluster already had it enabled by hand.
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- A cache's identity must cover the *inputs*, not only the representation
-- settings. Without these a --limit build and a full build share an identity,
-- and the partial one can be registered as the complete result.
ALTER TABLE feature_version ADD COLUMN IF NOT EXISTS manifest_sha256 TEXT;
ALTER TABLE feature_version ADD COLUMN IF NOT EXISTS population TEXT;
ALTER TABLE feature_version ADD COLUMN IF NOT EXISTS completeness TEXT
    NOT NULL DEFAULT 'unknown';          -- full | partial | unknown
ALTER TABLE feature_version ADD COLUMN IF NOT EXISTS library_versions JSONB;
ALTER TABLE feature_version ADD COLUMN IF NOT EXISTS validated_at TIMESTAMPTZ;
CREATE INDEX IF NOT EXISTS feature_version_manifest_idx
    ON feature_version (manifest_sha256);
"""


M9_SQL = """
-- A candidate library is a frozen, versioned set of compounds. The member list
-- is digested so a ranking can be reproduced exactly, and the selection rule is
-- stored beside it so nobody has to infer it from the members.
CREATE TABLE IF NOT EXISTS candidate_library (
    id            SERIAL PRIMARY KEY,
    name          TEXT NOT NULL UNIQUE,
    selection_rule TEXT NOT NULL,
    source_release_id INTEGER REFERENCES source_release(id) ON DELETE RESTRICT,
    n_members     INTEGER NOT NULL,
    member_sha256 TEXT NOT NULL,
    cap           INTEGER,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    notes         TEXT
);
CREATE TABLE IF NOT EXISTS candidate_library_member (
    library_id  INTEGER NOT NULL REFERENCES candidate_library(id) ON DELETE CASCADE,
    compound_id INTEGER NOT NULL REFERENCES compound(id) ON DELETE RESTRICT,
    PRIMARY KEY (library_id, compound_id)
);
"""

MIGRATIONS: tuple[Migration, ...] = (
    Migration(
        version=1,
        name="baseline_raw_layer",
        sql=SCHEMA_SQL,
        baseline_probe="SELECT to_regclass('public.source_release')",
    ),
    Migration(version=2, name="m2_auxiliary_raw_tables", sql=M2_SQL),
    Migration(version=3, name="release_wal_accounting", sql=WAL_SQL),
    Migration(version=4, name="m3_curated_layer", sql=M3_SQL),
    Migration(version=5, name="activity_bound_inclusivity", sql=RELATION_SQL),
    Migration(version=6, name="structure_keyed_identity", sql=IDENTITY_SQL),
    Migration(version=7, name="m4_endpoint_layer", sql=M4_SQL),
    Migration(version=8, name="pair_eval_exclusion_flags", sql=EVAL_FLAG_SQL),
    Migration(version=9, name="m6_split_layer", sql=M6_SQL),
    Migration(version=10, name="cluster_threshold_numeric", sql=CLUSTER_THRESHOLD_SQL),
    Migration(version=11, name="temporal_partition_endpoints", sql=TEMPORAL_SQL),
    Migration(version=12, name="m7_feature_layer", sql=M7_SQL),
    Migration(version=13, name="m7_cache_input_identity", sql=M7_V2_SQL),
    Migration(version=14, name="m9_candidate_library", sql=M9_SQL),
)


def applied_versions(conn: psycopg.Connection) -> set[int]:
    conn.execute(LEDGER_SQL)
    rows = conn.execute("SELECT version FROM schema_migration").fetchall()
    return {int(r[0]) for r in rows}


def apply_pending(conn: psycopg.Connection) -> list[tuple[int, str, bool]]:
    """Apply outstanding migrations. Returns (version, name, baselined) for each."""
    done = applied_versions(conn)
    performed: list[tuple[int, str, bool]] = []

    for migration in MIGRATIONS:
        if migration.version in done:
            continue

        baselined = False
        if migration.baseline_probe is not None:
            row = conn.execute(migration.baseline_probe).fetchone()
            baselined = bool(row and row[0] is not None)

        if not baselined:
            conn.execute(migration.sql)

        conn.execute(
            "INSERT INTO schema_migration (version, name, baselined) VALUES (%s, %s, %s)",
            (migration.version, migration.name, baselined),
        )
        performed.append((migration.version, migration.name, baselined))

    return performed
