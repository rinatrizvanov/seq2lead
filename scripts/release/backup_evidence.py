"""Back up the evidence that exists only on this machine, and verify the copy.

Four groups, from `configs/manifests/closeout_inventory.json` and
`docs/BACKUP_CHECKLIST.md`:

    A  the consolidated historical-revisions archive
    B  the four retained review ZIPs
    C  the scientific evidence excluded from Git -- the unrecoverable group
    D  working material preserved outside the repository

Group C is enumerated from disk rather than from the inventory, because the
inventory records it per category (files and bytes) and not per path.

    uv run python scripts/release/backup_evidence.py manifest --out M.tsv
    uv run python scripts/release/backup_evidence.py copy     --dest /Volumes/X
    uv run python scripts/release/backup_evidence.py verify   --dest /Volumes/X
    uv run python scripts/release/backup_evidence.py restore  --dest /Volumes/X

`copy` and `verify` REFUSE a destination on the same physical disk as the source.
A second copy on one disk shares that disk's failure, so it is not a backup, and
this refuses rather than warning -- a warning is something a tired person skips.

Nothing here deletes or moves a source file. Each run writes into a dated
generation directory, so a failed copy can never overwrite the previous good one.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import plistlib
import shutil
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PROJECTS = ROOT.parent

GROUP_A = [PROJECTS / "seq2lead-historical-revisions-archive.zip"]
GROUP_B = [
    PROJECTS / "seq2lead-m11h-integrity-review.zip",
    PROJECTS / "seq2lead-m8-review-v4.zip",
    PROJECTS / "seq2lead-m9-review-v4.zip",
    PROJECTS / "seq2lead-m10-review-v3.zip",
]
GROUP_D = [PROJECTS / "seq2lead-CLAUDE_CLOSEOUT_PROMPT.md"]
#: Where group C lives. Everything under these that git does not track is evidence.
#:
#: `literature/` is deliberately absent: those are third-party copyrighted papers,
#: obtainable again from their publishers, and they are not evidence this project
#: produced. Pass --include-literature to copy them anyway.
GROUP_C_ROOTS = ["data", "caches", "reports", "tools"]
LITERATURE_ROOT = "literature"


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 22), b""):
            h.update(chunk)
    return h.hexdigest()


def tracked() -> set[str]:
    out = subprocess.run(  # noqa: S603
        ["git", "-C", str(ROOT), "ls-files"],  # noqa: S607
        capture_output=True,
        text=True,
        check=True,
    )
    return set(out.stdout.splitlines())


def group_c(*, include_literature: bool = False) -> list[Path]:
    """Files present under the evidence roots that git does not track."""
    known = tracked()
    found: list[Path] = []
    roots = [*GROUP_C_ROOTS, *([LITERATURE_ROOT] if include_literature else [])]
    for root in roots:
        base = ROOT / root
        if not base.exists():
            continue
        for path in base.rglob("*"):
            if not path.is_file() or path.is_symlink():
                continue
            rel = path.relative_to(ROOT).as_posix()
            if rel not in known:
                found.append(path)
    return sorted(found)


def sources(*, include_literature: bool = False) -> list[tuple[str, Path, str]]:
    """(group, absolute source path, path to use under the destination)."""
    items: list[tuple[str, Path, str]] = []
    for group, paths in (("A", GROUP_A), ("B", GROUP_B), ("D", GROUP_D)):
        for p in paths:
            items.append((group, p, f"group{group}/{p.name}"))
    for p in group_c(include_literature=include_literature):
        items.append(("C", p, f"groupC/{p.relative_to(ROOT).as_posix()}"))
    return items


# ----------------------------------------------------------------- same disk


def whole_disk(path: Path) -> str | None:
    """The physical whole-disk identifier behind `path`, via diskutil.

    Returns None when it cannot be determined -- a network or image volume, say.
    """
    probe = path
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    try:
        out = subprocess.run(  # noqa: S603
            ["/usr/sbin/diskutil", "info", "-plist", str(probe)],
            capture_output=True,
            check=True,
        )
        info = plistlib.loads(out.stdout)
    except (OSError, subprocess.CalledProcessError, plistlib.InvalidFileException):
        return None
    for key in ("PhysicalStores", "ParentWholeDisk", "DeviceIdentifier"):
        value = info.get(key)
        if key == "PhysicalStores" and isinstance(value, list) and value:
            inner = value[0]
            if isinstance(inner, dict) and inner.get("DeviceIdentifier"):
                return str(inner["DeviceIdentifier"]).rstrip("s0123456789") or None
        elif isinstance(value, str) and value:
            return value.rstrip("s0123456789") or None
    return None


def refuse_same_disk(dest: Path) -> None:
    src_dev = os.stat(ROOT).st_dev
    probe = dest
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    dest_dev = os.stat(probe).st_dev

    if src_dev == dest_dev:
        raise SystemExit(
            f"REFUSED: {dest} is on the same filesystem as {ROOT} "
            f"(st_dev {src_dev}). A second copy on one disk shares that disk's "
            "failure. Use a different physical device, or storage you control "
            "off this machine."
        )
    src_disk, dest_disk = whole_disk(ROOT), whole_disk(probe)
    if src_disk and dest_disk and src_disk == dest_disk:
        raise SystemExit(
            f"REFUSED: {dest} is a different volume but sits on the same physical "
            f"disk as the source ({src_disk}). Separate volumes do not survive a "
            "disk failure together. Use a different physical device."
        )
    print(f"  destination device check: source st_dev={src_dev} disk={src_disk}")
    print(f"                            dest   st_dev={dest_dev} disk={dest_disk}  OK")


# ------------------------------------------------------------------ commands


def cmd_manifest(args: argparse.Namespace) -> int:
    items = sources(include_literature=args.include_literature)
    lines = ["group\tbytes\tsha256\tsource\tdestination_relative_path"]
    total = 0
    missing = []
    for group, src, rel in items:
        if not src.exists():
            missing.append(str(src))
            continue
        size = src.stat().st_size
        total += size
        lines.append(f"{group}\t{size}\t{digest(src)}\t{src}\t{rel}")
    body = "\n".join(lines) + "\n"
    if args.out:
        Path(args.out).write_text(body)
        print(f"wrote {args.out}")
    else:
        sys.stdout.write(body)
    counts: dict[str, int] = {}
    for group, src, _ in items:
        if src.exists():
            counts[group] = counts.get(group, 0) + 1
    print(f"  files: {sum(counts.values()):,}  {counts}")
    print(f"  bytes: {total:,} ({total / 1024**3:.2f} GB)")
    if missing:
        print(f"  MISSING {len(missing)} source(s):")
        for m in missing:
            print(f"    {m}")
        return 1
    return 0


def generation_dir(dest: Path, *, create: bool) -> Path:
    if args_stamp := os.environ.get("SEQ2LEAD_BACKUP_GENERATION"):
        gen = dest / args_stamp
    else:
        existing = sorted(p for p in dest.glob("seq2lead-evidence-*") if p.is_dir())
        if not create and existing:
            return existing[-1]
        gen = dest / f"seq2lead-evidence-{datetime.now(UTC):%Y%m%dT%H%M%SZ}"
    if create:
        gen.mkdir(parents=True, exist_ok=False)
    return gen


def cmd_copy(args: argparse.Namespace) -> int:
    dest = Path(args.dest).resolve()
    refuse_same_disk(dest)
    all_sources = sources(include_literature=args.include_literature)
    items = [(g, s, r) for g, s, r in all_sources if s.exists()]
    need = sum(s.stat().st_size for _, s, _ in items)
    free = shutil.disk_usage(dest).free
    print(f"  to copy {need:,} bytes; destination free {free:,}")
    if free < need * 2:
        print(
            "  NOTE: less than twice the payload is free, so there is no room for a "
            "second generation. A failed copy would leave you with one unverified copy."
        )
    gen = generation_dir(dest, create=True)
    print(f"  generation: {gen}")
    for i, (_group, src, rel) in enumerate(items, 1):
        target = gen / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, target)
        if i % 250 == 0 or i == len(items):
            print(f"    copied {i:,}/{len(items):,}")
    manifest = gen / "SOURCE-MANIFEST.tsv"
    args.out = str(manifest)
    cmd_manifest(args)
    print(f"  copy complete. Now run: verify --dest {args.dest}")
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    dest = Path(args.dest).resolve()
    refuse_same_disk(dest)
    gen = generation_dir(dest, create=False)
    print(f"  verifying generation: {gen}")
    bad, ok, absent = [], 0, []
    for _group, src, rel in sources(include_literature=args.include_literature):
        if not src.exists():
            continue
        target = gen / rel
        if not target.exists():
            absent.append(rel)
            continue
        if digest(target) != digest(src):
            bad.append(rel)
        else:
            ok += 1
    print(f"  verified identical: {ok:,}")
    if absent:
        print(f"  MISSING at destination: {len(absent):,}")
        for a in absent[:20]:
            print(f"    {a}")
    if bad:
        print(f"  DIGEST MISMATCH: {len(bad):,}")
        for b in bad[:20]:
            print(f"    {b}")
    if absent or bad:
        print("  VERDICT: this is NOT a verified backup. Re-copy before relying on it.")
        return 1
    record = gen / "VERIFIED.json"
    record.write_text(
        json.dumps(
            {
                "verified_at": datetime.now(UTC).isoformat(),
                "files_verified": ok,
                "source_root": str(ROOT),
                "generation": str(gen),
                "method": "SHA-256 of every file, source and destination, compared pairwise",
            },
            indent=1,
        )
        + "\n"
    )
    print(f"  VERDICT: verified backup. Recorded at {record}")
    print(f"  Now run: restore --dest {args.dest}")
    return 0


def _refuse_unsafe_scratch(scratch: Path) -> None:
    """The restore target must be an empty directory outside the project.

    Three ways this could otherwise damage the thing it is meant to protect:
    a scratch inside the working tree would write restored bytes over project
    files; a non-empty scratch makes a stale file from an earlier run
    indistinguishable from a freshly restored one; and a scratch that is the
    project root would be both at once.
    """
    root = ROOT.resolve()
    resolved = scratch.resolve() if scratch.exists() else scratch.absolute()
    if resolved == root or root in resolved.parents:
        raise SystemExit(
            f"REFUSED: --scratch {resolved} is inside the project at {root}. "
            "Restoring there would write backup bytes over the working tree. "
            "Use a path outside the project, e.g. a fresh mktemp -d."
        )
    if resolved.exists():
        if not resolved.is_dir():
            raise SystemExit(f"REFUSED: --scratch {resolved} exists and is not a directory.")
        existing = sorted(resolved.iterdir())
        if existing:
            raise SystemExit(
                f"REFUSED: --scratch {resolved} is not empty ({len(existing)} entries). "
                "A stale file there cannot be told apart from a restored one. "
                "Point --scratch at a new empty directory."
            )
    else:
        resolved.mkdir(parents=True)
    return None


def cmd_restore(args: argparse.Namespace) -> int:
    """Spot-restore one file per group and category.

    A backup that has never been read from is untested, so this copies a sample
    back OFF the destination and re-hashes it against the source. It reads the
    destination and writes only into an empty scratch directory outside the
    project; it never writes to the project and never deletes anything.
    """
    dest = Path(args.dest).resolve()
    root = ROOT.resolve()
    if dest == root or root in dest.parents:
        raise SystemExit(
            f"REFUSED: --dest {dest} is inside the project at {root}. A backup "
            "inside the tree it backs up is not a backup, and restoring from it "
            "would prove nothing."
        )
    gen = generation_dir(dest, create=False)
    if not gen.exists():
        raise SystemExit(
            f"REFUSED: no backup generation found under {dest}. Expected a "
            "directory named seq2lead-evidence-<timestamp>. Run `copy` first, "
            "then `verify`, then this."
        )

    if args.scratch:
        scratch = Path(args.scratch)
    else:
        scratch = Path(tempfile.mkdtemp(prefix="seq2lead-restore-"))
    _refuse_unsafe_scratch(scratch)
    scratch = scratch.resolve()
    print(f"  generation: {gen}")
    print(f"  restoring into: {scratch}  (empty, outside the project)")

    # One sample per EVIDENCE CATEGORY, which for group C is the second level
    # under groupC/ -- data/asof, data/raw, data/features, reports/results and so
    # on. Keying on the first level instead would collapse every one of those into
    # a single "data" bucket and sample one file out of 3,966, while still
    # printing a per-category-looking result. That is the shape of a check that
    # passes because it examined almost nothing.
    picks: dict[str, tuple[Path, str]] = {}
    for group, src, rel in sources(include_literature=args.include_literature):
        parts = Path(rel).parts
        if group == "C" and len(parts) > 2:
            category = "/".join(parts[1:3])
        elif group == "C" and len(parts) > 1:
            category = parts[1]
        else:
            category = group
        key = f"{group}:{category}"
        if key not in picks and src.exists():
            picks[key] = (src, rel)

    failures = 0
    for key, (src, rel) in sorted(picks.items()):
        target = gen / rel
        if not target.exists():
            print(f"  {key}: MISSING AT DESTINATION  {rel}")
            failures += 1
            continue
        # The full relative path, not the basename: two categories can hold files
        # of the same name, and collapsing them would silently overwrite one
        # sample with another and still report success.
        back = scratch / rel
        back.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(target, back)
        same = digest(back) == digest(src)
        print(f"  {key}: {'ok' if same else 'DIGEST MISMATCH'}  {rel}")
        failures += 0 if same else 1

    print(f"  restored {len(picks)} sample(s); failures: {failures}")
    if failures:
        print("  VERDICT: the destination did not return what the source holds.")
        return 1
    print(f"  VERDICT: sample restored and verified. Delete {scratch} when done.")
    print("  This tool never deletes anything; removing the scratch is your call.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="command", required=True)
    m = sub.add_parser("manifest", help="hash every source file; write the digest record")
    m.add_argument("--out")
    m.add_argument(
        "--include-literature",
        action="store_true",
        help="also copy literature/ -- third-party papers, obtainable again from publishers",
    )
    m.set_defaults(func=cmd_manifest)
    for name, fn, helptext in (
        ("copy", cmd_copy, "copy to a destination on a different physical disk"),
        ("verify", cmd_verify, "re-hash at the destination and compare, file by file"),
        ("restore", cmd_restore, "spot-restore a sample and confirm digests"),
    ):
        p = sub.add_parser(name, help=helptext)
        p.add_argument("--dest", required=True)
        p.add_argument("--out", default=None)
        p.add_argument("--scratch", default=None)
        p.add_argument("--include-literature", action="store_true")
        p.set_defaults(func=fn)
    args = ap.parse_args()
    return int(args.func(args) or 0)


if __name__ == "__main__":
    raise SystemExit(main())
