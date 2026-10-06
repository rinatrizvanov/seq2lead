# Attribution and licensing audit

Prepared for owner review before any public release. Every claim below was
checked against this tree, against the database's own row-level provenance, or
against the exact pinned upstream revision. Nothing is inferred from a general
statement about BindingDB, ChEMBL or Meta. Where a term could not be
established it is marked **unresolved** rather than guessed.

**There is no `LICENSE` file in effect.** `pyproject.toml` declares
`license = { text = "MIT" }`, which is metadata. A draft is prepared at
`LICENSE.draft` for owner review; see §6.

---

## 1. Project code

| Item | Extent | Proposed terms |
| --- | --- | --- |
| `src/seq2lead/` | 106 files | MIT, per `LICENSE.draft` |
| `tests/` | 51 files | same |
| `scripts/asof/` | 13 preserved execution scripts | same; excluded from lint/format so recorded digests stay valid |
| `paper/build_paper.py` | figure and manuscript builder | same |
| `docs/`, `README.md`, `REVIEW.md` | prose | same |

Authorship: `CITATION.cff` records **Rizvanov, Timur** as author of the work,
and commits are authored under the owner's configured Git identity
`rinatrizvanov <1812Rinat@gmail.com>`. These describe different things — the
scholarly author of the work and the account that committed it — and are
recorded as the owner intends. Neither was changed.

## 2. Incorporated third-party code

### What was checked

Absent copyright headers prove nothing on their own, so the review did not stop
there. Seven checks, all negative:

1. **Attribution markers** — `copyright`, `SPDX-License-Identifier`, `licensed
   under`, `all rights reserved`, `adapted from`, `taken from`, `ported from`,
   `vendored`, `stackoverflow`, `gist.github` across `src/`, `tests/`,
   `scripts/`, `paper/`. Every hit was the project's own word *derived* used
   about data provenance, not about code.
2. **Vendor directories** — no `vendor/`, `third_party/`, `_vendor/`,
   `extern/` or `contrib/` anywhere under `src/`.
3. **Bundled assets** — `src/` contains Python files only; no data, binaries
   or templates are shipped inside the package.
4. **Lockfile sources** — every package in `uv.lock` resolves from a package
   index. No `git+` or direct-URL dependency, so nothing is vendored by lock.
5. **Embedded payloads** — no `base64.b64decode` of a literal, no long
   `bytes.fromhex`, no `zlib.decompress` of inline data. The one `b64decode`
   call reads the project's own encoded raw rows at ingest.
6. **Standard algorithms** — `midranks`, `auroc`, `average_precision`,
   `recall_at_k`, `enrichment_factor`, `spearman`, `concordance_index`,
   `tanimoto_to_reference`, `bedroc`. These implement published formulae and
   are written in the project's own voice; `bedroc` cites Truchon & Bayly
   2007. Implementing a published formula is not incorporating code.
7. **Re-export of dependencies** — none; dependencies are imported and used.

### Residual uncertainty, stated plainly

These checks can establish that nothing *declares* third-party origin and that
no third-party file or archive is present. They **cannot** prove that no code
was ever written by consulting another implementation, or that no short
fragment resembles one. Establishing that would need a provenance tool or the
author's own account of how each module was written. The conclusion supported
by evidence is: **no third-party code is redistributed in this tree**, and no
third-party attribution obligation was found. That is narrower than "no code
was ever influenced by anything", which was not tested.

| Item | Shipped here? | Terms |
| --- | --- | --- |
| Declared dependencies — `psycopg`, `typer`, `rdkit`, `torch`, `transformers`, `lightgbm`, `scikit-learn`, `scipy`, `meeko`, `gemmi` | **No** — resolved at install time | each project's own licence applies to the installed package, not to this repository |
| AutoDock Vina 1.2.7 executable | **No** — `tools/vina/` is git-ignored | Vina's own terms |
| ESM-2 weights | **No** — local HF cache only | see §4 |
| Protein structures (PDB `3K34`), docking poses, receptors | **No** — git-ignored | PDB terms would apply if ever shipped |

