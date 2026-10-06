# Seq2Lead

**Auditable protein–ligand ranking and historical BindingDB evaluation.**

Given a protein amino-acid sequence and a defined library, Seq2Lead ranks existing compounds by predicted pKi using chiral ECFP4 fingerprints and frozen ESM-2 representations. The repository also records how the data, endpoints, splits, features, fits and published results were constructed and checked.

**Status: M11h closed; historical evaluation completed.** Results are exploratory, Ki pooling across assays is provisional, and the prospective confirmatory freeze is unsigned. Retrieval is unbuilt; evaluation evidence display is off; `label_reversal-v3` is unscored. The project does not generate molecules or establish experimentally validated new binders.

## Main findings

| Evaluation | Observed result | Interpretation |
| --- | --- | --- |
| Historical January → September 2026, new to fitting | Concat-MLP macro AUROC 0.788873; dual encoder 0.781229 | No demonstrated dual-encoder improvement; no equivalence or significance claim |
| Headline coverage | 22,221 pairs, 992 targets; 123 meet ≥5 positives and ≥5 negatives | Macro ranking metrics describe the rankable minority |
| Target-conditioned ligand 1-NN | Recurrent AUROC ≈0.9642; absent-from-A ≈0.6685 | A contrast between different populations, not an isolated causal effect of recurrence |
| Separate CA2 docking gate | AUROC 0.624060 versus declared 0.70 threshold: FAIL | This protocol/cohort fails the standalone ranking requirement |
| Known-pose recovery | 1.514 Å RMSD | Pose recovery and affinity ranking are different tests |

Seed variation is training variation, not a target-level confidence interval. No paired target-level uncertainty analysis was performed for the historical model comparison. Full results and denominators: [M11h report](reports/m11h_results.md). Consolidated interpretation: [closeout report](reports/project_closeout.md).

## Significance

Useful screening requires more than a high aggregate metric: evidence visibility, entity content and model selection must be traceable. Seq2Lead connects GPU-accelerated PyTorch modelling, PostgreSQL curation and counted historical snapshot evaluation. The preserved comparison shows no observed mean dual-encoder improvement over the concat MLP, while the independent docking gate fails despite successful pose recovery. These are bounded empirical findings rather than claims of biological validation.

## Architecture and results

![Independent compound and protein towers](paper/figures/figure_2_dual_encoder.svg)

![Historical ranking comparison with individual seed runs](paper/figures/figure_3_ranking_results.svg)

Figures use saved evidence; seed ranges are not confidence intervals. [Figure sources and scope](paper/FIGURES.md). Recorded neural-network fits used Apple MPS; CUDA was not used in these experiments.

## Significance

Useful screening requires more than a high aggregate metric: evidence visibility, entity content and model selection must be traceable. Seq2Lead connects GPU-accelerated PyTorch modelling, PostgreSQL curation and counted historical snapshot evaluation. The preserved comparison shows no observed mean dual-encoder improvement over the concat MLP, while the independent docking gate fails despite successful pose recovery. These are bounded empirical findings rather than claims of biological validation.

## Architecture and results

![Independent compound and protein towers](paper/figures/figure_2_dual_encoder.svg)

![Historical ranking comparison with individual seed runs](paper/figures/figure_3_ranking_results.svg)

Figures use saved evidence; seed ranges are not confidence intervals. [Figure sources and scope](paper/FIGURES.md). Recorded neural-network fits used Apple MPS; CUDA was not used in these experiments.

## What is contributed

The main contribution is an integrated, auditable case study: preserved raw evidence; exact/censored endpoint semantics; counted snapshot matching with row locators; independent historical/increment/full-B readings; bounded-memory processing; content-bound feature resolution; and reproducible results with fail-closed publication checks. Protein-language-model dual encoders and temporal benchmarks have prior art. No claim of a first method, novel architecture, or state-of-the-art performance is made. See [related work](docs/NOVELTY.md).

## Start here

