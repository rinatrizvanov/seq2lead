# M6 — leakage-controlled splits

Generated 2026-09-29 16:57 UTC.

> **Endpoint status: `provisional_pooled_for_exploratory_benchmark`**
>
> Ki is pooled across assays **provisionally, for exploratory benchmarking only**. The pre-declared decision rule returned `pool`, but it compared medians and was blind to a tail in which a quarter of assay-spanning pairs differ by more than tenfold. Pooling is therefore an operating assumption carried forward under protest, not a validated conclusion. Any result computed on pooled Ki inherits this limitation and must state it.
>
> See [`assay_variance.md`](assay_variance.md) for the evidence and its limits.

Built to the contract in [`../docs/SPLITS.md`](../docs/SPLITS.md), which was written before this code. Every split is immutable and versioned; a changed rule mints a new name rather than editing one.

## Unit of assignment

Four splits assign **whole compound-target pairs**. A pair is the smallest scored object, so splitting one across partitions would place a measurement of a test pair into training — a leak no later filter can undo.

`temporal_proxy` assigns **activities**, then aggregates inside each partition. Aggregating first would leak: a pair's median would already summarise measurements dated after the cut.

## Splits built

| Split | Type | Protocol | Train | Validation | Test | Excluded | Groups held together |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `random_pair-v3` | random_pair | `trainable` | 328,251 | 46,893 | 93,786 | 18,632 | — |
| `cold_protein-v3` | cold_protein | `trainable` | 328,251 | 46,893 | 93,786 | 18,632 | 5,425 sequence clusters |
| `chemistry_disjoint-v3` | chemistry_disjoint | `trainable` | 328,251 | 46,893 | 93,786 | 18,632 | 96,230 scaffolds |
| `label_reversal-v3` | label_reversal | `diagnostic_frozen` | 467,157 | 0 | 1,773 | 18,632 | 1,000 reversed compounds |
| `temporal_proxy-v4` | temporal_proxy | `trainable` | 360,404 | 41,638 | 96,664 | 5,745 | cut 2020-01-01 |

**`protocol`** separates what a split is for. `trainable` splits may be used to fit and select models. `diagnostic_frozen` splits may not: see the `label_reversal` section below.

`excluded` carries the pairs the governing endpoint marked `excluded_from_eval` — contradictory, empty-intersection, no-usable-evidence, discordant and ambiguous. They are kept out of validation and test in every split, and out of training too by default, so a first benchmark is not trained on evidence already declared unreliable. For `temporal_proxy` the governing endpoint is the partition's own, which is why its count differs.

## Grouping that makes the cold splits cold

| Grouping | Members | Groups | Mean size |
| --- | --- | --- | --- |
| MMseqs2 sequence clusters (40% identity) | 11,013 | 5,425 | 2.03 |
| Bemis-Murcko scaffolds | 252,130 | 96,230 | 2.62 |

**11,013 distinct sequences collapse to 5,425 clusters.** Nearly half of what the curated layer calls separate targets are homologs of something else in it. Holding out individual proteins while their homologs stayed in training would not have been a cold-protein test, which is the whole reason for clustering first.

A build-failing assertion confirms **every scored target and every scored compound carries a cluster assignment**. An unclustered entity would otherwise be silently exempt from the very disjointness the split claims: it belongs to no group, so no group-overlap check can catch it.

## `temporal_proxy`

### Each partition aggregates only its own measurements

A temporal split that assigns dates but then joins a **global** aggregate has not held anything out. The global `pair_label` median and interval intersection summarise every measurement of a pair, including ones dated after the cut, so a test pair's ground truth would already contain its own future. This build therefore materialises a **separate endpoint per partition**, each built from that partition's assigned activities alone under the identical exact/censored conflict rules.

| Partition | Endpoint built for it | Labelled pairs | Regression aggregates | Evidence it may read |
| --- | --- | --- | --- | --- |
| `train` | `temporal_proxy-v4--train` | 360,404 | 314,170 | activities dated before the validation cut |
| `validation` | `temporal_proxy-v4--validation` | 42,946 | 36,779 | activities in the validation window only |
| `test` | `temporal_proxy-v4--test` | 101,255 | 84,762 | activities in the test window only |

