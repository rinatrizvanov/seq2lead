# M10 hardening pass — change record

**No docking was rerun, no model refitted, and no published number changed.**
All three runs' score files, failure files, receptors, prepared ligands and
output poses are byte-identical to their pre-pass digests, and every headline
metric recomputes from the saved scores to within 1e-12. The pre-declared gate
rule (AUROC ≥ 0.70 with CI lower bound > 0.5) and the 20 Å headline box are
unchanged, and the decision is still **FAIL** at AUROC 0.624, CI [0.578, 0.670].

What follows separates **code fixes**, **reporting corrections**, and the
**numbers that did not move**.

---

## Code fixes

### 1. The cohort's identity ignored what was actually docked

Confirmed before changing anything: setting a member's SMILES to `CCO` left
`membership_sha256()` identical, and `run_cohort()` accepted the result,
because `selection_sha256` describes the selection *recipe* rather than its
output. A cohort of ethanol would have docked.

Added `Cohort.content_sha256()`, covering every member's compound id, label,
evidence, SMILES, heavy-atom count, censoring bounds, exact median and
observation counts, together with the cohort name, endpoint, endpoint id, target
and seed. Added `Cohort.validate()` for unique ids, admissible labels and
evidence, declared class counts, total size and non-empty structures.

`verify_cohort()` now runs **before receptor or ligand preparation** and checks,
in order: internal consistency, the selection binding, the declared class counts,
and the content digest. It takes the cohort *object*, so a cohort injected
directly faces exactly the same checks as one loaded from the frozen file —
previously the file was guarded and the in-memory path was not.

**The expected digest is a literal in the contract**, under a new top-level
`cohort_pin` block; the actual digest is computed from the cohort. The two come
from different inputs, so the check is not self-satisfying.

### 2. Manifest verification was incomplete

Two confirmed defects: the ligand and pose directory digests and file counts were
recorded but never checked, and an empty `runs` list with `n_runs: 3` verified
clean.

`verify_manifest_scoped()` now checks the manifest's own self-consistency
(declared `n_runs` against the actual list, a non-empty list, unique run labels,
and an expected run set when given), the contract digest, every per-run file,
**every referenced directory's contents and file count**, and the published gate,
QA and sensitivity artifacts.

It returns a `VerificationScope` separating **verified**, **unavailable** and
**problems**. That distinction is load-bearing for the review ZIP, which ships
the manifest but not the ~3,500 ligand and pose files: with
`allow_missing_trees`, an absent directory is *named* as unavailable rather than
silently counted as verified, and `complete` is false. A directory that *is*
present is still checked, and a wrong digest is still a problem.

### 3. Reporting trusted the file it was reporting

Confirmed: copying `m10_gate.json`, editing it to `decision: pass` and
`auroc: 0.99`, and calling `render()` produced a report announcing a PASS, with
the 578 contradicting scores untouched on disk beside it.

New `seq2lead.dock.verify` recomputes the gate from the saved per-ligand scores
and failures under the recorded contract, checking that the cohort matches the
contract pin, every scored row belongs to the cohort with the cohort's own label,
`ranking_score == -affinity` for every row, and that scored plus failed account
for all 600 members exactly once each. `report.write()` calls it and raises
`EvidenceMismatch` **before writing anything**; the published report now carries
a "Verified before publishing" section listing the checks that passed.

---

## Reporting corrections

