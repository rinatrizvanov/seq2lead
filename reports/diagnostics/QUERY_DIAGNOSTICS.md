# Query diagnostics: how much does the ranking move between proteins?

Exploratory diagnosis, run 2026-10-07 against the v0.2.0 inference bundle
(`seq2lead-inference-bundle-v1`, library `curated-ki-25k-v1`, 25,000 compounds).

**Scope and limits, stated first.** Nothing here evaluates accuracy, nothing here
was used to tune anything, and nothing here supports a claim of biological
specificity. None of the five panel proteins appears in the benchmark, so no
measured labels exist for any pair discussed below. Two queries agreeing is not
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

## Where the agreement comes from — measured on the protein side

The rank agreement above could arise from the compound side, from the protein
side, or from the scoring geometry. Since the score is
`scale · cos(compound_z, protein_z) + offset`, two query vectors that are nearly
parallel must produce nearly identical rankings. So the projected query vectors
were measured directly rather than inferred from the correlated outputs:

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

The mean-pooled ESM-2 embeddings of five unrelated proteins are already highly
similar — mean pairwise cosine 0.8626, minimum 0.6728. That is a property of mean
pooling over a 650M-parameter language model, present before Seq2Lead sees the
sequence at all.

**Every projection moves the pair apart, without exception** (all ten deltas
negative, −0.098 to −0.522; mean cosine 0.8626 → 0.5631). The learned protein
tower is therefore *decorrelating* these queries, not collapsing them. The
residual rank agreement is inherited from the input geometry that the single
linear layer only partly undoes, and the pairs that stay closest after projection
(`NFKB1`/`VCP` 0.8590, `SACS`/`VCP` 0.8247) are exactly the pairs with the highest
rank correlation. That is the mechanism, and it sits upstream of the tower.

This parallels the finding in `projection_repeats.json` on the compound side: in
both cases the representation entering the tower, not the tower, is where
information is lost.

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

Established, by measurement:

- rankings for unrelated proteins in this panel agree at ρ up to 0.9366 with
  18–19/20 shortlist overlap;
- high rank correlation does not imply shortlist overlap (ρ = 0.8040 with 0/50);
- mean-pooled ESM-2 embeddings of unrelated proteins are highly similar
  (mean cosine 0.8626) and the protein tower reduces that similarity for all ten
  pairs;
- top-20 shortlists are chemically redundant for four of five queries.

Not established, and not claimed:

- that any of these rankings is accurate — there are no labels here;
- that the model does or does not discriminate between protein families in
  general, from a panel of five with one related pair and four out-of-domain
  proteins;
- that the published benchmark numbers are affected. They are not recomputed,
  revised or contradicted by anything above; the benchmark's own ligand-only gate
  (B1) is the measurement that speaks to compound-side dominance under labels, and
  it is unchanged.

Open questions a future experiment could settle: whether the agreement shrinks for
proteins inside the training distribution; whether a per-residue or attention
pooling replaces enough of the lost protein detail to separate the queries; and
how much of the shared ranking survives the ligand-only baseline's prediction.
None of these was pursued, because each needs training and this was diagnosis.
