# M9 — dual encoder

Generated 2026-09-30 18:11 UTC.

> **Endpoint status: `provisional_pooled_for_exploratory_benchmark`**
>
> Ki is pooled across assays **provisionally, for exploratory benchmarking only**. The pre-declared decision rule returned `pool`, but it compared medians and was blind to a tail in which a quarter of assay-spanning pairs differ by more than tenfold. Pooling is therefore an operating assumption carried forward under protest, not a validated conclusion. Any result computed on pooled Ki inherits this limitation and must state it.
>
> See [`assay_variance.md`](assay_variance.md) for the evidence and its limits.

Contract: [`../docs/M9.md`](../docs/M9.md), frozen before fitting. Experiment `m9-dual-encoder-v1`, inputs pinned by digest and identical to M8.

## These numbers are exploratory

**The M8 test partitions had already been inspected** before M9 existed — the baseline leaderboard was read, discussed and corrected twice against them. A model compared against those same sets is being compared on data the project has already seen, so this is **not** a confirmatory held-out result and is not presented as one.

What was done to keep it honest anyway:

- **Every configuration was selected on validation alone.** No test score chose a projection dimension, an epoch, a seed or a decision to keep tuning.
- **The selection was frozen to disk before the test pass ran** (`reports/results/m9_selection.json`, frozen 2026-09-30T16:47:21+00:00). The test phase reads that file and cannot alter it.
- **Disappointing test numbers did not trigger more tuning.** The search is the two configurations declared in the contract, and it stayed that way.

A genuinely confirmatory result needs a partition nobody has looked at. That is the M11 as-of temporal evaluation, which trains on an older archived BindingDB release and tests on pairs new in a later one.

`label_reversal-v3` remains **unscored**, as in M8.

## What one run is

Each of the 20 final runs is one (split, seed) pair, and does exactly three things in order:

1. **Fit** on the training partition only.
2. **Select its own checkpoint** by validation RMSE, evaluated once per epoch, restoring the best-validation weights and early-stopping after 5 epochs without improvement.
3. **Score test once**, after the weights are fixed.

Test metrics never select an epoch, a seed or a configuration. The projection dimension was chosen in a separate earlier phase that touched no test partition at all.

## Architecture and the scoring head

Two towers that never see each other's input: ECFP4 → `Linear(2048, h)` → ReLU → `Linear(h, h)`, and ESM-2 → `Linear(1280, h)` → ReLU → `Linear(h, h)`. That independence is what lets the compound library be projected **once** and reused for every query, which is the property the CLI runs on.

**The head is an affine map on cosine, and the units are pKi.** Cosine alone is bounded to [-1, 1] and the labels run roughly 2–12, so it cannot represent the target. `sigmoid(cosine)` cannot either, and it is **not** a calibrated probability — claiming that would need a reliability diagram and an ECE, neither of which exists here. So:

```
score = a * cos(z_compound, z_protein) + b       # a, b trainable, output in pKi
```

At the selected `h=512` the final models hold **2,230,274 parameters** (two towers of `in→h` and `h→h`, plus the scale and offset). The h=256 pilot used for runtime scoping held 984,066; that figure describes the pilot, not these models.

### The learned scale and offset

`a` is **unconstrained**. Its sign matters: a negative scale reverses the cosine ordering, so ranking by raw cosine would be exactly backwards for such a model. **The CLI therefore ranks by the affine score — the predicted pKi — which carries the sign with it.** Two tests cover this, including one that flips a trained scale and asserts the CLI's ranking flips with it.

| Split | a (scale) | b (offset) | predicted pKi: min | max | mean | sd |
| --- | --- | --- | --- | --- | --- | --- |
| `random_pair-v3` | 7.125 | 6.906 | 0.47 | 13.06 | 7.08 | 1.36 |
| `cold_protein-v3` | 7.044 | 7.051 | 1.63 | 10.42 | 6.53 | 0.93 |
| `chemistry_disjoint-v3` | 6.868 | 7.002 | 0.89 | 11.95 | 7.12 | 1.25 |
| `temporal_proxy-v4` | 7.051 | 6.977 | 1.09 | 11.26 | 7.07 | 0.92 |

