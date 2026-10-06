"""Build the five splits defined in `docs/SPLITS.md`.

Every split here assigns **whole compound-target pairs** except
`temporal_proxy`, which assigns activities and then aggregates inside each
partition. That asymmetry is the contract, not an inconsistency: a pair is the
smallest scored object, so splitting one would put a measurement of a test pair
into training, while a temporal cut is defined on dates and has to act on the
dated thing.

Pairs the endpoint marked `excluded_from_eval` are kept out of validation and
test everywhere, and by default out of training too — a first benchmark should
not learn from evidence the project has already called unreliable.
"""

from __future__ import annotations

import datetime as dt
import json
import random
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from seq2lead.splits.clustering import (
    DEFAULT_IDENTITY,
    MMSEQS_METHOD,
    SCAFFOLD_METHOD,
)

if TYPE_CHECKING:
    import psycopg

BUILDER_VERSION = "m6/v3"

TRAIN, VALIDATION, TEST, EXCLUDED = "train", "validation", "test", "excluded"
DEFAULT_FRACTIONS = (0.70, 0.10, 0.20)

#: Cut for `temporal_proxy`. Publication dates run from the 1970s to the release
#: date; this leaves a test period with enough new pairs to be worth scoring.
DEFAULT_TEMPORAL_CUT = dt.date(2020, 1, 1)
DEFAULT_VALIDATION_CUT = dt.date(2018, 1, 1)

STRATUM_NEW = "new"
STRATUM_RECURRENT = "recurrent"


@dataclass
class SplitCounters:
    split_id: int = 0
    pairs: int = 0
    by_partition: dict[str, int] = field(default_factory=dict)
    by_stratum: dict[str, int] = field(default_factory=dict)
    groups: int = 0
    activities: int = 0
    seconds: float = 0.0

    def bump(self, bucket: dict[str, int], key: str, n: int = 1) -> None:
        bucket[key] = bucket.get(key, 0) + n


def _register(
    conn: psycopg.Connection,
    name: str,
    split_type: str,
    endpoint_id: int,
    seed: int,
    params: dict,
    notes: str = "",
    protocol: str = "trainable",
) -> int:
    existing = conn.execute("SELECT id FROM split_version WHERE name=%s", (name,)).fetchone()
    if existing is not None:
        raise ValueError(
            f"split {name!r} already exists as id {existing[0]}. Splits are immutable; "
            "build a new name rather than editing one."
        )
    row = conn.execute(
        "INSERT INTO split_version (name, split_type, endpoint_id, seed, params, "
        "builder_version, notes, protocol) VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
        (
            name,
            split_type,
            endpoint_id,
            seed,
            json.dumps(params),
            BUILDER_VERSION,
            notes,
            protocol,
        ),
    ).fetchone()
    assert row is not None
    return int(row[0])


def _eligible_pairs(
    conn: psycopg.Connection, endpoint_id: int, include_excluded: bool = True
) -> list[tuple[int, int, bool]]:
    """(compound_id, target_id, excluded_from_eval) for every labelled pair."""
    clause = "" if include_excluded else " AND NOT excluded_from_eval"
    rows = conn.execute(
        "SELECT compound_id, target_id, excluded_from_eval FROM pair_label "
        f"WHERE endpoint_id=%s{clause} ORDER BY compound_id, target_id",  # noqa: S608
        (endpoint_id,),
    ).fetchall()
    return [(int(r[0]), int(r[1]), bool(r[2])) for r in rows]


def _write_pairs(
    conn: psycopg.Connection, split_id: int, rows: list[tuple[int, int, str, str | None]]
) -> None:
    copy_sql = (
        "COPY split_pair_assignment (split_id, compound_id, target_id, partition, stratum) "
        "FROM STDIN"
    )
    with conn.cursor() as cur, cur.copy(copy_sql) as cp:
        for compound_id, target_id, partition, stratum in rows:
            cp.write_row((split_id, compound_id, target_id, partition, stratum))


def _assign_groups(
    groups: dict[str, list[tuple[int, int]]],
    seed: int,
    fractions: tuple[float, float, float],
) -> list[tuple[int, int, str, None]]:
    """Assign whole groups to partitions, largest first for stable proportions."""
    rng = random.Random(seed)
    ordered = sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    total = sum(len(v) for v in groups.values())
    quota = {
        TRAIN: fractions[0] * total,
        VALIDATION: fractions[1] * total,
        TEST: fractions[2] * total,
    }
    filled = dict.fromkeys(quota, 0)
    out: list[tuple[int, int, str, None]] = []
    for _, members in ordered:
        # Greedy: whichever partition is furthest below its quota takes the group.
        # Ties broken by the seeded rng so the result is reproducible.
        deficits = [(quota[p] - filled[p], rng.random(), p) for p in (TRAIN, VALIDATION, TEST)]
        target = max(deficits)[2]
        filled[target] += len(members)
        out.extend((c, t, target, None) for c, t in members)
    return out


