"""DDL for the M6 split layer.

Splits are immutable, versioned artifacts. A changed rule mints a new version
rather than editing one, exactly as endpoint versions do.

The contract these tables implement is `docs/SPLITS.md`, written before this
code. The load-bearing choices:

* **Whole compound-target pairs move together** for every split except
  `temporal_proxy`. A pair is the smallest scored object, so splitting one across
  partitions would put a measurement of a test pair into training.
* **`temporal_proxy` assigns activities**, then aggregates inside each partition.
  A pair may therefore appear in more than one partition, which is why the
  primary key includes `partition` rather than forbidding it.
* **Recurrent pairs are a stratum, not a leak.** They are enumerated and scored
  separately from new pairs, never pooled.
"""

from __future__ import annotations

M6_SQL = """
CREATE TABLE IF NOT EXISTS split_version (
    id              SERIAL PRIMARY KEY,
    name            TEXT NOT NULL UNIQUE,
    split_type      TEXT NOT NULL,
    endpoint_id     INTEGER NOT NULL REFERENCES endpoint_version(id) ON DELETE RESTRICT,
    seed            INTEGER NOT NULL,
    params          JSONB   NOT NULL DEFAULT '{}'::jsonb,
    builder_version TEXT    NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    n_train         BIGINT NOT NULL DEFAULT 0,
    n_validation    BIGINT NOT NULL DEFAULT 0,
    n_test          BIGINT NOT NULL DEFAULT 0,
    n_excluded      BIGINT NOT NULL DEFAULT 0,
    notes           TEXT
);

COMMENT ON COLUMN split_version.split_type IS
    'random_pair | cold_protein | chemistry_disjoint | label_reversal | temporal_proxy';

-- `partition` is part of the key so temporal_proxy can place a recurrent pair on
-- both sides of the cut. For every other split type the zero-overlap assertion
-- requires exactly one row per pair.
CREATE TABLE IF NOT EXISTS split_pair_assignment (
    split_id    INTEGER NOT NULL REFERENCES split_version(id) ON DELETE CASCADE,
    compound_id BIGINT  NOT NULL REFERENCES compound(id) ON DELETE RESTRICT,
    target_id   BIGINT  NOT NULL REFERENCES target(id) ON DELETE RESTRICT,
    partition   TEXT    NOT NULL,
    stratum     TEXT,
    PRIMARY KEY (split_id, compound_id, target_id, partition)
);

COMMENT ON COLUMN split_pair_assignment.stratum IS
    'temporal_proxy only: new | recurrent. Scored separately, never pooled.';

-- temporal_proxy only. Every activity lands in exactly one partition.
CREATE TABLE IF NOT EXISTS split_activity_assignment (
    split_id    INTEGER NOT NULL REFERENCES split_version(id) ON DELETE CASCADE,
    activity_id BIGINT  NOT NULL REFERENCES activity(id) ON DELETE RESTRICT,
    partition   TEXT    NOT NULL,
    date_source TEXT    NOT NULL,
    PRIMARY KEY (split_id, activity_id)
);

COMMENT ON COLUMN split_activity_assignment.date_source IS
    'publication | bindingdb -- which date was available and therefore used.';

-- Cluster memberships backing cold_protein and chemistry_disjoint. Kept so a
-- split can be audited without re-running the clustering.
CREATE TABLE IF NOT EXISTS target_cluster (
    method     TEXT   NOT NULL,
    threshold  NUMERIC(4,3) NOT NULL,
    target_id  BIGINT NOT NULL REFERENCES target(id) ON DELETE RESTRICT,
    cluster_id TEXT   NOT NULL,
    PRIMARY KEY (method, threshold, target_id)
);

CREATE TABLE IF NOT EXISTS compound_cluster (
    method      TEXT   NOT NULL,
    compound_id BIGINT NOT NULL REFERENCES compound(id) ON DELETE RESTRICT,
    cluster_id  TEXT   NOT NULL,
    PRIMARY KEY (method, compound_id)
);

COMMENT ON TABLE compound_cluster IS
    'Bemis-Murcko scaffold per compound. Butina clustering on ECFP4 is O(n^2) and '
    'infeasible at ~400k compounds; scaffold grouping is the standard alternative '
    'and is what chemistry_disjoint uses.';
"""

M6_INDEX_SQL: dict[str, str] = {
    "split_pair_partition_idx": (
        "CREATE INDEX IF NOT EXISTS split_pair_partition_idx "
        "ON split_pair_assignment(split_id, partition)"
    ),
    "split_activity_partition_idx": (
        "CREATE INDEX IF NOT EXISTS split_activity_partition_idx "
        "ON split_activity_assignment(split_id, partition)"
    ),
    "target_cluster_idx": (
        "CREATE INDEX IF NOT EXISTS target_cluster_idx ON target_cluster(method, cluster_id)"
    ),
    "compound_cluster_idx": (
        "CREATE INDEX IF NOT EXISTS compound_cluster_idx ON compound_cluster(method, cluster_id)"
    ),
}