**All 20 fitted models learned a positive scale** (range 6.85–7.15), so cosine ordering happens to be preserved in every one of them. That is an observation about these runs, not a guarantee — nothing in the objective prevents a negative scale, which is why the CLI does not depend on it.

## Configuration selection (validation only)

Two configurations, `h ∈ {256, 512}`, fitted once each per split on seed 20260930. Metric: validation RMSE, lower wins.

| Split | h=256 | h=512 | chosen | validation pairs | targets |
| --- | --- | --- | --- | --- | --- |
| `random_pair-v3` | 0.7719 | 0.7455 | h=512 | 40,905 | 2,140 |
| `cold_protein-v3` | 1.5859 | 1.5536 | h=512 | 40,892 | 1,007 |
| `chemistry_disjoint-v3` | 0.9569 | 0.9373 | h=512 | 41,448 | 1,979 |
| `temporal_proxy-v4` | 1.1243 | 1.1116 | h=512 | 36,028 | 752 |

`h=512` won on every split. Coverage was sufficient everywhere, so the declared fallback — carry over the `random_pair-v3` choice where validation is too thin to separate configurations — was never invoked.

### Training and validation curves

Reported because the stopping behaviour differs sharply across splits, and that is an **observation**, not a diagnosis. Training loss falls throughout on every split; what changes is how quickly validation stops following it.

| Split | epochs run | best epoch | stopped early | first→last val RMSE | train MSE first→last |
| --- | --- | --- | --- | --- | --- |
| `random_pair-v3` | 20 | 19 | False | 0.9098 → 0.7474 | 1.0850 → 0.0916 |
| `cold_protein-v3` | 6 | 1 | True | 1.5536 → 1.6369 | 0.9415 → 0.2309 |
| `chemistry_disjoint-v3` | 11 | 6 | True | 0.9943 → 0.9399 | 1.0027 → 0.1557 |
| `temporal_proxy-v4` | 7 | 2 | True | 1.1423 → 1.1401 | 1.0306 → 0.2291 |

**Stopping rule:** best-validation checkpoint restored; stop after 5 epochs without improvement; cap 20 epochs.

On `cold_protein-v3` validation RMSE rises monotonically from epoch 1 while training MSE falls by a factor of four — the fit is working, and each additional epoch makes held-out proteins worse. `temporal_proxy-v4` bottoms at epoch 2 and `chemistry_disjoint-v3` at epoch 6, while `random_pair-v3` was still improving at epoch 19 and never early-stopped. **What that shows is where this **These are observations from these particular fits**, under one learning rate, one batch size and one head, with two projection dimensions searched. They do not establish a transfer limit of the architecture: a different optimiser schedule, regulariser or head might move them, and none was tried. What they do rule out is a broken optimiser -- training loss descends everywhere. No tuning was restarted against test scores.

## Results against the M8 baselines

Same corrected metric implementation (`m8/v2`), same cohorts, same eligibility. Temporal figures are the **new-pair stratum**. ± is spread across seeds, which is optimiser variance and **not** uncertainty across targets.

### Ranking AUROC

| Model | `random_pair-v3` | `cold_protein-v3` | `chemistry_disjoint-v3` | `temporal_proxy-v4` |
| --- | --- | --- | --- | --- |
| `B0-target-mean` | 0.5000 | 0.5000 | 0.5000 | 0.5000 |
| `B1-ligand-ecfp4-lgbm` | 0.6889 ± 0.0001 | 0.5892 ± 0.0000 | 0.6608 ± 0.0015 | 0.6033 ± 0.0000 |
| `B3L-ligand-1nn` | 0.8192 | 0.5000 | 0.7814 | 0.5748 |
| `B4-concat-mlp` | 0.8499 ± 0.0016 | 0.6016 ± 0.0008 | 0.7941 ± 0.0034 | 0.6401 ± 0.0043 |
| `M9-dual-encoder` | 0.8596 ± 0.0024 | 0.6005 ± 0.0027 | 0.7908 ± 0.0026 | 0.6279 ± 0.0081 |

