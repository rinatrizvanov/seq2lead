"""Training visibility under the temporal split, on data built for the purpose.

The question these tests settle is narrow and was previously answered wrongly:
when a pair's *later* measurements contradict each other, does its clean earlier
measurement stay visible to a model training on the earlier period?

It must. A conflict among test-period records is a fact about the test period.
Letting it reach back and delete a clean training record is a future measurement
deciding what the model may learn from the past.
"""

from __future__ import annotations

import hashlib
import zipfile
from datetime import UTC, datetime

import pytest

from seq2lead.curate import CURATOR_VERSION, curate_release
from seq2lead.db import connect, transaction
from seq2lead.db.maintenance import no_entity_leak
from seq2lead.endpoint.build import build_endpoint
from seq2lead.ingest.bindingdb import ingest
from seq2lead.ingest.download import DownloadResult
from seq2lead.ingest.sources import SourceFile
from seq2lead.splits import assertions as sa
from seq2lead.splits.build import build_temporal_proxy


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
    with isolated_schema("seq2lead_test_temporal") as schema:
        yield schema


HEADER = [
    "BindingDB Reactant_set_id",
    "Ligand SMILES",
    "Ligand InChI Key",
    "Target Name",
    "Number of Protein Chains in Target (>1 implies a multichain complex)",
    "BindingDB Target Chain Sequence 1",
    "Ki (nM)",
    "Date of publication",
    "Date in BindingDB",
]
SEQ = "MKVLSSAAWQRTTYNEQ"
CLEAN = "CCO"  # the pair whose training evidence must survive
QUIET = "c1ccccc1"  # a pair with no later conflict, as a control

#: Train is before 2018-01-01, validation to 2020-01-01, test after.
TRAIN_DATE = "01/15/2015"
TEST_DATE_A = "03/02/2022"
TEST_DATE_B = "07/09/2022"


def _row(rsid: str, smiles: str, ki: str, date: str) -> list[str]:
    base = dict.fromkeys(HEADER, "")
    base.update(
        {
            "BindingDB Reactant_set_id": rsid,
            "Ligand SMILES": smiles,
            "Ligand InChI Key": "KEY-" + smiles,
            "Number of Protein Chains in Target (>1 implies a multichain complex)": "1",
            "BindingDB Target Chain Sequence 1": SEQ,
            "Ki (nM)": ki,
            "Date of publication": date,
            "Date in BindingDB": date,
        }
    )
    return [base[h] for h in HEADER]


#: One pair with clean training evidence and self-contradictory test evidence,
#: plus a control pair measured only in training.
ROWS = [
    _row("1", CLEAN, "1000", TRAIN_DATE),  # pKi 6.0, unambiguous
    _row("2", CLEAN, "1", TEST_DATE_A),  # pKi 9.0
    _row("3", CLEAN, "1000000", TEST_DATE_B),  # pKi 3.0 -- six logs apart
    _row("4", QUIET, "1000", TRAIN_DATE),
]


def _cleanup(release_id: int, names: list[str]) -> None:
    with transaction() as conn:
        for name in names:
            row = conn.execute("SELECT id FROM split_version WHERE name=%s", (name,)).fetchone()
            if row is None:
                continue
            sid = int(row[0])
            conn.execute("DELETE FROM split_pair_assignment WHERE split_id=%s", (sid,))
            conn.execute("DELETE FROM split_activity_assignment WHERE split_id=%s", (sid,))
            conn.execute("DELETE FROM split_partition_endpoint WHERE split_id=%s", (sid,))
            conn.execute("DELETE FROM split_version WHERE id=%s", (sid,))
        for name in conn.execute(
            "SELECT name FROM endpoint_version WHERE source_release_id=%s", (release_id,)
        ).fetchall():
            eid = conn.execute(
                "SELECT id FROM endpoint_version WHERE name=%s", (name[0],)
            ).fetchone()[0]
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


