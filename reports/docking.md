# M10 — docking validation gate

**Gate decision: FAIL.** Target Carbonic anhydrase 2 (human) (UniProt P00918), structure 3K34 at 0.9 Å, AutoDock Vina 1.2.7.

> AUROC 0.624 with 95% CI [0.578, 0.670]: the whole interval lies below the pre-registered threshold 0.7, so the practical effect is ruled out on this cohort.

The contract and its decision rule were frozen before this cohort was docked: `configs/experiments/m10-docking-v1.yaml` (`84b6515d5c0c656e…`), with the reasoning in `docs/DOCKING.md`. The question is whether **this** structure, pocket and protocol rank measured actives above measured inactives for **this** target. It is not evidence that docking improves the M9 ranking; that is a separate evaluation and was not attempted.

## The result

| | |
| --- | --- |
| Primary metric | AUROC **0.624** |
| Ranking score | **−1 × best-pose Vina energy**, so a higher score means predicted to bind more tightly. Raw energies in kcal/mol are retained in `scores.json` as `affinity_kcal_per_mol`. |
| 95% CI (10,000 class-stratified bootstrap resamples) | [0.578, 0.670] |
| Pre-registered effect threshold | 0.7 |
| Decision | **FAIL** |
| Scored | 286 active / 292 inactive |

### Secondary, and none of it can change the decision

| Measure | Value |
| --- | --- |
| Mann–Whitney U (two-sided) | U = 52,116, p = 2.46e-07 |
| Median ranking score, actives | 8.145  (= −8.145 kcal/mol Vina energy) |
| Median ranking score, inactives | 7.739  (= −7.739 kcal/mol Vina energy) |
| EF@1% | 1.35 |
| EF@5% | 1.32 |
| BEDROC (α=20) | 0.629 |

The Mann–Whitney test is two-sided and therefore indifferent to direction. A gate resting on it would accept a protocol that ranks inactives first, so it is reported next to each class's median ranking score and never used to decide.

**What the interval does and does not cover.** The bootstrap resamples **individual compounds**, stratified on class. This cohort is largely congeneric — aryl sulfonamide series in which many members are close analogues. The interval and p-value use an analysis that does not account for dependence among chemical analogues. Their calibration under that dependence is unknown.

The pre-declared statistics and the gate decision stand exactly as pre-registered. A separately labelled post-hoc analysis at the scaffold or series level would be a legitimate way to probe this, and was not performed here; nothing in this report should be read as generalising beyond this target and this chemical series.

## Attrition, by class

| Class | Scored | Failed | Stages |
| --- | ---: | ---: | --- |
| active | 286 | 14 | docking: 2, embed: 1, engine_output: 1, pdbqt: 10 |
| inactive | 292 | 8 | docking: 2, parse: 1, pdbqt: 5 |

600 requested, 578 scored, 22 dropped. A compound that fails preparation or docking is **dropped and counted here**, never scored as "worst" — filling failures with the minimum would invent exactly the ordering the gate is testing for. The per-class split is reported so a dropout that hits one class harder is visible.

### The dropout is chemically systematic, not random

| Blocking feature | Compounds dropped |
| --- | ---: |
| `Se` | 11 |
| `B` | 4 |
| `Se,Te` | 2 |
| `Te` | 2 |
| `no exotic element (embed)` | 1 |
| `no exotic element (engine_output)` | 1 |
| `unparseable SMILES` | 1 |

**19 of the 22 dropped compounds** carry an element the preparation toolchain could not type — 4 with B, 11 with Se, 2 with Se,Te, 2 with Te. Each compound is counted once: an earlier draft summed per-element tallies and reported 21, double-counting the two compounds containing both selenium and tellurium. The table above and this total are generated from `failures.json` and the frozen cohort, so they cannot drift apart again.

