"""M4 endpoint: pKi conversion, interval algebra, and pair construction."""

from __future__ import annotations

import pytest

from seq2lead.endpoint.interval import (
    ACTIVE,
    AMBIGUOUS,
    INACTIVE,
    Interval,
    classify,
    constraint,
    intersect_all,
    is_usable_magnitude,
    pki_from_nm,
)

THRESHOLD = 6.0  # pKi 6.0 == Ki 1000 nM


# ================================================================ conversion


def test_one_micromolar_is_exactly_pki_six() -> None:
    """The whole boundary argument rests on this being exact, not approximate."""
    assert pki_from_nm(1000.0) == 6.0


@pytest.mark.parametrize(
    ("nm", "pki"),
    [(1.0, 9.0), (10.0, 8.0), (100.0, 7.0), (1000.0, 6.0), (10000.0, 5.0), (0.1, 10.0)],
)
def test_pki_conversion(nm, pki) -> None:
    assert pki_from_nm(nm) == pytest.approx(pki)


@pytest.mark.parametrize(
    ("value", "usable"),
    [
        (1.0, True),
        (1e-9, True),
        (0.0, False),  # occurs in the real data as '0.000'
        (-1.0, False),
        (None, False),
        (float("inf"), False),
        (float("nan"), False),
    ],
)
def test_only_positive_finite_magnitudes_may_enter_a_logarithm(value, usable) -> None:
    assert is_usable_magnitude(value) is usable


def test_zero_produces_no_constraint_rather_than_negative_infinity() -> None:
    """'0.000' and '>0.000' are both real BindingDB values.

    35 Ki, 164 IC50, 34 Kd and 37 EC50 records carry a zero magnitude. log10(0)
    is -inf, and 'Ki > 0' is true of every compound ever made, so neither the
    regression nor the interval logic may see them.
    """
    assert constraint("=", 0.0) is None
    assert constraint(">", 0.0) is None
    assert constraint("<", -5.0) is None


# ============================================================ constraint sense


def test_censoring_direction_inverts_through_the_logarithm() -> None:
    """A lower bound on Ki is an UPPER bound on pKi."""
    gt = constraint(">", 1000.0)  # Ki > 1000
    assert gt.lo is None
    assert gt.hi == 6.0 and gt.hi_inclusive is False  # pKi < 6

    lt = constraint("<", 1000.0)  # Ki < 1000
    assert lt.hi is None
    assert lt.lo == 6.0 and lt.lo_inclusive is False  # pKi > 6


@pytest.mark.parametrize(
    ("relation", "lo", "lo_inc", "hi", "hi_inc"),
    [
        ("=", 6.0, True, 6.0, True),
        ("<", 6.0, False, None, False),
        ("<=", 6.0, True, None, False),
        (">", None, False, 6.0, False),
        (">=", None, False, 6.0, True),
    ],
)
def test_constraint_shapes(relation, lo, lo_inc, hi, hi_inc) -> None:
    c = constraint(relation, 1000.0)
    assert (c.lo, c.lo_inclusive, c.hi, c.hi_inclusive) == (lo, lo_inc, hi, hi_inc)


@pytest.mark.parametrize("relation", ["~", "?", "unknown"])
def test_approximate_and_unknown_relations_bound_nothing(relation) -> None:
    assert constraint(relation, 1000.0) is None


# ============================================================ the boundary


@pytest.mark.parametrize(
    ("relation", "nm", "verdict"),
    [
        ("=", 1000.0, ACTIVE),
        ("<", 1000.0, ACTIVE),
        ("<=", 1000.0, ACTIVE),
        (">", 1000.0, INACTIVE),
        (">=", 1000.0, AMBIGUOUS),  # admits exactly 1000 nM, which is active
    ],
)
def test_equality_boundary_at_one_micromolar(relation, nm, verdict) -> None:
    assert classify(constraint(relation, nm), THRESHOLD) == verdict


def test_the_two_lower_bounds_on_ki_disagree_at_the_threshold() -> None:
    """`>` excludes the endpoint and decides; `>=` admits it and cannot."""
    assert classify(constraint(">", 1000.0), THRESHOLD) == INACTIVE
    assert classify(constraint(">=", 1000.0), THRESHOLD) == AMBIGUOUS
    # They agree once the bound clears the threshold.
    assert classify(constraint(">", 1001.0), THRESHOLD) == INACTIVE
    assert classify(constraint(">=", 1001.0), THRESHOLD) == INACTIVE


