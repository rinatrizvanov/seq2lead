"""Branch-specific cohort accounting, the audit export, and the memory safeguards.

The first revision of the KI aggregation pooled every eligible pair into one set
of cohort figures and recorded consistency-branch membership separately. So the
published target coverage, class balance and rankability described the
**pre-screen pool** while being read as the cohort — and the two differ in a way
that matters, which the first test here demonstrates.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from seq2lead.asof.ki_aggregation import (
    CONSISTENCY_BRANCHES,
    INCREMENT_ARMS,
    MIN_PER_CLASS,
    AlignmentError,
    ArmCounts,
    ShardTooLarge,
    aggregate_shard,
    training_only_labels,
)

THRESHOLD = 6.0
ONE_TARGET = "c" * 64


def row(
    inchikey,
    *,
    seq=ONE_TARGET,
    relation="=",
    value="1",
    pmid="1",
    release="BindingDB/202601/all",
    raw="1",
    mtype="KI",
    entry_doi=None,
    rsid=None,
):
    return {
        "inchikey": inchikey,
        "sequence_sha256": seq,
        "pmid": pmid,
        "ph_text": "7.4",
        "temp_c_text": "25",
        "curation_source": "BindingDB",
        "measurement_type": mtype,
        "relation": relation,
        "value_text": value,
        "entry_doi": entry_doi,
        "reactant_set_id": rsid,
        "source_release": release,
        "raw_row": raw,
    }


def run(a_rows, b_rows, *, excluded=frozenset(), threshold=THRESHOLD):
    primary = ArmCounts(name="declared_increment")
    sens = ArmCounts(name="cross_slot_excluded")
    records: list[dict] = []
    result = aggregate_shard(
        a_rows,
        b_rows,
        shard=0,
        threshold=threshold,
        excluded_locators=excluded,
        primary=primary,
        sensitivity=sens,
        pair_records=records,
    )
    return result, primary, sens, records


def contradicted_active_pairs(n: int) -> tuple[list[dict], list[dict]]:
    """n pairs: increment reads `ok/active`, full-B is self-contradictory.

    A holds a censored bound `> 10000 nM` (pKi < 5); B keeps it and adds an exact
    at 1 nM (pKi 9). The increment is a clean active; actual B holds an exact
    outside its own censored interval, so full-B is `exact_bound_conflict` and the
    pair is screened out of the primary branch while remaining eligible.
    """
    a_rows, b_rows = [], []
    for i in range(n):
        key = f"ACT{i:011d}-AAAAAAAAAA-A"
        a_rows.append(row(key, relation=">", value="10000", raw=f"1{i}"))
        b_rows.append(row(key, relation=">", value="10000", raw=f"1{i}"))
        b_rows.append(row(key, value="1", pmid="999", release="BindingDB/202609/all", raw=f"2{i}"))
    return a_rows, b_rows


def clean_inactive_pairs(n: int) -> list[dict]:
    return [
        row(
            f"INA{i:011d}-AAAAAAAAAA-A",
            value="100000",
            pmid="888",
            release="BindingDB/202609/all",
            raw=f"3{i}",
        )
        for i in range(n)
    ]


# ======================================================= the reproduced defect


def test_the_screened_cohort_is_not_rankable_when_its_actives_are_screened_out() -> None:
    """The discriminator. Five contradicted actives and five clean inactives, one target.

    Pre-screen the target looks rankable at five per class. In the screened
    primary branch it has **no actives at all**, so it is not rankable there — and
    the first revision reported the pre-screen figure as the cohort.
    """
    a_rows, b_rows = contradicted_active_pairs(5)
    b_rows += clean_inactive_pairs(5)
    _, primary, _, _ = run(a_rows, b_rows)
    body = primary.as_dict()

    unscreened = body["branches"]["unscreened_sensitivity"]
    screened = body["branches"]["screened_primary"]

    # the pool: 5 and 5, and rankable at five per class
    assert unscreened["admitted_pairs"] == 10
    assert unscreened["class_balance"] == {
        "active": 5,
        "inactive": 5,
        "decided": 10,
        "positive_rate": 0.5,
        "minority_count": 5,
    }
    assert unscreened["rankability"]["rankable_at"]["5"] == 1

    # the cohort: the actives are gone, so nothing is rankable
    assert screened["admitted_pairs"] == 5
    assert screened["class_balance"]["active"] == 0
    assert screened["class_balance"]["inactive"] == 5
    assert screened["rankability"]["rankable_at"]["5"] == 0
    assert all(screened["rankability"]["rankable_at"][str(n)] == 0 for n in MIN_PER_CLASS)
    assert screened["rankability"]["targets_single_class_only"] == 1

    # and the screen's effect is reported rather than left to be subtracted
    effect = body["branches"]["effect_of_the_screen"]
    assert effect["pairs_removed"] == 5
    assert effect["rankable_at_5_before"] == 1
    assert effect["rankable_at_5_after"] == 0
    assert body["branches"]["screened_because"] == {"exact_bound_conflict": 5}


def test_the_pre_screen_pool_is_kept_under_a_name_that_says_so() -> None:
    """The old figures were not wrong, they were mislabelled."""
    a_rows, b_rows = contradicted_active_pairs(3)
    b_rows += clean_inactive_pairs(3)
    _, primary, _, _ = run(a_rows, b_rows)
    body = primary.as_dict()
    assert body["pre_screen_pool"]["scoreable_pairs"] == 6
    assert (
        body["pre_screen_pool"]["scoreable_pairs"]
        == (body["branches"]["unscreened_sensitivity"]["admitted_pairs"])
    )
    assert "the pool the cohort is drawn FROM" in body["pre_screen_pool"]["note"]


def test_eligibility_exclusions_are_branch_independent() -> None:
    """Discordance is decided before the screen, so it is counted once."""
    a_rows: list[dict] = []
    b_rows = [
        row(
            "DIS00000000000-AAAAAAAAAA-A",
            value="1",
            pmid="1",
            release="BindingDB/202609/all",
            raw="1",
        ),
        row(
            "DIS00000000000-AAAAAAAAAA-A",
            value="100000",
            pmid="2",
            release="BindingDB/202609/all",
            raw="2",
        ),
    ]
    _, primary, _, _ = run(a_rows, b_rows)
    body = primary.as_dict()
    assert body["excluded_pairs_by_reason"] == {"discordant": 1}
    assert body["branches"]["unscreened_sensitivity"]["admitted_pairs"] == 0
    assert body["branches"]["screened_primary"]["admitted_pairs"] == 0
    assert "before the consistency screen" in body["eligibility_is_branch_independent"]


def test_all_four_cells_are_reported() -> None:
    """Two increment arms by two consistency branches."""
    assert set(INCREMENT_ARMS) == {"declared_increment", "cross_slot_excluded"}
    assert set(CONSISTENCY_BRANCHES) == {"unscreened_sensitivity", "screened_primary"}
    a_rows, b_rows = contradicted_active_pairs(2)
    b_rows += clean_inactive_pairs(2)
    _, primary, sens, _ = run(a_rows, b_rows)
    for arm in (primary, sens):
        body = arm.as_dict()
        for branch in CONSISTENCY_BRANCHES:
            cell = body["branches"][branch]
            assert "admitted_pairs" in cell
            assert "class_balance" in cell
            assert "coverage" in cell
            assert "rankability" in cell
            assert cell["branch"] == branch


def test_screened_primary_is_a_subset_of_the_unscreened_pool() -> None:
    a_rows, b_rows = contradicted_active_pairs(4)
    b_rows += clean_inactive_pairs(6)
    _, primary, _, _ = run(a_rows, b_rows)
    body = primary.as_dict()
    u, s = body["branches"]["unscreened_sensitivity"], body["branches"]["screened_primary"]
    assert s["admitted_pairs"] <= u["admitted_pairs"]
    assert s["coverage"]["distinct_targets"] <= u["coverage"]["distinct_targets"]
    for n in MIN_PER_CLASS:
        assert s["rankability"]["rankable_at"][str(n)] <= u["rankability"]["rankable_at"][str(n)]
    assert u["admitted_pairs"] - s["admitted_pairs"] == body["branches"]["screened_out_of_primary"]


# ============================================================== the pair audit


def test_the_audit_explains_every_decision_for_both_arms() -> None:
    a_rows, b_rows = contradicted_active_pairs(1)
    b_rows += clean_inactive_pairs(1)
    _, _, _, records = run(a_rows, b_rows)
    assert len(records) == 2
    for rec in records:
        for arm in INCREMENT_ARMS:
            view = rec["arms"][arm]
            if not view["has_increment"]:
                continue
            for key in (
                "increment_status",
                "increment_label",
                "increment_exact",
                "eligibility_reason",
                "is_scoreable",
                "screened_because",
                "branch_membership",
                "stratum",
            ):
                assert key in view, f"{arm} view is missing {key}"
            assert set(view["branch_membership"]) == set(CONSISTENCY_BRANCHES)
        # full-B reads in_b, which both arms share, so it is reported ONCE at the
        # top level rather than duplicated per arm
        assert "full_b_audit" in rec
        for key in ("status", "label", "exact", "would_be_screened", "has_evidence"):
            assert key in rec["full_b_audit"], key
        assert "training_from_a" in rec
        assert "increment_locators" in rec
        assert "removed_locators" in rec
        assert "withheld_locators" in rec

    contradicted = next(r for r in records if r["pair"].startswith("ACT"))
    view = contradicted["arms"]["declared_increment"]
    assert view["increment_label"] == "active"
    assert contradicted["full_b_audit"]["status"] == "exact_bound_conflict"
    assert contradicted["full_b_audit"]["would_be_screened"] is True
    assert view["screened_because"] == "exact_bound_conflict"
    assert view["branch_membership"] == {
        "unscreened_sensitivity": True,
        "screened_primary": False,
    }


def test_an_audit_record_survives_a_fully_withheld_increment() -> None:
    """The pairs the withholding policy acts on are exactly the ones to keep."""
    a_rows = [row("WITHHELD0000AA-AAAAAAAAAA-A", value="10", entry_doi="10.7270/C", raw="1")]
    b_rows = [
        row(
            "WITHHELD0000AA-AAAAAAAAAA-A",
            value="11",
            entry_doi="10.7270/C",
            release="BindingDB/202609/all",
            raw="2",
        )
    ]
    _, primary, _, records = run(a_rows, b_rows)
    assert primary.pairs == 0, "no increment, so the pair enters no arm"
    assert len(records) == 1, "but its audit record is retained"
    rec = records[0]
    assert rec["n_withheld_corrections"] == 1
    assert rec["arms"]["declared_increment"]["has_increment"] is False
    assert "withheld" in rec["arms"]["declared_increment"]["absence_reason"]
    assert rec["withheld_locators"]
    # the readings survive even though no arm admitted the pair
    assert rec["training_from_a"]["has_evidence"] is True
    assert rec["full_b_audit"]["has_evidence"] is True


def test_an_audit_record_survives_a_fully_excluded_sensitivity_increment() -> None:
    a_rows = [row("XSLOT00000000A-AAAAAAAAAA-A", pmid="", value="183", raw="1")]
    b_rows = [
        row(
            "XSLOT00000000A-AAAAAAAAAA-A",
            pmid="999",
            value="183",
            release="BindingDB/202609/all",
            raw="2",
        )
    ]
    _, primary, sens, records = run(a_rows, b_rows, excluded=frozenset({"BindingDB/202609/all#2"}))
    assert primary.pairs == 1
    assert sens.pairs == 0
    assert sens.pairs_with_no_increment == 1
    rec = records[0]
    assert rec["arms"]["declared_increment"]["has_increment"] is True
    assert rec["arms"]["cross_slot_excluded"]["has_increment"] is False
    assert rec["increment_locators_excluded_by_sensitivity"] == ["BindingDB/202609/all#2"]


def test_summary_counts_reconstruct_from_the_exported_audit() -> None:
    """If the audit cannot rebuild the summary, one of them is wrong."""
    a_rows, b_rows = contradicted_active_pairs(3)
    b_rows += clean_inactive_pairs(4)
    b_rows += [
        row(
            "DIS00000000000-AAAAAAAAAA-A",
            value="1",
            pmid="7",
            release="BindingDB/202609/all",
            raw="7",
        ),
        row(
            "DIS00000000000-AAAAAAAAAA-A",
            value="100000",
            pmid="8",
            release="BindingDB/202609/all",
            raw="8",
        ),
    ]
    _, primary, sens, records = run(a_rows, b_rows)

    for arm_name, arm in (("declared_increment", primary), ("cross_slot_excluded", sens)):
        views = [r["arms"][arm_name] for r in records if r["arms"][arm_name]["has_increment"]]
        body = arm.as_dict()
        assert len(views) == body["pairs_with_any_increment"], arm_name
        scoreable = [v for v in views if v["is_scoreable"]]
        assert len(scoreable) == body["branches"]["unscreened_sensitivity"]["admitted_pairs"]
        in_primary = [v for v in views if v["branch_membership"]["screened_primary"]]
        assert len(in_primary) == body["branches"]["screened_primary"]["admitted_pairs"]
        from collections import Counter

        assert Counter(v["increment_label"] for v in scoreable) == Counter(
            body["branches"]["unscreened_sensitivity"]["label_counts"]
        )
        assert Counter(v["increment_label"] for v in in_primary) == Counter(
            body["branches"]["screened_primary"]["label_counts"]
        )
        excluded = Counter(v["eligibility_reason"] for v in views if not v["is_scoreable"])
        assert excluded == Counter(body["excluded_pairs_by_reason"])


def test_a_locator_misalignment_raises_instead_of_truncating() -> None:
    """`zip(strict=False)` would silently under-apply the exclusion set."""
    from unittest.mock import patch

    from seq2lead.asof import ki_aggregation

    a_rows: list[dict] = []
    b_rows = [
        row("ALIGN000000000-AAAAAAAAAA-A", value="5", release="BindingDB/202609/all", raw="1"),
        row(
            "ALIGN000000000-AAAAAAAAAA-A",
            value="6",
            pmid="2",
            release="BindingDB/202609/all",
            raw="2",
        ),
    ]
    real = ki_aggregation.build_increments

    def short_locators(*args, **kwargs):
        bridged, diff = real(*args, **kwargs)
        for item in bridged:
            del item.provenance.increment_locators[1:]  # drop the tail
        return bridged, diff

    with patch.object(ki_aggregation, "build_increments", side_effect=short_locators):
        with pytest.raises(AlignmentError, match="locators"):
            run(a_rows, b_rows)


# ======================================================== the memory safeguards


def _write_shards(tmp_path: Path, n_a: int, n_b: int) -> tuple[Path, Path]:
    a_dir, b_dir = tmp_path / "sa", tmp_path / "sb"
    a_dir.mkdir(parents=True)
    b_dir.mkdir(parents=True)
    (a_dir / "a-0000.jsonl").write_text(
        "".join(json.dumps(row(f"P{i:013d}-AAAAAAAAAA-A", raw=str(i))) + "\n" for i in range(n_a)),
        encoding="utf-8",
    )
    (b_dir / "b-0000.jsonl").write_text(
        "".join(json.dumps(row(f"P{i:013d}-AAAAAAAAAA-A", raw=str(i))) + "\n" for i in range(n_b)),
        encoding="utf-8",
    )
    return a_dir, b_dir


def test_an_oversized_pair_shard_is_refused_before_being_materialised(tmp_path) -> None:
    from unittest.mock import patch

    from seq2lead.asof import ki_aggregation

    a_dir, b_dir = _write_shards(tmp_path, 30, 30)
    with patch.object(
        ki_aggregation,
        "_read_shard_bounded",
        side_effect=AssertionError("the budget must be enforced before materialising a shard"),
    ):
        with pytest.raises(ShardTooLarge, match="over the declared budget"):
            ki_aggregation.aggregate(a_dir, b_dir, 1, threshold=THRESHOLD, budget=10)


def test_a_lying_preliminary_counter_cannot_bypass_the_budget(tmp_path) -> None:
    """Belt to the pre-check's braces: the reads are themselves bounded."""
    from unittest.mock import patch

    from seq2lead.asof import ki_aggregation

    a_dir, b_dir = _write_shards(tmp_path, 30, 30)
    with patch.object(ki_aggregation, "count_rows", return_value=0):
        with pytest.raises(ShardTooLarge, match="refusing to read further"):
            ki_aggregation.aggregate(a_dir, b_dir, 1, threshold=THRESHOLD, budget=10)


