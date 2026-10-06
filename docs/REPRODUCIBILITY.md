# Reproduction and verification

For what the release as a whole does and does not let a reader reproduce — saved-prediction benchmark reproduction versus a full raw-to-fit rebuild, with the excluded artifacts measured and the re-obtainability of each pinned input stated — see [release scope](RELEASE_SCOPE.md).

## 1. Saved-prediction review — no database, vectors or refitting required

Use the repository root as the working directory: recorded paths are relative to it. Install the declared environment with `uv sync --all-groups`.

```bash
uv run python -m seq2lead.asof.verify_results data/asof/m11h/run-20261005T134842Z
```

The CLI writes `verification.json`. The Python `verify()` API is read-only. Available checks include the fit-recorded prediction/table digests, row alignment, reproduction of published `results.json`, and independent AUROC for all 22 model tags. In this review copy, absent checkpoints and the training membership file are named as unavailable; feature caches are also absent. A scoped passed verdict is not verification of those missing bytes.

To regenerate derived results and the report, use a disposable clone:

```bash
uv run python -m seq2lead.asof.recompute data/asof/m11h/run-20261005T134842Z
uv run python -m seq2lead.asof.verify_results data/asof/m11h/run-20261005T134842Z
uv run python -m seq2lead.asof.publish data/asof/m11h/run-20261005T134842Z
```

Recompute writes results only; verification binds its verdict to current digests; publication checks that binding and carries expected fit digests unchanged. No models are fitted. Do not use `--allow-unverified-inputs` for publication: it marks output NOT_PUBLISHABLE.

## 2. Targeted integrity tests

```bash
uv run pytest tests/test_m11h_publish.py tests/test_m11h_recompute.py --junit-xml=m11h-integrity.xml
```

These tests use temporary clones and test valid publication as well as refusal. Read their results; do not infer a total from truncated log dots.

## 3. Full historical refit — requires the full artifact set

The supported review reproduction above is distinct from rebuilding the experiment. The actual executed fitting code is preserved at `scripts/asof/m11h_fit.py`. It relies on the complete pinned inputs, database identities, feature caches/extensions, resolution maps and A-membership export. Inspect its preconditions and paths before invoking it; this copy alone cannot refit. Some preserved scripts have historical scratchpad assumptions. They are execution evidence, not an unconditional one-command installer.

A full rebuild must preserve the pinned curator, both source identities, endpoint semantics, partition seed/fraction, exact-only regression targets, clean validation-RMSE cohort, both feature bindings, role resolution and emitted count/digest checks. Output preflight must refuse collisions. No sweep, cutoff change, or train+validation refit is part of the accepted experiment.

## 4. Full-corpus pipeline entry points

The following are documented building blocks, not a promise that an arbitrary empty database reproduces all historical surrogate IDs:

```bash
uv run seq2lead ingest m2
uv run seq2lead curate assays
uv run seq2lead curate run --subset all
uv run seq2lead curate index
uv run seq2lead endpoint build --name ki-pki6-v2 --threshold 6.0
uv run seq2lead endpoint index
uv run seq2lead analyze assay-variance --endpoint ki-pki6-v2
uv run seq2lead features coverage
```

Existing immutable names refuse rebuilding. Resolve identities from the actual registry; do not overwrite accepted releases or infer them from newest timestamps. Some historical monthly URLs may no longer resolve; locally preserved archives and quarterly deposits matter. A data rebuild requires an explicit manifest/identity plan.

## 5. Verification scope

| Evidence | Review copy | Full local archive |
| --- | --- | --- |
| Predictions, evaluation table, published metrics | Available | Available |
| Checkpoint bytes | Unavailable | Digest-verifiable if present |
| Non-recurrent training-overlap check | Unavailable without A-membership | Re-derivable |
| Bound feature-cache bytes | Unavailable | Digest-verifiable |
| Docking pose/directories and raw structure | Unavailable | Verifiable under recorded M10 scope |

Reproducibility establishes consistency with saved evidence. It does not externally attest the original run or establish biological validity.
