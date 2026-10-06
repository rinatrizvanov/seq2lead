"""Fitting the dual encoder, and the records that make a run reproducible.

Selection reads validation only. The test partition is scored once, after the
configuration is frozen, and never selects anything.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np

from seq2lead.models.dual_encoder import DualEncoder, DualEncoderConfig, ProteinTransform

if TYPE_CHECKING:
    from seq2lead.eval.cohort import Cohort
    from seq2lead.eval.features import FeatureBank

RUN_ROOT = Path("data/m9")


@dataclass
class TrainingRecord:
    """Everything needed to explain, or repeat, one fit."""

    config: dict[str, Any]
    config_digest: str
    split: str
    seed: int
    projection_dim: int
    n_train: int
    n_validation: int
    n_parameters: int
    best_epoch: int
    best_validation_rmse: float
    epochs_run: int
    seconds: float
    device: str
    history: list[dict[str, Any]] = field(default_factory=list)
    checkpoint: str = ""
    stopped_early: bool = False


def _device() -> str:
    import torch

    if torch.backends.mps.is_available():
        return "mps"
    return "cuda" if torch.cuda.is_available() else "cpu"


def _batches(bank: FeatureBank, cohort: Cohort, index: np.ndarray, transform, device: str):
    import torch

    fingerprints = torch.from_numpy(bank.ligand(cohort.compound_id[index]))
    proteins = torch.from_numpy(transform.apply(bank.protein(cohort.target_id[index])))
    return fingerprints.to(device), proteins.to(device)


def predict(model: DualEncoder, bank: FeatureBank, cohort: Cohort, batch: int = 4096) -> np.ndarray:
    import torch

    device = next(model.module.parameters()).device.type
    model.train_mode(False)
    out = np.empty(len(cohort), dtype=np.float64)
    with torch.no_grad():
        for start in range(0, len(cohort), batch):
            index = np.arange(start, min(start + batch, len(cohort)))
            fingerprints, proteins = _batches(bank, cohort, index, model.transform, device)
            out[index] = model.score_pairs(fingerprints, proteins).cpu().numpy()
    return out


def fit(
    bank: FeatureBank,
    train: Cohort,
    validation: Cohort,
    config: DualEncoderConfig,
    split_name: str,
    run_dir: Path = RUN_ROOT,
    progress: bool = False,
) -> tuple[DualEncoder, TrainingRecord]:
    """Fit one configuration on one split. Selection is validation RMSE."""
    import torch

    started = time.perf_counter()
    device = _device()
    rng = np.random.default_rng(config.seed)

    model = DualEncoder(config)
    # Preprocessing fitted on training inputs only, then frozen.
    model.transform = ProteinTransform.fit(bank.protein(train.target_id), fitted_on="train")
    model.initialise_head(train.y)
    model.to(device)

    optimiser = torch.optim.Adam(model.parameters(), lr=config.learning_rate)
    loss_fn = torch.nn.MSELoss()
    best = (float("inf"), 0, None)
    epochs_run = 0

    for epoch in range(1, config.max_epochs + 1):
        epochs_run = epoch
        model.train_mode(True)
        order = rng.permutation(len(train))
        total = 0.0
        for start in range(0, len(order), config.batch_size):
            index = order[start : start + config.batch_size]
            fingerprints, proteins = _batches(bank, train, index, model.transform, device)
            targets = torch.from_numpy(train.y[index].astype(np.float32)).to(device)
            optimiser.zero_grad()
            loss = loss_fn(model.score_pairs(fingerprints, proteins), targets)
            loss.backward()
            optimiser.step()
            total += float(loss.detach()) * len(index)

        predicted = predict(model, bank, validation)
        rmse = float(np.sqrt(np.mean((predicted - validation.y) ** 2)))
        model.history.append(
            {
                "epoch": epoch,
                "train_mse": total / max(len(order), 1),
                "validation_rmse": rmse,
            }
        )
        if progress:
            print(f"      epoch {epoch:>2}  val RMSE {rmse:.4f}", flush=True)  # noqa: T201
        if rmse < best[0] - 1e-6:
            best = (
                rmse,
                epoch,
                {k: v.detach().clone() for k, v in model.module.state_dict().items()},
            )
        elif epoch - best[1] >= config.patience:
            break

    if best[2] is not None:
        model.module.load_state_dict(best[2])
    model.train_mode(False)

    record = TrainingRecord(
        config=asdict(config),
        config_digest=config.digest(),
        split=split_name,
        seed=config.seed,
        projection_dim=config.projection_dim,
        n_train=len(train),
        n_validation=len(validation),
        n_parameters=model.n_parameters(),
        best_epoch=best[1],
        best_validation_rmse=best[0],
        epochs_run=epochs_run,
        seconds=time.perf_counter() - started,
        device=device,
        history=model.history,
        stopped_early=epochs_run < config.max_epochs,
    )
    return model, record


def write_record(record: TrainingRecord, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(record), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path
