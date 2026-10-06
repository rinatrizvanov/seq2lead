# M4 — Ki endpoint

Generated 2026-09-29 12:52 UTC.

Provisional Ki endpoint tables built from the curated `activity` layer. **Ki only** — IC50, Kd and EC50 remain in `activity`, unmerged and untouched. No train/test partition is assigned here; `eval_exclusion_reason` records why a pair *would* be held out, and partitioning is M6.

> **Endpoint status: `provisional_pooled_for_exploratory_benchmark`**
>
> Ki is pooled across assays **provisionally, for exploratory benchmarking only**. The pre-declared decision rule returned `pool`, but it compared medians and was blind to a tail in which a quarter of assay-spanning pairs differ by more than tenfold. Pooling is therefore an operating assumption carried forward under protest, not a validated conclusion. Any result computed on pooled Ki inherits this limitation and must state it.
>
> See [`assay_variance.md`](assay_variance.md) for the evidence and its limits.

## Version

| Field | Value |
| --- | --- |
| Endpoint | `ki-pki6-v2` (id 96) |
| Measurement type | KI |
| Classification threshold | **pKi >= 6.0** |
| Discordance threshold | 1.0 pKi units |
| Source release | 117 |
| Curator | `m3/v3+rdkit-2026.03.6/cleanup+fragment-parent+uncharge/v1` |
| Builder | `m4/v2` |

The threshold is **predeclared**: fixed before any split exists and before any model is trained. Sensitivity versions are built as separate named endpoints rather than by mutating this one, so no threshold can be retrofitted to a result.

### Supersedes a corrected version

This version replaces `ki-pki6-v1` (builder `m4/v1`). The old version is **left intact and marked superseded**, not edited, so the correction is auditable.

> builder m4/v1 tested only the MEDIAN of exact values against the censored interval, so 87 pairs with an individual exact value outside their bounds kept status='ok' and a confident label. Superseded, not edited.

| Count | Before (`ki-pki6-v1`) | After (`ki-pki6-v2`) |
| --- | --- | --- |
| label = active | 332,952 | 332,903 |
| label = inactive | 145,325 | 145,287 |
| label = ambiguous | 6,563 | 6,563 |
| label = none | 2,722 | 2,809 |
| status = ok | 484,840 | 484,753 |
| status = exact_bound_conflict | 2,525 | 2,612 |
| **ok despite an exact outside bounds** | 87 | 0 |

The old rule tested only the **median** of the exact values against the censored interval. A pair with exact values at pKi 5 and pKi 9 under a bound of `pKi > 6` has a median of 7, which sits inside the interval, so the pair was labelled confidently — while the observation at pKi 5 flatly contradicts the bound. The rule now tests **every** exact observation.

Sibling versions: `ki-pki7-v2` (pKi >= 7.0), `ki-pki8-v2` (pKi >= 8.0).

## Input

| | Count |
| --- | --- |
| Ki activities read | 619,931 |
| Rejected: magnitude not positive and finite | 34 |
| Rejected: relation bounds nothing (`~`, `?`) | 0 |
| Distinct (compound, target) pairs | 487,562 |

Zero magnitudes occur in the source as `0.000` and `>0.000`. `log10(0)` is `-inf`, and `Ki > 0` is true of every compound ever made, so such records reach neither the regression nor the interval logic. They remain in `activity` with their original text.

`n_no_constraint` counts records whose relation bounds nothing — `~` (approximate) and `?` (operator not understood). In builder `m4/v1` this counter was declared but never incremented, so it always read zero and said nothing; it is now wired. It still reads zero here, but for a real reason: this release contains no `~` or `?` Ki records at all. On another source it would not.

## `pair_regression` — exact values only

| | Count |
| --- | --- |
| Pairs | 421,059 |
| Contributing activities | 539,171 |
| **Discordant (spread > 1.0 pKi)** | **9,624** (2.29%) |
| pKi median: min / median / max | 0.00 / 7.11 / 14.22 |
| Observations per pair: mean / max | 1.28 / 526 |

