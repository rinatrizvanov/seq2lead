# M11d — snapshot matching, 202601 against 202609

**Scope: descriptive matching only.** No endpoint build, no Ki aggregation, no
pair-eligibility calculation, no features, no fitting, no evaluation score, no
docking, no downloads. The confirmatory freeze is **not signed** and the study
remains **exploratory**. Snapshot B is our already-inspected 202609, from which
the M8/M9 results were produced, so this validates machinery and measures rates —
it is not a confirmatory evaluation and does not become one by being careful.

Machine-readable record: `configs/manifests/m11d_matching_202601_202609.json`.
Detail files are referenced **by digest**, not inlined; they hold 1,091,482 records
across 380 MB.

## 0. Correction pass — what changed since the first revision

**The main diff was not rerun.** All nine detail files were verified
byte-identical by digest before and after this pass, and the headline totals,
reconciliations and per-type counts are unchanged. Three defects were in the
*cross-slot candidate-correspondence analysis* and one in the shard budget ordering.

### The cross-slot analysis had two defects, both now regression-tested

1. **Addition counts were never consumed.** A removal was matched against an
   addition without decrementing it, so two removals sharing a group key both
   counted as moves into a single addition.
2. **The first eligible addition in file order was chosen.** Reversing the detail
   file changed which record a removal was compared against, and with it the
   reported identifier agreement — demonstrated going from 1/1 agreement to 0/1
   on the same data.

Both are reproduced in `tests/test_m11d_cross_slot_pairing.py` before being
fixed.

**What the old 30,699 actually was.** Not matched pairs: the count of removal
*occurrences* sitting in a group with at least one eligible addition. It equals
the corrected removal-occurrences-considered figure exactly, which is how the
defect was confirmed.

### Old against corrected

| | First revision | Corrected |
| --- | ---: | ---: |
| Matched occurrences | 30,699 | **28,231** |
| Entry DOI agreement | 29,749 / 30,699 = **96.91%** | 28,194 / 28,231 = **99.87%** |
| `reactant_set_id` agreement | 29,244 / 30,699 = **95.26%** | 28,194 / 28,231 = **99.87%** |
| Gained an attribution | 30,215 | 28,129 |
| Changed attribution | not separated | 102 |
| Moved on a non-publication field | 484 (an artifact) | **0** |
| EC50 / IC50 / KD / KI | 1,756 / 21,851 / 1,875 / 5,217 | 1,663 / 20,679 / 1,858 / 4,031 |
| Ambiguous groups | not reported — silently resolved | **1,673** |

**Agreement went up, and the reason matters.** The old figure *understated*
agreement on the forced-correspondence population, because it folded in ambiguous
groups whose records it had paired arbitrarily — and **all 1,673** ambiguous
groups contain candidates whose identifiers disagree, so those arbitrary pairings
were counted as disagreements. Blending the two populations was the error. The
corrected report keeps them apart and gives the ambiguous groups their own counts
plus a labelled algorithm-dependent sensitivity.

### The EC50 framing was also wrong, not just the number

The first revision said re-attribution "accounts for 7.7% of removals". True, and
beside the point: a re-attribution contributes one removal **and** one addition,
so it is net-neutral and cannot be a partial explanation of a *net* decline. See
§6.

### Budget ordering

`diff_sharded` loaded both shard files and *then* checked the row budget — which
is checking after the allocation that would exhaust memory has already happened.
The budget is now applied from a counted byte scan while nothing is resident, and
the subsequent reads are themselves bounded, so neither a stale count nor a wrong
one can get past it. Both paths are tested, including one that patches the counter
to lie.

## 1. Bounded-memory matching

`diff_snapshots` takes both sides as in-memory lists, measured at 25.3 GB for
this corpus against 16 GiB of physical memory. `seq2lead.asof.sharded` is a
**decomposition of that function, not a reimplementation**: every shard pair is
handed to the same `diff_snapshots`, so the matching rules, the counted-multiset
pairing, the correction-link policy and the numeric normalisation cannot drift
from it.

