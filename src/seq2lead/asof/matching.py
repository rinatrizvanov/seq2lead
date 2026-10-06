"""Deterministic multiset matching between two snapshots.

Why multisets: a slot legitimately carries repeated identical rows, and the
files do not say whether those are replicate experiments, double ingestion, or
distinct conditions we do not key on. So counts are compared and the
interpretation is left open -- see `docs/M11.md` §3.4.

Why deterministic: pairing equal-valued rows must not depend on file order or on
what order the database returned them, or two runs over the same inputs would
classify differently. Everything is sorted into a canonical order first.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from seq2lead.asof.normalise import (
    DECLARED_UNITS,
    NORMALISATION_VERSION,
    normalise_number,
    normalise_text,
)

__all_units__ = DECLARED_UNITS


class Classification(StrEnum):
    UNCHANGED = "unchanged"
    ADDITION = "addition"
    REMOVAL = "removal"
    NEW_SLOT = "new_slot"
    REMOVED_SLOT = "removed_slot"
    CORRECTION_CANDIDATE = "correction_candidate"
    UNRESOLVED_ADDITION = "unresolved_addition"
    UNRESOLVED_REMOVAL = "unresolved_removal"


def _text(value: Any) -> str:
    return normalise_text(value)


def _publication_ref(pmid: Any, doi: Any, patent: Any) -> str:
    """Normalised external publication reference: PMID, else DOI, else patent.

    Deliberately not our local `publication.id`, which is a surrogate assigned at
    ingest and carries no meaning across releases.
    """
    if pmid not in (None, "", 0):
        return f"pmid:{_text(pmid)}"
    if doi not in (None, ""):
        return f"doi:{_text(doi)}"
    if patent not in (None, ""):
        return f"patent:{_text(patent)}"
    return "unattributed"


@dataclass(frozen=True, order=True)
class SourceRef:
    """Where one observation came from: a release, and a row within it.

    Provenance, not identity. Two rows from different releases can be the same
    observation, so this is excluded from matching equality and from the grouping
    keys -- folding it in would make every row unique and the counted multiset
    comparison meaningless. It serves as a deterministic sort tiebreaker and
    appears in the exported audit detail.
    """

    source_release: str
    raw_row: str

    def locator(self) -> str:
        return f"{self.source_release}#{self.raw_row}"


@dataclass(frozen=True, order=True)
class Slot:
    """What a measurement is about. Stable under correction.

    `ph` and `temp_c` hold **canonical** numeric keys so that `7.4` and `7.40`
    are one slot; `ph_original` and `temp_c_original` keep what the file said and
    are excluded from ordering and equality.
    """

    inchikey: str
    sequence_sha256: str
    publication_ref: str
    ph: str
    temp_c: str
    curation_source: str
    ph_original: str = field(default="", compare=False)
    temp_c_original: str = field(default="", compare=False)

    def serialised(self) -> str:
        return "\t".join(
            (
                self.inchikey,
                self.sequence_sha256,
                self.publication_ref,
                self.ph,
                self.temp_c,
                self.curation_source,
            )
        )


@dataclass(frozen=True, order=True)
class Value:
    """What was reported. Exactly what a correction changes.

    `value_text` is the **canonical** numeric key, in the declared unit (nM).
    `value_original` is the file's own spelling, kept for reporting and excluded
    from equality -- otherwise `12` and `12.0` would be different values and
    every such pair would look like a correction.
    """

    measurement_type: str
    relation: str
    value_text: str
    value_original: str = field(default="", compare=False)
    value_note: str | None = field(default=None, compare=False)

    def serialised(self) -> str:
        return "\t".join((self.measurement_type, self.relation, self.value_text))


@dataclass(frozen=True)
class Observation:
    """One row, with its release-stable identifiers kept beside it.

    `entry_doi` is BindingDB's per-entry DOI (`BindingDB Entry DOI` in the source
    TSV, e.g. `10.7270/Q2ZW1J3M`). It is **entry-level provenance**: an externally
    minted identifier for the curated document-level entry a row came from.

    Two things it is **not**, and earlier wording implied both:

    * it is **not** an individual-measurement identity. One entry covers many
      measurements, so rows sharing an entry DOI come from the same curated entry
      and nothing stronger. Agreement is necessary for a correction link, not
      sufficient -- which is why a link is asserted only when exactly one removal
      and one addition sit at a single slot.
    * its **cross-release stability is unmeasured**. External registration gives
      it a *reason* to be stable; we have not compared two releases, so we have
      not observed it.

    `reactant_set_id` is a local surrogate, consulted only when the entry DOIs
    cannot decide, and recorded so agreement or disagreement can be counted.
    """

    slot: Slot
    value: Value
    entry_doi: str | None = None
    reactant_set_id: str | None = None
    source: SourceRef | None = None

    def row_digest(self) -> str:
        """A stable tiebreaker for canonical ordering within a tied group.

        `source` is **not** folded in: two indistinguishable observations must
        keep the same digest whatever rows they came from.
        """
        body = "\x00".join(
            (
                self.slot.serialised(),
                self.value.serialised(),
                self.entry_doi or "",
                self.reactant_set_id or "",
            )
        )
        return hashlib.sha256(body.encode("utf-8")).hexdigest()

    def sort_key(self) -> tuple[str, str]:
        """Digest first, then the source locator, so ordering is total."""
        return (self.row_digest(), self.source.locator() if self.source else "")

    def locator(self) -> str | None:
        return self.source.locator() if self.source else None


def slot_of(row: dict[str, Any]) -> Slot:
    ph = normalise_number(row.get("ph_text"), "ph")
    temp = normalise_number(row.get("temp_c_text"), "temp_c")
    return Slot(
        inchikey=_text(row.get("inchikey")),
        sequence_sha256=_text(row.get("sequence_sha256")),
        publication_ref=_publication_ref(row.get("pmid"), row.get("doi"), row.get("patent_number")),
        ph=ph.key(),
        temp_c=temp.key(),
        curation_source=_text(row.get("curation_source")),
        ph_original=ph.original,
        temp_c_original=temp.original,
    )


def normalise_value(row: dict[str, Any]) -> Value:
    number = normalise_number(row.get("value_text"), "value")
    return Value(
        measurement_type=_text(row.get("measurement_type")),
        relation=_text(row.get("relation")),
        value_text=number.key(),
        value_original=number.original,
        value_note=number.note,
    )


def observation_of(row: dict[str, Any]) -> Observation:
    release = row.get("source_release")
    raw_row = row.get("raw_row", row.get("raw_measurement_id", row.get("line_no")))
    source = (
        SourceRef(source_release=str(release), raw_row=str(raw_row))
        if release is not None and raw_row is not None
        else None
    )
    return Observation(
        slot=slot_of(row),
        value=normalise_value(row),
        entry_doi=_text(row.get("entry_doi")) or None,
        reactant_set_id=_text(row.get("reactant_set_id")) or None,
        source=source,
    )


@dataclass
class CorrectionCandidate:
    """A removal and an addition at one slot that *might* be the same measurement.

    Provisional by construction: it is asserted only on a release-stable
    identifier match, and whether `reactant_set_id` is release-stable is still
    unestablished (`docs/M11.md` §9).
    """

    slot: Slot
    before: Value
    after: Value
    linked_by: str  # entry_doi | reactant_set_id
    identifier: str
    provisional: bool = True


@dataclass
class DiffReport:
    """Everything the diff found, with unresolved items kept separable."""

    unchanged: Counter[tuple[str, str]] = field(default_factory=Counter)
    additions: Counter[tuple[str, str]] = field(default_factory=Counter)
    removals: Counter[tuple[str, str]] = field(default_factory=Counter)
    new_slots: set[str] = field(default_factory=set)
    removed_slots: set[str] = field(default_factory=set)
    correction_candidates: list[CorrectionCandidate] = field(default_factory=list)
    unresolved_additions: Counter[tuple[str, str]] = field(default_factory=Counter)
    unresolved_removals: Counter[tuple[str, str]] = field(default_factory=Counter)
    ambiguous_slots: set[str] = field(default_factory=set)
    identifier_conflicts: list[IdentifierConflict] = field(default_factory=list)
    # the rows themselves, so an auditor can see provenance rather than a total
    addition_rows: list[Observation] = field(default_factory=list)
    removal_rows: list[Observation] = field(default_factory=list)
    unresolved_addition_rows: list[Observation] = field(default_factory=list)
    unresolved_removal_rows: list[Observation] = field(default_factory=list)
    normalisation_version: str = NORMALISATION_VERSION
    is_single_snapshot_proxy: bool = False
    proxy_note: str = ""

    def summary(self) -> dict[str, Any]:
        return {
            "normalisation_version": self.normalisation_version,
            "is_single_snapshot_proxy": self.is_single_snapshot_proxy,
            "proxy_note": self.proxy_note,
            "unchanged_observations": sum(self.unchanged.values()),
            "added_observations": sum(self.additions.values()),
            "removed_observations": sum(self.removals.values()),
            "new_slots": len(self.new_slots),
            "removed_slots": len(self.removed_slots),
            "correction_candidates": len(self.correction_candidates),
            "unresolved_additions": sum(self.unresolved_additions.values()),
            "unresolved_removals": sum(self.unresolved_removals.values()),
            "ambiguous_slots": len(self.ambiguous_slots),
            "identifier_conflicts": len(self.identifier_conflicts),
        }

    @staticmethod
    def _rows(observations: list[Observation]) -> list[dict[str, Any]]:
        """Counted, sorted, provenance-bearing detail for a set of rows.

        Counts alone do not make a change "separately identifiable": an auditor
        needs the slot, both spellings of the value, and the identifiers that a
        candidate link would have rested on. Grouped and sorted so two runs over
        the same inputs emit byte-identical output.
        """
        grouped: dict[tuple[str, ...], dict[str, Any]] = {}
        for obs in observations:
            key = (
                obs.slot.serialised(),
                obs.value.serialised(),
                obs.entry_doi or "",
                obs.reactant_set_id or "",
            )
            entry = grouped.setdefault(
                key,
                {
                    "slot": obs.slot.serialised(),
                    "value": obs.value.serialised(),
                    "value_original": obs.value.value_original,
                    "value_note": obs.value.value_note,
                    "ph_original": obs.slot.ph_original,
                    "temp_c_original": obs.slot.temp_c_original,
                    "entry_doi": obs.entry_doi,
                    "reactant_set_id": obs.reactant_set_id,
                    "count": 0,
                    "source_locators": [],
                },
            )
            entry["count"] += 1
            if obs.source is not None:
                entry["source_locators"].append(obs.source.locator())
        for entry in grouped.values():
            entry["source_locators"] = sorted(entry["source_locators"])
        return [grouped[k] for k in sorted(grouped)]

    def to_json(self) -> str:
        body = self.summary()
        body["declared_units"] = dict(DECLARED_UNITS)
        body["correction_candidates_detail"] = [
            {
                "slot": c.slot.serialised(),
                "before": c.before.serialised(),
                "before_original": c.before.value_original,
                "after": c.after.serialised(),
                "after_original": c.after.value_original,
                "linked_by": c.linked_by,
                "identifier": c.identifier,
                "provisional": c.provisional,
            }
            for c in sorted(
                self.correction_candidates,
                key=lambda c: (c.slot.serialised(), c.before.serialised(), c.after.serialised()),
            )
        ]
        body["identifier_conflicts_detail"] = [
            {
                "slot": c.slot.serialised(),
                "before": c.before.serialised(),
                "after": c.after.serialised(),
                "before_entry_doi": c.before_entry_doi,
                "after_entry_doi": c.after_entry_doi,
                "before_reactant_set_id": c.before_reactant_set_id,
                "after_reactant_set_id": c.after_reactant_set_id,
                "surrogate_agreed": c.surrogate_agreed,
                "note": (
                    "entry DOIs disagree, so no link was made. The surrogate agreed, "
                    "and was deliberately not allowed to decide."
                    if c.surrogate_agreed
                    else "entry DOIs disagree and the surrogate did not agree either."
                ),
            }
            for c in sorted(
                self.identifier_conflicts,
                key=lambda c: (c.slot.serialised(), c.before_entry_doi, c.after_entry_doi),
            )
        ]
        body["additions_detail"] = self._rows(self.addition_rows)
        body["removals_detail"] = self._rows(self.removal_rows)
        body["unresolved_additions_detail"] = self._rows(self.unresolved_addition_rows)
        body["unresolved_removals_detail"] = self._rows(self.unresolved_removal_rows)
        body["ambiguous_slots_detail"] = sorted(self.ambiguous_slots)
        return json.dumps(body, indent=2, sort_keys=True) + "\n"


def _group(rows: list[dict[str, Any]]) -> dict[Slot, dict[Value, list[Observation]]]:
    grouped: dict[Slot, dict[Value, list[Observation]]] = defaultdict(lambda: defaultdict(list))
    for row in rows:
        obs = observation_of(row)
        grouped[obs.slot][obs.value].append(obs)
    # canonical order inside every tied group, so pairing never depends on input order
    for values in grouped.values():
        for bucket in values.values():
            bucket.sort(key=lambda o: o.sort_key())
    return grouped


@dataclass
class IdentifierConflict:
    """Both rows carry an entry DOI and the two disagree.

    Not a link, and not a plain absence of evidence either: it is positive
    evidence *against* the two rows being the same measurement. Recorded on its
    own so it can be counted rather than disappearing into the unresolved pile.
    """

    slot: Slot
    before: Value
    after: Value
    before_entry_doi: str
    after_entry_doi: str
    before_reactant_set_id: str | None
    after_reactant_set_id: str | None
    surrogate_agreed: bool


def _link(
    removed: list[Observation], added: list[Observation]
) -> tuple[CorrectionCandidate | None, IdentifierConflict | None]:
    """Link one removal to one addition, or report why not.

    **Entry DOI decides when both sides have one.** If the two disagree there is
    no link, and in particular no fallback to `reactant_set_id`: an earlier
    version looped over both identifiers in turn, so two rows with *different*
    entry DOIs but a shared surrogate were linked anyway. A shared local
    surrogate cannot outvote two externally minted identifiers that disagree.

    The surrogate is consulted only when the entry DOIs cannot decide, because at
    least one side lacks one.
    """
    if len(removed) != 1 or len(added) != 1:
        return (None, None)  # ambiguous many-to-many: never linked
    before, after = removed[0], added[0]

    if before.entry_doi and after.entry_doi:
        if before.entry_doi == after.entry_doi:
            return (
                CorrectionCandidate(
                    slot=before.slot,
                    before=before.value,
                    after=after.value,
                    linked_by="entry_doi",
                    identifier=before.entry_doi,
                    provisional=True,
                ),
                None,
            )
        return (
            None,
            IdentifierConflict(
                slot=before.slot,
                before=before.value,
                after=after.value,
                before_entry_doi=before.entry_doi,
                after_entry_doi=after.entry_doi,
                before_reactant_set_id=before.reactant_set_id,
                after_reactant_set_id=after.reactant_set_id,
                surrogate_agreed=bool(
                    before.reactant_set_id and before.reactant_set_id == after.reactant_set_id
                ),
            ),
        )

    a, b = before.reactant_set_id, after.reactant_set_id
    if a and b and a == b:
        return (
            CorrectionCandidate(
                slot=before.slot,
                before=before.value,
                after=after.value,
                linked_by="reactant_set_id",
                identifier=a,
                provisional=True,
            ),
            None,
        )
    return (None, None)


def diff_snapshots(
    earlier: list[dict[str, Any]],
    later: list[dict[str, Any]],
    *,
    is_single_snapshot_proxy: bool = False,
    proxy_note: str = "",
) -> DiffReport:
    """Classify the difference between two row sets. Deterministic.

    `is_single_snapshot_proxy` must be set when the two sides come from one
    snapshot cut by date. Such an exercise can produce additions and new slots
    but **cannot** produce corrections or withdrawals, because a single snapshot
    holds no revisions -- so a clean correction count from it means nothing and
    the flag travels with the report to say so.
    """
    report = DiffReport(is_single_snapshot_proxy=is_single_snapshot_proxy, proxy_note=proxy_note)
    a_groups, b_groups = _group(earlier), _group(later)

    for slot in sorted(set(a_groups) | set(b_groups)):
        in_a, in_b = slot in a_groups, slot in b_groups
        a_values = a_groups.get(slot, {})
        b_values = b_groups.get(slot, {})
        if not in_a:
            report.new_slots.add(slot.serialised())
        if not in_b:
            report.removed_slots.add(slot.serialised())

        surplus_removed: list[Observation] = []
        surplus_added: list[Observation] = []
        for value in sorted(set(a_values) | set(b_values)):
            na, nb = len(a_values.get(value, [])), len(b_values.get(value, []))
            key = (slot.serialised(), value.serialised())
            if min(na, nb):
                report.unchanged[key] += min(na, nb)
            if nb > na:
                report.additions[key] += nb - na
                extra = b_values[value][na:]
                surplus_added.extend(extra)
                report.addition_rows.extend(extra)
            elif na > nb:
                report.removals[key] += na - nb
                gone = a_values[value][nb:]
                surplus_removed.extend(gone)
                report.removal_rows.extend(gone)

        # a correction is inferred only within a slot that exists on both sides
        if surplus_removed and surplus_added and in_a and in_b:
            candidate, conflict = _link(surplus_removed, surplus_added)
            if candidate is not None:
                report.correction_candidates.append(candidate)
                continue
            if conflict is not None:
                report.identifier_conflicts.append(conflict)
            else:
                report.ambiguous_slots.add(slot.serialised())
        for obs in surplus_removed:
            report.unresolved_removals[(slot.serialised(), obs.value.serialised())] += 1
            report.unresolved_removal_rows.append(obs)
        for obs in surplus_added:
            report.unresolved_additions[(slot.serialised(), obs.value.serialised())] += 1
            report.unresolved_addition_rows.append(obs)
    return report


def training_population(earlier: list[dict[str, Any]]) -> dict[str, Any]:
    """The training population, as a function of the earlier snapshot **alone**.

    Takes one argument on purpose. The as-of claim is that training is what an
    analyst standing at T_train would have had, and the cheapest way to keep that
    true is to make it impossible to pass the later snapshot in.
    """
    grouped = _group(earlier)
    rows = sorted(
        f"{slot.serialised()}\t{value.serialised()}\t{len(bucket)}"
        for slot, values in grouped.items()
        for value, bucket in values.items()
    )
    return {
        "n_slots": len(grouped),
        "n_observations": sum(len(b) for v in grouped.values() for b in v.values()),
        "digest": hashlib.sha256("\n".join(rows).encode("utf-8")).hexdigest(),
        "normalisation_version": NORMALISATION_VERSION,
    }
