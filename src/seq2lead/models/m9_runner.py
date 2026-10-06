"""The M9 pass: select on validation, freeze, then score the test partitions once.

Two phases, deliberately separated so the freeze is a real event with an
artifact rather than an intention. Phase one fits every declared configuration
and records validation RMSE. Phase two reads the frozen selection and scores
test. Nothing in phase two can change what phase one chose.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np

from seq2lead.eval.cohort import build_cohort, ranking_cohort
from seq2lead.eval.runner import evaluate_ranking, evaluate_regression
from seq2lead.models.dual_encoder import save_checkpoint
from seq2lead.models.m9_artifacts import effective_settings
from seq2lead.models.train import fit, predict, write_record

if TYPE_CHECKING:
    import psycopg

    from seq2lead.eval.config import ExperimentConfig
    from seq2lead.eval.features import FeatureBank

RUN_ROOT = Path("data/m9")
PREDICTION_DIR = Path("data/predictions_m9")
SELECTION_PATH = Path("reports/results/m9_selection.json")

MODEL_NAME = "M9-dual-encoder"


@dataclass
class SelectionEntry:
    split: str
    projection_dim: int
    validation_rmse: float
    best_epoch: int
    seconds: float
    n_validation_pairs: int
    n_validation_targets: int
    sufficient_coverage: bool
    chosen: bool = False
    fallback_from: str | None = None


@dataclass
class SelectionRecord:
    experiment: str
    metric: str
    direction: str
    min_validation_pairs: int
    min_validation_targets: int
    seed: int
    # Binding fields: what this selection was frozen against. Defaulted so a
    # pre-binding record still parses, but `check_selection_binding` refuses an
    # empty one unless legacy recovery is asked for explicitly.
    config_sha256: str = ""
    declared_splits: list[str] = field(default_factory=list)
    declared_projection_dims: list[int] = field(default_factory=list)
    entries: list[SelectionEntry] = field(default_factory=list)
    chosen: dict[str, int] = field(default_factory=dict)
    frozen_at: str = ""
    note: str = ""


def selection_record_path(
    label: str, split: str, dim: int, seed: int, run_root: Path = RUN_ROOT
) -> Path:
    """Where one selection fit's training record goes, scoped by label."""
    return run_root / "selection" / label / f"{split}__h{dim}__seed{seed}.json"


def select(
    conn: psycopg.Connection,
    config: ExperimentConfig,
    bank: FeatureBank,
    projection_dims: tuple[int, ...],
    seed: int,
    min_pairs: int = 1000,
    min_targets: int = 20,
    run_root: Path = RUN_ROOT,
    progress: bool = False,
    label: str = "default",
    selection_path: Path | None = None,
    overwrite: bool = False,
) -> SelectionRecord:
    """Phase one. Validation only -- the test partitions are not touched here.

    Every destination -- each selection training record and the frozen selection
    JSON -- is checked before the first fit. Discovering the collision on the
    last split would already have overwritten the earlier ones, and would have
    cost eight fits to find out.
    """
    from datetime import UTC, datetime

    from seq2lead.models.m9_artifacts import ArtifactCollision

    planned = [
        selection_record_path(label, split.name, dim, seed, run_root)
        for split in config.splits
        for dim in projection_dims
    ]
    destination = selection_path or SELECTION_PATH
    if not overwrite:
        occupied = [p for p in [*planned, destination] if p.exists()]
        if occupied:
            raise ArtifactCollision(
                f"{len(occupied)} selection destination(s) already exist, e.g. "
                f"{[str(p) for p in occupied[:3]]}. Refusing before any selection fit "
                "starts. Use a new label, or pass overwrite explicitly if you mean to "
                "discard the existing selection."
            )

    record = SelectionRecord(
        experiment=config.version,
        config_sha256=config.config_sha256,
        declared_splits=[s.name for s in config.splits],
        declared_projection_dims=list(projection_dims),
        metric="validation_rmse",
        direction="minimise",
        min_validation_pairs=min_pairs,
        min_validation_targets=min_targets,
        seed=seed,
    )
    for split in config.splits:
        train = build_cohort(conn, config, split.name, "train")
        validation = build_cohort(conn, config, split.name, "validation")
        sufficient = len(validation) >= min_pairs and validation.n_targets >= min_targets
        for dim in projection_dims:
            settings = effective_settings(config, dim, seed)
            if progress:
                print(f"    {split.name} h={dim}", flush=True)  # noqa: T201
            model, training = fit(bank, train, validation, settings, split.name)
            write_record(training, selection_record_path(label, split.name, dim, seed, run_root))
            del model
            record.entries.append(
                SelectionEntry(
                    split=split.name,
                    projection_dim=dim,
                    validation_rmse=training.best_validation_rmse,
                    best_epoch=training.best_epoch,
                    seconds=training.seconds,
                    n_validation_pairs=len(validation),
                    n_validation_targets=validation.n_targets,
                    sufficient_coverage=sufficient,
                )
            )

    # Choose per split, applying the declared fallback where coverage is thin.
    reference = None
    for split in config.splits:
        candidates = [e for e in record.entries if e.split == split.name]
        if not candidates:
            continue
        best = min(candidates, key=lambda e: e.validation_rmse)
        if split.name == "random_pair-v3":
            reference = best.projection_dim
    for split in config.splits:
        candidates = [e for e in record.entries if e.split == split.name]
        if not candidates:
            continue
        best = min(candidates, key=lambda e: e.validation_rmse)
        if not best.sufficient_coverage and reference is not None:
            chosen_dim = reference
            for entry in candidates:
                if entry.projection_dim == chosen_dim:
                    entry.chosen = True
                    entry.fallback_from = "random_pair-v3"
        else:
            chosen_dim = best.projection_dim
            best.chosen = True
        record.chosen[split.name] = chosen_dim

    record.frozen_at = datetime.now(UTC).isoformat(timespec="seconds")
    record.note = (
        "Selected on validation RMSE only. Frozen before any test partition was "
        "scored; the test pass reads this file and cannot change it."
    )
    return record


