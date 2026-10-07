# Does the model rank differently per target, and does that help?

Measured 2026-10-07 against the shipped v0.2.0 bundle, on a bounded panel of
targets **with held-out labels** and **documented training exposure**. Read-only:
no score, rank, bundle, checkpoint or published result was modified, and nothing
here was tuned.

Raw numbers: [`labelled_panel.json`](labelled_panel.json).
Selection rule: [`labelled_panel_selection_rule.md`](labelled_panel_selection_rule.md).

## Why this exists

The unlabelled panel in [`QUERY_DIAGNOSTICS.md`](QUERY_DIAGNOSTICS.md) found
unrelated proteins ranking the library at ρ up to 0.9366. That panel has no
labels, so it could not say whether the model changes rankings between targets in
a way that *helps*, and its four non-example proteins sit far outside the
training distribution. This panel answers the question the other one could not.

## How the panel was chosen

The rule was written down and committed **before any score was computed**, so the
panel cannot have been picked for a flattering result. In short: held-out pairs
from the published as-of evaluation, compounds restricted to those present in the
bundled library, targets needing **≥ 10 actives and ≥ 10 inactives**, ordered by
labelled-compound count with a digest tie-break, first **12** taken. The full
rule is in the companion file.

What the funnel cost:

| Stage | Count |
| --- | --- |
| Labelled, scoreable held-out pairs | 26,421 |
| …whose compound is in the bundled 25,000 library | 5,033 |
| Distinct targets with at least one such pair | 618 |
| …meeting the ≥10/≥10 floor | 23 |
| **Panel** | **12** |

Only 19% of labelled pairs survive the bundle restriction, because the bundled
library is a deterministic 25,000-compound prefix of 232,721 eligible compounds.
The panel is therefore a view of the benchmark *through the shipped artifact*,
not the benchmark itself.

## Training exposure

**All twelve panel targets appear in the training membership export**
(`data/asof/m11f/a-membership.jsonl`, digest verified against the run manifest).
These are seen targets. Nothing below speaks to cold-target generalisation.

Per-target exposure varies: training rows for the target range from 240 to 9,735,
and the number of this target's *evaluation* compounds that were also seen with
it during training ranges from **0 to 59**. The correlation between that overlap
and the target-specific advantage is **r = −0.011** — effectively none, across
twelve points.

## The comparison

- **Target-specific ranking** — the shipped bundle's score for that target's
  sequence against each labelled compound.
- **Query-independent baseline** — a single compound ranking that ignores the
  query, built from the mean per-target z-scored library score over **40
  reference targets drawn deterministically (seed 20261007) from the 10,698
  eligible targets, with all 12 panel targets excluded**. It uses no labels and
  nothing from the target it is scored against. A second baseline, leave-one-out
  across the panel, is reported in the JSON and behaves similarly
  (macro AUROC 0.7430).

## Results

| Target | len | act | inact | AUROC | baseline | Δ | AP | baseline AP |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `A9754AA0EED8` | 440 | 157 | 10 | 0.9994 | 0.9274 | **+0.0720** | 1.000 | 0.995 |
| `8BB8E760C142` | 458 | 137 | 29 | 0.8807 | 0.7289 | **+0.1518** | 0.966 | 0.919 |
| `075CD160A22E` | 431 | 87 | 74 | 0.9231 | 0.7080 | **+0.2151** | 0.912 | 0.729 |
| `4D0C1AE8D9C6` | 400 | 50 | 33 | 0.8570 | 0.4539 | **+0.4031** | 0.914 | 0.541 |
| `945340616BAF` | 380 | 52 | 29 | 0.8402 | 0.6180 | **+0.2222** | 0.897 | 0.672 |
| `0CF62E1CCB94` | 176 | 62 | 12 | 0.7540 | 0.8078 | −0.0538 | 0.949 | 0.950 |
| `B75060695513` | 372 | 39 | 33 | 0.9068 | 0.4328 | **+0.4740** | 0.873 | 0.486 |
| `0F79DD8C1EA2` | 770 | 10 | 59 | 0.7356 | 0.6627 | **+0.0729** | 0.336 | 0.241 |
| `0153DBEBB226` | 465 | 49 | 19 | 0.5478 | 0.7680 | **−0.2202** | 0.787 | 0.897 |
| `8C2B6E1477FF` | 443 | 25 | 41 | 0.7873 | 0.8068 | −0.0195 | 0.760 | 0.763 |
| `351664067C22` | 622 | 48 | 16 | 0.9635 | 0.8776 | **+0.0859** | 0.988 | 0.957 |
| `051FC4732E49` | 326 | 19 | 44 | 0.5598 | 0.6244 | −0.0646 | 0.381 | 0.401 |
| **macro** | | | | **0.8129** | **0.7014** | **+0.1116** | 0.814 | 0.713 |

