# Attribution and licensing audit

Prepared for owner review before any public release. Every claim below was
checked against this tree, against the database's own row-level provenance, or
against the exact pinned upstream revision. Nothing is inferred from a general
statement about BindingDB, ChEMBL or Meta. Where a term could not be
established it is marked **unresolved** rather than guessed.

**`LICENSE` is in effect** and covers the project code only; `DATA_LICENSE`
covers the shipped data, per file. `pyproject.toml` declares
`license = { text = "MIT" }`, which is packaging metadata describing the code
licence and says nothing about the data; see §6.

---

## 1. Project code

| Item | Extent | Proposed terms |
| --- | --- | --- |
| `src/seq2lead/` | 106 files | MIT, per `LICENSE` |
| `tests/` | 51 files | same |
| `scripts/asof/` | 13 preserved execution scripts | same; excluded from lint/format so recorded digests stay valid |
| `paper/build_paper.py` | figure and manuscript builder | same |
| `docs/`, `README.md`, `REVIEW.md` | prose | same |

Authorship: `CITATION.cff` records **Rizvanov, Rinat** as author of the work,
and commits are authored under the owner's configured Git identity
`rinatrizvanov <1812Rinat@gmail.com>`. These describe different things — the
scholarly author of the work and the account that committed it — and they now
agree on the given name. An earlier revision recorded "Timur"; the owner
corrected it to **Rinat** and asked for that name consistently across the
manuscript, `CITATION.cff`, copyright notices, the README and release metadata.
The Git author and committer identity itself was **not** changed.

The affiliation **Boston University, Boston, MA, USA** was supplied by the owner on
request and applied verbatim to `CITATION.cff` and the manuscript byline. The
manuscript's disclosure no longer lists affiliation as outstanding, because it is
no longer outstanding; the contributor list, repository URL and release identifier
still are.

`LICENSE`'s copyright line now reads **`Copyright (c) 2026 Rinat Rizvanov`**,
on the owner's instruction. Only the two occurrences of the name changed: the
licence terms, the scope paragraph and the `.draft` filename are untouched, so no
licence is in effect and no licensing choice has been made on the owner's behalf.

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

### What this means for terms — resolved from the providers' own pages

| Source | Official statement | Where |
| --- | --- | --- |
| **BindingDB**, data curated by its own staff | **Creative Commons Attribution 3.0** | BindingDB's terms page, <https://www.bindingdb.org/rwd/bind/info.jsp> |
| **BindingDB**, data imported from ChEMBL | **Creative Commons Attribution-Share Alike 3.0 Unported** | same page |
| **ChEMBL**, for its own data | **Creative Commons Attribution-Share Alike 3.0 Unported**, requiring attribution and that derivative works be shared under the same terms | ChEMBL interface documentation, <https://chembl.gitbook.io/chembl-interface-documentation/about> |
| Required BindingDB citation | Liu T, Hwang L, Burley SK, Nitsche CI, Southan C, Walters WP, Gilson MK. *BindingDB in 2024: a FAIR knowledgebase of protein-small molecule binding data.* Nucleic Acids Research. 2025;53:D1633–D1644 | BindingDB's terms page |
| Snapshot A deposit | DOI `10.6075/J0V40W61`, whose DataCite rights metadata declares CC BY 4.0 | `configs/manifests/m11_acquisition_202601.json` |

**Two earlier statements in this project are now reconciled rather than one
overriding the other.** The hand-written constant in
`src/seq2lead/ingest/sources.py` reads
`CC-BY-3.0 (BindingDB-curated); CC-BY-SA-3.0 (ChEMBL-derived rows)` — and that
**matches BindingDB's own terms page exactly**. The M11b finding that the
archival deposit declares CC BY 4.0 is a statement about *the deposit's DataCite
record*, which is a different object from BindingDB's per-source terms. Both are
true of different things, and the per-source terms are the ones that govern rows.
An earlier revision of this audit called the constant "stale"; that was wrong.

**So the share-alike question is answered, and answered against the simple
case.** ChEMBL is the single largest source at 50.81% of rows, and BindingDB
imports those rows under CC BY-SA 3.0. Fourteen shipped files reference compounds
with such rows, and `predictions.npz` makes fifteen. Those files carry a
share-alike obligation.

### Can separate licences cover the data without touching the code's MIT?

**Yes, and that is what `DATA_LICENSE` does.** Share-alike under CC BY-SA
3.0 attaches to *Adaptations of the Work* — the licensed material. The project
code is not an adaptation of the data: it is independently authored software that
reads it, and it would function on any other corpus. So:

* `LICENSE` (MIT) covers project code, configuration, figures, prose, and
  artifacts that carry no third-party content.
* `DATA_LICENSE` assigns CC BY 3.0 or CC BY-SA 3.0 per file, from row
  provenance, and carries the attribution set.
* Where a file's compounds mix BindingDB-curated and ChEMBL-sourced rows, the
  stricter licence governs that file.

This is the ordinary dual-licence arrangement for a code-plus-data repository.
It does not require weakening MIT, and it does not let MIT imply anything about
the data.

Full licence texts are in `licenses/`, retrieved verbatim from creativecommons.org
with their digests recorded in `licenses/README.md`. They are not paraphrased.

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
| **Our artifacts computed by running the model** — `protein-transform.npz` (a 1×1280 mean, a 1×1280 scale and a fitted-row count) and `predictions.npz` (26,444 rows × 22 model-seed score columns, indexed by 16,795 InChIKeys) | **yes, both shipped** | see below |

**Using a model does not place its licence on every output.** MIT grants rights
to use, copy, modify and distribute *the Software*, and conditions that on
carrying the notice when the Software or substantial portions of it are
distributed. Neither artifact contains the Software or any portion of the
weights: the transform is 2,560 summary statistics, and the predictions are
scores. MIT's text does not address model outputs at all, so it neither grants
nor restricts anything about them. Whether any separate obligation attaches to
outputs is a question about Meta's terms and about the inputs, not something the
MIT text answers — and the inputs bring us back to §3.

A correction to an earlier revision of this audit: a byte scan reported
`predictions.npz` as containing no identifiers. That was wrong — the file is
compressed, which hid them. Loading it shows a `pair` array of **16,795
InChIKeys**, so it is listed under CC BY-SA 3.0 in `DATA_LICENSE` on the
strength of those identifiers alone. `protein-transform.npz` was checked the same
way and genuinely holds only numeric arrays: no identifier, no structure, no
sequence, no measured value.

What is therefore **established**: we are not redistributing ESM-2 code or
weights, and carry no MIT notice obligation for them; and `predictions.npz`
carries ChEMBL-linked identifiers, so share-alike applies to it regardless of how
the scores are characterised.

What remains **unresolved**: whether the *scores themselves*, and the
identifier-free `protein-transform.npz`, are "Adaptations" under CC BY-SA 3.0.
Neither provider's published terms addresses statistical or model-derived
outputs. Missing wording is neither permission nor prohibition, so a draft
inquiry has been written and is held outside this repository. It has **not** been
sent.

**The transform's licence was corrected on measurement, not on reading.** An
earlier revision of this audit placed `protein-transform.npz` under MIT because it
holds only numeric arrays. That remains true of the bytes and does not settle the
question, because the file is a statistic *over* licensed data. Resolving the
exact rows it was fitted on — its `n_fitted` is 353,957, which is precisely the
A-train pairs `data/asof/m11f/partition-summary.json` records as supplied to
fitting — against BindingDB's row-level `Curation/DataSource`:

| Of the population the transform summarises | |
| --- | ---: |
| exact-Ki records behind those pairs | 454,694 |
| ChEMBL-sourced records | 304,963 (67.07%) |
| pairs with ≥1 ChEMBL-sourced record | 251,811 (71.14%) |
| train sequences touching ChEMBL rows | 2,613 of 3,466 (75.4%) |

A file summarising a population that is roughly seven-tenths CC BY-SA 3.0 is not
safely labelled MIT on the strength of containing no identifier. It is now listed
under CC BY-SA 3.0 in `DATA_LICENSE`, with the reason recorded as an
explicit `licence_exception` in the release inventory.

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

## 6. The code licence

`LICENSE` holds an MIT text under
**`Copyright (c) 2026 Rinat Rizvanov`** — the given name was corrected from
"Timur" on the owner's instruction; the terms were never touched, and the MIT
text is byte-identical to the draft the owner approved. The file was activated by
the owner's authorisation for the first public release. Its preamble states that
it covers project code only and names what it does not cover — the §3 data files,
the §4 model-derived artifacts, and every third-party component. A software
licence cannot grant rights in data the licensor does not hold.

---

## Decisions needed from the owner

> The owner's consolidated decision checklist is working material and is not
> tracked in this repository. What follows is the licensing detail a reader needs
> in order to judge the terms, which is why it stays here.

The licensing questions that blocked a complete release are now **answered from
the providers' own terms**. What remains is approval plus one genuine unknown.

### Ready for your decision

1. **Confirm rights and rename** `LICENSE` → `LICENSE` (MIT, project code)
   and `DATA_LICENSE` → `DATA_LICENSE` (CC BY 3.0 / CC BY-SA 3.0 per file).