def write_selection(
    record: SelectionRecord, path: Path = SELECTION_PATH, *, overwrite: bool = False
) -> Path:
    """Freeze a selection. A conflicting replacement is refused.

    The frozen selection is what the test phase reads, so silently replacing it
    would change which configuration a already-published result was produced
    under. An identical rewrite is a no-op, so re-running is safe.
    """
    from seq2lead.models.m9_artifacts import ArtifactCollision

    path.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(asdict(record), indent=2, sort_keys=True) + "\n"
    if path.exists() and not overwrite:
        current = path.read_text(encoding="utf-8")
        if current == body:
            return path
        raise ArtifactCollision(
            f"{path} already holds a different frozen selection. Refusing to replace "
            "it: the test phase reads this file, so overwriting would change which "
            "configuration a published result was produced under. Write to a new path, "
            "or pass overwrite explicitly."
        )
    path.write_text(body, encoding="utf-8")
    return path


def read_selection(path: Path = SELECTION_PATH) -> SelectionRecord:
    raw = json.loads(path.read_text(encoding="utf-8"))
    entries = [SelectionEntry(**e) for e in raw.pop("entries", [])]
    return SelectionRecord(**raw, entries=entries)


def score_test(
    conn: psycopg.Connection,
    config: ExperimentConfig,
    bank: FeatureBank,
    selection: SelectionRecord,
    seeds: tuple[int, ...],
    run_root: Path = RUN_ROOT,
    prediction_dir: Path = PREDICTION_DIR,
    progress: bool = False,
    run_label: str = "final",
    overwrite: bool = False,
    binding: Any = None,
) -> list[dict[str, Any]]:
    """Phase two. The frozen configuration, scored once per seed per split."""
    from seq2lead.models.binding import BindingError
    from seq2lead.models.m9_artifacts import preflight, run_paths

    # A checkpoint without a binding depends on a sidecar that may never be
    # written, and inference then refuses it. Required here, before any fit, so
    # the failure costs nothing rather than twenty models.
    if binding is None:
        raise BindingError(
            "score_test() requires a feature binding: without it every checkpoint "
            "records `feature_binding: None` and can only be used if someone "
            "remembers to write a sidecar afterwards. Build one with "
            "`binding.from_experiment(conn, config)`."
        )
    if binding.experiment != config.version:
        raise BindingError(
            f"binding was built for experiment {binding.experiment!r}, config is {config.version!r}"
        )
    if binding.config_sha256 and binding.config_sha256 != config.config_sha256:
        raise BindingError(
            "binding was built under a different config file:\n"
            f"  binding ran under {binding.config_sha256[:16]}…\n"
            f"  loaded config is  {config.config_sha256[:16]}…"
        )

    # Every destination is checked before a single fit starts. Discovering the
    # collision on run 17 of 20 would already have overwritten sixteen.
    planned = [
        run_paths(
            run_label,
            MODEL_NAME,
            split.name,
            seed,
            run_root=run_root,
            prediction_root=prediction_dir,
        )
        for split in config.splits
        if selection.chosen.get(split.name) is not None
        for seed in seeds
    ]
    preflight(planned, overwrite=overwrite)

    results: list[dict[str, Any]] = []

    for split in config.splits:
        dim = selection.chosen.get(split.name)
        if dim is None:
            continue
        train = build_cohort(conn, config, split.name, "train")
        validation = build_cohort(conn, config, split.name, "validation")
        test_regression = build_cohort(conn, config, split.name, "test")
        test_ranking = ranking_cohort(conn, config, split.name, "test")

        for seed in seeds:
            if progress:
                print(f"    {split.name} h={dim} seed={seed}", flush=True)  # noqa: T201
            settings = effective_settings(config, dim, seed)
            model, training = fit(bank, train, validation, settings, split.name)

            started = time.perf_counter()
            predicted_regression = predict(model, bank, test_regression)
            predicted_ranking = predict(model, bank, test_ranking)
            predict_seconds = time.perf_counter() - started

            destination = run_paths(
                run_label,
                MODEL_NAME,
                split.name,
                seed,
                run_root=run_root,
                prediction_root=prediction_dir,
            )
            destination.checkpoint.parent.mkdir(parents=True, exist_ok=True)
            destination.prediction.parent.mkdir(parents=True, exist_ok=True)
            checkpoint = save_checkpoint(
                model,
                destination.checkpoint,
                extra={
                    "experiment": config.version,
                    "config_digest": config.config_sha256,
                    "split": split.name,
                    "seed": seed,
                    "projection_dim": dim,
                    # Bound at save time so inference never has to guess which
                    # caches this model saw.
                    "feature_binding": binding.to_dict() if binding is not None else None,
                },
            )
            write_record(training, destination.record)
            np.savez_compressed(
                destination.prediction,
                compound_id=test_ranking.compound_id,
                target_id=test_ranking.target_id,
                label=np.array([str(x) for x in test_ranking.label]),
                prediction=predicted_ranking,
                regression_compound_id=test_regression.compound_id,
                regression_target_id=test_regression.target_id,
                regression_y=test_regression.y,
                regression_prediction=predicted_regression,
            )

            entry: dict[str, Any] = {
                "model": MODEL_NAME,
                "split": split.name,
                "seed": seed,
                "deterministic": False,
                "projection_dim": dim,
                "n_parameters": training.n_parameters,
                "fit_seconds": training.seconds,
                "predict_seconds": predict_seconds,
                "n_train": len(train),
                "checkpoint": str(checkpoint),
                "fit_notes": {
                    "best_epoch": training.best_epoch,
                    "best_validation_rmse": training.best_validation_rmse,
                    "epochs_run": training.epochs_run,
                    "stopped_early": training.stopped_early,
                    "selected_on": "validation RMSE, frozen before test scoring",
                    "head": "affine on cosine, output in pKi units",
                    "device": training.device,
                },
                "ranking": evaluate_ranking(test_ranking, predicted_ranking),
                "regression": evaluate_regression(test_regression, predicted_regression),
            }
            entry["ranking"].pop("per_target", None)
            entry["regression"].pop("per_target", None)
            entry["strata"] = _strata(
                conn,
                config,
                split,
                test_ranking,
                predicted_ranking,
                test_regression,
                predicted_regression,
            )
            results.append(entry)
            del model
    return results


