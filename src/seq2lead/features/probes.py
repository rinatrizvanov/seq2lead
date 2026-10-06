"""Runnable probes behind the embedding claims, and the artifacts they emit.

The first version of `reports/features.md` hardcoded its measurements: the
long-sequence timings, the prefix-drift table and the cluster-similarity numbers
were literals in the report generator. That makes them unfalsifiable -- nothing
regenerates them, nothing checks them, and a reader cannot tell a measurement
from a recollection.

Each probe here writes a JSON artifact under `reports/probes/` carrying the
inputs it selected (entity ids and content hashes), the environment it ran in
(model revision, pooling, device, library versions, seed), every individual
measurement, and every failure. The report renders statistics *from those
files*. If an artifact is missing, the report says so rather than inventing a
number.
"""

from __future__ import annotations

import json
import platform
import statistics as st
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import psycopg

PROBE_DIR = Path("reports/probes")


@dataclass
class ProbeArtifact:
    """One probe run: what it did, under what conditions, and what it saw."""

    name: str
    description: str
    metadata: dict[str, Any]
    selection: dict[str, Any]
    measurements: list[dict[str, Any]] = field(default_factory=list)
    failures: list[dict[str, Any]] = field(default_factory=list)

    def write(self, directory: Path = PROBE_DIR) -> Path:
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{self.name}.json"
        path.write_text(json.dumps(asdict(self), indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return path

    @classmethod
    def load(cls, name: str, directory: Path = PROBE_DIR) -> ProbeArtifact | None:
        path = directory / f"{name}.json"
        if not path.exists():
            return None
        return cls(**json.loads(path.read_text(encoding="utf-8")))

    def column(self, key: str) -> list[float]:
        return [float(m[key]) for m in self.measurements if m.get(key) is not None]


def _environment(device: str, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    from seq2lead.features.manifest import library_versions
    from seq2lead.features.plm import MODEL, MODEL_REVISION, POOLING

    env = {
        "model": MODEL,
        "model_revision": MODEL_REVISION,
        "pooling": POOLING,
        "device": device,
        "platform": platform.platform(),
        "processor": platform.processor() or platform.machine(),
        "libraries": library_versions("esm2"),
        "run_at": datetime.now(UTC).isoformat(timespec="seconds"),
        # Every probe here is a deterministic forward pass -- no dropout, no
        # sampling -- so a seed only ever selects *which* entities are measured.
        "note": (
            "Forward passes are deterministic; the seed selects inputs, not outputs. "
            "Values are specific to this device and dtype: the same specification on "
            "another backend yields the same representation, not the same bytes."
        ),
    }
    if extra:
        env.update(extra)
    return env


def _load_model(device: str | None):
    import torch
    from transformers import AutoModel, AutoTokenizer

    from seq2lead.features.plm import MODEL, MODEL_REVISION, _resolve_device

    resolved = _resolve_device(device)
    tokenizer = AutoTokenizer.from_pretrained(MODEL, revision=MODEL_REVISION)
    model = (
        AutoModel.from_pretrained(MODEL, revision=MODEL_REVISION, dtype=torch.float32)
        .eval()
        .to(resolved)
    )
    return tokenizer, model, resolved, torch


def _embed(tokenizer, model, torch, device: str, sequence: str):
    encoded = {k: v.to(device) for k, v in tokenizer(sequence, return_tensors="pt").items()}
    with torch.no_grad():
        hidden = model(**encoded).last_hidden_state[0]
    return hidden[1:-1].float().cpu()


# ----------------------------------------------------------------- feasibility


def probe_length_feasibility(
    conn: psycopg.Connection,
    targets: tuple[int, ...] = (1022, 2000, 4000, 8000, 20000, 34350),
    device: str | None = None,
) -> ProbeArtifact:
    """Can the model embed a sequence of length L at all, and how long does it take?

    Uses the **real** target nearest each requested length, so the timing
    reflects sequences the corpus actually contains rather than a poly-alanine
    stand-in.
    """
    tokenizer, model, resolved, torch = _load_model(device)
    artifact = ProbeArtifact(
        name="length_feasibility",
        description=(
            "Wall time and success for a single forward pass over the real target "
            "nearest each requested length."
        ),
        metadata=_environment(resolved),
        selection={
            "rule": "for each requested length, the target minimising |len(sequence) - L|",
            "requested_lengths": list(targets),
            "population": "all curated targets",
        },
    )
    for wanted in targets:
        row = conn.execute(
            "SELECT id, sequence_sha256, length, sequence FROM target "
            "ORDER BY abs(length - %s), id LIMIT 1",
            (wanted,),
        ).fetchone()
        if row is None:
            continue
        target_id, sha, length, sequence = int(row[0]), str(row[1]), int(row[2]), str(row[3])
        try:
            started = time.perf_counter()
            _embed(tokenizer, model, torch, resolved, sequence)
            if resolved == "mps":
                torch.mps.synchronize()
            artifact.measurements.append(
                {
                    "requested_length": wanted,
                    "target_id": target_id,
                    "sequence_sha256": sha,
                    "length": length,
                    "seconds": round(time.perf_counter() - started, 3),
                    "ok": True,
                }
            )
        except Exception as exc:  # noqa: BLE001 - a failure is the result here
            artifact.failures.append(
                {
                    "requested_length": wanted,
                    "target_id": target_id,
                    "length": length,
                    "error": f"{type(exc).__name__}: {exc}"[:400],
                }
            )
        finally:
            if resolved == "mps":
                torch.mps.empty_cache()
    return artifact


# ---------------------------------------------------------------- prefix drift


def probe_prefix_drift(
    conn: psycopg.Connection,
    n_targets: int = 16,
    min_length: int = 1200,
    max_length: int = 6000,
    window: int = 1022,
    seed: int = 20260929,
    device: str | None = None,
) -> ProbeArtifact:
    """How far does a prefix's representation move when the model extrapolates?

    Embed the first `window` residues alone -- inside the pre-training crop
    length -- then embed the full sequence and extract the same residues. Same
    residues, one in regime and one out.

    This measures **movement, not accuracy**. Neither representation is a ground
    truth, and nothing here says which is better for any task.
    """
    import torch.nn.functional as F  # noqa: N812

    tokenizer, model, resolved, torch = _load_model(device)
    rows = conn.execute(
        "SELECT id, sequence_sha256, length, sequence FROM target "
        "WHERE length BETWEEN %s AND %s ORDER BY md5(sequence_sha256 || %s) LIMIT %s",
        (min_length, max_length, str(seed), n_targets),
    ).fetchall()
    artifact = ProbeArtifact(
        name="prefix_drift",
        description=(
            "Cosine between the first N residues embedded alone (in the pre-training "
            "crop window) and the same residues embedded inside the full sequence."
        ),
        metadata=_environment(resolved, {"window": window, "seed": seed}),
        selection={
            "rule": (
                f"targets with {min_length} <= length <= {max_length}, ordered by "
                "md5(sequence_sha256 || seed), first n taken"
            ),
            "n_requested": n_targets,
            "min_length": min_length,
            "max_length": max_length,
            "window": window,
        },
    )
    for row in rows:
        target_id, sha, length, sequence = int(row[0]), str(row[1]), int(row[2]), str(row[3])
        try:
            in_regime = _embed(tokenizer, model, torch, resolved, sequence[:window])
            full = _embed(tokenizer, model, torch, resolved, sequence)[:window]
            pooled_a, pooled_b = in_regime.mean(0), full.mean(0)
            artifact.measurements.append(
                {
                    "target_id": target_id,
                    "sequence_sha256": sha,
                    "length": length,
                    "pooled_cosine": round(
                        float(F.cosine_similarity(pooled_a[None], pooled_b[None]).item()), 6
                    ),
                    "mean_residue_cosine": round(
                        float(F.cosine_similarity(in_regime, full, dim=1).mean().item()), 6
                    ),
                    "pooled_relative_l2": round(
                        float(((pooled_a - pooled_b).norm() / pooled_a.norm()).item()), 6
                    ),
                }
            )
        except Exception as exc:  # noqa: BLE001
            artifact.failures.append(
                {
                    "target_id": target_id,
                    "length": length,
                    "error": f"{type(exc).__name__}: {exc}"[:400],
                }
            )
        finally:
            if resolved == "mps":
                torch.mps.empty_cache()
    return artifact


# ----------------------------------------------------------- cluster similarity


def probe_cluster_similarity(
    conn: psycopg.Connection,
    feature_name: str,
    n_pairs: int = 4000,
    seed: int = 20260929,
    method: str = "mmseqs2-cluster",
) -> ProbeArtifact:
    """Cosine between embeddings of same-cluster and different-cluster target pairs.

    A sanity check that the cache encodes homology. Note what a "different
    cluster" pair is **not**: MMseqs2 clusters at 40% identity with 80% coverage,
    so two proteins in different clusters may still be homologous, share a domain
    or share a fold. The contrast is between *clustered-together* and
    *not-clustered-together*, not between related and unrelated.
    """
    import numpy as np

    from seq2lead.features.store import load_features

    ids, vectors = load_features(conn, feature_name)
    index = {int(t): i for i, t in enumerate(ids)}
    normed = vectors / (np.linalg.norm(vectors, axis=1, keepdims=True) + 1e-9)
    cluster = {
        int(r[0]): str(r[1])
        for r in conn.execute(
            "SELECT target_id, cluster_id FROM target_cluster WHERE method=%s", (method,)
        ).fetchall()
    }
    same_rows = conn.execute(
        "SELECT a.target_id, b.target_id, a.cluster_id FROM target_cluster a "
        "JOIN target_cluster b ON b.cluster_id = a.cluster_id AND b.target_id > a.target_id "
        "WHERE a.method = %s ORDER BY md5((a.target_id * 1000003 + b.target_id)::text || %s) "
        "LIMIT %s",
        (method, str(seed), n_pairs),
    ).fetchall()

    artifact = ProbeArtifact(
        name="cluster_similarity",
        description=(
            "Cosine between mean-pooled ESM-2 embeddings for target pairs inside one "
            "MMseqs2 cluster and for pairs drawn from different clusters."
        ),
        metadata=_environment(
            "n/a (reads a built cache)",
            {"feature_cache": feature_name, "cluster_method": method, "seed": seed},
        ),
        selection={
            "rule": (
                "same-cluster pairs ordered by md5 of the id pair and seed; "
                "different-cluster pairs drawn uniformly at random from clustered "
                "targets present in the cache, rejecting same-cluster draws"
            ),
            "n_pairs_requested": n_pairs,
            "cluster_method": method,
            "seed": seed,
        },
    )
    for a, b, cluster_id in same_rows:
        if int(a) in index and int(b) in index:
            artifact.measurements.append(
                {
                    "kind": "same_cluster",
                    "target_a": int(a),
                    "target_b": int(b),
                    "cluster_a": str(cluster_id),
                    "cluster_b": str(cluster_id),
                    "cosine": round(float(normed[index[int(a)]] @ normed[index[int(b)]]), 6),
                }
            )
    rng = np.random.default_rng(seed)
    keys = [t for t in cluster if t in index]
    drawn = 0
    while drawn < n_pairs and len(keys) > 1:
        i, j = rng.choice(len(keys), 2, replace=False)
        ta, tb = keys[int(i)], keys[int(j)]
        if cluster[ta] == cluster[tb]:
            continue
        artifact.measurements.append(
            {
                "kind": "different_cluster",
                "target_a": int(ta),
                "target_b": int(tb),
                "cluster_a": cluster[ta],
                "cluster_b": cluster[tb],
                "cosine": round(float(normed[index[ta]] @ normed[index[tb]]), 6),
            }
        )
        drawn += 1
    return artifact


def quantiles(values: list[float]) -> dict[str, float]:
    """p05/p25/median/p75/p95 plus min and max, or an empty dict."""
    if not values:
        return {}
    ordered = sorted(values)

    def at(q: float) -> float:
        return ordered[min(len(ordered) - 1, max(0, int(round(q * (len(ordered) - 1)))))]

    return {
        "n": len(ordered),
        "min": ordered[0],
        "p05": at(0.05),
        "p25": at(0.25),
        "median": st.median(ordered),
        "p75": at(0.75),
        "p95": at(0.95),
        "max": ordered[-1],
    }


def probe_chirality_impact(
    conn: psycopg.Connection, achiral_name: str, chiral_name: str
) -> ProbeArtifact:
    """How much does encoding stereochemistry change the fingerprints?

    Compares the two caches over the compounds they share, and counts how many
    *distinct* compounds share a fingerprint with another compound under each
    representation. The residual collision count is as important as the resolved
    one: ECFP4 is a hashed fingerprint and stays lossy either way.
    """
    import hashlib as _h

    import numpy as np

    paths = {}
    for name in (achiral_name, chiral_name):
        row = conn.execute(
            "SELECT storage_path FROM feature_version WHERE name=%s", (name,)
        ).fetchone()
        if row is None:
            raise LookupError(f"no feature cache named {name!r}")
        paths[name] = str(row[0])

    with np.load(paths[achiral_name]) as d:
        old_ids, old_v = d["ids"], d["vectors"]
    with np.load(paths[chiral_name]) as d:
        new_ids, new_v = d["ids"], d["vectors"]

    common = np.intersect1d(old_ids, new_ids)
    oi = {int(t): i for i, t in enumerate(old_ids)}
    ni = {int(t): i for i, t in enumerate(new_ids)}
    o_idx = np.array([oi[int(t)] for t in common])
    n_idx = np.array([ni[int(t)] for t in common])
    changed = int((old_v[o_idx] != new_v[n_idx]).any(axis=1).sum())

    def collisions(matrix, keys) -> tuple[int, int]:
        seen: dict[bytes, int] = {}
        for row in matrix:
            digest = _h.blake2b(row.tobytes(), digest_size=16).digest()
            seen[digest] = seen.get(digest, 0) + 1
        colliding = sum(count for count in seen.values() if count > 1)
        groups = sum(1 for count in seen.values() if count > 1)
        return colliding, groups

    old_c, old_g = collisions(old_v[o_idx], common)
    new_c, new_g = collisions(new_v[n_idx], common)

    artifact = ProbeArtifact(
        name="chirality_impact",
        description=(
            "Effect of includeChirality=True on the ECFP4 cache: how many fingerprints "
            "change, and how many distinct compounds stop sharing a vector."
        ),
        metadata={
            "achiral_cache": achiral_name,
            "chiral_cache": chiral_name,
            "run_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "note": (
                "Collisions are counted over compounds present in both caches. A "
                "hashed 2,048-bit fingerprint collides regardless of stereochemistry, "
                "so the residual count is expected to be non-zero."
            ),
        },
        selection={"rule": "compounds present in both caches", "n": int(len(common))},
        measurements=[
            {"metric": "compounds_in_both", "value": int(len(common))},
            {"metric": "fingerprint_changed", "value": changed},
            {"metric": "achiral_colliding_compounds", "value": old_c},
            {"metric": "achiral_collision_groups", "value": old_g},
            {"metric": "chiral_colliding_compounds", "value": new_c},
            {"metric": "chiral_collision_groups", "value": new_g},
        ],
    )
    return artifact
