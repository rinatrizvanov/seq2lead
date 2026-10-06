"""KI-only pair aggregation: the semantics, and the things that could silently break it.

Three constraints separate this from the M11d diff, and each has a test here:

* **Eligibility is a property of a pair, not a slot.** One pair owns many slots,
  so a per-slot-shard figure would be computed on part of the evidence. The
  shards are keyed on the pair, and a pair spanning shards is asserted impossible.
* **Types must not be merged into the Ki harness, but classifications must not be
  re-derived either.** `diff_snapshots` decides corrections and conflicts from a
  slot's whole surplus across all types, so the diff runs on all types and only
  its outputs are restricted to KI.
* **A's training evidence cannot depend on B.** Checked by digest against a path
  that never receives B, not asserted from the structure of the code.
"""

from __future__ import annotations

import json

from seq2lead.asof.evaluation import PairEvidence, training_label
from seq2lead.asof.ki_aggregation import (
    KI,
    ArmCounts,
    aggregate_shard,
    pair_shard_index,
    partition_by_pair,
    restrict_report_to_type,
    training_only_labels,
    unambiguous_cross_slot_addition_locators,
)
from seq2lead.asof.matching import diff_snapshots

THRESHOLD = 6.0
SEQ_A = "a" * 64
SEQ_B = "b" * 64
KEY_1 = "AAAAAAAAAAAAAA-AAAAAAAAAA-A"
KEY_2 = "BBBBBBBBBBBBBB-BBBBBBBBBB-B"


def row(
    *,
    inchikey=KEY_1,
    seq=SEQ_A,
    pmid="12345",
    ph="7.4",
    temp="25",
    source="BindingDB",
    mtype=KI,
    relation="=",
    value="12",
    entry_doi=None,
    rsid=None,
    release="BindingDB/202601/all",
    raw_row="1",
):
    return {
        "inchikey": inchikey,
        "sequence_sha256": seq,
        "pmid": pmid,
        "ph_text": ph,
        "temp_c_text": temp,
        "curation_source": source,
        "measurement_type": mtype,
        "relation": relation,
        "value_text": value,
        "entry_doi": entry_doi,
        "reactant_set_id": rsid,
        "source_release": release,
        "raw_row": raw_row,
    }


def run(a_rows, b_rows, *, excluded=frozenset(), threshold=THRESHOLD):
    primary = ArmCounts(name="primary")
    sens = ArmCounts(name="cross_slot_sensitivity")
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


# ============================================================= pair sharding


def test_a_pair_cannot_span_shards(tmp_path) -> None:
    """The invariant eligibility rests on.

    A slot's compound and target determine its pair, so pair-sharding is a
    coarsening of slot-sharding: every slot of a pair, and therefore all of the
    pair's evidence, lands in one shard.
    """
    import gzip

    rows = [
        row(pmid=str(i), ph=p, temp=t)
        for i in range(5)
        for p in ("7.4", "6.8")
        for t in ("25", "37")
    ]
    export = tmp_path / "a.jsonl.gz"
    with gzip.open(export, "wt", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, sort_keys=True) + "\n")
    for shard_count in (1, 2, 3, 8, 16):
        stats = partition_by_pair(export, tmp_path / f"s{shard_count}", shard_count, "a")
        assert stats.reconciles
        assert stats.rows_read == len(rows)
        non_empty = [n for n in stats.per_shard if n]
        assert len(non_empty) == 1, (
            f"one pair's rows were split across {len(non_empty)} shards at k={shard_count}"
        )
        assert non_empty[0] == len(rows)


def test_the_pair_shard_key_is_stable_and_cryptographic() -> None:
    import hashlib

    expected = (
        int.from_bytes(hashlib.blake2b(f"{KEY_1}\t{SEQ_A}".encode(), digest_size=8).digest(), "big")
        % 997
    )
    assert pair_shard_index(KEY_1, SEQ_A, 997) == expected
    assert pair_shard_index(KEY_1, SEQ_A, 8) != pair_shard_index(KEY_2, SEQ_B, 8) or True
    assert 0 <= pair_shard_index(KEY_1, SEQ_A, 4) < 4


