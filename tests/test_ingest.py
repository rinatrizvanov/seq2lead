"""Ingest behaviour, exercised on synthetic archives rather than the real download."""

from __future__ import annotations

import hashlib
import zipfile
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from seq2lead.db import connect, transaction
from seq2lead.ingest import bindingdb
from seq2lead.ingest import download as download_mod
from seq2lead.ingest.bindingdb import (
    HeaderError,
    ReleaseConflict,
    ingest,
    read_header,
    reconstruct_line,
    validate_header,
)
from seq2lead.ingest.download import ChecksumMismatch, DownloadResult
from seq2lead.ingest.sources import BINDINGDB_202609_ALL, BINDINGDB_202609_PDSPKI, SourceFile, get


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
    with isolated_schema("seq2lead_test_ingest") as schema:
        yield schema


if TYPE_CHECKING:
    from pathlib import Path

MEMBER = "test.tsv"
HEADER = ["id", "Ligand SMILES", "Ki (nM)", "pH", "Chain 2 Sequence"]
REQUIRED = ("id", "Ki (nM)")


def _tsv(rows: list[list[str]]) -> bytes:
    return ("\n".join("\t".join(r) for r in rows) + "\n").encode("utf-8")


def _archive(tmp_path: Path, content: bytes, name: str = "test.zip") -> Path:
    path = tmp_path / name
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(MEMBER, content)
    return path


def _source(**overrides: object) -> SourceFile:
    base = {
        "source_name": "TestSource",
        "version": "0000",
        "subset": "unit-test",
        "filename": "test.zip",
        "archive_member": MEMBER,
        "license": "test",
        "required_columns": REQUIRED,
        # Any non-None value makes the source pinned; ingest() checks only that a
        # pin exists, while fetch() is what compares it to the bytes.
        "expected_sha256": "0" * 64,
    }
    base.update(overrides)
    return SourceFile(**base)  # type: ignore[arg-type]


def _result(path: Path, sha: str | None = None) -> DownloadResult:
    digest = sha or hashlib.sha256(path.read_bytes()).hexdigest()
    return DownloadResult(
        path=path,
        sha256=digest,
        md5="0" * 32,
        md5_verified=False,
        sha256_pinned=True,
        archive_bytes=path.stat().st_size,
        downloaded_at=datetime.now(UTC),
        reused_existing=False,
    )


def _cleanup(source: SourceFile) -> None:
    with transaction() as conn:
        conn.execute(
            "DELETE FROM ingest_exclusion WHERE source_release_id IN "
            "(SELECT id FROM source_release WHERE source_name=%s AND subset=%s)",
            (source.source_name, source.subset),
        )
        conn.execute(
            "DELETE FROM raw_measurement WHERE source_release_id IN "
            "(SELECT id FROM source_release WHERE source_name=%s AND subset=%s)",
            (source.source_name, source.subset),
        )
        conn.execute(
            "DELETE FROM source_release WHERE source_name=%s AND subset=%s",
            (source.source_name, source.subset),
        )


# =========================================================== manifest / pinning


def test_pilot_sha256_is_pinned_in_the_manifest() -> None:
    assert BINDINGDB_202609_PDSPKI.expected_sha256 == (
        "5a212e99f495e5c11befb09baba264435201dfced1cada926be13df73f971ac3"
    )


def test_full_release_digest_was_observed_not_guessed() -> None:
    """The pin must be a real observed digest.

    It was deliberately left None until `ingest inspect` reported the digest of an
    actual download; a fabricated value would have 'verified' the wrong artifact.
    The note records that provenance.
    """
    assert BINDINGDB_202609_ALL.expected_sha256 == (
        "4c04e0fec46fadab8a48465a7ac2344cf4e2b3887c6b69acc968676130b73e16"
    )
    assert "observed" in BINDINGDB_202609_ALL.note


def test_registry_rejects_unknown_subsets() -> None:
    with pytest.raises(KeyError, match="Unknown subset"):
        get("no-such-subset")


def test_fetch_rejects_bytes_that_do_not_match_the_pin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(download_mod, "_published_md5", lambda _s: None)
    _archive(tmp_path, _tsv([HEADER]))
    source = _source(expected_sha256="f" * 64)
    with pytest.raises(ChecksumMismatch, match="frozen manifest"):
        download_mod.fetch(source, dest_dir=tmp_path)


def test_fetch_accepts_bytes_that_match_the_pin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(download_mod, "_published_md5", lambda _s: None)
    path = _archive(tmp_path, _tsv([HEADER]))
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    result = download_mod.fetch(_source(expected_sha256=digest), dest_dir=tmp_path)
    assert result.sha256 == digest
    assert result.sha256_pinned is True


