"""Cross-slot candidate re-attributions: occurrence accounting and order independence.

The first version of this analysis had two defects, both reproduced here as
regression tests:

* it matched a removal against an addition **without consuming the addition's
  count**, so two removals sharing a key both "moved" into a single addition and
  the population was inflated;
* it took the **first eligible addition in file order**, so reversing the detail
  file changed which record a removal was compared against, and with it the
  reported identifier agreement.

The fix is counted, deterministic pairing in which each occurrence participates
at most once, identifier agreement is reported only where the correspondence is
*forced* rather than chosen, and ambiguous groups are counted instead of being
resolved by picking one.
"""

from __future__ import annotations

import json
import random
from pathlib import Path

import pytest

from seq2lead.asof.sharded import moved_observations

SEQ = "a" * 64
VALUE = "EC50\t=\t183"


def slot(
    pub: str,
    *,
    inchikey: str = "KEY1-AAAAAAAAAA-A",
    seq: str = SEQ,
    ph: str = "raw:",
    temp: str = "raw:",
    source: str = "BindingDB",
) -> str:
    return "\t".join((inchikey, seq, pub, ph, temp, source))


def rec(
    slot_str: str,
    count: int = 1,
    *,
    entry_doi: str | None = None,
    rsid: str | None = None,
    value: str = VALUE,
    locators: list[str] | None = None,
):
    """One grouped detail record, as `DiffReport._rows` emits them."""
    return {
        "slot": slot_str,
        "value": value,
        "value_original": value.split("\t")[-1],
        "value_note": None,
        "ph_original": "",
        "temp_c_original": "",
        "entry_doi": entry_doi,
        "reactant_set_id": rsid,
        "count": count,
        "source_locators": locators if locators is not None else ["A#1"],
        "measurement_type": value.split("\t")[0],
        "shard": 0,
    }


def analyse(tmp_path: Path, removals: list[dict], additions: list[dict]):
    tmp_path.mkdir(parents=True, exist_ok=True)
    for name, rows in (("removals", removals), ("additions", additions)):
        (tmp_path / f"{name}.jsonl").write_text(
            "".join(json.dumps(r, sort_keys=True) + "\n" for r in rows), encoding="utf-8"
        )
    return moved_observations(tmp_path)


def summary_without_sensitivity(stats) -> dict:
    body = stats.as_dict()
    body.pop("positional_pairing_sensitivity")
    body["unambiguous"].pop("pairs_with_locators_both_sides", None)
    return body


# ===================================================== the two reproduced defects


def test_two_removals_against_one_addition_do_not_both_move(tmp_path) -> None:
    """Regression: the addition's count was never consumed, so this reported 2."""
    stats = analyse(
        tmp_path,
        removals=[
            rec(slot("unattributed"), entry_doi="10.7270/A", rsid="1"),
            rec(slot("pmid:222"), entry_doi="10.7270/A", rsid="1"),
        ],
        additions=[rec(slot("pmid:111"), entry_doi="10.7270/A", rsid="1")],
    )
    assert stats.matched_occurrences == 0, "no correspondence is forced here"
    assert stats.ambiguous_groups == 1
    assert dict(stats.ambiguous_shapes) == {"2x1": 1}
    assert stats.ambiguous_removal_occurrences == 2
    assert stats.ambiguous_addition_occurrences == 1
    # The ceiling, not a claim: at most one pair could be formed.
    assert stats.ambiguous_pairable_upper_bound == 1


def test_one_removal_against_two_additions_is_order_independent(tmp_path) -> None:
    """Regression: the first addition in file order decided identifier agreement."""
    removals = [rec(slot("unattributed"), entry_doi="10.7270/SAME", rsid="1")]
    additions = [
        rec(slot("pmid:111"), entry_doi="10.7270/SAME", rsid="1"),
        rec(slot("pmid:222"), entry_doi="10.7270/OTHER", rsid="2"),
    ]
    forward = analyse(tmp_path / "fwd", removals, additions)
    reversed_ = analyse(tmp_path / "rev", removals, list(reversed(additions)))

    assert summary_without_sensitivity(forward) == summary_without_sensitivity(reversed_)
    # No identifier agreement is claimed, because no correspondence is forced.
    assert forward.entry_doi_both_present == 0
    assert forward.reactant_set_id_both_present == 0
    assert forward.ambiguous_groups == 1
    assert forward.ambiguous_groups_with_identifier_disagreement == 1
    # Even the labelled sensitivity must not depend on file order.
    assert forward.sensitivity.as_dict() == reversed_.sensitivity.as_dict()


