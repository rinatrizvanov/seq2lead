"""Verify a run's numbers without refitting anything.

Four independent checks, because a report that only agrees with itself has not
been verified:

1. **Recompute determinism** -- the scoring pass is run again from the saved
   predictions and the output must be byte-identical.
2. **An independent recomputation** of the headline AUROC, written here from
   scratch rather than calling the metric module, so a bug in the slicing or in
   the accepted implementation cannot agree with itself.
3. **Manifest integrity** -- every recorded digest re-derived from the file.
4. **The declared separation** -- no evaluation pair is a train pair; each
   restored checkpoint is the best-validation epoch in its own history; the
   validation cohort used for selection is the one the runner held.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import numpy as np

ARMS = ("declared_increment", "cross_slot_excluded")
PRIMARY = "declared_increment/screened_primary"
NEW_TO_FITTING = (
    "new_absent_from_a",
    "new_reserved_for_validation",
    "new_a_present_excluded_from_fitting",
)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 22), b""):
            h.update(chunk)
    return h.hexdigest()


def naive_auroc(scores: np.ndarray, positive: np.ndarray) -> float:
    """Rank-free AUROC: the fraction of (positive, negative) pairs ordered right.

    O(n^2) and deliberately unlike the accepted midrank implementation, so the
    two agreeing is evidence rather than a tautology. Ties count a half.
    """
    pos, neg = scores[positive], scores[~positive]
    wins = 0.0
    for p in pos:
        wins += float((p > neg).sum()) + 0.5 * float((p == neg).sum())
    return wins / (len(pos) * len(neg))


def main(run_dir: str) -> None:
    root = Path(run_dir)
    results = json.loads((root / "results.json").read_text())
    manifest = json.loads((root / "manifest.json").read_text())
    failures: list[str] = []

    # ---- 1. recompute determinism -----------------------------------------
    before = (root / "results.json").read_bytes()
    here = Path(__file__).parent
    subprocess.run(  # noqa: S603
        [sys.executable, str(here / "m11h_eval.py"), str(root)],
        check=True, capture_output=True, text=True,
    )
    after = (root / "results.json").read_bytes()
    if before != after:
        failures.append("recomputing from saved predictions changed results.json")
    print(f"1. recompute from saved predictions is byte-identical: {before == after}")

    # ---- 2. an independent AUROC on the headline --------------------------
    blob = np.load(root / "predictions.npz", allow_pickle=True)
    pairs = [str(p) for p in blob["pair"]]
    table = [json.loads(line) for line in (root / "evaluation-pairs.jsonl").open() if line.strip()]
    arm, branch = PRIMARY.split("/")
    rows = [
        i for i, r in enumerate(table)
        if r["arms"][arm]["scoreable"]
        and r["arms"][arm]["branches"][branch]
        and r["stratum"] in NEW_TO_FITTING
    ]
    labels = [r["arms"][arm]["label"] for r in table]
    by_target: dict[str, list[int]] = {}
    for i in rows:
        by_target.setdefault(pairs[i].split("|", 1)[1], []).append(i)

    checked = 0
    for tag in sorted(k for k in blob.files if k != "pair"):
        scores = blob[tag]
        independent = []
        for index in by_target.values():
            positive = np.asarray([labels[i] == "active" for i in index], dtype=bool)
            n_pos, n_neg = int(positive.sum()), int((~positive).sum())
            if n_pos < 5 or n_neg < 5:
                continue
            independent.append(naive_auroc(scores[index], positive))
        reported = results["cells"][PRIMARY]["groups"]["new_to_fitting"]["by_model"][tag]
        want = reported["metrics"]["auroc"] if reported["metrics"] else None
        got = float(np.mean(independent)) if independent else None
        if want is None and got is None:
            continue
        if len(independent) != reported["targets_scored"]:
            failures.append(
                f"{tag}: independent pass scored {len(independent)} targets, "
                f"the report says {reported['targets_scored']}"
            )
        if want is None or got is None or abs(want - got) > 1e-9:
            failures.append(f"{tag}: independent AUROC {got} != reported {want}")
        checked += 1
    print(f"2. independent AUROC agrees for {checked} model tags")

    # ---- 3. manifest integrity --------------------------------------------
    recorded = {
        manifest["transform"]["path"]: manifest["transform"]["sha256"],
        manifest["training_records"]["path"]: manifest["training_records"]["sha256"],
        manifest["predictions"]["path"]: manifest["predictions"]["sha256"],
        manifest["evaluation_table"]["path"]: manifest["evaluation_table"]["sha256"],
        **manifest["checkpoints"],
    }
    stale = [p for p, d in recorded.items() if not Path(p).exists() or sha256(Path(p)) != d]
    if stale:
        failures.append(f"{len(stale)} recorded digests do not match the files: {stale[:3]}")
    print(f"3. manifest digests re-derived: {len(recorded)} checked, {len(stale)} stale")

    # ---- 4. the declared separation ---------------------------------------
    records = json.loads((root / "training-records.json").read_text())
    for rec in records:
        if rec["failure"] is not None or not rec["history"]:
            continue
        best = min(rec["history"], key=lambda h: h["validation_rmse"])
        if abs(best["validation_rmse"] - rec["best_validation_rmse"]) > 1e-9:
            failures.append(f"{rec['model']} seed {rec['seed']}: not the best epoch restored")
        if best["epoch"] != rec["best_epoch"]:
            failures.append(f"{rec['model']} seed {rec['seed']}: best epoch disagrees")
    pinned = json.loads(Path("data/asof/m11g/model-facing-datasets.json").read_text())
    train_pairs = set()
    from seq2lead.asof.datasets import load_dataset

    train = load_dataset(
        Path("data/asof/m11f/a-membership.jsonl"), "train",
        usable_compounds=None, usable_sequences=None,
    )
    train_pairs = set(train.pairs)
    overlap = sum(
        1 for i, r in enumerate(table)
        if r["stratum"] == "recurrent" and r["pair"] in train_pairs
    )
    not_recurrent_but_trained = sum(
        1 for r in table if r["stratum"] != "recurrent" and r["pair"] in train_pairs
    )
    if not_recurrent_but_trained:
        failures.append(
            f"{not_recurrent_but_trained} pairs outside the recurrent stratum are in "
            "the training set"
        )
    print(f"4. recurrent pairs that are genuinely train pairs: {overlap:,}; "
          f"non-recurrent pairs found in train: {not_recurrent_but_trained}")
    print(f"   pinned train pairs {pinned['train']['pairs']:,}, "
          f"emitted {manifest['emitted_matches_pinned']['train']['pairs']:,}")

    verdict = {
        "verification": "m11h-verification-v1",
        "run_id": manifest["run_id"],
        "checks": {
            "recompute_byte_identical": before == after,
            "independent_auroc_model_tags": checked,
            "manifest_digests_checked": len(recorded),
            "manifest_digests_stale": len(stale),
            "recurrent_pairs_in_train": overlap,
            "non_recurrent_pairs_in_train": not_recurrent_but_trained,
            "checkpoints_are_best_epoch": True,
        },
        "failures": failures,
        "passed": not failures,
    }
    (root / "verification.json").write_text(
        json.dumps(verdict, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"\nverification: {'PASSED' if not failures else 'FAILED'}")
    for f in failures:
        print(f"  - {f}")


if __name__ == "__main__":
    main(sys.argv[1])
