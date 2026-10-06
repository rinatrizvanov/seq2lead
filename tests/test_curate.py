"""M3 curation: parsing, standardization, joins, scope and restart behaviour."""

from __future__ import annotations

import hashlib
import zipfile
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from seq2lead.curate import parse as parse_mod
from seq2lead.curate import standardizer as std_mod
from seq2lead.curate.parse import (
    ACTIVE,
    AMBIGUOUS,
    INACTIVE,
    MEASUREMENT_COLUMNS,
    decode_entities,
    label_at_threshold,
    parse_chain_count,
    parse_value,
    sequence_sha256,
)
from seq2lead.curate.pipeline import CURATOR_VERSION, curate_release
from seq2lead.curate.standardizer import STANDARDIZER_VERSION, standardize
from seq2lead.db import connect, transaction
from seq2lead.db.maintenance import no_entity_leak
from seq2lead.ingest.bindingdb import ingest
from seq2lead.ingest.download import DownloadResult
from seq2lead.ingest.sources import SourceFile


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
    with isolated_schema("seq2lead_test_curate") as schema:
        yield schema


if TYPE_CHECKING:
    from pathlib import Path

# ============================================================ relation parsing


@pytest.mark.parametrize(
    ("raw", "relation", "numeric"),
    [
        ("4.5", "=", 4.5),
        ("  4.5  ", "=", 4.5),
        ("=4.5", "=", 4.5),
        (">10000", ">", 10000.0),
        ("> 10000", ">", 10000.0),
        ("<1", "<", 1.0),
        (">=10000", ">=", 10000.0),
        ("<=1", "<=", 1.0),
        ("\u226510000", ">=", 10000.0),  # unicode >=
        ("\u22641", "<=", 1.0),  # unicode <=
        ("~5", "~", 5.0),
        ("\u22485", "~", 5.0),  # unicode approx
        ("1.2e3", "=", 1200.0),
        ("1,000", "=", 1000.0),
    ],
)
def test_relation_and_magnitude_are_separated(raw, relation, numeric) -> None:
    parsed = parse_value(raw)
    assert parsed is not None
    assert parsed.relation == relation
    assert parsed.value_numeric == pytest.approx(numeric)


@pytest.mark.parametrize(
    ("raw", "relation", "inclusive"),
    [
        (">1000", ">", False),
        (">=1000", ">=", True),
        ("<1000", "<", False),
        ("<=1000", "<=", True),
        ("1000", "=", None),
        ("~1000", "~", None),
    ],
)
def test_inclusive_and_exclusive_bounds_stay_distinct(raw, relation, inclusive) -> None:
    """The bug this replaces collapsed '>=' to '>' and '<=' to '<'."""
    parsed = parse_value(raw)
    assert parsed is not None
    assert parsed.relation == relation
    assert parsed.bound_inclusive is inclusive


def test_original_operator_is_kept_for_provenance() -> None:
    for raw, op in [(">=10", ">="), ("\u226510", "\u2265"), ("> 10", ">"), ("10", "")]:
        parsed = parse_value(raw)
        assert parsed is not None
        assert parsed.relation_raw == op


def test_unrecognized_relation_is_not_silently_called_exact() -> None:
    """Anything we could not read must be '?', never '='."""
    parsed = parse_value("ca. 500")
    assert parsed is not None
    assert parsed.relation == "?"
    assert parsed.value_numeric is None
    assert parsed.value_text == "ca. 500"


def test_approximate_records_are_distinct_from_exact() -> None:
    assert parse_value("~5").relation == "~"
    assert parse_value("5").relation == "="
    assert parse_value("~5").relation != parse_value("5").relation


# ---------------------------------------------------- the M4 reading contract

THRESHOLD_NM = 1000.0  # pKi 6.0