Every aggregate's support activity IDs are recorded, and a build-failing assertion confirms **no aggregate is supported by an activity assigned to a different partition**. That is the check the previous build had no way to make, because it built no aggregates.

### How much the global endpoint was actually deciding

Comparing each scored pair's partition-scoped ground truth against the global one it would previously have been graded by:

| Held-out pairs where … | Count | Share of the 138,302 scored |
| --- | --- | --- |
| the partition and global labels **disagree** | 624 | 0.5% |
| &nbsp;&nbsp;· period says `active`, global says `none` | 281 | |
| &nbsp;&nbsp;· period says `inactive`, global says `none` | 157 | |
| &nbsp;&nbsp;· period says `inactive`, global says `active` | 115 | |
| &nbsp;&nbsp;· period says `active`, global says `inactive` | 71 | |

**2,072 pairs are scored here that the global endpoint excludes**, by reason:

| Global exclusion reason | Pairs reinstated | Why it does not apply |
| --- | --- | --- |
| `discordant` | 1,634 | the spread appears only once later measurements are pooled in |
| `exact_bound_conflict` | 386 | the contradicting exact value is dated after the cut |
| `empty_intersection` | 52 | the censored bounds only conflict once later records join |

Each of these exclusions is a judgement formed from evidence that did not exist at the time the partition represents. Letting it remove a pair from the test set would be a future measurement controlling the benchmark's composition, so eligibility is decided by the partition's own endpoint. The converse count is **0**: no pair is excluded by its own period yet clean globally, which is the expected direction — a partition endpoint reads a subset of the measurements, so restricting evidence can resolve a conflict but never invent one.

This is also why `temporal_proxy` reports fewer excluded pairs than the pair-level splits: it is not a laxer rule, it is the same rule applied to less evidence.

### New and recurrent, relative to what the model actually saw

**Protocol: `train_only`.** The model scored on a given evaluation period is fitted on the `train` partition alone. Validation selects between models and stops training; it is not folded back in before the test evaluation.

That choice decides the stratum definition, so it is recorded on the split rather than left implicit. A pair is **recurrent** for a period if the model scoring it *had already been shown a measurement of it*, and **new** otherwise:

| Evaluation period | Fitted on | Recurrent means |
| --- | --- | --- |
| `validation` | `train` | measured in `train` |
| `test` | `train` | measured in `train` |

The earlier definition judged the test period against `train` + `validation`. Under a train-only protocol that overstates what the model knows: a pair first measured in the validation window is genuinely new to a model that never trained on validation. Counting it as recurrent credits the model with evidence it was never shown, and moves pairs out of the prospective stratum that belong in it.

Earlier measurements are not discarded — they are reported as a **separate axis**, so a pair that was measured before the test window but never trained on is visible as exactly that:

| Period | Stratum | Measured earlier in | Pairs | Reading |
| --- | --- | --- | --- | --- |
| `test` | `new` | (never) | 86,868 | never measured before — prospective |
| `test` | `new` | validation | 3,431 | **measured before the test window, but invisible to a train-only model** |
| `test` | `recurrent` | train | 3,871 | the model was trained on this pair |
| `test` | `recurrent` | train,validation | 2,494 | trained on, and measured again since |
| `validation` | `new` | (never) | 35,087 | never measured before — prospective |
| `validation` | `recurrent` | train | 6,551 | the model was trained on this pair |

The `test` / `new` / `validation` row is the one the previous version mislabelled as recurrent. Those pairs are new to a train-only model and are scored in the headline stratum, with their prior measurement disclosed rather than hidden.

If the protocol ever changes to refit on `train` + `validation` before scoring test, that is a different experiment: it widens what `training_visible_activities()` returns, lets validation evidence into every retrieval index and feature, and makes those 3,431 pairs genuinely recurrent. It would mint a new split version, not edit this one.

| Period | `new` (headline) | `recurrent` | Recurrence rate |
| --- | --- | --- | --- |
| `validation` | 35,087 | 6,551 | 15.7% |
| `test` | 90,299 | 6,365 | 6.6% |

