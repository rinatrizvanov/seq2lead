# M11c — isolated ingest and curation of the January 2026 snapshot

**Scope: ingestion, curation and observation export only.** No snapshot matching,
no model fitting, no evaluation score, no docking. The confirmatory freeze is
**not signed** and the study remains **exploratory**. A bounded matching step is
proposed in `docs/M11.md` §10b and is not started.

Machine-readable record: `configs/manifests/m11c_isolated_ingest_202601.json`.
The acquisition record it completes, `m11_acquisition_202601.json`, is
**unchanged**.

## What deliberately did not change

| | |
| --- | --- |
| Accepted corpus (`public`) | untouched — read by `SELECT` only, and the export and statistics passes ran inside `READ ONLY` transactions so the database itself would have refused a write |
| Accepted 202609 releases | row counts unchanged: 12 `pdspki` 27,715, 90 `rsid_eaids` 3,179,005, 91 `assays` 224,430, 94 `target_sequences` 11,527, 117 `all` 3,237,046 |
| The three 202601 acquisition rows (1203, 1204, 1205) | still `ingested_at IS NULL`, `rows_loaded = 0` — the evidence that acquisition happened without parsing |
| Pinned SHA-256 values | re-verified, **not replaced**; zero downloads performed |
| M8 / M9 / M10 artifacts | not modified — read only by the verification tests, which pass unchanged |

## 1. The acquired-to-ingested transition

The loaders could not complete a release the acquisition step had registered.
Both treated *any* existing row with a matching SHA-256 as a verified no-op, so a
registered-but-unparsed release reported success and stayed at `rows_loaded = 0`
permanently. The fix distinguishes the three meanings an existing row can have, plus
the case where there is no row at all:

| State | What the row means | What the loader does |
| --- | --- | --- |
| `ABSENT` | nothing registered | insert, then load |
| `UNPARSED` | provenance only, `ingested_at IS NULL` | **complete the registered row in place**, then load |
| `INGESTED` | already loaded from these bytes | verified no-op |
| `CONFLICT` | registered from *different* bytes | refuse |

Three properties are asserted rather than assumed, in
`tests/test_m11c_release_lifecycle.py` (10 tests):

- **Release identity is preserved.** Completion is an `UPDATE` of the registered
  row, writing only the parse-derived fields. Inserting a second row would split
  one artifact across two identities, and `activity.source_release_id` would
  reference whichever one happened to be used. The test asserts
  `report.source_release_id == registered_id` *and* that only one release row
  exists afterwards.
- **Conflicting bytes are refused even when unparsed.** A registered row is
  immutable provenance, not a draft to overwrite.
- **Completion is transactional.** A load that fails partway leaves no ingest
  marker, no rows, and the registered provenance intact. Verified by injecting a
  failure, not by reading the code.

Both loaders — the measurement loader (`ingest/bindingdb.py`) and the auxiliary
TSV/FASTA loader (`ingest/auxiliary.py`) — go through the same decision and are
each tested against a pre-registered unparsed release.

## 2. Schema isolation, proved before writing

Everything was loaded into `seq2lead_m11c_202601`. Before any write,
`is_fully_shadowed()` was called and returned an **empty list**: every table the
migrations create resolves to the isolated schema rather than falling through to
the corpus. `public` stays on the search path only because extensions live there.

This is a derived check, not a maintained list — it compares against the corpus
schema's actual table inventory, so a migration that adds a table cannot quietly
escape it.

## 3. Release identity: acquisition records and isolated releases

The same three artifacts now have two sets of ids, because `source_release.id` is
a per-schema surrogate. **What ties them together is the artifact identity triple
plus the SHA-256, not the integers.**

| Subset | Artifact identity | Acquisition id (`public`) | Isolated id | SHA-256 matches | Rows |
| --- | --- | ---: | ---: | --- | ---: |
| `assays` | `BindingDB/202601/assays` | 1204 (unparsed) | 1 | **yes** | 218,973 |
| `rsid_eaids` | `BindingDB/202601/rsid_eaids` | 1205 (unparsed) | 2 | **yes** | 3,107,915 |
| `all` | `BindingDB/202601/all` | 1203 (unparsed) | 3 | **yes** | 3,140,596 |

The isolated ids are not in acquisition order: `assays` and `rsid_eaids` are
loaded first because the assay-context join must exist before the measurement
rows that reference it. The acquisition rows keep their original ids and their
unparsed state.

