# M10 — the docking validation gate

The machine-readable contract is `configs/experiments/m10-docking-v1.yaml`. This
document says why each value in it was chosen. Both were frozen before the gate
cohort was docked.

## The question

> **Does this fixed structure, pocket and preparation protocol rank measured
> actives above measured inactives for this target?**

That is the whole claim under test. In particular the gate does **not** ask
whether docking improves the M9 ranking: that needs the model's scores and a
control for what the model already knows, and it is deferred to a separate
evaluation. A passing gate licenses *this protocol on this cohort* and nothing
wider.

## 1. Choosing the target

Selected on measured coverage and structural suitability, before any docking was
run. Candidates were every target with at least 100 eligible actives and 100
eligible inactives under `ki-pki6-v2`, ranked by the smaller of the two counts.

| Target | UniProt | Curated len | Actives | Inactives | Verdict |
| --- | --- | ---: | ---: | ---: | --- |
| **Carbonic anhydrase 2** | P00918 | 260 | **6,787** | **2,210** | **selected** |
| Carbonic anhydrase 1 | P00915 | 261 | 4,737 | 3,898 | runner-up; same structural profile, better balance |
| Thrombin | P00734 | 622 | 2,346 | 2,239 | rejected — construct mismatch |
| Factor Xa | P00742 | 488 | 2,495 | 1,400 | rejected — construct mismatch |
| Carbonic anhydrase 9 | Q16790 | 459 | 4,905 | 1,231 | rejected — only the catalytic domain crystallises |
| D2, A1, A2a, A3, CB1, CB2, δ/μ-opioid | — | 318–472 | 2,268–6,332 | 1,558–2,941 | rejected — engineered GPCR constructs |

**Why the construct mismatch is disqualifying.** Our curated target sequences are
full UniProt precursors. Thrombin's curated sequence is 622 residues of
prothrombin; a thrombin crystal structure contains the light and heavy chains of
the mature enzyme, roughly 295 residues. Less than half the curated sequence is
present, so "this structure represents our curated target" would be a stretch
rather than a verified fact. The same applies to Factor Xa and CA9. The GPCRs
crystallise only as thermostabilised mutants or fusion constructs, which is the
same problem in a different form.

Carbonic anhydrase 2 has the opposite property, and it is the reason it won: the
deposited construct **is** the curated sequence.

**The risk we accepted, declared before scoring.** Carbonic anhydrase is a zinc
metalloenzyme, and nearly every ligand in the cohort carries a sulfonamide that
coordinates the catalytic Zn(2+) directly. Vina's scoring function is an
empirical sum of pairwise terms with no explicit model of metal coordination
chemistry. A failing gate here may therefore say more about the scoring function
than about docking as an approach. This is written down in advance precisely so
it cannot be produced afterwards as an excuse for a result we did not like.

## 2. Verifying the structure against our curated target

**PDB 3K34**, 0.90 Å, X-ray, single chain.

| Check | Result |
| --- | --- |
| SEQRES vs curated target 223 | **byte-identical, 260 / 260** |
| Mutations | 0 |
| Organism | *Homo sapiens*, matching the curated target |
| Missing residues | Met1, Ser2 — N-terminal, >20 Å from the zinc |
| Catalytic cofactor | Zn(2+) present; **kept** |
| Reference ligand | SUA, a benzenesulfonamide; its sulfonamide N sits **1.94 Å** from the Zn |

Removed, with the reason recorded for each:

| Removed | Count | Why |
| --- | --- | --- |
| SUA | 21 atoms | the reference ligand — the box is derived from it, so it cannot also occupy the site |
| GOL | 18 atoms (3 glycerols) | cryoprotectant; **one sits 4.8 Å from the Zn, inside the cleft** |
| HGB | 12 atoms | 4-(hydroxymercury)benzoic acid, a surface adduct 15.2 Å from the Zn |
| HOH | 249 | docked dry, see below |
| altloc B | 27 atoms | alternate conformations resolved by keeping altloc A |