The definition is not academic. **3,431 pairs are measured in validation and test but never in training.** Three rules disagree about them, and the disagreement is the whole point:

| Rule | Validation | Test | What it assumes |
| --- | --- | --- | --- |
| *appears in >1 partition* | `recurrent` | `recurrent` | that a later measurement can make a pair familiar in an earlier period |
| *measured earlier in time* | `new` | `recurrent` | that the model saw everything the database had recorded by then |
| **what the model was fitted on** | `new` | `new` | only that the model was trained on `train` — which it was |

The first rule grades validation against the future. The second grades test against evidence a train-only model never received. This build uses the third, and records the earlier measurement separately so nothing is lost.

**Test counts are reported separately from train and validation and the strata are never pooled into one figure.** A combined number would let the recurrence rate — an artifact of how often BindingDB re-measures a pair — drive the score. `new` is the headline because it is the prospective question: can the model rank a pair nobody had measured when training stopped? `recurrent` is a much easier question and is reported beside it, never inside it.

Recurrent pairs are also why `temporal_proxy` is exempt from the pair-overlap assertion: by construction such a pair has measurements on both sides of the cut. The exemption is narrow — the *measurements* are still disjoint, and the partition-endpoint assertions above are what the exemption is traded for.

### Dates used

| Date used | Activities |
| --- | --- |
| bindingdb | 10,584 |
| publication | 609,347 |

A **proxy**, not an as-of snapshot. BindingDB's publication date is the article's date, not the date the record became retrievable, and records are revised and back-filled. The name says so wherever it appears.

## `label_reversal` is a diagnostic, not a benchmark

It has **no validation partition**, and that is structural rather than an oversight: every compound it holds out is chosen precisely because its label reverses, so there is no comparable held-out slice left to tune against. A split with no tuning set cannot honestly be used for model selection — selecting on it *is* fitting to it.

It is therefore marked `protocol = diagnostic_frozen`, which means:

1. It is **evaluated once, after every model choice is frozen** — architecture, hyperparameters, features, curation rules, the lot.
2. Its result **may not motivate a change** to any of those choices. If it does, the change mints a new `dataset_version` and the reversal number is re-earned on a split the new choices have not seen.
3. It is reported as a **ligand-bias measurement**, not as a leaderboard rank. TransformerCPI's finding is the calibration point: every reference model scored below 0.5 on its Kinase reversal set.

The alternative — treating it as a fifth trainable split — would invite exactly the tuning that makes a bias probe meaningless.

## Leakage assertions

Build-failing checks, not statistics to eyeball. A split that fails one is not written.

**`random_pair-v3`** (random_pair)

| Assertion | Observed | Expected | Result |
| --- | --- | --- | --- |
| pair appears in exactly one partition | 0 | 0 | pass |
| no pair excluded by the endpoint reaches validation or test | 0 | 0 | pass |
| every assignment names a known partition | 0 | 0 | pass |

**`cold_protein-v3`** (cold_protein)

| Assertion | Observed | Expected | Result |
| --- | --- | --- | --- |
| pair appears in exactly one partition | 0 | 0 | pass |
| no sequence cluster spans scored partitions | 0 | 0 | pass |
| every scored target has a cluster assignment | 0 | 0 | pass |
| no pair excluded by the endpoint reaches validation or test | 0 | 0 | pass |
| every assignment names a known partition | 0 | 0 | pass |

**`chemistry_disjoint-v3`** (chemistry_disjoint)

| Assertion | Observed | Expected | Result |
| --- | --- | --- | --- |
| pair appears in exactly one partition | 0 | 0 | pass |
| no scaffold spans scored partitions | 0 | 0 | pass |
| every scored compound has a scaffold assignment | 0 | 0 | pass |
| no pair excluded by the endpoint reaches validation or test | 0 | 0 | pass |
| every assignment names a known partition | 0 | 0 | pass |

**`label_reversal-v3`** (label_reversal)

