"""M6 splits: assignment units, grouping, and the leakage assertions."""

from __future__ import annotations

import pytest

from seq2lead.db import connect
from seq2lead.splits import assertions as sa
from seq2lead.splits import build, clustering

ENDPOINT = "ki-pki6-v2"
SCORED = ("train", "validation", "test")


# ================================================================ pure logic


def test_group_assignment_keeps_groups_whole() -> None:
    groups = {f"g{i}": [(i, j) for j in range(3)] for i in range(30)}
    assigned = build._assign_groups(groups, seed=1, fractions=(0.7, 0.1, 0.2))
    by_group: dict[int, set[str]] = {}
    for compound_id, _target, partition, _ in assigned:
        by_group.setdefault(compound_id, set()).add(partition)
    assert all(len(parts) == 1 for parts in by_group.values())


def test_group_assignment_is_deterministic_for_a_seed() -> None:
    groups = {f"g{i}": [(i, 0)] for i in range(50)}
    first = build._assign_groups(groups, seed=7, fractions=(0.7, 0.1, 0.2))
    second = build._assign_groups(groups, seed=7, fractions=(0.7, 0.1, 0.2))
    assert first == second


def test_group_assignment_respects_quotas_approximately() -> None:
    groups = {f"g{i}": [(i, j) for j in range(2)] for i in range(500)}
    assigned = build._assign_groups(groups, seed=3, fractions=(0.7, 0.1, 0.2))
    counts: dict[str, int] = {}
    for *_rest, partition, _ in assigned:
        counts[partition] = counts.get(partition, 0) + 1
    total = sum(counts.values())
    assert abs(counts["train"] / total - 0.7) < 0.05
    assert abs(counts["test"] / total - 0.2) < 0.05


def test_acyclic_compounds_do_not_collapse_into_one_scaffold() -> None:
    """An empty Murcko scaffold would group every acyclic compound together."""
    a = clustering._scaffold("CCCC")
    b = clustering._scaffold("CCCCC")
    assert a != b
    assert a.startswith("__acyclic__")


def test_scaffold_groups_analogs_together() -> None:
    core = clustering._scaffold("c1ccccc1C(=O)NC")
    analog = clustering._scaffold("c1ccccc1C(=O)NCC")
    assert core == analog


def test_unparseable_structure_gets_a_sentinel_not_a_crash() -> None:
    assert clustering._scaffold("C(((") == "__unparseable__"


def test_scaffold_batch_preserves_keys() -> None:
    out = clustering._scaffold_batch([(1, "c1ccccc1"), (2, "bad!!")])
    assert [k for k, _ in out] == [1, 2]
    assert out[1][1] == "__unparseable__"


# ============================================================== contract shape


def test_temporal_is_the_only_activity_assigned_split() -> None:
    """Everything else assigns whole pairs; the asymmetry is the contract."""
    pair_builders = {
        "random_pair": build.build_random_pair,
        "cold_protein": build.build_cold_protein,
        "chemistry_disjoint": build.build_chemistry_disjoint,
        "label_reversal": build.build_label_reversal,
    }
    assert "temporal_proxy" not in pair_builders
    assert callable(build.build_temporal_proxy)


def test_training_visible_sql_is_sql_not_rows() -> None:
    """Returned as SQL so a caller cannot quietly widen the training set."""
    import inspect

    source = inspect.getsource(sa.training_visible_activities)
    assert "-> str" in source


# ================================================================= database


@pytest.fixture(scope="module")
def splits() -> dict[str, int]:
    with connect() as conn:
        rows = conn.execute("SELECT split_type, id FROM split_version ORDER BY id").fetchall()
    if not rows:
        pytest.skip("no splits built")
    return {str(r[0]): int(r[1]) for r in rows}


@pytest.mark.requires_db
def test_all_splits_pass_their_assertions(splits) -> None:
    with connect() as conn:
        for split_type, split_id in splits.items():
            checks = sa.assert_no_leakage(conn, split_id)
            assert checks, f"{split_type} produced no checks"


@pytest.mark.requires_db
@pytest.mark.parametrize(
    "split_type", ["random_pair", "cold_protein", "chemistry_disjoint", "label_reversal"]
)
def test_pair_splits_place_each_pair_in_one_partition(splits, split_type) -> None:
    if split_type not in splits:
        pytest.skip(f"{split_type} not built")
    with connect() as conn:
        overlap = conn.execute(
            "SELECT count(*) FROM (SELECT compound_id, target_id FROM "
            "split_pair_assignment WHERE split_id=%s GROUP BY 1,2 HAVING count(*)>1) x",
            (splits[split_type],),
        ).fetchone()[0]
    assert overlap == 0