**On the waters.** Docking dry is the weaker choice in general, and for carbonic
anhydrase the obvious worry is the zinc-bound solvent. In 3K34 that position is
occupied by the bound sulfonamide, not by water: the nearest water to the Zn is
5.97 Å away. So removing waters here does not remove a metal-coordinating water.
It does remove ordered waters lining the cleft, and that remains a limitation.

Both files are pinned by SHA-256 in the contract, with their RCSB download URLs.

## 3. Ground truth, and what is allowed to be a negative

Labels come from **`ki-pki6-v2`** (endpoint id 96, θ = pKi 6.0, BindingDB release
117). A pair is eligible when `status = 'ok'`, `in_benchmark_scope`, and not
`excluded_from_eval`.

* **Decisive censored records are negatives**, and keep their relation and bound.
  134 of the 300 inactives in the cohort are censored, so dropping them would
  have discarded nearly half the negative class and biased it toward whichever
  compounds somebody chose to measure exactly.
* **Ambiguous or contradictory evidence is never a negative.** For this target
  that excludes 34 ambiguous censored pairs and 15 with an exact value outside
  its censored bounds. They are not re-admitted here under any guise.
* **An unmeasured pair is never a negative.** The demonstration library does not
  appear in this milestone at all.

### The frozen cohort

`reports/results/m10_cohort.json`, frozen before any docking score existed, and
protected against replacement — a conflicting rewrite refuses.

| | |
| --- | --- |
| Name | `ca2-balanced-600` |
| Method | simple random sample without replacement within class, seed 20261001 |
| Actives | 300, all from exact records |
| Inactives | 300 — 165 exact, 134 decisive censored, 1 both |
| Drawn from | 6,787 eligible actives / 2,210 eligible inactives |
| Membership digest | `f2797290bbe71f36…` |

**Chemical coverage and its limits.** These are BindingDB's measured carbonic
anhydrase ligands, which are overwhelmingly aryl sulfonamides: median 25 heavy
atoms, median MW 390, median 5 rotatable bonds for actives (22 / 325 / 4 for
inactives). That makes the discrimination task narrow and hard in a specific way
— it is mostly "which sulfonamide binds tightly", not "sulfonamide versus
non-binder" — and it is not a scaffold-diverse screening library. A gate result
here does not transfer to a library of unrelated chemotypes. Balancing the
classes was a precision decision, not a prevalence claim: the eligible pool is
75% active, and AUROC is prevalence-independent.

## 4. Declared before scoring

| Declaration | Value |
| --- | --- |
| Score direction | **lower Vina energy is better**; the ranking score is `-1 × best-pose affinity`, so higher always means "predicted to bind more tightly" |
| Primary metric | AUROC, tie-corrected |
| Uncertainty | class-stratified bootstrap, 10,000 resamples, 95% percentile interval, seed 20261001 |
| Effect threshold | **AUROC 0.70** |
| **Pass** | `ci_low > 0.5` **and** `auroc ≥ 0.70` |
| **Fail** | `ci_high < 0.70` — which covers both no separation and separation in the wrong direction |
| **Inconclusive** | anything else: the interval straddles 0.70 and the cohort cannot decide |
| Wrong direction | `auroc < 0.5` fails outright, whatever its significance |
| Minimum usable | 50 per class after attrition, else `inconclusive_insufficient_data` |
| Failed preparation or docking | **dropped and reported by class**, never scored as "worst" |

Why 0.70 rather than "better than random": a gate that passes at AUROC 0.52 would
license a protocol that cannot triage anything. 0.70 means a random active
outranks a random inactive 70% of the time, which is the lowest we would act on.
Bootstrap stratified on class so the class balance cannot wander between
replicates and widen the interval for a reason unrelated to separation.

**A two-sided Mann–Whitney U is reported but can never make the gate pass.** It
is indifferent to direction, so a gate resting on it would accept a protocol that
ranks inactives first. It appears alongside the median ranking score of each
class, so the direction is always visible. EF@1%, EF@5% and BEDROC(α=20) are
reported as secondary early-recognition measures.