# ============================================================ the required cases


def test_unequal_grouped_counts_consume_the_smaller_side(tmp_path) -> None:
    """One record each side, counts 5 and 3: correspondence forced, 2 left over."""
    stats = analyse(
        tmp_path,
        removals=[rec(slot("unattributed"), 5, entry_doi="10.7270/A", rsid="7")],
        additions=[rec(slot("pmid:111"), 3, entry_doi="10.7270/A", rsid="7")],
    )
    assert stats.unambiguous_groups == 1
    assert stats.matched_occurrences == 3
    assert stats.unmatched_removal_occurrences == 2
    assert stats.unmatched_addition_occurrences == 0
    assert stats.entry_doi_both_present == 3
    assert stats.entry_doi_agreed == 3
    assert stats.publication_gained == 3
    body = stats.as_dict()
    assert body["occurrence_accounting"]["removal_occurrences_considered"] == 5
    assert body["occurrence_accounting"]["addition_occurrences_considered"] == 3


def test_several_slots_sharing_one_compound_target_value_are_ambiguous(tmp_path) -> None:
    """Three removal slots and two addition slots: nothing is asserted."""
    stats = analyse(
        tmp_path,
        removals=[
            rec(slot("unattributed"), 2, entry_doi="10.7270/A", rsid="1"),
            rec(slot("pmid:1"), 1, entry_doi="10.7270/B", rsid="2"),
            rec(slot("pmid:2"), 1, entry_doi="10.7270/C", rsid="3"),
        ],
        additions=[
            rec(slot("pmid:3"), 1, entry_doi="10.7270/A", rsid="1"),
            rec(slot("pmid:4"), 3, entry_doi="10.7270/D", rsid="4"),
        ],
    )
    assert stats.unambiguous_groups == 0
    assert stats.matched_occurrences == 0
    assert stats.ambiguous_groups == 1
    assert dict(stats.ambiguous_shapes) == {"3x2": 1}
    assert stats.ambiguous_removal_occurrences == 4
    assert stats.ambiguous_addition_occurrences == 4
    assert stats.ambiguous_pairable_upper_bound == 4


def test_shuffled_detail_file_order_changes_nothing(tmp_path) -> None:
    """Across a mixed population, not just one group."""
    removals = [
        rec(
            slot("unattributed", inchikey=f"K{i:013d}-AAAAAAAAAA-A"),
            1,
            entry_doi=f"10.7270/E{i}",
            rsid=str(i),
        )
        for i in range(12)
    ]
    additions = [
        rec(
            slot(f"pmid:{i}", inchikey=f"K{i:013d}-AAAAAAAAAA-A"),
            1,
            entry_doi=f"10.7270/E{i}",
            rsid=str(i),
        )
        for i in range(12)
    ]
    # make some groups ambiguous
    removals.append(
        rec(
            slot("pmid:99", inchikey="K0000000000000-AAAAAAAAAA-A"),
            1,
            entry_doi="10.7270/X",
            rsid="99",
        )
    )
    additions.append(
        rec(
            slot("pmid:98", inchikey="K0000000000000-AAAAAAAAAA-A"),
            1,
            entry_doi="10.7270/Y",
            rsid="98",
        )
    )

    baseline = analyse(tmp_path / "base", removals, additions)
    rng = random.Random(20261003)
    for trial in range(4):
        r, a = list(removals), list(additions)
        rng.shuffle(r)
        rng.shuffle(a)
        got = analyse(tmp_path / f"t{trial}", r, a)
        assert got.as_dict() == baseline.as_dict(), f"trial {trial} differs"


def test_ambiguous_candidates_with_disagreeing_identifiers_are_counted_not_resolved(
    tmp_path,
) -> None:
    """The case the old code silently resolved in favour of whichever came first."""
    stats = analyse(
        tmp_path,
        removals=[
            rec(slot("unattributed"), 1, entry_doi="10.7270/A", rsid="1"),
            rec(slot("pmid:7"), 1, entry_doi="10.7270/A", rsid="1"),
        ],
        additions=[
            rec(slot("pmid:8"), 1, entry_doi="10.7270/A", rsid="1"),
            rec(slot("pmid:9"), 1, entry_doi="10.7270/ZZZ", rsid="99"),
        ],
    )
    assert stats.ambiguous_groups == 1
    assert stats.ambiguous_groups_with_identifier_disagreement == 1
    assert stats.entry_doi_both_present == 0, "no agreement may be claimed here"
    body = stats.as_dict()
    sens = body["positional_pairing_sensitivity"]
    assert "ALGORITHM-DEPENDENT" in sens["status"]
    assert sens["paired_occurrences"] == 2


