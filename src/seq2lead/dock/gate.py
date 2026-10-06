"""The gate: does the protocol rank measured actives above measured inactives?

The decision rule lives in the frozen contract, and this module applies it
without reinterpretation. Three outcomes, and the third is a real answer:

* **pass**        -- separation is in the declared direction and meets the
                     pre-registered effect threshold;
* **fail**        -- the practical effect can be ruled out, which includes
                     separation in the *wrong* direction;
* **inconclusive** -- the confidence interval straddles the threshold, so the
                     cohort cannot decide it either way.

A two-sided Mann-Whitney U is reported, but it cannot make the gate pass on its
own: it is indifferent to direction, and a gate that ignored direction would
accept a protocol that ranks inactives first.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Any

import numpy as np

from seq2lead.eval.metrics import auroc, enrichment_factor

PASS, FAIL, INCONCLUSIVE = "pass", "fail", "inconclusive"
INSUFFICIENT = "inconclusive_insufficient_data"


def _expected_exponential_weights(scores: np.ndarray, alpha: float) -> np.ndarray:
    """Each row's exp(-alpha*rank/n), averaged over the orderings of its tied block.

    A mid-rank is the wrong device here. `exp` is convex, so exponentiating the
    mid-rank of a tied block is not the block's mean weight -- for a fully tied
    ranking it collapses to the weight of the middle of the list, which is
    nearly zero at alpha=20, rather than the chance level the block deserves.
    Averaging the weight over the ranks the block spans is exact, because within
    a tied block every member is equally likely to take every rank.
    """
    n = len(scores)
    order = np.argsort(-scores, kind="mergesort")
    ordered = scores[order]
    weights = np.empty(n, dtype=np.float64)
    i = 0
    while i < n:
        j = i
        while j + 1 < n and ordered[j + 1] == ordered[i]:
            j += 1
        block = np.arange(i + 1, j + 2, dtype=np.float64)  # 1-based ranks
        weights[order[i : j + 1]] = np.exp(-alpha * block / n).mean()
        i = j + 1
    return weights


def bedroc(scores: np.ndarray, positive: np.ndarray, alpha: float = 20.0) -> float:
    """BEDROC (Truchon & Bayly 2007), with ties averaged inside each tied block.

    Early recognition, which is what a screening triage actually cares about:
    alpha=20 concentrates roughly 80% of the weight in the top 8% of the list.

    A fully tied ranking returns the **expected value for a random ranking**, not
    a near-zero score (see `_expected_exponential_weights`). That value is

        Ra*sinh(a/2) / (cosh(a/2) - cosh(a/2 - a*Ra))  +  1 / (1 - exp(a*(1-Ra)))

    because giving every row the mean weight over all N ranks makes RIE collapse
    to exactly 1. It is **not** the positive prevalence Ra in general: the two
    coincide at Ra = 0.5 and diverge sharply as the class balance skews -- at
    alpha=20 the random value is 0.116 for Ra=0.1 and 0.061 for Ra=0.02. An
    earlier version of this docstring claimed the prevalence, which is only true
    of the balanced case this project happens to use.
    """
    n = len(scores)
    n_pos = int(positive.sum())
    if n_pos == 0 or n_pos == n:
        raise ValueError("BEDROC needs both classes")
    ra = n_pos / n
    s = float(_expected_exponential_weights(scores, alpha)[positive].sum())
    rie_denom = (1.0 - math.exp(-alpha)) / (math.exp(alpha / n) - 1.0)
    rie = (s / n_pos) / (rie_denom / n)
    factor = (
        ra * math.sinh(alpha / 2.0) / (math.cosh(alpha / 2.0) - math.cosh(alpha / 2.0 - alpha * ra))
    )
    return float(rie * factor + 1.0 / (1.0 - math.exp(alpha * (1.0 - ra))))


def bootstrap_auroc_ci(
    scores: np.ndarray,
    positive: np.ndarray,
    resamples: int,
    level: float,
    seed: int,
) -> tuple[float, float]:
    """Percentile CI from a bootstrap stratified on class.

    Stratified because resampling the pooled cohort would let the class balance
    wander between replicates, widening the interval for a reason that has
    nothing to do with how well the protocol separates.
    """
    rng = np.random.default_rng(seed)
    pos_idx = np.flatnonzero(positive)
    neg_idx = np.flatnonzero(~positive)
    draws = np.empty(resamples, dtype=np.float64)
    for b in range(resamples):
        p = rng.choice(pos_idx, size=len(pos_idx), replace=True)
        q = rng.choice(neg_idx, size=len(neg_idx), replace=True)
        idx = np.concatenate([p, q])
        draws[b] = auroc(scores[idx], positive[idx])
    tail = (1.0 - level) / 2.0 * 100.0
    return float(np.percentile(draws, tail)), float(np.percentile(draws, 100.0 - tail))


@dataclass
class GateResult:
    decision: str
    auroc: float | None
    ci_low: float | None
    ci_high: float | None
    effect_threshold: float
    n_active: int
    n_inactive: int
    rule: dict[str, str]
    secondary: dict[str, Any] = field(default_factory=dict)
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def evaluate_gate(
    ranking_scores: np.ndarray,
    positive: np.ndarray,
    *,
    effect_threshold: float,
    resamples: int,
    level: float,
    seed: int,
    minimum_per_class: int,
) -> GateResult:
    """Apply the frozen decision rule. `ranking_scores`: higher = binds tighter."""
    n_pos, n_neg = int(positive.sum()), int((~positive).sum())
    rule = {
        "pass": "ci_low > 0.5 AND auroc >= effect_threshold",
        "fail": "ci_high < effect_threshold",
        "inconclusive": "otherwise (the interval straddles the threshold)",
        "wrong_direction": "auroc < 0.5 is a failure, never a pass",
    }
    if n_pos < minimum_per_class or n_neg < minimum_per_class:
        return GateResult(
            decision=INSUFFICIENT,
            auroc=None,
            ci_low=None,
            ci_high=None,
            effect_threshold=effect_threshold,
            n_active=n_pos,
            n_inactive=n_neg,
            rule=rule,
            reason=(
                f"{n_pos} active and {n_neg} inactive survived preparation and docking; "
                f"the contract requires at least {minimum_per_class} per class."
            ),
        )

    a = auroc(ranking_scores, positive)
    lo, hi = bootstrap_auroc_ci(ranking_scores, positive, resamples, level, seed)

    from scipy.stats import mannwhitneyu

    u = mannwhitneyu(ranking_scores[positive], ranking_scores[~positive], alternative="two-sided")
    secondary: dict[str, Any] = {
        "mannwhitney_u": float(u.statistic),
        "mannwhitney_p_two_sided": float(u.pvalue),
        "median_ranking_score_active": float(np.median(ranking_scores[positive])),
        "median_ranking_score_inactive": float(np.median(ranking_scores[~positive])),
        "ef_1pct": enrichment_factor(ranking_scores, positive, 0.01),
        "ef_5pct": enrichment_factor(ranking_scores, positive, 0.05),
        "bedroc_alpha20": bedroc(ranking_scores, positive, 20.0),
    }

    if a < 0.5:
        decision = FAIL
        reason = (
            f"AUROC {a:.3f} is below 0.5: the protocol ranks measured inactives above "
            "measured actives. Separation in the wrong direction fails regardless of "
            f"its significance (Mann-Whitney p = {u.pvalue:.3g})."
        )
    elif lo > 0.5 and a >= effect_threshold:
        decision = PASS
        reason = (
            f"AUROC {a:.3f} with {level:.0%} CI [{lo:.3f}, {hi:.3f}]: the interval excludes "
            f"0.5 and the point estimate meets the pre-registered threshold {effect_threshold}."
        )
    elif hi < effect_threshold:
        decision = FAIL
        reason = (
            f"AUROC {a:.3f} with {level:.0%} CI [{lo:.3f}, {hi:.3f}]: the whole interval lies "
            f"below the pre-registered threshold {effect_threshold}, so the practical effect "
            "is ruled out on this cohort."
        )
    else:
        decision = INCONCLUSIVE
        reason = (
            f"AUROC {a:.3f} with {level:.0%} CI [{lo:.3f}, {hi:.3f}]: the interval straddles "
            f"the pre-registered threshold {effect_threshold}, so this cohort cannot decide "
            "the gate either way."
        )

    return GateResult(
        decision=decision,
        auroc=a,
        ci_low=lo,
        ci_high=hi,
        effect_threshold=effect_threshold,
        n_active=n_pos,
        n_inactive=n_neg,
        rule=rule,
        secondary=secondary,
        reason=reason,
    )


def attrition_bounds(
    ranking_scores: np.ndarray,
    positive: np.ndarray,
    n_active_total: int,
    n_inactive_total: int,
) -> dict[str, float]:
    """How far could the dropped compounds have moved AUROC, at the extremes?

    Attrition is only harmless if it could not have changed the decision. Rather
    than assert that, bound it: AUROC is a count of concordant (active, inactive)
    pairs over all such pairs, so the pairs involving a dropped compound are
    simply unknown. Assigning every one of them to the favourable outcome gives
    the highest AUROC the full cohort could have produced, and the reverse gives
    the lowest. If the optimistic bound still misses the threshold, the dropout
    cannot be what failed the gate.
    """
    n_pos, n_neg = int(positive.sum()), int((~positive).sum())
    observed_pairs = n_pos * n_neg
    total_pairs = n_active_total * n_inactive_total
    unknown_pairs = total_pairs - observed_pairs
    if unknown_pairs < 0:
        raise ValueError("more pairs scored than the cohort contains")
    concordant = auroc(ranking_scores, positive) * observed_pairs
    return {
        "observed_auroc": concordant / observed_pairs,
        "best_case_auroc": (concordant + unknown_pairs) / total_pairs,
        "worst_case_auroc": concordant / total_pairs,
        "unknown_pairs": float(unknown_pairs),
        "total_pairs": float(total_pairs),
        "fraction_unknown": unknown_pairs / total_pairs,
    }
