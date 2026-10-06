# M11h — exploratory as-of fitting and evaluation

**Scope: one bounded exploratory stage.** The confirmatory freeze is **not
signed**, retrieval is **unbuilt**, evaluation evidence display is **off**,
`label_reversal-v3` is **unscored**, and nothing was downloaded or docked. The
study is **exploratory** and the **provisional-pooling** qualification stands:
snapshot B is our already-inspected 202609, so any score here is exploratory
whatever it shows.

Contract: `m11f-asof-evaluation-contract-v5`. Runner: `m11g/runner/v2`.
Run: `20261005T134842Z`, artifacts under `data/asof/m11h/run-20261005T134842Z`. Metric version:
**`m8/v2`**. Manifest: `configs/manifests/m11h_fit.json`.
Recomputed by `seq2lead.asof.recompute` from saved
predictions; nothing was refitted to produce this report.

## 0. This is a corrected report

Six deliberate changes were made after first publication. **No number
produced by a fit changed**: the prediction arrays are the original ones,
verified against the digest the fit recorded, and every metric here is
recomputed from them. Nothing was refitted, retuned or rebuilt.

| | Change | Why |
| --- | --- | --- |
| **C1** | the joint-model comparison no longer claims equivalence | seed spread is observed training variation across five fits. Comparing a between-model difference to it does not test anything, so using it to call two models equivalent was unsupported. |
| **C2** | B3L is described by what it conditions on | the earlier description was factually wrong about the model's inputs. |
| **C3** | the joint-versus-baseline margin is no longer attributed to protein features | a multi-factor difference cannot isolate one factor. |
| **C4** | the seed-invariance claim is withdrawn and replaced by measurements | the claim was inferred from identical ranking metrics without examining the prediction arrays. The inference does not hold. |
| **C5** | between-stratum comparisons are labelled as population contrasts | the populations differ in several ways at once, so the difference between them is not attributable to exposure alone. |
| **C6** | the execution code and a recomputation entry point are in the repository | a published result whose code is not preserved cannot be reproduced. |

One published figure was wrong, and not because of a different computation:

- **B1-ligand-ecfp4-lgbm seed SD on the headline AUROC**: `0.0000` → `3.600e-07`. not a different computation: round(sd, 6) printed a true SD of 3.6e-07 as 0.0, and that zero was then read as the seeds having produced identical fits.

Wording changes are recorded apart from numerical ones because they carry
different risk: a wording change can be checked by reading, a numerical one
cannot. Full record: `data/asof/m11h/closeout-changes.json`.

---

## 1. The result

A model win was not required and none is claimed.

- **`B4-concat-mlp` has the higher observed mean headline AUROC**:
  **0.788873** against `dual-encoder`'s
  **0.781229**, a difference of **0.007644** in B4's
  favour. Observed per-seed ranges: B4 0.7813–0.7930,
  dual encoder 0.7701–0.7889.
- **No paired target-level uncertainty analysis was performed.** Nothing here
  establishes whether that difference is distinguishable from noise, and no
  equivalence between the two models is claimed. Seed spread is observed
  training variation; it is not a confidence interval and is not a test.
- **Both joint models exceed both single-modality and lookup baselines** by
  roughly 0.091 AUROC. §4 explains why
  that margin does **not** isolate a protein-feature contribution.
- **Only 123 of 992 targets clear the
  scoring floor**, so every macro-average here describes a minority of the
  cohort's targets, selected by carrying enough of both classes.

§8 lists what these numbers do and do not support.

---

## 2. What was verified before anything was fitted

| Check | Result |
| --- | --- |
| Pinned inputs verified through the corrected runner | **14 of 14** re-hashed from disk |
| Reuse map `ecfp4` verified against the pinned resolution | 251,917 entries, `fe7dcbffead3…` |
| Reuse map `esm2` verified against the pinned resolution | 3,812 entries, `55feecc8c443…` |
| Role `evaluation` derived from the pinned artifacts | 17,373 compounds, 1,060 sequences |
| Role `train` derived from the pinned artifacts | 209,187 compounds, 3,466 sequences |
| Role `validation` derived from the pinned artifacts | 53,870 compounds, 2,394 sequences |
| Emitted `train` matches the pinned record | 353,957 pairs, `dfe59a028fe0…` |
| Emitted `validation` matches the pinned record | 60,981 pairs, `f2e9febdad66…` |
| Output paths preflighted before the first fit | 30 planned, **0** collisions |

Nothing the run consumed was a caller parameter: the membership, the feature
caches, the reuse maps and every role's entities are derived from the verified
artifact set, as M11g's v5 correction requires. The accepted caches and
extensions were read, never written.

| Guarantee | How it is held |
| --- | --- |
| Learned transforms fitted on A-train only | `ProteinTransform` on 353,957 A-train rows, `fitted_on="a_train"`, frozen and saved (`40154b722869…`) |
| A-validation used only for checkpoint selection | 60,981 pairs, metric `validation_rmse`, direction `minimise` |
| The selection set is the one the runner holds | digest re-derived and refused on mismatch: `faedbaed702e…` |
| Best validation checkpoint restored before evaluation | 22 fits, each checked against its own history |
| Never refit on train + validation | `True` — there is no such code path |