### Why sharding by slot is sound

Every decision `diff_snapshots` makes is scoped to one slot — `_group` keys on
`Slot`, value multiplicities are compared within a slot, `_link` draws its one
removal and one addition from a single slot's surplus, and `new_slots` /
`removed_slots` are per-slot by definition. No rule reads across slots. And every
`DiffReport` field composes: counters add, slot sets union, detail lists
concatenate. Because each slot lands in exactly one shard the per-shard slot sets
are **disjoint**, so union degenerates to addition and no cross-shard
de-duplication is needed.

### The shard key is cryptographic, deliberately

`blake2b-64` of the slot's canonical serialisation, modulo the shard count.
**Not Python's `hash()`**, which is randomised per process unless `PYTHONHASHSEED`
is set — a partition built on it would differ between runs and could not be
reproduced from the recorded configuration. The test pins a known-answer value
rather than comparing `shard_index` to itself, which would pass even if it called
`hash()`.

The key is the string `_group` already keys on, so two observations with the same
slot cannot be separated — including when one spells pH `7.4` and the other
`7.40`, because `Slot.serialised()` emits the canonical numeric key and excludes
the `compare=False` original spellings. That invariant is tested directly.

### Measured: partition, pilot, full run

| Phase | Time | Peak RSS |
| --- | ---: | ---: |
| Partition A (3,136,836 rows) | 14.2s | — |
| Partition B (3,233,963 rows) | 15.1s | **0.05 GB** |
| Pilot: largest shard pair alone | 3s | **0.89 GB** |
| Full diff, 32 shard pairs | 100.8s | **0.90 GB** |

Partitioning streams one row at a time, which is why its peak is 0.05 GB. The
full diff peaks at **0.90 GB against the 25.3 GB the unsharded call would need —
a 28× reduction**, with 15.1 GiB of headroom on this machine.

### Shard sizes, and the budget

The declared budget is **400,000 observations per shard pair**, sized from the
per-observation cost. At 32 shards the measured pair sizes are:

| | Observations |
| --- | ---: |
| Minimum pair | 197,370 |
| Median pair | 199,001 |
| **Maximum pair** | **200,060** (50% of budget) |

BLAKE2b distributed 6.37M observations across 32 shards within a 1.4% spread, so
no repartition was needed. Had a shard exceeded the budget, `diff_sharded` raises
`ShardTooLarge` **before either file is materialised** — the check runs on a
counted byte scan while nothing is resident, and the reads that follow are
themselves bounded so a stale or wrong count cannot slip past. The remedy is a
larger shard count, which moves whole slots and never splits one. (The first
revision checked the budget *after* loading both files, which is checking after
the allocation that would exhaust memory has already happened; see §0.)

**The pilot corrected my own estimate.** The budget was sized at ~3,965 bytes per
observation, extrapolated from a 100,000-row sample. The pilot measured **4,674
bytes** on the largest real shard — 18% higher. The budget still holds (400,000 ×
4,674 ≈ 1.87 GB), but the margin is smaller than the estimate implied, which is
the reason for measuring the worst case rather than the average.

## 2. Equivalence, proved before the corpus run

The sharded result is the only result anyone will see, so "it should be the same"
is not good enough. `tests/test_m11d_shard_equivalence.py` (**88 tests**) compares
sharded against unsharded on ten fixtures reaching every classification branch:

count changes, withdrawals, correction candidates by entry DOI, correction
candidates by surrogate, identifier conflicts, ambiguous many-to-many surplus,
one-to-one-undecidable, numeric normalisation (`12`/`12.0`, `7.4`/`7.40`,
`.50`/`0.5`), a measurement-type change, and a 41-row many-slot case so sharding
actually distributes.

Each is checked at **shard counts 1, 2, 3, 7 and 16**, under **shuffled input on
both sides**, and compared on the **canonical detail** — additions, removals,
unresolved rows, correction candidates, identifier conflicts and ambiguous slots,
field by field — not only on summary totals. Two diffs can agree on how many rows
were added and disagree on which; the summary would not show it.

