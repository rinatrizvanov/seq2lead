"""Recompute an as-of run's results from its saved predictions. Fits nothing.

The scoring that produced the published numbers lived in a session scratchpad
script. That is the wrong home for code a published result depends on, so the
logic lives here, as a supported module with an entry point, and the scripts as
run are preserved under `scripts/asof/` with their digests.

Three things this refuses, each because the alternative is a silently wrong
report rather than an error:

* **tampered predictions** -- the manifest records the prediction file's digest,
  so a changed array is a refusal, not a new result;
* **a missing model array** -- a model the manifest claims was predicted must be
  present, or the report would quietly describe a smaller leaderboard;
* **misaligned pairs** -- the prediction rows and the evaluation table are
  positionally joined, so a reordered or resized table would attach every score
  to the wrong pair while still producing plausible-looking metrics.

Nothing here fits, loads a checkpoint, or touches a feature cache.
"""

from __future__ import annotations

import hashlib
import json
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from seq2lead.eval.metrics import MIN_NEGATIVES, MIN_POSITIVES, score_target_ranking

__all__ = [
    "DERIVED_OUTPUTS",
    "MIN_NEGATIVES",
    "MIN_POSITIVES",
    "NEW_TO_FITTING",
    "PRIMARY_CELL",
    "SCORED_INPUTS",
    "RecomputeError",
    "current_digests",
    "score_run",
    "sha256",
    "verify_and_load",
]

#: Bumped when the scoring, the slicing or the recorded fields change.
RECOMPUTE_VERSION = "m11h/recompute/v1"

#: Metric implementation this reuses, which is M8's and M9's.
METRIC_VERSION = "m8/v2"

ARMS = ("declared_increment", "cross_slot_excluded")
BRANCHES = ("screened_primary", "unscreened_sensitivity")
PRIMARY_CELL = ("declared_increment", "screened_primary")
NEW_TO_FITTING = (
    "new_absent_from_a",
    "new_reserved_for_validation",
    "new_a_present_excluded_from_fitting",
)
STRICTEST = "new_absent_from_a"
RECALL_KS = (10, 50)
ENRICHMENT_FRACTIONS = (0.01, 0.05)

#: Models that are fitted once because the declared protocol treats them as
#: deterministic. Their prediction arrays are still only one fit each, so no
#: claim about seed behaviour follows from them.
SINGLE_FIT = frozenset({"B0-target-mean", "B3L-ligand-1nn"})


class RecomputeError(RuntimeError):
    """The saved artifacts are not a run that can be recomputed."""


def sha256(path: str | Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 22), b""):
            h.update(chunk)
    return h.hexdigest()


@dataclass(frozen=True)
class LoadedRun:
    """A verified run: predictions, the pair table, and the manifest."""

    root: Path
    manifest: dict[str, Any]
    pairs: list[str]
    table: list[dict[str, Any]]
    scores: dict[str, np.ndarray]

    @property
    def tags(self) -> list[str]:
        return sorted(self.scores)


