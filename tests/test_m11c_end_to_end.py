"""Matcher to evaluation, end to end, on synthetic snapshots.

Each scenario starts from two row sets -- the shape real snapshots arrive in --
runs the provenance-aware matcher, bridges its classified observations into the
harness, and checks all three readings plus scoring eligibility. Nothing is
fitted and no dataset is downloaded.

The point of going end to end is that the two halves disagreed in ways neither
half's own tests could see: the harness cannot know which additions are the
second half of a correction, and the matcher cannot know what a label is.
"""

from __future__ import annotations

import json

import pytest

from seq2lead.asof.bridge import build_increments
from seq2lead.asof.evaluation import Measurement, PairEvidence, evaluate_pair
from seq2lead.endpoint.build import STATUS_CONFLICT, STATUS_NO_EVIDENCE
from seq2lead.endpoint.interval import ACTIVE, INACTIVE

THETA = 6.0


def row(
    *,
    compound="K1",
    target="S1",
    pmid="1",
    ph="7.4",
    temp="25",
    source="BindingDB",
    mtype="KI",
    relation="=",
    value="10",
    entry_doi=None,
    rsid=None,
):
    return {
        "inchikey": compound,
        "sequence_sha256": target,
        "pmid": pmid,
        "ph_text": ph,
        "temp_c_text": temp,
        "curation_source": source,
        "measurement_type": mtype,
        "relation": relation,
        "value_text": value,
        "entry_doi": entry_doi,
        "reactant_set_id": rsid,
    }


def bridge_one(earlier, later):
    """Run the full path and return (outcome, provenance, diff) for the only pair."""
    increments, diff = build_increments(earlier, later)
    assert len(increments) == 1, f"expected one pair, got {len(increments)}"
    bridged = increments[0]
    return evaluate_pair(bridged.evidence, THETA), bridged.provenance, diff


# ============================================================ 1. unchanged


def test_unchanged_evidence_yields_no_increment_and_keeps_training() -> None:
    outcome, prov, diff = bridge_one([row()], [row()])
    assert diff.summary()["unchanged_observations"] == 1
    assert prov.n_added_observations == 0
    assert prov.n_removed_observations == 0
    assert outcome.increment_status == STATUS_NO_EVIDENCE
    assert outcome.in_primary is False and outcome.in_sensitivity is False
    assert (outcome.training_status, outcome.training_label) == ("ok", ACTIVE)


# ====================== 2. identical value, different publication or context


def test_the_same_value_under_a_different_publication_is_a_new_observation() -> None:
    """A slot fixes publication and assay context, so this is not a duplicate."""
    outcome, prov, diff = bridge_one([row(pmid="111")], [row(pmid="111"), row(pmid="222")])
    assert diff.summary()["new_slots"] == 1, "the second publication is its own slot"
    assert prov.n_added_observations == 1
    assert len(prov.slots) >= 2, "both slots are carried into the provenance"
    assert outcome.increment_status == "ok"
    assert outcome.increment_label == ACTIVE


@pytest.mark.parametrize(("field", "value"), [("ph", "6.0"), ("temp", "37"), ("source", "ChEMBL")])
def test_the_same_value_under_different_assay_context_is_a_new_observation(field, value) -> None:
    base = row()
    other = row(**{field: value})
    outcome, prov, diff = bridge_one([base], [base, other])
    assert diff.summary()["new_slots"] == 1
    assert prov.n_added_observations == 1
    assert outcome.increment_status == "ok"


def test_the_same_value_in_the_same_slot_is_not_a_new_observation() -> None:
    """The control for the two above: identical slot and value means unchanged."""
    outcome, prov, _ = bridge_one([row()], [row()])
    assert prov.n_added_observations == 0
    assert outcome.increment_status == STATUS_NO_EVIDENCE


# ======================================================== 3. count increases


