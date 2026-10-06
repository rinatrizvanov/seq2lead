"""Does the train_only gate actually hold on the as-of three-way partition?

These tests drive the **production** functions -- the SQL gate every
activity-derived artifact embeds, the retrieval guard, and the activity feature
builder -- against a split carrying `train`, `validation` and an evaluation
partition at once. A unit test of a helper would not have told us anything: the
question is whether the code the as-of experiment will actually call keeps both
reserved partitions out.

Each test writes into a throwaway schema, because a test that leaves synthetic
compounds in the corpus is how two test molecules once reached the M7 caches.
"""

from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.requires_db


def _db_disabled() -> bool:
    return os.environ.get("SEQ2LEAD_SKIP_DB_TESTS") == "1"


@pytest.fixture(scope="module", autouse=True)
def _isolated_schema():
    from seq2lead.db.isolation import isolated_schema

    if _db_disabled():
        yield None
        return
    with isolated_schema("seq2lead_test_m11c") as schema:
        yield schema


#: The three-way partition an as-of run uses. `b_increment` stands in for the
#: evaluation partition; the gate must exclude it for the same reason it excludes
#: `validation` -- neither is training evidence.
PARTITIONS = ("train", "validation", "b_increment")


@pytest.fixture(scope="module")
def asof_split():
    """A split with all three partitions populated, and the ids of each.

    Module-scoped: built once. A function-scoped version re-inserted the same
    split name and InChIKeys for every test and collided on their unique
    constraints.
    """
    from seq2lead.db import connect

    with connect() as conn:
        release = conn.execute(
            """
            INSERT INTO source_release
              (source_name, version, subset, url, archive_member, sha256, md5,
               md5_verified, sha256_pinned, archive_bytes, member_bytes, license,
               downloaded_at, column_names)
            VALUES ('synthetic', 'm11c', 'all', 'http://example.invalid', 'x.tsv',
                    %s, %s, true, true, 1, 1, 'test', now(), '[]'::jsonb)
            RETURNING id
            """,
            ("0" * 64, "0" * 32),
        ).fetchone()[0]
        # One endpoint per partition, as a temporal split declares. A single
        # global endpoint would let a judgement formed after the cut govern the
        # training partition's labels.
        endpoints = {
            partition: conn.execute(
                "INSERT INTO endpoint_version (name, measurement_type, threshold_pki, "
                "discordance_pki, source_release_id, curator_version, builder_version) "
                "VALUES (%s, 'KI', 6.0, 1.0, %s, 'test', 'test') RETURNING id",
                (f"m11c-endpoint--{partition}", release),
            ).fetchone()[0]
            for partition in PARTITIONS
        }
        endpoint = endpoints["train"]
        split = conn.execute(
            "INSERT INTO split_version (name, split_type, endpoint_id, seed, builder_version) "
            "VALUES ('m11c-asof', 'temporal_proxy', %s, 1, 'test') RETURNING id",
            (endpoint,),
        ).fetchone()[0]
        target = conn.execute(
            "INSERT INTO target (sequence_sha256, sequence, length) "
            "VALUES (%s, %s, %s) RETURNING id",
            ("a" * 64, "MKVLSSAAWQRTTYNEQ", 17),
        ).fetchone()[0]

        ids: dict[str, list[int]] = {p: [] for p in PARTITIONS}
        compounds: dict[str, list[int]] = {p: [] for p in PARTITIONS}
        for index, partition in enumerate(PARTITIONS):
            for replicate in range(2):
                compound = conn.execute(
                    "INSERT INTO compound (inchikey, canonical_smiles, standardizer_version) "
                    "VALUES (%s, %s, 'test') RETURNING id",
                    (f"{partition[:3].upper()}{replicate}" + "A" * 20, "CCO"),
                ).fetchone()[0]
                raw = conn.execute(
                    "INSERT INTO raw_measurement (source_release_id, line_no, payload) "
                    "VALUES (%s, %s, '{}'::jsonb) RETURNING id",
                    (release, index * 10 + replicate),
                ).fetchone()[0]
                # a distinct, decisive pKi per partition so a leak is visible in
                # the aggregate rather than merely in a count
                value = {"train": "10", "validation": "100000", "b_increment": "1"}[partition]
                activity = conn.execute(
                    "INSERT INTO activity (source_release_id, raw_measurement_id, "
                    "reactant_set_id, compound_id, target_id, assay_join_status, "
                    "measurement_type, relation, value_text, value_numeric, value_unit, "
                    "in_benchmark_scope, curator_version) "
                    "VALUES (%s,%s,%s,%s,%s,'none','KI','=',%s,%s,'nM',true,'test') RETURNING id",
                    (
                        release,
                        raw,
                        f"{partition}-{replicate}",
                        compound,
                        target,
                        value,
                        float(value),
                    ),
                ).fetchone()[0]
                conn.execute(
                    "INSERT INTO split_activity_assignment "
                    "(split_id, activity_id, partition, date_source) "
                    "VALUES (%s,%s,%s,'synthetic')",
                    (split, activity, partition),
                )
                ids[partition].append(activity)
                compounds[partition].append(compound)

                # the pair-level rows `build_cohort` actually reads, under this
                # partition's own endpoint
                pki = {"train": 8.0, "validation": 4.0, "b_increment": 9.0}[partition]
                conn.execute(
                    "INSERT INTO split_pair_assignment "
                    "(split_id, compound_id, target_id, partition, stratum) "
                    "VALUES (%s,%s,%s,%s,%s)",
                    (split, compound, target, partition, "new"),
                )
                conn.execute(
                    "INSERT INTO pair_regression (endpoint_id, compound_id, target_id, "
                    "n_obs, p_median, p_min, p_max, p_mad, p_spread, is_discordant, "
                    "n_assays, n_publications, in_benchmark_scope) "
                    "VALUES (%s,%s,%s,1,%s,%s,%s,0,0,false,1,1,true)",
                    (endpoints[partition], compound, target, pki, pki, pki),
                )
                conn.execute(
                    "INSERT INTO pair_label (endpoint_id, compound_id, target_id, label, "
                    "status, evidence, n_exact, n_censored, in_benchmark_scope) "
                    "VALUES (%s,%s,%s,%s,'ok','exact',1,0,true)",
                    (
                        endpoints[partition],
                        compound,
                        target,
                        "active" if pki >= 6.0 else "inactive",
                    ),
                )
        conn.commit()
        yield {
            "split_id": split,
            "endpoint_id": endpoint,
            "endpoints": endpoints,
            "ids": ids,
            "compounds": compounds,
            "target_id": target,
        }