def test_the_combined_budget_is_enforced_across_both_sides(tmp_path) -> None:
    """A shard under budget on each side alone can exceed it combined."""
    from unittest.mock import patch

    from seq2lead.asof import ki_aggregation

    a_dir, b_dir = _write_shards(tmp_path, 8, 8)
    with patch.object(ki_aggregation, "count_rows", return_value=0):
        with pytest.raises(ShardTooLarge):
            ki_aggregation.aggregate(a_dir, b_dir, 1, threshold=THRESHOLD, budget=12)


def test_a_shard_within_budget_still_processes(tmp_path) -> None:
    from seq2lead.asof import ki_aggregation

    a_dir, b_dir = _write_shards(tmp_path, 2, 2)
    agg = ki_aggregation.aggregate(a_dir, b_dir, 1, threshold=THRESHOLD, budget=100)
    assert agg.shards_processed == 1
    assert agg.shard_observations_max == 4
    body = agg.as_dict()
    assert body["resources"]["budget_enforced_before_materialising"] is True


# ============================================= the strengthened A-independence check


def test_equal_training_labels_with_different_evidence_do_not_match() -> None:
    """The weakness in the first check: status, label and a count can all agree.

    Two actives at pKi 9 and two at pKi 8.7 both read `ok/active` with n=2. A
    digest over status, label and count alone would call them identical; the
    canonical evidence must distinguish them.
    """
    one = [
        row("SAME0000000000-AAAAAAAAAA-A", value="1", raw="1"),
        row("SAME0000000000-AAAAAAAAAA-A", value="1", pmid="2", raw="2"),
    ]
    two = [
        row("SAME0000000000-AAAAAAAAAA-A", value="2", raw="1"),
        row("SAME0000000000-AAAAAAAAAA-A", value="2", pmid="2", raw="2"),
    ]
    d1, c1 = training_only_labels(one, THRESHOLD)
    d2, c2 = training_only_labels(two, THRESHOLD)
    assert c1 == c2, "the labels and counts agree, which is the point"
    assert d1 != d2, "but the digest must notice the evidence changed"


