# M2 — full raw ingest

Generated 2026-09-29 09:41 UTC.

Scope: the pinned BindingDB 202609 measurement TSV, the two mapping files needed to reach assay text, and the target-sequence FASTA, loaded verbatim into the immutable raw layer. **No curation, no model, no embedding cache, and no `VACUUM FULL`.**

## Artifacts

| Subset | File | Bytes | SHA-256 | MD5 vs publisher | Pinned |
| --- | --- | --- | --- | --- | --- |
| `all` | `BindingDB_All_202609_tsv.zip` | 593,578,299 | `4c04e0fec46fadab8a48…` | yes | yes |
| `assays` | `BindingDB_Assays_202609_tsv.zip` | 10,095,267 | `b9949b6271de7a3d2e42…` | yes | yes |
| `rsid_eaids` | `BindingDB_rsid_eaids_202609_tsv.zip` | 7,541,165 | `0c967f8a34ab21d766a5…` | yes | yes |
| `target_sequences` | `BindingDBTargetSequences.fasta` | 7,649,780 | `811507d7d61175a31b0f…` | none published | yes |

Full digests, for the manifest and any later audit:

- `all` — `4c04e0fec46fadab8a48465a7ac2344cf4e2b3887c6b69acc968676130b73e16`
  - https://www.bindingdb.org/rwd/bind/downloads/BindingDB_All_202609_tsv.zip
- `assays` — `b9949b6271de7a3d2e4269498fffc9a5374b0e45d72202c4c347d798876b345e`
  - https://www.bindingdb.org/rwd/bind/downloads/BindingDB_Assays_202609_tsv.zip
- `rsid_eaids` — `0c967f8a34ab21d766a5e9ca327a02ad45970321f691816f1d27109ef79c7cf9`
  - https://www.bindingdb.org/rwd/bind/downloads/BindingDB_rsid_eaids_202609_tsv.zip
- `target_sequences` — `811507d7d61175a31b0f298ac8848c37e0d7058652af68fb5e6724f06fd261b5`
  - https://www.bindingdb.org/rwd/bind/BindingDBTargetSequences.fasta

**Release coherence.** The measurement, assay and mapping archives all carry `202609` in the filename. `BindingDBTargetSequences.fasta` does not — it is a rolling file at a different base path (`/rwd/bind/`, not `/rwd/bind/downloads/`) and has no publisher md5. Its `Last-Modified` was 2026-08-30, the same date as the 202609 archives, which is the only evidence tying it to this release; the frozen SHA-256 is what makes that binding reproducible. Recorded rather than assumed away.

**Citable archive.** BindingDB recommends the UCSD Library quarterly deposit (`library.ucsd.edu/dc/collection/bb03870458`) for a referenceable version. Its per-file 202609 artifacts were not resolvable programmatically, so these loads use the live downloads pinned by observed SHA-256. The archive remains the citation of record; the digests above are what make the bytes verifiable.

## Schemas as found in the files

**`all`** — `BindingDB_All.tsv`, 640 columns, 8,568.5 MiB uncompressed.

**`assays`** — `BindingDB_Assays.tsv`, 4 columns, 45.2 MiB uncompressed.

**`rsid_eaids`** — `BindingDB_rsid_eaids.tsv`, 2 columns, 53.0 MiB uncompressed.

**`target_sequences`** — FASTA, 11,527 records.

Headers look like `>p1 mol:protein length:376 Thymidine kinase`.

**Join path to assay text, confirmed from the files.** Assay descriptions are not in the measurement TSV. Reaching them is a two-hop join:

```
raw_measurement.payload->>'BindingDB Reactant_set_id'
    -> raw_record.payload->>'REACTANT_SET_ID'      (rsid_eaids)
       raw_record.payload->>'ENTRYID_ASSAYID'      e.g. '285_1'
    -> raw_record.payload->>'ENTRYID' = '285'      (assays)
       AND raw_record.payload->>'ASSAYID' = '1'
       -> ASSAY_NAME, DESCRIPTION
```

