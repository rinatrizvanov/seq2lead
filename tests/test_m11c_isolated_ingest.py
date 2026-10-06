"""The isolated-ingest record: does the report agree with the manifest?

A report and a manifest that disagree are worse than either alone, because a
reader cannot tell which is stale. These assertions tie the figures that matter
to the machine-readable record, so a later edit to one has to touch both.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

MANIFEST = Path("configs/manifests/m11c_isolated_ingest_202601.json")
ACQUISITION = Path("configs/manifests/m11_acquisition_202601.json")
REPORT = Path("reports/m11c_isolated_ingest.md")

#: subset -> (acquisition release id, isolated release id, rows loaded)
EXPECTED_IDENTITIES = {
    "all": (1203, 3, 3_140_596),
    "assays": (1204, 1, 218_973),
    "rsid_eaids": (1205, 2, 3_107_915),
}


@pytest.fixture(scope="module")
def manifest() -> dict:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def report() -> str:
    return REPORT.read_text(encoding="utf-8")


def test_the_manifest_ties_every_isolated_release_to_its_acquisition_record(manifest) -> None:
    """The two sets of ids are surrogates; the digest is what identifies the artifact."""
    rows = {r["subset"]: r for r in manifest["release_identity_map"]}
    assert set(rows) == set(EXPECTED_IDENTITIES)
    acquisition = {
        a["subset"]: a["sha256"]
        for a in json.loads(ACQUISITION.read_text(encoding="utf-8"))["artifacts"]
    }
    for subset, (acq_id, iso_id, rows_loaded) in EXPECTED_IDENTITIES.items():
        row = rows[subset]
        assert row["acquisition_release_id"] == acq_id
        assert row["isolated_release_id"] == iso_id
        assert row["rows_loaded"] == rows_loaded
        assert row["artifact_identity"] == f"BindingDB/202601/{subset}"
        assert row["sha256_matches_acquisition_record"] is True
        # Not just the flag: the digest itself must equal the acquired one.
        assert row["sha256"] == acquisition[subset]


def test_the_acquisition_record_is_declared_unchanged(manifest) -> None:
    assert manifest["acquisition_record"]["unchanged_by_this_step"] is True
    assert manifest["bytes_reverified"]["pins_changed"] is False
    assert manifest["bytes_reverified"]["downloads_performed"] == 0


def test_the_accepted_acquisition_rows_are_still_unparsed(manifest) -> None:
    """The evidence that acquisition happened without parsing must survive."""
    rows = manifest["accepted_artifacts_unchanged"]["acquisition_rows_in_accepted_schema"]
    assert {r["id"] for r in rows} == {1203, 1204, 1205}
    for row in rows:
        assert row["ingested"] is False, f"release {row['id']} is no longer unparsed"
        assert row["rows_loaded"] == 0


def test_the_accepted_corpus_row_count_is_unchanged(manifest) -> None:
    """3,233,963 is the figure in the accepted `reports/curation.md`."""
    assert manifest["accepted_artifacts_unchanged"]["202609_activity_rows"] == 3_233_963


def test_the_curation_reconciles_in_the_manifest(manifest) -> None:
    c = manifest["curation"]
    assert c["rows_curated"] + c["rows_excluded"] == c["rows_seen"] == 3_140_596
    assert c["reconciles"] is True
    assert c["policy"] == "m3/v3+rdkit-2026.03.6/cleanup+fragment-parent+uncharge/v1"
    assert sum(c["exclusions_by_rule"].values()) == c["rows_excluded"]


def test_the_exports_record_full_locator_coverage(manifest) -> None:
    exports = manifest["observation_exports"]
    assert set(exports) == {"202601", "202609"}
    for label, expected_rows in (("202601", 3_136_836), ("202609", 3_233_963)):
        e = exports[label]
        assert e["rows"] == expected_rows
        assert e["with_locator"] == expected_rows, "a row without a locator is untraceable"
        assert e["locator_coverage"] == 1.0
        assert sum(e["measurement_types"].values()) == expected_rows
        assert len(e["sha256"]) == 64


def test_entry_doi_is_recorded_as_distinct_from_the_publication_doi(manifest) -> None:
    entry = manifest["entry_doi"]
    assert entry["source_column"] == "BindingDB Entry DOI"
    assert "Article DOI" in entry["distinct_from"]
    assert "publication.doi" in entry["distinct_from"]


def test_the_report_records_the_reconciliations(report) -> None:
    for figure in (
        "3,130,970 + 9,626 = 3,140,596",  # January raw -> curated
        "3,228,136 + 8,910 = 3,237,046",  # the accepted September reconciliation
        "3,136,836",  # January observations
        "3,233,963",  # September observations
        "100.000%",  # locator coverage
    ):
        assert figure in report, f"the report does not state: {figure}"


def test_the_report_keeps_the_non_monotone_finding(report) -> None:
    """The EC50 decrease is the finding most tempting to drop. It must stay.

    A later snapshot holding *fewer* rows of one measurement type is the evidence
    that removals and corrections had to be modelled at all. A revision that
    quietly rounded it away would remove the justification for a chunk of the
    design.
    """
    assert "10,137" in report
    assert "not monotone" in report
    # And it must not be presented as a settled cause.
    assert "does not claim a cause" in report


def test_the_report_keeps_the_study_exploratory_and_the_freeze_unsigned(report) -> None:
    assert "exploratory" in report
    assert "not signed" in report
    assert "No model was fitted" in report
    assert "nine remain open" in report.lower()


def test_the_memory_constraint_on_the_next_step_is_recorded(report) -> None:
    """A measured blocker, not an estimate, and the next step depends on it."""
    assert "15.9 GB" in report
    assert "25.3 GB" in report
    assert "6,370,799" in report


def test_the_report_records_that_both_snapshots_ran_the_same_curator(report) -> None:
    """Without this, the exclusion comparison means nothing.

    A rule firing 99 times on one snapshot and 0 times on the other is only a
    statement about the data if the rule existed and ran on both. Had it been
    added after September was curated, the row would have measured our tooling.
    """
    assert "identical\ncurator" in report or "identical curator" in report
    assert "m3/v3+rdkit-2026.03.6/cleanup+fragment-parent+uncharge/v1" in report
    assert "not an artifact of a rule that only existed for one of them" in report


def test_the_manifest_records_curator_parity_between_the_snapshots(manifest) -> None:
    """The exclusion comparison is only about the data if the curator matched."""
    c = manifest["curation"]
    assert c["policies_identical"] is True
    assert c["policy"] == c["accepted_snapshot_policy"]
    assert "not in the tooling" in c["policy_parity_note"]


def test_the_manifest_records_the_measured_entry_doi_grain(manifest) -> None:
    """Entry-level, measured on both sides, with stability still unmeasured."""
    grain = manifest["entry_doi"]["measured_grain"]
    assert set(grain) == {"202601", "202609"}
    for label, per in grain.items():
        assert per["observations_per_entry_doi"] > 50, (
            f"{label}: an entry DOI covering ~1 observation would make it a "
            "measurement identifier, which the matcher must not assume"
        )
        assert per["coverage"] > 0.99
        assert per["with_entry_doi"] > per["distinct_entry_dois"]
    assert manifest["entry_doi"]["cross_release_stability"].startswith("UNMEASURED")


def test_the_manifest_names_each_checksum_algorithm(manifest) -> None:
    """A 40-hex SHA-1 in a column called `md5` must say which it is.

    The 202609 rows carry real published MD5s with `md5_verified = true`; the
    202601 rows carry the archive's SHA-1. The algorithm therefore cannot be
    inferred from the column name or the release version, so it is stored in the
    value and recorded here.
    """
    c = manifest["checksums"]
    assert c["pinned_algorithm"] == "SHA-256"
    assert c["archive_published_algorithm"] == "SHA-1"
    assert c["legacy_md5_column"]["md5_verified"] is False
    for subset, row in c["per_artifact"].items():
        assert len(row["sha256_pinned"]) == 64, subset
        assert len(row["sha1_archive"]) == 40, subset
        assert row["md5_column_value"] == f"sha1:{row['sha1_archive']}"
        # The stored value must not be mistakable for an MD5.
        assert len(row["md5_column_value"]) != 32


def test_the_report_measures_stereochemistry_retention_rather_than_asserting_it(
    report,
) -> None:
    """ "Stereochemistry is preserved" is a claim about a standardizer, not a given.

    `cleanup + fragment-parent + uncharge` could strip a stereocentre along with a
    counterion, and the diff would then read a lost stereocentre as a new compound.
    The report carries a sampled measurement and says it is sampled.
    """
    assert "99.85%" in report
    assert "20,000-row sample" in report
    assert "Sampled, and said so" in report
