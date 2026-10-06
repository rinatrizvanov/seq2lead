"""M11b: the cross-snapshot matcher, on synthetic fixtures and local data.

No second dataset is downloaded here, nothing is fitted, and nothing is docked.
The fixtures exist because a single snapshot cannot produce a correction or a
withdrawal -- it holds no revisions -- so those paths are unreachable from local
data alone and have to be constructed.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from seq2lead.asof.matching import (
    NORMALISATION_VERSION,
    diff_snapshots,
    normalise_value,
    slot_of,
    training_population,
)

INVENTORY = Path("configs/manifests/m11_archive_inventory.json")


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
    }


# ====================================================== slot / value separation


def test_the_slot_is_stable_when_the_value_changes() -> None:
    """The whole reason for two keys: a correction must not change the slot."""
    before, after = row(value="12"), row(value="21")
    assert slot_of(before) == slot_of(after)
    assert normalise_value(before) != normalise_value(after)


def test_the_slot_uses_an_external_publication_reference() -> None:
    """A local `publication_id` is meaningless across releases, so it is not used."""
    by_pmid = slot_of(row(pmid="999"))
    assert by_pmid.publication_ref == "pmid:999"
    no_pmid = slot_of({**row(pmid=None), "doi": "10.1021/jm9602571"})
    assert no_pmid.publication_ref == "doi:10.1021/JM9602571"
    patent = slot_of({**row(pmid=None), "doi": None, "patent_number": "US1234"})
    assert patent.publication_ref == "patent:US1234"
    assert slot_of({**row(pmid=None), "doi": None}).publication_ref == "unattributed"


def test_normalisation_is_insensitive_to_case_and_whitespace() -> None:
    assert slot_of(row(source=" bindingdb ")) == slot_of(row(source="BindingDB"))
    assert normalise_value(row(mtype="ki")) == normalise_value(row(mtype="KI"))


# ============================================================ (a) unchanged


def test_an_unchanged_observation_is_classified_unchanged() -> None:
    report = diff_snapshots([row()], [row()])
    assert report.summary()["unchanged_observations"] == 1
    assert report.summary()["added_observations"] == 0
    assert report.summary()["removed_observations"] == 0
    assert report.summary()["correction_candidates"] == 0


# ======================================================= (d) count increases


def test_a_count_increase_yields_exactly_one_addition() -> None:
    report = diff_snapshots([row(), row()], [row(), row(), row()])
    s = report.summary()
    assert s["unchanged_observations"] == 2
    assert s["added_observations"] == 1
    assert s["removed_observations"] == 0
    # repeated identical rows are not asserted to be duplicate experiments
    assert s["correction_candidates"] == 0


def test_a_count_decrease_yields_a_removal_not_a_correction() -> None:
    report = diff_snapshots([row(), row()], [row()])
    s = report.summary()
    assert s["removed_observations"] == 1
    assert s["correction_candidates"] == 0
    assert s["unresolved_removals"] == 1


# ================================================================ (c) removals


def test_a_withdrawn_slot_is_a_removed_slot() -> None:
    report = diff_snapshots([row()], [])
    s = report.summary()
    assert s["removed_slots"] == 1
    assert s["removed_observations"] == 1
    assert s["correction_candidates"] == 0, "a withdrawal is not a correction"
    assert s["unresolved_removals"] == 1


def test_a_brand_new_slot_is_a_new_slot() -> None:
    report = diff_snapshots([], [row()])
    assert report.summary()["new_slots"] == 1
    assert report.summary()["added_observations"] == 1


# ================================================ (b) provisional correction links


def test_a_correction_links_only_on_a_release_stable_identifier() -> None:
    before = row(value="12", entry_doi="10.7270/Q2ZW1J3M")
    after = row(value="21", entry_doi="10.7270/Q2ZW1J3M")
    report = diff_snapshots([before], [after])
    assert report.summary()["correction_candidates"] == 1
    candidate = report.correction_candidates[0]
    assert candidate.before.value_text == "12"
    assert candidate.after.value_text == "21"
    assert candidate.linked_by == "entry_doi"
    assert candidate.provisional is True, "no link may be asserted as settled"
    assert report.summary()["unresolved_additions"] == 0
    assert report.summary()["unresolved_removals"] == 0


def test_without_a_stable_identifier_it_stays_unresolved() -> None:
    """Two rows changing hands is not evidence that a value was corrected."""
    report = diff_snapshots([row(value="12")], [row(value="21")])
    s = report.summary()
    assert s["correction_candidates"] == 0
    assert s["unresolved_additions"] == 1
    assert s["unresolved_removals"] == 1


def test_a_mismatched_identifier_does_not_link() -> None:
    report = diff_snapshots(
        [row(value="12", entry_doi="10.7270/AAA")],
        [row(value="21", entry_doi="10.7270/BBB")],
    )
    assert report.summary()["correction_candidates"] == 0
    assert report.summary()["unresolved_additions"] == 1


def test_the_surrogate_can_link_but_is_recorded_as_such() -> None:
    report = diff_snapshots([row(value="12", rsid="555")], [row(value="21", rsid="555")])
    assert report.summary()["correction_candidates"] == 1
    assert report.correction_candidates[0].linked_by == "reactant_set_id"


# ================================================== ambiguous many-to-many


def test_an_ambiguous_many_to_many_change_is_never_linked() -> None:
    """Two removals and two additions at one slot: no one-to-one pairing exists."""
    report = diff_snapshots(
        [row(value="10", entry_doi="10.7270/X"), row(value="11", entry_doi="10.7270/X")],
        [row(value="20", entry_doi="10.7270/X"), row(value="21", entry_doi="10.7270/X")],
    )
    s = report.summary()
    assert s["correction_candidates"] == 0, "a many-to-many set was linked"
    assert s["ambiguous_slots"] == 1
    assert s["unresolved_additions"] == 2
    assert s["unresolved_removals"] == 2


def test_unresolved_candidates_stay_separately_identifiable() -> None:
    # two DIFFERENT slots: one pair linkable, one not. Putting both at the same
    # slot would make it a 2x2 ambiguous set, which is a different case (above).
    report = diff_snapshots(
        [row(pmid="111", value="12"), row(pmid="222", value="99", entry_doi="10.7270/Z")],
        [row(pmid="111", value="21"), row(pmid="222", value="98", entry_doi="10.7270/Z")],
    )
    body = json.loads(report.to_json())
    assert body["correction_candidates"] == 1
    assert body["unresolved_additions"] == 1
    assert body["unresolved_removals"] == 1
    assert len(body["correction_candidates_detail"]) == 1
    assert body["correction_candidates_detail"][0]["provisional"] is True


# ============================================= determinism and training isolation


def test_the_diff_is_independent_of_input_order() -> None:
    a = [row(value="1"), row(value="2"), row(value="2")]
    b = [row(value="2"), row(value="1"), row(value="2")]
    assert diff_snapshots(a, b).summary() == diff_snapshots(list(reversed(a)), b).summary()
    assert diff_snapshots(a, b).summary() == diff_snapshots(a, list(reversed(b))).summary()


def test_the_training_population_cannot_see_the_later_snapshot() -> None:
    """Structural, not remembered: the function takes one snapshot."""
    import inspect

    params = list(inspect.signature(training_population).parameters)
    assert params == ["earlier"], f"training_population takes {params}"


@pytest.mark.parametrize(
    "later",
    [
        [],  # everything withdrawn
        [row()],  # unchanged
        [row(), row()],  # additions
        [row(value="21", entry_doi="10.7270/Q")],  # a correction
    ],
)
def test_training_is_invariant_to_whatever_the_later_snapshot_says(later) -> None:
    earlier = [row(entry_doi="10.7270/Q")]
    baseline = training_population(earlier)
    diff_snapshots(earlier, later)
    assert training_population(earlier) == baseline
    assert baseline["n_observations"] == 1
    assert baseline["normalisation_version"] == NORMALISATION_VERSION


def test_the_training_digest_changes_only_with_the_training_data() -> None:
    one = training_population([row()])
    assert training_population([row()]) == one
    assert training_population([row(), row()])["digest"] != one["digest"]
    assert training_population([row(value="21")])["digest"] != one["digest"]


# ====================================== the date cut is a proxy, and says so


def test_a_date_cut_exercise_must_declare_itself_a_proxy() -> None:
    report = diff_snapshots(
        [row()],
        [row(), row(value="7")],
        is_single_snapshot_proxy=True,
        proxy_note="202609 cut at bindingdb_date; not an archive comparison",
    )
    body = json.loads(report.to_json())
    assert body["is_single_snapshot_proxy"] is True
    assert "not an archive comparison" in body["proxy_note"]


def test_a_real_comparison_is_not_flagged_as_a_proxy() -> None:
    assert diff_snapshots([row()], [row()]).summary()["is_single_snapshot_proxy"] is False


@pytest.mark.requires_db
def test_the_local_date_cut_proxy_runs_and_is_labelled() -> None:
    """Exercises the matcher on real rows. A proxy, and flagged as one."""
    from seq2lead.db import connect

    with connect() as conn:
        rows = conn.execute(
            """
            SELECT c.inchikey, t.sequence_sha256, p.pmid, p.doi, p.patent_number,
                   a.ph_text, a.temp_c_text, a.curation_source,
                   a.measurement_type, a.relation, a.value_text,
                   a.reactant_set_id, a.bindingdb_date
            FROM activity a
              JOIN compound c ON c.id = a.compound_id
              JOIN target t ON t.id = a.target_id
              LEFT JOIN publication p ON p.id = a.publication_id
            WHERE a.measurement_type = 'KI'
              AND a.bindingdb_date ~ '^\\d{1,2}/\\d{1,2}/\\d{4}$'
              AND substring(a.bindingdb_date from '(\\d{4})$')::int = 2026
            LIMIT 4000
            """
        ).fetchall()
    keys = (
        "inchikey",
        "sequence_sha256",
        "pmid",
        "doi",
        "patent_number",
        "ph_text",
        "temp_c_text",
        "curation_source",
        "measurement_type",
        "relation",
        "value_text",
        "reactant_set_id",
        "bindingdb_date",
    )
    records = [dict(zip(keys, r, strict=True)) for r in rows]
    if len(records) < 100:
        pytest.skip("too few local rows for the proxy")

    def month(rec):
        return int(rec["bindingdb_date"].split("/")[0])

    earlier = [r for r in records if month(r) <= 4]
    later = records
    report = diff_snapshots(
        earlier,
        later,
        is_single_snapshot_proxy=True,
        proxy_note="single 202609 snapshot cut at bindingdb_date month <= 4",
    )
    s = report.summary()
    assert s["is_single_snapshot_proxy"] is True
    assert s["added_observations"] > 0, "the later side should add observations"
    assert s["removed_observations"] == 0, (
        "a date cut is a superset relation: one snapshot cannot withdraw anything"
    )
    assert s["correction_candidates"] == 0, (
        "a single snapshot holds no revisions, so any correction here would be spurious"
    )
    # training is a function of the earlier side alone
    assert training_population(earlier)["digest"] == training_population(earlier)["digest"]


# ========================================================= inventory artifact


def test_the_archive_inventory_records_what_it_must() -> None:
    inv = json.loads(INVENTORY.read_text(encoding="utf-8"))
    assert inv["inventory_version"] == "m11-archive-inventory-v3"
    assert "metadata only" in inv["method"]
    deposits = inv["quarterly_deposits"]
    assert len(deposits) == 11
    for d in deposits:
        assert d["doi"].startswith("10.6075/")
        assert d["version_date"]
        assert d["landing_url"]
        assert d["license"], "every deposit must carry its declared licence"


def test_the_inventory_records_per_file_sizes_and_digests() -> None:
    """v3 resolved these from the public object endpoint; v2 had them as null."""
    inv = json.loads(INVENTORY.read_text(encoding="utf-8"))
    for d in inv["quarterly_deposits"]:
        assert d["object_id"], "every deposit needs its DAMS object id"
        assert d["bytes"] and d["bytes"] > 0
        assert d["checksums"] == {"algorithm": "SHA-1", "per_file": True}
        assert d["file_names"]
        assert isinstance(d["auxiliary_files_confirmed"], dict)
        for f in d["files"]:
            assert f["bytes"] > 0
            assert f["sha1"], f"{f['name']} has no digest"
            assert f["checksum_algorithm"] == "SHA-1"
        measurement = d["measurement_file"]
        assert measurement and "BindingDB_All_" in measurement["name"]


def test_the_inventory_still_names_what_remains_unresolved() -> None:
    inv = json.loads(INVENTORY.read_text(encoding="utf-8"))
    unresolved = inv["unresolved_fields"]
    for field in (
        "2026-04-01",
        "ki_share_across_releases",
        "release_specific_licensing",
        "zip_member_listings",
        "sha256",
    ):
        assert field in unresolved and unresolved[field]
    assert "SHA-1" in unresolved["sha256"]
    assert "not described by this metadata" in unresolved["zip_member_listings"]


def test_the_inventory_records_how_access_was_obtained() -> None:
    inv = json.loads(INVENTORY.read_text(encoding="utf-8"))
    access = inv["access_method"]
    assert "/dc/object/" in access["endpoint"]
    assert "HEAD only" in access["download_verified_by"]
    assert "swaps" in access["checksum_field_quirk"]


def test_only_two_deposits_can_reproduce_our_curation() -> None:
    """The three-way assay join needs both auxiliary files; nine deposits lack them."""
    inv = json.loads(INVENTORY.read_text(encoding="utf-8"))
    compat = inv["curation_compatibility"]
    assert compat["deposits_with_both_auxiliary_files"] == ["2026-01-01", "2026-07-01"]
    assert len(compat["deposits_without"]) == 9
    for d in inv["quarterly_deposits"]:
        aux = d["auxiliary_files_confirmed"]
        expected = d["version_date"] in compat["deposits_with_both_auxiliary_files"]
        assert aux["assays"] is expected
        assert aux["rsid_eaids"] is expected
        assert aux["target_fasta"] is False, "no deposit carries the FASTA"


def test_the_missing_fasta_is_shown_not_to_block_curation() -> None:
    inv = json.loads(INVENTORY.read_text(encoding="utf-8"))
    fasta = inv["curation_compatibility"]["target_fasta"]
    assert fasta["present_in_any_deposit"] is False
    assert fasta["blocks_curation"] is False
    assert "Target Chain Sequence 1" in fasta["why_not"]
    assert "never read downstream" in fasta["why_not"]


def test_the_inventory_records_the_licence_discrepancy() -> None:
    inv = json.loads(INVENTORY.read_text(encoding="utf-8"))
    finding = inv["license_finding"]
    assert "4.0" in finding["deposit_declared"]
    assert "CC-BY-3.0" in finding["our_source_release_rows_say"]
    assert "stale" in finding["resolution"]


def test_the_inventory_reports_a_search_result_not_an_absence() -> None:
    """An earlier version called 2026-04-01 absent. We only know our search missed it."""
    inv = json.loads(INVENTORY.read_text(encoding="utf-8"))
    assert inv["cadence_gaps"] == [], "an absence must not be asserted"
    observed = inv["cadence_observations"]
    assert observed["not_found_by_the_recorded_search"] == ["2026-04-01"]
    assert observed["search_used"]
    assert "not about the archive" in observed["note"]
    dates = {d["version_date"] for d in inv["quarterly_deposits"]}
    assert "2026-04-01" not in dates
    assert "2026-01-01" in dates and "2026-07-01" in dates


def test_the_inventory_calls_growth_approximate_net_not_an_increment() -> None:
    inv = json.loads(INVENTORY.read_text(encoding="utf-8"))
    growth = inv["growth_between_deposits"]
    assert "net growth" in growth["what_this_is"]
    assert "not a count of added measurements" in growth["what_this_is"]
    assert growth["only_a_real_diff_settles_these"] is True
    assumptions = " ".join(growth["unmeasured_assumptions"]).lower()
    for unmeasured in ("ki-specific growth", "back-fill", "withdrawals", "eligible fraction"):
        assert unmeasured in assumptions, f"{unmeasured} is not named as unmeasured"
    for row in growth["approximate_net_growth"]:
        assert "approx_net" in row, "the field name must not imply an exact increment"


def test_the_inventory_does_not_claim_to_know_archive_contents() -> None:
    """Sizes and digests are of the deposited ZIPs, not of their members."""
    inv = json.loads(INVENTORY.read_text(encoding="utf-8"))
    for d in inv["quarterly_deposits"]:
        assert "n_zip_members_declared" not in d, "that field name overclaimed"
        assert "n_deposited_zip_files" in d
        assert "rounded at source" in d["approx_measurements_millions_note"]
    assert "zip_member_listings" in inv["unresolved_fields"]
    assert "ki_share_across_releases" in inv["unresolved_fields"]


def test_the_inventory_records_that_past_monthly_files_are_gone() -> None:
    inv = json.loads(INVENTORY.read_text(encoding="utf-8"))
    probed = {p["release"]: p["http"] for p in inv["live_monthly_channel"]["probed"]}
    assert probed["202610"] == 200
    assert probed["202609"] == 404, "our own pinned release must be recorded as unretrievable"
    assert inv["locally_held"]["release"] == "202609"


# =========================== declared numeric normalisation (correction pass)


@pytest.mark.parametrize(
    "spellings",
    [
        ["12", "12.0", "12.00", "1.2e1", "1.2E+1", " 12 ", "+12"],
        ["0.001", "1e-3", "1.0E-3"],
        ["1000", "1E3", "1.0e+3"],
        ["0", "0.0", "-0", "0E0"],
    ],
)
def test_equivalent_numeric_spellings_collapse_to_one_value(spellings) -> None:
    keys = {normalise_value(row(value=s)) for s in spellings}
    assert len(keys) == 1, f"{spellings} produced {len(keys)} distinct values"


@pytest.mark.parametrize(
    ("a", "b"),
    [("12", "12.5"), ("12", "120"), ("0.001", "0.01"), ("25", "-25"), ("1e3", "1e4")],
)
def test_genuinely_different_quantities_stay_distinct(a, b) -> None:
    assert normalise_value(row(value=a)) != normalise_value(row(value=b))


def test_equivalent_ph_and_temperature_spellings_are_one_slot() -> None:
    assert slot_of(row(ph="7.4")) == slot_of(row(ph="7.40"))
    assert slot_of(row(temp="25")) == slot_of(row(temp="25.000"))
    assert slot_of(row(temp="2.5e1")) == slot_of(row(temp="25"))


def test_different_ph_and_temperature_are_different_slots() -> None:
    assert slot_of(row(ph="7.4")) != slot_of(row(ph="7.5"))
    assert slot_of(row(temp="25")) != slot_of(row(temp="37"))


def test_the_original_text_is_retained_beside_the_canonical_form() -> None:
    value = normalise_value(row(value="1.2e1"))
    assert value.value_text == "12"
    assert value.value_original == "1.2e1"
    slot = slot_of(row(ph="7.40", temp="25.000"))
    assert (slot.ph, slot.ph_original) == ("7.4", "7.40")
    assert (slot.temp_c, slot.temp_c_original) == ("25", "25.000")


def test_units_are_declared_and_not_inferred() -> None:
    from seq2lead.asof.normalise import DECLARED_UNITS

    assert set(DECLARED_UNITS) == {"value", "ph", "temp_c"}
    assert "nM" in DECLARED_UNITS["value"]
    assert "Celsius" in DECLARED_UNITS["temp_c"]
    assert "dimensionless" in DECLARED_UNITS["ph"]


def test_a_unit_suffix_is_flagged_rather_than_stripped() -> None:
    """Stripping `nM` would equate quantities that may differ by orders of magnitude."""
    from seq2lead.asof.normalise import normalise_number

    bearing = normalise_number("12 nM", "value")
    assert bearing.ok is False
    assert bearing.original == "12 nM"
    assert "not stripped" in bearing.note
    # and it must not collide with the bare number
    assert bearing.key() != normalise_number("12", "value").key()


def test_an_unparseable_value_matches_only_an_identical_spelling() -> None:
    from seq2lead.asof.normalise import normalise_number

    assert normalise_number("abc", "value").key() == normalise_number("abc", "value").key()
    assert normalise_number("abc", "value").key() != normalise_number("abd", "value").key()


def test_a_relation_prefix_is_not_part_of_the_number() -> None:
    from seq2lead.asof.normalise import normalise_number

    assert normalise_number(">10000", "value").key() == "10000"
    assert normalise_number("<1", "value").key() == "1"


def test_normalisation_version_is_recorded_in_the_export() -> None:
    body = json.loads(diff_snapshots([row()], [row()]).to_json())
    assert body["normalisation_version"] == NORMALISATION_VERSION
    assert body["declared_units"]


def test_a_rewritten_spelling_is_not_mistaken_for_a_correction() -> None:
    """`12` -> `12.0` is the same quantity and must produce no change at all."""
    report = diff_snapshots(
        [row(value="12", entry_doi="10.7270/Q")],
        [row(value="12.0", entry_doi="10.7270/Q")],
    )
    s = report.summary()
    assert s["unchanged_observations"] == 1
    assert s["correction_candidates"] == 0
    assert s["added_observations"] == 0 and s["removed_observations"] == 0


# ================== entry DOI authority, no silent surrogate fallback


def test_disagreeing_entry_dois_do_not_fall_back_to_the_surrogate() -> None:
    """The defect: a shared reactant_set_id linked rows whose entry DOIs differed."""
    report = diff_snapshots(
        [row(value="12", entry_doi="10.7270/AAA", rsid="555")],
        [row(value="21", entry_doi="10.7270/BBB", rsid="555")],
    )
    s = report.summary()
    assert s["correction_candidates"] == 0, "a shared surrogate outvoted two entry DOIs"
    assert s["identifier_conflicts"] == 1
    conflict = report.identifier_conflicts[0]
    assert conflict.before_entry_doi == "10.7270/AAA"
    assert conflict.after_entry_doi == "10.7270/BBB"
    assert conflict.surrogate_agreed is True


def test_identifier_conflicts_are_recorded_separately_from_unresolved_changes() -> None:
    report = diff_snapshots(
        [row(value="12", entry_doi="10.7270/AAA", rsid="555")],
        [row(value="21", entry_doi="10.7270/BBB", rsid="555")],
    )
    body = json.loads(report.to_json())
    assert body["identifier_conflicts"] == 1
    detail = body["identifier_conflicts_detail"][0]
    assert detail["surrogate_agreed"] is True
    assert "deliberately not allowed to decide" in detail["note"]
    # a conflict is not merely an ambiguous slot
    assert body["ambiguous_slots"] == 0


def test_the_surrogate_still_links_when_entry_dois_cannot_decide() -> None:
    report = diff_snapshots([row(value="12", rsid="555")], [row(value="21", rsid="555")])
    assert report.summary()["correction_candidates"] == 1
    assert report.correction_candidates[0].linked_by == "reactant_set_id"
    assert report.summary()["identifier_conflicts"] == 0


def test_one_sided_entry_doi_falls_through_to_the_surrogate() -> None:
    report = diff_snapshots(
        [row(value="12", entry_doi="10.7270/AAA", rsid="555")],
        [row(value="21", rsid="555")],
    )
    assert report.summary()["correction_candidates"] == 1
    assert report.correction_candidates[0].linked_by == "reactant_set_id"


def test_the_entry_doi_is_documented_as_entry_level_provenance() -> None:
    """It is not an individual-measurement identity, and its stability is unmeasured."""
    from seq2lead.asof.matching import Observation

    doc = Observation.__doc__ or ""
    assert "entry-level provenance" in doc
    assert "not** an individual-measurement identity" in doc
    assert "unmeasured" in doc
    assert "release-stable identifier, which is what makes it usable" not in doc


# ======================= counted, auditable detail for every change class


def test_additions_are_exported_with_counts_and_provenance() -> None:
    """Counts alone do not make a change separately identifiable."""
    body = json.loads(
        diff_snapshots(
            [row(value="9")],
            [row(value="9"), row(value="77", entry_doi="10.7270/Z", rsid="7")],
        ).to_json()
    )
    assert body["added_observations"] == 1
    detail = body["additions_detail"]
    assert len(detail) == 1
    only = detail[0]
    assert only["count"] == 1
    assert only["value"].endswith("77")
    assert only["entry_doi"] == "10.7270/Z"
    assert only["reactant_set_id"] == "7"
    assert only["slot"]


def test_removals_are_exported_with_counts_and_provenance() -> None:
    body = json.loads(diff_snapshots([row(value="5", rsid="3")], []).to_json())
    assert body["removed_observations"] == 1
    assert body["removals_detail"][0]["reactant_set_id"] == "3"
    assert body["removals_detail"][0]["count"] == 1


def test_repeated_rows_are_exported_as_a_count_not_duplicated_entries() -> None:
    body = json.loads(diff_snapshots([], [row(), row(), row()]).to_json())
    detail = body["additions_detail"]
    assert len(detail) == 1, "identical rows should group"
    assert detail[0]["count"] == 3


def test_unresolved_changes_carry_the_provenance_needed_to_audit_them() -> None:
    body = json.loads(
        diff_snapshots([row(value="12", rsid="1")], [row(value="21", rsid="2")]).to_json()
    )
    assert body["unresolved_additions"] == 1 and body["unresolved_removals"] == 1
    add = body["unresolved_additions_detail"][0]
    rem = body["unresolved_removals_detail"][0]
    # an auditor can see exactly why no link was made: the identifiers differ
    assert rem["reactant_set_id"] == "1"
    assert add["reactant_set_id"] == "2"
    assert rem["slot"] == add["slot"], "same slot, so a link was at least plausible"


def test_the_detail_export_is_byte_deterministic() -> None:
    a = [row(value="1"), row(value="2"), row(value="2")]
    b = [row(value="2"), row(value="3")]
    first = diff_snapshots(a, b).to_json()
    assert diff_snapshots(list(reversed(a)), list(reversed(b))).to_json() == first


def test_original_spellings_appear_in_the_detail_for_reporting() -> None:
    body = json.loads(diff_snapshots([], [row(value="1.2e1")]).to_json())
    only = body["additions_detail"][0]
    assert only["value"].endswith("12"), "the key is canonical"
    assert only["value_original"] == "1.2e1", "the original is reported"
