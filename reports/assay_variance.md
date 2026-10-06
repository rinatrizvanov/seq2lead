# M5 — assay-context variance

Generated 2026-09-29 13:03 UTC. Analysis `m5/v2`.

Endpoint under analysis: **`ki-pki6-v2`** (id 96, pKi >= 6.0). Superseded versions are refused by default, so the corrected `-v2` build is what these numbers describe.

The question M4 could not answer. `pair_regression` takes a median over every exact Ki observation for a pair, which assumes they measure the same quantity. If changing assay format shifts Ki systematically, that median averages across conditions rather than across noise. **Nothing here builds splits or trains anything, and no M4 data was altered.**

## Denominators

| Population | Pairs | Share of regression pairs |
| --- | --- | --- |
| `pair_label` rows | 487,562 | — |
| `pair_regression` rows (**the denominator below**) | 421,059 | 100.0% |
| with >= 2 exact observations | 63,289 | 15.0% |
| spanning more than one assay | 51,164 | 12.2% |
| spanning more than one publication | 42,024 | 10.0% |
| with at least one usable assay description | 410,009 | 97.4% |
| discordant (exact spread > 1 pKi) | 9,624 | 2.3% |
| **no benchmark label** (all causes) | 2,809 | 0.7% |
| -- exact value outside its censored bounds | 2,612 | 0.6% |
| -- censored bounds cannot all hold | 173 | 0.0% |
| -- no usable magnitude or bound | 24 | 0.0% |

The 2,809 unlabelled pairs are **not all contradictory**. 2,612 are exact-versus-bound conflicts, 173 have censored bounds that cannot all hold, and 24 carry no usable evidence at all. An earlier revision labelled the whole total 'contradictory', which conflated three different failures.

**Only 63,289 of 421,059 pairs (15.0%) have any repeat observation at all.** Every variance statement below rests on that minority; the other 85% contribute a single measurement and say nothing about reproducibility.

## Covariate missingness, measured first

Measured before attempting any decomposition, because a decomposition over covariates that are absent produces numbers with no referent.

| Covariate | Present | Of | Coverage |
| --- | --- | --- | --- |
| assay_id linked | 516,252 | 539,171 | 95.75% |
| assay description non-empty | 516,252 | 539,171 | 95.75% |
| assay name non-empty | 516,252 | 539,171 | 95.75% |
| publication linked | 539,171 | 539,171 | 100.00% |
| pH recorded | 34,551 | 539,171 | 6.41% |
| temperature recorded | 30,484 | 539,171 | 5.65% |
| curation source recorded | 539,171 | 539,171 | 100.00% |
| publication date recorded | 539,171 | 539,171 | 100.00% |

**pH and temperature are unusable as covariates.** At ~6% coverage, any condition effect they carry is unidentifiable for the other 94%, and conditioning on them would silently restrict the analysis to a self-selected subset. They are excluded from the decomposition.

A correction to the M1 pilot: it reported pH and temperature as **0%** populated. The full release has 6.41% and 5.65%. The pilot's figure was a property of the PDSP Ki subset, not of BindingDB, and generalising it was wrong.

## What can and cannot be identified

| Pairs with >= 2 exact observations | Count | Share |
| --- | --- | --- |
| no assay id on any observation | 3,191 | 5.0% |
| assay id on some observations only | 2,107 | 3.3% |
| **assay varies, publication fixed** | 11,429 | 18.1% |
| publication varies, assay fixed | 0 | 0.0% |
| both vary (**confounded**) | 37,195 | 58.8% |
| neither varies | 9,367 | 14.8% |
| **sum** | **63,289** | |
| population | 63,289 | 100% |

**The table now reconciles** (63,289 = 63,289). An earlier revision used `count(DISTINCT assay_id)`, which ignores NULLs, so the 3,191 pairs with no assay id and the 2,107 with partial coverage fell into no bucket and the total silently fell short.

**A publication effect cannot be estimated at all.** Exactly **0 pairs** hold the assay fixed while the publication changes. An earlier revision reported 849 here; that figure was an artifact of counting a NULL assay id as a single distinct assay, and it is withdrawn.