**Why not complete the acquisition rows themselves?** Because that would have
written 3.1M measurement rows and a second curated corpus into the accepted
schema. The loader *can* now complete them — that is §1 — and the isolated
schema is where it was exercised. The acquisition rows stay as the historical
record.

## 4. Headers and ingest

Checksums are handled with explicit algorithms throughout. The archive publishes
**SHA-1**; our pipeline pins **SHA-256**. The legacy `source_release.md5` column
is `NOT NULL` and predates this source, so it holds the archive's SHA-1 prefixed
`sha1:` with `md5_verified = false`. The prefix is what stops a 40-character hex
string being read as a 32-character MD5.

The distinction has to be visible in the value rather than inferred from the
release, because the same column means different things across rows: of the five
202609 rows, **four carry a real published MD5 with `md5_verified = true`**, and
the fifth (release 94, the target FASTA) holds an MD5-shaped value with
`md5_verified = false`. So neither the column name nor the flag alone tells a
reader which algorithm produced a given string.

All three archives' pinned SHA-256 were recomputed from the local files and matched
before loading. No download occurred and no pin was altered.

**A defect found while verifying this, and corrected.** The isolated ingest driver
wrote the placeholder string `sha1-not-md5` into that column instead of the digest.
The acquisition rows had it right; the isolated rows recorded only that *a* SHA-1
existed, for the three rows that provide provenance to 3.1M measurements. Both
digests were then recomputed from the local archives — SHA-256 checked against each
release row, SHA-1 against the acquisition record — and the correct
`sha1:<digest>` values written, with `md5_verified` left false. All three now
verify, and none is readable as an MD5. Recorded under `checksums` in the manifest.

This was caught by querying the column rather than by trusting the ingest
script's own summary, which had reported success.

### Required columns and the comparison to 202609

| Artifact | Columns | Missing required | vs 202609 |
| --- | ---: | --- | --- |
| `all` | 640 | none | 640 → 640, **0 added, 0 removed, order identical** |
| `assays` | 4 | none | 4 → 4, identical |
| `rsid_eaids` | 2 | none | 2 → 2, identical |

Headers are byte-identical between the two snapshots, **including column order**.
This matters beyond tidiness: the matcher builds a slot out of named columns, so a
reordering or rename between snapshots would have shifted slot construction and
made the diff measure our parsing rather than BindingDB's revisions.

### Row reconciliation

Every source row is either loaded or quarantined with its original bytes
preserved. Loaded plus quarantined is reconciled against input records per
artifact:

| Artifact | Input records | Loaded | Quarantined | Reconciles |
| --- | ---: | ---: | ---: | --- |
| `assays` | 218,973 | 218,973 | 0 | **yes** |
| `rsid_eaids` | 3,107,915 | 3,107,915 | 0 | **yes** |
| `all` | 3,140,596 | 3,140,596 | 0 | **yes** |

Zero quarantined rows on all three, confirmed two ways: `ingest_exclusion` is empty
for all three isolated releases, and `data_lines = rows_loaded + rows_excluded` holds
for each. Both were checked by query rather than taken from the ingest script's own
summary — which had also reported success while writing a placeholder into the
checksum column (above).

**January's clean result means the quarantine path never fired, so it is only
demonstrable on September**, which quarantined 6 rows of 3,237,052 for
`field_count_mismatch`. Those six store their original bytes with declared length
equal to stored length — five are genuinely zero-length lines and one is 3,118 bytes
— so the bytes are preserved untruncated and a ragged row remains recoverable.
Reporting the mechanism as verified on January alone would have been reporting an
untaken code path as evidence.

Growth to September, measured rather than inferred from rounded prose:

| Artifact | 202601 | 202609 | Change |
| --- | ---: | ---: | ---: |
| `all` | 3,140,596 | 3,237,046 | **+96,450** |
| `assays` | 218,973 | 224,430 | +5,457 |
| `rsid_eaids` | 3,107,915 | 3,179,005 | +71,090 |

## 5. Curation under the pinned policy

Curator version `m3/v3+rdkit-2026.03.6/cleanup+fragment-parent+uncharge/v1` — the
same policy and the same RDKit as the accepted corpus, so an apparent new compound
in the diff cannot be a standardisation artifact.

Measurement types, censoring operators and source structures are preserved by
construction: `activity` keeps `relation`, `relation_raw` and `bound_inclusive`
separately, so `>` and `>=` remain distinguishable — the distinction that decides
whether a bound at exactly the threshold can label a pair at all — and
`compound_source` keeps each source SMILES beside the standardized parent, so the
transform stays inspectable.

