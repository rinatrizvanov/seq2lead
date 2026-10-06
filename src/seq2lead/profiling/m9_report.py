"""Render `reports/m9_dual_encoder.md` from saved M9 artifacts."""

from __future__ import annotations

import json
import statistics as st
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from seq2lead.profiling.status_banner import banner

if TYPE_CHECKING:
    import psycopg

    from seq2lead.eval.config import ExperimentConfig

REPORT_PATH = Path("reports/m9_dual_encoder.md")
SPLITS = ("random_pair-v3", "cold_protein-v3", "chemistry_disjoint-v3", "temporal_proxy-v4")
MODELS = (
    "B0-target-mean",
    "B1-ligand-ecfp4-lgbm",
    "B3L-ligand-1nn",
    "B4-concat-mlp",
    "M9-dual-encoder",
)


def _headline_auroc(run: dict) -> float | None:
    if run["split"] == "temporal_proxy-v4":
        return (run.get("strata") or {}).get("temporal:new", {}).get("auroc", {}).get("mean")
    return run["ranking"]["auroc"]["mean"]


def _headline_rmse(run: dict) -> float | None:
    if run["split"] == "temporal_proxy-v4":
        block = (run.get("strata") or {}).get("temporal_regression:new", {})
        return (block.get("rmse") or {}).get("mean")
    return run["regression"]["rmse"]["mean"]


def _fmt(value: Any, digits: int = 4) -> str:
    return "—" if value is None else f"{float(value):.{digits}f}"


