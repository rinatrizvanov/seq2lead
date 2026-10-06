# DRAFT inquiry — NOT SENT

Addressed to: BindingDB (bindingdb@gmail.com / the contact on
<https://www.bindingdb.org>) and the ChEMBL helpdesk
(<https://www.ebi.ac.uk/chembl/> → Contact / chembl-help@ebi.ac.uk).

**Status: draft only.** It has not been sent, and sending it is the owner's
decision. Nothing in this repository treats the absence of an answer as either
permission or prohibition.

---

## Subject

Does CC BY-SA 3.0 share-alike attach to model predictions and fitted statistics
derived from ChEMBL-sourced BindingDB rows?

## Body

We maintain a research repository that evaluates protein–ligand ranking against
BindingDB measurements. We want to redistribute our evaluation artifacts and are
trying to apply your terms correctly rather than guess.

**What we understand your terms to be.** BindingDB states that data curated by
BindingDB staff is provided under Creative Commons Attribution 3.0, and that data
imported from ChEMBL is provided under Creative Commons
Attribution-Share Alike 3.0 Unported. ChEMBL states the same licence for its own
data. We read the per-row `Curation/DataSource` field in the BindingDB export to
tell the two apart; in the release we work with, that field records `ChEMBL` for
1,658,927 of 3,264,761 rows.

**What we intend to redistribute.** A public, private-until-approved repository
containing:

1. **Compound and target identifier sets.** InChIKeys and SHA-256 sequence
   hashes, with internal surrogate ids. No structures, no measured values.
   Largest file: 251,917 InChIKeys.
2. **Two files containing chemical structures.** Canonical SMILES for 3,422
   compounds (`recompute-compounds.json`) and for a 600-member docking cohort
   together with aggregated pKi values and activity labels
   (`m10_cohort.json`).
3. **Derived evaluation tables.** For 26,444 compound–target pairs: an activity
   label, a population stratum and eligibility flags. Labels are derived from
   measured pKi values against a declared 1 µM threshold; no raw measurement
   value is reproduced.
4. **Model predictions.** One floating-point score per pair per fitted model,
   26,444 rows × 22 model-seed columns, in a compressed array whose index is the
   16,795 InChIKeys of the evaluated compounds.
5. **Fitted summary statistics.** A 1 × 1280 mean vector and a 1 × 1280 scale
   vector computed over protein-language-model embeddings of the training
   proteins, plus the count of rows they were fitted on. These contain no
   identifier, no structure and no measured value.

Items 1–3 we take to be adaptations of your data and intend to distribute under
CC BY-SA 3.0 with the attribution set out below, treating the stricter licence as
governing any file whose compounds include ChEMBL-sourced rows.

**Our question concerns items 4 and 5.** Neither your published terms nor the
CC BY-SA 3.0 text addresses statistical or model-derived outputs. Specifically:

- Do you consider **model predictions** (item 4) to be an Adaptation of the
  licensed data, such that share-alike attaches — given that the scores are
  outputs of a model fitted on the data, and that the file is indexed by
  compound identifiers drawn from it?
- Do you consider **aggregate fitted statistics** (item 5) to be an Adaptation,
  given that they are means and standard deviations over representations of
  protein sequences and contain no identifier or measured value?
- If share-alike does attach, is attribution of the form below sufficient, or do
  you require a per-row or per-release ChEMBL identifier? BindingDB's export
  records the `ChEMBL` source label but not the ChEMBL release each row came
  from, so we cannot presently cite a specific release DOI for those rows.

**Attribution we propose to carry.** The BindingDB 2025 *Nucleic Acids Research*
reference (53:D1633–D1644); the archival deposit DOI `10.6075/J0V40W61` for our
historical snapshot; the pinned BindingDB 202609 release for our later snapshot;
and ChEMBL credited as the upstream source of the rows BindingDB attributes to
it, under CC BY-SA 3.0.

**What we are not asking for.** We are not seeking relicensing, an exception, or
permission to drop attribution. We would rather label the artifacts correctly.

Thank you.

---

## Exact affected content, for the record

| Item | File | Size | What it contains |
| --- | --- | ---: | --- |
| 4 | `data/asof/m11h/run-20261005T134842Z/predictions.npz` | 2,520,900 B | 26,444 pair rows; `pair` array of 16,795 InChIKeys; 22 float64 score columns |
| 5 | `data/asof/m11h/run-20261005T134842Z/protein-transform.npz` | 11,000 B | `mean` (1×1280), `scale` (1×1280), `n_fitted` (1) |

Items 1–3 are enumerated file by file in `DATA_LICENSE.draft`.

### How much ChEMBL-sourced data item 5 summarises

Item 5 carries no identifier, so its dependence on ChEMBL-sourced rows is a
property of the population it was fitted over, not of its bytes. That population
was resolved against BindingDB's own row-level `Curation/DataSource`. Its
`n_fitted` field, 353,957, is exactly the A-train pair count recorded as supplied
to fitting.

| | |
| --- | ---: |
| exact-Ki records behind those 353,957 pairs | 454,694 |
| of which `Curation/DataSource = ChEMBL` | 304,963 (67.07%) |
| pairs with at least one ChEMBL-sourced exact-Ki record | 251,811 (71.14%) |
| distinct training sequences touching a ChEMBL-sourced row | 2,613 of 3,466 (75.4%) |

Pending your answer we have labelled item 5 CC BY-SA 3.0, the same as item 4, so
that it is correctly licensed whichever way the question resolves. We are not
asserting that share-alike attaches; we are avoiding a permissive label we could
not withdraw if it turned out to be wrong.