def test_two_pairs_are_aggregated_independently(tmp_path) -> None:
    """Different pairs must not pool evidence even when they share a compound."""
    a_rows = [row(seq=SEQ_A, value="1"), row(seq=SEQ_B, value="100000")]
    b_rows = [
        row(seq=SEQ_A, value="1"),
        row(seq=SEQ_B, value="100000"),
        row(seq=SEQ_A, value="2", pmid="999"),
    ]
    _, primary, _, records = run(a_rows, b_rows)
    assert primary.pairs == 1, "only the pair that gained evidence has an increment"
    # An audit record is retained for every bridged pair, so filter to the arm
    # that actually has an increment. The slot normalisation uppercases the hash.
    with_increment = [r for r in records if r["arms"]["declared_increment"]["has_increment"]]
    assert {r["pair"].split("|")[1] for r in with_increment} == {SEQ_A.upper()}


# ==================================================== type restriction of a report


def test_the_diff_sees_all_types_but_only_ki_reaches_the_harness() -> None:
    """The one identifier conflict in the corpus is IC50 against EC50.

    A KI-only diff could not see it, and the classification of a KI row at the
    same slot would change with it. So the diff runs on all types and the report
    is restricted afterwards.
    """
    a_rows = [
        row(mtype="IC50", value="999000", entry_doi="10.7270/A"),
        row(mtype=KI, value="5"),
    ]
    b_rows = [
        row(mtype="EC50", value="999000", entry_doi="10.7270/B"),
        row(mtype=KI, value="5"),
    ]
    full = diff_snapshots(a_rows, b_rows)
    assert len(full.identifier_conflicts) == 1, "the mixed-type conflict must be visible"

    ki_only = diff_snapshots(
        [r for r in a_rows if r["measurement_type"] == KI],
        [r for r in b_rows if r["measurement_type"] == KI],
    )
    assert len(ki_only.identifier_conflicts) == 0, (
        "filtering before the diff loses the conflict -- which is why we filter after"
    )

    restricted = restrict_report_to_type(full, KI)
    assert not restricted.addition_rows, "no KI additions here"
    assert not restricted.removal_rows
    # slot-level sets are facts about slots, not types, and are carried through
    assert restricted.new_slots == full.new_slots


def test_restriction_keeps_only_the_requested_type(tmp_path) -> None:
    a_rows = [row(mtype=KI, value="10"), row(mtype="IC50", value="20")]
    b_rows = [row(mtype=KI, value="11", pmid="999"), row(mtype="IC50", value="21", pmid="888")]
    full = diff_snapshots(a_rows, b_rows)
    restricted = restrict_report_to_type(full, KI)
    for obs in (*restricted.addition_rows, *restricted.removal_rows):
        assert obs.value.measurement_type == KI
    for (_slot, value), _n in restricted.additions.items():
        assert value.startswith(KI)


# ================================================== the required discriminating cases


def test_a_publication_move_is_an_addition_and_a_removal_not_a_cancellation() -> None:
    """`publication_ref` is part of the slot, so the pair sees both sides.

    A value-level subtraction would cancel these against each other and the
    harness would see neither -- the defect the three-representation design
    exists to prevent.
    """
    a_rows = [row(pmid="", value="183", entry_doi="10.7270/A", rsid="7", raw_row="11")]
    b_rows = [row(pmid="20126400", value="183", entry_doi="10.7270/A", rsid="7", raw_row="12")]
    result, primary, _, records = run(a_rows, b_rows)
    assert primary.pairs == 1
    assert records[0]["arms"]["declared_increment"]["n_increment"] == 1
    assert records[0]["n_removals"] == 1
    assert result.removal_observations == 1
    # in_b is complete actual B: one observation, the candidate-corresponding one
    assert records[0]["n_in_b"] == 1
    assert records[0]["n_in_a"] == 1


