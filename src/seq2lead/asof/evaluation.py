"""The three readings of "what B says", implemented on the real endpoint logic.

`docs/M11.md` §5.2a separates them; this module is where that separation becomes
code rather than prose, because the §5.2-versus-example-(e) contradiction arose
from having it only in prose.

* **increment label** -- from the newly added observations alone. Forms the label.
* **full-B status** -- over all of B's evidence, A included. Audit, and the input
  to the screen.
* **consistency screen** -- drops pairs whose full-B status is contradictory.
  A declared filter, reported both ways.

Interval arithmetic and the threshold reading come from
`seq2lead.endpoint.interval`, not from a reimplementation here, so the censoring
branches and the inclusive/exclusive boundary behave exactly as M4 does. Nothing
in this module fits a model.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from seq2lead.endpoint.build import (
    DEFAULT_DISCORDANCE_PKI,
    STATUS_CONFLICT,
    STATUS_EMPTY,
    STATUS_NO_EVIDENCE,
    exclusion_reason,
)
from seq2lead.endpoint.interval import (
    ACTIVE,
    AMBIGUOUS,
    INACTIVE,
    Interval,
    classify,
    constraint,
    intersect_all,
)

STATUS_OK = "ok"


class Branch(StrEnum):
    PRIMARY = "primary_screened"
    SENSITIVITY = "sensitivity_unscreened"


@dataclass(frozen=True)
class Measurement:
    """One observation, in the terms the endpoint logic needs."""

    relation: str
    value_nm: float | None
    is_exact: bool = False

    def as_interval(self):
        return constraint(self.relation, self.value_nm)


@dataclass
class PairEvidence:
    """A pair's evidence, in **three** separate representations.

    Earlier versions held two and derived the third, and both derivations were
    wrong in a way the other reading hid:

    * deriving the increment as a multiset difference of `Measurement` compares
      on relation and value only, so an observation that **moved to a different
      publication** cancelled against the one it replaced -- the matcher reported
      one addition and one removal, and the harness saw neither;
    * withholding a correction candidate from `in_b` kept it out of the increment
      *and* out of actual B, so the full-B audit read `no_usable_evidence` for a
      pair B plainly had evidence for.

    So the three are held independently, and each reading uses exactly one:

    | Reading | Field |
    | --- | --- |
    | training | `in_a` -- historical A evidence |
    | consistency screen / audit | `in_b` -- **complete** actual B evidence |
    | increment label | `increment` -- **eligible** new evidence only |

    `increment` is supplied by the bridge from the matcher's counted additions,
    minus any occurrence attributed to a correction. It is not recomputed here.
    """

    pair: str
    in_a: list[Measurement] = field(default_factory=list)
    in_b: list[Measurement] = field(default_factory=list)
    increment: list[Measurement] | None = None
    removals: list[Measurement] | None = None

    @classmethod
    def from_delta(
        cls,
        pair: str,
        in_a: list[Measurement],
        added: list[Measurement] | None = None,
        removed: list[Measurement] | None = None,
    ) -> PairEvidence:
        """Build from A plus an explicit delta. For fixtures and hand-traced cases.

        Sets `increment` to `added` explicitly rather than leaving it to be
        derived, so even a hand-built fixture goes through the same three-way
        representation the bridge uses.
        """
        gone = list(removed or [])
        surviving = list(in_a)
        for m in gone:
            if m in surviving:
                surviving.remove(m)
        additions = list(added or [])
        return cls(
            pair=pair,
            in_a=list(in_a),
            in_b=[*surviving, *additions],
            increment=additions,
            removals=gone,
        )

    @property
    def eligible_increment(self) -> list[Measurement]:
        """The evidence the increment label is read from.

        Falls back to the multiset difference only when no explicit increment was
        supplied, and that fallback is exactly the computation that loses a
        publication move -- so the bridge always supplies one.
        """
        if self.increment is not None:
            return self.increment
        return self.added

    @property
    def effective_removals(self) -> list[Measurement]:
        """The removals the audit counts.

        Symmetric with `eligible_increment`: supplied explicitly by the bridge
        from the matcher's counted removal rows, because the value-only
        difference below cannot see a removal whose value still appears
        elsewhere in B -- a publication replacement being exactly that case.
        """
        if self.removals is not None:
            return self.removals
        return self.removed

    @property
    def added(self) -> list[Measurement]:
        """Multiset difference B − A, by value. Diagnostic only.

        Deliberately **not** what the increment label reads: two observations
        with the same relation and value but different publications are different
        observations, and this cannot tell them apart.
        """
        remaining = list(self.in_a)
        out: list[Measurement] = []
        for m in self.in_b:
            if m in remaining:
                remaining.remove(m)
            else:
                out.append(m)
        return out

    @property
    def removed(self) -> list[Measurement]:
        """Observations A had that B does not, by value. Diagnostic only."""
        remaining = list(self.in_b)
        out: list[Measurement] = []
        for m in self.in_a:
            if m in remaining:
                remaining.remove(m)
            else:
                out.append(m)
        return out

    @property
    def is_recurrent(self) -> bool:
        return bool(self.in_a)


def _status_and_label(observations: list[Measurement], threshold: float) -> tuple[str, str]:
    """Endpoint status and label for a set of observations.

    Mirrors `seq2lead.endpoint.build._label_row` deliberately: the **censored
    bounds alone** are intersected, and exact values are then tested *against*
    that interval. Intersecting the exacts in too would turn every
    exact-versus-bound contradiction into `empty_intersection` and make
    `exact_bound_conflict` unreachable -- the two mean different things and the
    audit record needs to tell them apart.
    """
    exact_pki: list[float] = []
    censored: list[Any] = []
    for m in observations:
        bound = m.as_interval()
        if bound is None:
            continue
        if m.is_exact:
            if bound.lo is not None:
                exact_pki.append(bound.lo)
        else:
            censored.append(bound)

    if not exact_pki and not censored:
        return (STATUS_NO_EVIDENCE, AMBIGUOUS)

    interval = intersect_all(censored) if censored else Interval()
    if censored and interval.is_empty:
        return (STATUS_EMPTY, AMBIGUOUS)

    if exact_pki and censored:
        outside = [p for p in exact_pki if not interval.contains(p)]
        if outside:
            return (STATUS_CONFLICT, AMBIGUOUS)

    if exact_pki:
        median = statistics.median(exact_pki)
        return (STATUS_OK, ACTIVE if median >= threshold else INACTIVE)

    return (STATUS_OK, classify(interval, threshold))


@dataclass(frozen=True)
class ExactStats:
    """Statistics over a set of exact observations, in pKi. Audit material.

    Retained whether or not the pair is scoreable: a discordant pair is excluded
    from scoring but its spread is exactly the thing worth reporting.
    """

    n: int
    median: float | None
    minimum: float | None
    maximum: float | None

    @property
    def spread(self) -> float | None:
        if self.minimum is None or self.maximum is None:
            return None
        return self.maximum - self.minimum

    def is_discordant(self, discordance: float = DEFAULT_DISCORDANCE_PKI) -> bool:
        """Mirrors M4: a spread strictly greater than the threshold is discordant."""
        spread = self.spread
        return spread is not None and spread > discordance

    def to_dict(self) -> dict[str, Any]:
        return {
            "n_exact": self.n,
            "median_pki": self.median,
            "min_pki": self.minimum,
            "max_pki": self.maximum,
            "spread_pki": self.spread,
        }


def exact_stats(observations: list[Measurement]) -> ExactStats:
    points: list[float] = []
    for m in observations:
        if not m.is_exact:
            continue
        bound = m.as_interval()
        if bound is not None and bound.lo is not None:
            points.append(bound.lo)
    if not points:
        return ExactStats(n=0, median=None, minimum=None, maximum=None)
    return ExactStats(
        n=len(points),
        median=statistics.median(points),
        minimum=min(points),
        maximum=max(points),
    )


def increment_label(evidence: PairEvidence, threshold: float) -> tuple[str, str]:
    """Label from the **eligible increment alone**. This is the as-of label."""
    return _status_and_label(evidence.eligible_increment, threshold)


def full_b_status(evidence: PairEvidence, threshold: float) -> tuple[str, str]:
    """Status over snapshot B's **actual** evidence. Audit, and the screen's input.

    Reads `in_b`, not `in_a + added`. If B withdrew one of A's observations, that
    observation is not part of B and must not contribute to B's status.
    """
    return _status_and_label(evidence.in_b, threshold)


def training_label(evidence: PairEvidence, threshold: float) -> tuple[str, str]:
    """Label from snapshot A alone. Never a function of B."""
    return _status_and_label(evidence.in_a, threshold)


#: Full-B statuses that the primary protocol screens out. Declared, not inferred
#: at call time, so the screen cannot quietly widen.
SCREENED_STATUSES = frozenset({STATUS_CONFLICT, STATUS_EMPTY})


@dataclass
class PairOutcome:
    """A pair's labels, its statuses, and -- separately -- its scoring eligibility.

    The distinction is load-bearing. A discordant pair *has* a label and an `ok`
    status; what it lacks is the right to be scored, because replicate exacts
    four logs apart cannot arbitrate a ranking. Collapsing the two would either
    discard a usable label or score a pair on evidence that disagrees with itself.
    """

    pair: str
    increment_status: str
    increment_label: str
    full_b_status: str
    full_b_label: str
    training_status: str
    training_label: str
    in_primary: bool
    in_sensitivity: bool
    screened_because: str | None
    stratum: str
    # eligibility, kept apart from label/status
    eligibility_reason: str | None = None
    increment_exact: ExactStats | None = None
    full_b_exact: ExactStats | None = None
    training_exact: ExactStats | None = None
    n_added: int = 0
    n_removed: int = 0

    @property
    def is_scoreable(self) -> bool:
        return self.eligibility_reason is None

    def to_dict(self) -> dict[str, Any]:
        return {
            "pair": self.pair,
            "increment_status": self.increment_status,
            "increment_label": self.increment_label,
            "full_b_status": self.full_b_status,
            "full_b_label": self.full_b_label,
            "training_status": self.training_status,
            "training_label": self.training_label,
            "in_primary": self.in_primary,
            "in_sensitivity": self.in_sensitivity,
            "screened_because": self.screened_because,
            "stratum": self.stratum,
            "eligibility_reason": self.eligibility_reason,
            "is_scoreable": self.is_scoreable,
            "n_added": self.n_added,
            "n_removed": self.n_removed,
            "increment_exact": (self.increment_exact.to_dict() if self.increment_exact else None),
            "full_b_exact": self.full_b_exact.to_dict() if self.full_b_exact else None,
            "training_exact": self.training_exact.to_dict() if self.training_exact else None,
        }


def evaluate_pair(
    evidence: PairEvidence,
    threshold: float,
    discordance: float = DEFAULT_DISCORDANCE_PKI,
) -> PairOutcome:
    """Apply all three readings, then M4's eligibility rules. No model is involved."""
    inc_status, inc_label = increment_label(evidence, threshold)
    b_status, b_label = full_b_status(evidence, threshold)
    tr_status, tr_label = training_label(evidence, threshold)

    inc_exact = exact_stats(evidence.eligible_increment)
    b_exact = exact_stats(evidence.in_b)
    tr_exact = exact_stats(evidence.in_a)

    # Scoring eligibility, via M4's own precedence: contradiction outranks
    # discordance, because contradictory evidence leaves no label at all while
    # discordant evidence has one that simply cannot arbitrate a ranking.
    reason = exclusion_reason(
        inc_status if inc_status != STATUS_OK else None,
        "ambiguous_label" if inc_label == AMBIGUOUS else None,
        "discordant" if inc_exact.is_discordant(discordance) else None,
    )

    scoreable = reason is None
    screened = b_status in SCREENED_STATUSES
    return PairOutcome(
        pair=evidence.pair,
        increment_status=inc_status,
        increment_label=inc_label,
        full_b_status=b_status,
        full_b_label=b_label,
        training_status=tr_status,
        training_label=tr_label,
        in_primary=scoreable and not screened,
        in_sensitivity=scoreable,
        screened_because=b_status if (scoreable and screened) else None,
        stratum="recurrent" if evidence.is_recurrent else "new_pair",
        eligibility_reason=reason,
        increment_exact=inc_exact,
        full_b_exact=b_exact,
        training_exact=tr_exact,
        n_added=len(evidence.eligible_increment),
        n_removed=len(evidence.effective_removals),
    )


