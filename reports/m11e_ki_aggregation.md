# M11e — KI-only pair aggregation, eligibility and semantics

**Scope: aggregation and eligibility semantics only.** No model was fitted, no
prediction metric was computed, nothing was docked, nothing was downloaded, and
the confirmatory freeze is **not signed**. The study remains **exploratory** and
the **provisional-pooling** qualification stands.

Machine-readable record: `configs/manifests/m11e_ki_aggregation.json`.
Pre-registered sensitivity: `configs/m11e_cross_slot_sensitivity.json`
(`f8332101…`, declared and digested **before** any eligibility figure existed).

## 0. Correction passes — what changed, and what the published figures were

This is **v3** of the report. Records: `m11e-correction-v1` and `m11e-closeout-v1`
inside the manifest.

### Closeout (v3): the audit readings no longer depend on an increment

`_audit_record` took `training_from_a` from whichever arm produced a
`PairOutcome`. A pair whose entire increment was withheld against a correction
candidate produced **neither** outcome, so its historical A status, label and
exact statistics came back `None` — and the full-B reading was only available
inside an arm view, so it vanished too. That is exactly §7a example (b), the case
where training *must* keep A's value.

Both readings are now computed from their own evidence: `training_from_a` reads
`in_a` directly and `full_b_audit` reads `in_b` directly, neither depending on
what survived into an increment. Full-B moved to the **top level** of the record
rather than being duplicated per arm, because `in_b` is the same in both arms and
was never arm-dependent.

And an arm with no increment now **names** the reason instead of reporting a
blank: `no_additions_at_this_pair`,
`all_additions_withheld_as_correction_candidates`,
`all_remaining_additions_excluded_by_cross_slot_sensitivity`, or
`all_additions_had_values_that_would_not_normalise`. Those are four different
facts.

The discriminator — a pair with clean active A evidence whose single addition is
withheld — is in `tests/test_m11e_branch_accounting.py` and asserts that A's
status, label, exact statistics and discordance verdict all survive.

**Regenerated:** the pKi 6.0 pair audit (80 MB, 44,966 records) and all three
threshold summaries. **The figures did not move** — the audit is an output, not an
input — and that identity is itself the check that the change was confined to the
audit.

### Correction pass v1 (unchanged below)

### The cohort figures described the pre-screen pool

`ArmCounts._absorb` added every eligible pair to the cohort figures — target
coverage, class balance, rankability — **before** consulting whether that pair had
survived the full-B consistency screen. Branch membership was recorded separately.
So the published figures were correct figures **for the pre-screen pool**, read as
if they described the cohort.

**The discriminator.** Five pairs whose increment reads `ok/active` but whose
full-B evidence contradicts itself, plus five clean `inactive` pairs, all against
one target. Pre-screen the target has 5 and 5 and looks rankable at five per
class; in the screened primary branch it has **no actives at all** and is not
rankable at any floor. Reproduced before the fix in
`tests/test_m11e_branch_accounting.py`.

**The fix.** Cohort figures now live *inside* a consistency branch, and there are
**two independent axes** — increment arm (declared / cross-slot-excluded) ×
consistency branch (unscreened sensitivity / screened primary) — giving four
cells, all reported. Eligibility exclusions are decided before the screen and so
are counted once per arm.

### Recomputed, not assumed

| Conservative cell, pKi 6.0 | Published as the cohort (v1) | Recomputed cohort (v2) |
| --- | ---: | ---: |
| Pairs | 22,939 | **22,834** |
| Targets | 1,016 | 1,016 |
| Rankable at ≥5 per class | 118 | **117** |
| Positive rate | 0.730 | 0.731 |
| Population | pre-screen pool | screened primary branch |

The screen removes 105 pairs and **one** rankable target in this arm, so the
corpus turns out to be barely sensitive to the defect. That is a measurement, not
a reason the defect did not matter: the discriminator shows the same mistake
turning a rankable target into an unrankable one. **C1–C5 were re-evaluated
against the recomputed cohort and all five are still met.**

### Four further defects

