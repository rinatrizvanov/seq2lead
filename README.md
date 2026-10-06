# Seq2Lead

**Scientific software for protein-sequence-based prioritisation of compounds from an existing library.**

## What it is for

You have a protein sequence and a library of compounds that already exist. Seq2Lead orders that library by predicted pKi, so that a limited number of compounds can be taken forward first. It prioritises *existing* compounds — it does not design or generate molecules, and a predicted ranking is a hypothesis for triage, not evidence of binding.

Prioritisation is only useful if the ranking can be trusted, so the project treats the evaluation as the hard part. It keeps raw evidence, records which snapshot every measurement came from, keeps exact and censored measurements apart, and refuses to publish a number whose inputs it cannot re-verify.

## What it actually is today

**A bounded CLI prototype, plus an auditable benchmark of it.** Stated plainly so the scope is not overread:

- **The prototype.** `seq2lead rank` takes one protein FASTA and a frozen library and returns a ranked list with predicted pKi. It works, and it is a command-line tool only — there is no web interface, no hosted service and no API. It needs a trained checkpoint, a registered library in a local PostgreSQL corpus, the exact bound feature caches and the pinned ESM-2 weights, none of which are in this repository.
- **The benchmark.** A historical January → September 2026 BindingDB evaluation, with its predictions, labels, provenance and verification gates shipped so the reported numbers can be recomputed from a clone. This is the part a reader can check.

The benchmark is the contribution. The prototype is what the benchmark measures.

## Three ways to run it, and which one works from a clone

| Mode | Works from a clone? | Needs |
| --- | --- | --- |
| **Saved-prediction reproduction** — recompute the published metrics from the shipped predictions and labels | **yes**, nothing to download | tracked files only |
| **Live sequence-query ranking** — the product path, `seq2lead rank` on a new sequence | **no** | checkpoint, frozen library, bound feature caches and ESM-2 weights, all stored separately |
| **Raw-to-fit rebuild** — ingest BindingDB and refit end to end | **no** | raw archives, databases and the full intermediate set; also not independently reproducible, see below |

Definitions and the measured artifact sizes: [release scope](docs/RELEASE_SCOPE.md). The **full local test suite is none of the three** — it checks the software, not the science.

**Status: M11h closed; historical evaluation completed.** Results are exploratory, Ki pooling across assays is provisional, and the prospective confirmatory freeze is unsigned. Retrieval is unbuilt, evaluation evidence display is off, and `label_reversal-v3` is unscored. The project does not generate molecules and does not establish experimentally validated new binders. No paper has been submitted or peer reviewed.

## Why it is built this way

Useful screening requires more than a high aggregate metric: evidence visibility, entity content and model selection all have to be traceable. So the pipeline keeps raw evidence, records which snapshot each reading came from, and refuses to publish a result whose inputs it cannot verify. What the measurements then show is bounded: no observed mean dual-encoder improvement over a concatenated-feature MLP, and an independent docking gate that fails despite successful pose recovery. These are empirical findings about one corpus and one protocol, not biological validation.

**Stack.** PyTorch for the models, PostgreSQL for provenance-preserving curation, RDKit for standardisation and fingerprints, ESM-2 for protein representations, AutoDock Vina for the separate docking gate, and `uv` for a pinned Python 3.11 environment. Recorded neural-network fits used Apple **MPS**; CUDA was not used in these experiments, and CUDA support in the code is not evidence of NVIDIA execution.

## Architecture

![Independent compound and protein towers with a trainable affine cosine head](paper/figures/figure_2_dual_encoder.svg)

Two independent towers project a 2,048-bit chiral ECFP4 fingerprint and a frozen ESM-2 embedding into a shared 512-dimensional space; an affine map on their cosine similarity produces a score in pKi units. Because the towers are independent, compound projections can be precomputed before any sequence query arrives. The bounded cosine head limits expressiveness: contrastive training and cross-attention were not implemented.

## Key results

| Evaluation | Observed result | Interpretation |
| --- | --- | --- |
| Historical January → September 2026, new to fitting | Concat-MLP macro AUROC 0.788873; dual encoder 0.781229 | No demonstrated dual-encoder improvement; no equivalence or significance claim |
| Headline coverage | 22,221 pairs, 992 targets; 123 meet ≥5 positives and ≥5 negatives | Macro ranking metrics describe the rankable minority |
| Target-conditioned ligand 1-NN | Recurrent AUROC ≈0.9642; absent-from-A ≈0.6685 | A contrast between different populations, not an isolated causal effect of recurrence |
| Separate CA2 docking gate | AUROC 0.624060 versus declared 0.70 threshold: FAIL | This protocol and cohort fail the standalone ranking requirement |
| Known-pose recovery | 1.514 Å RMSD | Pose recovery and affinity ranking are different tests |