@pytest.fixture(scope="module")
def temporal(tmp_path_factory):
    """A three-row release built into its own temporal split."""
    tag = "tvis"
    tmp_path = tmp_path_factory.mktemp(tag)
    leak_guard = no_entity_leak()
    leak_guard.__enter__()
    tsv = "\n".join("\t".join(r) for r in [HEADER, *ROWS]) + "\n"
    path = tmp_path / f"{tag}.zip"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("m.tsv", tsv)
    source = SourceFile(
        source_name="TemporalVisibilityTest",
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
    split_name = f"temporal-{tag}"
    try:
        with transaction() as conn:
            endpoint_id, _ = build_endpoint(
                conn, release_id, name=f"ep-{tag}", curator_version=CURATOR_VERSION
            )
            build_temporal_proxy(conn, endpoint_id, split_name)
        with connect() as conn:
            split_id = int(
                conn.execute(
                    "SELECT id FROM split_version WHERE name=%s", (split_name,)
                ).fetchone()[0]
            )
        yield split_id, release_id
    finally:
        _cleanup(release_id, [split_name])
        leak_guard.__exit__(None, None, None)


def _activity(conn, smiles: str, partition: str, split_id: int) -> list[int]:
    return [
        int(r[0])
        for r in conn.execute(
            "SELECT s.activity_id FROM split_activity_assignment s "
            "JOIN activity a ON a.id = s.activity_id "
            "JOIN compound c ON c.id = a.compound_id "
            "WHERE s.split_id=%s AND s.partition=%s AND c.canonical_smiles=%s",
            (split_id, partition, smiles),
        ).fetchall()
    ]


@pytest.mark.requires_db
def test_the_fixture_really_does_conflict_in_the_test_period(temporal) -> None:
    """Guard: if the test evidence stopped conflicting, the tests below go vacuous."""
    split_id, _ = temporal
    with connect() as conn:
        excluded = conn.execute(
            """
            SELECT l.excluded_from_eval, l.eval_exclusion_reason
            FROM split_partition_endpoint spe
            JOIN pair_label l ON l.endpoint_id = spe.endpoint_id
            JOIN compound c ON c.id = l.compound_id
            WHERE spe.split_id=%s AND spe.partition='test' AND c.canonical_smiles=%s
            """,
            (split_id, CLEAN),
        ).fetchone()
        train_ok = conn.execute(
            """
            SELECT l.excluded_from_eval FROM split_partition_endpoint spe
            JOIN pair_label l ON l.endpoint_id = spe.endpoint_id
            JOIN compound c ON c.id = l.compound_id
            WHERE spe.split_id=%s AND spe.partition='train' AND c.canonical_smiles=%s
            """,
            (split_id, CLEAN),
        ).fetchone()
    assert excluded is not None and excluded[0] is True, "test evidence should conflict"
    assert train_ok is not None and train_ok[0] is False, "training evidence should be clean"


@pytest.mark.requires_db
def test_clean_training_evidence_survives_a_later_conflict(temporal) -> None:
    """The regression. A test-period conflict must not delete a training record."""
    split_id, _ = temporal
    with connect() as conn:
        visible = training_ids = sa.training_visible_activities(conn, split_id)
        assert "partition = 'excluded'" not in visible, (
            "training visibility still consults the excluded partition, which is "
            "populated from validation and test evidence"
        )
        train_activity = _activity(conn, CLEAN, "train", split_id)
        assert len(train_activity) == 1
        seen = conn.execute(
            f"SELECT count(*) FROM ({training_ids}) v WHERE v.activity_id = %s",  # noqa: S608
            (train_activity[0],),
        ).fetchone()[0]
    assert seen == 1, "the clean training activity was suppressed by later evidence"


@pytest.mark.requires_db
def test_test_period_evidence_is_never_visible(temporal) -> None:
    """The other half: the conflicting records themselves stay held out."""
    split_id, _ = temporal
    with connect() as conn:
        training_ids = sa.training_visible_activities(conn, split_id)
        test_activities = _activity(conn, CLEAN, "test", split_id)
        assert len(test_activities) == 2
        leaked = conn.execute(
            f"SELECT count(*) FROM ({training_ids}) v WHERE v.activity_id = ANY(%s)",  # noqa: S608
            (test_activities,),
        ).fetchone()[0]
    assert leaked == 0


@pytest.mark.requires_db
def test_a_pair_its_own_training_period_rejects_stays_invisible(temporal) -> None:
    """The fix must not become 'training sees everything dated early'."""
    split_id, _ = temporal
    with connect() as conn:
        training_ids = sa.training_visible_activities(conn, split_id)
        suppressed = conn.execute(
            f"""
            SELECT count(*) FROM split_activity_assignment s
            JOIN activity a ON a.id = s.activity_id
            JOIN split_partition_endpoint spe
              ON spe.split_id = s.split_id AND spe.partition = 'train'
            JOIN pair_label l ON l.endpoint_id = spe.endpoint_id
              AND l.compound_id = a.compound_id AND l.target_id = a.target_id
            WHERE s.split_id = %s AND s.partition = 'train' AND l.excluded_from_eval
              AND s.activity_id IN ({training_ids})
            """,  # noqa: S608
            (split_id,),
        ).fetchone()[0]
    assert suppressed == 0


@pytest.mark.requires_db
def test_retrieval_index_still_refuses_held_out_activities(temporal) -> None:
    """The widened visibility must not widen what an index may contain."""
    split_id, _ = temporal
    with connect() as conn:
        test_activities = _activity(conn, CLEAN, "test", split_id)
        with pytest.raises(sa.LeakageError):
            sa.assert_index_is_training_only(conn, split_id, test_activities)
        # ...while the clean training activity is accepted.
        sa.assert_index_is_training_only(conn, split_id, _activity(conn, CLEAN, "train", split_id))


#: The condition that was removed, kept here verbatim so the regression can prove
#: it was load-bearing rather than merely asserting that today's code is fine.
_RETIRED_CONDITION = """
    AND NOT EXISTS (
        SELECT 1 FROM split_pair_assignment p
        WHERE p.split_id = {sid} AND p.partition = 'excluded'
          AND p.compound_id = a.compound_id AND p.target_id = a.target_id
    )
"""


@pytest.mark.requires_db
def test_the_retired_condition_would_have_suppressed_it(temporal) -> None:
    """Proves the fixture discriminates: the old rule fails it, the new one passes.

    Without this, the tests above would keep passing even if the bug were
    reintroduced in a form they happen not to probe.
    """
    split_id, _ = temporal
    with connect() as conn:
        train_activity = _activity(conn, CLEAN, "train", split_id)[0]
        old_rule = (
            "SELECT s.activity_id FROM split_activity_assignment s "
            "JOIN activity a ON a.id = s.activity_id "
            f"WHERE s.split_id = {split_id} AND s.partition = 'train' "
            + _RETIRED_CONDITION.format(sid=split_id)
        )
        under_old = conn.execute(
            f"SELECT count(*) FROM ({old_rule}) v WHERE v.activity_id = %s",  # noqa: S608
            (train_activity,),
        ).fetchone()[0]
        under_new = conn.execute(
            f"SELECT count(*) FROM ({sa.training_visible_activities(conn, split_id)}) v "  # noqa: S608
            "WHERE v.activity_id = %s",
            (train_activity,),
        ).fetchone()[0]
    assert under_old == 0, "the fixture does not reproduce the bug it exists to catch"
    assert under_new == 1