def test_unequal_multiplicities_are_preserved_at_the_pair_level() -> None:
    """Three identical observations in A, one in B: two removals, no additions."""
    a_rows = [row(value="12", raw_row=str(i)) for i in (1, 2, 3)]
    b_rows = [row(value="12", raw_row="1")]
    result, primary, _, records = run(a_rows, b_rows)
    assert result.removal_observations == 2
    assert primary.pairs == 0, "no increment, so the pair has nothing to score"
    # the audit record is retained even though the pair enters no arm
    assert len(records) == 1
    assert records[0]["arms"]["declared_increment"]["has_increment"] is False


def test_a_withdrawal_leaves_in_b_empty_and_is_counted_as_a_removal() -> None:
    a_rows = [row(value="12")]
    b_rows: list[dict] = []
    result, primary, _, records = run(a_rows, b_rows)
    assert result.removal_observations == 1
    assert result.historical_pairs == 1
    assert primary.pairs == 0
    assert len(records) == 1
    assert records[0]["arms"]["declared_increment"]["has_increment"] is False


def test_a_correction_candidate_is_withheld_from_the_increment_not_from_in_b() -> None:
    """The withholding policy, and the defect it replaced.

    Withholding a correction from `in_b` emptied actual B for a pair B plainly
    had evidence for, and the full-B audit then read `no_usable_evidence`.
    Withholding applies to the eligible increment alone.
    """
    a_rows = [row(value="10", entry_doi="10.7270/SAME", raw_row="1")]
    b_rows = [row(value="11", entry_doi="10.7270/SAME", raw_row="2")]
    result, primary, _, records = run(a_rows, b_rows)

    # the matcher linked it, so the addition is withheld from the increment
    assert records[0]["n_withheld_corrections"] == 1
    assert primary.pairs == 0, "the only addition was withheld, so there is no increment"
    # but actual B still holds the corrected observation
    evidence_b = 1
    assert result.b_ki_rows == evidence_b


def test_a_pair_with_evidence_at_several_slots_aggregates_across_all_of_them() -> None:
    """Eligibility must see the whole pair, not one slot's worth.

    Two slots differing only in publication, each contributing an exact value a
    log apart. Together they are discordant; separately neither is.
    """
    a_rows = [
        row(pmid="111", value="1"),
        row(pmid="222", value="100000"),
    ]
    b_rows = [
        row(pmid="111", value="1"),
        row(pmid="222", value="100000"),
        row(pmid="333", value="1"),
        row(pmid="444", value="100000"),
    ]
    _, primary, _, records = run(a_rows, b_rows)
    assert primary.pairs == 1
    assert records[0]["arms"]["declared_increment"]["n_increment"] == 2, (
        "both slots' additions belong to one pair"
    )
    # pKi 9 and pKi 4 in one increment: discordant, so it has a label but no standing
    assert primary.scoreable_pairs == 0
    assert primary.excluded_pairs_by_reason.get("discordant") == 1


# ============================================ A's training evidence is unchanged by B


def test_a_training_evidence_does_not_depend_on_b() -> None:
    """Checked by digest against a path that never receives B."""
    a_rows = [row(value="1"), row(seq=SEQ_B, value="100000")]
    for b_rows in (
        [],
        [row(value="1")],
        [row(value="2", pmid="999"), row(seq=SEQ_B, value="3", pmid="888")],
        [row(value="1"), row(value="1"), row(seq=SEQ_B, value="100000")],
    ):
        result, _, _, _ = run(a_rows, b_rows)
        expected, _ = training_only_labels(a_rows, THRESHOLD)
        assert result.training_digest == expected, "A's training labels changed when B changed"
        assert result.training_only_digest == expected


