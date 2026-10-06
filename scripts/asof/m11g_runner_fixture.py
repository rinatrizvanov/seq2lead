"""A valid runner invocation on synthetic artifacts, with one knob per case.

Shares the shape of tests/test_m11g_runner.py's harness so a case reproduced
here transfers directly into the suite.
"""

from __future__ import annotations

import hashlib
import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np

from seq2lead.asof.contract import PINNED_SETTINGS, REQUIRED_DIGESTS, STORED_DIMS
from seq2lead.asof.datasets import TRAIN_ROLE, VALIDATION_ROLE
from seq2lead.asof.partition import TRAIN, VALIDATION
from seq2lead.asof.runner import EVALUATION_ROLE, FeatureSource, RoleEntities, run

TRAIN_COMPOUNDS = ("CMPDAAAAAAAAAA-AAAAAAAAAA-N", "CMPDBBBBBBBBBB-BBBBBBBBBB-N")
VALIDATION_COMPOUNDS = ("CMPDCCCCCCCCCC-CCCCCCCCCC-N",)
SEQUENCES = ("a" * 64,)


def sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def vector(kind: str, dim: int, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    if kind == "ecfp4":
        return rng.integers(0, 256, size=dim, dtype=np.uint8)
    return rng.standard_normal(dim).astype(np.float32)


class Fixture:
    def __init__(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="m11g-repro-"))
        self.membership_override: Path | None = None

        ids = np.arange(1, len(TRAIN_COMPOUNDS) + 1, dtype=np.int64)
        self.ecfp4_accepted = self.root / "ecfp4-accepted.npz"
        np.savez(
            self.ecfp4_accepted, ids=ids,
            vectors=np.stack([vector("ecfp4", 256, n) for n in range(len(TRAIN_COMPOUNDS))]),
        )
        self.esm2_accepted = self.root / "esm2-accepted.npz"
        np.savez(
            self.esm2_accepted, ids=np.array([1], dtype=np.int64),
            vectors=np.stack([vector("esm2", 1280, 0)]),
        )
        self.ecfp4_ext = self.root / "ecfp4-extension.npz"
        np.savez(
            self.ecfp4_ext, keys=np.asarray(VALIDATION_COMPOUNDS, dtype=object),
            vectors=np.stack([vector("ecfp4", 256, 900)]),
        )
        self.esm2_ext = self.root / "esm2-extension.npz"
        np.savez(
            self.esm2_ext, keys=np.asarray([], dtype=object),
            vectors=np.zeros((0, 1280), dtype=np.float32),
        )

        self.reuse = {
            "ecfp4": {k: int(i) for k, i in zip(TRAIN_COMPOUNDS, ids, strict=True)},
            "esm2": {SEQUENCES[0]: 1},
        }
        self.sources = {
            "ecfp4": FeatureSource(
                kind="ecfp4", accepted_path=self.ecfp4_accepted,
                accepted_sha256=sha256(self.ecfp4_accepted),
                reuse_map=dict(self.reuse["ecfp4"]),
                extension_path=self.ecfp4_ext, extension_sha256=sha256(self.ecfp4_ext),
            ),
            "esm2": FeatureSource(
                kind="esm2", accepted_path=self.esm2_accepted,
                accepted_sha256=sha256(self.esm2_accepted),
                reuse_map=dict(self.reuse["esm2"]),
                extension_path=self.esm2_ext, extension_sha256=sha256(self.esm2_ext),
            ),
        }
        self.roles = {
            TRAIN_ROLE: RoleEntities(set(TRAIN_COMPOUNDS), set(SEQUENCES)),
            VALIDATION_ROLE: RoleEntities(set(VALIDATION_COMPOUNDS), set(SEQUENCES)),
            EVALUATION_ROLE: RoleEntities(
                set(TRAIN_COMPOUNDS) | set(VALIDATION_COMPOUNDS), set(SEQUENCES)
            ),
        }

        records = [
            {
                "pair": f"{c}|{SEQUENCES[0]}", "partition": p,
                "classification_eligible": True, "regression_eligible": True,
                "validation_rmse_eligible": True, "regression_target_pki": 7.0 + n,
            }
            for n, (c, p) in enumerate(
                [(TRAIN_COMPOUNDS[0], TRAIN), (TRAIN_COMPOUNDS[1], TRAIN),
                 (VALIDATION_COMPOUNDS[0], VALIDATION)]
            )
        ]
        self.membership = self.root / "a-membership.jsonl"
        self.membership.write_text(
            "".join(json.dumps(r, sort_keys=True) + "\n" for r in records), encoding="utf-8"
        )

        bound = self.root / "bound"
        bound.mkdir()
        self.digests, self.artifacts = {}, {}
        for name in sorted(REQUIRED_DIGESTS):
            if name == "a_membership":
                path = self.membership
            else:
                path = bound / f"{name}.bin"
                path.write_bytes(name.encode())
            self.digests[name] = sha256(path)
            self.artifacts[name] = path

        self.config = dict(PINNED_SETTINGS)
        self.fit_transforms = MagicMock(name="fit_transforms")
        self.fit_model = MagicMock(name="fit_model")
        self.select_checkpoint = MagicMock(name="select_checkpoint")

    def vector_for(self, kind: str, key: str) -> np.ndarray:
        from seq2lead.asof.features import load_binding

        s = self.sources[kind]
        b = load_binding(
            kind, accepted_path=s.accepted_path, accepted_sha256=s.accepted_sha256,
            reuse_map=s.reuse_map, stored_dim=STORED_DIMS[kind],
            extension_path=s.extension_path, extension_sha256=s.extension_sha256,
        )
        return b.vector(key)

    def run(self, **overrides):
        kwargs = {
            "membership_path": self.membership_override or self.membership,
            "expected_digests": self.digests,
            "artifacts": self.artifacts,
            "config": self.config,
            "feature_sources": self.sources,
            "roles": self.roles,
            "fit_transforms": self.fit_transforms,
            "fit_model": self.fit_model,
            "select_checkpoint": self.select_checkpoint,
        }
        return run(**{**kwargs, **overrides})