| | Defect | Fix |
| --- | --- | --- |
| D2 | the pair audit exported counts and locators but not the decisions | every status, label, exact spread, eligibility reason, screen reason and branch membership, for **both** arms; records retained for pairs whose increment was entirely withheld or entirely excluded |
| D3 | increment measurements were paired to locators with `zip(strict=False)`, which would truncate to the shorter list and under-apply the locator-selected sensitivity | a length check raising `AlignmentError`, and `strict=True` |
| D4 | both pair shards were materialised **before** the row budget was checked | counted byte scan applies the combined budget first, then bounded reads; tested with a counter patched to lie |
| D5 | the A-independence digest compared status, label and count, so different A evidence sharing a label would pass | the digest carries the canonical A evidence, the exact-spread statistics and the discordance verdict; B-only pairs stay outside |

**What was rerun:** the full aggregation at all three thresholds, the audit export,
and the audit-to-summary reconstruction. **What was not:** the matcher (KI
classifications unchanged and still equal to the M11d column), the exports
(digests re-verified), the pair partition, the pre-registered sensitivity spec
(digest unchanged).

## 1. A wording correction carried over from M11d

Equal compound, target, operator and canonical value at two **different** slots
establishes a **candidate correspondence, not experimental identity.** Nothing in
the files says the two rows report the same experiment: they may be one
measurement recorded again under a new attribution, or two independent
measurements that happen to agree. M11d's wording slid between "candidate" and a
bare "a re-attribution" that presumed the interpretation; `sharded.py`, the M11d
report and its manifest now say candidate throughout, and "moved" is labelled as
shorthand for a counting operation.

The one **individually source-verified** case stays separate and is stronger than
value equality — those two rows share an entry DOI *and* a reactant set id, and
both locators were resolved to their raw bytes. Even there the files do not state
that the rows report one experiment; that is a well-supported inference about one
case and is not transferable.

## 2. What was run

| | |
| --- | --- |
| Inputs | the pinned exports, digests verified before processing (`0b67b64b…`, `2b2b4ad8…`) |
| Classifications | **reused** from the existing matcher, not re-derived |
| Sharding | **64 shards keyed on the compound–target pair** |
| Endpoint | KI only. IC50, KD and EC50 never entered the harness |
| Rules | the existing bridge and M4 endpoint logic — censoring, interval intersection, exact-versus-bound conflicts, empty intersections, discordance, threshold boundaries |
| Thresholds | pKi **6.0** primary (pre-registered), 7.0 and 8.0 as pre-registered sensitivities |
| Discordance | 1.0 log unit |
| Runtime | 101s per threshold, peak RSS **0.48 GB** |

### Why pair-sharding, and why the diff still sees all four types

**Eligibility is a property of a pair, not a slot.** One pair owns many slots, so
a per-slot-shard eligibility figure would be computed on a fraction of the pair's
evidence. The shards are therefore keyed on the pair — which is sound in both
directions, because a slot's compound and target *determine* its pair. Pair
sharding is a **coarsening** of slot sharding and keeps every slot whole as well
as every pair. Asserted directly: one pair's rows land in exactly one shard at
every shard count tested.

**But the diff must not be given KI-only input.** `diff_snapshots` decides
corrections, conflicts and ambiguity from a slot's whole surplus *across all
values at that slot*. The single identifier conflict in the corpus is an `IC50`
removal against an `EC50` addition — a KI-only diff could never see it, and the
classification of a KI row at the same slot could change with it. So the diff
runs on all four types, exactly as in M11d, and only its **outputs** are
restricted to KI. The classification each row received is preserved rather than
re-derived. Both halves of this are tested.

**Verified, not assumed:** the KI classifications from this pair-sharded run equal
the KI column of the slot-sharded M11d run exactly — unchanged **585,978**,
additions **33,953**, removals **28,777**.

## 3. Observations and pairs, reported separately

### KI observations

| | A (202601) | B (202609) |
| --- | ---: | ---: |
| KI observations | 614,755 | 619,931 |
| Unchanged | 585,978 | 585,978 |
| Removed from A | 28,777 | — |
| Added in B | — | 33,953 |

Every added KI observation is accounted for:

| | Observations |
| --- | ---: |
| Matcher KI additions | 33,953 |
| In some pair's increment | 33,941 |
| Withheld against a linked correction | 12 |
| Addition rows whose value would not normalise | 0 |
| **Sum** | **33,953** ✓ |

