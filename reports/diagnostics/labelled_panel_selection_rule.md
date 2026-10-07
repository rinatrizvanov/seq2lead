# Labelled-panel selection rule — fixed 2026-10-07, before any score was computed

Written down first so the panel cannot be chosen by how well the model does on it.
Nothing below refers to a predicted score, a rank or a metric.

1. **Labels.** `data/asof/m11h/run-20261005T134842Z/evaluation-pairs.jsonl`,
   arm `declared_increment`, `scoreable == true`, label in {active, inactive}.
   This is the held-out evaluation set of the published as-of run.
2. **Compound eligibility.** The pair's InChIKey must resolve to a row of the
   bundled `curated-ki-25k-v1` library, via the RDKit InChIKey of the bundle's
   stored SMILES. Compounds outside the bundle cannot be ranked by the shipped
   artifact and are dropped.
3. **Target eligibility.** The target sequence must resolve from the `target`
   table by `sequence_sha256`, and must be inside the bundle's declared length
   window so it runs without a refusal.
4. **Label-count floor.** Keep targets with **>= 10 actives and >= 10 inactives**
   among eligible compounds, so AUROC is not decided by one or two rows.
5. **Ordering.** Descending by total eligible labelled compounds; ties broken by
   `sequence_sha256` ascending. Deterministic and performance-blind.
6. **Size.** The first **12** targets. Bounded in advance.
7. **Training exposure** is recorded, never used to select: for each target,
   whether it appears in `data/asof/m11f/a-membership.jsonl` (the training
   membership export, digest verified against the run manifest), and how many of
   its evaluation compounds co-occur with it there.

No target is added, dropped, reordered or re-cut after metrics are seen. If the
rule yields fewer than 12 targets, the panel is smaller and that is reported.

## What the rule did not include

Exposure to the **shipped checkpoint** was not a selection criterion. The rule
screens on label availability and class counts only, and "held out" above refers
to the m11h as-of evaluation partition, which is a different experiment from the
m9 `cold_protein-v3` run whose checkpoint the bundle ships. Exposure to that
checkpoint was measured *after* the panel was fixed, and 8 of the 12 targets
proved to be train-exposed to it. That is reported in
[`labelled_panel.md`](labelled_panel.md) and
[`panel_exposure.json`](panel_exposure.json) and was **not** used to reselect,
reorder or drop any target.