class _StubConfig:
    """The minimum `build_cohort` reads from an ExperimentConfig.

    A stub, not a mock: `build_cohort` itself is the production code under test,
    and this only supplies the three attributes it looks up.
    """

    def __init__(self, fixture: dict) -> None:
        self._fixture = fixture
        self.cohort = {"exclude_unusable_fingerprints": False}
        self.caches: dict[str, object] = {}
        self.endpoint_id = fixture["endpoint_id"]

    def split(self, name: str):
        from types import SimpleNamespace

        assert name == "m11c-asof"
        # one endpoint per partition, as a temporal split declares. A global
        # endpoint would import a judgement formed after the cut.
        return SimpleNamespace(
            name=name,
            id=self._fixture["split_id"],
            endpoint_id=self._fixture["endpoint_id"],
            partition_endpoints=dict(self._fixture["endpoints"]),
        )


# ============================= the SQL gate every artifact embeds


def test_the_production_gate_admits_only_the_train_partition(asof_split) -> None:
    """`training_visible_activities` is the only sanctioned source. Drive it directly."""
    from seq2lead.db import connect
    from seq2lead.splits.assertions import training_visible_activities

    with connect() as conn:
        sql = training_visible_activities(conn, asof_split["split_id"])
        visible = {r[0] for r in conn.execute(f"SELECT activity_id FROM ({sql}) v").fetchall()}  # noqa: S608

    assert visible == set(asof_split["ids"]["train"]), "the gate did not return exactly train"
    for reserved in ("validation", "b_increment"):
        leaked = visible & set(asof_split["ids"][reserved])
        assert not leaked, f"{reserved} activities {sorted(leaked)} passed the gate"