@pytest.mark.requires_db
def test_cold_protein_holds_whole_sequence_clusters(splits) -> None:
    if "cold_protein" not in splits:
        pytest.skip("not built")
    with connect() as conn:
        spanning = conn.execute(
            """
            SELECT count(*) FROM (
                SELECT c.cluster_id FROM split_pair_assignment p
                JOIN target_cluster c ON c.target_id = p.target_id
                WHERE p.split_id=%s AND p.partition IN ('train','validation','test')
                GROUP BY c.cluster_id HAVING count(DISTINCT p.partition) > 1
            ) x
            """,
            (splits["cold_protein"],),
        ).fetchone()[0]
    assert spanning == 0


@pytest.mark.requires_db
def test_chemistry_disjoint_holds_whole_scaffolds(splits) -> None:
    if "chemistry_disjoint" not in splits:
        pytest.skip("not built")
    with connect() as conn:
        spanning = conn.execute(
            """
            SELECT count(*) FROM (
                SELECT c.cluster_id FROM split_pair_assignment p
                JOIN compound_cluster c ON c.compound_id = p.compound_id
                WHERE p.split_id=%s AND p.partition IN ('train','validation','test')
                GROUP BY c.cluster_id HAVING count(DISTINCT p.partition) > 1
            ) x
            """,
            (splits["chemistry_disjoint"],),
        ).fetchone()[0]
    assert spanning == 0


@pytest.mark.requires_db
def test_no_excluded_pair_reaches_validation_or_test(splits) -> None:
    """The check that failed the first temporal build.

    Eligibility is judged by the endpoint that governs the partition. For
    `temporal_proxy` that is the partition's own endpoint: consulting the global
    one would let an exclusion decided from later measurements reach back and
    reshape an earlier partition.
    """
    with connect() as conn:
        for split_type, split_id in splits.items():
            if split_type == "temporal_proxy":
                sql = """
                    SELECT count(*) FROM split_pair_assignment p
                    JOIN split_partition_endpoint spe
                      ON spe.split_id = p.split_id AND spe.partition = p.partition
                    JOIN pair_label l ON l.endpoint_id = spe.endpoint_id
                      AND l.compound_id = p.compound_id AND l.target_id = p.target_id
                    WHERE p.split_id=%s AND l.excluded_from_eval
                      AND p.partition IN ('validation','test')
                """
                params = (split_id,)
            else:
                sql = """
                    SELECT count(*) FROM split_pair_assignment p
                    JOIN pair_label l ON l.compound_id=p.compound_id
                      AND l.target_id=p.target_id AND l.endpoint_id=%s
                    WHERE p.split_id=%s AND l.excluded_from_eval
                      AND p.partition IN ('validation','test')
                """
                endpoint_id = conn.execute(
                    "SELECT endpoint_id FROM split_version WHERE id=%s", (split_id,)
                ).fetchone()[0]
                params = (endpoint_id, split_id)
            leaked = conn.execute(sql, params).fetchone()[0]
            assert leaked == 0, f"{split_type} leaked {leaked} excluded pairs"


@pytest.mark.requires_db
def test_temporal_labels_every_pair_with_a_stratum(splits) -> None:
    if "temporal_proxy" not in splits:
        pytest.skip("not built")
    with connect() as conn:
        unlabelled = conn.execute(
            "SELECT count(*) FROM split_pair_assignment WHERE split_id=%s AND stratum IS NULL",
            (splits["temporal_proxy"],),
        ).fetchone()[0]
        strata = dict(
            conn.execute(
                "SELECT stratum, count(*) FROM split_pair_assignment WHERE split_id=%s "
                "GROUP BY stratum",
                (splits["temporal_proxy"],),
            ).fetchall()
        )
    assert unlabelled == 0
    assert set(strata) <= {"new", "recurrent"}
    assert strata.get("recurrent", 0) > 0  # the stratum exists and is populated


