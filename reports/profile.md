# M1 — bounded pilot profile

Generated 2026-09-28 20:19 UTC on the M1 pilot subset.

**Scope.** Everything under *Measured* was observed on `BindingDB_PDSPKi_202609_tsv.zip`. Everything under *Extrapolated* is arithmetic on those measurements and is explicitly uncertain — the pilot is a Ki-only, single-curator subset and is not a random sample of BindingDB.

## Provenance

| Field | Value |
| --- | --- |
| Source | BindingDB 202609, subset `pdspki` |
| URL | `https://www.bindingdb.org/rwd/bind/downloads/BindingDB_PDSPKi_202609_tsv.zip` |
| Archive member | `BindingDB_PDSPKi.tsv` |
| SHA-256 (computed) | `5a212e99f495e5c11befb09baba264435201dfced1cada926be13df73f971ac3` |
| SHA-256 pinned in manifest | **yes** — checked before any database write |
| MD5 (computed) | `11070acf5be6b5f316c1379c64b84969` |
| MD5 matched publisher | yes |
| Archive bytes | 5,590,095 (5.3 MiB) |
| Uncompressed bytes | 67,525,039 (64.4 MiB) |
| Compression ratio | 12.08× |
| License | CC-BY-3.0 (BindingDB-curated); CC-BY-SA-3.0 (ChEMBL-derived rows) |

The two checksums answer different questions. The **SHA-256 pin lives in the source manifest** and is what fixes the dataset version: if upstream republishes a file under the same name, only this catches it. The publisher's **MD5 attests transfer integrity only** — it is re-published beside the archive, so it moves with any replacement.

## Measured — ingest

| Metric | Value |
| --- | --- |
| TSV columns | 640 |
| Data lines | 27,715 |
| Rows loaded | 27,715 |
| Rows excluded | 0 |
| **Reconciles** (loaded + excluded = data lines) | **yes** |
| COPY wall time | 1.5 s |
| **COPY rate** | **18,391 rows/s** |
| Exclusion write time | 0.0 s |
| Index build time | 0.3 s |
| **Complete ingest (COPY + indexes + commit)** | **1.9 s** |
| Effective end-to-end rate | 14,621 rows/s |

### Index build time

| Index | Seconds |
| --- | --- |
| `raw_measurement_payload_gin` | 0.25 |
| `raw_measurement_reactant_set_idx` | 0.08 |
| **total** | **0.33** |

Indexes are dropped before the COPY and rebuilt after it, so these are real build times rather than a no-op against indexes left from a prior run. **COPY rate is not ingest rate** — the row above marked *complete ingest* is the number to plan with.

## Measured — storage

`raw_measurement` after `VACUUM FULL`, so these are **live** bytes. PostgreSQL does not return allocated pages to the filesystem when rows are deleted, so an un-compacted relation reports its high-water mark.

| Component | Total | Per row |
| --- | --- | --- |
| Heap (main fork) | 6.4 MiB | 241 B |
| TOAST (out-of-line values) | 74.5 MiB | 2,820 B |
| Indexes | 8.5 MiB | 320 B |
| **Total** | **89.4 MiB** | **3,381 B** |

| Tuple state | Value |
| --- | --- |
| Rows | 27,715 |
| Live tuples | 27,715 |
| Dead tuples | 0 |
| Lifetime inserts | 27,760 |
| Lifetime deletes | 42 |
| Allocated before compaction | 89.4 MiB |
| Change after `VACUUM FULL` | 0.0 MiB (+0%) |
| `VACUUM FULL` wall time | 1.1 s |

Compaction reclaimed nothing here, which is the expected result and the point of the immutability change: the release was loaded exactly once, so there are no dead tuples to remove. The small size difference is the index rebuild — a GIN index built in one pass over a full table does not come out byte-identical to the one built incrementally during the load.

**TOAST dominates: 2,820 B/row against 241 B in the heap.** Each row carries its target's full amino-acid sequence inline and the pilot has only 747 distinct sequences across 27,715 rows. The raw layer stores that redundancy deliberately — it is a record of the source file — but it means bytes/row is driven by repeated sequence text, not field count. The curated layer normalizes sequences into `target` at M3, so curated storage will not scale from this number.

## Measured — what the data looks like

| Metric | Value |
| --- | --- |
| Rows | 27,715 |
| Columns ever populated | 53 of 640 (8.3%) |
| Mean populated fields per row | 29.5 |
| Distinct chain-1 sequences | 747 |
| Distinct ligand InChIKeys | 2,990 |
| Distinct target names | 400 |
| Single-chain rows | 27,694 (99.9%) |
| Ki present | 27,715 |
| Ki exact (`=`) | 22,213 (80.1%) |
| Ki right-censored (`>`) | 5,502 (19.9%) |
| Ki left-censored (`<`) | 0 |
| `Date of publication` populated | 27,715 |
| `Date in BindingDB` populated | 27,715 |
| `pH` populated | 0 |
| `Temp (C)` populated | 0 |

## Measured — ESM-2 throughput

| Metric | Value |
| --- | --- |
| Model | `facebook/esm2_t33_650M_UR50D` |
| Device | mps |
| Dtype | float32 |
| Embedding dim | 1280 |
| Model load time | 3.5 s |
| Sequences embedded | 24 |
| Sequences truncated at 1022 residues | 2 |
| Residues processed | 11,390 |
| Wall time | 7.3 s |
| **Throughput** | **3.30 seq/s** |
| Throughput | 1,566 residues/s |
| Median per sequence | 0.266 s |

