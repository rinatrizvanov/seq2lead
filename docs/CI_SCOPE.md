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

The authoritative boundary is the union of those two measured runs: **18 files**.

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

## The two causes, kept apart

### (a) Artifact-dependent — 16 files

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

### (b) Environment-dependent — 2 files

These assert that a flag name appears in CLI `--help` output. On the runner,
Rich emits ANSI escape sequences **inside** the flag names, so the substring
match fails. Reproduced locally with `FORCE_COLOR=1 COLUMNS=80`, which gives
the identical assertion message as CI. `NO_COLOR=1` does **not** fix it:
`FORCE_COLOR` takes precedence in Rich.

- `tests/test_cli.py`
- `tests/test_safeguards.py`

**This is a test defect, not a missing artifact.** The fix is to strip ANSI
before asserting, or to assert against a styled-output-safe form. That is a
change to accepted test code, so it is reported here for owner review rather
than made unreviewed. Until then these two are excluded in CI for a cause that
is honestly labelled, not conflated with the artifact group.

## What was deliberately not done

None of the 18 files was weakened, and no test was made to skip instead of
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
failures, 0 errors, 0 skipped** per JUnit.