def test_the_two_upper_bounds_on_ki_agree_at_the_threshold() -> None:
    """Both decide, because the active side is the inclusive one."""
    assert classify(constraint("<", 1000.0), THRESHOLD) == ACTIVE
    assert classify(constraint("<=", 1000.0), THRESHOLD) == ACTIVE


@pytest.mark.parametrize(
    ("relation", "nm", "verdict"),
    [
        (">", 10000.0, INACTIVE),
        (">", 100.0, AMBIGUOUS),  # could be 200 nM (active) or 1e6 (inactive)
        ("<", 1.0, ACTIVE),
        ("<", 10000.0, AMBIGUOUS),  # could be 5 nM (active) or 5000 (inactive)
    ],
)
def test_bounds_away_from_the_threshold(relation, nm, verdict) -> None:
    assert classify(constraint(relation, nm), THRESHOLD) == verdict


@pytest.mark.parametrize("threshold", [6.0, 7.0, 8.0])
def test_sensitivity_thresholds_shift_the_boundary_coherently(threshold) -> None:
    nm = 10 ** (9.0 - threshold)  # the Ki that sits exactly on this threshold
    assert classify(constraint(">", nm), threshold) == INACTIVE
    assert classify(constraint(">=", nm), threshold) == AMBIGUOUS
    assert classify(constraint("<=", nm), threshold) == ACTIVE


# ============================================================ intersection


def test_compatible_bounds_intersect_to_the_tightest() -> None:
    #  Ki < 10000  ->  pKi > 5 ;  Ki > 100  ->  pKi < 7
    combined = intersect_all([constraint("<", 10000.0), constraint(">", 100.0)])
    assert combined.lo == 5.0 and combined.hi == 7.0
    assert combined.is_empty is False
    assert classify(combined, THRESHOLD) == AMBIGUOUS  # straddles 6.0


def test_intersection_keeps_the_stronger_of_two_lower_bounds() -> None:
    combined = intersect_all([constraint("<", 10000.0), constraint("<", 100.0)])
    assert combined.lo == 7.0  # Ki < 100 is the tighter statement


def test_exclusive_wins_a_tie_on_the_same_endpoint() -> None:
    combined = constraint("<", 1000.0).intersect(constraint("<=", 1000.0))
    assert combined.lo == 6.0
    assert combined.lo_inclusive is False


def test_contradictory_bounds_give_an_empty_intersection() -> None:
    #  Ki < 10 -> pKi > 8   and   Ki > 10000 -> pKi < 5 : nothing satisfies both
    combined = intersect_all([constraint("<", 10.0), constraint(">", 10000.0)])
    assert combined.is_empty is True
    assert classify(combined, THRESHOLD) == AMBIGUOUS  # never a confident label


def test_touching_exclusive_bounds_are_empty() -> None:
    #  pKi > 6 and pKi < 6 cannot both hold
    combined = constraint("<", 1000.0).intersect(constraint(">", 1000.0))
    assert combined.is_empty is True


def test_touching_inclusive_bounds_admit_exactly_one_point() -> None:
    combined = constraint("<=", 1000.0).intersect(constraint(">=", 1000.0))
    assert combined.is_empty is False
    assert combined.contains(6.0)
    assert not combined.contains(6.000001)


def test_empty_intersection_is_never_averaged_away() -> None:
    """A median of censoring thresholds would invent a measurement."""
    bounds = [constraint(">", 10000.0), constraint("<", 10.0)]
    combined = intersect_all(bounds)
    midpoint = (constraint(">", 10000.0).hi + constraint("<", 10.0).lo) / 2
    assert combined.is_empty
    assert not combined.contains(midpoint)  # the "average" satisfies neither bound


def test_unbounded_interval_decides_nothing() -> None:
    assert Interval().is_empty is False
    assert classify(Interval(), THRESHOLD) == AMBIGUOUS


def test_containment_respects_inclusivity() -> None:
    assert constraint("<=", 1000.0).contains(6.0) is True
    assert constraint("<", 1000.0).contains(6.0) is False
    assert constraint(">=", 1000.0).contains(6.0) is True
    assert constraint(">", 1000.0).contains(6.0) is False