def test_a_count_increase_becomes_one_added_observation() -> None:
    outcome, prov, diff = bridge_one([row(), row()], [row(), row(), row()])
    assert diff.summary()["unchanged_observations"] == 2
    assert diff.summary()["added_observations"] == 1
    assert prov.n_added_observations == 1
    assert outcome.increment_status == "ok"
    assert outcome.increment_label == ACTIVE
    assert outcome.stratum == "recurrent"


# ============================================ 4. withdrawals and replacements


def test_a_withdrawal_leaves_training_intact_and_b_clean() -> None:
    """A holds a decisive inactive bound; B drops it and adds an exact active."""
    bound = row(relation=">", value="10000")
    exact = row(relation="=", value="10")
    outcome, prov, diff = bridge_one([bound], [exact])

    assert diff.summary()["removed_slots"] == 0, "same slot, so this is a value change"
    assert prov.n_removed_observations == 1
    assert prov.n_added_observations == 1
    assert (outcome.training_status, outcome.training_label) == ("ok", INACTIVE)
    assert outcome.increment_label == ACTIVE
    # B holds only the exact value, so B is internally consistent
    assert outcome.full_b_status == "ok"
    assert outcome.full_b_label == ACTIVE
    assert outcome.screened_because is None
    assert outcome.in_primary is True


def test_a_fully_withdrawn_pair_has_no_b_evidence() -> None:
    outcome, prov, diff = bridge_one([row()], [])
    assert diff.summary()["removed_slots"] == 1
    assert prov.n_removed_observations == 1
    assert outcome.full_b_status == STATUS_NO_EVIDENCE
    assert outcome.in_primary is False
    assert (outcome.training_status, outcome.training_label) == ("ok", ACTIVE)


def test_a_retained_bound_really_does_contradict() -> None:
    """Contrast with the withdrawal: if B keeps both rows the screen is right."""
    bound = row(relation=">", value="10000", pmid="111")
    exact = row(relation="=", value="10", pmid="222")
    outcome, _prov, _diff = bridge_one([bound], [bound, exact])
    assert outcome.full_b_status == STATUS_CONFLICT
    assert outcome.screened_because == STATUS_CONFLICT
    assert outcome.in_primary is False
    assert outcome.in_sensitivity is True, "the unscreened branch still admits it"


# =================================== 5. provisional correction candidates


def test_a_correction_candidate_never_becomes_an_evaluation_measurement() -> None:
    """The declared policy, enforced across the bridge rather than in prose."""
    outcome, prov, diff = bridge_one(
        [row(value="10", entry_doi="10.7270/Q")],
        [row(value="20000", entry_doi="10.7270/Q")],
    )
    assert diff.summary()["correction_candidates"] == 1
    assert diff.correction_candidates[0].provisional is True
    assert prov.n_correction_candidates_withheld == 1
    assert prov.n_added_observations == 0, "the corrected value entered the increment"
    assert outcome.increment_status == STATUS_NO_EVIDENCE
    assert outcome.in_primary is False and outcome.in_sensitivity is False
    # and training is untouched by the correction
    assert (outcome.training_status, outcome.training_label) == ("ok", ACTIVE)


def test_an_unresolved_addition_is_admitted_but_flagged() -> None:
    """No release-stable identifier, so no link; the row is new evidence, flagged."""
    outcome, prov, diff = bridge_one([row(value="10")], [row(value="20000")])
    assert diff.summary()["correction_candidates"] == 0
    assert diff.summary()["unresolved_additions"] == 1
    assert prov.n_correction_candidates_withheld == 0
    assert prov.n_unresolved_additions == 1
    assert prov.n_added_observations == 1
    assert outcome.increment_status == "ok"
    assert outcome.increment_label == INACTIVE


def test_an_identifier_conflict_is_counted_against_the_pair() -> None:
    outcome, prov, diff = bridge_one(
        [row(value="10", entry_doi="10.7270/AAA", rsid="5")],
        [row(value="20000", entry_doi="10.7270/BBB", rsid="5")],
    )
    assert diff.summary()["identifier_conflicts"] == 1
    assert prov.n_identifier_conflicts == 1
    assert prov.n_correction_candidates_withheld == 0, "a conflict is not a correction"
    assert prov.n_added_observations == 1, "with no link, the row is new evidence"
    assert outcome.increment_status == "ok"


