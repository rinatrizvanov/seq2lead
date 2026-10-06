# What CI covers, and what it cannot

A green CI badge on this repository does **not** mean the whole suite passed.
It means the portable subset passed. This file records the boundary, how it was
measured, and a correction to an earlier version of it.

## Corrected: the first boundary was measured wrongly

An earlier revision put the boundary at **7 files**, derived from running the
suite on a fresh clone with `SEQ2LEAD_SKIP_DB_TESTS=1`. That was the wrong model
of CI. The workflow's `Test` step runs **with a live, migrated, empty**
PostgreSQL service, so database-backed tests execute there and find no data —
a condition the skip-flag run never exercised.

Real GitHub Actions runs settled it. Every run on this branch had failed:

| Commit | Result | Failing step |
| --- | --- | --- |
| `4a64d90` | failure | Test |
| `0013451` | failure | Test |
| `43e3397` | failure | Test |
| `1d16719` | failure | Test — **92 failed, 1,130 passed, 66 skipped, 34 errors** across 18 files |
| `a83ee47` | failure | Test (portable subset) — the 7-file exclusion was insufficient: **22 failed, 979 passed, 47 skipped, 27 errors** |
| `0034bdc` | **success** | all 13 steps green — the first passing run on this branch |

The union of those two measured runs put the boundary at **18 files**: 16 that
need local artifacts, and 2 that failed for an unrelated reason. The 2 have since
been repaired and now run in CI, so the boundary is **16 files** — see "The two
causes, and why there is now one" below.

## The first green run, and why the collected count fell

**Run <https://github.com/rinatrizvanov/seq2lead/actions/runs/37429970087>**,
commit `0034bdc9f9b9acce09a3bfcfc6b64e30281d8a00`, conclusion **success**,
2026-10-06 07:29:24Z → 08:01:17Z. All 13 steps green: Lint, Format check, Apply
migrations, both pytest steps, the scope notice, and the CLI smoke check.

| Step | Collected | Passed | Failed | Skipped | Errors |
| --- | ---: | ---: | ---: | ---: | ---: |
| Test (portable subset) | 887 | 875 | 0 | 12 | 0 |
| Test without a database (portable subset) | 887 | 789 | 0 | 98 | 0 |

The second step skips 86 more than the first, which is `SEQ2LEAD_SKIP_DB_TESTS=1`
doing its job: those are the `requires_db` tests, skipped by `conftest.py` rather
than silently passing.

### 1,075 → 887 is entirely the exclusion list

The suite itself did not change. It collects **1,322** tests at both commits, so
the drop is accounted for by which files CI was told to ignore — nothing was
deleted, renamed, or deselected inside the test code:

| | `a83ee47` | `0034bdc` |
| --- | ---: | ---: |
| suite collects, complete local tree | 1,322 | 1,322 |
| files excluded in the workflow | 7 | 18 |
| tests in those files | 247 | 435 |
| **collected by CI** | **1,075** | **887** |

1,322 − 247 = 1,075 and 1,322 − 435 = 887. The difference of **188** is exactly
the 11 files added to the list:

| File added to the exclusion list | Tests | Cause |
| --- | ---: | --- |
| `tests/test_splits.py` | 36 | artifact-dependent |
| `tests/test_m9_followup.py` | 28 | artifact-dependent |
| `tests/test_feature_contracts.py` | 19 | artifact-dependent |
| `tests/test_feature_identity.py` | 19 | artifact-dependent |
| `tests/test_eval_contracts.py` | 15 | artifact-dependent |
| `tests/test_m11c_acquisition.py` | 15 | artifact-dependent |
| `tests/test_safeguards.py` | 15 | environment-dependent — **since fixed** |
| `tests/test_eval_verification.py` | 13 | artifact-dependent |
| `tests/test_dual_encoder.py` | 12 | artifact-dependent |
| `tests/test_result_versions.py` | 11 | artifact-dependent |
| `tests/test_cli.py` | 5 | environment-dependent — **since fixed** |
| | **188** | |

And the 7 that were already excluded at `a83ee47`, all artifact-dependent:
`test_m10_corrections.py` (58), `test_m10_hardening.py` (57),
`test_m10_docking.py` (52), `test_m9_corrections.py` (26),
`test_m11f_record.py` (24), `test_m11g_contract.py` (15),
`test_m9_wiring.py` (15) — 247 in total.

So of the 435 tests CI did not run on `0034bdc`, **20 were excluded for a test
defect** and 415 for missing artifacts. The 20 have been repaired, which returns
`test_cli.py` and `test_safeguards.py` to CI and leaves **16 files / 415 tests**
outside it. Every one of the 415 is excluded because a checkout cannot carry the
artifact it needs — not one is excluded because it fails.

Two inventory-reconciliation guards were added to `test_safeguards.py` in the same
pass, so the suite is now **1,324** and CI collects **909 of 1,324** across 31
files. 1,324 − 415 = 909.

## Reproducing CI's condition locally

