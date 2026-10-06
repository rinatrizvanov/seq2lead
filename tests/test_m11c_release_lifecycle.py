"""The acquired-to-ingested transition, for both loaders.

The acquisition step registers provenance with `ingested_at IS NULL`. Both
loaders previously treated any existing row with a matching SHA-256 as a
verified no-op, so a registered-but-unparsed release could never be loaded: it
reported success and stayed at `rows_loaded = 0` forever.

Three states, and each loader must tell them apart:

* **unparsed** -- registered provenance only. Permit the first ingest, and
  complete the registered row rather than inserting a second identity.
* **ingested, identical bytes** -- verified no-op.
* **conflicting bytes** -- refuse, whether or not the row was ever parsed.
"""

from __future__ import annotations

import os
import zipfile
from pathlib import Path

import pytest

from seq2lead.ingest.bindingdb import (
    ExistingRelease,
    ReleaseConflict,
    ReleaseState,
    classify_release,
)

pytestmark = pytest.mark.requires_db


def _db_disabled() -> bool:
    return os.environ.get("SEQ2LEAD_SKIP_DB_TESTS") == "1"


@pytest.fixture(scope="module", autouse=True)
def _isolated_schema():
    from seq2lead.db.isolation import isolated_schema

    if _db_disabled():
        yield None
        return
    with isolated_schema("seq2lead_test_lifecycle") as schema:
        yield schema


# ============================================ the decision table, in isolation


@pytest.mark.parametrize(
    ("existing", "incoming", "expected"),
    [
        (None, "aa", ReleaseState.ABSENT),
        (ExistingRelease(1, "aa", False), "aa", ReleaseState.UNPARSED),
        (ExistingRelease(1, "aa", True), "aa", ReleaseState.INGESTED),
        (ExistingRelease(1, "aa", True), "bb", ReleaseState.CONFLICT),
        (ExistingRelease(1, "aa", False), "bb", ReleaseState.CONFLICT),
    ],
)
def test_the_three_way_decision(existing, incoming, expected) -> None:
    assert classify_release(existing, incoming) is expected


def test_conflicting_bytes_refuse_even_when_unparsed() -> None:
    """A registered row is immutable provenance; it is not a draft to overwrite."""
    assert classify_release(ExistingRelease(1, "aa", False), "bb") is ReleaseState.CONFLICT


# =================================== both loaders, against a registered row


def _tsv_archive(tmp_path: Path, name: str, header: list[str], rows: list[list[str]]) -> Path:
    member = name.replace("_tsv.zip", ".tsv").replace(".zip", ".tsv")
    body = "\t".join(header) + "\n" + "".join("\t".join(r) + "\n" for r in rows)
    archive = tmp_path / name
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(member, body)
    return archive


def _download(archive: Path, sha256: str | None = None):
    import hashlib

    from seq2lead.ingest.download import DownloadResult

    raw = archive.read_bytes()
    digest = sha256 or hashlib.sha256(raw).hexdigest()
    from datetime import UTC, datetime

    return DownloadResult(
        path=archive,
        sha256=digest,
        md5="0" * 32,
        md5_verified=False,
        sha256_pinned=True,
        archive_bytes=len(raw),
        downloaded_at=datetime.now(UTC),
        reused_existing=False,
    )


def _register_unparsed(conn, source, download) -> int:
    """What the acquisition step does: provenance only, nothing parsed."""
    return conn.execute(
        """
        INSERT INTO source_release
          (source_name, version, subset, url, archive_member, sha256, md5,
           md5_verified, sha256_pinned, archive_bytes, member_bytes, license,
           downloaded_at, column_names, data_lines, rows_loaded, ingested_at)
        VALUES (%s,%s,%s,%s,%s,%s,%s,false,true,%s,0,'test',%s,'[]'::jsonb,0,0,NULL)
        RETURNING id
        """,
        (
            source.source_name,
            source.version,
            source.subset,
            source.url,
            source.archive_member,
            download.sha256,
            f"sha1:{'a' * 40}",
            download.archive_bytes,
            download.downloaded_at,
        ),
    ).fetchone()[0]


def test_the_auxiliary_loader_completes_a_registered_release(tmp_path) -> None:
    from seq2lead.db.connection import transaction
    from seq2lead.ingest import auxiliary
    from seq2lead.ingest.sources import SourceFile

    header = ["Entry ID", "Assay ID", "Description"]
    archive = _tsv_archive(
        tmp_path,
        "T_Assays_lifecycle_tsv.zip",
        header,
        [["1", "10", "a"], ["2", "20", "b"]],
    )
    download = _download(archive)
    source = SourceFile(
        source_name="LifecycleTest",
        version="L1",
        subset="assays",
        base_url="file://" + str(archive.parent),
        filename=archive.name,
        archive_member="T_Assays_lifecycle.tsv",
        expected_sha256=download.sha256,
        license="test",
        required_columns=tuple(header),
    )

    with transaction() as conn:
        from seq2lead.db.schema import create_schema

        create_schema(conn)
        registered = _register_unparsed(conn, source, download)

    with transaction() as conn:
        report = auxiliary.ingest_tsv(conn, source, download)
    assert report.was_noop is False, "an unparsed release was treated as a no-op"
    assert report.loaded == 2
    assert report.source_release_id == registered, "release identity was not preserved"

    with transaction() as conn:
        row = conn.execute(
            "SELECT ingested_at IS NOT NULL, rows_loaded, jsonb_array_length(column_names) "
            "FROM source_release WHERE id=%s",
            (registered,),
        ).fetchone()
        n_releases = conn.execute(
            "SELECT count(*) FROM source_release WHERE source_name='LifecycleTest'"
        ).fetchone()[0]
    assert row[0] is True, "the completed release is not marked ingested"
    assert row[1] == 2
    assert row[2] == len(header), "the header was not recorded on completion"
    assert n_releases == 1, "a second release identity was created"

    # a second run, identical bytes, is now a verified no-op
    with transaction() as conn:
        again = auxiliary.ingest_tsv(conn, source, download)
    assert again.was_noop is True
    assert again.loaded == 2


