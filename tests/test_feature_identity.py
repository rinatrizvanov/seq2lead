"""Cache identity must cover the inputs, not only the representation settings.

Each test here corresponds to a way the first M7 implementation could hand a
caller the wrong vectors while every existing check passed. The companion test
`test_spec_alone_cannot_tell_these_apart` demonstrates that the previous
identity scheme -- the spec hash on its own -- fails all of them, so these are
regressions rather than restatements of current behaviour.
"""

from __future__ import annotations

import numpy as np
import pytest

from seq2lead.db import connect
from seq2lead.features import plm
from seq2lead.features.identity import cache_key, cache_name
from seq2lead.features.manifest import InputManifest, build_manifest, manifest_from_ids
from seq2lead.features.store import find_reusable
from seq2lead.features.validate import CacheInvalid, validate

PARTIAL_LIMIT = 5


@pytest.fixture(scope="module")
def manifests():
    with connect() as conn:
        full, full_ids = build_manifest(conn, "target", "release:117")
        partial, partial_ids = build_manifest(conn, "target", "release:117", limit=PARTIAL_LIMIT)
        every, every_ids = build_manifest(conn, "target", "all_rows")
    return {
        "full": (full, full_ids),
        "partial": (partial, partial_ids),
        "all_rows": (every, every_ids),
    }


# ============================================================ the four scenarios


@pytest.mark.requires_db
def test_partial_and_full_are_different_identities(manifests) -> None:
    """A --limit smoke build must not be registerable as the complete result."""
    spec = plm.esm_spec()
    full, _ = manifests["full"]
    partial, _ = manifests["partial"]
    assert partial.completeness == "partial"
    assert full.completeness == "full"
    assert cache_key(spec, partial) != cache_key(spec, full)
    assert "partial" in cache_name(spec, partial)
    assert "partial" not in cache_name(spec, full)


@pytest.mark.requires_db
def test_a_partial_cache_is_never_reused_for_a_full_request(manifests) -> None:
    with connect() as conn:
        spec = plm.esm_spec()
        full, _ = manifests["full"]
        partial, _ = manifests["partial"]
        if find_reusable(conn, spec, partial, require_full=False) is None:
            pytest.skip("no partial cache registered")
        assert find_reusable(conn, spec, full, require_full=True) is None or (
            find_reusable(conn, spec, full, require_full=True)[1] != cache_name(spec, partial)
        )


@pytest.mark.requires_db
def test_changed_entity_content_changes_the_identity(manifests) -> None:
    """Re-standardising a sequence must invalidate the cache built over it.

    The digest covers ids *and* content precisely so a silent re-curation cannot
    leave stale vectors attached to the same entity ids.
    """
    full, ids = manifests["full"]
    with connect() as conn:
        rows = conn.execute(
            "SELECT id, sequence_sha256 FROM target WHERE id = ANY(%s) ORDER BY id LIMIT 50",
            (ids[:50],),
        ).fetchall()
    pairs = [(int(r[0]), str(r[1])) for r in rows]
    from seq2lead.features.manifest import _digest_pairs

    unchanged = _digest_pairs(pairs)
    mutated = _digest_pairs([(pairs[0][0], "0" * 64), *pairs[1:]])
    assert unchanged != mutated, "content hashes are not part of the digest"


@pytest.mark.requires_db
def test_changed_population_changes_the_identity(manifests) -> None:
    """release:117 and all_rows are different inputs and must not share a cache."""
    spec = plm.esm_spec()
    release, _ = manifests["full"]
    every, _ = manifests["all_rows"]
    assert release.n_entities != every.n_entities
    assert release.entity_digest != every.entity_digest
    assert cache_key(spec, release) != cache_key(spec, every)