`ENTRYID_ASSAYID` is `ENTRYID` and `ASSAYID` joined by an underscore. Verified against real rows, not inferred from the column names.

## Row reconciliation

| Subset | Input records | Loaded | Quarantined | Reconciles |
| --- | --- | --- | --- | --- |
| `all` | 3,237,052 | 3,237,046 | 6 | yes |
| `assays` | 224,430 | 224,430 | 0 | yes |
| `rsid_eaids` | 3,179,005 | 3,179,005 | 0 | yes |
| `target_sequences` | 11,527 | 11,527 | 0 | yes |

Quarantined records by rule, with original bytes preserved:

| Subset | Rule | Records |
| --- | --- | --- |
| `all` | `field_count_mismatch` | 6 |

#### `all` — what the quarantined records actually are

| Line | Rule | Bytes | Content |
| --- | --- | --- | --- |
| 411,915 | `field_count_mismatch` | 0 | empty line |
| 413,387 | `field_count_mismatch` | 3,118 | **all NUL bytes** |
| 413,921 | `field_count_mismatch` | 0 | empty line |
| 415,877 | `field_count_mismatch` | 0 | empty line |
| 416,139 | `field_count_mismatch` | 0 | empty line |
| 417,673 | `field_count_mismatch` | 0 | empty line |

**This is corruption in the published artifact, not in our download.** The archive's MD5 matches the publisher's, so the damaged bytes are what BindingDB distributes. All 6 bad records sit in one narrow region (lines 411,915–417,673 of 3,237,052), and one of them is a 3,118-byte run containing nothing but NUL — the signature of a partially written block rather than a malformed row. The lines immediately either side parse normally, so the damage is localised.

**Correction to an earlier revision of this report.** It claimed that a parser using `errors="replace"` would have turned the NUL run into replacement characters, and that the old 8 KB truncation would have clipped it. Both were wrong. A NUL byte is valid UTF-8, so the decode succeeded and produced a 3,118-character string; `errors="replace"` would have changed nothing. And 3,118 bytes is well under 8,192, so no truncation would have applied either.

What is actually true, and still useful: all six records are preserved byte-for-byte in `ingest_exclusion.raw_bytes`, and all six are classified by the same rule, `field_count_mismatch` — the NUL run decoded cleanly and contained no tab, so it presented as a single field against the expected 640, exactly like an empty line does. **The rule code alone cannot distinguish a blank line from a 3 KB corrupt block.** Only the retained bytes and `byte_length` can, which is how the table above was built. The `invalid_utf8` rule has never fired on real data and is exercised only by fixtures.

### Against the publisher's stated count

Two comparisons are possible and they differ, so both are given. The input-line count is what the file contains; the loaded-row count is what survived quarantine.

| | Count | vs published |
| --- | --- | --- |
| BindingDB states for 202609 | 3,241,782 | — |
| Input data lines in the TSV | 3,237,052 | -4,730 |
| Rows loaded | 3,237,046 | -4,736 |

The shortfall is 4,736 rows, 0.146% of the published figure.

What it is **not**:

- Not records lost in ingest. Every one of the 3,237,052 data lines in the file is accounted for as loaded or quarantined, and only 6 were quarantined.
- Not duplicate rows inflating or deflating the count. `BindingDB Reactant_set_id` is distinct on every row (3,237,046 distinct values over 3,237,046 rows), so rows and reactant sets are one-to-one here.

**The reason for the discrepancy is unknown.** It would be easy to say the page's headline is regenerated on a different schedule, or that it counts measurements in some internal sense rather than export rows — but neither has been checked against anything, so both are hypotheses, not findings. What *is* established is that the gap did not arise here: the file's own lines reconcile exactly, and the artifact matches its SHA-256. Resolving it would mean asking BindingDB what the headline counts, which has not been done.

