# M8 — baseline leaderboard

Generated 2026-09-30 15:55 UTC.

> **Endpoint status: `provisional_pooled_for_exploratory_benchmark`**
>
> Ki is pooled across assays **provisionally, for exploratory benchmarking only**. The pre-declared decision rule returned `pool`, but it compared medians and was blind to a tail in which a quarter of assay-spanning pairs differ by more than tenfold. Pooling is therefore an operating assumption carried forward under protest, not a validated conclusion. Any result computed on pooled Ki inherits this limitation and must state it.
>
> See [`assay_variance.md`](assay_variance.md) for the evidence and its limits.

Result version **`m8/v2`** (metric version `m8/v2`, 72 runs, published 2026-09-30T14:59:50+00:00). Selected explicitly; nothing here is chosen by recency.

Experiment **`baseline-v1`**, contract in [`../docs/EVALUATION.md`](../docs/EVALUATION.md), written before any model was fitted. Every input below is pinned by digest and re-verified at load; nothing resolves by recency.

## Pinned inputs

| Input | Identity |
| --- | --- |
| endpoint | `ki-pki6-v2` (id 96), θ = pKi 6.0 |
| ecfp4 cache | `ecfp4-compound-42351e003acb` · manifest `cba42fd66563…` · bytes `964471e0055f…` |
| esm2 cache | `esm2-target-48cfa09487ce` · manifest `f18f2742dc17…` · bytes `1ecf4ced0d59…` |
| seeds | 20260930, 20260931, 20260932, 20260933, 20260934 |

## What is **not** here

- **`label_reversal-v3` was not scored.** diagnostic_frozen. Evaluated once, after every model choice is frozen; its result may not guide architecture or feature decisions. The M8 leaderboard therefore covers four splits, not five.

So this leaderboard covers **4 splits, not five**. Saying otherwise would misrepresent what was measured.

## Findings

Better scores are not an acceptance criterion. What follows is what the numbers say, including where a baseline wins, and stops where the evidence stops.

**1. A target-specific constant beats the ligand-only regressor.** On `random_pair-v3`, `chemistry_disjoint-v3`, B0 -- the training mean pKi of the target -- has a lower macro RMSE than B1: 1.049 against 1.261 on `random_pair-v3`.

What that establishes is narrow: **this** ligand-only gradient-boosted regressor, on this endpoint, under this bounded budget, does not beat knowing which target you are looking at. It does **not** establish that the benchmark is broken, nor that learned models lose in general -- B4 beats B0 on RMSE on every split scored here. It does mean any future model must clear a constant before its architecture is worth discussing.

**2. A within-target chemical-similarity lookup is strong where the target is seen in training.** On `random_pair-v3`, B3L reaches AUROC 0.8192 against the joint model's 0.8499.

**B3L is target-conditioned and is not a ligand-only baseline.** It searches only the compounds measured against *that same target* in training, so it uses the target identity as a key even though it reads no protein features. Its score therefore measures how far *chemical similarity within an already-measured target* carries a ranking -- it does not isolate ligand memorisation.

**B1 is the genuine ligand-only baseline**: one model over all targets, fingerprint in, pKi out, no target key of any kind. It reaches 0.6889 on `random_pair-v3`. The ligand-bias question is B1's to answer, not B3L's.

**3. Every protein-only predictor scores exactly chance.** B2 and B3P assign one score to all of a target's compounds, so within-target ranking is fully tied. The tie-aware metrics return AUROC 0.5000 and AP equal to prevalence rather than letting row order manufacture a signal. This is the metric implementation working: a model that cannot separate anything should score at chance.

**4. The joint model scores 0.8499 on `random_pair-v3` and 0.6016 on `cold_protein-v3`.** That is a **descriptive difference between two evaluation populations**, not a measured effect of protein holdout on its own. The two splits score different targets, different numbers of them, and different compound pools; the cold split's scored cohort is roughly half the size. Attributing the whole gap to protein novelty would require holding the evaluation population fixed, which no run here does.

B3L drops to exactly chance on `cold_protein-v3`, and that part is mechanical rather than statistical: every test target is unseen in training, so its declared no-neighbour fallback -- the global training mean -- fires for every pair. A within-target lookup has nothing to look up.