# ========================================================== accounting invariants


def test_every_occurrence_in_a_two_sided_group_is_counted_exactly_once(tmp_path) -> None:
    """matched + unmatched + ambiguous, per side, with no double counting."""
    removals = [
        rec(
            slot("unattributed", inchikey="K1AAAAAAAAAAAA-AAAAAAAAAA-A"),
            4,
            entry_doi="10.7270/A",
            rsid="1",
        ),
        rec(
            slot("unattributed", inchikey="K2AAAAAAAAAAAA-AAAAAAAAAA-A"),
            2,
            entry_doi="10.7270/B",
            rsid="2",
        ),
        rec(
            slot("pmid:5", inchikey="K2AAAAAAAAAAAA-AAAAAAAAAA-A"),
            3,
            entry_doi="10.7270/C",
            rsid="3",
        ),
        # a group present only on the removal side: not considered at all
        rec(slot("unattributed", inchikey="K9AAAAAAAAAAAA-AAAAAAAAAA-A"), 7),
    ]
    additions = [
        rec(
            slot("pmid:1", inchikey="K1AAAAAAAAAAAA-AAAAAAAAAA-A"),
            1,
            entry_doi="10.7270/A",
            rsid="1",
        ),
        rec(
            slot("pmid:2", inchikey="K2AAAAAAAAAAAA-AAAAAAAAAA-A"),
            6,
            entry_doi="10.7270/B",
            rsid="2",
        ),
    ]
    stats = analyse(tmp_path, removals, additions)
    body = stats.as_dict()

    # K1: 1x1 -> unambiguous, min(4,1)=1 matched, 3 unmatched removals
    # K2: 2x1 -> ambiguous, 5 removal and 6 addition occurrences
    assert stats.matched_occurrences == 1
    assert stats.unmatched_removal_occurrences == 3
    assert stats.unmatched_addition_occurrences == 0
    assert stats.ambiguous_removal_occurrences == 5
    assert stats.ambiguous_addition_occurrences == 6
    assert body["occurrence_accounting"]["removal_occurrences_considered"] == 1 + 3 + 5
    assert body["occurrence_accounting"]["addition_occurrences_considered"] == 1 + 0 + 6
    # The one-sided group contributes nothing.
    assert stats.total_removal_occurrences_considered == 9


def test_pairing_never_consults_an_identifier(tmp_path) -> None:
    """Otherwise the agreement figure would be circular.

    Same slots and counts, identifiers swapped so that a pairing which *tried* to
    maximise agreement would reach a different answer. The group shape, matched
    count and accounting must be identical either way.
    """
    base = dict(
        removals=[rec(slot("unattributed"), 1, entry_doi="10.7270/A", rsid="1")],
        additions=[rec(slot("pmid:1"), 1, entry_doi="10.7270/A", rsid="1")],
    )
    agreeing = analyse(tmp_path / "agree", **base)
    disagreeing = analyse(
        tmp_path / "disagree",
        removals=[rec(slot("unattributed"), 1, entry_doi="10.7270/A", rsid="1")],
        additions=[rec(slot("pmid:1"), 1, entry_doi="10.7270/ZZZ", rsid="99")],
    )
    for stats in (agreeing, disagreeing):
        assert stats.unambiguous_groups == 1
        assert stats.matched_occurrences == 1
        assert stats.entry_doi_both_present == 1
    assert agreeing.entry_doi_agreed == 1
    assert disagreeing.entry_doi_agreed == 0, "disagreement must be reported, not paired away"


def test_a_candidate_contributes_one_removal_and_one_addition_together(tmp_path) -> None:
    """Which is why re-attribution cannot by itself move a net count."""
    stats = analyse(
        tmp_path,
        removals=[rec(slot("unattributed"), 3, entry_doi="10.7270/A", rsid="1")],
        additions=[rec(slot("pmid:1"), 3, entry_doi="10.7270/A", rsid="1")],
    )
    assert stats.matched_occurrences == 3
    assert stats.unmatched_removal_occurrences == stats.unmatched_addition_occurrences == 0
    assert "contributes nothing to a net count" in stats.as_dict()["interpretation"]


