"""Recompute corrected metrics from saved predictions, without refitting.

The M8 v1 average precision was not threshold-block AP: it averaged precision
over orderings *within* a tied block instead of admitting the block whole. That
is a different quantity, not a bounded approximation, and **its error runs in
both directions** -- measured on one real split it disagreed with
`sklearn.metrics.average_precision_score` on 245 of 328 scored targets, by up to
0.057 either way. Fixing it changes AP, and nothing else: AUROC, the regression
metrics and the predictions themselves are untouched by the definition.

So this recomputes rather than retrains. Predictions are read back from
`data/predictions/`, every metric is recomputed under the corrected definitions,
and the result is written as a new version beside the old one. The v1 numbers are
kept: a corrected figure is only checkable against the figure it replaced.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np

from seq2lead.eval.runner import PREDICTION_DIR, RESULT_DIR, evaluate_ranking, evaluate_regression

if TYPE_CHECKING:
    import psycopg

    from seq2lead.eval.config import ExperimentConfig

#: Bumped whenever a metric definition or a reporting rule changes.
METRIC_VERSION = "m8/v2"

CHANGES = (
    "average_precision: threshold-block AP. Equal scores are admitted as one "
    "block before precision is read, matching sklearn.metrics.average_precision_"
    "score. The superseded version averaged precision over orderings inside a "
    "tied block, which is a different quantity and errs in BOTH directions -- "
    "measured on one real split it disagreed with sklearn on 245 of 328 scored "
    "targets, by up to 0.057 in either direction.",
    "temporal reporting: `new` is the headline stratum for temporal_proxy-v4; "
    "`recurrent` is reported separately; the combined figure is removed from "
    "headline comparisons.",
    "temporal regression: `new` and `recurrent` breakouts added. These are "
    "additional results computed from the same saved predictions, not changes "
    "to any fitted value.",
    "near-homolog reporting: seed-averaged per model with scored-target counts, "
    "replacing a selection of the single largest gap across runs.",
)


@dataclass
class ComparisonRow:
    model: str
    split: str
    seed: str
    metric: str
    old: float | None
    new: float | None

    @property
    def changed(self) -> bool:
        if self.old is None or self.new is None:
            return self.old is not self.new
        return not np.isclose(self.old, self.new, rtol=0, atol=5e-7)


@dataclass
class RecomputeReport:
    metric_version: str
    experiment_version: str
    n_runs: int
    source_version: str = ""
    source_sha256: str = ""
    source_metric_version: str = ""
    manifest_version: str = ""
    manifest_sha256: str = ""
    manifest_provenance: str = ""
    verification_checks: tuple[str, ...] = ()
    prediction_digests: dict[str, str] = field(default_factory=dict)
    source_digests: dict[str, str] = field(default_factory=dict)
    changes: tuple[str, ...] = CHANGES
    comparisons: list[ComparisonRow] = field(default_factory=list)

    def changed(self) -> list[ComparisonRow]:
        return [c for c in self.comparisons if c.changed]

    def unchanged_metrics(self) -> set[str]:
        by_metric: dict[str, bool] = {}
        for row in self.comparisons:
            by_metric[row.metric] = by_metric.get(row.metric, False) or row.changed
        return {m for m, changed in by_metric.items() if not changed}


def _stratum_maps(conn: psycopg.Connection, config: ExperimentConfig) -> dict[str, dict]:
    """Per-split lookups for every stratum the contract requires."""
    maps: dict[str, dict] = {}
    for split in config.splits:
        entry: dict[str, Any] = {}
        rows = conn.execute(
            "SELECT compound_id, target_id, stratum FROM split_pair_assignment "
            "WHERE split_id=%s AND partition='test'",
            (split.id,),
        ).fetchall()
        entry["pair_stratum"] = {(int(a), int(b)): str(c) for a, b, c in rows}
        entry["near_homolog"] = {
            int(r[0])
            for r in conn.execute(
                "SELECT target_id FROM split_target_stratum "
                "WHERE split_id=%s AND stratum='near_homolog'",
                (split.id,),
            ).fetchall()
        }
        maps[split.name] = entry
    maps["_over_window"] = {
        int(r[0])
        for r in conn.execute(
            "SELECT entity_id FROM feature_entity_flag f "
            "JOIN feature_version v ON v.id = f.feature_id "
            "WHERE v.name=%s AND f.flag='over_training_window'",
            (config.caches["esm2"].name,),
        ).fetchall()
    }
    return maps


def _cohort_from(compound_id, target_id, label, stratum, split, y=None):
    from seq2lead.eval.cohort import Cohort

    return Cohort(
        split=split,
        partition="test",
        endpoint_id=0,
        compound_id=compound_id,
        target_id=target_id,
        y=y if y is not None else np.zeros(len(compound_id)),
        label=label,
        stratum=stratum,
    )


def _slice(cohort, predictions, mask, ranking: bool = True) -> dict[str, Any]:
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
    scored = (
        evaluate_ranking(subset, predictions[mask])
        if ranking
        else evaluate_regression(subset, predictions[mask])
    )
    scored.pop("per_target", None)
    scored["n_pairs"] = int(mask.sum())
    scored["n_targets"] = int(np.unique(subset.target_id).shape[0])
    return scored


def recompute_all(
    conn: psycopg.Connection,
    config: ExperimentConfig,
    source_version: str,
    prediction_dir: Path = PREDICTION_DIR,
    result_dir: Path = RESULT_DIR,
    manifest_path: Path | None = None,
) -> tuple[list[dict[str, Any]], RecomputeReport]:
    """Recompute every run's metrics from its **verified** saved predictions.

    Verification runs first and in full. If anything about the artifacts fails to
    match the frozen manifest, nothing is computed and nothing is written, so the
    published results stay exactly as they were.
    """
    from seq2lead.eval.verify import MANIFEST_PATH, load_manifest, verify_predictions
    from seq2lead.features.store import sha256_file

    manifest = load_manifest(manifest_path or MANIFEST_PATH)
    verification = verify_predictions(conn, config, prediction_dir, manifest)

    # The selected source, loaded through the registry so its digest and
    # experiment binding are verified. Reading a hardcoded file here would let a
    # run compare against one version while recording and superseding another.
    from seq2lead.eval.results import load as load_result_version

    source_runs, source_entry = load_result_version(source_version, config, result_dir)
    previous = {(r["model"], r["split"], str(r.get("seed"))): r for r in source_runs}
    if len(previous) != len(source_runs):
        raise ValueError(
            f"source version {source_version!r} contains duplicate (model, split, seed) "
            "keys, so comparisons would be ambiguous"
        )

    # The source must describe the same runs the manifest expects, or the
    # before/after comparison silently pairs the wrong things.
    manifest_keys = {
        (r.model, r.split, "None" if r.deterministic else str(r.seed)) for r in manifest.runs
    }
    missing = sorted(manifest_keys - set(previous))
    extra = sorted(set(previous) - manifest_keys)
    if missing or extra:
        raise ValueError(
            f"source version {source_version!r} does not match the prediction manifest: "
            f"{len(missing)} runs missing (e.g. {missing[:2]}), "
            f"{len(extra)} unexpected (e.g. {extra[:2]})"
        )
    report_source = (source_entry.version, source_entry.sha256, source_entry.metric_version)
    maps = _stratum_maps(conn, config)
    over_window = maps["_over_window"]

    corrected: list[dict[str, Any]] = []
    report = RecomputeReport(
        metric_version=METRIC_VERSION,
        experiment_version=config.version,
        source_version=report_source[0],
        source_sha256=report_source[1],
        source_metric_version=report_source[2],
        manifest_version=manifest.manifest_version,
        manifest_sha256=manifest.sha256,
        manifest_provenance=manifest.provenance,
        verification_checks=tuple(verification.checks),
        n_runs=0,
        source_digests={kind: ref.storage_sha256 for kind, ref in config.caches.items()},
    )

    # Ordered by filename, which is the order the original recomputation emitted
    # (it globbed the directory). Keeping it means republishing an unchanged
    # version is byte-identical, so the overwrite guard proves nothing moved
    # rather than tripping on a cosmetic reshuffle.
    for run in sorted(manifest.runs, key=lambda r: r.filename):
        path = prediction_dir / run.filename
        model, split = run.model, run.split
        seed = "det" if run.deterministic else str(run.seed)
        report.prediction_digests[run.filename] = sha256_file(path)

        with np.load(path, allow_pickle=False) as data:
            compound_id = data["compound_id"]
            target_id = data["target_id"]
            label = data["label"].astype(object)
            prediction = data["prediction"]
            reg_compound = data["regression_compound_id"]
            reg_target = data["regression_target_id"]
            reg_y = data["regression_y"]
            reg_prediction = data["regression_prediction"]

        pair_stratum = maps.get(split, {}).get("pair_stratum", {})
        stratum = np.array(
            [
                pair_stratum.get((int(c), int(t)), "")
                for c, t in zip(compound_id, target_id, strict=True)
            ],
            dtype=object,
        )
        ranking_cohort = _cohort_from(compound_id, target_id, label, stratum, split)
        regression_stratum = np.array(
            [
                pair_stratum.get((int(c), int(t)), "")
                for c, t in zip(reg_compound, reg_target, strict=True)
            ],
            dtype=object,
        )
        regression_cohort = _cohort_from(
            reg_compound,
            reg_target,
            np.array([""] * len(reg_target), dtype=object),
            regression_stratum,
            split,
            y=reg_y,
        )

        entry: dict[str, Any] = {
            "model": model,
            "split": split,
            "seed": None if seed == "det" else int(seed),
            "deterministic": seed == "det",
            "metric_version": METRIC_VERSION,
            "prediction_file": path.name,
            "prediction_sha256": report.prediction_digests[path.name],
            "ranking": evaluate_ranking(ranking_cohort, prediction),
            "regression": evaluate_regression(regression_cohort, reg_prediction),
        }
        entry["ranking"].pop("per_target", None)
        entry["regression"].pop("per_target", None)

        strata: dict[str, Any] = {}
        is_temporal = bool(config.split(split).partition_endpoints)
        if is_temporal:
            for name in ("new", "recurrent"):
                mask = stratum == name
                if mask.any():
                    strata[f"temporal:{name}"] = _slice(ranking_cohort, prediction, mask)
                reg_mask = regression_stratum == name
                if reg_mask.any():
                    strata[f"temporal_regression:{name}"] = _slice(
                        regression_cohort, reg_prediction, reg_mask, ranking=False
                    )
        near = maps.get(split, {}).get("near_homolog", set())
        if near:
            mask = np.isin(target_id, list(near))
            if mask.any():
                strata["near_homolog"] = _slice(ranking_cohort, prediction, mask)
                strata["not_near_homolog"] = _slice(ranking_cohort, prediction, ~mask)
        if over_window:
            mask = np.isin(target_id, list(over_window))
            if mask.any():
                strata["over_training_window"] = _slice(ranking_cohort, prediction, mask)
                strata["within_training_window"] = _slice(ranking_cohort, prediction, ~mask)
        entry["strata"] = strata

        old = previous.get((model, split, str(entry["seed"])))
        if old is not None:
            entry["fit_seconds"] = old.get("fit_seconds")
            entry["predict_seconds"] = old.get("predict_seconds")
            entry["n_train"] = old.get("n_train")
            entry["fit_notes"] = old.get("fit_notes")
            for metric, old_path, new_value in (
                ("ranking.auroc", ("ranking", "auroc", "mean"), entry["ranking"]["auroc"]["mean"]),
                (
                    "ranking.average_precision",
                    ("ranking", "average_precision", "mean"),
                    entry["ranking"]["average_precision"]["mean"],
                ),
                (
                    "regression.rmse",
                    ("regression", "rmse", "mean"),
                    entry["regression"]["rmse"]["mean"],
                ),
                (
                    "regression.mae",
                    ("regression", "mae", "mean"),
                    entry["regression"]["mae"]["mean"],
                ),
                (
                    "regression.spearman",
                    ("regression", "spearman", "mean"),
                    entry["regression"]["spearman"]["mean"],
                ),
            ):
                node: Any = old
                for key in old_path:
                    node = node.get(key) if isinstance(node, dict) else None
                    if node is None:
                        break
                report.comparisons.append(
                    ComparisonRow(model, split, str(entry["seed"]), metric, node, new_value)
                )
        corrected.append(entry)

    report.n_runs = len(corrected)
    return corrected, report


def write(
    corrected: list[dict[str, Any]],
    report: RecomputeReport,
    config,
    *,
    version: str,
    result_dir: Path = RESULT_DIR,
) -> tuple[Path, Path]:
    """Publish the corrected results under a named version, and the change record.

    The source version is left untouched: a corrected figure is only checkable
    against the one it replaced.
    """
    from seq2lead.eval.results import publish, supersede

    entry = publish(
        corrected,
        version=version,
        filename=f"{version.replace('/', '_')}_summary.json",
        metric_version=report.metric_version,
        config=config,
        directory=result_dir,
        source_version=report.source_version,
        note="recomputed from verified predictions; no model refitted",
        extra={
            "manifest_version": report.manifest_version,
            "manifest_sha256": report.manifest_sha256,
            "source_sha256": report.source_sha256,
        },
    )
    # Supersede only the source that was actually loaded, and only now that the
    # destination is published: marking it earlier would leave the source retired
    # behind a publication that never happened.
    if report.source_version and report.source_version != version:
        supersede(report.source_version, version, result_dir)

    record = asdict(report)
    record["comparisons"] = [{**asdict(c), "changed": c.changed} for c in report.comparisons]
    record["published_as"] = vars(entry)
    record_path = result_dir / f"correction_{version.replace('/', '_')}.json"
    record_path.write_text(
        json.dumps(record, indent=1, sort_keys=True, default=str) + "\n", encoding="utf-8"
    )
    return result_dir / entry.filename, record_path