**5. Scores are higher on the near-homolog stratum than on the rest of the cold-protein test set**, seed-averaged per model:

| Model | near-homolog AUROC | rest AUROC | difference | targets (near / rest) |
| --- | --- | --- | --- | --- |
| `B0-target-mean` | 0.5000 | 0.5000 | 0.000 | 8 / 324 |
| `B1-ligand-ecfp4-lgbm` | 0.7662 | 0.5848 | 0.181 | 8 / 324 |
| `B2-protein-esm2-lgbm` | 0.5000 | 0.5000 | 0.000 | 8 / 324 |
| `B3L-ligand-1nn` | 0.5000 | 0.5000 | 0.000 | 8 / 324 |
| `B3P-protein-1nn` | 0.5000 | 0.5000 | 0.000 | 8 / 324 |
| `B4-concat-mlp` | 0.7450 | 0.5980 | 0.147 | 8 / 324 |

**This is an association, and a small one to measure.** Only 8 of the 20 near-homolog targets clear the >=5 positives / >=5 negatives floor, against 324 for the rest, so the two estimates are not comparably precise and no significance is claimed -- no test was run, and seed spread would not support one anyway since it measures optimiser variance rather than variation across targets.

**It cannot be read as exploitation of homologous protein representations.** B1 shows the gap and B1 sees no protein features at all. Whatever makes these 8 targets easier is therefore not, by itself, protein-embedding similarity: their compound sets, label balance and measurement coverage may simply differ from the rest of the split. Separating those explanations would need a matched comparison this run does not provide.

**6. Most targets cannot be scored at all.** Across the splits, the majority of held-out targets fail the pre-declared >=5 positives / >=5 negatives floor and are excluded from every ranking metric, with counts given under each table. Reported prevalence is also high -- around 0.62 -- because the eligible pools are majority-active, which is why every AP figure is printed beside its prevalence.

## Models

| Model | Definition | Runs |
| --- | --- | --- |
| `B0-target-mean` | Training mean pKi of the target; global training mean for an unseen target. Ties every compound of a target. | deterministic, 1 run |
| `B1-ligand-ecfp4-lgbm` | Chiral ECFP4 → LightGBM. Sees no protein at all: the **bias gate**. | 5 seeds |
| `B2-protein-esm2-lgbm` | Mean-pooled ESM-2 → LightGBM. Sees no ligand: ties every compound of a target. | 5 seeds |
| `B3L-ligand-1nn` | Nearest training compound *measured against the same target* by ECFP4 Tanimoto; predicts that neighbour's training pKi. | deterministic, 1 run |
| `B3P-protein-1nn` | Nearest training target by ESM-2 cosine; predicts its mean training pKi. Ties every compound of a target. | deterministic, 1 run |
| `B4-concat-mlp` | [ECFP4 ‖ ESM-2] → 2-hidden-layer MLP, protein block standardised on train. | 5 seeds |

## Ranking (primary)

Per target, over that target's eligible held-out **measured** compounds. Unmeasured pairs are never negatives. Macro-averaged; ± is spread across seeds, which is optimiser variance and **not** uncertainty across targets.

**`random_pair-v3`**

| Model | AUROC | AP | prevalence | R@10 | EF@1% | targets scored | all-tied |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `B0-target-mean` | 0.5000 | 0.6200 | 0.620 | 0.251 | 1.00 | 583 | 583 |
| `B1-ligand-ecfp4-lgbm` | 0.6889 ± 0.0001 | 0.7707 | 0.620 | 0.306 | 1.37 | 583 | 0 |
| `B2-protein-esm2-lgbm` | 0.5000 | 0.6200 | 0.620 | 0.251 | 1.00 | 583 | 583 |
| `B3L-ligand-1nn` | 0.8192 | 0.8559 | 0.620 | 0.356 | 1.60 | 583 | 1 |
| `B3P-protein-1nn` | 0.5000 | 0.6200 | 0.620 | 0.251 | 1.00 | 583 | 583 |
| `B4-concat-mlp` | 0.8499 ± 0.0016 | 0.8892 | 0.620 | 0.371 | 1.75 | 583 | 0 |

