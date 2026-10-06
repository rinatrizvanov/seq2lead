# M7 feature contract

Revised after review. Four questions this settles: what makes two caches
different objects, what a feature is allowed to read, what happens to sequences
longer than the model's training window, and what the evidence behind those
choices actually supports.

## 1. Identity is the spec **and** the inputs

A cache named `ecfp4` or `esm2` is a trap, and so is one keyed on the
representation settings alone. The first version hashed only the spec, so a
`--limit 1000` smoke build and a full 1.4M-compound build produced the same
identity — the partial cache could be registered under the full one's name and
handed to anything that asked for the complete set.

Identity is now `sha256(spec ‖ input manifest)`.

**The spec** — everything about *how* a vector is computed:

| Field | Why it is in the identity |
| --- | --- |
| `kind`, `entity` | different feature, different object |
| `model`, `model_revision` | the **commit sha**, never the tag — tags move |
| `pooling` | mean-over-residues and CLS are different vectors from the same weights |
| `length_policy`, `max_length` | two policies differ exactly where it matters most |
| `radius`, `n_bits`, `chirality` | fingerprint geometry, including stereochemistry |
| `standardizer_version` | the curation generation the inputs came from |
| `dtype`, `split` | |

**The input manifest** — everything about *what went in*:

| Field | Why |
| --- | --- |
| entity ids **and content hashes** | a re-standardised structure under the same id must invalidate the cache |
| `population` | `release:<id>` is reproducible; `all_rows` is whatever is in the table |
| `limit`, `completeness` | `full` and `partial` are different objects |
| `split`, `evidence_digest` | activity features: the same split with a rebuilt training set yields different numbers |

Library versions (`rdkit`, `torch`, `transformers`, `numpy`, Python) are recorded
with each cache. Device, batch size and worker count are deliberately **not** in
the identity: they cannot change what a vector represents.

**Validation before acceptance.** Ids unique; ids exactly equal to the manifest
population; rows aligned with ids; non-zero width; no non-finite values. A NaN
from a failed forward pass reaches a model as a number and poisons every gradient
that touches it.

**Reuse before computation.** `find_reusable()` runs before any fingerprint is
computed or ESM-2 is loaded. It matches the full identity, verifies the stored
bytes against their recorded digest, refuses a superseded cache, and never
returns a partial cache for a full request.

**Loading.** `load_features()` takes a cache **name**, or a `FeatureSpec`
**together with its `InputManifest`**. A spec on its own is refused: it is the
representation recipe, not an identity, and cannot distinguish a partial build
from a full one or one population from another. An earlier version hashed the
spec alone and looked the result up in the identity column, so once identities
became manifest-aware every spec-based load silently resolved to nothing.
Strings are tried as a name and then as a full identity — never inferred from
length. A superseded cache requires `allow_superseded=True`.

**Validation is enforced at registration, not by the caller.** `register()`
validates before it writes: structural checks plus the entity digest re-derived
from the cache's own ids against the database's current content hashes. A cache
that fails is never written to disk and never recorded, so a half-built or
mis-populated one cannot be read back later. Putting this in the CLI alone would
let anything using the library directly skip every check.

## 2. Feature coverage

Identity proves a cache matches the population it *claims*. Whether that
population covers the entities a split will **score** is a different question,
and the gap is where a training run fails late.

`assert_feature_coverage()` checks every non-superseded split against the current
caches, for both entity types, and names the split, the entity and example ids on
failure.

**Missing and unusable are counted separately.** A missing vector raises at
lookup. An unusable one — an all-zero fingerprint from a structure that would not
parse — does not: a model reads it as a real molecule with no features. The
quieter failure is the one worth surfacing, so it is reported beside the covered
count rather than inside it.

**Population.** Benchmark caches are scoped to `release:<id>` — the entities the
pinned release actually measured — not to every row present. Curation inserts
compounds and targets with no foreign key back to a release, so `all_rows` is not
reproducible: a test fixture's entities join it silently. That is not
hypothetical; see `reports/features.md`.

## 3. What a feature may read

The dividing line is **input** versus **measured outcome**.

| Feature | Function of | May cover held-out entities? |
| --- | --- | --- |
| ECFP4 | the compound structure | **yes** |
| ESM-2 | the target sequence | **yes** |
| activity aggregates | measured pKi values | **no** |

A fingerprint for a held-out compound leaks nothing: a deployed model derives it
from the structure alone. Activity aggregates summarise the outcomes the model is
asked to predict, so each is built from `training_visible_activities()` for the
active split and nothing else. That function returns SQL rather than rows, and
the builder composes it rather than restating the filter, so the two cannot
drift apart. `assert_no_held_out_activity_in_support()` re-derives the support
and fails the build if any held-out activity is reachable.

For `temporal_proxy` the visible set is the training partition's activities minus
pairs the **training period's own** endpoint rejected — never the global
endpoint's, and never filtered by the `excluded` partition, which is populated
from evidence dated after the cut. Protocol stays `train_only`. See `SPLITS.md`
§6–7.

