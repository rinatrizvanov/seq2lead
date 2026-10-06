# What this release reproduces, and what it does not

One sentence: **the repository alone reproduces the benchmark's reported numbers
from saved predictions; it does not rebuild those predictions from raw BindingDB
data.** Both halves of that sentence are stated precisely below, with the
excluded artifacts named and measured rather than gestured at.

The distinction matters because "reproducible" is used for both, and they carry
very different guarantees. Recomputing scores from saved predictions proves the
reported metrics follow from the predictions and the labels, and it detects
tampering with either. It does not prove the predictions follow from the raw data,
because the fitting inputs are not shipped.

## Three modes, three names

Seq2Lead can be run in three distinct ways. They need different artifacts and
establish different things, and the repository supports exactly one of them from a
clone. Where any document uses one of these terms it means the one defined here.

| Mode | What it does | Needs | From a clone? |
| --- | --- | --- | --- |
| **saved-prediction reproduction** | recomputes the published metrics from the shipped `predictions.npz` and `evaluation-pairs.jsonl`, fitting nothing | only tracked files | **yes** |
| **live sequence-query ranking** | the product path: `seq2lead rank` takes one protein FASTA and orders a frozen compound library by predicted pKi | a trained checkpoint, a registered frozen library in PostgreSQL, the exact bound feature caches, and the pinned ESM-2 weights — **all stored separately and none tracked** | **no** |
| **raw-to-fit rebuild** | ingests BindingDB from raw archives, re-curates, re-featurises and refits end to end | the raw archives, the databases and the full intermediate set | **no**, and not independently reproducible at all while snapshot B is a rolling release |

What each one establishes, which is not interchangeable:

- Saved-prediction reproduction establishes that **the reported numbers follow
  from the predictions and the labels**, and that tampering with either is
  detected. It says nothing about whether the predictions follow from the raw
  data.
- Live sequence-query ranking **demonstrates the intended use**. It produces a
  ranked list, not evidence: a predicted pKi is not a calibrated probability and
  not experimental proof of binding. The recorded example output is
  `reports/examples/rank_demo.txt`.
- Raw-to-fit rebuild would establish that **the predictions follow from the raw
  data**. Nobody can currently do this independently; see the re-obtainability
  section below.

**The full local test suite is none of the three.** `uv run pytest` over all 1,324
tests on a complete artifact set establishes that the *software* behaves as
specified. It is a software check, not a reproduction of the benchmark, and a
green suite — or a green CI badge over its 909-test subset — is not evidence about
the scientific result.

For the commands themselves and the per-check availability table, see
[`docs/REPRODUCIBILITY.md`](REPRODUCIBILITY.md); this file states the *scope* those
commands cover and names what is missing.

## Supported from a clone, with nothing downloaded

| Capability | Entry point | Inputs, all tracked |
| --- | --- | --- |
| Recompute every reported metric from saved predictions | `python -m seq2lead.asof.recompute <run-dir>` | `manifest.json`, `predictions.npz`, `evaluation-pairs.jsonl` |
| Compare the recomputation against the published results and record the binding | `python -m seq2lead.asof.verify_results <run-dir>` | the above plus `results.json` |
| Refuse publication from a stale or unauthorised verification | `python -m seq2lead.asof.publish <run-dir>` | the above plus `verification.json` |
| Tamper detection — flip labels or edit predictions in a self-contained clone and confirm the gates refuse | `scripts/asof/m11h_reproduce_integrity.py` | the above plus `training-records.json` and `protein-transform.npz` |
| Read the per-target scores, cohort sizes and seed spread | `reports/m11h_results.md`, `results.json` | tracked |

Every file those paths read is in the release inventory with its SHA-256, and the
run manifest records the digest of each one, so a changed byte is detected rather
than silently reproduced. This is the complete-benchmark claim: **17,373
evaluation compounds across 1,060 sequences, 26,444 scored pairs, 22 model-seed
score columns**, with the metrics recomputable end to end.

## Not supported from a clone

Seven capabilities need artifacts a checkout does not carry. Nothing here is
hidden by a fallback or a stub: each refuses with a message naming the missing
input.