_2,162 targets (13,279 pairs) in this split were not scored: {'needs >=5 positives and >=5 negatives': 2162}. Scored: 583 targets._

**`cold_protein-v3`**

| Model | AUROC | AP | prevalence | R@10 | EF@1% | targets scored | all-tied |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `B0-target-mean` | 0.5000 | 0.6184 | 0.618 | 0.169 | 1.00 | 332 | 332 |
| `B1-ligand-ecfp4-lgbm` | 0.5892 | 0.6939 | 0.618 | 0.186 | 1.26 | 332 | 0 |
| `B2-protein-esm2-lgbm` | 0.5000 | 0.6184 | 0.618 | 0.169 | 1.00 | 332 | 332 |
| `B3L-ligand-1nn` | 0.5000 | 0.6184 | 0.618 | 0.169 | 1.00 | 332 | 332 |
| `B3P-protein-1nn` | 0.5000 | 0.6184 | 0.618 | 0.169 | 1.00 | 332 | 332 |
| `B4-concat-mlp` | 0.6016 ± 0.0008 | 0.7011 | 0.618 | 0.194 | 1.22 | 332 | 0 |

_792 targets (8,705 pairs) in this split were not scored: {'needs >=5 positives and >=5 negatives': 792}. Scored: 332 targets._

**`chemistry_disjoint-v3`**

| Model | AUROC | AP | prevalence | R@10 | EF@1% | targets scored | all-tied |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `B0-target-mean` | 0.5000 | 0.6360 | 0.636 | 0.232 | 1.00 | 539 | 539 |
| `B1-ligand-ecfp4-lgbm` | 0.6608 ± 0.0015 | 0.7581 | 0.636 | 0.270 | 1.25 | 539 | 0 |
| `B2-protein-esm2-lgbm` | 0.5000 | 0.6360 | 0.636 | 0.232 | 1.00 | 539 | 539 |
| `B3L-ligand-1nn` | 0.7814 | 0.8364 | 0.636 | 0.314 | 1.52 | 539 | 2 |
| `B3P-protein-1nn` | 0.5000 | 0.6360 | 0.636 | 0.232 | 1.00 | 539 | 539 |
| `B4-concat-mlp` | 0.7941 ± 0.0034 | 0.8502 | 0.636 | 0.313 | 1.58 | 539 | 0 |

_1,954 targets (14,765 pairs) in this split were not scored: {'needs >=5 positives and >=5 negatives': 1954}. Scored: 539 targets._

**`temporal_proxy-v4`**

Scored on the **`new` stratum only** — pairs no model had been shown at training time. That is the prospective question this split exists to ask, so the combined figure does not appear here; `recurrent` is reported in its own table below.

| Model | AUROC | AP | prevalence | R@10 | EF@1% | targets scored | all-tied |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `B0-target-mean` | 0.5000 | 0.6205 | 0.620 | 0.187 | 1.00 | 307 | 307 |
| `B1-ligand-ecfp4-lgbm` | 0.6033 ± 0.0000 | 0.7139 | 0.620 | 0.217 | 1.26 | 307 | 0 |
| `B2-protein-esm2-lgbm` | 0.5000 | 0.6205 | 0.620 | 0.187 | 1.00 | 307 | 307 |
| `B3L-ligand-1nn` | 0.5748 | 0.6853 | 0.620 | 0.210 | 1.22 | 307 | 75 |
| `B3P-protein-1nn` | 0.5000 | 0.6205 | 0.620 | 0.187 | 1.00 | 307 | 307 |
| `B4-concat-mlp` | 0.6401 ± 0.0043 | 0.7350 | 0.620 | 0.222 | 1.36 | 307 | 0 |

_959 targets (17,559 pairs) in the `new` stratum were not scored: {'needs >=5 positives and >=5 negatives': 959}. Scored: 307 targets, 90,299 pairs._

## Regression (secondary)

Eligible exact measurements only. Per target, then macro-averaged — never pooled.

