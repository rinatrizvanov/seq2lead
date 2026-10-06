"""Render `reports/leaderboard.md` from saved run results."""

from __future__ import annotations

import json
import statistics as st
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from seq2lead.eval.results import load as load_result_version
from seq2lead.profiling.status_banner import banner

if TYPE_CHECKING:
    import psycopg

    from seq2lead.eval.config import ExperimentConfig

REPORT_PATH = Path("reports/leaderboard.md")

MODEL_NOTES = {
    "B0-target-mean": (
        "Training mean pKi of the target; global training mean for an unseen target. "
        "Ties every compound of a target."
    ),
    "B1-ligand-ecfp4-lgbm": ("Chiral ECFP4 → LightGBM. Sees no protein at all: the **bias gate**."),
    "B2-protein-esm2-lgbm": (
        "Mean-pooled ESM-2 → LightGBM. Sees no ligand: ties every compound of a target."
    ),
    "B3L-ligand-1nn": (
        "Nearest training compound *measured against the same target* by ECFP4 Tanimoto; "
        "predicts that neighbour's training pKi."
    ),
    "B3P-protein-1nn": (
        "Nearest training target by ESM-2 cosine; predicts its mean training pKi. Ties "
        "every compound of a target."
    ),
    "B4-concat-mlp": "[ECFP4 ‖ ESM-2] → 2-hidden-layer MLP, protein block standardised on train.",
}


def _fmt(value: Any, digits: int = 4) -> str:
    if value is None:
        return "—"
    return f"{float(value):.{digits}f}"


#: Splits whose headline table must use a stratum rather than the combined score.
#: The temporal contract makes `new` the prospective question; reporting the
#: combined figure as the headline would let recurrence rate -- an artifact of how
#: often BindingDB re-measures a pair -- lift the number.
HEADLINE_STRATUM = {"temporal_proxy-v4": "temporal:new"}


def _node(run: dict, path: tuple[str, ...]):
    node = run
    for key in path:
        node = node.get(key) if isinstance(node, dict) else None
        if node is None:
            return None
    return node


def _agg(runs: list[dict], path: tuple[str, ...]) -> tuple[float | None, float | None]:
    """Mean and spread over seeds of one metric."""
    values = []
    for run in runs:
        node: Any = run
        for key in path:
            node = node.get(key) if isinstance(node, dict) else None
            if node is None:
                break
        if node is not None:
            values.append(float(node))
    if not values:
        return None, None
    return st.mean(values), (st.pstdev(values) if len(values) > 1 else 0.0)