### Regression RMSE (lower is better)

| Model | `random_pair-v3` | `cold_protein-v3` | `chemistry_disjoint-v3` | `temporal_proxy-v4` |
| --- | --- | --- | --- | --- |
| `B0-target-mean` | 1.049 | 1.704 | 1.119 | 1.452 |
| `B1-ligand-ecfp4-lgbm` | 1.261 | 1.462 | 1.328 | 1.304 |
| `B3L-ligand-1nn` | 0.960 | 1.704 | 1.055 | 1.535 |
| `B4-concat-mlp` | 0.875 | 1.554 | 1.023 | 1.337 |
| `M9-dual-encoder` | 0.827 | 1.455 | 1.057 | 1.289 |

### Reading this

**The dual encoder does not win outright.** On AUROC it beats B4 on 1 of 4 splits (`random_pair-v3`) and loses on 3 (`cold_protein-v3`, `chemistry_disjoint-v3`, `temporal_proxy-v4`). On RMSE it is ahead on three of four. That is the result; it was not required to win and no tuning was done to make it.

The two architectures are close enough on these splits that the difference is not the interesting part. What is interesting is that **both collapse on `cold_protein-v3`** — the dual encoder to 0.60 AUROC from 0.86 — and that a within-target nearest-ligand lookup (B3L) still reaches 0.82 on `random_pair-v3` with no protein information beyond the target key.

Per-target metrics, prevalence, scored/skipped counts and the near-homolog and long-sequence breakouts are preserved in `reports/results/m9_v1_summary.json` under the same schema as M8.

## The ranking CLI

```bash
uv run seq2lead rank \
  --sequence-file reports/examples/query_target.fasta \
  --library curated-ki-25k-v1 \
  --model data/m9/final/M9-dual-encoder__cold_protein-v3__seed20260930.pt \
  --top-k 15 --evidence-mode demo
```

### Which checkpoint, and why

**Split `cold_protein-v3`, seed 20260930, h=512.**

CLI queries are arbitrary user-supplied sequences, most likely unseen in training, which is the cold-protein setting. The checkpoint therefore comes from cold_protein-v3. The seed is the lowest VALIDATION RMSE within that split. Test scores were not consulted.

| Seed | validation RMSE | best epoch |
| --- | --- | --- |
| 20260930 | 1.5536 | 1 ← chosen |
| 20260932 | 1.5587 | 3 |
| 20260931 | 1.5617 | 1 |
| 20260933 | 1.5821 | 3 |
| 20260934 | 1.5826 | 4 |

The compound projections the CLI scores against are computed from the checkpoint's own compound tower and the library's frozen member list, and the query embedding is standardised with **the transform stored inside that checkpoint** — not one re-derived at inference. A checkpoint saved without its transform is refused outright, because the same weights on differently scaled inputs are a different model.

### What it may and may not claim

- Scores are **predicted pKi**, not probabilities and not calibrated confidences.
- Known measurements are shown in `demo` mode, labelled **prior measured evidence**, in their own column. They are **not** independent validation: the model was not asked to discover them and they may have been in its training partition.
- **No compound it surfaces has been experimentally tested by this project.**

A worked run is in [`examples/rank_demo.txt`](examples/rank_demo.txt).

## The demonstration library

`curated-ki-25k-v1` — **25,000 compounds**, digest `7c0ea2ec20f8b30e…`. Selection rule: compounds with at least one eligible exact Ki measurement in the pinned release, ordered by `compound.id`, capped at 25,000.

**This is a bounded demonstration library, not a representative sample.** The members are an **ordered eligible prefix**: the lowest 25,000 compound ids that satisfy the rule, spanning ids 1,347,358–1,496,949. That span holds 149,592 ids, so 124,592 ids inside it are **not** members — the prefix is ordered, not contiguous, because ineligible compounds are skipped. Either way the ordering reflects ingestion and source order rather than chemistry. It was frozen before any ranking was produced and has not been changed since.