# ========================== 6. contradictory and discordant increments


def test_a_contradictory_increment_is_ineligible() -> None:
    outcome, _prov, _diff = bridge_one(
        [],
        [row(relation="=", value="10", pmid="111"), row(relation=">", value="1e9", pmid="111")],
    )
    assert outcome.increment_status == STATUS_CONFLICT
    assert outcome.eligibility_reason == STATUS_CONFLICT
    assert outcome.is_scoreable is False
    assert outcome.in_primary is False and outcome.in_sensitivity is False


def test_a_discordant_increment_is_ineligible_but_audited() -> None:
    """Exact Ki of 1 and 10,000 nM, four logs apart, through the full path."""
    outcome, prov, _diff = bridge_one(
        [],
        [row(value="1", pmid="111"), row(value="10000", pmid="222")],
    )
    assert outcome.increment_status == "ok", "a label exists"
    assert outcome.eligibility_reason == "discordant"
    assert outcome.is_scoreable is False
    assert outcome.in_primary is False and outcome.in_sensitivity is False
    assert outcome.increment_exact.spread == pytest.approx(4.0)
    assert outcome.increment_exact.n == 2
    assert prov.n_added_observations == 2


def test_a_concordant_increment_is_eligible() -> None:
    outcome, _prov, _diff = bridge_one(
        [], [row(value="10", pmid="111"), row(value="12", pmid="222")]
    )
    assert outcome.eligibility_reason is None
    assert outcome.in_primary is True


# ================================ 7. training invariance under changes to B


def _training_reading(earlier, later):
    outcome, _prov, _diff = bridge_one(earlier, later)
    return (outcome.training_status, outcome.training_label)


@pytest.mark.parametrize(
    ("name", "later"),
    [
        ("unchanged", [row()]),
        ("one addition", [row(), row(pmid="222")]),
        ("count increase", [row(), row()]),
        ("withdrawal", []),
        ("replacement", [row(value="20000")]),
        ("correction candidate", [row(value="20000", entry_doi="10.7270/Q")]),
        ("contradictory addition", [row(), row(relation=">", value="1e9", pmid="222")]),
        ("discordant addition", [row(), row(value="100000", pmid="222")]),
    ],
)
def test_training_is_invariant_to_whatever_b_does(name, later) -> None:
    """Across every shape B can take, A's reading does not move."""
    earlier = [row(entry_doi="10.7270/Q")]
    baseline = _training_reading(earlier, earlier)
    assert _training_reading(earlier, later) == baseline, f"B's {name} changed training"
    assert baseline == ("ok", ACTIVE)


def test_the_training_population_digest_ignores_b_entirely() -> None:
    from seq2lead.asof.matching import training_population

    earlier = [row(entry_doi="10.7270/Q")]
    digest = training_population(earlier)["digest"]
    for later in ([], [row()], [row(), row()], [row(value="20000", entry_doi="10.7270/Q")]):
        build_increments(earlier, later)
        assert training_population(earlier)["digest"] == digest


# ===================================== provenance survives the bridge


def test_provenance_crosses_the_bridge() -> None:
    """Counts, context, identifiers and traceability, not just a label."""
    _outcome, prov, _diff = bridge_one(
        [row(pmid="111", entry_doi="10.7270/A", rsid="1")],
        [
            row(pmid="111", entry_doi="10.7270/A", rsid="1"),
            row(pmid="222", entry_doi="10.7270/B", rsid="2"),
        ],
    )
    body = prov.to_dict()
    assert body["pair"] == ["K1", "S1"]
    assert len(body["slots"]) == 2, "both publication contexts are retained"
    assert body["entry_dois"] == ["10.7270/A", "10.7270/B"]
    assert body["reactant_set_ids"] == ["1", "2"]
    assert body["n_added_observations"] == 1
    assert "n_unresolved_additions" in body
    assert "n_correction_candidates_withheld" in body