## Performance

| Subset | COPY | rows/s | Indexes | Exclusions | End-to-end |
| --- | --- | --- | --- | --- | --- |
| `all` | 2.4 min | 22,443 | 16.3 s | 0.0 s | 2.7 min |
| `assays` | 1.7 s | 134,707 | 0.0 s | 0.0 s | 1.7 s |
| `rsid_eaids` | 13.4 s | 236,769 | 1.9 s | 0.0 s | 15.4 s |
| `target_sequences` | 0.1 s | 79,185 | 0.0 s | 0.0 s | 0.2 s |

### WAL and checkpoints

WAL figures are **not available** for `all`, `assays`, `rsid_eaids`, `target_sequences`. Those releases were loaded before WAL accounting was persisted on the release row (migration `0003_release_wal_accounting`), and a release is immutable, so it cannot be re-loaded to measure it. Reporting the ~0 delta of a verified no-op re-run would be a lie, so nothing is reported.

A WAL cost was measured instead on a **different and much smaller artifact** — see [`wal_probe.md`](wal_probe.md), produced by `seq2lead profile wal`, which loads `rsid_eaids` under a scratch release and removes it afterwards. That artifact has a comparable *row* count (3,179,005) but a far smaller payload: roughly 200 MiB against the measurement table's ~11 GiB, and two short columns against ~30 populated fields carrying full protein sequences.

**So the probe does not tell us what the full load did.** Its checkpoint count is a property of that load, not of the measurement load, and it must not be read across: a load writing fifty times the payload would plausibly behave differently in either direction. The full load's own counters were never captured, and the release is immutable, so the honest position is that **the measurement load's WAL and checkpoint cost is unknown**.

What the probe does establish is that bulk COPY on this cluster can generate WAL several times the size of the stored payload and can trigger requested checkpoints at all. That is worth knowing before M3 writes a comparable volume, but it is **not** evidence that `max_wal_size` must be raised for M3 — that would need measuring the load in question. Migration `0003_release_wal_accounting` now records these counters per release, so the next load answers this for itself.

Caveat on all of these: `pg_stat_wal` and the checkpoint counters are cluster-wide. This is a dedicated development database with no concurrent workload, so a delta is attributable to the load that bracketed it; on a shared cluster it would not be.

## Storage

**Allocated sizes, not live.** `VACUUM FULL` was not run: it takes an ACCESS EXCLUSIVE lock and rewrites the table, which is not something to do to a multi-gigabyte relation for a report. The dead-tuple column is what makes these numbers interpretable — a relation with few dead tuples has allocated and live size near-identical, and one with many does not.

**Zero dead tuples does not mean allocated equals live.** A relation only shrinks when it is rewritten; pages freed earlier stay allocated and are reused, and autovacuum resets the dead-tuple counter once it has marked that space reusable. So a low count says space is *available for reuse*, not that none is being held. These totals are upper bounds on live size, and by how much is not measured here.

`raw_record` is known to be inflated regardless of what its dead-tuple count currently reads: `seq2lead profile wal` inserted ~3.18M rows into it under a scratch release and deleted them again. Those pages remain allocated. Its total and B/row figures are therefore **not** a per-row storage estimate for the mapping artifacts. `VACUUM FULL` would quantify the gap but was deliberately not run.

| Relation | Rows | Heap | TOAST | Indexes | Total | B/row | Dead tuples |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `raw_measurement` | 3,264,761 | 536.9 MiB | 10,435.5 MiB | 252.3 MiB | **10.96 GiB** | 3,605 | 444 |
| `raw_record` | 3,403,435 | 445.2 MiB | 0.6 MiB | 771.1 MiB | **1.19 GiB** | 375 | 0 |
| `raw_sequence` | 11,527 | 7.6 MiB | 1.2 MiB | 0.6 MiB | **0.01 GiB** | 860 | 0 |