def verify_and_load(run_dir: str | Path, *, expect_digest: bool = True) -> LoadedRun:
    """Verify the saved artifacts, then load them. Refuses rather than repairing."""
    root = Path(run_dir)
    manifest_path = root / "manifest.json"
    if not manifest_path.exists():
        msg = f"{root} has no manifest.json, so there is nothing to verify against"
        raise RecomputeError(msg)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    predictions = Path(manifest["predictions"]["path"])
    if not predictions.exists():
        msg = f"the manifest names predictions at {predictions}, which does not exist"
        raise RecomputeError(msg)
    if expect_digest:
        actual = sha256(predictions)
        if actual != manifest["predictions"]["sha256"]:
            msg = (
                f"the saved predictions have changed: {actual[:16]}… is not the "
                f"recorded {manifest['predictions']['sha256'][:16]}…. Refusing to "
                "recompute a report from predictions the manifest does not describe."
            )
            raise RecomputeError(msg)

    blob = np.load(predictions, allow_pickle=True)
    if "pair" not in blob.files:
        msg = "the prediction archive has no `pair` array, so no alignment can be checked"
        raise RecomputeError(msg)
    pairs = [str(p) for p in blob["pair"]]
    scores = {k: np.asarray(blob[k], dtype=np.float64) for k in blob.files if k != "pair"}

    expected = set(manifest["predictions"]["models"])
    missing = sorted(expected - scores.keys())
    if missing:
        msg = (
            f"the manifest claims predictions for {len(expected)} models and "
            f"{len(missing)} are absent from the archive: {missing[:5]}. Refusing to "
            "report a smaller leaderboard than the run recorded."
        )
        raise RecomputeError(msg)
    extra = sorted(scores.keys() - expected)
    if extra:
        msg = f"the archive holds model arrays the manifest does not name: {extra[:5]}"
        raise RecomputeError(msg)

    # The table the run CONSUMES, not the one it was built from. An earlier
    # revision checked only that the pair identities and their order matched,
    # which a tampered table satisfies: flipping 400 labels while preserving
    # every pair and its position moved a published AUROC by 0.027 and was
    # accepted. Labels, strata, eligibility and branch membership all live in
    # this file, so its digest is what covers them.
    table_path = Path(manifest["evaluation_table"]["path"])
    if not table_path.exists():
        table_path = root / "evaluation-pairs.jsonl"
    if not table_path.exists():
        msg = f"{table_path} is missing, so the predictions cannot be attached to pairs"
        raise RecomputeError(msg)
    if expect_digest:
        actual_table = sha256(table_path)
        recorded = manifest["evaluation_table"]["sha256"]
        if actual_table != recorded:
            msg = (
                f"the evaluation table has changed: {actual_table[:16]}\u2026 is not the "
                f"recorded {recorded[:16]}\u2026. Pair identities and order alone do not "
                "establish that the labels, strata, eligibility or branch membership are "
                "the ones the run was scored against. Refusing to recompute."
            )
            raise RecomputeError(msg)
    table = [json.loads(line) for line in table_path.open(encoding="utf-8") if line.strip()]
    check_alignment(pairs, table, scores)
    return LoadedRun(root=root, manifest=manifest, pairs=pairs, table=table, scores=scores)


#: Inputs a run is scored from. Their expected digests come from the fit's own
#: manifest and are never recomputed by the publisher: a publisher that refreshes
#: the value it is meant to check launders whatever changed.
SCORED_INPUTS = ("predictions", "evaluation_table")

#: Derived outputs, regenerated on every recomputation.
DERIVED_OUTPUTS = ("results.json", "verification.json")


def current_digests(run: LoadedRun) -> dict[str, str]:
    """What a verification record must be bound to, computed now.

    `results.json` is included because a verification that did not see the
    published results cannot vouch for them, which is how a stale PASSED record
    came to authorise a report built from altered inputs.
    """
    out = {
        "predictions_sha256": sha256(run.manifest["predictions"]["path"]),
        "evaluation_table_sha256": sha256(run.manifest["evaluation_table"]["path"]),
        "run_manifest_sha256": sha256(run.root / "manifest.json"),
    }
    results = run.root / "results.json"
    out["results_sha256"] = sha256(results) if results.exists() else None
    return out


