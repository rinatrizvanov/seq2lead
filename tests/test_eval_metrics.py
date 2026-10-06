"""Metrics checked against hand-computed examples and against ties.

Two baselines assign one score to every compound of a target. If ties were
resolved by row order, those models would score above chance for no reason, and
the leaderboard would reward storage layout.
"""

from __future__ import annotations

import numpy as np
import pytest

from seq2lead.eval import metrics as M

# ===================================================================== mid-ranks


def test_midranks_average_tied_positions() -> None:
    # values 1,2,2,3 -> ranks 1, 2.5, 2.5, 4
    assert list(M.midranks(np.array([1.0, 2.0, 2.0, 3.0]))) == [1.0, 2.5, 2.5, 4.0]


def test_midranks_are_independent_of_input_order() -> None:
    values = np.array([3.0, 1.0, 2.0, 2.0])
    shuffled = values[[2, 0, 3, 1]]
    assert sorted(M.midranks(values)) == sorted(M.midranks(shuffled))


# ======================================================================== AUROC


def test_auroc_perfect_and_inverted() -> None:
    positive = np.array([True, True, False, False])
    assert M.auroc(np.array([4.0, 3.0, 2.0, 1.0]), positive) == 1.0
    assert M.auroc(np.array([1.0, 2.0, 3.0, 4.0]), positive) == 0.0


def test_auroc_hand_computed() -> None:
    # positives score 3 and 1; negatives score 2 and 0.
    # pairs: (3>2) (3>0) (1<2) (1>0) -> 3 of 4 concordant
    scores = np.array([3.0, 1.0, 2.0, 0.0])
    positive = np.array([True, True, False, False])
    assert M.auroc(scores, positive) == pytest.approx(0.75)


def test_all_tied_scores_give_exactly_chance() -> None:
    """The per-target-mean and protein-only case."""
    positive = np.array([True, True, False, False, False])
    assert M.auroc(np.ones(5), positive) == pytest.approx(0.5)


def test_auroc_is_unaffected_by_row_order_when_everything_ties() -> None:
    positive = np.array([True, False, True, False])
    for permutation in ([0, 1, 2, 3], [3, 2, 1, 0], [1, 3, 0, 2]):
        assert M.auroc(np.zeros(4), positive[permutation]) == pytest.approx(0.5)


# =========================================================== average precision


def test_average_precision_perfect_ranking() -> None:
    positive = np.array([True, True, False, False])
    assert M.average_precision(np.array([4.0, 3.0, 2.0, 1.0]), positive) == pytest.approx(1.0)


def test_average_precision_hand_computed() -> None:
    # ranked: pos, neg, pos, neg -> precisions 1/1 at recall .5, 2/3 at recall 1
    # AP = 0.5*1 + 0.5*(2/3) = 0.8333...
    scores = np.array([4.0, 3.0, 2.0, 1.0])
    positive = np.array([True, False, True, False])
    assert M.average_precision(scores, positive) == pytest.approx((1.0 + 2 / 3) / 2)


def test_the_partially_tied_block_case_that_broke_the_first_implementation() -> None:
    """The discriminating example. The superseded version returns 0.8541667.

    Admitting the tied block whole gives precision 2/3 at full recall:
    0.5*1 + 0.5*(2/3) = 0.8333333. Averaging over orderings *inside* the block,
    as the first implementation did, credits the positive for sometimes landing
    first and inflates the score.
    """
    scores = np.array([2.0, 1.0, 1.0])
    positive = np.array([True, True, False])
    assert M.average_precision(scores, positive) == pytest.approx(0.8333333, abs=1e-7)
    assert M.average_precision_superseded(scores, positive) == pytest.approx(0.8541667, abs=1e-7)
    assert M.average_precision(scores, positive) != pytest.approx(
        M.average_precision_superseded(scores, positive)
    )


