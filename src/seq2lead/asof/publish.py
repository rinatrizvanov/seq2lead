"""The publication gate. Refuses to publish what has not been verified as it stands.

Recomputation, verification and publication used to be one call, and that let a
`verification.json` produced *before* a file changed authorise a report built
*after* it. Worse, the publisher then recomputed the expected artifact digests
and wrote them into the manifest, so the change was laundered into the record it
was supposed to be caught by.

Three rules follow, and they are the whole module:

* **A verification record authorises nothing unless it is bound to the inputs as
  they stand now.** The record carries the digests it was computed against;
  publication re-derives them and refuses on any difference.
* **Expected input digests are read, never recomputed.** They come from the fit's
  own manifest, which is the root of trust. A publisher that refreshes the value
  it is meant to check is not checking anything.
* **A deliberate version bump is a separate, explicit act** that preserves the
  manifest it supersedes, so history is kept rather than overwritten.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from seq2lead.asof.recompute import (
    RecomputeError,
    current_digests,
    sha256,
    verify_and_load,
)

PUBLISH_VERSION = "m11h/publish/v1"
MANIFEST = Path("configs/manifests/m11h_fit.json")

#: Artifacts whose expected digests come from the fit's manifest and are compared
#: rather than refreshed.
FIT_ARTIFACTS = ("predictions", "evaluation_table", "transform", "training_records")

#: Regenerated on each publication, so their digests are recorded afresh.
DERIVED = ("results.json", "verification.json")


class PublicationRefused(RuntimeError):
    """The run may not be published in its current state."""


def _load_verification(run) -> dict[str, Any]:
    path = run.root / "verification.json"
    if not path.exists():
        msg = (
            f"{path} does not exist. Publication requires a verification record; run "
            f"`python -m seq2lead.asof.verify_results {run.root}` first."
        )
        raise PublicationRefused(msg)
    record = json.loads(path.read_text(encoding="utf-8"))
    if "bound_to" not in record:
        msg = (
            f"{path} carries no `bound_to` digests, so it cannot be shown to describe the "
            "current inputs. It predates the binding requirement; re-verify."
        )
        raise PublicationRefused(msg)
    return record


def check_authorisation(run) -> dict[str, Any]:
    """The verification record must have passed AND be bound to the inputs as they are."""
    record = _load_verification(run)
    now = current_digests(run)
    bound = record["bound_to"]
    drifted = {
        key: {"verified": bound.get(key), "now": value}
        for key, value in now.items()
        if bound.get(key) != value
    }
    if drifted:
        named = ", ".join(sorted(drifted))
        msg = (
            f"the verification record is stale: {named} changed since it was written. A "
            "PASSED record describes the inputs it saw, not the ones on disk now, so it "
            f"does not authorise publication. Re-verify: "
            f"`python -m seq2lead.asof.verify_results {run.root}`. Drift: "
            + json.dumps(
                {k: {s: (d or "")[:16] for s, d in v.items()} for k, v in drifted.items()},
                sort_keys=True,
            )
        )
        raise PublicationRefused(msg)
    if not record.get("passed"):
        msg = (
            "the verification record did not pass, so there is nothing to publish: "
            + json.dumps(record.get("failures", []))[:400]
        )
        raise PublicationRefused(msg)
    return record


def check_expected_inputs(run) -> dict[str, Any]:
    """Compare each fit artifact to the digest the fit recorded. Never refresh it."""
    manifest = run.manifest
    checked, stale, unavailable = {}, [], []
    for name in FIT_ARTIFACTS:
        entry = manifest.get(name)
        if not isinstance(entry, dict) or not entry.get("sha256"):
            continue
        path = Path(entry["path"])
        if not path.exists():
            unavailable.append(str(path))
            continue
        actual = sha256(path)
        checked[name] = {
            "path": str(path),
            "expected": entry["sha256"],
            "matches": actual == entry["sha256"],
        }
        if actual != entry["sha256"]:
            stale.append(
                f"{name} ({path}): expected {entry['sha256'][:16]}\u2026, found {actual[:16]}\u2026"
            )
    for path, expected in sorted(manifest.get("checkpoints", {}).items()):
        if not Path(path).exists():
            unavailable.append(path)
            continue
        if sha256(Path(path)) != expected:
            stale.append(f"checkpoint {path}")
    if stale:
        msg = (
            "fit artifacts do not match the digests the fit recorded, so publication is "
            "refused rather than registering the new values: " + "; ".join(stale[:4])
        )
        raise PublicationRefused(msg)
    return {
        "compared_against": "the fit's own manifest; these values are never recomputed here",
        "checked": checked,
        "unavailable": sorted(unavailable),
    }


def publish(
    run_dir: str | Path,
    *,
    bump: str | None = None,
    manifest_path: Path | None = None,
) -> dict[str, Any]:
    """Verify, then render the report and update the manifest. Fits nothing."""
    from seq2lead.asof import results_report

    target = manifest_path or MANIFEST
    run = verify_and_load(run_dir)
    results_path = run.root / "results.json"
    if not results_path.exists():
        msg = (
            f"{results_path} does not exist; run "
            f"`python -m seq2lead.asof.recompute {run.root}` first."
        )
        raise PublicationRefused(msg)
    results = json.loads(results_path.read_text(encoding="utf-8"))
    if "NOT_PUBLISHABLE" in results:
        msg = f"these results are marked not publishable: {results['NOT_PUBLISHABLE']}"
        raise PublicationRefused(msg)

    record = check_authorisation(run)
    inputs = check_expected_inputs(run)

    previous = json.loads(target.read_text(encoding="utf-8")) if target.exists() else None
    if bump and previous is not None:
        historical = target.with_suffix(f".{previous['manifest']}.json")
        if not historical.exists():
            shutil.copy2(target, historical)
        preserved = str(historical)
    else:
        preserved = None

    report_path = results_report.render(run, results, record)
    manifest = results_report.build_manifest(
        run,
        results,
        record,
        inputs,
        report_path=report_path,
        version=bump or (previous or {}).get("manifest", "m11h-fit-v2"),
        supersedes=(previous or {}).get("manifest") if bump else (previous or {}).get("supersedes"),
        preserved_as=preserved,
    )
    target.write_text(json.dumps(manifest, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return {
        "publish_version": PUBLISH_VERSION,
        "report": str(report_path),
        "manifest": str(target),
        "authorised_by": record["verification"],
        "bound_to": record["bound_to"],
        "fit_artifacts_unavailable": inputs["unavailable"],
        "manifest_version": manifest["manifest"],
        "previous_manifest_preserved_as": preserved,
    }


def main(argv: list[str] | None = None) -> int:
    """`python -m seq2lead.asof.publish <run-dir> [--bump NAME]`."""
    import argparse

    parser = argparse.ArgumentParser(prog="seq2lead-asof-publish")
    parser.add_argument("run_dir")
    parser.add_argument(
        "--bump",
        help="deliberately mint a new manifest version, preserving the current one",
    )
    args = parser.parse_args(argv)
    try:
        out = publish(args.run_dir, bump=args.bump)
    except (PublicationRefused, RecomputeError) as exc:
        print(f"REFUSED: {exc}")
        return 1
    for key, value in sorted(out.items()):
        print(f"  {key:<34} {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