# ============================================================ database-backed

import hashlib  # noqa: E402
import zipfile  # noqa: E402
from datetime import UTC, datetime  # noqa: E402
from typing import TYPE_CHECKING  # noqa: E402

from seq2lead.curate import CURATOR_VERSION, curate_release  # noqa: E402
from seq2lead.db import connect, transaction  # noqa: E402
from seq2lead.db.maintenance import no_entity_leak  # noqa: E402
from seq2lead.endpoint.build import build_endpoint  # noqa: E402
from seq2lead.ingest.bindingdb import ingest  # noqa: E402
from seq2lead.ingest.download import DownloadResult  # noqa: E402
from seq2lead.ingest.sources import SourceFile  # noqa: E402


def _db_disabled() -> bool:
    import os

    return os.environ.get("SEQ2LEAD_SKIP_DB_TESTS") == "1"


@pytest.fixture(scope="module", autouse=True)
def _isolated_schema():
    """Every fixture in this module writes into a throwaway schema.

    These tests ingest synthetic releases and curate them. Run against the
    corpus they leave entities behind that no provenance query can find, which
    is how two test molecules and two test proteins reached the M7 caches.
    """
    from seq2lead.db.isolation import isolated_schema

    if _db_disabled():
        yield None
        return
    with isolated_schema("seq2lead_test_endpoint") as schema:
        yield schema


if TYPE_CHECKING:
    from pathlib import Path

HEADER = [
    "BindingDB Reactant_set_id",
    "Ligand SMILES",
    "Ligand InChI Key",
    "Target Name",
    "Number of Protein Chains in Target (>1 implies a multichain complex)",
    "BindingDB Target Chain Sequence 1",
    "Ki (nM)",
    "IC50 (nM)",
    "Kd (nM)",
    "EC50 (nM)",
]
SEQ = "MKVLSSAAWQR"
ETHANOL = "CCO"
BENZENE = "c1ccccc1"


def _row(rsid: str, smiles: str, ki: str = "", **extra: str) -> list[str]:
    base = dict.fromkeys(HEADER, "")
    base.update(
        {
            "BindingDB Reactant_set_id": rsid,
            "Ligand SMILES": smiles,
            "Ligand InChI Key": "KEY-" + smiles,
            "Number of Protein Chains in Target (>1 implies a multichain complex)": "1",
            "BindingDB Target Chain Sequence 1": SEQ,
            "Ki (nM)": ki,
        }
    )
    base.update(extra)
    return [base[h] for h in HEADER]


def _load_and_curate(tmp_path: Path, rows: list[list[str]], tag: str) -> int:
    tsv = "\n".join("\t".join(r) for r in [HEADER, *rows]) + "\n"
    path = tmp_path / f"{tag}.zip"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("m.tsv", tsv)
    source = SourceFile(
        source_name="EndpointTest",
        version="0",
        subset=tag,
        filename=path.name,
        archive_member="m.tsv",
        license="x",
        expected_sha256="0" * 64,
        required_columns=tuple(HEADER[:6]),
    )
    result = DownloadResult(
        path=path,
        sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        md5="0" * 32,
        md5_verified=False,
        sha256_pinned=True,
        archive_bytes=path.stat().st_size,
        downloaded_at=datetime.now(UTC),
        reused_existing=False,
    )
    with transaction() as conn:
        release_id = ingest(conn, source, result).source_release_id
    curate_release(transaction, release_id, batch_size=100, workers=1)
    return release_id