**Scoring a failed compound as "worst" would manufacture separation.** If
preparation fails more often for large flexible actives, filling their scores
with the minimum would invent exactly the ordering the gate is testing for. They
are dropped, and the attrition table is reported per class so a biased dropout is
visible rather than hidden.

## 5. The engine

| | |
| --- | --- |
| Engine | AutoDock Vina **1.2.7**, native `mac_aarch64` |
| Digest | `823c2bbacf26d721…`, verified before every run |
| Source | the project's GitHub release |

The `vina` on this machine's PATH is **1.1.2, x86_64, dated May 2011**, running
under Rosetta. It is not used, and the runner refuses it by digest. vina.scripps.edu
states it is the legacy site for that version and points to 1.2.x; 1.2.7 is the
current release. Verifying the binary is not ceremony: an emulated 2011 build has
a different scoring function and different defaults, and a score produced by it
is not comparable to anything.

Preparation is `meeko` 0.8.0 with `gemmi` 0.7.5 and RDKit 2026.03.6, all pinned
in `uv.lock`. **The receptor PDBQT is checked for the Zn atom type before
docking** — meeko silently drops atoms it cannot type, and for a metalloenzyme
that is the difference between docking into the real site and docking into a hole.

## 6. Protocol, fixed from structural information alone

No measured label was consulted to choose any protocol setting, so no separate
development cohort was needed. The two inputs were the reference-ligand redocking
QA and a runtime pilot whose 24 ligands were **all actives** — nothing about
discrimination was visible.

| Setting | Value | Why |
| --- | --- | --- |
| Box | 20 Å cube on the SUA centre of mass `(-5.560, 5.030, 13.030)` | contains the Zn and the whole cleft |
| Exhaustiveness | **8** (Vina's default) | 8, 16 and 32 recovered the SUA pose at 1.54 / 1.54 / 1.51 Å — the extra search bought no accuracy, and the pilot measured 88 CPU-s per ligand at 16 |
| Poses per ligand | 9 | Vina's default |
| Seed | 20261001 | fixed, recorded |
| CPU | 1 per ligand | parallelism is across ligands |
| Ligand prep | one ETKDGv3 conformer, MMFF-optimised, seed 20261001 | single conformer; no tautomer or protonation enumeration |
| Stereochemistry | as curated; unspecified centres left unspecified | we do not invent a configuration the measurement did not record |

Known weaknesses of this preparation, stated rather than discovered later: one
conformer per ligand, no tautomer enumeration, no pH-based protonation model, a
rigid receptor with no flexible side chains, and no explicit metal-coordination
term. Each is a reason the gate could fail for a preparation reason rather than a
docking reason.

## 7. Pose-recovery QA, reported separately

`reports/results/m10_qa_redock.json`. The reference ligand is rebuilt from SMILES
and re-embedded, never from the deposited coordinates — otherwise the search
would start at the answer and the RMSD would measure nothing.

This is deliberately **not** part of the gate. Pose recovery says the search can
find the crystallographic minimum when one exists; the gate asks whether the
protocol can rank one ligand above another. A protocol can do the first perfectly
and the second at chance.

## 8. Auditability

Run-scoped artifacts under `data/m10/runs/<label>/`, with every destination
preflighted before the first ligand is prepared. Each run writes per-ligand
scores and per-ligand failures, so every metric can be recomputed without docking
again. The run record binds the result to the contract, receptor, cohort and
engine by digest.

The cohort's identity is split in two deliberately: `selection_sha256` covers
only the fields that decide *which* compounds are in it, while `config_sha256`
records which contract version built it. Tightening the exhaustiveness must not
invalidate a frozen cohort; changing the endpoint or the sampling must.

Box-size sensitivity (±25%) is a **separately labelled analysis**, reported after
the primary result. It is not a way to choose the best gate score.