---

## 3. The fits: runtime, stopping and curves

Declared models: **6**, five seeds (`20260930, 20260931, 20260932, 20260933, 20260934`), no sweep and no tuning.
Two models are fitted once because the declared protocol treats them as
deterministic; that is a protocol choice, and §5 reports what the prediction
arrays actually show for the rest.

| Model | Seed | Runtime | Stopping | Best validation RMSE | Curve recorded |
| --- | --- | ---: | --- | ---: | --- |
| `B0-target-mean` | — | 0.0s | — | — | none; this model has no epochs |
| `B1-ligand-ecfp4-lgbm` | 20260930 | 8.5s | 400 rounds — **cap bound** | — | none; this model has no epochs |
| `B1-ligand-ecfp4-lgbm` | 20260931 | 7.5s | 400 rounds — **cap bound** | — | none; this model has no epochs |
| `B1-ligand-ecfp4-lgbm` | 20260932 | 7.7s | 400 rounds — **cap bound** | — | none; this model has no epochs |
| `B1-ligand-ecfp4-lgbm` | 20260933 | 7.1s | 400 rounds — **cap bound** | — | none; this model has no epochs |
| `B1-ligand-ecfp4-lgbm` | 20260934 | 7.3s | 400 rounds — **cap bound** | — | none; this model has no epochs |
| `B2-protein-esm2-lgbm` | 20260930 | 70.8s | 400 rounds — **cap bound** | — | none; this model has no epochs |
| `B2-protein-esm2-lgbm` | 20260931 | 69.8s | 400 rounds — **cap bound** | — | none; this model has no epochs |
| `B2-protein-esm2-lgbm` | 20260932 | 69.8s | 400 rounds — **cap bound** | — | none; this model has no epochs |
| `B2-protein-esm2-lgbm` | 20260933 | 70.2s | 400 rounds — **cap bound** | — | none; this model has no epochs |
| `B2-protein-esm2-lgbm` | 20260934 | 70.3s | 400 rounds — **cap bound** | — | none; this model has no epochs |
| `B3L-ligand-1nn` | — | 0.0s | — | — | none; this model has no epochs |
| `B4-concat-mlp` | 20260930 | 52.4s | best epoch 12 | 0.8077 | best epoch and best validation RMSE only; this model records no per-epoch curve |
| `B4-concat-mlp` | 20260931 | 50.5s | best epoch 11 | 0.8270 | best epoch and best validation RMSE only; this model records no per-epoch curve |
| `B4-concat-mlp` | 20260932 | 50.7s | best epoch 12 | 0.8100 | best epoch and best validation RMSE only; this model records no per-epoch curve |
| `B4-concat-mlp` | 20260933 | 50.9s | best epoch 11 | 0.8098 | best epoch and best validation RMSE only; this model records no per-epoch curve |
| `B4-concat-mlp` | 20260934 | 52.8s | best epoch 12 | 0.8095 | best epoch and best validation RMSE only; this model records no per-epoch curve |
| `dual-encoder` | 20260930 | 124.6s | best epoch 17 of 20, ran to cap | 0.7260 | per-epoch validation RMSE |
| `dual-encoder` | 20260931 | 130.3s | best epoch 17 of 20, ran to cap | 0.7238 | per-epoch validation RMSE |
| `dual-encoder` | 20260932 | 125.4s | best epoch 19 of 20, ran to cap | 0.7249 | per-epoch validation RMSE |
| `dual-encoder` | 20260933 | 127.4s | best epoch 15 of 20, ran to cap | 0.7252 | per-epoch validation RMSE |
| `dual-encoder` | 20260934 | 120.0s | best epoch 14 of 19, early | 0.7221 | per-epoch validation RMSE |

**Failures: none.**

- **The LightGBM round cap bound.** B1 and B2 ran all 400 boosting rounds with
  50-round early stopping wired and never triggered: validation RMSE was still
  improving at the cap, so the cap and not validation decided where they
  stopped. `n_estimators` is a carried-over M8 setting and was not tuned here.
- **B4 records no per-epoch curve.** The accepted `ConcatMLP` restores its best
  epoch and reports that epoch's RMSE but keeps no history, and has no early
  stopping. Only the dual encoder has a full curve. Adding history to accepted
  M8 code mid-run would have changed code the existing leaderboard depends on.

---

## 4. The primary cell, new to fitting — the headline

Cell **`declared_increment/screened_primary`**, the declared primary; stratum **new to fitting**, all three
subgroups, **22,221 pairs**. Scored targets must carry
>=5 actives and >=5 inactives per target.