def _strata(
    conn, config, split, ranking, predicted_ranking, regression, predicted_regression
) -> dict[str, Any]:
    """The breakouts the contract requires, computed exactly as M8 does."""
    from seq2lead.eval.recompute import _slice

    out: dict[str, Any] = {}
    if split.partition_endpoints:
        for name in ("new", "recurrent"):
            mask = np.array([s == name for s in ranking.stratum])
            if mask.any():
                out[f"temporal:{name}"] = _slice(ranking, predicted_ranking, mask)
            reg_mask = np.array([s == name for s in regression.stratum])
            if reg_mask.any():
                out[f"temporal_regression:{name}"] = _slice(
                    regression, predicted_regression, reg_mask, ranking=False
                )
    near = {
        int(r[0])
        for r in conn.execute(
            "SELECT target_id FROM split_target_stratum WHERE split_id=%s AND stratum=%s",
            (split.id, "near_homolog"),
        ).fetchall()
    }
    if near:
        mask = np.isin(ranking.target_id, list(near))
        if mask.any():
            out["near_homolog"] = _slice(ranking, predicted_ranking, mask)
            out["not_near_homolog"] = _slice(ranking, predicted_ranking, ~mask)
    long_targets = {
        int(r[0])
        for r in conn.execute(
            "SELECT entity_id FROM feature_entity_flag f "
            "JOIN feature_version v ON v.id = f.feature_id "
            "WHERE v.name=%s AND f.flag='over_training_window'",
            (config.caches["esm2"].name,),
        ).fetchall()
    }
    if long_targets:
        mask = np.isin(ranking.target_id, list(long_targets))
        if mask.any():
            out["over_training_window"] = _slice(ranking, predicted_ranking, mask)
            out["within_training_window"] = _slice(ranking, predicted_ranking, ~mask)
    return out
