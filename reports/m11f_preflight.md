# M11f preflight — the as-of evaluation contract, made executable

**Superseded by `reports/m11g_preflight.md`.** The contract is now v4 and
the datasets here were re-emitted by the production runner; the figures in §5
below predate the feature extension and the strict precondition gates.

**Scope: preparation only.** Nothing was fitted, no prediction metric was
computed, nothing was downloaded or docked, and the confirmatory freeze is **not
signed**. The study remains **exploratory** and the **provisional-pooling**
qualification stands.

Contract: `configs/m11f_evaluation_contract.json` (`dee1822e…`), superseding the
v1 draft. Records: `data/asof/m11f/`.

## 0. Correction pass — what changed and what was rerun

This is **v2** of the preflight. Record: `m11f-correction-v1`; contract now v3.

### E1 — classification eligibility was used as fitting eligibility

The partition counted **decisive censored-only pairs** as supplied to fitting,
while the contract declares exact-only pKi regression with MSE. A `> 10000 nM`
record puts the whole admissible range below pKi 6.0, so it is a real class
label — and there is no exact value to regress on. Those pairs could not have
been fitted at all.

Three eligibilities are now kept apart:

| | Rule | Train | Validation |
| --- | --- | ---: | ---: |
| **Classification** | A-only status `ok`, label in {active, inactive} | 401,818 | 71,224 |
| **Regression** | median of the pair's **exact** pKi values — the established `pair_regression` rule | **353,957** | 62,703 |
| **Validation RMSE** | regression-eligible **and** status `ok` **and** not discordant | — | **60,981** |
| *of which* decisive censored-only | class label, no regression target | 50,007 | 8,901 |

**`pairs_supplied_to_model_fitting` falls from 401,818 to 353,957** — the old
figure overcounted by **47,861**. A censoring boundary is never substituted for a
point: `exact_stats` ignores non-exact measurements, so the substitution is
structurally impossible rather than merely avoided. Censored evidence is retained
for ranking eligibility and audit.

**Validation-RMSE eligibility is not reused from training eligibility.**
Checkpoint selection is an evaluation, so it uses the endpoint's own rule — which
excludes discordant pairs and non-`ok` statuses. 1,722 validation pairs have a
regression target and still may not select a checkpoint.