def _cleanup(release_id: int, endpoint_names: list[str]) -> None:
    with transaction() as conn:
        for name in endpoint_names:
            row = conn.execute("SELECT id FROM endpoint_version WHERE name=%s", (name,)).fetchone()
            if row is None:
                continue
            eid = row[0]
            conn.execute(
                "DELETE FROM pair_regression_support WHERE pair_id IN "
                "(SELECT id FROM pair_regression WHERE endpoint_id=%s)",
                (eid,),
            )
            conn.execute(
                "DELETE FROM pair_label_support WHERE pair_id IN "
                "(SELECT id FROM pair_label WHERE endpoint_id=%s)",
                (eid,),
            )
            conn.execute("DELETE FROM pair_regression WHERE endpoint_id=%s", (eid,))
            conn.execute("DELETE FROM pair_label WHERE endpoint_id=%s", (eid,))
            conn.execute("DELETE FROM endpoint_version WHERE id=%s", (eid,))
        conn.execute("DELETE FROM activity WHERE source_release_id=%s", (release_id,))
        conn.execute("DELETE FROM curation_exclusion WHERE source_release_id=%s", (release_id,))
        conn.execute("DELETE FROM curation_run WHERE source_release_id=%s", (release_id,))
        conn.execute("DELETE FROM raw_measurement WHERE source_release_id=%s", (release_id,))
        conn.execute("DELETE FROM ingest_exclusion WHERE source_release_id=%s", (release_id,))
        conn.execute("DELETE FROM source_release WHERE id=%s", (release_id,))


@pytest.fixture
def endpoint(tmp_path: Path, request: pytest.FixtureRequest):
    rows = request.param
    tag = f"ep-{abs(hash(str(rows))) % 10**8}"
    leak_guard = no_entity_leak()
    leak_guard.__enter__()
    name = f"test-{tag}"
    release_id = _load_and_curate(tmp_path, rows, tag)
    try:
        with transaction() as conn:
            endpoint_id, counters = build_endpoint(
                conn, release_id, name=name, curator_version=CURATOR_VERSION
            )
        yield endpoint_id, counters, name
    finally:
        _cleanup(release_id, [name, name + "-rerun", name + "-t7"])
        leak_guard.__exit__(None, None, None)


def _label(endpoint_id: int) -> tuple:
    with connect() as conn:
        return conn.execute(
            "SELECT label, status, evidence, n_exact, n_censored, exact_median_pki, "
            "lo_pki, hi_pki, n_exact_outside_bounds, eval_exclusion_reason "
            "FROM pair_label WHERE endpoint_id=%s",
            (endpoint_id,),
        ).fetchone()


@pytest.mark.requires_db
@pytest.mark.parametrize("endpoint", [[_row("a", ETHANOL, "1000")]], indirect=True)
def test_exact_at_the_threshold_is_active(endpoint) -> None:
    endpoint_id, _, _ = endpoint
    label = _label(endpoint_id)
    assert label[0] == ACTIVE
    assert label[1] == "ok"
    assert label[2] == "exact"
    assert label[5] == pytest.approx(6.0)


@pytest.mark.requires_db
@pytest.mark.parametrize("endpoint", [[_row("a", ETHANOL, ">1000")]], indirect=True)
def test_exclusive_lower_bound_at_the_threshold_is_inactive(endpoint) -> None:
    endpoint_id, _, _ = endpoint
    label = _label(endpoint_id)
    assert (label[0], label[2]) == (INACTIVE, "censored")
    assert label[7] == pytest.approx(6.0)  # upper bound on pKi


@pytest.mark.requires_db
@pytest.mark.parametrize("endpoint", [[_row("a", ETHANOL, ">=1000")]], indirect=True)
def test_inclusive_lower_bound_at_the_threshold_is_ambiguous(endpoint) -> None:
    """The case that makes '>=' worth keeping distinct from '>'."""
    endpoint_id, _, _ = endpoint
    label = _label(endpoint_id)
    assert label[0] == AMBIGUOUS
    assert label[9] == "ambiguous_label"


@pytest.mark.requires_db
@pytest.mark.parametrize(
    "endpoint", [[_row("a", ETHANOL, "1000"), _row("b", ETHANOL, "1000")]], indirect=True
)
def test_duplicate_exact_measurements_aggregate_to_one_pair(endpoint) -> None:
    endpoint_id, counters, _ = endpoint
    assert counters.pairs == 1
    with connect() as conn:
        reg = conn.execute(
            "SELECT n_obs, p_median, p_min, p_max, p_spread, is_discordant "
            "FROM pair_regression WHERE endpoint_id=%s",
            (endpoint_id,),
        ).fetchone()
        support = conn.execute(
            "SELECT count(*) FROM pair_regression_support s JOIN pair_regression r "
            "ON r.id = s.pair_id WHERE r.endpoint_id=%s",
            (endpoint_id,),
        ).fetchone()[0]
    assert reg[0] == 2
    assert reg[1] == pytest.approx(6.0)
    assert reg[4] == pytest.approx(0.0)
    assert reg[5] is False
    assert support == 2  # every contributing activity is linked