@pytest.mark.requires_db
def test_temporal_assigns_each_activity_once(splits) -> None:
    if "temporal_proxy" not in splits:
        pytest.skip("not built")
    with connect() as conn:
        dup = conn.execute(
            "SELECT count(*) FROM (SELECT activity_id FROM split_activity_assignment "
            "WHERE split_id=%s GROUP BY 1 HAVING count(*)>1) x",
            (splits["temporal_proxy"],),
        ).fetchone()[0]
    assert dup == 0


@pytest.mark.requires_db
def test_retrieval_index_rejects_held_out_activities(splits) -> None:
    """The guard that keeps held-out evidence out of a support index."""
    split_id = splits.get("random_pair")
    if split_id is None:
        pytest.skip("not built")
    with connect() as conn:
        held_out = conn.execute(
            """
            SELECT sup.activity_id FROM split_pair_assignment p
            JOIN pair_label l ON l.compound_id=p.compound_id AND l.target_id=p.target_id
            JOIN pair_label_support sup ON sup.pair_id=l.id
            WHERE p.split_id=%s AND p.partition='test'
              AND l.endpoint_id=(SELECT endpoint_id FROM split_version WHERE id=%s)
            LIMIT 5
            """,
            (split_id, split_id),
        ).fetchall()
        if not held_out:
            pytest.skip("no held-out activities")
        ids = [int(r[0]) for r in held_out]
        with pytest.raises(sa.LeakageError, match="not in its training partition"):
            sa.assert_index_is_training_only(conn, split_id, ids)


@pytest.mark.requires_db
def test_retrieval_index_accepts_training_activities(splits) -> None:
    split_id = splits.get("random_pair")
    if split_id is None:
        pytest.skip("not built")
    with connect() as conn:
        rows = conn.execute(
            f"{sa.training_visible_activities(conn, split_id)} LIMIT 5"  # noqa: S608
        ).fetchall()
        if not rows:
            pytest.skip("no training activities")
        sa.assert_index_is_training_only(conn, split_id, [int(r[0]) for r in rows])


@pytest.mark.requires_db
def test_splits_are_immutable(splits) -> None:
    from seq2lead.db import transaction

    with connect() as conn:
        name, endpoint_id = conn.execute(
            "SELECT name, endpoint_id FROM split_version WHERE id=%s",
            (next(iter(splits.values())),),
        ).fetchone()
    with pytest.raises(ValueError, match="already exists"), transaction() as conn:
        build.build_random_pair(conn, int(endpoint_id), str(name))


# ============================ regressions: these fail the pre-v3 temporal build


def _temporal(conn) -> int | None:
    row = conn.execute(
        "SELECT id FROM split_version WHERE split_type='temporal_proxy' "
        "AND superseded_by IS NULL ORDER BY id DESC LIMIT 1"
    ).fetchone()
    return int(row[0]) if row else None


@pytest.mark.requires_db
def test_temporal_builds_an_endpoint_per_partition() -> None:
    """The v1 builder created date assignments and no aggregates at all."""
    with connect() as conn:
        split_id = _temporal(conn)
        if split_id is None:
            pytest.skip("no current temporal split")
        parts = {
            str(r[0])
            for r in conn.execute(
                "SELECT partition FROM split_partition_endpoint WHERE split_id=%s",
                (split_id,),
            ).fetchall()
        }
    assert parts == {"train", "validation", "test"}


@pytest.mark.requires_db
def test_temporal_aggregates_are_supported_only_by_their_own_partition() -> None:
    """The v1 builder had no support-provenance check; this is what it missed."""
    with connect() as conn:
        split_id = _temporal(conn)
        if split_id is None:
            pytest.skip("no current temporal split")
        available, foreign = conn.execute(
            """
            SELECT count(*),
                   count(*) FILTER (
                     WHERE s.activity_id IS NULL OR s.partition <> spe.partition)
            FROM split_partition_endpoint spe
            JOIN pair_label l ON l.endpoint_id = spe.endpoint_id
            JOIN pair_label_support sup ON sup.pair_id = l.id
            LEFT JOIN split_activity_assignment s
              ON s.split_id = spe.split_id AND s.activity_id = sup.activity_id
            WHERE spe.split_id = %s
            """,
            (split_id,),
        ).fetchone()
    # Without this the assertion below passes vacuously on a build that recorded
    # no supports at all -- which is exactly what the previous builder did.
    assert available > 0, "no support rows to check; the assertion would be vacuous"
    assert foreign == 0


