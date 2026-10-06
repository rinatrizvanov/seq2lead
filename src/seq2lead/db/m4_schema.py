"""DDL for the M4 endpoint layer.

Versioned and regenerable. Nothing here is a split: `pair_label` records whether
a pair would be *eligible* for evaluation and why not, but assigns no train/test
partition. Partitioning is M6.

Scope: **Ki only**. IC50, Kd and EC50 remain in `activity`, untouched and
un-merged.
"""

from __future__ import annotations

M4_SQL = """
CREATE TABLE IF NOT EXISTS endpoint_version (
    id                  SERIAL PRIMARY KEY,
    name                TEXT NOT NULL UNIQUE,
    measurement_type    TEXT NOT NULL,
    threshold_pki       DOUBLE PRECISION NOT NULL,
    discordance_pki     DOUBLE PRECISION NOT NULL,
    source_release_id   INTEGER NOT NULL REFERENCES source_release(id) ON DELETE RESTRICT,
    curator_version     TEXT NOT NULL,
    builder_version     TEXT NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    superseded_by       TEXT,
    superseded_reason   TEXT,
    n_activities_in     BIGINT NOT NULL DEFAULT 0,
    n_unusable_magnitude BIGINT NOT NULL DEFAULT 0,
    n_no_constraint     BIGINT NOT NULL DEFAULT 0
);

COMMENT ON COLUMN endpoint_version.threshold_pki IS
    'Predeclared provisional classification threshold, fixed before any split '
    'exists and before any model is trained. Regenerate other versions to vary it.';

-- Exact, positive, finite '=' records only. Censored values never enter here:
-- there is no meaningful median of censoring thresholds.
CREATE TABLE IF NOT EXISTS pair_regression (
    id            BIGSERIAL PRIMARY KEY,
    endpoint_id   INTEGER NOT NULL REFERENCES endpoint_version(id) ON DELETE RESTRICT,
    compound_id   BIGINT  NOT NULL REFERENCES compound(id) ON DELETE RESTRICT,
    target_id     BIGINT  NOT NULL REFERENCES target(id) ON DELETE RESTRICT,
    n_obs         INTEGER NOT NULL,
    p_median      DOUBLE PRECISION NOT NULL,
    p_min         DOUBLE PRECISION NOT NULL,
    p_max         DOUBLE PRECISION NOT NULL,
    p_mad         DOUBLE PRECISION NOT NULL,
    p_spread      DOUBLE PRECISION NOT NULL,
    is_discordant BOOLEAN NOT NULL,
    n_assays      INTEGER NOT NULL,
    n_publications INTEGER NOT NULL,
    in_benchmark_scope BOOLEAN NOT NULL,
    excluded_from_eval BOOLEAN NOT NULL DEFAULT FALSE,
    eval_exclusion_reason TEXT,
    UNIQUE (endpoint_id, compound_id, target_id)
);

COMMENT ON COLUMN pair_regression.eval_exclusion_reason IS
    'Why this pair would be held out of validation/test if it were evaluated. '
    'Records intent only; no partition is assigned here.';

CREATE TABLE IF NOT EXISTS pair_regression_support (
    pair_id     BIGINT NOT NULL REFERENCES pair_regression(id) ON DELETE CASCADE,
    activity_id BIGINT NOT NULL REFERENCES activity(id) ON DELETE RESTRICT,
    PRIMARY KEY (pair_id, activity_id)
);

-- Exact points and censored bounds, combined by interval intersection.
CREATE TABLE IF NOT EXISTS pair_label (
    id              BIGSERIAL PRIMARY KEY,
    endpoint_id     INTEGER NOT NULL REFERENCES endpoint_version(id) ON DELETE RESTRICT,
    compound_id     BIGINT  NOT NULL REFERENCES compound(id) ON DELETE RESTRICT,
    target_id       BIGINT  NOT NULL REFERENCES target(id) ON DELETE RESTRICT,
    label           TEXT    NOT NULL,
    status          TEXT    NOT NULL,
    evidence        TEXT    NOT NULL,
    lo_pki          DOUBLE PRECISION,
    lo_inclusive    BOOLEAN,
    hi_pki          DOUBLE PRECISION,
    hi_inclusive    BOOLEAN,
    exact_median_pki DOUBLE PRECISION,
    n_exact         INTEGER NOT NULL,
    n_censored      INTEGER NOT NULL,
    n_exact_outside_bounds INTEGER NOT NULL DEFAULT 0,
    in_benchmark_scope BOOLEAN NOT NULL,
    excluded_from_eval BOOLEAN NOT NULL DEFAULT FALSE,
    eval_exclusion_reason TEXT,
    UNIQUE (endpoint_id, compound_id, target_id)
);

COMMENT ON COLUMN pair_label.label IS
    'active | inactive | ambiguous | none. "none" means no benchmark label is '
    'assigned, and status says why.';
COMMENT ON COLUMN pair_label.status IS
    'ok | empty_intersection | exact_bound_conflict | no_usable_evidence';
COMMENT ON COLUMN pair_label.evidence IS
    'exact | censored | both | none -- which kinds of record supported the label.';

CREATE TABLE IF NOT EXISTS pair_label_support (
    pair_id     BIGINT NOT NULL REFERENCES pair_label(id) ON DELETE CASCADE,
    activity_id BIGINT NOT NULL REFERENCES activity(id) ON DELETE RESTRICT,
    role        TEXT   NOT NULL,
    PRIMARY KEY (pair_id, activity_id)
);
"""

M4_INDEX_SQL: dict[str, str] = {
    "pair_regression_target_idx": (
        "CREATE INDEX IF NOT EXISTS pair_regression_target_idx "
        "ON pair_regression(endpoint_id, target_id)"
    ),
    "pair_label_target_idx": (
        "CREATE INDEX IF NOT EXISTS pair_label_target_idx "
        "ON pair_label(endpoint_id, target_id, label)"
    ),
    "pair_label_status_idx": (
        "CREATE INDEX IF NOT EXISTS pair_label_status_idx ON pair_label(endpoint_id, status)"
    ),
}