@pytest.mark.requires_db
@pytest.mark.parametrize(
    "endpoint", [[_row("a", ETHANOL, "1"), _row("b", ETHANOL, "100000")]], indirect=True
)
def test_pairs_spanning_more_than_one_pki_unit_are_flagged_discordant(endpoint) -> None:
    endpoint_id, counters, _ = endpoint
    with connect() as conn:
        reg = conn.execute(
            "SELECT p_spread, is_discordant, eval_exclusion_reason FROM pair_regression "
            "WHERE endpoint_id=%s",
            (endpoint_id,),
        ).fetchone()
    assert reg[0] == pytest.approx(5.0)  # pKi 9 vs pKi 4
    assert reg[1] is True
    assert reg[2] == "discordant"  # intent recorded; no partition assigned
    assert counters.discordant == 1


@pytest.mark.requires_db
@pytest.mark.parametrize(
    "endpoint", [[_row("a", ETHANOL, "<10"), _row("b", ETHANOL, ">10000")]], indirect=True
)
def test_contradictory_censored_bounds_get_no_label(endpoint) -> None:
    endpoint_id, _, _ = endpoint
    label = _label(endpoint_id)
    assert label[0] == "none"
    assert label[1] == "empty_intersection"
    assert label[9] == "empty_intersection"


@pytest.mark.requires_db
@pytest.mark.parametrize(
    "endpoint", [[_row("a", ETHANOL, "1"), _row("b", ETHANOL, ">10000")]], indirect=True
)
def test_exact_outside_its_censored_bound_gets_no_label(endpoint) -> None:
    """pKi 9 cannot coexist with 'pKi < 5'. Recorded, not reconciled."""
    endpoint_id, _, _ = endpoint
    label = _label(endpoint_id)
    assert label[0] == "none"
    assert label[1] == "exact_bound_conflict"
    assert label[2] == "both"
    assert label[8] == 1  # one exact falls outside


@pytest.mark.requires_db
@pytest.mark.parametrize(
    "endpoint", [[_row("a", ETHANOL, "100"), _row("b", ETHANOL, ">10000")]], indirect=True
)
def test_compatible_exact_and_bound_label_from_the_exact_value(endpoint) -> None:
    """pKi 7 sits inside 'pKi < 5'? No -- it does not, so this must conflict."""
    endpoint_id, _, _ = endpoint
    label = _label(endpoint_id)
    assert label[1] == "exact_bound_conflict"


@pytest.mark.requires_db
@pytest.mark.parametrize(
    "endpoint", [[_row("a", ETHANOL, "100"), _row("b", ETHANOL, ">10")]], indirect=True
)
def test_exact_inside_its_bound_yields_a_label_from_both(endpoint) -> None:
    #  exact pKi 7 ; bound Ki > 10 -> pKi < 8 : consistent
    endpoint_id, _, _ = endpoint
    label = _label(endpoint_id)
    assert (label[0], label[1], label[2]) == (ACTIVE, "ok", "both")
    assert label[8] == 0


@pytest.mark.requires_db
@pytest.mark.parametrize("endpoint", [[_row("a", ETHANOL, "0.000")]], indirect=True)
def test_nonpositive_values_reach_neither_output(endpoint) -> None:
    """The activity survives in M3; it must not enter a logarithm in M4."""
    endpoint_id, counters, _ = endpoint
    with connect() as conn:
        n_reg = conn.execute(
            "SELECT count(*) FROM pair_regression WHERE endpoint_id=%s", (endpoint_id,)
        ).fetchone()[0]
    assert n_reg == 0
    assert counters.unusable_magnitude == 1
    label = _label(endpoint_id)
    assert (label[0], label[1]) == ("none", "no_usable_evidence")


@pytest.mark.requires_db
@pytest.mark.parametrize(
    "endpoint",
    [[_row("a", ETHANOL, "1000"), _row("b", BENZENE, ">10000")]],
    indirect=True,
)
def test_measurement_types_other_than_ki_are_untouched(endpoint) -> None:
    endpoint_id, counters, _ = endpoint
    assert counters.pairs == 2
    with connect() as conn:
        types = conn.execute(
            "SELECT DISTINCT a.measurement_type FROM pair_label_support s "
            "JOIN activity a ON a.id = s.activity_id "
            "JOIN pair_label p ON p.id = s.pair_id WHERE p.endpoint_id=%s",
            (endpoint_id,),
        ).fetchall()
    assert [t[0] for t in types] == ["KI"]