@pytest.mark.requires_db
def test_temporal_labels_do_not_come_from_the_global_endpoint() -> None:
    """A test-period label must differ from the global one somewhere, or it is the global one."""
    with connect() as conn:
        split_id = _temporal(conn)
        if split_id is None:
            pytest.skip("no current temporal split")
        global_endpoint, test_endpoint = conn.execute(
            "SELECT v.endpoint_id, spe.endpoint_id FROM split_version v "
            "JOIN split_partition_endpoint spe ON spe.split_id=v.id AND spe.partition='test' "
            "WHERE v.id=%s",
            (split_id,),
        ).fetchone()
        assert int(global_endpoint) != int(test_endpoint)
        differing = conn.execute(
            """
            SELECT count(*) FROM pair_label t
            JOIN pair_label g ON g.compound_id=t.compound_id AND g.target_id=t.target_id
              AND g.endpoint_id=%s
            WHERE t.endpoint_id=%s AND (t.label <> g.label OR t.n_exact <> g.n_exact)
            """,
            (int(global_endpoint), int(test_endpoint)),
        ).fetchone()[0]
    # If the partition endpoint were a copy of the global one, nothing would differ.
    assert differing > 0


@pytest.mark.requires_db
def test_temporal_strata_are_defined_per_evaluation_period() -> None:
    """The stratum must be relative to what the *fitted model* saw.

    The discriminating case is a pair measured in validation and test but never
    in training. Under the `train_only` protocol the model scoring the test
    period is fitted on `train` alone, so such a pair is `new` to it -- even
    though the database had measured it before the test window opened. Judging
    recurrence against "everything earlier" would call it recurrent and credit
    the model with evidence it was never shown.

    The earlier measurement is not discarded: it is recorded in
    `history_partitions` and reported as a separate axis.
    """
    with connect() as conn:
        split_id = _temporal(conn)
        if split_id is None:
            pytest.skip("no current temporal split")
        rows = conn.execute(
            """
            WITH per AS (
                SELECT compound_id, target_id,
                       bool_or(partition = 'train') AS tr,
                       bool_or(partition = 'validation') AS va,
                       bool_or(partition = 'test') AS te
                FROM split_pair_assignment
                WHERE split_id = %s AND partition IN ('train','validation','test')
                GROUP BY 1, 2
            )
            SELECT p.partition, p.stratum, p.history_partitions, count(*)
            FROM split_pair_assignment p
            JOIN per ON per.compound_id = p.compound_id AND per.target_id = p.target_id
            WHERE p.split_id = %s AND NOT per.tr AND per.va AND per.te
              AND p.partition IN ('validation','test')
            GROUP BY 1, 2, 3
            """,
            (split_id, split_id),
        ).fetchall()
        history = conn.execute(
            "SELECT count(*) FROM split_pair_assignment WHERE split_id=%s "
            "AND stratum='recurrent' AND coalesce(history_partitions,'')=''",
            (split_id,),
        ).fetchone()[0]
    observed = {(str(a), str(b), str(c or "")) for a, b, c, _ in rows}
    if not observed:
        pytest.skip("no pair measured in validation and test but not training")
    assert observed == {
        ("validation", "new", ""),
        # New to a train-only model, but its earlier validation measurement is
        # recorded rather than dropped.
        ("test", "new", "validation"),
    }, observed
    assert history == 0


@pytest.mark.requires_db
def test_temporal_protocol_is_recorded_on_the_split() -> None:
    """A stratum definition is only interpretable next to the protocol it assumes."""
    with connect() as conn:
        split_id = _temporal(conn)
        if split_id is None:
            pytest.skip("no current temporal split")
        params = conn.execute(
            "SELECT params FROM split_version WHERE id=%s", (split_id,)
        ).fetchone()[0]
    assert (params or {}).get("protocol") == "train_only"


@pytest.mark.requires_db
def test_recurrent_pairs_only_cite_partitions_that_precede_them() -> None:
    """A validation pair may cite train; a train pair may cite nothing."""
    allowed = {"train": set(), "validation": {"train"}, "test": {"train", "validation"}}
    with connect() as conn:
        split_id = _temporal(conn)
        if split_id is None:
            pytest.skip("no current temporal split")
        rows = conn.execute(
            "SELECT DISTINCT partition, history_partitions FROM split_pair_assignment "
            "WHERE split_id=%s AND coalesce(history_partitions,'') <> '' "
            "AND partition IN ('train','validation','test')",
            (split_id,),
        ).fetchall()
    assert rows, "no recurrence history recorded at all"
    for partition, history in rows:
        cited = {p for p in str(history).split(",") if p}
        assert cited <= allowed[str(partition)], f"{partition} cites {cited}"


