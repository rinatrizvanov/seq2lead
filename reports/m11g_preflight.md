# M11g — feature completion and execution preflight

**Scope: preparation only.** Nothing was fitted, no prediction metric was
computed, nothing was downloaded or docked, retrieval is **unbuilt**, evaluation
evidence display is **off**, and the confirmatory freeze is **not signed**. The
study remains **exploratory** and the **provisional-pooling** qualification
stands.

Contract: `configs/m11f_evaluation_contract.json` at **v5**
(`3600854abdd0…`), superseding v4. Manifest:
`configs/manifests/m11g_preflight.json` (`d018ba7533ec…`), which records that
digest and agrees with the file. Records: `data/asof/m11g/`. Review ZIP:
`seq2lead-m11g-preflight-review.zip`. Supersedes `reports/m11f_preflight.md`
(v2).

**Verdict: READY to fit**, subject to the qualifications above.

**Correction round 2 (v5).** Three further defects were found and closed, all
the same shape: *a value the run consumed arrived as a caller parameter beside a
digest that did not govern it*, so the verification was decorative. Each was
reproduced through `runner.run` with fitting mocked and every bound digest
matching — **all three reached the fit** — and each is now impossible by
construction rather than caught by a check. §2a has the detail and
`data/asof/m11g/binding-defects.json` the measurements. **No feature vector was
rebuilt**; this was a wiring correction, and the emitted dataset digests are
unchanged. The three
defects named in the request are closed (**E5–E7**, §2). Two more were found by
this step's own gates and closed: the runner refused its first real invocation
because the feature completion had covered the fitting roles only (**E8**, §4),
and M11f's `missing_or_unusable_feature: 0` turned out to mean "no filter was
applied" rather than "nothing was missing" (**E9**, §5). One defect was
introduced and fixed inside this step — the contract generator read its lineage
from the file it overwrites (§7). §8 lists seven items that remain open; **none
of them blocks fitting**, and five are qualifications on how a result may be
read rather than faults.

---

## 1. The missing feature population, completed

### Reuse is decided by content identity, and it mattered

The accepted caches are keyed by the **202609** schema's surrogate ids. Those
ids mean nothing in the 202601 schema, so reuse goes through the thing the
vector is actually a function of — canonical SMILES for a fingerprint, the
sequence for an embedding.

| A-role entities | Total | Reused | Recomputed |
| --- | ---: | ---: | ---: |
| Compounds | 249,323 | 245,901 | **3,422** |
| Targets | 3,766 | 3,753 | **13** |

The 3,422 compounds are **3,153** absent from the accepted cache plus **269**
whose canonical SMILES disagrees between the two schemas while the InChIKey
matches. That second group is the reason content identity is not a formality: of
19 such pairs checked, **18 produce a genuinely different Morgan fingerprint**.
The first is a tautomer — `…nc3nc[nH]c23` against `…nc3[nH]cnc23` — that
standard InChI normalises to one key. Reusing the cached vector there would have
been a silent error, not a saving.

### Reconciling against the figure in the request

The request named **3,817 compounds and 20 targets**. Those are M11f's
**per-role sums**: compounds with no 202609 id (2,942 train + 875 validation),
and targets unresolved (11 + 6 with no id, plus 2 + 1 missing from the cache).

Resolving by content identity gives a deduplicated population, and it is also a
**different** population — it adds the 269 SMILES disagreements, which *have* a
202609 id and would otherwise have been reused wrongly. Measured both ways:

| | Distinct computed | Per-role draw (train / validation / evaluation) | Sum |
| --- | ---: | --- | ---: |
| Compounds | 3,422 | 3,044 / 854 / 28 | 3,926 |
| Targets | 13 | 13 / 7 / 0 | 20 |

The sums exceed the distinct counts because an entity needed by two roles is
drawn twice and computed once. The 20 targets match the request exactly. (The
draws are smaller than v4's 3,193 / 934 because the roles are now derived over
the rows the loader keeps — see §2a. The 3,422 computed is unchanged, and
nothing was recomputed.)

### Extensions, registered under their own identities

A cache is its spec **and** its population, so a larger population cannot
inherit an accepted cache's identity. Each extension is its own artifact:

| | `ecfp4-compound-m11g-ext-8198510a392a` | `esm2-target-m11g-ext-77469cfc5652` |
| --- | --- | --- |
| Requested / written / unusable | 3,422 / 3,422 / **0** | 13 / 13 / **0** |
| Storage digest | `62594be41994…` | `cc7d1639a2fa…` |
| Size, time | 0.98 MB, 0.3 s | 0.07 MB, 5.6 s on `mps` |
| Keyed by | InChIKey | `sequence_sha256` |

Pinned settings used, unchanged: ECFP4 radius 2, 2048 bits, **chirality on**,
bit-packed to 256 `uint8`; ESM-2 `facebook/esm2_t33_650M_UR50D` at revision
`08e4846e5371…`, `mean_over_residues_excluding_special_tokens`, length policy
`full`, `max_length` 40000, float32, dim 1280. One of the 13 sequences exceeds
the 1,022-residue training window; it is **flagged, not truncated**, which keeps
the provisional length-policy qualification intact (§8 item 5 gives the measured
count across all roles).

**Accepted caches verified byte-for-byte unchanged:** `964471e0055f9178…`
(ECFP4), `1ecf4ced0d59d105…` (ESM-2).

**Nothing is imputed.** A vector that could not be computed is recorded as
absent. No zero vector and no mean substitute is written for any entity, and
`missing` and `unusable` are reported apart throughout: the first needs an
extension, the second needs the cache fixed.

---

## 2. The frozen contract, enforced on the runner path

Three defects were named in the request. All three were real.

### E5 — `prepare_fitting` permitted omitted preconditions

`expected_digests`, `artifacts` and `config` were keyword arguments defaulting
to `None`, and the body skipped verification when they were omitted. A caller
could reach the fit callbacks with **nothing checked**. They are now required
arguments, and a test asserts they have no defaults.

### E6 — `verify_preconditions` accepted an empty digest set

The digest loop had nothing to iterate, so "nothing was checked" read as
"everything verified". The previous suite **asserted this as intended
behaviour** — `verify_preconditions(expected_digests={}, …)` was a passing test.
An empty set is now refused, the complete pinned set is required, and an
unrecognised bound input is refused rather than ignored.

### E7 — the dimension and metric version were never compared

`projection_dim` and `metric_version` were required to *exist*. A run at
`projection_dim` 256 under `metric_version` `m8/v1` passed the gate and would
have produced numbers that cannot be placed beside M8/M9's. Every declared
setting is now compared to the frozen value.

### The production entry point

`seq2lead.asof.runner.run` is what a run calls. `prepare_fitting` stays an
orchestration primitive — its fit callables are parameters, which is what lets a
test inspect them — but it is no longer the thing production calls.

The pinned values live in one module, `seq2lead.asof.contract`, because holding
them in the checker *or* the runner lets the other drift. `tests/test_m11g_contract.py`
holds that module against the published contract file **in both directions**.

Gate order, and the order is the guarantee:

1. every declared execution setting, compared to the frozen value;
2. the complete pinned input set — **13 inputs** — each digest recomputed from
   the bytes on disk;
3. every feature binding loaded and its digests verified;
4. **complete coverage for every role**, missing and unusable counted apart;
5. the model-facing datasets loaded, with the verified entity set passed as a
   **per-role** feature allow-list;
6. any train/validation overlap refused;
7. transforms fitted on A-train, then the model on A-train;
8. the checkpoint selected on A-validation, and nothing else.

A failure at any gate raises before the first callback and before anything is
published.

---

## 2a. Nothing the run consumes is a parameter

Verifying an artifact's digest says nothing about a run that consumes something
else. Three parameters did exactly that. Each was reproduced through the real
entry point with a bound input set whose every digest matched:

| Reproduced defect | Reached the fit | What the published record showed |
| --- | --- | --- |
| **E10** `membership_path` was loaded while `artifacts["a_membership"]` was verified. A substituted file emitted **1 train pair instead of 2** and moved a regression target from pKi 7.0 to 9.0 | yes | every gate green; train digest changed, and nothing compared it to anything |
| **E11** `FeatureSource.reuse_map` came from the caller. **Swapping two entries** repointed those keys at each other's cached rows | yes | **nothing changed at all** — storage digests valid, coverage complete, and the emitted dataset digest byte-identical, because a dataset records pairs and targets rather than vectors |
| **E12** `roles` came from the caller, and only train and validation were required. Dropping the evaluation role skipped its coverage check; declaring 1 of 2 train compounds emitted 1 pair instead of 2 | yes | coverage "complete"; the dropped rows counted as `excluded_missing_feature` rather than refused |