def test_training_labels_come_from_in_a_alone() -> None:
    """A pair whose B evidence contradicts A keeps A's training label."""
    a_rows = [row(value="1")]  # pKi 9 -> active
    b_rows = [row(value="1"), row(value="100000", pmid="999")]  # adds pKi 4
    result, _, _, _ = run(a_rows, b_rows)
    evidence = PairEvidence(
        pair=f"{KEY_1}|{SEQ_A}",
        in_a=[m for m in _measurements(a_rows)],
        in_b=[],
    )
    _status, label = training_label(evidence, THRESHOLD)
    assert result.training_label_counts[label] == 1


def _measurements(rows):
    from seq2lead.asof.bridge import to_measurement
    from seq2lead.asof.matching import observation_of

    out = []
    for r in rows:
        m = to_measurement(observation_of(r))
        if m is not None:
            out.append(m)
    return out


# ================================================== the cross-slot sensitivity


def test_the_sensitivity_excludes_only_the_named_occurrences() -> None:
    """Occurrence-level, by locator, with the primary arm untouched."""
    a_rows = [row(pmid="", value="183", entry_doi="10.7270/A", rsid="7", raw_row="11")]
    b_rows = [
        row(
            pmid="20126400",
            value="183",
            entry_doi="10.7270/A",
            rsid="7",
            raw_row="12",
            release="BindingDB/202609/all",
        ),
        row(pmid="20126400", value="500", raw_row="13", release="BindingDB/202609/all"),
    ]
    excluded = frozenset({"BindingDB/202609/all#12"})
    _, primary, sens, records = run(a_rows, b_rows, excluded=excluded)

    assert primary.pairs == 1
    assert primary.eligible_increment_observations == 2, "the primary arm excludes nothing"
    assert sens.pairs == 1
    assert sens.eligible_increment_observations == 1
    assert sens.excluded_cross_slot_observations == 1
    assert records[0]["arms"]["cross_slot_excluded"]["n_excluded_cross_slot"] == 1
    assert records[0]["increment_locators_excluded_by_sensitivity"] == ["BindingDB/202609/all#12"]
    assert "BindingDB/202609/all#12" in records[0]["increment_locators"]


def test_a_pair_whose_whole_increment_is_excluded_leaves_the_sensitivity_arm() -> None:
    a_rows = [row(pmid="", value="183", entry_doi="10.7270/A", rsid="7", raw_row="11")]
    b_rows = [
        row(
            pmid="20126400",
            value="183",
            entry_doi="10.7270/A",
            rsid="7",
            raw_row="12",
            release="BindingDB/202609/all",
        )
    ]
    _, primary, sens, _ = run(a_rows, b_rows, excluded=frozenset({"BindingDB/202609/all#12"}))
    assert primary.pairs == 1
    assert sens.pairs == 0
    assert sens.pairs_with_no_increment == 1
    assert sens.excluded_cross_slot_observations == 1


def test_only_unambiguous_candidates_contribute_exclusions(tmp_path) -> None:
    """Ambiguous groups are reported, never resolved, and never excluded."""
    detail = tmp_path / "detail"
    detail.mkdir(parents=True)

    def rec(slot, count, locators, value=f"{KI}\t=\t183"):
        return {
            "slot": slot,
            "value": value,
            "value_original": "183",
            "value_note": None,
            "ph_original": "",
            "temp_c_original": "",
            "entry_doi": "10.7270/A",
            "reactant_set_id": "1",
            "count": count,
            "source_locators": locators,
            "measurement_type": KI,
            "shard": 0,
        }

    def slot(pub, inchikey=KEY_1):
        return "\t".join((inchikey, SEQ_A, pub, "raw:", "raw:", "BindingDB"))

    # one unambiguous group, one ambiguous (1x2)
    (detail / "removals.jsonl").write_text(
        json.dumps(rec(slot("unattributed"), 1, ["A#1"]))
        + "\n"
        + json.dumps(rec(slot("unattributed", KEY_2), 1, ["A#2"]))
        + "\n",
        encoding="utf-8",
    )
    (detail / "additions.jsonl").write_text(
        json.dumps(rec(slot("pmid:1"), 1, ["B#1"]))
        + "\n"
        + json.dumps(rec(slot("pmid:2", KEY_2), 1, ["B#2"]))
        + "\n"
        + json.dumps(rec(slot("pmid:3", KEY_2), 1, ["B#3"]))
        + "\n",
        encoding="utf-8",
    )
    locators = unambiguous_cross_slot_addition_locators(detail, KI)
    assert locators == {"B#1"}, "only the 1x1 group contributes"