@pytest.mark.parametrize(
    ("scores", "positive", "expected"),
    [
        # one threshold admits everything -> precision = prevalence
        ([0.0, 0.0, 0.0, 0.0], [True, True, False, False], 0.5),
        ([0.0, 0.0, 0.0, 0.0], [True, False, False, False], 0.25),
        # a mixed tied block below a clean positive
        ([3.0, 2.0, 2.0, 2.0, 1.0], [True, False, True, False, True], 0.7),
        # untied, interleaved
        ([5.0, 4.0, 3.0, 2.0], [True, False, True, False], (1.0 + 2 / 3) / 2),
    ],
)
def test_average_precision_cases(scores, positive, expected) -> None:
    assert M.average_precision(np.array(scores, dtype=float), np.array(positive)) == pytest.approx(
        expected
    )


def test_fully_tied_average_precision_equals_prevalence() -> None:
    """Chance level, not an order-dependent accident."""
    positive = np.array([True, False, False, False])
    assert M.average_precision(np.zeros(4), positive) == pytest.approx(0.25)
    positive = np.array([True, True, False, False])
    assert M.average_precision(np.zeros(4), positive) == pytest.approx(0.5)


def test_average_precision_is_invariant_to_order_within_a_tied_block() -> None:
    """Admitting the block whole is what makes this hold without averaging."""
    scores = np.array([3.0, 2.0, 2.0, 2.0, 1.0])
    positive = np.array([True, False, True, False, True])
    reference = M.average_precision(scores, positive)
    rng = np.random.default_rng(11)
    for _ in range(25):
        permutation = np.arange(5)
        block = permutation[1:4]
        rng.shuffle(block)
        permutation[1:4] = block
        assert M.average_precision(scores[permutation], positive[permutation]) == pytest.approx(
            reference
        )


def test_average_precision_matches_sklearn_on_seeded_examples() -> None:
    """The external reference. Ties are the whole point, so scores are coarse."""
    sklearn_metrics = pytest.importorskip("sklearn.metrics")
    rng = np.random.default_rng(20260930)
    checked = 0
    for _ in range(300):
        n = int(rng.integers(5, 80))
        scores = rng.integers(0, 6, n).astype(float)  # many ties by construction
        positive = rng.random(n) < 0.4  # noqa: PLR2004
        if not positive.any():
            continue
        assert M.average_precision(scores, positive) == pytest.approx(
            sklearn_metrics.average_precision_score(positive, scores)
        )
        checked += 1
    assert checked > 250, f"only {checked} usable cases generated"  # noqa: PLR2004


# ============================================================ recall and EF


def test_cutoff_rounding_is_the_declared_rule() -> None:
    assert M.cutoff_index(100, 0.01) == 1
    assert M.cutoff_index(101, 0.01) == 2  # ceil(1.01)
    assert M.cutoff_index(10, 0.01) == 1  # max(1, ...)
    assert M.cutoff_index(200, 0.05) == 10


def test_recall_at_k_clips_k_to_the_cohort() -> None:
    positive = np.array([True, True, False])
    assert M.recall_at_k(np.array([3.0, 2.0, 1.0]), positive, 50) == pytest.approx(1.0)


def test_recall_at_k_hand_computed() -> None:
    positive = np.array([True, False, True, False, False])
    scores = np.array([5.0, 4.0, 3.0, 2.0, 1.0])
    assert M.recall_at_k(scores, positive, 1) == pytest.approx(0.5)
    assert M.recall_at_k(scores, positive, 3) == pytest.approx(1.0)


def test_tied_block_contributes_pro_rata_not_by_row_order() -> None:
    """Top-2 of a four-way tie containing two positives -> exactly 1 expected hit."""
    positive = np.array([True, True, False, False])
    assert M._expected_hits(np.zeros(4), positive, 2) == pytest.approx(1.0)  # noqa: SLF001
    assert M.recall_at_k(np.zeros(4), positive, 2) == pytest.approx(0.5)


def test_enrichment_of_a_fully_tied_ranking_is_one() -> None:
    positive = np.array([True] * 10 + [False] * 90)
    assert M.enrichment_factor(np.zeros(100), positive, 0.01) == pytest.approx(1.0)