E11 is the serious one. A swapped mapping changes **which vector every affected
compound is trained on** and leaves no trace in any digest the run reports — so
no amount of after-the-fact checking of the published record could detect it.
The fix is not a further check.

### The fix: derivation

`run` no longer takes `membership_path`, `feature_sources` or `roles`. Its
signature is `expected_digests`, `artifacts`, `config` and the three callables.
Everything it consumes is derived from the bound artifact set:

| Consumed | Derived from | Pinned as |
| --- | --- | --- |
| the membership | `artifacts["a_membership"]`, bytes **re-verified at the moment of consumption** | `a_membership` |
| the feature caches | the verified feature binding | `feature_binding` |
| the reuse maps | the verified resolution, each map's recorded digest checked against its own bytes | `feature_resolution` |
| the evaluation role's entities | a new pinned artifact | `evaluation_entities` |
| the fitting roles' entities | the verified membership, over exactly the rows the loader keeps | `a_membership` |
| the expected output | the pinned model-facing record, compared by count **and** digest | `model_facing_datasets` |

Three details carry the weight:

- **Re-verification at consumption.** A digest checked at the gate does not
  protect a file that changed since. The test proves this by swapping the file
  *from inside the run*, after the role derivation — changing it beforehand only
  exercises the gate, which an earlier version of that test did.
- **Cross-checking two artifacts.** The reuse map's digest is recorded in the
  resolution and its entry count in the binding, so a swapped map fails the
  first and a map whose record was refreshed fails the resolution's own pinned
  digest.
- **Roles derived over loader-kept rows.** The derived entity set now equals the
  emitted dataset's distinct entities **exactly** — asserted, not assumed. Wider
  would make the allow-list useless; narrower is the silent filtering that was
  the defect.

The pinned input set grows from 13 to **14** (`evaluation_entities`). The chain
terminates at `expected_digests`, which is the set the published contract
records: a caller free to rewrite both an artifact *and* its pinned digest
controls the run by definition, so the guarantee is that **no mutation leaving
the pinned set unchanged can alter what is fitted**.

### What changed in the numbers

| | v4 | v5 |
| --- | --- | --- |
| Train dataset digest | `dfe59a028fe0…` | `dfe59a028fe0…` — **unchanged** |
| Validation dataset digest | `f2e9febdad66…` | `f2e9febdad66…` — **unchanged** |
| Train pairs / validation pairs | 353,957 / 60,981 | 353,957 / 60,981 — unchanged |
| Headline cohort | 22,221 pairs, 992 targets, r@5 123, pos 0.697493 | identical |
| Strictest subgroup | 19,517 pairs, r@5 98, pos 0.728647 | identical |
| Recurrence agreement | 0 disagreements | 0 disagreements |
| C1–C5 | all met, no floor moved | all met, no floor moved |
| **Coverage requested, train** | 227,550 compounds / 3,663 targets | **209,187 / 3,466** |
| **Coverage requested, validation** | 62,217 / 2,533 | **53,870 / 2,394** |
| Coverage requested, evaluation | 17,373 / 1,060 | 17,373 / 1,060 — unchanged |
| Missing / unusable, every role | 0 / 0 | 0 / 0 |

**Only the coverage request counts moved**, and for a stated reason: v4 declared
role entities over *every* membership pair in the partition, while the runner now
derives them over the rows the loader keeps. The requested counts therefore equal
the emitted datasets' distinct entities exactly. Extension draws fall with them
(train ECFP4 3,193 → 3,044; validation 934 → 854) because fewer entities are
requested — **no vector was recomputed and no cache changed**. Dataset digests,
feature mappings, recurrence and feasibility are all unchanged.

---

## 3. The entry point exercised, with fitting mocked