The 12 withheld match M11d's KI correction count exactly. Note that the
*arm-level* withheld figure is 1, not 12: **11 of those pairs had their entire
increment withheld**, so they contribute no increment and never enter an arm at
all. An earlier revision of this summary counted withholding only from pairs that
reached an arm and left nine occurrences unexplained.

### KI pairs

| | Pairs |
| --- | ---: |
| With KI evidence in either snapshot | 502,527 |
| Present in A | 482,196 |
| Absent from A | 20,331 |
| With KI removals | 22,247 |
| **With any increment** | **27,498** |
| Distinct compounds with an increment | 17,373 |

**Historical presence is not recurrence.** "Present in A" is presence in snapshot
A. It is **not** recurrence relative to an eventual fitted training set: which
pairs a model is actually trained on depends on split construction that has not
been run, so recurrence against a fitted set cannot be reported here and is not.
The `recurrent` / `new_pair` strata below are likewise defined against A, not
against a fitted set.

## 4. The three representations, kept separate

| Reading | Field | Source |
| --- | --- | --- |
| Training eligibility | `in_a` | historical A evidence alone |
| Consistency screen / audit | `in_b` | **complete** actual B, correction candidates included |
| Increment label | `increment` | the matcher's counted additions, minus one occurrence per linked correction |

Withholding applies to the **increment alone**. Withholding from `in_b` emptied
actual B for pairs B plainly had evidence for, and the full-B audit then read
`no_usable_evidence`. Explicit removals and source locators are preserved on every
pair; the streamed pair detail carries `increment_locators`, `withheld_locators`
and `removed_locators`.

### A's training evidence is unchanged by B — checked, and the check was wrong first

Per shard, the `(pair, status, label, n)` digest from the two-snapshot run is
compared against the same digest computed on a path that **never receives B**.
All 64 shards agree. Training labels from A alone: **active 328,549, inactive
144,493, ambiguous 9,154** — summing to the 482,196 pairs present in A.

The first version of this check reported **False**, and the fault was in the
check. It digested every pair the two-snapshot run enumerated, including the
20,331 pairs that appear first in B — whose `in_a` is empty and which the A-only
path correctly never sees. Restricted to pairs that **have** A evidence, the two
paths agree exactly. The B-only count is now reported separately rather than
folded into a digest that then compared two different populations.

## 5. Eligibility at the primary threshold (pKi 6.0)

Eligibility is decided **before** the consistency screen, so these exclusions are
the same in both consistency branches and are counted once per increment arm.

| | Declared increment | Cross-slot excluded |
| --- | ---: | ---: |
| Pairs with any increment | 27,498 | 23,871 |
| Excluded — discordant | 522 | 463 |
| Excluded — ambiguous label | 375 | 315 |
| Excluded — exact-versus-bound conflict | 177 | 151 |
| Excluded — empty intersection | 2 | 2 |
| Excluded — no usable evidence | 1 | 1 |
| **Pre-screen pool (eligible)** | **26,421** | **22,939** |
| Eligible increment observations | 33,941 | 29,910 |

**Labels, statuses and eligibility stay separate.** A discordant pair *has* a
label and an `ok` increment status; what it lacks is standing to be scored. Across
all 27,498 pairs the increment labels are active 19,231, inactive 7,712, ambiguous
555 — the ambiguous ones are then excluded, which is why the admitted label counts
are smaller.

## 6. Four cells: increment arm × consistency branch

The two axes are independent. The **increment arm** says which evidence counts.
The **consistency branch** says whether snapshot B's own evidence for a pair
contradicts itself: `screened_primary` admits only pairs whose full-B status is
not `exact_bound_conflict` or `empty_intersection`.

**pKi 6.0, all four cells:**

| Increment arm | Branch | Admitted | Targets | Single-class | ≥1 each | ≥5 | ≥10 | Active | Inactive | Positive rate |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| declared | unscreened sensitivity | 26,421 | 1,041 | 678 | 363 | 142 | 89 | 18,751 | 7,670 | 0.710 |
| declared | **screened primary** | 26,320 | 1,041 | 681 | 362 | 142 | 86 | 18,698 | 7,622 | 0.710 |
| cross-slot excluded | unscreened sensitivity | 22,939 | 1,016 | 665 | 351 | 118 | 69 | 16,756 | 6,183 | 0.730 |
| cross-slot excluded | **screened primary** | **22,834** | **1,016** | **668** | 350 | **117** | **66** | 16,696 | 6,138 | **0.731** |