| Assertion | Observed | Expected | Result |
| --- | --- | --- | --- |
| pair appears in exactly one partition | 0 | 0 | pass |
| diagnostic split declares it has no tuning set | 0 | 0 | pass |
| reversed compounds carry opposite labels across the cut | 0 | 0 | pass |
| no pair excluded by the endpoint reaches validation or test | 0 | 0 | pass |
| every assignment names a known partition | 0 | 0 | pass |

**`temporal_proxy-v4`** (temporal_proxy)

| Assertion | Observed | Expected | Result |
| --- | --- | --- | --- |
| activity assigned to exactly one partition | 0 | 0 | pass |
| every temporal partition has its own endpoint | 0 | 0 | pass |
| no aggregate is supported by an activity from another partition | 0 | 0 | pass |
| no regression aggregate borrows support across partitions | 0 | 0 | pass |
| assigned pairs come from the partition endpoint, not the global one | 0 | 0 | pass |
| recurrent pairs are labelled, not hidden | 0 | 0 | pass |
| no pair its own partition excluded reaches validation or test | 0 | 0 | pass |
| global endpoint exclusions do not gate the temporal training set | 0 | 0 | pass |
| every assignment names a known partition | 0 | 0 | pass |

### What the assertions caught

**First build.** `temporal_proxy` placed 7,939 endpoint-excluded pairs into validation and test, because it partitioned purely by date and never consulted `excluded_from_eval`. The assertion failed the build.

**Second build.** The generic exclusion assertion consulted the *global* endpoint for every split, and failed `temporal_proxy` on 2,072 pairs. That failure was the assertion's, not the builder's: the pairs are excluded globally only because of measurements dated after the cut. Reading a global exclusion into a temporal partition is itself the leak. The assertion was made partition-aware and the same 2,072 pairs are now correctly scored — see the table above.

Both corrections were to the *check* or the *builder*, never to the data. No split was edited to make an assertion pass.

## How unfamiliar are the held-out entities, really?

A zero-overlap assertion proves no *group* spans partitions. It does not prove the held-out entities are unfamiliar: two proteins below the clustering threshold can still share a binding site, and two compounds with different Bemis-Murcko scaffolds can still be fingerprint near-neighbours. Disjointness by construction is not the same as novelty, so it is measured rather than assumed.

**test target -> nearest train target (sequence identity)**  
n = 1,124, exhaustive

| p10 | median | p90 | p99 |
| --- | --- | --- | --- |
| 0.000 | 0.311 | 0.547 | 1.000 |

| Held-out entities with a training neighbour above … | Share |
| --- | --- |
| 0.3 | 52.9% |
| 0.5 | 11.7% |
| 0.9 | 3.9% |

**test compound -> nearest train compound (ECFP4 Tanimoto)**  
n = 2,000, sampled

| p10 | median | p90 | p99 |
| --- | --- | --- | --- |
| 0.358 | 0.586 | 0.778 | 0.983 |

| Held-out entities with a training neighbour above … | Share |
| --- | --- |
| 0.4 | 84.2% |
| 0.7 | 23.9% |
| 0.9 | 2.2% |

**These numbers qualify the cold splits rather than endorsing them.** 3.9% of held-out targets have a training target at 90%+ sequence identity despite sharing no MMseqs2 cluster — clustering at 40% identity with 80% coverage does not catch a pair that aligns strongly over a shorter region. On the chemistry side 23.9% of held-out compounds have a training neighbour above 0.7 Tanimoto, which is the scaffold limitation made quantitative: **`chemistry_disjoint` guarantees scaffold disjointness, not dissimilarity.** Butina clustering on ECFP4 would group by the similarity actually measured here, but it is O(n^2) in compounds and this endpoint carries a quarter of a million; the substitution is recorded as a limitation, not presented as equivalent.

The compound figures are a **lower bound**: the training side is sampled, so an unsampled training compound can only be nearer, never further. Sample sizes are printed above so the bound is auditable.

### Auditing the high-identity hits

A bare identity number cannot tell *the same protein under another accession* from *two proteins sharing one domain*. Every held-out target with a 90%-or-better local alignment into training was therefore re-examined with its alignment length and its coverage of **both** sequences.