| Capability | Blocked on | Size |
| --- | --- | ---: |
| **Live sequence-query ranking** (`seq2lead rank`) | a trained checkpoint under `data/m9/final/`, a registered frozen library in the local PostgreSQL corpus, the exact bound feature caches, and the pinned ESM-2 weights. The CLI refuses a missing or incompatible feature binding rather than silently using whatever cache is current. | 53.6 MB checkpoints + 826.1 MB caches + 2.6 GB ESM-2 weights (never redistributed) |
| Re-predict from the M11h fitted models | `…/run-20261005T134842Z/checkpoints/` (5 files) | 42.6 MB |
| Refit the models | the A-train membership export `data/asof/m11f/a-membership.jsonl` | 413.5 MB |
| Rebuild the features | `data/features/` ECFP4 and ESM-2 caches (9 files) | 826.1 MB |
| Rebuild from raw, including the snapshot match | `data/raw/` BindingDB 202601 and 202609 archives, plus the `data/asof/` intermediates | 1,157.7 MB raw; 7.7 GB intermediates |
| Re-run the docking gate | `data/m10/` (3,566 pose and log files) and the Vina binary under `tools/` | 58.3 MB + 1.1 MB |
| Re-run the M9 per-seed study | `data/m9/`, `data/predictions/` | 170.8 MB + 78.5 MB |

Totals: the working tree holds **≈10.1 GB** of artifacts the release excludes,
against **34.9 MB** tracked. The exclusions are mechanical — `.gitignore` excludes
bulk data wholesale and re-admits an explicit allow-list — so an artifact is
shipped only by being named.

## Which excluded inputs can be obtained again, and which cannot

This is the real limit on a from-raw rebuild, and it is not a packaging choice.

**Re-obtainable.** The January 2026 snapshot is the UC San Diego Library archival
deposit, **DOI 10.6075/J0V40W61**, which is citable and persistent; its per-file
digests are recorded in `configs/manifests/m11_acquisition_202601.json`. ESM-2 is
`facebook/esm2_t33_650M_UR50D` at revision
`08e4846e537177426273712802403f7ba8261b6c`, still fetchable by that revision.
AutoDock Vina 1.2.x for arm64 is a public release. None of these three is
redistributed here, and none needs to be.

**Not reliably re-obtainable.** The September 2026 snapshot is BindingDB's
**rolling** monthly release, `BindingDB_All_202609_tsv.zip`, pinned in
`src/seq2lead/ingest/sources.py` by SHA-256
`4c04e0fe…b73e16`. BindingDB replaces the rolling file each month and does not
archive previous months at a stable URL, so a third party attempting a from-raw
rebuild **may be unable to acquire the exact B-side bytes**, and the digest will
then detect the substitution rather than paper over it. The archival deposit
covers 202601 only; there is no equivalent deposit pinned here for 202609.

The consequence, stated plainly: the as-of comparison between January and
September is reproducible **by us**, from the pinned bytes we hold, and is
verifiable **by anyone** at the level of saved predictions and recorded digests.
It is not independently rebuildable from raw by a third party unless BindingDB
supplies the 202609 archive, or the evaluation is re-pinned to two archival
deposits. Re-pinning is a scientific change and is therefore out of scope for
this release; it is the natural first step of any public release.

## What is excluded for licensing rather than for size

- `literature/` (16.3 MB) — copyrighted papers, not redistributable.
- `*.docx` design documents — private working material. The one exception is the
  owner-requested manuscript, `paper/Seq2Lead_scientific_manuscript.docx`.
- `tools/` — a third-party AutoDock Vina binary, not ours to redistribute.
- ESM-2 weights — 2.6 GB, cached locally, never committed.

## Summary for the owner

Approving this release approves publishing a **saved-prediction reproduction**: the numbers, the predictions behind them, the labels, the
provenance and the gates that refuse tampered inputs. It does not claim, and the
documents must not be read as claiming, that a third party can rebuild the
predictions from BindingDB. The blocker on that is the rolling 202609 snapshot,
which no amount of repackaging fixes.
