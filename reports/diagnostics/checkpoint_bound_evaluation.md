# Checkpoint-bound evaluation — results

Executed 2026-10-08 against the specification in
[`checkpoint_bound_evaluation_spec.md`](checkpoint_bound_evaluation_spec.md),
which was frozen before any score was computed. Read-only: no score, bundle,
checkpoint or published artifact was modified, and nothing was tuned or
reselected.

Raw numbers: [`checkpoint_bound_evaluation.json`](checkpoint_bound_evaluation.json).

## Headline

On the deployed checkpoint's **own** held-out protein partition, across a
**census of 244 qualifying targets and 79,493 labelled pair observations**, there
is **no demonstrated average improvement** from the target-specific ranking over
a compound ordering that never reads the query.

| | Macro AUROC |
| --- | --- |
| Target-specific | **0.6164** |
| Query-independent (B-ref40) | **0.6224** |
| Query-independent, un-normalised (B-prior) | 0.6219 |
| Random floor (B-random) | 0.5032 |

Mean Δ (specific − query-independent) = **−0.0060**, 95% hierarchical bootstrap
interval **[−0.0192, +0.0073]**, improving on **112 of 244** targets (46%),
Wilcoxon signed-rank **p = 0.351**. The interval contains zero and the point
estimate is slightly negative. **No average improvement is demonstrated on this
population.**

That is a statement about what the evidence shows, not a claim that the two
rankings are equivalent. No equivalence test was performed and no equivalence
margin was declared, so this result does not establish that the two are the same
— only that an average advantage for the target-specific ranking was not
demonstrated here.

This does not reproduce the twelve-target panel's pooled +0.1116. That panel was
8/12 train-exposed and, as reported there, its advantage sat entirely with the
exposed targets. On the checkpoint's own held-out partition the advantage is
absent.

## This is not the model the headline benchmark evaluated

The numbers here and the numbers in [Key results](../../README.md#key-results)
describe **different models on different evaluations**, and must not be compared.

| | Deployed checkpoint (this report) | Historical M11h comparison (Key results) |
| --- | --- | --- |
| Checkpoint | `M9-dual-encoder__cold_protein-v3__seed20260930.pt`, sha256 `934adcdb…` | `m11h/.../checkpoints/dual-encoder-seed20260930.pt`, sha256 `0b4e22fa…`, and four further seeds |
| Experiment | `m9-dual-encoder-v1` | `m11h-asof-fit-v1` |
| Split | `cold_protein-v3` — protein clusters held out | as-of temporal, January → September 2026 |
| Evaluation population | 244 qualifying targets, cold-protein test partition | 123 targets clearing ≥5 actives and ≥5 inactives, from 22,221 pairs over 992 targets |
| Comparator | a query-independent compound ordering | other models (concat-MLP, ligand-only, target-mean, 1-NN) |
| Seeds | 1 | 5 |
| Headline | macro AUROC 0.6164 vs 0.6224 query-independent | dual encoder 0.781229, concat-MLP 0.788873 |

**The shipped bundle does not contain an M11h model.** Its checkpoint digest
`934adcdb…` is not among the five M11h checkpoints. The benchmark's 0.781229
therefore describes a model that is *not* what `uv run seq2lead prioritise` or
the browser interface runs.

The two numbers are also not on a comparable scale: different splits, different
target populations, different label cohorts and different comparators. **0.6164
is not a regression from 0.781229**, and neither figure can be used to check the
other. Each is only interpretable against the comparator inside its own
evaluation.

What the two do share: neither demonstrates an improvement for the dual encoder
over its comparator. The M11h row reports "No demonstrated dual-encoder
improvement" against concat-MLP; this report finds no demonstrated average
improvement over a query-independent ordering. Those are separate findings about
separate models that happen to point the same way.

## Provenance

| | |
| --- | --- |
| Checkpoint | `M9-dual-encoder__cold_protein-v3__seed20260930.pt`, sha256 `934adcdb…` |
| Config | `m9-dual-encoder-v1`, sha256 `58662cd2…` — **matches the digest the bundle records** |
| Split | `cold_protein-v3`, id 10 |
| Endpoint | `ki-pki6-v2`, id 96, KI, pKi ≥ 6.0 |
| Scoring | as shipped, `scale · cosine + offset` |

## Cohorts and denominators

**Two different units are counted here and an earlier draft confused them.** A
*pair observation* is one (target, compound) row with a label; a *compound* is a
distinct molecule. A compound measured against six targets contributes six pair
observations. The figure 79,493 is **pair observations**, not compounds; an
earlier version of this report called it "labelled compounds", which was wrong.

Cohort before any floor is applied:

| | Pair observations | Targets | Distinct compounds |
| --- | --- | --- | --- |
| Eligible test cohort, before exclusions | 91,639 | 1,106 | 67,746 |
| **After exclusions** | **91,630** | **1,106** | **67,737** |
| …involving a bundled compound | 12,688 | — | 7,107 |
| …outside the bundled library | 78,942 | — | 60,630 |

Every evaluated cell, with each denominator named:

| Cohort | Floor | Targets | Pair observations | Distinct compounds | …of which bundled |
| --- | --- | --- | --- | --- | --- |
| Complete | ≥5 | 329 | 83,043 | 62,443 | 6,453 |
| **Complete** | **≥10** | **244** | **79,493** | **59,912** | **5,749** |
| Complete | ≥25 | 146 | 70,870 | 54,557 | 5,362 |
| Bundle | ≥5 | 107 | 8,830 | 5,115 | 5,115 |
| Bundle | ≥10 | 62 | 6,596 | 3,657 | 3,657 |
| Bundle | ≥25 | 26 | 4,244 | 2,231 | 2,231 |

Which denominator each metric uses:

- **AUROC, average precision, EF** — computed within one target's ranking set;
  the denominator is that target's pair observations (median 159 at the primary
  cell, range 23–2,796), and AUROC's pair denominator is its actives × inactives.
