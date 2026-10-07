"""A small synthetic inference bundle, shared by the inference and web tests."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import numpy as np


def build_bundle(root: Path, *, n: int = 6, dim: int = 4) -> Path:
    from seq2lead.inference.bundle import BUNDLE_FILES, BUNDLE_VERSION, sha256_file

    rng = np.random.default_rng(0)
    (root / "model").mkdir(parents=True, exist_ok=True)
    (root / "library").mkdir(parents=True, exist_ok=True)
    np.savez(
        root / "model/protein_tower.npz",
        **{
            "0.weight": rng.normal(size=(dim, 8)).astype(np.float32),
            "0.bias": np.zeros(dim, dtype=np.float32),
            "2.weight": rng.normal(size=(dim, dim)).astype(np.float32),
            "2.bias": np.zeros(dim, dtype=np.float32),
        },
        scale=np.asarray(2.0, dtype=np.float32),
        offset=np.asarray(7.0, dtype=np.float32),
    )
    np.savez(
        root / "model/compound_tower.npz",
        **{"0.weight": rng.normal(size=(dim, 8)).astype(np.float32)},
    )
    np.savez(
        root / "model/protein_transform.npz",
        mean=np.zeros((1, 8), dtype=np.float32),
        scale=np.ones((1, 8), dtype=np.float32),
        n_fitted=np.asarray(10),
    )
    np.save(root / "library/projections.npy", rng.normal(size=(n, dim)).astype(np.float32))
    smiles = ["c1ccccc1", "CCO", "c1ccccc1C", "CCCCCCCCCCCCCCCCCC", "CCN", "c1ccncc1"]
    with (root / "library/compounds.csv").open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["row", "compound_id", "smiles", "flags"])
        for i in range(n):
            writer.writerow([i, f"C{i}", smiles[i % len(smiles)], ""])

    manifest = {
        "bundle_version": BUNDLE_VERSION,
        "model": {"projection_dim": dim, "scoring_rule": "scale * cosine + offset"},
        "representation": {
            "protein": {
                "model": "facebook/esm2_t33_650M_UR50D",
                "model_revision": "deadbeef",
                "pooling": "mean_over_residues_excluding_special_tokens",
                "dtype": "float32",
                "length_policy": "full",
                "max_length": 40000,
                "training_window": 1022,
            }
        },
        "library": {"name": "test-lib"},
        "counts": {"compounds": n, "projection_dim": dim},
        "files": {
            name: {"bytes": (root / name).stat().st_size, "sha256": sha256_file(root / name)}
            for name in BUNDLE_FILES
        },
    }
    (root / "manifest.json").write_text(json.dumps(manifest, indent=1) + "\n")
    return root
