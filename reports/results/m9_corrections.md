# M9 correction pass — change record

No model was refitted, no prediction changed, no database row altered. The
corrections are to inference binding, displayed evidence, artifact safety, one
unenforced limit, and to four claims that were wrong.

## Fixes

| # | Defect | Fix | Effect on results |
| --- | --- | --- | --- |
| 1 | `rank_library()` fell back to `_current_ecfp()` — "whatever compound cache is newest" — and checkpoints recorded no feature identity at all. | Every checkpoint now resolves a `FeatureBinding`: compound cache name with manifest and storage digests, plus the protein spec (model, commit, pooling, dtype, length policy, max length). No binding, no inference. The fallback is deleted. | **None.** The bound cache is the one the fallback happened to pick, so the demo ranking is byte-identical. Latent risk, not active corruption. |
| 2 | `_prior_evidence()` aggregated values without their relation, so `>10000 nM` — a decisive non-binder — displayed as `10000 nM`. | Exact and censored evidence kept apart; measurement type, relation and units preserved; activity ids exposed for traceability. | **Wording only in this demo** — all four displayed compounds had exact records. Corpus-wide the defect touched **650,516 of 3,233,963 records (20.1%)**; 14% of this very target's records are censored, so a deeper top-k would have hit it. |
| 3 | Checkpoints, records and predictions were written to fixed paths unconditionally; refusal came from the result registry only at the end, after everything was overwritten. | Run-scoped paths plus `preflight()` over the whole planned set before the first fit. Selection binds to the config digest, declared split set and declared search space. Training settings are built from the config, with unknown keys rejected. | No change to existing artifacts; all 20 runs verify against a frozen manifest. |
| 4 | 40,001 residues was accepted against a declared 40,000-residue refusal point. | Enforced before ESM-2 loads, using the checkpoint's recorded limit. Never truncates. | None; the demo query is 443 residues. |

## Claims corrected

| Claim | Was | Is | How verified |
| --- | --- | --- | --- |
| Final model size | 984,066 parameters | **2,230,274** | recorded in all 20 run records; matches arithmetic for h=512. 984,066 is the h=256 **pilot**. |
| Library shape | "contiguous block" | **ordered eligible prefix**: ids 1,347,358–1,496,949, a 149,592-id span holding 25,000 members, so **124,592 ids absent** | queried from the frozen member list |
| Previous test count | 461 | **419 passed, 0 failed, 0 skipped** | JUnit XML; the 461 came from counting dots in a `tail`-truncated log |
| Early stopping | "where this architecture stops transferring" | an observation from these fits under one learning rate, batch size and head | reframed; no new evidence claimed |

## Binding provenance

The 20 existing checkpoints predate the binding, so theirs was **recovered
retrospectively** from `configs/experiments/m9-dual-encoder-v1.yaml`, whose cache
identities are re-verified against `feature_version` and the vector files at load
time. Every checkpoint's stored `config_digest` was confirmed to match that
config, so the recovery is checked rather than assumed. Bindings are sidecars;
**checkpoint bytes and digests are unchanged**.

The M9 run manifest (`configs/manifests/m9_runs.json`) is likewise frozen
retrospectively. Unlike the M8 prediction manifest there is **no earlier
independent record** to recover digests from, because the first M9 pass wrote
none. It therefore establishes a baseline for detecting future drift; it cannot
attest that the bytes were unaltered between being written and being frozen.

## Verification

Full suite **445 passed, 0 failed, 0 skipped** (JUnit XML, pytest exit 0). One
earlier run of this pass had 1 failure: the new binding guard correctly refused a
synthetic checkpoint in an older test. Fixed by giving the fixture a real
binding, not by weakening the guard.

Unchanged and verified: M8 artifacts (4 digests plus 72 prediction files), M9
runs (manifest, 0 problems), 27 split assertions, 10 coverage checks, corpus
11,012 targets / 1,424,670 compounds. `label_reversal-v3` remains unscored.

---

# Follow-up review

Five points re-examined against the complete files on disk. Two were already
covered or were excerpt artefacts; four were confirmed defects and fixed. No
model was refitted; checkpoint and prediction bytes are unchanged.

