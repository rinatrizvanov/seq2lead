"""Render `reports/ingest.md` for the M2 full-release load."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from seq2lead.ingest.bindingdb import IngestReport
    from seq2lead.ingest.download import DownloadResult
    from seq2lead.ingest.sources import SourceFile
    from seq2lead.profiling.storage import StorageProfile
    from seq2lead.profiling.wal import WalDelta

# BindingDB's own stated totals for 202609, from the downloads page.
PUBLISHED_MEASUREMENTS = 3_241_782
PUBLISHED_COMPOUNDS = 1_440_011
PUBLISHED_TARGETS = 11_509

# Pilot-derived predictions being tested (from reports/profile.md).
PILOT_BYTES_PER_ROW = 3_381
PILOT_PREDICTED_TOTAL_GIB = 10.21
PILOT_PREDICTED_INGEST_SECONDS = 3.7 * 60
PILOT_UNCOMPRESSED_PREDICTION_GIB = 6.68

REPORT_PATH = Path("reports/ingest.md")


@dataclass
class ArtifactOutcome:
    source: SourceFile
    download: DownloadResult
    report: IngestReport
    wal: WalDelta | None = None
    exclusions_by_rule: dict[str, int] = field(default_factory=dict)
    quarantine_detail: list[tuple[int, str, int, bool]] = field(default_factory=list)
    distinct_keys: int | None = None
    total_seconds: float | None = None


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
    outcomes: list[ArtifactOutcome],
    storage: dict[str, StorageProfile],
    commands: list[tuple[str, str]],
) -> str:
    lines: list[str] = []
    a = lines.append

    a("# M2 — full raw ingest")
    a("")
    a(f"Generated {datetime.now(UTC):%Y-%m-%d %H:%M} UTC.")
    a("")
    a(
        "Scope: the pinned BindingDB 202609 measurement TSV, the two mapping files "
        "needed to reach assay text, and the target-sequence FASTA, loaded verbatim "
        "into the immutable raw layer. **No curation, no model, no embedding cache, "
        "and no `VACUUM FULL`.**"
    )
    a("")

    # ---------------------------------------------------------------- artifacts
    a("## Artifacts")
    a("")
    a("| Subset | File | Bytes | SHA-256 | MD5 vs publisher | Pinned |")
    a("| --- | --- | --- | --- | --- | --- |")
    for o in outcomes:
        a(
            f"| `{o.source.subset}` | `{o.source.filename}` | "
            f"{o.download.archive_bytes:,} | `{o.download.sha256[:20]}…` | "
            f"{'yes' if o.download.md5_verified else 'none published'} | "
            f"{'yes' if o.download.sha256_pinned else '**NO**'} |"
        )
    a("")
    a("Full digests, for the manifest and any later audit:")
    a("")
    for o in outcomes:
        a(f"- `{o.source.subset}` — `{o.download.sha256}`")
        a(f"  - {o.source.url}")
    a("")
    a(
        "**Release coherence.** The measurement, assay and mapping archives all carry "
        "`202609` in the filename. `BindingDBTargetSequences.fasta` does not — it is a "
        "rolling file at a different base path (`/rwd/bind/`, not `/rwd/bind/downloads/`) "
        "and has no publisher md5. Its `Last-Modified` was 2026-08-30, the same date as "
        "the 202609 archives, which is the only evidence tying it to this release; the "
        "frozen SHA-256 is what makes that binding reproducible. Recorded rather than "
        "assumed away."
    )
    a("")
    a(
        "**Citable archive.** BindingDB recommends the UCSD Library quarterly deposit "
        "(`library.ucsd.edu/dc/collection/bb03870458`) for a referenceable version. Its "
        "per-file 202609 artifacts were not resolvable programmatically, so these loads "
        "use the live downloads pinned by observed SHA-256. The archive remains the "
        "citation of record; the digests above are what make the bytes verifiable."
    )
    a("")

    # ---------------------------------------------------------------- schemas
    a("## Schemas as found in the files")
    a("")
    for o in outcomes:
        if o.source.kind == "fasta":
            a(f"**`{o.source.subset}`** — FASTA, {o.report.data_lines:,} records.")
            a("")
            a("Headers look like `>p1 mol:protein length:376 Thymidine kinase`.")
            a("")
            continue
        a(
            f"**`{o.source.subset}`** — `{o.source.archive_member}`, "
            f"{o.report.column_count:,} columns, {_mib(o.report.member_bytes)} uncompressed."
        )
        a("")
    a(
        "**Join path to assay text, confirmed from the files.** Assay descriptions are "
        "not in the measurement TSV. Reaching them is a two-hop join:"
    )
    a("")
    a("```")
    a("raw_measurement.payload->>'BindingDB Reactant_set_id'")
    a("    -> raw_record.payload->>'REACTANT_SET_ID'      (rsid_eaids)")
    a("       raw_record.payload->>'ENTRYID_ASSAYID'      e.g. '285_1'")
    a("    -> raw_record.payload->>'ENTRYID' = '285'      (assays)")
    a("       AND raw_record.payload->>'ASSAYID' = '1'")
    a("       -> ASSAY_NAME, DESCRIPTION")
    a("```")
    a("")
    a(
        "`ENTRYID_ASSAYID` is `ENTRYID` and `ASSAYID` joined by an underscore. Verified "
        "against real rows, not inferred from the column names."
    )
    a("")

    # ---------------------------------------------------------------- reconciliation
    a("## Row reconciliation")
    a("")
    a("| Subset | Input records | Loaded | Quarantined | Reconciles |")
    a("| --- | --- | --- | --- | --- |")
    for o in outcomes:
        a(
            f"| `{o.source.subset}` | {o.report.data_lines:,} | {o.report.loaded:,} | "
            f"{o.report.excluded:,} | {'yes' if o.report.reconciles else '**NO**'} |"
        )
    a("")
    any_excluded = any(o.report.excluded for o in outcomes)
    if any_excluded:
        a("Quarantined records by rule, with original bytes preserved:")
        a("")
        a("| Subset | Rule | Records |")
        a("| --- | --- | --- |")
        for o in outcomes:
            for rule, count in sorted(o.exclusions_by_rule.items()):
                a(f"| `{o.source.subset}` | `{rule}` | {count:,} |")
        a("")
        for o in outcomes:
            if not o.quarantine_detail:
                continue
            a(f"#### `{o.source.subset}` — what the quarantined records actually are")
            a("")
            a("| Line | Rule | Bytes | Content |")
            a("| --- | --- | --- | --- |")
            for line_no, rule, blen, all_nul in o.quarantine_detail:
                what = (
                    "empty line" if blen == 0 else ("**all NUL bytes**" if all_nul else "non-empty")
                )
                a(f"| {line_no:,} | `{rule}` | {blen:,} | {what} |")
            a("")
            nul = [q for q in o.quarantine_detail if q[3] and q[2] > 0]
            blank = [q for q in o.quarantine_detail if q[2] == 0]
            if nul or blank:
                lines_span = [q[0] for q in o.quarantine_detail]
                a(
                    f"**This is corruption in the published artifact, not in our "
                    f"download.** The archive's MD5 matches the publisher's, so the "
                    f"damaged bytes are what BindingDB distributes. All "
                    f"{len(o.quarantine_detail)} bad records sit in one narrow region "
                    f"(lines {min(lines_span):,}–{max(lines_span):,} of "
                    f"{o.report.data_lines:,}), "
                    + (
                        f"and one of them is a {nul[0][2]:,}-byte run containing "
                        "nothing but NUL — the signature of a partially written "
                        "block rather than a malformed row. "
                        if nul
                        else ""
                    )
                    + "The lines immediately either side parse normally, so the damage "
                    "is localised."
                )
                a("")
                a(
                    "**Correction to an earlier revision of this report.** It claimed "
                    'that a parser using `errors="replace"` would have turned the NUL '
                    "run into replacement characters, and that the old 8 KB truncation "
                    "would have clipped it. Both were wrong. A NUL byte is valid UTF-8, "
                    "so the decode succeeded and produced a 3,118-character string; "
                    '`errors="replace"` would have changed nothing. And 3,118 bytes is '
                    "well under 8,192, so no truncation would have applied either."
                )
                a("")
                a(
                    "What is actually true, and still useful: all six records are "
                    "preserved byte-for-byte in `ingest_exclusion.raw_bytes`, and all "
                    "six are classified by the same rule, `field_count_mismatch` — the "
                    "NUL run decoded cleanly and contained no tab, so it presented as a "
                    "single field against the expected 640, exactly like an empty line "
                    "does. **The rule code alone cannot distinguish a blank line from a "
                    "3 KB corrupt block.** Only the retained bytes and `byte_length` can, "
                    "which is how the table above was built. The `invalid_utf8` rule has "
                    "never fired on real data and is exercised only by fixtures."
                )
                a("")
    measurements = next((o for o in outcomes if o.source.subset == "all"), None)
    if measurements is not None:
        loaded = measurements.report.loaded
        diff = loaded - PUBLISHED_MEASUREMENTS
        a("### Against the publisher's stated count")
        a("")
        a(
            "Two comparisons are possible and they differ, so both are given. The "
            "input-line count is what the file contains; the loaded-row count is what "
            "survived quarantine."
        )
        a("")
        a("| | Count | vs published |")
        a("| --- | --- | --- |")
        a(f"| BindingDB states for 202609 | {PUBLISHED_MEASUREMENTS:,} | — |")
        a(
            f"| Input data lines in the TSV | {measurements.report.data_lines:,} | "
            f"{measurements.report.data_lines - PUBLISHED_MEASUREMENTS:+,} |"
        )
        a(f"| Rows loaded | {loaded:,} | {diff:+,} |")
        a("")
        if diff == 0:
            a("Exact match.")
        else:
            pct = abs(diff) / PUBLISHED_MEASUREMENTS
            a(f"The shortfall is {abs(diff):,} rows, {pct:.3%} of the published figure.")
            a("")
            a("What it is **not**:")
            a("")
            a(
                f"- Not records lost in ingest. Every one of the "
                f"{measurements.report.data_lines:,} data lines in the file is "
                f"accounted for as loaded or quarantined, and only "
                f"{measurements.report.excluded} were quarantined."
            )
            if measurements.distinct_keys is not None:
                a(
                    f"- Not duplicate rows inflating or deflating the count. "
                    f"`BindingDB Reactant_set_id` is distinct on every row "
                    f"({measurements.distinct_keys:,} distinct values over "
                    f"{measurements.report.loaded:,} rows), so rows and reactant sets "
                    "are one-to-one here."
                )
            a("")
            a(
                "**The reason for the discrepancy is unknown.** It would be easy to say "
                "the page's headline is regenerated on a different schedule, or that it "
                "counts measurements in some internal sense rather than export rows — "
                "but neither has been checked against anything, so both are hypotheses, "
                "not findings. What *is* established is that the gap did not arise here: "
                "the file's own lines reconcile exactly, and the artifact matches its "
                "SHA-256. Resolving it would mean asking BindingDB what the headline "
                "counts, which has not been done."
            )
        a("")

    # ---------------------------------------------------------------- performance
    a("## Performance")
    a("")
    a("| Subset | COPY | rows/s | Indexes | Exclusions | End-to-end |")
    a("| --- | --- | --- | --- | --- | --- |")
    for o in outcomes:
        a(
            f"| `{o.source.subset}` | {_hms(o.report.copy_seconds)} | "
            f"{o.report.rows_per_second:,.0f} | {_hms(o.report.index_seconds_total)} | "
            f"{_hms(o.report.exclusion_seconds)} | "
            f"{_hms(o.total_seconds) if o.total_seconds else 'n/a'} |"
        )
    a("")

    a("### WAL and checkpoints")
    a("")
    measured = [o for o in outcomes if o.wal is not None]
    unmeasured = [o for o in outcomes if o.wal is None]
    if measured:
        a("| Subset | WAL generated | WAL records | Full-page images | Checkpoints (timed/req) |")
        a("| --- | --- | --- | --- | --- |")
        for o in measured:
            w = o.wal
            assert w is not None
            a(
                f"| `{o.source.subset}` | {_mib(w.wal_bytes)} | {w.wal_records:,} | "
                f"{w.wal_fpi:,} | {w.checkpoints_timed}/{w.checkpoints_requested} |"
            )
        a("")
    if unmeasured:
        a(
            "WAL figures are **not available** for "
            + ", ".join(f"`{o.source.subset}`" for o in unmeasured)
            + ". Those releases were loaded before WAL accounting was persisted on the "
            "release row (migration `0003_release_wal_accounting`), and a release is "
            "immutable, so it cannot be re-loaded to measure it. Reporting the ~0 delta "
            "of a verified no-op re-run would be a lie, so nothing is reported."
        )
        a("")
        a(
            "A WAL cost was measured instead on a **different and much smaller "
            "artifact** — see [`wal_probe.md`](wal_probe.md), produced by "
            "`seq2lead profile wal`, which loads `rsid_eaids` under a scratch release "
            "and removes it afterwards. That artifact has a comparable *row* count "
            "(3,179,005) but a far smaller payload: roughly 200 MiB against the "
            "measurement table's ~11 GiB, and two short columns against ~30 populated "
            "fields carrying full protein sequences."
        )
        a("")
        a(
            "**So the probe does not tell us what the full load did.** Its checkpoint "
            "count is a property of that load, not of the measurement load, and it must "
            "not be read across: a load writing fifty times the payload would plausibly "
            "behave differently in either direction. The full load's own counters were "
            "never captured, and the release is immutable, so the honest position is "
            "that **the measurement load's WAL and checkpoint cost is unknown**."
        )
        a("")
        a(
            "What the probe does establish is that bulk COPY on this cluster can "
            "generate WAL several times the size of the stored payload and can trigger "
            "requested checkpoints at all. That is worth knowing before M3 writes a "
            "comparable volume, but it is **not** evidence that `max_wal_size` must be "
            "raised for M3 — that would need measuring the load in question. Migration "
            "`0003_release_wal_accounting` now records these counters per release, so "
            "the next load answers this for itself."
        )
        a("")
    a(
        "Caveat on all of these: `pg_stat_wal` and the checkpoint counters are "
        "cluster-wide. This is a dedicated development database with no concurrent "
        "workload, so a delta is attributable to the load that bracketed it; on a "
        "shared cluster it would not be."
    )
    a("")

    # ---------------------------------------------------------------- storage
    a("## Storage")
    a("")
    a(
        "**Allocated sizes, not live.** `VACUUM FULL` was not run: it takes an ACCESS "
        "EXCLUSIVE lock and rewrites the table, which is not something to do to a "
        "multi-gigabyte relation for a report. The dead-tuple column is what makes "
        "these numbers interpretable — a relation with few dead tuples has allocated "
        "and live size near-identical, and one with many does not."
    )
    a("")
    a(
        "**Zero dead tuples does not mean allocated equals live.** A relation only "
        "shrinks when it is rewritten; pages freed earlier stay allocated and are "
        "reused, and autovacuum resets the dead-tuple counter once it has marked that "
        "space reusable. So a low count says space is *available for reuse*, not that "
        "none is being held. These totals are upper bounds on live size, and by how "
        "much is not measured here."
    )
    a("")
    bloated = {n: s for n, s in storage.items() if s.rows and s.dead_tuples > s.rows * 0.1}
    if bloated:
        names = ", ".join(f"`{n}`" for n in bloated)
        a(
            f"{names} additionally carries visible churn from the insert-and-delete "
            "cycle in `seq2lead profile wal`, so its totals are inflated well beyond "
            "whatever the general caveat above amounts to."
        )
        a("")
    else:
        a(
            "`raw_record` is known to be inflated regardless of what its dead-tuple "
            "count currently reads: `seq2lead profile wal` inserted ~3.18M rows into it "
            "under a scratch release and deleted them again. Those pages remain "
            "allocated. Its total and B/row figures are therefore **not** a per-row "
            "storage estimate for the mapping artifacts. `VACUUM FULL` would quantify "
            "the gap but was deliberately not run."
        )
        a("")
    a("| Relation | Rows | Heap | TOAST | Indexes | Total | B/row | Dead tuples |")
    a("| --- | --- | --- | --- | --- | --- | --- | --- |")
    for name, s in storage.items():
        a(
            f"| `{name}` | {s.rows:,} | {_mib(s.heap_main)} | {_mib(s.toast)} | "
            f"{_mib(s.indexes)} | **{_gib(s.total)}** | {s.per_row(s.total):,.0f} | "
            f"{s.dead_tuples:,} |"
        )
    a("")

    a("### Index decisions")
    a("")
    a(
        "The pilot built a `jsonb_path_ops` GIN across the whole payload. **That index "
        "is not built on the full release.** Its size scales with distinct key/value "
        "pairs rather than row count, and no query M3 issues needs it: M3 reads the "
        "measurement table sequentially to build the curated layer, and its one keyed "
        "lookup is the assay join. What is built instead is targeted at that join:"
    )
    a("")
    a("| Index | Serves |")
    a("| --- | --- |")
    a("| `raw_measurement_reactant_set_idx` | measurement -> rsid_eaids |")
    a("| `raw_record_reactant_set_idx` | rsid_eaids lookup by REACTANT_SET_ID |")
    a("| `raw_record_entry_assay_idx` | assays lookup by (ENTRYID, ASSAYID) |")
    a("")

    # ---------------------------------------------------------------- deviations
    a("## Deviations from the pilot's predictions")
    a("")
    if measurements is not None:
        s = storage.get("raw_measurement")
        a("| Quantity | Pilot predicted | M2 measured | Deviation |")
        a("| --- | --- | --- | --- |")
        ratio = measurements.report.member_bytes / measurements.download.archive_bytes
        member_gib = measurements.report.member_bytes / 1024**3
        member_dev = member_gib / PILOT_UNCOMPRESSED_PREDICTION_GIB - 1
        a(
            f"| Uncompressed TSV | {PILOT_UNCOMPRESSED_PREDICTION_GIB:.2f} GiB | "
            f"{_gib(measurements.report.member_bytes)} | {member_dev:+.0%} |"
        )
        a(f"| Compression ratio | 12.08x | {ratio:.2f}x | — |")
        if s is not None:
            measured_gib = s.total / 1024**3
            a(
                f"| `raw_measurement` total | {PILOT_PREDICTED_TOTAL_GIB:.2f} GiB | "
                f"{_gib(s.total)} | {measured_gib / PILOT_PREDICTED_TOTAL_GIB - 1:+.0%} |"
            )
            a(
                f"| Bytes per row | {PILOT_BYTES_PER_ROW:,} B | "
                f"{s.per_row(s.total):,.0f} B | "
                f"{s.per_row(s.total) / PILOT_BYTES_PER_ROW - 1:+.0%} |"
            )
        if measurements.total_seconds:
            a(
                f"| End-to-end ingest | {_hms(PILOT_PREDICTED_INGEST_SECONDS)} | "
                f"{_hms(measurements.total_seconds)} | "
                f"{measurements.total_seconds / PILOT_PREDICTED_INGEST_SECONDS - 1:+.0%} |"
            )
        a("")
        a(
            "The pilot flagged these as **low-confidence underestimates**, reasoning "
            "that the PDSP subset is denser in repeated sequence text and sparser in "
            "cross-references than the full release. That caveat was **right on "
            "storage and wrong on time**: uncompressed size and bytes/row both came in "
            "above the prediction, but the load ran faster than predicted, not slower. "
            "Extrapolating a COPY rate from a subset that fits in cache was supposed "
            "to be optimistic; in practice the full file compressed better (15.14x vs "
            "12.08x) and streamed more efficiently than the pilot's small archive."
        )
        a("")
        a(
            "One caveat on the storage rows: `raw_measurement` holds the pilot's "
            "27,715 rows as well, so its total and bytes/row are computed over "
            f"{(storage['raw_measurement'].rows if 'raw_measurement' in storage else 0):,} "
            "rows rather than the full release alone. The effect is well under one "
            "percent and does not change the direction of the deviation."
        )
        a("")

    # ---------------------------------------------------------------- open issues
    a("## Unresolved issues")
    a("")
    a("| # | Issue |")
    a("| --- | --- |")
    a(
        "| 1 | `BindingDBTargetSequences.fasta` has no versioned filename and no "
        "publisher md5. Coherence with 202609 rests on its `Last-Modified` date plus "
        "our frozen digest. If BindingDB rotates it, the pin will fail loudly — which "
        "is the intended behaviour, but it will need a new manifest entry. |"
    )
    a(
        "| 2 | The UCSD citable archive's per-file 202609 artifacts were not resolvable "
        "programmatically, so the pins are on live downloads. Fine for reproducibility, "
        "weaker for long-term citation. |"
    )
    a(
        "| 3 | Assay `DESCRIPTION` carries HTML entities (`&#181;` for micro, and "
        "probably markup elsewhere). The raw layer keeps them verbatim; M3 must decide "
        "on decoding and record that decision. |"
    )
    a(
        "| 4 | The FASTA record count does not equal BindingDB's stated target count. "
        "Both numbers are recorded; which entities each counts is an M3 question, and "
        "the sequences are keyed by their own identifiers until then. |"
    )
    a(
        "| 5 | Auxiliary artifacts share one `raw_record` table, so its expression "
        "indexes span rows from both. Harmless at this size, but if M3's joins turn out "
        "to be slow, per-artifact partial indexes are the fix. |"
    )
    a(
        "| 6 | **Six records in the published measurement file are corrupt** and cannot "
        "be recovered from it — five empty lines and a 3,118-byte NUL run. Whatever "
        "measurements they held are simply absent, here and from anyone else's copy of "
        "this release. Worth reporting upstream. The loss is 6 of 3,237,052 records, so "
        "it does not threaten the benchmark, but it should be stated rather than "
        "rounded away. |"
    )
    a(
        "| 7 | **The full measurement load's WAL and checkpoint cost was never "
        "captured** and cannot be recovered, since the release is immutable. The figures "
        "in `wal_probe.md` come from a smaller artifact and do not substitute for it. "
        "Whether `max_wal_size` needs raising for a load of that size is therefore an "
        "open question, not a settled recommendation; migration 0003 means the next "
        "large load will record its own answer. |"
    )
    a("")

    # ---------------------------------------------------------------- verification
    a("## Verification")
    a("")
    a("Exact commands and their results:")
    a("")
    a("```")
    for command, result in commands:
        a(f"$ {command}")
        a(result)
    a("```")
    a("")
    return "\n".join(lines) + "\n"


def write(content: str, path: Path = REPORT_PATH) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path
