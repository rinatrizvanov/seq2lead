# M8 evaluation contract

Written before any model was fitted. It fixes what is being predicted, on which
rows, with which inputs, and how the resulting numbers are computed — so that a
leaderboard entry can be recomputed by someone who has only this document, the
configs, and the database.

Status: the endpoint is **`provisional_pooled_for_exploratory_benchmark`**. Ki
pooling across assays was never validated; M5 reported the spread and declined to
claim otherwise. Every number produced under this contract inherits that status
and every results report states it.

## 1. Explicit inputs, never "latest"

An experiment names its inputs and their digests. Nothing resolves by recency —
a "latest cache" lookup silently changes what a leaderboard row means when a new
cache is built.

Each config in `configs/experiments/` pins:

| Input | Pinned by |
| --- | --- |
| endpoint | name and `endpoint_version.id` |
| split | name and `split_version.id` |
| feature caches | cache name, `manifest_sha256`, `storage_sha256` |

The loader re-reads each digest from the database and re-hashes each vector file
before any model is fitted. A mismatch is a hard failure, not a warning: the
experiment is no longer the one the config describes.

## 2. The prediction task

**Primary shared comparison: affinity regression on exact-relation labels.**

Targets come from `pair_regression.p_median` — the median of exact (`relation =
'='`) pKi measurements for a pair. Censored records contribute **no regression
target**. A `>` or `<` bound is an interval, not a point, and assigning it as a
scalar would invent a measurement nobody made.

Censored records re-enter at **evaluation**, where they are usable: a decisive
censored label (`pKi < θ` with the whole interval below the threshold, or `> θ`
with it above) is a real measured class label for ranking. Ranking asks "is this
compound active against this target", and a decisive bound answers that.

Models predict pKi. Ranking uses the predicted pKi to order each target's
eligible measured candidates.

**What each baseline isolates.** B1 is the **ligand-only** baseline: one model
over all targets, fingerprint in, pKi out, no target key. B3L is **target-
conditioned** — it searches only compounds measured against the same target in
training — so it measures within-target chemical-similarity lookup and does
**not** isolate ligand memorisation, despite reading no protein features. The
ligand-bias question belongs to B1.

**Any classifier, or any model consuming activity-derived features, is a
separately named experiment** with its own config, objective and provenance. It
does not enter the shared comparison, because it is not solving the same problem.

## 3. Cohort and eligibility

One cohort, shared by every model in a comparison. A model that scores a
different set of rows is not comparable to one that does not.

A pair is eligible when all hold:

1. It is assigned to the split partition being used.
2. It is not `excluded_from_eval` in the endpoint governing that partition.
3. Its compound has a **usable** fingerprint.
4. For regression, it has an exact-relation aggregate.

Rule 3 excludes the **179 compounds whose structures do not parse**. Their
fingerprints are all-zero rows, which a model reads as a real molecule with no
features — not as missing data. Rather than impute, they are excluded from the
shared primary cohort for every compared model. This is a **versioned experiment
eligibility rule**: the database rows and split assignments are untouched, and a
later experiment may include them under a declared imputation policy.

Affected pairs, by split and partition:

| Split | Partition | Pairs | Excluded | Remaining |
| --- | --- | --- | --- | --- |
| `random_pair-v3` | train | 328,251 | 26 | 328,225 |
| `random_pair-v3` | validation | 46,893 | 2 | 46,891 |
| `random_pair-v3` | test | 93,786 | 10 | 93,776 |
| `cold_protein-v3` | train | 328,251 | 28 | 328,223 |
| `cold_protein-v3` | validation | 46,893 | 1 | 46,892 |
| `cold_protein-v3` | test | 93,786 | 9 | 93,777 |
| `chemistry_disjoint-v3` | train | 328,251 | 38 | 328,213 |
| `chemistry_disjoint-v3` | validation | 46,893 | 0 | 46,893 |
| `chemistry_disjoint-v3` | test | 93,786 | 0 | 93,786 |
| `temporal_proxy-v4` | train | 360,404 | 38 | 360,366 |
| `temporal_proxy-v4` | validation | 41,638 | 3 | 41,635 |
| `temporal_proxy-v4` | test | 96,664 | 0 | 96,664 |
| `label_reversal-v3` | train | 467,157 | 35 | 467,122 |
| `label_reversal-v3` | test | 1,773 | 3 | 1,770 |

