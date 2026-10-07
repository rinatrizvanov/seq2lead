# Query diagnostics: how much does the ranking move between proteins?

Exploratory diagnosis, run 2026-10-07 against the v0.2.0 inference bundle
(`seq2lead-inference-bundle-v1`, library `curated-ki-25k-v1`, 25,000 compounds).

**This is the unlabelled, exploratory panel.** It is retained as exploratory
evidence and is *not* the basis for any conclusion about target specificity. The
labelled measurement is in
[`labelled_panel.md`](labelled_panel.md), which uses held-out labels, a
pre-declared selection rule and a query-independent baseline. Where the two
disagree, the labelled panel is the one that measured the question.

**Scope and limits.** Nothing here evaluates accuracy, nothing here was used to
tune anything, and nothing here supports a claim of biological specificity. None
of the five panel proteins appears in the benchmark, so no measured labels exist
for any pair discussed below. Two queries agreeing is not
evidence that either ranking is correct, and two queries disagreeing is not
evidence that either is right. Scores and ranks were read, never modified.

Raw numbers: `reports/diagnostics/query_panel.json`.

## The panel

Five sequences, each ranked against the full library. Digests are SHA-256 of the
cleaned sequence actually embedded, so any result here can be tied to its exact
input.

| Query | Length | SHA-256 (first 16) | Why it is in the panel |
| --- | --- | --- | --- |
| `VCP` | 806 | `7762b7e6637bdd56` | Requested. AAA+ ATPase, p97. |
| `SACS` | 4,579 | `5b976fe4afbf54bd` | Longest protein available locally; tests the far end of the length range. |
| `RELA` | 287 | `b99cecf7c0b50caf` | NF-κB p65 Rel homology domain. |
| `NFKB1` | 324 | `abb80ddd28da6c2d` | NF-κB p50 Rel homology domain — a genuine homolog of `RELA`, included deliberately as the one related pair. |
| `BUNDLED` | 443 | `8c2b6e1477ff2109` | The example sequence shipped with the repository. |

**SMARCAL1 is not in the panel.** It was requested, and no SMARCAL1 FASTA exists
on this machine — `~/Downloads`, `~/Desktop` and the local
`BindingDBTargetSequences.fasta` were all searched by header and by accession
(Q9NZC9), with no match. Nothing was substituted for it, and the panel is four
proteins plus the bundled example rather than five plus it.

All five are inside the 20–2,000 declared execution range except `SACS` at 4,579
residues, which is past it; it ran without error and is marked
`past_training_window` in the JSON. Its numbers are reported but should be read as
out-of-range behaviour.

## Score distributions

| Query | min | median | max | sd | top-1 |
| --- | --- | --- | --- | --- | --- |
| `BUNDLED` | 4.414 | 6.472 | 9.173 | 0.533 | 1369460 |
| `NFKB1` | 3.147 | 6.458 | 10.481 | 0.872 | 1351830 |
| `RELA` | 3.424 | — | 10.277 | 0.904 | 1492297 |
| `SACS` | 3.822 | — | 10.721 | 0.843 | 1351820 |
| `VCP` | 3.260 | — | 9.956 | 0.789 | 1351830 |

Medians sit near 6.4–6.5 pKi for every query, and three of five queries put the
same two compound IDs (1351830, 1351820) at rank 1. The bundled example has a
visibly narrower spread (sd 0.533) than the rest.

## Rank agreement between different proteins

| Pair | Spearman ρ | Kendall τ | top-20 shared | top-50 shared |
| --- | --- | --- | --- | --- |
| `SACS` vs `VCP` | **0.9366** | 0.7825 | 18/20 | 33/50 |
| `NFKB1` vs `VCP` | **0.9219** | 0.7610 | 19/20 | 28/50 |
| `NFKB1` vs `SACS` | 0.8778 | 0.6960 | 18/20 | 31/50 |
| `NFKB1` vs `RELA` | 0.8040 | 0.6106 | **0/20** | **0/50** |
| `RELA` vs `VCP` | 0.6594 | 0.4765 | 0/20 | 0/50 |
| `BUNDLED` vs `SACS` | 0.5900 | 0.4187 | 9/20 | 16/50 |
| `BUNDLED` vs `VCP` | 0.5851 | 0.4153 | 9/20 | 16/50 |
| `BUNDLED` vs `NFKB1` | 0.5801 | 0.4129 | 9/20 | 16/50 |
| `RELA` vs `SACS` | 0.5704 | 0.4030 | 0/20 | 0/50 |
| `BUNDLED` vs `RELA` | 0.3229 | 0.2200 | 0/20 | 0/50 |