`tests/test_m11g_runner.py` — **94 tests**. Several refusal tests leave the
membership file holding **unparseable JSON with a matching digest**. A gate that
fires raises `PreflightError`; a skipped gate reaches the derivation and raises
`JSONDecodeError`, so "refused early" is a measurement rather than a convention
— and one test asserts that `JSONDecodeError` directly. (The earlier version
pointed a `membership_path` at a nonexistent file; that parameter no longer
exists, which is itself the E10 fix.)

| Refusal demonstrated | How |
| --- | --- |
| **Missing features** | an evaluation-cohort entity with no vector; a membership entity withheld from the reuse map whose vector *is* in the cache — refused rather than resolved by a bare InChIKey match |
| **Under-declared roles** | structurally impossible: roles are derived. Also pinned evaluation entities missing a key, or holding an empty list (4 parametrised cases) |
| **Altered inputs** | each of the **14** bound inputs tampered with in turn (parametrised), plus an incomplete set, an empty set, a missing artifact, an unreadable one, and one that is not JSON |
| **Substituted inputs** | a membership changed mid-run, changed with its digest refreshed, or with a moved regression target; a swapped reuse map with and without its resolution record refreshed; an emitted dataset that is not the pinned one, by count or digest (6 parametrised cases) |
| **Incompatible bindings** | a binding declaring the logical 2048 width; a tampered accepted cache; a key in **both** accepted cache and extension; a binding missing either feature kind; a resolution missing a reuse map; a reuse map whose size disagrees with the binding, or with the resolution |
| **Changed configuration** | 13 single-setting mutations, plus each of the **22** declared settings dropped in turn, plus an empty configuration |
| **Gate ordering** | with both a bad setting and a tampered input, the *setting* is reported — a run is never told its digests are bad when it asked for the wrong experiment |

And the success path, asserted on what the mocks actually received:

- `fit_transforms` and `fit_model` were each called **once**, both with the
  **A-train** dataset, and the validation pair appears in neither;
- `select_checkpoint` was called once with the **A-validation** dataset and the
  fitted model, and that dataset reached no fit;
- B has **no parameter to arrive through** — checked by signature, not by hoping
  no caller passes it.

---

## 4. Coverage, measured by the runner

| Role / kind | Requested | Usable | Missing | Unusable | From accepted cache | From extension |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `evaluation` / ecfp4 | 17,373 | 17,373 | 0 | 0 | 17,345 | 28 |
| `evaluation` / esm2 | 1,060 | 1,060 | 0 | 0 | 1,060 | 0 |
| `train` / ecfp4 | 209,187 | 209,187 | 0 | 0 | 206,143 | 3,044 |
| `train` / esm2 | 3,466 | 3,466 | 0 | 0 | 3,453 | 13 |
| `validation` / ecfp4 | 53,870 | 53,870 | 0 | 0 | 53,016 | 854 |
| `validation` / esm2 | 2,394 | 2,394 | 0 | 0 | 2,387 | 7 |

Every role's entities are **derived** from the pinned artifacts, so these are not
counts a caller chose. The requested figures equal the emitted datasets' distinct
entities exactly for the fitting roles — asserted in the preflight, not assumed.

### E8 — the completion pass covered the fitting roles only

The first run of the runner **refused**: 5,786 evaluation-cohort compounds and
59 targets had no binding. The completion pass had resolved what the A roles
need, and the evaluation cohort is drawn from snapshot B.

That refusal is the gate doing its job, and the fix is not a cross-schema
shortcut: these identities come from snapshot B, which **is** 202609 — the
accepted cache's own schema — so each was resolved by looking its content
identity up in that schema. No surrogate id is equated across schemas, because
no crossing happens. All **17,373** compounds and **1,060** targets now bind:
11,329 were already in the A-role reuse map, 28 come from the A-role extension,
and **6,016** compounds and **59** targets were newly resolved — **none
unresolvable**.

The 6,016 exceeds the 5,786 the refusal reported because the two figures are
measured over different populations: the first run used the narrower
`is_scoreable` filter (16,792 compounds), and the corrected rule is the union
over arms of `has_increment` (17,373). Both counts are right for their
population; the wider one is what coverage is now checked against.

The evaluation entity rule is the **union over both increment arms** of pairs
with `has_increment` — a superset of either arm, so coverage covers every cell
that could be reported. On this corpus the union equals the declared arm's set
exactly, at 17,373 / 1,060, which is also the population M11f measured.

