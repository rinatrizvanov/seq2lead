# M6 split contract

Written before implementation, so the implementation can be checked against it
rather than described by it.

The endpoint carries `provisional_pooled_for_exploratory_benchmark`. Splits do
not change that, and every split-derived report repeats it.

## 1. Unit of assignment

**Four of the five splits assign whole compound–target pairs.** A pair is the
smallest object the benchmark scores, so splitting one across partitions would
put a measurement of a test pair into training. That is the leak these splits
exist to prevent, and it is not recoverable by any later filter.

| Split | Assignment unit | Grouping constraint |
| --- | --- | --- |
| `random_pair` | pair | none |
| `cold_protein` | pair | whole MMseqs2 sequence clusters held out together |
| `chemistry_disjoint` | pair | whole Butina/scaffold chemistry clusters held out together |
| `label_reversal` | pair | compounds selected by class membership; all their pairs move together |
| `temporal_proxy` | **activity** | see §2 |

A pair's `pair_regression` row, its `pair_label` row and every activity in
`pair_regression_support` / `pair_label_support` share the pair's partition.
There is no case in which some of a pair's evidence is in training and the rest
is held out.

## 2. `temporal_proxy` assigns activities, then aggregates

Aggregating first and splitting afterwards leaks: a pair's median would already
summarise measurements dated after the cut. So, as planned at M2:

1. Assign each **`activity`** to a temporal partition by publication date,
   falling back to curation date where publication date is absent, recording
   which was used per row.
2. **Aggregate inside each partition independently.** A pair may therefore exist
   in more than one partition, each with its own aggregate over that partition's
   activities only.
3. Never carry an aggregate across the cut.

Concretely, the split **materialises one endpoint version per partition**, named
`<split>--<partition>`, built by the same endpoint builder from that partition's
assigned activities alone and under the identical exact/censored conflict rules.
Each aggregate records the **activity IDs that support it**, and a build-failing
assertion confirms every support belongs to the partition that owns the
aggregate.

The global M4 `pair_regression` and `pair_label` rows are **never** used as a
temporal training target or as held-out ground truth. They summarise a pair's
entire measurement history, so using one to grade a temporal test pair would
grade it partly against its own future.

This is a *proxy*, not an as-of snapshot: BindingDB's publication date is the
article's date, not the date the record became retrievable, and records are
revised and back-filled. The name says so everywhere it appears.

## 3. Recurrent pairs, and what the temporal score measures

**Protocol: `train_only`.** The model scored on any evaluation period is fitted
on the `train` partition alone. Validation selects between models and stops
training; it is not folded back in before the test evaluation. The protocol is
stored in the split's `params` because the stratum definition is meaningless
without it.

Recurrence is defined **relative to what the fitted model actually saw**. A pair
is **recurrent** for a period if the model scoring it had already been shown a
measurement of that pair, and **new** otherwise:

| Evaluation period | Fitted on | Recurrent means |
| --- | --- | --- |
| `validation` | `train` | measured in `train` |
| `test` | `train` | measured in `train` |

Under this protocol a pair first measured in the validation window is **new** to
the model scoring the test period, even though the database had measured it
earlier. Judging it against "everything measured earlier" would credit the model
with evidence it was never shown.

Earlier measurements are not thrown away: `history_partitions` records, per pair,
which earlier partitions actually measured it, and the report cross-tabulates it
against the stratum. A test pair that is `new` but was measured in validation is
reported as exactly that.

If the protocol ever changes to refit on `train` + `validation` before scoring
test, that is a different experiment — it widens `training_visible_activities()`,
lets validation evidence into retrieval indexes and features, and makes those
pairs genuinely recurrent. It mints a new split version rather than editing this
one.

Defining recurrence globally — "appears in more than one partition" — would be
wrong twice over: it would count a pair as recurrent in validation because of a
*later* test measurement, and recurrent in test because of a validation
measurement the model never trained on. Counts are reported **separately for each
period**, and test counts are never merged with train or validation.

These answer different questions and are **reported as separate strata, never
pooled into one headline number**:

| Stratum | Question it answers |
| --- | --- |
| **New pairs** (primary) | Can the model rank a pair nobody had measured at training time? This is the prospective use the project exists for. |
| **Recurrent pairs** (secondary) | Given earlier measurements of this pair, does the model predict the later ones? Much easier, and not the deployment question. |

**Decision: both are computed, new pairs is the headline, and a combined figure
is not reported.** Pooling them would let recurrence rate — an artifact of how
often BindingDB re-measures things — drive the score. Each stratum's size is
reported next to its metric, because a stratum of a few hundred pairs cannot
carry a conclusion.

Recurrent pairs are also the reason `temporal_proxy` cannot use the same
zero-overlap assertion as the others: by construction a recurrent pair appears on
both sides of the cut. That is intended, and it is precisely why the strata are
scored separately.

## 4. Assertions, by split

Build-failing checks. "Pair overlap" means the same `(compound_id, target_id)`
in more than one partition.

