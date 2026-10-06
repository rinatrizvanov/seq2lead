"""M7 feature caches: identity, integrity, and what may read measured outcomes."""

from __future__ import annotations

import numpy as np
import pytest

from seq2lead.db import connect
from seq2lead.features import ecfp, plm
from seq2lead.features.identity import FeatureSpec
from seq2lead.features.store import FeatureCacheError, FeatureStore, load_features, sha256_file

# ===================================================== identity: what makes a cache


def _base() -> FeatureSpec:
    return plm.esm_spec()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("model_revision", "deadbeef" * 5),
        ("pooling", "cls_token"),
        ("length_policy", "truncate:1022"),
        ("max_length", 1022),
        ("dtype", "float16"),
        ("model", "facebook/esm2_t6_8M_UR50D"),
    ],
)
def test_anything_that_changes_a_vector_changes_the_identity(field, value) -> None:
    """A cache keyed only by 'ESM-2' would silently reuse vectors across these."""
    from dataclasses import replace

    base = _base()
    assert replace(base, **{field: value}).sha256() != base.sha256()


def test_identity_is_stable_across_processes() -> None:
    """The hash must not depend on dict ordering or float formatting."""
    assert _base().sha256() == _base().sha256()
    assert len(_base().sha256()) == 64  # noqa: PLR2004


def test_identity_ignores_things_that_cannot_change_a_vector() -> None:
    """Device and batch size are not in the spec, so a CPU rebuild is the same cache."""
    canonical = _base().canonical()
    for irrelevant in ("device", "batch", "workers", "mps", "cuda", "progress"):
        assert irrelevant not in canonical


def test_cache_name_carries_the_hash() -> None:
    spec = _base()
    assert spec.short() in spec.cache_name()
    assert spec.kind in spec.cache_name()


def test_ecfp_geometry_is_part_of_the_identity() -> None:
    a = ecfp.ecfp_spec("m3/v2", radius=2, n_bits=2048)
    assert a.sha256() != ecfp.ecfp_spec("m3/v2", radius=3, n_bits=2048).sha256()
    assert a.sha256() != ecfp.ecfp_spec("m3/v2", radius=2, n_bits=1024).sha256()
    # The curation generation the structures came from also counts.
    assert a.sha256() != ecfp.ecfp_spec("m3/v1", radius=2, n_bits=2048).sha256()


def test_activity_features_are_a_different_object_per_split() -> None:
    """The training set differs, so the feature differs, even with identical code."""
    from seq2lead.features.activity import activity_spec

    a = activity_spec("target", "temporal_proxy-v4", 6.0)
    b = activity_spec("target", "cold_protein-v3", 6.0)
    assert a.sha256() != b.sha256()


# ============================================================ storage integrity


def _toy(tmp_path) -> FeatureStore:
    spec = FeatureSpec(kind="toy", entity="compound", extra={"n": 3})
    return FeatureStore(
        spec=spec,
        ids=np.array([7, 8, 9], dtype=np.int64),
        vectors=np.arange(9, dtype=np.float32).reshape(3, 3),
        flags={},
    )


def test_vectors_round_trip(tmp_path) -> None:
    store = _toy(tmp_path)
    path, digest = store.write(tmp_path)
    assert sha256_file(path) == digest
    with np.load(path) as data:
        assert np.array_equal(data["ids"], store.ids)
        assert np.array_equal(data["vectors"], store.vectors)


def test_bitpacked_fingerprints_round_trip() -> None:
    bits = np.zeros((2, 2048), dtype=np.uint8)
    bits[0, [1, 5, 2047]] = 1
    bits[1, [0, 1023]] = 1
    packed = np.packbits(bits, axis=1)
    assert packed.shape == (2, 256)
    assert np.array_equal(ecfp.unpack(packed), bits)


@pytest.mark.requires_db
def test_a_tampered_cache_is_refused() -> None:
    """A vector file that no longer matches its digest must not be silently used."""
    with connect() as conn:
        row = conn.execute(
            "SELECT name FROM feature_version WHERE superseded_by IS NULL "
            "AND completeness='full' ORDER BY id LIMIT 1"
        ).fetchone()
        if row is None:
            pytest.skip("no feature cache built")
        name = str(row[0])
        original = conn.execute(
            "SELECT storage_sha256 FROM feature_version WHERE name=%s", (name,)
        ).fetchone()[0]
        conn.execute("UPDATE feature_version SET storage_sha256=%s WHERE name=%s", ("0" * 64, name))
        try:
            with pytest.raises(FeatureCacheError, match="digest"):
                load_features(conn, name)
        finally:
            conn.execute(
                "UPDATE feature_version SET storage_sha256=%s WHERE name=%s", (original, name)
            )