| Split | Model | MAE | RMSE | Spearman | CI | targets |
| --- | --- | --- | --- | --- | --- | --- |
| `random_pair-v3` | `B0-target-mean` | 0.928 | 1.049 | — | 0.500 | 2,606 |
| `random_pair-v3` | `B1-ligand-ecfp4-lgbm` | 1.141 | 1.261 | 0.292 | 0.606 | 2,606 |
| `random_pair-v3` | `B2-protein-esm2-lgbm` | 0.940 | 1.059 | — | 0.500 | 2,606 |
| `random_pair-v3` | `B3L-ligand-1nn` | 0.809 | 0.960 | 0.532 | 0.704 | 2,606 |
| `random_pair-v3` | `B3P-protein-1nn` | 0.913 | 1.033 | — | 0.500 | 2,606 |
| `random_pair-v3` | `B4-concat-mlp` | 0.768 | 0.875 | 0.564 | 0.718 | 2,606 |
| `cold_protein-v3` | `B0-target-mean` | 1.573 | 1.704 | — | 0.500 | 1,051 |
| `cold_protein-v3` | `B1-ligand-ecfp4-lgbm` | 1.322 | 1.462 | 0.115 | 0.543 | 1,051 |
| `cold_protein-v3` | `B2-protein-esm2-lgbm` | 1.425 | 1.559 | — | 0.500 | 1,051 |
| `cold_protein-v3` | `B3L-ligand-1nn` | 1.573 | 1.704 | — | 0.500 | 1,051 |
| `cold_protein-v3` | `B3P-protein-1nn` | 1.653 | 1.794 | — | 0.500 | 1,051 |
| `cold_protein-v3` | `B4-concat-mlp` | 1.407 | 1.554 | 0.132 | 0.549 | 1,051 |
| `chemistry_disjoint-v3` | `B0-target-mean` | 0.993 | 1.119 | — | 0.500 | 2,437 |
| `chemistry_disjoint-v3` | `B1-ligand-ecfp4-lgbm` | 1.200 | 1.328 | 0.223 | 0.581 | 2,437 |
| `chemistry_disjoint-v3` | `B2-protein-esm2-lgbm` | 1.026 | 1.150 | — | 0.500 | 2,437 |
| `chemistry_disjoint-v3` | `B3L-ligand-1nn` | 0.901 | 1.055 | 0.450 | 0.661 | 2,437 |
| `chemistry_disjoint-v3` | `B3P-protein-1nn` | 1.003 | 1.129 | — | 0.500 | 2,437 |
| `chemistry_disjoint-v3` | `B4-concat-mlp` | 0.902 | 1.023 | 0.431 | 0.662 | 2,437 |

**`temporal_proxy-v4` regression, by stratum.** New pairs are the headline here too.

| Model | Stratum | MAE | RMSE | Spearman | targets | pairs |
| --- | --- | --- | --- | --- | --- | --- |
| `B0-target-mean` | new | 1.334 | 1.452 | — | 1,237 | 76,954 |
| `B0-target-mean` | recurrent | 1.212 | 1.320 | — | 452 | 5,741 |
| `B1-ligand-ecfp4-lgbm` | new | 1.186 | 1.304 | 0.112 | 1,237 | 76,954 |
| `B1-ligand-ecfp4-lgbm` | recurrent | 1.222 | 1.325 | 0.304 | 452 | 5,741 |
| `B2-protein-esm2-lgbm` | new | 1.257 | 1.371 | — | 1,237 | 76,954 |
| `B2-protein-esm2-lgbm` | recurrent | 1.268 | 1.371 | — | 452 | 5,741 |
| `B3L-ligand-1nn` | new | 1.385 | 1.535 | 0.120 | 1,237 | 76,954 |
| `B3L-ligand-1nn` | recurrent | 0.525 | 0.662 | 0.784 | 452 | 5,741 |
| `B3P-protein-1nn` | new | 1.403 | 1.521 | — | 1,237 | 76,954 |
| `B3P-protein-1nn` | recurrent | 1.213 | 1.321 | — | 452 | 5,741 |
| `B4-concat-mlp` | new | 1.212 | 1.337 | 0.174 | 1,237 | 76,954 |
| `B4-concat-mlp` | recurrent | 0.819 | 0.915 | 0.594 | 452 | 5,741 |