In 37,195 pairs (59%) assay and publication move together and cannot be separated: a difference there is jointly attributable to assay format, laboratory, compound batch, protein preparation and reporting convention.

## How much replication is restatement

Of 539,171 exact Ki observations, **6,512 (1.21%) are *potential* duplicate reports** — the same value, under the same assay id and the same publication, for the same pair, across 6,142 keys. That pattern is consistent with one experiment written twice; it is **not proof of it**. A paper can legitimately report two runs of one assay that agree to the reported precision, and BindingDB's assay id is a curation grouping rather than an experiment identifier.

Separately, 5,927 of 14,252 within-assay groups (41.6%) have a spread of exactly zero.

**A correction to the previous revision of this report.** It treated that zero-spread fraction as restatement and removed it from the primary analysis. That was wrong twice over. **Zero-spread groups are not all restatements** — two independent measurements can agree, and agreement is the outcome the analysis exists to detect. And the rate of even *potential* duplication is 1.21%, not 41.6%. Removing agreement on suspicion deletes exactly the evidence that would support pooling, which biases the answer toward disagreement.

**Zero-spread observations are therefore kept in the primary analysis.** Only provenance-demonstrable duplicates are collapsed, and only in the sensitivity row below. Assay and publication ids remain an upper bound on independent replication, never a replicate count.

## Within- against between-assay spread, on a matched population

Both quantities are computed from **the same pairs**. A pair qualifies only if it holds publication fixed, spans more than one assay, and has at least one assay carrying two or more observations — so within- and between-assay spread are both computable from that pair alone.

A previous revision drew the within-assay figure from every pair and the between-assay figure from the publication-fixed subset. Those were different populations, and their difference did not mean what it appeared to.

**Pairs supporting both comparisons: 623** — 0.15% of regression pairs and 1.0% of pairs with any repeat. Everything in this section rests on that population.

| Comparison | n | p10 | median | p90 | share > 1 pKi |
| --- | --- | --- | --- | --- | --- |
| Within one assay | 846 | 0.000 | **0.032** | 1.195 | 15.5% |
| Across assays, publication fixed | 623 | 0.025 | **0.373** | 1.289 | 15.2% |

**These two medians are not weighted alike, and their difference (+0.341 pKi) is not an assay effect.** The within-assay row is a distribution over 846 *(pair, assay) groups*; the between-assay row is a distribution over 623 *pairs*. A pair spanning three qualifying assays contributes three within-assay values and one between-assay value, so pairs with more assay records are weighted more heavily on one side of the comparison than the other. The difference of medians across differently-weighted units is a descriptive contrast, not an estimate of anything.

**This is an association, not a causal estimate, and the wording in the previous revision ("changing assay alone shifts Ki") overstated it.** Holding publication fixed removes the between-paper contribution and nothing else. Remaining confounders inside a single publication include: different compound batches or lots; different protein constructs, preparations or suppliers; different sub-experiments reported together; differing numerical precision; and the assignment of assay ids itself, which is a curation act rather than an experimental fact. Any of these could produce the same association with no assay-format effect at all.

### Sensitivity to duplicate handling

| Population | Pairs | Within median | Between median | Association |
| --- | --- | --- | --- | --- |
| Primary (all observations kept) | 623 | 0.032 | 0.373 | **+0.341** |
| Potential duplicates collapsed (different cohort) | 503 | 0.459 | 0.370 | **-0.089** |

**The two rows are not the same comparison.** Collapsing potential duplicate reports drops 120 of the 623 qualifying pairs, because a pair whose repeats were all identical no longer has an assay group with two or more observations and stops qualifying. **The eligible cohort changes**, so the second row describes a different and smaller population, not the same population measured more carefully.

Read with that caveat, the contrast moves from +0.341 to -0.089 pKi and changes sign. Whether that reflects duplicate handling, cohort change, or both cannot be separated here. It is reported because a quantity this unstable under a defensible preprocessing choice should not be relied on in either direction — not because the second number is the better one.

The tail, on the primary population:

| Between-assay spread exceeds | Share of qualifying pairs | In Ki terms |
| --- | --- | --- |
| 0.3 pKi | 61.6% | 2x |
| 0.5 pKi | 36.1% | 3x |
| 1.0 pKi | 15.2% | 10x |

## Discordant and contradictory pairs

Both are included in the diagnostic above and identified separately here. They are excluded from *evaluation*, not from *understanding the data* — excluding them from the diagnostic would remove exactly the evidence the diagnostic exists to find.

| Population | Pairs with >= 2 obs | Within-assay median | Across-assay median | share > 1 pKi |
| --- | --- | --- | --- | --- |
| All pairs (diagnostic, includes discordant and contradictory) | 63,289 | 0.005 | 0.001 | 11.9% |
| Discordant pairs removed | 53,665 | 0.001 | 0.000 | 0.0% |
| Evaluation-eligible pairs only | 53,036 | 0.001 | 0.000 | 0.0% |

**The change when discordant pairs are removed is circular and must not be read as reassurance.** Discordant is *defined* as exact spread > 1 pKi, so removing those pairs mechanically drives every `share > 1 pKi` to 0.0%. That is arithmetic, not evidence of agreement. The only honest reading is the all-pairs row.

## Traceable examples

Drawn from the **matched, publication-fixed** population. Every assay group shown carries at least two observations, so the claim that observations agree within an assay rests on actual repeats rather than on a single measurement trivially having zero spread with itself.

```
pair_regression 1581413  compound=2467827  target=479
  n_obs=6  p_median=7.959  p_spread=2.516  discordant=True  excluded=discordant
  3 assays; between-assay spread 2.516 pKi; worst within-assay 0.000 pKi
    assay 109386  pub 79442  'ChEMBL_302570 (CHEMBL875218)'
      activity 7936750 <- raw_measurement 2432276  pKi=9.174
      activity 7936700 <- raw_measurement 2432226  pKi=9.174
    assay 109390  pub 79442  'ChEMBL_308672 (CHEMBL833648)'
      activity 7936831 <- raw_measurement 2432357  pKi=7.959
      activity 7936849 <- raw_measurement 2432375  pKi=7.959
    assay 109393  pub 79442  'ChEMBL_302763 (CHEMBL838827)'
      activity 7936832 <- raw_measurement 2432358  pKi=6.658
      activity 7936759 <- raw_measurement 2432285  pKi=6.658
```

```
pair_regression 1637950  compound=2613913  target=1967
  n_obs=4  p_median=4.722  p_spread=2.430  discordant=True  excluded=discordant
  2 assays; between-assay spread 2.414 pKi; worst within-assay 0.023 pKi
    assay 150871  pub 91501  'ChEMBL_1520445 (CHEMBL3624574)'
      activity 8322796 <- raw_measurement 2819326  pKi=3.500
      activity 8322825 <- raw_measurement 2819355  pKi=3.523
    assay 150874  pub 91501  'ChEMBL_1520444 (CHEMBL3624443)'
      activity 8322756 <- raw_measurement 2819286  pKi=5.921
      activity 8322785 <- raw_measurement 2819315  pKi=5.930
```

```
pair_regression 1618956  compound=2563734  target=577
  n_obs=6  p_median=7.292  p_spread=2.388  discordant=True  excluded=discordant
  3 assays; between-assay spread 2.388 pKi; worst within-assay 0.002 pKi
    assay 137489  pub 87380  'ChEMBL_1290726 (CHEMBL3117738)'
      activity 8200514 <- raw_measurement 2696858  pKi=7.291
      activity 8200511 <- raw_measurement 2696855  pKi=7.292
    assay 137491  pub 87380  'ChEMBL_1290725 (CHEMBL3117737)'
      activity 8200510 <- raw_measurement 2696854  pKi=6.780
      activity 8200513 <- raw_measurement 2696857  pKi=6.780
    assay 137492  pub 87380  'ChEMBL_1290727 (CHEMBL3117739)'
      activity 8200512 <- raw_measurement 2696856  pKi=9.167
      activity 8200515 <- raw_measurement 2696859  pKi=9.167
```

