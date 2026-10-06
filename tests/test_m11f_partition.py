"""The three-way as-of partition, its strata, and the leakage gate on real loaders.

Training and validation are both carved out of snapshot A. Three properties have
to hold, and each is checked here rather than argued:

* whole pairs stay together, and the assignment is reproducible from
  `(fraction, seed)` alone;
* **B is unavailable** on the path that derives A's eligibility;
* recurrence is defined against the pairs actually supplied to fitting, not
  against historical presence in A -- and the difference is not zero.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from seq2lead.asof.evaluation import ActivityRow, LeakageError, select_for_fitting
from seq2lead.asof.partition import (
    NEW_A_PRESENT_NOT_FITTED,
    NEW_ABSENT_FROM_A,
    NEW_RESERVED_FOR_VALIDATION,
    NEW_TO_FITTING,
    PARTITION_SEED,
    RECURRENT,
    TRAIN,
    VALIDATION,
    VALIDATION_FRACTION,
    InvalidPartitionConfig,
    Membership,
    build_partition,
    classification_eligible,
    pair_partition,
    regression_eligible,
    regression_target,
    stratify,
    validation_rmse_eligible,
)

THRESHOLD = 6.0
SEQ_A, SEQ_B = "a" * 64, "b" * 64


def row(key, *, seq=SEQ_A, relation="=", value="1", pmid="1", raw="1"):
    return {
        "inchikey": key,
        "sequence_sha256": seq,
        "pmid": pmid,
        "ph_text": "7.4",
        "temp_c_text": "25",
        "curation_source": "BindingDB",
        "measurement_type": "KI",
        "relation": relation,
        "value_text": value,
        "entry_doi": None,
        "reactant_set_id": None,
        "source_release": "BindingDB/202601/all",
        "raw_row": raw,
    }


def write_shards(tmp_path: Path, rows_by_shard: dict[int, list[dict]], label="a") -> Path:
    d = tmp_path / f"shards-{label}"
    d.mkdir(parents=True, exist_ok=True)
    for shard in range(max(rows_by_shard) + 1 if rows_by_shard else 1):
        (d / f"{label}-{shard:04d}.jsonl").write_text(
            "".join(json.dumps(r, sort_keys=True) + "\n" for r in rows_by_shard.get(shard, [])),
            encoding="utf-8",
        )
    return d


# ======================================================== the assignment itself


def test_the_assignment_is_deterministic_and_seed_pinned() -> None:
    """Reproducible from (fraction, seed) alone, across processes."""
    import hashlib

    key, seq = "AAAAAAAAAAAAAA-AAAAAAAAAA-A", SEQ_A
    body = f"{PARTITION_SEED}\x00{key}\x00{seq}".encode()
    draw = int.from_bytes(hashlib.blake2b(body, digest_size=8).digest(), "big") / float(1 << 64)
    expected = VALIDATION if draw < VALIDATION_FRACTION else TRAIN
    assert pair_partition(key, seq) == expected
    # a different seed must be able to move a pair
    moved = [
        pair_partition(f"K{i:013d}-AAAAAAAAAA-A", seq, seed="other")
        != pair_partition(f"K{i:013d}-AAAAAAAAAA-A", seq)
        for i in range(200)
    ]
    assert any(moved), "the seed does not affect the assignment"


def test_whole_pairs_stay_together_whatever_their_observations_look_like() -> None:
    """The key IS the pair, so no grouping step can separate its observations."""
    key, seq = "BBBBBBBBBBBBBB-BBBBBBBBBB-B", SEQ_B
    assigned = pair_partition(key, seq)
    # pH, temperature, publication and value all vary; the pair does not
    for pmid in ("1", "2", "999"):
        for value in ("1", "10", "100000"):
            assert pair_partition(key, seq) == assigned, (pmid, value)


def test_the_realised_fraction_tracks_the_declared_one() -> None:
    keys = [f"K{i:013d}-AAAAAAAAAA-A" for i in range(20_000)]
    n_val = sum(1 for k in keys if pair_partition(k, SEQ_A) == VALIDATION)
    realised = n_val / len(keys)
    assert abs(realised - VALIDATION_FRACTION) < 0.01, realised


def test_a_different_fraction_changes_the_split_monotonically() -> None:
    keys = [f"K{i:013d}-AAAAAAAAAA-A" for i in range(5_000)]
    small = {k for k in keys if pair_partition(k, SEQ_A, fraction=0.10) == VALIDATION}
    large = {k for k in keys if pair_partition(k, SEQ_A, fraction=0.30) == VALIDATION}
    assert small < large, "a larger fraction must be a superset, so the draw is a threshold"


# ========================================================== training eligibility


@pytest.mark.parametrize(
    ("status", "label", "expected"),
    [
        ("ok", "active", True),
        ("ok", "inactive", True),
        ("ok", "ambiguous", False),
        ("exact_bound_conflict", "ambiguous", False),
        ("empty_intersection", "ambiguous", False),
        ("no_usable_evidence", "ambiguous", False),
    ],
)
def test_classification_eligibility_rule(status, label, expected) -> None:
    assert classification_eligible(status, label) is expected


def test_a_censored_only_decisive_pair_has_a_label_but_no_regression_target() -> None:
    """The discriminator for the closeout's first defect.

    `> 10000 nM` puts the whole admissible range below pKi 6.0, so the pair is
    decisively inactive and classification-eligible. It has no exact measurement,
    so the declared exact-only MSE objective has nothing to regress on. An
    earlier revision counted such pairs as supplied to fitting.
    """
    from seq2lead.asof.bridge import to_measurement
    from seq2lead.asof.evaluation import PairEvidence, training_label
    from seq2lead.asof.matching import observation_of

    censored = [to_measurement(observation_of(row("X", relation=">", value="10000")))]
    evidence = PairEvidence(pair="x|y", in_a=censored, in_b=[])
    status, label = training_label(evidence, THRESHOLD)

    assert (status, label) == ("ok", "inactive"), "a decisive bound is a real class label"
    assert classification_eligible(status, label) is True
    assert regression_target(censored) is None, "a boundary is not a point estimate"
    assert regression_eligible(censored) is False
    assert validation_rmse_eligible(censored, status) is False

    # an exact pair at the same magnitude DOES have a target
    exact = [to_measurement(observation_of(row("X", value="10000")))]
    assert regression_target(exact) == pytest.approx(5.0)
    assert regression_eligible(exact) is True


def test_a_censored_only_pair_is_counted_separately_by_the_builder(tmp_path) -> None:
    key = "CENSORED0000AA-AAAAAAAAAA-A"
    rows = [row(key, relation=">", value="10000", raw="1")]
    summary = build_partition(write_shards(tmp_path, {0: rows}), 1, threshold=THRESHOLD)
    body = summary.as_dict()
    e = body["eligibility"]
    assert sum(e["classification_by_partition"].values()) == 1
    assert sum(e["regression_by_partition"].values()) == 0
    assert sum(e["censored_only_decisive_by_partition"].values()) == 1
    assert summary.fitted_pairs == 0, "nothing to regress on, so nothing is supplied"
    assert "censored_evidence_retained_for" in e


def test_validation_rmse_eligibility_is_not_training_eligibility(tmp_path) -> None:
    """A discordant pair may train and may NOT select a checkpoint."""
    from seq2lead.asof.bridge import to_measurement
    from seq2lead.asof.matching import observation_of

    wide = [
        to_measurement(observation_of(row("X", value="1", raw="1"))),
        to_measurement(observation_of(row("X", value="10000", pmid="2", raw="2"))),
    ]
    assert regression_eligible(wide) is True, "it has exact values, so it has a target"
    assert validation_rmse_eligible(wide, "ok") is False, "but it cannot select"


def test_a_discordant_pair_stays_training_eligible(tmp_path) -> None:
    """M4 leaves discordant pairs available for training; only scoring is denied.

    §7a example (h). Excluding them here would be a different rule than the
    endpoint declares -- and the record says so for each use separately.
    """
    key = "DISCORD00000AA-AAAAAAAAAA-A"
    rows = [row(key, value="1", raw="1"), row(key, value="10000", pmid="2", raw="2")]
    a_dir = write_shards(tmp_path, {0: rows})
    summary = build_partition(a_dir, 1, threshold=THRESHOLD)
    body = summary.as_dict()
    assert summary.pairs == 1
    assert sum(body["eligibility"]["regression_by_partition"].values()) == 1
    assert sum(body["eligibility"]["validation_rmse_by_partition"].values()) == 0
    treatment = body["discordance_treatment"]
    assert "REMAIN available for training" in treatment["training"]
    assert "EXCLUDED from checkpoint selection" in treatment["validation_rmse"]
    assert sum(treatment["discordant_regression_eligible_by_partition"].values()) == 1


# =============================================================== B unavailability


def test_the_partition_builder_never_opens_a_b_shard(tmp_path, monkeypatch) -> None:
    """The strongest available check: make opening anything but A an error.

    A docstring saying B is not read is not evidence. This patches `Path.open`
    to refuse any path outside the A shard directory, so a read of B would raise
    rather than quietly succeed.
    """
    a_rows = [row(f"K{i:013d}-AAAAAAAAAA-A", value="1", raw=str(i)) for i in range(20)]
    a_dir = write_shards(tmp_path, {0: a_rows})
    b_dir = write_shards(tmp_path, {0: a_rows}, label="b")

    real_open = Path.open
    opened: list[str] = []

    def guarded(self, *args, **kwargs):
        opened.append(str(self))
        if "shards-b" in str(self):
            msg = f"the partition path must not read B: {self}"
            raise AssertionError(msg)
        return real_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", guarded)
    summary = build_partition(a_dir, 1, threshold=THRESHOLD)
    assert summary.pairs == 20
    assert any("shards-a" in p for p in opened)
    assert not any("shards-b" in p for p in opened)
    assert b_dir.exists(), "B existed and was simply not read"


def test_as_eligibility_does_not_change_when_b_changes(tmp_path) -> None:
    """Built from A twice, with B present and absent on disk. Digests must match."""
    a_rows = [row(f"K{i:013d}-AAAAAAAAAA-A", value=str(i + 1), raw=str(i)) for i in range(30)]
    first = build_partition(write_shards(tmp_path / "one", {0: a_rows}), 1, threshold=THRESHOLD)
    write_shards(tmp_path / "two", {0: a_rows + [row("ZZZZZZZZZZZZZZ-ZZZZZZZZZZ-Z")]}, label="b")
    second = build_partition(write_shards(tmp_path / "two", {0: a_rows}), 1, threshold=THRESHOLD)
    assert first.evidence_digests == second.evidence_digests
    assert first.membership_digests == second.membership_digests


def test_the_membership_and_evidence_digests_are_recorded(tmp_path) -> None:
    a_rows = [row(f"K{i:013d}-AAAAAAAAAA-A", value="1", raw=str(i)) for i in range(50)]
    summary = build_partition(write_shards(tmp_path, {0: a_rows}), 1, threshold=THRESHOLD)
    body = summary.as_dict()
    for part in (TRAIN, VALIDATION):
        assert len(body["membership_digests"][part]) == 64
        assert len(body["evidence_support_digests"][part]) == 64
    assert body["membership_digests"][TRAIN] != body["membership_digests"][VALIDATION]
    assert body["b_was_unavailable_on_this_path"] is True


def test_changing_one_pairs_evidence_changes_only_its_partitions_digest(tmp_path) -> None:
    """The evidence digest must notice a changed value, not just a changed label."""
    keys = [f"K{i:013d}-AAAAAAAAAA-A" for i in range(60)]
    base = [row(k, value="1", raw=str(i)) for i, k in enumerate(keys)]
    first = build_partition(write_shards(tmp_path / "a", {0: base}), 1, threshold=THRESHOLD)
    # same label (active), different value
    altered = [row(k, value="2" if i == 0 else "1", raw=str(i)) for i, k in enumerate(keys)]
    second = build_partition(write_shards(tmp_path / "b", {0: altered}), 1, threshold=THRESHOLD)
    moved = [
        p for p in (TRAIN, VALIDATION) if first.evidence_digests[p] != second.evidence_digests[p]
    ]
    assert len(moved) == 1, "exactly the partition holding the altered pair should move"
    assert first.membership_digests == second.membership_digests, (
        "membership did not change, so its digest must not"
    )


# ===================================================================== the strata


def test_recurrence_is_defined_against_the_fitted_set() -> None:
    # recurrence keys on REGRESSION eligibility: what the training loader emits
    assert stratify(Membership(TRAIN, True, True)) == RECURRENT
    assert stratify(Membership(TRAIN, True, False)) == NEW_A_PRESENT_NOT_FITTED, (
        "classification-eligible but no regression target is NOT recurrent"
    )
    assert stratify(Membership(TRAIN, False, False)) == NEW_A_PRESENT_NOT_FITTED
    assert stratify(Membership(VALIDATION, True, True)) == NEW_RESERVED_FOR_VALIDATION
    assert stratify(Membership(VALIDATION, True, False)) == NEW_RESERVED_FOR_VALIDATION
    assert stratify(None) == NEW_ABSENT_FROM_A


def test_an_a_present_pair_can_still_be_new_to_fitting() -> None:
    """The distinction historical presence cannot express.

    Both of these pairs have A evidence. Neither reaches fitting -- one is
    reserved for validation, the other's A reading gives no usable label -- so
    calling either 'recurrent' because it appears in A would be wrong.
    """
    for membership in (
        Membership(VALIDATION, True, True),
        Membership(TRAIN, True, False),
    ):
        assert stratify(membership) in NEW_TO_FITTING
        assert stratify(membership) != RECURRENT


def test_the_validation_subgroup_is_not_described_as_untouched() -> None:
    from seq2lead.asof.partition import VALIDATION_EXPOSURE_NOTE

    assert "NOT untouched by model selection" in VALIDATION_EXPOSURE_NOTE
    assert "weaker evidence" in VALIDATION_EXPOSURE_NOTE


# ====================================== the leakage gate on the production loader


def test_the_production_selection_gate_refuses_validation_evidence() -> None:
    """`select_for_fitting` is the function that decides what a fitted artifact sees.

    Exercised with a three-way partition, not train-vs-test: the as-of design has
    train, validation-within-A and the B increment, and only the first is fittable.
    """
    rows = [
        ActivityRow(activity_id=1, partition="train"),
        ActivityRow(activity_id=2, partition="train"),
        ActivityRow(activity_id=3, partition="validation"),
        ActivityRow(activity_id=4, partition="increment"),
    ]
    selected = select_for_fitting(rows, "ecfp4-cache")
    assert selected == [1, 2], "only the train partition is fittable"
    assert 3 not in selected and 4 not in selected


def test_the_gate_raises_when_an_id_spans_a_fittable_and_a_reserved_partition() -> None:
    rows = [
        ActivityRow(activity_id=7, partition="train"),
        ActivityRow(activity_id=7, partition="validation"),
    ]
    with pytest.raises(LeakageError, match="both a fittable and a reserved partition"):
        select_for_fitting(rows, "esm2-cache")


def test_a_reserved_id_cannot_reach_a_fitted_artifact() -> None:
    from seq2lead.asof.evaluation import assert_no_reserved_evidence

    rows = [
        ActivityRow(activity_id=1, partition="train"),
        ActivityRow(activity_id=2, partition="validation"),
        ActivityRow(activity_id=3, partition="increment"),
    ]
    assert_no_reserved_evidence([1], rows, "scaler")
    for leaked, name in ((2, "validation"), (3, "increment")):
        with pytest.raises(LeakageError, match=name):
            assert_no_reserved_evidence([1, leaked], rows, "scaler")


def test_every_new_to_fitting_subgroup_is_named() -> None:
    assert set(NEW_TO_FITTING) == {
        NEW_ABSENT_FROM_A,
        NEW_RESERVED_FOR_VALIDATION,
        NEW_A_PRESENT_NOT_FITTED,
    }
    assert RECURRENT not in NEW_TO_FITTING


# ====================================== the configuration reaches every assignment


def test_fraction_zero_sends_every_pair_to_train(tmp_path) -> None:
    """The bug this catches: build_partition accepted fraction and ignored it.

    `iter_a_pairs` called `pair_partition` with the module defaults, so a caller
    asking for a different split got the default one -- honoured in the record and
    ignored in the assignment.
    """
    rows = [row(f"K{i:013d}-AAAAAAAAAA-A", value="1", raw=str(i)) for i in range(200)]
    a_dir = write_shards(tmp_path, {0: rows})
    summary = build_partition(a_dir, 1, threshold=THRESHOLD, fraction=0.0)
    body = summary.as_dict()
    assert body["pairs_by_partition"] == {TRAIN: 200}
    assert body["declared_validation_fraction"] == 0.0
    assert body["realised_validation_fraction"] == 0.0


def test_fraction_one_sends_every_pair_to_validation(tmp_path) -> None:
    rows = [row(f"K{i:013d}-AAAAAAAAAA-A", value="1", raw=str(i)) for i in range(200)]
    a_dir = write_shards(tmp_path, {0: rows})
    summary = build_partition(a_dir, 1, threshold=THRESHOLD, fraction=1.0)
    body = summary.as_dict()
    assert body["pairs_by_partition"] == {VALIDATION: 200}
    assert body["realised_validation_fraction"] == 1.0


def test_a_changed_seed_changes_membership_through_the_builder(tmp_path) -> None:
    rows = [row(f"K{i:013d}-AAAAAAAAAA-A", value="1", raw=str(i)) for i in range(400)]
    a_dir = write_shards(tmp_path, {0: rows})
    one = build_partition(a_dir, 1, threshold=THRESHOLD, seed="seed-one")
    two = build_partition(a_dir, 1, threshold=THRESHOLD, seed="seed-two")
    assert one.membership_digests != two.membership_digests, "the seed did not reach the assignment"
    assert one.as_dict()["seed"] == "seed-one"
    assert two.as_dict()["seed"] == "seed-two"


def test_the_recorded_parameters_reproduce_the_emitted_membership(tmp_path) -> None:
    """Re-deriving the split from the record alone must give the same answer."""
    rows = [row(f"K{i:013d}-AAAAAAAAAA-A", value="1", raw=str(i)) for i in range(300)]
    a_dir = write_shards(tmp_path, {0: rows})
    out = tmp_path / "membership.jsonl"
    summary = build_partition(
        a_dir, 1, threshold=THRESHOLD, membership_path=out, fraction=0.25, seed="recorded"
    )
    body = summary.as_dict()

    emitted = {}
    with out.open(encoding="utf-8") as fh:
        for line in fh:
            rec = json.loads(line)
            emitted[rec["pair"]] = rec["partition"]
    # re-derive from the RECORDED parameters only
    for pair, assigned in emitted.items():
        inchikey, sequence = pair.split("|", 1)
        rederived = pair_partition(
            inchikey,
            sequence,
            fraction=body["declared_validation_fraction"],
            seed=body["seed"],
        )
        assert rederived == assigned, pair
    assert abs(body["realised_validation_fraction"] - 0.25) < 0.08


@pytest.mark.parametrize(
    "bad",
    [
        {"fraction": -0.1},
        {"fraction": 1.5},
        {"fraction": "half"},
        {"fraction": True},
        {"seed": ""},
        {"seed": 42},
    ],
)
def test_invalid_parameters_are_rejected(tmp_path, bad) -> None:
    rows = [row("K0000000000000-AAAAAAAAAA-A", value="1", raw="1")]
    a_dir = write_shards(tmp_path, {0: rows})
    with pytest.raises(InvalidPartitionConfig):
        build_partition(a_dir, 1, threshold=THRESHOLD, **bad)
    with pytest.raises(InvalidPartitionConfig):
        pair_partition("K", "S", **bad)