![Historical ranking comparison with individual seed runs](paper/figures/figure_3_ranking_results.svg)

Seed variation is training variation, not a target-level confidence interval, and the seed ranges drawn in the figures are not confidence intervals. No paired target-level uncertainty analysis was performed for the historical model comparison, so no difference between models here is shown to be distinguishable from noise.

Full results and denominators: [M11h report](reports/m11h_results.md). Consolidated interpretation: [closeout report](reports/project_closeout.md). Figure sources and scope: [FIGURES](paper/FIGURES.md).

## What is contributed

An integrated, auditable case study: preserved raw evidence; exact and censored endpoint semantics kept apart; counted snapshot matching with row locators; independent historical, increment and full-B readings; bounded-memory processing; content-bound feature resolution; and reproducible results behind fail-closed publication checks. Protein-language-model dual encoders and temporal benchmarks have prior art. No claim of a first method, a novel architecture, or state-of-the-art performance is made. See [related work](docs/NOVELTY.md).

## Mode 1 — saved-prediction reproduction

Run from the repository root. The project declares Python 3.11 and pins its environment in `uv.lock`.

```bash
uv sync --all-groups
uv run python -m seq2lead.asof.verify_results data/asof/m11h/run-20261005T134842Z
```

This verifies the saved predictions, evaluation table and published results **without fitting anything**. Checks it cannot perform are reported as unavailable rather than passed: in a review copy the model checkpoints and the training-membership export are absent, and omitted feature-cache bytes are not verified.

The full supported scoring and publication sequence writes derived files, so run it in a disposable clone if you want the reviewed copy untouched:

```bash
uv run python -m seq2lead.asof.recompute       data/asof/m11h/run-20261005T134842Z
uv run python -m seq2lead.asof.verify_results  data/asof/m11h/run-20261005T134842Z
uv run python -m seq2lead.asof.publish         data/asof/m11h/run-20261005T134842Z
```

The three stages are deliberately separate: publication refuses a verification record that is not bound to the inputs as they currently stand. For read-only verification from Python:

```python
from seq2lead.asof.verify_results import verify

verdict = verify("data/asof/m11h/run-20261005T134842Z")
print(verdict["passed"], verdict["checks_not_performed"])
```

That is mode 1 in full: no refitting, nothing downloaded. **Mode 3, a raw-to-fit rebuild, is a different claim** — it has further local dependencies and historical identity assumptions, is not a tested one-command fresh clone, and is not independently reproducible while the September snapshot remains a rolling release. See the [reproduction guide](docs/REPRODUCIBILITY.md) and [release scope](docs/RELEASE_SCOPE.md).

## Mode 2 — live sequence-query ranking

This is the product path, and it does **not** work from a clone. `seq2lead rank` takes one protein FASTA and a frozen library and returns a ranked list with predicted pKi. It needs a trained checkpoint, a registered frozen library in a local PostgreSQL corpus, the exact bound feature caches and the pinned ESM-2 weights — all stored separately, none tracked here. The CLI refuses a missing or incompatible feature binding rather than quietly using whichever cache is current. A recorded example output is at `reports/examples/rank_demo.txt`; prerequisites, evidence modes and the exact commands are in the [demo guide](docs/DEMO.md). A predicted pKi is not a calibrated probability and not evidence of binding. Evaluation evidence display stays off: there is no partition-filtered evidence API, so evaluation mode shows no evidence at all.

## Development checks

```bash
uv run ruff check .
uv run ruff format --check .
uv run pytest --junit-xml=test-results.xml
```

For a database:

```bash
cp .env.example .env
docker compose --env-file .env -f docker/compose.yml up -d
uv run seq2lead db migrate
uv run seq2lead db ping
```

