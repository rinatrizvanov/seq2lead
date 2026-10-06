"""The acquired January 2026 deposit: provenance, not ingest.

These assert what acquisition established and, just as importantly, what it did
not. A registered artifact that looked ingested would be the worst outcome here,
so the unparsed markers are tested as carefully as the digests.
"""

from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

import pytest

MANIFEST = Path("configs/manifests/m11_acquisition_202601.json")
REPORT = Path("reports/m11_acquisition.md")
INVENTORY = Path("configs/manifests/m11_archive_inventory.json")
SUBSETS = ("all", "assays", "rsid_eaids")


@pytest.fixture(scope="module")
def manifest():
    if not MANIFEST.exists():
        pytest.skip("no acquisition manifest")
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def test_the_manifest_records_the_deposit_identity(manifest) -> None:
    deposit = manifest["deposit"]
    assert deposit["doi"] == "10.6075/j0v40w61"
    assert deposit["version_date"] == "2026-01-01"
    assert deposit["release_version_label"] == "202601"
    assert deposit["dams_object_id"] == "bb6055793n"


def test_every_artifact_verified_against_the_recorded_sha1(manifest) -> None:
    """The digest was recorded before download; it was not adjusted to fit."""
    assert len(manifest["artifacts"]) == 3
    for art in manifest["artifacts"]:
        assert art["sha1_verified"] is True, art["file_name"]
        assert art["actual_sha1"] == art["expected_sha1_from_inventory"]
        assert art["bytes_match"] is True
        assert art["actual_bytes"] == art["expected_bytes_from_inventory"]


def test_the_expected_digests_match_the_pre_download_inventory(manifest) -> None:
    """Cross-check against the inventory, not against the manifest's own copy."""
    inv = json.loads(INVENTORY.read_text(encoding="utf-8"))
    deposit = next(d for d in inv["quarterly_deposits"] if d["version_date"] == "2026-01-01")
    by_name = {f["name"]: f for f in deposit["files"]}
    for art in manifest["artifacts"]:
        recorded = by_name[art["file_name"]]
        assert art["expected_sha1_from_inventory"] == recorded["sha1"]
        assert art["expected_bytes_from_inventory"] == recorded["bytes"]


def test_the_files_on_disk_still_match_their_recorded_digests(manifest) -> None:
    for art in manifest["artifacts"]:
        path = Path(art["local_path"])
        if not path.exists():
            pytest.skip(f"{path} not present locally")
        h = hashlib.sha256()
        with path.open("rb") as fh:
            for block in iter(lambda: fh.read(1 << 20), b""):
                h.update(block)
        assert h.hexdigest() == art["sha256"], f"{art['file_name']} bytes changed"


def test_archive_members_were_observed_not_assumed(manifest) -> None:
    """The names our ingest expects, read out of the archives themselves."""
    expected = {
        "all": "BindingDB_All.tsv",
        "assays": "BindingDB_Assays.tsv",
        "rsid_eaids": "BindingDB_rsid_eaids.tsv",
    }
    for art in manifest["artifacts"]:
        assert art["archive_integrity_ok"] is True
        assert len(art["members"]) == 1
        member = art["members"][0]
        assert member["name"] == expected[art["subset"]]
        assert member["uncompressed_bytes"] > 0
        assert member["crc32"]


def test_the_archives_still_pass_a_crc_check(manifest) -> None:
    for art in manifest["artifacts"]:
        path = Path(art["local_path"])
        if not path.exists():
            pytest.skip(f"{path} not present locally")
        with zipfile.ZipFile(path) as z:
            assert z.testzip() is None, f"{art['file_name']} is corrupt"
            names = [i.filename for i in z.infolist()]
        assert names == [m["name"] for m in art["members"]]


# ===================================== one deposit, three artifact identities


def test_the_release_structure_is_documented(manifest) -> None:
    structure = manifest["release_structure"]
    assert "(source_name, version, subset)" in structure["relationship"]
    assert structure["deposit_key"] == "source_name='BindingDB', version='202601'"
    assert set(structure["registered_ids"]) == set(SUBSETS)


@pytest.mark.requires_db
def test_the_deposit_is_one_version_with_three_subsets() -> None:
    from seq2lead.db import connect

    with connect() as conn:
        rows = conn.execute(
            "SELECT subset, archive_member, sha256, md5, archive_bytes, member_bytes "
            "FROM source_release WHERE source_name='BindingDB' AND version='202601' "
            "ORDER BY subset"
        ).fetchall()
    assert [r[0] for r in rows] == sorted(SUBSETS)
    # each artifact carries its own identity, not the deposit's
    assert len({r[2] for r in rows}) == 3, "artifacts share a sha256"
    assert len({r[1] for r in rows}) == 3, "artifacts share an archive_member"
    assert len({r[4] for r in rows}) == 3, "artifacts share an archive_bytes"