| # | Point | Verdict | Where |
| --- | --- | --- | --- |
| 1 | Selection validation | **Confirmed defect, worse than described** — `check_selection_binding()` was called *only from tests*; the CLI did a name-only check. Missing fields also disabled every check. | `m9_artifacts.check_selection_binding`, wired into `cli.m9_test` |
| 2 | Protein representation | **Partly covered, one confirmed defect** — the esm2 cache does record a complete spec, so the `or plm.X` fallbacks never fired. But `verify()` compared digests only and never the spec, so a binding with right bytes and wrong pooling passed. | `binding.protein_spec_from_params`, `binding.verify` |
| 3 | Sidecar ownership | **Confirmed defect** — no checkpoint digest, so a sidecar was portable between checkpoints; `write_sidecar` overwrote unconditionally. | `binding.write_sidecar`, `binding.read_sidecar` |
| 4 | Runner preflight | **Confirmed gap** — no test exercised `score_test()` at all. | `test_m9_followup.py::test_the_runner_refuses_before_fitting_when_a_destination_is_occupied` |
| 5 | Tamper test | **Excerpt artefact, no fix needed** — the test on disk contains both the tampering operation and `problems = verify_manifest()`. | `test_m9_corrections.py::test_a_tampered_artifact_is_detected_by_the_manifest` |
| — | Superseded bound cache | **Confirmed defect** — `verify()` promised the checkpoint still uses its bound cache; `load_features()` then refused it, making that promise unreachable. | `rank.rank_library` now passes `allow_superseded=True` |

## Retrospectively recovered metadata

Two records were backfilled rather than regenerated. Both are documented in the
artifacts themselves.

- **Binding sidecars** now carry an envelope with the checkpoint's SHA-256, its
  name, and the provenance string. The protein spec is read from the esm2
  cache's own recorded params, not from module constants.
- **`m9_selection.json`** predated the binding fields. `config_sha256`,
  `declared_splits` and `declared_projection_dims` were recovered from the
  record's **own `entries`**, which list every (split, projection_dim) actually
  fitted, cross-checked against the config. Anything genuinely unrecoverable
  requires an explicit `--allow-legacy`, which emits a warning and records a note.

## Manifest re-freeze

Rewriting the sidecars changed their bytes, and the existing manifest caught it:
**20 "binding bytes changed" problems and zero checkpoint or prediction
problems**. That is how a deliberate metadata change was distinguished from
drift. The manifest was then re-frozen, with the old digests' disposition
recorded in its provenance.

## Verification

Full suite **473 passed, 0 failed, 0 skipped**, pytest exit code **0** checked
directly. Three earlier tests failed on the first run of this pass — one fixture
predating the tightened `chosen` requirement, two manifest tests detecting the
sidecar rewrite — and were corrected rather than relaxed.

Unchanged and verified: M8 artifacts (4 digests plus 72 prediction files), M9
checkpoints and predictions (manifest, 0 problems), 27 split assertions, 10
coverage checks, corpus 11,012 / 1,424,670.

---

# Second correction pass — three defects from the v3 review

All three were confirmed on disk before anything was changed. The third was
demonstrated live: a training record was edited, `verify_manifest()` was run, and
it reported **0 problems**.

## 1. The feature binding never reached production fitting

`cli.py::m9_test` called `score_test(..., progress=True)` with no `binding`
argument, so the runner's parameter defaulted to `None` and every checkpoint it
saved recorded `"feature_binding": null`. The binding machinery added in the
previous pass was real and tested, and nothing in the production path used it.
A checkpoint saved that way is only usable if someone later remembers to write a
sidecar by hand; inference refuses it otherwise.

Changed: `m9_test` now builds the binding with `binding.from_experiment(conn,
config)`, echoes the two cache names it resolved, and passes `binding=binding`.
`score_test()` now **requires** one and raises `BindingError` before the first
fit if it is missing, if `binding.experiment` disagrees with `config.version`, or
if `binding.config_sha256` disagrees with the loaded config. Checked before any
fitting, so a mismatch costs nothing rather than twenty models.

Existing checkpoints and the sidecars recovered in the previous pass are
untouched; `--allow-legacy` still reads them.

## 2. The selection phase overwrote its own artifacts

`select()` wrote each fit's training record to a path fixed by split, dimension
and seed, and `write_selection()` replaced the frozen selection JSON
unconditionally. A second selection run silently overwrote the first, including
the file the test phase reads to decide which configuration a published result
was produced under.

Changed: selection records now go to `selection/<label>/<split>__h<dim>__seed<n>.json`
via `selection_record_path()`, with `--label` on `seq2lead m9 select`.
`select()` preflights **every** planned record destination plus the frozen-selection
destination before the first fit, so a collision on the last planned record stops
the run before the first one. `write_selection()` refuses a conflicting
replacement and treats a byte-identical write as a no-op; `--overwrite` is
explicit. `m9 test` gained `--selection-path` so the test phase reads an
explicitly named record rather than whichever file happens to occupy the path.

