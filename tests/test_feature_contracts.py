"""The load/register contract, feature coverage, and schema isolation."""

from __future__ import annotations

import tempfile
from pathlib import Path

import numpy as np
import pytest

from seq2lead.db import connect
from seq2lead.features import plm
from seq2lead.features.coverage import (
    CoverageError,
    assert_feature_coverage,
    check_all_splits,
)
from seq2lead.features.manifest import build_manifest
from seq2lead.features.store import FeatureCacheError, FeatureStore, load_features, register
from seq2lead.features.validate import CacheInvalid

# ===================================================== loading by spec + manifest


@pytest.mark.requires_db
def test_a_spec_without_its_manifest_cannot_identify_a_cache() -> None:
    """The regression: the spec hash alone was looked up in the identity column.

    Once identities became manifest-aware, `spec.sha256()` could never match a
    stored row, so every spec-based load silently failed to resolve. Refusing is
    correct -- the spec does not say which entities went in.
    """
    with connect() as conn:
        with pytest.raises(FeatureCacheError, match="requires the InputManifest"):
            load_features(conn, plm.esm_spec())


@pytest.mark.requires_db
def test_loading_by_spec_and_manifest_matches_loading_by_name() -> None:
    with connect() as conn:
        row = conn.execute(
            "SELECT name, population FROM feature_version WHERE kind='esm2' "
            "AND superseded_by IS NULL AND completeness='full' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if row is None:
            pytest.skip("no complete esm2 cache")
        manifest, _ = build_manifest(conn, "target", str(row[1]))
        by_name = load_features(conn, str(row[0]))
        by_spec = load_features(conn, plm.esm_spec(), manifest)
    assert np.array_equal(by_name[0], by_spec[0])
    assert np.array_equal(by_name[1], by_spec[1])


@pytest.mark.requires_db
def test_a_mismatched_manifest_resolves_to_nothing() -> None:
    """A different population must not quietly return the full cache."""
    with connect() as conn:
        partial, _ = build_manifest(conn, "target", "release:117", limit=3)
        with pytest.raises(FeatureCacheError, match="no feature cache registered"):
            load_features(conn, plm.esm_spec(), partial)


@pytest.mark.requires_db
def test_superseded_caches_need_an_explicit_opt_in() -> None:
    with connect() as conn:
        row = conn.execute(
            "SELECT name FROM feature_version WHERE superseded_by IS NOT NULL ORDER BY id LIMIT 1"
        ).fetchone()
        if row is None:
            pytest.skip("nothing superseded")
        with pytest.raises(FeatureCacheError, match="superseded"):
            load_features(conn, str(row[0]))
        ids, _ = load_features(conn, str(row[0]), allow_superseded=True)
    assert ids.shape[0] > 0


# ======================================================= validation at registration


def _store(ids, vectors, manifest):
    return FeatureStore(
        spec=plm.esm_spec(),
        ids=np.asarray(ids, dtype=np.int64),
        vectors=np.asarray(vectors, dtype=np.float32),
        flags={},
        manifest=manifest,
    )


@pytest.mark.requires_db
@pytest.mark.parametrize(
    ("label", "mutate"),
    [
        ("wrong population", lambda ids: (ids[:-1] + [999_999_999], np.zeros((len(ids), 4)))),
        ("short population", lambda ids: (ids[:10], np.zeros((10, 4)))),
        ("duplicate ids", lambda ids: ([ids[0]] * len(ids), np.zeros((len(ids), 4)))),
        ("misaligned rows", lambda ids: (ids, np.zeros((len(ids) - 1, 4)))),
        ("non-finite values", lambda ids: (ids, np.full((len(ids), 4), np.nan))),
    ],
)
def test_register_refuses_an_invalid_cache(label, mutate) -> None:
    """Validation is enforced in `register()`, not left to whoever calls it.

    A caller using the library directly would otherwise bypass every check the
    CLI runs, and the bad cache would be readable ever after.
    """
    with connect() as conn:
        manifest, ids = build_manifest(conn, "target", "release:117", limit=20)
        bad_ids, vectors = mutate(list(ids))
        with tempfile.TemporaryDirectory() as tmp:
            with pytest.raises(CacheInvalid):
                register(
                    conn,
                    _store(bad_ids, vectors, manifest),
                    builder_version="test",
                    root=Path(tmp),
                )
            assert not list(Path(tmp).glob("*.npz")), (
                f"{label}: a rejected cache was still written to disk"
            )


@pytest.mark.requires_db
def test_register_accepts_a_cache_that_matches_its_manifest() -> None:
    with connect() as conn:
        manifest, ids = build_manifest(conn, "target", "release:117", limit=20)
        with tempfile.TemporaryDirectory() as tmp:
            store = _store(list(ids), np.zeros((len(ids), 4)), manifest)
            # Written and registered; rolled back by the surrounding connection.
            feature_id = register(conn, store, builder_version="test", root=Path(tmp))
            assert feature_id > 0
            assert list(Path(tmp).glob("*.npz"))
            conn.rollback()


# ================================================================ feature coverage


@pytest.mark.requires_db
def test_every_scored_entity_has_a_feature_vector() -> None:
    """Cache identity proves a cache matches its own population, not the split's."""
    with connect() as conn:
        checks = assert_feature_coverage(conn)
    assert checks, "no splits to check"
    for check in checks:
        assert check.missing == 0
        assert check.covered == check.scored


@pytest.mark.requires_db
def test_coverage_is_checked_for_every_active_split_and_both_entities() -> None:
    with connect() as conn:
        checks = check_all_splits(conn)
        active = {
            str(r[0])
            for r in conn.execute(
                "SELECT name FROM split_version WHERE superseded_by IS NULL"
            ).fetchall()
        }
    assert {c.split for c in checks} == active
    for split in active:
        assert {c.entity for c in checks if c.split == split} == {"compound", "target"}


@pytest.mark.requires_db
def test_unusable_features_are_counted_separately_from_missing_ones() -> None:
    """An all-zero fingerprint is covered but not usable; the two must not merge."""
    with connect() as conn:
        checks = check_all_splits(conn)
    compound_checks = [c for c in checks if c.entity == "compound"]
    assert compound_checks
    for check in compound_checks:
        assert check.flagged > 0, "the unparseable compounds should still be flagged"
        assert check.missing == 0
        assert check.usable == check.covered - check.flagged


@pytest.mark.requires_db
def test_coverage_failure_names_the_split_and_the_gap() -> None:
    """A coverage error has to be actionable, not just a boolean."""
    from seq2lead.features import coverage

    with connect() as conn:
        original = coverage._cached_ids  # noqa: SLF001
        coverage._cached_ids = lambda _conn, _fid: set()  # noqa: SLF001
        try:
            with pytest.raises(CoverageError) as excinfo:
                assert_feature_coverage(conn)
        finally:
            coverage._cached_ids = original  # noqa: SLF001
    message = str(excinfo.value)
    assert "scored entities absent from" in message
    assert "temporal_proxy-v4" in message or "random_pair-v3" in message


# =============================================================== schema isolation


@pytest.mark.requires_db
def test_an_isolated_schema_starts_empty_and_leaves_the_corpus_alone() -> None:
    from seq2lead.db.isolation import isolated_schema

    with connect() as conn:
        before = conn.execute("SELECT count(*) FROM compound").fetchone()[0]
    with isolated_schema("seq2lead_test_contract"):
        with connect() as conn:
            inside = conn.execute("SELECT count(*) FROM compound").fetchone()[0]
            path = conn.execute("SHOW search_path").fetchone()[0]
    with connect() as conn:
        after = conn.execute("SELECT count(*) FROM compound").fetchone()[0]
        leftover = conn.execute(
            "SELECT count(*) FROM information_schema.schemata "
            "WHERE schema_name = 'seq2lead_test_contract'"
        ).fetchone()[0]
    assert inside == 0, "the test schema saw the corpus"
    assert "seq2lead_test_contract" in path
    assert before == after
    assert leftover == 0, "the schema was not dropped"


@pytest.mark.requires_db
def test_the_shadow_inventory_is_derived_not_hand_written() -> None:
    """The first list named 19 tables where the database had 31.

    Twelve -- `pair_label_support`, `target_cluster`, `schema_migration` among
    them -- were absent, so a query against any of them inside a test schema
    would have reached the corpus. The inventory is now the corpus schema itself.
    """
    from seq2lead.db.isolation import corpus_tables, is_fully_shadowed

    with connect() as conn:
        expected = corpus_tables(conn)
        assert len(expected) > 25, f"only {len(expected)} corpus tables found"
        for required in (
            "pair_label_support",
            "pair_regression_support",
            "target_cluster",
            "compound_cluster",
            "split_partition_endpoint",
            "split_target_stratum",
            "raw_record",
            "raw_sequence",
            "assay_link",
            "ingest_exclusion",
            "target_organism",
            "schema_migration",
        ):
            assert required in expected, f"{required} missing from the inventory"
        # Nothing resolves in a schema that does not exist.
        assert set(is_fully_shadowed(conn, "definitely_not_a_schema")) == expected


@pytest.mark.requires_db
def test_a_missing_table_is_detected_before_any_fixture_runs() -> None:
    """The guard must fire at setup, not when a query silently hits the corpus."""
    from seq2lead.db import transaction
    from seq2lead.db.isolation import IsolationError, is_fully_shadowed, isolated_schema

    schema = "seq2lead_test_incomplete"
    with isolated_schema(schema):
        with connect() as conn:
            assert is_fully_shadowed(conn, schema) == []
        # Simulate a migration that forgot a table.
        with transaction() as conn:
            conn.execute(f'DROP TABLE IF EXISTS "{schema}".pair_label_support CASCADE')
        with connect() as conn:
            gaps = is_fully_shadowed(conn, schema)
        assert gaps == ["pair_label_support"], gaps

    # And the context manager itself refuses to hand over such a schema.
    import seq2lead.db.isolation as isolation

    original = isolation.is_fully_shadowed
    isolation.is_fully_shadowed = lambda _conn, _schema: ["pair_label_support"]
    try:
        with pytest.raises(IsolationError, match="does not shadow"):
            with isolated_schema("seq2lead_test_refused"):
                pytest.fail("fixture body ran despite incomplete isolation")
    finally:
        isolation.is_fully_shadowed = original

    with connect() as conn:
        left = conn.execute(
            "SELECT count(*) FROM information_schema.schemata "
            "WHERE schema_name IN ('seq2lead_test_incomplete', 'seq2lead_test_refused')"
        ).fetchone()[0]
    assert left == 0, "a refused or finished schema was left behind"


def test_isolation_rejects_an_unsafe_schema_name() -> None:
    from seq2lead.db.isolation import IsolationError, isolated_schema

    with pytest.raises(IsolationError, match="unsafe schema name"):
        with isolated_schema('evil"; DROP SCHEMA public CASCADE; --'):
            pass


@pytest.mark.requires_db
def test_corpus_connection_escapes_an_active_test_schema() -> None:
    """Isolation must not silently turn corpus assertions into skips.

    Two tests that check real corpus properties began skipping when their
    modules were isolated, because the schema they landed in was empty. A test
    that skips is a test that stopped testing, so corpus assertions ask for the
    corpus explicitly.
    """
    from seq2lead.db.isolation import corpus_connection, isolated_schema

    with connect() as conn:
        expected = conn.execute("SELECT count(*) FROM compound").fetchone()[0]
    with isolated_schema("seq2lead_test_escape"):
        with connect() as isolated:
            assert isolated.execute("SELECT count(*) FROM compound").fetchone()[0] == 0
        with corpus_connection() as corpus:
            assert corpus.execute("SELECT count(*) FROM compound").fetchone()[0] == expected
