"""Wiring the real fits onto the as-of path, reusing the accepted M8/M9 code.

The models and the metric implementation are not rewritten here. `FeatureBank`,
the `Baseline` suite, `DualEncoder` and `train.fit` are the ones M8 and M9 were
scored with, which is the only way a number produced here can sit beside theirs
under metric version `m8/v2`. What this module supplies is the plumbing between
them and the as-of artifacts.

Two differences have to be bridged. The accepted code indexes entities by the
202609 schema's integer surrogate ids; the as-of path knows entities only by
content identity. And the accepted code reads cohorts from the database; the
as-of cohorts come from the verified export. So entities are given **run-scoped**
integer indices here -- local to one run, written into that run's manifest, and
never equated with any schema's ids.

The separation the contract demands is kept measurable rather than structural.
`fit_transforms` sees A-train alone. Every gradient and boosting update comes
from A-train rows. A-validation reaches a fit only through the per-epoch RMSE
that chooses a checkpoint, and `select_checkpoint` re-derives that cohort's
digest to confirm the set used for scoring was the one the runner holds.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np

from seq2lead.eval.cohort import Cohort
from seq2lead.eval.features import FeatureBank

if TYPE_CHECKING:
    from seq2lead.asof.features import FeatureBinding

#: Bumped when the wiring, the declared model set or the recorded fields change.
FITTING_VERSION = "m11h/fitting/v1"

#: The models the contract declares. B3P is deliberately absent: the contract
#: lists five baselines and the dual encoder, and adding a sixth baseline here
#: would be a new experiment rather than the declared one.
DECLARED_BASELINES = (
    "B0-target-mean",
    "B1-ligand-ecfp4-lgbm",
    "B2-protein-esm2-lgbm",
    "B3L-ligand-1nn",
    "B4-concat-mlp",
)
MAIN_MODEL = "dual-encoder"
DECLARED_MODELS = (*DECLARED_BASELINES, MAIN_MODEL)

#: Written to every run directory so an output path cannot be mistaken for an
#: accepted artifact.
RUN_MARKER = "asof-run.json"


class FittingError(RuntimeError):
    """The fitting wiring is not what the contract declares."""


class OutputCollision(FittingError):
    """A planned output path is already occupied."""


# --------------------------------------------------------------- output paths


@dataclass(frozen=True)
class RunPaths:
    """Every path a run will write, allocated and checked before the first fit.

    A fit that discovers halfway through that it cannot write its checkpoint has
    already spent the compute and, worse, may have overwritten something. So the
    whole tree is planned and refused up front.
    """

    root: Path
    run_id: str

    @property
    def planned(self) -> dict[str, Path]:
        return {
            "marker": self.root / RUN_MARKER,
            "transform": self.root / "protein-transform.npz",
            "training_records": self.root / "training-records.json",
            "predictions": self.root / "predictions.npz",
            "evaluation_table": self.root / "evaluation-pairs.jsonl",
            "results": self.root / "results.json",
            "manifest": self.root / "manifest.json",
            "checkpoints": self.root / "checkpoints",
        }

    def checkpoint(self, model: str, seed: int | None) -> Path:
        tag = model if seed is None else f"{model}-seed{seed}"
        return self.root / "checkpoints" / f"{tag}.pt"

    def preflight(self, models: tuple[str, ...], seeds: tuple[int, ...]) -> dict[str, Any]:
        """Refuse before the first fit if anything would be overwritten."""
        occupied = [str(p) for p in self.planned.values() if p.exists()]
        checkpoints = [
            self.checkpoint(m, None if m in DETERMINISTIC else s)
            for m in models
            for s in (seeds if m not in DETERMINISTIC else (seeds[0],))
        ]
        occupied += [str(p) for p in checkpoints if p.exists()]
        if occupied:
            msg = (
                f"{len(occupied)} planned output paths are already occupied, so the run is "
                f"refused before fitting: {sorted(occupied)[:5]}"
            )
            raise OutputCollision(msg)
        if self.root.exists() and any(self.root.iterdir()):
            msg = f"the run directory {self.root} exists and is not empty; refusing"
            raise OutputCollision(msg)
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "checkpoints").mkdir(exist_ok=True)
        return {
            "run_id": self.run_id,
            "root": str(self.root),
            "planned": {k: str(v) for k, v in sorted(self.planned.items())},
            "planned_checkpoints": sorted(str(p) for p in checkpoints),
            "collisions": [],
            "note": (
                "every path was checked before the first fit; a collision refuses rather "
                "than overwriting, and accepted artifacts live outside this tree"
            ),
        }


#: Models whose output does not depend on the seed. M8's convention: they are
#: fitted once and their seed spread is zero by construction, not by luck.
DETERMINISTIC = frozenset({"B0-target-mean", "B3L-ligand-1nn"})


# -------------------------------------------------------- run-scoped indexing


@dataclass
class LocalIndex:
    """Content key -> a run-scoped integer. Never a schema's surrogate id."""

    kind: str
    keys: list[str] = field(default_factory=list)
    index: dict[str, int] = field(default_factory=dict)

    def add(self, key: str) -> int:
        existing = self.index.get(key)
        if existing is not None:
            return existing
        assigned = len(self.keys)
        self.index[key] = assigned
        self.keys.append(key)
        return assigned

    def of(self, key: str) -> int:
        try:
            return self.index[key]
        except KeyError as exc:
            msg = f"{self.kind}: {key!r} has no run-scoped index"
            raise FittingError(msg) from exc

    def digest(self) -> str:
        body = "\n".join(f"{i}\t{k}" for i, k in enumerate(self.keys))
        return hashlib.sha256(body.encode("utf-8")).hexdigest()

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "entities": len(self.keys),
            "digest": self.digest(),
            "scope": "this run only; these integers are not any schema's ids",
        }


