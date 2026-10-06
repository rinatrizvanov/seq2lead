# Schema and how it changes

## How the M1 pilot database actually reached the current schema

Stated plainly, because it matters for interpreting the pilot's release ids.

`create_schema()` runs `CREATE TABLE IF NOT EXISTS`, which does **nothing** when a
table already exists with the wrong columns. It is not a migration. The M1 revision
made changes that it therefore could not apply:

| Change | Why `IF NOT EXISTS` could not apply it |
| --- | --- |
| Added `sha256_pinned`, `field_total`, `copy_seconds`, `exclusion_seconds`, `index_seconds`, `total_seconds` to `source_release` | new columns on an existing table |
| Replaced `ingest_exclusion.raw_line TEXT` with `raw_bytes BYTEA` + `byte_length` | column type and name change |
| Changed both foreign keys from `ON DELETE CASCADE` to `ON DELETE RESTRICT` | constraint redefinition |

**What was actually done: the raw-layer tables were dropped and the pilot was
re-ingested from the pinned archive.** That was safe — the raw layer is
reproducible from a checksummed artifact, and the re-ingest reconciled 27,715 of
27,715 rows — but it was *not* identity-preserving:

| | Before | After |
| --- | --- | --- |
| Pilot release id | 33 | 12 |
| Raw row ids | reassigned | 16 … 27,730 |

The id 33 reflected 33 create/destroy cycles under the old mutable re-ingest
behaviour; 12 likewise counts releases created by the test suite. Neither number
means anything beyond "this row was inserted at some point". No data was lost, but
any external reference to release 33 is dangling, and nothing in the database
records that the renumbering happened. That is precisely the failure mode the
migration ledger now prevents.

## The migration ledger

`src/seq2lead/db/migrations.py` holds an ordered list, applied by
`seq2lead db migrate` and recorded in `schema_migration`:

| Version | Name | Notes |
| --- | --- | --- |
| 0001 | `baseline_raw_layer` | Baseline. On a database that already has these tables it is **recorded as applied without running**; on an empty one it creates them. |
| 0002 | `m2_auxiliary_raw_tables` | Adds `raw_record` and `raw_sequence` for the M2 mapping files and FASTA. Pure `CREATE TABLE`. |
| 0003 | `release_wal_accounting` | Adds `wal_bytes`, `wal_records`, `wal_fpi`, `checkpoints_timed`, `checkpoints_requested` to `source_release`. Pure `ADD COLUMN IF NOT EXISTS`. |
| 0004 | `m3_curated_layer` | Adds `compound`, `compound_source`, `target`, `target_alias`, `assay`, `assay_link`, `publication`, `activity`, `curation_exclusion`, `curation_run`. Pure `CREATE TABLE`. |
| 0005 | `activity_bound_inclusivity` | Adds `activity.relation_raw` and `activity.bound_inclusive`, after an earlier build wrongly collapsed `>=` into `>` and `<=` into `<`. Pure `ADD COLUMN`. |
| 0006 | `structure_keyed_identity` | Re-keys `compound_source` on the structure itself, adds `activity.source_structure_sha256`, `target_organism`, and the target annotation counts. **Changes compound identity**, so it required a derived-layer rebuild -- see below. |
| 0007 | `m4_endpoint_layer` | Adds `endpoint_version`, `pair_regression`, `pair_label` and their support tables. Pure `CREATE TABLE`. |
| 0008 | `pair_eval_exclusion_flags` | Adds `excluded_from_eval` to both pair tables and `superseded_by` / `superseded_reason` to `endpoint_version`. Pure `ADD COLUMN`. |
| 0009 | `m6_split_layer` | Adds `split_version`, `split_pair_assignment`, `split_activity_assignment`, `target_cluster`, `compound_cluster`. Pure `CREATE TABLE`. |
| 0010 | `cluster_threshold_numeric` | Retypes `target_cluster.threshold` from `REAL` to `NUMERIC(4,3)`. A `REAL` never compared equal to the `float8` literal it was written with, so threshold-scoped lookups silently matched nothing. |
| 0011 | `temporal_partition_endpoints` | Adds `split_partition_endpoint`, plus `protocol` / `superseded_by` / `superseded_reason` on `split_version` and `history_partitions` on `split_pair_assignment`. Supports one endpoint per temporal partition. |
| 0012 | `m7_feature_layer` | Adds `feature_version`, `feature_entity_flag`, `split_target_stratum`. Pure `CREATE TABLE`. |

`schema_migration.baselined` distinguishes the two cases, so it is always visible
whether a migration ran or was merely adopted. On the current pilot database:

```
0001  baseline_raw_layer          (baselined against existing tables)
0002  m2_auxiliary_raw_tables     (applied)
0003  release_wal_accounting      (applied)
0004  m3_curated_layer            (applied)
0005  activity_bound_inclusivity  (applied)
0006  structure_keyed_identity    (applied)
0007  m4_endpoint_layer           (applied)
0008  pair_eval_exclusion_flags   (applied)
0009  m6_split_layer              (applied)
0010  cluster_threshold_numeric   (applied)
0011  temporal_partition_endpoints (applied)
0012  m7_feature_layer            (applied)
```

Only 0006 changed identity and forced a rebuild; 0010 was a correction to a type that made a comparison silently fail. Every other migration is
`CREATE TABLE` or `ADD COLUMN`, so release ids, raw row ids and (from 0006
onward) compound and target ids were preserved.