# --------------------------------------------------------------------- splits


def build_random_pair(
    conn: psycopg.Connection,
    endpoint_id: int,
    name: str,
    seed: int = 20260929,
    fractions: tuple[float, float, float] = DEFAULT_FRACTIONS,
    exclude_ineligible_from_training: bool = True,
) -> SplitCounters:
    """Whole pairs assigned at random. The inflated reference number."""
    started = time.perf_counter()
    split_id = _register(
        conn,
        name,
        "random_pair",
        endpoint_id,
        seed,
        {"fractions": list(fractions)},
        "Whole pairs assigned at random; the reference against which the cold splits are read.",
    )
    pairs = _eligible_pairs(conn, endpoint_id)
    rng = random.Random(seed)
    rng.shuffle(pairs)

    counters = SplitCounters(split_id=split_id)
    rows: list[tuple[int, int, str, str | None]] = []
    usable = [(c, t) for c, t, excluded in pairs if not excluded]
    ineligible = [(c, t) for c, t, excluded in pairs if excluded]

    n_train = int(len(usable) * fractions[0])
    n_val = int(len(usable) * fractions[1])
    for index, (compound_id, target_id) in enumerate(usable):
        partition = TRAIN if index < n_train else (VALIDATION if index < n_train + n_val else TEST)
        rows.append((compound_id, target_id, partition, None))
        counters.bump(counters.by_partition, partition)
    for compound_id, target_id in ineligible:
        partition = EXCLUDED if exclude_ineligible_from_training else TRAIN
        rows.append((compound_id, target_id, partition, None))
        counters.bump(counters.by_partition, partition)

    _write_pairs(conn, split_id, rows)
    counters.pairs = len(rows)
    counters.seconds = time.perf_counter() - started
    return _finalise(conn, counters)


def _grouped_split(
    conn: psycopg.Connection,
    endpoint_id: int,
    name: str,
    split_type: str,
    group_of: dict[int, str],
    key: str,
    seed: int,
    fractions: tuple[float, float, float],
    params: dict,
    notes: str,
    exclude_ineligible_from_training: bool = True,
) -> SplitCounters:
    """Shared machinery for cold_protein and chemistry_disjoint."""
    started = time.perf_counter()
    split_id = _register(conn, name, split_type, endpoint_id, seed, params, notes)
    pairs = _eligible_pairs(conn, endpoint_id)

    counters = SplitCounters(split_id=split_id)
    groups: dict[str, list[tuple[int, int]]] = {}
    rows: list[tuple[int, int, str, str | None]] = []
    for compound_id, target_id, excluded in pairs:
        if excluded and exclude_ineligible_from_training:
            rows.append((compound_id, target_id, EXCLUDED, None))
            counters.bump(counters.by_partition, EXCLUDED)
            continue
        entity = compound_id if key == "compound" else target_id
        cluster = group_of.get(entity)
        if cluster is None:
            # No cluster assignment means we cannot guarantee disjointness for it.
            rows.append((compound_id, target_id, EXCLUDED, None))
            counters.bump(counters.by_partition, EXCLUDED)
            continue
        groups.setdefault(cluster, []).append((compound_id, target_id))

    assigned = _assign_groups(groups, seed, fractions)
    for compound_id, target_id, partition, _ in assigned:
        rows.append((compound_id, target_id, partition, None))
        counters.bump(counters.by_partition, partition)

    _write_pairs(conn, split_id, rows)
    counters.pairs = len(rows)
    counters.groups = len(groups)
    counters.seconds = time.perf_counter() - started
    return _finalise(conn, counters)


def build_cold_protein(
    conn: psycopg.Connection,
    endpoint_id: int,
    name: str,
    identity: float = DEFAULT_IDENTITY,
    seed: int = 20260929,
    fractions: tuple[float, float, float] = DEFAULT_FRACTIONS,
) -> SplitCounters:
    """Whole sequence clusters held out. Tests generalization to new targets."""
    membership = dict(
        conn.execute(
            "SELECT target_id, cluster_id FROM target_cluster "
            "WHERE method=%s AND threshold=%s::numeric",
            (MMSEQS_METHOD, identity),
        ).fetchall()
    )
    if not membership:
        raise RuntimeError(
            f"no target clusters at {identity} identity. Run `seq2lead split cluster-targets` "
            "first; holding out individual proteins while their homologs stay in training "
            "would not be a cold-protein test."
        )
    return _grouped_split(
        conn,
        endpoint_id,
        name,
        "cold_protein",
        {int(k): str(v) for k, v in membership.items()},
        "target",
        seed,
        fractions,
        {"identity": identity, "method": MMSEQS_METHOD},
        f"Whole MMseqs2 clusters at {identity:.0%} identity held out together.",
    )


