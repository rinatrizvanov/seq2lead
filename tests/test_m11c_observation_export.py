"""The observation export: the contract, the DOI separation, and read-only-ness.

The as-of comparison reads exports, not tables, because one of the two snapshots
is accepted data that must not be rebuilt. That makes the export a load-bearing
interface rather than a convenience, and three things about it are worth holding
down with tests.
"""

from __future__ import annotations

import gzip
import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path

import pytest

from seq2lead.asof.export import (
    ARTICLE_DOI_COLUMN,
    ENTRY_DOI_COLUMN,
    OBSERVATION_COLUMNS,
    export_observations,
    read_only_transaction,
    release_key,
)
from seq2lead.asof.matching import observation_of
from seq2lead.curate.pipeline import ASSAY_MATCHED

pytestmark = pytest.mark.requires_db


def _db_disabled() -> bool:
    return os.environ.get("SEQ2LEAD_SKIP_DB_TESTS") == "1"


# ======================================================= the contract, statically

#: Keys `observation_of` consults that are deliberately *not* export columns:
#: documented fallbacks for callers that hand it a differently-shaped row.
FALLBACK_KEYS = {"line_no"}


def test_the_export_supplies_every_key_the_matcher_reads() -> None:
    """A column the matcher reads and the export omits is silently lost evidence.

    `observation_of` degrades quietly: a missing `entry_doi` key is `None`, which
    is indistinguishable from a row that genuinely has no entry DOI, and a
    missing `source_release` drops the locator entirely. Neither raises. So the
    two sides are compared statically instead of waiting for a diff to come out
    wrong for a reason nobody can see.
    """
    source = Path("src/seq2lead/asof/matching.py").read_text(encoding="utf-8")
    read_keys = set(re.findall(r'row\.get\(\s*"([a-z_0-9]+)"', source))
    assert read_keys, "found no row.get(...) calls -- the regex has gone stale"
    missing = sorted(read_keys - set(OBSERVATION_COLUMNS) - FALLBACK_KEYS)
    assert not missing, f"the matcher reads keys the export never writes: {missing}"


def test_entry_doi_is_read_from_the_entry_column_not_the_article_column() -> None:
    """The two DOI columns are unrelated; conflating them would fabricate provenance."""
    assert ENTRY_DOI_COLUMN == "BindingDB Entry DOI"
    assert ARTICLE_DOI_COLUMN == "Article DOI"
    # The rendered query, not the module source: the source holds an f-string
    # placeholder, so reading it would assert nothing about what Postgres runs.
    from seq2lead.asof.export import _OBSERVATION_SQL

    assert f"payload ->> '{ENTRY_DOI_COLUMN}'" in _OBSERVATION_SQL
    assert ARTICLE_DOI_COLUMN not in _OBSERVATION_SQL


# ============================================================ against a database


@pytest.fixture(scope="module")
def seeded():
    """One release, two observations, with an article DOI that is not the entry DOI."""
    from seq2lead.db import connect
    from seq2lead.db.isolation import isolated_schema

    if _db_disabled():
        pytest.skip("database tests disabled")

    with isolated_schema("seq2lead_test_export"):
        with connect() as conn:
            release_id = conn.execute(
                "INSERT INTO source_release (source_name, version, subset, url, archive_member,"
                " sha256, md5, md5_verified, sha256_pinned, archive_bytes, member_bytes, license,"
                " downloaded_at, ingested_at, column_names, rows_loaded)"
                " VALUES ('BindingDB','202601','all','file:///x','m.tsv','aa','sha1:bb',"
                " false, true, 1, 1, 'CC BY 3.0', %s, %s, '[]'::jsonb, 2) RETURNING id",
                (datetime.now(UTC), datetime.now(UTC)),
            ).fetchone()[0]
            compound_id = conn.execute(
                "INSERT INTO compound (inchikey, canonical_smiles, standardizer_version)"
                " VALUES ('AAAAAAAAAAAAAA-BBBBBBBBBB-C','CCO','v1') RETURNING id"
            ).fetchone()[0]
            target_id = conn.execute(
                "INSERT INTO target (sequence_sha256, sequence, length)"
                " VALUES ('deadbeef','MKV',3) RETURNING id"
            ).fetchone()[0]
            publication_id = conn.execute(
                "INSERT INTO publication (pmid, doi) VALUES ('12345','10.1021/jm-article')"
                " RETURNING id"
            ).fetchone()[0]
            for line_no, entry_doi in ((11, "10.7270/Q2ZW1J3M"), (12, None)):
                payload = {
                    "BindingDB Reactant_set_id": str(line_no),
                    ARTICLE_DOI_COLUMN: "10.1021/jm-article",
                }
                if entry_doi is not None:
                    payload[ENTRY_DOI_COLUMN] = entry_doi
                raw_id = conn.execute(
                    "INSERT INTO raw_measurement (source_release_id, line_no, payload)"
                    " VALUES (%s,%s,%s::jsonb) RETURNING id",
                    (release_id, line_no, json.dumps(payload)),
                ).fetchone()[0]
                conn.execute(
                    "INSERT INTO activity (source_release_id, raw_measurement_id, reactant_set_id,"
                    " compound_id, target_id, assay_join_status, publication_id, measurement_type,"
                    " relation, value_text, value_unit, in_benchmark_scope, curator_version)"
                    " VALUES (%s,%s,%s,%s,%s,%s,%s,'KI','=','12.0','nM',true,'v1')",
                    (
                        release_id,
                        raw_id,
                        str(line_no),
                        compound_id,
                        target_id,
                        ASSAY_MATCHED,
                        publication_id,
                    ),
                )
            conn.commit()
            yield conn, release_id


