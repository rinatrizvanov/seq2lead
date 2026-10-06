"""Features derived from measured activities -- the ones that can leak.

Fingerprints and embeddings are functions of the input alone, so computing them
for a held-out entity gives a model nothing it could not derive at inference.
Everything in this module is different: a per-target mean pKi, an observation
count, an assay spread are all summaries of *measured outcomes*. Computed over
the whole database they would hand a model the answer to its own test.

So every query here draws from `training_visible_activities()` for the active
split and from nothing else, the SQL is composed from that function rather than
rewritten alongside it, and `assert_built_from_training_only()` re-derives the
support afterwards and refuses a cache that touched a held-out record.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

import numpy as np

from seq2lead.features.identity import FeatureSpec
from seq2lead.features.manifest import (
    evidence_digest_for_split,
    library_versions,
    manifest_from_ids,
)
from seq2lead.features.store import FeatureStore
from seq2lead.splits.assertions import LeakageError, training_visible_activities

if TYPE_CHECKING:
    import psycopg

    from seq2lead.features.manifest import InputManifest

#: Columns produced, in order. Stated explicitly because a consumer indexing by
#: position needs this to be stable across rebuilds.
TARGET_COLUMNS = ("n_obs", "mean_pki", "median_pki", "spread_pki")
COMPOUND_COLUMNS = ("n_obs", "mean_pki", "median_pki", "spread_pki")


def activity_spec(entity: str, split_name: str, threshold: float) -> FeatureSpec:
    return FeatureSpec(
        kind="activity",
        entity=entity,
        pooling="none",
        dtype="float32",
        split=split_name,
        extra={
            "columns": list(TARGET_COLUMNS if entity == "target" else COMPOUND_COLUMNS),
            "source": "training_visible_activities",
            "threshold_pki": threshold,
        },
    )


def _aggregate(conn: psycopg.Connection, split_id: int, column: str) -> list[tuple]:
    """Aggregate exact pKi per entity over training-visible activities only.

    `training_visible_activities()` returns SQL rather than rows precisely so it
    can be embedded here without a caller being able to widen it.
    """
    visible = training_visible_activities(conn, split_id)
    return conn.execute(
        f"""
        SELECT a.{column},
               count(*)                                           AS n_obs,
               avg(9 - log(a.value_numeric))                       AS mean_pki,
               percentile_cont(0.5) WITHIN GROUP (ORDER BY 9 - log(a.value_numeric))
                                                                   AS median_pki,
               coalesce(max(9 - log(a.value_numeric))
                        - min(9 - log(a.value_numeric)), 0)        AS spread_pki
        FROM activity a
        WHERE a.id IN ({visible})
          AND a.measurement_type = 'KI'
          AND a.relation = '='
          AND a.value_numeric IS NOT NULL
          AND a.value_numeric > 0
        GROUP BY a.{column}
        """,  # noqa: S608
        (),
    ).fetchall()


def build_entity_activity_features(
    conn: psycopg.Connection,
    split_id: int,
    entity: str = "target",
    threshold: float = 6.0,
    population: str = "training_visible",
) -> tuple[FeatureStore, InputManifest, list[int]]:
    """Per-entity summaries of *training* evidence. Never of held-out evidence."""
    started = time.perf_counter()
    column = "target_id" if entity == "target" else "compound_id"
    split_name = str(
        conn.execute("SELECT name FROM split_version WHERE id=%s", (split_id,)).fetchone()[0]
    )
    rows = _aggregate(conn, split_id, column)

    ids = np.zeros(len(rows), dtype=np.int64)
    vectors = np.zeros((len(rows), 4), dtype=np.float32)
    for i, (entity_id, n_obs, mean_pki, median_pki, spread) in enumerate(rows):
        ids[i] = int(entity_id)
        vectors[i] = (
            float(n_obs),
            float(mean_pki),
            float(median_pki),
            float(spread),
        )
    entity_ids = [int(r[0]) for r in rows]
    manifest = manifest_from_ids(
        conn,
        entity,
        population,
        entity_ids,
        split=split_name,
        evidence_digest=evidence_digest_for_split(conn, split_id),
    )
    return (
        FeatureStore(
            spec=activity_spec(entity, split_name, threshold),
            ids=ids,
            vectors=vectors,
            flags={},
            seconds=time.perf_counter() - started,
            manifest=manifest,
            library_versions=library_versions("activity"),
        ),
        manifest,
        sorted(entity_ids),
    )


def held_out_exposure(conn: psycopg.Connection, split_id: int, entity: str = "target") -> int:
    """How many held-out activities exist for entities the model trains on.

    Reported, not asserted -- a held-out measurement *existing* for a trained-on
    target is normal and unavoidable. What must not happen is it reaching the
    aggregate, which `assert_no_held_out_activity_in_support()` is the hard check
    for. This number is the size of the margin that check is protecting.
    """
    column = "target_id" if entity == "target" else "compound_id"
    visible = training_visible_activities(conn, split_id)
    # A CTE, not a repeated `IN (subquery)`: the visible set is ~400k rows and
    # inlining it twice turns a seconds-long count into a minutes-long one.
    leaked = conn.execute(
        f"""
        WITH visible AS MATERIALIZED ({visible}),
        trained_entities AS (
            SELECT DISTINCT a.{column} AS entity_id
            FROM visible v JOIN activity a ON a.id = v.activity_id
        )
        SELECT count(*)
        FROM split_activity_assignment s
        JOIN activity a ON a.id = s.activity_id
        JOIN trained_entities te ON te.entity_id = a.{column}
        WHERE s.split_id = %s
          AND s.partition IN ('validation','test')
          AND a.measurement_type = 'KI'
          AND a.relation = '='
          AND a.value_numeric > 0
        """,  # noqa: S608
        (split_id,),
    ).fetchone()[0]
    return int(leaked)


def assert_no_held_out_activity_in_support(conn: psycopg.Connection, split_id: int) -> None:
    """The hard check: the visible set must contain no held-out activity at all."""
    visible = training_visible_activities(conn, split_id)
    leaked = conn.execute(
        f"""
        SELECT count(*) FROM ({visible}) v
        JOIN split_activity_assignment s ON s.activity_id = v.activity_id
        WHERE s.split_id = %s AND s.partition IN ('validation','test','excluded')
        """,  # noqa: S608
        (split_id,),
    ).fetchone()[0]
    if leaked:
        raise LeakageError(
            f"{leaked:,} held-out activities are reachable through "
            f"training_visible_activities() for split {split_id}. Every activity-derived "
            "feature built from it would carry held-out outcomes into training."
        )
