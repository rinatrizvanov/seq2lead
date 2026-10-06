"""Ranking and regression metrics, with ties handled explicitly.

Two baselines here assign the *same score to every compound of a target*: the
per-target mean, and any protein-only model. Whatever order the database happens
to return those rows in must not become a ranking advantage, so every rank-based
metric uses mid-ranks and every threshold metric averages over the tied block. A
model that ties everything then scores exactly at chance, which is the truthful
answer rather than a flattering one.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

#: Below these counts a per-target ranking metric is not computed. Predeclared.
MIN_POSITIVES = 5
MIN_NEGATIVES = 5
#: Below this a rank correlation is meaningless.
MIN_PAIRS_FOR_RANK_CORRELATION = 5


@dataclass
class TargetRanking:
    target_id: int
    n: int
    n_positive: int
    n_negative: int
    prevalence: float
    auroc: float | None = None
    average_precision: float | None = None
    recall_at: dict[int, float] = field(default_factory=dict)
    enrichment_at: dict[float, float] = field(default_factory=dict)
    all_tied: bool = False
    undefined_reason: str | None = None


@dataclass
class TargetRegression:
    target_id: int
    n: int
    mae: float | None = None
    rmse: float | None = None
    spearman: float | None = None
    concordance_index: float | None = None
    undefined_reason: str | None = None


def midranks(scores: np.ndarray) -> np.ndarray:
    """Ranks with ties averaged. The basis of every tie-aware metric here."""
    order = np.argsort(scores, kind="mergesort")
    ranks = np.empty(len(scores), dtype=np.float64)
    ordered = scores[order]
    i = 0
    while i < len(ordered):
        j = i
        while j + 1 < len(ordered) and ordered[j + 1] == ordered[i]:
            j += 1
        ranks[order[i : j + 1]] = 0.5 * (i + j) + 1.0
        i = j + 1
    return ranks


def auroc(scores: np.ndarray, positive: np.ndarray) -> float:
    """Tie-corrected AUROC via the Mann-Whitney statistic; ties count 0.5.

    All-tied scores give exactly 0.5, which is what a model that cannot separate
    anything deserves.
    """
    n_pos = int(positive.sum())
    n_neg = int((~positive).sum())
    if n_pos == 0 or n_neg == 0:
        raise ValueError("AUROC needs at least one positive and one negative")
    ranks = midranks(scores)
    return float((ranks[positive].sum() - n_pos * (n_pos + 1) / 2.0) / (n_pos * n_neg))


def average_precision(scores: np.ndarray, positive: np.ndarray) -> float:
    """Non-interpolated AP: sum over thresholds of (recall increment x precision).

    Each *distinct score* is a threshold, and the whole block of rows sharing it
    is admitted at once before precision is read. That is what makes the result
    independent of order inside a tied block without needing to average over
    permutations.

    A fully tied ranking admits everything at the single threshold, giving
    precision = prevalence and recall = 1, so AP equals the positive prevalence --
    the chance level.
    """
    n_pos = int(positive.sum())
    if n_pos == 0:
        raise ValueError("average precision needs at least one positive")
    order = np.argsort(-scores, kind="mergesort")
    sorted_scores = scores[order]
    sorted_pos = positive[order].astype(np.float64)

    total = 0.0
    tp = 0.0
    admitted = 0
    previous_recall = 0.0
    i = 0
    while i < len(sorted_scores):
        j = i
        while j + 1 < len(sorted_scores) and sorted_scores[j + 1] == sorted_scores[i]:
            j += 1
        # Admit the entire tied block, then read precision once.
        tp += float(sorted_pos[i : j + 1].sum())
        admitted += j - i + 1
        recall = tp / n_pos
        total += (recall - previous_recall) * (tp / admitted)
        previous_recall = recall
        i = j + 1
    return float(total)


def average_precision_superseded(scores: np.ndarray, positive: np.ndarray) -> float:
    """The M8 v1 implementation, kept so historical numbers stay explicable.

    It averaged precision over every ordering *within* a tied block rather than
    admitting the block whole. That is a different quantity, and it errs in both
    directions rather than being a bounded approximation: on `scores = [2, 1, 1]`
    with `positive = [True, True, False]` it returns 0.8541667 where the standard
    definition and `sklearn.metrics.average_precision_score` return 0.8333333, but
    on real data it also returns values *below* the standard. Measured on one
    split it disagreed with sklearn on 245 of 328 scored targets, by up to 0.057
    either way.

    Retained for provenance only. Nothing current calls it.
    """
    n_pos = int(positive.sum())
    if n_pos == 0:
        raise ValueError("average precision needs at least one positive")
    order = np.argsort(-scores, kind="mergesort")
    sorted_scores = scores[order]
    sorted_pos = positive[order].astype(np.float64)

    total = 0.0
    seen = 0
    tp = 0.0
    i = 0
    while i < len(sorted_scores):
        j = i
        while j + 1 < len(sorted_scores) and sorted_scores[j + 1] == sorted_scores[i]:
            j += 1
        block = slice(i, j + 1)
        block_size = j - i + 1
        block_pos = float(sorted_pos[block].sum())
        for step in range(1, block_size + 1):
            rank = seen + step
            expected_tp = tp + block_pos * step / block_size
            total += (expected_tp / rank) * ((block_pos / block_size) / n_pos)
        tp += block_pos
        seen += block_size
        i = j + 1
    return float(total)


def cutoff_index(n: int, fraction: float) -> int:
    """Declared rounding: `max(1, ceil(fraction * n))`."""
    return max(1, math.ceil(fraction * n))


def recall_at_k(scores: np.ndarray, positive: np.ndarray, k: int) -> float:
    """Recall in the top k. k is clipped to n. Tied blocks contribute pro rata."""
    n = len(scores)
    k = min(k, n)
    n_pos = int(positive.sum())
    if n_pos == 0:
        raise ValueError("recall@k needs at least one positive")
    return float(_expected_hits(scores, positive, k) / n_pos)


def enrichment_factor(scores: np.ndarray, positive: np.ndarray, fraction: float) -> float:
    """EF = (hits in top f) / (expected hits at that depth)."""
    n = len(scores)
    k = cutoff_index(n, fraction)
    n_pos = int(positive.sum())
    if n_pos == 0:
        raise ValueError("enrichment needs at least one positive")
    expected = n_pos * k / n
    return float(_expected_hits(scores, positive, k) / expected)


def _expected_hits(scores: np.ndarray, positive: np.ndarray, k: int) -> float:
    """Positives in the top k, averaging over the ordering of any tied block.

    Taking whichever rows the database returned first would let storage order
    decide the score for a model that assigns identical values.
    """
    order = np.argsort(-scores, kind="mergesort")
    sorted_scores = scores[order]
    sorted_pos = positive[order].astype(np.float64)
    hits = 0.0
    filled = 0
    i = 0
    while i < len(sorted_scores) and filled < k:
        j = i
        while j + 1 < len(sorted_scores) and sorted_scores[j + 1] == sorted_scores[i]:
            j += 1
        block_size = j - i + 1
        block_pos = float(sorted_pos[i : j + 1].sum())
        take = min(block_size, k - filled)
        hits += block_pos * take / block_size
        filled += take
        i = j + 1
    return hits


def spearman(x: np.ndarray, y: np.ndarray) -> float:
    """Rank correlation on mid-ranks."""
    rx, ry = midranks(x), midranks(y)
    rx = rx - rx.mean()
    ry = ry - ry.mean()
    denominator = float(np.sqrt((rx**2).sum() * (ry**2).sum()))
    if denominator == 0.0:
        raise ValueError("no variance in one of the rankings")
    return float((rx * ry).sum() / denominator)


def concordance_index(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    """Fraction of comparable pairs ordered correctly; ties in prediction count 0.5.

    O(n^2) by definition, so callers cap the per-target size; targets above the
    cap are reported as such rather than silently approximated.
    """
    n = len(y_true)
    if n < 2:  # noqa: PLR2004
        raise ValueError("concordance index needs at least two observations")
    diff_true = y_true[:, None] - y_true[None, :]
    comparable = diff_true > 0
    if not comparable.any():
        raise ValueError("no comparable pairs: all labels equal")
    diff_pred = y_pred[:, None] - y_pred[None, :]
    concordant = (diff_pred > 0)[comparable].sum()
    tied = (diff_pred == 0)[comparable].sum()
    return float((concordant + 0.5 * tied) / comparable.sum())


def score_target_ranking(
    target_id: int,
    scores: np.ndarray,
    positive: np.ndarray,
    recall_ks: tuple[int, ...] = (10, 50),
    enrichment_fractions: tuple[float, ...] = (0.01, 0.05),
    min_positives: int = MIN_POSITIVES,
    min_negatives: int = MIN_NEGATIVES,
) -> TargetRanking:
    """All ranking metrics for one target, or a stated reason for none."""
    n_pos, n_neg = int(positive.sum()), int((~positive).sum())
    result = TargetRanking(
        target_id=target_id,
        n=len(scores),
        n_positive=n_pos,
        n_negative=n_neg,
        prevalence=(n_pos / len(scores)) if len(scores) else 0.0,
        all_tied=bool(len(scores) and np.all(scores == scores[0])),
    )
    if n_pos < min_positives or n_neg < min_negatives:
        result.undefined_reason = (
            f"needs >={min_positives} positives and >={min_negatives} negatives; "
            f"has {n_pos} and {n_neg}"
        )
        return result
    result.auroc = auroc(scores, positive)
    result.average_precision = average_precision(scores, positive)
    result.recall_at = {k: recall_at_k(scores, positive, k) for k in recall_ks}
    result.enrichment_at = {f: enrichment_factor(scores, positive, f) for f in enrichment_fractions}
    return result


def score_target_regression(
    target_id: int,
    y_true: np.ndarray,
    y_pred: np.ndarray,
    min_pairs: int = MIN_PAIRS_FOR_RANK_CORRELATION,
    ci_cap: int = 2000,
) -> TargetRegression:
    """MAE/RMSE always; rank metrics only where they are defined."""
    n = len(y_true)
    result = TargetRegression(target_id=target_id, n=n)
    if n == 0:
        result.undefined_reason = "no eligible pairs"
        return result
    residual = y_pred - y_true
    result.mae = float(np.abs(residual).mean())
    result.rmse = float(np.sqrt((residual**2).mean()))
    if n < min_pairs:
        result.undefined_reason = f"needs >={min_pairs} pairs for rank metrics; has {n}"
        return result
    try:
        result.spearman = spearman(y_true, y_pred)
    except ValueError as exc:
        result.undefined_reason = str(exc)
    if n <= ci_cap:
        try:
            result.concordance_index = concordance_index(y_true, y_pred)
        except ValueError as exc:
            result.undefined_reason = result.undefined_reason or str(exc)
    else:
        result.undefined_reason = result.undefined_reason or (
            f"concordance index skipped: {n:,} pairs exceeds the {ci_cap:,} cap"
        )
    return result