### Index decisions

The pilot built a `jsonb_path_ops` GIN across the whole payload. **That index is not built on the full release.** Its size scales with distinct key/value pairs rather than row count, and no query M3 issues needs it: M3 reads the measurement table sequentially to build the curated layer, and its one keyed lookup is the assay join. What is built instead is targeted at that join:

| Index | Serves |
| --- | --- |
| `raw_measurement_reactant_set_idx` | measurement -> rsid_eaids |
| `raw_record_reactant_set_idx` | rsid_eaids lookup by REACTANT_SET_ID |
| `raw_record_entry_assay_idx` | assays lookup by (ENTRYID, ASSAYID) |

## Deviations from the pilot's predictions

| Quantity | Pilot predicted | M2 measured | Deviation |
| --- | --- | --- | --- |
| Uncompressed TSV | 6.68 GiB | 8.37 GiB | +25% |
| Compression ratio | 12.08x | 15.14x | — |
| `raw_measurement` total | 10.21 GiB | 10.96 GiB | +7% |
| Bytes per row | 3,381 B | 3,605 B | +7% |
| End-to-end ingest | 3.7 min | 2.7 min | -28% |

The pilot flagged these as **low-confidence underestimates**, reasoning that the PDSP subset is denser in repeated sequence text and sparser in cross-references than the full release. That caveat was **right on storage and wrong on time**: uncompressed size and bytes/row both came in above the prediction, but the load ran faster than predicted, not slower. Extrapolating a COPY rate from a subset that fits in cache was supposed to be optimistic; in practice the full file compressed better (15.14x vs 12.08x) and streamed more efficiently than the pilot's small archive.

One caveat on the storage rows: `raw_measurement` holds the pilot's 27,715 rows as well, so its total and bytes/row are computed over 3,264,761 rows rather than the full release alone. The effect is well under one percent and does not change the direction of the deviation.

## Unresolved issues

| # | Issue |
| --- | --- |
| 1 | `BindingDBTargetSequences.fasta` has no versioned filename and no publisher md5. Coherence with 202609 rests on its `Last-Modified` date plus our frozen digest. If BindingDB rotates it, the pin will fail loudly — which is the intended behaviour, but it will need a new manifest entry. |
| 2 | The UCSD citable archive's per-file 202609 artifacts were not resolvable programmatically, so the pins are on live downloads. Fine for reproducibility, weaker for long-term citation. |
| 3 | Assay `DESCRIPTION` carries HTML entities (`&#181;` for micro, and probably markup elsewhere). The raw layer keeps them verbatim; M3 must decide on decoding and record that decision. |
| 4 | The FASTA record count does not equal BindingDB's stated target count. Both numbers are recorded; which entities each counts is an M3 question, and the sequences are keyed by their own identifiers until then. |
| 5 | Auxiliary artifacts share one `raw_record` table, so its expression indexes span rows from both. Harmless at this size, but if M3's joins turn out to be slow, per-artifact partial indexes are the fix. |
| 6 | **Six records in the published measurement file are corrupt** and cannot be recovered from it — five empty lines and a 3,118-byte NUL run. Whatever measurements they held are simply absent, here and from anyone else's copy of this release. Worth reporting upstream. The loss is 6 of 3,237,052 records, so it does not threaten the benchmark, but it should be stated rather than rounded away. |
| 7 | **The full measurement load's WAL and checkpoint cost was never captured** and cannot be recovered, since the release is immutable. The figures in `wal_probe.md` come from a smaller artifact and do not substitute for it. Whether `max_wal_size` needs raising for a load of that size is therefore an open question, not a settled recommendation; migration 0003 means the next large load will record its own answer. |

## Verification

Exact commands and their results:

```
$ uv run ruff check .
All checks passed!
$ uv run ruff format --check .
32 files already formatted
$ uv run pytest
58 passed in 187.69s (0:03:07)
```