Organoselenium and boronic-acid carbonic-anhydrase inhibitors are real, measured ligand classes, and no compound containing selenium, tellurium or boron survived into the scored set. So the compounds that were scored are not a random sample of the frozen cohort — they are the subset this toolchain can represent, which is the kind of dropout that can bias a gate.

**Scope of that claim.** It describes what was observed from meeko 0.8.0 writing PDBQT and AutoDock Vina 1.2.7 reading it, at the versions pinned in `uv.lock`, with RDKit 2026.03.6 building the conformer. The messages were `atom number N has None type` from meeko and `Atom type B is not a valid AutoDock type` from Vina. It is not a claim about AutoDock file formats in general, about other preparation tools, or about other versions of either.

### Could the dropout have rescued the gate? No

AUROC counts concordant (active, inactive) pairs, so pairs involving a dropped compound are simply unknown. Resolving **every** unknown pair in favour of the actives gives the highest AUROC the full frozen cohort could have produced; resolving every one against them gives the lowest.

| | AUROC |
| --- | ---: |
| Observed, on what was scored | 0.624 |
| Best case over the full 300/300 cohort | **0.651** |
| Worst case over the full 300/300 cohort | 0.579 |
| Pairs unknown | 6,488 of 90,000 (7.2%) |

Even the optimistic bound stays below the pre-registered threshold 0.7, so the attrition is not what failed this gate.

## The measured cohort

| | |
| --- | --- |
| Name | `ca2-balanced-600` |
| Endpoint | `ki-pki6-v2` (id 96), θ = pKi 6.0 |
| Selection | simple_random_sample_without_replacement_within_class, seed 20261001 |
| Members | 300 active / 300 inactive |
| Evidence | active/exact 300, inactive/both 1, inactive/censored 134, inactive/exact 165 |
| Eligible pool | 6,787 active / 2,210 inactive |
| Membership digest | `f2797290bbe71f36…` |

Frozen before any docking score existed, and protected: a conflicting rewrite refuses. Decisive censored records are negatives and keep their bounds — 134 of the 300 inactives are censored, so discarding them would have thrown away a large part of the negative class. Ambiguous or contradictory evidence is never a negative, and an unmeasured pair never enters at all.

## Structure and preparation

| | |
| --- | --- |
| Structure | 3K34, 0.9 Å, X-RAY DIFFRACTION |
| Sequence vs curated target | **byte-identical, 260/260** |
| Mutations | 0 |
| Missing residues | MET1, SER2 |
| Receptor atoms kept | 2,060 |
| Cofactor kept | ZN (1) |
| Heteroatoms removed | GOL 18, HGB 12, HOH 249, SUA 21 |
| Alternate conformations | kept altloc A, 27 atoms dropped |
| Box | 20 Å cube on (-5.56, 5.03, 13.03), from the reference ligand |
| Exhaustiveness / poses / seed | 8 / 9 / 20261001 |

The prepared receptor is checked for the Zn atom type before docking: meeko silently drops atoms it cannot type, and a zinc enzyme without its zinc is a different experiment. Why each heteroatom was removed is in `docs/DOCKING.md`; the one that matters is a glycerol sitting 4.8 Å from the catalytic zinc.

## Pose-recovery QA, reported separately

| | |
| --- | --- |
| Reference ligand | SUA |
| Top-pose RMSD to crystal | **1.514 Å** (threshold 2.0 Å) |
| Best of the returned poses | 1.493 Å, rank 6 |
| Zn contact, crystal vs docked | 1.94 Å vs 2.19 Å |
| Pose recovered | yes |

The reference ligand is rebuilt from SMILES and re-embedded, never from the deposited coordinates, so the search does not start at the answer it is asked to recover. Pose recovery only. It does not license the ranking the gate measures.

## Box-size sensitivity — a separately labelled analysis

