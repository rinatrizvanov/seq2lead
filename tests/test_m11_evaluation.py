"""The three readings of "what B says", exercised on the real endpoint logic.

Nothing is fitted here. The point is that `docs/M11.md` §5.2a is implemented
rather than merely written down, since the §5.2-versus-example-(e) contradiction
came from having it only in prose.
"""

from __future__ import annotations

import pytest

from seq2lead.asof.evaluation import (
    SCREENED_STATUSES,
    ActivityRow,
    Branch,
    LeakageError,
    Measurement,
    PairEvidence,
    assert_no_reserved_evidence,
    evaluate,
    evaluate_pair,
    full_b_status,
    increment_label,
    select_for_fitting,
    training_label,
)
from seq2lead.endpoint.build import STATUS_CONFLICT, STATUS_EMPTY, STATUS_NO_EVIDENCE
from seq2lead.endpoint.interval import ACTIVE, AMBIGUOUS, INACTIVE

THETA = 6.0

EXACT_TIGHT = Measurement("=", 10.0, is_exact=True)  # pKi 8.0 -> active
BOUND_ACTIVE = Measurement("<", 1.0)  # pKi > 9  -> decisive active
BOUND_INACTIVE = Measurement(">", 10000.0)  # pKi < 5  -> decisive inactive
BOUND_AMBIGUOUS = Measurement(">", 100.0)  # pKi < 7  -> straddles theta


# ============================================= censoring, both branches


def test_a_decisive_upper_bound_on_ki_gives_an_active_label() -> None:
    """The branch an earlier draft omitted: censored evidence can decide ACTIVE."""
    status, label = increment_label(PairEvidence.from_delta("p", [], [BOUND_ACTIVE]), THETA)
    assert (status, label) == ("ok", ACTIVE)


def test_a_decisive_lower_bound_on_ki_gives_an_inactive_label() -> None:
    status, label = increment_label(PairEvidence.from_delta("p", [], [BOUND_INACTIVE]), THETA)
    assert (status, label) == ("ok", INACTIVE)


def test_a_straddling_bound_decides_nothing() -> None:
    status, label = increment_label(PairEvidence.from_delta("p", [], [BOUND_AMBIGUOUS]), THETA)
    assert status == "ok"
    assert label == AMBIGUOUS


@pytest.mark.parametrize(
    ("relation", "value_nm", "expected"),
    [
        ("<=", 1e-3, ACTIVE),  # pKi >= 12, inclusive, well above theta
        (">=", 1e9, INACTIVE),  # pKi <= 0, inclusive, well below theta
        ("~", 10.0, AMBIGUOUS),  # bounds nothing at all
    ],
)
def test_inclusive_and_unbounded_relations(relation, value_nm, expected) -> None:
    status, label = increment_label(
        PairEvidence.from_delta("p", [], [Measurement(relation, value_nm)]), THETA
    )
    if expected is AMBIGUOUS and relation == "~":
        assert status == STATUS_NO_EVIDENCE
    assert label == expected


def test_the_threshold_boundary_is_read_as_implemented() -> None:
    """`pKi >= theta` is active, so an inclusive upper bound at theta cannot decide."""
    at_theta_nm = 10 ** (9 - THETA)  # pKi exactly 6.0
    inclusive_upper = Measurement(">=", at_theta_nm)  # pKi <= 6.0, inclusive
    exclusive_upper = Measurement(">", at_theta_nm)  # pKi <  6.0
    assert (
        increment_label(PairEvidence.from_delta("p", [], [inclusive_upper]), THETA)[1] == AMBIGUOUS
    )
    assert (
        increment_label(PairEvidence.from_delta("p", [], [exclusive_upper]), THETA)[1] == INACTIVE
    )
    inclusive_lower = Measurement("<=", at_theta_nm)  # pKi >= 6.0
    assert increment_label(PairEvidence.from_delta("p", [], [inclusive_lower]), THETA)[1] == ACTIVE


# ===================================================== examples (e) and (f)


def test_example_e_exact_active_in_a_then_a_decisive_inactive_bound() -> None:
    """Increment labels it inactive; full B is an exact-versus-bound conflict."""
    evidence = PairEvidence.from_delta("P", in_a=[EXACT_TIGHT], added=[BOUND_INACTIVE])
    outcome = evaluate_pair(evidence, THETA)
    assert (outcome.increment_status, outcome.increment_label) == ("ok", INACTIVE)
    assert outcome.full_b_status == STATUS_CONFLICT
    assert (outcome.training_status, outcome.training_label) == ("ok", ACTIVE)
    assert outcome.in_primary is False
    assert outcome.in_sensitivity is True
    assert outcome.screened_because == STATUS_CONFLICT
    assert outcome.stratum == "recurrent"


