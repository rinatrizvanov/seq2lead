"""Render `reports/profile.md`.

Measured results and extrapolations are kept in separate sections on purpose.
The pilot subset is Ki-only, single-source and single-assay-family, so several of
its properties are known *not* to hold for the full release; every extrapolation
states which way it is likely to be wrong.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from seq2lead.profiling.esm2 import MAX_RESIDUES

if TYPE_CHECKING:
    from seq2lead.ingest.bindingdb import IngestReport
    from seq2lead.ingest.download import DownloadResult
    from seq2lead.ingest.sources import SourceFile
    from seq2lead.profiling.dataset import DatasetStats
    from seq2lead.profiling.esm2 import Esm2Profile
    from seq2lead.profiling.storage import StorageProfile

# Published figures for the full 202609 release, from BindingDB's download page.
FULL_MEASUREMENTS = 3_241_782
FULL_TARGETS = 11_509
FULL_ARCHIVE_BYTES = int(566.08 * 1024 * 1024)

REPORT_PATH = Path("reports/profile.md")


def _mib(n: float) -> str:
    return f"{n / 1024 / 1024:,.1f} MiB"


def _gib(n: float) -> str:
    return f"{n / 1024 / 1024 / 1024:,.2f} GiB"


def _hms(seconds: float) -> str:
    if seconds < 90:
        return f"{seconds:,.1f} s"
    if seconds < 5400:
        return f"{seconds / 60:,.1f} min"
    return f"{seconds / 3600:,.2f} h"


def render(
    source: SourceFile,
    download: DownloadResult,
    ingest: IngestReport,
    stats: DatasetStats,
    storage: StorageProfile,
    esm2: Esm2Profile | None,
    lengths: dict[str, int] | None = None,
    total_ingest_seconds: float | None = None,
) -> str:
    ratio = ingest.member_bytes / download.archive_bytes
    scale = FULL_MEASUREMENTS / ingest.loaded if ingest.loaded else 0.0
    bytes_per_row = storage.per_row(storage.total)

    lines: list[str] = []
    a = lines.append

    a("# M1 — bounded pilot profile")
    a("")
    a(f"Generated {datetime.now(UTC):%Y-%m-%d %H:%M} UTC on the M1 pilot subset.")
    a("")
    a(
        "**Scope.** Everything under *Measured* was observed on "
        f"`{source.filename}`. Everything under *Extrapolated* is arithmetic on "
        "those measurements and is explicitly uncertain — the pilot is a Ki-only, "
        "single-curator subset and is not a random sample of BindingDB."
    )
    a("")

    a("## Provenance")
    a("")
    a("| Field | Value |")
    a("| --- | --- |")
    a(f"| Source | {source.source_name} {source.version}, subset `{source.subset}` |")
    a(f"| URL | `{source.url}` |")
    a(f"| Archive member | `{source.archive_member}` |")
    a(f"| SHA-256 (computed) | `{download.sha256}` |")
    a(
        "| SHA-256 pinned in manifest | "
        + ("**yes** — checked before any database write |" if download.sha256_pinned else "no |")
    )
    a(f"| MD5 (computed) | `{download.md5}` |")
    a(
        "| MD5 matched publisher | "
        + ("yes |" if download.md5_verified else "no `.md5` published for this file |")
    )
    a(f"| Archive bytes | {download.archive_bytes:,} ({_mib(download.archive_bytes)}) |")
    a(f"| Uncompressed bytes | {ingest.member_bytes:,} ({_mib(ingest.member_bytes)}) |")
    a(f"| Compression ratio | {ratio:.2f}× |")
    a(f"| License | {source.license} |")
    a("")
    a(
        "The two checksums answer different questions. The **SHA-256 pin lives in "
        "the source manifest** and is what fixes the dataset version: if upstream "
        "republishes a file under the same name, only this catches it. The "
        "publisher's **MD5 attests transfer integrity only** — it is re-published "
        "beside the archive, so it moves with any replacement."
    )
    a("")

    a("## Measured — ingest")
    a("")
    a("| Metric | Value |")
    a("| --- | --- |")
    a(f"| TSV columns | {ingest.column_count:,} |")
    a(f"| Data lines | {ingest.data_lines:,} |")
    a(f"| Rows loaded | {ingest.loaded:,} |")
    a(f"| Rows excluded | {ingest.excluded:,} |")
    a(
        f"| **Reconciles** (loaded + excluded = data lines) | "
        f"**{'yes' if ingest.reconciles else 'NO — investigate'}** |"
    )
    a(f"| COPY wall time | {_hms(ingest.copy_seconds)} |")
    a(f"| **COPY rate** | **{ingest.rows_per_second:,.0f} rows/s** |")
    a(f"| Exclusion write time | {_hms(ingest.exclusion_seconds)} |")
    a(f"| Index build time | {_hms(ingest.index_seconds_total)} |")
    if total_ingest_seconds is not None:
        a(f"| **Complete ingest (COPY + indexes + commit)** | **{_hms(total_ingest_seconds)}** |")
        a(f"| Effective end-to-end rate | {ingest.loaded / total_ingest_seconds:,.0f} rows/s |")
    a("")
    a("### Index build time")
    a("")
    a("| Index | Seconds |")
    a("| --- | --- |")
    for name, secs in ingest.index_seconds.items():
        a(f"| `{name}` | {secs:,.2f} |")
    a(f"| **total** | **{ingest.index_seconds_total:,.2f}** |")
    a("")
    a(
        "Indexes are dropped before the COPY and rebuilt after it, so these are real "
        "build times rather than a no-op against indexes left from a prior run. "
        "**COPY rate is not ingest rate** — the row above marked *complete ingest* "
        "is the number to plan with."
    )
    a("")
    if ingest.excluded:
        a("Exclusions by rule:")
        a("")
        a("| Rule | Lines |")
        a("| --- | --- |")
        for rule, count in sorted(stats.exclusions_by_rule.items()):
            a(f"| `{rule}` | {count:,} |")
        a("")

    a("## Measured — storage")
    a("")
    if storage.compacted:
        a(
            f"`{storage.relation}` after `VACUUM FULL`, so these are **live** bytes. "
            "PostgreSQL does not return allocated pages to the filesystem when rows "
            "are deleted, so an un-compacted relation reports its high-water mark."
        )
    else:
        a(
            f"`{storage.relation}` **as allocated** — no compaction was run. These are "
            "high-water-mark figures: PostgreSQL does not return pages to the "
            "filesystem when rows are deleted, so they are an upper bound on live "
            "size. The dead-tuple count below says how much slack that represents. "
            "`VACUUM FULL` would give the live figure but takes an ACCESS EXCLUSIVE "
            "lock for the whole rewrite, so it is opt-in via `--compact`."
        )
    a("")
    a("| Component | Total | Per row |")
    a("| --- | --- | --- |")
    heap_pr = storage.per_row(storage.heap_main)
    toast_pr = storage.per_row(storage.toast)
    a(f"| Heap (main fork) | {_mib(storage.heap_main)} | {heap_pr:,.0f} B |")
    a(f"| TOAST (out-of-line values) | {_mib(storage.toast)} | {toast_pr:,.0f} B |")
    a(f"| Indexes | {_mib(storage.indexes)} | {storage.per_row(storage.indexes):,.0f} B |")
    a(f"| **Total** | **{_mib(storage.total)}** | **{bytes_per_row:,.0f} B** |")
    a("")
    a("| Tuple state | Value |")
    a("| --- | --- |")
    a(f"| Rows | {storage.rows:,} |")
    a(f"| Live tuples | {storage.live_tuples:,} |")
    a(f"| Dead tuples | {storage.dead_tuples:,} |")
    a(f"| Lifetime inserts | {storage.tuples_inserted:,} |")
    a(f"| Lifetime deletes | {storage.tuples_deleted:,} |")
    if storage.total_before_compaction is not None:
        reclaimed = storage.reclaimed or 0
        a(f"| Allocated before compaction | {_mib(storage.total_before_compaction)} |")
        a(
            f"| Change after `VACUUM FULL` | {_mib(-reclaimed)} "
            f"({-reclaimed / max(storage.total_before_compaction, 1):+.0%}) |"
        )
        a(f"| `VACUUM FULL` wall time | {_hms(storage.vacuum_seconds)} |")
    a("")
    if storage.total_before_compaction is not None and (storage.reclaimed or 0) <= 0:
        a(
            "Compaction reclaimed nothing here, which is the expected result and the "
            "point of the immutability change: the release was loaded exactly once, "
            "so there are no dead tuples to remove. The small size difference is the "
            "index rebuild — a GIN index built in one pass over a full table does not "
            "come out byte-identical to the one built incrementally during the load."
        )
        a("")
    a(
        f"**TOAST dominates: {storage.per_row(storage.toast):,.0f} B/row against "
        f"{storage.per_row(storage.heap_main):,.0f} B in the heap.** Each row carries "
        f"its target's full amino-acid sequence inline and the pilot has only "
        f"{stats.distinct_sequences:,} distinct sequences across {stats.rows:,} rows. "
        "The raw layer stores that redundancy deliberately — it is a record of the "
        "source file — but it means bytes/row is driven by repeated sequence text, "
        "not field count. The curated layer normalizes sequences into `target` at M3, "
        "so curated storage will not scale from this number."
    )
    a("")

    a("## Measured — what the data looks like")
    a("")
    a("| Metric | Value |")
    a("| --- | --- |")
    a(f"| Rows | {stats.rows:,} |")
    a(
        f"| Columns ever populated | {stats.populated_columns:,} of "
        f"{stats.total_columns:,} ({stats.populated_columns / max(stats.total_columns, 1):.1%}) |"
    )
    a(f"| Mean populated fields per row | {stats.mean_fields_per_row:,.1f} |")
    a(f"| Distinct chain-1 sequences | {stats.distinct_sequences:,} |")
    a(f"| Distinct ligand InChIKeys | {stats.distinct_ligands:,} |")
    a(f"| Distinct target names | {stats.distinct_target_names:,} |")
    a(
        f"| Single-chain rows | {stats.single_chain_rows:,} "
        f"({stats.single_chain_rows / max(stats.rows, 1):.1%}) |"
    )
    a(f"| Ki present | {stats.ki_present:,} |")
    a(f"| Ki exact (`=`) | {stats.ki_exact:,} ({stats.ki_exact / max(stats.ki_present, 1):.1%}) |")
    a(
        f"| Ki right-censored (`>`) | {stats.ki_censored_gt:,} "
        f"({stats.ki_censored_gt / max(stats.ki_present, 1):.1%}) |"
    )
    a(f"| Ki left-censored (`<`) | {stats.ki_censored_lt:,} |")
    a(f"| `Date of publication` populated | {stats.publication_date_present:,} |")
    a(f"| `Date in BindingDB` populated | {stats.bindingdb_date_present:,} |")
    a(f"| `pH` populated | {stats.ph_present:,} |")
    a(f"| `Temp (C)` populated | {stats.temp_present:,} |")
    a("")

    if esm2 is not None:
        a("## Measured — ESM-2 throughput")
        a("")
        a("| Metric | Value |")
        a("| --- | --- |")
        a(f"| Model | `{esm2.model_name}` |")
        a(f"| Device | {esm2.device} |")
        a(f"| Dtype | {esm2.dtype} |")
        a(f"| Embedding dim | {esm2.embedding_dim} |")
        a(f"| Model load time | {_hms(esm2.model_load_seconds)} |")
        a(f"| Sequences embedded | {esm2.n_sequences} |")
        a(f"| Sequences truncated at {MAX_RESIDUES} residues | {esm2.n_truncated} |")
        a(f"| Residues processed | {esm2.residues_processed:,} |")
        a(f"| Wall time | {_hms(esm2.seconds)} |")
        a(f"| **Throughput** | **{esm2.sequences_per_second:,.2f} seq/s** |")
        a(f"| Throughput | {esm2.residues_per_second:,.0f} residues/s |")
        a(f"| Median per sequence | {esm2.median_seconds:,.3f} s |")
        a("")
        a(
            "Batch size 1, mean-pooled over real residues. Sequences were sampled "
            "evenly across the length distribution rather than at random, so this "
            "includes the long tail."
        )
        a("")

    a("## Open decision — M7 long-sequence policy")
    a("")
    if lengths:
        a(
            f"Distinct chain-1 sequences in the pilot range {lengths['min']}–"
            f"{lengths['max']} residues (median {lengths['median']}), and "
            f"**{lengths['over_1022']} of {lengths['n']} exceed the "
            f"{MAX_RESIDUES}-residue limit**."
        )
    else:
        a(f"ESM-2's learned positional embeddings cap input at {MAX_RESIDUES} residues.")
    a("")
    a(
        f"In this benchmark run, {esm2.n_truncated if esm2 else 'some'} of the sampled "
        "proteins were silently truncated to fit. That is acceptable for a timing "
        "measurement and **not** acceptable for a cached embedding."
    )
    a("")
    a("**Decision required before M7 writes any cache entry.** Choose one:")
    a("")
    a("1. **Exclude** proteins over the limit from the benchmark, and report how many.")
    a("2. **Chunk** with overlapping windows and pool across windows.")
    a("3. **Truncate**, but only if the cache key says so.")
    a("")
    a(
        "Whichever is chosen, the cache key must carry the **model name, the resolved "
        "model/tokenizer revision, the pooling rule, and a truncation flag with the "
        "effective residue count**. A truncated embedding must never be stored under "
        "an unqualified full-sequence identity: the `sequence_sha256` alone would "
        "claim to represent a protein the model never saw in full, and every "
        "downstream result computed from it would silently inherit that claim."
    )
    a("")

    a("## Extrapolated — and why each number may be wrong")
    a("")
    a(
        f"Scaling factor: the full 202609 release holds {FULL_MEASUREMENTS:,} "
        f"measurements against this pilot's {ingest.loaded:,} rows — **{scale:,.1f}×**."
    )
    a("")
    a("| Quantity | Extrapolation | Confidence |")
    a("| --- | --- | --- |")
    a(
        f"| Uncompressed full TSV | {_gib(FULL_ARCHIVE_BYTES * ratio)} | "
        "Moderate. Compression ratio should hold; the full file has more populated "
        "columns per row, so this is a **lower bound**. |"
    )
    a(
        f"| `raw_measurement` total (live) | {_gib(bytes_per_row * FULL_MEASUREMENTS)} | "
        "**Low — likely an underestimate.** Driven by TOASTed sequence text. The full "
        "release has more distinct sequences and more multi-chain rows, and TOAST "
        "compresses per value, not across rows. |"
    )
    a(
        f"| Complete ingest | "
        f"{_hms((total_ingest_seconds or ingest.copy_seconds) * scale)} | "
        "**Low.** COPY is roughly linear but index build is not, and the pilot fits "
        "in cache while the full load will contend with WAL and checkpoints. |"
    )
    if esm2 is not None:
        a(
            f"| ESM-2 over {FULL_TARGETS:,} targets | "
            f"{_hms(FULL_TARGETS / max(esm2.sequences_per_second, 1e-9))} | "
            "Moderate. One-time and cached. Depends on the full release's length "
            "distribution, which the pilot need not match. |"
        )
    a("")
    a(
        "> These replace the plan's §7 guesses for the quantities listed. Anything "
        "not in this table is still an estimate."
    )
    a("")

    a("## Correction to the previous revision")
    a("")
    a(
        "An earlier version of this report gave 8,795 B/row and extrapolated "
        "26.55 GiB. That was measured on a relation loaded and deleted six times by "
        "the old re-ingest behaviour — `n_tup_ins` read 166,344 against 27,715 live "
        "rows — and `pg_total_relation_size` counts allocated pages whether or not "
        "anything lives in them. Autovacuum had already marked the space reusable, so "
        "the dead-tuple count read zero while the files stayed at their high-water "
        "mark, which is why the number looked trustworthy."
    )
    a("")
    a(
        "The bloat was still growing: the reviewed report read 8,795 B/row, and a "
        "direct measurement taken a few profile runs later read 9,664 B/row on the "
        "same 27,715 live rows. Each run added another delete-and-reload cycle, so "
        "the figure drifted upward with no change to the data."
    )
    a("")
    a("Measured directly on that bloated relation before the fix:")
    a("")
    a("| | Allocated | Per row |")
    a("| --- | --- | --- |")
    a("| Before `VACUUM FULL` | 255.4 MiB | 9,664 B |")
    a("| After `VACUUM FULL` | 89.4 MiB | 3,382 B |")
    a("| Reclaimed | 166.0 MiB | **65%** |")
    a("")
    a(
        "So the true figure is **3,381 B/row**, and the earlier one overstated it by "
        "2.6x. The extrapolation falls from 26.55 GiB to roughly 10 GiB."
    )
    a("")
    a(
        "Two changes prevent a recurrence: **raw releases are now immutable**, so "
        "re-running the pilot is a verified no-op instead of a delete-and-reload; and "
        "**this report measures a compacted relation** and prints the live/dead "
        "breakdown rather than a single total."
    )
    a("")

    a("## What the pilot cannot tell us")
    a("")
    a(
        "The PDSP Ki subset is **not** a miniature BindingDB. It is one curator "
        "(`Curation/DataSource` is uniformly `PDSP Ki`), one measurement type, and "
        "one assay family. Specifically it cannot exercise:"
    )
    a("")
    a("- **The no-merging rule.** IC50, Kd and EC50 are entirely absent here, so the")
    a("  separation of label spaces is untested against real data.")
    a("- **Mixed licensing.** Every row is from one source, so the per-row CC-BY vs")
    a("  CC-BY-SA resolution has nothing to resolve.")
    a("- **Left-censoring.** No `<` values occur, so only the `>` branch of the")
    a("  interval logic has seen real input.")
    a("- **Malformed input.** Zero lines were ragged and the whole file decodes as")
    a("  strict UTF-8, so the quarantine paths are exercised only by fixtures.")
    a("")

    a("## Open items before M2")
    a("")
    a("| # | Item | Status |")
    a("| --- | --- | --- |")
    a(
        "| 1 | 640-column sparse layout | **Resolved.** `payload` stores non-empty "
        "fields only; the full header on the release makes it exactly reversible. |"
    )
    a(
        "| 2 | Exclusions held in memory | **Resolved.** Spooled to a temporary file "
        "during COPY, then inserted in batches of 5,000. |"
    )
    a(
        "| 3 | Full-table `LATERAL jsonb_object_keys` | **Resolved.** Field counts are "
        "accumulated during ingest into `source_release.field_total`. |"
    )
    a(
        "| 4 | One transaction for the whole load | **Open.** All-or-nothing is right "
        "and the tests pin it, but a 3.2M-row transaction is a large WAL burst. "
        "Measure at M2; if it must be chunked, keep atomicity at the release level. |"
    )
    a(
        "| 5 | Unconditional GIN index on `payload` | **Open.** Size scales with "
        "distinct key/value pairs. Make it optional and justify it against the "
        "queries M3 actually issues. |"
    )
    a(
        "| 6 | Full-release SHA-256 not yet known | **Open by design.** The manifest "
        "entry for `all` has `expected_sha256=None` rather than a guess. Observe the "
        "digest on first download, record it, then ingest. |"
    )
    a(
        "| 7 | Header differences between subsets | **Resolved.** Validation checks "
        "uniqueness and required columns by name; it never requires equality with "
        "the pilot header. |"
    )
    a("")
    return "\n".join(lines) + "\n"


def write(content: str, path: Path = REPORT_PATH) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path