def test_the_gate_sql_names_the_train_partition_explicitly(asof_split) -> None:
    from seq2lead.db import connect
    from seq2lead.splits.assertions import training_visible_activities

    with connect() as conn:
        sql = training_visible_activities(conn, asof_split["split_id"])
    assert "partition = 'train'" in sql
    for reserved in ("validation", "b_increment"):
        assert reserved not in sql, f"the gate mentions {reserved}, which it must simply exclude"


# ================================= activity-derived features


def test_activity_features_are_built_from_train_evidence_only(asof_split) -> None:
    """The real builder, on a three-way partition.

    Each partition carries a distinct pKi, so a leak changes the aggregate rather
    than only a count -- train is 10 nM (pKi 8), validation 100000 nM (pKi 4) and
    the increment 1 nM (pKi 9). If either reserved partition contributed, the
    spread would widen and the mean would move.
    """
    from seq2lead.db import connect
    from seq2lead.features.activity import build_entity_activity_features

    with connect() as conn:
        store, _manifest, entity_ids = build_entity_activity_features(
            conn, asof_split["split_id"], entity="target"
        )

    assert entity_ids == [asof_split["target_id"]]
    n_obs, mean_pki, median_pki, spread = store.vectors[0]
    assert int(n_obs) == 2, f"expected the 2 train activities, aggregated {int(n_obs)}"
    assert mean_pki == pytest.approx(8.0, abs=1e-6), "the mean moved, so a reserved row leaked"
    assert median_pki == pytest.approx(8.0, abs=1e-6)
    assert spread == pytest.approx(0.0, abs=1e-6), (
        "a non-zero spread means more than one distinct value reached the aggregate"
    )


def test_a_widened_gate_would_be_caught_by_this_assertion(asof_split) -> None:
    """Shows the previous test can fail: aggregate all three and the numbers move."""
    from seq2lead.db import connect

    with connect() as conn:
        rows = conn.execute(
            """
            SELECT count(*), avg(9 - log(a.value_numeric)),
                   max(9 - log(a.value_numeric)) - min(9 - log(a.value_numeric))
            FROM activity a
            JOIN split_activity_assignment s ON s.activity_id = a.id
            WHERE s.split_id = %s AND a.measurement_type = 'KI' AND a.relation = '='
            """,
            (asof_split["split_id"],),
        ).fetchone()
    assert int(rows[0]) == 6, "all three partitions should be present in the fixture"
    assert float(rows[1]) != pytest.approx(8.0, abs=1e-6)
    assert float(rows[2]) > 0.0, "the unguarded aggregate must differ from the guarded one"


# ============ retrieval: the guard exists; the production path does not
#
# These two are deliberately separate. Testing a guard says the guard works;
# it says nothing about whether anything calls it.


def test_the_retrieval_guard_refuses_a_reserved_activity(asof_split) -> None:
    """The guard itself, driven against a three-way partition."""
    from seq2lead.db import connect
    from seq2lead.splits.assertions import LeakageError, assert_index_is_training_only

    train = asof_split["ids"]["train"]
    with connect() as conn:
        assert_index_is_training_only(conn, asof_split["split_id"], train)
        for reserved in ("validation", "b_increment"):
            with pytest.raises(LeakageError, match="not in its training partition"):
                assert_index_is_training_only(
                    conn, asof_split["split_id"], [*train, asof_split["ids"][reserved][0]]
                )