Also asserted: the result does not change with the shard count (a configuration
choice must not change a scientific result), and every observation is accounted
for at every shard count.

## 3. Endpoint boundaries preserved

All four measurement types were matched in **one pass**, with the type retained in
every detail record and the counts reported separately. The type lives in `Value`,
not in `Slot`, so a measurement whose type changed between snapshots stays at one
slot with two values — a type-filtered diff would instead report it as an
unexplained removal in one run and an unexplained addition in another.

**Nothing was fed to the Ki evaluation harness**, and **pair eligibility was not
computed**. Eligibility cannot be derived inside a slot shard: one compound–target
pair may have evidence at several slots and therefore in several shards, so a
per-shard eligibility figure would be wrong by construction. The eligible
new-pair count needs a later, explicitly scoped Ki aggregation through the bridge
and endpoint rules — **checklist item 8 stays OPEN**.

## 4. The diff

### Observations by measurement type

| Type | A (202601) | B (202609) | Unchanged | Added | Removed | Net |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| KI | 614,755 | 619,931 | 585,978 | 33,953 | 28,777 | +5,176 |
| IC50 | 2,117,146 | 2,212,313 | 2,031,890 | 180,423 | 85,256 | +95,167 |
| KD | 124,499 | 131,420 | 120,115 | 11,305 | 4,384 | +6,921 |
| EC50 | 280,436 | 270,299 | 257,613 | 12,686 | 22,823 | **−10,137** |
| **Total** | **3,136,836** | **3,233,963** | **2,995,596** | **238,367** | **141,240** | **+97,127** |

### Input accounting

| Identity | Reconstructed | Input | Reconciles |
| --- | ---: | ---: | --- |
| A = unchanged + removals | 3,136,836 | 3,136,836 | **yes** |
| B = unchanged + additions | 3,233,963 | 3,233,963 | **yes** |

Exact on both sides, and the per-type columns sum to the overall figures. The
partition reconciles too: rows read equals rows written equals the sum over
shards, on both sides, and each matches the digest-verified export's own row
count.

A second identity closes as well. A linked correction consumes one surplus
removal and one surplus addition, which are then *not* reported as unresolved:

- additions 238,367 = unresolved 238,266 + corrections 101 ✓
- removals 141,240 = unresolved 141,139 + corrections 101 ✓

### Slots, corrections, conflicts

| | Count |
| --- | ---: |
| New slots (absent from A) | 212,307 |
| Removed slots (absent from B) | 120,054 |
| **Correction candidates** | **101** (all linked by entry DOI) |
| **Identifier conflicts** | **1** |
| Ambiguous many-to-many slots | 7 |
| Unresolved additions | 238,266 |
| Unresolved removals | 141,139 |

**Corrections are vanishingly rare here, and that is a structural finding rather
than a null result.** A correction can only be inferred at a slot carrying surplus
on *both* sides, and only **109 slots** in 6.37M observations do: 102 one-to-one
and 7 many-to-many. Almost every addition and removal is one-sided at its slot,
so it was never a link candidate. Correction links remain **provisional**, as the
design requires.

The 101 corrections are IC50 75, KI 12, KD 10, EC50 4, and **none changed
measurement type**. One is a sentinel being repaired: `KI = 99999999999999 nM` in
A becomes `KI = 10000` in B at the same slot, linked by entry DOI
`10.7270/Q2QJ7M2N`.

**The single identifier conflict is itself a type change the matcher refused to
link**: `IC50 = 999000` removed, `EC50 = 999000` added at one slot, with entry
DOIs `10.7270/Q2MS3R5M` and `10.7270/Q2RV0M49` that disagree. The surrogate did
not agree either. Refusing is correct — two externally minted identifiers that
disagree are positive evidence *against* the rows being the same measurement —
and it is recorded separately so it can be counted rather than vanishing into the
unresolved pile.