| Model | Scored targets | AUROC | AUPRC | Recall@10 | EF@1% | EF@5% |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `B0-target-mean` | 123 (123 all-tied) | 0.5000 | 0.5435 | 0.2506 | 1.000 | 1.000 |
| `B1-ligand-ecfp4-lgbm-seed20260930` | 123 | 0.6941 | 0.7322 | 0.3496 | 1.556 | 1.610 |
| `B1-ligand-ecfp4-lgbm-seed20260931` | 123 | 0.6941 | 0.7322 | 0.3496 | 1.556 | 1.610 |
| `B1-ligand-ecfp4-lgbm-seed20260932` | 123 | 0.6941 | 0.7322 | 0.3496 | 1.556 | 1.610 |
| `B1-ligand-ecfp4-lgbm-seed20260933` | 123 | 0.6941 | 0.7322 | 0.3496 | 1.556 | 1.610 |
| `B1-ligand-ecfp4-lgbm-seed20260934` | 123 | 0.6941 | 0.7322 | 0.3496 | 1.556 | 1.610 |
| `B2-protein-esm2-lgbm-seed20260930` | 123 (123 all-tied) | 0.5000 | 0.5435 | 0.2506 | 1.000 | 1.000 |
| `B2-protein-esm2-lgbm-seed20260931` | 123 (123 all-tied) | 0.5000 | 0.5435 | 0.2506 | 1.000 | 1.000 |
| `B2-protein-esm2-lgbm-seed20260932` | 123 (123 all-tied) | 0.5000 | 0.5435 | 0.2506 | 1.000 | 1.000 |
| `B2-protein-esm2-lgbm-seed20260933` | 123 (123 all-tied) | 0.5000 | 0.5435 | 0.2506 | 1.000 | 1.000 |
| `B2-protein-esm2-lgbm-seed20260934` | 123 (123 all-tied) | 0.5000 | 0.5435 | 0.2506 | 1.000 | 1.000 |
| `B3L-ligand-1nn` | 123 (13 all-tied) | 0.6982 | 0.7226 | 0.3508 | 1.575 | 1.537 |
| `B4-concat-mlp-seed20260930` | 123 | 0.7813 | 0.8073 | 0.3798 | 1.907 | 1.898 |
| `B4-concat-mlp-seed20260931` | 123 | 0.7928 | 0.8139 | 0.3971 | 1.809 | 1.930 |
| `B4-concat-mlp-seed20260932` | 123 | 0.7889 | 0.8092 | 0.3895 | 1.885 | 1.814 |
| `B4-concat-mlp-seed20260933` | 123 | 0.7884 | 0.8078 | 0.3870 | 2.124 | 1.939 |
| `B4-concat-mlp-seed20260934` | 123 | 0.7930 | 0.8131 | 0.3888 | 1.876 | 1.941 |
| `dual-encoder-seed20260930` | 123 | 0.7837 | 0.8056 | 0.3815 | 1.962 | 1.870 |
| `dual-encoder-seed20260931` | 123 | 0.7701 | 0.7964 | 0.3812 | 1.948 | 1.776 |
| `dual-encoder-seed20260932` | 123 | 0.7889 | 0.8054 | 0.3774 | 1.772 | 1.784 |
| `dual-encoder-seed20260933` | 123 | 0.7765 | 0.7968 | 0.3745 | 1.830 | 1.794 |
| `dual-encoder-seed20260934` | 123 | 0.7870 | 0.7988 | 0.3851 | 1.726 | 1.716 |

Prevalence over scored targets **0.544**,
over pairs 0.697. AUPRC is uninterpretable without
it. Targets in the slice 992; scored
**123**; excluded below the floor
869.

### What each model conditions on

| Model | Conditions on | Mean headline AUROC |
| --- | --- | ---: |
| `B0-target-mean` | target-conditioned constant (training mean for the target) | 0.500000 |
| `B1-ligand-ecfp4-lgbm` | **ligand-only** — the target is not an input at all | 0.694096 |
| `B2-protein-esm2-lgbm` | protein-only — one score per target, so ties within it | 0.500000 |
| `B3L-ligand-1nn` | **target-conditioned chemical nearest-neighbour lookup** — the neighbour is chosen among compounds measured against *this target* in training | 0.698151 |
| `B4-concat-mlp` | joint — concatenated fingerprint and protein embedding | 0.788873 |
| `dual-encoder` | joint — independent projections, cosine, affine head | 0.781229 |

**`B3L-ligand-1nn` is not a ligand-only model.** It takes the compounds measured
against *this target* in training and returns the pKi of the ECFP4-nearest one,
so target identity is an input — it is a **target-conditioned chemical
nearest-neighbour lookup**. An earlier revision of this report called it
ligand-only, which was wrong. **`B1-ligand-ecfp4-lgbm` is the ligand-only
model**: the target is not an input to it in any form.

**The joint-versus-baseline margin does not isolate a protein-feature
contribution.** The joint models lead `B1` (ligand-only) by
0.095 and `B3L` (target-conditioned
lookup) by 0.091 AUROC. But the joint
models differ from each baseline in **more than one way at once** — they see
protein features, they see both modalities jointly, and they are trained
end-to-end. `B2-protein-esm2-lgbm` shows that protein features *alone* produce a
tied ranking within every target, and no ablation removing protein features from
a joint architecture while holding the rest fixed was run. So the margin is
consistent with protein features contributing and does not establish it.