@pytest.mark.requires_db
def test_nothing_is_marked_successfully_ingested() -> None:
    """Registered provenance must not look like a completed ingest."""
    from seq2lead.db import connect

    with connect() as conn:
        rows = conn.execute(
            "SELECT subset, ingested_at, rows_loaded, column_names "
            "FROM source_release WHERE source_name='BindingDB' AND version='202601'"
        ).fetchall()
    assert rows
    for subset, ingested_at, rows_loaded, column_names in rows:
        assert ingested_at is None, f"{subset} is marked ingested"
        assert rows_loaded == 0, f"{subset} claims {rows_loaded} rows loaded"
        assert column_names == [], f"{subset} claims parsed columns"


@pytest.mark.requires_db
def test_the_sha1_is_labelled_with_its_algorithm() -> None:
    """The `md5` column is NOT NULL; a bare SHA-1 there would be misread."""
    from seq2lead.db import connect

    with connect() as conn:
        rows = conn.execute(
            "SELECT subset, md5 FROM source_release WHERE source_name='BindingDB' "
            "AND version='202601'"
        ).fetchall()
    for subset, value in rows:
        assert value.startswith("sha1:"), f"{subset} stores an unlabelled digest: {value!r}"
        assert len(value) == len("sha1:") + 40


@pytest.mark.requires_db
def test_the_accepted_202609_release_is_untouched() -> None:
    from seq2lead.db import connect

    with connect() as conn:
        rows = conn.execute(
            "SELECT subset, sha256, rows_loaded, ingested_at IS NOT NULL "
            "FROM source_release WHERE source_name='BindingDB' AND version='202609' "
            "ORDER BY subset"
        ).fetchall()
    assert len(rows) == 5
    for subset, _sha, rows_loaded, ingested in rows:
        assert ingested is True, f"202609/{subset} lost its ingested marker"
        assert rows_loaded > 0, f"202609/{subset} lost its row count"
    assert {r[0] for r in rows} == {"all", "assays", "pdspki", "rsid_eaids", "target_sequences"}


# ================================ what acquisition did NOT establish


def test_the_report_separates_facts_from_assumptions() -> None:
    body = REPORT.read_text(encoding="utf-8")
    assert "## Verified facts" in body
    assert "## Remaining assumptions" in body
    for assumption in (
        "TSV structure is unread",
        "Row counts are unknown",
        "Licence at deposit level only",
        "Entry DOI and locator columns",
    ):
        assert assumption in body, f"the report does not record: {assumption}"


def test_the_report_keeps_the_study_exploratory(manifest) -> None:
    body = REPORT.read_text(encoding="utf-8")
    assert "exploratory" in body
    assert "freeze is **not signed**" in body or "not signed" in body
    assert "historical" in body
    assert "does not resolve" in body.lower() or "does nothing for the requirement" in body
    assert "not signed" in manifest["scope"]


def test_the_freeze_is_still_unsigned() -> None:
    assert "NOT SIGNED" in Path("docs/M11.md").read_text(encoding="utf-8")


def test_acquisition_records_agree_with_their_lifecycle_state() -> None:
    """Unparsed releases hold no derived rows; ingested ones reconcile instead.

    A flat "the 202601 release must have zero rows" was the right assertion while
    acquisition was the whole story: nothing had been parsed, so any derived row
    meant something had run that was not supposed to. It stops being right the
    moment a release is deliberately ingested. In the isolated M11c schema the
    January artifacts are *meant* to have rows, and a test demanding zero would
    assert that the next milestone never happened.

    The invariant that survives the transition is the one actually worth holding:
    a release's declared state and its derived rows agree. Still unparsed means
    still empty -- which is what keeps the untouched acquisition records in the
    accepted schema as evidence. Ingested means `rows_loaded` matches the raw
    rows that are really present.
    """
    pytest.importorskip("psycopg")
    from seq2lead.db import connect

    with connect() as conn:
        releases = conn.execute(
            "SELECT id, subset, rows_loaded, ingested_at IS NOT NULL FROM source_release "
            "WHERE source_name='BindingDB' AND version='202601' ORDER BY id"
        ).fetchall()
        if not releases:
            pytest.skip("release not registered")
        for release_id, subset, rows_loaded, ingested in releases:
            # Which raw table an artifact lands in depends on what it is -- the
            # measurement TSV goes to raw_measurement, the two auxiliary TSVs to
            # raw_record, a FASTA to raw_sequence. Summing over all three keeps
            # the invariant about the release rather than about one table.
            raw = sum(
                conn.execute(
                    f"SELECT count(*) FROM {table} WHERE source_release_id = %s",  # noqa: S608
                    (release_id,),
                ).fetchone()[0]
                for table in ("raw_measurement", "raw_record", "raw_sequence")
            )
            if not ingested:
                activities = conn.execute(
                    "SELECT count(*) FROM activity WHERE source_release_id = %s",
                    (release_id,),
                ).fetchone()[0]
                assert raw == 0, f"{subset}: unparsed release holds {raw} raw rows"
                assert activities == 0, f"{subset}: unparsed release holds {activities} activities"
                assert rows_loaded == 0, f"{subset}: unparsed release claims {rows_loaded} loaded"
            else:
                assert raw == rows_loaded, (
                    f"{subset}: release claims {rows_loaded} rows loaded but holds {raw}"
                )