| Property | Library | Full eligible pool |
| --- | --- | --- |
| Compounds | 25,000 | 232,708 (10.7% covered) |
| Mean heavy atoms | 33.8 | 32.6 |
| Heavy-atom range | 1–424 | — |
| With stereocentres | 10,783 (43.1%) | — |
| Targets measured against | 2,124 of 3,606 (58.9%) | — |

## Implemented versus deferred

| Ablation | Status | Why |
| --- | --- | --- |
| Dual encoder, regression head | **implemented** | the bounded core of this milestone |
| Contrastive training | deferred | outside the bounded core. If run, negatives come only from eligible **measured** training evidence — never unmeasured pairs — and contradictory or ambiguous evidence is kept out of confident negatives |
| Cross-attention variant | deferred | a different architecture, not a setting |
| ProtBert / PLM sweep | deferred | needs a second embedding cache |
| Retrieval as a feature | deferred | ships as an interpretability layer first, per the original plan's own Risk 2 |

None of these was deferred on the basis of a `label_reversal-v3` score, because that split was not scored.

## Corrections applied after review

This report supersedes the first M9 write-up. No model was refitted and no prediction changed; the corrections are to inference binding, displayed evidence, artifact safety and to claims I had got wrong.

| # | Correction | Effect on results |
| --- | --- | --- |
| 1 | **Inference is bound to the checkpoint's feature identities.** `rank_library()` fell back to whatever ECFP4 cache was current; that fallback is removed. Each checkpoint now carries, or has a sidecar recording, its compound cache name and digests and the full protein spec (model commit, pooling, dtype, length policy, max length). Inference refuses without them. | **None.** The bound cache is the one the fallback happened to select, so the demo ranking is byte-identical. The fallback was latent risk, not active corruption. |
| 2 | **Censoring is preserved in displayed evidence.** The old formatter aggregated values without their relation, so `>10000 nM` — a decisive non-binder — rendered as `10000 nM`. | **Wording only in this demo**: all four compounds shown had exact records. Corpus-wide the defect touched **650,516 of 3,233,963 measured records (20.1%)**, and 14% of this very target's records are censored, so a different query or a deeper top-k would have hit it. |
| 3 | **Artifact paths are run-scoped and checked before fitting.** Writes were unconditional and refusal came from the result registry at the very end, after every artifact had been overwritten. | No change to existing artifacts; all 20 runs verify against a frozen manifest. |
| 4 | **The length refusal point is enforced.** 40,001 residues was accepted against a declared 40,000 limit. It is now rejected before ESM-2 loads. | None; the demo query is 443 residues. |

### Claims I had wrong

| Claim | Was | Is |
| --- | --- | --- |
| Final model size | 984,066 parameters | **2,230,274** — I quoted the h=256 *pilot* as though it were the h=512 final models |
| Library shape | "contiguous block" | an **ordered eligible prefix**; the id span holds 149,592 ids for 25,000 members, so 124,592 are absent |
| Previous test count | 461 | **419 passed, 0 failed, 0 skipped** — I counted dots in a `tail`-truncated log |
| Early stopping | "where this architecture stops transferring" | an observation from these fits under one set of hyperparameters |

## Limitations

| # | Limitation |
| --- | --- |
| 1 | **Exploratory, not confirmatory.** The test partitions were inspected during M8. A clean confirmatory number needs the M11 as-of split. |
| 2 | **The head is a bounded cosine times a learned scale.** That is the entire expressive range of the score, and it is an architectural constraint rather than a tuning choice. |
| 3 | **Validation stops improving within a few epochs on three of four splits**, while training loss keeps falling. Reported as an observation; no remedy was attempted in this pass. |
| 4 | **Seed spread is optimiser variance**, not uncertainty across targets. No target-level bootstrap was run, so no confidence intervals are reported. |
| 5 | **The library is an ingestion-order slice**, useful for demonstrating the path end to end and not for characterising chemical space. |
| 6 | **The endpoint is provisional.** Ki pooling across assays was never validated; every number here inherits that. |
| 7 | **Two configurations were searched, not a grid.** A better dual encoder may well exist; this pass does not look for it. |

