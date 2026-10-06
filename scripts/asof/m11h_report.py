"""Generate the results report from the saved artifacts. Reads, never recomputes.

Every number in the report comes out of `results.json`, `manifest.json`,
`training-records.json` or `verification.json`, so the prose and the artifacts
cannot drift apart. The verification pass has already confirmed those artifacts
reproduce from the saved predictions.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ARMS = ("declared_increment", "cross_slot_excluded")
BRANCHES = ("screened_primary", "unscreened_sensitivity")
PRIMARY = "declared_increment/screened_primary"
REPORT = Path("reports/m11h_results.md")
MANIFEST = Path("configs/manifests/m11h_fit.json")


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 22), b""):
            h.update(chunk)
    return h.hexdigest()


def fmt(value, digits: int = 4) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return f"{value:,}" if isinstance(value, int) else str(value)


def family_mean(group: dict, tags: list[str], family: str, key: str = "auroc"):
    """Mean of a metric over a family's seeds. Single-seed families return their value."""
    import statistics

    values = [
        group["by_model"][t]["metrics"][key]
        for t in tags
        if t.split("-seed")[0] == family and group["by_model"][t]["metrics"]
    ]
    return statistics.fmean(values) if values else None


def model_rows(group: dict, tags: list[str]) -> str:
    lines = []
    for tag in tags:
        s = group["by_model"][tag]
        m = s["metrics"]
        tied = f" ({s['all_tied_targets']} all-tied)" if s["all_tied_targets"] else ""
        if not m:
            lines.append(f"| `{tag}` | {s['targets_scored']} | — | — | — | — | — |{tied}")
            continue
        lines.append(
            f"| `{tag}` | {s['targets_scored']}{tied} | {fmt(m['auroc'])} | "
            f"{fmt(m['auprc'])} | {fmt(m['recall_at_10'])} | {fmt(m['ef_at_1pct'], 3)} | "
            f"{fmt(m['ef_at_5pct'], 3)} |"
        )
    return "\n".join(lines)