- **Macro AUROC** — unweighted mean over the cell's **targets** (244 at the
  primary cell), not over pair observations.
- **Mean Δ, Wilcoxon, sign test** — one value per **target**; n = 244.
- **Compound-level bootstrap** — resamples a target's **pair observations**.
- **Hierarchical bootstrap** — resamples **targets**, then pair observations
  within each.
- **Analogue dependence** — denominators are **distinct compounds**, sampled 1,500
  from the 67,737 and compared against 196,365 train/validation compounds.

**Exclusions**: 9 compounds carried the `unusable_fingerprint` flag for feature
version `ecfp4-compound-42351e003acb` and were dropped under the m9 cohort rule
`exclude_unusable_fingerprints`, removing 9 pairs. No other compound or pair was
excluded; 67,737 of 67,746 compounds were fingerprinted.

### Scoring compounds outside the bundle

Only 7,107 of 67,746 cohort compounds are in the bundled library, so most of the
cohort cannot be scored by reading `library/projections.npy`. The compound side
was recomputed instead, and the path was **verified before use**: for 400 bundled
compounds drawn at random, recomputing ECFP4 from the stored SMILES (radius 2,
2048 bits, chirality on — feature version `ecfp4-compound-42351e003acb`, manifest
`cba42fd6…`, storage `964471e0…`, the digests the bundle and the m9 config both
record) and applying the shipped compound tower reproduced the stored projection
in **400 of 400** cases to `atol=1e-4`, cosine 1.000000.

## Results at every declared floor

≥ 10 / ≥ 10 is primary; ≥ 5 / ≥ 5 and ≥ 25 / ≥ 25 are the declared sensitivity
analyses and do not replace it.

