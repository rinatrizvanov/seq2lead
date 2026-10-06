"""A dual encoder over frozen ESM-2 and chiral ECFP4 features.

Two towers that never see each other's input. That separation is the whole
point: a compound's projection depends only on its fingerprint, so the library
can be projected **once** and reused for every query target. The ranking CLI
rests on that property, and a test asserts batched pair scoring agrees with
scoring against a precomputed compound matrix.

**On the scoring head.** Cosine similarity is bounded to [-1, 1] and the labels
are pKi values of roughly 2-12, so cosine alone cannot represent the target.
`sigmoid(cosine)` cannot either, and it is *not* a calibrated probability
whatever it looks like -- calling it one would need a reliability diagram and an
ECE, neither of which this milestone computes. The head is therefore an explicit
affine map on the cosine, `a * cos + b`, with `a` and `b` trainable scalars and
the output in **pKi units**, directly comparable with the M8 baselines.

The cost of that choice is real and is reported rather than hidden: a bounded
cosine times a learned scale is the entire expressive range of the score.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np

if TYPE_CHECKING:
    import torch

COMPOUND_DIM = 2048
PROTEIN_DIM = 1280


@dataclass
class DualEncoderConfig:
    projection_dim: int = 256
    compound_dim: int = COMPOUND_DIM
    protein_dim: int = PROTEIN_DIM
    learning_rate: float = 1e-3
    batch_size: int = 512
    max_epochs: int = 20
    patience: int = 5
    seed: int = 20260930

    def digest(self) -> str:
        import hashlib

        return hashlib.sha256(json.dumps(asdict(self), sort_keys=True).encode("utf-8")).hexdigest()


@dataclass
class ProteinTransform:
    """Standardisation fitted on training inputs only, then frozen.

    Stored with the checkpoint so inference -- including the CLI, months later --
    applies exactly the transform training used, rather than re-deriving one from
    whatever data is to hand.
    """

    mean: np.ndarray
    scale: np.ndarray
    fitted_on: str = ""
    n_fitted: int = 0

    @classmethod
    def fit(cls, protein: np.ndarray, fitted_on: str = "train") -> ProteinTransform:
        return cls(
            mean=protein.mean(axis=0, keepdims=True).astype(np.float32),
            scale=np.maximum(protein.std(axis=0, keepdims=True), 1e-6).astype(np.float32),
            fitted_on=fitted_on,
            n_fitted=int(protein.shape[0]),
        )

    def apply(self, protein: np.ndarray) -> np.ndarray:
        return ((protein - self.mean) / self.scale).astype(np.float32)


def _tower(in_dim: int, hidden: int):
    from torch import nn

    return nn.Sequential(
        nn.Linear(in_dim, hidden),
        nn.ReLU(),
        nn.Linear(hidden, hidden),
    )


class DualEncoder:
    """Independent projections, cosine similarity, affine head in pKi units."""

    def __init__(self, config: DualEncoderConfig) -> None:
        import torch
        from torch import nn

        self.config = config
        torch.manual_seed(config.seed)
        self.compound_tower = _tower(config.compound_dim, config.projection_dim)
        self.protein_tower = _tower(config.protein_dim, config.projection_dim)
        # Initialised from the training-label spread by `initialise_head`, so the
        # model starts predicting something in the right range rather than
        # spending epochs learning the offset.
        self.scale = nn.Parameter(torch.tensor(1.0))
        self.offset = nn.Parameter(torch.tensor(0.0))
        self.module = nn.ModuleDict(
            {"compound": self.compound_tower, "protein": self.protein_tower}
        )
        self.module.register_parameter("scale", self.scale)
        self.module.register_parameter("offset", self.offset)
        self.transform: ProteinTransform | None = None
        self.history: list[dict[str, Any]] = []

    # ------------------------------------------------------------------ pieces

    def parameters(self):
        return self.module.parameters()

    def n_parameters(self) -> int:
        return int(sum(p.numel() for p in self.module.parameters()))

    def to(self, device: str) -> DualEncoder:
        self.module.to(device)
        return self

    def train_mode(self, on: bool = True) -> None:
        self.module.train(on)

    def initialise_head(self, y: np.ndarray) -> None:
        import torch

        with torch.no_grad():
            self.offset.fill_(float(np.mean(y)))
            self.scale.fill_(max(float(np.ptp(y)) / 2.0, 1e-3))

    # ---------------------------------------------------------------- scoring

    def project_compounds(self, fingerprints: torch.Tensor) -> torch.Tensor:
        """Library-side projection. Independent of any protein, so precomputable."""
        return self.compound_tower(fingerprints)

    def project_proteins(self, embeddings: torch.Tensor) -> torch.Tensor:
        return self.protein_tower(embeddings)

    def score_from_projections(
        self, compound_z: torch.Tensor, protein_z: torch.Tensor
    ) -> torch.Tensor:
        """Affine map on cosine. Output is in pKi units."""
        import torch.nn.functional as F  # noqa: N812

        cosine = F.cosine_similarity(compound_z, protein_z, dim=-1)
        return self.scale * cosine + self.offset

    def score_pairs(self, fingerprints: torch.Tensor, embeddings: torch.Tensor) -> torch.Tensor:
        return self.score_from_projections(
            self.project_compounds(fingerprints), self.project_proteins(embeddings)
        )

    def score_library(
        self, compound_z: torch.Tensor, protein_embedding: torch.Tensor
    ) -> torch.Tensor:
        """One protein against a precomputed compound matrix.

        The path the CLI uses. Asserted equal to `score_pairs` on the same inputs.
        """
        protein_z = self.project_proteins(protein_embedding.reshape(1, -1))
        return self.score_from_projections(compound_z, protein_z.expand(compound_z.shape[0], -1))


# ----------------------------------------------------------------- persistence


@dataclass
class Checkpoint:
    config: DualEncoderConfig
    transform: ProteinTransform
    history: list[dict[str, Any]]
    n_parameters: int
    extra: dict[str, Any] = field(default_factory=dict)


def save_checkpoint(model: DualEncoder, path: Path, extra: dict[str, Any] | None = None) -> Path:
    """Weights, the frozen transform and the training history, together.

    A checkpoint without its preprocessing transform is not reproducible: the
    same weights on differently scaled inputs are a different model.
    """
    import torch

    path.parent.mkdir(parents=True, exist_ok=True)
    if model.transform is None:
        raise ValueError(
            "refusing to save a checkpoint without its protein transform: the "
            "weights alone would not reproduce a prediction"
        )
    torch.save(
        {
            "state_dict": model.module.state_dict(),
            "config": asdict(model.config),
            "transform": {
                "mean": model.transform.mean,
                "scale": model.transform.scale,
                "fitted_on": model.transform.fitted_on,
                "n_fitted": model.transform.n_fitted,
            },
            "history": model.history,
            "n_parameters": model.n_parameters(),
            "extra": extra or {},
        },
        path,
    )
    return path


def load_checkpoint(path: Path, device: str = "cpu") -> tuple[DualEncoder, dict[str, Any]]:
    import torch

    blob = torch.load(path, map_location=device, weights_only=False)
    config = DualEncoderConfig(**blob["config"])
    model = DualEncoder(config)
    model.module.load_state_dict(blob["state_dict"])
    transform = blob["transform"]
    model.transform = ProteinTransform(
        mean=np.asarray(transform["mean"], dtype=np.float32),
        scale=np.asarray(transform["scale"], dtype=np.float32),
        fitted_on=transform.get("fitted_on", ""),
        n_fitted=int(transform.get("n_fitted", 0)),
    )
    model.history = blob.get("history", [])
    model.to(device)
    model.train_mode(False)
    return model, blob.get("extra", {})
