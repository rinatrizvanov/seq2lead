"""Assembling model inputs from the pinned caches.

Fingerprints are stored bit-packed (256 bytes for 2,048 bits). Unpacking the
whole corpus would cost ~3 GB, so rows are unpacked for the pairs a model
actually needs and nothing else.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    import psycopg

    from seq2lead.eval.config import ExperimentConfig

N_BITS = 2048


@dataclass
class FeatureBank:
    """The pinned caches, indexed for row lookup by entity id."""

    compound_index: dict[int, int]
    compound_packed: np.ndarray  # (n_compounds, 256) uint8
    target_index: dict[int, int]
    target_vectors: np.ndarray  # (n_targets, 1280) float32

    @classmethod
    def load(cls, conn: psycopg.Connection, config: ExperimentConfig) -> FeatureBank:
        from seq2lead.features.store import load_features

        c_ids, c_packed = load_features(conn, config.caches["ecfp4"].name)
        t_ids, t_vectors = load_features(conn, config.caches["esm2"].name)
        return cls(
            compound_index={int(v): i for i, v in enumerate(c_ids)},
            compound_packed=c_packed,
            target_index={int(v): i for i, v in enumerate(t_ids)},
            target_vectors=np.ascontiguousarray(t_vectors, dtype=np.float32),
        )

    def ligand(self, compound_ids: np.ndarray, dtype=np.float32) -> np.ndarray:
        rows = np.fromiter(
            (self.compound_index[int(c)] for c in compound_ids),
            dtype=np.int64,
            count=len(compound_ids),
        )
        return np.unpackbits(self.compound_packed[rows], axis=1)[:, :N_BITS].astype(dtype)

    def ligand_bits(self, compound_ids: np.ndarray) -> np.ndarray:
        """Unpacked 0/1 bits as uint8 -- for Tanimoto, which needs no floats."""
        return self.ligand(compound_ids, dtype=np.uint8)

    def protein(self, target_ids: np.ndarray) -> np.ndarray:
        rows = np.fromiter(
            (self.target_index[int(t)] for t in target_ids),
            dtype=np.int64,
            count=len(target_ids),
        )
        return self.target_vectors[rows]

    def covers(self, compound_ids: np.ndarray, target_ids: np.ndarray) -> bool:
        return all(int(c) in self.compound_index for c in compound_ids) and all(
            int(t) in self.target_index for t in target_ids
        )


def tanimoto_to_reference(query_bits: np.ndarray, reference_bits: np.ndarray) -> np.ndarray:
    """Tanimoto of each query row against every reference row.

    `query @ reference.T` counts shared on-bits; the union follows from the two
    popcounts. Kept as a dense block because callers bound the reference set --
    an exhaustive compound-by-compound matrix is never built.
    """
    q = query_bits.astype(np.float32)
    r = reference_bits.astype(np.float32)
    intersection = q @ r.T
    q_bits = q.sum(axis=1, keepdims=True)
    r_bits = r.sum(axis=1, keepdims=True).T
    union = q_bits + r_bits - intersection
    with np.errstate(divide="ignore", invalid="ignore"):
        result = np.where(union > 0, intersection / union, 0.0)
    return result.astype(np.float32)