The bottom row is the **conservative cell** and the one feasibility is judged on.

**The screen's effect at pKi 6.0** is small on this corpus: it removes 101 pairs
in the declared arm (97 `exact_bound_conflict`, 4 `empty_intersection`) and 105 in
the conservative arm (102 / 3), costing **no** targets and **one** rankable target.
That is a measured property of this snapshot pair, not a general one — the
discriminator in §0 shows the same accounting error removing a target's entire
active class.

### Across the pre-registered thresholds, conservative cell

| Threshold | Admitted | Targets | Single-class | ≥5 | ≥10 | Positive rate |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| pKi 6.0 | 22,834 | 1,016 | 668 | **117** | 66 | 0.731 |
| pKi 7.0 | 22,438 | 1,016 | 619 | **134** | 85 | 0.527 |
| pKi 8.0 | 22,358 | 1,016 | 649 | **125** | 67 | 0.297 |

## 7. The cross-slot sensitivity

Specified and digested **before** any eligibility figure was computed, so the
comparison cannot have been shaped by its outcome.

- **Primary (declared increment)**: the increment semantics as declared. A
  cross-slot candidate addition is a genuine addition *at its slot*;
  re-specifying that here would be changing the contract after the fact.
- **Sensitivity (cross-slot excluded)**: the primary increment minus every
  counted addition occurrence belonging to an **unambiguous** cross-slot
  candidate — **4,031 KI occurrences**, each named by its source locator in
  `data/asof/m11e/excluded-locators-ki.json`, so every exclusion is individually
  checkable against the archived bytes. Measurement-to-locator alignment is
  asserted, not assumed (§0, D3).
- **Ambiguous candidates are reported, never resolved.** 1,673 ambiguous groups,
  all containing candidates whose identifiers disagree; at most 1,099 KI
  occurrences could pair in them. That ceiling is **not** an observed count and is
  subtracted in **neither** arm.

### Effect, holding the consistency branch fixed

| Threshold | Screened primary: declared → excluded | Rankable ≥5: declared → excluded |
| --- | ---: | ---: |
| pKi 6.0 | 26,320 → 22,834 (−13.2%) | 142 → 117 |
| pKi 7.0 | 25,910 → 22,438 (−13.4%) | 152 → 134 |
| pKi 8.0 | 25,831 → 22,358 (−13.4%) | 134 → 125 |

The gap is **13%, stable across thresholds**, and costs 25 rankable targets at
pKi 6.0. Large enough that eligibility genuinely depends on an unresolved
question; small enough that no C1–C5 verdict turns on it.

**What the comparison cannot show** is which arm is correct. Value equality at
different slots establishes candidacy, not identity.

## 8. Minimum evaluation cohort — **accepted**

> **Accepted by the user** in the M11e closeout instruction, as **pragmatic
> feasibility floors for this exploratory study** — explicitly *not* a power
> calculation and *not* a guarantee of informative results. pKi 6.0 stays
> primary; both sensitivity axes are retained. This closes checklist item 8.

**Pragmatic feasibility criteria, not a power calculation.** No effect size,
variance model or target power underlies them. They are floors chosen so a cohort
clearing them is not obviously too small to macro-average over, and they would
have to be replaced by an actual power analysis before any claim about detectable
differences. **Accepted on those terms** (above).

Judged on the **screened primary branch of the cross-slot-excluded arm** — the
most conservative of the four cells, so a cohort is not called adequate on
evidence that either the screen or the unresolved candidate-correspondence
question could remove.