def check_alignment(
    pairs: list[str], table: list[dict[str, Any]], scores: dict[str, np.ndarray]
) -> None:
    """The join is positional, so every row must line up exactly.

    A reordered table does not fail loudly on its own: the metrics would still
    compute, on scores attached to the wrong pairs. So position, length and
    identity are all checked rather than assumed from the row count.
    """
    if len(table) != len(pairs):
        msg = (
            f"the evaluation table has {len(table):,} rows and the predictions have "
            f"{len(pairs):,}; the join is positional, so this cannot be reconciled"
        )
        raise RecomputeError(msg)
    table_pairs = [r["pair"] for r in table]
    if table_pairs != pairs:
        first = next(
            (i for i, (a, b) in enumerate(zip(pairs, table_pairs, strict=True)) if a != b),
            None,
        )
        msg = (
            "the prediction rows and the evaluation table are not in the same order; "
            f"first mismatch at row {first}: predictions have {pairs[first]!r}, the "
            f"table has {table_pairs[first]!r}. Scoring would attach every score from "
            "there on to the wrong pair."
        )
        raise RecomputeError(msg)
    for tag, array in sorted(scores.items()):
        if array.shape != (len(pairs),):
            msg = f"{tag}: prediction array has shape {array.shape}, expected ({len(pairs)},)"
            raise RecomputeError(msg)
        if not np.all(np.isfinite(array)):
            msg = f"{tag}: prediction array holds non-finite values"
            raise RecomputeError(msg)


def _macro(values: list[float | None]) -> float | None:
    present = [v for v in values if v is not None]
    return float(np.mean(present)) if present else None


def score_slice(
    rows: list[int], pairs: list[str], labels: list[str], scores: np.ndarray
) -> dict[str, Any]:
    """Per-target ranking metrics, macro-averaged, with exclusions counted."""
    by_target: dict[str, list[int]] = defaultdict(list)
    for row in rows:
        by_target[pairs[row].split("|", 1)[1]].append(row)

    scored, excluded = [], Counter()
    for target, index in sorted(by_target.items()):
        positive = np.asarray([labels[i] == "active" for i in index], dtype=bool)
        result = score_target_ranking(
            target_id=0,
            scores=scores[index],
            positive=positive,
            recall_ks=RECALL_KS,
            enrichment_fractions=ENRICHMENT_FRACTIONS,
        )
        if result.undefined_reason is not None:
            excluded["below_the_per_class_floor"] += 1
            continue
        scored.append((target, result))

    n_pos = sum(1 for r in rows if labels[r] == "active")
    out: dict[str, Any] = {
        "pairs": len(rows),
        "targets_in_slice": len(by_target),
        "targets_scored": len(scored),
        "targets_excluded": dict(excluded),
        "scoring_floor": f">={MIN_POSITIVES} actives and >={MIN_NEGATIVES} inactives per target",
        "prevalence_over_pairs": round(n_pos / len(rows), 6) if rows else None,
        "prevalence_over_scored_targets": (
            round(float(np.mean([r.prevalence for _t, r in scored])), 6) if scored else None
        ),
        "all_tied_targets": sum(1 for _t, r in scored if r.all_tied),
    }
    if not scored:
        out["metrics"] = None
        return out
    out["metrics"] = {
        "auroc": _macro([r.auroc for _t, r in scored]),
        "auprc": _macro([r.average_precision for _t, r in scored]),
        **{f"recall_at_{k}": _macro([r.recall_at.get(k) for _t, r in scored]) for k in RECALL_KS},
        **{
            f"ef_at_{int(f * 100)}pct": _macro([r.enrichment_at.get(f) for _t, r in scored])
            for f in ENRICHMENT_FRACTIONS
        },
    }
    return out