@pytest.mark.parametrize(
    ("relation", "value_nm", "verdict", "why"),
    [
        # Exactly at the threshold: Ki = 1000 nM is pKi 6.0, which is active.
        ("=", 1000.0, ACTIVE, "pKi == 6.0 is on the active side of '>= 6.0'"),
        ("<", 1000.0, ACTIVE, "pKi > 6.0, decisive"),
        ("<=", 1000.0, ACTIVE, "pKi >= 6.0, decisive"),
        (">", 1000.0, INACTIVE, "equality excluded, so pKi < 6.0 throughout"),
        (">=", 1000.0, AMBIGUOUS, "admits exactly 1000 nM, which is ACTIVE"),
        ("~", 1000.0, AMBIGUOUS, "no decidable bound"),
        ("?", 1000.0, AMBIGUOUS, "operator not understood"),
        # One unit either side of the threshold.
        ("=", 999.0, ACTIVE, "pKi just above 6.0"),
        ("=", 1001.0, INACTIVE, "pKi just below 6.0"),
        (">=", 1001.0, INACTIVE, "whole range is above the threshold"),
        (">=", 999.0, AMBIGUOUS, "could be 999 (active) or 10^6 (inactive)"),
        (">", 999.0, AMBIGUOUS, "could be 999.5 (active) or 10^6 (inactive)"),
        # Bounds far from the threshold.
        (">", 10000.0, INACTIVE, "decisively weak"),
        ("<", 1.0, ACTIVE, "decisively potent"),
        ("<", 10000.0, AMBIGUOUS, "could be 5 nM or 5000 nM"),
        ("<=", 10000.0, AMBIGUOUS, "same, endpoint included"),
    ],
)
def test_threshold_reading_contract(relation, value_nm, verdict, why) -> None:
    """How M4 must read each operator at pKi 6.0. M4 aggregates; this decides.

    The asymmetry between '>' and '>=' at exactly 1000 nM is the reason inclusive
    and exclusive bounds cannot be collapsed.
    """
    assert label_at_threshold(relation, value_nm, THRESHOLD_NM) == verdict, why


def test_the_two_lower_bounds_disagree_exactly_at_the_threshold() -> None:
    """The single case that makes the distinction load-bearing."""
    assert label_at_threshold(">", 1000.0, THRESHOLD_NM) == INACTIVE
    assert label_at_threshold(">=", 1000.0, THRESHOLD_NM) == AMBIGUOUS
    # ... and agree once the bound clears the threshold.
    assert label_at_threshold(">", 1001.0, THRESHOLD_NM) == INACTIVE
    assert label_at_threshold(">=", 1001.0, THRESHOLD_NM) == INACTIVE


def test_upper_bounds_agree_at_the_threshold() -> None:
    """Both are decisive, because the active side is the inclusive one."""
    assert label_at_threshold("<", 1000.0, THRESHOLD_NM) == ACTIVE
    assert label_at_threshold("<=", 1000.0, THRESHOLD_NM) == ACTIVE


def test_unparseable_magnitude_is_never_decisive() -> None:
    for relation in (">", ">=", "<", "<=", "=", "~", "?"):
        assert label_at_threshold(relation, None, THRESHOLD_NM) == AMBIGUOUS


def test_original_text_is_preserved_verbatim() -> None:
    """The raw field is the evidence; the parse is an interpretation of it."""
    assert parse_value(">10000").value_text == ">10000"
    assert parse_value("  >10000 ").value_text == ">10000"


@pytest.mark.parametrize("raw", ["", "   ", None])
def test_empty_values_yield_no_measurement(raw) -> None:
    assert parse_value(raw) is None


def test_unparseable_magnitude_keeps_the_relation_and_text() -> None:
    """A value we cannot turn into a number is still evidence, not garbage."""
    parsed = parse_value(">not-a-number")
    assert parsed is not None
    assert parsed.relation == ">"
    assert parsed.value_numeric is None
    assert parsed.value_text == ">not-a-number"


def test_all_four_measurement_types_are_distinct_columns() -> None:
    assert MEASUREMENT_COLUMNS == {
        "KI": "Ki (nM)",
        "IC50": "IC50 (nM)",
        "KD": "Kd (nM)",
        "EC50": "EC50 (nM)",
    }
    assert len(set(MEASUREMENT_COLUMNS.values())) == 4


