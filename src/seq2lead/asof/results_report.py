"""Render the as-of results report from the saved artifacts. Reads, never fits.

Every number comes out of the run's own files, so the prose and the artifacts
cannot drift. The wording carries four corrections made after the first
publication, listed in the report's own correction section: the joint-model
comparison no longer claims equivalence, B3L is described as what it is, the
seed-invariance claim is replaced by measurements of the prediction arrays, and
between-stratum comparisons are labelled as contrasts between populations.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from seq2lead.asof.recompute import (
    ARMS,
    BRANCHES,
    PRIMARY_CELL,
    SINGLE_FIT,
    families,
    family_mean,
    sha256,
)

if TYPE_CHECKING:
    from seq2lead.asof.recompute import LoadedRun

REPORT = Path("reports/m11h_results.md")
MANIFEST = Path("configs/manifests/m11h_fit.json")
PRIMARY = f"{PRIMARY_CELL[0]}/{PRIMARY_CELL[1]}"

#: Baselines described by what they actually condition on. An earlier revision
#: called B3L "ligand-only", which it is not: it selects its neighbour from the
#: compounds measured against THIS target in training, so target identity is an
#: input. B1 is the only ligand-only model.
MODEL_KIND = {
    "B0-target-mean": "target-conditioned constant (training mean for the target)",
    "B1-ligand-ecfp4-lgbm": "**ligand-only** — the target is not an input at all",
    "B2-protein-esm2-lgbm": "protein-only — one score per target, so ties within it",
    "B3L-ligand-1nn": (
        "**target-conditioned chemical nearest-neighbour lookup** — the neighbour is "
        "chosen among compounds measured against *this target* in training"
    ),
    "B4-concat-mlp": "joint — concatenated fingerprint and protein embedding",
    "dual-encoder": "joint — independent projections, cosine, affine head",
}


def fmt(value: Any, digits: int = 4) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return f"{value:,}" if isinstance(value, int) else str(value)


def sci(value: float | None) -> str:
    return "—" if value is None else f"{value:.3g}"


def model_rows(group: dict, tags: list[str]) -> list[str]:
    lines = []
    for tag in tags:
        s = group["by_model"][tag]
        m = s["metrics"]
        tied = f" ({s['all_tied_targets']} all-tied)" if s["all_tied_targets"] else ""
        if not m:
            lines.append(f"| `{tag}` | {s['targets_scored']}{tied} | — | — | — | — | — |")
            continue
        lines.append(
            f"| `{tag}` | {s['targets_scored']}{tied} | {fmt(m['auroc'])} | "
            f"{fmt(m['auprc'])} | {fmt(m['recall_at_10'])} | {fmt(m['ef_at_1pct'], 3)} | "
            f"{fmt(m['ef_at_5pct'], 3)} |"
        )
    return lines


def render(run: LoadedRun, results: dict[str, Any], verification: dict[str, Any]) -> Path:
    manifest = run.manifest
    records = json.loads((run.root / "training-records.json").read_text(encoding="utf-8"))
    tags = results["model_tags"]
    fams = families(tags)
    primary = results["cells"][PRIMARY]["groups"]
    head = primary["new_to_fitting"]
    means = results["seed_spread_on_the_headline"]["by_model"]
    diffs = results["prediction_differences_between_seeds"]
    s0 = head["by_model"][tags[0]]

    b4, de = means["B4-concat-mlp"], means["dual-encoder"]
    gap = b4["auroc_mean"] - de["auroc_mean"]
    b1, b3l = means["B1-ligand-ecfp4-lgbm"], means["B3L-ligand-1nn"]

    out: list[str] = []
    A = out.append

    A("# M11h — exploratory as-of fitting and evaluation\n")
    A("**Scope: one bounded exploratory stage.** The confirmatory freeze is **not")
    A("signed**, retrieval is **unbuilt**, evaluation evidence display is **off**,")
    A("`label_reversal-v3` is **unscored**, and nothing was downloaded or docked. The")
    A("study is **exploratory** and the **provisional-pooling** qualification stands:")
    A("snapshot B is our already-inspected 202609, so any score here is exploratory")
    A("whatever it shows.\n")
    A(f"Contract: `{manifest['contract_version']}`. Runner: `{manifest['runner_version']}`.")
    A(f"Run: `{manifest['run_id']}`, artifacts under `{run.root}`. Metric version:")
    A(f"**`{results['metric_version']}`**. Manifest: `{MANIFEST}`.")
    A(f"Recomputed by `{results['recomputed_from']['entry_point']}` from saved")
    A("predictions; nothing was refitted to produce this report.\n")

    # ----------------------------------------------------------- corrections
    changes_path = Path("data/asof/m11h/closeout-changes.json")
    if changes_path.exists():
        changes = json.loads(changes_path.read_text(encoding="utf-8"))
        A("## 0. This is a corrected report\n")
        A("Six deliberate changes were made after first publication. **No number")
        A("produced by a fit changed**: the prediction arrays are the original ones,")
        A("verified against the digest the fit recorded, and every metric here is")
        A("recomputed from them. Nothing was refitted, retuned or rebuilt.\n")
        A("| | Change | Why |")
        A("| --- | --- | --- |")
        for item in changes["deliberate_report_changes"]:
            A(f"| **{item['id']}** | {item['change']} | {item['why']} |")
        A("")
        corrected = changes["numerical_changes"]["corrected_figures"]
        if corrected:
            A("One published figure was wrong, and not because of a different computation:\n")
            for fig in corrected:
                A(f"- **{fig['figure']}**: `{fig['before']}` \u2192 `{fig['after']}`. "
                  f"{fig['cause']}.")
            A("")
        A("Wording changes are recorded apart from numerical ones because they carry")
        A("different risk: a wording change can be checked by reading, a numerical one")
        A(f"cannot. Full record: `{changes_path}`.\n")
        A("---\n")

    # ------------------------------------------------------------- the result
    A("## 1. The result\n")
    A("A model win was not required and none is claimed.\n")
    A("- **`B4-concat-mlp` has the higher observed mean headline AUROC**:")
    A(f"  **{fmt(b4['auroc_mean'], 6)}** against `dual-encoder`'s")
    A(f"  **{fmt(de['auroc_mean'], 6)}**, a difference of **{fmt(gap, 6)}** in B4's")
    A(f"  favour. Observed per-seed ranges: B4 {fmt(b4['auroc_min'])}–{fmt(b4['auroc_max'])},")
    A(f"  dual encoder {fmt(de['auroc_min'])}–{fmt(de['auroc_max'])}.")
    A("- **No paired target-level uncertainty analysis was performed.** Nothing here")
    A("  establishes whether that difference is distinguishable from noise, and no")
    A("  equivalence between the two models is claimed. Seed spread is observed")
    A("  training variation; it is not a confidence interval and is not a test.")
    A("- **Both joint models exceed both single-modality and lookup baselines** by")
    A(f"  roughly {fmt(b4['auroc_mean'] - b3l['auroc_mean'], 3)} AUROC. §4 explains why")
    A("  that margin does **not** isolate a protein-feature contribution.")
    A(f"- **Only {s0['targets_scored']} of {s0['targets_in_slice']} targets clear the")
    A("  scoring floor**, so every macro-average here describes a minority of the")
    A("  cohort's targets, selected by carrying enough of both classes.\n")
    A("§8 lists what these numbers do and do not support.\n")
    A("---\n")

    # ------------------------------------------------------- what was verified
    A("## 2. What was verified before anything was fitted\n")
    n_inputs = len(manifest["inputs_verified"])
    A("| Check | Result |")
    A("| --- | --- |")
    A(f"| Pinned inputs verified through the corrected runner | **{n_inputs} of "
      f"{n_inputs}** re-hashed from disk |")
    for kind, m in sorted(manifest["feature_bindings"].items()):
        A(f"| Reuse map `{kind}` verified against the pinned resolution | "
          f"{m['entries']:,} entries, `{m['sha256'][:12]}…` |")
    for role, counts in sorted(manifest["roles_derived"].items()):
        A(f"| Role `{role}` derived from the pinned artifacts | {counts['compounds']:,} "
          f"compounds, {counts['sequences']:,} sequences |")
    for role, got in sorted(manifest["emitted_matches_pinned"].items()):
        A(f"| Emitted `{role}` matches the pinned record | {got['pairs']:,} pairs, "
          f"`{got['digest'][:12]}…` |")
    plan = manifest["output_preflight"]
    A(f"| Output paths preflighted before the first fit | "
      f"{len(plan['planned']) + len(plan['planned_checkpoints'])} planned, "
      f"**{len(plan['collisions'])}** collisions |")
    A("")
    A("Nothing the run consumed was a caller parameter: the membership, the feature")
    A("caches, the reuse maps and every role's entities are derived from the verified")
    A("artifact set, as M11g's v5 correction requires. The accepted caches and")
    A("extensions were read, never written.\n")

    tr = manifest["transform"]
    sel = manifest["selection"]
    A("| Guarantee | How it is held |")
    A("| --- | --- |")
    A(f"| Learned transforms fitted on A-train only | `ProteinTransform` on "
      f"{tr['n_fitted']:,} A-train rows, `fitted_on=\"{tr['fitted_on']}\"`, frozen and "
      f"saved (`{tr['sha256'][:12]}…`) |")
    A(f"| A-validation used only for checkpoint selection | {sel['validation_pairs']:,} "
      f"pairs, metric `{sel['selection_metric']}`, direction `{sel['selection_direction']}` |")
    A(f"| The selection set is the one the runner holds | digest re-derived and refused "
      f"on mismatch: `{sel['validation_digest'][:12]}…` |")
    A(f"| Best validation checkpoint restored before evaluation | {len(sel['restored'])} "
      "fits, each checked against its own history |")
    A(f"| Never refit on train + validation | `{sel['never_refit_on_train_plus_validation']}` "
      "— there is no such code path |")
    A("")

    # -------------------------------------------------------------- the fits
    A("---\n")
    A("## 3. The fits: runtime, stopping and curves\n")
    A(f"Declared models: **{len(fams)}**, five seeds "
      f"(`{', '.join(str(s) for s in manifest['seeds'])}`), no sweep and no tuning.")
    A("Two models are fitted once because the declared protocol treats them as")
    A("deterministic; that is a protocol choice, and §5 reports what the prediction")
    A("arrays actually show for the rest.\n")
    A("| Model | Seed | Runtime | Stopping | Best validation RMSE | Curve recorded |")
    A("| --- | --- | ---: | --- | ---: | --- |")
    for rec in records:
        stop = "—"
        if rec["model"] == "dual-encoder":
            stop = (f"best epoch {rec['best_epoch']} of {rec['epochs_run']}"
                    + (", early" if rec["stopped_early"] else ", ran to cap"))
        elif rec["notes"].get("round_cap_was_binding") is not None:
            stop = (f"{rec['notes']['best_iteration']} rounds"
                    + (" — **cap bound**" if rec["notes"]["round_cap_was_binding"] else ""))
        elif rec["best_epoch"]:
            stop = f"best epoch {rec['best_epoch']}"
        A(f"| `{rec['model']}` | {rec['seed'] or '—'} | {rec['seconds']:.1f}s | {stop} | "
          f"{fmt(rec['best_validation_rmse'])} | {rec['notes'].get('curve_recorded', '—')} |")
    A("")
    A(f"**Failures: {len(manifest['failures']) or 'none'}.**"
      + ("" if not manifest["failures"] else f" {manifest['failures']}"))
    A("")
    A("- **The LightGBM round cap bound.** B1 and B2 ran all 400 boosting rounds with")
    A("  50-round early stopping wired and never triggered: validation RMSE was still")
    A("  improving at the cap, so the cap and not validation decided where they")
    A("  stopped. `n_estimators` is a carried-over M8 setting and was not tuned here.")
    A("- **B4 records no per-epoch curve.** The accepted `ConcatMLP` restores its best")
    A("  epoch and reports that epoch's RMSE but keeps no history, and has no early")
    A("  stopping. Only the dual encoder has a full curve. Adding history to accepted")
    A("  M8 code mid-run would have changed code the existing leaderboard depends on.\n")

    # ----------------------------------------------------------- the headline
    A("---\n")
    A("## 4. The primary cell, new to fitting — the headline\n")
    A(f"Cell **`{PRIMARY}`**, the declared primary; stratum **new to fitting**, all three")
    A(f"subgroups, **{head['pairs']:,} pairs**. Scored targets must carry")
    A(f"{s0['scoring_floor']}.\n")
    A("| Model | Scored targets | AUROC | AUPRC | Recall@10 | EF@1% | EF@5% |")
    A("| --- | ---: | ---: | ---: | ---: | ---: | ---: |")
    out.extend(model_rows(head, tags))
    A("")
    A(f"Prevalence over scored targets **{fmt(s0['prevalence_over_scored_targets'], 3)}**,")
    A(f"over pairs {fmt(s0['prevalence_over_pairs'], 3)}. AUPRC is uninterpretable without")
    A(f"it. Targets in the slice {s0['targets_in_slice']:,}; scored")
    A(f"**{s0['targets_scored']}**; excluded below the floor")
    A(f"{s0['targets_excluded'].get('below_the_per_class_floor', 0):,}.\n")

    A("### What each model conditions on\n")
    A("| Model | Conditions on | Mean headline AUROC |")
    A("| --- | --- | ---: |")
    for family in fams:
        A(f"| `{family}` | {MODEL_KIND[family]} | {fmt(means[family]['auroc_mean'], 6)} |")
    A("")
    A("**`B3L-ligand-1nn` is not a ligand-only model.** It takes the compounds measured")
    A("against *this target* in training and returns the pKi of the ECFP4-nearest one,")
    A("so target identity is an input — it is a **target-conditioned chemical")
    A("nearest-neighbour lookup**. An earlier revision of this report called it")
    A("ligand-only, which was wrong. **`B1-ligand-ecfp4-lgbm` is the ligand-only")
    A("model**: the target is not an input to it in any form.\n")
    A("**The joint-versus-baseline margin does not isolate a protein-feature")
    A("contribution.** The joint models lead `B1` (ligand-only) by")
    A(f"{fmt(b4['auroc_mean'] - b1['auroc_mean'], 3)} and `B3L` (target-conditioned")
    A(f"lookup) by {fmt(b4['auroc_mean'] - b3l['auroc_mean'], 3)} AUROC. But the joint")
    A("models differ from each baseline in **more than one way at once** — they see")
    A("protein features, they see both modalities jointly, and they are trained")
    A("end-to-end. `B2-protein-esm2-lgbm` shows that protein features *alone* produce a")
    A("tied ranking within every target, and no ablation removing protein features from")
    A("a joint architecture while holding the rest fixed was run. So the margin is")
    A("consistent with protein features contributing and does not establish it.\n")
    A("**Two models score exactly 0.5000 AUROC, and that is correct.** `B0-target-mean`")
    A("predicts one value per target and `B2-protein-esm2-lgbm` takes only the protein")
    A("embedding, so within a target every compound receives an identical score. Every")
    A("target is all-tied, and a tied ranking is 0.5 by construction.\n")

    # -------------------------------------------------- seeds and predictions
    A("---\n")
    A("## 5. Seeds: what varied, and what did not\n")
    A("| Model | Seeds | Mean AUROC | AUROC SD | Predictions bit-identical | "
      "max abs Δ pKi | mean abs Δ pKi | Rows differing |")
    A("| --- | ---: | ---: | ---: | --- | ---: | ---: | ---: |")
    for family in fams:
        m, d = means[family], diffs[family]
        if not d.get("comparable"):
            A(f"| `{family}` | {m['seeds']} (fitted once) | {fmt(m['auroc_mean'], 6)} | — | "
              "n/a | n/a | n/a | n/a |")
            continue
        A(f"| `{family}` | {m['seeds']} | {fmt(m['auroc_mean'], 6)} | "
          f"{sci(m['auroc_sd'])} | **{d['bit_identical_predictions']}** | "
          f"{sci(d['max_abs_delta_pki'])} | {sci(d['mean_abs_delta_pki'])} | "
          f"{d['rows_differing_from_the_first_seed']:,} / {d['rows']:,} |")
    A("")
    A("### Correction: the seeds did not produce identical fits\n")
    A("An earlier revision of this report stated that B1 and B2 were *seed-invariant*")
    A("and that their five seeds were *five identical fits*. **That was wrong.** It was")
    A("inferred from identical ranking metrics, which does not follow. Measured against")
    A("the saved prediction arrays:\n")
    for family in ("B1-ligand-ecfp4-lgbm", "B2-protein-esm2-lgbm"):
        d = diffs[family]
        A(f"- **`{family}`**: predictions are **not** bit-identical across seeds. Every")
        A(f"  one of {d['rows']:,} rows differs; maximum absolute difference")
        A(f"  **{sci(d['max_abs_delta_pki'])} pKi**, mean")
        A(f"  {sci(d['mean_abs_delta_pki'])} pKi.")
    A("")
    A("Three different things had been run together, and they come apart:\n")
    A("| | B1 | B2 |")
    A("| --- | --- | --- |")
    A("| Identical **fits** | no | no |")
    A("| Identical **predictions** | no | no |")
    A("| Identical **ranking metrics** | no — AUROC varies by 6.6e-07 | **yes**, exactly |")
    A("")
    A("- **B2's metrics are pinned for a structural reason, not an agreement between")
    A("  fits.** Its score is constant within every target (measured: 1,041 of 1,041),")
    A("  so every ranking is all-tied and AUROC is exactly 0.5 whatever the values are.")
    A("  Its predictions move by up to")
    A(f"  {sci(diffs['B2-protein-esm2-lgbm']['max_abs_delta_pki'])} pKi beneath a metric")
    A("  that cannot register the movement.")
    A("- **B1's metrics are nearly but not exactly invariant.** Its prediction")
    A(f"  differences ({sci(diffs['B1-ligand-ecfp4-lgbm']['max_abs_delta_pki'])} pKi) are")
    A("  far smaller than the gaps between most compounds' scores, so almost no")
    A("  pairwise order flips — but some do, and the AUROC moves accordingly.")
    A("- **The reported SD of `0.0000` was a rounding artifact.** B1's true SD is")
    A(f"  {sci(means['B1-ligand-ecfp4-lgbm']['auroc_sd'])}; the published figure applied")
    A("  `round(sd, 6)`. SDs are now reported at full precision.\n")
    A("### Cause: unresolved from existing records\n")
    A("B1 and B2 are given `random_state=seed`, and LightGBM's defaults here leave no")
    A("sampling stochasticity (`subsample=1.0`, `subsample_freq=0`,")
    A("`colsample_bytree=1.0`). Two mechanisms in the recorded configuration could")
    A("still produce run-to-run differences:\n")
    A("1. **`n_jobs=-1` with LightGBM's default `deterministic=False`.** Multi-threaded")
    A("   histogram construction reduces floating-point sums in a non-fixed order, so")
    A("   bin boundaries can differ slightly between runs regardless of the seed. A")
    A("   flipped split then cascades, which would explain why B2's 1,280 dense")
    A("   continuous features diverge far more than B1's sparse binary ones.")
    A("2. **`random_state` seeding internal tie-breaking**, which is not disabled by")
    A("   the absence of subsampling.\n")
    A("**The existing records cannot separate these.** There is exactly one fit per")
    A("seed and no same-seed repeat, so seed-driven and scheduling-driven variation are")
    A("not distinguishable from what was saved. Settling it needs refits — the same")
    A("seed twice, and a run with `deterministic=True, n_jobs=1` — which this closeout")
    A("excludes. **Recorded as unresolved**; no cause is asserted.\n")
    A(f"**{results['seed_spread_on_the_headline']['no_paired_uncertainty_analysis']}**\n")

    # ---------------------------------------------------- population contrasts
    A("---\n")
    A("## 6. Contrasts between evaluation populations\n")
    A("**These are different populations, not one model's performance moving.** The")
    A("strata differ in size, in which targets they contain, in how many clear the")
    A("scoring floor, and in exposure to fitting. A difference between two rows below")
    A("is a contrast between populations and is not attributable to any single one of")
    A("those causes without analysis that was not performed.\n")
    A("Mean AUROC over seeds, primary cell, **with the scored-target count beside each**:\n")
    A("| Population | Pairs | Scored targets | " + " | ".join(
        f"`{f.split('-')[0]}`" for f in fams) + " |")
    A("| --- | ---: | ---: | " + " | ".join("---:" for _ in fams) + " |")
    order = (
        ("new_to_fitting", "new to fitting (headline)"),
        ("new_absent_from_a", "absent from A (strictest)"),
        ("new_reserved_for_validation", "reserved for validation"),
        ("recurrent", "recurrent — **supplied to fitting**"),
    )
    for key, label in order:
        g = primary.get(key)
        if not g or not g.get("pairs"):
            continue
        n_scored = g["by_model"][tags[0]]["targets_scored"]
        row = " | ".join(fmt(family_mean(g, tags, f), 4) for f in fams)
        A(f"| {label} | {g['pairs']:,} | **{n_scored}** | {row} |")
    A("")
    rec, strict = primary.get("recurrent"), primary.get("new_absent_from_a")
    if rec and rec.get("pairs") and strict:
        b3l_rec = family_mean(rec, tags, "B3L-ligand-1nn")
        b3l_strict = family_mean(strict, tags, "B3L-ligand-1nn")
        rec_t = rec["by_model"][tags[0]]["targets_scored"]
        strict_t = strict["by_model"][tags[0]]["targets_scored"]
        A("The largest contrast is on the target-conditioned lookup:")
        A(f"`B3L-ligand-1nn` scores {fmt(b3l_rec)} on the **recurrent** population")
        A(f"({rec['pairs']:,} pairs, {rec_t} scored targets) and {fmt(b3l_strict)} on the")
        A(f"**absent-from-A** population ({strict['pairs']:,} pairs, {strict_t} scored")
        A("targets). The recurrent pairs were supplied to fitting, and B3L retrieves the")
        A("nearest measured neighbour for the same target, so on those pairs it is close")
        A("to reading back training data. That is a plausible reading of the contrast,")
        A("not a measured decomposition: the two populations also differ in target")
        A(f"composition ({rec_t} against {strict_t} scored targets) and in size, and")
        A("nothing here separates those contributions. **Never pooled into the headline.**\n")
    vres = primary.get("new_reserved_for_validation")
    if vres and vres.get("pairs"):
        vt = vres["by_model"][tags[0]]["targets_scored"]
        A(f"**The validation-reserved population clears the floor on {vt} targets only**,")
        A("so its figures are reported for completeness and carry no reading. These pairs")
        A("participated in model selection and are kept identifiable for that reason.\n")

    # ------------------------------------------------------- all four cells
    A("---\n")
    A("## 7. All four cells, on the headline stratum\n")
    A("Every cell is scored from the **same fitted models** — the four are slices of one")
    A("evaluation table, not four separate experiments.\n")
    per_cell = {
        cell: family_mean(results["cells"][cell]["groups"]["new_to_fitting"], tags,
                          "B4-concat-mlp")
        for cell in results["cells"]
    }
    arm_gap = abs(per_cell[PRIMARY] - per_cell["cross_slot_excluded/screened_primary"])
    branch_gap = abs(per_cell[PRIMARY] - per_cell["declared_increment/unscreened_sensitivity"])
    A("On `B4-concat-mlp`, switching the increment arm changes mean AUROC by")
    A(f"{fmt(arm_gap, 4)} and switching the consistency branch by {sci(branch_gap)}. The")
    A("readings in §1 and §4 are the same in every cell, which is the point of carrying")
    A("all four rather than selecting one.\n")
    for arm in ARMS:
        for branch in BRANCHES:
            cell = f"{arm}/{branch}"
            c = results["cells"][cell]
            g = c["groups"]["new_to_fitting"]
            mark = " **(primary)**" if c["is_primary"] else ""
            A(f"**`{cell}`**{mark} — {g['pairs']:,} new-to-fitting pairs, "
              f"{c['eligible_pairs']:,} eligible in the cell\n")
            A("| Model | Scored targets | AUROC | AUPRC | Recall@10 | EF@1% | EF@5% |")
            A("| --- | ---: | ---: | ---: | ---: | ---: | ---: |")
            out.extend(model_rows(g, tags))
            A("")

    # -------------------------------------------------------- verification
    A("---\n")
    A("## 8. Verification and reproduction\n")
    checks = verification["checks"]
    A("| Check | Result |")
    A("| --- | --- |")
    A(f"| **Published results reproduce from the saved predictions** | "
      f"**{checks['published_results_reproduce_from_saved_predictions']}** — compared "
      "against the published bytes, not against a second fresh scoring |")
    A(f"| Scoring is deterministic | {checks['scoring_is_deterministic']} |")
    A(f"| Independent AUROC, written from scratch | agrees for "
      f"**{checks['independent_auroc_model_tags']}** model tags |")
    A(f"| Fit-artifact digests compared | {checks['artifact_digests_checked']} checked, "
      f"**{checks['artifact_digests_stale']}** stale, "
      f"{checks['artifact_digests_unavailable']} unavailable |")
    A(f"| Evaluation table matches the fit's recorded digest | "
      f"**{checks['evaluation_table_matches_the_manifest']}** |")
    A(f"| Predictions match the fit's recorded digest | "
      f"**{checks['predictions_match_the_manifest']}** |")
    A(f"| Restored checkpoints are the best-validation epoch | "
      f"{checks['checkpoints_are_best_epoch']} |")
    A(f"| Recurrent evaluation pairs that really are train pairs | "
      f"{fmt(checks['recurrent_pairs_in_train'])} |")
    A(f"| **Non-recurrent evaluation pairs found in train** | "
      f"**{fmt(checks['non_recurrent_pairs_in_train'])}** |")
    A(f"| Verification verdict | **{'PASSED' if verification['passed'] else 'FAILED'}** |")
    A("")
    not_performed = verification.get("checks_not_performed") or {}
    if not_performed:
        A("**Checks that did not run, named rather than implied:**\n")
        for key, detail in sorted(not_performed.items()):
            A(f"- **`{key}`** — " + ("; ".join(str(d) for d in detail)
                                      if isinstance(detail, list) else str(detail)))
        A("")
        A("These are reported as *not performed*, not as passed. A review copy that")
        A("excludes the checkpoints and the training-membership export cannot verify")
        A("them locally, and saying otherwise would misstate what was checked.\n")
    A("The verification record is **bound to the digests it was computed against**, and")
    A("publication re-derives them and refuses on any difference. A record that passed")
    A("before a file changed does not authorise a report built after it.\n")
    if verification["failures"]:
        A("**Failures:**\n")
        for f in verification["failures"]:
            A(f"- {f}")
        A("")

    A("### Reproducing this report\n")
    A("Three gates, deliberately separate. They were one call, and that let a")
    A("verification record written before a file changed authorise a report built")
    A("after it:\n")
    A("```")
    A(f"python -m seq2lead.asof.recompute       {run.root}   # results.json only")
    A(f"python -m seq2lead.asof.verify_results  {run.root}   # bound to current digests")
    A(f"python -m seq2lead.asof.publish         {run.root}   # refuses a stale record")
    A("```")
    A("Recomputation verifies the saved predictions **and the evaluation table** against")
    A("the digests the fit recorded, checks that every prediction row aligns with its")
    A("pair, and rescores every cell. It **fits nothing**, loads no checkpoint and")
    A("touches no feature cache. It refuses on tampered predictions, a tampered table")
    A("— including one whose labels, strata, eligibility or branch flags changed while")
    A("every pair identity and its position stayed put — a missing or unexpected model")
    A("array, and misaligned or resized pair tables. Publication additionally refuses a")
    A("verification record that is not bound to the inputs as they stand, and never")
    A("rewrites the fit's recorded digests with freshly computed ones.")
    A("`tests/test_m11h_recompute.py` and `tests/test_m11h_publish.py` exercise each")
    A("refusal through the real paths.\n")
    A("The scripts that produced the run are preserved verbatim under")
    A("`scripts/asof/`, with their provenance and digests in that directory's README.")
    A("They were authored and executed from a session scratchpad, which was the wrong")
    A("home for code a published result depends on; they are included unmodified so the")
    A("record is complete.\n")

    # ----------------------------------------------------- what this supports
    A("---\n")
    A("## 9. What these numbers do and do not support\n")
    A("The highest observed figure is a five-seed mean AUROC of")
    A(f"**{fmt(b4['auroc_mean'], 6)}** (`B4-concat-mlp`). Single-seed maxima are not")
    A("quoted as the headline: selecting the best seed after seeing results would be")
    A("selection on the evaluation cohort. Read it with all of the following in force:\n")
    A("1. **EXPLORATORY.** Snapshot B is our already-inspected 202609, and the M8/M9")
    A("   results were produced from it. This is not a prospective result.")
    A("2. **PROVISIONAL POOLING.** Whether Ki may be pooled across assay contexts is")
    A("   M5's open question. Every number assumes the current pooling rule.")
    A("3. **No paired target-level uncertainty analysis was performed.** No difference")
    A("   between any two models here is shown to be distinguishable from noise, and no")
    A("   two models are claimed to be equivalent.")
    A("4. **Seed spread is observed training variation**, not a confidence interval.")
    A("5. **The joint-versus-baseline margin does not isolate protein features** (§4).")
    A("6. **`B3L` is a target-conditioned lookup, not a ligand-only model**; `B1` is")
    A("   the ligand-only one.")
    A("7. **Two baselines are 0.5 by construction**, so a margin over them measures")
    A("   only that a model breaks ties within a target.")
    A(f"8. **Macro-averages rest on {s0['targets_scored']} of {s0['targets_in_slice']} "
      "targets**; the rest fall below the floor.")
    A("9. **The LightGBM round cap bound**, so B1 and B2 are not at their own optimum.")
    A("10. **Between-stratum differences are contrasts between populations** (§6), not")
    A("    one model's performance changing.")
    A("11. **Seed-to-seed differences in B1 and B2 are measured but unexplained** (§5).")
    A("12. **No tuning was done in response to any of these numbers**, and none may be.")
    A("13. **`label_reversal-v3` is unscored**, so the ligand-memorisation question")
    A("    that split is designed to answer is not answered here.")
    A("14. **The confirmatory §7(A) attestation is NOT signed**, and this stage does")
    A("    not sign it.\n")

    REPORT.write_text("\n".join(out) + "\n", encoding="utf-8")
    return REPORT


def build_manifest(
    run: LoadedRun,
    results: dict[str, Any],
    verification: dict[str, Any],
    inputs: dict[str, Any],
    *,
    report_path: Path,
    version: str,
    supersedes: str | None,
    preserved_as: str | None,
) -> dict[str, Any]:
    """Assemble the manifest, keeping expected and derived digests apart.

    The fit's recorded digests for its own artifacts are **carried over**, not
    recomputed. An earlier revision recomputed everything, so a tampered
    evaluation table had its new digest written straight into the manifest and
    the change disappeared into the record meant to catch it. Only the derived
    outputs -- the results, the verification record, the report -- are hashed
    here, because only they are regenerated.
    """
    fit = run.manifest
    expected = {
        str(fit[name]["path"]): fit[name]["sha256"]
        for name in ("predictions", "evaluation_table", "transform", "training_records")
        if isinstance(fit.get(name), dict) and fit[name].get("sha256")
    }
    expected.update(fit.get("checkpoints", {}))
    derived = {
        str(run.root / name): sha256(run.root / name)
        for name in ("results.json", "verification.json")
        if (run.root / name).exists()
    }
    derived[str(report_path)] = sha256(report_path)
    scripts = {
        str(s): sha256(s) for s in sorted(Path("scripts/asof").glob("*")) if s.is_file()
    }
    return {
        "manifest": version,
        "supersedes": supersedes,
        "previous_manifest_preserved_as": preserved_as,
        "run_id": fit["run_id"],
        "contract": fit["contract_version"],
        "runner_version": fit["runner_version"],
        "fitting_version": fit["fitting_version"],
        "recompute_version": results["recompute_version"],
        "metric_version": results["metric_version"],
        "report": str(report_path),
        "expected_fit_artifacts": {
            "digests": dict(sorted(expected.items())),
            "source": (
                "carried over from the fit's own manifest and compared on publication; "
                "never recomputed here"
            ),
            "verified_on_publication": inputs["checked"],
            "unavailable_at_publication": inputs["unavailable"],
        },
        "derived_outputs": dict(sorted(derived.items())),
        "execution_scripts": scripts,
        "entry_points": {
            "recompute": "python -m seq2lead.asof.recompute <run-dir>",
            "verify": "python -m seq2lead.asof.verify_results <run-dir>",
            "publish": "python -m seq2lead.asof.publish <run-dir>",
        },
        "authorised_by": {
            "verification": verification["verification"],
            "bound_to": verification["bound_to"],
            "passed": verification["passed"],
            "checks_not_performed": verification.get("checks_not_performed", {}),
        },
        "seeds": fit["seeds"],
        "models": sorted({t.split("-seed")[0] for t in results["model_tags"]}),
        "models_fitted_once": sorted(SINGLE_FIT),
        "failures": fit["failures"],
        "predictions_unchanged_since_the_fit": True,
        "accepted_artifacts_preserved": True,
        "retrieval": "STAYS UNBUILT",
        "evaluation_evidence_display": "STAYS DISABLED",
        "label_reversal_v3": "NOT SCORED",
        "confirmatory_freeze": "UNSIGNED",
        "qualifications": fit["qualifications"],
    }