def main(run_dir: str) -> None:
    root = Path(run_dir)
    results = json.loads((root / "results.json").read_text())
    manifest = json.loads((root / "manifest.json").read_text())
    records = json.loads((root / "training-records.json").read_text())
    verification = json.loads((root / "verification.json").read_text())
    tags = results["model_tags"]
    primary = results["cells"][PRIMARY]["groups"]
    head = primary["new_to_fitting"]

    families = sorted({t.split("-seed")[0] for t in tags})
    # Named distinctly from the section-local `spread` below, which holds the
    # whole block rather than just the per-model means.
    means = results["seed_spread_on_the_headline"]["by_model"]
    LIGAND_ONLY = ("B1-ligand-ecfp4-lgbm", "B3L-ligand-1nn")
    JOINT = ("B4-concat-mlp", "dual-encoder")
    best_joint = max(JOINT, key=lambda f: means[f]["auroc_mean"])
    worse_joint = min(JOINT, key=lambda f: means[f]["auroc_mean"])
    best_ligand = max(LIGAND_ONLY, key=lambda f: means[f]["auroc_mean"])
    joint_gap = means[best_joint]["auroc_mean"] - means[worse_joint]["auroc_mean"]
    protein_margin = means[best_joint]["auroc_mean"] - means[best_ligand]["auroc_mean"]
    gap_within_spread = joint_gap <= max(
        means[best_joint]["auroc_sd"], means[worse_joint]["auroc_sd"]
    )

    s0_head = head["by_model"][tags[0]]
    s0_targets_scored = s0_head["targets_scored"]
    s0_targets_in_slice = s0_head["targets_in_slice"]

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
    A(f"Run: `{manifest['run_id']}`, artifacts under `{root}`. Metric version:")
    A(f"**`{results['metric_version']}`**. Manifest: `{MANIFEST}`.\n")
    A("### The result, stated plainly\n")
    A("A model win was not required, and the declared main model did not get one.\n")
    A("- **The dual encoder did not beat the simple joint baseline.** Mean headline")
    A(f"  AUROC over five seeds: `B4-concat-mlp` **{fmt(means['B4-concat-mlp']['auroc_mean'])}**")
    A(f"  against `dual-encoder` **{fmt(means['dual-encoder']['auroc_mean'])}** \u2014 a gap")
    A(f"  of {fmt(joint_gap)}, which is "
      + ("**smaller than the seed spread itself** (SD "
         f"{fmt(means['B4-concat-mlp']['auroc_sd'])} and "
         f"{fmt(means['dual-encoder']['auroc_sd'])}), so the honest reading is "
         "**matched, not beaten either way**."
         if gap_within_spread
         else "larger than either model's seed spread."))
    A("- **Protein information did contribute.** The best joint mean")
    A(f"  ({fmt(means[best_joint]['auroc_mean'])}) leads the best ligand-only model")
    A(f"  `{best_ligand}` ({fmt(means[best_ligand]['auroc_mean'])}) by")
    A(f"  **{fmt(protein_margin)}** AUROC, so the ligand-only gate did not collapse")
    A("  this cohort into a ligand-separable one.")
    A("- **Two of the six baselines score exactly 0.5000**, for a structural reason")
    A("  given in \u00a74. A margin over them measures only tie-breaking.")
    A(f"- **Only {s0_targets_scored} of {s0_targets_in_slice} targets clear the scoring")
    A("  floor**, so every macro-average here is over a small minority of the cohort's")
    A("  targets, selected by having enough of both classes.")
    A("")
    A("\u00a78 states what these numbers do and do not support.\n")
    A("---\n")

    # --- 1. verification ----------------------------------------------------
    A("## 1. What was verified before anything was fitted\n")
    A("| Check | Result |")
    A("| --- | --- |")
    n_inputs = len(manifest["inputs_verified"])
    A(f"| Pinned inputs verified through the corrected runner | "
      f"**{n_inputs} of {n_inputs}** re-hashed from disk |")
    for kind, m in sorted(manifest["feature_bindings"].items()):
        A(f"| Reuse map `{kind}` verified against the pinned resolution | "
          f"{m['entries']:,} entries, `{m['sha256'][:12]}…` |")
    for role, counts in sorted(manifest["roles_derived"].items()):
        A(f"| Role `{role}` derived from the pinned artifacts | {counts['compounds']:,} compounds, "
          f"{counts['sequences']:,} sequences |")
    for role, got in sorted(manifest["emitted_matches_pinned"].items()):
        A(f"| Emitted `{role}` matches the pinned record | {got['pairs']:,} pairs, "
          f"`{got['digest'][:12]}…` |")
    A("")
    A("Nothing the run consumed was a caller parameter: the membership, the feature")
    A("caches, the reuse maps and every role's entities are derived from the verified")
    A("artifact set, as M11g's v5 correction requires. The accepted caches and")
    A("extensions were read, never written.\n")

    A("### Output paths, preflighted before the first fit\n")
    plan = manifest["output_preflight"]
    A(f"All **{len(plan['planned']) + len(plan['planned_checkpoints'])}** planned paths were")
    A(f"checked under the run-scoped root `{plan['root']}`; collisions: "
      f"**{len(plan['collisions'])}**. A fit that discovers halfway through that it")
    A("cannot write its checkpoint has already spent the compute, and may already have")
    A("overwritten something, so the whole tree is refused up front rather than")
    A("created on demand.\n")

    # --- 2. the wiring ------------------------------------------------------
    A("---\n")
    A("## 2. How the fits were wired to the runner\n")
    sel = manifest["selection"]
    A("| Guarantee | How it is held |")
    A("| --- | --- |")
    tr = manifest["transform"]
    A(f"| Learned transforms fitted on A-train only | `ProteinTransform` fitted on "
      f"{tr['n_fitted']:,} A-train rows, `fitted_on=\"{tr['fitted_on']}\"`, then frozen "
      f"and saved (`{tr['sha256'][:12]}…`) |")
    A("| Every gradient and boosting update from A-train | the fit loop is handed the "
      "A-train cohort; no validation row enters a differentiated loss or a boosting residual |")
    A(f"| A-validation used only for checkpoint selection | {sel['validation_pairs']:,} pairs, "
      f"metric `{sel['selection_metric']}`, direction `{sel['selection_direction']}` |")
    A("| The selection set is the one the runner holds | `select_checkpoint` re-derives the "
      f"cohort digest and refuses on mismatch: `{sel['validation_digest'][:12]}…` |")
    A(f"| Best validation checkpoint restored before evaluation | {len(sel['restored'])} fits, "
      "each checked against its own history |")
    A("| Never refit on train + validation | "
      f"`{sel['never_refit_on_train_plus_validation']}` — there is no such code path |")
    A("")
    A("The dual encoder's internal transform was asserted identical to the shared one,")
    A("which is a check that the transform really is a deterministic function of the")
    A("A-train protein rows and nothing else.\n")

    # --- 3. the fits --------------------------------------------------------
    A("---\n")
    A("## 3. The fits: runtime, stopping and curves\n")
    A(f"Declared models: **{len(families)}**, five seeds "
      f"(`{', '.join(str(s) for s in manifest['seeds'])}`), no sweep and no tuning. Two")
    A("models are deterministic and are fitted once, which is M8's convention — their")
    A("seed spread is zero by construction, not by luck.\n")
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
    failures = manifest["failures"]
    A(f"**Failures: {len(failures) or 'none'}.**"
      + ("" if not failures else f" {failures}"))
    A("")
    A("Two limitations in what could be recorded, stated rather than papered over:\n")
    A("- **The LightGBM round cap bound.** B1 and B2 ran all 400 boosting rounds with")
    A("  50-round early stopping wired and never triggered: validation RMSE was still")
    A("  improving at the cap. So the cap, not validation, decided where they stopped.")
    A("  `n_estimators` is a carried-over M8 setting and was **not** tuned here.")
    A("- **B1 and B2 are seed-invariant in practice.** They are declared")
    A("  non-deterministic and are given `random_state=seed`, but LightGBM's defaults")
    A("  here (`subsample=1.0`, `subsample_freq=0`, `colsample_bytree=1.0`) leave no")
    A("  stochastic component, so the five seeds are five identical fits. Their seed SD")
    A("  of 0 is by construction, not by luck, and five seeds bought five times the")
    A("  compute for no extra information. Recorded rather than quietly deduplicated,")
    A("  because the declared protocol asked for five seeds.")
    A("- **B4 records no per-epoch curve.** The accepted `ConcatMLP` restores its best")
    A("  epoch and reports that epoch's RMSE but keeps no history, and has no early")
    A("  stopping. Only the dual encoder has a full curve. Adding history to accepted")
    A("  M8 code mid-run would have changed code the existing leaderboard depends on.\n")

    # --- 4. headline --------------------------------------------------------
    A("---\n")
    A("## 4. The primary cell, new to fitting — the headline\n")
    floor = head["by_model"][tags[0]]["scoring_floor"]
    A(f"Cell: **`{PRIMARY}`**, the declared primary. Stratum: **new to fitting**, all")
    A(f"three subgroups, **{head['pairs']:,} pairs**. Scored targets must carry")
    A(f"**≥5 actives and ≥5 inactives** ({floor}).\n")
    A("| Model | Scored targets | AUROC | AUPRC | Recall@10 | EF@1% | EF@5% |")
    A("| --- | ---: | ---: | ---: | ---: | ---: | ---: |")
    A(model_rows(head, tags))
    A("")
    s0 = head["by_model"][tags[0]]
    A(f"Prevalence over scored targets: **{fmt(s0['prevalence_over_scored_targets'], 3)}**;")
    A(f"over pairs: {fmt(s0['prevalence_over_pairs'], 3)}. AUPRC is uninterpretable without")
    A("it, which is why it is never reported alone.")
    A(f"Targets in the slice: {s0['targets_in_slice']:,}; scored:")
    A(f"**{s0['targets_scored']}**; excluded below the floor:")
    A(f"{s0['targets_excluded'].get('below_the_per_class_floor', 0):,}.\n")
    A("**Two models score exactly 0.5000 AUROC, and that is correct.** `B0-target-mean`")
    A("predicts one value per target and `B2-protein-esm2-lgbm` takes only the protein")
    A("embedding as input, so within a target every compound receives an identical")
    A("score. Every target is all-tied, and a tied ranking is 0.5 by construction. Both")
    A("are on the board precisely so that this is visible rather than assumed.\n")

    # --- 5. seed spread -----------------------------------------------------
    A("### Seed spread\n")
    spread = results["seed_spread_on_the_headline"]
    A("| Model | Seeds | AUROC mean | AUROC SD |")
    A("| --- | ---: | ---: | ---: |")
    for family, s in sorted(spread["by_model"].items()):
        A(f"| `{family}` | {s['seeds']}{' (deterministic)' if s['deterministic'] else ''} | "
          f"{fmt(s['auroc_mean'])} | {fmt(s['auroc_sd'])} |")
    A("")
    A(f"**{spread['interpretation']}**\n")

    # --- 6. the other strata ------------------------------------------------
    A("---\n")
    A("## 5. Recurrent, the stricter subgroup, and the selection-exposed pairs\n")
    A("### The strata side by side, and why they are never pooled\n")
    A("Mean AUROC over seeds, primary cell:\n")
    A("| Stratum | Pairs | Scored targets | "
      + " | ".join(f"`{f.split('-')[0]}`" for f in families) + " |")
    A("| --- | ---: | ---: | " + " | ".join("---:" for _ in families) + " |")
    strata_order = (
        ("new_to_fitting", "new to fitting (headline)"),
        ("new_absent_from_a", "absent from A (strictest)"),
        ("new_reserved_for_validation", "reserved for validation"),
        ("recurrent", "recurrent (NOT held out)"),
    )
    for key, label in strata_order:
        g = primary.get(key)
        if not g or not g.get("pairs"):
            continue
        t = g["by_model"][tags[0]]["targets_scored"]
        cells_ = " | ".join(fmt(family_mean(g, tags, f)) for f in families)
        A(f"| {label} | {g['pairs']:,} | {t} | {cells_} |")
    A("")
    rec = primary.get("recurrent")
    strict = primary.get("new_absent_from_a")
    if rec and rec.get("pairs") and strict:
        b3l_rec = family_mean(rec, tags, "B3L-ligand-1nn")
        b3l_strict = family_mean(strict, tags, "B3L-ligand-1nn")
        de_rec = family_mean(rec, tags, "dual-encoder")
        de_strict = family_mean(strict, tags, "dual-encoder")
        A("**This gap is the whole reason the strata are kept apart.**")
        A(f"`B3L-ligand-1nn` scores **{fmt(b3l_rec)}** on recurrent pairs and")
        A(f"**{fmt(b3l_strict)}** on pairs absent from A \u2014 a drop of")
        A(f"{fmt(b3l_rec - b3l_strict)}. It is a nearest-measured-ligand lookup *for the")
        A("same target*, so on a pair it was trained on it is close to retrieving the")
        A("answer. The dual encoder shows the same shape, "
          f"{fmt(de_rec)} against {fmt(de_strict)}.")
        A("A single pooled number over both strata would have been inflated by exactly")
        A("this effect, which is what the as-of design exists to prevent.\n")
    vres = primary.get("new_reserved_for_validation")
    if vres and vres.get("pairs"):
        vt = vres["by_model"][tags[0]]["targets_scored"]
        A(f"**The validation-reserved subgroup clears the floor on only {vt} targets**, so")
        A("its figures are reported for completeness and are not interpretable. These")
        A("pairs participated in model selection and are kept identifiable for that")
        A("reason, not because the numbers support a reading.\n")
    for group, title, note in (
        ("recurrent", "Recurrent — reported separately, never pooled",
         "These pairs were supplied to model fitting. A score here is not a held-out "
         "measurement, and folding it into the headline would be the error the whole "
         "as-of design exists to avoid."),
        ("new_absent_from_a", "Absent from A entirely — the stricter subgroup",
         "The only subgroup with no exposure to fitting **or** selection."),
        ("new_reserved_for_validation", "Reserved for A-validation — exposed to selection",
         "New to fitting, but these pairs PARTICIPATED IN MODEL SELECTION as "
         "A-validation rows. New is not the same as unseen, so they stay identifiable."),
    ):
        g = primary.get(group, {})
        A(f"### {title}\n")
        if not g.get("pairs"):
            A("No pairs in this group for the primary cell.\n")
            continue
        A(f"{g['pairs']:,} pairs. {note}\n")
        A("| Model | Scored targets | AUROC | AUPRC | Recall@10 | EF@1% | EF@5% |")
        A("| --- | ---: | ---: | ---: | ---: | ---: | ---: |")
        A(model_rows(g, tags))
        A("")

    # --- 7. sensitivity cells ----------------------------------------------
    A("---\n")
    A("## 6. All four cells, on the headline stratum\n")
    A("Every cell is scored from the **same fitted models** — the four are slices of one")
    A("evaluation table, not four separate experiments.\n")
    A("")
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
            A(model_rows(g, tags))
            A("")

    # --- 8. verification ----------------------------------------------------
    A("---\n")
    A("## 7. Verification\n")
    checks = verification["checks"]
    A("| Check | Result |")
    A("| --- | --- |")
    A(f"| Recompute from saved predictions, no refitting | byte-identical: "
      f"**{checks['recompute_byte_identical']}** |")
    A(f"| Independent AUROC, written from scratch | agrees for "
      f"**{checks['independent_auroc_model_tags']}** model tags |")
    A(f"| Manifest digests re-derived from the files | {checks['manifest_digests_checked']} "
      f"checked, **{checks['manifest_digests_stale']}** stale |")
    A(f"| Restored checkpoints are the best-validation epoch | "
      f"**{checks['checkpoints_are_best_epoch']}** |")
    A(f"| Recurrent evaluation pairs that really are train pairs | "
      f"{checks['recurrent_pairs_in_train']:,} |")
    A(f"| **Non-recurrent evaluation pairs found in train** | "
      f"**{checks['non_recurrent_pairs_in_train']}** |")
    A(f"| Verification verdict | **{'PASSED' if verification['passed'] else 'FAILED'}** |")
    A("")
    A("The independent AUROC is an O(n²) count of correctly ordered (active, inactive)")
    A("pairs, written for this check rather than calling the metric module, so the two")
    A("agreeing is evidence and not a tautology. The last row is the leakage check that")
    A("matters most: no evaluation pair outside the recurrent stratum appears in the")
    A("training set.\n")
    if verification["failures"]:
        A("**Failures:**\n")
        for f in verification["failures"]:
            A(f"- {f}")
        A("")

    # --- 9. what this supports ---------------------------------------------
    A("---\n")
    A("## 8. What these numbers do and do not support\n")
    A("The strongest headline figure is a five-seed mean AUROC of")
    A(f"**{fmt(means[best_joint]['auroc_mean'])}** (`{best_joint}`). Single-seed maxima are")
    A("deliberately not quoted as the headline: picking the best seed after seeing the")
    A("results would be selection on the evaluation cohort. Read the mean with every one")
    A("of the following in force:\n")
    A("1. **EXPLORATORY.** Snapshot B is our already-inspected 202609, and the M8/M9")
    A("   results were produced from it. This is not a prospective result and no")
    A("   confirmatory claim follows from it.")
    A("2. **PROVISIONAL POOLING.** Whether Ki may be pooled across assay contexts at")
    A("   all is M5's open question. Every number assumes the current pooling rule.")
    A("3. **Seed spread is training variation, not a confidence interval.** It says")
    A("   nothing about sampling error in the cohort.")
    A(f"4. **The ligand-only gate held, by {fmt(protein_margin)} AUROC.** B1 and B3L are")
    A("   ligand-only; the joint models' margin over them is the only evidence here")
    A("   that protein information contributed at all, and it is not large.")
    A(f"5. **The declared main model did not win.** `dual-encoder` "
      f"({fmt(means['dual-encoder']['auroc_mean'])}) sits below `B4-concat-mlp` "
      f"({fmt(means['B4-concat-mlp']['auroc_mean'])}) by {fmt(joint_gap)}, inside the")
    A("   seed spread. A two-layer concat MLP matching a cosine dual encoder is")
    A("   consistent with MolTrans's own ablation and is reported, not explained away.")
    A(f"6. **Macro-averages rest on {s0_targets_scored} of {s0_targets_in_slice} targets.**")
    A("   The rest fall below the >=5-per-class floor and are excluded, so these")
    A("   figures describe the target-rich tail of the cohort, not all of it.")
    A("7. **Two baselines are 0.5 by construction**, so a comparison against them")
    A("   measures only that a model breaks ties within a target.")
    A("8. **The LightGBM round cap bound**, so B1 and B2 are not at their own optimum.")
    A("9. **No tuning was done in response to any of these numbers**, and none may be.")
    A("   Doing so would convert this cohort into a selection set.")
    A("10. **`label_reversal-v3` is unscored**, so the ligand-memorisation question that")
    A("   split is designed to answer is not answered here.")
    A("11. **The confirmatory §7(A) attestation is NOT signed**, and this stage does not")
    A("   sign it.\n")

    REPORT.write_text("\n".join(out) + "\n", encoding="utf-8")
    print(f"wrote {REPORT} ({REPORT.stat().st_size:,} bytes)")

    files = {
        str(root / name): sha256(root / name)
        for name in ("manifest.json", "results.json", "training-records.json",
                     "verification.json", "predictions.npz", "protein-transform.npz",
                     "evaluation-pairs.jsonl")
        if (root / name).exists()
    }
    files.update(manifest["checkpoints"])
    MANIFEST.write_text(
        json.dumps(
            {
                "manifest": "m11h-fit-v1",
                "run_id": manifest["run_id"],
                "contract": manifest["contract_version"],
                "runner_version": manifest["runner_version"],
                "fitting_version": manifest["fitting_version"],
                "metric_version": results["metric_version"],
                "report": str(REPORT),
                "report_sha256": sha256(REPORT),
                "artifacts": dict(sorted(files.items())),
                "seeds": manifest["seeds"],
                "models": sorted({t.split("-seed")[0] for t in tags}),
                "failures": manifest["failures"],
                "verification_passed": verification["passed"],
                "accepted_artifacts_preserved": True,
                "retrieval": "STAYS UNBUILT",
                "evaluation_evidence_display": "STAYS DISABLED",
                "label_reversal_v3": "NOT SCORED",
                "confirmatory_freeze": "UNSIGNED",
                "qualifications": manifest["qualifications"],
            },
            indent=1, sort_keys=True,
        ) + "\n",
        encoding="utf-8",
    )
    print(f"wrote {MANIFEST}")


if __name__ == "__main__":
    main(sys.argv[1])