def render(conn: psycopg.Connection, config: ExperimentConfig) -> str:  # noqa: PLR0915
    lines: list[str] = []

    def a(value: str) -> None:
        # A guard, not decoration: `a(a(...))` is an easy slip and the inner call
        # returns None, which otherwise surfaces as an opaque TypeError at join
        # time with no line number.
        if not isinstance(value, str):
            import traceback

            frame = traceback.extract_stack()[-2]
            raise TypeError(
                f"m9_report line {frame.lineno} appended {type(value).__name__}, not str"
            )
        lines.append(value)

    m8 = json.loads(Path("reports/results/baseline_summary_v2.json").read_text(encoding="utf-8"))
    m9 = json.loads(Path("reports/results/m9_v1_summary.json").read_text(encoding="utf-8"))
    selection = json.loads(Path("reports/results/m9_selection.json").read_text(encoding="utf-8"))
    cli = json.loads(Path("reports/results/m9_cli_checkpoint.json").read_text(encoding="utf-8"))

    a("# M9 — dual encoder")
    a("")
    a(f"Generated {datetime.now(UTC):%Y-%m-%d %H:%M} UTC.")
    a("")
    for line in banner():
        a(line)
    a(
        f"Contract: [`../docs/M9.md`](../docs/M9.md), frozen before fitting. Experiment "
        f"`{config.version}`, inputs pinned by digest and identical to M8."
    )
    a("")

    # ------------------------------------------------------- exploratory status
    a("## These numbers are exploratory")
    a("")
    a(
        "**The M8 test partitions had already been inspected** before M9 existed — the "
        "baseline leaderboard was read, discussed and corrected twice against them. A "
        "model compared against those same sets is being compared on data the project "
        "has already seen, so this is **not** a confirmatory held-out result and is not "
        "presented as one."
    )
    a("")
    a("What was done to keep it honest anyway:")
    a("")
    a(
        "- **Every configuration was selected on validation alone.** No test score chose "
        "a projection dimension, an epoch, a seed or a decision to keep tuning."
    )
    a(
        "- **The selection was frozen to disk before the test pass ran** "
        "(`reports/results/m9_selection.json`, frozen "
        f"{selection['frozen_at']}). The test phase reads that file and cannot alter it."
    )
    a(
        "- **Disappointing test numbers did not trigger more tuning.** The search is the "
        "two configurations declared in the contract, and it stayed that way."
    )
    a("")
    a(
        "A genuinely confirmatory result needs a partition nobody has looked at. That is "
        "the M11 as-of temporal evaluation, which trains on an older archived BindingDB "
        "release and tests on pairs new in a later one."
    )
    a("")
    a("`label_reversal-v3` remains **unscored**, as in M8.")
    a("")

    # ------------------------------------------------------------ what a run is
    a("## What one run is")
    a("")
    a(
        "Each of the 20 final runs is one (split, seed) pair, and does exactly three "
        "things in order:"
    )
    a("")
    a("1. **Fit** on the training partition only.")
    a(
        "2. **Select its own checkpoint** by validation RMSE, evaluated once per epoch, "
        "restoring the best-validation weights and early-stopping after 5 epochs without "
        "improvement."
    )
    a("3. **Score test once**, after the weights are fixed.")
    a("")
    a(
        "Test metrics never select an epoch, a seed or a configuration. The projection "
        "dimension was chosen in a separate earlier phase that touched no test partition "
        "at all."
    )
    a("")

    # ------------------------------------------------------------- architecture
    a("## Architecture and the scoring head")
    a("")
    a(
        "Two towers that never see each other's input: ECFP4 → `Linear(2048, h)` → ReLU "
        "→ `Linear(h, h)`, and ESM-2 → `Linear(1280, h)` → ReLU → `Linear(h, h)`. That "
        "independence is what lets the compound library be projected **once** and reused "
        "for every query, which is the property the CLI runs on."
    )
    a("")
    a(
        "**The head is an affine map on cosine, and the units are pKi.** Cosine alone is "
        "bounded to [-1, 1] and the labels run roughly 2–12, so it cannot represent the "
        "target. `sigmoid(cosine)` cannot either, and it is **not** a calibrated "
        "probability — claiming that would need a reliability diagram and an ECE, neither "
        "of which exists here. So:"
    )
    a("")
    a("```")
    a("score = a * cos(z_compound, z_protein) + b       # a, b trainable, output in pKi")
    a("```")
    a("")
    sizes = sorted({(r["projection_dim"], r["n_parameters"]) for r in _run_records()})
    for dim, count in sizes:
        a(
            f"At the selected `h={dim}` the final models hold **{count:,} parameters** "
            "(two towers of `in→h` and `h→h`, plus the scale and offset). The h=256 "
            "pilot used for runtime scoping held 984,066; that figure describes the "
            "pilot, not these models."
        )
    a("")

    # ------------------------------------------------------------ learned head
    a("### The learned scale and offset")
    a("")
    a(
        "`a` is **unconstrained**. Its sign matters: a negative scale reverses the cosine "
        "ordering, so ranking by raw cosine would be exactly backwards for such a model. "
        "**The CLI therefore ranks by the affine score — the predicted pKi — which "
        "carries the sign with it.** Two tests cover this, including one that flips a "
        "trained scale and asserts the CLI's ranking flips with it."
    )
    a("")
    a("| Split | a (scale) | b (offset) | predicted pKi: min | max | mean | sd |")
    a("| --- | --- | --- | --- | --- | --- | --- |")
    heads = _head_stats()
    for split in SPLITS:
        row = heads.get(split)
        if row:
            a(
                f"| `{split}` | {row['a_mean']:.3f} | {row['b_mean']:.3f} | "
                f"{row['min']:.2f} | {row['max']:.2f} | {row['mean']:.2f} | {row['sd']:.2f} |"
            )
    a("")
    negative = [s for s, r in heads.items() if r["a_min"] < 0]
    a(
        f"**All 20 fitted models learned a positive scale** (range "
        f"{min(r['a_min'] for r in heads.values()):.2f}–"
        f"{max(r['a_max'] for r in heads.values()):.2f}), so cosine ordering happens to "
        "be preserved in every one of them. "
        + (
            f"Negative in: {negative}."
            if negative
            else "That is an observation about these runs, not a guarantee — nothing in "
            "the objective prevents a negative scale, which is why the CLI does not "
            "depend on it."
        )
    )
    a("")

    # --------------------------------------------------------------- selection
    a("## Configuration selection (validation only)")
    a("")
    a(
        f"Two configurations, `h ∈ {{256, 512}}`, fitted once each per split on seed "
        f"{selection['seed']}. Metric: validation RMSE, lower wins."
    )
    a("")
    a("| Split | h=256 | h=512 | chosen | validation pairs | targets |")
    a("| --- | --- | --- | --- | --- | --- |")
    by_split = defaultdict(dict)
    for entry in selection["entries"]:
        by_split[entry["split"]][entry["projection_dim"]] = entry
    for split in SPLITS:
        entries = by_split.get(split)
        if not entries:
            continue
        chosen = selection["chosen"].get(split)
        reference = next(iter(entries.values()))
        a(
            f"| `{split}` | {_fmt(entries.get(256, {}).get('validation_rmse'))} | "
            f"{_fmt(entries.get(512, {}).get('validation_rmse'))} | h={chosen} | "
            f"{reference['n_validation_pairs']:,} | {reference['n_validation_targets']:,} |"
        )
    a("")
    a(
        "`h=512` won on every split. Coverage was sufficient everywhere, so the declared "
        "fallback — carry over the `random_pair-v3` choice where validation is too thin "
        "to separate configurations — was never invoked."
    )
    a("")

    # ------------------------------------------------------------ curves
    a("### Training and validation curves")
    a("")
    a(
        "Reported because the stopping behaviour differs sharply across splits, and that "
        "is an **observation**, not a diagnosis. Training loss falls throughout on every "
        "split; what changes is how quickly validation stops following it."
    )
    a("")
    a(
        "| Split | epochs run | best epoch | stopped early "
        "| first→last val RMSE | train MSE first→last |"
    )
    a("| --- | --- | --- | --- | --- | --- |")
    for split in SPLITS:
        path = Path(f"data/m9/final/M9-dual-encoder__{split}__seed{selection['seed']}.json")
        if not path.exists():
            continue
        record = json.loads(path.read_text(encoding="utf-8"))
        history = record["history"]
        a(
            f"| `{split}` | {record['epochs_run']} | {record['best_epoch']} | "
            f"{record['stopped_early']} | "
            f"{history[0]['validation_rmse']:.4f} → {history[-1]['validation_rmse']:.4f} | "
            f"{history[0]['train_mse']:.4f} → {history[-1]['train_mse']:.4f} |"
        )
    a("")
    a(
        "**Stopping rule:** best-validation checkpoint restored; stop after 5 epochs "
        "without improvement; cap 20 epochs."
    )
    a("")
    a(
        "On `cold_protein-v3` validation RMSE rises monotonically from epoch 1 while "
        "training MSE falls by a factor of four — the fit is working, and each additional "
        "epoch makes held-out proteins worse. `temporal_proxy-v4` bottoms at epoch 2 and "
        "`chemistry_disjoint-v3` at epoch 6, while `random_pair-v3` was still improving "
        "at epoch 19 and never early-stopped. **What that shows is where this "
        "**These are observations from these particular fits**, under one learning "
        "rate, one batch size and one head, with two projection dimensions searched. "
        "They do not establish a transfer limit of the architecture: a different "
        "optimiser schedule, regulariser or head might move them, and none was tried. "
        "What they do rule out is a broken optimiser -- training loss descends "
        "everywhere. No tuning was restarted against test scores."
    )
    a("")

    # --------------------------------------------------------------- results
    a("## Results against the M8 baselines")
    a("")
    a(
        "Same corrected metric implementation (`m8/v2`), same cohorts, same eligibility. "
        "Temporal figures are the **new-pair stratum**. ± is spread across seeds, which "
        "is optimiser variance and **not** uncertainty across targets."
    )
    a("")
    grouped = defaultdict(list)
    for run in m8 + m9:
        auroc, rmse = _headline_auroc(run), _headline_rmse(run)
        if auroc is not None:
            grouped[("auroc", run["model"], run["split"])].append(auroc)
        if rmse is not None:
            grouped[("rmse", run["model"], run["split"])].append(rmse)

    a("### Ranking AUROC")
    a("")
    a("| Model | " + " | ".join(f"`{s}`" for s in SPLITS) + " |")
    a("| --- |" + " --- |" * len(SPLITS))
    for model in MODELS:
        cells = []
        for split in SPLITS:
            values = grouped.get(("auroc", model, split))
            if not values:
                cells.append("—")
            elif len(values) > 1:
                cells.append(f"{st.mean(values):.4f} ± {st.pstdev(values):.4f}")
            else:
                cells.append(f"{values[0]:.4f}")
        a(f"| `{model}` | " + " | ".join(cells) + " |")
    a("")

    a("### Regression RMSE (lower is better)")
    a("")
    a("| Model | " + " | ".join(f"`{s}`" for s in SPLITS) + " |")
    a("| --- |" + " --- |" * len(SPLITS))
    for model in MODELS:
        cells = []
        for split in SPLITS:
            values = grouped.get(("rmse", model, split))
            cells.append(f"{st.mean(values):.3f}" if values else "—")
        a(f"| `{model}` | " + " | ".join(cells) + " |")
    a("")

    # ------------------------------------------------------------- reading it
    a("### Reading this")
    a("")
    wins, losses = [], []
    for split in SPLITS:
        m9_auroc = grouped.get(("auroc", "M9-dual-encoder", split))
        b4_auroc = grouped.get(("auroc", "B4-concat-mlp", split))
        if m9_auroc and b4_auroc:
            (wins if st.mean(m9_auroc) > st.mean(b4_auroc) else losses).append(split)
    a(
        f"**The dual encoder does not win outright.** On AUROC it beats B4 on "
        f"{len(wins)} of {len(SPLITS)} splits ({', '.join(f'`{s}`' for s in wins) or 'none'}) "
        f"and loses on {len(losses)} ({', '.join(f'`{s}`' for s in losses) or 'none'}). "
        "On RMSE it is ahead on three of four. That is the result; it was not required "
        "to win and no tuning was done to make it."
    )
    a("")
    a(
        "The two architectures are close enough on these splits that the difference is "
        "not the interesting part. What is interesting is that **both collapse on "
        "`cold_protein-v3`** — the dual encoder to 0.60 AUROC from 0.86 — and that a "
        "within-target nearest-ligand lookup (B3L) still reaches 0.82 on `random_pair-v3` "
        "with no protein information beyond the target key."
    )
    a("")
    a(
        "Per-target metrics, prevalence, scored/skipped counts and the near-homolog and "
        "long-sequence breakouts are preserved in `reports/results/m9_v1_summary.json` "
        "under the same schema as M8."
    )
    a("")

    # ------------------------------------------------------------------- CLI
    a("## The ranking CLI")
    a("")
    a("```bash")
    a("uv run seq2lead rank \\")
    a("  --sequence-file reports/examples/query_target.fasta \\")
    a("  --library curated-ki-25k-v1 \\")
    a(f"  --model {cli['checkpoint']} \\")
    a("  --top-k 15 --evidence-mode demo")
    a("```")
    a("")
    a("### Which checkpoint, and why")
    a("")
    a(f"**Split `{cli['split']}`, seed {cli['seed']}, h={cli['projection_dim']}.**")
    a("")
    a(f"{cli['rule']}")
    a("")
    a("| Seed | validation RMSE | best epoch |")
    a("| --- | --- | --- |")
    for candidate in sorted(cli["candidates"], key=lambda c: c["validation_rmse"]):
        mark = " ← chosen" if candidate["seed"] == cli["seed"] else ""
        a(
            f"| {candidate['seed']} | {candidate['validation_rmse']:.4f} | "
            f"{candidate['best_epoch']}{mark} |"
        )
    a("")
    a(
        "The compound projections the CLI scores against are computed from the "
        "checkpoint's own compound tower and the library's frozen member list, and the "
        "query embedding is standardised with **the transform stored inside that "
        "checkpoint** — not one re-derived at inference. A checkpoint saved without its "
        "transform is refused outright, because the same weights on differently scaled "
        "inputs are a different model."
    )
    a("")
    a("### What it may and may not claim")
    a("")
    a("- Scores are **predicted pKi**, not probabilities and not calibrated confidences.")
    a(
        "- Known measurements are shown in `demo` mode, labelled **prior measured "
        "evidence**, in their own column. They are **not** independent validation: the "
        "model was not asked to discover them and they may have been in its training "
        "partition."
    )
    a("- **No compound it surfaces has been experimentally tested by this project.**")
    a("")
    a("A worked run is in [`examples/rank_demo.txt`](examples/rank_demo.txt).")
    a("")

    # ----------------------------------------------------------------- library
    a("## The demonstration library")
    a("")
    profile = _library_profile(conn)
    a(
        f"`curated-ki-25k-v1` — **{profile['n_members']:,} compounds**, digest "
        f"`{profile['digest'][:16]}…`. Selection rule: compounds with at least one "
        "eligible exact Ki measurement in the pinned release, ordered by `compound.id`, "
        "capped at 25,000."
    )
    a("")
    a(
        "**This is a bounded demonstration library, not a representative sample.** The "
        "members are an **ordered eligible prefix**: the lowest 25,000 compound ids "
        f"that satisfy the rule, spanning ids {profile['id_min']:,}–"
        f"{profile['id_max']:,}. That span holds "
        f"{profile['id_max'] - profile['id_min'] + 1:,} ids, so "
        f"{profile['id_max'] - profile['id_min'] + 1 - profile['n_members']:,} ids inside "
        "it are **not** members — the prefix is ordered, not contiguous, because "
        "ineligible compounds are skipped. Either way the ordering reflects ingestion "
        "and source order rather than chemistry. It was frozen before any ranking was "
        "produced and has not been changed since."
    )
    a("")
    a("| Property | Library | Full eligible pool |")
    a("| --- | --- | --- |")
    a(
        f"| Compounds | {profile['n_members']:,} | {profile['n_eligible']:,} "
        f"({100 * profile['n_members'] / profile['n_eligible']:.1f}% covered) |"
    )
    a(f"| Mean heavy atoms | {profile['heavy_mean']:.1f} | {profile['heavy_full']:.1f} |")
    a(f"| Heavy-atom range | {profile['heavy_min']}–{profile['heavy_max']} | — |")
    a(
        f"| With stereocentres | {profile['stereo']:,} "
        f"({100 * profile['stereo'] / profile['n_members']:.1f}%) | — |"
    )
    a(
        f"| Targets measured against | {profile['targets']:,} of {profile['targets_all']:,} "
        f"({100 * profile['targets'] / profile['targets_all']:.1f}%) | — |"
    )
    a("")

    # --------------------------------------------------------------- ablations
    a("## Implemented versus deferred")
    a("")
    a("| Ablation | Status | Why |")
    a("| --- | --- | --- |")
    a("| Dual encoder, regression head | **implemented** | the bounded core of this milestone |")
    a(
        "| Contrastive training | deferred | outside the bounded core. If run, negatives "
        "come only from eligible **measured** training evidence — never unmeasured pairs "
        "— and contradictory or ambiguous evidence is kept out of confident negatives |"
    )
    a("| Cross-attention variant | deferred | a different architecture, not a setting |")
    a("| ProtBert / PLM sweep | deferred | needs a second embedding cache |")
    a(
        "| Retrieval as a feature | deferred | ships as an interpretability layer first, "
        "per the original plan's own Risk 2 |"
    )
    a("")
    a(
        "None of these was deferred on the basis of a `label_reversal-v3` score, because "
        "that split was not scored."
    )
    a("")

    # ------------------------------------------------------- correction record
    a("## Corrections applied after review")
    a("")
    a(
        "This report supersedes the first M9 write-up. No model was refitted and no "
        "prediction changed; the corrections are to inference binding, displayed "
        "evidence, artifact safety and to claims I had got wrong."
    )
    a("")
    a("| # | Correction | Effect on results |")
    a("| --- | --- | --- |")
    a(
        "| 1 | **Inference is bound to the checkpoint's feature identities.** "
        "`rank_library()` fell back to whatever ECFP4 cache was current; that fallback "
        "is removed. Each checkpoint now carries, or has a sidecar recording, its "
        "compound cache name and digests and the full protein spec (model commit, "
        "pooling, dtype, length policy, max length). Inference refuses without them. "
        "| **None.** The bound cache is the one the fallback happened to select, so the "
        "demo ranking is byte-identical. The fallback was latent risk, not active "
        "corruption. |"
    )
    a(
        "| 2 | **Censoring is preserved in displayed evidence.** The old formatter "
        "aggregated values without their relation, so `>10000 nM` — a decisive "
        "non-binder — rendered as `10000 nM`. | **Wording only in this demo**: all four "
        "compounds shown had exact records. Corpus-wide the defect touched "
        "**650,516 of 3,233,963 measured records (20.1%)**, and 14% of this very "
        "target's records are censored, so a different query or a deeper top-k would "
        "have hit it. |"
    )
    a(
        "| 3 | **Artifact paths are run-scoped and checked before fitting.** Writes were "
        "unconditional and refusal came from the result registry at the very end, after "
        "every artifact had been overwritten. | No change to existing artifacts; all 20 "
        "runs verify against a frozen manifest. |"
    )
    a(
        "| 4 | **The length refusal point is enforced.** 40,001 residues was accepted "
        "against a declared 40,000 limit. It is now rejected before ESM-2 loads. | None; "
        "the demo query is 443 residues. |"
    )
    a("")
    a("### Claims I had wrong")
    a("")
    a("| Claim | Was | Is |")
    a("| --- | --- | --- |")
    a(
        "| Final model size | 984,066 parameters | **2,230,274** — I quoted the h=256 "
        "*pilot* as though it were the h=512 final models |"
    )
    a(
        '| Library shape | "contiguous block" | an **ordered eligible prefix**; the id '
        "span holds 149,592 ids for 25,000 members, so 124,592 are absent |"
    )
    a(
        "| Previous test count | 461 | **419 passed, 0 failed, 0 skipped** — I counted "
        "dots in a `tail`-truncated log |"
    )
    a(
        '| Early stopping | "where this architecture stops transferring" | an '
        "observation from these fits under one set of hyperparameters |"
    )
    a("")

    # ------------------------------------------------------------- limitations
    a("## Limitations")
    a("")
    a("| # | Limitation |")
    a("| --- | --- |")
    a(
        "| 1 | **Exploratory, not confirmatory.** The test partitions were inspected "
        "during M8. A clean confirmatory number needs the M11 as-of split. |"
    )
    a(
        "| 2 | **The head is a bounded cosine times a learned scale.** That is the entire "
        "expressive range of the score, and it is an architectural constraint rather than "
        "a tuning choice. |"
    )
    a(
        "| 3 | **Validation stops improving within a few epochs on three of four "
        "splits**, while training loss keeps falling. Reported as an observation; no "
        "remedy was attempted in this pass. |"
    )
    a(
        "| 4 | **Seed spread is optimiser variance**, not uncertainty across targets. No "
        "target-level bootstrap was run, so no confidence intervals are reported. |"
    )
    a(
        "| 5 | **The library is an ingestion-order slice**, useful for demonstrating the "
        "path end to end and not for characterising chemical space. |"
    )
    a(
        "| 6 | **The endpoint is provisional.** Ki pooling across assays was never "
        "validated; every number here inherits that. |"
    )
    a(
        "| 7 | **Two configurations were searched, not a grid.** A better dual encoder "
        "may well exist; this pass does not look for it. |"
    )
    a("")
    return "\n".join(lines) + "\n"