Batch size 1, mean-pooled over real residues. Sequences were sampled evenly across the length distribution rather than at random, so this includes the long tail.

## Open decision — M7 long-sequence policy

Distinct chain-1 sequences in the pilot range 11–4303 residues (median 424), and **52 of 747 exceed the 1022-residue limit**.

In this benchmark run, 2 of the sampled proteins were silently truncated to fit. That is acceptable for a timing measurement and **not** acceptable for a cached embedding.

**Decision required before M7 writes any cache entry.** Choose one:

1. **Exclude** proteins over the limit from the benchmark, and report how many.
2. **Chunk** with overlapping windows and pool across windows.
3. **Truncate**, but only if the cache key says so.

Whichever is chosen, the cache key must carry the **model name, the resolved model/tokenizer revision, the pooling rule, and a truncation flag with the effective residue count**. A truncated embedding must never be stored under an unqualified full-sequence identity: the `sequence_sha256` alone would claim to represent a protein the model never saw in full, and every downstream result computed from it would silently inherit that claim.

## Extrapolated — and why each number may be wrong

Scaling factor: the full 202609 release holds 3,241,782 measurements against this pilot's 27,715 rows — **117.0×**.

| Quantity | Extrapolation | Confidence |
| --- | --- | --- |
| Uncompressed full TSV | 6.68 GiB | Moderate. Compression ratio should hold; the full file has more populated columns per row, so this is a **lower bound**. |
| `raw_measurement` total (live) | 10.21 GiB | **Low — likely an underestimate.** Driven by TOASTed sequence text. The full release has more distinct sequences and more multi-chain rows, and TOAST compresses per value, not across rows. |
| Complete ingest | 3.7 min | **Low.** COPY is roughly linear but index build is not, and the pilot fits in cache while the full load will contend with WAL and checkpoints. |
| ESM-2 over 11,509 targets | 58.1 min | Moderate. One-time and cached. Depends on the full release's length distribution, which the pilot need not match. |

> These replace the plan's §7 guesses for the quantities listed. Anything not in this table is still an estimate.

## Correction to the previous revision

An earlier version of this report gave 8,795 B/row and extrapolated 26.55 GiB. That was measured on a relation loaded and deleted six times by the old re-ingest behaviour — `n_tup_ins` read 166,344 against 27,715 live rows — and `pg_total_relation_size` counts allocated pages whether or not anything lives in them. Autovacuum had already marked the space reusable, so the dead-tuple count read zero while the files stayed at their high-water mark, which is why the number looked trustworthy.

The bloat was still growing: the reviewed report read 8,795 B/row, and a direct measurement taken a few profile runs later read 9,664 B/row on the same 27,715 live rows. Each run added another delete-and-reload cycle, so the figure drifted upward with no change to the data.

Measured directly on that bloated relation before the fix:

| | Allocated | Per row |
| --- | --- | --- |
| Before `VACUUM FULL` | 255.4 MiB | 9,664 B |
| After `VACUUM FULL` | 89.4 MiB | 3,382 B |
| Reclaimed | 166.0 MiB | **65%** |

So the true figure is **3,381 B/row**, and the earlier one overstated it by 2.6x. The extrapolation falls from 26.55 GiB to roughly 10 GiB.

Two changes prevent a recurrence: **raw releases are now immutable**, so re-running the pilot is a verified no-op instead of a delete-and-reload; and **this report measures a compacted relation** and prints the live/dead breakdown rather than a single total.

## What the pilot cannot tell us

The PDSP Ki subset is **not** a miniature BindingDB. It is one curator (`Curation/DataSource` is uniformly `PDSP Ki`), one measurement type, and one assay family. Specifically it cannot exercise:

- **The no-merging rule.** IC50, Kd and EC50 are entirely absent here, so the
  separation of label spaces is untested against real data.
- **Mixed licensing.** Every row is from one source, so the per-row CC-BY vs
  CC-BY-SA resolution has nothing to resolve.
- **Left-censoring.** No `<` values occur, so only the `>` branch of the
  interval logic has seen real input.
- **Malformed input.** Zero lines were ragged and the whole file decodes as
  strict UTF-8, so the quarantine paths are exercised only by fixtures.

## Open items before M2

| # | Item | Status |
| --- | --- | --- |
| 1 | 640-column sparse layout | **Resolved.** `payload` stores non-empty fields only; the full header on the release makes it exactly reversible. |
| 2 | Exclusions held in memory | **Resolved.** Spooled to a temporary file during COPY, then inserted in batches of 5,000. |
| 3 | Full-table `LATERAL jsonb_object_keys` | **Resolved.** Field counts are accumulated during ingest into `source_release.field_total`. |
| 4 | One transaction for the whole load | **Open.** All-or-nothing is right and the tests pin it, but a 3.2M-row transaction is a large WAL burst. Measure at M2; if it must be chunked, keep atomicity at the release level. |
| 5 | Unconditional GIN index on `payload` | **Open.** Size scales with distinct key/value pairs. Make it optional and justify it against the queries M3 actually issues. |
| 6 | Full-release SHA-256 not yet known | **Open by design.** The manifest entry for `all` has `expected_sha256=None` rather than a guess. Observe the digest on first download, record it, then ingest. |
| 7 | Header differences between subsets | **Resolved.** Validation checks uniqueness and required columns by name; it never requires equality with the pilot header. |

