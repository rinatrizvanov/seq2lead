# Repeated compound projections: where they come from

Exploratory diagnosis, run 2026-10-07 against the v0.2.0 inference bundle. No
model was retrained, no weight was changed, and no published number is revised by
anything here. Raw output: `reports/diagnostics/projection_repeats.json`.

## The observation

`library/projections.npy` holds 25,000 × 512 float32 compound vectors. Of those
rows, only **24,702 are distinct**. The repeats form **218 groups** covering
**516 compounds** (2.06% of the library); the largest group has 16 members, then
7, 6, 6, 5, 5, 5, 4.

Two compounds sharing a projection receive an identical score against every
possible query, since the score is an affine function of cosine against the query
vector. They are indistinguishable to the model for all inputs, not just for one.

## Why identical outputs are not themselves an explanation

Identical output vectors are consistent with at least three different causes:
identical inputs to the tower, distinct inputs that the tower maps together, or a
defect in how projections were written to disk. Reading the cause off the outputs
alone would be guessing between them. So the inputs were recomputed from SMILES
and compared directly.

## The six named compounds

All six share **one** projection vector. Recomputing their ECFP4 fingerprints
(2,048 bits, radius 2, chirality on — the bundle's declared settings) gives **one
distinct fingerprint** across all six, digest `a5c046b422d6…`, with 78 bits set in
each. The fingerprints are byte-identical.

The SMILES are not:

| Compound | Linker | Carbons |
| --- | --- | --- |
| 1382349 | `OCCCCC` | 5 |
| 1382347 | `OCCCCCC` | 6 |
| 1382356 | `OCCCCCCC` | 7 |
| 1382343 | `OCCCCCCCC` | 8 |
| 1382341 | `OCCCCCCCCC` | 9 |
| 1382339 | `OCCCCCCCCCC` | 10 |

They are a homologous series of macrocyclic HIV-protease-inhibitor-like compounds
(bis-tetrahydrofuranyl carbamate, hydroxyethylamine core, aryl sulfonamide)
differing *only* in the length of the polymethylene tether — 5 to 10 carbons, a
complete run with no gaps.

This is the expected behaviour of a circular fingerprint, not a defect. ECFP4
enumerates substructures within two bonds of each atom. Every carbon in the
interior of a long `-CH2-` chain has the identical two-bond environment
(`C(-C)(-C)` with hydrogens), so it hashes to the same bit that is already set.
Lengthening the chain adds more copies of a bit that is on and changes nothing
else. The fingerprint is blind to chain length beyond its radius, and the bit
count stays at 78 for all six.

## Checking all 218 groups, not just the six

The six could have been a special case, so the same comparison was run across the
whole library:

| Measurement | Count |
| --- | --- |
| Repeated-projection groups | 218 |
| …whose members have **identical** fingerprints | **218** |
| …whose members have **differing** fingerprints | **0** |
| Duplicate-fingerprint groups in the library | 218 |
| …that produced **differing** output vectors | **0** |

The two directions close on each other. Every repeated projection traces to an
identical ECFP4 fingerprint, with no exceptions; and every set of duplicate
fingerprints produced one shared output, as a deterministic function must.

## Conclusion

The repetition is **entirely upstream of the learned model**. It is created by
ECFP4 at radius 2, which cannot represent the distinctions these compounds differ
by, and the compound tower then behaves as any deterministic function does on
equal inputs. The tower collapses nothing beyond what its input already lost:
there is not one group where distinct fingerprints were mapped to a shared vector.

## Consequences, stated plainly

- Roughly 2% of the library is partitioned into model-indistinguishable sets. A
  query cannot rank within such a set, in any order, for any protein — ties there
  are structural, not numerical coincidence.
- Shortlists can fill with members of one series. This is visible in
  `reports/diagnostics/QUERY_DIAGNOSTICS.md`, where top-20 mean internal Tanimoto
  reaches 0.696 and every query has a near-duplicate pair at ≥ 0.94.
- The fix is a representation change, not a scoring change: a fingerprint or
  descriptor set that encodes chain length and global shape — a path-based or
  topological-distance-aware feature, or a graph encoder. That is a retraining
  task and was deliberately not attempted here.
- The diversity filter in the local interface mitigates the *symptom* for a
  shortlist. It does not recover the lost distinction, and it is not presented as
  doing so.

Nothing above affects the published benchmark results, which used the same
fingerprint definition throughout and are unchanged.