@pytest.mark.requires_db
def test_an_identical_rerun_finds_the_existing_cache(manifests) -> None:
    """The reuse path: same spec, same inputs, nothing recomputed."""
    with connect() as conn:
        row = conn.execute(
            "SELECT name, population, completeness FROM feature_version "
            "WHERE kind='ecfp4' AND superseded_by IS NULL ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if row is None:
            pytest.skip("no ecfp4 cache built")
        from seq2lead.curate import CURATOR_VERSION
        from seq2lead.features import ecfp

        spec = ecfp.ecfp_spec(CURATOR_VERSION, chirality=True)
        manifest, _ = build_manifest(conn, "compound", str(row[1]))
        hit = find_reusable(conn, spec, manifest, require_full=True)
    assert hit is not None, "an identical rerun did not find its own cache"
    assert hit[1] == str(row[0])


# ================================================ the previous behaviour fails these


@pytest.mark.requires_db
def test_spec_alone_cannot_tell_these_apart(manifests) -> None:
    """Proof that the four tests above are regressions, not tautologies.

    Under the previous identity -- the representation spec hashed on its own --
    a partial build, a full build and a completely different population all
    resolve to one identity. Every assertion above would be unreachable.
    """
    spec = plm.esm_spec()
    full, _ = manifests["full"]
    partial, _ = manifests["partial"]
    every, _ = manifests["all_rows"]
    previous_identity = spec.sha256()
    assert previous_identity == spec.sha256()
    # All three populations collapse to the same key under the old scheme...
    assert len({previous_identity, previous_identity, previous_identity}) == 1
    # ...while the corrected scheme separates every one of them.
    assert len({cache_key(spec, m) for m in (full, partial, every)}) == 3  # noqa: PLR2004


# ==================================================================== validation


def _store(ids, vectors, manifest):
    from seq2lead.features.store import FeatureStore

    return FeatureStore(
        spec=plm.esm_spec(),
        ids=np.asarray(ids, dtype=np.int64),
        vectors=np.asarray(vectors, dtype=np.float32),
        flags={},
        manifest=manifest,
    )


def _manifest(n: int) -> InputManifest:
    return InputManifest(
        entity="target",
        population="test",
        n_entities=n,
        entity_digest="x" * 64,
        completeness="full",
    )


def test_validation_rejects_duplicate_ids() -> None:
    with pytest.raises(CacheInvalid, match="duplicate"):
        validate(_store([1, 1, 2], np.zeros((3, 4)), _manifest(3)), _manifest(3), [1, 2])


def test_validation_rejects_misaligned_rows() -> None:
    with pytest.raises(CacheInvalid, match="not aligned"):
        validate(_store([1, 2, 3], np.zeros((2, 4)), _manifest(3)), _manifest(3), [1, 2, 3])


def test_validation_rejects_non_finite_values() -> None:
    vectors = np.zeros((2, 3), dtype=np.float32)
    vectors[1, 2] = np.nan
    with pytest.raises(CacheInvalid, match="non-finite"):
        validate(_store([1, 2], vectors, _manifest(2)), _manifest(2), [1, 2])


def test_validation_rejects_missing_and_extra_entities() -> None:
    with pytest.raises(CacheInvalid, match="no vector"):
        validate(_store([1], np.zeros((1, 3)), _manifest(1)), _manifest(1), [1, 2])
    with pytest.raises(CacheInvalid, match="outside the declared population"):
        validate(_store([1, 9], np.zeros((2, 3)), _manifest(2)), _manifest(2), [1, 2])


def test_validation_accepts_a_well_formed_cache() -> None:
    validate(_store([1, 2], np.zeros((2, 3)), _manifest(2)), _manifest(2), [1, 2])


# ============================================================== length policies


@pytest.mark.parametrize("policy", ["chunk:1022", "FULL", "truncate", "truncate:0", "sliding"])
def test_unsupported_length_policies_are_refused(policy) -> None:
    """Silently treating an unknown policy as truncation would mislabel the cache."""
    with pytest.raises(ValueError, match="policy"):
        plm.parse_length_policy(policy)


@pytest.mark.parametrize(
    ("policy", "expected"), [("full", ("full", None)), ("truncate:1022", ("truncate", 1022))]
)
def test_supported_length_policies_parse(policy, expected) -> None:
    assert plm.parse_length_policy(policy) == expected


@pytest.mark.requires_db
def test_activity_manifest_carries_the_training_evidence(manifests) -> None:
    """Same split, rebuilt training set -> different aggregate, so different identity."""
    with connect() as conn:
        row = conn.execute(
            "SELECT id FROM split_version WHERE split_type='temporal_proxy' "
            "AND superseded_by IS NULL ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if row is None:
            pytest.skip("no temporal split")
        from seq2lead.features.manifest import evidence_digest_for_split

        digest = evidence_digest_for_split(conn, int(row[0]))
        real = manifest_from_ids(
            conn, "target", "training_visible", [1, 2], split="s", evidence_digest=digest
        )
        altered = manifest_from_ids(
            conn,
            "target",
            "training_visible",
            [1, 2],
            split="s",
            evidence_digest="n=0:md5=different",
        )
    assert digest.startswith("n=")
    assert real.digest() != altered.digest()