**Two models score exactly 0.5000 AUROC, and that is correct.** `B0-target-mean`
predicts one value per target and `B2-protein-esm2-lgbm` takes only the protein
embedding, so within a target every compound receives an identical score. Every
target is all-tied, and a tied ranking is 0.5 by construction.

---

## 5. Seeds: what varied, and what did not

| Model | Seeds | Mean AUROC | AUROC SD | Predictions bit-identical | max abs Δ pKi | mean abs Δ pKi | Rows differing |
| --- | ---: | ---: | ---: | --- | ---: | ---: | ---: |
| `B0-target-mean` | 1 (fitted once) | 0.500000 | — | n/a | n/a | n/a | n/a |
| `B1-ligand-ecfp4-lgbm` | 5 | 0.694096 | 3.6e-07 | **False** | 8.96e-05 | 7.32e-06 | 26,444 / 26,444 |
| `B2-protein-esm2-lgbm` | 5 | 0.500000 | 0 | **False** | 1.05 | 0.0273 | 26,444 / 26,444 |
| `B3L-ligand-1nn` | 1 (fitted once) | 0.698151 | — | n/a | n/a | n/a | n/a |
| `B4-concat-mlp` | 5 | 0.788873 | 0.00474 | **False** | 2.89 | 0.444 | 26,444 / 26,444 |
| `dual-encoder` | 5 | 0.781229 | 0.00782 | **False** | 3.19 | 0.433 | 26,444 / 26,444 |

### Correction: the seeds did not produce identical fits

An earlier revision of this report stated that B1 and B2 were *seed-invariant*
and that their five seeds were *five identical fits*. **That was wrong.** It was
inferred from identical ranking metrics, which does not follow. Measured against
the saved prediction arrays:

- **`B1-ligand-ecfp4-lgbm`**: predictions are **not** bit-identical across seeds. Every
  one of 26,444 rows differs; maximum absolute difference
  **8.96e-05 pKi**, mean
  7.32e-06 pKi.
- **`B2-protein-esm2-lgbm`**: predictions are **not** bit-identical across seeds. Every
  one of 26,444 rows differs; maximum absolute difference
  **1.05 pKi**, mean
  0.0273 pKi.

Three different things had been run together, and they come apart:

| | B1 | B2 |
| --- | --- | --- |
| Identical **fits** | no | no |
| Identical **predictions** | no | no |
| Identical **ranking metrics** | no — AUROC varies by 6.6e-07 | **yes**, exactly |

- **B2's metrics are pinned for a structural reason, not an agreement between
  fits.** Its score is constant within every target (measured: 1,041 of 1,041),
  so every ranking is all-tied and AUROC is exactly 0.5 whatever the values are.
  Its predictions move by up to
  1.05 pKi beneath a metric
  that cannot register the movement.
- **B1's metrics are nearly but not exactly invariant.** Its prediction
  differences (8.96e-05 pKi) are
  far smaller than the gaps between most compounds' scores, so almost no
  pairwise order flips — but some do, and the AUROC moves accordingly.
- **The reported SD of `0.0000` was a rounding artifact.** B1's true SD is
  3.6e-07; the published figure applied
  `round(sd, 6)`. SDs are now reported at full precision.

### Cause: unresolved from existing records

B1 and B2 are given `random_state=seed`, and LightGBM's defaults here leave no
sampling stochasticity (`subsample=1.0`, `subsample_freq=0`,
`colsample_bytree=1.0`). Two mechanisms in the recorded configuration could
still produce run-to-run differences:

1. **`n_jobs=-1` with LightGBM's default `deterministic=False`.** Multi-threaded
   histogram construction reduces floating-point sums in a non-fixed order, so
   bin boundaries can differ slightly between runs regardless of the seed. A
   flipped split then cascades, which would explain why B2's 1,280 dense
   continuous features diverge far more than B1's sparse binary ones.
2. **`random_state` seeding internal tie-breaking**, which is not disabled by
   the absence of subsampling.

**The existing records cannot separate these.** There is exactly one fit per
seed and no same-seed repeat, so seed-driven and scheduling-driven variation are
not distinguishable from what was saved. Settling it needs refits — the same
seed twice, and a run with `deterministic=True, n_jobs=1` — which this closeout
excludes. **Recorded as unresolved**; no cause is asserted.

**No paired target-level uncertainty analysis was performed, so no claim about whether any difference between models is distinguishable from noise is made or supported here.**

---

## 6. Contrasts between evaluation populations

**These are different populations, not one model's performance moving.** The
strata differ in size, in which targets they contain, in how many clear the
scoring floor, and in exposure to fitting. A difference between two rows below
is a contrast between populations and is not attributable to any single one of
those causes without analysis that was not performed.

Mean AUROC over seeds, primary cell, **with the scored-target count beside each**:

| Population | Pairs | Scored targets | `B0` | `B1` | `B2` | `B3L` | `B4` | `dual` |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| new to fitting (headline) | 22,221 | **123** | 0.5000 | 0.6941 | 0.5000 | 0.6982 | 0.7889 | 0.7812 |
| absent from A (strictest) | 19,517 | **98** | 0.5000 | 0.6533 | 0.5000 | 0.6685 | 0.7690 | 0.7583 |
| reserved for validation | 1,018 | **6** | 0.5000 | 0.5902 | 0.5000 | 0.6992 | 0.7760 | 0.8047 |
| recurrent — **supplied to fitting** | 4,099 | **29** | 0.5000 | 0.7143 | 0.5000 | 0.9642 | 0.8808 | 0.9592 |

The largest contrast is on the target-conditioned lookup:
`B3L-ligand-1nn` scores 0.9642 on the **recurrent** population
(4,099 pairs, 29 scored targets) and 0.6685 on the
**absent-from-A** population (19,517 pairs, 98 scored
targets). The recurrent pairs were supplied to fitting, and B3L retrieves the
nearest measured neighbour for the same target, so on those pairs it is close
to reading back training data. That is a plausible reading of the contrast,
not a measured decomposition: the two populations also differ in target
composition (29 against 98 scored targets) and in size, and
nothing here separates those contributions. **Never pooled into the headline.**

**The validation-reserved population clears the floor on 6 targets only**,
so its figures are reported for completeness and carry no reading. These pairs
participated in model selection and are kept identifiable for that reason.

---

## 7. All four cells, on the headline stratum

Every cell is scored from the **same fitted models** — the four are slices of one
evaluation table, not four separate experiments.

On `B4-concat-mlp`, switching the increment arm changes mean AUROC by
0.0225 and switching the consistency branch by 0.000373. The
readings in §1 and §4 are the same in every cell, which is the point of carrying
all four rather than selecting one.

**`declared_increment/screened_primary`** **(primary)** — 22,221 new-to-fitting pairs, 26,320 eligible in the cell

| Model | Scored targets | AUROC | AUPRC | Recall@10 | EF@1% | EF@5% |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `B0-target-mean` | 123 (123 all-tied) | 0.5000 | 0.5435 | 0.2506 | 1.000 | 1.000 |
| `B1-ligand-ecfp4-lgbm-seed20260930` | 123 | 0.6941 | 0.7322 | 0.3496 | 1.556 | 1.610 |
| `B1-ligand-ecfp4-lgbm-seed20260931` | 123 | 0.6941 | 0.7322 | 0.3496 | 1.556 | 1.610 |
| `B1-ligand-ecfp4-lgbm-seed20260932` | 123 | 0.6941 | 0.7322 | 0.3496 | 1.556 | 1.610 |
| `B1-ligand-ecfp4-lgbm-seed20260933` | 123 | 0.6941 | 0.7322 | 0.3496 | 1.556 | 1.610 |
| `B1-ligand-ecfp4-lgbm-seed20260934` | 123 | 0.6941 | 0.7322 | 0.3496 | 1.556 | 1.610 |
| `B2-protein-esm2-lgbm-seed20260930` | 123 (123 all-tied) | 0.5000 | 0.5435 | 0.2506 | 1.000 | 1.000 |
| `B2-protein-esm2-lgbm-seed20260931` | 123 (123 all-tied) | 0.5000 | 0.5435 | 0.2506 | 1.000 | 1.000 |
| `B2-protein-esm2-lgbm-seed20260932` | 123 (123 all-tied) | 0.5000 | 0.5435 | 0.2506 | 1.000 | 1.000 |
| `B2-protein-esm2-lgbm-seed20260933` | 123 (123 all-tied) | 0.5000 | 0.5435 | 0.2506 | 1.000 | 1.000 |
| `B2-protein-esm2-lgbm-seed20260934` | 123 (123 all-tied) | 0.5000 | 0.5435 | 0.2506 | 1.000 | 1.000 |
| `B3L-ligand-1nn` | 123 (13 all-tied) | 0.6982 | 0.7226 | 0.3508 | 1.575 | 1.537 |
| `B4-concat-mlp-seed20260930` | 123 | 0.7813 | 0.8073 | 0.3798 | 1.907 | 1.898 |
| `B4-concat-mlp-seed20260931` | 123 | 0.7928 | 0.8139 | 0.3971 | 1.809 | 1.930 |
| `B4-concat-mlp-seed20260932` | 123 | 0.7889 | 0.8092 | 0.3895 | 1.885 | 1.814 |
| `B4-concat-mlp-seed20260933` | 123 | 0.7884 | 0.8078 | 0.3870 | 2.124 | 1.939 |
| `B4-concat-mlp-seed20260934` | 123 | 0.7930 | 0.8131 | 0.3888 | 1.876 | 1.941 |
| `dual-encoder-seed20260930` | 123 | 0.7837 | 0.8056 | 0.3815 | 1.962 | 1.870 |
| `dual-encoder-seed20260931` | 123 | 0.7701 | 0.7964 | 0.3812 | 1.948 | 1.776 |
| `dual-encoder-seed20260932` | 123 | 0.7889 | 0.8054 | 0.3774 | 1.772 | 1.784 |
| `dual-encoder-seed20260933` | 123 | 0.7765 | 0.7968 | 0.3745 | 1.830 | 1.794 |
| `dual-encoder-seed20260934` | 123 | 0.7870 | 0.7988 | 0.3851 | 1.726 | 1.716 |