def test_example_f_censored_active_in_a_then_a_decisive_inactive_bound() -> None:
    """A's label is ACTIVE and comes from censored evidence; full B is empty."""
    evidence = PairEvidence.from_delta("Q", in_a=[BOUND_ACTIVE], added=[BOUND_INACTIVE])
    outcome = evaluate_pair(evidence, THETA)
    assert (outcome.increment_status, outcome.increment_label) == ("ok", INACTIVE)
    assert outcome.full_b_status == STATUS_EMPTY
    assert (outcome.training_status, outcome.training_label) == ("ok", ACTIVE)
    assert outcome.in_primary is False
    assert outcome.in_sensitivity is True


def test_e_and_f_are_distinguished_rather_than_collapsed() -> None:
    """Both are contradictions, and the audit record must say which kind."""
    e = evaluate_pair(PairEvidence.from_delta("P", [EXACT_TIGHT], [BOUND_INACTIVE]), THETA)
    f = evaluate_pair(PairEvidence.from_delta("Q", [BOUND_ACTIVE], [BOUND_INACTIVE]), THETA)
    assert e.full_b_status != f.full_b_status
    assert {e.full_b_status, f.full_b_status} == {STATUS_CONFLICT, STATUS_EMPTY}
    assert e.full_b_status in SCREENED_STATUSES
    assert f.full_b_status in SCREENED_STATUSES


# ============================================= unchanged evidence, corrections


def test_unchanged_evidence_yields_no_evaluation_label_but_keeps_training() -> None:
    evidence = PairEvidence.from_delta("U", in_a=[EXACT_TIGHT], added=[])
    outcome = evaluate_pair(evidence, THETA)
    assert outcome.increment_status == STATUS_NO_EVIDENCE
    assert outcome.in_primary is False and outcome.in_sensitivity is False
    assert (outcome.training_status, outcome.training_label) == ("ok", ACTIVE)


def test_a_corrected_value_does_not_change_the_training_label() -> None:
    """A's label is computed from A's evidence whatever B later says it is."""
    before = PairEvidence.from_delta("C", in_a=[Measurement("=", 10.0, is_exact=True)], added=[])
    corrected = PairEvidence.from_delta(
        "C",
        in_a=[Measurement("=", 10.0, is_exact=True)],
        added=[Measurement("=", 20000.0, is_exact=True)],
    )
    assert training_label(before, THETA) == training_label(corrected, THETA)
    assert training_label(corrected, THETA) == ("ok", ACTIVE)
    # the increment alone reads inactive, and full B averages two exacts
    assert increment_label(corrected, THETA) == ("ok", INACTIVE)
    assert full_b_status(corrected, THETA)[0] == "ok"


def test_a_consistent_addition_survives_the_screen() -> None:
    evidence = PairEvidence.from_delta("K", in_a=[EXACT_TIGHT], added=[Measurement("<", 100.0)])
    outcome = evaluate_pair(evidence, THETA)
    assert outcome.full_b_status == "ok", "pKi 8 sits inside pKi > 7, so no contradiction"
    assert outcome.in_primary is True
    assert outcome.in_sensitivity is True


# ============================================ both branches are always reported


def test_evaluate_reports_primary_and_sensitivity_together() -> None:
    pairs = [
        PairEvidence.from_delta("clean", [], [BOUND_ACTIVE]),
        PairEvidence.from_delta("contradicted", [EXACT_TIGHT], [BOUND_INACTIVE]),
        PairEvidence.from_delta("ambiguous", [], [BOUND_AMBIGUOUS]),
    ]
    result = evaluate(pairs, THETA)
    assert result[Branch.PRIMARY] == ["clean"]
    assert result[Branch.SENSITIVITY] == ["clean", "contradicted"]
    assert result["screened_out"] == ["contradicted"]
    assert "ambiguous" not in result[Branch.SENSITIVITY]
    assert sorted(SCREENED_STATUSES) == result["screened_statuses"]


def test_the_screen_is_declared_not_assembled_at_call_time() -> None:
    assert SCREENED_STATUSES == frozenset({STATUS_CONFLICT, STATUS_EMPTY})