**Stereochemistry survives standardization, measured rather than assumed.** Over a
20,000-row sample of source SMILES carrying a stereocentre marker, **19,970
(99.85%)** still carry one after `cleanup + fragment-parent + uncharge`, and 33
carry the no-stereo InChIKey block. Double-bond geometry survives too — `/C=C/`
appears in the canonical form. The ~0.15% that lose a marker are consistent with
fragment-parent selection discarding a stereo-bearing counterion or canonicalization
removing a redundant centre; this report does not claim which, only that the loss is
small and bounded. Sampled, and said so: there are 1,373,204 curated compounds.

| | Count |
| --- | ---: |
| Raw measurement rows | 3,140,596 |
| Curated (≥1 activity) | 3,130,970 |
| Excluded with a rule code | 9,626 |
| **curated + excluded** | **3,140,596** |
| **Reconciles** | **yes** |
| `activity` rows written | 3,136,836 |

`activity` exceeds the curated row count because a row carrying more than one
affinity type yields one row per type.

### Exclusions by rule, both snapshots

| Rule | 202601 | 202609 |
| --- | ---: | ---: |
| `invalid_smiles` | 6,575 | 5,896 |
| `no_measurement_value` | 2,941 | 2,939 |
| `missing_target_sequence` | **99** | **0** |
| `missing_structure` | 11 | 75 |
| **Total** | **9,626** | **8,910** |

**The comparison is only meaningful because both snapshots ran the identical
curator.** September's `curation_run` and all 8,910 of its exclusion rows carry
`m3/v3+rdkit-2026.03.6/cleanup+fragment-parent+uncharge/v1`, the same version
January used, and `missing_target_sequence` is a rule in that pipeline. So a rule
firing 99 times on one snapshot and 0 times on the other is a difference in the
**data**, not an artifact of a rule that only existed for one of them. That had to
be checked: had the rule been added after September was curated, the entire row
would have been an artifact of our own tooling.

Given that, two differences are worth naming rather than averaging away. January
has **99 rows whose target chain 1 sequence is empty and September has none**.
Something happened to those rows in the interval — a correction, a withdrawal, or
their replacement by different rows — and distinguishing which is exactly what the
per-slot diff does; the count alone cannot. And January excludes **679 more** rows
for invalid SMILES while excluding **64 fewer** for missing structures.

## 6. Entry DOI as entry-level provenance

BindingDB publishes two unrelated DOI columns, and the distinction is load-bearing:

| Column | Identifies | Where it lives |
| --- | --- | --- |
| `Article DOI` | the **publication** | curated into `publication.doi` |
| `BindingDB Entry DOI` | the curated **entry** a row belongs to | lifted at export time from the raw payload |

A sample row shows them side by side: entry `10.7270/Q2ZW1J3M`, article
`10.1021/jm9602571` — different registrants, different namespaces.

**It is lifted at export time, not into an `activity` column.** A curated column
was the original plan (`docs/M11.md` §10a) and was rejected for two reasons. It
would have altered the accepted September corpus's schema to add a field only the
January side could populate, when the instruction was to obtain September's
provenance read-only. And it would have let the two snapshots obtain the field by
different routes — the asymmetry that makes a diff between them unreadable.

### It is entry-level, and that is now measured

§3 of the design doc asserted that an entry DOI covers many measurements and so
cannot identify an individual one. Measured on both snapshots:

| | 202601 | 202609 |
| --- | ---: | ---: |
| Observations with an entry DOI | 3,129,830 (**99.777%**) | 3,232,193 (**99.945%**) |
| Distinct entry DOIs | 55,430 | 57,087 |
| **Observations per entry DOI** | **56.5** | **56.6** |

So agreement on an entry DOI is **necessary for a correction link, never
sufficient** — which is why the matcher asserts a link only when exactly one
removal and one addition sit at a single slot. That was a design assumption; it is
now a measurement on 6.4M real observations.

Its **cross-release stability remains unmeasured.** External registration gives it
a reason to be stable; comparing the two snapshots is what would observe it, and
that is the matching step.

## 7. Observation exports for both snapshots

The matcher does not read two schemas. It reads two exports, and that is the
point: one snapshot is accepted data, and a comparison that required rebuilding it
would risk the accepted results in order to measure a delta.

`seq2lead.asof.export` writes one gzipped JSON Lines row per curated observation.
Reproduce either side with:

```
seq2lead asof export --release-id 3   --out data/asof/observations-202601.jsonl.gz   # isolated schema
seq2lead asof export --release-id 117 --out data/asof/observations-202609.jsonl.gz   # accepted schema
```

Four properties, each tested in `tests/test_m11c_observation_export.py`:

- **Read-only, enforced by the database.** Every export runs inside a
  `SET TRANSACTION READ ONLY` transaction — including the January one, which had
  no accepted data to protect, so neither side is a special case. The test asserts
  an `INSERT` on such a connection raises `ReadOnlySqlTransaction`. A comment
  promising not to write is not evidence.
- **The matcher's full key set is supplied.** Asserted statically against
  `matching.py`, because `observation_of` degrades silently: a missing `entry_doi`
  key becomes `None`, indistinguishable from a row that genuinely has none, and a
  missing `source_release` drops the locator entirely. Neither raises.
- **Release identity survives the schema.** `source_release` is the artifact's own
  triple (`BindingDB/202601/all`), not the surrogate id. §10a had proposed the id;
  it is per-schema and means nothing elsewhere.
- **Byte-reproducible.** `mtime` and the embedded filename are pinned in the gzip
  header. Without that, two exports of identical rows differed in digest, so a
  recorded SHA-256 certified *when the file was written* rather than its contents —
  worse than recording nothing, because it looks like provenance. Confirmed by
  exporting both snapshots twice: **identical digests across independent runs.**

### The exported artifacts

| | 202601 | 202609 |
| --- | --- | --- |
| Schema read | `seq2lead_m11c_202601` | `public` (accepted), `READ ONLY` |
| Release id | 3 | 117 |
| Observations | 3,136,836 | 3,233,963 |
| Bytes (gzip) | 81,918,145 | 84,540,685 |
| SHA-256 | `0b67b64bef49fb7c…` | `2b2b4ad8b920241f…` |
| Export seconds | 110 | 96 |

Full digests are in `data/asof/export-summary-both.json`. Both row counts were
confirmed independently with `gunzip -c | wc -l`, not only from the writer's own
counter.

### The source locator points into the file, not into our database

Each row carries `source_release` (the identity triple) and `raw_row`, which is
`raw_measurement.line_no` — the line number in the distributed TSV. `raw_row = 2`
is the first data line, line 1 being the header. `raw_measurement_id` is carried
alongside but is only a local surrogate.

This is a correction to the plan, which proposed `raw_measurement_id` as the
locator. A surrogate can only be resolved by the database that minted it; a line
number can be checked against the archived bytes, which is what makes a disputed
observation auditable by someone who has the deposit and not our Postgres.

**Locator completeness: 3,136,836 / 3,136,836 and 3,233,963 / 3,233,963 — 100.000%
on both sides.**

## 8. Readiness for matching

### Measurement types — and the delta is not monotone

Counts of what the source says, kept distinct by construction. No pooling
decision is made here.

| Type | 202601 | 202609 | Change |
| --- | ---: | ---: | ---: |
| KI | 614,755 | 619,931 | +5,176 |
| IC50 | 2,117,146 | 2,212,313 | +95,167 |
| KD | 124,499 | 131,420 | +6,921 |
| EC50 | 280,436 | 270,299 | **−10,137** |
| **Total** | **3,136,836** | **3,233,963** | **+97,127** |

Both totals reconcile against their `activity` row counts, and the 202609 column
reproduces the accepted `reports/curation.md` figures **exactly** — an independent
check that the export reads the accepted corpus faithfully and that the corpus is
unchanged.

**EC50 observations decreased by 10,137 between the two snapshots.** Net growth is
+97,127, so the delta is **not monotone**: the later snapshot holds fewer rows of
one measurement type than the earlier one. This report does not claim a cause —
withdrawal, reclassification to another type, or a curation-side difference are all
consistent with a bare count, and distinguishing them is precisely what the
per-slot diff does. What it does establish is that modelling removals and
corrections was not defensive over-engineering: a monotone-growth assumption would
have been wrong on real data, in a measurable way, before any matching ran.

Note also that curated observations grew **more** than raw rows (+97,127 against
+96,450) while January excluded 716 more rows. Both are consistent with a changed
mix of multi-type rows and neither is explained by the counts alone.

### Assay-join coverage

| | 202601 | 202609 |
| --- | ---: | ---: |
| `matched` | 3,073,862 (**97.992%**) | 3,176,034 (**98.209%**) |
| `no_rsid_match` | 62,974 (2.008%) | 57,929 (1.791%) |