def build_bank(
    bindings: dict[str, FeatureBinding],
    compounds: set[str],
    sequences: set[str],
) -> tuple[FeatureBank, LocalIndex, LocalIndex]:
    """Assemble the accepted `FeatureBank` over run-scoped indices.

    Vectors come from the verified bindings, so a key the binding cannot supply
    raises here rather than silently producing a zero row.
    """
    compound_ix, sequence_ix = LocalIndex("compound"), LocalIndex("sequence")
    packed = np.zeros((len(compounds), 256), dtype=np.uint8)
    for key in sorted(compounds):
        vector = bindings["ecfp4"].vector(key)
        if vector is None:
            msg = f"compound {key!r} has no vector in the verified binding"
            raise FittingError(msg)
        packed[compound_ix.add(key)] = vector
    embeddings = np.zeros((len(sequences), 1280), dtype=np.float32)
    for key in sorted(sequences):
        vector = bindings["esm2"].vector(key)
        if vector is None:
            msg = f"sequence {key!r} has no vector in the verified binding"
            raise FittingError(msg)
        embeddings[sequence_ix.add(key)] = vector
    bank = FeatureBank(
        compound_index={i: i for i in range(len(compound_ix.keys))},
        compound_packed=packed,
        target_index={i: i for i in range(len(sequence_ix.keys))},
        target_vectors=embeddings,
    )
    return bank, compound_ix, sequence_ix


def cohort_from_pairs(
    pairs: list[str],
    targets: list[float] | None,
    compound_ix: LocalIndex,
    sequence_ix: LocalIndex,
    *,
    partition: str,
    labels: list[str] | None = None,
    strata: list[str] | None = None,
) -> Cohort:
    """Build the accepted `Cohort` shape over run-scoped indices."""
    n = len(pairs)
    compound = np.empty(n, dtype=np.int64)
    target = np.empty(n, dtype=np.int64)
    for row, pair in enumerate(pairs):
        c, t = pair.split("|", 1)
        compound[row] = compound_ix.of(c)
        target[row] = sequence_ix.of(t)
    return Cohort(
        split="asof-202601-202609",
        partition=partition,
        endpoint_id=-1,
        compound_id=compound,
        target_id=target,
        y=np.asarray(targets if targets is not None else [np.nan] * n, dtype=np.float64),
        label=np.asarray(labels if labels is not None else ["none"] * n, dtype=object),
        stratum=np.asarray(strata if strata is not None else ["none"] * n, dtype=object),
    )


def cohort_digest(cohort: Cohort) -> str:
    """Identifies the exact rows a cohort holds, for cross-checking the wiring."""
    h = hashlib.sha256()
    h.update(f"{cohort.partition}\n{len(cohort)}\n".encode())
    for c, t, y in zip(cohort.compound_id, cohort.target_id, cohort.y, strict=True):
        h.update(f"{int(c)}\t{int(t)}\t{y!r}\n".encode())
    return h.hexdigest()


# ------------------------------------------------------------------- the fits


@dataclass
class FitOutcome:
    """One fitted model, its selection record and where it was written."""

    model: str
    seed: int | None
    seconds: float
    n_train: int
    n_validation: int
    selected_on: str
    best_epoch: int | None = None
    best_validation_rmse: float | None = None
    epochs_run: int | None = None
    stopped_early: bool | None = None
    n_parameters: int | None = None
    history: list[dict[str, Any]] = field(default_factory=list)
    checkpoint: str | None = None
    notes: dict[str, Any] = field(default_factory=dict)
    failure: str | None = None


def declared_config(seed: int, settings: dict[str, Any]):
    """The dual encoder's configuration, taken from the frozen settings."""
    from seq2lead.models.dual_encoder import DualEncoderConfig

    return DualEncoderConfig(
        projection_dim=int(settings["projection_dim"]),
        learning_rate=float(settings["learning_rate"]),
        batch_size=int(settings["batch_size"]),
        max_epochs=int(settings["max_epochs"]),
        patience=int(settings["early_stopping_patience"]),
        seed=seed,
    )


def write_transform(transform, path: Path) -> str:
    np.savez(
        path, mean=transform.mean, scale=transform.scale,
        n_fitted=np.asarray([transform.n_fitted]),
    )
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_records(outcomes: list[FitOutcome], path: Path) -> str:
    path.write_text(
        json.dumps([asdict(o) for o in outcomes], indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return hashlib.sha256(path.read_bytes()).hexdigest()


def timed() -> float:
    return time.perf_counter()