def test_enrichment_perfect_ranking() -> None:
    positive = np.array([True] * 10 + [False] * 90)
    scores = np.concatenate([np.full(10, 9.0), np.zeros(90)])
    # top 1% is 1 compound, a positive; expected hits 10*1/100 = 0.1 -> EF 10
    assert M.enrichment_factor(scores, positive, 0.01) == pytest.approx(10.0)


# ======================================================== regression metrics


def test_regression_errors_hand_computed() -> None:
    result = M.score_target_regression(1, np.array([6.0, 7.0]), np.array([6.5, 6.5]))
    assert result.mae == pytest.approx(0.5)
    assert result.rmse == pytest.approx(0.5)


def test_spearman_needs_variance() -> None:
    with pytest.raises(ValueError, match="no variance"):
        M.spearman(np.array([1.0, 2.0, 3.0]), np.array([5.0, 5.0, 5.0]))


def test_concordance_index_hand_computed() -> None:
    # true 1<2<3; predicted 1<3>2 -> pairs (2>1) ok, (3>1) ok, (3>2) wrong -> 2/3
    assert M.concordance_index(
        np.array([1.0, 2.0, 3.0]), np.array([1.0, 3.0, 2.0])
    ) == pytest.approx(2 / 3)


def test_concordance_index_counts_prediction_ties_as_half() -> None:
    assert M.concordance_index(np.array([1.0, 2.0]), np.array([5.0, 5.0])) == pytest.approx(0.5)


# ============================================ undefined per-target metrics


def test_target_below_the_positive_floor_is_reported_not_dropped() -> None:
    positive = np.array([True] * 2 + [False] * 20)
    result = M.score_target_ranking(7, np.arange(22.0), positive)
    assert result.auroc is None
    assert result.undefined_reason is not None
    assert "positives" in result.undefined_reason
    assert result.n_positive == 2  # noqa: PLR2004


def test_target_with_no_negatives_is_reported_not_dropped() -> None:
    result = M.score_target_ranking(8, np.arange(10.0), np.ones(10, dtype=bool))
    assert result.auroc is None
    assert result.n_negative == 0


def test_all_tied_is_flagged_on_the_result() -> None:
    positive = np.array([True] * 5 + [False] * 5)
    result = M.score_target_ranking(9, np.zeros(10), positive)
    assert result.all_tied is True
    assert result.auroc == pytest.approx(0.5)
    assert result.average_precision == pytest.approx(0.5)


def test_regression_rank_metrics_are_skipped_below_the_floor() -> None:
    result = M.score_target_regression(10, np.array([6.0, 7.0]), np.array([6.0, 7.0]))
    assert result.mae is not None
    assert result.spearman is None
    assert "rank metrics" in result.undefined_reason


# ============================================== the tracked discriminating fixture


def _ap_cases():
    import json
    from pathlib import Path

    path = Path(__file__).parent / "fixtures" / "average_precision_cases.json"
    return json.loads(path.read_text(encoding="utf-8"))["cases"]


@pytest.mark.parametrize("case", _ap_cases(), ids=lambda c: c["name"][:40])
def test_average_precision_fixture_cases(case) -> None:
    """Every case is checked three ways: corrected, superseded, and sklearn.

    The fixture is tracked so the regression stays legible: it records what the
    old implementation returned as well as what the right answer is.
    """
    sklearn_metrics = pytest.importorskip("sklearn.metrics")
    scores = np.array(case["scores"], dtype=float)
    positive = np.array(case["positive"])

    assert M.average_precision(scores, positive) == pytest.approx(case["expected"])
    assert sklearn_metrics.average_precision_score(positive, scores) == pytest.approx(
        case["expected"]
    )
    assert M.average_precision_superseded(scores, positive) == pytest.approx(case["superseded"])


def test_the_superseded_error_runs_in_both_directions() -> None:
    """Not a bounded approximation: it is a different quantity.

    Calling it 'inflated' would be wrong, and the fixture carries one case each
    way to keep that honest.
    """
    cases = _ap_cases()
    signed = [c["superseded"] - c["expected"] for c in cases]
    assert max(signed) > 1e-6, "no case where the superseded value is too high"
    assert min(signed) < -1e-6, "no case where the superseded value is too low"