| Correction | Was | Now |
| --- | --- | --- |
| Element attrition | "21", a sum of per-element tallies | **19 unique compounds** — 11 Se-only, 2 Se+Te, 2 Te-only, 4 B — generated from `failures.json` and the frozen cohort so it cannot drift again |
| Toolchain claim | "AutoDock has no atom type for Se or Te" | scoped to meeko 0.8.0, Vina 1.2.7 and RDKit 2026.03.6 at the pinned versions, quoting the actual error strings, and explicitly not a claim about AutoDock formats in general or other tools and versions |
| Bootstrap | reported as a 95% CI without qualification | states that it resamples **individual compounds** and so does not account for dependence among chemical analogues; in a largely congeneric cohort the effective sample size is smaller and the true interval is **wider**, so p = 2.5e-7 is a lower bound on the tail probability, not a calibrated figure |
| BEDROC docstring | "a fully tied ranking returns the positive prevalence" | a fully tied ranking returns the **expected random-ranking value**, `Ra·sinh(α/2)/(cosh(α/2) − cosh(α/2 − αRa)) + 1/(1 − exp(α(1−Ra)))`, which equals the prevalence only at Ra = 0.5 — it is 0.116 for Ra = 0.1 and 0.061 for Ra = 0.02 |
| Ranking score | bare numbers | labelled **−1 × best-pose Vina energy**, with the raw kcal/mol energy shown alongside every median and retained in `scores.json` as `affinity_kcal_per_mol` |

The pre-declared interval and p-value are **preserved as the pre-declared
results**; only the claims made about them are qualified. Substituting
scaffold-level resampling now would be choosing a statistic after seeing the
outcome, so it is named as the better model and not adopted.

BEDROC gained a test against an independently derived closed form at four
prevalences including 2% and 5%, where the old claim is wrong by up to 203%. The
previously published BEDROC of 0.629 is unaffected: this cohort is balanced, and
at Ra = 0.5 the two expressions coincide.

---

## Manifest and contract versioning

The contract gained the `cohort_pin` block, which changed its digest. Stripping
that block from the current file reproduces **exactly** the digest the runs were
produced under (`141d200eb8ee76e9`), and no pre-existing key changed — protocol,
gate rule, cohort recipe and endpoint blocks are identical. So:

- the per-run `config_sha256` still records `141d200eb8ee76e9`, the contract the
  run was actually docked under. Rewriting it would falsify the record.
- that digest is declared in the manifest's new `superseded_contracts` list, and
  verification treats an **undeclared** run contract as a problem.
- the previous manifest is kept at `configs/manifests/m10_docking.v1.json`
  (`ae48c84d8fc569f1…`); the new one is `a5afc5ff480b7eb7…`.
- the contract is `356f69c205067545…`.

Every carried-over digest was cross-checked against the archive: **0 changes**
across receptor, cohort, scores, failures, record, both directory digests, file
counts, scored and failed counts and exhaustiveness. The only additions are the
`published`, `config_sha256`, `config_path` and `superseded_contracts` fields.

**Retrospective pinning, disclosed.** `cohort_pin.content_sha256` was computed
from the already-frozen cohort file. It attests that the file has not changed
since the pin was taken; it is **not** evidence about what the file contained
before the pin existed.

Recomputing the published metrics from the saved scores establishes that the
saved scores and labels are **mutually consistent** and that the published
numbers follow from them. It does **not** establish that the recorded SMILES were
the structures actually docked: that is structure-to-docking provenance, and it
would require the preparation artifacts and their bindings — the prepared ligand
PDBQTs are referenced by a directory digest, but nothing binds a given PDBQT back
to the cohort row it was built from. That gap is unresolved and is listed as
such.

---

## Numbers that did not move

| | |
| --- | --- |
| Gate decision | **FAIL**, unchanged |
| AUROC / CI | 0.6240600153 / [0.5781079366, 0.6702409534], all to 1e-12 |
| Effect threshold | 0.70, unchanged |
| EF@1% / EF@5% / BEDROC | 1.347319 / 1.324090 / 0.628935, unchanged |
| Mann–Whitney U | 52,116.5, unchanged |
| Attrition bound | best case 0.651161, unchanged |
| Headline box | 20 Å, unchanged; sensitivity remains a separate analysis |
| Score / failure / receptor / record files | 12 of 12 byte-identical |
| Ligand and pose directories | 6 of 6 unchanged, 583 and 579 files each |
| Pose-recovery QA | 1.514 Å, unchanged |

---

# Second correction pass