## 5. Identifier agreement, on two defined populations

Both figures are agreement **within a stated population**. Neither is proof of
global identifier stability.

### Population 1 — matcher link candidates (one surplus removal, one surplus addition)

| | Count |
| --- | ---: |
| One-to-one slots | 102 |
| Entry DOI present on both sides | 102 |
| **Agreed** | **101 (99.02%)** |
| Disagreed | 1 |
| `reactant_set_id` consulted | **0** |

The surrogate was never consulted: every one-to-one candidate had an entry DOI on
both sides, so the entry DOI always decided. This population says nothing about
`reactant_set_id`.

### Population 2 — cross-slot candidate correspondences

`publication_ref` is part of the slot, so a row that gains a PMID or patent number
leaves its old slot and reappears at a new one. The matcher reports that as one
removal plus one addition and is right to — corrections are inferred only within a
slot — so these pairs are never link candidates and Population 1 cannot describe
them.

Grouping is on (compound, target, measurement type, canonical value) and slot
inequality only. **Neither identifier is consulted to form a pairing**, so
measuring their agreement afterwards is not circular. Each occurrence
participates **at most once**.

A group is **unambiguous** when it holds exactly one removal record and one
addition record at different slots: the correspondence is then *forced* rather
than chosen, and identifier agreement is well defined. Groups with more on either
side are **ambiguous**; no correspondence is asserted for them.

| Unambiguous groups | |
| --- | ---: |
| Groups | 28,231 |
| Matched occurrences | 28,231 |
| Unmatched removal / addition occurrences | 0 / 0 |
| of which KI / IC50 / KD / EC50 | 4,031 / 20,679 / 1,858 / 1,663 |
| Gained an attribution | **28,129** |
| Changed from one attribution to another | 102 |
| Lost an attribution | **0** |
| Moved on a non-publication slot field | **0** |
| Entry DOI agreed | **28,194 / 28,231 (99.87%)** |
| `reactant_set_id` agreed | **28,194 / 28,231 (99.87%)** |
| Pairs with locators on both sides | 28,231 |

| Ambiguous groups — counted, never paired | |
| --- | ---: |
| Groups | 1,673 |
| Removal / addition occurrences | 2,468 / 3,331 |
| Pairable upper bound | 2,260 |
| Groups whose candidate identifiers disagree | **1,673 (all of them)** |
| Commonest shapes (removal×addition records) | `1x2` 1,003, `2x2` 500, `3x1` 72, `2x1` 48 |

Occurrence accounting closes: 28,231 matched + 0 unmatched + 2,468 ambiguous =
**30,699 removal occurrences** considered, and 28,231 + 0 + 3,331 = **31,562
addition occurrences**. Every occurrence in a group present on both sides is
counted exactly once.

**A positional-pairing sensitivity, labelled algorithm-dependent.** If the
ambiguous groups *were* paired by lexicographic greedy matching, 2,260 pairs would
form at 85.40% entry-DOI and 85.27% surrogate agreement. That is deterministic but
it is an artifact of the sort order, not evidence about any individual
observation. It is reported only so a reader can see how much the ambiguous
groups could move the headline, and it is the reason they must not be blended into
it.

**What this licenses.** Identifier agreement within the unambiguous population.
This is the **first measurement of `reactant_set_id` cross-release stability** —
99.87%, equal to the entry DOI's on this population. It is **not** proof of global
stability: the population is selected by value equality, which is precisely where
stability is easiest to observe, and it says nothing about rows whose value
changed or which appear in only one snapshot. Checklist item 13 is **advanced, not
closed**.

**These are candidate correspondences, not established re-attributions.** Equal
compound, target, operator and canonical value at two different slots is
consistent with one measurement reappearing under a new attribution — and equally
consistent with two independent measurements that agree. Nothing in the files
distinguishes the two, so "moved" below is shorthand for the counting operation
and not a claim about what happened to a row. The individually source-verified
case in §6 is evidence about that case alone and is reported separately.

