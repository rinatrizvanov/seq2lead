# Owner decisions

One place for everything still waiting on the owner. Two parts, kept apart on
purpose:

- **Part 1 — approvals.** Decisions only the owner can make. Each has an exact
  wording, a recommendation, and the files it touches.
- **Part 2 — disclosed scientific limitations.** *Not* decisions. Measured facts
  already published in the manuscript, README and reports. They stay true whatever
  is approved, and approving the release does not resolve or soften any of them.

Confusing the two is the thing this document exists to prevent. A limitation is
not an open question awaiting a decision, and an approval is not a scientific
finding.

Current state: repository **PRIVATE**, no `LICENSE` in effect, licence terms
unchanged pending Part 1.

---

## Part 1 — Approvals

### Already decided by the owner in this pass

| | Decision | Applied to |
| --- | --- | --- |
| D1 | Author name is **Rinat Rizvanov** | `CITATION.cff`, `paper/manuscript.md`, `paper/build_paper.py`, `paper/Seq2Lead_scientific_manuscript.docx`, `docs/ATTRIBUTION.md` |
| D2 | Affiliation is **Boston University, Boston, MA, USA** | `CITATION.cff`, `paper/manuscript.md`, `paper/build_paper.py`, the `.docx` |
| D3 | Copyright line is **`Copyright (c) 2026 Rinat Rizvanov`** | `LICENSE.draft` — the name only. Terms, scope and the `.draft` filename untouched, so nothing is in effect |

The `.docx` was edited in place rather than rebuilt, so the five embedded figures
are byte-identical; only `docProps/core.xml` and `word/document.xml` changed.

### Still open

| # | Exact decision | Recommended | Affected files |
| --- | --- | --- | --- |
| **A1** | Confirm you hold the rights in the project code, then rename `LICENSE.draft` → `LICENSE` and `DATA_LICENSE.draft` → `DATA_LICENSE`, putting both into effect. | **Approve.** The drafts are complete and the copyright line is now correct. Renaming is deliberately your act. | `LICENSE.draft`, `DATA_LICENSE.draft`, `README.md` (states no licence is in effect), `docs/ATTRIBUTION.md` §6, and both manifests, which key on path and must be regenerated |
| **A2** | Accept the per-file licence assignment: **330 MIT / 16 CC BY-SA 3.0 / 3 CC BY 3.0 / 2 CC0 1.0** = 351 entries, every label a single licence. (Counts move when a file is added; `build_inventory.py` is the authority, and it refuses if anything is unclassified.) | **Approve.** Each rests on BindingDB's own row-level `Curation/DataSource`, and the arithmetic is enforced by a generator that refuses and by two tests. | `configs/manifests/release_classification.json`, `configs/manifests/release_inventory.json`, `DATA_LICENSE.draft` |
| **A3** | Accept the attribution set: BindingDB (*NAR* 2025;53:D1633–D1644), deposit DOI `10.6075/J0V40W61`, the pinned 202609 release, ChEMBL under CC BY-SA 3.0, ESM-2 at revision `08e4846e`. | **Approve.** Taken from the providers' own pages, not inferred. | `DATA_LICENSE.draft`, `docs/ATTRIBUTION.md`, `licenses/README.md` |
| **A4** | Confirm the conservative **CC BY-SA 3.0** labelling of `predictions.npz` and `protein-transform.npz`, **or** direct that the transform be excluded instead. | **Keep CC BY-SA 3.0.** Excluding the transform would cost the tamper-detection reproduction, which copies it into its run clone, to avoid a labelling question the label already answers. | `DATA_LICENSE.draft`, `docs/ATTRIBUTION.md` §4, both manifests |
| **A5** | Send, or withhold, the drafted inquiry to BindingDB and ChEMBL. | **Send it.** It can only relax the conservative labelling or confirm it, and it is already written with the measured figures. Still your call — it is an external message. | `docs/inquiries/bindingdb-chembl-derived-artifacts.md` (**not sent**) |
| **A6** | Decide on `docs/CLAUDE_CLOSEOUT_PROMPT.md`, untracked in HEAD but reachable in history at `4a64d90`. Publication would expose it. | **Accept as-is.** It carries no third-party data and no secret. Rewriting history to remove it would invalidate every recorded commit digest. | git history only; recorded in `release_inventory.json` under `also_in_git_history` |
| **A7** | Accept the CI scope: **16 files / 415 tests** are outside CI because a checkout cannot carry their artifacts. | **Approve**, and read a green badge as covering 909 of 1,324 tests, never the whole suite. | `.github/workflows/ci.yml`, `docs/CI_SCOPE.md` |
| **A8** | Accept the release scope: this is a **saved-prediction reproduction**, not a raw-to-fit rebuild. | **Approve.** The limit on a rebuild is external — see L10. | `docs/RELEASE_SCOPE.md`, `docs/REPRODUCIBILITY.md`, `README.md` |
| **A9** | Perform and verify the evidence backup: **3,973 files / 10,592,625,649 bytes (9.87 GB)** to independent storage. | **Do this before A10.** The previous figure of 1.83 GB was wrong — see the correction in the checklist. `data/raw/BindingDB_All_202609_tsv.zip` may not be obtainable again. | `docs/BACKUP_CHECKLIST.md`, `scripts/release/backup_evidence.py` |
| **A10** | Make the repository public. | **Not yet.** Do A1–A4 and A9 first; a verified backup should exist before the tree is published, and the licence should be in effect before anyone can copy it. | repository visibility only |
| **A11** | Optional: add an `authors` field to `pyproject.toml`, which currently has none. | **Add it on release**, so packaging metadata names the author too. Deliberately not done unasked. | `pyproject.toml` |