def test_the_bridge_does_not_recompute_differences_from_value_alone() -> None:
    """It must consume the matcher's classification, not re-derive it.

    Two rows differing only by publication are *different* observations. Code that
    compared relation and value alone would call them identical and lose one.
    """
    _outcome, prov, diff = bridge_one(
        [row(pmid="111", value="10")],
        [row(pmid="111", value="10"), row(pmid="222", value="10")],
    )
    assert diff.summary()["unchanged_observations"] == 1
    assert diff.summary()["added_observations"] == 1
    assert prov.n_added_observations == 1, "the second publication's row was lost"


def test_multiple_pairs_are_bridged_independently() -> None:
    increments, _diff = build_increments(
        [row(compound="K1"), row(compound="K2")],
        [row(compound="K1"), row(compound="K2"), row(compound="K2", pmid="999")],
    )
    assert len(increments) == 2
    by_pair = {tuple(i.provenance.pair): i for i in increments}
    assert by_pair[("K1", "S1")].provenance.n_added_observations == 0
    assert by_pair[("K2", "S1")].provenance.n_added_observations == 1


# ================= correction pass: classifications survive the bridge


def srow(**kw):
    """A row carrying a source locator, so traceability can be asserted."""
    base = row(**{k: v for k, v in kw.items() if k not in ("release", "raw")})
    base["source_release"] = kw.get("release", "R")
    base["raw_row"] = kw.get("raw", "1")
    return base


def test_a_publication_move_is_not_cancelled_by_value_equality() -> None:
    """The confirmed defect.

    A: exact Ki 10 nM at publication 1. B: the same value at publication 2, with
    publication 1 gone. The matcher reports one addition and one removal at two
    different slots. The bridge used to convert both to `Measurement` and cancel
    them on value-only equality, so the harness saw an empty increment.
    """
    increments, diff = build_increments([srow(pmid="1", raw="1")], [srow(pmid="2", raw="2")])
    summary = diff.summary()
    assert summary["added_observations"] == 1
    assert summary["removed_observations"] == 1
    assert summary["new_slots"] == 1 and summary["removed_slots"] == 1

    bridged = increments[0]
    assert len(bridged.evidence.increment) == 1, "the matcher's addition was lost"
    assert bridged.provenance.n_added_observations == 1

    outcome = evaluate_pair(bridged.evidence, THETA)
    assert outcome.increment_status == "ok"
    assert outcome.increment_label == ACTIVE

    # the value-only difference still cancels -- which is why it is no longer read
    assert bridged.evidence.added == [], (
        "the diagnostic multiset difference should still cancel; "
        "the point is that the label no longer depends on it"
    )


def test_the_increment_is_taken_from_the_matcher_not_recomputed() -> None:
    """Three observations at three publications, all the same value."""
    increments, diff = build_increments(
        [srow(pmid="1", raw="1")],
        [srow(pmid="1", raw="1"), srow(pmid="2", raw="2"), srow(pmid="3", raw="3")],
    )
    assert diff.summary()["added_observations"] == 2
    bridged = increments[0]
    assert len(bridged.evidence.increment) == 2
    # here the value-only difference happens to agree, because the *counts*
    # differ. It disagrees precisely when counts match and slots move, which the
    # publication-move test above covers -- so the increment is read from the
    # matcher either way rather than depending on which case we are in.
    assert len(bridged.evidence.added) == 2
    assert evaluate_pair(bridged.evidence, THETA).increment_status == "ok"


def test_multiplicity_is_preserved_through_the_bridge() -> None:
    increments, diff = build_increments(
        [srow(raw="1"), srow(raw="2")], [srow(raw="1"), srow(raw="2"), srow(raw="3")]
    )
    assert diff.summary()["added_observations"] == 1
    assert len(increments[0].evidence.increment) == 1
    assert len(increments[0].evidence.in_b) == 3, "actual B keeps all three"


# ============ correction pass: actual B separate from increment eligibility


