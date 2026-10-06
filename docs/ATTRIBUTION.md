# Attribution and licensing audit

Prepared for owner review before any public release. Every row below was checked
against this tree or against metadata recorded in it; nothing is inferred from a
general claim about BindingDB or about any upstream project. Where a term could
not be established locally it is marked **unresolved** rather than guessed.

**There is no `LICENSE` file in this repository.** `pyproject.toml` declares
`license = { text = "MIT" }`, which is metadata only. A software licence covers
code; it does **not** cover the datasets or the model-derived artifacts below.

---

## 1. Project code

| Item | Extent | Terms | Status |
| --- | --- | --- | --- |
| `src/seq2lead/` | 106 files | declared MIT in `pyproject.toml` | **no LICENSE file present** |
| `tests/` | 50 files | same | same |
| `scripts/asof/` | 13 preserved execution scripts | same | same; excluded from lint/format so recorded digests stay valid |
| `paper/build_paper.py` | figure and manuscript builder | same | same |
| `docs/`, `README.md`, `REVIEW.md` | prose | same unless stated otherwise | same |

**Authorship discrepancy — owner decision required.** `CITATION.cff` records the
author as **Rizvanov, Timur**. The configured Git identity and the authenticated
GitHub account are **`rinatrizvanov` / `1812Rinat@gmail.com`**, and all commits
are authored under that identity. These are not the same name. I have not changed
either, because which is correct is not mine to decide.

## 2. Incorporated third-party code

| Item | Shipped here? | Terms |
| --- | --- | --- |
| Vendored or copied third-party source | **No.** No file under `src/`, `tests/` or `scripts/` carries a third-party copyright or `SPDX-License-Identifier` header | n/a |
| Declared Python dependencies — `psycopg`, `typer`, `rdkit`, `torch`, `transformers`, `lightgbm`, `scikit-learn`, `scipy`, `meeko`, `gemmi` | **No.** Declared in `pyproject.toml` and resolved at install time; no wheel or source is redistributed | each project's own licence applies to the installed package, not to this repository |
| AutoDock Vina 1.2.7 executable | **No.** Lives at `tools/vina/` and is git-ignored | Vina's own terms; not redistributed here |
| ESM-2 weights (`facebook/esm2_t33_650M_UR50D`) | **No.** Downloaded to a local cache; not in the tree | Meta's model terms — **unresolved**, see §4 |

## 3. Shipped data derived from BindingDB

Established by scanning every tracked file for InChIKeys, protein sequences of 60
or more residues, and `smiles` fields in structured data. Two distinct groups:

### 3a. Data files with substantial measured content — 17 files, 24.6 MB

`data/asof/m11d/examples.json`, `data/asof/m11e/examples.json`,
`data/asof/m11e/worked-example-review.json`,
`data/asof/m11g/evaluation-entities.json`,
`data/asof/m11g/recompute-compounds.json`,
`data/asof/m11g/recompute-targets.json`,
`data/asof/m11g/recurrence-agreement.json`,
`data/asof/m11g/reuse-compounds.json`,
`data/asof/m11h/run-20261005T134842Z/evaluation-pairs.jsonl`,
`reports/examples/query_target.fasta`, `reports/results/m10_cohort.json`,
`reports/results/m11d_ambiguous_slots.jsonl`,
`reports/results/m11d_correction_candidates.jsonl`,
`reports/results/m11d_examples.json`,
`reports/results/m11d_identifier_conflicts.jsonl`,
`reports/results/m11e_examples.json`,
`reports/results/m11e_worked_example_review.json`.

These carry compound identifiers, protein sequences or canonical SMILES traceable
to BindingDB measurements.

### 3b. Illustrative identifiers in code and documentation — 10 real InChIKeys

`tests/test_curate.py` (3), `reports/curation.md` (5), `docs/SCHEMA.md` (1),
`reports/m11d_matching.md` (1). Used as worked examples. Every other
InChIKey-shaped string in `tests/` and `scripts/` is a synthetic placeholder
written for this project (`CMPDAAAAAAAAAA-AAAAAAAAAA-N` and similar) and carries
no third-party content.

### What terms actually apply

