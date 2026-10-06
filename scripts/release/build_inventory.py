"""Build configs/manifests/release_inventory.json from the tracked bytes.

The inventory is a pure function of (release_classification.json, the files git
tracks at HEAD). Keeping the assignment in its own input is what lets this script
*refuse*: an inventory that silently omits a tracked file, or that carries a
licence label nothing defines, records nothing useful. So every tracked file must
be classified and every classified path must be tracked, with the two
self-excluded manifests as the only permitted difference -- neither can record its
own digest, because writing the digest changes the bytes being described.

    uv run python scripts/release/build_inventory.py [--check]

``--check`` rebuilds and reports whether the committed inventory still matches,
which is how inventory drift was caught before: two files were edited after the
inventory was generated, so its digests described bytes that no longer existed.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CLASSIFICATION = ROOT / "configs/manifests/release_classification.json"
INVENTORY = ROOT / "configs/manifests/release_inventory.json"

INVENTORY_VERSION = "seq2lead-complete-release-inventory-v2"


def tracked_files() -> list[str]:
    """Everything git would ship: files already tracked, plus untracked files that
    .gitignore does not exclude.

    Listing only ``--cached`` would make a newly written, not-yet-staged file
    invisible to the refusals below -- which is precisely the file most likely to
    reach a release unclassified.
    """
    out = subprocess.run(  # noqa: S603
        ["git", "-C", str(ROOT), "ls-files", "--cached", "--others", "--exclude-standard"],  # noqa: S607
        capture_output=True,
        text=True,
        check=True,
    )
    return sorted({line for line in out.stdout.splitlines() if line})


def head_commit() -> str:
    out = subprocess.run(  # noqa: S603
        ["git", "-C", str(ROOT), "rev-parse", "HEAD"],  # noqa: S607
        capture_output=True,
        text=True,
        check=True,
    )
    return out.stdout.strip()


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def build() -> dict:
    spec = json.loads(CLASSIFICATION.read_text())
    classified: dict[str, dict] = spec["files"]
    excluded = set(spec["self_excluded"])
    tracked = set(tracked_files())

    # --- the refusals -------------------------------------------------------
    unclassified = sorted(tracked - set(classified) - excluded)
    if unclassified:
        raise SystemExit(
            f"{len(unclassified)} tracked file(s) have no licence assignment, so an "
            "inventory built now would silently omit them. Classify them in "
            f"{CLASSIFICATION.name} first:\n  " + "\n  ".join(unclassified)
        )
    missing = sorted(set(classified) - tracked)
    if missing:
        raise SystemExit(
            f"{len(missing)} classified path(s) are not tracked at HEAD, so the "
            "assignment describes files this release does not ship:\n  " + "\n  ".join(missing)
        )
    self_referential = sorted(excluded & set(classified))
    if self_referential:
        raise SystemExit(
            "a self-excluded manifest cannot also be an entry, because its digest "
            "would describe bytes that change when the digest is written:\n  "
            + "\n  ".join(self_referential)
        )
    known = set(spec["licence_basis"])
    undefined = sorted({v["licence"] for v in classified.values()} - known)
    if undefined:
        raise SystemExit(
            "licence label(s) with no entry in licence_basis, so nothing in the "
            f"release states what they rest on: {undefined}"
        )

    # --- the entries --------------------------------------------------------
    entries = []
    for path in sorted(classified):
        row = classified[path]
        full = ROOT / path
        entry = {
            "path": path,
            "bytes": full.stat().st_size,
            "sha256": digest(full),
            "category": row["category"],
            "licence": row["licence"],
        }
        if "exception" in row:
            entry["licence_exception"] = row["exception"]
        entries.append(entry)

    by_licence = Counter(e["licence"] for e in entries)
    exceptions = [e["path"] for e in entries if "licence_exception" in e]
    return {
        "inventory": INVENTORY_VERSION,
        "generated_by": "scripts/release/build_inventory.py",
        "classification": spec["classification"],
        # Deliberately NOT called "commit": this file cannot name the commit that
        # contains it, because writing that name changes the bytes being
        # committed. It names the commit the working tree stood on when the
        # digests were taken; the authoritative check is --check, which compares
        # the recorded digests against the files as they are now.
        "generated_after_commit": head_commit(),
        "recorded_at": datetime.now(UTC).isoformat(),
        "release_option": (
            "A - complete benchmark, pending owner approval. Repository remains PRIVATE."
        ),
        "scientific_bytes_preserved": True,
        "files": len(entries),
        "total_bytes": sum(e["bytes"] for e in entries),
        "by_category": dict(sorted(Counter(e["category"] for e in entries).items())),
        "by_licence": dict(sorted(by_licence.items())),
        "licence_basis": spec["licence_basis"],
        "licence_exceptions": {
            "count": len(exceptions),
            "paths": exceptions,
            "note": (
                "An exception is a file whose licence needs a stated reason beyond the "
                "licence_basis line. Each entry carries that reason in its own "
                "`licence_exception` field. Every licence label is a single licence: "
                "the earlier revision put the reason inside the label itself "
                "('CC-BY-3.0 (verbatim licence text)'), which made the labels fail to "
                "sum to the file count."
            ),
        },
        "reconciliation": {
            "tracked_at_head": len(tracked),
            "self_excluded": sorted(excluded),
            "entries": len(entries),
            "licence_labels_sum": sum(by_licence.values()),
            "accounts_for_every_tracked_file": (
                len(entries) + len(excluded) == len(tracked)
                and sum(by_licence.values()) == len(entries)
            ),
        },
        "self_excluded": {
            "paths": sorted(excluded),
            "why": spec["self_excluded_why"],
        },
        "digest_note": (
            "every digest here was computed from the working tree as it stood when "
            "this file was generated, one commit after `generated_after_commit`. An "
            "inventory whose digests do not match its files records nothing, so the "
            "check that matters is `python scripts/release/build_inventory.py "
            "--check`, which recomputes every digest from the files as they are now. "
            "Two files were once edited after generation and the inventory described "
            "bytes that no longer existed; this is the guard against repeating that."
        ),
        "provenance_method": spec["provenance_method"],
        # Untracked at HEAD but still reachable in history, so publishing the
        # repository would expose it. Carried from the classification because it is
        # an input: nothing in the tracked bytes can reveal it.
        "also_in_git_history": spec.get("also_in_git_history", []),
        "entries": entries,
    }


def main() -> int:
    built = build()
    rendered = json.dumps(built, indent=1, sort_keys=True) + "\n"
    if "--check" in sys.argv:
        if not INVENTORY.exists():
            print("no inventory committed yet")
            return 1
        current = json.loads(INVENTORY.read_text())
        drift = [
            e["path"]
            for e, c in zip(built["entries"], current.get("entries", []), strict=False)
            if e["path"] == c["path"] and (e["sha256"] != c["sha256"] or e["bytes"] != c["bytes"])
        ]
        same_paths = [e["path"] for e in built["entries"]] == [
            e["path"] for e in current.get("entries", [])
        ]
        if drift or not same_paths:
            print(f"inventory is STALE: {len(drift)} digest change(s), paths match: {same_paths}")
            for p in drift:
                print(f"  {p}")
            return 1
        print(f"inventory matches the tracked bytes: {built['files']} files")
        return 0
    INVENTORY.write_text(rendered)
    r = built["reconciliation"]
    print(f"wrote {INVENTORY.relative_to(ROOT)}")
    print(f"  tracked at HEAD   {r['tracked_at_head']}")
    print(f"  self-excluded     {len(r['self_excluded'])}  {r['self_excluded']}")
    print(f"  entries           {r['entries']}")
    for licence, n in built["by_licence"].items():
        print(f"      {licence:14} {n:4}")
    print(f"  labels sum to     {r['licence_labels_sum']}")
    print(f"  every tracked file accounted for: {r['accounts_for_every_tracked_file']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
