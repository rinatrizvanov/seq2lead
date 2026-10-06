"""The baseline suite. Every model predicts pKi; ranking uses that prediction.

B0 and B3 are deterministic and run once. B1, B2 and B4 are stochastic and run
over the declared seed list.

All preprocessing is fitted on the training partition and applied unchanged
afterwards. Validation selects models and stopping points; the test partition is
scored once, after configurations are frozen, and selects nothing.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from seq2lead.eval.cohort import Cohort
    from seq2lead.eval.features import FeatureBank

#: Cap on the reference block for the ligand nearest-neighbour search, declared
#: and versioned. An exhaustive test-by-train Tanimoto matrix over a quarter of
#: a million compounds is ~10^11 comparisons and is never built.
LIGAND_NEIGHBOUR_CAP = 4096


@dataclass
class FitReport:
    """What a fit actually did -- recorded so a leaderboard row is explicable."""

    model: str
    seed: int | None
    seconds: float
    n_train: int
    notes: dict[str, object] = field(default_factory=dict)


class Baseline:
    """Common interface. `deterministic` models ignore the seed list."""

    name = "base"
    deterministic = True
    stochastic_note = ""

    def fit(self, bank: FeatureBank, train: Cohort, validation: Cohort, seed: int) -> FitReport:
        raise NotImplementedError

    def predict(self, bank: FeatureBank, cohort: Cohort) -> np.ndarray:
        raise NotImplementedError


# ------------------------------------------------------------------------ B0


class GlobalAndTargetMean(Baseline):
    """B0 -- the floor almost nobody reports.

    Predicts the training mean pKi of the target. A target unseen in training
    falls back to the **global training mean**, declared here rather than left to
    a NaN: on a cold-protein split every test target is unseen, so the fallback
    *is* the model there, and that is exactly the point of running it.

    Every compound of a target receives the same score, so ranking within a
    target is fully tied. The tie-aware metrics report that as chance rather than
    letting row order manufacture a signal.
    """

    name = "B0-target-mean"
    deterministic = True

    def __init__(self) -> None:
        self.global_mean = 0.0
        self.target_mean: dict[int, float] = {}

    def fit(self, bank, train, validation, seed) -> FitReport:  # noqa: ARG002
        started = time.perf_counter()
        self.global_mean = float(np.mean(train.y)) if len(train) else 0.0
        sums: dict[int, float] = {}
        counts: dict[int, int] = {}
        for target_id, value in zip(train.target_id, train.y, strict=True):
            key = int(target_id)
            sums[key] = sums.get(key, 0.0) + float(value)
            counts[key] = counts.get(key, 0) + 1
        self.target_mean = {k: sums[k] / counts[k] for k in sums}
        return FitReport(
            model=self.name,
            seed=None,
            seconds=time.perf_counter() - started,
            n_train=len(train),
            notes={
                "global_mean_pki": round(self.global_mean, 4),
                "targets_seen": len(self.target_mean),
                "fallback": "global training mean for unseen targets",
            },
        )

    def predict(self, bank, cohort) -> np.ndarray:  # noqa: ARG002
        return np.array(
            [self.target_mean.get(int(t), self.global_mean) for t in cohort.target_id],
            dtype=np.float64,
        )


# ------------------------------------------------------------------ B1 / B2


class LightGBMBaseline(Baseline):
    """Shared plumbing for the two single-modality gradient-boosted models."""

    deterministic = False

    def __init__(self, n_estimators: int = 400, learning_rate: float = 0.05) -> None:
        self.n_estimators = n_estimators
        self.learning_rate = learning_rate
        self.model = None
        self.best_iteration: int | None = None

    def _matrix(self, bank: FeatureBank, cohort: Cohort) -> np.ndarray:
        raise NotImplementedError

    def fit(self, bank, train, validation, seed) -> FitReport:
        import lightgbm as lgb

        started = time.perf_counter()
        x_train = self._matrix(bank, train)
        x_valid = self._matrix(bank, validation)
        self.model = lgb.LGBMRegressor(
            n_estimators=self.n_estimators,
            learning_rate=self.learning_rate,
            random_state=seed,
            n_jobs=-1,
            verbose=-1,
        )
        self.model.fit(
            x_train,
            train.y,
            eval_X=x_valid,
            eval_y=validation.y,
            eval_metric="rmse",
            callbacks=[lgb.early_stopping(50, verbose=False)],
        )
        self.best_iteration = int(getattr(self.model, "best_iteration_", 0) or 0)
        return FitReport(
            model=self.name,
            seed=seed,
            seconds=time.perf_counter() - started,
            n_train=len(train),
            notes={
                "best_iteration": self.best_iteration,
                "selected_on": "validation RMSE, early stopping 50 rounds",
                "n_features": int(x_train.shape[1]),
            },
        )

    def predict(self, bank, cohort) -> np.ndarray:
        return np.asarray(self.model.predict(self._matrix(bank, cohort)), dtype=np.float64)


class LigandOnly(LightGBMBaseline):
    """B1 -- the bias gate. If this matches a joint model, the split is ligand-separable."""

    name = "B1-ligand-ecfp4-lgbm"

    def _matrix(self, bank, cohort):
        return bank.ligand(cohort.compound_id)


class ProteinOnly(LightGBMBaseline):
    """B2 -- the target prior alone.

    Every compound of a target gets one score, because the input is identical
    for all of them. Ranking within a target is therefore tied by construction.
    """

    name = "B2-protein-esm2-lgbm"

    def _matrix(self, bank, cohort):
        return bank.protein(cohort.target_id)


# ------------------------------------------------------------------------ B3


class LigandNeighbour(Baseline):
    """B3L -- nearest measured ligand *for the same target*.

    Neighbour selection: among the compounds measured against this target in
    training, take the one with the highest ECFP4 Tanimoto to the query, and
    predict its training pKi. Searching within the target keeps the comparison
    bounded by that target's training set instead of the whole corpus, which is
    what makes this affordable without approximation.

    Where a target has more than `LIGAND_NEIGHBOUR_CAP` training compounds, the
    reference block is a seeded sample of that size -- declared, versioned, and
    reported, not silent.

    No-neighbour fallback: a target absent from training has no reference at all,
    so the prediction is the global training mean.
    """

    name = "B3L-ligand-1nn"
    deterministic = True

    def __init__(self, cap: int = LIGAND_NEIGHBOUR_CAP, seed: int = 20260930) -> None:
        self.cap = cap
        self.seed = seed
        self.global_mean = 0.0
        self._by_target: dict[int, tuple[np.ndarray, np.ndarray]] = {}
        self.sampled_targets = 0

    def fit(self, bank, train, validation, seed) -> FitReport:  # noqa: ARG002
        started = time.perf_counter()
        self.global_mean = float(np.mean(train.y)) if len(train) else 0.0
        rng = np.random.default_rng(self.seed)
        order = np.argsort(train.target_id, kind="mergesort")
        targets = train.target_id[order]
        compounds = train.compound_id[order]
        values = train.y[order]
        self._by_target.clear()
        self.sampled_targets = 0
        start = 0
        for end in np.flatnonzero(np.diff(targets)) + 1:
            self._store(rng, int(targets[start]), compounds[start:end], values[start:end])
            start = int(end)
        if len(targets):
            self._store(rng, int(targets[start]), compounds[start:], values[start:])
        return FitReport(
            model=self.name,
            seed=None,
            seconds=time.perf_counter() - started,
            n_train=len(train),
            notes={
                "neighbour": "highest ECFP4 Tanimoto among the target's training compounds",
                "aggregation": "1-NN, the neighbour's training pKi",
                "fallback": "global training mean when the target is unseen",
                "reference_cap": self.cap,
                "targets_sampled_at_cap": self.sampled_targets,
            },
        )

    def _store(self, rng, target_id, compounds, values) -> None:
        if len(compounds) > self.cap:
            pick = rng.choice(len(compounds), self.cap, replace=False)
            compounds, values = compounds[pick], values[pick]
            self.sampled_targets += 1
        self._by_target[target_id] = (compounds, values)

    def predict(self, bank, cohort) -> np.ndarray:
        out = np.full(len(cohort), self.global_mean, dtype=np.float64)
        order = np.argsort(cohort.target_id, kind="mergesort")
        targets = cohort.target_id[order]
        start = 0
        boundaries = list(np.flatnonzero(np.diff(targets)) + 1) + [len(targets)]
        for end in boundaries:
            if end == start:
                continue
            block = order[start:end]
            reference = self._by_target.get(int(targets[start]))
            if reference is not None:
                ref_compounds, ref_values = reference
                similarity = _tanimoto(bank, cohort.compound_id[block], ref_compounds)
                out[block] = ref_values[np.argmax(similarity, axis=1)]
            start = int(end)
        return out


class ProteinNeighbour(Baseline):
    """B3P -- nearest training target by ESM-2 cosine, predict its training mean.

    Neighbour selection: cosine over mean-pooled embeddings against every target
    present in training. That reference set is a few thousand rows, so the search
    is exact rather than approximate.

    Aggregation: the neighbour target's mean training pKi. Fallback: the global
    training mean when training has no targets at all.

    Like B0 and B2, this ties every compound of a target.
    """

    name = "B3P-protein-1nn"
    deterministic = True

    def __init__(self) -> None:
        self.global_mean = 0.0
        self._ids = np.zeros(0, dtype=np.int64)
        self._unit = np.zeros((0, 0), dtype=np.float32)
        self._means = np.zeros(0, dtype=np.float64)

    def fit(self, bank, train, validation, seed) -> FitReport:  # noqa: ARG002
        started = time.perf_counter()
        self.global_mean = float(np.mean(train.y)) if len(train) else 0.0
        unique = np.unique(train.target_id)
        means = np.array(
            [float(train.y[train.target_id == t].mean()) for t in unique], dtype=np.float64
        )
        vectors = bank.protein(unique)
        norms = np.linalg.norm(vectors, axis=1, keepdims=True)
        self._ids = unique
        self._unit = vectors / np.maximum(norms, 1e-9)
        self._means = means
        return FitReport(
            model=self.name,
            seed=None,
            seconds=time.perf_counter() - started,
            n_train=len(train),
            notes={
                "neighbour": "highest ESM-2 cosine among training targets",
                "aggregation": "that target's mean training pKi",
                "fallback": "global training mean when training has no targets",
                "reference_targets": int(unique.shape[0]),
                "search": "exact",
            },
        )

    def predict(self, bank, cohort) -> np.ndarray:
        if self._ids.shape[0] == 0:
            return np.full(len(cohort), self.global_mean, dtype=np.float64)
        unique, inverse = np.unique(cohort.target_id, return_inverse=True)
        vectors = bank.protein(unique)
        unit = vectors / np.maximum(np.linalg.norm(vectors, axis=1, keepdims=True), 1e-9)
        nearest = np.argmax(unit @ self._unit.T, axis=1)
        return self._means[nearest][inverse]


# ------------------------------------------------------------------------ B4


class ConcatMLP(Baseline):
    """B4 -- `[ECFP4 ‖ ESM-2]` through a small MLP.

    Standardisation of the protein block is fitted on **training inputs only**
    and applied unchanged to validation and test. Fingerprint bits are already
    0/1 and are left alone.
    """

    name = "B4-concat-mlp"
    deterministic = False

    def __init__(self, hidden: int = 512, epochs: int = 12, batch_size: int = 512) -> None:
        self.hidden = hidden
        self.epochs = epochs
        self.batch_size = batch_size
        self.model = None
        self._mean = None
        self._scale = None
        self.best_epoch: int | None = None

    def _inputs(self, bank, cohort, index):
        ligand = bank.ligand(cohort.compound_id[index])
        protein = (bank.protein(cohort.target_id[index]) - self._mean) / self._scale
        return np.hstack([ligand, protein]).astype(np.float32)

    def fit(self, bank, train, validation, seed) -> FitReport:
        import torch
        from torch import nn

        started = time.perf_counter()
        torch.manual_seed(seed)
        rng = np.random.default_rng(seed)

        # Fitted on training inputs only.
        train_protein = bank.protein(train.target_id)
        self._mean = train_protein.mean(axis=0, keepdims=True)
        self._scale = np.maximum(train_protein.std(axis=0, keepdims=True), 1e-6)
        del train_protein

        device = "mps" if torch.backends.mps.is_available() else "cpu"
        width = 2048 + bank.target_vectors.shape[1]
        self.model = nn.Sequential(
            nn.Linear(width, self.hidden),
            nn.ReLU(),
            nn.Linear(self.hidden, self.hidden // 2),
            nn.ReLU(),
            nn.Linear(self.hidden // 2, 1),
        ).to(device)
        optimiser = torch.optim.Adam(self.model.parameters(), lr=1e-3)
        loss_fn = nn.MSELoss()

        best = (float("inf"), 0, None)
        for epoch in range(1, self.epochs + 1):
            self.model.train()
            order = rng.permutation(len(train))
            for start in range(0, len(order), self.batch_size):
                index = order[start : start + self.batch_size]
                x = torch.from_numpy(self._inputs(bank, train, index)).to(device)
                y = torch.from_numpy(train.y[index].astype(np.float32)).to(device)
                optimiser.zero_grad()
                loss = loss_fn(self.model(x).squeeze(-1), y)
                loss.backward()
                optimiser.step()
            rmse = float(
                np.sqrt(np.mean((self._predict(bank, validation, device) - validation.y) ** 2))
            )
            if rmse < best[0]:
                best = (
                    rmse,
                    epoch,
                    {k: v.detach().clone() for k, v in self.model.state_dict().items()},
                )
        if best[2] is not None:
            self.model.load_state_dict(best[2])
        self.best_epoch = best[1]
        self._device = device
        return FitReport(
            model=self.name,
            seed=seed,
            seconds=time.perf_counter() - started,
            n_train=len(train),
            notes={
                "best_epoch": best[1],
                "best_validation_rmse": round(best[0], 4),
                "selected_on": "validation RMSE per epoch",
                "device": device,
                "standardisation": "protein block, fitted on training inputs only",
            },
        )

    def _predict(self, bank, cohort, device) -> np.ndarray:
        import torch

        self.model.eval()
        out = np.empty(len(cohort), dtype=np.float64)
        with torch.no_grad():
            for start in range(0, len(cohort), 4096):
                index = np.arange(start, min(start + 4096, len(cohort)))
                x = torch.from_numpy(self._inputs(bank, cohort, index)).to(device)
                out[index] = self.model(x).squeeze(-1).cpu().numpy()
        return out

    def predict(self, bank, cohort) -> np.ndarray:
        return self._predict(bank, cohort, getattr(self, "_device", "cpu"))


def _tanimoto(bank, query_ids, reference_ids) -> np.ndarray:
    from seq2lead.eval.features import tanimoto_to_reference

    return tanimoto_to_reference(bank.ligand_bits(query_ids), bank.ligand_bits(reference_ids))


#: The suite, in leaderboard order.
SUITE = (GlobalAndTargetMean, LigandOnly, ProteinOnly, LigandNeighbour, ProteinNeighbour, ConcatMLP)
