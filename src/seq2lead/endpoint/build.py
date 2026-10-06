"""Build the Ki endpoint tables from `activity`.

Two outputs, deliberately separate:

* **`pair_regression`** — exact, positive, finite `=` records only. A censoring
  threshold has no place in a median: "Ki > 10000" is not a measurement of 10000.
* **`pair_label`** — exact points *and* censored bounds, combined by interval
  intersection on pKi. Where the intersection is empty, or where the exact
  evidence falls outside the censored bounds, the pair gets **no benchmark
  label** and the contradiction is recorded instead of being averaged away.

Scope is Ki alone. IC50, Kd and EC50 stay in `activity`, unmerged.

No partition is assigned here. `eval_exclusion_reason` records why a pair *would*
be held out of validation and test; choosing partitions is M6.
"""

from __future__ import annotations

import statistics
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from seq2lead.endpoint.interval import (
    ACTIVE,
    AMBIGUOUS,
    CENSORED_RELATIONS,
    INACTIVE,
    Interval,
    classify,
    constraint,
    is_usable_magnitude,
    pki_from_nm,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

    import psycopg

# v2: an exact observation outside the censored interval makes the pair
#     contradictory, regardless of where the median sits; discordance propagates
#     to the label's evaluation eligibility; explicit excluded_from_eval flags.
BUILDER_VERSION = "m4/v2"
MEASUREMENT_TYPE = "KI"

#: Predeclared provisional threshold, fixed before any split exists and before any
#: model is trained. Sensitivity versions are regenerated, not substituted.
DEFAULT_THRESHOLD_PKI = 6.0
SENSITIVITY_THRESHOLDS = (6.0, 7.0, 8.0)

#: Exact pairs spanning more than this many pKi units are flagged discordant.
DEFAULT_DISCORDANCE_PKI = 1.0

STATUS_OK = "ok"
STATUS_EMPTY = "empty_intersection"
STATUS_CONFLICT = "exact_bound_conflict"
STATUS_NO_EVIDENCE = "no_usable_evidence"

#: Why a pair is kept out of validation and test, strongest reason first.
#: Contradiction outranks discordance: contradictory evidence cannot be
#: reconciled at all, whereas discordant evidence exists but disagrees. A pair
#: that is both is reported as contradictory, because there is no label to score
#: it against in the first place.
EXCLUSION_PRECEDENCE = (
    STATUS_CONFLICT,
    STATUS_EMPTY,
    STATUS_NO_EVIDENCE,
    "discordant",
    "ambiguous_label",
)


def exclusion_reason(*candidates: str | None) -> str | None:
    """Pick the strongest applicable reason, or None if the pair is usable."""
    present = {c for c in candidates if c}
    for reason in EXCLUSION_PRECEDENCE:
        if reason in present:
            return reason
    return next(iter(present), None)


_PAIR_BATCH = 5_000


@dataclass
class BuildCounters:
    activities_in: int = 0
    unusable_magnitude: int = 0
    no_constraint: int = 0
    pairs: int = 0
    regression_pairs: int = 0
    label_pairs: int = 0
    discordant: int = 0
    regression_excluded: int = 0
    label_excluded: int = 0
    by_exclusion: dict[str, int] = field(default_factory=dict)
    by_status: dict[str, int] = field(default_factory=dict)
    by_label: dict[str, int] = field(default_factory=dict)
    by_evidence: dict[str, int] = field(default_factory=dict)
    seconds: float = 0.0

    def bump(self, bucket: dict[str, int], key: str) -> None:
        bucket[key] = bucket.get(key, 0) + 1


@dataclass
class PairEvidence:
    compound_id: int
    target_id: int
    exact: list[tuple[int, float]] = field(default_factory=list)
    censored: list[tuple[int, Interval]] = field(default_factory=list)
    unusable: list[int] = field(default_factory=list)
    no_constraint: list[int] = field(default_factory=list)
    assays: set[int] = field(default_factory=set)
    publications: set[int] = field(default_factory=set)
    all_in_scope: bool = True


def _iter_pairs(
    conn: psycopg.Connection, release_id: int, activity_filter: str | None = None
) -> Iterator[PairEvidence]:
    """Stream Ki activities grouped by (compound, target), bounded in memory.

    `activity_filter` restricts the source to a subset of activity ids, given as
    SQL. A temporal partition passes its own assigned activities so that its
    aggregates summarise that period and nothing else.
    """
    restriction = f" AND id IN ({activity_filter})" if activity_filter else ""
    sql = (
        "SELECT compound_id, target_id, id, relation, value_numeric, assay_id, "
        "publication_id, in_benchmark_scope FROM activity "
        "WHERE source_release_id = %s AND measurement_type = %s"
        f"{restriction} "
        "ORDER BY compound_id, target_id"
    )
    current: PairEvidence | None = None
    with conn.cursor(name="ki_stream") as cur:
        cur.itersize = 50_000
        cur.execute(sql, (release_id, MEASUREMENT_TYPE))
        for compound_id, target_id, activity_id, relation, value, assay_id, pub_id, scope in cur:
            if current is None or (compound_id, target_id) != (
                current.compound_id,
                current.target_id,
            ):
                if current is not None:
                    yield current
                current = PairEvidence(compound_id=compound_id, target_id=target_id)

            if assay_id is not None:
                current.assays.add(assay_id)
            if pub_id is not None:
                current.publications.add(pub_id)
            if not scope:
                current.all_in_scope = False

            if not is_usable_magnitude(value):
                current.unusable.append(activity_id)
            elif relation not in ("=", *CENSORED_RELATIONS):
                # '~' and '?' have a usable magnitude but bound nothing.
                current.no_constraint.append(activity_id)
            elif relation == "=":
                current.exact.append((activity_id, pki_from_nm(float(value))))
            else:
                bound = constraint(relation, float(value))
                if bound is None:  # pragma: no cover - guarded above
                    current.no_constraint.append(activity_id)
                else:
                    current.censored.append((activity_id, bound))
    if current is not None:
        yield current


def _regression_row(pair: PairEvidence, discordance: float, label_status: str) -> tuple | None:
    """Statistics survive even when the pair is unusable, for audit and for M5."""
    if not pair.exact:
        return None
    values = sorted(p for _, p in pair.exact)
    median = statistics.median(values)
    mad = statistics.median([abs(v - median) for v in values])
    spread = values[-1] - values[0]
    discordant = spread > discordance
    reason = exclusion_reason(
        label_status if label_status != STATUS_OK else None,
        "discordant" if discordant else None,
    )
    return (
        len(values),
        median,
        values[0],
        values[-1],
        mad,
        spread,
        discordant,
        len(pair.assays),
        len(pair.publications),
        pair.all_in_scope,
        reason is not None,
        reason,
    )


def _is_discordant(pair: PairEvidence, discordance: float) -> bool:
    if len(pair.exact) < 2:
        return False
    values = [p for _, p in pair.exact]
    return (max(values) - min(values)) > discordance


def _label_row(pair: PairEvidence, threshold: float, discordant: bool) -> tuple:
    """Resolve one pair's evidence into a label, or refuse to."""
    interval = Interval()
    for _, bound in pair.censored:
        interval = interval.intersect(bound)

    n_exact, n_censored = len(pair.exact), len(pair.censored)
    exact_median = statistics.median([p for _, p in pair.exact]) if pair.exact else None
    outside = (
        sum(1 for _, p in pair.exact if not interval.contains(p))
        if pair.exact and pair.censored
        else 0
    )

    if not pair.exact and not pair.censored:
        label, status, evidence = "none", STATUS_NO_EVIDENCE, "none"
    elif pair.censored and interval.is_empty:
        # Bounds that cannot all hold at once. Averaging them would manufacture a
        # value no measurement supports.
        label, status, evidence = "none", STATUS_EMPTY, "censored"
    elif outside > 0:
        # ANY exact observation outside the consistent censored interval makes the
        # pair contradictory. Testing only the median was wrong: exacts of pKi 5
        # and pKi 9 under a bound of pKi > 6 have a median of 7, which sits inside
        # the interval, while the value of 5 flatly contradicts the bound.
        label, status, evidence = "none", STATUS_CONFLICT, "both"
    elif pair.exact:
        label = ACTIVE if exact_median >= threshold else INACTIVE
        status = STATUS_OK
        evidence = "both" if pair.censored else "exact"
    else:
        label = classify(interval, threshold)
        status, evidence = STATUS_OK, "censored"

    reason = exclusion_reason(
        status if status != STATUS_OK else None,
        "ambiguous_label" if label == AMBIGUOUS else None,
        # Discordant replicates do not invalidate a label, but they must not
        # arbitrate one either. Training use stays open; validation and test do not.
        "discordant" if discordant else None,
    )

    return (
        label,
        status,
        evidence,
        interval.lo,
        interval.lo_inclusive if interval.lo is not None else None,
        interval.hi,
        interval.hi_inclusive if interval.hi is not None else None,
        exact_median,
        n_exact,
        n_censored,
        outside,
        pair.all_in_scope,
        reason is not None,
        reason,
    )


def _next_ids(conn: psycopg.Connection, sequence: str, count: int) -> list[int]:
    if count == 0:
        return []
    rows = conn.execute(
        "SELECT nextval(%s) FROM generate_series(1, %s)", (sequence, count)
    ).fetchall()
    return [int(r[0]) for r in rows]


def build_endpoint(
    conn: psycopg.Connection,
    release_id: int,
    *,
    name: str,
    threshold: float = DEFAULT_THRESHOLD_PKI,
    discordance: float = DEFAULT_DISCORDANCE_PKI,
    curator_version: str,
    activity_filter: str | None = None,
    notes: str = "",
    progress=None,
) -> tuple[int, BuildCounters]:
    """Build one versioned endpoint. Re-running the same name is refused."""
    started = time.perf_counter()
    counters = BuildCounters()

    existing = conn.execute("SELECT id FROM endpoint_version WHERE name = %s", (name,)).fetchone()
    if existing is not None:
        raise ValueError(
            f"endpoint '{name}' already exists as id {existing[0]}. Endpoint versions are "
            "immutable; build a new name or drop the old one deliberately."
        )

    row = conn.execute(
        "INSERT INTO endpoint_version (name, measurement_type, threshold_pki, "
        "discordance_pki, source_release_id, curator_version, builder_version) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id",
        (
            name,
            MEASUREMENT_TYPE,
            threshold,
            discordance,
            release_id,
            curator_version,
            BUILDER_VERSION,
        ),
    ).fetchone()
    assert row is not None
    endpoint_id = int(row[0])

    reg_rows: list[tuple] = []
    reg_support: list[tuple] = []
    lab_rows: list[tuple] = []
    lab_support: list[tuple] = []

    def flush() -> None:
        if reg_rows:
            with (
                conn.cursor() as cur,
                cur.copy(
                    "COPY pair_regression (id, endpoint_id, compound_id, target_id, n_obs, "
                    "p_median, p_min, p_max, p_mad, p_spread, is_discordant, n_assays, "
                    "n_publications, in_benchmark_scope, excluded_from_eval, "
                    "eval_exclusion_reason) FROM STDIN"
                ) as cp,
            ):
                for record in reg_rows:
                    cp.write_row(record)
            with (
                conn.cursor() as cur,
                cur.copy("COPY pair_regression_support (pair_id, activity_id) FROM STDIN") as cp,
            ):
                for record in reg_support:
                    cp.write_row(record)
        if lab_rows:
            with (
                conn.cursor() as cur,
                cur.copy(
                    "COPY pair_label (id, endpoint_id, compound_id, target_id, label, status, "
                    "evidence, lo_pki, lo_inclusive, hi_pki, hi_inclusive, exact_median_pki, "
                    "n_exact, n_censored, n_exact_outside_bounds, in_benchmark_scope, "
                    "excluded_from_eval, eval_exclusion_reason) FROM STDIN"
                ) as cp,
            ):
                for record in lab_rows:
                    cp.write_row(record)
            with (
                conn.cursor() as cur,
                cur.copy("COPY pair_label_support (pair_id, activity_id, role) FROM STDIN") as cp,
            ):
                for record in lab_support:
                    cp.write_row(record)
        reg_rows.clear()
        reg_support.clear()
        lab_rows.clear()
        lab_support.clear()

    pending_reg: list[tuple[PairEvidence, tuple]] = []
    pending_lab: list[tuple[PairEvidence, tuple]] = []

    def assign() -> None:
        for ids, pending, rows, support, seq in (
            (None, pending_reg, reg_rows, reg_support, "pair_regression_id_seq"),
            (None, pending_lab, lab_rows, lab_support, "pair_label_id_seq"),
        ):
            del ids
            allocated = _next_ids(conn, seq, len(pending))
            for pair_id, (pair, payload) in zip(allocated, pending, strict=True):
                rows.append((pair_id, endpoint_id, pair.compound_id, pair.target_id, *payload))
                if seq == "pair_regression_id_seq":
                    support.extend((pair_id, aid) for aid, _ in pair.exact)
                else:
                    support.extend((pair_id, aid, "exact") for aid, _ in pair.exact)
                    support.extend((pair_id, aid, "censored") for aid, _ in pair.censored)
        pending_reg.clear()
        pending_lab.clear()

    for pair in _iter_pairs(conn, release_id, activity_filter):
        counters.pairs += 1
        counters.activities_in += (
            len(pair.exact) + len(pair.censored) + len(pair.unusable) + len(pair.no_constraint)
        )
        counters.unusable_magnitude += len(pair.unusable)
        counters.no_constraint += len(pair.no_constraint)

        discordant = _is_discordant(pair, discordance)
        label = _label_row(pair, threshold, discordant)

        regression = _regression_row(pair, discordance, label[1])
        if regression is not None:
            counters.regression_pairs += 1
            if regression[6]:
                counters.discordant += 1
            if regression[11]:
                counters.regression_excluded += 1
            pending_reg.append((pair, regression))

        counters.label_pairs += 1
        counters.bump(counters.by_label, label[0])
        counters.bump(counters.by_status, label[1])
        counters.bump(counters.by_evidence, label[2])
        if label[12]:
            counters.label_excluded += 1
            counters.bump(counters.by_exclusion, label[13])
        pending_lab.append((pair, label))

        if len(pending_lab) >= _PAIR_BATCH:
            assign()
            flush()
            if progress is not None:
                progress(counters)

    assign()
    flush()

    conn.execute(
        "UPDATE endpoint_version SET n_activities_in=%s, n_unusable_magnitude=%s, "
        "n_no_constraint=%s WHERE id=%s",
        (counters.activities_in, counters.unusable_magnitude, counters.no_constraint, endpoint_id),
    )
    counters.seconds = time.perf_counter() - started
    return endpoint_id, counters