def test_unit_is_recorded_and_is_nanomolar() -> None:
    assert parse_mod.VALUE_UNIT == "nM"


# ============================================================ chains / sequence


@pytest.mark.parametrize(
    ("raw", "expected"), [("1", 1), ("4", 4), ("", None), (None, None), ("x", None)]
)
def test_chain_count_parsing(raw, expected) -> None:
    assert parse_chain_count(raw) == expected


def test_sequence_hash_is_case_and_whitespace_insensitive() -> None:
    a = sequence_sha256("  mkvlss ")
    b = sequence_sha256("MKVLSS")
    assert a == b
    assert a == hashlib.sha256(b"MKVLSS").hexdigest()


def test_sequence_hash_distinguishes_different_sequences() -> None:
    assert sequence_sha256("MKV") != sequence_sha256("MKW")


# ============================================================ HTML entities


def test_html_entities_are_decoded_and_the_raw_form_is_recoverable() -> None:
    raw = "2 &#181;L of 10 &micro;M &amp; buffer"
    decoded = decode_entities(raw)
    assert decoded == "2 µL of 10 µM & buffer"
    assert decode_entities(None) is None
    # The transform is one-way, which is exactly why the raw column is kept too.
    assert raw != decoded


# ============================================================ standardization


def test_standardizer_version_names_rdkit_and_the_pipeline() -> None:
    assert "rdkit-" in STANDARDIZER_VERSION
    assert "fragment-parent" in STANDARDIZER_VERSION


def test_salt_and_free_base_standardize_to_one_compound() -> None:
    """FragmentParent is what makes a hydrochloride and its free base one compound."""
    free = standardize("CN1CCC[C@H]1c1cccnc1")
    salt = standardize("CN1CCC[C@H]1c1cccnc1.Cl")
    assert free is not None and salt is not None
    assert free.inchikey == salt.inchikey


def test_charged_and_neutral_forms_standardize_together() -> None:
    acid = standardize("CC(=O)O")
    anion = standardize("CC(=O)[O-]")
    assert acid is not None and anion is not None
    assert acid.inchikey == anion.inchikey


def test_invalid_smiles_returns_none_rather_than_raising() -> None:
    for bad in ["", "   ", "not-a-molecule", "C(((", "[Xx]"]:
        assert standardize(bad) is None


def test_standardized_fields_are_populated() -> None:
    result = standardize("CC(=O)Oc1ccccc1C(=O)O")
    assert result is not None
    assert result.inchikey.count("-") == 2
    assert result.n_heavy_atoms == 13
    assert result.canonical_smiles


def test_standardize_batch_preserves_keys_and_order() -> None:
    out = std_mod.standardize_batch([("a", "CCO"), ("b", "bad!!"), ("c", "c1ccccc1")])
    assert [k for k, _ in out] == ["a", "b", "c"]
    assert out[1][1] is None


# ============================================================ database-backed

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
    "pH",
    "Temp (C)",
    "Curation/DataSource",
    "Date of publication",
    "Date in BindingDB",
    "PMID",
    "Article DOI",
    "Patent Number",
]
REQUIRED = tuple(HEADER[:6])


def _row(**kw: str) -> list[str]:
    base = dict.fromkeys(HEADER, "")
    base.update(kw)
    return [base[h] for h in HEADER]