| Assertion | random_pair | cold_protein | chemistry_disjoint | label_reversal | temporal_proxy |
| --- | --- | --- | --- | --- | --- |
| Pair overlap across partitions = 0 | ✓ | ✓ | ✓ | ✓ | **not applicable** |
| Activity assigned to exactly one partition | ✓ | ✓ | ✓ | ✓ | ✓ |
| Sequence-cluster overlap = 0 | — | ✓ | — | — | — |
| Chemistry-cluster overlap = 0 | — | — | ✓ | — | — |
| Compound appears in opposite classes across partitions | — | — | — | ✓ (by design) | — |
| No aggregate spans partitions | ✓ | ✓ | ✓ | ✓ | ✓ |
| Held-out activity absent from any retrieval index | ✓ | ✓ | ✓ | ✓ | ✓ |
| Model-facing spread computed within training only | ✓ | ✓ | ✓ | ✓ | ✓ |
| Every scored target carries a cluster | — | ✓ | — | — | — |
| Every scored compound carries a cluster | — | — | ✓ | — | — |
| Partition has its own materialised endpoint | — | — | — | — | ✓ |
| No aggregate supported by another partition's activity | — | — | — | — | ✓ |
| Assigned pairs come from the partition endpoint | — | — | — | — | ✓ |

The **cluster-coverage** assertions exist because an entity with no cluster
assignment is silently exempt from the disjointness the split claims: it belongs
to no group, so no group-overlap check can catch it. Coverage is asserted over
every scored target and compound, not over the clustering input.

For `temporal_proxy` the pair-overlap assertion is replaced by:

* every activity is in exactly one partition, **and**
* every partition has its own materialised endpoint, **and**
* every aggregate's support activities belong exclusively to that partition,
  **and**
* assigned pairs are read from the partition endpoint, not the global one,
  **and**
* recurrent pairs are enumerated per evaluation period and reported rather than
  silently allowed.

Alongside the assertions, and **not** as assertions, the cold splits report the
distribution of each held-out entity's similarity to its nearest training
entity — sequence identity for targets, ECFP4 Tanimoto for compounds. Zero
cluster overlap does not imply unfamiliarity, and the distribution is what makes
the difference visible instead of assumed.

Every held-out target whose best training hit exceeds 90% identity is further
audited with **alignment length and coverage on both sequences**, because
identity alone cannot separate "the same protein under another accession" from
"two proteins sharing one domain". The claim `cold_protein` is entitled to make
is therefore bounded:

> No held-out target shares an MMseqs2 cluster with any training target at 40%
> identity and 80% coverage.

It is **not** a claim that held-out targets are unrelated to training proteins.
The audit reports how many have a high-identity alignment covering most of both
sequences, and those are flagged so a score on them can be read as near-homolog
performance rather than novel-target performance.

`label_reversal` deliberately places the same compound in different classes
across partitions — that is the construction, not a leak. Its assertion is that
no *pair* is shared, and that the reversed compounds really do carry opposite
labels on the two sides.

## 5. What must not reach training

Three channels, each with a named rule:

1. **Evidence.** No held-out `activity` row may be read when constructing a
   training aggregate, feature or target. Enforced by partition-scoped
   aggregation and asserted.
2. **Retrieval.** Any nearest-neighbour or support index is built from the
   training partition alone. A held-out `activity_id` appearing in an index is a
   build failure.
3. **Assay-spread features.** Per the M5 contract: a pair's *model-facing* spread
   is the spread of its **training** observations, even where more exist.
   Audit-facing spread may use everything but is written to reports only and
   never joined into a feature, filter or sample weight. The two are stored under
   different names, and the partition-scoped one carries its `split_version`.

## 6. Exclusions carried from M4

`excluded_from_eval` pairs — contradictory, empty-intersection, no-usable-
evidence, discordant, ambiguous — are **kept out of validation and test in every
split**.

**For `temporal_proxy`, eligibility is decided by the partition's own endpoint,
not the global one.** A global exclusion is a judgement formed by pooling a
pair's whole measurement history; applying it to an earlier partition would let a
measurement dated after the cut decide which pairs the benchmark contains and
which the model may train on. The same rule is applied — it is simply applied to
the evidence the partition actually has. `training_visible_activities()` likewise
consults the **training period's** endpoint, so a pair that period judged
unusable cannot re-enter as a training example.

It deliberately does **not** consult `split_pair_assignment.partition =
'excluded'`. A pair earns that row when the validation or test endpoint finds a
conflict among *its* measurements, all of which are dated after the training cut.
Suppressing a clean training record on that basis would let a future measurement
decide what the model may learn from the past — the same leak, running backwards.
Training eligibility is decided by the training period alone. Whether they may be used for training is a separate decision and is
recorded per split rather than assumed; the default is to exclude them there too,
so that a first benchmark is not trained on evidence the project has already
declared unreliable.

## 7. Protocol: what a split may be used for

Each split carries a `protocol`:

- **`trainable`** — may be used to fit models and select between them.
- **`diagnostic_frozen`** — may not. It is evaluated **once, after every model
  choice is frozen**, and its result may not motivate a change to any of those
  choices; if it does, the change mints a new version and the number is re-earned
  on a split the new choices have not seen.

`label_reversal` is `diagnostic_frozen`. It has no validation partition, and
structurally cannot have one: every compound it holds out is chosen precisely
because its label reverses, leaving no comparable slice to tune against.
Selecting a model on a bias probe is fitting to the probe, which is the one thing
that would make its number meaningless.

## 8. Versioning

Each split is written with a `split_version` and a seed. Splits are immutable
once built: a changed rule mints a new version rather than editing one, exactly
as endpoint versions do. Reports name the `split_version` they describe.

A superseded split is **marked, never deleted or edited**: `superseded_by` and
`superseded_reason` are set and every assignment it owns is retained. A split is
a claim about what a number means, so correcting one by rewriting it in place
would erase the evidence that the earlier number was different. Reports render
only the current versions and list the superseded ones with their reasons.