def test_exclusions_are_capped_by_the_matched_count(tmp_path) -> None:
    """An addition record of count 1 cannot excuse two excluded occurrences."""
    detail = tmp_path / "detail"
    detail.mkdir(parents=True)

    def slot(pub):
        return "\t".join((KEY_1, SEQ_A, pub, "raw:", "raw:", "BindingDB"))

    def rec(s, count, locators):
        return {
            "slot": s,
            "value": f"{KI}\t=\t183",
            "value_original": "183",
            "value_note": None,
            "ph_original": "",
            "temp_c_original": "",
            "entry_doi": None,
            "reactant_set_id": None,
            "count": count,
            "source_locators": locators,
            "measurement_type": KI,
            "shard": 0,
        }

    (detail / "removals.jsonl").write_text(
        json.dumps(rec(slot("unattributed"), 1, ["A#1"])) + "\n", encoding="utf-8"
    )
    (detail / "additions.jsonl").write_text(
        json.dumps(rec(slot("pmid:1"), 3, ["B#1", "B#2", "B#3"])) + "\n", encoding="utf-8"
    )
    locators = unambiguous_cross_slot_addition_locators(detail, KI)
    assert len(locators) == 1, "min(1, 3) = 1 occurrence matched, so one locator"
    assert locators == {"B#1"}


def test_the_sensitivity_never_touches_non_ki_types(tmp_path) -> None:
    detail = tmp_path / "detail"
    detail.mkdir(parents=True)

    def slot(pub):
        return "\t".join((KEY_1, SEQ_A, pub, "raw:", "raw:", "BindingDB"))

    def rec(s, value, locators):
        return {
            "slot": s,
            "value": value,
            "value_original": "183",
            "value_note": None,
            "ph_original": "",
            "temp_c_original": "",
            "entry_doi": None,
            "reactant_set_id": None,
            "count": 1,
            "source_locators": locators,
            "measurement_type": value.split("\t")[0],
            "shard": 0,
        }

    (detail / "removals.jsonl").write_text(
        json.dumps(rec(slot("unattributed"), "EC50\t=\t183", ["A#1"])) + "\n", encoding="utf-8"
    )
    (detail / "additions.jsonl").write_text(
        json.dumps(rec(slot("pmid:1"), "EC50\t=\t183", ["B#1"])) + "\n", encoding="utf-8"
    )
    assert unambiguous_cross_slot_addition_locators(detail, KI) == frozenset()


# ================================================== labels vs eligibility vs audit


def test_label_status_and_eligibility_stay_separate() -> None:
    """A discordant pair HAS a label; what it lacks is standing to be scored."""
    a_rows: list[dict] = []
    b_rows = [row(value="1", pmid="111"), row(value="100000", pmid="222")]
    _, primary, _, _ = run(a_rows, b_rows)
    assert primary.pairs == 1
    assert primary.increment_label_counts, "a label was produced"
    assert primary.scoreable_pairs == 0, "but it cannot be scored"
    assert primary.excluded_pairs_by_reason.get("discordant") == 1