## Decision

### The pre-declared rule, kept as historical evidence

Fixed in `assay_variance.py` before any number was computed, and expressed in pKi so it means something chemically (0.3 pKi is 2x in Ki, 1.0 is 10x):

```
if median(within-assay spread) >= 1.0:  endpoint unsuitable
elif median(across-assay) - median(within-assay) <= 0.3:  pool
elif median(within-assay) < 0.5:  restrict to assay groups
else:  endpoint unsuitable
```

Applied to the all-pairs comparison it returned: **pool**.

| Margin (pKi) | Verdict |
| --- | --- |
| 0.2 | pool |
| 0.3 | pool |
| 0.5 | pool |

**That verdict is recorded, not relied on.** It was produced by a rule that compares medians and is blind to the tail, on a population that mixed matched and unmatched comparisons, before the duplicate question was posed correctly. A rule cannot validate the thing its failure mode concerns: the `pool` result is evidence about the rule, not about pooling.

### What the corrected analysis supports

On the matched, publication-fixed population of 623 pairs, the descriptive contrast is +0.341 pKi between differently-weighted units, and it changes sign (-0.089) once potential duplicates are collapsed and the eligible cohort shifts with them. The evidence base is small, the contrast is unstable and not an effect estimate, and a publication effect cannot be estimated at all. **None of that demonstrates that assays disagree; none of it demonstrates that they agree.**

### Operative status for M6 and beyond

```
provisional_pooled_for_exploratory_benchmark
```

Ki is pooled across assays **provisionally, for exploratory benchmarking only**. The pre-declared decision rule returned `pool`, but it compared medians and was blind to a tail in which a quarter of assay-spanning pairs differ by more than tenfold. Pooling is therefore an operating assumption carried forward under protest, not a validated conclusion. Any result computed on pooled Ki inherits this limitation and must state it.

This status is emitted by `seq2lead.profiling.status_banner` into every report built on pooled Ki, so a downstream number cannot be read without it. It is not a decision to pool; it is a decision to proceed **provisionally, for exploratory benchmarking**, with the limitation attached.

### Assay-spread audits must be partitioned

The natural next step is to attach each pair's assay-to-assay spread so downstream code can see which medians rest on agreeing assays. Done naively that leaks: a spread computed over all observations of a pair summarises measurements that may land in validation or test, and any filtering, weighting or feature derived from it would carry held-out information into the model.

The rule for M6, to be enforced in code rather than remembered:

1. **Model-facing spread statistics are computed within the training partition only.** A pair's spread as seen by a model is the spread of its *training* observations, even where more exist.
2. **Audit-facing spread statistics may use every observation**, but are written to reports only and never joined into a feature, a filter or a sample weight.
3. **The two are stored under different names** so a join cannot confuse them, and the partition-scoped one carries its `split_version`.
4. **Pairs are the unit of partitioning.** Splitting observations of one pair across partitions would put a measurement of a test pair into training, which is the leak this rule exists to prevent.

## Limitations

| # | Limitation |
| --- | --- |
| 1 | **Assay and publication are confounded in 59% of repeat pairs.** Only 11,429 pairs isolate the assay effect, and only 0 isolate the publication effect — too few for the latter to be estimated at all. |
| 2 | **pH and temperature are ~6% populated** and are excluded. Any temperature or buffer effect is unmeasured, not absent. |
| 3 | **Assay ids are not replicate counts.** 41.6% of within-assay groups are literal restatements. Independent replication is strictly less than the counts suggest, and how much less cannot be determined from the data. |
| 4 | **No random-effects model was fitted.** With confounded factors, restated observations and a covariate at 6% coverage, a variance-components model would attribute variance to factors the design cannot separate. The descriptive comparison is what the data supports. |
| 5 | **Only 15% of pairs have any repeat.** The other 85% are single measurements whose reproducibility is entirely unknown, and nothing here licenses an assumption about them. |
| 6 | **Assay description text was not parsed.** 95.75% of observations have one, but grouping assays into comparable formats from free text is its own piece of work and was not attempted; `assay_id` identity is the proxy used. |