def test_the_population_is_described_as_candidates(tmp_path) -> None:
    stats = analyse(
        tmp_path,
        removals=[rec(slot("unattributed"), 1, entry_doi="10.7270/A")],
        additions=[rec(slot("pmid:1"), 1, entry_doi="10.7270/A")],
    )
    body = stats.as_dict()
    assert "CANDIDATE" in body["status"]
    assert "NOT established experimental identity" in body["status"]
    assert "candidate correspondences" in body["population"]
    # Equality of compound/target/operator/value is not experimental identity, and
    # the record has to say so rather than leaving it to a careful reader.
    established = body["what_equality_establishes"]
    assert "not experimental identity" in established
    assert "two independent measurements that happen to agree" in established


def test_measurement_type_is_part_of_the_group_key(tmp_path) -> None:
    """An EC50 and an IC50 of the same magnitude are not the same measurement."""
    stats = analyse(
        tmp_path,
        removals=[rec(slot("unattributed"), 1, value="EC50\t=\t183", entry_doi="10.7270/A")],
        additions=[rec(slot("pmid:1"), 1, value="IC50\t=\t183", entry_doi="10.7270/A")],
    )
    assert stats.unambiguous_groups == 0
    assert stats.ambiguous_groups == 0
    assert stats.matched_occurrences == 0


# ============================================================ the budget ordering


def test_an_oversized_shard_is_refused_before_being_fully_loaded(tmp_path) -> None:
    """Checking the budget after loading is checking too late.

    The allocation that would exhaust memory has already happened by then. The
    budget is applied from a counted byte scan while nothing is resident, and the
    reads are themselves bounded so a wrong count cannot slip past.
    """
    from unittest.mock import patch

    from seq2lead.asof import sharded

    a_dir, b_dir = tmp_path / "sa", tmp_path / "sb"
    a_dir.mkdir()
    b_dir.mkdir()
    (a_dir / "a-0000.jsonl").write_text(
        "".join(json.dumps(rec(slot(f"pmid:{i}"), 1)) + "\n" for i in range(50)),
        encoding="utf-8",
    )
    (b_dir / "b-0000.jsonl").write_text("", encoding="utf-8")

    with patch.object(
        sharded,
        "_read_shard_bounded",
        side_effect=AssertionError("the budget must be enforced before any shard is materialised"),
    ):
        with pytest.raises(sharded.ShardTooLarge, match="over the declared budget"):
            sharded.diff_sharded(a_dir, b_dir, tmp_path / "d", 1, budget=10)


def test_a_wrong_row_count_cannot_bypass_the_budget(tmp_path) -> None:
    """Belt to the pre-check's braces: the read itself is bounded."""
    from unittest.mock import patch

    from seq2lead.asof import sharded

    a_dir, b_dir = tmp_path / "sa", tmp_path / "sb"
    a_dir.mkdir()
    b_dir.mkdir()
    (a_dir / "a-0000.jsonl").write_text(
        "".join(json.dumps(rec(slot(f"pmid:{i}"), 1)) + "\n" for i in range(50)),
        encoding="utf-8",
    )
    (b_dir / "b-0000.jsonl").write_text("", encoding="utf-8")

    # A count that lies: the pre-check passes and the bounded read must still refuse.
    with patch.object(sharded, "count_rows", return_value=0):
        with pytest.raises(sharded.ShardTooLarge, match="refusing to read further"):
            sharded.diff_sharded(a_dir, b_dir, tmp_path / "d", 1, budget=10)


def test_a_shard_within_budget_still_processes(tmp_path) -> None:
    """The refusal must not fire on a shard that fits."""
    from seq2lead.asof import sharded

    a_dir, b_dir = tmp_path / "sa", tmp_path / "sb"
    a_dir.mkdir()
    b_dir.mkdir()
    rows = [
        {
            "inchikey": "AAAAAAAAAAAAAA-AAAAAAAAAA-A",
            "sequence_sha256": SEQ,
            "pmid": "1",
            "ph_text": "7.4",
            "temp_c_text": "25",
            "curation_source": "BindingDB",
            "measurement_type": "KI",
            "relation": "=",
            "value_text": "12",
            "entry_doi": None,
            "reactant_set_id": None,
            "source_release": "BindingDB/202601/all",
            "raw_row": "1",
        }
    ]
    (a_dir / "a-0000.jsonl").write_text(json.dumps(rows[0]) + "\n", encoding="utf-8")
    (b_dir / "b-0000.jsonl").write_text(json.dumps(rows[0]) + "\n", encoding="utf-8")
    summary = sharded.diff_sharded(a_dir, b_dir, tmp_path / "d", 1, budget=10)
    assert summary.unchanged == 1
    assert summary.reconciles