def test_a_discordance_change_alone_changes_the_digest() -> None:
    """Training statistics the reading depends on, not just its label."""
    tight = [
        row("DSC0000000000A-AAAAAAAAAA-A", value="1", raw="1"),
        row("DSC0000000000A-AAAAAAAAAA-A", value="1", pmid="2", raw="2"),
    ]
    wide = [
        row("DSC0000000000A-AAAAAAAAAA-A", value="1", raw="1"),
        row("DSC0000000000A-AAAAAAAAAA-A", value="100", pmid="2", raw="2"),
    ]
    d_tight, _ = training_only_labels(tight, THRESHOLD)
    d_wide, _ = training_only_labels(wide, THRESHOLD)
    assert d_tight != d_wide


def test_a_relation_change_alone_changes_the_digest() -> None:
    """An exact and a bound at the same magnitude are different evidence."""
    exact = [row("REL0000000000A-AAAAAAAAAA-A", relation="=", value="1", raw="1")]
    bound = [row("REL0000000000A-AAAAAAAAAA-A", relation="<", value="1", raw="1")]
    d_exact, _ = training_only_labels(exact, THRESHOLD)
    d_bound, _ = training_only_labels(bound, THRESHOLD)
    assert d_exact != d_bound


def test_b_only_pairs_stay_outside_the_comparison() -> None:
    a_rows = [row("HASA0000000000-AAAAAAAAAA-A", value="1", raw="1")]
    b_rows = [
        row("HASA0000000000-AAAAAAAAAA-A", value="1", raw="1"),
        row(
            "BONLY000000000-AAAAAAAAAA-A",
            value="5",
            pmid="9",
            release="BindingDB/202609/all",
            raw="9",
        ),
    ]
    result, _, _, _ = run(a_rows, b_rows)
    assert result.b_only_pairs_excluded_from_training == 1
    expected, _ = training_only_labels(a_rows, THRESHOLD)
    assert result.training_digest == expected


