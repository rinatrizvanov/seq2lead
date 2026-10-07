# Checkpoint-bound evaluation — specification

**Status: SPECIFIED, NOT EXECUTED.** Nothing in this document has been run. No
score, rank or metric described here exists yet. It is written in advance so that
every choice is fixed before any result is visible, and so that running it later
is a mechanical act rather than a series of judgement calls made while looking at
outcomes.

Written 2026-10-07. Supersedes nothing: the twelve-target panel in
[`labelled_panel.md`](labelled_panel.md) and every existing artifact are retained
exactly as they stand.

## Why

The twelve-target panel was selected against the **m11h as-of** evaluation, which
is not the experiment the deployed checkpoint came from. Eight of its targets
turned out to be train-exposed to that checkpoint, leaving four genuinely held
out — too few to support any directional conclusion. The fix is not a better
panel but the right population: evaluate the deployed checkpoint on **its own**
held-out partition.

## 1. The artifact under test — frozen

| | |
| --- | --- |
| Checkpoint | `data/m9/final/M9-dual-encoder__cold_protein-v3__seed20260930.pt` |
| SHA-256 | `934adcdb1a71b71c3147a00b9bd74a953394642522abeb4a038e99d7f6c86c6e` |
| Experiment | `m9-dual-encoder-v1`, config `58662cd2033d81c73e0b3b90fdea0861d7d9f19fe76bff49cbb7ee37958b951f` |
| Split | `cold_protein-v3`, split_id 10, seed 20260929, builder `m6/v2` |
| Endpoint | id 96, `ki-pki6-v2`, measurement type KI, threshold pKi ≥ 6.0, not superseded |
| Compound universe | bundled `curated-ki-25k-v1`, member digest `7c0ea2ec20f8b30e…` |
| Scoring | as shipped: `scale · cosine(compound_z, protein_z) + offset`, scale 7.0876760, offset 7.0619078 |

The run must assert each of these before scoring and abort on any mismatch.

## 2. Population — all qualifying targets, no sampling

Every target in the **`test` partition of `cold_protein-v3`** that qualifies.
There is no draw, no cap and no ordering, so there is no opportunity to select on
outcome.

**Qualification** (label counts only, no score involved):

1. target is in the `test` partition of split 10;
2. its sequence resolves from `target.sequence_sha256` and has length 20–2,000;
3. labels are taken from `pair_label` at endpoint 96, `label ∈ {active, inactive}`,
   `excluded_from_eval` false;
4. the compound is present in the bundled library, matched by InChIKey of the
   RDKit-standardised parent;
5. **≥ 10 actives and ≥ 10 inactives** survive 3–4.

**Measured feasibility** (counts only, computed 2026-10-07; no model was loaded):

| Floor | Targets | Labelled compounds | Median per target |
| --- | --- | --- | --- |
| ≥ 5 / ≥ 5 | 300 | 98,126 | 112 |
| **≥ 10 / ≥ 10** | **241** | **96,088** | **168** |
| ≥ 15 / ≥ 15 | 212 | 94,552 | 216 |
| ≥ 20 / ≥ 20 | 191 | 92,458 | 256 |
| ≥ 25 / ≥ 25 | 164 | 88,920 | 288 |

621 test-partition targets have at least one bundled labelled compound. **The
floor is ≥ 10 / ≥ 10, fixed here, giving 241 targets.** The other rows are
recorded so the choice is visible, not so it can be revisited after seeing
results. Results at other floors, if computed, ship as a labelled sensitivity
analysis and never replace the headline.

## 3. Denominators — stated per metric

Ambiguity about denominators is how ranking metrics become incomparable. Fixed:

- **Per-target ranking set** = that target's qualifying labelled compounds only
  (median 168), *not* the full 25,000. Every metric is computed within this set.
- **AUROC** denominator: all (active, inactive) pairs within the ranking set.
- **Average precision** is reported **always beside its positive rate**; the
  positive rate is the denominator that makes it readable.
- **EF@k%** uses `k% of the ranking set`, rounded to nearest integer, minimum 1,
  with both the numerator (actives retrieved) and the denominator (compounds
  examined) printed. Reported at k ∈ {1, 5, 10}; where the ranking set is too
  small for k=1 to exceed one compound, that cell is `n/a`, never silently
  rounded up.
- **Macro averages** are unweighted over qualifying targets; the target count is
  printed next to every macro figure.
- No metric is pooled across targets.

## 4. Baselines — frozen before execution

Three, all computed without labels, all fixed here:

- **B-ref40** — the reference-set compound ordering: score the library for each
  of 40 reference targets, z-score within each target, average per compound.
  Reference targets drawn **without replacement, uniform, seed 20261007**, from
  database targets of length 20–2,000, **excluding every target in the evaluation
  population and every target in the `train` or `validation` partition of split
  10**. The exclusion is wider than before: a reference target that the
  checkpoint trained on would make the baseline partly a trained artifact.
- **B-prior** — rank by each compound's mean score across the *same* reference
  set without z-scoring, to show the result is not an artifact of normalisation.
- **B-random** — a seeded random permutation of the ranking set, seed 20261007,
  as a floor. Expected AUROC 0.5; a run that does not reproduce ≈ 0.5 here is
  broken and must be discarded.

The target-specific ranking is the shipped scoring path, unchanged.

## 5. Exposure checks — assertions, not reports

The run aborts if any fails:

1. no evaluation target appears in the `train` or `validation` partition of split 10;
2. no reference target appears in the evaluation population, or in `train`/`validation`;
3. every evaluation pair is in the `test` partition of split 10;
4. the checkpoint digest matches §1 exactly;
5. the bundled library member digest matches §1 exactly;
6. no label used was sourced from any endpoint other than 96.

**Recorded, not asserted** — because it cannot be resolved from the artifacts and
must not be silently treated as clean: whether the measurement rows underlying
each test-partition label were consumed in fitting. `cold_protein-v3` carries no
activity-level assignment (`split_activity_assignment` is empty for split 10;
only temporal splits 8/15/16 have it), so the run records this as **unresolved
lineage** in its output and states it in any summary. A pair-level test assignment
is the strongest available guarantee and is what assertions 1–3 rest on.

## 6. Paired uncertainty

The target-specific ranking and each baseline are evaluated on **identical**
compound sets, so the comparison is paired and must be analysed that way.

- **Per target**: Δ = AUROC(specific) − AUROC(baseline) on that target's ranking
  set. A **paired bootstrap over compounds within the target**, 10,000 resamples,
  seed 7, resampling the ranking set with replacement and recomputing **both**
  rankings' AUROC on each resample, preserving the pairing. Report the 2.5th and
  97.5th percentiles of Δ.
- **Across targets**: a **hierarchical bootstrap**, 10,000 resamples, seed 7 —
  resample targets with replacement, then resample compounds within each drawn
  target, recomputing Δ throughout. This propagates both sources; it is the
  headline interval.
- **Paired test**: two-sided Wilcoxon signed-rank over per-target Δ, with the
  exact two-sided sign test reported beside it. The alternative is two-sided —
  Δ differs from zero, no direction assumed. Report the test statistic, the
  p-value, and the number of targets improved.
- **Effect size**: median per-target Δ with its interquartile range, alongside the
  mean, because the twelve-target panel showed Δ is heavy-tailed.

Every interval states its resampling unit in the output. No interval is presented
as establishing generalisation beyond the evaluated population: the population is
all qualifying targets in one split's test partition, which is a census of that
partition, not a sample of targets in general.

## 7. Outputs

Written to new paths under `reports/diagnostics/`; nothing existing is
overwritten:

- `checkpoint_bound_evaluation.json` — per-target rows, macro summaries,
  intervals, the frozen configuration from §1, and the exposure assertion results;
- `checkpoint_bound_evaluation.md` — the narrative, with Measured, Hypotheses and
  Not established kept separate as in the existing reports.

## 8. Cost, measured in advance

241 evaluation targets plus 40 reference targets = 281 ESM-2 embeddings and 281
library scorings of 25,000 compounds.

**Measured on this machine**, not estimated: 8 database target sequences of
length 99–503, embedded and scored end to end on the CPU device, took 9.1 s —
**1.14 s per sequence**. At that rate 281 sequences is **about 5.3 minutes**,
single process.

Two caveats on extrapolating it. The timing sample is short sequences; cost rises
with length, and the evaluation population is not length-matched to the sample.
And it was measured on the CPU device — the interface defaults to MPS, which
differs. Treat 5.3 minutes as the right order of magnitude, not a budget.

## 9. What this will and will not settle

**Will**: whether, on the deployed checkpoint's own held-out protein partition
and over a census of 241 qualifying targets, the target-specific ranking
discriminates measured actives better than a frozen query-independent ordering,
with paired intervals that propagate both compound- and target-level sampling.

**Will not**: anything about other splits, other seeds, other checkpoints, other
endpoints or other libraries; anything about calibration; and anything about
targets outside this partition. It also cannot resolve the evidence-lineage
question in §5, which needs an activity-level split record that does not exist
for `cold_protein-v3`.
