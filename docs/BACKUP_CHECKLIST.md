# Backup checklist

Derived from `configs/manifests/closeout_inventory.json`. Every item below is
evidence that exists **only on this machine's local disk**. A copy on the same
disk is not a backup, and the private GitHub repository is not a backup of
anything in group C — it does not contain those files at all.

Nothing here has been uploaded anywhere. Nothing further has been deleted.

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
repository. This is the group whose loss would be unrecoverable.

| Category | Files | Bytes |
| --- | ---: | ---: |
| feature caches and extensions | 9 | 866,154,787 |
| training membership export | 1 | 433,552,003 |
| M9 per-seed records and checkpoints | 60 | 178,920,547 |
| raw observation exports | 2 | 166,458,830 |
| large baseline run records | 1 | 79,189,068 |
| model checkpoints | 6 | 53,648,798 |
| M10 docking runs, receptors and poses | 3,564 | 53,432,351 |
| third-party binaries | 1 | 1,171,704 |
| private design documents | 2 | 59,257 |
| **total** | **3,646** | **1,832,587,345** |

Subtotal: **1,832,587,345 bytes** (1.83 GB) across 3,646 files.

## D. Working material preserved outside the repository

| Item | Bytes | SHA-256 |
| --- | ---: | --- |
| `/Users/rinatrizvanov/Desktop/Projects/seq2lead-CLAUDE_CLOSEOUT_PROMPT.md` | 5,216 | `676e8c170f852dbd67b4a91d631aaa5cc845ed0e9f0bb087d508032a39aeba5d` |

Untracked in the final-review pass and kept here so it is not lost. It remains
in Git history at commit `4a64d90`; history was not rewritten to remove it.

---

## Total to copy

| Group | Bytes | GB |
| --- | ---: | ---: |
| A — consolidated archive | 807,015 | 0.0008 |
| B — four retained ZIPs | 12,300,243 | 0.0123 |
| C — excluded scientific evidence | 1,832,587,345 | 1.8326 |
| D — working material | 5,216 | 0.0000 |
| **total** | **1,845,699,819** | **1.85** |

Allow headroom beyond this: a destination should hold the total plus room for a
second generation, so a failed copy never overwrites the only good one.

## Verification procedure

Do each step; a copy that has not been verified by digest is not a backup.

1. **Record the source digests.** The tables above are that record. Regenerate
   them with `shasum -a 256 <path>` and confirm they match before copying.
2. **Copy to independent media** — a different physical device, or a storage
   service the owner controls. Do not copy to another directory on this disk
   and call it a backup.
3. **Re-hash at the destination** and compare to the tables above, file by
   file. For group C, hash every one of the files, not a sample.
4. **Extract-and-compare the archive**: unzip group A at the destination and
   byte-compare each member against `MANIFEST.json`'s recorded digests. The
   archive's own manifest lists every member's source ZIP, original path and
   SHA-256.
5. **Spot-restore**: pick one file from each category in group C, copy it back
   to a scratch directory and confirm the digest. A backup that has never been
   read from is untested.
6. **Record the outcome** — destination, date, and the digest list used — then
   and only then may any further wrapper be considered for deletion.

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

