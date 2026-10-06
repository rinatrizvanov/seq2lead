"""DDL for the M3 curated layer.

Derived from the pinned raw releases and always rebuildable from them. Nothing
here aggregates: `activity` is one row per (raw measurement row x populated
measurement type), and Ki / IC50 / Kd / EC50 keep their own rows. Pair-level
aggregation (`pair_regression`, `pair_label`) is M4 and deliberately absent.

Two design points worth stating:

* **A missing assay description is recorded, never silently dropped.**
  `activity.assay_id` is nullable and `assay_join_status` says why it is null.
  Measured before building: 98.21% of measurements reach an assay description and
  1.79% do not, and the join was verified row-preserving, so it cannot multiply
  activity rows.
* **Out-of-scope rows are flagged, not deleted.** Multi-chain targets get an
  `activity` row with `in_benchmark_scope = false` and a reason, so the scope
  decision stays visible and reversible. Only rows that cannot be curated at all
  (no structure, no sequence, no value) become `curation_exclusion` entries.
"""

from __future__ import annotations

M3_SQL = """
CREATE TABLE IF NOT EXISTS compound (
    id                   BIGSERIAL PRIMARY KEY,
    inchikey             TEXT NOT NULL,
    canonical_smiles     TEXT NOT NULL,
    standardizer_version TEXT NOT NULL,
    n_heavy_atoms        INTEGER,
    UNIQUE (inchikey, standardizer_version)
);

-- Doubles as the standardization cache: a source structure already seen is never
-- re-standardized, which is what makes a restart cheap.
CREATE TABLE IF NOT EXISTS compound_source (
    source_inchikey      TEXT NOT NULL,
    standardizer_version TEXT NOT NULL,
    compound_id          BIGINT NOT NULL REFERENCES compound(id) ON DELETE RESTRICT,
    source_smiles        TEXT NOT NULL,
    PRIMARY KEY (source_inchikey, standardizer_version)
);

CREATE TABLE IF NOT EXISTS target (
    id              BIGSERIAL PRIMARY KEY,
    sequence_sha256 TEXT NOT NULL UNIQUE,
    sequence        TEXT NOT NULL,
    length          INTEGER NOT NULL,
    organism        TEXT
);

CREATE TABLE IF NOT EXISTS target_alias (
    target_id BIGINT NOT NULL REFERENCES target(id) ON DELETE RESTRICT,
    source    TEXT NOT NULL,
    source_id TEXT NOT NULL,
    PRIMARY KEY (target_id, source, source_id)
);

CREATE TABLE IF NOT EXISTS assay (
    id                BIGSERIAL PRIMARY KEY,
    source_release_id INTEGER NOT NULL REFERENCES source_release(id) ON DELETE RESTRICT,
    entry_id          TEXT NOT NULL,
    assay_id_src      TEXT NOT NULL,
    name_raw          TEXT,
    description_raw   TEXT,
    name_text         TEXT,
    description_text  TEXT,
    decode_version    TEXT NOT NULL,
    UNIQUE (source_release_id, entry_id, assay_id_src)
);

COMMENT ON COLUMN assay.description_raw IS
    'Verbatim from the source file, HTML entities intact.';
COMMENT ON COLUMN assay.description_text IS
    'description_raw after html.unescape(). Both are kept so the transform is reversible.';

-- reactant_set_id -> assay, materialized once so curation does not re-run a
-- 3.2M-row join per batch.
CREATE TABLE IF NOT EXISTS assay_link (
    reactant_set_id TEXT PRIMARY KEY,
    assay_id        BIGINT NOT NULL REFERENCES assay(id) ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS publication (
    id               BIGSERIAL PRIMARY KEY,
    pmid             TEXT NOT NULL DEFAULT '',
    doi              TEXT NOT NULL DEFAULT '',
    patent_number    TEXT NOT NULL DEFAULT '',
    publication_date TEXT NOT NULL DEFAULT '',
    UNIQUE (pmid, doi, patent_number, publication_date)
);

CREATE TABLE IF NOT EXISTS activity (
    id                  BIGSERIAL PRIMARY KEY,
    source_release_id   INTEGER NOT NULL REFERENCES source_release(id) ON DELETE RESTRICT,
    raw_measurement_id  BIGINT  NOT NULL REFERENCES raw_measurement(id) ON DELETE RESTRICT,
    reactant_set_id     TEXT    NOT NULL,
    compound_id         BIGINT  NOT NULL REFERENCES compound(id) ON DELETE RESTRICT,
    target_id           BIGINT  NOT NULL REFERENCES target(id) ON DELETE RESTRICT,
    assay_id            BIGINT  REFERENCES assay(id) ON DELETE RESTRICT,
    assay_join_status   TEXT    NOT NULL,
    publication_id      BIGINT  REFERENCES publication(id) ON DELETE RESTRICT,
    measurement_type    TEXT    NOT NULL,
    relation            TEXT    NOT NULL,
    relation_raw        TEXT,
    bound_inclusive     BOOLEAN,
    value_text          TEXT    NOT NULL,
    value_numeric       DOUBLE PRECISION,
    value_unit          TEXT    NOT NULL,
    ph_text             TEXT,
    temp_c_text         TEXT,
    curation_source     TEXT,
    publication_date    TEXT,
    bindingdb_date      TEXT,
    n_protein_chains    INTEGER,
    in_benchmark_scope  BOOLEAN NOT NULL,
    scope_reason        TEXT,
    curator_version     TEXT NOT NULL,
    UNIQUE (raw_measurement_id, measurement_type)
);

COMMENT ON COLUMN activity.relation IS
    'Canonical operator: = < <= > >= ~ ?. Inclusive and exclusive bounds are kept '
    'distinct because they differ at an activity threshold.';
COMMENT ON COLUMN activity.relation_raw IS
    'The operator exactly as it appeared in the source, empty string if none.';
COMMENT ON COLUMN activity.bound_inclusive IS
    'True for <= and >=, false for < and >, NULL where inclusivity does not apply.';
COMMENT ON COLUMN activity.value_text IS
    'The original field verbatim, relation prefix included (e.g. ">10000").';
COMMENT ON COLUMN activity.value_unit IS
    'Unit from the source column header. BindingDB reports all four types in nM.';
COMMENT ON COLUMN activity.in_benchmark_scope IS
    'False means curated but outside the single-protein benchmark scope; see scope_reason.';

CREATE TABLE IF NOT EXISTS curation_exclusion (
    id                 BIGSERIAL PRIMARY KEY,
    source_release_id  INTEGER NOT NULL REFERENCES source_release(id) ON DELETE RESTRICT,
    raw_measurement_id BIGINT  NOT NULL REFERENCES raw_measurement(id) ON DELETE RESTRICT,
    rule_code          TEXT    NOT NULL,
    detail             TEXT    NOT NULL,
    curator_version    TEXT    NOT NULL,
    UNIQUE (raw_measurement_id, curator_version)
);

-- Restart bookkeeping. Curation walks raw_measurement.id ascending and records
-- how far it got, so an interrupted run resumes instead of starting over.
CREATE TABLE IF NOT EXISTS curation_run (
    source_release_id  INTEGER NOT NULL REFERENCES source_release(id) ON DELETE RESTRICT,
    curator_version    TEXT    NOT NULL,
    last_raw_id        BIGINT  NOT NULL DEFAULT 0,
    rows_seen          BIGINT  NOT NULL DEFAULT 0,
    rows_curated       BIGINT  NOT NULL DEFAULT 0,
    rows_excluded      BIGINT  NOT NULL DEFAULT 0,
    activities_written BIGINT  NOT NULL DEFAULT 0,
    started_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at        TIMESTAMPTZ,
    PRIMARY KEY (source_release_id, curator_version)
);
"""

# Built after the curation load, for the queries M4 will issue against activity.
M3_INDEX_SQL: dict[str, str] = {
    "activity_compound_idx": (
        "CREATE INDEX IF NOT EXISTS activity_compound_idx ON activity(compound_id)"
    ),
    "activity_target_idx": (
        "CREATE INDEX IF NOT EXISTS activity_target_idx ON activity(target_id)"
    ),
    "activity_type_idx": (
        "CREATE INDEX IF NOT EXISTS activity_type_idx ON activity(measurement_type, relation)"
    ),
    "activity_scope_idx": (
        "CREATE INDEX IF NOT EXISTS activity_scope_idx ON activity(in_benchmark_scope)"
    ),
}