| | Targets | Share of held-out |
| --- | --- | --- |
| Held-out targets | 1,124 | 100% |
| …with a ≥90% identity local hit into training | 44 | 3.9% |
| …where the alignment covers ≥50% of **both** | 20 | 1.78% |
| …where the alignment covers ≥80% of **both** | 0 | 0.00% |

**43 of the 44 are fragment containment**: one sequence is ≥95% covered by the alignment while the other is not. These are domain constructs, isolated subunits and short peptides sitting alongside full-length proteins — the median alignment is 161 aa. MMseqs2 was run at `--min-seq-id 0.4 -c 0.8`, so it *correctly* declines to cluster a 40-residue peptide with the 3,430-residue protein that contains it; that is the coverage rule working, not failing.

The highest mutual coverage anywhere in the set is **0.80**. No held-out target has a ≥90%-identity alignment spanning 80% of both itself and a training protein.

The worst cases, by how much of both proteins the alignment explains:

| Identity | Alignment | Held-out length (covered) | Training length (covered) |
| --- | --- | --- | --- |
| 1.000 | 322 aa | 405 aa (80%) | 322 aa (100%) |
| 1.000 | 596 aa | 596 aa (100%) | 770 aa (77%) |
| 0.959 | 322 aa | 432 aa (74%) | 322 aa (100%) |
| 0.986 | 886 aa | 1,194 aa (74%) | 906 aa (98%) |
| 1.000 | 322 aa | 435 aa (74%) | 322 aa (100%) |

**So the cold-protein claim is this, and not more than this:**

> No held-out target shares an MMseqs2 cluster with any training target at 40% identity and 80% coverage.

It is **not** a claim that no held-out target resembles a training protein. 20 held-out targets (1.8%) have a ≥90%-identity alignment covering at least half of both themselves and a training protein. For those, a mean-pooled sequence embedding will sit close to a training example, and any cold-protein score on them should be read as **near-homolog performance, not novel-target performance**. They are identifiable, so M7 can report them as their own stratum rather than letting them flatter the headline. The remaining 24 are short local matches where a shared motif does not make two proteins the same.

## What must not reach training

Three channels, each with a rule enforced in code rather than remembered:

1. **Evidence.** No held-out `activity` may be read when building a training aggregate, feature or target. Partition-scoped aggregation plus the assertions above. `training_visible_activities()` additionally drops any pair the *training period's own* endpoint marked excluded, so a pair judged unusable on its early evidence cannot re-enter as a training example. It deliberately does **not** consult the `excluded` partition: a pair lands there when the validation or test endpoint finds a conflict among measurements dated after the cut, and suppressing a clean training record on that basis would be a future measurement deciding what the model may learn from the past — the same leak running backwards. Removing that condition restored 386 training activities across 228 pairs, every one of them with clean training-period evidence.
2. **Retrieval.** `assert_index_is_training_only()` refuses an index containing any held-out activity. `training_visible_activities()` returns SQL rather than rows, so a caller cannot quietly widen it.
3. **Assay-spread features.** Per the M5 contract, a pair's *model-facing* spread is the spread of its **training** observations, even where more exist. Audit-facing spread may use everything but is written to reports only and never joined into a feature, filter or sample weight.

## Versioning and supersession