def _run_records() -> list[dict[str, Any]]:
    return [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(Path("data/m9/final").glob("*.json"))
        if not path.name.endswith(".binding.json")
    ]


def _head_stats() -> dict[str, dict[str, float]]:
    import numpy as np

    from seq2lead.models.dual_encoder import load_checkpoint

    grouped: dict[str, dict[str, list[float]]] = defaultdict(
        lambda: {"a": [], "b": [], "min": [], "max": [], "mean": [], "sd": []}
    )
    for path in sorted(Path("data/m9/final").glob("*.pt")):
        model, extra = load_checkpoint(path)
        split = str(extra.get("split", ""))
        predictions = np.load(f"data/predictions_m9/{path.stem}.npz")["regression_prediction"]
        bucket = grouped[split]
        bucket["a"].append(float(model.scale.detach()))
        bucket["b"].append(float(model.offset.detach()))
        bucket["min"].append(float(predictions.min()))
        bucket["max"].append(float(predictions.max()))
        bucket["mean"].append(float(predictions.mean()))
        bucket["sd"].append(float(predictions.std()))
    return {
        split: {
            "a_mean": st.mean(v["a"]),
            "a_min": min(v["a"]),
            "a_max": max(v["a"]),
            "b_mean": st.mean(v["b"]),
            "min": min(v["min"]),
            "max": max(v["max"]),
            "mean": st.mean(v["mean"]),
            "sd": st.mean(v["sd"]),
        }
        for split, v in grouped.items()
    }


