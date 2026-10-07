# Does the model rank differently per target, and does that help?

Measured 2026-10-07 against the shipped v0.2.0 bundle, on a bounded panel of
targets with measured labels. Read-only: no score, rank, bundle, checkpoint or
published result was modified, and nothing here was tuned.

Raw numbers: [`labelled_panel.json`](labelled_panel.json) ·
exposure: [`panel_exposure.json`](panel_exposure.json) ·
selection rule: [`labelled_panel_selection_rule.md`](labelled_panel_selection_rule.md).

> **Read the exposure section before the results.** Eight of the twelve panel
> targets turned out to be in the training partition of the split the shipped
> checkpoint was fitted on. Pooled across all twelve, the numbers overstate what
> this model does on a target it has not seen.

## Exposure to the exact shipped checkpoint

The labels come from the **m11h as-of** evaluation. The bundle does **not** ship
an m11h model. It ships:

```
data/m9/final/M9-dual-encoder__cold_protein-v3__seed20260930.pt
sha256 934adcdb1a71b71c3147a00b9bd74a953394642522abeb4a038e99d7f6c86c6e
experiment m9-dual-encoder-v1, split cold_protein-v3
```

Being held out of the m11h *as-of* partition says nothing about whether this
checkpoint saw a pair, because the two experiments use different split designs.
The question that matters is what partition each panel pair occupies under
**`cold_protein-v3`**, the split this checkpoint was actually fitted on. Three
kinds of exposure, reported separately:

| Exposure | Definition | Panel total |
| --- | --- | --- |
| **Training-pair** | the (compound, target) pair is in the `train` partition of `cold_protein-v3`, so it was available to fit the weights | **804 of 1,134 pairs (71%)** |
| **Validation-pair** | the pair is in the `validation` partition, so it was available for model selection and early stopping | **0** |
| **Measurement-evidence** | a curated measurement for the pair exists in the snapshot at all, in any partition | 1,134 of 1,134 |

Measurement evidence exists for every panel pair by construction — they are
labelled pairs. What distinguishes them is whether that evidence reached the
fitting process, and the first two rows answer that. No panel pair fell in the
validation partition, so validation-pair exposure is nil across the panel.

`cold_protein-v3` holds out whole protein clusters and is clean at target level:
of 1,610 train targets and 1,124 test targets, **0 appear in both**. So each
panel target is wholly exposed or wholly held out.

| Target | partition | training pairs | validation pairs | test pairs | Δ AUROC |
| --- | --- | --- | --- | --- | --- |
| `B75060695513` | train | 68 | 0 | 0 | +0.4740 |
| `4D0C1AE8D9C6` | train | 78 | 0 | 0 | +0.4031 |
| `945340616BAF` | train | 77 | 0 | 0 | +0.2222 |
| `075CD160A22E` | train | 154 | 0 | 0 | +0.2151 |
| `8BB8E760C142` | train | 148 | 0 | 0 | +0.1518 |
| `351664067C22` | train | 61 | 0 | 0 | +0.0859 |
| `A9754AA0EED8` | train | 166 | 0 | 0 | +0.0720 |
| `8C2B6E1477FF` | train | 52 | 0 | 0 | −0.0195 |
| `0F79DD8C1EA2` | **test** | 0 | 0 | 69 | +0.0729 |
| `0CF62E1CCB94` | **test** | 0 | 0 | 74 | −0.0538 |
| `051FC4732E49` | **test** | 0 | 0 | 61 | −0.0646 |
| `0153DBEBB226` | **test** | 0 | 0 | 59 | −0.2202 |

`8C2B6E1477FF` is the example sequence shipped with the repository. It is
train-exposed for this checkpoint.

## Reclassified results

| | Held out from this checkpoint | Train-exposed | Pooled (as first reported) |
| --- | --- | --- | --- |
| Targets | **4** | 8 | 12 |
| Macro AUROC, target-specific | **0.6493** | 0.8947 | 0.8129 |
| Macro AUROC, query-independent | **0.7157** | 0.6942 | 0.7014 |
| Mean Δ | **−0.0664** | +0.2006 | +0.1116 |
| 95% interval over targets | **[−0.1786, +0.0385]** | [+0.0966, +0.3134] | [+0.0063, +0.2203] |
| Targets improved | **1 / 4** | 7 / 8 | 8 / 12 |
| Sign test, two-sided | 1.0 | 0.0703 | 0.3877 |

**The separation is the finding.** The target-specific advantage visible in the
pooled number is confined to targets whose pairs were available to fit this
checkpoint. On the four targets genuinely held out from it, the target-specific
ranking scored **below** the query-independent baseline on average, and improved
on only one of four. With four targets the interval spans zero and no directional
claim is warranted — but the pooled +0.1116 should not be read as this model's
behaviour on an unseen target, because 8 of the 12 targets behind it were seen.