**`declared_increment/unscreened_sensitivity`** — 22,251 new-to-fitting pairs, 26,421 eligible in the cell

| Model | Scored targets | AUROC | AUPRC | Recall@10 | EF@1% | EF@5% |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `B0-target-mean` | 123 (123 all-tied) | 0.5000 | 0.5437 | 0.2490 | 1.000 | 1.000 |
| `B1-ligand-ecfp4-lgbm-seed20260930` | 123 | 0.6945 | 0.7323 | 0.3476 | 1.557 | 1.608 |
| `B1-ligand-ecfp4-lgbm-seed20260931` | 123 | 0.6945 | 0.7323 | 0.3476 | 1.557 | 1.608 |
| `B1-ligand-ecfp4-lgbm-seed20260932` | 123 | 0.6945 | 0.7323 | 0.3476 | 1.557 | 1.608 |
| `B1-ligand-ecfp4-lgbm-seed20260933` | 123 | 0.6945 | 0.7323 | 0.3476 | 1.557 | 1.608 |
| `B1-ligand-ecfp4-lgbm-seed20260934` | 123 | 0.6945 | 0.7323 | 0.3476 | 1.557 | 1.608 |
| `B2-protein-esm2-lgbm-seed20260930` | 123 (123 all-tied) | 0.5000 | 0.5437 | 0.2490 | 1.000 | 1.000 |
| `B2-protein-esm2-lgbm-seed20260931` | 123 (123 all-tied) | 0.5000 | 0.5437 | 0.2490 | 1.000 | 1.000 |
| `B2-protein-esm2-lgbm-seed20260932` | 123 (123 all-tied) | 0.5000 | 0.5437 | 0.2490 | 1.000 | 1.000 |
| `B2-protein-esm2-lgbm-seed20260933` | 123 (123 all-tied) | 0.5000 | 0.5437 | 0.2490 | 1.000 | 1.000 |
| `B2-protein-esm2-lgbm-seed20260934` | 123 (123 all-tied) | 0.5000 | 0.5437 | 0.2490 | 1.000 | 1.000 |
| `B3L-ligand-1nn` | 123 (13 all-tied) | 0.6980 | 0.7227 | 0.3481 | 1.572 | 1.546 |
| `B4-concat-mlp-seed20260930` | 123 | 0.7815 | 0.8075 | 0.3776 | 1.904 | 1.895 |
| `B4-concat-mlp-seed20260931` | 123 | 0.7931 | 0.8140 | 0.3949 | 1.806 | 1.927 |
| `B4-concat-mlp-seed20260932` | 123 | 0.7892 | 0.8092 | 0.3883 | 1.882 | 1.811 |
| `B4-concat-mlp-seed20260933` | 123 | 0.7890 | 0.8082 | 0.3858 | 2.121 | 1.936 |
| `B4-concat-mlp-seed20260934` | 123 | 0.7934 | 0.8131 | 0.3876 | 1.874 | 1.938 |
| `dual-encoder-seed20260930` | 123 | 0.7840 | 0.8055 | 0.3787 | 1.959 | 1.867 |
| `dual-encoder-seed20260931` | 123 | 0.7703 | 0.7959 | 0.3786 | 1.945 | 1.773 |
| `dual-encoder-seed20260932` | 123 | 0.7891 | 0.8051 | 0.3749 | 1.769 | 1.781 |
| `dual-encoder-seed20260933` | 123 | 0.7768 | 0.7966 | 0.3727 | 1.827 | 1.791 |
| `dual-encoder-seed20260934` | 123 | 0.7870 | 0.7985 | 0.3826 | 1.723 | 1.713 |

**`cross_slot_excluded/screened_primary`** — 20,666 new-to-fitting pairs, 22,834 eligible in the cell

