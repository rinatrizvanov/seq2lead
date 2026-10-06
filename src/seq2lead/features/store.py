"""Where feature vectors live, and how a cache proves it is the one it claims.

Vectors go to disk as `.npz`; the database keeps the spec, the byte digest and
the per-entity flags. Two digests are recorded and they answer different
questions: `spec_sha256` says *what was asked for*, `storage_sha256` says *what
was produced*. A cache whose bytes no longer hash to its recorded digest has
been edited or corrupted, and is refused rather than quietly used.
"""

from __future__ import annotations

import hashlib
import json as _json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    import psycopg

    from seq2lead.features.identity import FeatureSpec
    from seq2lead.features.manifest import InputManifest

CACHE_ROOT = Path("data/features")


class FeatureCacheError(RuntimeError):
    """A cache is missing, altered, or not the one the spec asks for."""


@dataclass
class FeatureStore:
    """One built cache: the ids, the matrix, the spec and the population."""

    spec: FeatureSpec
    ids: np.ndarray
    vectors: np.ndarray
    flags: dict[int, list[tuple[str, dict]]]
    seconds: float = 0.0
    manifest: InputManifest | None = None
    library_versions: dict[str, str] | None = None

    @property
    def dim(self) -> int:
        return int(self.vectors.shape[1]) if self.vectors.ndim == 2 else 0

    @property
    def name(self) -> str:
        from seq2lead.features.identity import cache_name

        if self.manifest is None:
            return self.spec.cache_name()
        return cache_name(self.spec, self.manifest)

    def path(self, root: Path = CACHE_ROOT) -> Path:
        return root / f"{self.name}.npz"

    def write(self, root: Path = CACHE_ROOT) -> tuple[Path, str]:
        path = self.path(root)
        path.parent.mkdir(parents=True, exist_ok=True)
        # Uncompressed: these are read far more often than written, and the
        # dominant cost is float32 entropy that compresses poorly anyway.
        with path.open("wb") as fh:
            np.savez(fh, ids=self.ids, vectors=self.vectors)
        return path, sha256_file(path)


def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while block := fh.read(chunk):
            digest.update(block)
    return digest.hexdigest()


def register(
    conn: psycopg.Connection,
    store: FeatureStore,
    *,
    builder_version: str,
    split_id: int | None = None,
    root: Path = CACHE_ROOT,
    expected_ids: list[int] | None = None,
) -> int:
    """Write the vectors and record the cache. Re-registering the same spec is a no-op.

    Validates before writing: a cache that fails is never stored and never
    registered, so a half-built or mis-populated one cannot be read back later.
    """
    from seq2lead.features.identity import cache_key

    spec = store.spec
    manifest = store.manifest
    identity = cache_key(spec, manifest) if manifest else spec.sha256()
    existing = conn.execute(
        "SELECT id, storage_path, storage_sha256 FROM feature_version WHERE spec_sha256=%s",
        (identity,),
    ).fetchone()
    if existing is not None:
        path = Path(str(existing[1]))
        if path.exists() and sha256_file(path) == str(existing[2]):
            return int(existing[0])
        raise FeatureCacheError(
            f"feature cache {store.name} is registered but its bytes at {path} "
            "are missing or no longer match the recorded digest. Rebuild it rather "
            "than trusting vectors whose provenance is broken."
        )

    # Validation is enforced here, not left to the caller. A cache registered
    # through the library directly would otherwise skip every check the CLI runs.
    if manifest is not None:
        from seq2lead.features.manifest import content_digest_for_ids
        from seq2lead.features.validate import validate

        recomputed = (
            content_digest_for_ids(conn, manifest.entity, [int(i) for i in store.ids])
            if manifest.entity in {"compound", "target"}
            else None
        )
        validate(store, manifest, expected_ids, recomputed_digest=recomputed)

    path, digest = store.write(root)
    feature_id = int(
        conn.execute(
            "INSERT INTO feature_version (name, kind, entity, spec_sha256, params, dim, "
            "n_entities, storage_path, storage_sha256, split_id, builder_version, seconds, "
            "manifest_sha256, population, completeness, library_versions, validated_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,now()) RETURNING id",
            (
                store.name,
                spec.kind,
                spec.entity,
                identity,
                spec.canonical(),
                store.dim,
                int(store.ids.shape[0]),
                str(path),
                digest,
                split_id,
                builder_version,
                store.seconds,
                manifest.digest() if manifest else None,
                manifest.population if manifest else None,
                manifest.completeness if manifest else "unknown",
                _json.dumps(store.library_versions or {}),
            ),
        ).fetchone()[0]
    )
    if store.flags:
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO feature_entity_flag (feature_id, entity_id, flag, detail) "
                "VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                [
                    (feature_id, int(entity_id), flag, _json.dumps(detail))
                    for entity_id, entries in store.flags.items()
                    for flag, detail in entries
                ],
            )
    return feature_id


