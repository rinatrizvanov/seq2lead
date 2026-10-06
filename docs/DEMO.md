# Ranking demonstration

## Two ways to inspect the demo

The static historical output is `reports/examples/rank_demo.txt`. It shows what the M9 CLI returned; it is a recorded example, not a new inference or evidence of novel binders.

Live ranking needs the local PostgreSQL corpus, registered frozen library, checkpoint and owned binding sidecar/embedded binding, the exact bound feature files, and the pinned ESM-2 model. These are not bundled in the review tree. No web UI or hosted service is implemented.

## Live CLI

Supply a single-protein FASTA at `target.fasta`. In the full original tree, the recorded demo checkpoint path is:

```bash
uv run seq2lead rank --sequence-file target.fasta \
  --library curated-ki-25k-v1 \
  --model data/m9/final/M9-dual-encoder__cold_protein-v3__seed20260930.pt \
  --top-k 20 --evidence-mode evaluation --output reports/examples/my_ranking.txt
```

The CLI uses the affine predicted-pKi score, not raw cosine. It refuses missing or incompatible feature bindings rather than choosing current caches. The checkpoint comes from M9 cold-protein selection by validation RMSE. The demonstration library is a frozen ordered eligible prefix, not representative sampling.

For displaying historical measured evidence, deliberately choose `--evidence-mode demo`. That evidence is separate from the prediction, retains censoring, and is not a prospective validation. Evaluation mode suppresses it because a partition-filtered evidence API is unimplemented.

Sequences above the checkpoint's recorded 40,000-residue refusal point are refused; proteins beyond the 1,022-residue training window are flagged. Predicted pKi is neither a calibrated probability nor experimental proof of binding.

Keep ranked outputs separate from accepted scientific artifacts. This demonstration does not run docking or select an experimental test set.