`8C2B6E1477FF` is the example sequence shipped with the repository. It entered by
the selection rule, not by choice, and it is one of the four where the
query-independent baseline wins.

## The two questions, answered

### 1. Does the model meaningfully change rankings between targets?

**Yes, on in-domain targets.** Mean pairwise Spearman between the twelve panel
targets' rankings of the full 25,000-compound library is **0.42**, range **−0.02
to 0.88**. Some pairs are nearly uncorrelated.

This materially revises the impression from the unlabelled panel, where unrelated
proteins agreed at 0.88–0.94. That panel was not representative: three of its four
non-example proteins are not Ki-assay targets at all. **Rank agreement between two
arbitrary queries is not a fixed property of the model** — it depends heavily on
whether the queries resemble anything it was fitted on.

### 2. Do those changes improve discrimination of measured actives?

**On average yes, but weakly and inconsistently.**

| | Value |
| --- | --- |
| Macro AUROC, target-specific | 0.8129 |
| Macro AUROC, query-independent | 0.7014 |
| Mean Δ | **+0.1116** |
| 95% bootstrap CI over targets | **[+0.0063, +0.2203]** |
| Targets improved | 8 / 12 |
| Sign test, two-sided | **p = 0.388** |
| Per-target Δ range | **−0.2202 to +0.4740** |

Read carefully:

- The interval excludes zero, but its lower bound is **+0.006** — the panel is
  consistent with an advantage close to nothing.
- The sign test does **not** reject chance. Eight of twelve improving is what a
  coin would often do.
- The spread is the real finding: Δ runs from −0.22 to +0.47. The model is not
  uniformly target-aware; it is strongly target-aware for some targets and
  actively worse than a query-independent ranking for others.
- **A query-independent ranking already reaches 0.7014 macro AUROC.** Most of the
  discrimination on this panel is a compound-side prior that does not depend on
  the query at all. This is the same phenomenon the benchmark's ligand-only gate
  (B1) exists to catch, now visible through the shipped artifact.
- AUROC is the headline because average precision is inflated here: positive
  rates run from 0.30 to 0.84, and several targets have far more actives than
  inactives.

## Limits

- **Twelve targets, 63–167 labelled compounds each.** Per-target AUROC carries
  sampling error that the bootstrap over targets does not include.
- **All twelve were seen in training.** This says nothing about new targets, which
  is the case the product is for.
- **19% pair survival** through the bundle restriction. The panel describes the
  shipped library, not the benchmark.
- **One model, one bundle, no seeds.** The published benchmark reports ≥ 5 seeds;
  this diagnostic reads the single shipped artifact.
- The reference baseline depends on a seeded draw of 40 targets. A different seed
  would move it somewhat; the leave-one-out variant agrees within 0.042 macro
  AUROC, which is some evidence it is not an artifact of that draw.

## What was not done

No retraining, no representation change, no tuning against this panel, no
reselection after seeing results, and no edit to any published result, prediction
or evaluation file. The bundle and every score are unchanged.
