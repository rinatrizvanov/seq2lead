# M3 — curation

Generated 2026-09-29 12:52 UTC.

The derived layer: `compound`, `target`, `assay`, `publication` and a one-row-per-measurement `activity` table, built from the pinned raw releases. **No aggregation happens here** — Ki, IC50, Kd and EC50 keep their own rows, and pair-level labels (`pair_regression`, `pair_label`) are M4 and deliberately absent.

> **Endpoint status: `provisional_pooled_for_exploratory_benchmark`**
>
> Ki is pooled across assays **provisionally, for exploratory benchmarking only**. The pre-declared decision rule returned `pool`, but it compared medians and was blind to a tail in which a quarter of assay-spanning pairs differ by more than tenfold. Pooling is therefore an operating assumption carried forward under protest, not a validated conclusion. Any result computed on pooled Ki inherits this limitation and must state it.
>
> See [`assay_variance.md`](assay_variance.md) for the evidence and its limits.

## Source releases

| Subset | Release | Rows loaded | SHA-256 |
| --- | --- | --- | --- |
| `pdspki` | 12 | 27,715 | `5a212e99f495e5c1…` |
| `rsid_eaids` | 90 | 3,179,005 | `0c967f8a34ab21d7…` |
| `assays` | 91 | 224,430 | `b9949b6271de7a3d…` |
| `target_sequences` | 94 | 11,527 | `811507d7d61175a3…` |
| `all` | 117 | 3,237,046 | `4c04e0fec46fadab…` |

Curator version: `m3/v3+rdkit-2026.03.6/cleanup+fragment-parent+uncharge/v1`

## Assay join, measured before it was implemented

Assay descriptions are not in the measurement file. Reaching them is a two-hop join, and the shape of that join was measured first — scoped by `source_release_id`, because `raw_record` holds both mapping artifacts and an unscoped join would silently cross them.

```
raw_measurement 'BindingDB Reactant_set_id'
   -> rsid_eaids 'REACTANT_SET_ID' -> 'ENTRYID_ASSAYID'   e.g. '285_1'
   -> assays ('ENTRYID','ASSAYID') = ('285','1')          -> DESCRIPTION
```

| Hop | Measurement | Count |
| --- | --- | --- |
| 0 | measurement rows | 3,237,046 |
| 0 | with a reactant id | 3,237,046 (100.00%) |
| 0 | distinct reactant ids | 3,237,046 (1:1 with rows) |
| 1 | `rsid_eaids` rows / distinct keys | 3,179,005 / 3,179,005 |
| 1 | **matched** | 3,178,980 (98.21%) |
| 1 | unmatched | 58,066 |
| 1 | rows matching >1 mapping | **0** |
| 2 | `assays` rows / distinct keys | 224,430 / 224,430 |
| 2 | reach an assay description | 3,178,980 |
| 2 | lost at this hop | 0 |
| 2 | rows matching >1 assay | **0** |

**The join cannot multiply activity rows.** No key is duplicated in either mapping artifact, no measurement matches more than one mapping or more than one assay, and a LEFT JOIN across both hops returns exactly 3,237,046 rows — the measurement row count. This was verified before any curation code was written, not assumed.

Orphans, recorded rather than ignored: 25 `rsid_eaids` rows reference reactant ids absent from the measurement file; 0 assay rows are never referenced; 0 referenced assay keys have no assay row.

## Reconciliation

| | Count |
| --- | --- |
| Raw measurement rows in the release | 3,237,046 |
| Curated (>=1 activity) | 3,228,136 |
| Excluded with a rule code | 8,910 |
| **curated + excluded** | **3,237,046** |
| **Reconciles** | **yes** |
| `activity` rows written | 3,233,963 |

`activity` exceeds the curated row count because a measurement row carrying more than one affinity type yields one row per type. The `(raw_measurement_id, measurement_type)` unique key makes a double-write impossible.

### Exclusions by rule

| Rule | Rows | Share of release |
| --- | --- | --- |
| `invalid_smiles` | 5,896 | 0.182% |
| `no_measurement_value` | 2,939 | 0.091% |
| `missing_structure` | 75 | 0.002% |

