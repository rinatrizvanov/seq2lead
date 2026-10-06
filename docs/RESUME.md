# Resume wording

Suggested project title: **Seq2Lead — Molecular ML and Reproducible Data Systems**

- Engineered a PostgreSQL/SQL pipeline for 3.23M curated BindingDB activity records and implemented GPU-accelerated PyTorch protein–ligand ranking with ESM-2 embeddings and chiral molecular fingerprints.
- Evaluated six model families with leakage-controlled historical train/validation partitions; built deterministic snapshot matching that achieved 0.90 GB peak RSS versus an estimated 25.3 GB unsharded requirement.
- Implemented content-bound feature caches and publication integrity checks that reproduce ranking results from saved predictions and reject altered labels, mismatched features and stale verification records.

Optional result-oriented sentence: **The best tested historical joint baseline achieved 0.789 macro AUROC across 123 rankable targets; the dual encoder did not improve that observed mean.**

Use two bullets on a one-page resume. These claims describe a personal research/engineering project, not employment or a peer-reviewed publication. MPS was the recorded GPU backend. CUDA support exists, but CUDA training, custom CUDA kernels, ESM fine-tuning, state-of-the-art performance and wet-lab validation are not demonstrated. The 28× figure compares a measurement with an estimate, not two full executions. Be ready to explain the split, censoring, SQL design, memory decomposition and integrity tests in an interview.
