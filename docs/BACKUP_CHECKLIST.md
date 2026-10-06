# Backup checklist

> This is approval **A9** in [`OWNER_DECISIONS.md`](OWNER_DECISIONS.md), and it
> gates **A10**, the public release.

Derived from `configs/manifests/closeout_inventory.json`. Every item below is
evidence that exists **only on this machine's local disk**. A copy on the same
disk is not a backup, and the private GitHub repository is not a backup of
anything in group C — it does not contain those files at all.

Nothing here has been uploaded anywhere. Nothing further has been deleted.

## Correction: group C was understated by 8.15 GB

An earlier revision of this checklist put group C at **3,646 files /
1,832,587,345 bytes (1.83 GB)**, taken from the per-category counts in
`configs/manifests/closeout_inventory.json`. Enumerating the files themselves --
everything under `data/`, `reports/` and `tools/` that git does not track -- gives
**3,967 files / 10,579,513,175 bytes (9.85 GB)**: **+321 files, +8,746,925,830
bytes**.

The categories the old figure counted were real; the list of categories was
incomplete. It omitted `data/raw` entirely and counted only about 600 MB of the
7.70 GB under `data/asof`. That omission mattered most for the one file this
project has documented as possibly **not obtainable again**:
`data/raw/BindingDB_All_202609_tsv.zip`, 566 MB, the September rolling release
BindingDB does not archive at a stable URL (see `RELEASE_SCOPE.md`). The
inventory's own `never_delete` list already said "raw source archives", so the
checklist contradicted it.

Backing up 1.83 GB and believing the unrecoverable evidence was safe would have
left the single most irreplaceable file uncopied. Groups A, B and D were correct
and are unchanged.

---

## A. The consolidated historical-revisions archive

| Item | Bytes | SHA-256 |
| --- | ---: | --- |
| `/Users/rinatrizvanov/Desktop/Projects/seq2lead-historical-revisions-archive.zip` | 807,015 | `22d12c45eb22b2646f28c9f712ccaafe388da87483799936f36af13d9c7bf1f2` |

Holds **92 members** (2,701,866 bytes of original
content) that exist in no other location: the pre-correction revisions of
reports, manifests, contracts, source and tests from the thirteen deleted
wrappers. The repository has a single initial commit, so Git does not hold them.

**Independent backup: NO.** NONE. The archive is on the same local disk as the wrappers it replaced, so it is not yet a backup. Copy it to independent media before relying on it.

## B. The four retained review ZIPs

| Item | Bytes | SHA-256 | Why retained |
| --- | ---: | --- | --- |
| `/Users/rinatrizvanov/Desktop/Projects/seq2lead-m11h-integrity-review.zip` | 9,626,536 | `8768c266cbb1728c9540b1fbad168f0e21aeced3d5d2dfddaf24abebf8bda692` | the final M11h integrity ZIP; retained by instruction |
| `/Users/rinatrizvanov/Desktop/Projects/seq2lead-m8-review-v4.zip` | 669,539 | `e3587a416911fbc528b515758066ed692d84ac87ba3f239317fb8d6360e24023` | accepted M8 snapshot; retained until preservation is independently proved |
| `/Users/rinatrizvanov/Desktop/Projects/seq2lead-m9-review-v4.zip` | 825,771 | `5c1d63e1a60c5b27ed096ff2d5040cf4e2fa1521d650c29e391ace4bcd141ca5` | accepted M9 snapshot; retained until preservation is independently proved |
| `/Users/rinatrizvanov/Desktop/Projects/seq2lead-m10-review-v3.zip` | 1,178,397 | `280475deea30109b109ea537fc183db2657328603d314d1a0fd18702843388ce` | accepted M10 snapshot; retained until preservation is independently proved |

Subtotal: **12,300,243 bytes** (12.30 MB).

## C. Scientific evidence excluded from Git

Present on local disk, deliberately untracked, and **not** in the private
repository. This is the group whose loss would be unrecoverable. Enumerated from
disk, not from category totals.

| Path | Files | Bytes | MB |
| --- | ---: | ---: | ---: |
| `data/asof` | 222 | 8,077,479,529 | 7,703.3 |
| `data/raw` | 8 | 1,213,933,915 | 1,157.7 |
| `data/features` | 9 | 866,154,787 | 826.0 |
| `data/m9` | 68 | 178,935,356 | 170.6 |
| `data/predictions` | 72 | 82,190,781 | 78.4 |
| `reports/results` | 1 | 79,189,068 | 75.5 |
| `data/m10` | 3,566 | 54,402,201 | 51.9 |
| `data/predictions_m9` | 20 | 26,055,834 | 24.8 |
| `tools/vina` | 1 | 1,171,704 | 1.1 |
| **total** | **3,967** | **10,579,513,175** | **10,089.4** |

Subtotal: **10,579,513,175 bytes** (9.85 GB) across 3,967 files.

### Copy in this order, because they are not equally replaceable