@pytest.mark.requires_db
def test_recurrent_means_the_training_set_actually_contains_it() -> None:
    """Every `recurrent` pair must have a measurement in the model's training set."""
    with connect() as conn:
        split_id = _temporal(conn)
        if split_id is None:
            pytest.skip("no current temporal split")
        unsupported = conn.execute(
            """
            SELECT count(*) FROM split_pair_assignment p
            WHERE p.split_id = %s AND p.stratum = 'recurrent'
              AND p.partition IN ('validation','test')
              AND NOT EXISTS (
                  SELECT 1 FROM split_activity_assignment s
                  JOIN activity a ON a.id = s.activity_id
                  WHERE s.split_id = p.split_id AND s.partition = 'train'
                    AND a.compound_id = p.compound_id AND a.target_id = p.target_id
              )
            """,
            (split_id,),
        ).fetchone()[0]
    assert unsupported == 0


@pytest.mark.requires_db
def test_training_visibility_excludes_a_partition_excluded_pair() -> None:
    """A pair its own training period judged unusable must not be model-visible.

    Uses a real excluded pair that has an activity in the training period, which
    is exactly the case the previous implementation let through.
    """
    with connect() as conn:
        split_id = _temporal(conn)
        if split_id is None:
            pytest.skip("no current temporal split")
        train_endpoint = conn.execute(
            "SELECT endpoint_id FROM split_partition_endpoint WHERE split_id=%s "
            "AND partition='train'",
            (split_id,),
        ).fetchone()[0]
        victim = conn.execute(
            """
            SELECT l.compound_id, l.target_id FROM pair_label l
            WHERE l.endpoint_id=%s AND l.excluded_from_eval
              AND EXISTS (SELECT 1 FROM split_activity_assignment s
                          JOIN activity a ON a.id=s.activity_id
                          WHERE s.split_id=%s AND s.partition='train'
                            AND a.compound_id=l.compound_id AND a.target_id=l.target_id)
            LIMIT 1
            """,
            (int(train_endpoint), split_id),
        ).fetchone()
        if victim is None:
            pytest.skip("no excluded training pair with an early activity")
        visible = conn.execute(
            f"""
            SELECT count(*) FROM activity a
            WHERE a.compound_id=%s AND a.target_id=%s
              AND a.id IN ({sa.training_visible_activities(conn, split_id)})
            """,  # noqa: S608
            (int(victim[0]), int(victim[1])),
        ).fetchone()[0]
    assert visible == 0


@pytest.mark.requires_db
def test_label_reversal_is_declared_a_diagnostic() -> None:
    """It has no tuning set; pretending otherwise would invite tuning on it."""
    with connect() as conn:
        row = conn.execute(
            "SELECT protocol, n_validation FROM split_version "
            "WHERE split_type='label_reversal' AND superseded_by IS NULL LIMIT 1"
        ).fetchone()
        if row is None:
            pytest.skip("not built")
    assert row[0] == "diagnostic_frozen"
    assert row[1] == 0


@pytest.mark.requires_db
def test_every_scored_target_and_compound_is_clustered() -> None:
    with connect() as conn:
        for split_type, table, column, method in (
            ("cold_protein", "target_cluster", "target_id", "mmseqs2-cluster"),
            ("chemistry_disjoint", "compound_cluster", "compound_id", "bemis-murcko"),
        ):
            row = conn.execute(
                "SELECT id FROM split_version WHERE split_type=%s AND superseded_by IS NULL "
                "ORDER BY id DESC LIMIT 1",
                (split_type,),
            ).fetchone()
            if row is None:
                continue
            missing = conn.execute(
                f"""
                SELECT count(DISTINCT p.{column}) FROM split_pair_assignment p
                WHERE p.split_id=%s AND p.partition IN ('train','validation','test')
                  AND NOT EXISTS (SELECT 1 FROM {table} c
                                  WHERE c.{column}=p.{column} AND c.method=%s)
                """,  # noqa: S608
                (int(row[0]), method),
            ).fetchone()[0]
            assert missing == 0, f"{split_type}: {missing} unclustered entities"