| | Requirement | Why | Measured (pKi 6.0 / 7.0 / 8.0) | Met |
| --- | --- | --- | --- | --- |
| **C1** | ≥ **50 rankable targets**, rankable = ≥5 **admitted** pairs of **both** classes | per-target ranking statistics are macro-averaged over targets; a single-class target cannot be ranked at all, and an average over very few targets is dominated by per-target noise. 50 is a pragmatic floor, not a derived one | **117 / 134 / 125** | **yes** |
| **C2** | ≥ **2,000 admitted pairs** | enough that per-target cohorts are not single-digit after the macro partition; set well below the measured count so it binds on a future, smaller snapshot pair rather than being tuned to this one | 22,834 / 22,438 / 22,358 | **yes** |
| **C3** | positive rate within **[0.20, 0.80]** | AUPRC is uninterpretable without its positive rate and ranking metrics degrade at extremes | 0.731 / 0.527 / 0.297 | **yes** |
| **C4** | all **four cells** reported side by side | a figure quoted without naming its increment arm and consistency branch is what produced D1 | — | **yes** |
| **C5** | feasibility **not** argued from the fraction of added observations | 33,953 added KI observations sounds ample and says nothing about rankability; in the judged cell **668 of 1,016 targets carry only one class** | — | **yes** |

**Verdict: all five met at every pre-registered threshold in the most conservative
cell**, so an as-of evaluation on this snapshot pair is not blocked by cohort
size — and the criteria are now accepted on the stated terms. The figures are
lower than v1 published — 117 rankable targets on 22,834
admitted pairs, not 118 on 22,939 — because those were pre-screen figures.

pKi 7.0 remains the best-conditioned threshold (134 rankable, 0.527 positive
rate). **The primary threshold stays 6.0**, because 6.0 was pre-registered and
moving it after seeing class balance is exactly the selection this project's
guardrails exist to prevent. The comparison is reported, not acted on.

**What this does not establish.** That the evaluation would be informative, or
that any particular effect would be detectable. Cohort size is necessary, not
sufficient, and these are feasibility floors rather than a power analysis.
Snapshot B is already-inspected data from which M8/M9 were produced, so a score
on it is exploratory whatever the cohort looks like.

## 9. Traceable examples

Six pairs, one per category, re-derived from their pair-shards with full locator
provenance in `reports/results/m11e_examples.json`. Each carries both arms' views
and its branch membership.

| Category | in_a / in_b / inc / rem | Slots | Increment | Full-B | Eligible | Unscreened | Screened primary |
| --- | --- | ---: | --- | --- | --- | --- | --- |
| Additions and removals | 4 / 4 / 4 / 4 | 3 | ok / active | ok | yes | ✓ | ✓ |
| **Screened out of primary** | 9 / 10 / 1 / 0 | **8** | ok / **inactive** | **exact_bound_conflict** | **yes** | ✓ | **✗** |
| Partial cross-slot exclusion | 2 / 3 / 2 / 1 | 4 | ok / inactive | ok | yes | ✓ | ✓ |
| Fully excluded in sensitivity | 1 / 1 / 1 / 1 | 2 | ok / active | ok | yes | ✓ | ✓ |
| Pair absent from A | 0 / 2 / 2 / 0 | 1 | ok / active | ok | **no — discordant** | ✗ | ✗ |
| Withheld correction | 4 / 1 / **0** / 4 | 4 | — (no increment) | ok / active | — | ✗ | ✗ |

Three rows carry the corrections of §0 on real data.

**The screened-out pair is the defect made concrete.** It is eligible, carries a
clean `ok / inactive` increment label, and is admitted to the unscreened
sensitivity branch — but its actual B evidence contains an exact outside its own
censored interval, so the consistency screen removes it from the primary branch.
The first revision would have counted it in the cohort's class balance and target
coverage. It also spans **8 slots**, which a slot-sharded eligibility figure would
have split.

**The withheld-correction pair entered no arm at all**, because its single
addition was withheld against a linked correction candidate. Its audit record is
retained anyway — that is the point of §0's D2 — and records the outcome the
endpoint rules return for empty increment evidence, flagged as an audit view
rather than a cohort membership.

**The absent-from-A pair separates label from standing**: a well-formed `active`
increment label and still not scoreable, because its two new exacts disagree by
more than a log.

## 10. Verification