## Measurement types and relations

Kept distinct by construction. These are counts of what the source says, not a curation decision. M4 applies a predeclared threshold; whether Ki may be pooled across assays at all is M5's question.

| Type | Total | `=` | `<` | `<=` | `>` | `>=` | `~` | `?` | no magnitude |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| KI | 619,931 | 539,197 | 18,805 | 0 | 61,929 | 0 | 0 | 0 | 0 |
| IC50 | 2,212,313 | 1,745,453 | 186,114 | 0 | 280,746 | 0 | 0 | 0 | 0 |
| KD | 131,420 | 82,407 | 8,731 | 0 | 40,282 | 0 | 0 | 0 | 0 |
| EC50 | 270,299 | 216,390 | 11,042 | 0 | 42,867 | 0 | 0 | 0 | 0 |

### Inclusive and exclusive bounds are kept apart

An earlier revision collapsed `>=` into `>` and `<=` into `<`. That is wrong, and wrong precisely where it matters. Taking the activity threshold as pKi 6.0 -- Ki = 1,000 nM -- a record reading `>1000` excludes equality, so its whole admissible range sits below the threshold and it is decisively **inactive**. A record reading `>=1000` admits exactly 1,000 nM, which is pKi 6.0 and therefore **active**, so it cannot decide at all. Collapsing the two converts ambiguity into a confident label.

The contract M4 will aggregate over, at Ki = 1,000 nM:

| Record | Constraint on pKi | Verdict |
| --- | --- | --- |
| `= 1000` | `pKi == 6.0` | active |
| `< 1000` | `pKi > 6.0` | active |
| `<= 1000` | `pKi >= 6.0` | active |
| `> 1000` | `pKi < 6.0` | inactive |
| `>= 1000` | `pKi <= 6.0` | **ambiguous** |
| `~ 1000` | unspecified | ambiguous |
| `? 1000` | operator not understood | ambiguous |

`activity.relation` holds the canonical operator, `relation_raw` the operator exactly as the source wrote it, and `bound_inclusive` states inclusivity explicitly rather than leaving it to be inferred. Approximate (`~`) and unrecognized (`?`) relations are never folded into `=`.

Worth recording: `<=`, `>=`, `~`, `?` do not occur in this release at all, so the collapse had no effect on these numbers. That is a property of this data, not of the old code.

All values carry the unit recorded on the source column: **nM**.

## Assay coverage of the curated activities

| Status | Activities | Share |
| --- | --- | --- |
| `matched` | 3,176,034 | 98.21% |
| `no_rsid_match` | 57,929 | 1.79% |

**57,929 activity rows (57,921 measurement rows) are retained with no assay description** — 1.79% of the curated layer. They keep `assay_id IS NULL` and a status saying why. Nothing is dropped for want of assay context: a measurement without its assay text is still a measurement, and silently discarding it would bias the curated set toward whatever BindingDB happens to have mapped.

## Benchmark scope

| | Activities | Share |
| --- | --- | --- |
| In single-protein scope | 3,048,153 | 94.25% |
| Out of scope (retained, flagged) | 185,810 | 5.75% |

| Reason | Activities |
| --- | --- |
| `multi_chain` | 185,810 |

Out-of-scope rows are **flagged, not deleted**. The single-protein scope is a benchmark decision, and keeping the rows means it stays reversible and auditable rather than baked into the data.

## Entities

| Table | Rows |
| --- | --- |
| `compound` (standardized parents) | 1,424,672 |
| `compound_source` (source structures mapped) | 1,430,560 |
| `target` (distinct sequences) | 11,013 |
| `target_alias` | 9,997 |
| `assay` | 224,430 |
| `assay_link` | 3,179,005 |
| `publication` | 57,168 |

## Structure standardization

Version string: `rdkit-2026.03.6/cleanup+fragment-parent+uncharge/v1`.

Changing any step changes that string, which is part of the `compound` uniqueness key and of the cache key in `compound_source`, so results from two different pipelines can never silently mix.