## 4. Temporal protocol

`temporal_proxy-v4` is `train_only`: the model scored on any period is fitted on
the `train` partition alone. Validation selects models and stopping points and is
not folded back in.

**Labels come from the partition's own endpoint.** Training targets come from
`temporal_proxy-v4--train`; validation and test ground truth from
`temporal_proxy-v4--validation` and `--test`. The global M4 aggregates supply
neither labels nor eligibility for this split — they summarise a pair's entire
measurement history, so grading a temporal test pair against them grades it
partly against its own future.

Results are reported for **`new` and `recurrent` strata separately**, never
pooled, for **both ranking and regression**. `new` **is the headline table** for
this split: the combined figure does not appear in headline comparisons at all,
because the recurrence rate is an artifact of how often BindingDB re-measures a
pair and would otherwise lift the number. A renderer test asserts the new-pair
result is used even when a differing combined result exists.

## 5. Training discipline

- **Preprocessing is fitted on training inputs only.** Any centring, scaling,
  whitening or dimensionality reduction takes its parameters from the training
  partition and is applied unchanged to validation and test.
- **Validation selects; test selects nothing.** Early stopping, hyperparameter
  choice and model selection read validation only. The test partition is scored
  once, after configurations are frozen.
- **Tuning budget is bounded and declared** per model in its config. The budget
  is part of the experiment, not an open-ended search.
- **Seeds**: stochastic models run over at least five declared seeds.
  Deterministic models run once and are labelled deterministic.

## 6. Metrics

### Ranking (primary)

Per target, over that target's eligible held-out measured compounds. **Unmeasured
pairs are never negatives.**

| Metric | Definition |
| --- | --- |
| AUROC | rank-based, tie-corrected (equivalent to the Mann–Whitney statistic with ties at 0.5) |
| Average precision | non-interpolated threshold-block AP: each distinct score is a threshold, the whole tied block is admitted before precision is read, and the recall increment is multiplied by that precision. Matches `sklearn.metrics.average_precision_score`; verified against it on 300 seeded examples |
| Positive prevalence | positives / eligible, reported beside every AP — AP is uninterpretable without it |
| Recall@k | k ∈ {10, 50}, and k is clipped to the eligible count when smaller |
| Enrichment factor | at 1% and 5%; cutoff index is `max(1, ceil(fraction * n))` |
| BEDROC | reported only if implemented and verified against a reference; otherwise omitted, not approximated |

**Tie handling.** Admitting a tied block whole is what makes AP independent of
order within it — no averaging over permutations is needed, and doing so is
wrong: it computes a different quantity, and one that errs in **both**
directions rather than being a bounded approximation. On `scores = [2, 1, 1]`,
`positive = [True, True, False]` the correct value is 0.8333333 and the
superseded M8 v1 implementation returned 0.8541667; on real data it also returns
values below the standard. Measured on one split it disagreed with
`sklearn.metrics.average_precision_score` on 245 of 328 scored targets, by up to
0.057 either way, while the corrected implementation agreed on all 328.

Protein-only and per-target-mean models assign the *same score
to every compound of a target*. Database order must never become a ranking
advantage, so every rank-based metric uses mid-ranks for tied scores, and
threshold metrics (recall@k, EF) average over the tied block rather than taking
whichever row came first. A model that ties everything scores exactly at chance,
which is the correct answer.

**Eligibility for a per-target metric**: at least 5 positives and 5 negatives
after cohort filtering. Targets failing this are counted and reported, not
silently dropped.

**Aggregation**: macro-average over eligible targets. Each metric is reported
with the number of targets it was computed over.

### Regression (secondary)

On eligible exact measurements only: MAE, RMSE, Spearman ρ, and concordance
index. Reported **per target then macro-averaged**, never pooled — a pooled
correlation is inflated by between-target variance and largely measures which
targets have tighter binders. Spearman and CI need ≥ 5 pairs with variance in the
label; targets below that are reported as undefined, with counts.

### Strata that are always broken out

- **temporal**: `new` and `recurrent`, separately.
- **cold-protein**: the persisted `near_homolog` stratum (20 targets, 3,838 test
  pairs) reported apart from the rest, **seed-averaged per model with the scored-
  target count beside it** — not selected as whichever single run showed the
  largest gap. Only 8 of the 20 clear the scoring floor, so the two sides of the
  comparison are not comparably precise, and no significance is claimed.
