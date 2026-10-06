# Archive retention

Review ZIPs are snapshots, not the working project, Git history, source database, feature store or fitted-model backup. Many omit the expensive artifacts needed for refitting and inference.

## Keep

1. One immutable final M11h integrity ZIP, with its recorded checksum.
2. Final accepted M8, M9 and M10 archives until their unique reports/artifacts/manifests are demonstrably in version control or an independent backup.
3. Original raw archives for January and September, database backup, frozen caches/extensions, fitting membership, trained checkpoints/bindings, predictions and required docking evidence on separate backed-up storage.
4. Manifest history and correction records in Git, even when older wrapper ZIPs are retired.

## Usually retire after verification

Superseded `v1`, `v2` etc. review ZIPs and intermediate M11 preparation/acquisition/ingest/matching/aggregation/preflight ZIPs, provided a member-level inventory proves their unique scientific files are preserved elsewhere. Git must actually track them; the old ignore rules excluded execution scripts and data artifacts.

Before deletion: generate a filename/size/SHA-256 inventory for each ZIP; compare unique members with the final repository and independent backup; preserve any historical bytes needed for provenance; record the disposition. Then produce an exact deletion list for owner approval. Do not delete by a broad `seq2lead*.zip` glob. No ZIPs have been deleted by this closeout.

The exact old filenames on the Mac must be enumerated there. This archive does not establish which copies still exist on the Desktop. A private GitHub repo does not replace backups of files it excludes.