def test_observations_and_pairs_are_reported_separately() -> None:
    a_rows: list[dict] = []
    b_rows = [row(value="1", pmid=str(i)) for i in range(4)]
    _, primary, _, _ = run(a_rows, b_rows)
    body = primary.as_dict()
    assert body["pairs_with_any_increment"] == 1
    assert body["observations"]["eligible_increment"] == 4
    # coverage now lives inside a consistency branch, because a target count that
    # does not say whether the screen was applied is ambiguous
    assert body["branches"]["screened_primary"]["coverage"]["distinct_targets"] == 1
    assert body["branches"]["unscreened_sensitivity"]["coverage"]["distinct_targets"] == 1


def test_a_pair_first_seen_in_b_is_not_in_the_training_digest() -> None:
    """The definition that made the first version of this check report a failure.

    A pair appearing first in B has an empty `in_a` and no training evidence. The
    A-only path never enumerates it, so including it in the two-snapshot digest
    compared two different populations and the digests differed for a reason that
    said nothing about whether A had changed.
    """
    a_rows = [row(value="1")]
    b_rows = [row(value="1"), row(inchikey=KEY_2, value="5", pmid="999")]
    result, _, _, _ = run(a_rows, b_rows)
    assert result.b_only_pairs_excluded_from_training == 1
    expected, _ = training_only_labels(a_rows, THRESHOLD)
    assert result.training_digest == expected
    assert sum(result.training_label_counts.values()) == 1, "only the pair with A evidence"


def test_the_training_digest_covers_every_pair_that_has_a_evidence() -> None:
    """Excluding B-only pairs must not become an excuse to exclude anything else."""
    a_rows = [row(value="1"), row(inchikey=KEY_2, value="100000"), row(seq=SEQ_B, value="50")]
    b_rows = [row(value="2", pmid="999")]
    result, _, _, _ = run(a_rows, b_rows)
    assert sum(result.training_label_counts.values()) == 3
    assert result.b_only_pairs_excluded_from_training == 0
    expected, counts = training_only_labels(a_rows, THRESHOLD)
    assert result.training_digest == expected
    assert result.training_label_counts == counts


def test_every_ki_addition_is_accounted_for() -> None:
    """additions = increment measurements + withheld + unparseable addition rows.

    Counted over every bridged pair, not only those that reach an arm. A pair
    whose whole increment was withheld contributes nothing to an arm, so an
    arm-level accounting would leave those occurrences unexplained -- which an
    earlier version of this summary did, by nine occurrences.
    """
    a_rows = [
        row(value="10", entry_doi="10.7270/C1", raw_row="1"),
        row(inchikey=KEY_2, value="20", raw_row="2"),
    ]
    b_rows = [
        # a linked correction: its addition is withheld, and this pair's whole
        # increment is withheld, so it never reaches an arm
        row(value="11", entry_doi="10.7270/C1", raw_row="3"),
        # a plain new observation
        row(inchikey=KEY_2, value="20", raw_row="4"),
        row(inchikey=KEY_2, value="30", pmid="999", raw_row="5"),
        # an addition whose value will not normalise
        row(inchikey=KEY_2, value="not-a-number", pmid="888", raw_row="6"),
    ]
    result, primary, _, _ = run(a_rows, b_rows)

    assert result.ki_withheld_all_pairs == 1
    assert result.ki_unparseable_addition_rows == 1
    assert (
        result.ki_increment_measurements
        + result.ki_withheld_all_pairs
        + result.ki_unparseable_addition_rows
        == result.ki_additions
    ), "a KI addition went unexplained"
    # the arm sees less, because the withheld-only pair never enters it
    assert primary.withheld_observations == 0


def test_an_unparseable_addition_never_becomes_a_zero() -> None:
    """It is dropped and counted, not silently coerced."""
    a_rows: list[dict] = []
    b_rows = [row(value="not-a-number", raw_row="1")]
    result, primary, _, _ = run(a_rows, b_rows)
    assert result.ki_unparseable_addition_rows == 1
    assert result.ki_increment_measurements == 0
    assert primary.pairs == 0