- **long sequences**: results with the `over_training_window` group identified,
  so the length policy can be judged against a metric.

## 7. Uncertainty

Spread across training seeds measures **optimiser variance, not biological
uncertainty**. It says how stable a fit is, not how confident we are about a new
target, and the two are not interchangeable.

Where an interval across targets is reported, it comes from a documented
**target-level bootstrap**: resample targets with replacement, recompute the
macro-average, report the percentile interval, and state that it assumes targets
are exchangeable — which they are not, since they vary in assay coverage,
protein family and measurement density.

## 8. `label_reversal` stays frozen

`label_reversal-v3` is `diagnostic_frozen`. It is **not scored during M8**. Its
evaluation code and tests exist; the run is reserved until every model choice is
frozen, and its result may not guide architecture or feature decisions.

Consequently the M8 leaderboard covers **four splits, not five**, and says so.
Claiming coverage of all five while the diagnostic is deliberately pending would
misrepresent what was measured.

## 9. Reproducibility

Per-pair predictions and per-target metrics are saved so every leaderboard number
can be recomputed independently. Large artifacts are referenced by path and
checksum rather than bundled.

Better scores are not an acceptance criterion. A baseline beating the joint model
is a finding about the benchmark and is reported as the headline.

## 10. Artifact verification and result selection

**Predictions are verified against a frozen manifest before anything is
recomputed.** `configs/manifests/m8_predictions.json` names the experiment, every
expected (model, split, seed) run, its filename and its SHA-256. The expected
digests were recovered from records written *before* the verification code
existed and cross-checked against a second independent record; they are **not**
regenerated from the files being verified, which would make the check circular.

Before a single metric is computed, recomputation requires **exactly** the
expected run set — a missing run is refused rather than silently shrinking the
leaderboard, and an unexpected file is refused rather than silently joining it —
then verifies every digest, the required arrays and their lengths, 1-D
predictions, unique pair ids, allowed labels, finite values, membership of each
pair in its split's own test cohort (partition-specific endpoints for temporal),
and that every scored temporal pair resolves to a `new` or `recurrent` stratum.
An unresolved stratum is an error, not an empty string. **On any failure nothing
is written and the published results stay exactly as they were.**

**The recomputation source is the one you name.** It is loaded through the
registry, so its digest and experiment binding are verified, and its run keys are
checked against the prediction manifest before anything is computed — a source
describing different runs would silently pair the wrong before/after values. The
correction record carries the source version *and* its artifact digest, and the
source is superseded only after the destination publishes successfully.

**Result versions are named and bound, never selected by recency.** Each result
set is registered in `reports/results/index.json` with its metric version, the
experiment it ran under and the digest of that config file. Fitting publishes
under an explicit version; recomputation names its source and destination;
rendering loads only the version it is asked for. There is no default and no
fallback chain — that chain is exactly how a stale corrected summary could keep
being rendered after a newer fitting run. Republishing a version with different
content is refused, including under a different filename; republishing identical
content is a no-op that preserves the registered metadata, so a rerun is safe.
Historical versions stay loadable by name.

**A filename belongs to one version.** Publishing to a path another version owns
is refused outright: it would leave that version failing its own checksum.
Publication also refuses to write over an artifact that already fails its
recorded digest, since overwriting would hide the breakage rather than surface
it.

**Writes are staged and the index is written last.** A fitting run commits its
full per-target record and its summary together, after every check has passed —
writing the record first, as an earlier version did, left a stray file beside an
index that knew nothing about it. On any validation failure every existing file
and the index remain byte-identical.

## 11. Corrections

Metric definitions and reporting rules are versioned. `METRIC_VERSION` is bumped
whenever one changes, and `uv run seq2lead eval recompute` regenerates every
figure **from the saved predictions, fitting nothing**. The corrected results are
written beside the superseded ones rather than over them, together with a
per-value comparison recording which figures moved and which did not: a
corrected number is only checkable against the one it replaced.

`m8/v2` corrected the average precision, made `new` the temporal headline, added
temporal regression breakouts, and replaced the near-homolog max-gap selection
with a seed average. AUROC and every regression metric were verified unchanged.
