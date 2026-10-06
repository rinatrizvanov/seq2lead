"""Score every cell from the saved predictions. No fitting, no refitting.

Takes a run directory, reads `predictions.npz` and the evaluation table, and
computes the declared metrics with the accepted `m8/v2` implementation. Because
it reads only saved predictions it is also the recompute path: running it twice
must give identical numbers, and running it after the fit must reproduce what the
fit reported.

Four cells, each a slice of one table, so all four come from the same fitted
models. New-to-fitting is the headline; recurrent is reported separately and
never pooled into it; absent-from-A is the stricter subgroup; and the
validation-reserved pairs stay identifiable as having participated in selection.
"""

from __future__ import annotations

import hashlib
import json
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

from seq2lead.eval.metrics import MIN_NEGATIVES, MIN_POSITIVES, score_target_ranking
from seq2lead.eval.recompute import METRIC_VERSION

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


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 22), b""):
            h.update(chunk)
    return h.hexdigest()


def macro(values: list[float | None]) -> float | None:
    present = [v for v in values if v is not None]
    return float(np.mean(present)) if present else None


def score_slice(
    rows: list[int],
    pairs: list[str],
    labels: list[str],
    scores: np.ndarray,
) -> dict:
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
    out = {
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
        "auroc": macro([r.auroc for _t, r in scored]),
        "auprc": macro([r.average_precision for _t, r in scored]),
        **{f"recall_at_{k}": macro([r.recall_at.get(k) for _t, r in scored]) for k in RECALL_KS},
        **{
            f"ef_at_{int(f * 100)}pct": macro([r.enrichment_at.get(f) for _t, r in scored])
            for f in ENRICHMENT_FRACTIONS
        },
    }
    return out


def main(run_dir: str) -> None:
    root = Path(run_dir)
    blob = np.load(root / "predictions.npz", allow_pickle=True)
    pairs = [str(p) for p in blob["pair"]]
    tags = sorted(k for k in blob.files if k != "pair")
    table = [json.loads(line) for line in (root / "evaluation-pairs.jsonl").open() if line.strip()]
    if [r["pair"] for r in table] != pairs:
        msg = "the prediction rows and the evaluation table are not in the same order"
        raise SystemExit(msg)

    strata = [r["stratum"] for r in table]
    selection_flag = [bool(r["participated_in_model_selection"]) for r in table]

    results: dict = {
        "results": "m11h-asof-results-v1",
        "metric_version": METRIC_VERSION,
        "recomputed_from": {
            "predictions": str(root / "predictions.npz"),
            "predictions_sha256": sha256(root / "predictions.npz"),
            "evaluation_table_sha256": sha256(root / "evaluation-pairs.jsonl"),
            "note": "read from saved predictions; nothing was refitted to produce this",
        },
        "primary_cell": {"increment_arm": PRIMARY_CELL[0], "consistency_branch": PRIMARY_CELL[1]},
        "headline_stratum": "new_to_fitting (all three subgroups), never pooled with recurrent",
        "model_tags": tags,
        "cells": {},
    }

    for arm in ARMS:
        for branch in BRANCHES:
            cell = f"{arm}/{branch}"
            labels = [r["arms"][arm]["label"] or "none" for r in table]
            eligible = [
                i for i, r in enumerate(table)
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
            cell_out: dict = {
                "eligible_pairs": len(eligible),
                "is_primary": (arm, branch) == PRIMARY_CELL,
                "groups": {},
            }
            for group, rows in groups.items():
                if not rows:
                    cell_out["groups"][group] = {"pairs": 0, "metrics": None}
                    continue
                per_model = {}
                for tag in tags:
                    per_model[tag] = score_slice(rows, pairs, labels, blob[tag])
                cell_out["groups"][group] = {
                    "pairs": len(rows),
                    "participated_in_model_selection": sum(1 for i in rows if selection_flag[i]),
                    "by_model": per_model,
                }
            cell_out["groups"]["recurrent"]["note"] = (
                "reported separately. These pairs were supplied to model fitting, so a "
                "score here is not a held-out measurement and is never pooled into the "
                "headline."
            )
            if cell_out["groups"].get("new_reserved_for_validation", {}).get("pairs"):
                cell_out["groups"]["new_reserved_for_validation"]["note"] = (
                    "these pairs PARTICIPATED IN MODEL SELECTION as A-validation rows. "
                    "They are new to fitting but not unseen, and are kept identifiable "
                    "for exactly that reason."
                )
            results["cells"][cell] = cell_out

    # ---- seed spread, over models that have more than one seed -------------
    families: dict[str, list[str]] = defaultdict(list)
    for tag in tags:
        families[tag.split("-seed")[0]].append(tag)
    spread = {}
    primary = results["cells"][f"{PRIMARY_CELL[0]}/{PRIMARY_CELL[1]}"]["groups"]
    for family, members in sorted(families.items()):
        rows = []
        for tag in sorted(members):
            m = primary["new_to_fitting"]["by_model"][tag]["metrics"]
            if m and m["auroc"] is not None:
                rows.append(m["auroc"])
        spread[family] = {
            "seeds": len(members),
            "auroc_mean": round(statistics.fmean(rows), 6) if rows else None,
            "auroc_sd": round(statistics.stdev(rows), 6) if len(rows) > 1 else 0.0,
            "deterministic": len(members) == 1,
        }
    results["seed_spread_on_the_headline"] = {
        "by_model": spread,
        "interpretation": (
            "TRAINING VARIATION across seeds, not a confidence interval. It says nothing "
            "about sampling error in the cohort and must not be read as one."
        ),
    }
    results["qualifications"] = [
        "EXPLORATORY. Snapshot B is our already-inspected 202609.",
        "PROVISIONAL POOLING. Whether Ki may be pooled is M5's open question.",
        "The confirmatory freeze is NOT signed.",
        "Retrieval unbuilt; evaluation evidence display off.",
        "label_reversal-v3 is NOT scored.",
        "No tuning was done in response to any number here.",
    ]
    out = root / "results.json"
    out.write_text(json.dumps(results, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {out} ({out.stat().st_size:,} bytes)")

    head = primary["new_to_fitting"]
    print(f"\nprimary cell, new-to-fitting: {head['pairs']:,} pairs")
    print(f"{'model':<34} {'targets':>8} {'AUROC':>7} {'AUPRC':>7} {'prev':>6} {'EF@1%':>7}")
    for tag in tags:
        s = head["by_model"][tag]
        m = s["metrics"]
        if not m:
            print(f"{tag:<34} {s['targets_scored']:>8}   no scored targets")
            continue
        print(f"{tag:<34} {s['targets_scored']:>8} {m['auroc']:>7.4f} {m['auprc']:>7.4f} "
              f"{s['prevalence_over_scored_targets']:>6.3f} {m['ef_at_1pct']:>7.3f}")


if __name__ == "__main__":
    main(sys.argv[1])
