"""Feed the matcher's classified observations into the evaluation harness.

The harness used to be handed `PairEvidence` built by hand. In a real run the
increment has to come from the **matcher**, because only the matcher knows which
additions are genuinely new and which are the second half of a correction. A
harness that recomputed differences from relation and value alone would have no
way to tell those apart, and would silently promote every corrected value into a
new evaluation measurement.

So this module is the only place where matcher output becomes harness input, and
it carries the provenance across rather than discarding it: counts, the slot
(publication, pH, temperature, curation source), both identifiers, and a pointer
back to the source rows.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

from seq2lead.asof.evaluation import Measurement, PairEvidence
from seq2lead.asof.matching import (
    DiffReport,
    Observation,
    diff_snapshots,
    observation_of,
)

#: A pair is a (compound, target) identity. The slot is finer -- it also fixes
#: publication and assay context -- so one pair owns many slots.
PairKey = tuple[str, str]


def pair_key(observation: Observation) -> PairKey:
    return (observation.slot.inchikey, observation.slot.sequence_sha256)


def _value_nm(observation: Observation) -> float | None:
    """The canonical numeric value, in the declared unit (nM), or None."""
    try:
        return float(Decimal(observation.value.value_text))
    except (InvalidOperation, ValueError):
        return None


def to_measurement(observation: Observation) -> Measurement | None:
    """One matched observation as the endpoint logic's `Measurement`.

    Returns None when the value could not be normalised, so an unparseable row
    is dropped explicitly and counted rather than silently becoming a 0.
    """
    value_nm = _value_nm(observation)
    if value_nm is None:
        return None
    relation = observation.value.relation or "="
    return Measurement(
        relation=relation,
        value_nm=value_nm,
        is_exact=relation == "=",
    )


@dataclass
class IncrementProvenance:
    """Why a pair's increment looks the way it does, kept beside the evidence."""

    pair: PairKey
    slots: list[str] = field(default_factory=list)
    entry_dois: list[str] = field(default_factory=list)
    reactant_set_ids: list[str] = field(default_factory=list)
    n_added_observations: int = 0
    n_unresolved_additions: int = 0
    n_correction_candidates_withheld: int = 0
    n_identifier_conflicts: int = 0
    n_removed_observations: int = 0
    n_unparseable_dropped: int = 0
    # row-level traceability: which source rows the increment rests on, and
    # which were withheld. Aggregated identifier lists do not establish this.
    source_locators: list[str] = field(default_factory=list)
    increment_locators: list[str] = field(default_factory=list)
    withheld_locators: list[str] = field(default_factory=list)
    removed_locators: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "pair": list(self.pair),
            "slots": sorted(set(self.slots)),
            "entry_dois": sorted({d for d in self.entry_dois if d}),
            "reactant_set_ids": sorted({i for i in self.reactant_set_ids if i}),
            "n_added_observations": self.n_added_observations,
            "n_unresolved_additions": self.n_unresolved_additions,
            "n_correction_candidates_withheld": self.n_correction_candidates_withheld,
            "n_identifier_conflicts": self.n_identifier_conflicts,
            "n_removed_observations": self.n_removed_observations,
            "n_unparseable_dropped": self.n_unparseable_dropped,
            "source_locators": sorted({x for x in self.source_locators if x}),
            "increment_locators": sorted({x for x in self.increment_locators if x}),
            "withheld_locators": sorted({x for x in self.withheld_locators if x}),
            "removed_locators": sorted({x for x in self.removed_locators if x}),
        }


@dataclass
class BridgedIncrement:
    evidence: PairEvidence
    provenance: IncrementProvenance


def _collect(observations: list[Observation], prov: IncrementProvenance) -> list[Measurement]:
    """Convert observations to measurements, accumulating provenance as we go.

    Takes `prov` explicitly rather than closing over it: a closure over a loop
    variable is the kind of thing that works until someone defers the call.
    """
    kept: list[Measurement] = []
    for obs in observations:
        m = to_measurement(obs)
        if m is None:
            prov.n_unparseable_dropped += 1
            continue
        kept.append(m)
        prov.slots.append(obs.slot.serialised())
        prov.entry_dois.append(obs.entry_doi or "")
        prov.reactant_set_ids.append(obs.reactant_set_id or "")
        if obs.source is not None:
            prov.source_locators.append(obs.source.locator())
    return kept


