"""The rows every compared model sees, and nothing else.

A model scoring a different set of rows is not comparable to one that does not,
so the cohort is assembled once per (split, partition) and handed to every model
in the comparison. It is also where the partition-endpoint rule lives: for
`temporal_proxy`, labels and eligibility come from the partition's own endpoint,
never from the global aggregate, which summarises a pair's whole measurement
history and would grade a test pair partly against its own future.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    import psycopg

    from seq2lead.eval.config import ExperimentConfig, SplitRef

PARTITIONS = ("train", "validation", "test")


@dataclass
class Cohort:
    """Eligible pairs of one partition, with their labels and provenance."""

    split: str
    partition: str
    endpoint_id: int
    compound_id: np.ndarray
    target_id: np.ndarray
    y: np.ndarray  # exact-relation median pKi
    label: np.ndarray  # active / inactive / none, for ranking
    stratum: np.ndarray  # new / recurrent, temporal only
    n_before_exclusions: int = 0
    n_unusable_fingerprint: int = 0
    n_missing_regression: int = 0

    def __len__(self) -> int:
        return int(self.compound_id.shape[0])

    @property
    def n_targets(self) -> int:
        return int(np.unique(self.target_id).shape[0])


def unusable_compounds(conn: psycopg.Connection, cache_name: str) -> list[int]:
    """Compounds whose fingerprint is an all-zero row because the structure failed.

    Excluded from the shared primary cohort rather than imputed: a zero vector is
    not a missing value, and a model reads it as a real molecule with no features.
    """
    return [
        int(r[0])
        for r in conn.execute(
            "SELECT f.entity_id FROM feature_entity_flag f "
            "JOIN feature_version v ON v.id = f.feature_id "
            "WHERE v.name = %s AND f.flag = 'unparseable_smiles'",
            (cache_name,),
        ).fetchall()
    ]


def endpoint_for(config: ExperimentConfig, split: SplitRef, partition: str) -> int:
    """Which endpoint governs this partition's labels.

    For a temporal split that is the partition's own endpoint. Using the global
    one would import a judgement formed from measurements dated after the cut.
    """
    if split.partition_endpoints:
        try:
            return split.partition_endpoints[partition]
        except KeyError as exc:
            raise KeyError(
                f"split {split.name!r} declares partition endpoints but none for "
                f"{partition!r}; refusing to fall back to the global endpoint"
            ) from exc
    return config.endpoint_id


def build_cohort(
    conn: psycopg.Connection,
    config: ExperimentConfig,
    split_name: str,
    partition: str,
    require_regression_label: bool = True,
) -> Cohort:
    """Assemble one partition's eligible rows under the experiment's cohort rules."""
    split = config.split(split_name)
    endpoint_id = endpoint_for(config, split, partition)
    excluded = (
        unusable_compounds(conn, config.caches["ecfp4"].name)
        if config.cohort.get("exclude_unusable_fingerprints", True)
        else []
    )

    total = conn.execute(
        "SELECT count(*) FROM split_pair_assignment WHERE split_id=%s AND partition=%s",
        (split.id, partition),
    ).fetchone()[0]
    n_unusable = conn.execute(
        "SELECT count(*) FROM split_pair_assignment WHERE split_id=%s AND partition=%s "
        "AND compound_id = ANY(%s)",
        (split.id, partition, excluded),
    ).fetchone()[0]

    rows = conn.execute(
        """
        SELECT p.compound_id, p.target_id, r.p_median, l.label, p.stratum
        FROM split_pair_assignment p
        LEFT JOIN pair_regression r
          ON r.endpoint_id = %(endpoint)s AND r.compound_id = p.compound_id
         AND r.target_id = p.target_id AND NOT r.excluded_from_eval
        LEFT JOIN pair_label l
          ON l.endpoint_id = %(endpoint)s AND l.compound_id = p.compound_id
         AND l.target_id = p.target_id AND NOT l.excluded_from_eval
        WHERE p.split_id = %(split)s AND p.partition = %(partition)s
          AND NOT (p.compound_id = ANY(%(excluded)s))
        ORDER BY p.target_id, p.compound_id
        """,
        {
            "endpoint": endpoint_id,
            "split": split.id,
            "partition": partition,
            "excluded": excluded,
        },
    ).fetchall()

    keep = [r for r in rows if (r[2] is not None or not require_regression_label)]
    missing_regression = len(rows) - len(keep)
    if not keep:
        empty_i = np.zeros(0, dtype=np.int64)
        return Cohort(
            split=split_name,
            partition=partition,
            endpoint_id=endpoint_id,
            compound_id=empty_i,
            target_id=empty_i,
            y=np.zeros(0, dtype=np.float64),
            label=np.array([], dtype=object),
            stratum=np.array([], dtype=object),
            n_before_exclusions=int(total),
            n_unusable_fingerprint=int(n_unusable),
            n_missing_regression=missing_regression,
        )

    return Cohort(
        split=split_name,
        partition=partition,
        endpoint_id=endpoint_id,
        compound_id=np.array([int(r[0]) for r in keep], dtype=np.int64),
        target_id=np.array([int(r[1]) for r in keep], dtype=np.int64),
        y=np.array([float(r[2]) if r[2] is not None else np.nan for r in keep]),
        label=np.array([r[3] for r in keep], dtype=object),
        stratum=np.array([r[4] for r in keep], dtype=object),
        n_before_exclusions=int(total),
        n_unusable_fingerprint=int(n_unusable),
        n_missing_regression=missing_regression,
    )


def ranking_cohort(
    conn: psycopg.Connection, config: ExperimentConfig, split_name: str, partition: str
) -> Cohort:
    """Every eligible *measured* pair, including decisive censored labels.

    Ranking asks whether a compound is active against a target, and a decisive
    censored bound answers that even though it supplies no regression target.
    """
    cohort = build_cohort(conn, config, split_name, partition, require_regression_label=False)
    usable = np.array([lab in ("active", "inactive") for lab in cohort.label])
    if not usable.any():
        return cohort
    return Cohort(
        split=cohort.split,
        partition=cohort.partition,
        endpoint_id=cohort.endpoint_id,
        compound_id=cohort.compound_id[usable],
        target_id=cohort.target_id[usable],
        y=cohort.y[usable],
        label=cohort.label[usable],
        stratum=cohort.stratum[usable],
        n_before_exclusions=cohort.n_before_exclusions,
        n_unusable_fingerprint=cohort.n_unusable_fingerprint,
        n_missing_regression=cohort.n_missing_regression,
    )
