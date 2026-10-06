# Seq2Lead

**Auditable protein–ligand ranking and historical BindingDB evaluation.**

Given a protein amino-acid sequence and a defined compound library, Seq2Lead ranks existing compounds by predicted pKi using chiral ECFP4 fingerprints and frozen ESM-2 representations. The repository also records how the data, endpoints, splits, features, fits and published results were constructed and checked.

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

## Reproduce the saved results

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

Saved-prediction reproduction needs no refitting. A full raw-to-fit rebuild has further local dependencies and historical identity assumptions, and is **not** a tested one-command fresh clone — see the [reproduction guide](docs/REPRODUCIBILITY.md).

## Demo

Live ranking needs a populated database and the feature caches, neither of which ships in a review copy. Prerequisites, evidence modes and the exact commands are in the [demo guide](docs/DEMO.md). Evaluation evidence display stays off: there is no partition-filtered evidence API, so evaluation mode shows no evidence at all.

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

The full suite needs the complete local artifact set and PostgreSQL; a review copy is not a promise that every corpus-backed test runs without them. Do not treat skipped or unavailable checks as passed. The working-tree suite was recorded at 1,318 passed, which is a historical total and not a fresh-clone guarantee.

## Data and model boundaries

Ki, IC50, Kd and EC50 are retained separately. Regression targets use exact Ki alone; decisive censored evidence may supply ranking classes but never a point value. Primary activity is pKi ≥ 6. Discordance and contradiction are represented explicitly rather than averaged away. Whether Ki may be pooled across assay contexts at all remains an open question, so every count assumes the current pooling rule: [assay-variance analysis](reports/assay_variance.md).

Compounds use 2,048-bit chiral ECFP4 stored as 256 packed bytes. Proteins use revision-pinned ESM-2, mean-pooled over residues excluding special tokens. Full-length embeddings are provisional beyond the 1,022-residue training window, and 40,000 residues is a refusal point. Input-only representations may cover every role; learned transforms and model parameters fit A-training evidence only.

Historical snapshot A is the 2026-01-01 deposit, DOI [10.6075/J0V40W61](https://doi.org/10.6075/J0V40W61); B is the locally pinned BindingDB 202609 release. Historical labels are never rewritten using B. Recurrence is defined against the actual fitting rows. Both increment interpretations and both consistency branches remain available, and every reported number names its cell.

## Repository and artifact scope

This tree holds source, configs, reports, the preserved execution scripts and a narrow allow-list of small review artifacts, including the saved M11h predictions. Excluded: raw archives, database volumes, large feature caches, membership exports, model checkpoints, ligand and pose directories, third-party binaries and secrets. The learned protein transform is included as a small reproducibility artifact; it is not an embedding cache. **A private repository is not a backup of the excluded evidence** — see the [archive policy](docs/ARCHIVE_POLICY.md) and `configs/manifests/closeout_inventory.json`.

Before any public release, see the [attribution and licensing audit](docs/ATTRIBUTION.md). In short: there is **no `LICENSE` file**; `pyproject.toml` declares MIT, which is metadata and covers code only. Dataset and model terms are separate — the 2026-01-01 archival deposit declares CC BY 4.0 in its own rights metadata, while a possible ChEMBL-derived share-alike obligation on some shipped rows is **unresolved**. Do not infer redistribution permission from a software licence.

## Start here

- [Review scope](REVIEW.md) — what this copy can and cannot verify, and which evidence is missing.
- [Reproduction guide](docs/REPRODUCIBILITY.md) — saved-prediction reproduction versus full-data refitting.
- [Demo guide](docs/DEMO.md) — live ranking prerequisites and evidence modes.
- [Scientific manuscript](paper/manuscript.md) — illustrated Markdown draft; [editable Word version](paper/Seq2Lead_scientific_manuscript.docx). An unreviewed technical-report draft.
- [Next steps](docs/NEXT_STEPS.md) — remaining work and publication review.
- [Attribution and licensing audit](docs/ATTRIBUTION.md) — what is shipped, under which terms, and the decisions still open.
- [Backup checklist](docs/BACKUP_CHECKLIST.md) — the 1.85 GB that exists only on local disk, with its verification procedure.
- [Archive policy](docs/ARCHIVE_POLICY.md) — what to retain before deleting review ZIPs.
- [Preserved execution scripts](scripts/asof/README.md) — the code actually run, with recorded hashes.