## 3. Shipped data derived from BindingDB — row-level provenance

### The evidence used

BindingDB's raw records carry a **`Curation/DataSource`** column. The project
preserves every raw payload, so this is answerable per row rather than by
reading a licence page. Over all **3,264,761** raw rows in the accepted 202609
schema:

| `Curation/DataSource` | Rows | Share |
| --- | ---: | ---: |
| ChEMBL | 1,658,927 | 50.81% |
| US Patent | 1,341,920 | 41.10% |
| PubChem | 102,553 | 3.14% |
| Curated from the literature by BindingDB | 93,083 | 2.85% |
| PDSP Ki | 55,430 | 1.70% |
| WIPO | 8,766 | 0.27% |
| D3R | 2,990 | 0.09% |
| CSAR | 818 | 0.03% |
| Taylor Research Group, UCSD | 274 | 0.01% |

**The share-alike question is therefore not hypothetical: ChEMBL is the single
largest source, at just over half the corpus.** The project's own
`BINDINGDB_LICENSE` constant in `src/seq2lead/ingest/sources.py` was written by
hand and never derived from this column.

### File by file

`ChEMBL rows` counts measurement rows carrying `Curation/DataSource = ChEMBL`
for the compounds that file references. It is a measure of linkage, not of how
much ChEMBL text the file contains — the `Content shipped` column is that.

| File | Bytes | Content shipped | Keys | ChEMBL rows | Links to ChEMBL |
| --- | ---: | --- | ---: | ---: | --- |
| `data/asof/m11d/examples.json` | 3,145 | worked-example records: pair keys, counts, statuses | 1 | 15 | **yes** |
| `data/asof/m11e/examples.json` | 15,966 | worked-example records: pair keys, labels, exact spreads | 6 | 168 | **yes** |
| `data/asof/m11e/worked-example-review.json` | 24,615 | worked-example review: pair keys, decisions | 7 | 248 | **yes** |
| `data/asof/m11g/evaluation-entities.json` | 685,066 | InChIKey and sequence-hash lists only | 17,373 | 98,998 | **yes** |
| `data/asof/m11g/recompute-compounds.json` | 433,018 | InChIKey -> canonical SMILES (structures) | 3,422 | 1,074 | **yes** |
| `data/asof/m11g/recompute-targets.json` | 6,677 | sequence hash -> protein sequence (13 sequences) | 0 | 0 | no |
| `data/asof/m11g/recurrence-agreement.json` | 1,254 | counts plus 5 example pair keys | 2 | 102 | **yes** |
| `data/asof/m11g/reuse-compounds.json` | 10,076,682 | InChIKey -> 202609 surrogate id (no structures) | 251,917 | 614,890 | **yes** |
| `data/asof/m11h/run-20261005T134842Z/evaluation-pairs.jsonl` | 13,665,385 | pair keys, labels, strata, branch flags | 16,795 | 93,764 | **yes** |
| `reports/examples/query_target.fasta` | 469 | one protein sequence (demo input) | 0 | 0 | no |
| `reports/results/m10_cohort.json` | 192,763 | 600 members: compound_id, canonical SMILES, pKi aggregates, labels | 0 | see note | **yes** |
| `reports/results/m11d_ambiguous_slots.jsonl` | 1,110 | slot keys and counts | 7 | 178 | **yes** |
| `reports/results/m11d_correction_candidates.jsonl` | 40,551 | candidate correspondences: pair keys, values | 75 | 872 | **yes** |
| `reports/results/m11d_examples.json` | 3,145 | worked-example records | 1 | 15 | **yes** |
| `reports/results/m11d_identifier_conflicts.jsonl` | 486 | record metadata: measurement type, value, entry DOI, reactant_set_id | 1 | 0 | no |
| `reports/results/m11e_examples.json` | 15,966 | worked-example records | 6 | 168 | **yes** |
| `reports/results/m11e_worked_example_review.json` | 24,615 | worked-example review | 7 | 248 | **yes** |

