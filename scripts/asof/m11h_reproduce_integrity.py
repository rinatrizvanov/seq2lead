"""Reproduce the two publication-integrity holes. No fixes here, nothing real touched.

The report and manifest paths are redirected to a temp directory so the published
artifacts are not written during the reproduction.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
from pathlib import Path

REAL = Path("data/asof/m11h/run-20261005T134842Z")


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 22), b""):
            h.update(chunk)
    return h.hexdigest()


def clone(root: Path) -> Path:
    """A self-contained copy of the run, with the manifest repointed into it."""
    root.mkdir(parents=True, exist_ok=True)
    for name in ("manifest.json", "predictions.npz", "evaluation-pairs.jsonl",
                 "training-records.json", "verification.json", "results.json",
                 "protein-transform.npz"):
        shutil.copy2(REAL / name, root / name)
    manifest = json.loads((root / "manifest.json").read_text())
    manifest["predictions"]["path"] = str(root / "predictions.npz")
    manifest["evaluation_table"]["path"] = str(root / "evaluation-pairs.jsonl")
    manifest["training_records"]["path"] = str(root / "training-records.json")
    manifest["transform"]["path"] = str(root / "protein-transform.npz")
    manifest["checkpoints"] = {}
    (root / "manifest.json").write_text(json.dumps(manifest, indent=1, sort_keys=True))
    return root


def flip_labels(root: Path, n: int = 400) -> dict:
    """Change labels while keeping every pair identity and the row order."""
    lines = (root / "evaluation-pairs.jsonl").read_text().splitlines()
    before = [json.loads(line) for line in lines]
    flipped = 0
    rows = []
    for rec in before:
        arm = rec["arms"]["declared_increment"]
        if flipped < n and arm["scoreable"] and arm["label"] in ("active", "inactive"):
            arm["label"] = "inactive" if arm["label"] == "active" else "active"
            flipped += 1
        rows.append(rec)
    after_pairs = [r["pair"] for r in rows]
    (root / "evaluation-pairs.jsonl").write_text(
        "".join(json.dumps(r, sort_keys=True) + "\n" for r in rows), encoding="utf-8"
    )
    return {
        "labels_flipped": flipped,
        "pair_order_unchanged": after_pairs == [r["pair"] for r in before],
        "row_count_unchanged": len(rows) == len(before),
    }


def main() -> None:
    from seq2lead.asof import results_report
    from seq2lead.asof.recompute import RecomputeError, score_run, verify_and_load

    tmp = Path(tempfile.mkdtemp(prefix="m11h-integrity-"))
    run_dir = clone(tmp / "run")
    recorded_table_digest = json.loads(
        (run_dir / "manifest.json").read_text()
    )["evaluation_table"]["sha256"]

    baseline = score_run(verify_and_load(run_dir))
    baseline_auroc = baseline["cells"]["declared_increment/screened_primary"][
        "groups"]["new_to_fitting"]["by_model"]["dual-encoder-seed20260930"]["metrics"]["auroc"]

    detail = flip_labels(run_dir)
    now = sha256(run_dir / "evaluation-pairs.jsonl")
    print("=== hole 1: an evaluation table whose LABELS changed ===")
    print(f"  {detail}")
    print(f"  manifest records table digest {recorded_table_digest[:16]}…")
    print(f"  the file on disk is now       {now[:16]}…  (differs: "
          f"{now != recorded_table_digest})")
    try:
        run = verify_and_load(run_dir)
    except RecomputeError as exc:
        print(f"  REFUSED: {exc}")
        return
    results = score_run(run)
    auroc = results["cells"]["declared_increment/screened_primary"]["groups"][
        "new_to_fitting"]["by_model"]["dual-encoder-seed20260930"]["metrics"]["auroc"]
    print("  ACCEPTED — verify_and_load did not check the table digest")
    print(f"  dual-encoder AUROC: published {baseline_auroc:.6f} -> {auroc:.6f} "
          f"(moved {abs(auroc - baseline_auroc):.6f})")

    print("\n=== hole 2: publishing from a STALE verification record ===")
    stale = json.loads((run_dir / "verification.json").read_text())
    print(f"  verification.json on disk says passed={stale['passed']}, and it was "
          "produced before the table changed")
    report_path = tmp / "report.md"
    manifest_path = tmp / "m11h_fit.json"
    shutil.copy2("configs/manifests/m11h_fit.json", manifest_path)
    before_manifest = json.loads(manifest_path.read_text())
    results_report.REPORT = report_path
    results_report.MANIFEST = manifest_path
    try:
        results_report.write_report(run, results)
    except Exception as exc:  # noqa: BLE001
        print(f"  REFUSED: {type(exc).__name__}: {exc}")
        return
    text = report_path.read_text()
    print(f"  PUBLISHED a report of {len(text):,} chars from altered inputs")
    print(f"  it renders the verification verdict as: "
          f"{'PASSED' if 'PASSED' in text else 'not found'}")
    after = json.loads(manifest_path.read_text())
    # Compare by basename: the clone lives under a different root, so the full
    # paths differ while the artifacts themselves are the same ones.
    was = {Path(k).name: v for k, v in before_manifest["artifacts"].items()}
    now_ = {Path(k).name: v for k, v in after["artifacts"].items()}
    changed = sorted(n for n in now_ if n in was and now_[n] != was[n])
    print(f"  and it re-registered FRESH digests for {len(changed)} artifacts, which")
    print("  launders the change into the manifest:")
    for name in changed:
        print(f"    {name}: expected {was[name][:12]}\u2026 -> recorded {now_[name][:12]}\u2026")
    same = sorted(n for n in now_ if n in was and now_[n] == was[n])
    print(f"  ({len(same)} unchanged: {', '.join(same)})")


if __name__ == "__main__":
    main()