def load_features(
    conn: psycopg.Connection,
    name_or_spec: str | FeatureSpec,
    manifest: InputManifest | None = None,
    *,
    allow_superseded: bool = False,
) -> tuple[np.ndarray, np.ndarray]:
    """Read a registered cache back, verifying the bytes are the ones recorded.

    A `FeatureSpec` **requires** its `manifest`. The spec alone is the
    representation recipe, not an identity: on its own it cannot distinguish a
    partial build from a full one, or one population from another. An earlier
    version hashed the spec by itself and looked the result up in the identity
    column, which could never match anything once identities became
    manifest-aware -- a silent, total failure of the spec-based path.

    A string is resolved as a cache **name**, then as a full identity. Neither is
    inferred from its length.
    """
    if not isinstance(name_or_spec, str):
        if manifest is None:
            raise FeatureCacheError(
                "loading by FeatureSpec requires the InputManifest it was built over. "
                "The spec describes how vectors are computed; it does not say which "
                "entities went in, so it cannot identify a cache on its own. Pass the "
                "manifest, or load by cache name."
            )
        from seq2lead.features.identity import cache_key

        key, column, label = cache_key(name_or_spec, manifest), "spec_sha256", "spec+manifest"
    else:
        key, column, label = name_or_spec, "name", "name"
        row = conn.execute("SELECT 1 FROM feature_version WHERE name=%s", (key,)).fetchone()
        if row is None:
            column, label = "spec_sha256", "identity"

    row = conn.execute(
        f"SELECT name, storage_path, storage_sha256, superseded_by, superseded_reason "  # noqa: S608
        f"FROM feature_version WHERE {column}=%s",
        (key,),
    ).fetchone()
    if row is None:
        raise FeatureCacheError(f"no feature cache registered for {label} {key!r}")
    name, path_text, digest, superseded, reason = row
    if superseded and not allow_superseded:
        raise FeatureCacheError(
            f"feature cache {name!r} was superseded by {superseded!r}: {reason}\n"
            "Build against the current cache, or pass allow_superseded=True if you are "
            "deliberately reproducing an old result."
        )
    path = Path(str(path_text))
    if not path.exists():
        raise FeatureCacheError(f"feature cache {name!r} is registered but {path} is gone")
    if sha256_file(path) != str(digest):
        raise FeatureCacheError(
            f"feature cache at {path} does not match its recorded digest. It has been "
            "edited or corrupted since it was built; rebuild rather than use it."
        )
    with np.load(path) as data:
        return data["ids"], data["vectors"]


def timer() -> float:
    return time.perf_counter()


def find_reusable(
    conn: psycopg.Connection,
    spec: FeatureSpec,
    manifest: InputManifest,
    *,
    require_full: bool = True,
) -> tuple[int, str] | None:
    """An existing cache that already answers this exact request, or None.

    Checked **before** fingerprints are computed or ESM-2 is loaded, so an
    identical rerun costs a digest rather than an hour of GPU time. The bytes are
    verified against their recorded digest here too: a cache that has drifted is
    not reusable, it is a rebuild.

    A partial cache is never returned for a full request. That is the whole point
    of carrying completeness in the identity.
    """
    from seq2lead.features.identity import cache_key

    row = conn.execute(
        "SELECT id, name, storage_path, storage_sha256, completeness, superseded_by "
        "FROM feature_version WHERE spec_sha256=%s",
        (cache_key(spec, manifest),),
    ).fetchone()
    if row is None:
        return None
    feature_id, name, path, digest, completeness, superseded = row
    if superseded:
        return None
    if require_full and str(completeness) != "full":
        return None
    candidate = Path(str(path))
    if not candidate.exists() or sha256_file(candidate) != str(digest):
        return None
    return int(feature_id), str(name)