Two things stand out, both measured rather than inferred.

**Unrelated proteins rank the library very similarly.** Sacsin (4,579 aa, a
HSP70-domain protein) and VCP (806 aa, an AAA+ ATPase) share no function, no fold
family and no ligand class, and their rankings over 25,000 compounds agree at
ρ = 0.9366 with 18 of the top 20 in common. NF-κB p50 against VCP is ρ = 0.9219,
19 of 20. The ranking is therefore substantially query-independent across this
panel: a large shared component is coming from the compound side.

**Global correlation and head overlap come apart.** `NFKB1` vs `RELA` correlates
at ρ = 0.8040 over the whole library yet shares **nothing** in the top 50. A high
Spearman value across 25,000 items is compatible with completely disjoint
shortlists, because the correlation is dominated by the bulk and the shortlist is
decided by the tail. Anyone reading these numbers should treat rank correlation and
shortlist overlap as separate quantities; neither substitutes for the other.

The one biologically related pair in the panel behaves no more similarly than the
unrelated ones. `RELA` and `NFKB1` are both Rel homology domains and agree at
ρ = 0.8040, *below* sacsin-vs-VCP at 0.9366 — and with zero shortlist overlap
against each other's. In this panel, ranking similarity does not track biological
relatedness.

## The protein side, measured

The rank agreement above could arise from the compound side, from the protein
side, or from the scoring geometry. Since the score is
`scale · cos(compound_z, protein_z) + offset`, two query vectors that are exactly
parallel would produce identical rankings. So the projected query vectors were
measured directly rather than inferred from the correlated outputs:

| Pair | cosine, raw mean-pooled ESM-2 | cosine, projected query vector | change |
| --- | --- | --- | --- |
| `NFKB1` vs `VCP` | 0.9567 | 0.8590 | −0.0977 |
| `SACS` vs `VCP` | 0.9287 | 0.8247 | −0.1040 |
| `NFKB1` vs `SACS` | 0.9200 | 0.7424 | −0.1776 |
| `NFKB1` vs `RELA` | 0.8515 | 0.6685 | −0.1831 |
| `RELA` vs `VCP` | 0.7931 | 0.4964 | −0.2967 |
| `RELA` vs `SACS` | 0.6728 | 0.3559 | −0.3168 |
| `BUNDLED` vs `VCP` | 0.9274 | 0.5542 | −0.3732 |
| `BUNDLED` vs `NFKB1` | 0.9118 | 0.4948 | −0.4171 |
| `BUNDLED` vs `RELA` | 0.7303 | 0.2232 | −0.5071 |
| `BUNDLED` vs `SACS` | 0.9337 | 0.4122 | −0.5216 |
| mean | 0.8626 | 0.5631 | −0.2995 |

### What this measures

- Mean-pooled ESM-2 embeddings of these five proteins are highly similar to one
  another: mean pairwise cosine 0.8626, minimum 0.6728. That is a property of the
  embedding, present before Seq2Lead sees the sequence.
- The projection moves every pair apart, without exception: all ten cosines fall,
  by 0.098 to 0.522, mean 0.8626 → 0.5631.
- Within this panel, the two pairs with the highest projected cosine
  (`NFKB1`/`VCP` 0.8590, `SACS`/`VCP` 0.8247) are also the two with the highest
  rank correlation.

### What this does not establish

**It does not establish the cause of the cross-query rank agreement.** An earlier
version of this report said the residual agreement was "inherited from the input
geometry" that the tower "only partly undoes". That was an inference presented as
a measurement, and it is withdrawn. A drop in pairwise cosine shows the
projection separates these queries more than the embedding did; it says nothing
about which stage is responsible for the agreement that remains, and the
association between projected cosine and rank correlation is an ordering over ten
points in a panel of five, not a causal test.

Ten paired cosines also cannot separate the candidate explanations. Agreement
could come from the compound side — a direction in compound space that most query
vectors have a positive component along — from the protein side, from the
interaction of the two, or from the embedding and the tower jointly. Nothing here
distinguishes them.

### Correction: the tower architecture

The same withdrawn passage described the protein tower as a "single linear
layer". That is wrong. Read directly from the shipped bundle
(`model/protein_tower.npz`, `model/compound_tower.npz`):

| Tower | Shape | Structure |
| --- | --- | --- |
| protein | `0.weight` (512, 1280), `0.bias` (512), `2.weight` (512, 512), `2.bias` (512) | 1280 → 512, ReLU, 512 → 512 |
| compound | `0.weight` (512, 2048), `0.bias` (512), `2.weight` (512, 512), `2.bias` (512) | 2048 → 512, ReLU, 512 → 512 |