| Cohort | Floor | Targets | Compounds | Specific | Query-indep. | Mean Δ | 95% hierarchical CI | Improved | Wilcoxon p |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Complete | **≥10** | **244** | **79,493** | **0.6164** | **0.6224** | **−0.0060** | **[−0.0192, +0.0073]** | **112/244** | **0.351** |
| Complete | ≥5 | 329 | — | 0.5991 | 0.6044 | −0.0053 | [−0.0193, +0.0084] | 156/329 | 0.500 |
| Complete | ≥25 | 146 | — | 0.6033 | 0.6170 | −0.0138 | [−0.0294, +0.0025] | 61/146 | **0.024** |
| Bundle | ≥10 | 62 | 6,596 | 0.5910 | 0.6099 | −0.0189 | [−0.0505, +0.0131] | 27/62 | 0.207 |
| Bundle | ≥5 | 107 | 8,830 | 0.6162 | 0.6142 | +0.0020 | [−0.0282, +0.0330] | 52/107 | 0.902 |
| Bundle | ≥25 | 26 | 4,244 | 0.6349 | 0.6565 | −0.0217 | [−0.0637, +0.0193] | 10/26 | 0.258 |

Every cell is negative or indistinguishable from zero except bundle/≥5 at
+0.0020, whose interval spans zero and whose Wilcoxon p is 0.902. At the ≥25/≥25
floor on the complete cohort the Wilcoxon test reaches p = 0.024 — **in favour of
the query-independent baseline**, not the model. With six cells reported and no
multiplicity correction, one p-value near 0.02 is not treated as a finding; it is
reported because suppressing it would be selective.

## Dispersion

Per-target Δ on the primary cell: median −0.0041, IQR [−0.041, +0.038], range
−0.3185 to +0.3311. Per-target intervals (paired compound bootstrap):

- **37 of 244** exclude zero in favour of the target-specific ranking,
- **50 of 244** exclude zero in favour of the query-independent baseline,
- **157 of 244** are inconclusive.

**These per-target intervals are exploratory and unadjusted for multiple
comparisons.** 244 intervals were computed at nominal 95%, so under a null of no
per-target difference roughly 6 would exclude zero in each direction by chance
alone. The observed 37 and 50 exceed that, which is consistent with genuine
per-target heterogeneity — but **no individual target should be singled out as
significantly better or worse on this basis**. Doing so would require a
multiplicity adjustment that was not applied and was not pre-declared. The counts
are reported as a description of spread, not as 87 discoveries.

So the sequence does change the ranking for individual targets, sometimes
substantially and in both directions — but across the population those movements
do not add up to a demonstrated advantage.

## Declared checks

**Random-baseline sanity check.** Declared as a statistical test, not an exact
value: the check passes if the 99% percentile-bootstrap interval over targets
contains 0.5. **All six cells pass.**

| Cell | Macro AUROC | 99% interval | Contains 0.5 |
| --- | --- | --- | --- |
| Complete ≥10 | 0.5032 | [0.4930, 0.5138] | yes |
| Complete ≥5 | 0.5046 | [0.4920, 0.5169] | yes |
| Complete ≥25 | 0.4980 | [0.4879, 0.5079] | yes |
| Bundle ≥10 | 0.5065 | [0.4809, 0.5311] | yes |
| Bundle ≥5 | 0.4878 | [0.4650, 0.5112] | yes |
| Bundle ≥25 | 0.5033 | [0.4726, 0.5309] | yes |

**Exposure assertions.** No evaluation target appears in the `train` or
`validation` partition of split 10 (the cohort is drawn from `test` only); the 40
reference targets were drawn from 7,243 targets remaining after removing the
entire evaluation population **and** every train/validation target; all labels
come from endpoint 96 alone; the checkpoint and library digests match §1 of the
specification.

**Single-class bootstrap resamples** are discarded, never imputed and never
replaced by 0.5. Counts: 1 discard across the primary cell (244 targets ×
2,000 resamples), 291 at ≥5/≥5, 0 elsewhere. **No target fell below the 1,000
usable-resample threshold**, so no interval is marked unreliable.

## Deviation from the specification