Only exact `=` records with a positive finite magnitude, converted as `pKi = 9 - log10(Ki_nM)`. **Censored records never enter a median**: `Ki > 10000` is not a measurement of 10000, and averaging censoring thresholds manufactures a value no experiment produced.

Observations per pair:

| n_obs | Pairs |
| --- | --- |
| 1 | 357,770 |
| 2 | 44,036 |
| 3 | 9,479 |
| 4 | 4,119 |
| 5 | 2,726 |
| 6 | 898 |
| 7 | 460 |
| 8 | 353 |
| 9 | 204 |
| 10+ | 1,014 |

Discordant pairs are flagged with `eval_exclusion_reason = 'discordant'`. That records the intent to keep them out of validation and test — a pair whose replicates disagree by more than 1.0 pKi unit cannot arbitrate a prediction. **No partition is assigned.**

## `pair_label` — exact points and censored bounds

| Label | Pairs | Share |
| --- | --- | --- |
| `active` | 332,903 | 68.28% |
| `inactive` | 145,287 | 29.80% |
| `ambiguous` | 6,563 | 1.35% |
| `none` | 2,809 | 0.58% |

| Status | Pairs | Meaning |
| --- | --- | --- |
| `ok` | 484,753 | evidence resolved to a label |
| `exact_bound_conflict` | 2,612 | an exact value falls outside its own censored bounds |
| `empty_intersection` | 173 | censored bounds cannot all hold at once |
| `no_usable_evidence` | 24 | every record had an unusable magnitude or no bound |

| Evidence | Pairs |
| --- | --- |
| `exact` | 417,655 |
| `censored` | 66,506 |
| `both` | 3,377 |
| `none` | 24 |

**2,809 pairs get no benchmark label**, and the reason is recorded rather than resolved. Censored bounds are combined by interval intersection on pKi — never by a median of censoring thresholds. Where the intersection is empty, or where the exact evidence sits outside it, the contradiction is the finding; inventing a label would hide it.

### The operator contract at the threshold

`pKi = 9 - log10(Ki_nM)` is monotonically *decreasing*, so a lower bound on Ki is an upper bound on pKi. At the threshold (Ki = 1,000 nM = pKi 6.0):

| Ki record | Constraint on pKi | Verdict |
| --- | --- | --- |
| `= 1,000` | `pKi == 6.0` | active |
| `< 1,000` | `pKi > 6.0` | active |
| `<= 1,000` | `pKi >= 6.0` | active |
| `> 1,000` | `pKi < 6.0` | inactive |
| `>= 1,000` | `pKi <= 6.0` | **ambiguous** |

The two lower bounds on Ki disagree exactly at the threshold: `>` excludes the endpoint so its whole range is inactive, while `>=` admits the endpoint, which is itself active, and therefore decides nothing. The two upper bounds agree, because the active side is the inclusive one. This is why inclusive and exclusive relations are not collapsed.

## Target coverage

3,805 targets and 252,130 compounds appear in this endpoint.

Counts below use **measured** actives and inactives only. No negatives are fabricated: an unmeasured pair is absent, not inactive.

| Measured actives and inactives per target | Targets |
| --- | --- |
| >= 1 active and >= 1 inactive | 2,077 |
| >= 10 each | 845 |
| >= 25 each | 530 |
| >= 50 each | 356 |
| >= 100 each | 212 |

Restricted to `in_benchmark_scope` pairs (single-protein targets). Whether any of this is poolable across assays is **M5's question**, not settled here.

## Evaluation eligibility

`excluded_from_eval` is an **explicit column on both tables**. Downstream code does not have to join `pair_label` to discover that a pair is unusable, and a regression pair whose label is contradictory is flagged on `pair_regression` too. Excluded means **out of validation and test**; whether a pair may be used for training is a separate decision for M6.

| Table | Excluded | Share |
| --- | --- | --- |
| `pair_regression` | 11,899 | 2.83% |
| `pair_label` | 18,632 | 3.82% |