| | |
| --- | --- |
| Input digests | verified before processing |
| Sensitivity spec | digest compared against the pre-run record — unchanged since declaration |
| KI classifications | equal to the M11d slot-sharded KI column exactly (585,978 / 33,953 / 28,777) |
| Increment accounting | 33,941 + 12 + 0 = 33,953 ✓ |
| A unchanged by B | **true**, all 64 shards, on the **strengthened** digest (canonical A evidence, exact-spread statistics and discordance verdict, not just status/label/count) |
| **Audit reconstructs the summary** | **yes** — pairs with an increment, exclusions by reason, admitted pairs, label counts, target counts, rankability at 5, screened-out counts and screen reasons, rebuilt from the exported audit alone for **both** arms |
| Pair integrity | one pair's rows land in one shard, asserted at five shard counts |
| Budget | enforced from a counted scan **before** either shard is materialised, with bounded reads; tested against a counter patched to lie |
| Locator alignment | asserted, raising `AlignmentError` rather than truncating |
| Peak RSS | 0.51 GB on a 16 GiB machine; largest shard pair well inside the 400,000-observation budget |
| Audit readings | `training_from_a` and `full_b_audit` computed from `in_a` / `in_b` directly; verified invariant under two different exclusion sets |
| Absence reasons | named, not blank: 17,457 pairs with no additions, 11 fully withheld, 3,627 fully excluded by the sensitivity |
| Eight worked examples | all reviewed; every decision matched; one stale name found, in the document |
| Accepted corpora and artifacts | unchanged |
| **Full suite** | **1,069 tests, 0 failures, 0 errors, 0 skipped, 277s**. M11e contributes 89: 23 aggregation, 43 record, 23 branch accounting. Ruff clean across 160 files. |

The audit file holds **44,966 records** (62 MB) — every pair with a decision worth
explaining: an increment in either arm, an increment entirely withheld, one
entirely removed by the sensitivity, or explicit removals. Pairs unchanged between
the snapshots carry no decision and are not emitted; 500k such records would bury
the ones that do.

## 11. The eight worked examples of §7a, closed

Each example from `docs/M11.md` §7a was run as a synthetic fixture through the
real aggregation path, and the corpus audit was searched for a pair showing the
same pattern. Record: `data/asof/m11e/worked-example-review.json`.

**All seven searchable patterns are observed in the real 202601/202609 pair.**
Example (a) is the unchanged case, which by construction emits no audit record —
an unchanged pair has no addition, no removal and no withholding, so there is no
decision to explain. It is nonetheless the dominant pattern: 585,978 of 614,755
KI observations in A are unchanged in B. Nothing here is marked "not observed",
and no real example was manufactured.

| § | Pattern | Real pairs | Training (from A) | Increment | Actual B | Eligible | Branches |
| --- | --- | ---: | --- | --- | --- | --- | --- |
| (a) | Unchanged measurement | *no audit record* | included, A's label | none | ok | n/a | neither |
| (b) | A later correction | **12** | ok / **active**, A's 12 nM kept | **withheld** | ok / active | n/a | neither |
| (c) | A withdrawal | **17,468** | ok / active | none | **no_usable_evidence** | n/a | neither |
| (d) | Repeated identical rows | **22** | ok / active, n=2 | active | ok | yes | both, **recurrent** |
| (e) | Clean in A, contradictory in B | **7** | ok / **active** | **inactive** | **exact_bound_conflict** | yes | **unscreened only** |
| (f) | Opposite decisive bounds | **2** | ok / active | **inactive** | **empty_intersection** | yes | **unscreened only** |
| (g) | Withdrawal makes B cleaner | **2** | ok / **inactive** | **active** | ok / active | yes | both |
| (h) | Discordant replicates | **522** | — (no A evidence) | active | ok / active | **no — discordant** | **neither** |

Every decision matches what §7a specifies. The four that matter most:

- **(b)** training keeps A's 12 nM while the increment is withheld — "an analyst at
  `T_train` had 12 nM". The closeout fix is what makes this visible: before it, the
  withheld increment erased the A reading entirely.
- **(e)** and **(f)** are eligible, carry a decisive `inactive` increment label, and
  are admitted to the unscreened branch only. The screen drops them from primary
  because actual B contradicts itself — `exact_bound_conflict` for (e), where A's
  exact lies outside the new bound, and `empty_intersection` for (f), where the two
  bounds cannot both hold. The implementation distinguishes them, which §7a
  required.
- **(g)** keeps A's `inactive` label from a bound that B has since retracted, while
  the increment reads `active`. History is not edited by the future.
