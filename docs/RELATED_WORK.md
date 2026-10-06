# Related work

Seq2Lead ranks compounds from an existing library against a protein sequence, and
evaluates that ranking on a historical BindingDB snapshot pair. This page places it
against the nearest prior work and states plainly what is and is not claimed.

No external method was run under Seq2Lead's protocol. Everything below is a
comparison of designs and scope, not a measured comparison of performance. A
targeted reading of five works is not a systematic review.

## Nearest prior work

| Work | What it does | How Seq2Lead relates |
| --- | --- | --- |
| **ConPLex** — Singh et al. (2023) | Sequence-based drug–target prediction from protein-language-model representations, with contrastive coembedding | The closest architectural antecedent. Seq2Lead's dual encoder is the same shape — independent towers into a shared space — but uses frozen ESM-2 with chiral ECFP4, trains on exact-pKi squared error through an affine cosine head, and implements no contrastive stage. ConPLex also addresses affinity prediction, so affinity regression is not a point of difference. |
| **Papyrus** — Béquignon et al. (2023) | A large curated bioactivity dataset with standardisation, quality filtering and random and temporal splits | Prior art for the curation and temporal-split concerns Seq2Lead also has. Seq2Lead differs in unit of analysis: it counts observation-level changes between two dated BindingDB releases and binds published metrics to the fitted inputs, rather than publishing a curated dataset. |
| **PLINDER** — Durairaj et al. (preprint) | A structure-based protein–ligand dataset and evaluation resource with multi-level similarity annotation | Prior art for similarity-aware evaluation and leakage control. A structural setting; Seq2Lead is sequence-based and does not use structure except in the separate docking assessment. |
| **DiffDock** — Corso et al. (2023) | Generates ligand poses against a protein structure by diffusion | A different task. Pose generation, not ranking an existing library. Relevant only as context for the separate docking assessment, which uses AutoDock Vina rather than a learned pose model. |
| **ProtoDiff** — Zheng et al. (2026) | Few-shot molecular *image* generation by diffusion | A different task and output. Listed because the name recurs in this area; this entry is specifically DOI `10.1109/TCBBIO.2025.3638310`, and other methods share the name. |

## What this project claims

- A complete, inspectable implementation in which exact and censored measurements
  are kept apart, release-to-release changes are counted rather than assumed,
  features are bound to the content they were computed from, and published metrics
  are refused when they are not bound to the inputs that produced them.
- A measured case study of how ranking performance differs between pairs that were
  supplied to fitting and pairs that were not.
- A bounded-memory implementation of the snapshot matcher that calls the same
  tested matching code as the unsharded path, with recorded equivalence checks and
  resource measurements.
- Two preserved negative results: a dual encoder that did not improve on a simple
  concatenation baseline, and a docking protocol that failed its pre-registered
  ranking gate.

## What this project does not claim

It does not claim a first, a novel architecture, a leakage-free benchmark,
prospective validation, better performance than ConPLex or any other named method,
or a contribution to drug discovery. Protein-language-model dual encoders and
temporal bioactivity benchmarks both have prior art. Whether any part of the
evaluation design is novel in the wider literature would require a fuller review
than the five works above.

## References

1. Singh R, Sledzieski S, Bryson B, Cowen L, Berger B. *Contrastive learning in protein language space predicts interactions between drugs and protein targets*. PNAS (2023). <https://doi.org/10.1073/pnas.2220778120> · <https://github.com/samsledje/ConPLex>
2. Béquignon OJM et al. *Papyrus: a large-scale curated dataset aimed at bioactivity predictions*. J Cheminform 15, 3 (2023). <https://doi.org/10.1186/s13321-022-00672-x>
3. Durairaj J et al. *PLINDER: the protein–ligand interactions dataset and evaluation resource*. Preprint. <https://doi.org/10.1101/2024.07.17.603955> · <https://github.com/plinder-org/plinder>
4. Corso G et al. *DiffDock: diffusion steps, twists, and turns for molecular docking*. ICLR (2023). <https://arxiv.org/abs/2210.01776>
5. Zheng W et al. *ProtoDiff: prototypical diffusion model for few-shot molecular image generation*. IEEE Trans Comput Biol Bioinform 23(1), 224–235 (2026). <https://doi.org/10.1109/TCBBIO.2025.3638310>

ConPLex and ProtoDiff were checked against primary author and paper sources,
Papyrus against the article, PLINDER against its repository and preprint record.