def test_a_withheld_correction_stays_in_actual_b() -> None:
    """The confirmed defect: withholding emptied B as well as the increment."""
    increments, diff = build_increments(
        [srow(value="10", entry_doi="10.7270/Q", raw="10")],
        [srow(value="20000", entry_doi="10.7270/Q", raw="20")],
    )
    assert diff.summary()["correction_candidates"] == 1
    bridged = increments[0]

    # eligible increment: withheld, per the declared policy
    assert bridged.evidence.increment == []
    assert bridged.provenance.n_correction_candidates_withheld == 1

    # actual B: still holds the row, because B holds it
    assert len(bridged.evidence.in_b) == 1

    outcome = evaluate_pair(bridged.evidence, THETA)
    assert outcome.full_b_status == "ok", "full B must read B's own evidence"
    assert outcome.full_b_label == INACTIVE
    assert outcome.increment_status == STATUS_NO_EVIDENCE
    assert outcome.in_primary is False and outcome.in_sensitivity is False
    assert (outcome.training_status, outcome.training_label) == ("ok", ACTIVE)


def test_withholding_counts_occurrences_not_slot_value_keys() -> None:
    """An unchanged observation sharing a correction's after-value must survive.

    A holds `20000` at publication 9 and `10` at the correction's slot. B holds
    the unchanged `20000` at publication 9 and the corrected `20000` at the
    correction's slot. Filtering by (slot, value) key would be wrong in general;
    here the key includes the slot, so the sharper risk is over-withholding by
    count. Exactly one occurrence is withheld, and both B rows survive.
    """
    increments, diff = build_increments(
        [srow(value="20000", pmid="9", raw="a"), srow(value="10", entry_doi="10.7270/Q", raw="b")],
        [
            srow(value="20000", pmid="9", raw="a"),
            srow(value="20000", entry_doi="10.7270/Q", raw="c"),
        ],
    )
    assert diff.summary()["correction_candidates"] == 1
    bridged = increments[0]
    assert bridged.provenance.n_correction_candidates_withheld == 1, "over-withheld"
    assert len(bridged.evidence.in_b) == 2, "an unchanged B row was dropped"
    assert bridged.evidence.increment == []
    # and the audit says which row was withheld
    body = bridged.provenance.to_dict()
    assert body["withheld_locators"] == ["R#c"]


def test_the_three_representations_are_independent() -> None:
    increments, _diff = build_increments(
        [srow(value="10", entry_doi="10.7270/Q", raw="1")],
        [srow(value="20000", entry_doi="10.7270/Q", raw="2"), srow(pmid="7", raw="3")],
    )
    evidence = increments[0].evidence
    assert len(evidence.in_a) == 1, "historical A"
    assert len(evidence.in_b) == 2, "complete actual B, correction included"
    assert len(evidence.increment) == 1, "eligible increment, correction withheld"
    assert evidence.increment is not None, "the increment must be explicit, not derived"


# ===================== correction pass: row-level traceability


def test_source_locators_reach_the_exported_audit_detail() -> None:
    _increments, diff = build_increments(
        [srow(raw="1")], [srow(raw="1"), srow(pmid="2", release="R2", raw="99")]
    )
    body = json.loads(diff.to_json())
    added = body["additions_detail"]
    assert len(added) == 1
    assert added[0]["source_locators"] == ["R2#99"], (
        "an aggregated identifier list does not establish row-level traceability"
    )


def test_increment_and_withheld_rows_are_individually_identified() -> None:
    increments, _diff = build_increments(
        [srow(value="10", entry_doi="10.7270/Q", raw="1")],
        [srow(value="20000", entry_doi="10.7270/Q", raw="2"), srow(pmid="7", raw="3")],
    )
    body = increments[0].provenance.to_dict()
    assert body["increment_locators"] == ["R#3"]
    assert body["withheld_locators"] == ["R#2"]
    assert "R#1" in body["source_locators"]