@pytest.mark.requires_db
@pytest.mark.parametrize("endpoint", [[_row("a", ETHANOL, "1000")]], indirect=True)
def test_rebuilding_the_same_endpoint_name_is_refused(endpoint) -> None:
    """Endpoint versions are immutable; a rerun must not silently duplicate."""
    _, _, name = endpoint
    with connect() as conn:
        release_id = conn.execute(
            "SELECT source_release_id FROM endpoint_version WHERE name=%s", (name,)
        ).fetchone()[0]
    with pytest.raises(ValueError, match="already exists"), transaction() as conn:
        build_endpoint(conn, release_id, name=name, curator_version=CURATOR_VERSION)


@pytest.mark.requires_db
@pytest.mark.parametrize("endpoint", [[_row("a", ETHANOL, "1000")]], indirect=True)
def test_a_second_threshold_regenerates_as_its_own_version(endpoint) -> None:
    """7.0 and 8.0 sensitivity versions coexist with 6.0 rather than replacing it."""
    endpoint_id, _, name = endpoint
    with connect() as conn:
        release_id = conn.execute(
            "SELECT source_release_id FROM endpoint_version WHERE name=%s", (name,)
        ).fetchone()[0]
    with transaction() as conn:
        other_id, _ = build_endpoint(
            conn,
            release_id,
            name=name + "-t7",
            threshold=7.0,
            curator_version=CURATOR_VERSION,
        )
    with connect() as conn:
        at6 = conn.execute(
            "SELECT label FROM pair_label WHERE endpoint_id=%s", (endpoint_id,)
        ).fetchone()[0]
        at7 = conn.execute(
            "SELECT label FROM pair_label WHERE endpoint_id=%s", (other_id,)
        ).fetchone()[0]
    assert at6 == ACTIVE  # pKi 6.0 >= 6.0
    assert at7 == INACTIVE  # pKi 6.0 < 7.0


# ============================== the median-inside / value-outside contradiction


@pytest.mark.requires_db
@pytest.mark.parametrize(
    "endpoint",
    [[_row("a", ETHANOL, "10000"), _row("b", ETHANOL, "1"), _row("c", ETHANOL, "<1000")]],
    indirect=True,
)
def test_median_inside_but_an_exact_value_outside_is_contradictory(endpoint) -> None:
    """The reported bug, exactly.

    Exacts at pKi 5 (10000 nM) and pKi 9 (1 nM); censored `Ki < 1000` gives
    `pKi > 6`. The median is 7, which sits inside the bound, so the old rule
    returned `active`/`ok`. But pKi 5 flatly contradicts `pKi > 6`, so the pair
    must carry no label.
    """
    endpoint_id, _, _ = endpoint
    label = _label(endpoint_id)
    assert label[0] == "none"
    assert label[1] == "exact_bound_conflict"
    assert label[8] >= 1  # at least one exact outside
    assert label[9] == "exact_bound_conflict"


@pytest.mark.requires_db
@pytest.mark.parametrize(
    "endpoint",
    [
        [
            _row("a", ETHANOL, "10000"),
            _row("b", ETHANOL, "1"),
            _row("c", ETHANOL, "100"),
            _row("d", ETHANOL, "<1000"),
        ]
    ],
    indirect=True,
)
def test_several_exact_values_outside_the_bound_are_all_counted(endpoint) -> None:
    endpoint_id, _, _ = endpoint
    label = _label(endpoint_id)
    assert label[1] == "exact_bound_conflict"
    assert label[8] == 1  # only pKi 5 violates `pKi > 6`; 9 and 7 do not


@pytest.mark.requires_db
@pytest.mark.parametrize(
    "endpoint",
    [[_row("a", ETHANOL, "100"), _row("b", ETHANOL, "10"), _row("c", ETHANOL, "<10000")]],
    indirect=True,
)
def test_all_exact_values_inside_the_bound_still_label(endpoint) -> None:
    """The control: no exact outside, so the pair keeps its label."""
    endpoint_id, _, _ = endpoint
    label = _label(endpoint_id)
    assert (label[0], label[1], label[2]) == (ACTIVE, "ok", "both")
    assert label[8] == 0


