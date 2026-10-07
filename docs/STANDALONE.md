# Standalone ranking (v0.2, in development)

Rank a bundled compound library against one protein sequence, with **no
PostgreSQL, no feature caches and no upload**. This is the product path from the
[release scope](RELEASE_SCOPE.md) made usable from a clone plus one download.

> **What the output is.** Prioritised candidates for testing. Predicted pKi is a
> ranking score, not a binding probability, not a calibrated confidence, and not
> evidence that any compound binds. Nothing has been experimentally tested by
> this project. The ranking is a hypothesis for triage.

## Use it

```bash
# a FASTA file with ONE record
seq2lead prioritise --bundle ./seq2lead-bundle --sequence-file target.fasta --top-n 50

# or paste the sequence
seq2lead prioritise --sequence MDPLNLSWYDDDLERQNWSRPFNGSD... --top-n 20

# write the full result alongside the table
seq2lead prioritise --sequence-file target.fasta --top-n 50 --out-csv ranking.csv
```

The bundle is found at `--bundle`, else `$SEQ2LEAD_BUNDLE`, else
`./seq2lead-bundle`, else `~/.seq2lead/bundle`. If none exists the error names
every place it looked.

`seq2lead bundle verify` checks the manifest and every digest without loading a
model. The ranking path runs the same check first, so a damaged bundle is refused
in about a tenth of a second rather than after the encoder has loaded.

### What it refuses, and why

| Input | Response |
| --- | --- |
| two or more FASTA records | refused, with the record names. Choosing one would answer a question you did not ask |
| a nucleotide sequence | refused — it asks for a protein |
| characters that are not residue codes | refused, listing the offenders |
| fewer than 20 residues | refused; there is not enough protein to represent |
| more than the bundle's recorded refusal point | refused, **not truncated** — scoring a prefix answers a question about a different protein |
| past the encoder's 1,022-residue pre-training window | **allowed, and flagged**: embedded in full, representation quality beyond that window untested |
| ambiguous residue codes (B, Z, X) | allowed, and flagged — the ranking inherits the ambiguity |

## The bundled library: what it is and is not

`curated-ki-25k-v1`, 25,000 compounds. Membership means **a measured exact Ki
value existed for that compound against some protein** in the pinned BindingDB
release. That is all it means.

It is **not** a set of approved drugs, not a safety-screened set, not a vendor
catalogue, and carries no implied relationship to your query. Compounds are
ordered for selection by an internal surrogate key, which is arbitrary with
respect to anything a model predicts.

Its scope is biased, and the bias matters when reading a ranking: it is drawn
towards targets and chemistry that have been measured by Ki-style assays, and
away from chemistry nobody has published a Ki for. Structures are the
RDKit-standardised parents of BindingDB-supplied SMILES. Identifiers and
structures derive from BindingDB; see [`DATA_LICENSE`](../DATA_LICENSE).

## The bundle

A directory, not an archive, so the parts can be inspected and hashed
independently.

| Path | What it holds |
| --- | --- |
| `manifest.json` | versions, per-file SHA-256, provenance, the recorded representation spec |
| `model/protein_tower.npz` | protein-side projection weights, plus the affine head's scale and offset |
| `model/compound_tower.npz` | compound-side weights. Not needed to rank the bundled library, carried so a future custom library can be projected with the same weights |
| `model/protein_transform.npz` | the standardisation fitted on training inputs and frozen with the checkpoint |
| `library/projections.npy` | precomputed compound projections, 25,000 × 512 float32 |
| `library/compounds.csv` | stable identifier and SMILES per compound, in projection row order |

**Why projections rather than fingerprints.** The towers never see each other's
input, so a compound's projection depends only on its fingerprint. Precomputing
removes both the fingerprint cache and RDKit from the core inference path.

**Deliberately absent:** measured evidence. Prior activity is not validation of a
ranking, and the partition-filtered evidence API that would be needed to show it
safely is unimplemented. Evidence display stays off.

Build one with `seq2lead bundle export --model <checkpoint> --out <dir>`. That
step needs the database and is a maintainer action, not a user one.

## Agreement with the database-backed path

Verified on a fixed query (`reports/examples/query_target.fasta`, 443 residues)
against the full 25,000-compound library, comparing every compound:

| | |
| --- | --- |
| declared score tolerance | 1e-5 pKi, absolute |
| **measured max abs difference** | **0.0** |
| measured mean abs difference | 0.0 |
| compounds compared | 25,000 |
| tied scores in the reference | 686 |
| rank mismatches, untied | **0** (required) |
| rank mismatches, tied | 0 (permitted: order within a tie is arbitrary) |
| SMILES mismatches | 0 |

**Ties.** Equal scores are ordered by the compound's stable bundle row using a
stable sort, so position within a tie carries no information. Tied rows are
marked in both the table and the CSV. Rank agreement is only *required* where the
score is untied, because two correct implementations may order a tie differently.

## Measured cost

Apple M4, 16 GB, macOS. One process, 25,000 compounds, measured not estimated.