def test_the_retrieval_guard_message_counts_the_leak(asof_split) -> None:
    from seq2lead.db import connect
    from seq2lead.splits.assertions import LeakageError, assert_index_is_training_only

    offered = [*asof_split["ids"]["train"], *asof_split["ids"]["validation"]]
    with connect() as conn:
        with pytest.raises(LeakageError) as excinfo:
            assert_index_is_training_only(conn, asof_split["split_id"], offered)
    assert "2 of 4" in str(excinfo.value)


def test_no_production_code_builds_a_retrieval_index_yet() -> None:
    """**UNIMPLEMENTED**, recorded rather than claimed verified.

    `assert_index_is_training_only` has no caller outside tests, because no
    production retrieval index builder exists. A previous report listed retrieval
    as verified production wiring; what was verified was the guard. This test
    pins the gap so that adding a builder without wiring the guard fails here.
    """
    import subprocess

    hits = subprocess.run(
        ["grep", "-rn", "assert_index_is_training_only", "--include=*.py", "src/"],
        capture_output=True,
        text=True,
        check=False,
    ).stdout.splitlines()
    callers = [
        line
        for line in hits
        if "def assert_index_is_training_only" not in line
        and "splits/__init__.py" not in line  # a re-export, not a call
        and "profiling/split_report.py" not in line  # prose in a report
    ]
    assert callers == [], (
        "a production caller appeared; retrieval index construction is no longer "
        f"unimplemented and this test should become a real wiring test: {callers}"
    )


# ============ baseline target statistics, through the production loader


def test_the_production_cohort_loader_returns_one_partition(asof_split) -> None:
    """`eval.cohort.build_cohort` is the loader the baselines are fed from.

    An earlier version of this test hand-filtered a `Cohort` and then checked the
    filter it had just applied, which proved nothing about production. This calls
    the real loader once per partition and checks the partitions do not bleed.
    """
    from seq2lead.db import connect
    from seq2lead.eval.cohort import build_cohort

    config = _StubConfig(asof_split)
    loaded = {}
    with connect() as conn:
        for partition in PARTITIONS:
            loaded[partition] = build_cohort(
                conn, config, "m11c-asof", partition, require_regression_label=False
            )

    for partition, cohort in loaded.items():
        assert cohort.partition == partition
    # the three partitions carry disjoint compound sets
    sets = {p: set(c.compound_id.tolist()) for p, c in loaded.items()}
    assert sets["train"] and not sets["train"] & sets["validation"]
    assert not sets["train"] & sets["b_increment"]
    assert not sets["validation"] & sets["b_increment"]


def test_baseline_statistics_fitted_on_the_loaded_train_cohort(asof_split) -> None:
    """B0, fed the production loader's train cohort rather than a hand-built one."""
    from seq2lead.db import connect
    from seq2lead.eval.baselines import GlobalAndTargetMean
    from seq2lead.eval.cohort import build_cohort

    config = _StubConfig(asof_split)
    with connect() as conn:
        train = build_cohort(conn, config, "m11c-asof", "train", require_regression_label=False)
    assert len(train) == 2, f"the loader returned {len(train)} train rows"

    model = GlobalAndTargetMean()
    model.fit(bank=None, train=train, validation=None, seed=None)
    # train is 10 nM -> pKi 8; validation 100000 -> 4; increment 1 -> 9
    assert model.target_mean[asof_split["target_id"]] == pytest.approx(8.0, abs=1e-6)
    assert model.global_mean == pytest.approx(8.0, abs=1e-6)


def test_the_selection_gate_and_the_sql_gate_agree(asof_split) -> None:
    """Two independent implementations of 'train only' must not disagree."""
    from seq2lead.asof.evaluation import ActivityRow, select_for_fitting
    from seq2lead.db import connect
    from seq2lead.splits.assertions import training_visible_activities

    rows = [
        ActivityRow(activity_id=i, partition=partition)
        for partition in PARTITIONS
        for i in asof_split["ids"][partition]
    ]
    from_harness = set(select_for_fitting(rows, "cross-check"))
    with connect() as conn:
        sql = training_visible_activities(conn, asof_split["split_id"])
        from_sql = {r[0] for r in conn.execute(f"SELECT activity_id FROM ({sql}) v").fetchall()}  # noqa: S608
    assert from_harness == from_sql, (
        "the harness gate and the production SQL gate selected different activities"
    )