def test_the_strengthened_digest_still_ignores_b(tmp_path) -> None:
    """Whatever B does, A's digest must not move."""
    a_rows = [
        row("AAA0000000000A-AAAAAAAAAA-A", value="1", raw="1"),
        row("AAA0000000000A-AAAAAAAAAA-A", relation=">", value="10000", pmid="2", raw="2"),
        row("BBB0000000000B-AAAAAAAAAA-A", value="100000", raw="3"),
    ]
    expected, _ = training_only_labels(a_rows, THRESHOLD)
    for b_rows in (
        [],
        list(a_rows),
        [
            row(
                "AAA0000000000A-AAAAAAAAAA-A",
                value="7",
                pmid="77",
                release="BindingDB/202609/all",
                raw="77",
            )
        ],
        clean_inactive_pairs(3),
    ):
        result, _, _, _ = run(a_rows, b_rows)
        assert result.training_digest == expected
        assert result.training_only_digest == expected


# ============================== audit readings survive a vanished increment


def test_a_fully_withheld_increment_does_not_erase_as_historical_audit() -> None:
    """The discriminator for the closeout's first defect.

    A pair with valid, clean, *active* A evidence whose single addition is
    withheld against a linked correction candidate. It enters no increment arm —
    correctly — but its historical A status, label and exact statistics are facts
    about snapshot A and must survive. An earlier version took them from whichever
    arm produced an outcome, so they came back `None` for exactly this case, which
    is §7a example (b): the case where training *must* keep A's value.
    """
    a_rows = [
        row("WITHHELD0000AA-AAAAAAAAAA-A", value="1", entry_doi="10.7270/C", raw="1"),
        row("WITHHELD0000AA-AAAAAAAAAA-A", value="2", entry_doi="10.7270/C", pmid="2", raw="2"),
    ]
    b_rows = [
        # the first observation survives unchanged; the second is corrected
        row("WITHHELD0000AA-AAAAAAAAAA-A", value="1", entry_doi="10.7270/C", raw="1"),
        row(
            "WITHHELD0000AA-AAAAAAAAAA-A",
            value="3",
            entry_doi="10.7270/C",
            pmid="2",
            release="BindingDB/202609/all",
            raw="3",
        ),
    ]
    _, primary, sens, records = run(a_rows, b_rows)

    assert primary.pairs == 0, "the only addition was withheld, so no arm admits the pair"
    assert sens.pairs == 0
    assert len(records) == 1
    rec = records[0]
    assert rec["n_withheld_corrections"] == 1

    # A's evidence is historical and is preserved in full
    tr = rec["training_from_a"]
    assert tr["has_evidence"] is True
    assert tr["status"] == "ok"
    assert tr["label"] == "active", "A's two exacts are pKi 9 and 8.7"
    assert tr["exact"]["n_exact"] == 2
    assert tr["exact"]["median_pki"] is not None
    assert tr["is_discordant"] is False
    assert tr["source"].startswith("in_a")

    # and actual B is read directly too, not borrowed from an arm
    b = rec["full_b_audit"]
    assert b["has_evidence"] is True
    assert b["status"] == "ok"
    assert b["exact"]["n_exact"] == 2
    assert b["would_be_screened"] is False

    # the absence is named, not blank
    for arm in INCREMENT_ARMS:
        view = rec["arms"][arm]
        assert view["has_increment"] is False
        assert view["entered_this_arm"] is False
        assert view["absence_reason"] == "all_additions_withheld_as_correction_candidates"


