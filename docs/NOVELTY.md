# Contribution and related work

The defensible positioning is an **auditable historical bioactivity benchmark and negative-result case study with a sequence-based ranking implementation**. It is not a newly invented dual-encoder family, diffusion model or demonstrated state-of-the-art predictor.

## Closest comparisons

| Work | Task | Relation to Seq2Lead |
| --- | --- | --- |
| ConPLex, Singh et al. (2023) | Sequence-based drug–target prediction using PLM representations and contrastive coembedding | Closest architectural antecedent; Seq2Lead uses frozen ESM-2/chiral ECFP4 and exact-pKi MSE with an affine cosine head, without the contrastive stage |
| Papyrus, Béquignon et al. (2023) | Curated bioactivity dataset with modelling and random/temporal splits | Prior art for data standardisation, quality filtering and temporal benchmarking; Seq2Lead focuses on counted changes between two BindingDB releases and fit-bound publication integrity |
| PLINDER, Durairaj et al. | Structure-based protein–ligand dataset/evaluation resource with multi-level similarity annotation | Prior art for similarity-aware evaluation and leakage concerns; a different structural-data setting |
| DiffDock, Corso et al. (2023) | Generate ligand docking poses relative to a protein structure | Pose prediction, not Seq2Lead's sequence-based ranking of an existing library |
| ProtoDiff, Zheng et al. (2026) | Few-shot molecular image generation via diffusion | Different output/task: generating molecular images, not ranking measured library compounds |

"ProtoDiff" here refers specifically to DOI `10.1109/TCBBIO.2025.3638310`; other methods share that name. If a different paper was intended, its link is needed before comparing it.

No external method was run under Seq2Lead's common protocol. This is a conceptual comparison, not an empirical performance claim against ConPLex, Papyrus, PLINDER, DiffDock or ProtoDiff. ConPLex itself also discusses affinity prediction, so affinity regression alone is not a novelty claim.

## Evidence-supported claims

- A complete implementation combining exact/censored semantics, release matching, evidence invariance, feature resolution and publication verification.
- A quantified case study of model ranking under recurrent versus new-to-fitting cohorts.
- A bounded-memory implementation preserving the accepted matcher, with recorded equivalence tests and resource measurements.
- A preserved negative architecture comparison and failed standalone docking gate.

Do not claim "first", "novel architecture", "leakage-free", "prospectively validated", "better than ConPLex", or "new drug discovery". Broad literature novelty requires a fuller review than this targeted comparison.

## Primary references

1. Singh R, Sledzieski S, Bryson B, Cowen L, Berger B. *Contrastive learning in protein language space predicts interactions between drugs and protein targets*. PNAS (2023). https://doi.org/10.1073/pnas.2220778120 ; https://github.com/samsledje/ConPLex
2. Béquignon OJM et al. *Papyrus: a large-scale curated dataset aimed at bioactivity predictions*. J Cheminform 15, 3 (2023). https://doi.org/10.1186/s13321-022-00672-x
3. Durairaj J et al. *PLINDER: The protein-ligand interactions dataset and evaluation resource*. Preprint. https://doi.org/10.1101/2024.07.17.603955 ; https://github.com/plinder-org/plinder
4. Corso G et al. *DiffDock: Diffusion Steps, Twists, and Turns for Molecular Docking*. ICLR (2023). https://arxiv.org/abs/2210.01776
5. Zheng W et al. *ProtoDiff: Prototypical Diffusion Model for Few-Shot Molecular Image Generation*. IEEE Trans Comput Biol Bioinform 23(1), 224–235 (2026). https://doi.org/10.1109/TCBBIO.2025.3638310

ConPLex and ProtoDiff descriptions were checked against primary author/paper sources; Papyrus against the article; PLINDER against its official repository and preprint record. This is a targeted search, not a systematic review.
