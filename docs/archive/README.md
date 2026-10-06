# Documentation archive

Records kept for provenance, not for reading. Nothing here describes how to use
Seq2Lead; the current documentation is one level up and indexed from the
[README](../../README.md).

| File | What it is |
| --- | --- |
| `ARCHIVE_POLICY.md` | what had to be retained before any review archive could be deleted, and the list of evidence that must never be deleted because a review copy exists |
| `closeout_preservation.json` | the machine-readable record of which files were preserved, and where, at the close of the historical evaluation |
| `gitignore_precloseout.txt` | the `.gitignore` as it stood before the closeout pass, kept so the change in what the repository tracks is reconstructable |

## Two protocol documents are deliberately *not* here

`../M9.md` and `../M11.md` are the frozen pre-registration protocols for the dual
encoder and for the historical snapshot evaluation. They read like internal
milestone documents, and a public reader can skip them, but they stay at their
original paths for two mechanical reasons rather than editorial ones:

- **`M11.md` is read at run time by the test suite.** `tests/test_m11c_acquisition.py`
  and `tests/test_m11e_record.py` open `docs/M11.md` by that exact path to assert
  that the confirmatory freeze is still recorded as unsigned and that a corrected
  status name survives only inside the note recording the correction. Moving the
  file would turn both into failures.
- **`M9.md` is cited from a digest-pinned config.** `configs/experiments/m9-dual-encoder-v1.yaml`
  names it, and that file's SHA-256 is recorded in three manifests
  (`m11f_preflight.json`, `m11g_preflight.json`, `m11f_evaluation_contract.json`).
  Editing the config to point somewhere else would invalidate all three.

Both are therefore labelled rather than relocated. They are historical protocol
records: accurate for the experiment they governed, and not a guide to anything.