def test_the_three_absence_reasons_are_distinguished() -> None:
    """No additions, all withheld, all excluded by the sensitivity."""
    # (1) no additions at all -- only a removal
    a_only = [row("NOADD000000000-AAAAAAAAAA-A", value="1", raw="1")]
    _, _, _, recs = run(a_only, [])
    assert recs[0]["arms"]["declared_increment"]["absence_reason"] == ("no_additions_at_this_pair")

    # (2) every addition withheld against a correction
    a_rows = [row("ALLWITH000000A-AAAAAAAAAA-A", value="10", entry_doi="10.7270/W", raw="1")]
    b_rows = [
        row(
            "ALLWITH000000A-AAAAAAAAAA-A",
            value="11",
            entry_doi="10.7270/W",
            release="BindingDB/202609/all",
            raw="2",
        )
    ]
    _, _, _, recs = run(a_rows, b_rows)
    assert recs[0]["arms"]["declared_increment"]["absence_reason"] == (
        "all_additions_withheld_as_correction_candidates"
    )

    # (3) the declared arm has an increment; the sensitivity excludes all of it
    a_rows = [row("ALLEXCL00000AA-AAAAAAAAAA-A", pmid="", value="183", raw="1")]
    b_rows = [
        row(
            "ALLEXCL00000AA-AAAAAAAAAA-A",
            pmid="999",
            value="183",
            release="BindingDB/202609/all",
            raw="2",
        )
    ]
    _, _, _, recs = run(a_rows, b_rows, excluded=frozenset({"BindingDB/202609/all#2"}))
    rec = recs[0]
    assert rec["arms"]["declared_increment"]["has_increment"] is True
    assert rec["arms"]["cross_slot_excluded"]["absence_reason"] == (
        "all_remaining_additions_excluded_by_cross_slot_sensitivity"
    )


