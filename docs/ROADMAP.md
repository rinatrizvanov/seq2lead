# Roadmap

What is built, what is declared but unbuilt, and what would have to be true before
each remaining item is worth doing. Nothing here is scheduled or promised.

## Built

Curation with preserved row-level provenance · exact and censored endpoint
semantics kept apart · five leakage-controlled split families · ECFP4 and ESM-2
feature caches bound to the content they were computed from · six baseline families
and a dual encoder · counted snapshot matching between two dated BindingDB
releases · per-target tie-aware ranking metrics · a three-gate publication path
that refuses metrics not bound to their inputs · a command-line ranking tool · an
independent docking assessment.

## Declared but unbuilt

These were specified and then deliberately not built. Each is a real gap, not a
plan.

| Item | State | What it would take |
| --- | --- | --- |
| **Retrieval layer** | unbuilt | It was specified as an interpretability layer and would have to beat the within-target nearest-neighbour baseline on a hard split before becoming a model input. |
| **Evaluation-mode evidence display** | disabled | Showing measured evidence next to a prediction needs a partition-filtered evidence API, so that a held-out measurement cannot reach an evaluation view. Until that exists the display is off rather than filtered by convention. |
| **`label_reversal` split** | built, unscored | The split exists; no model has been scored on it. It is the one split where ligand memorisation is penalised rather than merely unhelpful, so scoring it would test a different claim from the current results. |
| **Calibration** | not computed | Outputs are ranking scores in pKi units. No reliability diagram or expected calibration error has been computed, which is why nothing is described as a probability. |
| **Cross-attention ablation** | unbuilt | Pre-registered as the architectural comparison most likely to matter on `label_reversal`, and therefore blocked behind scoring that split. |

## What would make the evaluation stronger

In rough order of how much each would change the conclusions.

1. **A confirmatory experiment.** The current historical study is exploratory: the
   later snapshot's labels and the earlier results were both inspected before it
   ran, and the prospective freeze is unsigned. A confirmatory run needs a complete
   dated specification followed by an unseen release or independently withheld
   labels.
2. **Re-pin both snapshots to archival deposits.** The September snapshot is a
   rolling monthly release that BindingDB does not archive at a stable URL, so a
   third party may be unable to acquire the exact bytes. Pinning both sides to
   quarterly deposits would make a from-raw rebuild independently reproducible.
3. **Paired target-level uncertainty.** The reported difference between the dual
   encoder and the concatenation baseline has no paired interval or test, so it
   supports neither a difference nor an equivalence claim.
4. **Resolve Ki poolability.** Pooling Ki across assay contexts is provisional, and
   every count assumes the current rule. Deciding it either restricts the endpoint
   to assay-homogeneous subsets or justifies the pooling.
5. **Raise rankable coverage.** Only 123 of 992 targets clear the five-active and
   five-inactive floor, so the macro metrics describe a minority. Any change here
   is a change to the cohort, and would mint a new dataset version rather than
   amend the current numbers.

## Out of scope

Molecule generation, pose prediction, and any claim of experimental validation.
Docking appears only as a separately reported assessment that failed its gate; it
is not part of the ranking pipeline.