2. **Accept the per-file licence assignment** in `DATA_LICENSE`, the
   inventory at `configs/manifests/release_inventory.json` and the assignment it
   is built from at `configs/manifests/release_classification.json`: **330 MIT,
   16 CC BY-SA 3.0, 3 CC BY 3.0, 2 CC0 1.0** — 351 entries, every label a single
   licence, plus the 2 self-excluded manifests, accounting for all 353 files git
   would ship. These counts move whenever a file is added, so
   `scripts/release/build_inventory.py` is the authority; it refuses to emit an
   inventory while anything is unclassified. Three entries carry a `licence_exception` giving the reason their
   label needs one: the two CC licence texts and `protein-transform.npz`.
3. **Accept the attribution set** — BindingDB's 2025 *NAR* citation, deposit DOI
   `10.6075/J0V40W61`, the pinned 202609 release, ChEMBL under CC BY-SA 3.0, and
   ESM-2 at its pinned revision.
4. **Decide on `docs/CLAUDE_CLOSEOUT_PROMPT.md`.** Untracked in HEAD but
   reachable in history at `4a64d90`; publication would expose it. It carries no
   third-party data. History was deliberately not rewritten.
5. **Accept the CI scope** in `docs/CI_SCOPE.md` — now **16** files out of CI's
   reach, all for one cause: they need local artifacts a checkout does not carry.
6. ~~Fix or accept the two ANSI-dependent CLI tests.~~ **Done.** Both now read
   switch names and documented guarantees from Click's parameter objects and
   render the CLI in a child process at a pinned width, so neither colour nor
   terminal width can decide the result (`tests/cli_help.py`). They run in CI;
   nothing was skipped, deselected or weakened. See `docs/CI_SCOPE.md`.
7. **Perform and verify the evidence backup** — 3,973 files, 10,592,625,649 bytes
   (9.87 GB), to storage on a different physical disk. The procedure and the tool
   are owner working material held outside this repository. An earlier figure of
   1.85 GB was wrong: it omitted `data/raw` entirely, including the September
   BindingDB archive, which is the one input that may not be obtainable again.
8. **Accept the release scope** in `docs/RELEASE_SCOPE.md`: this release is a
   **saved-prediction reproduction**, not a from-raw rebuild. Approving
   it does not claim a third party can regenerate the predictions from BindingDB,
   and the blocker on that is external — the 202609 snapshot is a rolling release
   BindingDB does not archive at a stable URL. Re-pinning the evaluation to two
   archival deposits would fix it and is a scientific change, deliberately not
   made here.

### The one unresolved permission question

9. **Are model predictions and fitted statistics "Adaptations" under CC BY-SA
   3.0?** Affects `predictions.npz` and `protein-transform.npz`. Not addressed
   by either provider's published terms.
   A draft inquiry is ready, held outside this repository, and has **not** been
   sent — sending it is the owner's call.

   **Both affected files now ship under CC BY-SA 3.0.** `predictions.npz` on
   the strength of the 16,795 InChIKeys it carries, and `protein-transform.npz`
   as a conservative packaging decision, because 71.14% of the pairs it was
   fitted over carry a ChEMBL-sourced exact-Ki record (measurement above).

   The uncertainty is **not** being called non-blocking on the ground that
   obligations could be added later. It is blocking for the permissive option,
   because the error is asymmetric and only one direction can be undone: a
   CC BY-SA label that turns out to be unnecessary can be relaxed by the owner,
   whereas an MIT label that turns out to be wrong cannot be withdrawn from
   recipients who already hold the file, and breaks the share-alike chain for
   everyone downstream of them. What makes the release shippable is not that the
   question is unimportant — it is that a label exists which is correct under
   either answer, and that label is in use.

   What is decided here is a **packaging decision** (these files ship, under
   these terms, with this attribution). What is *not* decided, and is not ours to
   decide, is the **legal conclusion** (whether either file is an "Adaptation"
   under CC BY-SA 3.0 §1(a), or whether a database right is engaged). Seq2Lead
   licenses only whatever rights it holds in these two files and makes no grant
   over BindingDB's or ChEMBL's data, which it does not own and cannot license.

   Excluding `protein-transform.npz` would also dispose of the question and was
   rejected: `scripts/asof/m11h_reproduce_integrity.py` copies it into the
   self-contained run clone used for tamper detection, so dropping it would trade
   reproduction coverage for a labelling question a label already answers. That
   option remains open to the owner.

Nothing in this file grants or assumes any permission, and no dataset or model
term was inferred from the software licence.