---

## 5. The datasets the runner emitted

Emitted by `seq2lead.asof.runner.run` to mocked callables — these are the exact
sets a run would consume, not a reconstruction.

| | Role | Pairs | Compounds | Sequences | Digest |
| --- | --- | ---: | ---: | ---: | --- |
| Fitting | `train` | **353,957** | 209,187 | 3,466 | `dfe59a028fe0…` |
| Selection | `validation` | **60,981** | 53,870 | 2,394 | `f2e9febdad66…` |

Exclusions, counted not dropped:

| | train | validation |
| --- | ---: | ---: |
| No regression target (decisive censored-only) | 55,653 | 9,883 |
| Not validation-RMSE eligible | 0 | 1,722 |
| **Missing or unusable feature** | **0** | **0** |

### E9 — the feature allow-list was never passed to the loader

M11f reported `missing_or_unusable_feature: 0` for both fitting roles, but **no
allow-list was ever passed to the loader**, so the figure meant "no filter was
applied" rather than "nothing was missing".

The runner derives the allow-list **per role** from the verified membership, so
the zero is a *consequence*, not independent evidence: the allow-list is the set
of entities the kept rows need, so it cannot exclude one of them. The guarantee
is therefore stronger than the zero looks — **every emitted pair has a verified
vector for both its compound and its target, or there is no run at all** — and
the zero alone would be a weak thing to rest on.

Since v5 the emitted datasets are additionally compared, by count **and** by
digest, against the pinned `model_facing_datasets` record before any callback.
That is the gate a substituted membership or an under-declared role cannot pass,
because both change what is emitted.

What the extension bought, measured by running the same loader with the
pre-extension allow-list:

| | Pairs excluded | Would remain |
| --- | ---: | ---: |
| `train` | **6,869** | 347,088 of 353,957 |
| `validation` | **1,205** | 59,776 of 60,981 |

The emitted digests are **identical** to M11f's published ones, which is the
evidence that the extension closed the gap completely rather than shifting it.

### The binding is self-consistent

This step regenerates three of the artifacts it binds, so the runner was run
**twice**: pass 1 bound the artifacts that existed going in, pass 2 re-bound to
what pass 1 wrote. Pass 2 emitted byte-identical datasets. A record that bound a
preflight its own run had superseded would not describe a reproducible run.

---

## 6. Effects on recurrence, cohorts and feasibility

### Recurrence — the agreement is measured, not structural

`stratify` reads the membership's partition and eligibility; the runner's
training set comes from the loader, which **also** applies the feature
allow-list. Those two agree only while coverage is complete, so it is measured:

| | Recurrent pairs | Not in the runner's training set |
| --- | ---: | ---: |
| With the extension | 4,184 | **0** — agrees |
| Without it | 4,184 | **45** — does not agree |

Without the extension, 45 pairs would have been called recurrent while the
loader had excluded them, **overstating exposure to fitting** and understating
the new-to-fitting headline by the same pairs. Record:
`data/asof/m11g/recurrence-agreement.json`.

### Cohort counts and feasibility — unchanged, and that is the finding

| | Headline cohort | Strictest subgroup |
| --- | --- | --- |
| Definition | `declared_increment × screened_primary`, new to fitting | …absent from A entirely |
| Pairs | 22,221 | 19,517 |
| Targets / compounds | 992 / 15,048 | 941 / 13,928 |
| Positive rate | 0.697493 | 0.728647 |
| Rankable @5 | 123 | 98 |

| Floor | Headline | Strictest |
| --- | --- | --- |
| C1 ≥ 50 rankable targets | **123** ✓ | **98** ✓ |
| C2 ≥ 2,000 pairs | **22,221** ✓ | **19,517** ✓ |
| C3 positive rate ∈ [0.20, 0.80] | **0.697** ✓ | **0.729** ✓ |
| C4 all four cells reported | ✓ | ✓ |
| C5 not argued from observation fraction | ✓ | ✓ |

Every figure is identical to v3, and **no floor was moved**. The reason is
measurable rather than lucky: the evaluation cohort's coverage was already
complete in M11f, and the fitting roles' feature exclusions are now zero, so
nothing moved. Had the extension not been built, the recurrence disagreement
above would have shifted 45 pairs between strata.