These breakouts are **additional results computed from the same saved predictions**, not changes to any fitted value.

## Required break-outs

Averaged over seeds, like the tables above.

### Temporal: new vs recurrent

`new` is the prospective question -- a pair the model had never been shown.

| Split | Model | Stratum | Pairs | Targets | AUROC | AP | prevalence |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `temporal_proxy-v4` | `B0-target-mean` | temporal:new | 90,299 | 307 | 0.5000 | 0.6205 | 0.620 |
| `temporal_proxy-v4` | `B0-target-mean` | temporal:recurrent | 6,365 | 39 | 0.5000 | 0.7317 | 0.732 |
| `temporal_proxy-v4` | `B1-ligand-ecfp4-lgbm` | temporal:new | 90,299 | 307 | 0.6033 | 0.7139 | 0.620 |
| `temporal_proxy-v4` | `B1-ligand-ecfp4-lgbm` | temporal:recurrent | 6,365 | 39 | 0.7186 | 0.8663 | 0.732 |
| `temporal_proxy-v4` | `B2-protein-esm2-lgbm` | temporal:new | 90,299 | 307 | 0.5000 | 0.6205 | 0.620 |
| `temporal_proxy-v4` | `B2-protein-esm2-lgbm` | temporal:recurrent | 6,365 | 39 | 0.5000 | 0.7317 | 0.732 |
| `temporal_proxy-v4` | `B3L-ligand-1nn` | temporal:new | 90,299 | 307 | 0.5748 | 0.6853 | 0.620 |
| `temporal_proxy-v4` | `B3L-ligand-1nn` | temporal:recurrent | 6,365 | 39 | 0.8881 | 0.9378 | 0.732 |
| `temporal_proxy-v4` | `B3P-protein-1nn` | temporal:new | 90,299 | 307 | 0.5000 | 0.6205 | 0.620 |
| `temporal_proxy-v4` | `B3P-protein-1nn` | temporal:recurrent | 6,365 | 39 | 0.5000 | 0.7317 | 0.732 |
| `temporal_proxy-v4` | `B4-concat-mlp` | temporal:new | 90,299 | 307 | 0.6401 | 0.7350 | 0.620 |
| `temporal_proxy-v4` | `B4-concat-mlp` | temporal:recurrent | 6,365 | 39 | 0.8811 | 0.9380 | 0.732 |

### Cold protein: near-homolog stratum

Held-out targets that satisfy the cluster guarantee and still have a training protein at >=90% identity over at least half of both sequences.

| Split | Model | Stratum | Pairs | Targets | AUROC | AP | prevalence |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `cold_protein-v3` | `B0-target-mean` | near_homolog | 3,838 | 8 | 0.5000 | 0.6393 | 0.639 |
| `cold_protein-v3` | `B0-target-mean` | not_near_homolog | 89,939 | 324 | 0.5000 | 0.6179 | 0.618 |
| `cold_protein-v3` | `B1-ligand-ecfp4-lgbm` | near_homolog | 3,838 | 8 | 0.7662 | 0.7754 | 0.639 |
| `cold_protein-v3` | `B1-ligand-ecfp4-lgbm` | not_near_homolog | 89,939 | 324 | 0.5848 | 0.6919 | 0.618 |
| `cold_protein-v3` | `B2-protein-esm2-lgbm` | near_homolog | 3,838 | 8 | 0.5000 | 0.6393 | 0.639 |
| `cold_protein-v3` | `B2-protein-esm2-lgbm` | not_near_homolog | 89,939 | 324 | 0.5000 | 0.6179 | 0.618 |
| `cold_protein-v3` | `B3L-ligand-1nn` | near_homolog | 3,838 | 8 | 0.5000 | 0.6393 | 0.639 |
| `cold_protein-v3` | `B3L-ligand-1nn` | not_near_homolog | 89,939 | 324 | 0.5000 | 0.6179 | 0.618 |
| `cold_protein-v3` | `B3P-protein-1nn` | near_homolog | 3,838 | 8 | 0.5000 | 0.6393 | 0.639 |
| `cold_protein-v3` | `B3P-protein-1nn` | not_near_homolog | 89,939 | 324 | 0.5000 | 0.6179 | 0.618 |
| `cold_protein-v3` | `B4-concat-mlp` | near_homolog | 3,838 | 8 | 0.7450 | 0.7679 | 0.639 |
| `cold_protein-v3` | `B4-concat-mlp` | not_near_homolog | 89,939 | 324 | 0.5980 | 0.6995 | 0.618 |