def evaluate(
    pairs: list[PairEvidence],
    threshold: float,
    discordance: float = DEFAULT_DISCORDANCE_PKI,
) -> dict[str, Any]:
    """Both branches plus the audit record, so neither can be chosen after the fact."""
    outcomes = [
        evaluate_pair(p, threshold, discordance) for p in sorted(pairs, key=lambda p: p.pair)
    ]
    by_reason: dict[str, int] = {}
    for o in outcomes:
        if o.eligibility_reason:
            by_reason[o.eligibility_reason] = by_reason.get(o.eligibility_reason, 0) + 1
    return {
        "threshold": threshold,
        "discordance_pki": discordance,
        "screened_statuses": sorted(SCREENED_STATUSES),
        Branch.PRIMARY: sorted(o.pair for o in outcomes if o.in_primary),
        Branch.SENSITIVITY: sorted(o.pair for o in outcomes if o.in_sensitivity),
        "screened_out": sorted(o.pair for o in outcomes if o.screened_because),
        "ineligible": sorted(o.pair for o in outcomes if not o.is_scoreable),
        "ineligible_by_reason": dict(sorted(by_reason.items())),
        # every pair is reported, scoreable or not: exclusion from scoring is not
        # exclusion from the record
        "outcomes": [o.to_dict() for o in outcomes],
    }


