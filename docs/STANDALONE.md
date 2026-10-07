# Standalone ranking (v0.2, in development)

Rank a bundled compound library against one protein sequence, with **no
PostgreSQL, no feature caches and no upload**. This is the product path from the
[release scope](RELEASE_SCOPE.md) made usable from a clone plus one download.

> **What the output is.** Prioritised candidates for testing. Predicted pKi is a
> ranking score, not a binding probability, not a calibrated confidence, and not
> evidence that any compound binds. Nothing has been experimentally tested by
> this project. The ranking is a hypothesis for triage.

## Install

Four steps. No database, no compiler, no upload. Every command below is
copy-pasteable into an interactive shell: there are no inline `#` comments, which
zsh rejects unless `interactive_comments` is set.

```bash
git clone --branch v0.2.0 https://github.com/rinatrizvanov/seq2lead.git
cd seq2lead
uv sync --all-groups
```

```bash
curl -LO https://github.com/rinatrizvanov/seq2lead/releases/download/v0.2.0/seq2lead-inference-bundle-v1.tar.gz
shasum -a 256 seq2lead-inference-bundle-v1.tar.gz
```

That must print:

```
941280a6ce2959a4bc46a0920b869a8e798456f9283c45d8e76c5b0d757a57d1
```

If it does not, stop and re-download. Then unpack and check:

```bash
tar -xzf seq2lead-inference-bundle-v1.tar.gz
mv seq2lead-inference-bundle-v1 seq2lead-bundle
uv run seq2lead bundle verify --bundle ./seq2lead-bundle
```

`bundle verify` re-hashes every file against the bundle's manifest, without
loading a model, so a damaged bundle is refused in about a tenth of a second. The
same manifest is tracked in this repository, so you can also check the download
against version control rather than against itself:

```bash
shasum -a 256 seq2lead-bundle/manifest.json
shasum -a 256 configs/bundles/seq2lead-inference-bundle-v1.manifest.json
```

Those two digests must be equal.

### The first query downloads ESM-2

The bundle does **not** contain the protein language model, and this project does
not redistribute it. On your first query `transformers` downloads
`facebook/esm2_t33_650M_UR50D` at the pinned revision from Hugging Face — about
**2.43 GiB**, once, cached under `~/.cache/huggingface`. Later queries reuse it.

