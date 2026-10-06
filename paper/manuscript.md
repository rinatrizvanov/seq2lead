Seq2Lead historical evaluation of protein ligand ranking

Timur Rizvanov

Scientific manuscript draft · 5 October 2026 · Not submitted or peer reviewed

## Abstract

Sequence-based protein–ligand prediction requires a clear boundary between historical training evidence and later evaluation evidence. We developed Seq2Lead, a provenance-preserving BindingDB pipeline and compound-library ranking tool, and evaluated it in an exploratory historical snapshot study. January and September 2026 snapshots were curated under an identical pinned pipeline. Counted matching distinguished unchanged observations, additions, removals and constrained correction candidates. Training used January evidence alone; increment labels and complete September consistency checks remained separate. Models fitted exact-Ki regression targets and were evaluated with tie-aware per-target ranking metrics. In the primary new-to-fitting cohort, 22,221 pairs covered 992 targets, with 123 meeting the five-positive/five-negative scoring floor. Mean macro AUROC was 0.788873 for a concatenated-feature MLP and 0.781229 for an affine-cosine dual encoder. A separate carbonic-anhydrase-2 docking protocol failed its declared ranking gate despite successful known-pose recovery. The contribution is an auditable implementation and measured case study. Prior corpus exposure and provisional assay pooling preclude a confirmatory interpretation.

## Significance

The practical question is whether a screening result can be traced to the evidence, feature content and model selection that produced it. Seq2Lead makes these boundaries inspectable and preserves negative results. It demonstrates that familiar pairs can have substantially different ranking performance from later pairs, and that recovering a crystal pose does not establish useful affinity ranking. These findings support careful evaluation of sequence-based screening tools before experimental prioritisation.

## Introduction

Therapeutics Data Commons organises datasets, tasks and benchmarks for therapeutic machine learning [4]. ConPLex established protein-language-model coembedding [1]; Papyrus addresses bioactivity curation [2], and PLINDER addresses protein–ligand evaluation resources [3]. Seq2Lead connects these concerns in a release-aware BindingDB study. It ranks existing compounds from a protein sequence. The aim is to compare a dual encoder with simpler predictors under explicit evidence visibility, while recording how cohort construction and artifact integrity affect interpretation.

## Data and evidence design

Figure 1. Release-aware evidence flow. Historical training, added evaluation evidence and complete later-snapshot consistency are separate readings. Two increment interpretations and two consistency branches are retained; the primary cell is declared increment with screening.

![Figure 1. Release-aware evidence flow. Historical training, added evaluation evidence and complete later-snapshot consistency are separate readings. Two increment interpretations and two consistency branches are retained; the primary cell is declared increment with screening.](figures/figure_1_evidence_workflow.svg)

The local September archive loaded 3,237,046 of 3,237,052 data lines, quarantining six with original bytes retained. Curation produced 3,233,963 activity records. Ki, IC50, Kd and EC50 remained distinct. PostgreSQL stored source provenance and curated entities; pinned RDKit processing preserved stated stereochemistry. BindingDB provides the underlying measured affinity evidence [5]. The January deposit (DOI 10.6075/J0V40W61) was ingested and curated in an isolated schema, with read-only exports and source-release/row locators on both sides.

Equation: pKi = 9 − log₁₀(Ki in nM)     ;     active if pKi ≥ 6

Only exact Ki observations supplied regression targets, using the median exact pKi per pair. Censored measurements retained their relation and inclusivity and could decide a class without becoming a numerical target. Empty bound intersections, exact–bound conflicts, ambiguity and discordance were retained in the audit. Exact spread greater than one pKi unit excluded ranking and validation selection under their declared policies; training could retain an exact median. Ki assay poolability was not established, so pooling remained provisional.

The full-B consistency screen reads actual September evidence, including removals and replacements. It does not reconstruct B by appending additions to A. Later corrections and contradictions affect evaluation eligibility and audit records without editing historical training evidence.

## Representation and model architecture

Figure 2. Independent compound and protein towers with a trainable affine cosine head. Frozen molecular/protein representations may be computed for every role; learned transforms and model parameters use A-training only. The schematic omits internal layer widths and activation details.

![Figure 2. Independent compound and protein towers with a trainable affine cosine head. Frozen molecular/protein representations may be computed for every role; learned transforms and model parameters use A-training only. The schematic omits internal layer widths and activation details.](figures/figure_2_dual_encoder.svg)