---

## Part 2 — Disclosed scientific limitations

Already published. Nothing here is awaiting a decision, and none of it is
weakened by approving the release. Every figure below is quoted from the recorded
artifacts, not restated from memory.

| # | Limitation | Where it is recorded |
| --- | --- | --- |
| **L1** | **Exploratory, not confirmatory.** September labels and prior benchmark results were already inspected before the historical experiment, and the prospective freeze is **unsigned**. Prior corpus exposure and provisional assay pooling preclude a confirmatory reading. | `paper/manuscript.md` abstract and limitations; `configs/manifests/m11h_fit.json` (`confirmatory_freeze: UNSIGNED`) |
| **L2** | **No demonstrated dual-encoder improvement.** Concat-MLP macro AUROC **0.788873** against the dual encoder's **0.781229**. No equivalence claim, no significance claim, and no paired target-level uncertainty analysis was performed. | `README.md` key results; `reports/m11h_results.md` |
| **L3** | **The macro metrics describe a rankable minority.** 22,221 pairs over 992 targets; only **123** meet the ≥5-positive/≥5-negative floor. | `README.md`; `reports/m11h_results.md` |
| **L4** | **Seed spread is training variation, not uncertainty over the target population**, and the seed ranges in the figures are not confidence intervals. Predictions are **not** bit-identical across seeds: every one of 26,444 rows differs, with max abs Δ pKi of 8.96e-05 (B1), 1.05 (B2), 2.89 (B4) and 3.19 (dual encoder). An earlier revision wrongly called B1 and B2 seed-invariant; that correction is published. | `reports/m11h_results.md` §5 and its correction note |
| **L5** | **Ki poolability across assay contexts is unresolved.** The current pooling rule is provisional, so every count assumes it. | `paper/manuscript.md`; `reports/assay_variance.md` |
| **L6** | **The CA2 docking gate FAILED**: AUROC **0.624060** against a declared 0.70 threshold, with both sensitivity boxes also failing and a full-cohort upper bound of ≈0.651. Known-pose recovery succeeded at **1.514 Å** RMSD, which is a different test. No docking re-ranking is reported. | `README.md`; `paper/manuscript.md`; `reports/results/m10_gate.json` |
| **L7** | **B3L is a target-conditioned chemical nearest-neighbour lookup, not a ligand-only model.** It searches compounds measured against the same target. B1 is the only ligand-only model. | `docs/EVALUATION.md`; `paper/manuscript.md` |
| **L8** | **The recurrence contrast is not a causal estimate.** Target-conditioned 1-NN macro AUROC 0.964204 on recurrent pairs against 0.668510 on pairs absent from A. Chemistry, coverage, label balance and exposure were not independently controlled. | `README.md`; `paper/manuscript.md`; `reports/m11h_results.md` |
| **L9** | **Declared-but-unbuilt scope.** Retrieval is unbuilt; evaluation-mode evidence display is disabled; the label-reversal split is unscored. | `paper/manuscript.md` limitations |
| **L10** | **The raw-to-fit rebuild is not independently reproducible.** Snapshot B is `BindingDB_All_202609_tsv.zip`, a rolling monthly release BindingDB replaces and does not archive at a stable URL. The SHA-256 pin detects a substitution rather than tolerating one. Snapshot A is re-fetchable from DOI `10.6075/J0V40W61`. | `docs/RELEASE_SCOPE.md`; `src/seq2lead/ingest/sources.py` |
| **L11** | **No prospective wet-lab validation and no external-method comparison** were performed. | `paper/manuscript.md` limitations |
| **L12** | **AI coding and review assistants were used extensively** in implementation, auditing and drafting. The human author is responsible for the final code, evidence, interpretation and manuscript. This disclosure is deliberately retained. | `paper/manuscript.md`; `paper/build_paper.py` |

---

## What this document replaces

`docs/NEXT_STEPS.md` steps 2–5 described the closeout that has since been
performed, and `docs/ATTRIBUTION.md` carried its own approval list. This file is
the single consolidated checklist; those remain for their detail, and their
approval items are reproduced above rather than maintained twice.