| Model | Scored targets | AUROC | AUPRC | Recall@10 | EF@1% | EF@5% |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `B0-target-mean` | 107 (107 all-tied) | 0.5000 | 0.5783 | 0.2733 | 1.000 | 1.000 |
| `B1-ligand-ecfp4-lgbm-seed20260930` | 107 | 0.6514 | 0.7288 | 0.3402 | 1.418 | 1.421 |
| `B1-ligand-ecfp4-lgbm-seed20260931` | 107 | 0.6514 | 0.7288 | 0.3402 | 1.418 | 1.421 |
| `B1-ligand-ecfp4-lgbm-seed20260932` | 107 | 0.6514 | 0.7288 | 0.3402 | 1.418 | 1.421 |
| `B1-ligand-ecfp4-lgbm-seed20260933` | 107 | 0.6514 | 0.7288 | 0.3402 | 1.418 | 1.421 |
| `B1-ligand-ecfp4-lgbm-seed20260934` | 107 | 0.6514 | 0.7288 | 0.3402 | 1.418 | 1.421 |
| `B2-protein-esm2-lgbm-seed20260930` | 107 (107 all-tied) | 0.5000 | 0.5783 | 0.2733 | 1.000 | 1.000 |
| `B2-protein-esm2-lgbm-seed20260931` | 107 (107 all-tied) | 0.5000 | 0.5783 | 0.2733 | 1.000 | 1.000 |
| `B2-protein-esm2-lgbm-seed20260932` | 107 (107 all-tied) | 0.5000 | 0.5783 | 0.2733 | 1.000 | 1.000 |
| `B2-protein-esm2-lgbm-seed20260933` | 107 (107 all-tied) | 0.5000 | 0.5783 | 0.2733 | 1.000 | 1.000 |
| `B2-protein-esm2-lgbm-seed20260934` | 107 (107 all-tied) | 0.5000 | 0.5783 | 0.2733 | 1.000 | 1.000 |
| `B3L-ligand-1nn` | 107 (13 all-tied) | 0.6698 | 0.7244 | 0.3419 | 1.423 | 1.407 |
| `B4-concat-mlp-seed20260930` | 107 | 0.7650 | 0.8106 | 0.3703 | 1.666 | 1.666 |
| `B4-concat-mlp-seed20260931` | 107 | 0.7663 | 0.8086 | 0.3835 | 1.607 | 1.722 |
| `B4-concat-mlp-seed20260932` | 107 | 0.7635 | 0.8042 | 0.3748 | 1.683 | 1.609 |
| `B4-concat-mlp-seed20260933` | 107 | 0.7668 | 0.8088 | 0.3787 | 1.936 | 1.737 |
| `B4-concat-mlp-seed20260934` | 107 | 0.7703 | 0.8075 | 0.3760 | 1.593 | 1.664 |
| `dual-encoder-seed20260930` | 107 | 0.7599 | 0.8037 | 0.3664 | 1.706 | 1.616 |
| `dual-encoder-seed20260931` | 107 | 0.7369 | 0.7899 | 0.3627 | 1.739 | 1.577 |
| `dual-encoder-seed20260932` | 107 | 0.7595 | 0.8014 | 0.3711 | 1.563 | 1.594 |
| `dual-encoder-seed20260933` | 107 | 0.7519 | 0.7926 | 0.3614 | 1.560 | 1.574 |
| `dual-encoder-seed20260934` | 107 | 0.7603 | 0.7912 | 0.3725 | 1.466 | 1.472 |

**`cross_slot_excluded/unscreened_sensitivity`** — 20,702 new-to-fitting pairs, 22,939 eligible in the cell

| Model | Scored targets | AUROC | AUPRC | Recall@10 | EF@1% | EF@5% |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `B0-target-mean` | 107 (107 all-tied) | 0.5000 | 0.5782 | 0.2704 | 1.000 | 1.000 |
| `B1-ligand-ecfp4-lgbm-seed20260930` | 107 | 0.6518 | 0.7288 | 0.3377 | 1.420 | 1.423 |
| `B1-ligand-ecfp4-lgbm-seed20260931` | 107 | 0.6518 | 0.7288 | 0.3377 | 1.420 | 1.423 |
| `B1-ligand-ecfp4-lgbm-seed20260932` | 107 | 0.6518 | 0.7288 | 0.3377 | 1.420 | 1.423 |
| `B1-ligand-ecfp4-lgbm-seed20260933` | 107 | 0.6518 | 0.7288 | 0.3377 | 1.420 | 1.423 |
| `B1-ligand-ecfp4-lgbm-seed20260934` | 107 | 0.6518 | 0.7288 | 0.3377 | 1.420 | 1.423 |
| `B2-protein-esm2-lgbm-seed20260930` | 107 (107 all-tied) | 0.5000 | 0.5782 | 0.2704 | 1.000 | 1.000 |
| `B2-protein-esm2-lgbm-seed20260931` | 107 (107 all-tied) | 0.5000 | 0.5782 | 0.2704 | 1.000 | 1.000 |
| `B2-protein-esm2-lgbm-seed20260932` | 107 (107 all-tied) | 0.5000 | 0.5782 | 0.2704 | 1.000 | 1.000 |
| `B2-protein-esm2-lgbm-seed20260933` | 107 (107 all-tied) | 0.5000 | 0.5782 | 0.2704 | 1.000 | 1.000 |
| `B2-protein-esm2-lgbm-seed20260934` | 107 (107 all-tied) | 0.5000 | 0.5782 | 0.2704 | 1.000 | 1.000 |
| `B3L-ligand-1nn` | 107 (13 all-tied) | 0.6699 | 0.7239 | 0.3382 | 1.424 | 1.406 |
| `B4-concat-mlp-seed20260930` | 107 | 0.7651 | 0.8105 | 0.3683 | 1.667 | 1.667 |
| `B4-concat-mlp-seed20260931` | 107 | 0.7663 | 0.8085 | 0.3815 | 1.608 | 1.723 |
| `B4-concat-mlp-seed20260932` | 107 | 0.7633 | 0.8039 | 0.3729 | 1.684 | 1.610 |
| `B4-concat-mlp-seed20260933` | 107 | 0.7672 | 0.8090 | 0.3757 | 1.938 | 1.738 |
| `B4-concat-mlp-seed20260934` | 107 | 0.7705 | 0.8074 | 0.3740 | 1.594 | 1.664 |
| `dual-encoder-seed20260930` | 107 | 0.7596 | 0.8034 | 0.3638 | 1.708 | 1.617 |
| `dual-encoder-seed20260931` | 107 | 0.7363 | 0.7891 | 0.3590 | 1.740 | 1.578 |
| `dual-encoder-seed20260932` | 107 | 0.7591 | 0.8009 | 0.3678 | 1.565 | 1.595 |
| `dual-encoder-seed20260933` | 107 | 0.7517 | 0.7923 | 0.3588 | 1.562 | 1.575 |
| `dual-encoder-seed20260934` | 107 | 0.7597 | 0.7905 | 0.3692 | 1.467 | 1.474 |