A genuine first run therefore costs that download *plus* the first-query timing
in [Measured cost](#measured-cost). Every timing there was taken with the weights
already cached and excludes the download.

### What the ESM-2 load report means

The first load prints a table with `lm_head.*` marked UNEXPECTED and `pooler.*`
marked MISSING. **Both are expected, and neither affects your results.**

- `lm_head.*` is the masked-language-model head. It is in the published
  checkpoint, the encoder architecture has no such layer, and it is discarded.
- `pooler.*` is the reverse: the architecture defines a pooler the checkpoint does
  not carry, so it is randomly initialised.

A randomly initialised layer would matter if the embedding read it. It does not —
the embedding is the mean over `last_hidden_state`, never `pooler_output`. Loading
the model twice under different torch seeds gives different pooler weights and a
different `pooler_output`, and a **bit-identical** embedding.

Anything outside those two prefixes is a genuine mismatch in the backbone that
produces the embedding. Seq2Lead reports those as warnings on the ranking rather
than letting them pass as noise.

## Use it

Point `--sequence-file` at your own FASTA. The repository ships one to try first,
`reports/examples/query_target.fasta` (443 residues):

```bash
uv run seq2lead prioritise --bundle ./seq2lead-bundle --sequence-file reports/examples/query_target.fasta --top-n 50
```

With your own file, give its path, absolute or relative to where you are:

```bash
uv run seq2lead prioritise --bundle ./seq2lead-bundle --sequence-file /path/to/your_target.fasta --top-n 50
```

The FASTA must hold exactly one record. To paste a sequence instead, pass it in
full — this is the same protein as the bundled example:

```bash
uv run seq2lead prioritise --bundle ./seq2lead-bundle --top-n 20 --sequence MDPLNLSWYDDDLERQNWSRPFNGSDGKADRPHYNYYATLLTLLIAVIVFGNVLVCMAVSREKALQTTTNYLIVSLAVADLLVATLVMPWVVYLEVVGEWKFSRIHCDIFVTLDVMMCTASILNLCAISIDRYTAVAMPMLYNTRYSSKRRVTVMISIVWVLSFTISCPLLFGLNNADQNECIIANPAFVVYSSIVSFYVPFIVTLLVYIKIYIVLRRRRKRVNTKRSSRAFRAHLRAPLKGNCTHPEDMKLCTVIMKSNGSFPVNRRRVEAARRAQELEMEMLSSTSPPERTRYSPIPPSHHQLTLPDPSHHGLHSTPDSPAKPEKNGHAKDHPKIAKIFEIQTMPNGKTRTSLKTMSRRKLSQQKEKKATQMLAIVLGVFIICWLPFFITHILNIHCDCNIPPVLYSAFTWLGYVNSAVNPIIYTTFNIEFRKAFLKILHC
```

Write the ranking to a file as well as the terminal:

```bash
uv run seq2lead prioritise --bundle ./seq2lead-bundle --sequence-file reports/examples/query_target.fasta --top-n 50 --out-csv ranking_top50.csv
```

**`--out-csv` overwrites its target without asking, and without warning.** Give
each run its own filename if you want to keep earlier results; the examples here
use distinct names for that reason.

The bundle is found at `--bundle`, else `$SEQ2LEAD_BUNDLE`, else
`./seq2lead-bundle`, else `~/.seq2lead/bundle`. If none exists, the error names
every place it looked.

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

`curated-ki-25k-v1`, exactly 25,000 compounds, frozen.

### The selection rule, in full

> Compounds with at least one eligible exact-relation (`=`) Ki measurement in the
> pinned BindingDB source release, **ordered by internal compound id ascending**,
> **capped at 25,000**.

Three consequences worth being explicit about, because each is a way the library
could be misread:

1. **It is a prefix, not a sample.** 232,721 compounds in the curated database
   satisfy the eligibility condition. The library is the first 25,000 of them by
   surrogate key — not a random draw, not the best-scoring, not the most
   drug-like. Re-running the rule gives the same 25,000 every time, which is the
   point: a ranking is only reproducible if the pool it ranked is.
2. **The ordering is arbitrary with respect to anything predicted**, which is why
   it is safe to truncate on. Compound id is an insertion-order surrogate, so it
   correlates loosely with how long a compound has been in BindingDB — the
   library therefore leans towards earlier entries. It does **not** correlate
   with affinity, with similarity to your query, or with any model output.
3. **Membership means a measured exact Ki existed against *some* protein.** It
   does not mean the compound is a drug, is approved, is safe, is purchasable, or
   has ever been measured against the protein you are querying.

### How it relates to the full database

| | |
| --- | ---: |
| compounds in the curated database | 1,424,670 |
| activity rows | 3,233,963 |
| compounds with at least one exact-relation Ki | 232,721 |
| **in this bundled library** | **25,000** |

The bundle is a **screening pool for the ranking prototype**, not an export of the
database. It carries 1.8% of the curated compounds and about 10.7% of those
eligible by the rule above. The full database is not distributed: it is tens of
gigabytes, it needs PostgreSQL, and the historical benchmark — not this library —
is what the published metrics were computed on.

The cap exists to keep the bundle downloadable. Raising it is a bundle rebuild,
not a code change, and would mint a new library version rather than amend this
one.

### Scope and bias

Its coverage is biased, and the bias matters when reading a ranking: it is drawn
towards targets and chemistry measured by Ki-style assays, and away from chemistry
nobody has published a Ki for. Structures are the RDKit-standardised parents of
BindingDB-supplied SMILES. Identifiers and structures derive from BindingDB; see
[`DATA_LICENSE`](../DATA_LICENSE).

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

**Benchmark hardware:** Apple M4, 10 cores, 16 GiB unified memory, macOS 15.6.1
(build 24G90), Python 3.11.15, PyTorch 2.14.0. One process, 25,000 compounds,
measured rather than estimated. Your numbers will differ.

| | CPU | MPS |
| --- | ---: | ---: |
| bundle load | 0.05 s | 0.06 s |
| **first query** | 2.92 s | 6.05 s |
| **repeat query** (median of 3, same process) | 0.52 s | 0.27 s |
| peak process RSS | 3,228 MB | 582 MB |

**First-query timings exclude downloads.** They were measured with the ESM-2
weights already in the local `transformers` cache. A genuine first run on a new
machine also pays for fetching 2.43 GiB of weights, which depends on your network
and is not included in any number above. The "first query" cost here is
constructing and loading the encoder into memory, not obtaining it.

The encoder and the compound projections are loaded once and reused, which is why
a repeat query costs a fraction of the first.

**RSS is not total memory.** The figures above are peak *process* resident set
size. On MPS the model weights live in accelerator memory, which this measurement
does not capture, so the 582 MB figure is **not** evidence that MPS uses less
memory overall — it is evidence that less of it is counted against the process.
On this machine the accelerator shares the same 16 GiB of unified memory as the
CPU. The CPU figure of 3,228 MB is closer to a true total because there is no
separate accelerator allocation to miss.

**Backends.** CPU and MPS are **tested**. CUDA is *supported by the code path*
and has not been run here; nothing in this repository is evidence that it works,
and on a discrete CUDA card the RSS/accelerator-memory distinction above matters
more, not less.

**Sequence length.** Ranking was run at 20, 50, 100, 250, 443, 800, 1022, 1023,
1500 and 2000 residues. Per-query CPU time grows roughly linearly, from 0.17 s at
50 residues to 3.49 s at 2000.

> **What that range does and does not establish.** It establishes that the code
> **executes** and returns a ranking across 20–2000 residues. It says nothing
> about whether the ranking is any **good** at those lengths. Beyond ESM-2's
> 1,022-residue pre-training window the protein representation is extrapolation:
> it was never evaluated there, no accuracy was measured at any length, and the
> benchmark in this repository does not cover it. Long-sequence results are
> flagged in the output for that reason. Treat a 2000-residue ranking as
> untested in quality, not as validated.

**Download.**

| | |
| --- | ---: |
| inference bundle | 62,165,563 B (59.3 MiB) |
| ESM-2 weights, fetched once by `transformers` | 2,609,506,392 B (2.43 GiB) |
| **total before a first query** | **2.49 GiB** |

## Local browser interface

A browser front end over the same inference API, for people who would rather not
use a terminal. It is **localhost only**.

```bash
uv run seq2lead web --bundle ./seq2lead-bundle
```

Then open <http://127.0.0.1:8765>. `--preload` loads the protein encoder at
startup instead of on the first query, trading a slower start for a faster first
result.

> **This is not a hosted service and must not be run as one.** It binds to
> 127.0.0.1 and refuses any other address. There is no authentication, no TLS and
> no rate limiting. If you need it from another machine, forward the port over
> SSH rather than binding publicly.

### What it does

- **Rank a sequence** — paste a sequence or a single FASTA record, or choose a
  `.fasta` file, which is read in the browser rather than uploaded as multipart.
  The candidate pool is configurable. Results show the structure, the predicted
  pKi, ties and any warnings, with the same wording as the CLI.
- **See the whole score distribution** — every ranking opens with a histogram of
  all 25,000 scores for that query, with the band the kept rows occupy marked, plus
  the quartiles and standard deviation. A predicted pKi is close to meaningless
  without the spread it came out of: measured spreads are narrow, and medians sit
  near 6.4 for every query in the diagnostic panel
  (`reports/diagnostics/QUERY_DIAGNOSTICS.md`).
- **Inspect one compound** — selecting any row or tile opens a detail panel with a
  large depiction, the full SMILES with a copy button, and computed properties:
  formula, molecular weight, cLogP, TPSA, hydrogen-bond donors and acceptors,
  rotatable bonds, rings, heavy atoms and stereocentres. These are computed from
  the structure with RDKit, not predicted, and none of them took part in ranking —
  the model saw only the ECFP4 fingerprint.
- **Browse the library** — paginated, 24 compounds a page with depictions and
  molecular weight, **substring** search over the compound identifier and the SMILES
  text (not a structure or similarity search), and the same optional
  molecular-weight and TPSA windows. Depictions are drawn once per compound and
  cached per bundle.
- **Export** — CSV byte-for-byte the file `--out-csv` writes, produced by the same
  function, caveat preamble included; and the shortlisted identifiers as a plain
  list for pasting elsewhere.
- **Progress and cancellation** — each ranking is a job with a phase, a progress
  bar and a Cancel button. Cancellation is cooperative: the worker checks between
  phases, so a cancel during embedding takes effect when that phase ends rather
  than instantly.

### How it behaves under the hood

- **The encoder is loaded once** and reused for every query in the process, which
  is why the first ranking is slow and later ones are not.
- **Inference is serialised.** One ranking runs at a time; a second request
  queues and the browser shows its position. Two concurrent rankings would
  contend for the same encoder and the same accelerator, making both slower and
  the reported timings meaningless.
- **Shortlisting is a view, exactly as on the CLI.** The unfiltered ranking is
  always returned in full; a removed row keeps its original rank and score and is
  marked in place with the reason it was removed. Filters and diversity act on the
  candidate pool that was kept, not on the whole library — with a pool of 50, a
  weight window filters those 50 rows rather than searching the other 24,950.
- **Bad input is refused with a reason.** A non-finite or unparseable bound, a
  negative one, an inverted window, an out-of-range page or a row outside the
  library each return 400 with a specific message instead of an empty-looking
  result. The browser shows that message next to the control that caused it.
- **Accessible by keyboard.** Every control is reachable by Tab with a visible
  focus ring, arrow keys move between ranked rows, Enter opens the detail panel,
  and closing it returns focus to the row it came from. Contrast is at least
  4.5:1 in both the light and dark colour schemes.
- **No external resources.** The page is served inline and the
  Content-Security-Policy allows nothing off-origin, so it works with no network
  beyond the first ESM-2 download.

### Verified against the CLI

The interface does not re-implement scoring; it calls
`seq2lead.inference.rank` exactly as the CLI does. A test asserts the rows
returned over HTTP equal the rows the library call produces for the same query,
and the live server was driven against the real 25,000-compound bundle and
reproduced the database-backed reference ranking exactly.

Not built, deliberately: custom-library upload (same reason as on the CLI), user
accounts, and any form of deployment.

## Optional shortlisting

**Off unless asked for, and it never replaces the ranking.** Shortlisting is a
view: every removed compound keeps the rank and score it had and is reported with
the reason, so you can see what the filter did rather than infer it from an
absence.

Property windows:

```bash
uv run seq2lead prioritise --bundle ./seq2lead-bundle --sequence-file reports/examples/query_target.fasta --top-n 200 --max-mw 500 --max-tpsa 140 --out-csv ranking_filtered.csv
```

Chemical diversity:

```bash
uv run seq2lead prioritise --bundle ./seq2lead-bundle --sequence-file reports/examples/query_target.fasta --top-n 200 --diverse 20 --diversity-threshold 0.7 --out-csv ranking_diverse.csv
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
