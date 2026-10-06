# M7 — feature caches

Generated 2026-09-30 12:56 UTC.

> **Endpoint status: `provisional_pooled_for_exploratory_benchmark`**
>
> Ki is pooled across assays **provisionally, for exploratory benchmarking only**. The pre-declared decision rule returned `pool`, but it compared medians and was blind to a tail in which a quarter of assay-spanning pairs differ by more than tenfold. Pooling is therefore an operating assumption carried forward under protest, not a validated conclusion. Any result computed on pooled Ki inherits this limitation and must state it.
>
> See [`assay_variance.md`](assay_variance.md) for the evidence and its limits.

Contract: [`../docs/FEATURES.md`](../docs/FEATURES.md). Every number below is read from a probe artifact under `reports/probes/` or queried from the database at render time; none is a literal in the report generator.

## The population a cache is built over

M7 v1 enumerated **every row currently in `compound` and `target`**. That is not a reproducible population: anything that ever reached those tables joins it, and nothing in the cache's identity records which rows were present.

### Why the entity totals moved

The review asked why the totals rose to 1,424,672 compounds and 11,014 targets. They were **test fixtures that survived their own cleanup**:

| Entity | id | Structure / sequence | Activities | Inserted by |
| --- | --- | --- | --- | --- |
| compound | 2777916 | `CCO` (ethanol) | 0 | `tests/test_ingest.py`, `tests/test_temporal_visibility.py` |
| compound | 2777917 | `CCC` (propane) | 0 | `tests/test_curate.py`, `tests/test_ingest.py` |
| target | 1 | `MKVLSSAAWQR` (11 aa) | 0 | `tests/test_endpoint.py` |
| target | 11014 | `MKVLSSAAWQRTTYNEQ` (17 aa) | 0 | `tests/test_temporal_visibility.py` |

The cause is structural rather than a slip: curation inserts into `compound` and `target` with **no foreign key back to the release** that caused the insert, so a fixture's teardown cannot find its own entities by provenance. Deleting by structure is not available either — benzene, aspirin and nicotine are all real BindingDB compounds that the tests reuse, so a structure-keyed cleanup would destroy production rows. Every `_cleanup` therefore stopped at activities and releases, and the entities accumulated.

Both synthetic targets were embedded by ESM-2 as though they were proteins. Neither appeared in any split assignment, so no split was affected, and `target 1` had already been counted in the 11,013 figure the review used as its baseline — that baseline was itself contaminated.

Fixed in `seq2lead.db.maintenance.no_entity_leak()`: a high-water mark on the id sequence, taken before a fixture runs and used to delete what it created afterwards. Rows still carrying an activity are never deleted, so a test that reuses a real structure cannot take the real entity with it.

The regression test earned its place immediately: the first fix wired the guard into two fixtures and missed a third in `tests/test_curate.py`, and a full suite run still leaked one target and two compounds. **Verified after the complete fix**: a full run now leaves `compound` and `target` counts exactly unchanged, and no activity-free synthetic target survives.

### Caches are now scoped to a declared population

| Population | Compounds | Targets | Reproducible? |
| --- | --- | --- | --- |
| `all_rows` (what v1 used) | 1,424,670 | 11,012 | no — depends on what is in the table |
| `release:117` (**used now**) | 1,423,920 | 11,003 | yes — derived from the pinned release |

The 750 compounds and 9 targets outside the release population are real curated entities whose activities were all excluded during curation, plus nothing else now that the fixtures are cleaned up. They are not scoreable, appear in no split, and carrying them in a benchmark feature cache only invites a silent mismatch later.

## Cache identity

A cache's identity is now the SHA-256 of **two** things: the representation spec, and an input manifest. v1 hashed only the first, and that is exploitable — a `--limit 1000` smoke build and a full 1.4M-compound build produced the same hash, so the partial one could be registered under the full one's name and handed to anything that asked for the complete cache.

| Half | Covers |
| --- | --- |
| representation spec | model + **commit sha**, pooling, length policy, `max_length`, fingerprint radius / bits / **chirality**, standardizer version, dtype, split |
| input manifest | entity ids **and their content hashes**, the declared population, the requested limit, completeness (`full` / `partial`), and for activity features the split and a digest of the training evidence |