| Step | What it does | Why it is here |
| --- | --- | --- |
| `MolFromSmiles` | parse and sanitize | a structure RDKit cannot read is excluded as `invalid_smiles` rather than guessed at |
| `Cleanup` | normalize functional groups, disconnect metals, reionize | removes drawing conventions that would otherwise split one substance in two |
| `FragmentParent` | keep the largest organic fragment | strips salts and solvates, so a hydrochloride and its free base are one compound |
| `Uncharger` | neutralize what can be neutralized | a carboxylate and its acid are one compound, not two |

**Deliberately not done: tautomer canonicalization.** RDKit's enumeration is slow, and its canonical choice is a convention rather than a chemical fact, so tautomers remain distinct compounds here. Recorded as a limitation, not hidden.

### Traceability to the original structure

Standardization is lossy by design, so nothing is allowed to depend on reversing it. `compound_source` keeps, for every source structure seen:

| Column | Holds |
| --- | --- |
| `source_inchikey` | the compound identifier **as BindingDB gave it** |
| `source_smiles` | the **original SMILES**, unmodified |
| `compound_id` | the standardized parent it resolved to |
| `standardizer_version` | which pipeline produced that mapping |

Every curated activity therefore reaches its original structure and source identifier in one join, and reaches the untouched source row via `activity.raw_measurement_id` in another. No standardization decision is irreversible at the record level.

### Collisions

| | Count |
| --- | --- |
| Distinct source structures standardized | 1,430,560 |
| Distinct standardized parents (`compound`) | 1,424,672 |
| **Source structures collapsed onto a shared parent** | **5,888** |
| Parents carrying more than one source structure | 5,720 |

Largest collision groups — salts, solvates and charge variants of one substance:

| Standardized parent | Source structures |
| --- | --- |
| `LELOWRISYMNNSU-UHFFFAOYSA-N` | 6 |
| `NBIIXXVUZAFLBC-UHFFFAOYSA-N` | 5 |
| `NQKDJWKHSVVCRZ-UHFFFAOYSA-N` | 4 |
| `QAOWNCQODCNURD-UHFFFAOYSA-N` | 4 |
| `NTNWOCRCBQPEKQ-YFKPBYRVSA-N` | 4 |

A collision is the pipeline working, not a fault: two source records differing only by counter-ion, solvate or protonation become one compound. Both remain individually addressable in `compound_source`.

Target sequence length: min 7, median 453, max 34,350. 1,299 exceed the 1,022-residue ESM-2 limit — the M7 long-sequence decision recorded in `profile.md`.

## Unresolved ambiguities

| # | Issue |
| --- | --- |
| 1 | **Tautomers are distinct compounds.** The standardizer does not canonicalize tautomers: RDKit's enumeration is slow and its canonical choice is a convention rather than a chemical fact. Two tautomers of one substance therefore get two `compound` rows. |
| 2 | **Stereochemistry is preserved as given.** No attempt is made to reconcile a racemate with its enantiomers, or to infer unspecified centres. Whether that is right depends on the assay, which M5 will have to look at. |
| 3 | **`publication` identity is weak.** Rows are keyed on (PMID, DOI, patent, date) with empty strings for missing parts, so one paper recorded inconsistently across entries becomes more than one publication row. |
| 4 | **Assay text decoding is one-way.** `description_text` is `html.unescape(description_raw)`; both are stored so the transform is auditable, but entities that were themselves literal text are now indistinguishable from decoded ones. |
| 5 | **Only chain 1 defines the target.** Multi-chain rows are flagged out of scope, but the sequence recorded for them is still chain 1's, which does not represent the complex. Do not read `target_id` on an out-of-scope row as the thing that was assayed. |
| 6 | **Target sequence length spans implausible extremes** — 7 residues at one end and 34,350 at the other. Neither is a plausible single druggable protein: the short one cannot form a pocket and the long one is likely a concatenated or mis-parsed entry. Both are retained without judgement here, because a length cutoff is an outcome-independent scope choice that belongs to M5 with the distribution in front of us. |
| 7 | **`ph` and `Temp (C)` are carried verbatim and largely empty.** The M1 pilot found them 100% unpopulated; how much the full release populates them is a question for the M5 assay-comparability analysis. |