Compounds used chiral ECFP4 fingerprints [6]: 2,048 logical bits stored as 256 packed uint8 values. Proteins used the frozen ESM-2 t33 650M model [7], with mean pooling over residues excluding special tokens and a pinned model commit. Full-length encoding was retained beyond the 1,022-residue training window, flagged as provisional; 40,000 residues was a refusal point. A cache identity combined the computation specification with content manifests. Historical-only feature extensions had separate identities and did not overwrite accepted caches.

Equation: ŷ = a · cos(zc, zp) + b     ;     L = (1/N) Σᵢ (ŷᵢ − pKiᵢ)²

The dual encoder projected both modalities into 512 dimensions and learned scale a and offset b in pKi units. Ranking used the affine score, including the possibility of negative a. Independent towers allow compound projections to be computed before a sequence query. The bounded cosine head limits expressiveness; contrastive training and cross-attention were not implemented. PyTorch neural-network runs used Apple’s MPS GPU backend. CUDA support in the code is not evidence of NVIDIA execution.

## Historical matching and fitting protocol

Context slots contained compound, target, normalised publication reference, pH, temperature and source. Canonical measurement values contained type, relation and value. Matching was count-aware: identical rows were indistinguishable observations rather than proof of duplicate experiments. A one-to-one removal/addition could be linked only under the declared identifier rule; ambiguous sets remained unresolved. Entry DOI was entry-level provenance, not a measurement identifier. Equal-valued observations at different slots were correspondence candidates, not proof of one experiment.

Deterministic BLAKE2b slot sharding invoked the same tested matcher for each shard. The recorded full comparison peaked at 0.90 GB RSS, compared with an estimated 25.3 GB unsharded requirement. Pair sharding was used for endpoint aggregation, keeping all evidence for a compound–target pair together. Ki yielded 585,978 unchanged, 33,953 added and 28,777 removed observations. Of the additions, 33,941 entered the declared increment and 12 linked correction after-values were withheld; all stayed in complete B.

### Evidence selection algorithm

1  Curate and export A and B under the pinned rules.
2  Match counted observations within context slots.
3  Derive A train/validation membership without reading B.
4  Label eligible additions; withhold linked correction occurrences.
5  Read actual B for the consistency screen and audit.
6  Derive recurrence from rows supplied to fitting.
7  Fit on A-train, restore the A-validation-selected model.
8  Score all declared cells and strata; verify before publishing.

| Role or cohort | Pairs | Purpose |
| --- | --- | --- |
| A-train exact regression | 353,957 | Transform and parameter fitting |
| A-validation clean regression | 60,981 | Checkpoint RMSE selection |
| Primary new to fitting | 22,221 | Headline ranking evaluation |
| Strictly absent from A | 19,517 | Separate exposure stratum |

A deterministic 15% pair reservation supplied validation. The 512-dimensional projection was carried from M9 validation selection with no new sweep. The dual encoder used learning rate 0.001, batch size 512, at most 20 epochs and patience five, restoring the best validation checkpoint. No train-plus-validation refit was performed. Architecture-specific stopping rules were retained; comparisons do not isolate architecture alone. LightGBM [8] runs reached the declared 400-round cap.

## Historical ranking results

Figure 3. Primary macro AUROC reproduced from saved predictions and labels. Each target contributes equally after meeting the minimum of five active and five inactive pairs. Family means summarise five seeded fits for LightGBM, concat MLP and dual encoder; target mean and target-conditioned 1-NN were fitted once. Seed ranges represent training variation only.

![Figure 3. Primary macro AUROC reproduced from saved predictions and labels. Each target contributes equally after meeting the minimum of five active and five inactive pairs. Family means summarise five seeded fits for LightGBM, concat MLP and dual encoder; target mean and target-conditioned 1-NN were fitted once. Seed ranges represent training variation only.](figures/figure_3_ranking_results.svg)

The concatenated-feature MLP reached mean AUROC 0.788873 and the dual encoder 0.781229, an observed difference of 0.007644. The dual encoder did not improve the observed mean. No paired target-level confidence interval, significance test or equivalence test was performed; overlapping seed ranges do not establish equivalence. Both joint configurations exceeded the observed single-modality configurations, but the comparison does not isolate a causal protein-feature effect.

Only 123 of 992 targets cleared the ranking floor; 869 did not. Macro AUROC therefore describes a selected subset with both classes and sufficient observations, not all targets. Pair prevalence was 0.697493, and average target prevalence in the scored subset was 0.543509. Average precision used entire tied threshold blocks, and AUROC gave ties half credit. A protein-only predictor is constant within a target and consequently gives AUROC 0.5 regardless of between-target score differences.

