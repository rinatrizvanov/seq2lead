# Verifying the published copy

The published copy is a subset of the full working tree. Two kinds of
evidence are deliberately absent for size, and the verification tooling names
them rather than reporting their checks as done.

## What you can verify from the published copy alone

```
python -m seq2lead.asof.recompute       data/asof/m11h/run-20261005T134842Z
python -m seq2lead.asof.verify_results  data/asof/m11h/run-20261005T134842Z
python -m seq2lead.asof.publish         data/asof/m11h/run-20261005T134842Z
```

| Check | Available here |
| --- | --- |
| The saved predictions reproduce the published `results.json` **byte-for-byte** | **yes** |
| An independent AUROC, written from scratch, agrees with the metric module | **yes** |
| Predictions match the digest the fit recorded | **yes** |
| The evaluation table matches the digest the fit recorded | **yes** |
| Every prediction row aligns with its pair | **yes** |
| The published report regenerates from the saved predictions | **yes** |
| The verification record is bound to the current input digests | **yes** |
| Each declared refusal (tampered table, stale record, laundered digest) | **yes** — `pytest tests/test_m11h_publish.py tests/test_m11h_recompute.py` |

## What you cannot verify from the published copy alone

| Missing evidence | Why it is absent | What cannot be checked |
| --- | --- | --- |
| **Model checkpoints** (`…/checkpoints/*.pt`, ~43 MB) | excluded for size | that each checkpoint's bytes match the digest the fit recorded. Their digests are in `configs/manifests/m11h_fit.json` under `expected_fit_artifacts.digests`, so a holder of the full tree can check them; this copy cannot. |
| **Training-membership export** (`data/asof/m11f/a-membership.jsonl`, 433 MB) | excluded for size | the **training-overlap check** — that no evaluation pair outside the recurrent stratum appears in the training set. The full-tree run recorded 4,184 recurrent pairs genuinely in training and **0** non-recurrent pairs in training; this copy cannot re-derive that. |
| **Feature caches and extensions** (`data/features/*.npz`, ~430 MB) | excluded for size | that the bound caches match their recorded digests. Not needed to recompute results, which read saved predictions only. |

Running `verify_results` here will report
`artifact_digests_unavailable` greater than zero and list the absent files under
`checks_not_performed`. **That is the intended behaviour**: an absent file makes
a check impossible, not passed, and the record says which.

## What is not in scope

No refitting, tuning, feature rebuild, download or docking is possible or
intended from the published copy. The run's scientific qualifications stand as
published: the study is **exploratory**, the Ki **pooling rule is provisional**,
the confirmatory freeze is **unsigned**, retrieval is **unbuilt**, evaluation
evidence display is **off**, and `label_reversal-v3` is **unscored**.

Start with `reports/m11h_results.md`. Its §0 lists every deliberate change made
after first publication; `data/asof/m11h/closeout-changes.json` is the full
record, with wording changes kept separate from numerical ones.