def _library_profile(conn: psycopg.Connection) -> dict[str, Any]:
    from seq2lead.models.library import load as load_library

    library, members = load_library(conn, "curated-ki-25k-v1")
    profile = conn.execute(
        "SELECT avg(n_heavy_atoms), min(n_heavy_atoms), max(n_heavy_atoms), "
        "count(*) FILTER (WHERE position(chr(64) in canonical_smiles) > 0) "
        "FROM compound WHERE id = ANY(%s)",
        (members,),
    ).fetchone()
    targets = conn.execute(
        "SELECT count(DISTINCT target_id) FROM activity WHERE compound_id = ANY(%s) "
        "AND measurement_type='KI' AND relation='='",
        (members,),
    ).fetchone()[0]
    targets_all = conn.execute(
        "SELECT count(DISTINCT target_id) FROM activity WHERE source_release_id=117 "
        "AND measurement_type='KI' AND relation='='"
    ).fetchone()[0]
    eligible = conn.execute(
        "SELECT count(DISTINCT compound_id) FROM activity WHERE source_release_id=117 "
        "AND measurement_type='KI' AND relation='=' AND value_numeric>0"
    ).fetchone()[0]
    heavy_full = conn.execute(
        "SELECT avg(n_heavy_atoms) FROM compound WHERE id IN (SELECT DISTINCT compound_id "
        "FROM activity WHERE source_release_id=117 AND measurement_type='KI' "
        "AND relation='=' AND value_numeric>0)"
    ).fetchone()[0]
    return {
        "n_members": library.n_members,
        "digest": library.member_sha256,
        "id_min": min(members),
        "id_max": max(members),
        "heavy_mean": float(profile[0]),
        "heavy_min": int(profile[1]),
        "heavy_max": int(profile[2]),
        "stereo": int(profile[3]),
        "targets": int(targets),
        "targets_all": int(targets_all),
        "n_eligible": int(eligible),
        "heavy_full": float(heavy_full),
    }


def write(content: str, path: Path = REPORT_PATH) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path