Six families produced 22 predictors. Distinct seeds did not produce identical prediction arrays, even where ranking aggregates were identical. B1 prediction differences were small; B2 predictions differed across seeds while all within-target rankings remained tied. The recorded fits cannot separate stochastic and numerical causes without same-seed repeats. No refits or tuning were undertaken in response to these results.

## Target diagnostics and evaluation strata

Figure 4. (a) Independent rank-sum AUROC calculations for each of the 123 headline targets, averaged across five seeds per family. The dashed line is equality, not a statistical test. (b) Descriptive contrasts between absent-from-A and recurrent populations; the 98 and 29 rankable-target sets differ. Lines connect summaries of different populations rather than longitudinal target trajectories.

![Figure 4. (a) Independent rank-sum AUROC calculations for each of the 123 headline targets, averaged across five seeds per family. The dashed line is equality, not a statistical test. (b) Descriptive contrasts between absent-from-A and recurrent populations; the 98 and 29 rankable-target sets differ. Lines connect summaries of different populations rather than longitudinal target trajectories.](figures/figure_4_target_and_stratum_diagnostics.svg)

The target-conditioned ligand 1-NN searched compounds measured against the same target. It was not a purely ligand-only predictor. Its macro AUROC was 0.964204 on recurrent pairs and 0.668510 on pairs absent from A, a descriptive difference of 0.295694. The dual encoder also performed better on the recurrent population. Chemistry, coverage, label balance and exposure were not independently controlled, so these gaps are not causal estimates of memorisation.

Recurrence was defined against the exact rows emitted to model fitting. Historical presence alone was insufficient: pairs could be reserved for validation, lack regression targets or otherwise be absent from fitting. New-to-fitting includes validation-reserved pairs whose historical evidence participated in model selection. The stricter absent-from-A stratum is therefore reported separately. The validation-reserved subgroup had only six rankable targets and supports little interpretation.

The other three increment/screening cells remain in the saved results. Cross-slot sensitivity removes unambiguous correspondence candidates without calling them the same experiment. These alternatives were declared before eligibility reporting. The paper does not choose an arm retrospectively because it performs better. Feasibility floors are pragmatic requirements, not a statistical power calculation.

## Independent docking gate

Figure 5. Separate CA2 docking assessment. The 20 Å primary box failed the declared AUROC 0.70 gate. Its bar shows the recorded compound-bootstrap 95% interval; sensitivity bars are rounded summaries and show no inferred interval. The bootstrap does not account for analogue dependence, and its calibration under that dependence is unknown. Pose recovery is a separate check, not a structural image or ranking validation.

![Figure 5. Separate CA2 docking assessment. The 20 Å primary box failed the declared AUROC 0.70 gate. Its bar shows the recorded compound-bootstrap 95% interval; sensitivity bars are rounded summaries and show no inferred interval. The bootstrap does not account for analogue dependence, and its calibration under that dependence is unknown. Pose recovery is a separate check, not a structural image or ranking validation.](figures/figure_5_docking_gate.svg)

AutoDock Vina 1.2.7 [9] used CA2 structure 3K34, whose recorded target sequence matched the curated sequence. A balanced cohort of 600 compounds was frozen before scoring. Of these, 286 actives and 292 inactives scored. Primary AUROC was 0.624060 with recorded interval [0.578108, 0.670241]. Both sensitivity boxes also failed. Even maximising every unknown active–inactive comparison involving attrition gave a full-cohort upper bound of approximately 0.651, below the gate.

Failures were chemically selective. Nineteen unique compounds contained Se, Te or B implicated by the recorded atom-typing errors; per-element counts would double-count two Se/Te compounds. The cohort was overwhelmingly aryl sulfonamides, so generalisation to other chemistry or targets is unsupported. The recorded Mann–Whitney p-value is not a practical effect-size criterion and does not account for analogue dependence.

Known-ligand pose recovery reached 1.514 Å RMSD, independently recalculated from saved pose and crystal evidence in the full local tree. A per-ligand provenance binding from cohort structure to prepared PDBQT was not recorded for existing runs. This assessment does not show whether adding docking to the ML ranking would help; that requires a separate comparison controlling for the model’s existing information.

## Discussion and novelty

The contribution is an integrated, auditable implementation and case study: counted historical release changes, three explicit evidence readings, content-bound feature resolution, deterministic memory-bounded processing and publication checks that reject drift. The 28-fold comparison is measured peak RSS against an unsharded estimate, not a benchmark against competing systems. The software also supplies a sequence-query library-ranking demonstration, with model and representation bindings.