1. **`data/raw` first -- 1,157.7 MB.** `BindingDB_All_202609_tsv.zip` is a rolling
   monthly release that BindingDB replaces and does not archive, so losing it may
   end the ability to rebuild snapshot B at all. The January archive is re-fetchable
   from DOI `10.6075/J0V40W61`, but it is cheap to copy alongside.
2. **Everything else under `data/` and `reports/results` -- 8.93 GB.** Derived from
   the raw archives by the pinned pipeline, so replaceable *in principle*; in
   practice regenerating it is the raw-to-fit rebuild, which `RELEASE_SCOPE.md`
   records as not a tested one-command path. Treat as irreplaceable.
3. **`tools/vina` -- 1.1 MB.** A public AutoDock Vina release; re-downloadable.
   Copied because the exact binary is what produced the recorded docking results.

`literature/` (6 PDFs, 17.0 MB) is **excluded by default**: third-party
copyrighted papers, obtainable again from their publishers, and not evidence this
project produced. `backup_evidence.py --include-literature` copies them anyway.

## D. Working material preserved outside the repository

| Item | Bytes | SHA-256 |
| --- | ---: | --- |
| `/Users/rinatrizvanov/Desktop/Projects/seq2lead-CLAUDE_CLOSEOUT_PROMPT.md` | 5,216 | `676e8c170f852dbd67b4a91d631aaa5cc845ed0e9f0bb087d508032a39aeba5d` |

Untracked in the final-review pass and kept here so it is not lost. It remains
in Git history at commit `4a64d90`; history was not rewritten to remove it.

---

## Total to copy

| Group | Files | Bytes | GB |
| --- | ---: | ---: | ---: |
| A — consolidated archive | 1 | 807,015 | 0.0008 |
| B — four retained ZIPs | 4 | 12,300,243 | 0.0115 |
| C — excluded scientific evidence | 3,967 | 10,579,513,175 | 9.8530 |
| D — working material | 1 | 5,216 | 0.0000 |
| **total** | **3,973** | **10,592,625,649** | **9.87** |

Measured by `scripts/release/backup_evidence.py manifest`, which hashes every one
of these files. Regenerate it rather than trusting this table.

Allow headroom beyond this: a destination should hold the total plus room for a
second generation, so a failed copy never overwrites the only good one. That is
**about 20 GB** of free space on the destination, and `copy` says so if there is
less.

## The exact steps

`scripts/release/backup_evidence.py` does all four phases. It **refuses** a
destination on the same filesystem, and also refuses a different volume that sits
on the same physical disk -- checked with `diskutil`, not assumed. A second copy on
one disk shares that disk's failure, so it is not a backup.

Nothing below deletes or moves a source file. Each run writes into its own dated
generation directory, so a failed copy cannot overwrite a previous good one.

```bash
cd "/Users/rinatrizvanov/Desktop/Projects/Project 2 - Seq2Lead"

# 0. Attach the destination. An external disk, or storage you control off this
#    machine. Confirm it is really a separate device:
diskutil info /Volumes/YOUR_DISK | grep -E "Device Node|Part of Whole|Physical Store"

# 1. Record the source digests BEFORE copying. ~10 GB is read, so allow a few
#    minutes. Keep this file; it is the record every later check compares against.
uv run python scripts/release/backup_evidence.py manifest \
    --out /Volumes/YOUR_DISK/seq2lead-source-digests.tsv

# 2. Copy. Refuses same-disk destinations; reports free space; writes
#    /Volumes/YOUR_DISK/seq2lead-evidence-<UTC timestamp>/ plus its own
#    SOURCE-MANIFEST.tsv.
uv run python scripts/release/backup_evidence.py copy --dest /Volumes/YOUR_DISK

# 3. Verify. Re-hashes EVERY file at the destination and compares to the source,
#    file by file -- not a sample. Writes VERIFIED.json only if all match.
uv run python scripts/release/backup_evidence.py verify --dest /Volumes/YOUR_DISK

# 4. Spot-restore. Copies one file per group and category back off the
#    destination and re-hashes it. A backup never read from is untested.
uv run python scripts/release/backup_evidence.py restore --dest /Volumes/YOUR_DISK
```

Step 3 exiting non-zero means you do **not** have a backup yet; re-copy before
relying on it. Step 3 writing `VERIFIED.json` is the record referred to below.

### Then, and only then

5. **Extract-and-compare group A**: unzip it at the destination and byte-compare
   each member against the archive's own `MANIFEST.json`, which lists every
   member's source ZIP, original path and SHA-256.
6. **Record the outcome** -- destination, date, and the digest file used. Only
   after that may any further wrapper be considered for deletion.
7. **Repeat to a second, different destination** if this evidence matters. One
   verified copy on one external disk is one failure away from none.

## What must never be deleted because a review copy exists

- raw source archives
- database volumes
- feature caches and extensions
- model checkpoints
- saved predictions
- training membership exports
- docking runs, receptors and poses

A review ZIP, the private repository and this checklist are all records *about*
that evidence. None of them is a substitute for it.