The spec asked for 10,000 compound-level resamples per target with a fully nested
hierarchical bootstrap. As implemented: **2,000** compound-level resamples per
target, and the hierarchical interval draws 10,000 times, each draw resampling
targets with replacement and then taking one precomputed realisation from each
drawn target's compound-level distribution. This is a two-stage approximation to
the nested scheme, adopted because the fully nested version is ~2.4 million AUROC
computations per cell. The target-only interval is reported beside the
hierarchical one in the JSON and the two agree closely on the primary cell
([−0.0183, +0.0058] against [−0.0192, +0.0073]), which is some evidence the
approximation is not driving the conclusion.

## Analogue dependence — the limitation that bounds all of this

`cold_protein-v3` holds out **proteins, not compounds**. The compound side is
therefore exposed by design, and the measurement says how much. 1,500 test
compounds sampled with seed 7, compared against all 196,365 train and validation
compounds:

| | |
| --- | --- |
| Test compounds that are **literally the same molecule** as a train/validation compound | **455 / 1,500 (30.3%)** |
| Max Tanimoto to nearest train compound ≥ 0.9 | 30.7% |
| ≥ 0.7 | 36.8% |
| Mean / median max Tanimoto | 0.6318 / 0.5419 |
| 10th / 90th percentile | 0.3261 / 1.0 |

Nearly a third of the evaluated compounds were seen during fitting, against other
proteins. This is a property of a cold-**protein** split and not a defect, but it
bounds what the result can mean: this is a held-out-protein evaluation on
substantially familiar chemistry. It is not evidence about new chemistry, and a
cold-compound or cold-both evaluation would be a different experiment.

## What this establishes, and what it does not

**Established, by measurement on this population:**

- across a census of 244 qualifying targets and 79,493 labelled pair
  observations in the deployed checkpoint's own cold-protein test partition, the
  target-specific ranking's macro AUROC (0.6164) is not higher than a
  query-independent ordering's (0.6224);
- the mean difference is −0.0060 with a 95% hierarchical interval of
  [−0.0192, +0.0073], and the Wilcoxon signed-rank test does not reject equality
  (p = 0.351);
- the direction and magnitude are stable across both cohorts and all three
  declared floors;
- the random floor behaves as a random floor in all six cells;
- individual targets do move substantially in both directions, with more
  per-target intervals excluding zero (37 and 50) than the ~6 each way expected
  by chance across 244 unadjusted comparisons.

**Not established, and not claimed:**

- anything about other splits, seeds, checkpoints, endpoints or libraries. This
  is one checkpoint (seed 20260930) on one split;
- anything about new chemistry — 30.3% of evaluated compounds are molecules the
  fit already saw;
- anything about calibration, which was not computed;
- that the two rankings are equivalent. No equivalence test was run and no margin
  was declared; "no demonstrated average improvement" is not "no difference";
- that any named target is significantly better or worse. The per-target
  intervals are exploratory and unadjusted for 244 comparisons;
- that the model is useless. An average advantage was not demonstrated *here*,
  which is a statement about this population and this comparator;
- anything about the published benchmark numbers. Those evaluate **different
  checkpoints** on a different split and population, are not recomputed or
  contradicted here, and remain unchanged. See the separation table above before
  placing the two side by side.

## Lineage, now reconstructed rather than assumed

An earlier report marked evidence lineage unresolved because
`split_activity_assignment` is empty for `cold_protein-v3`. That absence was not
treated as evidence of non-use. The lineage was instead reconstructed positively
from the support tables the loader actually consumes:

- the m9 config pins endpoint 96 and the objective target
  `pair_regression.p_median`, with `require_exact_regression_label: true`;
- `pair_regression_support` records which activity rows produced each pair's
  median, and `pair_label_support` does the same for labels;
- joining those supports to the split's pair partitions: **0 activity rows
  support pairs in more than one partition**. Distinct supporting rows are 342,658
  for train, 48,833 for validation, 99,198 for test and 48,482 excluded.

So no measurement row underlying a test-partition label was consumed as a
training target. The partition is clean at the evidence level, not only at the
pair level — a positive reconstruction, not an inference from a missing table.
