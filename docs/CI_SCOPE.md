# What CI covers, and what it cannot

A green CI badge on this repository does **not** mean the whole suite passed. It
means the portable subset passed. This file records the boundary and how it was
measured, so nobody has to infer it from a workflow file.

## How the boundary was measured

Not by reasoning about which tests "look local". A disposable clone of this
repository was made — which is exactly what a hosted runner gets — and the full
suite was run inside it with `SEQ2LEAD_SKIP_DB_TESTS=1`:

```
1,322 tests   65 failures   0 errors   229 skipped
```

Of the 65 failures, 50 report a missing `data/m10/runs` path; the rest report
missing `data/m9/final`, missing `data/features/*.npz`, or a missing
training-membership export. Those paths are git-ignored on purpose: they are
1.83 GB of docking runs, per-seed records, feature vectors and membership
exports, recorded in `configs/manifests/closeout_inventory.json`.

## Portable — run in CI

**40 test files, 1,075 tests** (197 of them skipped when no database is present).
Plus `ruff check .`, `ruff format --check .`, the migrations, and the CLI smoke
checks. These pass on a bare checkout.

## Full-local — NOT run in CI

**7 test files, 65 of whose tests cannot run on a checkout.**

| Test file | Needs | Tests / fail on a checkout |
| --- | --- | ---: |
| `tests/test_m10_corrections.py` | `data/m10/runs/` | 58 / 37 |
| `tests/test_m10_hardening.py` | `data/m10/runs/` | 57 / 16 |
| `tests/test_m10_docking.py` | `data/m10/runs/`, `data/m10/structures/` | 52 / 5 |
| `tests/test_m9_wiring.py` | `data/m9/final/` | 15 / 4 |
| `tests/test_m9_corrections.py` | `data/m9/final/` | 26 / 1 |
| `tests/test_m11f_record.py` | the pinned input set on disk | 24 / 1 |
| `tests/test_m11g_contract.py` | the pinned input set on disk | 15 / 1 |

Run them where the artifacts exist:

```bash
uv run pytest tests/test_m10_corrections.py tests/test_m10_hardening.py \
              tests/test_m10_docking.py tests/test_m9_wiring.py \
              tests/test_m9_corrections.py tests/test_m11f_record.py \
              tests/test_m11g_contract.py
```

On the full local tree these pass: the working-tree suite was recorded at
**1,322 passed, 0 failures, 0 errors, 0 skipped** per JUnit.

## What was deliberately not done

These seven files were **not** weakened, and no test was made to skip instead of
fail. A test that binds to a digest still binds to it; a test that verifies a
docking manifest still verifies every referenced file. They are excluded in the
*workflow*, not in the test code, so that:

* running the suite locally still exercises them in full, and
* a missing artifact still fails loudly rather than passing quietly.

Making them self-skip when an artifact is absent would have turned "this check
did not run" into a green tick, which is the thing this project spends most of
its verification effort avoiding.

## What a green CI run does and does not establish

**Does:** the code imports, lints, formats, migrates a fresh database, the CLI
starts, and 1,075 tests pass — including the as-of recomputation and publication
refusals, which ship their own small artifacts.

**Does not:** that the M10 docking manifest still verifies against its 3,564
files; that the M9 per-seed records are intact; that the pinned input digests
still match the bytes on disk. Those are properties of a local artifact set that
CI has no copy of, and `configs/manifests/closeout_inventory.json` is the record
of what that set contains.