---

## 8. Verification and reproduction

| Check | Result |
| --- | --- |
| **Published results reproduce from the saved predictions** | **True** — compared against the published bytes, not against a second fresh scoring |
| Scoring is deterministic | True |
| Independent AUROC, written from scratch | agrees for **22** model tags |
| Fit-artifact digests compared | 9 checked, **0** stale, 0 unavailable |
| Evaluation table matches the fit's recorded digest | **True** |
| Predictions match the fit's recorded digest | **True** |
| Restored checkpoints are the best-validation epoch | True |
| Recurrent evaluation pairs that really are train pairs | 4,184 |
| **Non-recurrent evaluation pairs found in train** | **0** |
| Verification verdict | **PASSED** |

The verification record is **bound to the digests it was computed against**, and
publication re-derives them and refuses on any difference. A record that passed
before a file changed does not authorise a report built after it.

### Reproducing this report

Three gates, deliberately separate. They were one call, and that let a
verification record written before a file changed authorise a report built
after it:

```
python -m seq2lead.asof.recompute       data/asof/m11h/run-20261005T134842Z   # results.json only
python -m seq2lead.asof.verify_results  data/asof/m11h/run-20261005T134842Z   # bound to current digests
python -m seq2lead.asof.publish         data/asof/m11h/run-20261005T134842Z   # refuses a stale record
```
Recomputation verifies the saved predictions **and the evaluation table** against
the digests the fit recorded, checks that every prediction row aligns with its
pair, and rescores every cell. It **fits nothing**, loads no checkpoint and
touches no feature cache. It refuses on tampered predictions, a tampered table
— including one whose labels, strata, eligibility or branch flags changed while
every pair identity and its position stayed put — a missing or unexpected model
array, and misaligned or resized pair tables. Publication additionally refuses a
verification record that is not bound to the inputs as they stand, and never
rewrites the fit's recorded digests with freshly computed ones.
`tests/test_m11h_recompute.py` and `tests/test_m11h_publish.py` exercise each
refusal through the real paths.

The scripts that produced the run are preserved verbatim under
`scripts/asof/`, with their provenance and digests in that directory's README.
They were authored and executed from a session scratchpad, which was the wrong
home for code a published result depends on; they are included unmodified so the
record is complete.

---

## 9. What these numbers do and do not support

The highest observed figure is a five-seed mean AUROC of
**0.788873** (`B4-concat-mlp`). Single-seed maxima are not
quoted as the headline: selecting the best seed after seeing results would be
selection on the evaluation cohort. Read it with all of the following in force:

1. **EXPLORATORY.** Snapshot B is our already-inspected 202609, and the M8/M9
   results were produced from it. This is not a prospective result.
2. **PROVISIONAL POOLING.** Whether Ki may be pooled across assay contexts is
   M5's open question. Every number assumes the current pooling rule.
3. **No paired target-level uncertainty analysis was performed.** No difference
   between any two models here is shown to be distinguishable from noise, and no
   two models are claimed to be equivalent.
4. **Seed spread is observed training variation**, not a confidence interval.
5. **The joint-versus-baseline margin does not isolate protein features** (§4).
6. **`B3L` is a target-conditioned lookup, not a ligand-only model**; `B1` is
   the ligand-only one.
7. **Two baselines are 0.5 by construction**, so a margin over them measures
   only that a model breaks ties within a target.
8. **Macro-averages rest on 123 of 992 targets**; the rest fall below the floor.
9. **The LightGBM round cap bound**, so B1 and B2 are not at their own optimum.
10. **Between-stratum differences are contrasts between populations** (§6), not
    one model's performance changing.
11. **Seed-to-seed differences in B1 and B2 are measured but unexplained** (§5).
12. **No tuning was done in response to any of these numbers**, and none may be.
13. **`label_reversal-v3` is unscored**, so the ligand-memorisation question
    that split is designed to answer is not answered here.
14. **The confirmatory §7(A) attestation is NOT signed**, and this stage does
    not sign it.