The underlying components have established precedents. ConPLex already supports protein-language-model coembedding [1], Papyrus already addresses bioactivity curation [2], and TDC and PLINDER already provide benchmark infrastructure [3,4]. ECFP, ESM-2, LightGBM and Vina are existing methods [6–9]. Seq2Lead introduces no claimed encoder architecture or state-of-the-art method. Its defensible novelty is the specific integration and measured release-aware evaluation, whose broader methodological originality would require further literature review.

### Reproducibility and publication integrity

Saved prediction arrays and the consumed evaluation table are checked against fit-recorded digests. The table digest covers labels, strata, eligibility and branch membership, not just pair identity and order. Verification records carry the digests they checked; publication re-hashes current inputs and refuses stale records. Expected input digests remain unchanged, while only derived outputs receive fresh hashes. Fresh scoring must match the published results bytes. Independent rank-sum AUROC provides a second calculation. These controls establish artifact consistency, not independent attestation of the original run.

### Limitations and future study

September labels and prior benchmark results were already inspected, and the prospective freeze is unsigned. The historical experiment is exploratory. Poolability across Ki assays, correspondence of cross-slot reports, and long-sequence embedding quality remain unresolved. Retrieval is unbuilt; evaluation evidence display is disabled; the label-reversal split is unscored. Rankability excludes most targets, and no prospective wet-lab validation or external-method comparison was performed. Seed spread is not uncertainty over the target population. A future confirmatory experiment requires a complete dated specification followed by an unseen release or independently withheld labels.

### Conclusion

Seq2Lead makes evidence visibility and recorded-artifact consistency inspectable in sequence-based protein–ligand ranking. In the tested historical configurations, the dual encoder did not improve the observed mean over the concat MLP. The separate CA2 docking protocol failed its declared ranking gate despite satisfactory pose recovery. Preserving these bounded findings is a useful result of the evaluation design.

## Availability and reproduction

The GitHub repository is pending private creation and owner review. The review package contains code, contracts, saved M11h predictions, evaluation labels and results. It excludes raw source archives, databases, cache vectors, checkpoints and the training membership export. Saved-result recomputation requires no model fit. Checks needing excluded artifacts must be reported unavailable in a fresh clone. The reproduction guide and demo guide distinguish this route from a full local rebuild.

uv sync --frozen
uv run python -m seq2lead.asof.recompute \
  data/asof/m11h/run-20261005T134842Z
uv run python -m seq2lead.asof.verify_results \
  data/asof/m11h/run-20261005T134842Z

Author affiliation, final contributor list, repository URL and any persistent release identifier remain to be confirmed before circulation. AI coding and review assistants were used extensively during implementation, auditing and drafting. The human author is responsible for the final code, evidence, interpretation and manuscript. This document is a draft technical report, not a submitted or peer-reviewed publication.

## References

1. Singh R, Sledzieski S, Bryson B, Cowen L, Berger B. Contrastive learning in protein language space predicts interactions between drugs and protein targets. PNAS. 2023. https://doi.org/10.1073/pnas.2220778120

2. Béquignon OJM et al. Papyrus: a large-scale curated dataset aimed at bioactivity predictions. Journal of Cheminformatics. 2023;15:3. https://doi.org/10.1186/s13321-022-00672-x

3. Durairaj J et al. PLINDER: The protein-ligand interactions dataset and evaluation resource. Preprint. 2024. https://doi.org/10.1101/2024.07.17.603955

4. Huang K et al. Artificial intelligence foundation for therapeutic science. Nature Chemical Biology. 2022;18:1033–1036. https://doi.org/10.1038/s41589-022-01131-2

5. BindingDB in 2024: a FAIR knowledgebase of protein-small molecule binding data. Nucleic Acids Research. 2025;53(D1):D1633–D1644. https://doi.org/10.1093/nar/gkae1075

6. Rogers D, Hahn M. Extended-connectivity fingerprints. Journal of Chemical Information and Modeling. 2010;50:742–754. https://doi.org/10.1021/ci100050t

7. Lin Z et al. Evolutionary-scale prediction of atomic-level protein structure with a language model. Science. 2023;379:1123–1130. https://doi.org/10.1126/science.ade2574

8. Ke G et al. LightGBM: A Highly Efficient Gradient Boosting Decision Tree. Advances in Neural Information Processing Systems. 2017;30. https://proceedings.neurips.cc/paper/2017/hash/6449f44a102fde848669bdd9eb6b76fa-Abstract.html

9. Eberhardt J et al. AutoDock Vina 1.2.0: New Docking Methods, Expanded Force Field, and Python Bindings. Journal of Chemical Information and Modeling. 2021;61:3891–3898. https://doi.org/10.1021/acs.jcim.1c00203
