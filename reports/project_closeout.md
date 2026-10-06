# Seq2Lead project closeout

Prepared 2026-10-05 from the accepted M11h integrity review. This closeout changes documentation and packaging rules only; it does not refit, rescore, rebuild features, dock, or replace scientific manifests.

## Scientific outcome

Seq2Lead ranks a defined compound library from protein sequence and implements an auditable benchmark. The historical January–September comparison is exploratory: B was already inspected. The experiment tests model performance under explicit evidence and cohort rules; it does not validate a new drug lead.

| Finding | Recorded evidence | Bound on interpretation |
| --- | --- | --- |
| Dual encoder did not improve observed mean headline AUROC | 0.781229 versus concat-MLP 0.788873 | No paired target-level test; neither superiority nor equivalence established |
| Limited rankable coverage | 123 of 992 headline targets; 22,221 admitted pairs | Macro metrics describe targets meeting ≥5/class |
| Recurrent/new population contrast | B3L ≈0.9642 recurrent versus ≈0.6685 absent from A | Populations differ in chemistry, balance and coverage; not a causal exposure estimate |
| Docking gate failed | CA2 AUROC 0.624060 against 0.70; sensitivities also fail | One target, declared chemistry/toolchain; not a test of incremental docking benefit over ML |
| Pose recovery passed separately | RMSD 1.514 Å | Known-pose recovery is not affinity discrimination |
| Poolability not resolved | M5 matched analysis small/confounded and sensitive to cohort handling | All pooled-Ki conclusions remain provisional |

## Method and engineering contributions

1. Immutable source identities with raw evidence and exclusions retained.
2. Exact regression separated from decisive censored classes; endpoint contradictions and discordance explicit.
3. Several split settings with partition-specific temporal aggregation and train_only fitting.
4. Chiral compound features and revision-pinned protein embeddings bound to content manifests and storage identities.
5. Counted, deterministic snapshot matching that handles additions, removals, ambiguous correspondence and constrained correction candidates.
6. Independent A-training, actual full-B and eligible increment readings; row locators in audits.
7. Bounded-memory sharding that preserves matching semantics, with pair-level coarsening for eligibility.
8. Runner inputs derived from verified artifacts; vector-resolution maps and emitted datasets checked.
9. Saved-prediction scoring and publication checks against original fit identities and current verification bindings.

These are integrated contributions to a case study, not proof that each technique is novel. The closest architectural antecedent is ConPLex; Papyrus and PLINDER show that curation, temporal/similarity-aware evaluation and leakage concerns have prior art.

## Verification record

The owner-side final suite was reported as 1,318 passed, zero failures/errors/skips. Independent ZIP review checked 307-member CRC integrity, unchanged predictions, exact published-results reproduction, independent AUROC across 22 tags, and refusals for changed labels, stale authorisation and altered published metrics. It did not rerun the full suite or check omitted checkpoint/cache bytes or training overlap.

The final reviewed source ZIP SHA-256 is `8768c266cbb1728c9540b1fbad168f0e21aeced3d5d2dfddaf24abebf8bda692`. This documentation bundle has a different identity. Original scientific reports and input/output artifacts are carried unchanged.

## Remaining limitations

- Already-inspected historical B, unsigned prospective freeze: exploratory only.
- Ki pooling across heterogeneous assay contexts remains unresolved.
- Cross-slot equal values are candidate correspondence, not experimental identity.
- Primary and sensitivity branches encode unresolved evidence choices.
- No target-level paired uncertainty analysis for architecture differences; seed spread is not a confidence interval.
- Rankable targets are a selected minority; validation-reserved pairs are not untouched by selection.
- Fixed fingerprints are lossy; full-length protein embedding quality beyond pretraining crops is provisional.
- LightGBM cap binding and seed-variation mechanisms are recorded, not settled through extra refits.
- Retrieval is unbuilt; evaluation evidence is suppressed.
- Label-reversal diagnostic remains unscored.
- Docking analogue dependence has unknown uncertainty calibration; ligand-to-prepared-structure mapping for the old runs is unresolved.
- Artifact consistency checks do not provide external attestation of original fits.
- No prospective experimental validation of newly ranked compounds.

## Completion boundary

The exploratory project is closed. Future prospective evaluation, retrieval, assay-aware modelling or wet-lab validation should be separately scoped studies, not retroactive tuning of this result. Public release follows owner review of attribution, code/data/model terms, repository contents and manuscript claims.