| Reason | `pair_label` | `pair_regression` |
| --- | --- | --- |
| `discordant` | 9,260 | 9,260 |
| `ambiguous_label` | 6,563 | 0 |
| `exact_bound_conflict` | 2,612 | 2,612 |
| `empty_intersection` | 173 | 27 |
| `no_usable_evidence` | 24 | 0 |

**Precedence.** A pair can be both contradictory and discordant. Contradiction outranks discordance, because contradictory evidence cannot be reconciled at all while discordant evidence merely disagrees — and a contradictory pair has no label to score against in the first place. The full order is `exact_bound_conflict` > `empty_intersection` > `no_usable_evidence` > `discordant` > `ambiguous_label`.

Discordant pairs keep their label and their statistics: they are retained for audit and for the M5 assay-variance analysis, and may later be considered for training. They must not silently arbitrate validation or test, which is what the flag prevents.

## Audits

**Pairs on a collapsed compound: 7,014.** Their compound was reached from more than one source structure — salts, solvates or charge variants standardized to one parent. Every contributing source structure remains individually addressable in `compound_source`.

Denominator for every row below: **421,059 `pair_regression` rows** for this endpoint. Two scopes are given because they answer different questions, and quoting one number without saying which is how they get confused.

| Replication | All pairs | In benchmark scope | Query |
| --- | --- | --- | --- |
| More than one assay | 51,164 | 46,415 | `n_assays > 1` |
| More than one publication | 42,024 | — | `n_publications > 1` |
| Discordant **and** multi-assay | 7,256 | 6,369 | `is_discordant AND n_assays > 1` |

All counts are `WHERE endpoint_id = <this endpoint>`; the scoped column adds `AND in_benchmark_scope`.

That last row is the one M5 needs: replicate disagreement that coincides with an assay change is exactly the signal that Ki may not be poolable across assay formats. Quantifying it is M5's job; M4 only records the counts.

## Traceability

Every pair reaches its supporting activities, and every activity reaches the raw row it came from. Worked example:

```
pair_regression id=1263438  compound=1347493  target=17
  n_obs=4  p_median=10.180  spread=1.501
    activity 7157645 <- raw_measurement 1650022  =1.90 nM  assay=25954  structure=532de782308c…
    activity 7203377 <- raw_measurement 1695997  =0.06 nM  assay=25953  structure=532de782308c…
    activity 7224955 <- raw_measurement 1717626  =0.066 nM  assay=33779  structure=532de782308c…
    activity 7239843 <- raw_measurement 1732527  =0.066 nM  assay=35460  structure=532de782308c…
```

`raw_measurement_id` resolves into the immutable raw layer, whose release carries the artifact's SHA-256. `source_structure_sha256` identifies the exact SMILES **that row** carried, so no row inherits another's compound.

## Scientific decisions still open for M5

| # | Decision |
| --- | --- |
| 1 | **Is Ki poolable across assay formats at all?** A pair measured by radioligand displacement and by a functional assay may legitimately differ. 51,164 pairs span assays and 7,256 of those are discordant. Until that variance is decomposed, pooling is an assumption. |
| 2 | **Does the threshold survive sensitivity?** 6.0 is predeclared and 7.0 / 8.0 are built as siblings. Whether conclusions hold across them is an M5 reading, not an M4 claim. |
| 3 | **What to do with discordant pairs.** Flagged, not removed. Excluding them from validation and test is the recorded intent; whether they belong in training is undecided. |
| 4 | **Whether `ambiguous` pairs carry usable signal.** They have bounds that straddle the threshold. They are not negatives and must not be recruited as such. |
| 5 | **Assay context is largely absent.** `pH` and `Temp (C)` are sparse, so the variance decomposition may have to lean on assay-description text and publication identity instead. |
| 6 | **Organism conflicts.** 1,276 targets carry more than one organism annotation and `target.organism` is deliberately NULL for them. Whether those sequences are one target or several is unresolved. |