| Box | AUROC | 95% CI | Decision under the same rule | Scored |
| --- | ---: | --- | --- | ---: |
| 20 Å (**primary**) | 0.624 | [0.578, 0.670] | **FAIL** | 286+292 |
| 15 Å (0.75×) | 0.607 | [0.561, 0.653] | fail | 286+292 |
| 25 Å (1.25×) | 0.621 | [0.574, 0.666] | fail | 287+292 |

Run **after** the primary result and reported as a robustness check, not as a menu. The headline is the pre-declared 20 Å box whatever these rows say.

As it happens the pre-declared box is also the best-scoring of the three, so the headline is not the worst case being reported out of obligation — and it fails anyway. Every box tested lands in the same place, which is what makes the conclusion a property of the protocol rather than of one arbitrary choice.

## Provenance

| Artifact | Digest |
| --- | --- |
| Contract | `141d200eb8ee76e9…` |
| Engine (AutoDock Vina v1.2.7) | `823c2bbacf26d721…` |
| Prepared receptor | `b356cdd24da4dc04…` |
| Cohort membership | `f2797290bbe71f36…` |
| Per-ligand scores | `a1a869f82deb9ca6…` |
| Per-ligand failures | `9dd18fa93a53da04…` |

Measured budget: 578 ligands docked in 48 minutes wall-clock on 9 parallel single-CPU workers. Per-ligand scores and failures are saved, so every metric here recomputes without docking again. Wall-clock is **not** comparable between the runs in the sensitivity table: other work shared the machine during some of them, which is why a smaller box can show a longer time. Vina is seeded, so the scores are unaffected.

## What this does and does not establish

A **FAIL** applies to **this protocol on this cohort**. It says nothing about whether adding docking to the M9 ranking would help: that comparison needs the model's scores and a control for what the model already knows from training, and it is deliberately left to a separate evaluation.

- The cohort is BindingDB's measured carbonic anhydrase ligands, which are overwhelmingly aryl sulfonamides. The task is largely "which sulfonamide binds tightly", not "binder versus non-binder", and the result does not transfer to a scaffold-diverse library.
- Carbonic anhydrase is a zinc metalloenzyme and Vina's scoring function has no explicit metal-coordination term. This was declared in the contract before scoring, and it limits what a result on this target can be read to mean about docking in general.
- One conformer per ligand, no tautomer enumeration, no pH-based protonation model, a rigid receptor and no waters. Any of these could cost a compound its pose independently of whether it binds.
- Classes were balanced for precision at a fixed compute budget. The eligible pool is 75% active, so the cohort is not a prevalence estimate.
- With the toolchain used here — meeko 0.8.0 and Vina 1.2.7 at the pinned versions — selenium, tellurium and boron could not be typed, so 19 measured compounds across three ligand classes were never scored. The gate therefore speaks only for the chemistry this toolchain can represent. A library containing those elements needs a preparation path that handles them, not a different threshold; whether other tools or later versions do was not tested.
- A single structure. Conformational selection across several structures of the same target is not tested here.

Published whatever the outcome, which is the point of running it as a gate rather than as a result we went looking for.

## Verified before publishing

This report was refused unless the published numbers recomputed from the saved per-ligand scores under the recorded contract. Checks that passed:

- cohort contents match the contract pin
- all 578 scored rows match the cohort's ids and labels
- ranking_score == -affinity for every scored row
- all 22 failure rows match the cohort's ids and labels
- 578 scored + 22 failed = 600 cohort members, each exactly once
- gate recomputed from the saved scores under the recorded contract
- attrition table, element breakdown and bounds recomputed and matched
- produced under declared superseded contract 141d200eb8ee76e9…
- manifest satisfies verification contract m10-verification-v1 (26 artifact(s))
- sensitivity row 15 Å (0.75×) recomputed from its own saved run
- sensitivity row 25 Å (1.25×) recomputed from its own saved run
- QA artifact matches the digest the manifest bound
- QA pose RMSD independently recalculated from the saved pose (1.514 Å), without re-docking

Recomputed AUROC 0.6240600153 against the published value, and the decision **fail** independently reproduced.