### Long sequences

Targets embedded past ESM-2's 1,022-residue pre-training window, against the rest.

| Split | Model | Stratum | Pairs | Targets | AUROC | AP | prevalence |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `chemistry_disjoint-v3` | `B0-target-mean` | over_training_window | 7,744 | 52 | 0.5000 | 0.6652 | 0.665 |
| `chemistry_disjoint-v3` | `B0-target-mean` | within_training_window | 86,042 | 487 | 0.5000 | 0.6329 | 0.633 |
| `chemistry_disjoint-v3` | `B1-ligand-ecfp4-lgbm` | over_training_window | 7,744 | 52 | 0.6764 | 0.7644 | 0.665 |
| `chemistry_disjoint-v3` | `B1-ligand-ecfp4-lgbm` | within_training_window | 86,042 | 487 | 0.6591 | 0.7574 | 0.633 |
| `chemistry_disjoint-v3` | `B2-protein-esm2-lgbm` | over_training_window | 7,744 | 52 | 0.5000 | 0.6652 | 0.665 |
| `chemistry_disjoint-v3` | `B2-protein-esm2-lgbm` | within_training_window | 86,042 | 487 | 0.5000 | 0.6329 | 0.633 |
| `chemistry_disjoint-v3` | `B3L-ligand-1nn` | over_training_window | 7,744 | 52 | 0.7520 | 0.8173 | 0.665 |
| `chemistry_disjoint-v3` | `B3L-ligand-1nn` | within_training_window | 86,042 | 487 | 0.7845 | 0.8385 | 0.633 |
| `chemistry_disjoint-v3` | `B3P-protein-1nn` | over_training_window | 7,744 | 52 | 0.5000 | 0.6652 | 0.665 |
| `chemistry_disjoint-v3` | `B3P-protein-1nn` | within_training_window | 86,042 | 487 | 0.5000 | 0.6329 | 0.633 |
| `chemistry_disjoint-v3` | `B4-concat-mlp` | over_training_window | 7,744 | 52 | 0.7464 | 0.8167 | 0.665 |
| `chemistry_disjoint-v3` | `B4-concat-mlp` | within_training_window | 86,042 | 487 | 0.7992 | 0.8538 | 0.633 |
| `cold_protein-v3` | `B0-target-mean` | over_training_window | 11,799 | 26 | 0.5000 | 0.5996 | 0.600 |
| `cold_protein-v3` | `B0-target-mean` | within_training_window | 81,978 | 306 | 0.5000 | 0.6200 | 0.620 |
| `cold_protein-v3` | `B1-ligand-ecfp4-lgbm` | over_training_window | 11,799 | 26 | 0.5981 | 0.6695 | 0.600 |
| `cold_protein-v3` | `B1-ligand-ecfp4-lgbm` | within_training_window | 81,978 | 306 | 0.5884 | 0.6959 | 0.620 |
| `cold_protein-v3` | `B2-protein-esm2-lgbm` | over_training_window | 11,799 | 26 | 0.5000 | 0.5996 | 0.600 |
| `cold_protein-v3` | `B2-protein-esm2-lgbm` | within_training_window | 81,978 | 306 | 0.5000 | 0.6200 | 0.620 |
| `cold_protein-v3` | `B3L-ligand-1nn` | over_training_window | 11,799 | 26 | 0.5000 | 0.5996 | 0.600 |
| `cold_protein-v3` | `B3L-ligand-1nn` | within_training_window | 81,978 | 306 | 0.5000 | 0.6200 | 0.620 |
| `cold_protein-v3` | `B3P-protein-1nn` | over_training_window | 11,799 | 26 | 0.5000 | 0.5996 | 0.600 |
| `cold_protein-v3` | `B3P-protein-1nn` | within_training_window | 81,978 | 306 | 0.5000 | 0.6200 | 0.620 |
| `cold_protein-v3` | `B4-concat-mlp` | over_training_window | 11,799 | 26 | 0.6431 | 0.6897 | 0.600 |
| `cold_protein-v3` | `B4-concat-mlp` | within_training_window | 81,978 | 306 | 0.5980 | 0.7021 | 0.620 |
| `random_pair-v3` | `B0-target-mean` | over_training_window | 6,720 | 55 | 0.5000 | 0.6338 | 0.634 |
| `random_pair-v3` | `B0-target-mean` | within_training_window | 87,056 | 528 | 0.5000 | 0.6185 | 0.619 |
| `random_pair-v3` | `B1-ligand-ecfp4-lgbm` | over_training_window | 6,720 | 55 | 0.6978 | 0.7778 | 0.634 |
| `random_pair-v3` | `B1-ligand-ecfp4-lgbm` | within_training_window | 87,056 | 528 | 0.6880 | 0.7700 | 0.619 |
| `random_pair-v3` | `B2-protein-esm2-lgbm` | over_training_window | 6,720 | 55 | 0.5000 | 0.6338 | 0.634 |
| `random_pair-v3` | `B2-protein-esm2-lgbm` | within_training_window | 87,056 | 528 | 0.5000 | 0.6185 | 0.619 |
| `random_pair-v3` | `B3L-ligand-1nn` | over_training_window | 6,720 | 55 | 0.8166 | 0.8367 | 0.634 |
| `random_pair-v3` | `B3L-ligand-1nn` | within_training_window | 87,056 | 528 | 0.8195 | 0.8579 | 0.619 |
| `random_pair-v3` | `B3P-protein-1nn` | over_training_window | 6,720 | 55 | 0.5000 | 0.6338 | 0.634 |
| `random_pair-v3` | `B3P-protein-1nn` | within_training_window | 87,056 | 528 | 0.5000 | 0.6185 | 0.619 |
| `random_pair-v3` | `B4-concat-mlp` | over_training_window | 6,720 | 55 | 0.8414 | 0.8765 | 0.634 |
| `random_pair-v3` | `B4-concat-mlp` | within_training_window | 87,056 | 528 | 0.8508 | 0.8905 | 0.619 |
| `temporal_proxy-v4` | `B0-target-mean` | over_training_window | 11,877 | 38 | 0.5000 | 0.6209 | 0.621 |
| `temporal_proxy-v4` | `B0-target-mean` | within_training_window | 84,787 | 290 | 0.5000 | 0.6193 | 0.619 |
| `temporal_proxy-v4` | `B1-ligand-ecfp4-lgbm` | over_training_window | 11,877 | 38 | 0.5360 | 0.6833 | 0.621 |
| `temporal_proxy-v4` | `B1-ligand-ecfp4-lgbm` | within_training_window | 84,787 | 290 | 0.6135 | 0.7171 | 0.619 |
| `temporal_proxy-v4` | `B2-protein-esm2-lgbm` | over_training_window | 11,877 | 38 | 0.5000 | 0.6209 | 0.621 |
| `temporal_proxy-v4` | `B2-protein-esm2-lgbm` | within_training_window | 84,787 | 290 | 0.5000 | 0.6193 | 0.619 |
| `temporal_proxy-v4` | `B3L-ligand-1nn` | over_training_window | 11,877 | 38 | 0.5513 | 0.6759 | 0.621 |
| `temporal_proxy-v4` | `B3L-ligand-1nn` | within_training_window | 84,787 | 290 | 0.6069 | 0.7037 | 0.619 |
| `temporal_proxy-v4` | `B3P-protein-1nn` | over_training_window | 11,877 | 38 | 0.5000 | 0.6209 | 0.621 |
| `temporal_proxy-v4` | `B3P-protein-1nn` | within_training_window | 84,787 | 290 | 0.5000 | 0.6193 | 0.619 |
| `temporal_proxy-v4` | `B4-concat-mlp` | over_training_window | 11,877 | 38 | 0.6276 | 0.7321 | 0.621 |
| `temporal_proxy-v4` | `B4-concat-mlp` | within_training_window | 84,787 | 290 | 0.6631 | 0.7497 | 0.619 |

