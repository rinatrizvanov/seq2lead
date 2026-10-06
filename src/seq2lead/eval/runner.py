"""Fit, score, and persist enough that every leaderboard number can be recomputed.

Per-pair predictions and per-target metrics are written to disk. A leaderboard
figure nobody can recompute is a claim, not a result.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np

from seq2lead.eval import metrics as M
from seq2lead.eval.cohort import build_cohort, ranking_cohort

if TYPE_CHECKING:
    import psycopg

    from seq2lead.eval.config import ExperimentConfig
    from seq2lead.eval.features import FeatureBank

PREDICTION_DIR = Path("data/predictions")
RESULT_DIR = Path("reports/results")


@dataclass
class RunResult:
    model: str
    split: str
    seed: int | None
    deterministic: bool
    fit_seconds: float
    predict_seconds: float
    n_train: int
    n_test_regression: int
    n_test_ranking: int
    regression: dict[str, Any] = field(default_factory=dict)
    ranking: dict[str, Any] = field(default_factory=dict)
    strata: dict[str, Any] = field(default_factory=dict)
    fit_notes: dict[str, Any] = field(default_factory=dict)
    prediction_path: str | None = None


def _macro(values: list[float | None]) -> dict[str, Any]:
    usable = [v for v in values if v is not None and np.isfinite(v)]
    if not usable:
        return {"mean": None, "n_targets": 0}
    return {
        "mean": float(np.mean(usable)),
        "median": float(np.median(usable)),
        "n_targets": len(usable),
    }


def evaluate_regression(cohort, predictions: np.ndarray) -> dict[str, Any]:
    """Per target, then macro-averaged. Never pooled across targets."""
    per_target: list[M.TargetRegression] = []
    for target in np.unique(cohort.target_id):
        mask = cohort.target_id == target
        per_target.append(M.score_target_regression(int(target), cohort.y[mask], predictions[mask]))
    undefined = [r for r in per_target if r.spearman is None]
    return {
        "mae": _macro([r.mae for r in per_target]),
        "rmse": _macro([r.rmse for r in per_target]),
        "spearman": _macro([r.spearman for r in per_target]),
        "concordance_index": _macro([r.concordance_index for r in per_target]),
        "n_targets": len(per_target),
        "n_targets_without_rank_metrics": len(undefined),
        "per_target": [asdict(r) for r in per_target],
    }


def evaluate_ranking(
    cohort, predictions: np.ndarray, threshold_label: str = "active"
) -> dict[str, Any]:
    """Per target over its eligible measured compounds. Unmeasured pairs never appear."""
    per_target: list[M.TargetRanking] = []
    for target in np.unique(cohort.target_id):
        mask = cohort.target_id == target
        positive = np.array([lab == threshold_label for lab in cohort.label[mask]])
        per_target.append(M.score_target_ranking(int(target), predictions[mask], positive))
    scored = [r for r in per_target if r.auroc is not None]
    skipped = [r for r in per_target if r.auroc is None]
    return {
        "auroc": _macro([r.auroc for r in per_target]),
        "average_precision": _macro([r.average_precision for r in per_target]),
        "prevalence": _macro([r.prevalence for r in scored]),
        "recall_at_10": _macro([r.recall_at.get(10) for r in scored]),
        "recall_at_50": _macro([r.recall_at.get(50) for r in scored]),
        "enrichment_at_1pct": _macro([r.enrichment_at.get(0.01) for r in scored]),
        "enrichment_at_5pct": _macro([r.enrichment_at.get(0.05) for r in scored]),
        "n_targets_scored": len(scored),
        "n_targets_skipped": len(skipped),
        "n_pairs_skipped": int(sum(r.n for r in skipped)),
        "n_targets_all_tied": int(sum(1 for r in scored if r.all_tied)),
        "skip_reasons": _reason_counts(skipped),
        "per_target": [asdict(r) for r in per_target],
    }


def _reason_counts(rows) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        key = (row.undefined_reason or "unspecified").split(";")[0]
        counts[key] = counts.get(key, 0) + 1
    return counts


def run_one(
    conn: psycopg.Connection,
    config: ExperimentConfig,
    bank: FeatureBank,
    model_cls,
    split_name: str,
    seed: int,
    prediction_dir: Path = PREDICTION_DIR,
) -> RunResult:
    """One model, one split, one seed: fit on train, select on validation, score test."""
    train = build_cohort(conn, config, split_name, "train")
    validation = build_cohort(conn, config, split_name, "validation")
    test_regression = build_cohort(conn, config, split_name, "test")
    test_ranking = ranking_cohort(conn, config, split_name, "test")

    model = model_cls()
    report = model.fit(bank, train, validation, seed)

    started = time.perf_counter()
    predicted_regression = model.predict(bank, test_regression)
    predicted_ranking = model.predict(bank, test_ranking)
    predict_seconds = time.perf_counter() - started

    result = RunResult(
        model=model.name,
        split=split_name,
        seed=None if model.deterministic else seed,
        deterministic=model.deterministic,
        fit_seconds=report.seconds,
        predict_seconds=predict_seconds,
        n_train=len(train),
        n_test_regression=len(test_regression),
        n_test_ranking=len(test_ranking),
        regression=evaluate_regression(test_regression, predicted_regression),
        ranking=evaluate_ranking(test_ranking, predicted_ranking),
        fit_notes=report.notes,
    )
    result.strata = _strata(conn, config, split_name, test_ranking, predicted_ranking)

    prediction_dir.mkdir(parents=True, exist_ok=True)
    tag = f"{model.name}__{split_name}__seed{seed if not model.deterministic else 'det'}"
    path = prediction_dir / f"{tag}.npz"
    np.savez_compressed(
        path,
        compound_id=test_ranking.compound_id,
        target_id=test_ranking.target_id,
        label=np.array([str(x) for x in test_ranking.label]),
        prediction=predicted_ranking,
        regression_compound_id=test_regression.compound_id,
        regression_target_id=test_regression.target_id,
        regression_y=test_regression.y,
        regression_prediction=predicted_regression,
    )
    result.prediction_path = str(path)
    return result


def _strata(conn, config, split_name, cohort, predictions) -> dict[str, Any]:
    """Break-outs the contract requires: temporal, near-homolog, long sequence."""
    out: dict[str, Any] = {}
    split = config.split(split_name)

    if split.partition_endpoints:  # temporal
        for name in ("new", "recurrent"):
            mask = np.array([s == name for s in cohort.stratum])
            if mask.any():
                out[f"temporal:{name}"] = _subset(cohort, predictions, mask)

    near = {
        int(r[0])
        for r in conn.execute(
            "SELECT target_id FROM split_target_stratum WHERE split_id=%s AND stratum=%s",
            (split.id, "near_homolog"),
        ).fetchall()
    }
    if near:
        mask = np.isin(cohort.target_id, list(near))
        if mask.any():
            out["near_homolog"] = _subset(cohort, predictions, mask)
            out["not_near_homolog"] = _subset(cohort, predictions, ~mask)

    long_targets = {
        int(r[0])
        for r in conn.execute(
            "SELECT entity_id FROM feature_entity_flag f "
            "JOIN feature_version v ON v.id = f.feature_id "
            "WHERE v.name = %s AND f.flag = 'over_training_window'",
            (config.caches["esm2"].name,),
        ).fetchall()
    }
    if long_targets:
        mask = np.isin(cohort.target_id, list(long_targets))
        if mask.any():
            out["over_training_window"] = _subset(cohort, predictions, mask)
            out["within_training_window"] = _subset(cohort, predictions, ~mask)
    return out


def _subset(cohort, predictions, mask) -> dict[str, Any]:
    from seq2lead.eval.cohort import Cohort

    subset = Cohort(
        split=cohort.split,
        partition=cohort.partition,
        endpoint_id=cohort.endpoint_id,
        compound_id=cohort.compound_id[mask],
        target_id=cohort.target_id[mask],
        y=cohort.y[mask],
        label=cohort.label[mask],
        stratum=cohort.stratum[mask],
    )
    scored = evaluate_ranking(subset, predictions[mask])
    scored.pop("per_target", None)
    scored["n_pairs"] = int(mask.sum())
    scored["n_targets"] = int(np.unique(subset.target_id).shape[0])
    return scored


def write_results(
    results: list[RunResult],
    config,
    *,
    version: str,
    metric_version: str,
    directory: Path = RESULT_DIR,
) -> tuple[Path, Path]:
    """Publish a fitting run under an explicit, registered version.

    The full per-target record and the summary are committed together, after
    every publication check has passed. Writing the full record first -- as an
    earlier version did -- meant a refused summary still left a new file on disk
    beside an index that knew nothing about it.
    """
    from seq2lead.eval.results import publish

    directory.mkdir(parents=True, exist_ok=True)
    payload = [asdict(r) for r in results]
    full_name = f"{version.replace('/', '_')}_runs.json"
    full_body = json.dumps(payload, indent=1, sort_keys=True, default=str) + "\n"

    summary = []
    for entry in payload:
        trimmed = dict(entry)
        for section in ("regression", "ranking"):
            block = dict(trimmed.get(section) or {})
            block.pop("per_target", None)
            trimmed[section] = block
        summary.append(trimmed)

    published = publish(
        summary,
        version=version,
        filename=f"{version.replace('/', '_')}_summary.json",
        metric_version=metric_version,
        config=config,
        directory=directory,
        note="fitted run",
        extra={"full_record": full_name},
        staged={full_name: full_body},
    )
    return directory / full_name, directory / published.filename