def build_chemistry_disjoint(
    conn: psycopg.Connection,
    endpoint_id: int,
    name: str,
    seed: int = 20260929,
    fractions: tuple[float, float, float] = DEFAULT_FRACTIONS,
) -> SplitCounters:
    """Whole Bemis-Murcko scaffolds held out. Tests generalization to new chemistry."""
    membership = dict(
        conn.execute(
            "SELECT compound_id, cluster_id FROM compound_cluster WHERE method=%s",
            (SCAFFOLD_METHOD,),
        ).fetchall()
    )
    if not membership:
        raise RuntimeError("no compound clusters. Run `seq2lead split cluster-compounds` first.")
    return _grouped_split(
        conn,
        endpoint_id,
        name,
        "chemistry_disjoint",
        {int(k): str(v) for k, v in membership.items()},
        "compound",
        seed,
        fractions,
        {"method": SCAFFOLD_METHOD},
        "Whole Bemis-Murcko scaffolds held out together.",
    )


def build_label_reversal(
    conn: psycopg.Connection,
    endpoint_id: int,
    name: str,
    n_compounds: int = 500,
    seed: int = 20260929,
) -> SplitCounters:
    """TransformerCPI's construction: memorizing a ligand becomes actively wrong.

    Compounds that carry both an active and an inactive label are selected; one
    set contributes its *inactive* pairs to test and its actives to train, a
    second set the reverse. Whole pairs still move together — the same compound
    appears on both sides, but no pair does.
    """
    started = time.perf_counter()
    split_id = _register(
        conn,
        name,
        "label_reversal",
        endpoint_id,
        seed,
        {"n_compounds": n_compounds},
        "DIAGNOSTIC. Compounds appearing in both classes; their pairs split so that "
        "a model memorizing ligand identity is penalized rather than merely unhelped. "
        "It has no tuning set by construction: a validation set drawn from reversal "
        "pairs would be used to tune against the very property being measured. Run it "
        "once, after model choices are frozen on the other splits.",
        protocol="diagnostic_frozen",
    )
    both = [
        int(r[0])
        for r in conn.execute(
            "SELECT compound_id FROM pair_label WHERE endpoint_id=%s "
            "AND NOT excluded_from_eval AND label IN ('active','inactive') "
            "GROUP BY compound_id "
            "HAVING count(*) FILTER (WHERE label='active') > 0 "
            "AND count(*) FILTER (WHERE label='inactive') > 0",
            (endpoint_id,),
        ).fetchall()
    ]
    rng = random.Random(seed)
    rng.shuffle(both)
    take = min(n_compounds * 2, len(both))
    reversed_to_inactive = set(both[: take // 2])
    reversed_to_active = set(both[take // 2 : take])

    counters = SplitCounters(split_id=split_id)
    rows: list[tuple[int, int, str, str | None]] = []
    for compound_id, target_id, label, excluded in conn.execute(
        "SELECT compound_id, target_id, label, excluded_from_eval FROM pair_label "
        "WHERE endpoint_id=%s",
        (endpoint_id,),
    ).fetchall():
        if excluded:
            partition = EXCLUDED
        elif compound_id in reversed_to_inactive:
            partition = TEST if label == "inactive" else TRAIN
        elif compound_id in reversed_to_active:
            partition = TEST if label == "active" else TRAIN
        else:
            partition = TRAIN
        rows.append((int(compound_id), int(target_id), partition, None))
        counters.bump(counters.by_partition, partition)

    _write_pairs(conn, split_id, rows)
    counters.pairs = len(rows)
    counters.groups = len(reversed_to_inactive) + len(reversed_to_active)
    counters.seconds = time.perf_counter() - started
    return _finalise(conn, counters)


#: The order in which a partition's history accumulates. Validation may see
#: training; test may see training and validation. Nothing sees its own future.
#: **Temporal protocol: `train_only`.** The model scored on a given evaluation
#: period is fitted on the `train` partition alone. Validation is used to select
#: between models and to stop training; it is *not* folded back in before the
#: test evaluation.
#:
#: The alternative -- refit on train+validation before scoring test -- is
#: defensible and standard, but it is a different protocol and would widen what
#: `training_visible_activities()` returns, letting validation evidence into
#: every retrieval index and model-facing feature. Choosing it later means
#: minting a new split version, not editing this one.
TEMPORAL_PROTOCOL = "train_only"

#: What the fitted model actually saw, per evaluation period. This is what the
#: headline `new`/`recurrent` stratum is computed against: a pair is recurrent
#: only if the model scoring it had already been shown a measurement of it.
TRAINING_SET = {TRAIN: (), VALIDATION: (TRAIN,), TEST: (TRAIN,)}

#: Everything measured *earlier in time* than the period, whether or not the
#: model saw it. Reported as a separate axis so a pair that was measured in the
#: validation window but never trained on is visible as such rather than being
#: silently counted as either new or recurrent.
HISTORY = {TRAIN: (), VALIDATION: (TRAIN,), TEST: (TRAIN, VALIDATION)}


def build_temporal_proxy(
    conn: psycopg.Connection,
    endpoint_id: int,
    name: str,
    test_cut: dt.date = DEFAULT_TEMPORAL_CUT,
    validation_cut: dt.date = DEFAULT_VALIDATION_CUT,
    threshold: float = 6.0,
) -> SplitCounters:
    """Assign activities by date, then build a **separate endpoint per partition**.

    The previous implementation stopped after assigning dates and then joined the
    global M4 `pair_label`. Those labels are medians and interval intersections
    over *every* measurement of a pair, including ones dated after the cut, so a
    temporal test pair's ground truth already contained its own future. That is
    the leak this split exists to prevent.

    Each partition now gets its own `endpoint_version`, built by the same M4
    builder over that partition's assigned activities alone and subject to the
    same exact/censored conflict rules. Eligibility is re-derived from the
    evidence available inside the partition: a pair the *global* endpoint
    excluded because of a later contradictory measurement is not excluded from
    temporal training on that account, because that measurement does not exist
    yet from training's point of view.
    """
    from seq2lead.curate import CURATOR_VERSION
    from seq2lead.endpoint.build import build_endpoint

    started = time.perf_counter()
    split_id = _register(
        conn,
        name,
        "temporal_proxy",
        endpoint_id,
        0,
        {
            "test_cut": test_cut.isoformat(),
            "validation_cut": validation_cut.isoformat(),
            "threshold_pki": threshold,
            "protocol": TEMPORAL_PROTOCOL,
        },
        "Activities assigned by date, then one endpoint built per partition from "
        "that partition's activities alone. A proxy, not an as-of snapshot.",
    )
    counters = SplitCounters(split_id=split_id)

    # The split covers the endpoint's release, not whatever else happens to be in
    # the database. Without this a second ingested release would silently join the
    # temporal partitions of a split built for the first.
    release_id = conn.execute(
        "SELECT source_release_id FROM endpoint_version WHERE id=%s", (endpoint_id,)
    ).fetchone()[0]

    conn.execute(
        r"""
        INSERT INTO split_activity_assignment (split_id, activity_id, partition, date_source)
        SELECT %(split)s, a.id,
               CASE
                   WHEN d.parsed >= %(test_cut)s       THEN 'test'
                   WHEN d.parsed >= %(validation_cut)s THEN 'validation'
                   ELSE 'train'
               END,
               d.source
        FROM activity a
        CROSS JOIN LATERAL (
            SELECT CASE
                       WHEN a.publication_date ~ '^\d{1,2}/\d{1,2}/\d{4}$'
                           THEN to_date(a.publication_date, 'MM/DD/YYYY')
                       WHEN a.bindingdb_date ~ '^\d{1,2}/\d{1,2}/\d{4}$'
                           THEN to_date(a.bindingdb_date, 'MM/DD/YYYY')
                   END AS parsed,
                   CASE
                       WHEN a.publication_date ~ '^\d{1,2}/\d{1,2}/\d{4}$'
                           THEN 'publication' ELSE 'bindingdb'
                   END AS source
        ) d
        WHERE a.measurement_type = 'KI' AND d.parsed IS NOT NULL
          AND a.source_release_id = %(release)s
        """,
        {
            "split": split_id,
            "test_cut": test_cut,
            "validation_cut": validation_cut,
            "release": int(release_id),
        },
    )
    row = conn.execute(
        "SELECT count(*) FROM split_activity_assignment WHERE split_id=%s", (split_id,)
    ).fetchone()
    counters.activities = int(row[0]) if row else 0

    # Which partitions each pair was actually measured in. Materialised once:
    # the stratum and history columns below both consult it per pair, and a
    # correlated scan of `activity` for half a million pairs is not affordable.
    conn.execute("DROP TABLE IF EXISTS pair_parts")
    conn.execute(
        "CREATE TEMP TABLE pair_parts AS "
        "SELECT DISTINCT a.compound_id, a.target_id, s.partition "
        "FROM split_activity_assignment s JOIN activity a ON a.id = s.activity_id "
        "WHERE s.split_id = %s",
        (split_id,),
    )
    conn.execute("CREATE INDEX ON pair_parts (compound_id, target_id)")
    conn.execute("ANALYZE pair_parts")

    for partition in (TRAIN, VALIDATION, TEST):
        activity_filter = (
            "SELECT activity_id FROM split_activity_assignment "
            f"WHERE split_id = {int(split_id)} AND partition = '{partition}'"
        )
        part_endpoint, part_counters = build_endpoint(
            conn,
            int(release_id),
            name=f"{name}--{partition}",
            threshold=threshold,
            curator_version=CURATOR_VERSION,
            activity_filter=activity_filter,
            notes=(
                f"Temporal partition '{partition}' of split {name}. Built from that "
                "partition's activities only; never from the global endpoint."
            ),
        )
        conn.execute(
            "INSERT INTO split_partition_endpoint (split_id, partition, endpoint_id) "
            "VALUES (%s,%s,%s)",
            (split_id, partition, part_endpoint),
        )
        counters.bump(counters.by_partition, partition, part_counters.label_pairs)

        # The stratum is relative to what the *fitted model* saw, not to
        # everything measured earlier. Under `train_only` a pair first measured
        # in the validation window is new to the model scoring the test period,
        # even though it is not new to the database.
        seen_sql = (
            "SELECT 1 FROM pair_parts pp WHERE pp.compound_id = l.compound_id "
            "AND pp.target_id = l.target_id AND pp.partition = ANY(%(training_set)s)"
        )
        history_sql = (
            "SELECT string_agg(pp.partition, ',' ORDER BY pp.partition) FROM pair_parts pp "
            "WHERE pp.compound_id = l.compound_id AND pp.target_id = l.target_id "
            "AND pp.partition = ANY(%(history)s)"
        )
        conn.execute(
            f"""
            INSERT INTO split_pair_assignment
                (split_id, compound_id, target_id, partition, stratum, history_partitions)
            SELECT %(split)s, l.compound_id, l.target_id,
                   CASE WHEN l.excluded_from_eval AND %(partition)s <> 'train'
                        THEN 'excluded' ELSE %(partition)s END,
                   CASE WHEN EXISTS ({seen_sql}) THEN '{STRATUM_RECURRENT}'
                        ELSE '{STRATUM_NEW}' END,
                   ({history_sql})
            FROM pair_label l
            WHERE l.endpoint_id = %(part_endpoint)s
            ON CONFLICT (split_id, compound_id, target_id, partition) DO NOTHING
            """,  # noqa: S608
            {
                "split": split_id,
                "partition": partition,
                "part_endpoint": part_endpoint,
                "training_set": list(TRAINING_SET[partition]) or [""],
                "history": list(HISTORY[partition]) or [""],
            },
        )

    for stratum, n in conn.execute(
        "SELECT stratum, count(*) FROM split_pair_assignment WHERE split_id=%s "
        "AND partition='test' GROUP BY 1",
        (split_id,),
    ).fetchall():
        counters.bump(counters.by_stratum, f"test:{stratum}", int(n))
    for stratum, n in conn.execute(
        "SELECT stratum, count(*) FROM split_pair_assignment WHERE split_id=%s "
        "AND partition='validation' GROUP BY 1",
        (split_id,),
    ).fetchall():
        counters.bump(counters.by_stratum, f"validation:{stratum}", int(n))

    counters.by_partition.clear()
    for partition, n in conn.execute(
        "SELECT partition, count(*) FROM split_pair_assignment WHERE split_id=%s GROUP BY 1",
        (split_id,),
    ).fetchall():
        counters.bump(counters.by_partition, str(partition), int(n))
    counters.pairs = sum(counters.by_partition.values())
    counters.seconds = time.perf_counter() - started
    return _finalise(conn, counters)


def _finalise(conn: psycopg.Connection, counters: SplitCounters) -> SplitCounters:
    conn.execute(
        "UPDATE split_version SET n_train=%s, n_validation=%s, n_test=%s, n_excluded=%s "
        "WHERE id=%s",
        (
            counters.by_partition.get(TRAIN, 0),
            counters.by_partition.get(VALIDATION, 0),
            counters.by_partition.get(TEST, 0),
            counters.by_partition.get(EXCLUDED, 0),
            counters.split_id,
        ),
    )
    return counters