## Runtime

| Model | Median fit (s) | Median predict (s) |
| --- | --- | --- |
| `B0-target-mean` | 0.0 | 0.0 |
| `B1-ligand-ecfp4-lgbm` | 6.6 | 0.8 |
| `B2-protein-esm2-lgbm` | 39.4 | 0.4 |
| `B3L-ligand-1nn` | 0.0 | 2.9 |
| `B3P-protein-1nn` | 0.2 | 0.0 |
| `B4-concat-mlp` | 41.4 | 1.4 |

## Reproducibility

Per-pair predictions are written to `data/predictions/` as `.npz` (compound id, target id, label, prediction, and the regression arrays), and per-target metrics to `reports/results/baseline_runs.json`. Every number above can be recomputed from those without refitting.

Large artifacts are referenced by path and checksum rather than bundled; see `reports/results/artifacts.md`.

## Corrections applied to these numbers

Metric version **`m8/v2`**, recomputed from the saved predictions of 72 runs with `uv run seq2lead eval recompute`. **No model was refitted**: the predictions are the originals, verified by digest.

- average_precision: threshold-block AP. Equal scores are admitted as one block before precision is read, matching sklearn.metrics.average_precision_score. The superseded version averaged precision over orderings inside a tied block, which is a different quantity and errs in BOTH directions -- measured on one real split it disagreed with sklearn on 245 of 328 scored targets, by up to 0.057 in either direction.
- temporal reporting: `new` is the headline stratum for temporal_proxy-v4; `recurrent` is reported separately; the combined figure is removed from headline comparisons.
- temporal regression: `new` and `recurrent` breakouts added. These are additional results computed from the same saved predictions, not changes to any fitted value.
- near-homolog reporting: seed-averaged per model with scored-target counts, replacing a selection of the single largest gap across runs.