def test_source_refs_do_not_change_matching_equality() -> None:
    """Provenance must not make two identical observations look different."""
    from seq2lead.asof.matching import observation_of

    a = observation_of(srow(release="R1", raw="1"))
    b = observation_of(srow(release="R2", raw="2"))
    assert a.row_digest() == b.row_digest()
    assert a.sort_key() != b.sort_key(), "ordering must still be total"
    # two snapshots of the same observation are unchanged, not add+remove
    _increments, diff = build_increments(
        [srow(release="R1", raw="1")], [srow(release="R2", raw="2")]
    )
    assert diff.summary()["unchanged_observations"] == 1
    assert diff.summary()["added_observations"] == 0


def test_a_row_without_a_source_ref_still_matches() -> None:
    _increments, diff = build_increments([row()], [row()])
    assert diff.summary()["unchanged_observations"] == 1
    body = json.loads(diff.to_json())
    assert body["unchanged_observations"] == 1


# ============ audit counts agree across matcher, bridge and outcome


def test_removal_counts_agree_across_all_three_layers() -> None:
    """The publication-replacement fixture, counted consistently.

    `PairOutcome.n_removed` used to come from the value-only multiset difference,
    which cannot see a removal whose value still appears elsewhere in B. The
    matcher and the bridge both reported one removal while the exported outcome
    reported zero.
    """
    increments, diff = build_increments([srow(pmid="1", raw="1")], [srow(pmid="2", raw="2")])
    bridged = increments[0]
    outcome = evaluate_pair(bridged.evidence, THETA)
    summary = diff.summary()

    assert summary["added_observations"] == 1
    assert summary["removed_observations"] == 1
    assert bridged.provenance.n_added_observations == 1
    assert bridged.provenance.n_removed_observations == 1
    assert outcome.n_added == 1
    assert outcome.n_removed == 1, "the exported outcome disagrees with the matcher"


def test_value_only_differences_remain_explicitly_diagnostic() -> None:
    """They still cancel; they are simply no longer what the counts report."""
    increments, _diff = build_increments([srow(pmid="1", raw="1")], [srow(pmid="2", raw="2")])
    evidence = increments[0].evidence
    assert evidence.added == [], "the value-only difference should still cancel"
    assert evidence.removed == []
    # the explicit fields are what the audit reads
    assert len(evidence.eligible_increment) == 1
    assert len(evidence.effective_removals) == 1


def test_the_removed_rows_are_individually_identified() -> None:
    increments, _diff = build_increments([srow(pmid="1", raw="11")], [srow(pmid="2", raw="22")])
    body = increments[0].provenance.to_dict()
    assert body["removed_locators"] == ["R#11"]
    assert body["increment_locators"] == ["R#22"]


def test_fixing_the_count_did_not_change_labels_or_screening() -> None:
    """The count is an audit field; it must not touch the three readings."""
    increments, _diff = build_increments([srow(pmid="1", raw="1")], [srow(pmid="2", raw="2")])
    outcome = evaluate_pair(increments[0].evidence, THETA)
    assert (outcome.increment_status, outcome.increment_label) == ("ok", ACTIVE)
    assert outcome.full_b_status == "ok"
    assert outcome.full_b_label == ACTIVE
    assert (outcome.training_status, outcome.training_label) == ("ok", ACTIVE)
    assert outcome.screened_because is None
    assert outcome.in_primary is True


def test_a_genuine_withdrawal_still_counts_one_removal() -> None:
    increments, diff = build_increments([srow(raw="1")], [])
    outcome = evaluate_pair(increments[0].evidence, THETA)
    assert diff.summary()["removed_observations"] == 1
    assert increments[0].provenance.n_removed_observations == 1
    assert outcome.n_removed == 1


def test_hand_built_fixtures_also_carry_explicit_removals() -> None:
    """`from_delta` sets removals explicitly, so fixtures agree with the bridge."""
    bound = Measurement(">", 10000.0)
    evidence = PairEvidence.from_delta(
        "F", in_a=[bound], added=[Measurement("=", 10.0, is_exact=True)], removed=[bound]
    )
    assert evidence.removals == [bound]
    assert evaluate_pair(evidence, THETA).n_removed == 1