Each tower is a **two-layer MLP with a ReLU between the layers**, and the protein
tower is preceded by a standardisation transform (`protein_transform.npz`, a
fitted mean and scale over the 1280 embedding dimensions). `project_query` in
`src/seq2lead/inference/rank.py` applies exactly that sequence. The map is
non-linear, so reasoning that treats it as a single linear operator — including
any expectation that it should act uniformly on pairwise angles — does not hold.

### Hypotheses this suggests, none of them tested here

- That mean-pooled embeddings discard pocket-level detail, so proteins that
  differ functionally can arrive close together. *Testable by comparing pooling
  strategies; not attempted.*
- That a query-independent compound ordering accounts for much of what the model
  produces for queries far from the training distribution. The labelled panel
  shows such an ordering is *competitive*, which is consistent with this but does
  not measure a share — AUROC does not decompose that way.
- That agreement shrinks for targets inside the training distribution.
  **This was measured on the labelled panel: mean pairwise rank correlation 0.42
  there, against 0.88–0.94 here.**

## Shortlist chemical redundancy

Mean pairwise Tanimoto within each query's own top 20:

| Query | mean | max |
| --- | --- | --- |
| `VCP` | 0.696 | 0.989 |
| `NFKB1` | 0.692 | 0.989 |
| `SACS` | 0.616 | 0.989 |
| `RELA` | 0.605 | 0.946 |
| `BUNDLED` | 0.298 | 0.989 |

Four of five shortlists are chemically redundant: a mean internal Tanimoto of
0.6–0.7 means a top-20 is largely one chemical series rather than twenty
independent suggestions, and every query has a near-duplicate pair at ≥ 0.94. A
20-compound shortlist is worth fewer than 20 distinct ideas. This is consistent
with the homologous-series effect documented in
`reports/diagnostics/projection_repeats.md`, where 516 compounds share a
projection with at least one other because ECFP4 at radius 2 cannot see
polymethylene chain length.

The diversity filter now available in the interface operates on exactly this
quantity, and these are the numbers it is there to address.

## What this does and does not establish

### Measured

These are read off the numbers above and require no interpretation:

- rankings for unrelated proteins in this panel agree at ρ up to 0.9366 with
  18–19/20 shortlist overlap;
- high rank correlation does not imply shortlist overlap (ρ = 0.8040 with 0/50);
- mean-pooled ESM-2 embeddings of unrelated proteins are highly similar
  (mean cosine 0.8626) and the protein tower reduces that similarity for all ten
  pairs;
- top-20 shortlists are chemically redundant for four of five queries.

### Hypotheses

These are consistent with the measurements and are *not* established by them.
They are listed so they are not mistaken for findings:

- that a query-independent compound ordering explains a given *share* of any
  ranking. The labelled panel shows such a baseline is **competitive** — macro
  AUROC 0.7014 pooled, and 0.7157 on the four targets held out from the shipped
  checkpoint, where it beat the target-specific ranking. "Competitive" is a
  comparison, not an attribution: AUROC is not additive and no share of
  performance is assigned to either side;
- that mean pooling is where protein detail is lost. Untested;
- that the tower's effect on pairwise angles explains the residual agreement.
  Withdrawn as stated above; ten paired cosines cannot support it.

### Not established, and not claimed

- that any of these rankings is accurate — there are no labels here;
- that the model does or does not discriminate between protein families in
  general. A panel of five, with one related pair and four proteins far outside
  the training distribution, cannot answer that. **The labelled panel shows this
  panel was not representative**: across twelve in-domain targets the mean
  pairwise rank correlation is 0.42, against 0.88–0.94 for the unrelated pairs
  here. Note also that eight of those twelve targets were train-exposed to the
  shipped checkpoint, so even that comparison is not a clean unseen-target
  measurement — see [`labelled_panel.md`](labelled_panel.md);
- that the published benchmark numbers are affected. They are not recomputed,
  revised or contradicted by anything above; the benchmark's own ligand-only gate
  (B1) is the measurement that speaks to compound-side dominance under labels, and
  it is unchanged.

Open questions. Whether the agreement shrinks for proteins inside the training
distribution **has since been measured** — it does; see
[`labelled_panel.md`](labelled_panel.md). Still open: whether per-residue or
attention pooling would recover enough protein detail to separate queries, and
how much of the shared ranking survives the ligand-only baseline. Both need
training and neither was attempted.