All four cells remain reported in `data/asof/m11g/cohort-preflight.json`. The
declared threshold stays **pKi 6.0** with 7.0 and 8.0 retained; the primary cell
stays `declared_increment × screened_primary`; the three sensitivities are
unchanged.

---

## 7. Verification

| Check | Result |
| --- | --- |
| Full suite | **1,279 passed**, 0 failures, 0 errors, 0 skipped, 265s — totals read from the JUnit XML, not from terminal output |
| `tests/test_m11g_runner.py` | 94 passed (was 72), rewritten for the derived entry point |
| `tests/test_m11f_datasets.py` | 50 passed (was 19), rewritten against the strict contract |
| `tests/test_m11g_contract.py` | 15 passed |
| Accepted ECFP4 cache digest | `964471e0055f9178…` — unchanged |
| Accepted ESM-2 cache digest | `1ecf4ced0d59d105…` — unchanged |
| Contract constants ↔ published contract | asserted both directions |
| Every bound input re-verified against disk | **14 of 14** match |
| Membership re-verified at consumption | asserted by swapping the file mid-run |
| Derived role entities vs emitted datasets | equal, asserted |
| Emitted datasets vs the pinned record | equal by count and digest |
| Reuse maps verified against the pinned resolution | ecfp4 251,917 / esm2 3,812 entries |
| Emitted dataset digests | identical to M11f's published values |
| Pass 2 datasets vs pass 1 | identical |
| Contract generator | idempotent over four consecutive runs |
| Nothing fitted | no model, no prediction metric, no download, no docking |

**The contract generator's lineage bug, twice.** It read its lineage from the
file it overwrites, so a second run recorded v4 as superseding **v4** and
replaced the carried-forward E1–E4 with E5–E9. I fixed that by sourcing E1–E4
from `configs/manifests/m11f_preflight.json`, which this step does not write —
and then **reintroduced the same bug** in the v5 round by reading E5–E9 back from
the contract, which duplicated them into both lists. Both rounds' items are now
literals the generator owns, `carried_forward` and `items` are asserted as exact
disjoint sets, and the generator is verified idempotent over four consecutive
runs. The timestamp lives in the manifest, not the contract, so the contract's
digest is stable across regenerations.

**Rewritten in the suite:** `test_m11f_datasets.py` collected 19 tests, of which
**12 failed** once the contract turned strict — they encoded the lax one, and one
of them asserted the empty-digest-set defect as intended behaviour. Its refusal
section is now 9 functions collecting **40** tests, including one parametrised
over each of the 22 declared settings and one over 10 single-setting changes. The
file collects 50 in total.

---

## 8. What remains unresolved

1. **The confirmatory freeze is unsigned**, and this step does not sign it.
2. **Snapshot B is our already-inspected 202609**, and the M8/M9 results were
   produced from it. Any score obtained under this contract is **exploratory**
   whatever it shows.
3. **Whether Ki may be pooled across assay contexts at all** is M5's open
   question. Every count here assumes the current pooling rule.
4. **Cross-slot candidates are candidates**, not proven re-attributions; both
   increment arms are carried for exactly that reason.
5. **The length policy is provisional.** `full`, with no truncation. **335** of
   the 3,825 distinct role targets exceed ESM-2's 1,022-residue training window
   — 319 in train, 222 in validation, 107 in evaluation, the longest 7,182
   residues — and they are **flagged, not truncated or excluded**. The
   sensitivity report is the check on that choice and has not been run. Record:
   `data/asof/m11g/length-window.json`.
6. **Retrieval stays unbuilt** and **evaluation evidence display stays off** —
   there is no partition-filtered evidence API, so evaluation mode would show no
   evidence at all.
7. **C1–C5 are pragmatic feasibility floors** for an exploratory study, accepted
   as such. They are not a power calculation and not a guarantee of informative
   results.

None of these blocks fitting. Items 1–4 and 7 are qualifications on how a result
may be read; 5 and 6 are scope held deliberately open.

---

## 9. Next step

Fitting is the next step and has not been taken. The runner refuses until every
gate above passes, and on this corpus they all do.
