"""Sharded matching must equal unsharded matching. Proved before the corpus run.

The corpus cannot be matched in one `diff_snapshots` call -- measured at 25.3 GB
against 16 GiB. Sharding is the way round that, which means the sharded result
is the only result anyone will see, and "it should be the same" is not good
enough. These tests compare it against the unsharded function on fixtures that
reach every classification branch, at several shard counts, under shuffled input.

Equality is checked on the **canonical detail**, not only on summary totals. Two
diffs can agree on how many rows were added and disagree on which, and the
summary would not show it.
"""

from __future__ import annotations

import json
import random
from pathlib import Path

import pytest

from seq2lead.asof.matching import diff_snapshots, slot_of
from seq2lead.asof.sharded import (
    MAX_SHARD_OBSERVATIONS,
    SHARD_HASH,
    canonical_from_detail,
    diff_sharded,
    partition,
    shard_index,
)

SHARD_COUNTS = (1, 2, 3, 7, 16)


def row(
    *,
    inchikey="AAAAAAAAAAAAAA-AAAAAAAAAA-A",
    seq="a" * 64,
    pmid="12345",
    ph="7.4",
    temp="25",
    source="BindingDB",
    mtype="KI",
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


def _write_export(rows, path: Path) -> None:
    """A gzipped JSONL export, as `asof.export` writes one."""
    import gzip

    with gzip.open(path, "wt", encoding="utf-8", newline="\n") as fh:
        for r in rows:
            fh.write(json.dumps(r, sort_keys=True) + "\n")


def run_sharded(a_rows, b_rows, tmp_path: Path, shard_count: int):
    """Partition both sides, diff every shard pair, return (summary, canonical)."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    a_export, b_export = tmp_path / "a.jsonl.gz", tmp_path / "b.jsonl.gz"
    _write_export(a_rows, a_export)
    _write_export(b_rows, b_export)
    a_dir, b_dir = tmp_path / "sa", tmp_path / "sb"
    detail = tmp_path / "detail"
    pa = partition(a_export, a_dir, shard_count, "a")
    pb = partition(b_export, b_dir, shard_count, "b")
    assert pa.reconciles and pb.reconciles
    assert pa.rows_read == len(a_rows)
    assert pb.rows_read == len(b_rows)
    summary = diff_sharded(a_dir, b_dir, detail, shard_count)
    return summary, canonical_from_detail(detail)


def unsharded_canonical(a_rows, b_rows) -> dict:
    """The same fields `canonical_from_detail` returns, from the real function."""
    body = json.loads(diff_snapshots(a_rows, b_rows).to_json())
    return {
        "additions_detail": body["additions_detail"],
        "removals_detail": body["removals_detail"],
        "unresolved_additions_detail": body["unresolved_additions_detail"],
        "unresolved_removals_detail": body["unresolved_removals_detail"],
        "correction_candidates_detail": body["correction_candidates_detail"],
        "identifier_conflicts_detail": body["identifier_conflicts_detail"],
        "new_slots": sorted(body.get("new_slots_detail", [])),
        "removed_slots": sorted(body.get("removed_slots_detail", [])),
        "ambiguous_slots_detail": body["ambiguous_slots_detail"],
    }


# ================================================================ the shard key


def test_the_shard_key_is_a_stable_cryptographic_hash_not_pythons() -> None:
    """`hash()` is per-process randomised, so a partition built on it is not reproducible.

    Pinned values, not a self-consistency check: comparing `shard_index` to
    itself inside one process would pass even if it called `hash()`.
    """
    assert SHARD_HASH == "blake2b-64"
    assert shard_index("abc", 16) == shard_index("abc", 16)
    # Known-answer: blake2b-64 of "abc", big-endian, mod 1000.
    import hashlib

    expected = int.from_bytes(hashlib.blake2b(b"abc", digest_size=8).digest(), "big") % 1000
    assert shard_index("abc", 1000) == expected
    assert shard_index("", 8) == shard_index("", 8)
    assert all(0 <= shard_index(f"slot-{i}", 7) < 7 for i in range(200))


def test_rows_sharing_a_slot_always_land_together() -> None:
    """The one invariant correctness rests on.

    Includes two rows whose pH is spelled differently but normalises to one slot:
    if the shard key were built from the raw text they would be separated and
    their counted comparison would silently split.
    """
    a = row(ph="7.4", value="10")
    b = row(ph="7.40", value="99")
    assert slot_of(a).serialised() == slot_of(b).serialised()
    for k in SHARD_COUNTS:
        assert shard_index(slot_of(a).serialised(), k) == shard_index(slot_of(b).serialised(), k)


# ===================================================== the classification branches

SCENARIOS: dict[str, tuple[list, list]] = {
    # counts rise and fall at one slot
    "count_change": (
        [row(value="10"), row(value="10"), row(value="20")],
        [row(value="10"), row(value="20"), row(value="20"), row(value="20")],
    ),
    # a whole slot disappears
    "withdrawal": (
        [row(value="10"), row(inchikey="BBBBBBBBBBBBBB-BBBBBBBBBB-B", value="5")],
        [row(value="10")],
    ),
    # one removal, one addition, entry DOIs agree -> correction candidate
    "correction_by_entry_doi": (
        [row(value="10", entry_doi="10.7270/AAA")],
        [row(value="11", entry_doi="10.7270/AAA")],
    ),
    # entry DOI absent on both, surrogate agrees -> correction by surrogate
    "correction_by_surrogate": (
        [row(value="10", rsid="777")],
        [row(value="11", rsid="777")],
    ),
    # both entry DOIs present and disagree -> identifier conflict, no link
    "identifier_conflict": (
        [row(value="10", entry_doi="10.7270/AAA", rsid="777")],
        [row(value="11", entry_doi="10.7270/BBB", rsid="777")],
    ),
    # two removals and two additions at one slot -> never linked
    "ambiguous_many_to_many": (
        [row(value="10", entry_doi="10.7270/AAA"), row(value="20", entry_doi="10.7270/BBB")],
        [row(value="11", entry_doi="10.7270/AAA"), row(value="21", entry_doi="10.7270/BBB")],
    ),
    # one-to-one where neither identifier can decide
    "one_to_one_undecidable": (
        [row(value="10")],
        [row(value="11")],
    ),
    # spellings that must normalise to one value, and one slot
    "numeric_normalisation": (
        [row(value="12", ph="7.4", temp="25"), row(value="0.5", ph="7.4")],
        [row(value="12.0", ph="7.40", temp="25.0"), row(value=".50", ph="7.4")],
    ),
    # the measurement type is part of the value, so a type change is a move
    "type_change": (
        [row(mtype="EC50", value="10", entry_doi="10.7270/CCC")],
        [row(mtype="IC50", value="10", entry_doi="10.7270/CCC")],
    ),
    # many slots, so sharding actually distributes
    "many_slots": (
        [row(inchikey=f"K{i:013d}-AAAAAAAAAA-A", value=str(i)) for i in range(40)]
        + [row(inchikey="K0000000000000-AAAAAAAAAA-A", value="0")],
        [row(inchikey=f"K{i:013d}-AAAAAAAAAA-A", value=str(i + 1)) for i in range(40)],
    ),
}


@pytest.mark.parametrize("scenario", sorted(SCENARIOS))
@pytest.mark.parametrize("shard_count", SHARD_COUNTS)
def test_sharded_equals_unsharded_on_every_branch(scenario, shard_count, tmp_path) -> None:
    a_rows, b_rows = SCENARIOS[scenario]
    summary, sharded = run_sharded(a_rows, b_rows, tmp_path, shard_count)
    plain = diff_snapshots(a_rows, b_rows)
    expected = unsharded_canonical(a_rows, b_rows)

    # summary totals
    assert summary.unchanged == sum(plain.unchanged.values())
    assert summary.additions == sum(plain.additions.values())
    assert summary.removals == sum(plain.removals.values())
    assert summary.new_slots == len(plain.new_slots)
    assert summary.removed_slots == len(plain.removed_slots)
    assert summary.correction_candidates == len(plain.correction_candidates)
    assert summary.identifier_conflicts == len(plain.identifier_conflicts)
    assert summary.ambiguous_slots == len(plain.ambiguous_slots)
    assert summary.unresolved_additions == sum(plain.unresolved_additions.values())
    assert summary.unresolved_removals == sum(plain.unresolved_removals.values())

    # canonical detail, field by field
    for key in (
        "additions_detail",
        "removals_detail",
        "unresolved_additions_detail",
        "unresolved_removals_detail",
        "correction_candidates_detail",
        "identifier_conflicts_detail",
        "ambiguous_slots_detail",
    ):
        assert sharded[key] == expected[key], f"{scenario}/k={shard_count}: {key} differs"

    # the slot sets are counted, and must match the unsharded sets
    assert set(sharded["new_slots"]) == set(plain.new_slots)
    assert set(sharded["removed_slots"]) == set(plain.removed_slots)


@pytest.mark.parametrize("scenario", sorted(SCENARIOS))
def test_the_result_does_not_depend_on_input_order(scenario, tmp_path) -> None:
    """Shuffling either side must not change anything.

    The matcher sorts into a canonical order precisely so pairing cannot depend
    on file order; sharding must not reintroduce the dependence by, for example,
    letting a shard's contents order the comparison.
    """
    a_rows, b_rows = SCENARIOS[scenario]
    rng = random.Random(20261002)
    baseline, base_detail = run_sharded(a_rows, b_rows, tmp_path / "base", 4)
    for trial in range(3):
        a_shuffled, b_shuffled = list(a_rows), list(b_rows)
        rng.shuffle(a_shuffled)
        rng.shuffle(b_shuffled)
        summary, detail = run_sharded(a_shuffled, b_shuffled, tmp_path / f"t{trial}", 4)
        assert summary.as_dict() | {"seconds": 0, "peak_rss_bytes": 0, "peak_rss_gb": 0} == (
            baseline.as_dict() | {"seconds": 0, "peak_rss_bytes": 0, "peak_rss_gb": 0}
        )
        assert detail == base_detail


@pytest.mark.parametrize("scenario", sorted(SCENARIOS))
def test_the_result_does_not_depend_on_the_shard_count(scenario, tmp_path) -> None:
    """A configuration choice must not change a scientific result."""
    a_rows, b_rows = SCENARIOS[scenario]
    reference = None
    for k in SHARD_COUNTS:
        summary, detail = run_sharded(a_rows, b_rows, tmp_path / f"k{k}", k)
        stripped = summary.as_dict()
        for volatile in (
            "shard_count",
            "shards_processed",
            "seconds",
            "peak_rss_bytes",
            "peak_rss_gb",
            "per_shard_peak_observations",
        ):
            stripped.pop(volatile)
        if reference is None:
            reference = (stripped, detail)
        else:
            assert stripped == reference[0], f"{scenario}: summary changed at k={k}"
            assert detail == reference[1], f"{scenario}: detail changed at k={k}"


# =========================================================== reconciliation + budget


@pytest.mark.parametrize("scenario", sorted(SCENARIOS))
def test_every_observation_is_accounted_for(scenario, tmp_path) -> None:
    """A = unchanged + removals and B = unchanged + additions, exactly."""
    a_rows, b_rows = SCENARIOS[scenario]
    summary, _ = run_sharded(a_rows, b_rows, tmp_path, 4)
    assert summary.a_rows_in == len(a_rows)
    assert summary.b_rows_in == len(b_rows)
    assert summary.a_reconstructed == len(a_rows)
    assert summary.b_reconstructed == len(b_rows)
    assert summary.reconciles


def test_a_shard_over_budget_refuses_rather_than_being_attempted(tmp_path) -> None:
    """Being killed partway through is worse than refusing up front."""
    a_rows = [row(inchikey=f"K{i:013d}-AAAAAAAAAA-A", value="1") for i in range(50)]
    a_export = tmp_path / "a.jsonl.gz"
    _write_export(a_rows, a_export)
    _write_export([], tmp_path / "b.jsonl.gz")
    partition(a_export, tmp_path / "sa", 1, "a")
    partition(tmp_path / "b.jsonl.gz", tmp_path / "sb", 1, "b")
    with pytest.raises(MemoryError, match="over the declared budget"):
        diff_sharded(tmp_path / "sa", tmp_path / "sb", tmp_path / "d", 1, budget=10)


def test_the_declared_budget_is_sized_against_the_measured_cost() -> None:
    """~3,965 bytes per observation was measured; the budget must reflect it."""
    measured_bytes_per_observation = 2492 + 1473
    assert MAX_SHARD_OBSERVATIONS * measured_bytes_per_observation < 2 * 2**30, (
        "the budget allows a shard larger than ~2 GB resident"
    )


def test_measurement_types_are_counted_separately_and_still_reconcile(tmp_path) -> None:
    """The endpoint boundary: all four types matched, counts kept apart.

    A type-filtered diff would report a measurement whose type changed as an
    unexplained removal in one run and an unexplained addition in another. Here
    it stays one slot with two values, and the per-type reconciliation closes.
    """
    a_rows = [
        row(mtype="KI", value="10"),
        row(mtype="IC50", value="20"),
        row(mtype="KD", value="30"),
        row(mtype="EC50", value="40"),
        row(mtype="EC50", value="50"),
    ]
    b_rows = [
        row(mtype="KI", value="10"),
        row(mtype="IC50", value="21"),
        row(mtype="KD", value="30"),
        row(mtype="EC50", value="40"),
    ]
    summary, _ = run_sharded(a_rows, b_rows, tmp_path, 4)
    by_type = summary.by_type.as_dict()
    assert set(by_type) == {"KI", "IC50", "KD", "EC50"}
    assert by_type["KI"] == {
        "unchanged": 1,
        "additions": 0,
        "removals": 0,
        "a_observations": 1,
        "b_observations": 1,
    }
    assert by_type["EC50"]["removals"] == 1
    assert by_type["EC50"]["a_observations"] == 2
    assert by_type["EC50"]["b_observations"] == 1
    assert by_type["IC50"] == {
        "unchanged": 0,
        "additions": 1,
        "removals": 1,
        "a_observations": 1,
        "b_observations": 1,
    }
    # per-type reconciliation sums to the overall one
    assert sum(v["a_observations"] for v in by_type.values()) == len(a_rows)
    assert sum(v["b_observations"] for v in by_type.values()) == len(b_rows)


def test_identifier_populations_separate_what_was_asked_from_what_was_not(tmp_path) -> None:
    """Agreement rates must describe a population where agreement was possible.

    A one-to-one slot where neither side has an entry DOI never had its DOIs
    compared; folding it into a denominator with the slots that did would
    understate agreement by counting silence as disagreement.
    """
    a_rows, b_rows = [], []
    # agreed entry DOI
    a_rows.append(row(inchikey="K1AAAAAAAAAAAA-AAAAAAAAAA-A", value="10", entry_doi="10.7270/A"))
    b_rows.append(row(inchikey="K1AAAAAAAAAAAA-AAAAAAAAAA-A", value="11", entry_doi="10.7270/A"))
    # disagreeing entry DOIs, surrogate agrees
    a_rows.append(
        row(inchikey="K2AAAAAAAAAAAA-AAAAAAAAAA-A", value="10", entry_doi="10.7270/B", rsid="9")
    )
    b_rows.append(
        row(inchikey="K2AAAAAAAAAAAA-AAAAAAAAAA-A", value="11", entry_doi="10.7270/C", rsid="9")
    )
    # no entry DOI, surrogate agrees
    a_rows.append(row(inchikey="K3AAAAAAAAAAAA-AAAAAAAAAA-A", value="10", rsid="5"))
    b_rows.append(row(inchikey="K3AAAAAAAAAAAA-AAAAAAAAAA-A", value="11", rsid="5"))
    # nothing can decide
    a_rows.append(row(inchikey="K4AAAAAAAAAAAA-AAAAAAAAAA-A", value="10"))
    b_rows.append(row(inchikey="K4AAAAAAAAAAAA-AAAAAAAAAA-A", value="11"))
    # many-to-many
    a_rows += [
        row(inchikey="K5AAAAAAAAAAAA-AAAAAAAAAA-A", value="10"),
        row(inchikey="K5AAAAAAAAAAAA-AAAAAAAAAA-A", value="20"),
    ]
    b_rows += [
        row(inchikey="K5AAAAAAAAAAAA-AAAAAAAAAA-A", value="11"),
        row(inchikey="K5AAAAAAAAAAAA-AAAAAAAAAA-A", value="21"),
    ]

    summary, _ = run_sharded(a_rows, b_rows, tmp_path, 8)
    ids = summary.populations.as_dict()
    assert ids["one_to_one_slots"] == 4
    assert ids["many_to_many_slots"] == 1
    assert ids["entry_doi"]["both_present"] == 2
    assert ids["entry_doi"]["agreed"] == 1
    assert ids["entry_doi"]["disagreed"] == 1
    assert ids["entry_doi"]["disagreed_but_surrogate_agreed"] == 1
    assert ids["entry_doi"]["agreement_rate_within_both_present"] == 0.5
    assert ids["reactant_set_id"]["consulted_because_entry_doi_could_not_decide"] == 2
    assert ids["reactant_set_id"]["agreed"] == 1
    assert ids["reactant_set_id"]["agreement_rate_when_consulted"] == 0.5


# ============================================ the moved-observation population


def test_moved_observations_pairs_across_slots_without_using_the_identifiers(tmp_path) -> None:
    """The population exists because the matcher deliberately will not pair these.

    `publication_ref` is part of the slot, so a row that gains a PMID leaves its
    slot. The matcher reports one removal and one addition and is right to: a
    correction is inferred only within a slot. Measuring identifier agreement on
    that population is therefore a separate, descriptive question -- and the
    pairing must not consult the identifiers, or the agreement measurement would
    be circular.
    """
    from seq2lead.asof.sharded import moved_observations

    a_rows = [
        # gains a PMID: same compound/target/value, different slot
        row(pmid="", value="183", entry_doi="10.7270/Q2ZG6XSW", rsid="391580"),
        # a genuine same-slot change, which must NOT count as moved
        row(inchikey="K2AAAAAAAAAAAA-AAAAAAAAAA-A", value="10", entry_doi="10.7270/X"),
        # disagreeing identifiers across a move
        row(
            inchikey="K3AAAAAAAAAAAA-AAAAAAAAAA-A",
            pmid="",
            value="5",
            entry_doi="10.7270/P",
            rsid="11",
        ),
    ]
    b_rows = [
        row(pmid="20126400", value="183", entry_doi="10.7270/Q2ZG6XSW", rsid="391580"),
        row(inchikey="K2AAAAAAAAAAAA-AAAAAAAAAA-A", value="11", entry_doi="10.7270/X"),
        row(
            inchikey="K3AAAAAAAAAAAA-AAAAAAAAAA-A",
            pmid="999",
            value="5",
            entry_doi="10.7270/Q",
            rsid="22",
        ),
    ]
    tmp_path.mkdir(parents=True, exist_ok=True)
    run_sharded(a_rows, b_rows, tmp_path, 4)
    moved = moved_observations(tmp_path / "detail")

    # Both moves are 1x1 groups, so each correspondence is forced rather than chosen.
    assert moved.unambiguous_groups == 2
    assert moved.matched_occurrences == 2, "only the slot-moves, not the same-slot change"
    assert moved.unmatched_removal_occurrences == 0
    assert moved.unmatched_addition_occurrences == 0
    assert moved.ambiguous_groups == 0
    assert moved.publication_gained == 2
    assert moved.publication_lost == 0
    assert moved.entry_doi_both_present == 2
    assert moved.entry_doi_agreed == 1
    assert moved.reactant_set_id_both_present == 2
    assert moved.reactant_set_id_agreed == 1
    body = moved.as_dict()
    assert body["unambiguous"]["entry_doi"]["agreement_rate"] == 0.5
    assert "not proof of global stability" in body["interpretation"]
    assert "CANDIDATE correspondences" in body["status"]


def test_a_same_slot_correction_is_not_counted_as_moved(tmp_path) -> None:
    """Guards the one way this analysis could double-count the matcher's work."""
    from seq2lead.asof.sharded import moved_observations

    a_rows = [row(value="10", entry_doi="10.7270/A")]
    b_rows = [row(value="11", entry_doi="10.7270/A")]
    tmp_path.mkdir(parents=True, exist_ok=True)
    summary, _ = run_sharded(a_rows, b_rows, tmp_path, 2)
    assert summary.correction_candidates == 1
    moved = moved_observations(tmp_path / "detail")
    assert moved.matched_occurrences == 0, "a correction is not a move"
    assert moved.ambiguous_groups == 0
    assert moved.same_slot_pairs_seen == 0