Content hashes matter as much as ids: a re-standardised structure or sequence under the same id would otherwise leave stale vectors attached to it. Library versions (`rdkit`, `torch`, `transformers`, `numpy`, Python) are recorded alongside each cache.

Still deliberately **absent**: device, batch size, worker count. They cannot change what a vector *represents*. They can change its bytes — see the reproducibility note below — and that distinction is the point.

### Caches

| Cache | Kind | Population | Complete | Entities | Dim | Build |
| --- | --- | --- | --- | --- | --- | --- |
| `ecfp4-compound-42351e003acb` | ecfp4 | `release:117` | full | 1,423,920 | 256 | 133 s |
| `activity-target-temporal_proxy-v4-64abbf7b5d9b` | activity | `training_visible` | full | 3,056 | 4 | 2 s |
| `esm2-target-48cfa09487ce` | esm2 | `release:117` | full | 11,003 | 1,280 | 5,163 s |

### Reuse, and what it costs

`find_reusable()` is consulted **before** any fingerprint is computed or ESM-2 is loaded. It matches on the full identity, verifies the stored bytes still hash to their recorded digest, refuses a superseded cache, and **never returns a partial cache for a full request**. An identical rerun therefore costs one manifest digest — about 3 s for 1.4M compounds — instead of the build.

Before acceptance a cache must pass validation: ids unique, ids exactly equal to the manifest's population, rows aligned with ids, non-zero width, and no non-finite values. A NaN from a failed forward pass reaches a model as a number and poisons every gradient that touches it, so it is caught at registration rather than three milestones later.

### Spec fields per cache

**`ecfp4-compound-42351e003acb`**

| Field | Value |
| --- | --- |
| `kind` | `ecfp4` |
| `entity` | `compound` |
| `pooling` | `none` |
| `radius` | `2` |
| `n_bits` | `2048` |
| `chirality` | `True` |
| `standardizer_version` | `m3/v3+rdkit-2026.03.6/cleanup+fragment-parent+uncharge/v1` |
| `dtype` | `uint8` |
| `extra` | `{"features": false, "storage": "bitpacked"}` |
| `libraries` | `{"numpy": "2.4.6", "python": "3.11.15", "rdkit": "2026.03.6"}` |

**`activity-target-temporal_proxy-v4-64abbf7b5d9b`**

| Field | Value |
| --- | --- |
| `kind` | `activity` |
| `entity` | `target` |
| `pooling` | `none` |
| `dtype` | `float32` |
| `split` | `temporal_proxy-v4` |
| `extra` | `{"columns": ["n_obs", "mean_pki", "median_pki", "spread_pki"], "source": "training_visible_activities", "threshold_pki": 6.0}` |
| `libraries` | `{"numpy": "2.4.6", "python": "3.11.15"}` |

**`esm2-target-48cfa09487ce`**

| Field | Value |
| --- | --- |
| `kind` | `esm2` |
| `entity` | `target` |
| `model` | `facebook/esm2_t33_650M_UR50D` |
| `model_revision` | `08e4846e537177426273712802403f7ba8261b6c` |
| `pooling` | `mean_over_residues_excluding_special_tokens` |
| `length_policy` | `full` |
| `max_length` | `40000` |
| `dtype` | `float32` |
| `extra` | `{"training_window": 1022}` |
| `libraries` | `{"numpy": "2.4.6", "python": "3.11.15", "torch": "2.14.0", "transformers": "5.17.0"}` |

## Stereochemistry in the fingerprints

M3 repaired compound identity after finding BindingDB's InChI Keys are stereo-insensitive — 64,486 keys carried 2–8 distinct stereoisomers across 448,735 rows — and re-keyed compounds on their structure. The v1 fingerprints then discarded that repair at the last step by building Morgan fingerprints with `includeChirality=False`.

Measured directly on three verified stereoisomer pairs, the achiral representation gives **byte-identical vectors**; the chiral one separates all three:

| Pair | Achiral fingerprints | Chiral fingerprints |
| --- | --- | --- |
| alanine R/S | identical | distinct |
| nicotine R/S | identical | distinct |
| 2-butene cis/trans | identical | distinct |

Tests in `tests/test_fingerprint_stereo.py` assert both directions on these pairs.

### Coverage affected

| | Compounds | Share |
| --- | --- | --- |
| In both the achiral and chiral caches | 1,423,920 | 100% |
| Fingerprint **changed** by enabling chirality | 563,538 | 39.6% |

