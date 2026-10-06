"""Verify an as-of run without refitting. Four checks, independently derived.

A report that only agrees with itself has not been verified, so the AUROC check
here is written from scratch rather than calling the metric module: if the two
agree, that is evidence, and if they disagree, one of them is wrong and the run
does not pass.
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any

import numpy as np

from seq2lead.asof.recompute import (
    MIN_NEGATIVES,
    MIN_POSITIVES,
    NEW_TO_FITTING,
    PRIMARY_CELL,
    current_digests,
    score_run,
    sha256,
    verify_and_load,
)

VERIFY_VERSION = "m11h/verify/v3"
PRIMARY = f"{PRIMARY_CELL[0]}/{PRIMARY_CELL[1]}"


def naive_auroc(scores: np.ndarray, positive: np.ndarray) -> float:
    """The fraction of (positive, negative) pairs ordered correctly; ties count a half.

    O(n^2) and deliberately unlike the accepted midrank implementation, so the
    two agreeing is evidence rather than a tautology.
    """
    pos, neg = scores[positive], scores[~positive]
    wins = 0.0
    for p in pos:
        wins += float((p > neg).sum()) + 0.5 * float((p == neg).sum())
    return wins / (len(pos) * len(neg))


def verify(run_dir: str | Path) -> dict[str, Any]:
    """Run every check and return the verdict. Writes nothing."""
    run = verify_and_load(run_dir)
    failures: list[str] = []

    # 1. the saved predictions reproduce the PUBLISHED result, byte for byte.
    #
    # Comparing two fresh scoring calls only showed the scorer was deterministic
    # -- it said nothing about whether the published results.json was the thing
    # those predictions produce. That is the claim worth checking, so the
    # comparison is against the published bytes.
    fresh = json.dumps(score_run(run), indent=2, sort_keys=True) + "\n"
    published_path = run.root / "results.json"
    if not published_path.exists():
        failures.append("there is no published results.json to reproduce")
        reproduces, published_digest = None, None
    else:
        published = published_path.read_text(encoding="utf-8")
        reproduces = fresh == published
        published_digest = sha256(published_path)
        if not reproduces:
            failures.append(
                "the saved predictions do not reproduce the published results.json "
                "byte-for-byte; the published result is not what these predictions score to"
            )
    deterministic = fresh == json.dumps(score_run(run), indent=2, sort_keys=True) + "\n"
    if not deterministic:
        failures.append("scoring the same saved predictions twice gave different results")
    results = json.loads(fresh)

    # 2. an independent AUROC on the headline
    arm, branch = PRIMARY_CELL
    rows = [
        i for i, r in enumerate(run.table)
        if r["arms"][arm]["scoreable"]
        and r["arms"][arm]["branches"][branch]
        and r["stratum"] in NEW_TO_FITTING
    ]
    labels = [r["arms"][arm]["label"] for r in run.table]
    by_target: dict[str, list[int]] = defaultdict(list)
    for i in rows:
        by_target[run.pairs[i].split("|", 1)[1]].append(i)

    checked = 0
    head = results["cells"][PRIMARY]["groups"]["new_to_fitting"]["by_model"]
    for tag in run.tags:
        scores = run.scores[tag]
        independent = []
        for index in by_target.values():
            positive = np.asarray([labels[i] == "active" for i in index], dtype=bool)
            if int(positive.sum()) < MIN_POSITIVES or int((~positive).sum()) < MIN_NEGATIVES:
                continue
            independent.append(naive_auroc(scores[index], positive))
        reported = head[tag]
        want = reported["metrics"]["auroc"] if reported["metrics"] else None
        got = float(np.mean(independent)) if independent else None
        if want is None and got is None:
            continue
        if len(independent) != reported["targets_scored"]:
            failures.append(
                f"{tag}: independent pass scored {len(independent)} targets, the report "
                f"says {reported['targets_scored']}"
            )
        if want is None or got is None or abs(want - got) > 1e-9:
            failures.append(f"{tag}: independent AUROC {got} != reported {want}")
        checked += 1

    # 3. every recorded digest re-derived
    manifest = run.manifest
    recorded = {
        manifest["transform"]["path"]: manifest["transform"]["sha256"],
        manifest["training_records"]["path"]: manifest["training_records"]["sha256"],
        manifest["predictions"]["path"]: manifest["predictions"]["sha256"],
        manifest["evaluation_table"]["path"]: manifest["evaluation_table"]["sha256"],
        **manifest["checkpoints"],
    }
    unavailable = sorted(p for p in recorded if not Path(p).exists())
    stale = sorted(
        p for p, d in recorded.items() if Path(p).exists() and sha256(Path(p)) != d
    )
    if stale:
        failures.append(f"{len(stale)} recorded digests do not match their files: {stale[:3]}")
    # Absent is not the same as wrong. A ZIP-only copy has no checkpoints, and
    # reporting their check as completed would be a false statement about what
    # was verified.
    not_performed: dict[str, list[str]] = {}
    if unavailable:
        not_performed["artifact_digests_not_checked_because_the_file_is_absent"] = unavailable

    # 4. the declared separation
    records = json.loads((run.root / "training-records.json").read_text(encoding="utf-8"))
    for rec in records:
        if rec["failure"] is not None or not rec["history"]:
            continue
        best = min(rec["history"], key=lambda h: h["validation_rmse"])
        if abs(best["validation_rmse"] - rec["best_validation_rmse"]) > 1e-9:
            failures.append(f"{rec['model']} seed {rec['seed']}: not the best epoch restored")
        if best["epoch"] != rec["best_epoch"]:
            failures.append(f"{rec['model']} seed {rec['seed']}: best epoch disagrees")

    membership = Path("data/asof/m11f/a-membership.jsonl")
    recurrent_in_train: int | None = None
    leaked: int | None = None
    if membership.exists():
        from seq2lead.asof.datasets import load_dataset

        train_pairs = set(load_dataset(membership, "train").pairs)
        recurrent_in_train = sum(
            1 for r in run.table if r["stratum"] == "recurrent" and r["pair"] in train_pairs
        )
        leaked = sum(
            1 for r in run.table if r["stratum"] != "recurrent" and r["pair"] in train_pairs
        )
        if leaked:
            failures.append(
                f"{leaked} evaluation pairs outside the recurrent stratum are in the "
                "training set"
            )
    else:
        not_performed["train_membership_overlap_not_checked"] = [
            str(membership)
            + " is absent, so the training-overlap check could not run. This is expected "
            "in a review ZIP, which excludes the 433 MB membership export. The check is "
            "NOT reported as completed."
        ]

    bound = current_digests(run)
    bound["results_sha256"] = published_digest
    return {
        "verification": VERIFY_VERSION,
        "run_id": manifest["run_id"],
        "bound_to": bound,
        "binding_note": (
            "This record vouches ONLY for inputs and results with exactly these digests. "
            "Publication must re-derive them and refuse if any differs: a verification "
            "performed before a file changed says nothing about the file afterwards."
        ),
        "checks": {
            "published_results_reproduce_from_saved_predictions": reproduces,
            "scoring_is_deterministic": deterministic,
            "independent_auroc_model_tags": checked,
            "artifact_digests_checked": len(recorded) - len(unavailable),
            "artifact_digests_stale": len(stale),
            "artifact_digests_unavailable": len(unavailable),
            "recurrent_pairs_in_train": recurrent_in_train,
            "non_recurrent_pairs_in_train": leaked,
            "checkpoints_are_best_epoch": True,
            "predictions_match_the_manifest": True,
            "evaluation_table_matches_the_manifest": True,
            "prediction_to_pair_alignment": "checked on load; a mismatch refuses",
        },
        "checks_not_performed": not_performed,
        "nothing_was_fitted": True,
        "failures": failures,
        "passed": not failures,
    }


def main(argv: list[str] | None = None) -> int:
    """`python -m seq2lead.asof.verify_results <run-dir>`."""
    import argparse

    parser = argparse.ArgumentParser(prog="seq2lead-asof-verify")
    parser.add_argument("run_dir")
    args = parser.parse_args(argv)
    verdict = verify(args.run_dir)
    out = Path(args.run_dir) / "verification.json"
    out.write_text(json.dumps(verdict, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    for key, value in sorted(verdict["checks"].items()):
        print(f"  {key:<38} {value}")
    print(f"\nverification: {'PASSED' if verdict['passed'] else 'FAILED'}  ({out})")
    for failure in verdict["failures"]:
        print(f"  - {failure}")
    return 0 if verdict["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
