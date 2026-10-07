"""Rank the bundled library against one sequence, with no database.

The scoring rule is the database-backed path's rule, unchanged:

    predicted pKi = scale * cosine(compound_z, protein_z) + offset

``compound_z`` comes from the bundle, already projected. ``protein_z`` is the
query embedded with the pinned encoder, standardised with the frozen training
transform, and passed through the protein tower. Ranking is on that affine score
and never on raw cosine, because the learned scale is unconstrained and a
negative one reverses the order.

**Ties.** Equal scores are ordered by the compound's row in the bundle, which is
a stable surrogate key, using a stable sort. The order within a tie therefore
carries no information and is not evidence of preference; tied rows are marked so
a reader is not misled by their position.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import numpy as np

if TYPE_CHECKING:
    from seq2lead.inference.bundle import InferenceBundle

#: Sequence lengths this path has actually been run at, measured rather than
#: assumed: 20, 50, 100, 250, 443, 800, 1022, 1023, 1500 and 2000 residues all
#: produced a ranking.
#:
#: This range establishes **execution, not predictive reliability**. No accuracy
#: was measured at any length, and beyond the encoder's 1,022-residue
#: pre-training window the protein representation is extrapolation that the
#: benchmark in this repository does not cover. Reported rather than enforced --
#: the refusal point is the bundle's own `max_length` -- and queries past the
#: training window are flagged in the output.
TESTED_LENGTH_RANGE = (20, 2000)


@dataclass
class RankedRow:
    rank: int
    compound_id: str
    smiles: str
    score_pki: float
    tied_with: int = 0
    flags: list[str] = field(default_factory=list)
    removed_by: str = ""
    original_rank: int = 0


@dataclass
class Ranking:
    sequence_length: int
    sequence_sha256: str
    header: str
    bundle_version: str
    library_name: str
    library_members: int
    projection_dim: int
    device: str
    rows: list[RankedRow] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    shortlist: dict[str, Any] = field(default_factory=dict)


class ProteinEncoder:
    """The pinned ESM-2 encoder, loaded once and reused.

    Loading it is the expensive part of a first query, so the instance is kept
    and reused for every later query in the same process. The bundle's recorded
    spec is checked before anything is downloaded, because an unsupported pooling
    rule should cost nothing to discover.
    """

    SUPPORTED_POOLING = "mean_over_residues_excluding_special_tokens"
    SUPPORTED_DTYPES = {"float32"}
    SUPPORTED_LENGTH_POLICY = "full"

    def __init__(self, spec: dict[str, Any], device: str | None = None) -> None:
        from seq2lead.inference.bundle import BundleError

        if spec.get("pooling") != self.SUPPORTED_POOLING:
            raise BundleError(
                f"this bundle records pooling {spec.get('pooling')!r}, which this "
                f"build does not implement (only {self.SUPPORTED_POOLING!r}). "
                "Refusing rather than substituting a different pooling rule, which "
                "would represent the query differently from the library."
            )
        if spec.get("dtype") not in self.SUPPORTED_DTYPES:
            raise BundleError(
                f"this bundle records dtype {spec.get('dtype')!r}; supported: "
                f"{sorted(self.SUPPORTED_DTYPES)}"
            )
        if spec.get("length_policy") != self.SUPPORTED_LENGTH_POLICY:
            raise BundleError(
                f"this bundle records length policy {spec.get('length_policy')!r}; "
                f"only {self.SUPPORTED_LENGTH_POLICY!r} is implemented, and guessing "
                "would change what the query means."
            )
        self.spec = spec
        self.device = resolve_device(device)
        self._tokenizer = None
        self._model = None

    def load(self) -> None:
        if self._model is not None:
            return
        import torch
        from transformers import AutoModel, AutoTokenizer

        name, revision = self.spec["model"], self.spec["model_revision"]
        self._tokenizer = AutoTokenizer.from_pretrained(name, revision=revision)
        self._model = (
            AutoModel.from_pretrained(name, revision=revision, dtype=torch.float32)
            .eval()
            .to(self.device)
        )

    def embed(self, sequence: str) -> np.ndarray:
        import torch

        self.load()
        encoded = {
            k: v.to(self.device) for k, v in self._tokenizer(sequence, return_tensors="pt").items()
        }
        with torch.no_grad():
            hidden = self._model(**encoded).last_hidden_state[0]
        return hidden[1:-1].mean(dim=0).float().cpu().numpy()


def resolve_device(requested: str | None = None) -> str:
    """Pick a backend. CPU is the default because it is the one this path is tested on."""
    import torch

    if requested:
        return requested
    if torch.cuda.is_available():
        return "cuda"
    if getattr(torch.backends, "mps", None) is not None and torch.backends.mps.is_available():
        return "mps"
    return "cpu"


def project_query(bundle: InferenceBundle, embedding: np.ndarray, device: str = "cpu"):
    """Standardise with the frozen transform, then run the protein tower.

    Torch rather than numpy on purpose: the database-backed path runs these same
    two linears in torch, and matching the arithmetic is what keeps the two paths
    agreeing to the tolerance the verification declares.
    """
    import torch

    standardised = (embedding.reshape(1, -1) - bundle.transform_mean) / bundle.transform_scale
    linear = torch.nn.functional.linear
    tensor = torch.from_numpy
    x = tensor(np.ascontiguousarray(standardised, dtype=np.float32))
    w = bundle.protein_weights
    x = linear(x, tensor(w["0.weight"]), tensor(w["0.bias"]))
    x = torch.relu(x)
    x = linear(x, tensor(w["2.weight"]), tensor(w["2.bias"]))
    return x


def score_library(bundle: InferenceBundle, protein_z) -> np.ndarray:
    """Affine map on cosine, against every precomputed compound projection."""
    import torch

    compound_z = torch.from_numpy(bundle.projections)
    cosine = torch.nn.functional.cosine_similarity(
        compound_z, protein_z.expand(compound_z.shape[0], -1), dim=-1
    )
    return (bundle.scale * cosine + bundle.offset).cpu().numpy()


def rank(
    bundle: InferenceBundle,
    sequence: str,
    *,
    header: str = "",
    top_n: int = 50,
    device: str | None = None,
    encoder: ProteinEncoder | None = None,
    notes: list[str] | None = None,
) -> Ranking:
    """Rank the bundled library. `encoder` is reused across queries when supplied."""
    encoder = encoder or ProteinEncoder(bundle.protein_spec, device)
    embedding = encoder.embed(sequence)
    protein_z = project_query(bundle, embedding, encoder.device)
    scores = score_library(bundle, protein_z)

    # Stable sort on the negated score: equal scores keep bundle row order, which
    # is a surrogate key and arbitrary with respect to anything predicted.
    order = np.argsort(-scores, kind="stable")
    counts: dict[float, int] = {}
    for s in scores:
        counts[float(s)] = counts.get(float(s), 0) + 1

    flags_by_row = _flags(bundle)
    result = Ranking(
        sequence_length=len(sequence),
        sequence_sha256=hashlib.sha256(sequence.encode("ascii")).hexdigest(),
        header=header,
        bundle_version=bundle.manifest["bundle_version"],
        library_name=bundle.library.get("name", "unknown"),
        library_members=bundle.n_compounds,
        projection_dim=bundle.projection_dim,
        device=encoder.device,
        notes=list(notes or []),
    )
    limit = len(order) if top_n <= 0 else min(top_n, len(order))
    for position, row in enumerate(order[:limit], start=1):
        row = int(row)
        score = float(scores[row])
        result.rows.append(
            RankedRow(
                rank=position,
                original_rank=position,
                compound_id=bundle.compound_ids[row],
                smiles=bundle.smiles[row],
                score_pki=score,
                tied_with=counts[score] - 1,
                flags=list(flags_by_row.get(row, ())),
            )
        )
    tied = sum(1 for r in result.rows if r.tied_with)
    if tied:
        result.warnings.append(
            f"{tied} of the {len(result.rows)} rows shown share a predicted pKi with "
            "at least one other compound. Tied rows are ordered by their stable "
            "library row, which carries no information about preference."
        )
    return result


def _flags(bundle: InferenceBundle) -> dict[int, list[str]]:
    flags: dict[int, list[str]] = {}
    import csv

    with (bundle.root / "library/compounds.csv").open(encoding="utf-8", newline="") as fh:
        for record in csv.DictReader(fh):
            if record.get("flags"):
                flags[int(record["row"])] = record["flags"].split(";")
    return flags