def families(tags: list[str]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = defaultdict(list)
    for tag in tags:
        out[tag.split("-seed")[0]].append(tag)
    return {k: sorted(v) for k, v in sorted(out.items())}


def family_mean(group: dict, tags: list[str], family: str, key: str = "auroc"):
    values = [
        group["by_model"][t]["metrics"][key]
        for t in tags
        if t.split("-seed")[0] == family and group["by_model"][t]["metrics"]
    ]
    return statistics.fmean(values) if values else None


def prediction_differences(run: LoadedRun) -> dict[str, Any]:
    """Measure whether a family's seeds actually produced the same predictions.

    An earlier revision inferred "identical fits" from identical ranking metrics.
    That does not follow: a model whose score is constant within a target has a
    tied ranking whatever the values are, so its metrics are pinned by
    construction while its predictions move freely. This measures the arrays.
    """
    out: dict[str, Any] = {}
    for family, members in families(run.tags).items():
        if len(members) < 2:
            out[family] = {
                "seeds": len(members),
                "comparable": False,
                "why": "fitted once under the declared protocol, so there is nothing to compare",
            }
            continue
        base = run.scores[members[0]]
        deltas = [np.abs(run.scores[t] - base) for t in members[1:]]
        stacked = np.stack([run.scores[t] for t in members])
        out[family] = {
            "seeds": len(members),
            "comparable": True,
            "bit_identical_predictions": all(
                bool(np.array_equal(run.scores[t], base)) for t in members[1:]
            ),
            "max_abs_delta_pki": float(max(float(d.max()) for d in deltas)),
            "mean_abs_delta_pki": float(max(float(d.mean()) for d in deltas)),
            "rows_differing_from_the_first_seed": int(max(int((d > 0).sum()) for d in deltas)),
            "rows": int(base.shape[0]),
            "per_row_sd_max": float(stacked.std(axis=0).max()),
            "constant_within_target": bool(
                all(
                    np.all(run.scores[members[0]][idx] == run.scores[members[0]][idx][0])
                    for idx in _targets(run).values()
                )
            ),
        }
    return out


def _targets(run: LoadedRun) -> dict[str, list[int]]:
    by_target: dict[str, list[int]] = defaultdict(list)
    for i, pair in enumerate(run.pairs):
        by_target[pair.split("|", 1)[1]].append(i)
    return by_target


def score_run(run: LoadedRun) -> dict[str, Any]:
    """Every cell, every stratum, from the saved predictions alone."""
    tags = run.tags
    strata = [r["stratum"] for r in run.table]
    selection_flag = [bool(r["participated_in_model_selection"]) for r in run.table]

    results: dict[str, Any] = {
        "results": "m11h-asof-results-v2",
        "recompute_version": RECOMPUTE_VERSION,
        "metric_version": METRIC_VERSION,
        "recomputed_from": {
            "predictions": str(run.root / "predictions.npz"),
            "predictions_sha256": sha256(run.root / "predictions.npz"),
            "evaluation_table_sha256": sha256(run.root / "evaluation-pairs.jsonl"),
            "note": "read from saved predictions; nothing was refitted to produce this",
            "entry_point": "seq2lead.asof.recompute",
        },
        "primary_cell": {"increment_arm": PRIMARY_CELL[0], "consistency_branch": PRIMARY_CELL[1]},
        "headline_stratum": "new_to_fitting (all three subgroups), never pooled with recurrent",
        "model_tags": tags,
        "population_contrast_note": (
            "Comparisons BETWEEN strata are contrasts between different evaluation "
            "populations, not a single model's performance moving. The strata differ in "
            "size, in target composition and in exposure to fitting, so a difference "
            "between them is not attributable to any one of those causes without "
            "further analysis that was not performed."
        ),
        "cells": {},
    }

    for arm in ARMS:
        for branch in BRANCHES:
            cell = f"{arm}/{branch}"
            labels = [r["arms"][arm]["label"] or "none" for r in run.table]
            eligible = [
                i
                for i, r in enumerate(run.table)
                if r["arms"][arm]["scoreable"] and r["arms"][arm]["branches"][branch]
            ]
            groups = {
                "new_to_fitting": [i for i in eligible if strata[i] in NEW_TO_FITTING],
                "recurrent": [i for i in eligible if strata[i] == "recurrent"],
                STRICTEST: [i for i in eligible if strata[i] == STRICTEST],
                "new_reserved_for_validation": [
                    i for i in eligible if strata[i] == "new_reserved_for_validation"
                ],
            }
            cell_out: dict[str, Any] = {
                "eligible_pairs": len(eligible),
                "is_primary": (arm, branch) == PRIMARY_CELL,
                "groups": {},
            }
            for group, rows in groups.items():
                if not rows:
                    cell_out["groups"][group] = {"pairs": 0, "metrics": None}
                    continue
                cell_out["groups"][group] = {
                    "pairs": len(rows),
                    "participated_in_model_selection": sum(1 for i in rows if selection_flag[i]),
                    "by_model": {
                        tag: score_slice(rows, run.pairs, labels, run.scores[tag]) for tag in tags
                    },
                }
            cell_out["groups"]["recurrent"]["note"] = (
                "A DIFFERENT EVALUATION POPULATION, not a held-out one: these pairs were "
                "supplied to model fitting. Never pooled into the headline."
            )
            if cell_out["groups"].get("new_reserved_for_validation", {}).get("pairs"):
                cell_out["groups"]["new_reserved_for_validation"]["note"] = (
                    "these pairs PARTICIPATED IN MODEL SELECTION as A-validation rows. "
                    "New to fitting is not the same as unseen."
                )
            results["cells"][cell] = cell_out

    primary = results["cells"][f"{PRIMARY_CELL[0]}/{PRIMARY_CELL[1]}"]["groups"]
    spread = {}
    for family, members in families(tags).items():
        values = [
            primary["new_to_fitting"]["by_model"][t]["metrics"]["auroc"]
            for t in members
            if primary["new_to_fitting"]["by_model"][t]["metrics"]
        ]
        spread[family] = {
            "seeds": len(members),
            "fitted_once": family in SINGLE_FIT,
            "auroc_mean": statistics.fmean(values) if values else None,
            "auroc_sd": statistics.stdev(values) if len(values) > 1 else None,
            "auroc_min": min(values) if values else None,
            "auroc_max": max(values) if values else None,
        }
    results["seed_spread_on_the_headline"] = {
        "by_model": spread,
        "interpretation": (
            "Observed variation across seeds. NOT a confidence interval, NOT a test, and "
            "NOT a basis for calling two models equivalent. Full precision is reported "
            "because rounding an SD of 3.6e-07 to six places printed 0.0 and was misread "
            "as the seeds having produced identical fits."
        ),
        "no_paired_uncertainty_analysis": (
            "No paired target-level uncertainty analysis was performed, so no claim about "
            "whether any difference between models is distinguishable from noise is made "
            "or supported here."
        ),
    }
    results["prediction_differences_between_seeds"] = prediction_differences(run)
    results["qualifications"] = [
        "EXPLORATORY. Snapshot B is our already-inspected 202609.",
        "PROVISIONAL POOLING. Whether Ki may be pooled is M5's open question.",
        "The confirmatory freeze is NOT signed.",
        "Retrieval unbuilt; evaluation evidence display off.",
        "label_reversal-v3 is NOT scored.",
        "No tuning was done in response to any number here.",
    ]
    return results


def main(argv: list[str] | None = None) -> int:
    """`python -m seq2lead.asof.recompute <run-dir>` -- writes results.json only.

    Rendering the report is a separate step on purpose. Recomputation, its
    verification and publication used to be one call, and that let a stale
    PASSED record authorise a report. They are now three gates, each of which
    can refuse.
    """
    import argparse

    parser = argparse.ArgumentParser(
        prog="seq2lead-asof-recompute",
        description="Recompute an as-of run's results from its saved predictions.",
    )
    parser.add_argument("run_dir")
    parser.add_argument(
        "--allow-unverified-inputs",
        action="store_true",
        help="skip the prediction and evaluation-table digest checks, for inspecting a "
        "deliberately altered run. The result may not be published.",
    )
    args = parser.parse_args(argv)

    run = verify_and_load(args.run_dir, expect_digest=not args.allow_unverified_inputs)
    results = score_run(run)
    if args.allow_unverified_inputs:
        results["NOT_PUBLISHABLE"] = (
            "scored with the input digest checks waived; this output is for inspection "
            "and must not be published"
        )
    out = run.root / "results.json"
    out.write_text(json.dumps(results, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {out} ({out.stat().st_size:,} bytes), nothing was fitted")
    print(f"next: python -m seq2lead.asof.verify_results {run.root}")
    print(f"then: python -m seq2lead.asof.publish {run.root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
