# WAL and checkpoint cost of a bulk COPY

Probe artifact: `rsid_eaids` (3,179,005 rows), loaded under a scratch release and
then removed. **This is not the full measurement release** — see the scope note
below before reading across.

| Metric | Value |
| --- | --- |
| Rows | 3,179,005 |
| Wall time | 23.0 s |
| WAL generated | 1,656.4 MiB |
| WAL records | 12,999,659 |
| Full-page images | 39,814 |
| WAL per row | 546 B |
| Payload stored | 201.5 MiB |
| **WAL amplification** | **8.22x** |
| Checkpoints, timed | 0 |
| **Checkpoints, requested** | **3** |
| `max_wal_size` | 1GB |

**Scope of this probe — read before using any number above.**

`rsid_eaids` is a **much smaller artifact than the full measurement release**. It
has a comparable *row* count, but two short text columns against the measurement
table's ~30 populated fields carrying full protein sequences: roughly 200 MiB of
payload against ~11 GiB. It was chosen because it is a real pinned artifact of
convenient size, not because it resembles the measurement load.

So these figures characterise **this load and no other**. In particular the
checkpoint count must not be read across to the full measurement ingest, whose
own counters were never captured and cannot be recovered — the release is
immutable. That load's WAL and checkpoint cost is simply **unknown**.

What this does establish is that bulk COPY on this cluster can generate WAL
several times the stored payload and can trigger requested checkpoints at all. A
requested checkpoint means WAL filled `max_wal_size` before the timed interval
elapsed, so the cluster checkpointed under pressure.

That is a reason to watch the next large load, **not** evidence that
`max_wal_size` must be raised for it — establishing that would mean measuring the
load in question. Migration `0003_release_wal_accounting` records these counters
per release, so the next one answers for itself.

One thing the probe does rule out: chunking the transaction would not help. It
would surrender the all-or-nothing guarantee that makes a partial load impossible
to mistake for a complete one, and it would not reduce WAL volume by a byte,
since the same rows are written either way.

Caveat: `pg_stat_wal` and the checkpoint counters are cluster-wide. This is a
dedicated development database with no concurrent workload, so the delta is
attributable to the probe; on a shared cluster it would not be.