| Distinct compounds sharing a fingerprint with another | Compounds | Groups |
| --- | --- | --- |
| achiral | 181,182 | 77,352 |
| chiral | 36,259 | 15,295 |

**144,923 compounds** stop sharing a vector with a different structure. **36,259 still do**, and that is the limit of the claim: ECFP4 is a *hashed* 2,048-bit fingerprint, so distinct molecules collide whether or not stereochemistry is encoded. Enabling chirality removes a systematic, avoidable collision between stereoisomers. It does **not** make the representation injective, and nothing here shows that every stereoisomer in the corpus is now uniquely identified.

The achiral cache is **retained and superseded, not deleted**. It is a valid achiral representation — the explicitly-named alternative for any experiment that wants one — rather than a corrupt artifact.

## The sequence-length policy

### The premise

ESM-2 is widely described as having a **1,022-residue limit**. For these checkpoints that is not a hard ceiling: the HF configs set `position_embedding_type='rotary'`, so `max_position_embeddings=1026` never indexes a learned position table, and nothing raises on a longer input.

Measured by `probe_length_feasibility` on mps (2.14.0), using the **real target nearest each requested length**:

| Requested | Nearest real target | Its length | Result | Wall time |
| --- | --- | --- | --- | --- |
| 1,022 | 1451 | 1,021 | embedded | 1.2 s |
| 2,000 | 3870 | 2,000 | embedded | 1.6 s |
| 4,000 | 4223 | 3,988 | embedded | 3.8 s |
| 8,000 | 9897 | 7,388 | embedded | 9.4 s |
| 20,000 | 9897 | 7,388 | embedded | 9.3 s |
| 34,350 | 10550 | 34,350 | embedded | 168.7 s |

Two requested lengths resolve to the same protein: the corpus has a gap between roughly 7,400 and 34,350 residues, so there is no real target near 8,000 or 20,000. The probe reports what it actually measured rather than padding a synthetic sequence to the requested length.

Nothing failed, up to the longest sequence in the corpus (34,350 residues). Neither a hard limit nor a memory wall forces a policy.

### What extrapolation does to a prefix

`probe_prefix_drift` embeds a target's first 1,022 residues alone — inside the pre-training crop length — then embeds the full sequence and extracts the same residues. Same residues, one in regime and one out.

Selection: targets with 1,200 ≤ length ≤ 6,000, ordered by `md5(sequence_sha256 || seed)`, first 16 taken (seed 20260929). Realised n = 16, lengths 1,205–3,256.

| Metric | min | p05 | p25 | median | p75 | p95 | max | n |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| pooled cosine | 0.9927 | 0.9969 | 0.9984 | 0.9992 | 0.9994 | 0.9997 | 0.9997 | 16 |
| mean per-residue cosine | 0.9672 | 0.9763 | 0.9910 | 0.9925 | 0.9935 | 0.9951 | 0.9968 | 16 |
| pooled relative L2 | 0.0252 | 0.0296 | 0.0350 | 0.0423 | 0.0566 | 0.0846 | 0.1234 | 16 |

**What this does and does not establish.** It measures how far a representation *moves* when the model is pushed past its training window. It is not an accuracy measurement: neither embedding is a ground truth, and no downstream task has been scored under either. It says nothing about binding prediction quality. And it is evidence at 1,205–3,256 residues only — it does **not** establish that the representation of a 34,350-residue protein is sound, because there is no in-regime version of residue 30,000 to compare against. Per-target measurements are in `reports/probes/prefix_drift.json`.

### Policy: `full`, and provisional

**Every residue is embedded in a single forward pass. No truncation, no chunking.** The reasoning:

1. There is no hard limit to respect — verified from the config and by running it.
2. Every sequence in the corpus is feasible on this hardware — measured above.
3. Truncation to 1,022 would discard up to 97% of a sequence, and would do so precisely to the large multi-domain proteins whose binding site is least likely to sit in the first 1,022 residues.
4. Chunking would keep every residue and stay in regime, but discards cross-window attention and adds a window-size parameter nothing in the data chooses.

**This is a provisional choice, not a validated one.** Points 1 and 2 are verified; point 3 is an argument, not a measurement; the drift probe bounds movement at moderate lengths and nothing more. The policy is falsifiable at M8 by scoring with and without the flagged targets, and by building a `truncate:1022` cache — which now mints a separate identity — and comparing. Unsupported policy strings such as `chunk:1022` are **refused**, not silently treated as truncation.