| | CPU | MPS |
| --- | ---: | ---: |
| bundle load | 0.05 s | 0.06 s |
| **first query** (includes loading the encoder) | 2.92 s | 6.05 s |
| **repeat query** (median of 3, same process) | 0.52 s | 0.27 s |
| peak process RSS | 3,228 MB | 582 MB |

The encoder and the compound projections are loaded once and reused, which is why
a repeat query costs a fraction of the first. MPS has the lower RSS because the
weights sit in GPU memory rather than process memory — it is not using less
total memory.

**Backends.** CPU and MPS are **tested**. CUDA is *supported by the code path*
and has not been run here; nothing in this repository is evidence that it works.

**Sequence length.** Ranking was run at 20, 50, 100, 250, 443, 800, 1022, 1023,
1500 and 2000 residues. Per-query CPU time grows roughly linearly, from 0.17 s at
50 residues to 3.49 s at 2000.

**Download.**

| | |
| --- | ---: |
| inference bundle | 62,165,563 B (59.3 MiB) |
| ESM-2 weights, fetched once by `transformers` | 2,609,506,392 B (2.43 GiB) |
| **total before a first query** | **2.49 GiB** |

## Optional shortlisting

**Off unless asked for, and it never replaces the ranking.** Shortlisting is a
view: every removed compound keeps the rank and score it had and is reported with
the reason, so you can see what the filter did rather than infer it from an
absence.

```bash
# property windows
seq2lead prioritise --sequence-file t.fasta --top-n 200 --max-mw 500 --max-tpsa 140

# chemical diversity
seq2lead prioritise --sequence-file t.fasta --top-n 200 --diverse 20 --diversity-threshold 0.7
```

**Diversity method, stated because the claim is meaningless without it:**

| | |
| --- | --- |
| fingerprint | Morgan (ECFP-style), radius 2, 2048 bits, chirality on |
| source | computed from the bundled SMILES with RDKit at shortlist time. Same family as the model's fingerprint, recomputed here; no claim that the bits are identical to the cached ones |
| similarity | Tanimoto |
| selection rule | greedy down the ranking: walk best-score-first, keep a compound if its maximum Tanimoto to everything already kept is below the threshold |

The rule is deterministic and order-dependent by design, and it never promotes a
lower-scoring compound above a higher-scoring one.

**Not applied, silently or otherwise:** drug-likeness scores, PAINS or other
structural-alert lists, and any toxicity or synthesisability heuristic. They
encode assumptions about what a useful compound looks like, they disagree with
each other, and applying one invisibly would reshape a ranking this project
otherwise keeps auditable. A test asserts none of them is called.

Property filters need RDKit. The unfiltered ranking does not.

## Output

A readable table and, with `--out-csv`, a CSV carrying `rank`, `compound_id`,
`smiles`, `predicted_pki` and `warnings`, plus shortlist columns when one was
requested. The CSV repeats the framing in a comment preamble, because a CSV
outlives the terminal it came from.

## Proposed distribution (not yet carried out)

The bundle is **not** in this repository and should not be. At 59.3 MiB it would
be 4.5x the largest tracked file and 1.7x the entire tracked tree, and it is a
derived artifact that can be rebuilt from the checkpoint and the library.

Proposed, for the owner to approve before anything is uploaded:

1. **Attach it as a GitHub release asset** on a `v0.2.0` tag — one file,
   `seq2lead-inference-bundle-v1.tar.gz`, well under the 2 GiB asset limit. The
   release notes carry the SHA-256 of the archive and of each file inside it, so
   a download can be checked against something published rather than against
   itself.
2. **Keep `manifest.json` in the repository** as well, so the expected digests are
   version-controlled and a tampered download is detectable even if the release
   page is not trusted.
3. **Licence the bundle as the data it derives from.** It carries BindingDB
   compound identifiers and structures, so CC BY-SA 3.0 with the attribution
   block in [`DATA_LICENSE`](../DATA_LICENSE) — the same conservative position the
   project already takes for `predictions.npz`, and for the same reason: the
   model-derived projections are computed over predominantly ChEMBL-sourced rows.
4. **Do not publish ESM-2 weights.** They are fetched by `transformers` from the
   pinned revision; this project redistributes neither the weights nor the code.
5. **State the provenance chain in the release notes**: which checkpoint, which
   library version, which feature caches, and the digests already recorded in the
   bundle manifest.

Not proposed: PyPI (a 59 MiB wheel payload is antisocial and the bundle versions
independently of the code), Git LFS (adds a hard dependency for everyone who
clones, to deliver one optional file), and committing it directly.

## Limits and follow-up work

- **Custom library import is not supported.** The bundle carries the compound
  tower so it becomes possible, but projecting arbitrary user SMILES needs
  standardisation, fingerprinting and failure handling that match how the model
  was trained, and getting that subtly wrong would silently score a different
  representation. Deferred rather than half-done.
- **No graphical interface.** Command line only.
- **Docking is not part of this path and is not a validation claim.** The
  project's docking protocol failed its pre-registered ranking gate.
- **One checkpoint per bundle.** The historical evaluation used several seeds;
  a bundle carries one.
- **The underlying model is unchanged from v0.1.0** and its limitations are
  unchanged with it: exploratory results, provisional Ki pooling, no calibration.