def test_release_identity_survives_the_schema_it_came_from(seeded) -> None:
    """The id is a per-schema surrogate; the triple identifies the artifact."""
    conn, release_id = seeded
    assert release_key(conn, release_id) == "BindingDB/202601/all"


def test_the_export_lifts_entry_doi_and_keeps_it_apart_from_the_publication(
    seeded, tmp_path
) -> None:
    conn, release_id = seeded
    summary = export_observations(conn, release_id, tmp_path / "obs.jsonl.gz")

    assert summary.rows == 2
    assert summary.with_entry_doi == 1, "one of the two rows has no entry DOI"
    assert summary.with_locator == 2
    assert summary.distinct_entry_dois == 1
    # Counted against the status the curation pipeline really writes. The first
    # version of this counter tested for "joined", a value nothing produces, and
    # so reported zero matches against 3.1M matched rows.
    assert summary.assay_joined == 2
    assert summary.assay_join_status == {ASSAY_MATCHED: 2}

    with gzip.open(summary.path, "rt", encoding="utf-8") as fh:
        rows = [json.loads(line) for line in fh]
    assert [r["raw_row"] for r in rows] == [11, 12]

    first = rows[0]
    assert first["entry_doi"] == "10.7270/Q2ZW1J3M"
    assert first["doi"] == "10.1021/jm-article"
    assert first["entry_doi"] != first["doi"], "entry and publication DOI must stay distinct"
    assert rows[1]["entry_doi"] is None, "an absent entry DOI must not borrow the article DOI"


def test_an_exported_row_reconstructs_an_observation_with_its_locator(seeded, tmp_path) -> None:
    """The export is only useful if the matcher can actually consume it."""
    conn, release_id = seeded
    summary = export_observations(conn, release_id, tmp_path / "obs.jsonl.gz")
    with gzip.open(summary.path, "rt", encoding="utf-8") as fh:
        row = json.loads(fh.readline())

    obs = observation_of(row)
    assert obs.entry_doi == "10.7270/Q2ZW1J3M"
    assert obs.source is not None
    assert obs.source.source_release == "BindingDB/202601/all"
    assert obs.source.raw_row == "11"
    assert obs.slot.inchikey == "AAAAAAAAAAAAAA-BBBBBBBBBB-C"
    assert obs.slot.sequence_sha256 == "DEADBEEF"
    # The slot's publication reference comes from the article, never the entry.
    assert "10.7270" not in obs.slot.publication_ref
    assert obs.value.measurement_type == "KI"


def test_a_read_only_transaction_actually_refuses_writes(seeded) -> None:
    """What lets the accepted September snapshot be exported without risk.

    Asserted against the database rather than inferred from the export only
    containing SELECTs, because the second is a property of today's source and
    the first is a property of the connection the export runs on.
    """
    import psycopg

    from seq2lead.db import connect

    with connect() as conn:
        read_only_transaction(conn)
        conn.execute("SELECT count(*) FROM activity").fetchone()
        with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
            conn.execute(
                "INSERT INTO target (sequence_sha256, sequence, length) VALUES ('x','M',1)"
            )
        conn.rollback()


def test_the_export_is_byte_reproducible(seeded, tmp_path) -> None:
    """Two exports of the same rows must have the same digest.

    `gzip.open` writes the current time into the member header, so an export that
    used it produced a different digest every run from the same data. A recorded
    SHA-256 then certified when the file was written rather than what was in it,
    which is worse than recording nothing: it looks like provenance.
    """
    import hashlib

    conn, release_id = seeded
    digests = []
    for name in ("a.jsonl.gz", "b.jsonl.gz"):
        summary = export_observations(conn, release_id, tmp_path / name)
        digests.append(hashlib.sha256(summary.path.read_bytes()).hexdigest())
    assert digests[0] == digests[1], "the export is not byte-reproducible"