### What the policy affects

| | Targets | Share |
| --- | --- | --- |
| In the cache population (`release:117`) | 11,003 | 100% |
| …past the 1,022-residue training window | 1,299 | 11.8% |

Restricted to entities a split actually scores:

| Split | Scored targets | Past the window | Scored pairs | Pairs affected |
| --- | --- | --- | --- | --- |
| `random_pair-v3` | 3,787 | 333 | 468,930 | 33,907 (7.2%) |
| `cold_protein-v3` | 3,787 | 333 | 468,930 | 33,907 (7.2%) |
| `chemistry_disjoint-v3` | 3,787 | 333 | 468,930 | 33,907 (7.2%) |
| `temporal_proxy-v4` | 3,804 | 334 | 498,706 | 36,604 (7.3%) |

All of them are flagged `over_training_window` with their length, so every M8 score can be reported with and without them. That sensitivity report is the check on this policy; until it exists, the policy is untested.

### Does the cache encode homology?

Cosine between mean-pooled embeddings for target pairs inside one MMseqs2 cluster, and for pairs from different clusters. Sampled pair ids and their cluster memberships are in `reports/probes/cluster_similarity.json`.

| Pairs | min | p05 | p25 | median | p75 | p95 | max | n |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| same cluster | 0.2425 | 0.9098 | 0.9822 | 0.9948 | 0.9989 | 0.9999 | 1.0000 | 3,997 |
| different cluster | -0.0112 | 0.6166 | 0.8302 | 0.8918 | 0.9287 | 0.9586 | 0.9894 | 4,000 |

The distributions are **offset but overlapping**: the different-cluster p95 is 0.9586 against a same-cluster p05 of 0.9098, and 39.2% of different-cluster pairs exceed that same-cluster p05. Reporting the share above a single median would have made the separation look cleaner than it is.

**A different cluster is not an unrelated protein.** MMseqs2 clustered at 40% identity with 80% coverage, so two targets in different clusters may still be homologous, share a domain, or share a fold — the M6 audit found exactly that. The contrast here is *clustered together* versus *not clustered together*, which is weaker than *related* versus *unrelated*.

The different-cluster median of **0.8918** is the number to carry into M8. Mean-pooled ESM-2 vectors occupy a narrow cone, so cosine has a compressed dynamic range — and the planned ConPLex-style dual encoder *scores by cosine*. That argues for treating the learned projection as doing real work, and for centring or whitening as a pre-registered ablation.

## What a feature is allowed to read

| Feature | Function of | May cover held-out entities? |
| --- | --- | --- |
| ECFP4 | the compound structure | **yes** |
| ESM-2 | the target sequence | **yes** |
| activity aggregates | measured pKi values | **no** |

A fingerprint or embedding for a held-out entity leaks nothing: a deployed model derives it from structure and sequence alone. Activity aggregates summarise the outcomes the model is asked to predict, so every one is built from `training_visible_activities()` for the active split and from nothing else. That function returns SQL rather than rows, and the builder composes it rather than restating the filter, so the two cannot drift apart.

| Check | Count |
| --- | --- |
| Activities in the split | 619,931 |
| Reachable through `training_visible_activities()` | 401,586 |
| …of those, held out (**must be 0**) | 0 |
| Held-out activities existing for trained-on targets | 129,258 |
| Temporal protocol | `train_only` |

### What the gate withholds

| | Gated (used) | Ungated |
| --- | --- | --- |
| Exact Ki observations | 355,809 | 521,032 |
| Targets whose count changes | 1,300 of 3,056 (42.5%) | — |

**31.7% of exact Ki observations are withheld.** The shift in a target's mean pKi between the two views is the error avoided — median **0.000**, p90 **0.342**, worst **4.94 log units**, roughly five orders of magnitude in Ki. A test recomputes the aggregate without the gate and asserts the two differ, so the gate cannot pass by doing nothing.

## Feature coverage

Cache identity guarantees a cache matches the population it **claims**. It says nothing about whether that population covers the entities a split will score — different question, and the gap between them is where a training run dies at hour three. Asserted for every active split and both entity types:

| Split | Entity | Scored | Covered | Missing | Unusable | Cache |
| --- | --- | --- | --- | --- | --- | --- |
| `random_pair-v3` | compound | 246,192 | 246,192 | 0 | 17 | `ecfp4-compound-42351e003acb` |
| `random_pair-v3` | target | 3,787 | 3,787 | 0 | 0 | `esm2-target-48cfa09487ce` |
| `cold_protein-v3` | compound | 246,192 | 246,192 | 0 | 17 | `ecfp4-compound-42351e003acb` |
| `cold_protein-v3` | target | 3,787 | 3,787 | 0 | 0 | `esm2-target-48cfa09487ce` |
| `chemistry_disjoint-v3` | compound | 246,192 | 246,192 | 0 | 17 | `ecfp4-compound-42351e003acb` |
| `chemistry_disjoint-v3` | target | 3,787 | 3,787 | 0 | 0 | `esm2-target-48cfa09487ce` |
| `label_reversal-v3` | compound | 246,192 | 246,192 | 0 | 17 | `ecfp4-compound-42351e003acb` |
| `label_reversal-v3` | target | 3,787 | 3,787 | 0 | 0 | `esm2-target-48cfa09487ce` |
| `temporal_proxy-v4` | compound | 250,316 | 250,316 | 0 | 17 | `ecfp4-compound-42351e003acb` |
| `temporal_proxy-v4` | target | 3,804 | 3,804 | 0 | 0 | `esm2-target-48cfa09487ce` |

**0 missing** across 10 checks. `assert_feature_coverage()` raises on any gap and names the split, the entity and example ids, so a failure is actionable rather than a boolean.

**Missing and unusable are counted separately, and the distinction matters.** A missing vector raises a `KeyError` at lookup — loud, and immediately obvious. An *unusable* one does not: the 17 flagged compounds per split hold an all-zero fingerprint because their structure would not parse, and a model reads that as a real molecule with no features. Nothing raises. It is the quieter failure of the two, which is why it is surfaced here rather than folded into the covered count.

## The near-homolog evaluation stratum

**`cold_protein-v3`** — 20 held-out targets, covering **3,838 of 93,786 test pairs (4.1%)**. They satisfy the cold-protein guarantee and still have a training protein aligning at ≥90% identity over at least half of both sequences, so a score on them measures near-homolog transfer. Reported separately at M8.

## Reproducibility

Probe artifacts under `reports/probes/` carry, for every run: the entity ids and content hashes selected, the selection rule and seed, the model and **commit sha**, the pooling policy, the device, the library versions, every individual measurement, and every failure. The report renders statistics from those files and says so when one is missing.

**A shared specification is not byte-identical output.** Two runs of the same spec on different backends — MPS versus CPU, a different BLAS, a different torch build — can differ in the last floating-point places, and reduction order alone is enough to cause it. What the identity guarantees is that two caches with the same key were built from the same inputs under the same representation rules. It does not guarantee the bytes match across machines, and `storage_sha256` is a tamper check on one file rather than a cross-machine reproducibility claim. Seeds in these probes select *which entities are measured*; the forward passes themselves are deterministic.

## Limitations and open M8 decisions

| # | Item | Status |
| --- | --- | --- |
| 1 | **179 compounds have no parseable structure** and hold an all-zero row. Scored pairs affected: `chemistry_disjoint-v3` 38, `cold_protein-v3` 38, `label_reversal-v3` 38, `random_pair-v3` 38, `temporal_proxy-v4` 41. A zero vector is **not** a missing value — a model reads it as a real molecule with no features. | **Open M8 decision**: drop these pairs, or add an explicit missing-feature indicator. No imputation is applied here. |
| 2 | The `full` length policy rests on feasibility plus a bounded drift measurement, not on any task score. | **Open M8 decision**: report every metric with and without `over_training_window` targets, and compare against a `truncate:1022` cache. |
| 3 | Chiral fingerprints resolve most stereoisomer collisions but ECFP4 stays a lossy hashed representation, with collisions remaining. | Measured, reported, not claimed away. |
| 4 | Mean pooling discards positional structure and compresses the cosine scale. | **Open M8 decision**: centring/whitening and per-residue attention pooling as pre-registered ablations. |
| 5 | The activity cache summarises **exact Ki only**; censored records carry no point value and contribute to no mean, median or spread. | By design; the interval evidence lives in `pair_label`. |
| 6 | No model has been trained. These are caches and their audits. | — |