The assay join (`activity.reactant_set_id` → `assay_link` → `assay`) is
reproducible on January because the deposit carries both auxiliary artifacts.
Coverage is within a quarter of a percentage point of the accepted corpus's, so
assay context is comparably available on both sides.

### Multiplicity — can the joins inflate a count?

Three multiplicities matter, because any of them could silently multiply rows and
make a diff count our joins instead of BindingDB's revisions.

**`reactant_set_id` is one-to-one with rows on January**: 3,140,596 distinct values
over 3,140,596 rows. `reports/ingest.md` established the same for September. This
was checked, not inherited from the September result.

**Activities per raw measurement row** — the full distribution, not a mean:

| Activities from one raw row | Rows |
| ---: | ---: |
| 1 | 3,125,212 |
| 2 | 5,650 |
| 3 | 108 |
| **Rows with ≥1 activity** | **3,130,970** |

Which reconciles twice over: the row total equals `rows_curated` (3,130,970), and
`1×3,125,212 + 2×5,650 + 3×108 = 3,136,836` equals the `activity` row count. The
multi-type expansion is fully accounted for; nothing is unexplained.

**Reactant sets per assay**: min 1, **mean 14.19**, max 7,524. An assay is
therefore a many-rows-to-one-assay relationship, as expected — the join direction
that cannot multiply measurement rows.

### Entity counts

| | 202601 (isolated) | 202609 (accepted) |
| --- | ---: | ---: |
| `compound` | 1,373,204 | 1,424,670 |
| `target` | 10,674 | 11,012 |
| `assay` | 218,973 | 224,430 |
| `publication` | 54,561 | 57,171 |
| `assay_link` | 3,107,915 | 3,179,005 |

**One caveat on three of these rows.** `compound`, `target` and `publication` carry
no release foreign key, so the accepted schema's totals cover everything ever
curated there — the 202609 release *and* the M1 PDSP Ki pilot. The isolated schema
holds 202601 alone. Those three rows are therefore **not clean snapshot deltas**,
and the differences (+51,466 compounds, +338 targets, +2,610 publications) are
upper bounds on growth rather than measurements of it. `assay` and `assay_link`
*are* release-scoped and their figures are exact. The release-scoped entity count
— distinct InChIKeys and sequence hashes per slot — is what the matching step
produces.

### Summary: the six readiness conditions

| Condition | 202601 | 202609 |
| --- | --- | --- |
| Raw → curated reconciliation | 3,130,970 + 9,626 = 3,140,596 ✓ | 3,228,136 + 8,910 = 3,237,046 ✓ |
| Exclusions carry a rule code | 9,626 / 9,626 | 8,910 / 8,910 |
| Measurement types preserved distinct | 4 types, sum = 3,136,836 ✓ | 4 types, sum = 3,233,963 ✓ |
| Assay join coverage | 97.992% | 98.209% |
| Entry DOI coverage | 99.777% | 99.945% |
| Source-locator completeness | **100.000%** | **100.000%** |

**Both snapshots are ready to be matched.**

## 9. Tests

**Full suite: 859 tests, 0 failures, 0 errors, 0 skipped, 257s.** The baseline before this step was 824, so 35 tests were added and none was removed or skipped. Ruff check and format are clean across 150 files.

| Suite | Tests | What it holds down |
| --- | ---: | --- |
| `test_m11c_release_lifecycle.py` | 10 | the three-way decision, identity preservation on completion, conflict refusal, transactional rollback — both loaders |
| `test_m11c_observation_export.py` | 7 | the matcher key contract, entry-vs-article DOI separation, locator reconstruction, read-only enforcement, byte reproducibility |
| `test_m11c_isolated_ingest.py` | 16 | the report's figures against the manifest, the acquisition record's unchanged state, curator parity between the snapshots, the measured entry-DOI grain, the named checksum algorithms, the sampled stereochemistry measurement, and that the non-monotone finding is not quietly dropped |
| `test_m11c_acquisition.py` | 15 | the acquisition record, now asserting lifecycle consistency rather than emptiness |
| `test_cli.py` | 5 | `asof export` is registered and reachable from the repo |

### The acquisition-state test was wrong to keep asserting zero rows

`test_no_curation_or_matching_ran_against_the_new_release` asserted that the
202601 releases held no derived rows. That was correct while acquisition was the
whole story: nothing had been parsed, so any derived row meant something had run
that should not have. It stops being correct the moment a release is deliberately
ingested — in the isolated schema the January artifacts are *supposed* to have
rows, and a test demanding zero asserts that the next milestone never happened.