**No docking rerun, no refitting, no new uncertainty analysis, no gate-rule
change.** All 18 docking artifacts (scores, failures, receptors, run records and
both referenced directories per run) are byte-identical to their **original**
pre-hardening digests, and every published number — primary, all seven secondary
metrics, the attrition table, the element breakdown, the bounds, and both
sensitivity rows — reproduces from the saved files. The decision is still
**FAIL** at AUROC 0.6240600153.

All three defects were reproduced before being fixed.

## Code fixes

### 1. Only the primary fields were compared

Reproduced: setting `gate.secondary.bedroc_alpha20` to 0.99 published `0.990`.
`check_published()` compared decision, AUROC, CI and class counts, and ignored
the secondary metrics the recomputation already held.

Now compared: Mann–Whitney U and p, both class medians, EF@1%, EF@5% and BEDROC —
the full `DISPLAYED_SECONDARY` tuple, which is the same list the report prints.
Missing, null and non-finite required values are refused rather than formatted.

**Attrition is recomputed too**: the per-class table, the element breakdown and
the bounds. The 19-unique-compound correction from the previous pass is only
durable if the breakdown is checked, so it now is.

**Each displayed sensitivity row is verified against its own saved run** — its
own scores, failures and run record — rather than taken on the primary's
authority. A row is also refused if its declared scale disagrees with its record's
box size, or if its record shows the primary box (in which case it is not a
sensitivity run).

**The QA artifact is verified against the digest the manifest bound.** At the
time of this pass the pose RMSD was not independently recalculated, and this
record wrongly said that doing so would require re-docking. It does not — see
the closeout below.

All of this is wired into `report.write()`, so the production publishing path
performs it and refuses before creating or overwriting anything.

### 2. Reconciliation did not validate the failure list

Reproduced: appending a duplicate failure row produced **no problems** and the
false summary "578 scored + 23 failed = 600 cohort members, each exactly once".
Only the scored list was deduplicated, and the arithmetic identity was asserted
in a message rather than tested.

Now: both lists are deduplicated independently; every failure row's id and label
are checked against the cohort exactly as scored rows were; disjointness and
complete coverage are enforced; and `scored + failed == len(cohort)` is a
**checked condition**, so the summary line can no longer state something untrue.
Invalid data returns `decision="unverified"` with no metrics computed.

### 3. Manifest completeness was defined by the manifest

Reproduced: deleting the `published` section and two runs and setting
`n_runs: 1` verified clean through the CLI, which called `verify_manifest()`
with no independent expectation.

Added `configs/manifests/m10_verification.json` (`b6c489019df19b1e…`), a versioned
verification contract **outside** the manifest it describes, declaring the
primary run, both sensitivity runs, and the required publication groups with
their counts and paths. Verification now fails on a missing or empty group, a
wrong count, duplicate entries, a missing required path, an undeclared group, or
an absent primary run. Both the `verify-manifest` CLI and the report publisher
enforce it, and the CLI gained `--manifest` and `--contract-path` so an archive
can be checked directly.

ZIP-scope reporting is retained: with `--allow-missing-trees`, absent ligand and
pose directories are **named** as unavailable and `complete` is false, never
silently counted as verified.

A test-quality defect surfaced here and is worth recording: the first version of
the CLI tests monkeypatched `artifacts.MANIFEST_PATH`, which does nothing,
because the path is a default argument bound at definition time. Every doctored
manifest was silently verifying the real one, and all seven tests passed for the
wrong reason. They now pass an explicit path to the CLI.

## Claims corrected

| | |
| --- | --- |
| Uncertainty | the previous "the true interval is **wider**" and "p-value is a lower bound" are replaced by: *the interval and p-value use an analysis that does not account for dependence among chemical analogues; their calibration under that dependence is unknown.* A separately labelled post-hoc scaffold-level analysis is legitimate in principle and was **not** performed. |
| Provenance | removed the claim that metric reproduction showed the recorded SMILES were the structures docked. Reproduction establishes that the saved scores and labels are mutually consistent and that the published numbers follow from them. Structure-to-docking provenance needs the preparation artifacts and their bindings, and is listed as unresolved. |

The pre-declared statistics and the gate decision are untouched.