**Discordance, stated per use:** discordant pairs **remain available for
training** (M4's declared treatment, §7a (h)) and are **excluded from checkpoint
selection**, because replicate exacts four logs apart cannot arbitrate which
checkpoint is better.

### E2 — the partition configuration was accepted and ignored

`build_partition` took `fraction` and `seed` and then let `iter_a_pairs` call
`pair_partition` with the module defaults. A caller asking for a different split
got the default one: honoured in the record, ignored in the assignment. The
configuration is now threaded through every assignment and validated
(`fraction` in [0, 1], non-empty string seed; `fraction=0` → all train,
`fraction=1` → all validation, both asserted).

**Was the existing default-config membership unchanged? Verified, not assumed.**
All 482,196 pairs compared one by one: **0 changed partition.** The membership
*digest* did change — because the recorded fields changed, two eligibility flags
per pair instead of one — and both facts are recorded rather than one standing in
for the other.

### E3 — recurrence keyed on the wrong set

Recurrence now keys on the exact pair set the **model-facing training loader
emits**. Effects: `recurrent` 5,797 → **4,170**; A-present-but-not-fitted 1,107
→ **2,734 (39.6%, up from 16.0%)**.

### E4 — feature coverage was blended and checked against the wrong width

Coverage was one figure over the whole cohort, and the check compared ECFP4's
**logical** 2,048-bit width against the **stored** array — which is bit-packed
into 256 uint8 bytes, so every vector came back "unusable". Coverage is now
per role, against the stored width, with `missing` and `unusable` counted apart
and non-finite values rejected only where the dtype can hold one (a packed uint8
fingerprint cannot, so asserting finiteness there would be a vacuous check
dressed as a real one).

### Rerun

The A partition and membership, the feature coverage and binding, the cohort and
strata, and the model-facing datasets. **Not rerun:** the matcher, the exports
(digests re-verified), the pair shards, the M11e aggregation, the sensitivity
spec.

## 1. What is pinned

| | |
| --- | --- |
| Primary cell | **`declared_increment` × `screened_primary`** |
| Sensitivities | the other three cells, retained; every number names its cell |
| Threshold | pKi **6.0** primary; 7.0 and 8.0 retained |
| Validation fraction | **0.15** declared, **0.150532** realised |
| Pair assignment | `blake2b-64` of (seed, inchikey, sequence_sha256), uniform in [0,1), validation below the fraction |
| Seed | **`m11f-partition-v1`** |
| Training eligibility | A-only status `ok` **and** label in {active, inactive}; discordant pairs **stay eligible** |
| Model | dual encoder, frozen encoders, affine-on-cosine head in pKi units |
| Projection dim | **512**, carried from M9's validation selection (it chose 512 on all four splits). **No sweep.** |
| Optimiser | adam, lr 0.001, batch 512, max 20 epochs, early stopping patience 5 |
| Run seeds | **20260930–20260934** (five, as M8/M9) |
| Checkpoint selection | `validation_rmse`, minimise, evaluated once per epoch, restore best-validation |
| Metric version | **`m8/v2`** |
| Feature bindings | `ecfp4-compound-42351e003acb`, `esm2-target-48cfa09487ce`, both with manifest and storage digests |

Nine input digests are bound into the contract: both exports, the cross-slot
exclusion set, the sensitivity spec, the partition summary, the A membership, the
feature coverage, the cohort preflight, and the M9 config.

**Why not `random` or `hash()`.** `random` is stateful, so the assignment would
depend on call order; `hash()` is per-process randomised, so the partition would
not be reproducible from `(fraction, seed)` alone. A keyed cryptographic hash is
reproducible from the recorded configuration and nothing else.

## 2. The feature-visibility contract, corrected

The v1 draft declared **all** features `train_only`. That was wrong.

A fixed ECFP4 fingerprint and a frozen ESM-2 embedding are **deterministic
functions of a structure or a sequence**. Computing one for an evaluation entity
reveals nothing about that entity's activity label, because no label is read while
computing it. Withholding them would not have prevented any leak; it would have
made the evaluation impossible to run on the pairs it is supposed to score.

| May be computed for training, validation **and** evaluation entities | Must be fitted on **training evidence only** |
| --- | --- |
| ECFP4 — radius 2, 2048 bits, **chirality ON** | feature scalers, centring and whitening |
| ESM-2 650M — revision `08e4846e…`, mean-pooled over residues, `length_policy: full` | activity-derived features |
| | calibrators |
| | model parameters |

Chirality stays on for the reason the module records: the curator's
fragment-parent step can leave two stereoisomers differing only in configuration,
and an achiral fingerprint would hand the model byte-identical vectors for them.
The protein representation stays pinned to its commit, not to "latest".

**Validation may select checkpoints** under the rule above, and nothing else.
**Retrieval stays unbuilt** (item 11d — the guard has no production caller) and
**evaluation-mode evidence display stays disabled** (item 11e — there is no
partition-filtered evidence API, so evaluation mode shows no evidence at all).

### Measured coverage, per entity role

Checked per role, because the three are reached by different paths and one
blended figure would hide a gap in any of them. `missing` and `unusable` are
counted apart: the first needs a cache extension, the second needs the cache
fixed.

| Role | Compounds | ECFP4 usable | missing | unusable | Targets | ESM-2 usable | missing | unusable |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `a_train` | 227,550 | 224,608 | 2,942 | 0 | 3,663 | 3,650 | 13 | 0 |
| `a_validation` | 62,217 | 61,342 | 875 | 0 | 2,533 | 2,526 | 7 | 0 |
| `evaluation` | 17,373 | 17,373 | 0 | 0 | 1,060 | 1,060 | 0 | 0 |

**The evaluation role is fully covered** — 17,373 compounds and 1,060 targets, all
usable — so the headline cohort's feasibility is unaffected.

**An A-role precondition is NOT met.** The accepted caches were built on the
202609 corpus and are keyed by that schema's surrogate ids. **3,817 A-role
compounds and 20 A-role targets have no entry**: they exist only in 202601.
Fitting is blocked until an extension exists; the evaluation cohort is not.

**An extension cannot inherit the accepted caches' identity.** The feature store's
own contract says a spec "does not say which entities went in, so it cannot
identify a cache on its own" — so a larger population is a different artifact
however unchanged the recipe. Any extension is registered under **its own**
manifest and storage digests, the accepted caches are left untouched, and the
experiment binds the artifacts actually loaded. **The extension is not built
here**: building it is execution.

Two checks worth naming. ECFP4 is stored **bit-packed** — 2,048 bits in 256 uint8
bytes — and an earlier revision compared the logical width against the stored
array, calling all 303,323 vectors unusable. And finiteness is asserted only for
the float32 ESM-2 cache: a packed uint8 fingerprint cannot hold a NaN, so
checking it there would be a vacuous check dressed as a real one.

107 targets exceed ESM-2's 1,022-residue pre-training window and are **flagged,
not excluded** — the length policy is `full` with no truncation, which M7 records
as a provisional choice whose sensitivity report is the check on it.

## 3. The partition within A

Built by a function that reads the **A shards and nothing else**. Not an argument:
the test patches `Path.open` to raise on any path under `shards-b`, so a read of B
would fail rather than quietly succeed.

| | Pairs | Observations | Classification | **Regression** | Validation RMSE |
| --- | ---: | ---: | ---: | ---: | ---: |
| `train` | 409,610 | 522,165 | 401,818 | **353,957** | — |
| `validation` | 72,586 | 92,589 | 71,224 | 62,703 | **60,981** |

**353,957 pairs are what model fitting receives** — train-partition pairs with
an exact-only regression target. Whole pairs stay together by
construction — the assignment key *is* the pair, so no grouping step can separate
a pair's observations.

The model-facing loaders emit exactly these sets, with digests:
`train` **353,957** pairs (`dfe59a02…`, 55,653 excluded for no regression target),
`validation` **60,981** pairs (`f2e9febd…`, 9,883 no target plus 1,722 not
RMSE-eligible).

Digests recorded per partition: **membership** (the split itself, so a later run
can prove it reproduced it) and **evidence support** (the canonical A evidence of
its eligible pairs, so a changed value is detected even when the label is
unchanged). A test confirms the latter: altering one pair's value while keeping
its `active` label moves exactly one partition's evidence digest and neither
membership digest.

Discordant pairs **remain available for training** and are **excluded from
checkpoint selection**. M4 leaves them available and denies them only scoring
standing (§7a example (h)); selection is an evaluation, so it applies the
endpoint's own rule. The two treatments are recorded separately rather than one
being inferred from the other.

## 4. Cohorts and strata — recurrence against the fitted set

**Recurrence is defined against the 401,818 pairs supplied to fitting, not against
historical presence in A.** The contract forbids assuming the difference is zero,
so it is measured:

| Declared arm, eligible cohort pairs | Count |
| --- | ---: |
| Present in snapshot A | 6,904 |
| Supplied to model fitting | **4,170** |
| **A-present but NOT fitted** | **2,734 (39.6% of A-present)** |

Those 2,734 would have been called "recurrent" by historical presence and are
not. The figure rose from 1,107 (16.0%) once recurrence keyed on the
regression-eligible set rather than on a class label.

### Strata, declared arm, eligible pairs (sums to the 26,421 pool)

| Stratum | Pairs |
| --- | ---: |
| `recurrent` — in the set the training loader emits | 4,170 |
| `new_absent_from_a` — no KI evidence in A at all | 19,517 |
| `new_reserved_for_validation` | 1,030 |
| `new_a_present_excluded_from_fitting` | 1,704 |

**The validation subgroup is not untouched by model selection.** Its A evidence is
what the checkpoint-selection rule reads, so the selected checkpoint is a function
of it. Those pairs are new to *fitting* and exposed to *selection*, and a result
on them is weaker evidence than a result on a pair absent from A entirely. They
are reported as their own subgroup for exactly that reason.

### All four cells × all four strata

| Cell | Stratum | Pairs | Targets | ≥5 each | Positive rate |
| --- | --- | ---: | ---: | ---: | ---: |
| cross-slot excl. / screened primary | recurrent | 2,168 | 335 | 10 | 0.729 |
| cross-slot excl. / screened primary | new absent from A | 19,517 | 941 | 98 | 0.729 |
| cross-slot excl. / screened primary | new reserved for validation | 522 | 163 | 5 | 0.739 |
| cross-slot excl. / screened primary | new A-present not fitted | 627 | 61 | 2 | 0.812 |
| cross-slot excl. / unscreened | recurrent | 2,237 | 340 | 11 | 0.723 |
| cross-slot excl. / unscreened | new absent from A | 19,517 | 941 | 98 | 0.729 |
| cross-slot excl. / unscreened | new reserved for validation | 537 | 165 | 5 | 0.732 |
| cross-slot excl. / unscreened | new A-present not fitted | 648 | 65 | 2 | 0.810 |
| declared / **screened primary** | recurrent | 4,099 | 366 | 29 | 0.780 |
| declared / **screened primary** | new absent from A | 19,517 | 941 | 98 | 0.729 |
| declared / **screened primary** | new reserved for validation | 1,018 | 217 | 6 | 0.661 |
| declared / **screened primary** | new A-present not fitted | 1,686 | 106 | 3 | 0.359 |
| declared / unscreened | recurrent | 4,170 | 370 | 31 | 0.775 |
| declared / unscreened | new absent from A | 19,517 | 941 | 98 | 0.729 |
| declared / unscreened | new reserved for validation | 1,030 | 218 | 6 | 0.658 |
| declared / unscreened | new A-present not fitted | 1,704 | 109 | 3 | 0.364 |

The `new_absent_from_a` row is identical across all four cells, which is expected:
a pair with no A evidence has no cross-slot candidate to exclude and no A exact to
contradict, so neither axis can move it.

**The `new_a_present_excluded_from_fitting` stratum grew from 17 pairs to 1,686**
once eligibility was corrected — it now holds the A-present pairs whose evidence
is decisive but censored-only. Over 106 targets it clears ≥5 per class on **3**,
far below the accepted floor of 50, so it is reported with its own figures rather
than described as unrankable or folded into the headline.

## 5. Feasibility after partitioning and feature exclusion

The headline is new-pair results, so the headline cohort is the **primary cell,
new to fitting** (all three subgroups).

| | Headline cohort | Strictest subgroup (absent from A) |
| --- | ---: | ---: |
| Pairs | 22,221 | 19,517 |
| Targets | 992 | 941 |
| Single-class targets | 671 | 640 |
| **Rankable ≥5 each** | **123** | **98** |
| Rankable ≥10 each | 72 | 59 |
| Positive rate | 0.697 | 0.729 |

| | Requirement | Headline | Strictest | Met |
| --- | --- | ---: | ---: | --- |
| **C1** | ≥50 rankable targets | **123** | **98** | **yes** |
| **C2** | ≥2,000 pairs | 22,221 | 19,517 | **yes** |
| **C3** | positive rate in [0.20, 0.80] | 0.697 | 0.729 | **yes** |
| **C4** | all four cells reported | — | — | **yes** |
| **C5** | not argued from the observation fraction | — | — | **yes** |

These are **planned fitting inputs**: the pairs the loaders would emit, not pairs
any model has seen.

**All five hold on the intended headline cohort, and on the strictest subgroup
too. Nothing was changed to obtain that.** The threshold stayed 6.0, the cohort
definition stayed as the contract declares it, and the floors stayed where they
were accepted.

Restricting to new-to-fitting costs **19 rankable targets**
against the full primary cell (142 → 123), and the strictest subgroup costs
25 more (→ 98). Both remain above the floor, and both are
reported rather than one being quoted as *the* number. The headline grew from v1's
20,552 pairs and 114 rankable targets because more pairs are new-to-fitting once
recurrence keys on the regression-eligible set — **the floors were not moved.**

## 6. Worked examples versus population counts

`reports/results/m11e_worked_example_review.json` is included so its predicates,
counts and locators can be inspected independently. It holds, per §7a example: the
expected decisions transcribed from the design document, the implementation's
result on a synthetic fixture, and — where the pattern occurs — one real pair with
its locators.

**These are two different kinds of claim and must not be read as one.** The
review's `matching_pairs_in_audit` figures count how many pairs in the corpus
match each pattern's **predicate**; they are population counts. The `real_example`
block beside them is **one pair**, shown so the predicate can be checked by hand
against the archived bytes. A single traceable example is evidence that the
pattern occurs and that the implementation handles it; it is not evidence about
the population, and the cohort figures in §4 and §5 are measured separately rather
than extrapolated from any example.

Example (a) is absent from the audit **by construction** — an unchanged pair has
no addition, no removal and no withholding, so there is no decision to explain —
while being the dominant pattern in the corpus. That is also not a population
count; it is a statement about what the audit emits.

## 7. Verification

| | |
| --- | --- |
| B unavailable on the A path | a test patches `Path.open` to raise on any `shards-b` path; the build succeeds and never touches B |
| A's eligibility invariant to B | identical membership **and** evidence digests with B present and absent on disk |
| Evidence digest sensitivity | altering one value while keeping its label moves exactly one partition's evidence digest |
| Whole pairs together | asserted across varying pH, temperature, publication and value |
| Realised fraction | 0.150532 against 0.15 declared |
| Leakage gate on the production loader | `select_for_fitting` exercised on a **three-way** partition: only `train` is fittable; a reserved id reaching a fitted artifact raises |
| Feature coverage | per role, before any metric exists; evaluation role 100%, A roles short by 3,817 compounds and 20 targets |
| Execution path | real loaders with **mocked fits**: only A-train reaches the transform and the model, A-validation selects only, B has no parameter to arrive through, and a bad digest/artifact/config refuses before any fit |
| Config threading | `fraction=0` → all train, `fraction=1` → all validation, a changed seed moves membership, recorded parameters re-derive the emitted split |
| Accepted artifacts | unchanged |
| Full suite | **1,139 tests, 0 failures, 0 errors, 0 skipped, 242s**. M11f contributes 78: 35 partition, 24 record, 19 datasets. Ruff clean across 166 files. |

## 8. What remains unresolved

- **No post-freeze snapshot** (item 7). This contract cannot be confirmatory; it
  is a wait, not a task.
- **Retrieval (11d) and the evaluation-mode evidence API (11e)** are unimplemented.
  The contract works around both — retrieval unbuilt, evidence display off — and
  working around a gap is not closing it.
- **Item 14.** The five existing M5/M7 leakage assertions are DB-backed and written
  for the M5 split tables. The as-of three-way partition is file-based and has its
  own assertions rather than a re-pointing of those; the DB-backed five are
  untouched.
- **M5's pooling question.** The endpoint's comparability across assay contexts is
  assumed, not established.
- **Which cross-slot increment arm is correct.** Candidacy is not identity.
- **A feature-cache extension for the A roles** (precondition P1). 3,817 compounds and 20
  targets have no vector in the accepted caches; fitting cannot start until an extension
  is registered under its own identity.
- **Every prediction metric.** Nothing was fitted or scored, by scope.

## 9. Next step

Execution: fit under this contract, with `train_only` asserted on every fitted
artifact, and report new-pair results as the headline with recurrent separate and
all four cells carried. **That step is where fitting begins** and is deliberately
not taken here.