@pytest.mark.requires_db
@pytest.mark.parametrize(
    "endpoint",
    [[_row("a", ETHANOL, "10000"), _row("b", ETHANOL, "1"), _row("c", ETHANOL, "<1000")]],
    indirect=True,
)
def test_a_contradictory_pair_is_excluded_from_eval_on_both_tables(endpoint) -> None:
    """Downstream must not need a join to pair_label to discover this."""
    endpoint_id, _, _ = endpoint
    with connect() as conn:
        reg = conn.execute(
            "SELECT excluded_from_eval, eval_exclusion_reason, n_obs, p_median, p_spread "
            "FROM pair_regression WHERE endpoint_id=%s",
            (endpoint_id,),
        ).fetchone()
        lab = conn.execute(
            "SELECT excluded_from_eval, eval_exclusion_reason FROM pair_label WHERE endpoint_id=%s",
            (endpoint_id,),
        ).fetchone()
    assert reg[0] is True
    assert reg[1] == "exact_bound_conflict"
    assert lab[0] is True
    assert lab[1] == "exact_bound_conflict"
    # Statistics are retained for audit and for the M5 assay-variance analysis.
    assert reg[2] == 2
    assert reg[3] is not None
    assert reg[4] == pytest.approx(4.0)


@pytest.mark.requires_db
@pytest.mark.parametrize(
    "endpoint", [[_row("a", ETHANOL, "1"), _row("b", ETHANOL, "100000")]], indirect=True
)
def test_discordance_propagates_to_the_label_eligibility(endpoint) -> None:
    """A discordant pair keeps its label but must not arbitrate validation or test."""
    endpoint_id, _, _ = endpoint
    with connect() as conn:
        lab = conn.execute(
            "SELECT label, status, excluded_from_eval, eval_exclusion_reason "
            "FROM pair_label WHERE endpoint_id=%s",
            (endpoint_id,),
        ).fetchone()
    assert lab[0] in (ACTIVE, INACTIVE)  # a label exists
    assert lab[1] == "ok"
    assert lab[2] is True  # but it is not eligible
    assert lab[3] == "discordant"


@pytest.mark.requires_db
@pytest.mark.parametrize(
    "endpoint",
    [[_row("a", ETHANOL, "10000"), _row("b", ETHANOL, "1"), _row("c", ETHANOL, "<1000")]],
    indirect=True,
)
def test_contradiction_outranks_discordance(endpoint) -> None:
    """Both apply here; the reason reported is the stronger one."""
    endpoint_id, _, _ = endpoint
    with connect() as conn:
        rows = conn.execute(
            "SELECT p.eval_exclusion_reason, r.is_discordant, r.eval_exclusion_reason "
            "FROM pair_label p JOIN pair_regression r "
            "ON r.compound_id=p.compound_id AND r.target_id=p.target_id "
            "AND r.endpoint_id=p.endpoint_id WHERE p.endpoint_id=%s",
            (endpoint_id,),
        ).fetchone()
    assert rows[1] is True  # spread of 4 pKi units, so also discordant
    assert rows[0] == "exact_bound_conflict"  # contradiction wins
    assert rows[2] == "exact_bound_conflict"


def test_exclusion_precedence_is_explicit() -> None:
    from seq2lead.endpoint.build import exclusion_reason

    assert exclusion_reason("exact_bound_conflict", "discordant") == "exact_bound_conflict"
    assert exclusion_reason("empty_intersection", "discordant") == "empty_intersection"
    assert exclusion_reason("discordant", "ambiguous_label") == "discordant"
    assert exclusion_reason(None, None) is None
    assert exclusion_reason("discordant") == "discordant"


@pytest.mark.requires_db
@pytest.mark.parametrize("endpoint", [[_row("a", ETHANOL, "1000")]], indirect=True)
def test_a_clean_pair_is_not_excluded(endpoint) -> None:
    endpoint_id, _, _ = endpoint
    with connect() as conn:
        reg = conn.execute(
            "SELECT excluded_from_eval, eval_exclusion_reason FROM pair_regression "
            "WHERE endpoint_id=%s",
            (endpoint_id,),
        ).fetchone()
        lab = conn.execute(
            "SELECT excluded_from_eval, eval_exclusion_reason FROM pair_label WHERE endpoint_id=%s",
            (endpoint_id,),
        ).fetchone()
    assert (reg[0], reg[1]) == (False, None)
    assert (lab[0], lab[1]) == (False, None)