def test_training_labels_are_identical_under_both_branches() -> None:
    pairs = [
        PairEvidence.from_delta("a", [EXACT_TIGHT], [BOUND_INACTIVE]),
        PairEvidence.from_delta("b", [BOUND_ACTIVE], [BOUND_INACTIVE]),
        PairEvidence.from_delta("c", [EXACT_TIGHT], []),
    ]
    before = {p.pair: training_label(p, THETA) for p in pairs}
    evaluate(pairs, THETA)
    assert {p.pair: training_label(p, THETA) for p in pairs} == before


# ============================ training / validation separation, via selection


def _rows() -> list[ActivityRow]:
    return [
        ActivityRow(1, "train"),
        ActivityRow(2, "train"),
        ActivityRow(3, "validation"),
        ActivityRow(4, "validation"),
        ActivityRow(5, "b_increment"),
    ]


def test_only_train_evidence_may_enter_a_fitted_artifact() -> None:
    """Driven through the actual selection, not through a one-argument signature."""
    selected = select_for_fitting(_rows(), "activity-target feature cache")
    assert selected == [1, 2]
    assert 3 not in selected and 4 not in selected, "validation evidence was selected"
    assert 5 not in selected, "evaluation evidence was selected"


@pytest.mark.parametrize(
    "artifact",
    [
        "activity-target feature cache",
        "B0 per-target mean",
        "B3 kNN retrieval index",
        "evidence payload",
        "near-homolog stratum",
    ],
)
def test_every_fitted_artifact_refuses_reserved_evidence(artifact) -> None:
    rows = _rows()
    with pytest.raises(LeakageError, match="reserved evidence"):
        assert_no_reserved_evidence([1, 2, 3], rows, artifact)
    with pytest.raises(LeakageError, match="b_increment"):
        assert_no_reserved_evidence([1, 5], rows, artifact)
    assert_no_reserved_evidence(select_for_fitting(rows, artifact), rows, artifact)


def test_the_leakage_message_names_the_partition_not_just_the_id() -> None:
    with pytest.raises(LeakageError) as excinfo:
        assert_no_reserved_evidence([3], _rows(), "B3 kNN retrieval index")
    message = str(excinfo.value)
    assert "validation" in message
    assert "train_only" in message


def test_validation_is_reserved_rather_than_merely_unused() -> None:
    """Reserving rows is not enough; the artifact must not carry them indirectly."""
    from seq2lead.asof.evaluation import FITTABLE_PARTITIONS

    assert FITTABLE_PARTITIONS == frozenset({"train"})
    assert "validation" not in FITTABLE_PARTITIONS
    assert "b_increment" not in FITTABLE_PARTITIONS


def test_selection_is_deterministic_and_sorted() -> None:
    rows = list(reversed(_rows()))
    assert select_for_fitting(rows, "x") == [1, 2]


# ============================ actual B, not A plus additions (correction pass)


def test_a_withdrawal_leaves_full_b_clean_rather_than_contradictory() -> None:
    """The confirmed defect, in one fixture.

    A holds a decisive **inactive** bound. B removes that bound and adds an exact
    **active** measurement. Snapshot B therefore contains only the exact value and
    is perfectly consistent. Computing the full-B status as `A + additions` put
    the withdrawn bound back, manufactured an exact-versus-bound contradiction,
    and screened out a pair B has no quarrel with.
    """
    bound = Measurement(">", 10000.0)  # pKi < 5 -> decisively inactive
    exact = Measurement("=", 10.0, is_exact=True)  # pKi 8.0 -> active
    evidence = PairEvidence.from_delta("W", in_a=[bound], added=[exact], removed=[bound])

    assert evidence.in_a == [bound], "A is unchanged by anything B did"
    assert evidence.in_b == [exact], "B holds only the exact value"
    assert evidence.added == [exact]
    assert evidence.removed == [bound]

    outcome = evaluate_pair(evidence, THETA)
    # training reads A alone and keeps A's inactive evidence
    assert (outcome.training_status, outcome.training_label) == ("ok", INACTIVE)
    # the increment alone reads active
    assert (outcome.increment_status, outcome.increment_label) == ("ok", ACTIVE)
    # and actual B is clean, so nothing is screened
    assert outcome.full_b_status == "ok"
    assert outcome.full_b_label == ACTIVE
    assert outcome.screened_because is None
    assert outcome.in_primary is True
    assert outcome.n_removed == 1