The current selection record and its recovery provenance are unchanged.

## 3. The manifest stored record digests it never checked

`freeze_manifest()` wrote `record_sha256` but no record path, and
`verify_manifest()` only looked at checkpoints, predictions and sidecars. The
digest had nothing to check it against, so training records were skipped
entirely — a tampered record verified clean.

Changed: `freeze_manifest()` now writes `"record"` alongside the digest, and
`verify_manifest()` checks existence and digest for checkpoint, prediction and
training record as **required** artifacts, with the binding sidecar optional. A
required artifact whose path or digest is missing is now reported as a problem
rather than skipped — an unverifiable run must not look like a verified one.

### How the manifest was updated

The previous manifest was archived to `configs/manifests/m9_runs.v1.json`
(`8318f20873f38ed1…`) before anything was written. The new manifest
(`b660858d89e477eb…`) was then built and diffed against it:

| | |
| --- | --- |
| runs | 20 → 20 |
| digest changes | **0** |
| fields added | `record` |
| fields removed | none |

**No expected digest was re-derived from a current file.** Every checkpoint,
prediction, sidecar and record digest was carried over from the archived manifest
and then independently re-verified against the files on disk. That ordering is
what distinguishes adding a missing field from quietly re-baselining a drifted
one.

The re-freeze initially dropped the disclosure that this manifest was **frozen
retrospectively** — the original M9 pass wrote none — and an existing test caught
it. The provenance string now carries the whole history: retrospective origin,
the sidecar rewrite, this record-path addition, and the standing caveat that with
no earlier independent record to recover digests from, the manifest is a drift
baseline rather than an attestation that the bytes were unaltered before the
first freeze. Final manifest digest `69de1689d43150ff…`.

## Tests

Fifteen new tests in `tests/test_m9_wiring.py`, all with mocked fitting — no model
was trained to validate any of this:

| Area | Test |
| --- | --- |
| binding | `score_test()` refuses with no binding, and creates no destination |
| binding | a binding naming another experiment, or another config digest, refuses — with `fit` monkeypatched to raise, so a fit would fail the test |
| binding | the CLI's `m9 test` path reaches the runner with a binding whose experiment, config digest and both cache names match |
| binding | a newly saved checkpoint resolves its own embedded binding with **no sidecar on disk** |
| selection | a collision on the **third of four** planned records: zero fits, the occupied file byte-identical, no sibling records created |
| selection | an occupied frozen-selection destination refuses before the first fit |
| selection | `write_selection()` no-ops on identical content, refuses conflicting content, obeys `--overwrite` |
| selection | record paths are scoped by label |
| manifest | every run records a training-record path that exists |
| manifest | a changed training record fails; restoring it passes |
| manifest | a missing training record fails; restoring it passes |
| manifest | a required artifact with no path is a **problem**, not a skip |
| manifest | an optional artifact with no path is skipped |
| manifest | the previous manifest is preserved, no digest changed, and the provenance keeps its disclosures |

## Verification

Full suite **488 passed, 0 failed, 0 errors, 0 skipped**, counted from JUnit XML,
with pytest's exit code read directly: **0**. Ruff check and format clean.

Two tests failed on the first full run and both were real signals, corrected
rather than relaxed: the provenance regression above, and a follow-up test whose
`score_test()` call predated the binding requirement — it was given a real
binding so it still reaches the collision it was written to prove.

Artifacts, checked against the digests recorded **before** this pass:

| | |
| --- | --- |
| M9 checkpoints, predictions, sidecars | 60 / 60 unchanged |
| M9 training records | 20 / 20 unchanged |
| M8 predictions | 72 / 72 unchanged |
| M9 manifest | `record` field added; 0 digest changes; previous archived |

## Limitations

- The binding guard protects **new** fits. Checkpoints written before this pass
  still carry `feature_binding: null` and still depend on their recovered
  sidecars; `--allow-legacy` remains the only way to read them.
- The record-path check proves a file matches the digest the manifest holds. It
  cannot prove that digest was correct when first recorded, for the reason the
  provenance states.
- Selection-record protection is per-label. Two runs sharing a label still
  collide — which is now a refusal rather than an overwrite, but not an
  automatic namespacing.
- No model was refitted and no prediction regenerated, so none of this is
  evidence about the numbers themselves — only about which configuration and
  feature identity produced them.
