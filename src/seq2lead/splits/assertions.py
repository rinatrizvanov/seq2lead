"""Build-failing leakage checks, one per row of the table in `docs/SPLITS.md`.

These are assertions, not statistics. A number to eyeball gets eyeballed once;
a check that fails the build gets fixed.

`temporal_proxy` is exempt from the pair-overlap assertion **by construction**:
a recurrent pair has measurements on both sides of the cut, which is the thing
the recurrent stratum exists to score. Its substitute assertions are that every
activity lands in exactly one partition and that no aggregate spans partitions.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import psycopg

TRAIN, VALIDATION, TEST, EXCLUDED = "train", "validation", "test", "excluded"
SCORED = (TRAIN, VALIDATION, TEST)


class LeakageError(AssertionError):
    """A split violates a guarantee the benchmark depends on."""


@dataclass
class Check:
    name: str
    applies_to: tuple[str, ...]
    observed: int
    expected: int
    detail: str = ""

    @property
    def passed(self) -> bool:
        return self.observed == self.expected


def _scalar(conn: psycopg.Connection, sql: str, params: tuple) -> int:
    row = conn.execute(sql, params).fetchone()
    return int(row[0]) if row and row[0] is not None else 0


def run_checks(conn: psycopg.Connection, split_id: int) -> list[Check]:
    meta = conn.execute(
        "SELECT name, split_type, endpoint_id, params FROM split_version WHERE id=%s",
        (split_id,),
    ).fetchone()
    if meta is None:
        raise LookupError(f"split {split_id} does not exist")
    split_type = str(meta[1])
    checks: list[Check] = []

    if split_type != "temporal_proxy":
        checks.append(
            Check(
                "pair appears in exactly one partition",
                (split_type,),
                _scalar(
                    conn,
                    "SELECT count(*) FROM (SELECT compound_id, target_id FROM "
                    "split_pair_assignment WHERE split_id=%s GROUP BY 1,2 "
                    "HAVING count(*) > 1) x",
                    (split_id,),
                ),
                0,
                "A pair in two partitions puts a test pair's measurement in training.",
            )
        )
    else:
        checks.append(
            Check(
                "activity assigned to exactly one partition",
                (split_type,),
                _scalar(
                    conn,
                    "SELECT count(*) FROM (SELECT activity_id FROM "
                    "split_activity_assignment WHERE split_id=%s GROUP BY 1 "
                    "HAVING count(*) > 1) x",
                    (split_id,),
                ),
                0,
                "Dates are assigned to activities; each belongs to one period.",
            )
        )
        checks.append(
            Check(
                "every temporal partition has its own endpoint",
                (split_type,),
                _scalar(
                    conn,
                    "SELECT 3 - count(*) FROM split_partition_endpoint WHERE split_id=%s "
                    "AND partition IN ('train','validation','test')",
                    (split_id,),
                ),
                0,
                "Without a partition-scoped endpoint the split falls back to global "
                "M4 labels, which summarise measurements from after the cut.",
            )
        )
        checks.append(
            Check(
                "no aggregate is supported by an activity from another partition",
                (split_type,),
                _scalar(
                    conn,
                    """
                    SELECT count(*) FROM split_partition_endpoint spe
                    JOIN pair_label l ON l.endpoint_id = spe.endpoint_id
                    JOIN pair_label_support sup ON sup.pair_id = l.id
                    LEFT JOIN split_activity_assignment s
                      ON s.split_id = spe.split_id AND s.activity_id = sup.activity_id
                    WHERE spe.split_id = %s
                      AND (s.activity_id IS NULL OR s.partition <> spe.partition)
                    """,
                    (split_id,),
                ),
                0,
                "An aggregate whose support comes from another period has seen the "
                "future; this is the check the previous implementation lacked.",
            )
        )
        checks.append(
            Check(
                "no regression aggregate borrows support across partitions",
                (split_type,),
                _scalar(
                    conn,
                    """
                    SELECT count(*) FROM split_partition_endpoint spe
                    JOIN pair_regression r ON r.endpoint_id = spe.endpoint_id
                    JOIN pair_regression_support sup ON sup.pair_id = r.id
                    LEFT JOIN split_activity_assignment s
                      ON s.split_id = spe.split_id AND s.activity_id = sup.activity_id
                    WHERE spe.split_id = %s
                      AND (s.activity_id IS NULL OR s.partition <> spe.partition)
                    """,
                    (split_id,),
                ),
                0,
                "A partition-scoped median must summarise only that partition.",
            )
        )
        checks.append(
            Check(
                "assigned pairs come from the partition endpoint, not the global one",
                (split_type,),
                _scalar(
                    conn,
                    """
                    SELECT count(*) FROM split_pair_assignment p
                    JOIN split_partition_endpoint spe
                      ON spe.split_id = p.split_id AND spe.partition = p.partition
                    WHERE p.split_id = %s
                      AND p.partition IN ('train','validation','test')
                      AND NOT EXISTS (
                          SELECT 1 FROM pair_label l
                          WHERE l.endpoint_id = spe.endpoint_id
                            AND l.compound_id = p.compound_id
                            AND l.target_id = p.target_id
                      )
                    """,
                    (split_id,),
                ),
                0,
                "",
            )
        )
        checks.append(
            Check(
                "recurrent pairs are labelled, not hidden",
                (split_type,),
                _scalar(
                    conn,
                    "SELECT count(*) FROM split_pair_assignment WHERE split_id=%s "
                    "AND stratum IS NULL",
                    (split_id,),
                ),
                0,
                "Every temporal pair carries new/recurrent so strata stay separate.",
            )
        )

    if split_type == "cold_protein":
        identity = float((meta[3] or {}).get("identity", 0.40))
        checks.append(
            Check(
                "no sequence cluster spans scored partitions",
                (split_type,),
                _scalar(
                    conn,
                    """
                    SELECT count(*) FROM (
                        SELECT c.cluster_id FROM split_pair_assignment p
                        JOIN target_cluster c ON c.target_id = p.target_id
                        WHERE p.split_id = %s AND c.method = 'mmseqs2-cluster'
                          AND c.threshold = %s::numeric
                          AND p.partition IN ('train','validation','test')
                        GROUP BY c.cluster_id HAVING count(DISTINCT p.partition) > 1
                    ) x
                    """,
                    (split_id, identity),
                ),
                0,
                "A homolog in training makes a held-out target not cold.",
            )
        )
        checks.append(
            Check(
                "every scored target has a cluster assignment",
                (split_type,),
                _scalar(
                    conn,
                    """
                    SELECT count(DISTINCT p.target_id) FROM split_pair_assignment p
                    WHERE p.split_id = %s AND p.partition IN ('train','validation','test')
                      AND NOT EXISTS (
                          SELECT 1 FROM target_cluster c
                          WHERE c.target_id = p.target_id
                            AND c.method = 'mmseqs2-cluster' AND c.threshold = %s::numeric
                      )
                    """,
                    (split_id, identity),
                ),
                0,
                "An unclustered target cannot be shown disjoint from training.",
            )
        )

    if split_type == "chemistry_disjoint":
        checks.append(
            Check(
                "no scaffold spans scored partitions",
                (split_type,),
                _scalar(
                    conn,
                    """
                    SELECT count(*) FROM (
                        SELECT c.cluster_id FROM split_pair_assignment p
                        JOIN compound_cluster c ON c.compound_id = p.compound_id
                        WHERE p.split_id = %s AND c.method = 'bemis-murcko'
                          AND p.partition IN ('train','validation','test')
                        GROUP BY c.cluster_id HAVING count(DISTINCT p.partition) > 1
                    ) x
                    """,
                    (split_id,),
                ),
                0,
                "An analog in training makes held-out chemistry not disjoint.",
            )
        )
        checks.append(
            Check(
                "every scored compound has a scaffold assignment",
                (split_type,),
                _scalar(
                    conn,
                    """
                    SELECT count(DISTINCT p.compound_id) FROM split_pair_assignment p
                    WHERE p.split_id = %s AND p.partition IN ('train','validation','test')
                      AND NOT EXISTS (
                          SELECT 1 FROM compound_cluster c
                          WHERE c.compound_id = p.compound_id AND c.method = 'bemis-murcko'
                      )
                    """,
                    (split_id,),
                ),
                0,
                "An unclustered compound cannot be shown disjoint from training.",
            )
        )

    if split_type == "label_reversal":
        checks.append(
            Check(
                "diagnostic split declares it has no tuning set",
                (split_type,),
                _scalar(
                    conn,
                    "SELECT count(*) FROM split_version WHERE id=%s "
                    "AND protocol <> 'diagnostic_frozen'",
                    (split_id,),
                ),
                0,
                "Its test pairs are the whole point; using them to tune would "
                "destroy the property being measured.",
            )
        )
        checks.append(
            Check(
                "reversed compounds carry opposite labels across the cut",
                (split_type,),
                _scalar(
                    conn,
                    """
                    SELECT count(*) FROM (
                        SELECT p.compound_id FROM split_pair_assignment p
                        JOIN pair_label l ON l.compound_id = p.compound_id
                          AND l.target_id = p.target_id AND l.endpoint_id = %s
                        WHERE p.split_id = %s AND p.partition IN ('train','test')
                        GROUP BY p.compound_id
                        HAVING count(DISTINCT p.partition) > 1
                           AND count(DISTINCT l.label) = 1
                    ) x
                    """,
                    (int(meta[2]), split_id),
                ),
                0,
                "A compound split across train/test with one label is not reversed.",
            )
        )

    if split_type == "temporal_proxy":
        # Eligibility is judged by the partition's own endpoint. Consulting the
        # global one would reimport a decision made with knowledge of later
        # measurements -- the same leak the partition endpoints exist to close.
        checks.append(
            Check(
                "no pair its own partition excluded reaches validation or test",
                (split_type,),
                _scalar(
                    conn,
                    """
                    SELECT count(*) FROM split_pair_assignment p
                    JOIN split_partition_endpoint spe
                      ON spe.split_id = p.split_id AND spe.partition = p.partition
                    JOIN pair_label l ON l.endpoint_id = spe.endpoint_id
                      AND l.compound_id = p.compound_id AND l.target_id = p.target_id
                    WHERE p.split_id = %s AND l.excluded_from_eval
                      AND p.partition IN ('validation','test')
                    """,
                    (split_id,),
                ),
                0,
                "Contradictory and discordant pairs cannot arbitrate a score.",
            )
        )
        checks.append(
            Check(
                "global endpoint exclusions do not gate the temporal training set",
                (split_type,),
                0,
                0,
                "Asserted by construction: the builder consults only the partition "
                "endpoint. Recorded here so the choice is visible in the audit.",
            )
        )
    else:
        checks.append(
            Check(
                "no pair excluded by the endpoint reaches validation or test",
                (split_type,),
                _scalar(
                    conn,
                    """
                    SELECT count(*) FROM split_pair_assignment p
                    JOIN pair_label l ON l.compound_id = p.compound_id
                      AND l.target_id = p.target_id AND l.endpoint_id = %s
                    WHERE p.split_id = %s AND l.excluded_from_eval
                      AND p.partition IN ('validation','test')
                    """,
                    (int(meta[2]), split_id),
                ),
                0,
                "Contradictory and discordant pairs cannot arbitrate a score.",
            )
        )

    checks.append(
        Check(
            "every assignment names a known partition",
            (split_type,),
            _scalar(
                conn,
                "SELECT count(*) FROM split_pair_assignment WHERE split_id=%s "
                "AND partition NOT IN ('train','validation','test','excluded')",
                (split_id,),
            ),
            0,
            "",
        )
    )
    return checks


def assert_no_leakage(conn: psycopg.Connection, split_id: int) -> list[Check]:
    checks = run_checks(conn, split_id)
    failed = [c for c in checks if not c.passed]
    if failed:
        lines = [f"split {split_id} failed {len(failed)} leakage check(s):"]
        lines += [
            f"  - {c.name}: observed {c.observed:,}, expected {c.expected}. {c.detail}"
            for c in failed
        ]
        raise LeakageError("\n".join(lines))
    return checks


def training_visible_activities(conn: psycopg.Connection, split_id: int) -> str:
    """SQL naming the activities a model may see. The only sanctioned source.

    Retrieval indexes and model-facing assay-spread features must be built from
    this and nothing else. Returned as SQL rather than rows so callers cannot
    quietly widen it.
    """
    meta = conn.execute("SELECT split_type FROM split_version WHERE id=%s", (split_id,)).fetchone()
    if meta is None:
        raise LookupError(f"split {split_id} does not exist")
    if str(meta[0]) == "temporal_proxy":
        # Activities of the training period, minus those belonging to pairs the
        # *training-period endpoint* judged unusable on its own evidence.
        #
        # Deliberately NOT filtered on `split_pair_assignment.partition =
        # 'excluded'`. A pair lands in that partition when the validation or test
        # endpoint finds a conflict among *its* measurements, which are dated
        # after the training cut. Suppressing a clean training activity on that
        # basis would let a future measurement decide what the model may learn
        # from the past -- the same leak, running backwards. Training eligibility
        # is decided by the training period alone.
        return (
            "SELECT s.activity_id FROM split_activity_assignment s "
            "JOIN activity a ON a.id = s.activity_id "
            f"WHERE s.split_id = {int(split_id)} AND s.partition = 'train' "
            "AND NOT EXISTS ("
            "  SELECT 1 FROM split_partition_endpoint spe "
            "  JOIN pair_label l ON l.endpoint_id = spe.endpoint_id "
            f"  WHERE spe.split_id = {int(split_id)} AND spe.partition = 'train' "
            "    AND l.compound_id = a.compound_id AND l.target_id = a.target_id "
            "    AND l.excluded_from_eval"
            ")"
        )
    return (
        "SELECT sup.activity_id FROM split_pair_assignment p "
        "JOIN pair_label l ON l.compound_id = p.compound_id AND l.target_id = p.target_id "
        "JOIN pair_label_support sup ON sup.pair_id = l.id "
        f"WHERE p.split_id = {int(split_id)} AND p.partition = 'train' "
        "AND l.endpoint_id = (SELECT endpoint_id FROM split_version "
        f"WHERE id = {int(split_id)})"
    )


def assert_index_is_training_only(
    conn: psycopg.Connection, split_id: int, activity_ids: list[int]
) -> None:
    """Refuse a retrieval index containing any held-out activity."""
    if not activity_ids:
        return
    leaked = _scalar(
        conn,
        f"SELECT count(*) FROM unnest(%s::bigint[]) AS a(id) "  # noqa: S608
        f"WHERE a.id NOT IN ({training_visible_activities(conn, split_id)})",
        (activity_ids,),
    )
    if leaked:
        raise LeakageError(
            f"{leaked:,} of {len(activity_ids):,} activities offered to a retrieval index "
            f"for split {split_id} are not in its training partition. An index built from "
            "held-out evidence leaks it into every prediction that consults it."
        )