def _load_raw(tmp_path: Path, rows: list[list[str]], subset: str) -> int:
    tsv = "\n".join("\t".join(r) for r in [HEADER, *rows]) + "\n"
    path = tmp_path / f"{subset}.zip"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("m.tsv", tsv)
    source = SourceFile(
        source_name="CurateTest",
        version="0",
        subset=subset,
        filename=path.name,
        archive_member="m.tsv",
        license="x",
        expected_sha256="0" * 64,
        required_columns=REQUIRED,
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
        report = ingest(conn, source, result)
    return report.source_release_id


def _cleanup(release_id: int) -> None:
    with transaction() as conn:
        for table in ("activity", "curation_exclusion", "curation_run"):
            conn.execute(f"DELETE FROM {table} WHERE source_release_id=%s", (release_id,))  # noqa: S608
        conn.execute("DELETE FROM raw_measurement WHERE source_release_id=%s", (release_id,))
        conn.execute("DELETE FROM ingest_exclusion WHERE source_release_id=%s", (release_id,))
        conn.execute("DELETE FROM source_release WHERE id=%s", (release_id,))


@pytest.fixture
def curated(tmp_path: Path, request: pytest.FixtureRequest):
    rows = request.param
    subset = f"curate-{abs(hash(str(rows))) % 10**8}"
    leak_guard = no_entity_leak()
    leak_guard.__enter__()
    release_id = _load_raw(tmp_path, rows, subset)
    try:
        counters = curate_release(transaction, release_id, batch_size=100, workers=1)
        yield release_id, counters
    finally:
        _cleanup(release_id)
        leak_guard.__exit__(None, None, None)


SEQ = "MKVLSSAAWQR"

GOOD = _row(
    **{
        "BindingDB Reactant_set_id": "t-1",
        "Ligand SMILES": "CCO",
        "Ligand InChI Key": "LFQSCWFLJHTTHZ-UHFFFAOYSA-N",
        "Number of Protein Chains in Target (>1 implies a multichain complex)": "1",
        "BindingDB Target Chain Sequence 1": SEQ,
        "Ki (nM)": "4.5",
        "Curation/DataSource": "PDSP Ki",
    }
)
MULTITYPE = _row(
    **{
        "BindingDB Reactant_set_id": "t-2",
        "Ligand SMILES": "c1ccccc1",
        "Ligand InChI Key": "UHOVQNZJYSORNB-UHFFFAOYSA-N",
        "Number of Protein Chains in Target (>1 implies a multichain complex)": "1",
        "BindingDB Target Chain Sequence 1": SEQ,
        "Ki (nM)": "1",
        "IC50 (nM)": ">10000",
        "Curation/DataSource": "ChEMBL",
    }
)
MULTICHAIN = _row(
    **{
        "BindingDB Reactant_set_id": "t-3",
        "Ligand SMILES": "CCC",
        "Ligand InChI Key": "ATUOYWHBWRKTHZ-UHFFFAOYSA-N",
        "Number of Protein Chains in Target (>1 implies a multichain complex)": "4",
        "BindingDB Target Chain Sequence 1": SEQ,
        "Ki (nM)": "9",
    }
)
BADSMILES = _row(
    **{
        "BindingDB Reactant_set_id": "t-4",
        "Ligand SMILES": "C(((",
        "Ligand InChI Key": "BADKEY-UHFFFAOYSA-N",
        "Number of Protein Chains in Target (>1 implies a multichain complex)": "1",
        "BindingDB Target Chain Sequence 1": SEQ,
        "Ki (nM)": "3",
    }
)
NOVALUE = _row(
    **{
        "BindingDB Reactant_set_id": "t-5",
        "Ligand SMILES": "CCO",
        "Ligand InChI Key": "LFQSCWFLJHTTHZ-UHFFFAOYSA-N",
        "Number of Protein Chains in Target (>1 implies a multichain complex)": "1",
        "BindingDB Target Chain Sequence 1": SEQ,
    }
)
NOSEQ = _row(
    **{
        "BindingDB Reactant_set_id": "t-6",
        "Ligand SMILES": "CCO",
        "Ligand InChI Key": "LFQSCWFLJHTTHZ-UHFFFAOYSA-N",
        "Number of Protein Chains in Target (>1 implies a multichain complex)": "1",
        "Ki (nM)": "2",
    }
)

ALL_ROWS = [GOOD, MULTITYPE, MULTICHAIN, BADSMILES, NOVALUE, NOSEQ]


@pytest.mark.requires_db
@pytest.mark.parametrize("curated", [ALL_ROWS], indirect=True)
def test_every_raw_row_is_curated_or_excluded(curated) -> None:
    release_id, counters = curated
    assert counters.rows_seen == len(ALL_ROWS)
    assert counters.rows_curated + counters.rows_excluded == counters.rows_seen
    with connect() as conn:
        acts = conn.execute(
            "SELECT count(DISTINCT raw_measurement_id) FROM activity WHERE source_release_id=%s",
            (release_id,),
        ).fetchone()[0]
        excl = conn.execute(
            "SELECT count(*) FROM curation_exclusion WHERE source_release_id=%s", (release_id,)
        ).fetchone()[0]
    assert acts + excl == len(ALL_ROWS)


@pytest.mark.requires_db
@pytest.mark.parametrize("curated", [[MULTITYPE]], indirect=True)
def test_one_row_with_two_types_yields_two_activities_never_merged(curated) -> None:
    release_id, counters = curated
    assert counters.activities == 2
    with connect() as conn:
        rows = conn.execute(
            "SELECT measurement_type, relation, value_text, value_unit FROM activity "
            "WHERE source_release_id=%s ORDER BY measurement_type",
            (release_id,),
        ).fetchall()
    assert [r[0] for r in rows] == ["IC50", "KI"]
    assert dict((r[0], (r[1], r[2])) for r in rows) == {
        "IC50": (">", ">10000"),
        "KI": ("=", "1"),
    }
    assert {r[3] for r in rows} == {"nM"}


@pytest.mark.requires_db
@pytest.mark.parametrize("curated", [[BADSMILES]], indirect=True)
def test_invalid_smiles_is_excluded_with_a_rule_code(curated) -> None:
    release_id, counters = curated
    assert counters.rows_curated == 0
    with connect() as conn:
        row = conn.execute(
            "SELECT rule_code FROM curation_exclusion WHERE source_release_id=%s", (release_id,)
        ).fetchone()
    assert row[0] == "invalid_smiles"


@pytest.mark.requires_db
@pytest.mark.parametrize("curated", [[NOVALUE, NOSEQ]], indirect=True)
def test_missing_value_and_missing_sequence_get_distinct_rule_codes(curated) -> None:
    release_id, _ = curated
    with connect() as conn:
        rules = {
            r[0]
            for r in conn.execute(
                "SELECT rule_code FROM curation_exclusion WHERE source_release_id=%s",
                (release_id,),
            ).fetchall()
        }
    assert rules == {"no_measurement_value", "missing_target_sequence"}


@pytest.mark.requires_db
@pytest.mark.parametrize("curated", [[GOOD, MULTICHAIN]], indirect=True)
def test_multichain_rows_are_curated_but_flagged_out_of_scope(curated) -> None:
    """Retained with a reason, not deleted: the scope decision stays reversible."""
    release_id, _ = curated
    with connect() as conn:
        rows = dict(
            conn.execute(
                "SELECT reactant_set_id, in_benchmark_scope FROM activity "
                "WHERE source_release_id=%s",
                (release_id,),
            ).fetchall()
        )
        reason = conn.execute(
            "SELECT scope_reason, n_protein_chains FROM activity "
            "WHERE source_release_id=%s AND reactant_set_id='t-3'",
            (release_id,),
        ).fetchone()
    assert rows["t-1"] is True
    assert rows["t-3"] is False
    assert reason[0] == "multi_chain:4"
    assert reason[1] == 4


@pytest.mark.requires_db
@pytest.mark.parametrize("curated", [[GOOD]], indirect=True)
def test_missing_assay_mapping_is_explicit_not_a_dropped_row(curated) -> None:
    """A measurement with no assay description must survive, flagged."""
    release_id, _ = curated
    with connect() as conn:
        row = conn.execute(
            "SELECT assay_id, assay_join_status FROM activity WHERE source_release_id=%s",
            (release_id,),
        ).fetchone()
    assert row[0] is None
    assert row[1] == "no_rsid_match"


@pytest.mark.requires_db
@pytest.mark.parametrize("curated", [[GOOD]], indirect=True)
def test_provenance_columns_are_populated(curated) -> None:
    release_id, _ = curated
    with connect() as conn:
        row = conn.execute(
            "SELECT source_release_id, raw_measurement_id, curation_source, curator_version "
            "FROM activity WHERE source_release_id=%s",
            (release_id,),
        ).fetchone()
        raw_exists = conn.execute(
            "SELECT count(*) FROM raw_measurement WHERE id=%s", (row[1],)
        ).fetchone()[0]
    assert row[0] == release_id
    assert raw_exists == 1  # the raw row id is preserved and still resolves
    assert row[2] == "PDSP Ki"  # per-row Curation/DataSource kept for licensing
    assert row[3] == CURATOR_VERSION


@pytest.mark.requires_db
@pytest.mark.parametrize("curated", [ALL_ROWS], indirect=True)
def test_rerunning_curation_is_idempotent(curated) -> None:
    """A completed release must not be curated twice on a second run."""
    release_id, first = curated
    with connect() as conn:
        before = conn.execute(
            "SELECT count(*) FROM activity WHERE source_release_id=%s", (release_id,)
        ).fetchone()[0]

    second = curate_release(transaction, release_id, batch_size=100, workers=1)

    with connect() as conn:
        after = conn.execute(
            "SELECT count(*) FROM activity WHERE source_release_id=%s", (release_id,)
        ).fetchone()[0]
    assert second.rows_seen == 0  # nothing left to do
    assert after == before
    assert first.activities == before


@pytest.mark.requires_db
@pytest.mark.parametrize("curated", [ALL_ROWS], indirect=True)
def test_interrupted_curation_resumes_from_the_last_committed_row(curated) -> None:
    """Restart behaviour: a partial run leaves a marker and the next run continues."""
    release_id, _ = curated
    with transaction() as conn:
        conn.execute("DELETE FROM activity WHERE source_release_id=%s", (release_id,))
        conn.execute("DELETE FROM curation_exclusion WHERE source_release_id=%s", (release_id,))
        conn.execute(
            "UPDATE curation_run SET last_raw_id=0, rows_seen=0, rows_curated=0, "
            "rows_excluded=0, activities_written=0, finished_at=NULL "
            "WHERE source_release_id=%s",
            (release_id,),
        )

    partial = curate_release(transaction, release_id, batch_size=2, workers=1, max_batches=1)
    assert partial.rows_seen == 2
    with connect() as conn:
        marker = conn.execute(
            "SELECT last_raw_id FROM curation_run WHERE source_release_id=%s", (release_id,)
        ).fetchone()[0]
    assert marker > 0

    rest = curate_release(transaction, release_id, batch_size=2, workers=1)
    assert rest.rows_seen == len(ALL_ROWS) - 2
    with connect() as conn:
        total = conn.execute(
            "SELECT count(DISTINCT raw_measurement_id) FROM activity WHERE source_release_id=%s",
            (release_id,),
        ).fetchone()[0]
        excl = conn.execute(
            "SELECT count(*) FROM curation_exclusion WHERE source_release_id=%s", (release_id,)
        ).fetchone()[0]
    assert total + excl == len(ALL_ROWS)  # every row accounted for exactly once


@pytest.mark.requires_db
@pytest.mark.parametrize("curated", [[GOOD, NOVALUE]], indirect=True)
def test_duplicate_source_structures_share_one_compound(curated) -> None:
    """Both rows carry the same InChI Key; standardization must run once."""
    release_id, _ = curated
    with connect() as conn:
        n = conn.execute(
            "SELECT count(DISTINCT compound_id) FROM activity WHERE source_release_id=%s",
            (release_id,),
        ).fetchone()[0]
        cached = conn.execute(
            "SELECT count(*) FROM compound_source WHERE source_inchikey=%s",
            ("LFQSCWFLJHTTHZ-UHFFFAOYSA-N",),
        ).fetchone()[0]
    assert n == 1
    # One cache entry per source structure, so a repeat never re-standardizes.
    assert cached == 1