@pytest.mark.requires_db
def test_a_superseded_cache_is_refused() -> None:
    with connect() as conn:
        row = conn.execute(
            "SELECT name FROM feature_version WHERE superseded_by IS NULL "
            "AND completeness='full' ORDER BY id LIMIT 1"
        ).fetchone()
        if row is None:
            pytest.skip("no feature cache built")
        name = str(row[0])
        conn.execute("UPDATE feature_version SET superseded_by=%s WHERE name=%s", ("newer", name))
        try:
            with pytest.raises(FeatureCacheError, match="superseded"):
                load_features(conn, name)
        finally:
            conn.execute("UPDATE feature_version SET superseded_by=NULL WHERE name=%s", (name,))


# ====================================================== leakage: measured outcomes


@pytest.mark.requires_db
def test_activity_features_read_only_training_visible_activities() -> None:
    """The check that separates an input-only feature from a leaking one."""
    from seq2lead.features.activity import _aggregate, assert_no_held_out_activity_in_support
    from seq2lead.splits.assertions import training_visible_activities

    with connect() as conn:
        row = conn.execute(
            "SELECT id FROM split_version WHERE split_type='temporal_proxy' "
            "AND superseded_by IS NULL ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if row is None:
            pytest.skip("no current temporal split")
        split_id = int(row[0])
        assert_no_held_out_activity_in_support(conn, split_id)

        gated = {int(r[0]): float(r[1]) for r in _aggregate(conn, split_id, "target_id")}
        # The same aggregate with the gate removed. If these agreed, the gate
        # would be doing nothing and this test would be worthless.
        ungated = {
            int(r[0]): float(r[1])
            for r in conn.execute(
                "SELECT a.target_id, count(*) FROM activity a "
                "WHERE a.measurement_type='KI' AND a.relation='=' AND a.value_numeric > 0 "
                "GROUP BY a.target_id"
            ).fetchall()
        }
        visible_sql = training_visible_activities(conn, split_id)
        held_out_reachable = conn.execute(
            f"SELECT count(*) FROM ({visible_sql}) v "  # noqa: S608
            "JOIN split_activity_assignment s ON s.activity_id = v.activity_id "
            "WHERE s.split_id=%s AND s.partition <> 'train'",
            (split_id,),
        ).fetchone()[0]

    assert held_out_reachable == 0
    shared = set(gated) & set(ungated)
    assert shared, "no overlap to compare"
    # Strictly fewer observations once held-out records are excluded.
    assert all(gated[t] <= ungated[t] for t in shared)
    assert any(gated[t] < ungated[t] for t in shared), (
        "the training gate changed nothing, so it is not being applied"
    )


@pytest.mark.requires_db
def test_input_only_features_may_cover_held_out_entities() -> None:
    """Fingerprints and embeddings are functions of the input, so this is not a leak."""
    with connect() as conn:
        row = conn.execute(
            "SELECT id, storage_path FROM feature_version WHERE kind='ecfp4' "
            "AND superseded_by IS NULL AND completeness='full' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if row is None:
            pytest.skip("no ecfp4 cache built")
        split_id = conn.execute(
            "SELECT id FROM split_version WHERE split_type='cold_protein' "
            "AND superseded_by IS NULL ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if split_id is None:
            pytest.skip("no cold_protein split")
        ids, _ = load_features(
            conn,
            str(
                conn.execute(
                    "SELECT name FROM feature_version WHERE id=%s", (int(row[0]),)
                ).fetchone()[0]
            ),
        )
        held_out = {
            int(r[0])
            for r in conn.execute(
                "SELECT DISTINCT compound_id FROM split_pair_assignment "
                "WHERE split_id=%s AND partition='test' LIMIT 500",
                (int(split_id[0]),),
            ).fetchall()
        }
    covered = held_out & set(ids.tolist())
    assert covered == held_out, "input-only features should cover held-out compounds too"


# ============================================================ the length policy


def test_the_length_policy_is_recorded_in_the_identity() -> None:
    """A truncated cache and a full cache must never share a name."""
    full = plm.esm_spec(length_policy="full")
    truncated = plm.esm_spec(length_policy=f"truncate:{plm.TRAINING_WINDOW}")
    assert full.sha256() != truncated.sha256()
    assert full.cache_name() != truncated.cache_name()
    assert full.length_policy == "full"


def test_the_training_window_is_recorded_but_is_not_a_ceiling() -> None:
    """ESM-2 here uses rotary embeddings; 1,022 is a regime boundary, not a limit."""
    spec = plm.esm_spec()
    assert spec.extra["training_window"] == 1022  # noqa: PLR2004
    assert spec.max_length > 1022  # noqa: PLR2004


@pytest.mark.requires_db
def test_targets_past_the_training_window_are_flagged() -> None:
    """Every over-window target in the cache's OWN population must be flagged.

    Scoped to the cache's declared population: comparing against every row in
    `target` would fail for a partial cache and would silently pass for a cache
    built over the wrong population.
    """
    with connect() as conn:
        row = conn.execute(
            "SELECT id, population FROM feature_version WHERE kind='esm2' "
            "AND superseded_by IS NULL AND completeness='full' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if row is None:
            pytest.skip("no complete esm2 cache built")
        feature_id, population = int(row[0]), str(row[1])
        release = population.partition(":")[2]
        if not release.isdigit():
            pytest.skip(f"population {population!r} is not release-scoped")
        flagged, expected = conn.execute(
            """
            SELECT (SELECT count(*) FROM feature_entity_flag
                    WHERE feature_id = %s AND flag = 'over_training_window'),
                   (SELECT count(*) FROM target t WHERE length(t.sequence) > 1022
                      AND EXISTS (SELECT 1 FROM activity a
                                  WHERE a.target_id = t.id AND a.source_release_id = %s))
            """,
            (feature_id, int(release)),
        ).fetchone()
    assert int(flagged) == int(expected)


@pytest.mark.requires_db
def test_every_all_zero_fingerprint_is_accounted_for() -> None:
    """A zero row must mean "we told you", not "we quietly failed".

    A model reads an all-zero fingerprint as a real molecule with no features, so
    every one has to be traceable to a flag rather than appearing by accident.
    """
    with connect() as conn:
        row = conn.execute(
            "SELECT id, name FROM feature_version WHERE kind='ecfp4' "
            "AND superseded_by IS NULL AND completeness='full' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if row is None:
            pytest.skip("no ecfp4 cache built")
        flagged = {
            int(r[0])
            for r in conn.execute(
                "SELECT entity_id FROM feature_entity_flag "
                "WHERE feature_id=%s AND flag='unparseable_smiles'",
                (int(row[0]),),
            ).fetchall()
        }
        ids, vectors = load_features(conn, str(row[1]))
    zero_ids = set(ids[vectors.sum(axis=1) == 0].tolist())
    assert zero_ids == flagged, (
        f"{len(zero_ids - flagged)} all-zero rows carry no flag; "
        f"{len(flagged - zero_ids)} flagged rows are not zero"
    )


# ============================================ fixtures must not leak into production


@pytest.mark.requires_db
def test_fixtures_leave_no_synthetic_entities_behind() -> None:
    """The regression for the contamination that reached the first M7 caches.

    Two test molecules (ethanol, propane) and two test proteins survived their
    fixtures' cleanup and were embedded as if they were real data. Nothing
    curated should be activity-free *and* newer than the pinned release's own
    entities.
    """
    from seq2lead.db.maintenance import no_entity_leak

    with connect() as conn:
        before = conn.execute("SELECT count(*) FROM target").fetchone()[0]
    with no_entity_leak():
        pass
    with connect() as conn:
        after = conn.execute("SELECT count(*) FROM target").fetchone()[0]
        synthetic = conn.execute(
            "SELECT count(*) FROM target WHERE organism IS NULL AND length < 25 "
            "AND NOT EXISTS (SELECT 1 FROM activity a WHERE a.target_id = target.id)"
        ).fetchone()[0]
    assert before == after, "the guard deleted something it should not have"
    assert synthetic == 0, f"{synthetic} synthetic-looking targets remain"