def test_the_union_of_a_and_additions_would_have_been_contradictory() -> None:
    """Pins the distinction: the old computation really did differ."""
    bound = Measurement(">", 10000.0)
    exact = Measurement("=", 10.0, is_exact=True)
    withdrawn = PairEvidence.from_delta("W", in_a=[bound], added=[exact], removed=[bound])
    retained = PairEvidence.from_delta("R", in_a=[bound], added=[exact])  # nothing removed

    assert full_b_status(withdrawn, THETA)[0] == "ok"
    assert full_b_status(retained, THETA)[0] == STATUS_CONFLICT
    # the two differ only in whether B still holds the bound
    assert evaluate_pair(withdrawn, THETA).in_primary is True
    assert evaluate_pair(retained, THETA).in_primary is False


def test_a_replacement_is_distinguished_from_a_withdrawal() -> None:
    """A correction replaces a value; a withdrawal removes one without replacing it."""
    old_exact = Measurement("=", 10.0, is_exact=True)  # pKi 8 -> active
    new_exact = Measurement("=", 20000.0, is_exact=True)  # pKi ~4.7 -> inactive
    replaced = PairEvidence.from_delta(
        "C", in_a=[old_exact], added=[new_exact], removed=[old_exact]
    )
    assert replaced.in_b == [new_exact], "only the replacement survives into B"
    outcome = evaluate_pair(replaced, THETA)
    # training keeps A's value whatever the replacement says
    assert (outcome.training_status, outcome.training_label) == ("ok", ACTIVE)
    assert (outcome.increment_status, outcome.increment_label) == ("ok", INACTIVE)
    # full B holds one exact value, so it is internally consistent
    assert outcome.full_b_status == "ok"
    assert outcome.full_b_label == INACTIVE
    assert (outcome.n_added, outcome.n_removed) == (1, 1)

    # a withdrawal with no replacement leaves B with nothing for this pair
    emptied = PairEvidence.from_delta("E", in_a=[old_exact], removed=[old_exact])
    assert emptied.in_b == []
    gone = evaluate_pair(emptied, THETA)
    assert gone.full_b_status == STATUS_NO_EVIDENCE
    assert gone.increment_status == STATUS_NO_EVIDENCE
    assert gone.in_primary is False and gone.in_sensitivity is False
    assert (gone.training_status, gone.training_label) == ("ok", ACTIVE)


def test_an_unreplaced_removal_never_alters_training() -> None:
    exact = Measurement("=", 10.0, is_exact=True)
    kept = PairEvidence.from_delta("K", in_a=[exact])
    emptied = PairEvidence.from_delta("K", in_a=[exact], removed=[exact])
    assert training_label(kept, THETA) == training_label(emptied, THETA)


def test_added_and_removed_are_multiset_differences() -> None:
    """Repeated identical observations must not collapse into one."""
    m = Measurement("=", 10.0, is_exact=True)
    evidence = PairEvidence(pair="M", in_a=[m, m], in_b=[m, m, m])
    assert len(evidence.added) == 1
    assert evidence.removed == []
    shrunk = PairEvidence(pair="M", in_a=[m, m, m], in_b=[m])
    assert len(shrunk.removed) == 2
    assert shrunk.added == []


# ======================== M4 eligibility: discordance (correction pass)


def test_discordant_added_exacts_are_excluded_from_scoring_but_kept_for_audit() -> None:
    """The confirmed defect: 1 nM and 10,000 nM entered both scoring branches.

    Four logs of disagreement between replicate exact values cannot arbitrate a
    ranking. M4 already flags this and withholds the pair from validation and
    test; the harness was scoring it.
    """
    evidence = PairEvidence(
        pair="D",
        in_a=[],
        in_b=[Measurement("=", 1.0, is_exact=True), Measurement("=", 10000.0, is_exact=True)],
    )
    outcome = evaluate_pair(evidence, THETA)

    # a label and a status still exist -- eligibility is a separate question
    assert outcome.increment_status == "ok"
    assert outcome.increment_label in (ACTIVE, INACTIVE)

    # but the pair is not scoreable, and is out of BOTH branches
    assert outcome.eligibility_reason == "discordant"
    assert outcome.is_scoreable is False
    assert outcome.in_primary is False
    assert outcome.in_sensitivity is False

    # and the statistics that justify the exclusion are preserved
    stats = outcome.increment_exact
    assert stats.n == 2
    assert stats.spread == pytest.approx(4.0)
    assert stats.minimum == pytest.approx(5.0)
    assert stats.maximum == pytest.approx(9.0)
    assert stats.median == pytest.approx(7.0)