Notes on the four files that needed resolution beyond an InChIKey scan:

- **`reports/results/m10_cohort.json`** identifies compounds by `compound_id`,
  not InChIKey, so an identifier scan found nothing. Resolved properly: its 600
  members carry **3,334 measurement rows, 91.96% ChEMBL**. It also ships **600
  canonical SMILES** — actual chemical structures, not identifiers. This is the
  most exposed file in the set, and a scan that only looked for InChIKeys would
  have cleared it.
- **`data/asof/m11g/recompute-compounds.json`** has 3,153 of 3,422 compounds
  absent from 202609 because they are January-only. Resolved in the isolated
  `seq2lead_m11c_202601` schema: **12,754 rows, 62.36% ChEMBL**. It ships
  canonical SMILES.
- **`reports/examples/query_target.fasta`** ships one protein sequence. That
  target has 12,916 ChEMBL-sourced measurement rows, but the sequence itself is
  target-chain data, not a ChEMBL compound record. Marked **no** for that
  reason; the distinction is the point.
- **`data/asof/m11g/recompute-targets.json`** ships 13 January-only protein
  sequences with no 202609 measurement rows. Same reasoning.

### What this means for terms

| Source | Verified | Where |
| --- | --- | --- |
| The 2026-01-01 UCSD Library deposit, DOI `10.6075/J0V40W61` | declares **CC BY 4.0** in its DataCite rights metadata | `configs/manifests/m11_acquisition_202601.json`, `deposit.license_declared_at_deposit_level` |
| The project's `source_release` string | `CC-BY-3.0 (BindingDB-curated); CC-BY-SA-3.0 (ChEMBL-derived rows)` — a hand-written constant, **stale** for the curated portion | `src/seq2lead/ingest/sources.py:44` |
| Snapshot B, the pinned 202609 rolling release | deposit-level rights metadata **not recorded locally** | — |
| ChEMBL's own terms for the rows it contributed | **UNRESOLVED.** Not broken out in the deposit's rights metadata, and not verified against ChEMBL's own licence in this pass | `docs/M11.md` §Licensing |

**The open question, stated precisely.** Deposit-level CC BY 4.0 is a statement
by the depositor about the deposit. It does not by itself establish the terms of
the 1.66 million rows the deposit attributes to ChEMBL. Fourteen of the
seventeen shipped files reference compounds whose measurements include such
rows, and two of those files ship chemical structures rather than identifiers.
If ChEMBL's terms carry share-alike, redistribution of at least those two files
is affected. This cannot be settled from deposit metadata, and it is not
settled here.

### Required attribution, as far as it is established

- Cite **BindingDB** as the source of the measured affinity evidence, and the
  **2026-01-01 deposit by DOI `10.6075/J0V40W61`** for snapshot A.
- Reproduce the deposit's **CC BY 4.0** notice for content covered by it.
- Name **ChEMBL** as the upstream source of the rows attributed to it, pending
  the unresolved question above.
- Nothing here authorises redistribution; attribution is necessary, not
  sufficient.

## 4. Model-derived artifacts and the ESM-2 terms

### What the pinned revision declares

The project pins `facebook/esm2_t33_650M_UR50D` at revision
`08e4846e537177426273712802403f7ba8261b6c`. Queried at that exact revision:

| Checked | Result |
| --- | --- |
| Hugging Face repo metadata at that revision | `license: "mit"`; revision last modified 2023-03-21 |
| Files present at that revision | `.gitattributes`, `README.md`, `config.json`, `model.safetensors`, `pytorch_model.bin`, `special_tokens_map.json`, `tf_model.h5`, `tokenizer_config.json`, `vocab.txt` — **no `LICENSE` file** |
| Meta's ESM repository LICENSE (`facebookresearch/esm`) | **MIT**, `Copyright (c) Meta Platforms, Inc. and affiliates.` |
| Scope wording in Meta's LICENSE | refers to "the Software" and "associated documentation files"; does **not** name model weights separately from code |
| Local cache at that revision | weights and tokeniser only; **no licence text cached** |

