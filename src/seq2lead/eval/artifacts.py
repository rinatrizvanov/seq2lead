"""A manifest of the large artifacts a review should not carry.

Feature vectors and per-pair predictions are hundreds of megabytes. Bundling
them into a review archive helps nobody; a path plus a checksum lets the
recipient verify the exact bytes the numbers came from, and fetch or rebuild
them if they want to.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import psycopg

    from seq2lead.eval.config import ExperimentConfig

MANIFEST_PATH = Path("reports/results/artifacts.md")


def _rows_for(directory: Path, pattern: str) -> list[tuple[Path, int, str]]:
    from seq2lead.features.store import sha256_file

    if not directory.exists():
        return []
    return [
        (path, path.stat().st_size, sha256_file(path)) for path in sorted(directory.glob(pattern))
    ]


def render(conn: psycopg.Connection, config: ExperimentConfig) -> str:
    lines: list[str] = []
    a = lines.append
    a("# M8 generated artifacts")
    a("")
    a(f"Generated {datetime.now(UTC):%Y-%m-%d %H:%M} UTC.")
    a("")
    a(
        "Referenced by path and SHA-256 rather than bundled. Everything in "
        "`reports/leaderboard.md` can be recomputed from these without refitting."
    )
    a("")

    a("## Feature caches (inputs)")
    a("")
    a("| Cache | Path | Bytes | SHA-256 |")
    a("| --- | --- | --- | --- |")
    for kind, ref in config.caches.items():
        size = ref.path.stat().st_size if ref.path.exists() else 0
        a(f"| {kind} | `{ref.path}` | {size:,} | `{ref.storage_sha256}` |")
    a("")

    predictions = _rows_for(Path("data/predictions"), "*.npz")
    a("## Per-pair predictions (outputs)")
    a("")
    if not predictions:
        a("_None written._")
    else:
        total = sum(size for _p, size, _d in predictions)
        a(
            f"{len(predictions)} files, {total / 1e6:,.1f} MB. Each holds compound id, "
            "target id, label and prediction for every scored pair, plus the regression "
            "arrays."
        )
        a("")
        a("| File | Bytes | SHA-256 |")
        a("| --- | --- | --- |")
        for path, size, digest in predictions:
            a(f"| `{path.name}` | {size:,} | `{digest}` |")
    a("")

    from seq2lead.features.store import sha256_file

    manifest = Path("configs/manifests/m8_predictions.json")
    index = Path("reports/results/index.json")
    if manifest.exists() or index.exists():
        a("## Verification inputs")
        a("")
        a("| File | Bytes | Role | SHA-256 |")
        a("| --- | --- | --- | --- |")
        if manifest.exists():
            a(
                f"| `{manifest}` | {manifest.stat().st_size:,} | frozen expected run set "
                f"and prediction digests | `{sha256_file(manifest)}` |"
            )
        if index.exists():
            a(
                f"| `{index}` | {index.stat().st_size:,} | named result versions | "
                f"`{sha256_file(index)}` |"
            )
        a("")
        a(
            "The prediction manifest is frozen: its expected digests were recovered from "
            "records written before the verification code existed, not regenerated from "
            "the files being checked. Recomputation refuses to run without it."
        )
        a("")

    records = (
        (Path("reports/results/baseline_runs.json"), "no — too large", "v1 full, superseded"),
        (Path("reports/results/baseline_summary.json"), "yes", "v1 summary, superseded"),
        (Path("reports/results/baseline_summary_v2.json"), "yes", "**current** (m8/v2)"),
        (Path("reports/results/correction_v2.json"), "yes", "per-value v1 to v2 comparison"),
    )
    if any(path.exists() for path, _archive, _note in records):
        a("## Run records")
        a("")
        a("| File | Bytes | In archive | Role | SHA-256 |")
        a("| --- | --- | --- | --- | --- |")
        for path, in_archive, note in records:
            if path.exists():
                a(
                    f"| `{path}` | {path.stat().st_size:,} | {in_archive} | {note} | "
                    f"`{sha256_file(path)}` |"
                )
        a("")
        a(
            "`baseline_runs.json` carries one entry per target per run and reaches tens "
            "of megabytes, so it is referenced by checksum rather than bundled. The "
            "summaries drop only the `per_target` arrays, so **every number in the "
            "leaderboard is recomputable from them**. The v1 files are kept rather than "
            "overwritten: a corrected figure is only checkable against the one it "
            "replaced, and `correction_v2.json` records the per-value comparison."
        )
        a("")
    return "\n".join(lines) + "\n"


def write(content: str, path: Path = MANIFEST_PATH) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path