# ============ evaluation-mode evidence: what the protection actually is


def test_the_evidence_api_does_not_filter_by_partition(asof_split) -> None:
    """**Correcting an earlier claim.**

    A previous report said evaluation-mode evidence was verified as restricted to
    the train partition. It is not, and the production code never claimed to be:
    `rank._prior_evidence` queries `activity` with no partition predicate, and
    its docstring says "demo only". The protection is that evaluation mode does
    not show evidence **at all**, which the CLI enforces by passing
    `show_evidence=False`.

    This test records the real behaviour, so a future partition-filtered evidence
    API has something to change.
    """
    import inspect

    from seq2lead.db import connect
    from seq2lead.models import rank

    source = inspect.getsource(rank._prior_evidence)
    # the docstring mentions "training partition" in prose, so inspect the SQL
    # rather than the whole function text
    sql = source[source.index('"""', source.index('"""') + 3) :]
    for predicate in ("split_activity_assignment", "split_pair_assignment", "partition ="):
        assert predicate not in sql, (
            f"the evidence query gained {predicate!r}; this test now understates it"
        )
    assert "Demo only" in (rank._prior_evidence.__doc__ or "")

    with connect() as conn:
        sequence = conn.execute(
            "SELECT sequence FROM target WHERE id=%s", (asof_split["target_id"],)
        ).fetchone()[0]
        compounds = [
            r[0]
            for r in conn.execute(
                "SELECT DISTINCT compound_id FROM activity WHERE target_id=%s",
                (asof_split["target_id"],),
            ).fetchall()
        ]
        evidence = rank._prior_evidence(conn, compounds, sequence)
    # it returns evidence for every partition's compounds, which is why it is
    # confined to demo mode rather than filtered
    assert len(evidence) == 6, f"expected all six compounds, got {len(evidence)}"


def test_evaluation_mode_suppresses_evidence_entirely() -> None:
    """The actual enforcement: the CLI never asks for evidence when evaluating."""
    import inspect

    from seq2lead import cli

    source = inspect.getsource(cli)
    assert 'show_evidence=evidence_mode == "demo"' in source, (
        "evaluation mode must not request evidence at all"
    )


def test_a_partition_filtered_evidence_api_is_unimplemented() -> None:
    """**UNIMPLEMENTED**, recorded rather than claimed verified.

    An as-of run that wants to *show* evidence during evaluation needs an API
    that restricts to the train partition. None exists. Until one does, the as-of
    experiment must run with evidence suppressed, and that is a limitation rather
    than a safeguard.
    """
    import inspect
    import subprocess

    hits = set(
        subprocess.run(
            ["grep", "-rln", "evidence_mode", "--include=*.py", "src/"],
            capture_output=True,
            text=True,
            check=False,
        ).stdout.split()
    )
    # `rank.render` reads the mode too -- it decides whether to *print* evidence.
    # Both gate on the mode; neither filters by partition.
    assert hits == {"src/seq2lead/cli.py", "src/seq2lead/models/rank.py"}, (
        f"evidence_mode handling moved ({sorted(hits)}); if a partition-filtered "
        "evidence API has been added, replace this test with a real wiring test"
    )
    from seq2lead.models import rank

    render_source = inspect.getsource(rank.render)
    # `render` gates on the mode, which is the actual protection: in evaluation
    # mode no evidence is printed at all. It is a display decision, not a query,
    # so there is nothing here to filter by partition.
    assert 'evidence_mode == "demo"' in render_source
    assert "SELECT" not in render_source.upper(), (
        "render issues a query now; it would need its own partition filter"
    )