# ==================================================== M5 assay-variance analysis


def test_distribution_of_empty_is_empty() -> None:
    from seq2lead.analysis.assay_variance import Distribution

    d = Distribution.of([])
    assert d.n == 0
    assert d.median is None


def test_distribution_summarises_a_sample() -> None:
    from seq2lead.analysis.assay_variance import Distribution

    d = Distribution.of([0.0, 0.5, 1.5, 2.0])
    assert d.n == 4
    assert d.median == pytest.approx(1.0)
    assert d.fraction_over_1 == pytest.approx(0.5)


def _comparison(within: list[float], between: list[float]):
    from seq2lead.analysis.assay_variance import Distribution, SpreadComparison

    return SpreadComparison(
        label="t",
        pairs_considered=len(within) + len(between),
        within_assay=Distribution.of(within),
        between_assay=Distribution.of(between),
        within_publication=Distribution.of([]),
        between_publication=Distribution.of([]),
    )


def test_decision_pools_when_assay_adds_little() -> None:
    from seq2lead.analysis.assay_variance import DECISION_POOL, decide

    decision, _ = decide(_comparison([0.2, 0.3, 0.4], [0.3, 0.4, 0.5]))
    assert decision == DECISION_POOL


def test_decision_restricts_when_assay_adds_a_lot_but_within_is_tight() -> None:
    from seq2lead.analysis.assay_variance import DECISION_RESTRICT, decide

    decision, _ = decide(_comparison([0.1, 0.2, 0.2], [1.2, 1.4, 1.6]))
    assert decision == DECISION_RESTRICT


def test_decision_declares_unsuitable_when_one_assay_cannot_reproduce_itself() -> None:
    from seq2lead.analysis.assay_variance import DECISION_UNSUITABLE, decide

    decision, why = decide(_comparison([1.2, 1.5, 1.8], [1.3, 1.6, 1.9]))
    assert decision == DECISION_UNSUITABLE
    assert "single assay" in why


def test_decision_refuses_without_evidence() -> None:
    from seq2lead.analysis.assay_variance import DECISION_UNSUITABLE, decide

    decision, _ = decide(_comparison([], []))
    assert decision == DECISION_UNSUITABLE


def test_decision_margin_is_a_declared_parameter() -> None:
    """Sensitivity has to be checkable, so the margin is an argument not a constant."""
    from seq2lead.analysis.assay_variance import DECISION_POOL, DECISION_RESTRICT, decide

    cmp = _comparison([0.1, 0.2, 0.2], [0.5, 0.6, 0.6])
    assert decide(cmp, margin=0.5)[0] == DECISION_POOL
    assert decide(cmp, margin=0.2)[0] == DECISION_RESTRICT


@pytest.mark.requires_db
def test_superseded_endpoints_are_refused_by_default() -> None:
    """Asserts about the real corpus, so it reads the corpus, not the test schema."""
    from seq2lead.analysis.assay_variance import SupersededEndpoint, resolve_endpoint
    from seq2lead.db.isolation import corpus_connection

    with corpus_connection() as conn:
        superseded = conn.execute(
            "SELECT name FROM endpoint_version WHERE superseded_by IS NOT NULL LIMIT 1"
        ).fetchone()
        if superseded is None:
            pytest.skip("no superseded endpoint present")
        with pytest.raises(SupersededEndpoint, match="superseded"):
            resolve_endpoint(conn, superseded[0])
        # ... but can be studied deliberately.
        eid, name, _ = resolve_endpoint(conn, superseded[0], allow_superseded=True)
        assert name == superseded[0]
        assert eid > 0


@pytest.mark.requires_db
def test_unknown_endpoint_is_reported_not_guessed() -> None:
    from seq2lead.analysis.assay_variance import resolve_endpoint

    with connect() as conn, pytest.raises(LookupError, match="does not exist"):
        resolve_endpoint(conn, "no-such-endpoint")