0002 is pure `CREATE TABLE` and 0003 is pure `ADD COLUMN IF NOT EXISTS`, so the
pilot's release 12 and every raw row id were untouched — verified before and after.

Note what 0003 could **not** do retroactively: it added the WAL columns *after* the
full measurement release was loaded, so that release's counters are `NULL` and
cannot be backfilled. A release is immutable and cannot be re-loaded to measure
it. This is the cost of adding accounting late, and it is why the M2 report
declines to state a WAL figure for that load.

## Rules for future changes

1. **Every schema change is a numbered migration.** `create_schema` is only ever
   the 0001 baseline; adding a column to it without a migration is a silent
   no-op on existing databases.
2. **Prefer `ALTER`.** Adding nullable columns, new tables and new indexes
   preserves identity and is always allowed.
3. **A change that cannot be expressed as `ALTER` needs a written rebuild plan
   before it runs** — which releases are affected, whether ids are preserved, and
   how downstream references are repaired. A rebuild is acceptable for the raw
   layer because it is reproducible from pinned artifacts; it is not acceptable
   silently.
4. **`ON DELETE RESTRICT` stays.** A release cannot be deleted while rows still
   reference it, which is what makes foreign keys into `source_release` safe for
   later milestones to rely on.

## What immutability does and does not guarantee

Stated precisely, because the word promises more than the implementation delivers.

**It guarantees:** a release is never overwritten in place. Re-ingesting the same
`(source_name, version, subset)` with identical bytes is a verified no-op that
preserves the release id and every raw row id; with different bytes it raises
`ReleaseConflict`. And `ON DELETE RESTRICT` means a release cannot be dropped out
from under rows that reference it.

**It does not guarantee** that nothing is ever deleted. `RESTRICT` blocks an
accidental cascade, not a deliberate ordered delete — remove the children first
and the parent will go.

### The one place that is used: `seq2lead profile wal`

The WAL probe needs to measure a bulk COPY, but an already-loaded release cannot
be re-loaded to measure it. So it loads a real pinned artifact under a **scratch
subset name** — `dataclasses.replace(source, subset=f"{source.subset}__walprobe")`
— which occupies a different unique key and can never collide with a release of
record. Afterwards it deletes, in one transaction, children then parent:

```sql
DELETE FROM raw_record     WHERE source_release_id = %s;
DELETE FROM source_release WHERE id = %s;
```

This is a deliberate, temporary, explicitly-named exception, not a general licence.
Two consequences worth recording:

* It leaves allocated pages behind in `raw_record`. The rows are gone; the space
  is not returned. Storage figures for that table are inflated as a result, which
  the M2 report states rather than hiding.
* Nothing prevents the same sequence being run against a release of record. The
  protection here is convention and a distinct name, not a constraint.

## Rebuilding the derived layer (migration 0006)

Migration 0006 changed compound identity, so the M3 layer could not be patched in
place and had to be regenerated. The audit that forced it:

BindingDB's `Ligand InChI Key` has `-UHFFFAOYSA-` as its second block, meaning
**no stereo layer**. 64,486 keys carried between two and eight genuinely different
SMILES -- enantiomers, epimers, anomers -- touching 448,735 raw rows. Keying the
standardization cache on that identifier meant the first structure seen won, and
every other row sharing the key silently inherited its compound. RDKit
standardizes those structures to different parents, so the conflation was real:
`AAAMYWMNTPTTKE-UHFFFAOYSA-N` resolves to both `-ABKXIKBNSA-N` and
`-JDNHERCYSA-N`.

The procedure used, which touches **no raw data**:

```bash
uv run seq2lead db migrate                        # 0006, 0007
uv run seq2lead curate rebuild --subset all --yes # clears ONLY the derived layer
uv run seq2lead curate run --subset all           # regenerate from pinned raw
uv run seq2lead curate finalize                   # target annotation summary
uv run seq2lead curate index
```

`clear_derived()` deletes `activity`, `curation_exclusion`, `curation_run`,
`compound_source`, `compound`, `target_alias` and `target_organism`. It never
touches `source_release`, `raw_measurement`, `raw_record`, `raw_sequence` or
`ingest_exclusion`, so the rebuild reads exactly the same checksummed bytes and
all five pinned releases keep their ids.

Outcome, both reconciling against the same 3,237,046 raw rows:

| | Before | After |
| --- | --- | --- |
| `compound` | 1,345,781 | 1,424,670 |
| InChI Keys spanning >1 compound | 0 (conflated) | 60,969 |
| Curated / excluded | 3,228,826 / 8,220 | 3,228,136 / 8,910 |

Compound and target ids changed, which is why this is a rebuild and not a patch.
`curator_version` moved to `m3/v3` so the two generations cannot be confused.

## Rebuilding from scratch

The raw layer is fully reproducible, so a clean rebuild is a legitimate route
when a migration would be more trouble than it is worth:

```bash
docker compose --env-file .env -f docker/compose.yml down -v   # drops the volume
docker compose --env-file .env -f docker/compose.yml up -d
uv run seq2lead db migrate
uv run seq2lead ingest m2
```

Every artifact is re-fetched and re-verified against its frozen SHA-256, so the
result is byte-identical input. Release ids will differ. Do this deliberately,
not as a way to avoid writing a migration.