The **full local test suite** is a software check, not a reproduction of the benchmark — see the three definitions in [release scope](docs/RELEASE_SCOPE.md). It needs the complete local artifact set and PostgreSQL. **A fresh clone cannot run all of it**: 16 files, **415 tests**, need the git-ignored docking runs, M9 records, feature caches and the membership export, so CI collects the remaining **909 of 1,324** and says which files it skipped. The boundary was measured from real workflow runs, not from a clone — an earlier estimate of 7 files was wrong because it modelled CI as having no database when CI in fact has a live, empty one. The first fully green run is [37429970087](https://github.com/rinatrizvanov/seq2lead/actions/runs/37429970087) — 875 passed, 12 skipped, 0 failed, of 887 collected under the previous 18-file list, before the two CLI help-output tests were repaired and returned to CI. Do not treat skipped or unavailable checks as passed — see [CI scope](docs/CI_SCOPE.md), which records each run's totals.

## Data and model boundaries

Ki, IC50, Kd and EC50 are retained separately. Regression targets use exact Ki alone; decisive censored evidence may supply ranking classes but never a point value. Primary activity is pKi ≥ 6. Discordance and contradiction are represented explicitly rather than averaged away. Whether Ki may be pooled across assay contexts at all remains an open question, so every count assumes the current pooling rule: [assay-variance analysis](reports/assay_variance.md).

Compounds use 2,048-bit chiral ECFP4 stored as 256 packed bytes. Proteins use revision-pinned ESM-2, mean-pooled over residues excluding special tokens. Full-length embeddings are provisional beyond the 1,022-residue training window, and 40,000 residues is a refusal point. Input-only representations may cover every role; learned transforms and model parameters fit A-training evidence only.

Historical snapshot A is the 2026-01-01 deposit, DOI [10.6075/J0V40W61](https://doi.org/10.6075/J0V40W61); B is the locally pinned BindingDB 202609 release. Historical labels are never rewritten using B. Recurrence is defined against the actual fitting rows. Both increment interpretations and both consistency branches remain available, and every reported number names its cell.

## Repository and artifact scope

This tree holds source, configs, reports, the preserved execution scripts and a narrow allow-list of small review artifacts, including the saved M11h predictions. Excluded: raw archives, database volumes, large feature caches, membership exports, model checkpoints, ligand and pose directories, third-party binaries and secrets. The learned protein transform is included as a small reproducibility artifact; it is not an embedding cache. **A private repository is not a backup of the excluded evidence** — see the [archive policy](docs/ARCHIVE_POLICY.md) and `configs/manifests/closeout_inventory.json`.

Before any public release, see the [attribution and licensing audit](docs/ATTRIBUTION.md). In short: there is **no `LICENSE` file in effect** — a draft sits at `LICENSE.draft` for owner review and covers project code only. Dataset and model terms are separate. BindingDB's own `Curation/DataSource` column shows **ChEMBL is the largest single source at 50.81%** of the corpus. ChEMBL's terms are **not** in doubt: ChEMBL's own site and BindingDB's terms page both state **CC BY-SA 3.0 Unported** for ChEMBL-sourced data, so share-alike does apply to it, and **16 shipped files** are assigned CC BY-SA 3.0 on row-level provenance, against 3 under CC BY 3.0 — enumerated per file in `DATA_LICENSE.draft` and in [`configs/manifests/release_inventory.json`](configs/manifests/release_inventory.json). Share-alike does not reach the project code, which is independently authored software that reads the data rather than an adaptation of it. One narrower question is genuinely open — whether model predictions and fitted statistics are themselves "Adaptations" — and both affected artifacts are labelled CC BY-SA 3.0 so that they are correctly licensed either way; the reasoning, and what is a packaging decision rather than a legal conclusion, is in `DATA_LICENSE.draft`. ESM-2's weights are MIT-declared at the pinned revision but are not redistributed here, and MIT does not speak to model outputs. Do not infer redistribution permission from a software licence.

## Start here

- [Owner decisions](docs/OWNER_DECISIONS.md) — **start here**: every remaining approval, and the disclosed scientific limitations kept separate from them.
- [Review scope](REVIEW.md) — what this copy can and cannot verify, and which evidence is missing.
- [Reproduction guide](docs/REPRODUCIBILITY.md) — how to run the saved-prediction reproduction, and why a raw-to-fit rebuild is not supported from a clone.
- [Demo guide](docs/DEMO.md) — live ranking prerequisites and evidence modes.
- [Scientific manuscript](paper/manuscript.md) — illustrated Markdown draft; [editable Word version](paper/Seq2Lead_scientific_manuscript.docx). An unreviewed technical-report draft.
- [Next steps](docs/NEXT_STEPS.md) — remaining work and publication review.
- [Attribution and licensing audit](docs/ATTRIBUTION.md) — row-level provenance for every shipped data file, the ESM-2 terms at the pinned revision, and the decisions still open.
- [CI scope](docs/CI_SCOPE.md) — which 909 tests CI covers and which 415 it cannot, measured from real workflow runs.
- [Release scope](docs/RELEASE_SCOPE.md) — what a clone reproduces (the benchmark, from saved predictions) versus what it cannot (a rebuild from raw), with the excluded artifacts measured.
- [Backup checklist](docs/BACKUP_CHECKLIST.md) — the 1.85 GB that exists only on local disk, with its verification procedure.
- [Archive policy](docs/ARCHIVE_POLICY.md) — what to retain before deleting review ZIPs.
- [Preserved execution scripts](scripts/asof/README.md) — the code actually run, with recorded hashes.