A separate empty database was created beside the real one, migrations applied,
and the suite run from a clone — which reproduced **90 failures and 34 errors**
across 16 of the 18 files, matching the real run. The real corpus database was
never touched.

```bash
createdb seq2lead_ci_sim          # or: CREATE DATABASE seq2lead_ci_sim;
git clone . /tmp/ci-sim && cd /tmp/ci-sim
SEQ2LEAD_DB_NAME=seq2lead_ci_sim uv run seq2lead db migrate
SEQ2LEAD_DB_NAME=seq2lead_ci_sim FORCE_COLOR=1 COLUMNS=80 uv run pytest -q
```

## The two causes, and why there is now one

### (a) Artifact-dependent — 16 files. Still excluded.

A checkout excludes 1.83 GB of docking runs, M9 per-seed records, feature
caches and the 433 MB training-membership export, and the CI database is
migrated but empty.

- `tests/test_dual_encoder.py`
- `tests/test_eval_contracts.py`
- `tests/test_eval_verification.py`
- `tests/test_feature_contracts.py`
- `tests/test_feature_identity.py`
- `tests/test_m10_corrections.py`
- `tests/test_m10_docking.py`
- `tests/test_m10_hardening.py`
- `tests/test_m11c_acquisition.py`
- `tests/test_m11f_record.py`
- `tests/test_m11g_contract.py`
- `tests/test_m9_corrections.py`
- `tests/test_m9_followup.py`
- `tests/test_m9_wiring.py`
- `tests/test_result_versions.py`
- `tests/test_splits.py`

### (b) Environment-dependent — was 2 files. Fixed; they now run in CI.

`tests/test_cli.py` and `tests/test_safeguards.py` each had exactly one failing
test, and both asserted that a flag name appears in CLI `--help` output:

- `test_asof_export_is_registered_and_documents_its_read_only_guarantee`
- `test_compact_flag_warns_about_the_lock`

Measured against a reproduced CI environment (an empty migrated database,
`FORCE_COLOR=1 COLUMNS=80`), each file failed on that one test and passed
everything else — 15 of 15 in `test_safeguards.py` bar one, 5 of 5 in
`test_cli.py` bar one — which is what established that the cause really was the
environment and not a missing artifact.

**Two separate effects, not one.** The first was known: Rich styles every switch,
so `--release-id` reaches stdout as escape-separated fragments and the substring
is absent. `NO_COLOR=1` cannot fix that, because `FORCE_COLOR` takes precedence in
Rich. The second was found while fixing it and invalidated the obvious repair:
Rich fits the options panel to the terminal width and, when it does not fit,
**truncates** — at width 34 the switch renders as `--rel…`. Those characters are
gone, so stripping escapes is not enough and no text normalisation recovers them.
A test cannot inherit its width; it has to set it.

**The fix** (`tests/cli_help.py`), which removes the dependency rather than
tolerating it:

1. Switch names and the documented guarantees are read from **Click's own command
   and parameter objects** — the text the CLI will render, before Rich touches it.
   No terminal participates. This is also strictly stronger than the old
   assertion: `--no-compact`'s absence is now checked against the declared
   switches, where a hidden flag would still be visible, instead of against help
   text where "hidden" and "absent" look identical.
2. The CLI is additionally rendered in a **child process at a width the test
   pins**, with colour off, confirming the text really does reach a help screen —
   which declarations alone cannot show. A child process is what makes the width
   effective: Typer reads `TERMINAL_WIDTH` once, at import.
3. Comparisons go through `squash()`, which strips any escape that still arrives
   and all whitespace, so a line break inside a phrase cannot matter either.

**Verified** across ten environment combinations — `FORCE_COLOR=1` at widths 80,
40 and 20, `NO_COLOR=1`, and `TERM=dumb`, each with and without
`SEQ2LEAD_SKIP_DB_TESTS=1` — all 20 tests passing in every one, including width
20, where the old assertions could not have passed under any normalisation.
Coverage was not removed and neither test self-skips.

## What was deliberately not done

None of the 16 files was weakened, and no test was made to skip instead of
fail. They are excluded in the **workflow**, not in the test code, so that
running the suite locally still exercises them in full and a missing artifact
still fails loudly. Making them self-skip would turn "this check did not run"
into a green tick.

## What a green CI run does and does not establish

**Does:** the code imports, lints, formats, migrates a fresh database, the CLI
starts, and the portable subset passes — including the as-of recomputation and
publication refusals, which ship their own small artifacts.

**Does not:** that the M10 docking manifest verifies against its 3,564 files;
that the M9 per-seed records are intact; that the pinned input digests match
the bytes on disk; that feature caches and splits are consistent. Those are
properties of a local artifact set CI has no copy of.

On a complete local tree the whole suite was recorded at **1,322 passed, 0
failures, 0 errors, 0 skipped** per JUnit, and is **1,324** after the two
inventory guards added in this pass. That is the same figure the arithmetic above
subtracts from, which is what makes the CI subset a subtraction rather than a
different suite.

