# `scripts/asof/` — the execution code, as it was run

These are the **actual scripts** that produced the M11g binding artifacts and the
M11h fit, included verbatim rather than described.

## Provenance

They were authored and executed from this session's scratchpad directory
(`/private/tmp/claude-501/.../scratchpad`), which is session-scoped and is not
part of the repository. That was the wrong place for code whose output is a
published result: a report cannot be reproduced from a script that no longer
exists. They are copied here **unmodified** — same bytes, same digests — so the
record is complete, and the digests below pin what was run.

Nothing here was edited to make it presentable. The scripts carry their original
structure, including the two-pass binding in `m11g_bind.py` and the
closure-passed validation cohort in `m11h_fit.py`, because that is what ran.

## What each one did

| Script | Role in the chain |
| --- | --- |
| `m11g_resolve_eval.py` | resolved the evaluation role's entities in 202609 after the runner refused |
| `m11g_bind.py` | wrote the artifacts the runner derives from, then ran it twice to confirm self-consistency |
| `m11g_contract.py` | regenerated the evaluation contract and its manifest |
| `m11g_recurrence_check.py` | measured whether the recurrence stratum agrees with the emitted training set |
| `m11g_window.py` | measured role targets over ESM-2's 1,022-residue training window |
| `m11g_reproduce.py`, `m11g_runner_fixture.py` | reproduced the three M11g binding defects through the real runner |
| `m11h_evalpairs.py` | built the evaluation pair table with per-cell membership and strata |
| **`m11h_fit.py`** | **the fit**: feature-bank assembly, the three runner callbacks, prediction, manifest |
| `m11h_eval.py` | scored every cell from saved predictions |
| `m11h_verify.py` | the four verification checks |
| `m11h_report.py` | generated the results report from the saved artifacts |
| `m11h_reproduce_integrity.py` | reproduced the two publication-integrity holes before they were fixed |

## Feature-bank assembly, selection and evaluation

The bank is assembled by `seq2lead.asof.fitting.build_bank`, called from
`m11h_fit.py`; the three callbacks (`fit_transforms`, `fit_model`,
`select_checkpoint`) are defined inside `m11h_fit.py:main` and passed to
`seq2lead.asof.runner.run`. Scoring is in `m11h_eval.py` and is now also
available as the supported module `seq2lead.asof.recompute`, which is the
entry point to use for reproduction — see `seq2lead asof recompute --help`.

## Digests of the files as run

| Script | Bytes | SHA-256 |
| --- | ---: | --- |
| `m11g_bind.py` | 20,945 | `93500e0497f1149f6e02bdf6dbb4c6466c480dd243efaa7047e978432562938d` |
| `m11g_contract.py` | 19,628 | `6544e36c44ede8fc3f23c6f08679e623b854e4543b98e5130ee394299bafd9c1` |
| `m11g_recurrence_check.py` | 3,739 | `37121f75a4684cb2a1eb91f00fe3075de0e305387f8e40fc2d005443218b2241` |
| `m11g_reproduce.py` | 4,397 | `a66d9a576e1b7eace407aef6a6039c0fa04b1827392e8ec09aaf71bac63ab356` |
| `m11g_resolve_eval.py` | 7,019 | `d153caf7703d93070bb6f1e354f154ddfa7397f5a3d8a4699dcb104e445b56ec` |
| `m11g_runner_fixture.py` | 5,708 | `a3862c27bcbd3580304dc0a8951e27c307c4d496deb6afc6acadb78328d41401` |
| `m11g_window.py` | 3,461 | `901e0b08d8208bdfd1e7ae65fb1f746612071c046ea02ea57aef9da2bdc32479` |
| `m11h_eval.py` | 9,718 | `de5318fb3282c9a82c67afefe665aad80607c845009f997ee109a5c3dcead8dc` |
| `m11h_evalpairs.py` | 5,293 | `ed3d9924b37d61d27fc3526fc772c33e277d3e8cc9a2d81da354567c2ca05d61` |
| `m11h_fit.py` | 19,162 | `38fd5ca6b819129332621cc78c4dd1d94000631ad8dbe7f671a7fa8032648360` |
| `m11h_report.py` | 25,188 | `54540ffced0fb9c1cb3ef5d623fc554a071e3117278c79759ae5ed248b335481` |
| `m11h_reproduce_integrity.py` | 5,783 | `eca64ad7fbe9c8ba8f8ee930670fbd38cc3db31ca7de717be992759ef2f0dd2b` |
| `m11h_verify.py` | 7,944 | `677f8c36eb7ff86e3d1b3354f49201e957dd2d924a0f281db15c54ec553e66bb` |

These are the digests of the copies in this directory, which are byte-identical
to the scratchpad originals.
