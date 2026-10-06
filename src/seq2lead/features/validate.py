"""Checks a cache must pass before anything is allowed to read it.

Each of these corresponds to a way a cache can be wrong while still looking
plausible: a partial build registered as complete, a row silently duplicated, a
NaN from a failed forward pass, a matrix whose rows no longer line up with its
ids. None of them raises on its own at build time, and all of them would quietly
corrupt whatever trains on the result.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from seq2lead.features.manifest import InputManifest
    from seq2lead.features.store import FeatureStore


class CacheInvalid(ValueError):
    """A built cache does not match the population or shape it claims."""


def validate(
    store: FeatureStore,
    manifest: InputManifest,
    expected_ids: list[int] | None = None,
    recomputed_digest: str | None = None,
) -> None:
    """Refuse a cache that does not match its manifest. Raises `CacheInvalid`.

    `expected_ids` checks membership directly and is used when the caller has the
    population to hand. `recomputed_digest` is the registration-time check: the
    entity digest re-derived from the cache's own ids and the database's current
    content hashes. Either one catches a cache built over the wrong population;
    the digest path also catches content that changed underneath it.
    """
    ids = np.asarray(store.ids)
    vectors = np.asarray(store.vectors)
    problems: list[str] = []

    if vectors.ndim != 2:  # noqa: PLR2004
        problems.append(f"vectors have {vectors.ndim} dimensions, expected 2")
    elif vectors.shape[0] != ids.shape[0]:
        problems.append(
            f"{vectors.shape[0]:,} vectors for {ids.shape[0]:,} ids -- rows and ids "
            "are not aligned, so every lookup would return another entity's features"
        )
    elif vectors.shape[1] == 0:
        problems.append("vectors have zero width")

    if ids.shape[0] != len(set(ids.tolist())):
        duplicates = ids.shape[0] - len(set(ids.tolist()))
        problems.append(f"{duplicates:,} duplicate entity ids")

    if expected_ids is not None:
        expected = set(expected_ids)
        present = set(ids.tolist())
        missing, extra = expected - present, present - expected
        if missing:
            problems.append(
                f"{len(missing):,} entities in the manifest have no vector "
                f"(e.g. {sorted(missing)[:3]})"
            )
        if extra:
            problems.append(
                f"{len(extra):,} vectors belong to entities outside the declared "
                f"population (e.g. {sorted(extra)[:3]})"
            )
    if recomputed_digest is not None and recomputed_digest != manifest.entity_digest:
        problems.append(
            "the entity digest re-derived from this cache's own ids does not match "
            "the manifest it claims. The cache covers a different population, or the "
            "structures or sequences underneath it changed after the manifest was taken"
        )
    if manifest.n_entities != ids.shape[0]:
        problems.append(
            f"manifest declares {manifest.n_entities:,} entities, cache holds {ids.shape[0]:,}"
        )

    # A NaN or inf reaches a model as a number and poisons every gradient that
    # touches it, so it is caught here rather than three milestones later.
    if vectors.dtype.kind == "f" and vectors.size:
        bad = int((~np.isfinite(vectors)).sum())
        if bad:
            rows = int((~np.isfinite(vectors)).any(axis=1).sum())
            problems.append(f"{bad:,} non-finite values across {rows:,} rows")

    if problems:
        raise CacheInvalid(
            f"cache for {manifest.population} ({manifest.entity}) is not usable:\n  - "
            + "\n  - ".join(problems)
        )