def test_the_measurement_loader_completes_a_registered_release(tmp_path) -> None:
    from seq2lead.db.connection import transaction
    from seq2lead.ingest import bindingdb
    from seq2lead.ingest.sources import SourceFile

    header = [
        "BindingDB Reactant_set_id",
        "Ligand SMILES",
        "Ligand InChI Key",
        "Target Name",
        "Number of Protein Chains in Target (>1 implies a multichain complex)",
        "BindingDB Target Chain Sequence 1",
        "Ki (nM)",
    ]
    archive = _tsv_archive(
        tmp_path,
        "T_All_lifecycle_tsv.zip",
        header,
        [
            ["1", "CCO", "A" * 27, "T", "1", "MKV", "10"],
            ["2", "CCC", "B" * 27, "T", "1", "MKV", "20"],
        ],
    )
    download = _download(archive)
    source = SourceFile(
        source_name="LifecycleTest",
        version="L1",
        subset="all",
        base_url="file://" + str(archive.parent),
        filename=archive.name,
        archive_member="T_All_lifecycle.tsv",
        expected_sha256=download.sha256,
        license="test",
        required_columns=tuple(header),
    )

    with transaction() as conn:
        from seq2lead.db.schema import create_schema

        create_schema(conn)
        registered = _register_unparsed(conn, source, download)

    with transaction() as conn:
        report = bindingdb.ingest(conn, source, download)
    assert report.was_noop is False, "an unparsed release was treated as a no-op"
    assert report.loaded == 2
    assert report.source_release_id == registered, "release identity was not preserved"

    with transaction() as conn:
        ingested, rows_loaded = conn.execute(
            "SELECT ingested_at IS NOT NULL, rows_loaded FROM source_release WHERE id=%s",
            (registered,),
        ).fetchone()
        n_releases = conn.execute(
            "SELECT count(*) FROM source_release WHERE source_name='LifecycleTest' AND subset='all'"
        ).fetchone()[0]
    assert ingested is True
    assert rows_loaded == 2
    assert n_releases == 1, "a second release identity was created"

    with transaction() as conn:
        again = bindingdb.ingest(conn, source, download)
    assert again.was_noop is True


def test_conflicting_bytes_are_refused_against_a_registered_release(tmp_path) -> None:
    from seq2lead.db.connection import transaction
    from seq2lead.ingest import auxiliary
    from seq2lead.ingest.sources import SourceFile

    header = ["Entry ID", "Assay ID", "Description"]
    archive = _tsv_archive(tmp_path, "T_Conflict_tsv.zip", header, [["1", "10", "a"]])
    download = _download(archive)
    source = SourceFile(
        source_name="LifecycleTest",
        version="L2",
        subset="assays",
        base_url="file://" + str(archive.parent),
        filename=archive.name,
        archive_member="T_Conflict.tsv",
        expected_sha256=download.sha256,
        license="test",
        required_columns=tuple(header),
    )
    with transaction() as conn:
        from seq2lead.db.schema import create_schema

        create_schema(conn)
        _register_unparsed(conn, source, download)

    other = _download(archive, sha256="f" * 64)
    import dataclasses

    source_other = dataclasses.replace(source, expected_sha256="f" * 64)
    with transaction() as conn, pytest.raises(ReleaseConflict, match="different artifact"):
        auxiliary.ingest_tsv(conn, source_other, other)


def test_a_failed_load_leaves_no_ingest_marker_and_no_rows(tmp_path) -> None:
    """Transactional completion: a mid-load failure must roll everything back."""
    from seq2lead.db.connection import transaction
    from seq2lead.ingest import auxiliary
    from seq2lead.ingest.sources import SourceFile

    header = ["Entry ID", "Assay ID", "Description"]
    archive = _tsv_archive(tmp_path, "T_Fail_tsv.zip", header, [["1", "10", "a"]])
    download = _download(archive)
    source = SourceFile(
        source_name="LifecycleTest",
        version="L3",
        subset="assays",
        base_url="file://" + str(archive.parent),
        filename=archive.name,
        archive_member="T_Fail.tsv",
        expected_sha256=download.sha256,
        license="test",
        required_columns=tuple(header),
    )
    with transaction() as conn:
        from seq2lead.db.schema import create_schema

        create_schema(conn)
        registered = _register_unparsed(conn, source, download)

    boom = RuntimeError("simulated failure after the rows were written")
    with pytest.raises(RuntimeError, match="simulated failure"):
        with transaction() as conn:
            auxiliary.ingest_tsv(conn, source, download)
            raise boom

    with transaction() as conn:
        ingested, rows_loaded = conn.execute(
            "SELECT ingested_at IS NOT NULL, rows_loaded FROM source_release WHERE id=%s",
            (registered,),
        ).fetchone()
        n_rows = conn.execute(
            "SELECT count(*) FROM raw_record WHERE source_release_id=%s", (registered,)
        ).fetchone()[0]
    assert ingested is False, "a failed load left a successful ingest marker"
    assert rows_loaded == 0
    assert n_rows == 0, f"a failed load left {n_rows} committed rows"
    # and the registered provenance survives, so the load can be retried
    with transaction() as conn:
        still = conn.execute(
            "SELECT count(*) FROM source_release WHERE id=%s", (registered,)
        ).fetchone()[0]
    assert still == 1, "the rollback destroyed the registered provenance row"
