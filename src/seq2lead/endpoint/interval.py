"""pKi constraints and their intersection.

Every Ki record is a *constraint* on pKi, not a value. An exact record pins a
point; a censored record bounds a half-line. Combining evidence for a pair means
intersecting those constraints — never averaging censoring thresholds, which
would invent a measurement nobody made.

**The direction flips.** ``pKi = 9 - log10(Ki_nM)`` is monotonically *decreasing*
in Ki, so a lower bound on Ki is an upper bound on pKi:

===============  ==========================  ==============================
Ki record        constraint on Ki            constraint on pKi
===============  ==========================  ==============================
``= v``          ``Ki == v``                 ``pKi == p``            (point)
``< v``          ``Ki < v``                  ``pKi > p``   (lower, exclusive)
``<= v``         ``Ki <= v``                 ``pKi >= p``  (lower, inclusive)
``> v``          ``Ki > v``                  ``pKi < p``   (upper, exclusive)
``>= v``         ``Ki >= v``                 ``pKi <= p``  (upper, inclusive)
===============  ==========================  ==============================

where ``p = 9 - log10(v)``. ``~`` and ``?`` carry no usable constraint.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

#: Relations that pin a value, and relations that bound one.
EXACT_RELATIONS = ("=",)
CENSORED_RELATIONS = ("<", "<=", ">", ">=")

ACTIVE = "active"
INACTIVE = "inactive"
AMBIGUOUS = "ambiguous"


def is_usable_magnitude(value_nm: float | None) -> bool:
    """A magnitude that can enter a logarithm.

    Zero is the case that actually occurs: BindingDB carries values like
    ``0.000`` and ``>0.000``. ``log10(0)`` is ``-inf``, and ``Ki > 0`` is
    vacuously true of every compound, so such records carry no information and
    must not reach either the regression or the interval logic.
    """
    return value_nm is not None and math.isfinite(value_nm) and value_nm > 0.0


def pki_from_nm(value_nm: float) -> float:
    """``9 - log10(Ki in nM)``. 1000 nM is exactly pKi 6.0."""
    return 9.0 - math.log10(value_nm)


@dataclass(frozen=True)
class Interval:
    """A closed/open interval on pKi. ``None`` means unbounded on that side."""

    lo: float | None = None
    lo_inclusive: bool = False
    hi: float | None = None
    hi_inclusive: bool = False

    @property
    def is_unbounded(self) -> bool:
        return self.lo is None and self.hi is None

    @property
    def is_empty(self) -> bool:
        """True when no value satisfies the constraints."""
        if self.lo is None or self.hi is None:
            return False
        if self.lo > self.hi:
            return True
        # Equal endpoints survive only when both sides admit them.
        return self.lo == self.hi and not (self.lo_inclusive and self.hi_inclusive)

    def contains(self, value: float) -> bool:
        if self.lo is not None:
            if value < self.lo or (value == self.lo and not self.lo_inclusive):
                return False
        if self.hi is not None:
            if value > self.hi or (value == self.hi and not self.hi_inclusive):
                return False
        return True

    def intersect(self, other: Interval) -> Interval:
        """Tightest interval satisfying both. Exclusive wins a tie."""
        lo, lo_inc = self.lo, self.lo_inclusive
        if other.lo is not None and (lo is None or other.lo > lo):
            lo, lo_inc = other.lo, other.lo_inclusive
        elif other.lo is not None and other.lo == lo:
            lo_inc = lo_inc and other.lo_inclusive

        hi, hi_inc = self.hi, self.hi_inclusive
        if other.hi is not None and (hi is None or other.hi < hi):
            hi, hi_inc = other.hi, other.hi_inclusive
        elif other.hi is not None and other.hi == hi:
            hi_inc = hi_inc and other.hi_inclusive

        return Interval(lo, lo_inc, hi, hi_inc)


def constraint(relation: str, value_nm: float | None) -> Interval | None:
    """The pKi constraint a single Ki record imposes, or None if it imposes none."""
    if not is_usable_magnitude(value_nm):
        return None
    p = pki_from_nm(float(value_nm))
    if relation == "=":
        return Interval(p, True, p, True)
    if relation == "<":
        return Interval(lo=p, lo_inclusive=False)
    if relation == "<=":
        return Interval(lo=p, lo_inclusive=True)
    if relation == ">":
        return Interval(hi=p, hi_inclusive=False)
    if relation == ">=":
        return Interval(hi=p, hi_inclusive=True)
    return None  # '~' and '?' bound nothing


def classify(interval: Interval, threshold: float) -> str:
    """Read an interval against ``pKi >= threshold``.

    Decisively **active** when the whole interval satisfies it, which holds
    whenever ``lo >= threshold`` — inclusivity is irrelevant there, since
    ``pKi > threshold`` implies ``pKi >= threshold``.

    Decisively **inactive** when the whole interval fails it. That needs
    ``hi < threshold``, *or* ``hi == threshold`` with the endpoint excluded. An
    inclusive upper bound at exactly the threshold admits the threshold itself,
    which is active — so it cannot decide.
    """
    if interval.is_empty:
        return AMBIGUOUS
    if interval.lo is not None and interval.lo >= threshold:
        return ACTIVE
    if interval.hi is not None and (
        interval.hi < threshold or (interval.hi == threshold and not interval.hi_inclusive)
    ):
        return INACTIVE
    return AMBIGUOUS


def intersect_all(intervals: list[Interval]) -> Interval:
    result = Interval()
    for item in intervals:
        result = result.intersect(item)
    return result