## 6. The EC50 decline

A 280,436 → B 270,299, net **−10,137**, decomposed as **12,686 additions against
22,823 removals**. Where the removals sit:

| | Observations |
| --- | ---: |
| Removed at an `unattributed` slot | 12,549 (55.0%) |
| Removed at an attributed slot | 10,274 |

### One individually source-verified case

Source-verified on both sides, and an **unambiguous** group — one removal record,
one addition record, so the correspondence is forced:

| | A (202601) | B (202609) |
| --- | --- | --- |
| Compound | `ACEHBQPPDDGCGZ-UHFFFAOYSA-N` | same |
| Target | `2B9B5928CCA6AC49…` | same |
| Value | `EC50 = 183` | same |
| Entry DOI | `10.7270/Q2ZG6XSW` | same |
| `reactant_set_id` | `391580` | same |
| Publication | **`unattributed`** (PMID null) | **`pmid:20126400`** |
| Locator | `BindingDB/202601/all#391578` | `BindingDB/202609/all#390114` |

Both locators were resolved back to their raw source rows by read-only query, and
the raw payloads confirm it: identical `EC50 (nM) = 183`, identical entry DOI,
identical reactant set id, PMID absent in January and `20126400` in September.

**This is evidence about this case, and it is stronger than value equality**: the
two rows share an entry DOI *and* a reactant set id, not merely a compound,
target, operator and value. Even so, the raw bytes do not state that the two rows
report the same experiment — that remains an inference, well supported here and
not transferable. It is **not** a population-wide claim, and the counted figures
below are measured separately rather than extrapolated from it.

### Counted, and net-neutral by construction

| | Observations | Share of 22,823 removals |
| --- | ---: | ---: |
| Matched in unambiguous cross-slot groups | 1,663 | 7.29% |
| Additional ceiling from ambiguous groups | ≤ 87 | — |
| **Re-attribution ceiling** | **≤ 1,750** | **≤ 7.67%** |

**Cross-slot candidates cannot explain the net decline at all.** A matched
candidate pairs one removal *with* one addition, so it is net-neutral by
construction whatever the correspondence means:
pairing 1,663 removals with 1,663 additions leaves 21,160 removals against 11,023
additions and the net is still exactly −10,137. The earlier revision of this
report said the mechanism "accounts for 7.7% of removals", which was true and
beside the point — it invited the reading that this was a partial explanation of
the decline, and a net-neutral mechanism is not a partial explanation of a *net*
change in either direction.

So a candidate correspondence exists for under 8% of the removals, it is
source-verified in at least one individual case, and it contributes **zero** to
the −10,137.

### What matching alone cannot settle

No correction candidate changed measurement type, so reclassification-in-place is
not visible in the correction population either. The one type change the matcher
saw (`IC50 → EC50`) was refused on disagreeing entry DOIs and would have been an
EC50 *gain*.

**No causal explanation is claimed.** Matching establishes that 22,823 EC50
observations present in January are absent from September at their slot, and
bounds the share that reappear elsewhere with the same compound, target and value
at under 8%. Distinguishing withdrawal from re-curation, from a change in a slot
field we have not isolated, requires evidence this step does not have.

## 7. Verification

| | |
| --- | --- |
| Input digests | verified **before** processing: `0b67b64b…` (A), `2b2b4ad8…` (B) |
| Matcher normalisation version | `m11-normalise-v2` |
| Shard version / hash | `m11d/shard/v1` / `blake2b-64` |
| Shard configuration | 32 shards, budget 400,000 observations per pair |
| Runtime | partition 29.3s, diff 100.8s |
| Peak RSS | partition 0.05 GB, diff 0.90 GB (16 GiB machine) |
| Input accounting | exact on both sides, and per measurement type |
| Accepted corpus | unchanged — 202609 `activity` still 3,233,963; all five releases' counts unchanged |
| Acquisition rows 1203–1205 | still unparsed, zero rows |
| Accepted manifests | byte-identical (`f38488aa…`, `0a43f7d4…`) |
| Exports | byte-identical to their recorded digests |
| Archives preserved | M8 v4, M9 v4, M10 v3, M11b v3, M11c ingest v1 |
| **Full suite** | **980 tests, 0 failures, 0 errors, 0 skipped, 240s**. M11d contributes 121: 88 shard-equivalence, 19 record, 14 cross-slot pairing. Ruff clean across 155 files. |
| Main-diff figures after the correction pass | **unchanged** — all nine detail files byte-identical by digest, and every total, reconciliation and per-type count equal to the untouched run record |