So the MIT declaration for the weights exists as **repository metadata**, while
the MIT text itself lives in Meta's **code** repository and is written in terms
of "the Software".

### Code, weights and our artifacts are three different things

| Thing | Redistributed here? | Consequence |
| --- | --- | --- |
| ESM-2 **code** (`facebookresearch/esm`, `transformers`) | no | MIT's notice condition is not triggered by this tree |
| ESM-2 **weights** (`model.safetensors`, 2.6 GB) | no — local cache only | same |
| **Our artifacts computed by running the model** — `protein-transform.npz` (1,280 means and 1,280 scales over A-training embeddings) and `predictions.npz` (one score per evaluated pair) | **yes, both shipped** | see below |

**Using a model does not place its licence on every output.** MIT grants rights
to use, copy, modify and distribute *the Software*, and conditions that on
carrying the notice when the Software or substantial portions of it are
distributed. Neither artifact contains the Software or any portion of the
weights: the transform is 2,560 summary statistics, and the predictions are
scores. MIT's text does not address model outputs at all, so it neither grants
nor restricts anything about them. Whether any separate obligation attaches to
outputs is a question about Meta's terms and about the inputs, not something the
MIT text answers — and the inputs bring us back to §3.

What is therefore **established**: we are not redistributing ESM-2 code or
weights, and we carry no MIT notice obligation for them. What is **unresolved**:
whether anything beyond §3 constrains the two shipped artifacts.

## 5. Figures and manuscript

| Item | Origin | Terms |
| --- | --- | --- |
| `paper/figures/figure_[1-5]*.png` / `.svg` | generated by `paper/build_paper.py` from saved results | project output; depict §3-derived data |
| `paper/figures/figure_provenance.json`, `per_target_comparison.json` | project output | project output |
| Third-party imagery, logos, journal templates | **none** | n/a |

`paper/manuscript.md` and `paper/Seq2Lead_scientific_manuscript.docx` are an
unreviewed technical-report draft, not submitted and not peer reviewed. Their
**AI-assistance disclosure is retained deliberately** and must not be removed;
it concerns how the text was produced and is separate from Git authorship.

## 6. The LICENSE draft

`LICENSE.draft` holds an MIT text with the proposed line
**`Copyright (c) 2026 Timur Rizvanov`**. It is named `.draft` and is **not in
effect**: renaming it to `LICENSE` is the owner's act, subject to confirming
they hold the relevant rights in the code. Its preamble states that it covers
project code only and names what it does not cover — the §3 data files, the §4
model-derived artifacts, and every third-party component. A software licence
cannot grant rights in data the licensor does not hold.

---

## Decisions needed from the owner

1. **Confirm rights and rename** `LICENSE.draft` to `LICENSE`, or supply a
   different copyright line.
2. **Settle the ChEMBL question** for the fourteen files in §3 — in particular
   `reports/results/m10_cohort.json` (600 structures, 91.96% ChEMBL) and
   `data/asof/m11g/recompute-compounds.json` (structures, 62.36% ChEMBL),
   which ship chemical structures rather than identifiers. If share-alike
   applies, the repository's own distribution terms are affected.
3. **Record snapshot B's deposit-level rights metadata**, absent locally.
4. **Decide whether any obligation attaches to the two model-derived
   artifacts** beyond what §3 implies; MIT does not answer it.
5. **Approve the attribution text** in §3 once 2 and 3 are settled.
6. **Decide whether to ship structures at all.** Dropping the SMILES columns
   from those two files would reduce the §3 exposure to identifiers and derived
   values. That is a scientific-reproducibility trade-off, not an editorial one,
   so it is the owner's call — and no such change was made here.
7. **Perform the backup** in `docs/BACKUP_CHECKLIST.md` (1.85 GB, single copy).
8. **Accept the CI scope** in `docs/CI_SCOPE.md`, which records that CI covers
   1,075 portable tests and cannot cover 65 artifact-bound ones.

Nothing in this file grants or assumes any permission, and no dataset or model
term was inferred from the software licence.