@pytest.mark.requires_db
def test_superseded_splits_are_marked_not_deleted() -> None:
    """A correction supersedes; it never rewrites or removes."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT name, superseded_by, superseded_reason FROM split_version "
            "WHERE superseded_by IS NOT NULL"
        ).fetchall()
        known = {str(r[0]) for r in conn.execute("SELECT name FROM split_version").fetchall()}
        orphaned = conn.execute(
            "SELECT count(*) FROM split_version v WHERE v.superseded_by IS NOT NULL "
            "AND NOT EXISTS (SELECT 1 FROM split_pair_assignment p WHERE p.split_id=v.id)"
        ).fetchone()[0]
    assert rows, "the corrected build should supersede its predecessor"
    for name, replaced_by, reason in rows:
        assert str(replaced_by) != str(name), f"{name} supersedes itself"
        assert reason and len(str(reason)) > 40, f"{name} has no substantive reason"
        assert str(replaced_by) in known, f"{name} points at {replaced_by}, which does not exist"
    # Superseded splits keep their assignments; marking is not deletion.
    assert orphaned == 0


# ================================================== near-homolog evaluation stratum


@pytest.mark.requires_db
def test_near_homolog_stratum_requires_coverage_not_just_identity() -> None:
    """Identity alone would sweep in fragment containment, which is not a homolog.

    The M6 audit found 43 of 44 high-identity hits were a domain construct against
    the full-length protein -- correctly placed in different clusters. Only the
    cases where the alignment explains most of *both* sequences belong here.
    """
    from seq2lead.splits.similarity import NEAR_HOMOLOG_COVERAGE, NEAR_HOMOLOG_IDENTITY

    with connect() as conn:
        rows = conn.execute(
            "SELECT detail FROM split_target_stratum WHERE stratum='near_homolog'"
        ).fetchall()
    if not rows:
        pytest.skip("stratum not recorded")
    for (detail,) in rows:
        d = detail if isinstance(detail, dict) else __import__("json").loads(detail)
        assert d["identity"] >= NEAR_HOMOLOG_IDENTITY
        assert min(d["query_cov"], d["target_cov"]) >= NEAR_HOMOLOG_COVERAGE
        assert d["aln_len"] > 0


@pytest.mark.requires_db
def test_near_homolog_targets_still_satisfy_the_cold_protein_guarantee() -> None:
    """They are a caveat on the split, not a breach of it.

    If one of these shared a cluster with a training target the split would be
    broken, not merely optimistic -- so this distinguishes the two cases.
    """
    with connect() as conn:
        rows = conn.execute(
            "SELECT st.split_id, st.target_id FROM split_target_stratum st "
            "WHERE st.stratum='near_homolog'"
        ).fetchall()
        if not rows:
            pytest.skip("stratum not recorded")
        breached = conn.execute(
            """
            SELECT count(*) FROM split_target_stratum st
            JOIN target_cluster tc ON tc.target_id = st.target_id
            WHERE st.stratum = 'near_homolog'
              AND EXISTS (
                  SELECT 1 FROM split_pair_assignment p
                  JOIN target_cluster tc2 ON tc2.target_id = p.target_id
                  WHERE p.split_id = st.split_id AND p.partition = 'train'
                    AND tc2.cluster_id = tc.cluster_id
              )
            """
        ).fetchone()[0]
    assert breached == 0, (
        f"{breached} near-homolog targets share a cluster with training; that is a "
        "leak, not a caveat"
    )


@pytest.mark.requires_db
def test_near_homolog_stratum_is_a_minority_of_the_test_set() -> None:
    """A sanity bound: if most of the test set were near-homologs the split is not cold."""
    with connect() as conn:
        row = conn.execute(
            """
            SELECT count(*) FILTER (WHERE st.target_id IS NOT NULL), count(*)
            FROM split_pair_assignment p
            LEFT JOIN split_target_stratum st
              ON st.split_id = p.split_id AND st.target_id = p.target_id
              AND st.stratum = 'near_homolog'
            WHERE p.split_id = (
                SELECT id FROM split_version WHERE split_type='cold_protein'
                AND superseded_by IS NULL ORDER BY id DESC LIMIT 1
            ) AND p.partition = 'test'
            """
        ).fetchone()
        if row is None or not row[1]:
            pytest.skip("no cold_protein test partition")
    share = int(row[0]) / int(row[1])
    assert 0 < share < 0.25, f"near-homolog share is {share:.1%}"  # noqa: PLR2004