# ------------------------------------------- training / validation artifacts

#: Partitions whose evidence may enter a fitted artifact or a retrieval index
#: under `train_only`. Validation is reserved and is NOT one of them.
FITTABLE_PARTITIONS = frozenset({"train"})


class LeakageError(RuntimeError):
    """An artifact was about to be built from evidence it may not see."""


@dataclass(frozen=True)
class ActivityRow:
    activity_id: int
    partition: str  # train | validation | b_increment


def select_for_fitting(rows: list[ActivityRow], artifact: str) -> list[int]:
    """Which activity ids may enter a fitted artifact or a retrieval index.

    This is the selection the assertions of `docs/M11.md` §5.3a protect. Testing
    a digest function that takes one argument shows the *signature* is safe; it
    does not show that the artifact built downstream excluded validation
    evidence. This is the function that decides that, so this is what the tests
    drive.
    """
    allowed = [r.activity_id for r in rows if r.partition in FITTABLE_PARTITIONS]
    leaked = sorted(
        r.activity_id
        for r in rows
        if r.partition not in FITTABLE_PARTITIONS and r.activity_id in set(allowed)
    )
    if leaked:
        raise LeakageError(
            f"{artifact}: activity ids {leaked[:10]} appear in both a fittable and a "
            "reserved partition"
        )
    return sorted(allowed)


def assert_no_reserved_evidence(
    selected: list[int], rows: list[ActivityRow], artifact: str
) -> None:
    """Refuse an artifact that carries validation or evaluation evidence."""
    reserved = {r.activity_id: r.partition for r in rows if r.partition not in FITTABLE_PARTITIONS}
    offenders = sorted(i for i in selected if i in reserved)
    if offenders:
        detail = ", ".join(f"{i} ({reserved[i]})" for i in offenders[:10])
        raise LeakageError(
            f"{artifact} would be built from reserved evidence: {detail}. Under "
            "`train_only` neither validation nor the B increment may reach a fitted "
            "artifact or a retrieval index."
        )