- **(h)** is excluded in **both** branches: discordance is not a consistency-screen
  matter, so lifting the screen does not admit it. §7a says exactly this.

### One discrepancy found, and it was in the document

§7a called the exact-versus-bound contradiction `unresolved_heterogeneity` in four
places. That is the project plan's §1 word for the `pair_label` status; M4
implemented it as **`exact_bound_conflict`**, and `unresolved_heterogeneity` is not
a status any code produces. The behaviour was never in question — the pair is
screened either way — but the document now uses the implemented name throughout.
This was the **only** mismatch across the eight examples.

## 12. The next evaluation contract — specified, not executed

`configs/m11f_evaluation_contract.json` (`0317a421…`). Nothing is fitted, no
prediction metric is computed, and the freeze stays unsigned.

| | Requirement |
| --- | --- |
| **Validation** | reserved **within A**, carved by pair before any fitting, membership recorded by digest; no reserved pair may appear in the fitted training set, asserted |
| **Training evidence** | derived from **A alone**. Already implemented and measured: `training_only_labels` takes one snapshot, and the strengthened per-shard digest — canonical A evidence, exact-spread statistics, discordance verdict — matches the two-snapshot run across all 64 shards |
| **`train_only` visibility** | declared for **every** fitted artifact: ECFP4, ESM-2, activity-derived features, retrieval index, checkpoints, and any fitted scaler or calibrator. A build that cannot prove it fails |
| **Strata** | `recurrent` / `new_pair` defined against the **actual fitted training set**, not historical presence in A. These differ: a pair present in A can still be absent from the fitted set by falling in reserved validation or being dropped by a scope rule. The difference from the historical figures is reported, not assumed to be zero |
| **Reporting** | **new-pair results are the headline**; recurrent reported separately and never pooled, because a recurrent pair's prediction can draw on evidence for that very pair in training |
| **Cohorts** | all four increment-arm × consistency-branch cells carried forward, every number naming its cell |
| **Before execution** | feature coverage over the cohort reported as a precondition; model selection rule recorded (B0–B4 and the frozen M9 dual encoder, selected on reserved validation only); every reported number bound to the digests of the artifacts that produced it |

Two known gaps constrain it: retrieval index construction has no production caller
(item 11d) and there is no partition-filtered evaluation-mode evidence API (item
11e). Under this contract retrieval stays unbuilt and evaluation-mode evidence
stays switched **off** rather than filtered.

## 13. What remains unavailable

- **A post-freeze snapshot.** The newest deposit predates the design document;
  checklist item 7 is a wait, not a task, and no amount of work here closes it.
- **Three production paths the as-of design assumes.** The train/validation gate
  is verified only where a caller exists (11c); retrieval index construction has
  no production caller, so its guard protects nothing (11d); and there is no
  partition-filtered evaluation-mode evidence API (11e). The M11f contract works
  around all three — retrieval unbuilt, evaluation-mode evidence switched off —
  and working around a gap is not closing it.
- **Re-pointed leakage assertions** (item 14). The five existing M5/M7 assertions
  are still written train-vs-test and have not been re-pointed at the three-way
  as-of partition.
- **Whether Ki may be pooled across assay contexts** (M5). Every count here
  assumes the current pooling rule and would change if it were narrowed.
- **Which cross-slot increment arm is correct.** Candidacy is not identity, and
  the 1,673 ambiguous groups stay unresolved.
- **Recurrence against a fitted training set.** Split construction has not run, so
  only historical presence in A is reported. The M11f contract requires the
  recomputation and forbids assuming the difference is zero.
- **Any prediction metric.** Nothing was fitted or scored, by scope.

## 14. Next step

The M11f contract (§12) is **specified and not executed**. Executing it means, in
order: carve and digest the reserved validation split within A; report feature
coverage over the cohort as a precondition; record the model selection rule;
recompute the `new_pair` / `recurrent` strata against the actual fitted training
set and report how they differ from the historical-presence figures; then fit,
with `train_only` visibility asserted on every artifact.

**That step is where fitting begins**, and it is deliberately not taken here.
Signing the §7(A) attestation remains out of reach for a reason no work on our
side can change: it needs a deposit cut after the freeze, and none exists.