- [Reproduction guide](docs/REPRODUCIBILITY.md): saved-prediction reproduction versus full-data refitting.
- [Demo guide](docs/DEMO.md): live ranking prerequisites and evidence modes.
- [Review scope](REVIEW.md): checks possible with this copy and missing evidence.
- [Scientific manuscript](paper/manuscript.md): illustrated Markdown draft; [editable Word version](paper/Seq2Lead_scientific_manuscript.docx).
- [Next steps](docs/NEXT_STEPS.md): apply documentation, create the private repository and review for publication.
- [Archive policy](docs/ARCHIVE_POLICY.md): what to retain before deleting review ZIPs.
- [Preserved execution scripts](scripts/asof/README.md): the code actually run, with recorded hashes.

## Install and reproduce saved results

Run from the repository root. The project declares Python 3.11 and its environment is pinned in `uv.lock`.

```bash
uv sync --all-groups
uv run python -m seq2lead.asof.verify_results data/asof/m11h/run-20261005T134842Z
```

This verifies the available saved predictions, evaluation table and results without fitting. Missing checkpoints and the training-membership export are explicitly reported as unavailable. It does not verify omitted feature-cache bytes.

The complete supported scoring/publication sequence writes derived files; run it in a disposable clone if preserving the reviewed copy:

```bash
uv run python -m seq2lead.asof.recompute data/asof/m11h/run-20261005T134842Z
uv run python -m seq2lead.asof.verify_results data/asof/m11h/run-20261005T134842Z
uv run python -m seq2lead.asof.publish data/asof/m11h/run-20261005T134842Z
```

For explicit read-only verification in Python:

```python
from seq2lead.asof.verify_results import verify
verdict = verify('data/asof/m11h/run-20261005T134842Z')
print(verdict['passed'], verdict['checks_not_performed'])
```

## Development checks

```bash
uv run ruff check .
uv run ruff format --check .
uv run pytest --junit-xml=test-results.xml
```

The full suite needs the full local artifact set and PostgreSQL; this review copy is not a promise that every corpus-backed test runs without them. For a database:

```bash
cp .env.example .env
docker compose --env-file .env -f docker/compose.yml up -d
uv run seq2lead db migrate
uv run seq2lead db ping
```

Do not treat skipped or unavailable checks as passed. The final working-tree suite was reported as 1,318 passed; that historical total is not a fresh-clone guarantee.

## Data and model boundaries

Ki, IC50, Kd and EC50 are retained separately. Regression targets use exact Ki alone; decisive censored evidence may supply ranking classes. Primary activity is pKi ≥6. Discordance and contradictions are explicitly represented. Assay pooling remains provisional: [analysis](reports/assay_variance.md).

Compounds use 2,048-bit chiral ECFP4, stored as 256 packed bytes. Proteins use revision-pinned ESM-2, mean-pooled over residues excluding special tokens. Full-length embeddings are provisional beyond the 1,022-residue training window; 40,000 residues is a refusal point. Input-only representations may cover all roles; learned transforms and model parameters fit A-training only.

Historical A is the 2026-01-01 deposit, DOI [10.6075/J0V40W61](https://doi.org/10.6075/J0V40W61); B is the locally pinned BindingDB 202609 release. Historical labels are not rewritten using B. Recurrence is defined against actual fitting rows. Both increment interpretations and consistency branches remain available.

## Repository and artifact scope

This prepared review tree includes source, configs, reports, preserved scripts and a narrow allow-list of small review artifacts, including saved M11h predictions. Raw archives, database volumes, large feature caches, membership exports, model checkpoints, ligand/pose directories and secrets are excluded. The learned protein transform is included as a small reproducibility artifact; it is not an embedding cache.

A private repository is the initial distribution target. Code-license metadata currently says MIT, but code ownership and a corresponding license file need owner review before public release. Dataset and model terms are separate; older source-license prose is not authoritative for every deposit. Do not infer redistribution permission from a software license. No paper has been submitted or peer reviewed.