def test_the_audit_readings_do_not_depend_on_either_arm() -> None:
    """Same A and B evidence, different exclusion sets: the readings must not move."""
    a_rows = [row("STABLE000000AA-AAAAAAAAAA-A", pmid="", value="1", raw="1")]
    b_rows = [
        row("STABLE000000AA-AAAAAAAAAA-A", pmid="", value="1", raw="1"),
        row(
            "STABLE000000AA-AAAAAAAAAA-A",
            pmid="999",
            value="2",
            release="BindingDB/202609/all",
            raw="2",
        ),
    ]
    baseline = None
    for excluded in (frozenset(), frozenset({"BindingDB/202609/all#2"})):
        _, _, _, recs = run(a_rows, b_rows, excluded=excluded)
        readings = (recs[0]["training_from_a"], recs[0]["full_b_audit"])
        if baseline is None:
            baseline = readings
        else:
            assert readings == baseline, "an audit reading moved with the exclusion set"


def test_a_screened_full_b_is_recorded_even_with_no_increment() -> None:
    """A contradicted pair whose increment was withheld still reports the contradiction."""
    a_rows = [
        row("SCRNWITH0000AA-AAAAAAAAAA-A", relation=">", value="10000", raw="1"),
        row("SCRNWITH0000AA-AAAAAAAAAA-A", value="1", entry_doi="10.7270/S", pmid="2", raw="2"),
    ]
    b_rows = [
        row("SCRNWITH0000AA-AAAAAAAAAA-A", relation=">", value="10000", raw="1"),
        row(
            "SCRNWITH0000AA-AAAAAAAAAA-A",
            value="2",
            entry_doi="10.7270/S",
            pmid="2",
            release="BindingDB/202609/all",
            raw="3",
        ),
    ]
    _, primary, _, recs = run(a_rows, b_rows)
    assert primary.pairs == 0, "the addition was withheld as a correction"
    rec = recs[0]
    assert rec["full_b_audit"]["status"] == "exact_bound_conflict"
    assert rec["full_b_audit"]["would_be_screened"] is True
    assert rec["training_from_a"]["status"] == "exact_bound_conflict"