def test_unpinned_source_is_recorded_as_unpinned(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(download_mod, "_published_md5", lambda _s: None)
    _archive(tmp_path, _tsv([HEADER]))
    result = download_mod.fetch(_source(expected_sha256=None), dest_dir=tmp_path)
    assert result.sha256_pinned is False


# =========================================================== header validation


def test_duplicate_header_names_are_rejected() -> None:
    with pytest.raises(HeaderError, match="Duplicate column names"):
        validate_header(["a", "b", "a", "c", "b"], ())


def test_missing_required_columns_are_rejected() -> None:
    with pytest.raises(HeaderError, match="Required columns are absent"):
        validate_header(["a", "b"], ("a", "Ki (nM)"))


def test_header_need_not_match_another_subsets_header() -> None:
    """The full release may legitimately carry different columns."""
    validate_header(["id", "Ki (nM)", "something new"], REQUIRED)


def test_undecodable_header_fails_loudly(tmp_path: Path) -> None:
    path = _archive(tmp_path, b"id\tKi (nM)\t\xff\xfe\n")
    with pytest.raises(HeaderError, match="not valid UTF-8"):
        read_header(path, MEMBER)


def test_read_header_does_not_need_a_database(tmp_path: Path) -> None:
    path = _archive(tmp_path, _tsv([HEADER]))
    assert read_header(path, MEMBER) == HEADER


# =========================================================== fidelity


def test_reconstruct_line_round_trips_a_projection() -> None:
    payload = {"id": "1", "Ki (nM)": ">10000"}
    assert reconstruct_line(HEADER, payload) == "1\t\t>10000\t\t"


@pytest.mark.requires_db
def test_whitespace_only_fields_are_preserved(tmp_path: Path) -> None:
    """A field of spaces is content. Dropping it would break reconstruction."""
    source = _source()
    path = _archive(tmp_path, _tsv([HEADER, ["1", "CCO", "4.5", "   ", ""]]))
    try:
        with transaction() as conn:
            report = ingest(conn, source, _result(path))
        with connect() as conn:
            row = conn.execute(
                "SELECT payload FROM raw_measurement WHERE source_release_id=%s",
                (report.source_release_id,),
            ).fetchone()
        assert row is not None
        assert row[0]["pH"] == "   "  # kept verbatim
        assert "Chain 2 Sequence" not in row[0]  # exactly empty, so omitted
        assert reconstruct_line(HEADER, row[0]) == "1\tCCO\t4.5\t   \t"
    finally:
        _cleanup(source)


@pytest.mark.requires_db
def test_every_loaded_row_reconstructs_to_its_original_line(tmp_path: Path) -> None:
    """The fidelity claim, asserted rather than asserted-in-prose."""
    source = _source()
    originals = [
        ["1", "CCO", "4.5", "", ""],
        ["2", "c1ccccc1", ">10000", " ", "MKV"],
        ["3", "C#N", "", "7.4", ""],
    ]
    path = _archive(tmp_path, _tsv([HEADER, *originals]))
    try:
        with transaction() as conn:
            report = ingest(conn, source, _result(path))
        with connect() as conn:
            rows = conn.execute(
                "SELECT line_no, payload FROM raw_measurement "
                "WHERE source_release_id=%s ORDER BY line_no",
                (report.source_release_id,),
            ).fetchall()
        assert len(rows) == 3
        for (_, payload), original in zip(rows, originals, strict=True):
            assert reconstruct_line(HEADER, payload) == "\t".join(original)
    finally:
        _cleanup(source)


@pytest.mark.requires_db
def test_invalid_utf8_is_quarantined_with_its_original_bytes(tmp_path: Path) -> None:
    """Bytes are never silently replaced."""
    source = _source()
    bad = b"2\tCCO\t4.5\t\xff\xfe\t\n"
    content = _tsv([HEADER, ["1", "CCO", "4.5", "", ""]]) + bad
    path = _archive(tmp_path, content)
    try:
        with transaction() as conn:
            report = ingest(conn, source, _result(path))
        assert report.loaded == 1
        assert report.excluded == 1
        assert report.reconciles
        with connect() as conn:
            row = conn.execute(
                "SELECT rule_code, raw_bytes, byte_length FROM ingest_exclusion "
                "WHERE source_release_id=%s",
                (report.source_release_id,),
            ).fetchone()
        assert row is not None
        assert row[0] == "invalid_utf8"
        assert bytes(row[1]) == bad.rstrip(b"\n")  # exact bytes, no replacement char
        assert row[2] == len(bad.rstrip(b"\n"))
    finally:
        _cleanup(source)


@pytest.mark.requires_db
def test_long_malformed_records_are_not_truncated(tmp_path: Path) -> None:
    source = _source()
    huge = "X" * 50_000
    path = _archive(tmp_path, _tsv([HEADER, ["1", huge]]))  # 2 fields, not 5
    try:
        with transaction() as conn:
            report = ingest(conn, source, _result(path))
        assert report.excluded == 1
        with connect() as conn:
            row = conn.execute(
                "SELECT raw_bytes, byte_length FROM ingest_exclusion WHERE source_release_id=%s",
                (report.source_release_id,),
            ).fetchone()
        assert row is not None
        raw_bytes, byte_length = bytes(row[0]), row[1]
        assert byte_length > 50_000  # nothing clipped at 8192
        assert raw_bytes.decode() == f"1\t{huge}"
    finally:
        _cleanup(source)


@pytest.mark.requires_db
def test_reconciliation_holds_with_mixed_malformed_records(tmp_path: Path) -> None:
    source = _source()
    content = (
        _tsv([HEADER, ["1", "CCO", "4.5", "", ""], ["2", "CCC", "9", "", ""]])
        + b"3\ttoo-few\n"
        + b"4\tCCO\t4.5\t\xff\t\n"
        + _tsv([["5", "CCN", "1.1", "", ""]])
    )
    path = _archive(tmp_path, content)
    try:
        with transaction() as conn:
            report = ingest(conn, source, _result(path))
        assert report.data_lines == 5
        assert report.loaded == 3
        assert report.excluded == 2
        assert report.reconciles
        with connect() as conn:
            rules = dict(
                conn.execute(
                    "SELECT rule_code, count(*) FROM ingest_exclusion "
                    "WHERE source_release_id=%s GROUP BY rule_code",
                    (report.source_release_id,),
                ).fetchall()
            )
        assert rules == {"field_count_mismatch": 1, "invalid_utf8": 1}
    finally:
        _cleanup(source)


# =========================================================== immutability


@pytest.mark.requires_db
def test_reingesting_identical_bytes_is_a_verified_noop(tmp_path: Path) -> None:
    source = _source()
    path = _archive(tmp_path, _tsv([HEADER, ["1", "CCO", "4.5", "", ""]]))
    result = _result(path)
    try:
        with transaction() as conn:
            first = ingest(conn, source, result)
        with connect() as conn:
            ids_before = [
                r[0]
                for r in conn.execute(
                    "SELECT id FROM raw_measurement WHERE source_release_id=%s ORDER BY id",
                    (first.source_release_id,),
                ).fetchall()
            ]

        with transaction() as conn:
            second = ingest(conn, source, result)

        assert second.was_noop is True
        assert second.source_release_id == first.source_release_id
        assert second.loaded == first.loaded
        assert second.data_lines == first.data_lines

        with connect() as conn:
            ids_after = [
                r[0]
                for r in conn.execute(
                    "SELECT id FROM raw_measurement WHERE source_release_id=%s ORDER BY id",
                    (first.source_release_id,),
                ).fetchall()
            ]
        assert ids_after == ids_before  # raw row identity survives
    finally:
        _cleanup(source)


@pytest.mark.requires_db
def test_reingesting_different_bytes_refuses_rather_than_overwrites(tmp_path: Path) -> None:
    source = _source()
    first_path = _archive(tmp_path, _tsv([HEADER, ["1", "CCO", "4.5", "", ""]]))
    second_path = _archive(tmp_path, _tsv([HEADER, ["1", "CCO", "9.9", "", ""]]), name="other.zip")
    try:
        with transaction() as conn:
            first = ingest(conn, source, _result(first_path))
        with pytest.raises(ReleaseConflict, match="immutable"), transaction() as conn:
            ingest(conn, source, _result(second_path))

        with connect() as conn:
            value = conn.execute(
                "SELECT payload ->> 'Ki (nM)' FROM raw_measurement WHERE source_release_id=%s",
                (first.source_release_id,),
            ).fetchone()
            count = conn.execute(
                "SELECT count(*) FROM source_release WHERE source_name=%s AND subset=%s",
                (source.source_name, source.subset),
            ).fetchone()
        assert value is not None
        assert value[0] == "4.5"  # original content untouched
        assert count is not None
        assert count[0] == 1  # no second release row
    finally:
        _cleanup(source)


@pytest.mark.requires_db
def test_release_cannot_be_deleted_while_raw_rows_reference_it(tmp_path: Path) -> None:
    """ON DELETE RESTRICT is what makes downstream foreign keys safe to rely on."""
    import psycopg

    source = _source()
    path = _archive(tmp_path, _tsv([HEADER, ["1", "CCO", "4.5", "", ""]]))
    try:
        with transaction() as conn:
            report = ingest(conn, source, _result(path))
        with pytest.raises(psycopg.errors.ForeignKeyViolation), transaction() as conn:
            conn.execute("DELETE FROM source_release WHERE id=%s", (report.source_release_id,))
    finally:
        _cleanup(source)


@pytest.mark.requires_db
def test_failed_ingest_leaves_no_partial_release(tmp_path: Path) -> None:
    """transaction() is what makes a half-finished load impossible to mistake for a whole one."""
    source = _source()
    path = _archive(tmp_path, _tsv([HEADER, ["1", "CCO", "4.5", "", ""]]))
    with pytest.raises(RuntimeError, match="interrupted"), transaction() as conn:
        ingest(conn, source, _result(path))
        raise RuntimeError("interrupted")

    with connect() as conn:
        row = conn.execute(
            "SELECT count(*) FROM source_release WHERE source_name=%s AND subset=%s",
            (source.source_name, source.subset),
        ).fetchone()
    assert row is not None
    assert row[0] == 0


# =========================================================== bookkeeping


@pytest.mark.requires_db
def test_field_total_is_counted_during_ingest(tmp_path: Path) -> None:
    """Replaces the full-table LATERAL scan the profile used to run."""
    source = _source()
    path = _archive(
        tmp_path,
        _tsv([HEADER, ["1", "CCO", "4.5", "", ""], ["2", "", "", "7.4", "MKV"]]),
    )
    try:
        with transaction() as conn:
            report = ingest(conn, source, _result(path))
        assert report.field_total == 6  # 3 populated + 3 populated
        assert report.mean_fields_per_row == 3.0
        with connect() as conn:
            row = conn.execute(
                "SELECT field_total FROM source_release WHERE id=%s",
                (report.source_release_id,),
            ).fetchone()
        assert row is not None
        assert row[0] == 6
    finally:
        _cleanup(source)


@pytest.mark.requires_db
def test_column_names_are_stored_for_reconstruction(tmp_path: Path) -> None:
    source = _source()
    path = _archive(tmp_path, _tsv([HEADER, ["1", "CCO", "4.5", "", ""]]))
    try:
        with transaction() as conn:
            report = ingest(conn, source, _result(path))
        with connect() as conn:
            row = conn.execute(
                "SELECT column_names FROM source_release WHERE id=%s",
                (report.source_release_id,),
            ).fetchone()
        assert row is not None
        assert row[0] == HEADER
    finally:
        _cleanup(source)


def testiter_lines_is_chunk_boundary_safe() -> None:
    """Lines must not be split by the read buffer."""
    import io

    data = b"\n".join(f"line-{i}".encode() for i in range(500)) + b"\n"
    got = list(bindingdb.iter_lines(io.BytesIO(data), chunk_size=7))
    assert got == data.rstrip(b"\n").split(b"\n")


# =========================================================== real-file fidelity

PILOT_ARCHIVE = "data/raw/BindingDB_PDSPKi_202609_tsv.zip"


@pytest.mark.requires_db
def test_real_pilot_rows_reconstruct_exactly() -> None:
    """The fidelity claim against the actual BindingDB file, not a fixture.

    Skips when the pilot archive or release is absent, so the suite still runs on
    a clean checkout and in CI.
    """
    import pathlib
    import zipfile

    archive = pathlib.Path(PILOT_ARCHIVE)
    if not archive.exists():
        pytest.skip(f"{PILOT_ARCHIVE} not downloaded")

    # The pilot release lives in the corpus, not in this module's test schema.
    from seq2lead.db.isolation import corpus_connection

    with corpus_connection() as conn:
        release = conn.execute(
            "SELECT id, column_names, archive_member FROM source_release "
            "WHERE source_name='BindingDB' AND subset='pdspki'"
        ).fetchone()
        if release is None:
            pytest.skip("pilot release not ingested")
        release_id, header, member = release
        payloads = dict(
            conn.execute(
                "SELECT line_no, payload FROM raw_measurement WHERE source_release_id=%s",
                (release_id,),
            ).fetchall()
        )

    checked = 0
    with zipfile.ZipFile(archive) as zf, zf.open(member) as fh:
        for offset, raw in enumerate(bindingdb.iter_lines(fh)):
            if offset == 0:
                continue
            payload = payloads.get(offset + 1)
            if payload is None:
                continue
            assert reconstruct_line(header, payload) == raw.decode("utf-8")
            checked += 1
    assert checked == len(payloads)