## Versioning

| | |
| --- | --- |
| Contract | `84b6515d5c0c656e…` — the only change is the corrected provenance comment on `cohort_pin`; protocol, gate, cohort and endpoint blocks identical |
| Manifest | `3a13decffd3c8926…`, with 0 digest changes against **both** archives |
| Archived | `m10_docking.v1.json` (original), `m10_docking.v2.json` (`a5afc5ff480b7eb7…`, hardening pass) |
| Superseded contract | `141d200eb8ee76e9` still declared; runs keep the digest they were docked under |

## Unresolved

- **Structure-to-docking provenance.** Nothing binds a prepared ligand PDBQT back
  to the cohort row it was built from. The prepared-ligand directory is covered by
  a single tree digest, so the set is verifiable but the per-compound mapping is
  not. Establishing it would need a per-ligand binding recorded at preparation
  time, which no existing run has.
- **QA pose RMSD** was verified by digest only in this pass. Resolved at closeout:
  recalculation does not require re-docking.
- **Analogue dependence** in the uncertainty model is described, not quantified.

---

# Closeout

Two small items, before M10 is closed as a bounded historical experiment. The
**FAIL decision, every recorded metric, the uncertainty qualifications and the
structure-to-docking provenance limitation are preserved unchanged.**

## 1. Pose RMSD does not require re-docking — corrected and acted on

The previous pass claimed independent recalculation of the QA pose RMSD "would
require re-docking the reference ligand". That was wrong, and it understated what
the published artifact could be held to. Three inputs suffice: the **saved pose**,
the **crystal reference coordinates**, and an **atom mapping** between them. All
three were already on disk, and the mapping did not even need to be supplied — the
pose file carries the molecule's SMILES in a `REMARK SMILES` line that meeko
writes, and `rdMolAlign.CalcRMS` resolves topological symmetry itself.

So the check was strengthened rather than merely reworded. `verify_qa()` now
recalculates the RMSD from `data/m10/runs/qa-redock/SUA_redock.pdbqt` against
`data/m10/structures/3k34.pdb` and compares it to the published value. It
reproduces **1.514 Å**, matching the artifact exactly, and the report now records
that as an independent recalculation rather than a digest check.

**Which inputs are unavailable in the review ZIP**, named individually when the
recalculation cannot run:

| Input | In the archive | Why |
| --- | --- | --- |
| `data/m10/runs/qa-redock/SUA_redock.pdbqt` | **absent** | pose directories are referenced by digest, not shipped |
| `data/m10/structures/3k34.pdb` | **absent** | a raw RCSB download, pinned by digest and URL in the contract |
| `reports/results/m10_qa_redock.json` | present | the QA artifact itself ships |
| `configs/manifests/m10_docking.json` | present | binds the QA digest |

A ZIP-only reviewer therefore gets digest verification of the QA artifact and is
told, by path, exactly which two files would be needed to re-derive its RMSD. Both
are obtainable: the structure from the URL and digest in the contract, the pose by
re-running the recorded QA command.

## 2. Publication now verifies the QA artifact it renders

Reproduced first: `render()` accepted a `qa_path` override while
`verify_publication()` checked a hardcoded path, so pointing the report at a
forged QA artifact published **"Top-pose RMSD to crystal: 0.01 Å"** while the real
file sat untouched and verification passed.

The selected QA path is now threaded through verification, so the artifact
verified is the artifact rendered. A forged override is refused on two independent
grounds: the manifest binds no digest for it, and the recalculated RMSD
contradicts it.

## Unchanged at closeout

| | |
| --- | --- |
| Gate decision | **FAIL**, AUROC 0.6240600153, CI [0.578, 0.670], threshold 0.70 |
| QA artifact | byte-identical; RMSD 1.514 Å, now independently confirmed |
| Uncertainty qualification | retained verbatim — calibration under analogue dependence is unknown |
| Structure-to-docking provenance | still unresolved; no per-ligand binding exists |
| Headline protocol | 20 Å box; sensitivity remains separately labelled |