## The two questions

### 1. Does the model change rankings between targets?

Yes. Mean pairwise Spearman between the twelve panel targets' rankings of the
full 25,000-compound library is **0.42**, range **−0.02 to 0.88**. Rank agreement
between two queries is not a fixed property of the model; the unlabelled panel's
0.88–0.94 reflected four proteins far outside the training distribution and was
not representative.

### 2. Do those changes improve discrimination of measured actives?

On train-exposed targets, yes and substantially. On the four targets held out
from this checkpoint, **not demonstrably** — the point estimate is negative, the
interval spans zero, and one of four improved.

Across the whole panel the **query-independent baseline is competitive**: macro
AUROC 0.7014 pooled, 0.7157 on the held-out four, where it exceeded the
target-specific ranking. A ranking that never reads the query is a serious
contender on this panel.

That statement is deliberately comparative. AUROC is not additive and does not
decompose into a query-independent part plus a target-specific part, so no share
of performance is attributed to either. An earlier draft said "most of the
discrimination is a compound-side prior"; that phrased a comparison as an
attribution and is withdrawn.

## Method

**Panel selection.** By **label availability and class counts only** — held-out
m11h pairs, compound present in the bundled library, ≥ 10 actives and ≥ 10
inactives, ordered by labelled-compound count with a digest tie-break, first 12.
The rule was written and committed before any score was computed. **Exposure to
the shipped checkpoint was not part of the rule**, which is how a
predominantly train-exposed panel arose; it was measured afterwards and is
reported above rather than used to reselect. No target was added, dropped or
reordered after metrics were seen.

**Query-independent baseline.** For each compound, the mean of its per-target
z-scored library score across a reference set of targets:

1. score the full 25,000-compound library for reference target *t*;
2. z-score those 25,000 values within *t*, so targets with different score ranges
   contribute comparably;
3. average across reference targets, giving one value per compound.

The result is a single compound ordering used for every target. It consults no
label and nothing from the target it is scored against.

**Reference-target selection.** 40 targets drawn **without replacement, uniformly
at random, seed 20261007**, from the 10,698 database targets with sequence length
20–2,000, **after removing all 12 panel targets**. Uniform over eligible targets,
not weighted by data volume. A leave-one-out variant over the panel gives macro
AUROC 0.7430, within 0.042 of the reference-set baseline, which is some evidence
the result is not an artifact of the particular draw.

**Confidence-interval procedure.** Percentile bootstrap, 20,000 resamples, seed
7. **The resampling unit is the target**: each resample draws *n* targets with
replacement from the *n* panel targets and recomputes the mean Δ; the 2.5th and
97.5th percentiles are reported. Per-target AUROC is held fixed, so the interval
captures spread **between** targets and **excludes** the sampling error within
each target's 63–167 labelled compounds. The true uncertainty is wider than
stated.

**Sign test.** Two-sided exact binomial on the number of targets with Δ > 0,
against the null that Δ is equally likely positive or negative (p = 0.5). The
alternative is two-sided: Δ positive or negative more often than chance, with no
direction assumed.

**What the interval does and does not cover.** It describes dispersion among
*these* targets, selected by label availability and class counts. **It does not
establish generalisation to other targets**, because the panel is not a random
sample of targets — it is the targets with enough bundled, labelled compounds to
measure at all. Any extrapolation beyond this panel is unsupported.

## Limits

- **Four held-out targets.** The held-out comparison rests on 4 targets and
  63–74 labelled compounds each. It is weak evidence, reported because it is the
  only part of the panel that speaks to unseen targets.
- **All exposure analysis is for one checkpoint and one split**
  (`cold_protein-v3`, seed 20260930). The published benchmark reports ≥ 5 seeds
  across five splits; this reads the single shipped artifact.
- **19% pair survival** through the bundle restriction (5,033 of 26,421), because
  the bundled library is a deterministic 25,000-compound prefix of 232,721
  eligible compounds. The panel describes the shipped library, not the benchmark.
- **Observed association, not a ruled-out effect.** The correlation between
  target-specific advantage and the count of evaluation compounds co-seen with
  that target in training is **r = −0.011 across twelve targets**. That is an
  observed association in a very small sample. It does **not** exclude an overlap
  effect: twelve points cannot detect one, and the estimate is compatible with a
  wide range of true values.
- Positive rates run 0.30–0.84, so AUROC is the headline and average precision
  is reported only alongside its base rate.

## What was not done

No retraining, no representation change, no tuning against this panel, no
reselection after seeing results, and no edit to any published result,
prediction, evaluation file or checkpoint. The bundle and every score are
unchanged.