It is now `test_acquisition_records_agree_with_their_lifecycle_state`, holding the
invariant that actually survives the transition: **a release's declared state and
its derived rows agree.** Still unparsed means still empty, which is what keeps the
untouched acquisition records in the accepted schema as evidence. Ingested means
`rows_loaded` matches the raw rows really present. It passes in both schemas, which
the old assertion could not do.

One defect surfaced while writing it: the reconciliation originally counted
`raw_measurement` only, and failed on `assays`, which lands in `raw_record`. It now
sums `raw_measurement`, `raw_record` and `raw_sequence`, so the invariant is about
the release rather than about one table.

### A defect the first version of the export shipped with

The export's `assay_joined` counter tested `status == "joined"`. The curation
pipeline writes `"matched"`. The counter therefore reported **0 matched rows out of
3.07 million**, and the test did not catch it because the fixture inserted
`'joined'` — a value the pipeline never produces. Fixed at the source: the
vocabulary is now `ASSAY_MATCHED` / `ASSAY_NO_RSID_MATCH` in
`curate/pipeline.py`, imported by the export, and the fixture uses the constant.
The exported rows were always correct; only the derived summary figure was wrong.

## 10. Proposed bounded next step: the matching run

Detailed in `docs/M11.md` §10b. In short: run the matcher over the two exports and
publish the diff — removals, corrections, identifier conflicts, unresolved
changes. No fitting, no evaluation score, no docking.

### One constraint has to be cleared first, and it was measured

`diff_snapshots` takes both sides as in-memory `list[dict]`. Measured on this
machine over the real exports: **2,492 bytes per row dict plus 1,473 bytes per
`Observation`**. Over both snapshots' 6,370,799 observations that is **15.9 GB for
the dicts alone and 25.3 GB with the observations**, before `_group`'s `Slot`/`Value`
index. Physical memory is **16 GiB** (`hw.memsize`), so a single full-corpus call
cannot complete — and it would fail only after loading both sides, which is the
worst point at which to find out. How long that load takes was not measured; the
memory figures were.

The fix is a decomposition of the existing matcher, not a second implementation:
shard both exports by `H(slot) mod K`, diff each shard pair with the same tested
function, and merge. It is sound because every decision `diff_snapshots` makes is
slot-scoped — `_group` keys on `Slot`, the counted multiset pairing compares value
multiplicities within a slot, `_link` proposes candidates from one slot's surplus —
and because every `DiffReport` field composes: Counters add, sets union, lists
concatenate.

### What the run would settle

| Question | Checklist item |
| --- | --- |
| Is `reactant_set_id` release-stable? | 13, **BLOCKED** on exactly this |
| Is `entry_doi` release-stable? Currently unmeasured | — |
| The removal and correction rates between two real snapshots | — |
| What caused EC50 to fall by 10,137 | — |
| The **eligible** new-pair count, after censoring, discordance and conflicts | 8, **OPEN** on exactly this |

### Bounds

- Matching and counting only — no endpoint rebuild on the increment, no features,
  no model, no score.
- Exploratory, and labelled so. B is our 202609, already read, and M8/M9 were
  produced from it. The run validates machinery and measures rates; it is not a
  confirmatory evaluation and does not become one by being careful.
- The January side stays in `seq2lead_m11c_202601`; September is read only through
  the export.

## 11. What this step did not resolve

The freeze is **not signed**. The checklist in `docs/M11.md` §11 has 23 rows; this
work closed two of them (6 and 12), and **nine remain open**: 5, 7, 8, 10b, 11c,
11d, 11e, 13, 14.

Specifically unchanged by anything above:

- **No post-freeze snapshot exists** (item 7). The newest deposit, 2026-07-01,
  predates the design document, and our 202609 is later still and already
  inspected. This is a wait, not a task.
- **Evaluation semantics are still unvalidated on real snapshots** (item 10b).
  Both snapshots being exported *unblocks* this; it does not do it. The matcher has
  not been run over them.
- **Two production paths remain unbuilt** (items 11d, 11e): retrieval index
  construction has no caller, and there is no partition-filtered evaluation-mode
  evidence API.
- **`reactant_set_id` cross-release stability is unmeasured** (item 13), as is the
  entry DOI's.
- The 2026-04-01 cadence gap is still unexplained (item 5).

Nothing here is a model result. No model was fitted, no prediction was scored, and
no docking was run.