| Source | What was verified | Where recorded |
| --- | --- | --- |
| Snapshot **A**, the 2026-01-01 UCSD Library archival deposit, DOI `10.6075/J0V40W61` | the deposit declares **Creative Commons Attribution 4.0 International** in its DataCite rights metadata | `configs/manifests/m11_acquisition_202601.json`, field `deposit.license_declared_at_deposit_level` |
| This project's own `source_release` rows | record `CC-BY-3.0 (BindingDB-curated); CC-BY-SA-3.0 (ChEMBL-derived rows)` — **stale** for the curated portion, superseded by the CC BY 4.0 finding above | `docs/M11.md` §"Licensing — and a discrepancy to resolve" |
| ChEMBL-derived rows and their **CC BY-SA 3.0 share-alike** carve-out | **UNRESOLVED.** Described in the literature but *not* broken out in the deposit's rights metadata, so it could not be confirmed at deposit level | `docs/M11.md`, same section |
| Snapshot **B**, the locally pinned BindingDB 202609 rolling release | deposit-level rights metadata **not separately recorded** for this release | — |

**Why the unresolved item matters.** A share-alike obligation on any ChEMBL-derived
row would constrain redistribution of the §3a files. Until it is established
whether any of those 17 files contains a ChEMBL-derived row, the safe reading is
that redistribution terms for §3a are **not settled**. Attributing them solely as
"CC BY 4.0" would overstate what was verified.

## 4. Model-derived artifacts

| Artifact | Derived from | Terms |
| --- | --- | --- |
| `data/asof/m11h/run-.../predictions.npz` | models fitted on BindingDB-derived pKi targets; inputs are ECFP4 fingerprints and ESM-2 embeddings | downstream of §3 and of the ESM-2 terms; **unresolved** |
| `data/asof/m11h/run-.../protein-transform.npz` | mean and scale over ESM-2 embeddings of A-training proteins | same |
| `data/asof/m11h/run-.../results.json`, `training-records.json` | metrics and fit records; no structures or sequences | project output |
| Model checkpoints | **not shipped** (git-ignored) | n/a |

**Unresolved:** the licence for `facebook/esm2_t33_650M_UR50D` was not verified
from its model card in this pass, and no local record states it. Because the
transform and the predictions are functions of its outputs, that term should be
confirmed before publishing those two files. MIT on the code does **not** reach
them.

## 5. Figures

| Item | Origin | Terms |
| --- | --- | --- |
| `paper/figures/figure_[1-5]*.png` / `.svg` | generated by `paper/build_paper.py` from saved results | project output |
| Figure provenance | `paper/figures/figure_provenance.json`, `per_target_comparison.json` | project output |
| Third-party imagery, logos, journal templates | **none** | n/a |
| Protein structures (PDB `3K34`) and docking poses | **not shipped**; `data/m10/structures/` and pose directories are git-ignored | PDB terms would apply if ever shipped |

## 6. Manuscript

`paper/manuscript.md` and `paper/Seq2Lead_scientific_manuscript.docx` are an
unreviewed technical-report draft, not submitted and not peer reviewed. **Their
AI-assistance disclosure is retained deliberately** and must not be removed; it
is a statement about how the text was produced, separate from Git authorship.

---

## Decisions needed from the owner

1. **Add a `LICENSE` file**, and state the copyright line to use. `pyproject.toml`
   says MIT; nothing in the tree establishes the copyright holder.
2. **Resolve the authorship name**: `CITATION.cff` says *Timur Rizvanov*; commits
   are authored by *rinatrizvanov \<1812Rinat@gmail.com\>*.
3. **Settle §3a redistribution**: determine whether any of the 17 data files
   contains a ChEMBL-derived row carrying CC BY-SA 3.0 share-alike. If any does,
   the repository's own distribution terms are affected.
4. **Record snapshot B's deposit-level rights metadata**, which is not captured
   locally.
5. **Confirm the ESM-2 licence** before publishing `predictions.npz` and
   `protein-transform.npz`.
6. **Decide the attribution text** for §3a and §3b once 3 and 4 are settled.

Nothing in this file grants or assumes any permission. No dataset or model term
was inferred from the software licence.