## 4. Stereochemistry

M3 repaired compound identity after finding BindingDB's InChI Keys are
stereo-insensitive — 64,486 keys carried 2–8 stereoisomers across 448,735 rows —
and re-keyed compounds on their structure. Building fingerprints with
`includeChirality=False` discards that repair at the last step: measured on
verified R/S and cis/trans pairs, the achiral representation gives
**byte-identical vectors**.

The primary cache therefore sets `includeChirality=True`, and the setting is part
of the identity. The achiral cache is **retained and superseded**, not deleted —
it is a valid achiral representation, available by name for any experiment that
wants one.

**The claim is bounded.** Chirality removes a systematic, avoidable collision
between stereoisomers. ECFP4 remains a *hashed* 2,048-bit fingerprint, so
distinct molecules still collide; the residual count is measured and reported.
Chiral hashed fingerprints do **not** uniquely identify every stereoisomer, and
nothing here claims they do.

## 5. The sequence-length policy

**The premise needed checking.** ESM-2 is widely described as having a
1,022-residue limit. For these checkpoints it is not a hard one: the configs set
`position_embedding_type='rotary'`, so `max_position_embeddings=1026` never
indexes a learned table, and no length attempted failed. The real constraint is
that ESM-2 was pre-trained on crops of 1,024 tokens, so beyond that the rotary
embedding extrapolates.

**Policy: `full`, and provisional.** Every residue in one forward pass; no
truncation, no chunking. Truncation would discard up to 97% of a sequence, and
would do it to precisely the large multi-domain proteins whose binding site is
least likely to sit in the first 1,022 residues. Chunking stays in regime and
keeps every residue but discards cross-window attention and adds a window-size
parameter nothing in the data chooses.

**What the evidence supports, and what it does not.** The probes establish that
nothing fails, and that a shared prefix moves little at the lengths sampled.
They do **not** establish representation quality at 34,350 residues — there is no
in-regime version of residue 30,000 to compare against — and they measure
*movement*, not accuracy: neither embedding is a ground truth and no task has
been scored under either. The policy is falsifiable at M8 by reporting every
metric with and without `over_training_window` targets, and by comparing against
a `truncate:1022` cache, which mints a separate identity.

`max_length = 40,000` is a **refusal point, not a truncation point**. Unsupported
policy strings (`chunk:1022`, `sliding`, a bare `truncate`) are **refused**
rather than silently treated as truncation, so a cache cannot be built under a
name that misstates how it was made.

## 6. Probes and artifacts

Every measurement in `reports/features.md` comes from a probe artifact under
`reports/probes/` or from a query run at render time. None is a literal in the
report generator — the first version hardcoded its timings and drift table, which
made them unfalsifiable.

Each artifact records the entity ids and content hashes selected, the selection
rule and seed, the model and commit sha, pooling, device, library versions, every
individual measurement, and every failure. Run them with
`uv run seq2lead features probe`.

**A shared specification is not byte-identical output.** Two runs of the same
spec on different backends can differ in the last floating-point places;
reduction order alone is enough. The identity guarantees that two caches with the
same key were built from the same inputs under the same representation rules. It
does not guarantee the bytes match across machines. `storage_sha256` is a tamper
check on one file, not a cross-machine reproducibility claim. Seeds select which
entities are measured; the forward passes are deterministic.

## 7. Strata that attach to a target

`split_target_stratum` records evaluation strata keyed to a target rather than a
pair. The first is `near_homolog`: held-out targets that satisfy the cold-protein
guarantee — no shared MMseqs2 cluster at 40% identity, 80% coverage — and still
have a training protein aligning at ≥90% identity over at least half of **both**
sequences. Both thresholds are load-bearing: identity alone sweeps in fragment
containment, which the M6 audit found in 43 of 44 high-identity hits.

## 8. Test isolation

Fixture-writing tests run against a **throwaway schema**, created and dropped
around the module via `seq2lead.db.isolation.isolated_schema()`. They previously
ingested synthetic releases straight into the curated corpus; cleanup was
best-effort and provenance-blind, because curation inserts compounds and targets
with no foreign key back to a release. Two test molecules and two test proteins
survived that way and were embedded by ESM-2 as though they were data.

`public` stays on the search path so extensions such as pgcrypto resolve, which
means shadowing has to be **proved rather than assumed**: `is_fully_shadowed()`
checks that every table the migrations create exists in the test schema, and the
fixture refuses to run if one would fall through to the corpus. Schema names are
validated against a strict pattern before interpolation.

The id high-water-mark guard (`no_entity_leak()`) is retained as a second line
for any path that is not isolated, and as the cleanup an operator can run by
hand.

## 9. Versioning

Caches are immutable. A changed spec or a changed population mints a new identity
and a new file rather than overwriting one. A superseded cache is **marked, not
deleted**, with a reason that says what was wrong with it — the same rule
endpoints and splits follow.