| Metric | Values compared | Changed | Max \|delta\| |
| --- | --- | --- | --- |
| `ranking.auroc` | 72 | 0 | — |
| `ranking.average_precision` | 72 | 43 | 0.001891 |
| `regression.mae` | 72 | 0 | — |
| `regression.rmse` | 72 | 0 | — |
| `regression.spearman` | 72 | 0 | — |

**Unchanged, as they should be:** `ranking.auroc`, `regression.mae`, `regression.rmse`, `regression.spearman`. The average-precision definition does not enter them, and the predictions behind them are byte-identical to the originals.

The superseded v1 figures are retained in `reports/results/baseline_summary.json` and the per-value comparison in `reports/results/correction_v2.json`. A corrected number is only checkable against the one it replaced.

## Limitations

| # | Limitation |
| --- | --- |
| 1 | **The endpoint is provisional.** Ki is pooled across assays as an operating assumption M5 declined to validate. Every number here inherits that. |
| 2 | **Seed spread is not uncertainty.** The ± figures are optimiser variance across training seeds. They say how stable a fit is, not how confident anyone should be about a new target. No target-level bootstrap was run, so **no confidence intervals across targets are reported** -- an interval computed from seeds would be a category error. |
| 3 | **Most held-out targets are unscoreable** at the pre-declared floor, so each macro-average covers a minority of the split and is weighted toward densely measured targets. |
| 4 | **Prevalence is high (~0.62)** because eligible pools are majority-active. AP and EF should be read against that, not against an implicit 50% or a screening-library prior. |
| 5 | **B3L uses a declared, versioned bound.** Where a target has more than 4,096 training compounds the reference block is a seeded sample of that size; the exhaustive alternative is a quarter-million-square Tanimoto matrix. The cap is recorded in the fit notes along with how many targets hit it. |
| 6 | **Concordance index is capped at 2,000 pairs per target**; targets above it report CI as undefined with the reason, rather than an approximation. |
| 7 | **BEDROC is not reported.** It was not implemented or verified against a reference, and an unverified implementation of a weighted metric is worse than its absence. |
| 8 | **One diagnostic split is deliberately pending**, so this is not a complete picture of the five splits M6 built. |
| 9 | **No model here is tuned.** The budget was bounded and small by design; these are baselines, and a stronger joint model may well beat them. What the table establishes is the floor any such model has to clear. |