def _withheld_counts(report: DiffReport) -> Counter[tuple[str, str]]:
    """How many occurrences at each (slot, value) a correction accounted for.

    **Counts, not keys.** An earlier version collected the keys and filtered out
    every row matching one, so an unchanged observation that happened to share a
    correction's after-value disappeared along with it. A correction accounts for
    exactly one occurrence, so exactly one is withheld.

    Withholding applies to the **eligible increment only**. The after-value stays
    in actual B, because B genuinely contains it and the full-B audit and the
    consistency screen have to read B as it stands.
    """
    return Counter(
        (candidate.slot.serialised(), candidate.after.serialised())
        for candidate in report.correction_candidates
    )


def build_increments(
    earlier: list[dict[str, Any]],
    later: list[dict[str, Any]],
    *,
    report: DiffReport | None = None,
) -> tuple[list[BridgedIncrement], DiffReport]:
    """Classify A against B, then build each pair's three representations.

    The eligible increment is assembled from the matcher's **counted addition
    rows**, not by subtracting value-only `Measurement`s. That distinction is the
    whole point of going through the matcher: two observations with the same
    relation and value but different publications are different observations, and
    a value-level subtraction cancels them against each other.
    """
    diff = report if report is not None else diff_snapshots(earlier, later)
    withheld = _withheld_counts(diff)
    unresolved = set(diff.unresolved_additions)

    conflicts_by_pair: dict[PairKey, int] = {}
    for conflict in diff.identifier_conflicts:
        key = (conflict.slot.inchikey, conflict.slot.sequence_sha256)
        conflicts_by_pair[key] = conflicts_by_pair.get(key, 0) + 1

    a_obs = [observation_of(r) for r in earlier]
    b_obs = [observation_of(r) for r in later]

    # The matcher's own surplus rows, grouped by pair. These are the additions,
    # with their slots and provenance intact.
    additions_by_pair: dict[PairKey, list[Observation]] = defaultdict(list)
    for obs in diff.addition_rows:
        additions_by_pair[pair_key(obs)].append(obs)
    removals_by_pair: dict[PairKey, list[Observation]] = defaultdict(list)
    for obs in diff.removal_rows:
        removals_by_pair[pair_key(obs)].append(obs)

    pairs: dict[PairKey, dict[str, list[Observation]]] = {}
    for obs in a_obs:
        pairs.setdefault(pair_key(obs), {"a": [], "b": []})["a"].append(obs)
    for obs in b_obs:
        pairs.setdefault(pair_key(obs), {"a": [], "b": []})["b"].append(obs)

    out: list[BridgedIncrement] = []
    for key in sorted(pairs):
        side = pairs[key]
        prov = IncrementProvenance(pair=key)
        prov.n_identifier_conflicts = conflicts_by_pair.get(key, 0)

        in_a = _collect(side["a"], prov)
        # Complete actual B. Nothing is withheld here -- withholding belongs to
        # the eligible increment, and B is what B contains.
        in_b = _collect(side["b"], prov)

        # The eligible increment, from the matcher's counted additions, minus one
        # occurrence per correction candidate.
        budget = Counter(withheld)
        increment: list[Measurement] = []
        for obs in sorted(additions_by_pair.get(key, []), key=lambda o: o.sort_key()):
            slot_value = (obs.slot.serialised(), obs.value.serialised())
            if budget[slot_value] > 0:
                budget[slot_value] -= 1
                prov.n_correction_candidates_withheld += 1
                prov.withheld_locators.append(obs.locator() or "")
                continue
            m = to_measurement(obs)
            if m is None:
                prov.n_unparseable_dropped += 1
                continue
            increment.append(m)
            prov.increment_locators.append(obs.locator() or "")
            if slot_value in unresolved:
                prov.n_unresolved_additions += 1

        # the matcher's counted removals, carried explicitly so the exported
        # outcome agrees with the matcher rather than with a value-only subtraction
        removal_obs = sorted(removals_by_pair.get(key, []), key=lambda o: o.sort_key())
        removals: list[Measurement] = []
        for obs in removal_obs:
            m = to_measurement(obs)
            if m is None:
                prov.n_unparseable_dropped += 1
                continue
            removals.append(m)
            prov.removed_locators.append(obs.locator() or "")

        evidence = PairEvidence(
            pair=f"{key[0]}|{key[1]}",
            in_a=in_a,
            in_b=in_b,
            increment=increment,
            removals=removals,
        )
        prov.n_added_observations = len(increment)
        prov.n_removed_observations = len(removals)
        out.append(BridgedIncrement(evidence=evidence, provenance=prov))
    return (out, diff)