| Superseded | Replaced by | Why |
| --- | --- | --- |
| `random_pair-v2` | `random_pair-v3` | Re-minted as part of the corrected M6 build so that every current split is produced by one builder generation (m6/v2) and carries the `protocol` column. Its pair assignments are byte-identical to the v3 split; nothing about this split was wrong. |
| `cold_protein-v2` | `cold_protein-v3` | Re-minted as part of the corrected M6 build so that every current split is produced by one builder generation (m6/v2) and carries the `protocol` column. Its pair assignments are byte-identical to the v3 split. What v3 adds is a build-failing assertion that every scored target carries a cluster, which v2 never checked -- an unclustered target belongs to no group and so is silently exempt from the disjointness the split claims. |
| `chemistry_disjoint-v2` | `chemistry_disjoint-v3` | Re-minted as part of the corrected M6 build so that every current split is produced by one builder generation (m6/v2) and carries the `protocol` column. Its pair assignments are byte-identical to the v3 split. What v3 adds is a cluster-coverage assertion over every scored compound, plus the measured nearest-train Tanimoto distribution that quantifies how much weaker scaffold disjointness is than dissimilarity. |
| `label_reversal-v2` | `label_reversal-v3` | Re-minted as part of the corrected M6 build so that every current split is produced by one builder generation (m6/v2) and carries the `protocol` column. Its pair assignments are byte-identical to the v3 split, but its protocol changed from `trainable` to `diagnostic_frozen`: it has no validation partition and structurally cannot have one, so using it for model selection would be fitting to the bias probe itself. The assignments are the same; what the split may be used for is not. |
| `temporal_proxy-v2` | `temporal_proxy-v3` | Corrected. Builder m6/v1 assigned activities to temporal partitions but built no partition-scoped aggregates: it joined the GLOBAL M4 `pair_label`, whose medians and interval intersections summarise every measurement of a pair including ones dated after the cut, so a test pair's ground truth already contained its own future. Its aggregate assertion only proved a pair had an activity in the named partition. It also defined recurrence globally, which labelled 3,273 pairs `recurrent` in validation on the strength of a later test measurement. v3 materialises one endpoint per partition, records support activity IDs, and judges both recurrence and eligibility against each period's own history. |
| `temporal_proxy-v3` | `temporal_proxy-v4` | Corrected on two counts. (1) `training_visible_activities()` suppressed a training activity whenever its pair carried a `partition='excluded'` row, and a pair earns that row when the validation or test endpoint finds a conflict among measurements dated after the training cut -- so a future measurement was deciding what the model could learn from the past. 386 training activities across 228 pairs, all with clean training-period evidence, were suppressed this way. (2) Recurrence for the test period was judged against train+validation, but under the `train_only` protocol the model scoring test is fitted on train alone, so a pair first measured in the validation window is new to it. 3,431 test pairs were labelled `recurrent` on the strength of evidence the fitted model never saw. v4 judges the stratum against the actual training set and reports earlier-measurement history as a separate axis. |

The superseded rows and every assignment they own are **retained intact and marked**, not deleted or edited. A split is a claim about what a number means; correcting one by rewriting it in place would erase the evidence that the earlier number was different. Anything already computed against a superseded split stays reproducible and stays identifiable as stale.

## Limitations

| # | Limitation |
| --- | --- |
| 1 | **The grouped splits hold proportions only approximately.** Whole groups are assigned greedily to whichever partition is furthest below quota, so a large cluster can overshoot. Realised sizes are in the table above; the nominal 70/10/20 is a target, not a guarantee. |
| 2 | **Scaffold grouping is not Butina clustering,** and the measured distributions above quantify the gap: a substantial minority of held-out compounds have a training neighbour above 0.7 Tanimoto despite sharing no scaffold. `chemistry_disjoint` is a scaffold-disjointness guarantee and nothing stronger. |
| 3 | **40% identity is one choice, and clustering is not the same as novelty.** 3.9% of held-out targets keep a 90%+-identity local alignment into training; the audit above shows 43 of those 44 are fragment containment, but 20 still cover at least half of both proteins and should be read as near-homolog cases. 30% and 60% identity were not built. |
| 4 | **`temporal_proxy` cannot assert pair disjointness** and does not try. Its guarantee is per-activity plus per-partition aggregation, and the recurrent stratum is where that difference shows up. |
| 5 | **The temporal partitions are thinner than the global endpoint.** Each is aggregated from its own window, so pairs supported by few measurements in that window get correspondingly weaker aggregates. This is the honest cost of not borrowing evidence across the cut, not a defect to tune away. |
| 6 | **The temporal protocol is `train_only`, and the strata depend on it.** A model refitted on train+validation before the test evaluation would make 3,431 of the test `new` pairs genuinely recurrent, and would need a wider `training_visible_activities()`. That is a different experiment and would mint a new split version. |
| 7 | **No metrics are computed here.** These are partitions and their audits. Scoring is M7 and beyond, and nothing in this milestone has been trained. |