### Detail files, referenced by digest

| Stream | Records | Size | SHA-256 |
| --- | ---: | ---: | --- |
| `additions` | 238,367 | 102.2 MB | `2fc6bfae5c157ceb…` |
| `unresolved_additions` | 238,266 | 102.1 MB | `5deabb5b1dbeeaac…` |
| `new_slots` | 212,307 | 34.0 MB | `3f588ca92f42ba1a…` |
| `removals` | 141,240 | 61.1 MB | `b9f558e43ff0fd45…` |
| `unresolved_removals` | 141,139 | 61.1 MB | `2ab1235f6097d938…` |
| `removed_slots` | 120,054 | 19.7 MB | `3206f20e6bcf0546…` |
| `correction_candidates` | 101 | 0.04 MB | `58988361e71833fc…` |
| `identifier_conflicts` | 1 | <0.01 MB | `4ec402817efca9e4…` |
| `ambiguous_slots` | 7 | <0.01 MB | `9e744975d46de52e…` |

Every detail record carries its measurement type, the canonical **and** original
value spellings, the original pH and temperature spellings, both identifiers, a
count, and the source locators. The small files and the traceable examples are in
the review ZIP; the large ones are referenced by digest.

## 8. What this step did not do

- **No Ki aggregation and no pair eligibility.** Eligibility is not computable
  within a slot shard, because one compound–target pair may have evidence in
  several shards. **Checklist item 8 stays OPEN.**
- No endpoint build, features, fitting, evaluation scores or docking.
- No downloads.
- The freeze is **not signed**.

## 9. Proposed next step: Ki-only semantics validation

Bounded, and deliberately narrower than this step:

1. **Restrict to KI.** 614,755 observations in A and 619,931 in B, already
   separated here. IC50, KD and EC50 stay out of the harness, unmerged.
2. **Aggregate to pairs through the existing bridge and endpoint rules**, so
   censoring, interval intersection, discordance and the threshold boundary
   behave exactly as M4 defines them. Aggregation is per compound–target pair and
   therefore runs **across** shards — the shard layout is an input-reading detail,
   not a unit of eligibility.
3. **Build the three representations** of §5.2a0 — `in_a`, complete actual `in_b`,
   and the eligible `increment` — and check them against the eight worked examples
   of §7a on real data, which is what checklist item **10b** needs and synthetic
   fixtures cannot supply.
4. **Then, and only then, declare a minimum eligible new-pair count** (item 8),
   sized against the measured eligible fraction rather than guessed before it.
5. Report with the provisional-pooling banner and the exploratory label intact.
   Still no fitting, no scores, no docking.

The honest expectation: of 33,953 added KI observations, the eligible fraction
after censoring, discordance and conflict screening is unknown, and it is the
number that decides whether an as-of evaluation on this snapshot pair is worth
running at all.

One caveat to carry in. **4,031 of those added KI observations** sit in
unambiguous cross-slot candidate re-attributions (§5), with at most 1,099 more in
ambiguous groups — so some of the increment is the same measurement under a new
attribution rather than new evidence. The slot-level diff cannot net that out,
because a re-attributed row is a genuine addition *at its slot*. Eligibility is
computed over **pairs**, so the aggregation step is where it can be, and the
eligible increment should be reported both with and without those candidates.