def render(  # noqa: PLR0915
    conn: psycopg.Connection,
    config: ExperimentConfig,
    results_version: str,
    results: list[dict] | None = None,
    entry=None,
) -> str:
    """Render one **named** result version.

    `results_version` is required. There is no default and no fallback: the
    previous fallback chain is how a stale corrected summary kept being rendered
    after a newer fitting run wrote different numbers.
    """
    lines: list[str] = []
    a = lines.append
    if results is None:
        results, entry = load_result_version(results_version, config)

    a("# M8 — baseline leaderboard")
    a("")
    a(f"Generated {datetime.now(UTC):%Y-%m-%d %H:%M} UTC.")
    a("")
    for line in banner():
        a(line)
    if entry is not None:
        a(
            f"Result version **`{entry.version}`** (metric version "
            f"`{entry.metric_version}`, {entry.n_runs} runs, published "
            f"{entry.created_at}). Selected explicitly; nothing here is chosen by "
            "recency."
        )
        a("")
    a(
        f"Experiment **`{config.version}`**, contract in "
        "[`../docs/EVALUATION.md`](../docs/EVALUATION.md), written before any model was "
        "fitted. Every input below is pinned by digest and re-verified at load; nothing "
        "resolves by recency."
    )
    a("")
    if not results:
        a("_No runs recorded. `uv run seq2lead eval run` first._")
        return "\n".join(lines) + "\n"

    # ------------------------------------------------------------- inputs
    a("## Pinned inputs")
    a("")
    a("| Input | Identity |")
    a("| --- | --- |")
    a(
        f"| endpoint | `{config.endpoint_name}` (id {config.endpoint_id}), "
        f"θ = pKi {config.threshold_pki} |"
    )
    for kind, ref in config.caches.items():
        a(
            f"| {kind} cache | `{ref.name}` · manifest "
            f"`{ref.manifest_sha256[:12]}…` · bytes `{ref.storage_sha256[:12]}…` |"
        )
    a(f"| seeds | {', '.join(str(s) for s in config.seeds)} |")
    a("")

    # ------------------------------------------------------ coverage caveat
    a("## What is **not** here")
    a("")
    for name, reason in config.deferred:
        a(f"- **`{name}` was not scored.** {reason}")
    a("")
    a(
        f"So this leaderboard covers **{len(config.splits)} splits, not five**. Saying "
        "otherwise would misrepresent what was measured."
    )
    a("")

    splits = [s.name for s in config.splits]
    by_model = defaultdict(list)
    for run in results:
        by_model[run["model"]].append(run)

    # ----------------------------------------------------------- findings
    RMSE = ("regression", "rmse", "mean")
    AUROC = ("ranking", "auroc", "mean")

    def metric(model: str, split: str, *path: str) -> float | None:
        runs = [r for r in by_model.get(model, []) if r["split"] == split]
        return _agg(runs, path)[0] if runs else None

    a("## Findings")
    a("")
    a(
        "Better scores are not an acceptance criterion. What follows is what the "
        "numbers say, including where a baseline wins, and stops where the evidence "
        "stops."
    )
    a("")

    joint_better = [
        s
        for s in splits
        if (metric("B4-concat-mlp", s, *RMSE) or 9e9) < (metric("B0-target-mean", s, *RMSE) or 0)
    ]
    joint_vs_constant = (
        " -- B4 beats B0 on RMSE on every split scored here"
        if len(joint_better) == len(splits)
        else f" -- B4 beats B0 on RMSE on {len(joint_better)} of {len(splits)} splits"
    )

    beaten = [
        s
        for s in splits
        if (metric("B0-target-mean", s, *RMSE) or 9e9)
        < (metric("B1-ligand-ecfp4-lgbm", s, *RMSE) or 0)
    ]
    if beaten:
        a(
            "**1. A target-specific constant beats the ligand-only regressor.** On "
            + ", ".join(f"`{s}`" for s in beaten)
            + ", B0 -- the training mean pKi of the target -- has a lower macro RMSE "
            f"than B1: {_fmt(metric('B0-target-mean', 'random_pair-v3', *RMSE), 3)} "
            f"against {_fmt(metric('B1-ligand-ecfp4-lgbm', 'random_pair-v3', *RMSE), 3)} "
            "on `random_pair-v3`."
        )
        a("")
        a(
            "What that establishes is narrow: **this** ligand-only gradient-boosted "
            "regressor, on this endpoint, under this bounded budget, does not beat "
            "knowing which target you are looking at. It does **not** establish that "
            "the benchmark is broken, nor that learned models lose in general"
            + joint_vs_constant
            + ". It does mean any future model must clear a constant before its "
            "architecture is worth discussing."
        )
        a("")

    a(
        "**2. A within-target chemical-similarity lookup is strong where the target is "
        "seen in training.** On `random_pair-v3`, B3L reaches AUROC "
        f"{_fmt(metric('B3L-ligand-1nn', 'random_pair-v3', *AUROC))} against the joint "
        f"model's {_fmt(metric('B4-concat-mlp', 'random_pair-v3', *AUROC))}."
    )
    a("")
    a(
        "**B3L is target-conditioned and is not a ligand-only baseline.** It searches "
        "only the compounds measured against *that same target* in training, so it uses "
        "the target identity as a key even though it reads no protein features. Its "
        "score therefore measures how far *chemical similarity within an already-"
        "measured target* carries a ranking -- it does not isolate ligand memorisation."
    )
    a("")
    a(
        "**B1 is the genuine ligand-only baseline**: one model over all targets, "
        "fingerprint in, pKi out, no target key of any kind. It reaches "
        f"{_fmt(metric('B1-ligand-ecfp4-lgbm', 'random_pair-v3', *AUROC))} on "
        "`random_pair-v3`. The ligand-bias question is B1's to answer, not B3L's."
    )
    a("")

    a(
        "**3. Every protein-only predictor scores exactly chance.** B2 and B3P assign "
        "one score to all of a target's compounds, so within-target ranking is fully "
        "tied. The tie-aware metrics return AUROC 0.5000 and AP equal to prevalence "
        "rather than letting row order manufacture a signal. This is the metric "
        "implementation working: a model that cannot separate anything should score at "
        "chance."
    )
    a("")

    cold = metric("B4-concat-mlp", "cold_protein-v3", *AUROC)
    rand = metric("B4-concat-mlp", "random_pair-v3", *AUROC)
    if cold and rand:
        a(
            f"**4. The joint model scores {_fmt(rand)} on `random_pair-v3` and "
            f"{_fmt(cold)} on `cold_protein-v3`.** That is a **descriptive difference "
            "between two evaluation populations**, not a measured effect of protein "
            "holdout on its own. The two splits score different targets, different "
            "numbers of them, and different compound pools; the cold split's scored "
            "cohort is roughly half the size. Attributing the whole gap to protein "
            "novelty would require holding the evaluation population fixed, which no "
            "run here does."
        )
        a("")
        a(
            "B3L drops to exactly chance on `cold_protein-v3`, and that part is "
            "mechanical rather than statistical: every test target is unseen in "
            "training, so its declared no-neighbour fallback -- the global training "
            "mean -- fires for every pair. A within-target lookup has nothing to look "
            "up."
        )
        a("")

    near = defaultdict(lambda: {"inside": [], "outside": [], "counts": None})
    for run in results:
        strata = run.get("strata") or {}
        inside = (strata.get("near_homolog") or {}).get("auroc", {}).get("mean")
        outside = (strata.get("not_near_homolog") or {}).get("auroc", {}).get("mean")
        if inside is None or outside is None:
            continue
        bucket = near[run["model"]]
        bucket["inside"].append(inside)
        bucket["outside"].append(outside)
        bucket["counts"] = (
            strata["near_homolog"].get("n_targets_scored"),
            strata["not_near_homolog"].get("n_targets_scored"),
            strata["near_homolog"].get("n_pairs"),
        )
    if near:
        a(
            "**5. Scores are higher on the near-homolog stratum than on the rest of the "
            "cold-protein test set**, seed-averaged per model:"
        )
        a("")
        a("| Model | near-homolog AUROC | rest AUROC | difference | targets (near / rest) |")
        a("| --- | --- | --- | --- | --- |")
        for model in MODEL_NOTES:
            bucket = near.get(model)
            if not bucket or not bucket["inside"]:
                continue
            inside = st.mean(bucket["inside"])
            outside = st.mean(bucket["outside"])
            near_n, rest_n, _pairs = bucket["counts"]
            a(
                f"| `{model}` | {_fmt(inside)} | {_fmt(outside)} | "
                f"{_fmt(inside - outside, 3)} | {near_n} / {rest_n} |"
            )
        a("")
        a(
            "**This is an association, and a small one to measure.** Only 8 of the 20 "
            "near-homolog targets clear the >=5 positives / >=5 negatives floor, against "
            "324 for the rest, so the two estimates are not comparably precise and no "
            "significance is claimed -- no test was run, and seed spread would not "
            "support one anyway since it measures optimiser variance rather than "
            "variation across targets."
        )
        a("")
        a(
            "**It cannot be read as exploitation of homologous protein representations.** "
            "B1 shows the gap and B1 sees no protein features at all. Whatever makes "
            "these 8 targets easier is therefore not, by itself, protein-embedding "
            "similarity: their compound sets, label balance and measurement coverage may "
            "simply differ from the rest of the split. Separating those explanations "
            "would need a matched comparison this run does not provide."
        )
        a("")

    a(
        "**6. Most targets cannot be scored at all.** Across the splits, the majority of "
        "held-out targets fail the pre-declared >=5 positives / >=5 negatives floor and "
        "are excluded from every ranking metric, with counts given under each table. "
        "Reported prevalence is also high -- around 0.62 -- because the eligible pools "
        "are majority-active, which is why every AP figure is printed beside its "
        "prevalence."
    )
    a("")

    # ------------------------------------------------------------- models
    a("## Models")
    a("")
    a("| Model | Definition | Runs |")
    a("| --- | --- | --- |")
    for name in MODEL_NOTES:
        runs = by_model.get(name, [])
        if not runs:
            continue
        kind = (
            "deterministic, 1 run"
            if runs[0]["deterministic"]
            else f"{len({r['seed'] for r in runs})} seeds"
        )
        a(f"| `{name}` | {MODEL_NOTES[name]} | {kind} |")
    a("")

    # ------------------------------------------------------------ results
    a("## Ranking (primary)")
    a("")
    a(
        "Per target, over that target's eligible held-out **measured** compounds. "
        "Unmeasured pairs are never negatives. Macro-averaged; ± is spread across seeds, "
        "which is optimiser variance and **not** uncertainty across targets."
    )
    a("")
    for split in splits:
        headline = HEADLINE_STRATUM.get(split)
        a(f"**`{split}`**")
        a("")
        if headline:
            a(
                f"Scored on the **`{headline.split(':')[1]}` stratum only** — pairs no "
                "model had been shown at training time. That is the prospective "
                "question this split exists to ask, so the combined figure does not "
                "appear here; `recurrent` is reported in its own table below."
            )
            a("")
        a("| Model | AUROC | AP | prevalence | R@10 | EF@1% | targets scored | all-tied |")
        a("| --- | --- | --- | --- | --- | --- | --- | --- |")
        for name in MODEL_NOTES:
            runs = [r for r in by_model.get(name, []) if r["split"] == split]
            if not runs:
                continue
            prefix = ("strata", headline) if headline else ("ranking",)
            auroc, auroc_sd = _agg(runs, (*prefix, "auroc", "mean"))
            ap, _ = _agg(runs, (*prefix, "average_precision", "mean"))
            prev, _ = _agg(runs, (*prefix, "prevalence", "mean"))
            r10, _ = _agg(runs, (*prefix, "recall_at_10", "mean"))
            ef1, _ = _agg(runs, (*prefix, "enrichment_at_1pct", "mean"))
            block = _node(runs[0], prefix) or {}
            scored = block.get("n_targets_scored", 0)
            tied = block.get("n_targets_all_tied", 0)
            spread = f" ± {auroc_sd:.4f}" if auroc_sd else ""
            a(
                f"| `{name}` | {_fmt(auroc)}{spread} | {_fmt(ap)} | {_fmt(prev, 3)} | "
                f"{_fmt(r10, 3)} | {_fmt(ef1, 2)} | {scored:,} | {tied:,} |"
            )
        a("")
        # Read the footer from the *same block the table above used*. Taking it
        # from the combined ranking while the table scores a stratum would put a
        # denominator next to numbers it does not belong to.
        source = ("strata", headline) if headline else ("ranking",)
        skipped = next(
            (block for r in results if r["split"] == split and (block := _node(r, source))),
            {},
        )
        scope = f"in the `{headline.split(':')[1]}` stratum" if headline else "in this split"
        a(
            f"_{skipped.get('n_targets_skipped', 0):,} targets "
            f"({skipped.get('n_pairs_skipped', 0):,} pairs) {scope} were not scored: "
            f"{skipped.get('skip_reasons', {})}. "
            f"Scored: {skipped.get('n_targets_scored', 0):,} targets"
            + (f", {skipped['n_pairs']:,} pairs" if "n_pairs" in skipped else "")
            + "._"
        )
        a("")

    a("## Regression (secondary)")
    a("")
    a("Eligible exact measurements only. Per target, then macro-averaged — never pooled.")
    a("")
    a("| Split | Model | MAE | RMSE | Spearman | CI | targets |")
    a("| --- | --- | --- | --- | --- | --- | --- |")
    for split in splits:
        if split in HEADLINE_STRATUM:
            continue  # reported by stratum below
        for name in MODEL_NOTES:
            runs = [r for r in by_model.get(name, []) if r["split"] == split]
            if not runs:
                continue
            mae, _ = _agg(runs, ("regression", "mae", "mean"))
            rmse, _ = _agg(runs, ("regression", "rmse", "mean"))
            rho, _ = _agg(runs, ("regression", "spearman", "mean"))
            ci, _ = _agg(runs, ("regression", "concordance_index", "mean"))
            n = runs[0]["regression"]["n_targets"]
            a(
                f"| `{split}` | `{name}` | {_fmt(mae, 3)} | {_fmt(rmse, 3)} | "
                f"{_fmt(rho, 3)} | {_fmt(ci, 3)} | {n:,} |"
            )
    a("")

    for split in (s for s in splits if s in HEADLINE_STRATUM):
        a(f"**`{split}` regression, by stratum.** New pairs are the headline here too.")
        a("")
        a("| Model | Stratum | MAE | RMSE | Spearman | targets | pairs |")
        a("| --- | --- | --- | --- | --- | --- | --- |")
        for name in MODEL_NOTES:
            runs = [r for r in by_model.get(name, []) if r["split"] == split]
            if not runs:
                continue
            for stratum in ("new", "recurrent"):
                key = ("strata", f"temporal_regression:{stratum}")
                block = _node(runs[0], key)
                if block is None:
                    continue
                mae, _ = _agg(runs, (*key, "mae", "mean"))
                rmse, _ = _agg(runs, (*key, "rmse", "mean"))
                rho, _ = _agg(runs, (*key, "spearman", "mean"))
                a(
                    f"| `{name}` | {stratum} | {_fmt(mae, 3)} | {_fmt(rmse, 3)} | "
                    f"{_fmt(rho, 3)} | {block.get('n_targets', 0):,} | "
                    f"{block.get('n_pairs', 0):,} |"
                )
        a("")
        a(
            "These breakouts are **additional results computed from the same saved "
            "predictions**, not changes to any fitted value."
        )
        a("")

    # ------------------------------------------------------------- strata
    a("## Required break-outs")
    a("")
    a("Averaged over seeds, like the tables above.")
    a("")
    for label, prefix, blurb in (
        (
            "Temporal: new vs recurrent",
            "temporal:",
            "`new` is the prospective question -- a pair the model had never been shown.",
        ),
        (
            "Cold protein: near-homolog stratum",
            "near_homolog",
            "Held-out targets that satisfy the cluster guarantee and still have a "
            "training protein at >=90% identity over at least half of both sequences.",
        ),
        (
            "Long sequences",
            "training_window",
            "Targets embedded past ESM-2's 1,022-residue pre-training window, against the rest.",
        ),
    ):
        grouped: dict[tuple[str, str, str], list[dict]] = defaultdict(list)
        for run in results:
            for stratum, payload in (run.get("strata") or {}).items():
                if stratum.startswith(prefix) or stratum.endswith(prefix):
                    grouped[(run["split"], run["model"], stratum)].append(payload)
        if not grouped:
            continue
        a(f"### {label}")
        a("")
        a(blurb)
        a("")
        a("| Split | Model | Stratum | Pairs | Targets | AUROC | AP | prevalence |")
        a("| --- | --- | --- | --- | --- | --- | --- | --- |")
        for (split, model, stratum), payloads in sorted(grouped.items()):
            auroc = [p["auroc"]["mean"] for p in payloads if p["auroc"]["mean"] is not None]
            ap = [
                p["average_precision"]["mean"]
                for p in payloads
                if p["average_precision"]["mean"] is not None
            ]
            prev = [
                p["prevalence"]["mean"] for p in payloads if p["prevalence"]["mean"] is not None
            ]
            first = payloads[0]
            a(
                f"| `{split}` | `{model}` | {stratum} | {first.get('n_pairs', 0):,} | "
                f"{first.get('n_targets_scored', 0):,} | "
                f"{_fmt(st.mean(auroc) if auroc else None)} | "
                f"{_fmt(st.mean(ap) if ap else None)} | "
                f"{_fmt(st.mean(prev) if prev else None, 3)} |"
            )
        a("")

    # --------------------------------------------------------- runtimes
    a("## Runtime")
    a("")
    a("| Model | Median fit (s) | Median predict (s) |")
    a("| --- | --- | --- |")
    for name in MODEL_NOTES:
        runs = by_model.get(name, [])
        if not runs:
            continue
        a(
            f"| `{name}` | {st.median(r['fit_seconds'] for r in runs):.1f} | "
            f"{st.median(r['predict_seconds'] for r in runs):.1f} |"
        )
    a("")

    # ------------------------------------------------------ reproducibility
    a("## Reproducibility")
    a("")
    a(
        "Per-pair predictions are written to `data/predictions/` as `.npz` "
        "(compound id, target id, label, prediction, and the regression arrays), and "
        "per-target metrics to `reports/results/baseline_runs.json`. Every number above "
        "can be recomputed from those without refitting."
    )
    a("")
    a(
        "Large artifacts are referenced by path and checksum rather than bundled; see "
        "`reports/results/artifacts.md`."
    )
    a("")

    a("## Corrections applied to these numbers")
    a("")
    record = Path("reports/results/correction_v2.json")
    if record.exists():
        data = json.loads(record.read_text(encoding="utf-8"))
        a(
            f"Metric version **`{data['metric_version']}`**, recomputed from the saved "
            f"predictions of {data['n_runs']} runs with "
            "`uv run seq2lead eval recompute`. **No model was refitted**: the "
            "predictions are the originals, verified by digest."
        )
        a("")
        for change in data.get("changes", []):
            a(f"- {change}")
        a("")
        comparisons = data.get("comparisons", [])
        changed = [c for c in comparisons if c.get("changed")]
        moved: dict[str, list[float]] = defaultdict(list)
        for row in changed:
            if row.get("old") is not None and row.get("new") is not None:
                moved[row["metric"]].append(abs(row["new"] - row["old"]))
        by_metric: dict[str, bool] = {}
        for row in comparisons:
            by_metric[row["metric"]] = by_metric.get(row["metric"], False) or row["changed"]
        a("| Metric | Values compared | Changed | Max \\|delta\\| |")
        a("| --- | --- | --- | --- |")
        for name in sorted(by_metric):
            same = [c for c in comparisons if c["metric"] == name]
            hits = moved.get(name, [])
            a(
                f"| `{name}` | {len(same)} | {len(hits)} | {max(hits):.6f} |"
                if hits
                else f"| `{name}` | {len(same)} | 0 | — |"
            )
        a("")
        unchanged = sorted(m for m, did in by_metric.items() if not did)
        if unchanged:
            a(
                "**Unchanged, as they should be:** "
                + ", ".join(f"`{m}`" for m in unchanged)
                + ". The average-precision definition does not enter them, and the "
                "predictions behind them are byte-identical to the originals."
            )
            a("")
        a(
            "The superseded v1 figures are retained in "
            "`reports/results/baseline_summary.json` and the per-value comparison in "
            "`reports/results/correction_v2.json`. A corrected number is only checkable "
            "against the one it replaced."
        )
        a("")
    else:
        a("_No correction record found._")
        a("")

    a("## Limitations")
    a("")
    a("| # | Limitation |")
    a("| --- | --- |")
    a(
        "| 1 | **The endpoint is provisional.** Ki is pooled across assays as an "
        "operating assumption M5 declined to validate. Every number here inherits that. |"
    )
    a(
        "| 2 | **Seed spread is not uncertainty.** The ± figures are optimiser variance "
        "across training seeds. They say how stable a fit is, not how confident anyone "
        "should be about a new target. No target-level bootstrap was run, so **no "
        "confidence intervals across targets are reported** -- an interval computed from "
        "seeds would be a category error. |"
    )
    a(
        "| 3 | **Most held-out targets are unscoreable** at the pre-declared floor, so "
        "each macro-average covers a minority of the split and is weighted toward "
        "densely measured targets. |"
    )
    a(
        "| 4 | **Prevalence is high (~0.62)** because eligible pools are majority-active. "
        "AP and EF should be read against that, not against an implicit 50% or a "
        "screening-library prior. |"
    )
    a(
        "| 5 | **B3L uses a declared, versioned bound.** Where a target has more than "
        "4,096 training compounds the reference block is a seeded sample of that size; "
        "the exhaustive alternative is a quarter-million-square Tanimoto matrix. The cap "
        "is recorded in the fit notes along with how many targets hit it. |"
    )
    a(
        "| 6 | **Concordance index is capped at 2,000 pairs per target**; targets above "
        "it report CI as undefined with the reason, rather than an approximation. |"
    )
    a(
        "| 7 | **BEDROC is not reported.** It was not implemented or verified against a "
        "reference, and an unverified implementation of a weighted metric is worse than "
        "its absence. |"
    )
    a(
        "| 8 | **One diagnostic split is deliberately pending**, so this is not a "
        "complete picture of the five splits M6 built. |"
    )
    a(
        "| 9 | **No model here is tuned.** The budget was bounded and small by design; "
        "these are baselines, and a stronger joint model may well beat them. What the "
        "table establishes is the floor any such model has to clear. |"
    )
    a("")
    return "\n".join(lines) + "\n"


def write(content: str, path: Path = REPORT_PATH) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path