def test_concordant_replicates_remain_scoreable() -> None:
    evidence = PairEvidence(
        pair="C",
        in_a=[],
        in_b=[Measurement("=", 10.0, is_exact=True), Measurement("=", 12.0, is_exact=True)],
    )
    outcome = evaluate_pair(evidence, THETA)
    assert outcome.increment_exact.spread < 1.0
    assert outcome.eligibility_reason is None
    assert outcome.is_scoreable is True
    assert outcome.in_primary is True


def test_the_discordance_threshold_is_m4s_and_is_strict() -> None:
    """M4 flags a spread *greater than* the threshold, not equal to it."""
    from seq2lead.asof.evaluation import exact_stats
    from seq2lead.endpoint.build import DEFAULT_DISCORDANCE_PKI

    assert DEFAULT_DISCORDANCE_PKI == 1.0
    exactly_one_log = [
        Measurement("=", 10.0, is_exact=True),
        Measurement("=", 100.0, is_exact=True),
    ]
    stats = exact_stats(exactly_one_log)
    assert stats.spread == pytest.approx(1.0)
    assert stats.is_discordant() is False, "a spread equal to the threshold is not discordant"
    more = [Measurement("=", 10.0, is_exact=True), Measurement("=", 200.0, is_exact=True)]
    assert exact_stats(more).is_discordant() is True


def test_a_contradiction_outranks_discordance_in_the_reason() -> None:
    """M4's precedence, reused rather than reimplemented."""
    evidence = PairEvidence(
        pair="X",
        in_a=[],
        in_b=[
            Measurement("=", 1.0, is_exact=True),
            Measurement("=", 10000.0, is_exact=True),
            Measurement(">", 1e9),  # pKi < 0, contradicts both
        ],
    )
    outcome = evaluate_pair(evidence, THETA)
    assert outcome.increment_status == STATUS_CONFLICT
    assert outcome.eligibility_reason == STATUS_CONFLICT, (
        "contradiction must outrank discordance: there is no label to score at all"
    )


def test_the_harness_reuses_m4s_exclusion_precedence() -> None:
    """A second incomplete implementation is what this avoids."""
    import inspect

    from seq2lead.asof import evaluation

    source = inspect.getsource(evaluation)
    assert "from seq2lead.endpoint.build import" in source
    assert "exclusion_reason" in source
    assert "DEFAULT_DISCORDANCE_PKI" in source
    assert "EXCLUSION_PRECEDENCE = (" not in source, "the precedence was re-declared locally"


def test_every_pair_is_reported_even_when_not_scoreable() -> None:
    """Exclusion from scoring is not exclusion from the record."""
    pairs = [
        PairEvidence.from_delta("clean", [], [BOUND_ACTIVE]),
        PairEvidence(
            "discordant",
            in_a=[],
            in_b=[Measurement("=", 1.0, is_exact=True), Measurement("=", 10000.0, is_exact=True)],
        ),
    ]
    result = evaluate(pairs, THETA)
    assert result[Branch.PRIMARY] == ["clean"]
    assert result[Branch.SENSITIVITY] == ["clean"]
    assert result["ineligible"] == ["discordant"]
    assert result["ineligible_by_reason"] == {"discordant": 1}
    assert {o["pair"] for o in result["outcomes"]} == {"clean", "discordant"}
    discordant = next(o for o in result["outcomes"] if o["pair"] == "discordant")
    assert discordant["increment_exact"]["spread_pki"] == pytest.approx(4.0)
    assert discordant["is_scoreable"] is False
    assert result["discordance_pki"] == 1.0


def test_labels_and_statuses_are_reported_separately_from_eligibility() -> None:
    evidence = PairEvidence(
        pair="D",
        in_a=[],
        in_b=[Measurement("=", 1.0, is_exact=True), Measurement("=", 10000.0, is_exact=True)],
    )
    body = evaluate_pair(evidence, THETA).to_dict()
    for key in ("increment_status", "increment_label", "full_b_status", "training_label"):
        assert key in body
    for key in ("eligibility_reason", "is_scoreable"):
        assert key in body
    assert body["increment_status"] == "ok" and body["is_scoreable"] is False
