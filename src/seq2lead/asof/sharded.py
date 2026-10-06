"""Bounded-memory snapshot matching, by slot-hash sharding.

`diff_snapshots` takes both sides as in-memory lists. Measured on the real
exports that is 15.9 GB for the row dicts alone and 25.3 GB once they are
`Observation` objects, against 16 GiB of physical memory -- so the corpus cannot
be matched in one call (`docs/M11.md` §10b).

**This is a decomposition of that function, not a second implementation of it.**
Every shard pair is handed to the same `diff_snapshots`, so the matching rules,
the counted-multiset pairing, the correction-link policy and the numeric
normalisation are not restated here and cannot drift from it.

Why sharding by slot is sound rather than merely convenient:

* Every decision `diff_snapshots` makes is scoped to one slot. `_group` keys on
  `Slot`; value multiplicities are compared within a slot; `_link` draws its one
  removal and one addition from a single slot's surplus; `new_slots` and
  `removed_slots` are per-slot by definition. No rule reads across slots.
* Every `DiffReport` field composes. The counters add, the slot sets union, the
  detail lists concatenate -- and because each slot lands in exactly one shard,
  the per-shard slot sets are **disjoint**, so union degenerates to addition and
  no cross-shard de-duplication is needed.

The one invariant correctness depends on: **every observation sharing a slot
lands in the same shard.** The shard key is a cryptographic hash of the slot's
canonical serialisation, which is exactly the string `_group` keys on, so two
observations with the same slot cannot be separated -- including when they spell
`7.4` and `7.40`, because `Slot.serialised()` emits the canonical numeric key and
excludes the `compare=False` original spellings.

**Not `hash()`.** Python randomises `hash()` of a string per process unless
`PYTHONHASHSEED` is set, so a shard assignment built on it would differ between
runs and the partition would not be reproducible from the recorded configuration.
BLAKE2b is used instead: stable across processes, platforms and versions.
"""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import resource
import time
from collections import Counter
from contextlib import ExitStack
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from seq2lead.asof.matching import diff_snapshots, slot_of
from seq2lead.asof.normalise import NORMALISATION_VERSION

if TYPE_CHECKING:
    from collections.abc import Iterator

    from seq2lead.asof.matching import DiffReport

#: Bumped when the sharding or the streamed output format changes. The matching
#: rules' own version is `NORMALISATION_VERSION`, which belongs to the matcher.
SHARD_VERSION = "m11d/shard/v1"

#: The hash behind the shard assignment, named in the report so a partition can
#: be reproduced exactly.
SHARD_HASH = "blake2b-64"

#: Declared budget: the most observations (both sides combined) one shard pair
#: may hold before it is diffed. At the measured ~3,965 bytes per observation
#: this is roughly 1.6 GB resident, which leaves usable headroom on a 16 GiB
#: machine. A shard over budget is not processed -- the partition is redone with
#: a larger shard count, which moves whole slots and never splits one.
MAX_SHARD_OBSERVATIONS = 400_000


def shard_index(slot_serialised: str, shard_count: int) -> int:
    """Which shard a slot belongs to. Deterministic across processes and runs."""
    digest = hashlib.blake2b(slot_serialised.encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, "big") % shard_count


def peak_rss_bytes() -> int:
    """Peak resident set size of this process, in bytes.

    `ru_maxrss` is bytes on macOS and kilobytes on Linux; normalised here so the
    reported figure means the same thing wherever it was measured.
    """
    raw = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    import sys

    return raw if sys.platform == "darwin" else raw * 1024


@dataclass
class PartitionStats:
    """How one side's rows were distributed. Bounded: one integer per shard."""

    label: str
    source_path: str
    shard_count: int
    rows_read: int = 0
    rows_written: int = 0
    per_shard: list[int] = field(default_factory=list)
    seconds: float = 0.0

    @property
    def reconciles(self) -> bool:
        return self.rows_read == self.rows_written == sum(self.per_shard)

    @property
    def max_shard(self) -> int:
        return max(self.per_shard) if self.per_shard else 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "source_path": self.source_path,
            "shard_count": self.shard_count,
            "rows_read": self.rows_read,
            "rows_written": self.rows_written,
            "reconciles": self.reconciles,
            "max_shard_rows": self.max_shard,
            "min_shard_rows": min(self.per_shard) if self.per_shard else 0,
            "per_shard": list(self.per_shard),
            "seconds": round(self.seconds, 1),
        }


def partition(
    export_path: str | Path,
    out_dir: str | Path,
    shard_count: int,
    label: str,
) -> PartitionStats:
    """Stream one export into `shard_count` shard files, keeping slots together.

    The original JSON line is written through **verbatim**. Re-serialising it
    would make the shard bytes depend on this module's JSON settings rather than
    on the export, and the input accounting would then be comparing our output
    to our output.
    """
    export_path, out_dir = Path(export_path), Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stats = PartitionStats(
        label=label,
        source_path=str(export_path),
        shard_count=shard_count,
        per_shard=[0] * shard_count,
    )
    started = time.time()
    with ExitStack() as stack:
        source = stack.enter_context(gzip.open(export_path, "rt", encoding="utf-8"))
        sinks = [
            stack.enter_context((out_dir / f"{label}-{i:04d}.jsonl").open("w", encoding="utf-8"))
            for i in range(shard_count)
        ]
        for line in source:
            if not line.strip():
                continue
            stats.rows_read += 1
            row = json.loads(line)
            index = shard_index(slot_of(row).serialised(), shard_count)
            sinks[index].write(line if line.endswith("\n") else line + "\n")
            stats.per_shard[index] += 1
            stats.rows_written += 1
    stats.seconds = time.time() - started
    return stats


def _read_shard(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def count_rows(path: Path) -> int:
    """How many rows a shard file holds, without materialising any of them.

    A byte scan for newlines. Used to apply the budget *before* the rows exist in
    memory -- checking afterwards is checking too late, because the allocation
    that would exhaust memory has already happened.
    """
    total = 0
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 22), b""):
            total += chunk.count(b"\n")
    return total


class ShardTooLarge(MemoryError):
    """A shard pair exceeds the declared budget. Repartition; do not raise the budget."""


def _read_shard_bounded(path: Path, limit: int) -> list[dict[str, Any]]:
    """Read at most `limit` rows, then refuse.

    The pre-check uses a counted scan, and this is the belt to its braces: if the
    count were stale -- a file rewritten between the scan and the read -- or
    simply wrong, the read would otherwise sail past the budget. Here it stops at
    the limit and raises, so no incorrect count can bypass the bound.
    """
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            if len(rows) >= limit:
                msg = (
                    f"{path.name} holds more than the {limit:,} rows the remaining budget "
                    f"allows; refusing to read further. Repartition at a larger shard count."
                )
                raise ShardTooLarge(msg)
            rows.append(json.loads(line))
    return rows


def _measurement_type(value_serialised: str) -> str:
    """The type is the first field of `Value.serialised()`."""
    return value_serialised.split("\t", 1)[0]


@dataclass
class TypeCounts:
    """Observation counts by measurement type. The endpoint boundary, preserved.

    All four types are matched for the descriptive diff -- a type-filtered diff
    would misreport a value whose *type* changed between snapshots as a plain
    removal plus a plain addition in two separate runs. Keeping them in one pass
    and separating them in the counts is what lets the reconciliation close while
    the types stay distinct.
    """

    unchanged: Counter[str] = field(default_factory=Counter)
    additions: Counter[str] = field(default_factory=Counter)
    removals: Counter[str] = field(default_factory=Counter)

    def absorb(self, report: DiffReport) -> None:
        for (_slot, value), n in report.unchanged.items():
            self.unchanged[_measurement_type(value)] += n
        for (_slot, value), n in report.additions.items():
            self.additions[_measurement_type(value)] += n
        for (_slot, value), n in report.removals.items():
            self.removals[_measurement_type(value)] += n

    def as_dict(self) -> dict[str, Any]:
        types = sorted(set(self.unchanged) | set(self.additions) | set(self.removals))
        return {
            t: {
                "unchanged": self.unchanged[t],
                "additions": self.additions[t],
                "removals": self.removals[t],
                # A = unchanged + removals and B = unchanged + additions hold per
                # type as well as overall, because both are per-(slot, value).
                "a_observations": self.unchanged[t] + self.removals[t],
                "b_observations": self.unchanged[t] + self.additions[t],
            }
            for t in types
        }


@dataclass
class LinkPopulations:
    """The populations an identifier-agreement figure is allowed to describe.

    `DiffReport.ambiguous_slots` conflates two different situations: a slot whose
    surplus is many-to-many and was never a link candidate, and a slot with
    exactly one surplus removal and one surplus addition where the identifiers
    could not decide. Counting identifier agreement over the union would mix a
    population where agreement was *possible* with one where it was never asked,
    so they are separated here.
    """

    one_to_one_entry_doi_agreed: int = 0
    one_to_one_entry_doi_disagreed: int = 0
    one_to_one_entry_doi_disagreed_surrogate_agreed: int = 0
    one_to_one_surrogate_agreed: int = 0
    one_to_one_undecidable: int = 0
    many_to_many_slots: int = 0

    @property
    def one_to_one_total(self) -> int:
        return (
            self.one_to_one_entry_doi_agreed
            + self.one_to_one_entry_doi_disagreed
            + self.one_to_one_surrogate_agreed
            + self.one_to_one_undecidable
        )

    @property
    def entry_doi_decidable(self) -> int:
        return self.one_to_one_entry_doi_agreed + self.one_to_one_entry_doi_disagreed

    def as_dict(self) -> dict[str, Any]:
        decidable = self.entry_doi_decidable
        return {
            "one_to_one_slots": self.one_to_one_total,
            "many_to_many_slots": self.many_to_many_slots,
            "entry_doi": {
                "both_present": decidable,
                "agreed": self.one_to_one_entry_doi_agreed,
                "disagreed": self.one_to_one_entry_doi_disagreed,
                "disagreed_but_surrogate_agreed": (
                    self.one_to_one_entry_doi_disagreed_surrogate_agreed
                ),
                "agreement_rate_within_both_present": (
                    round(self.one_to_one_entry_doi_agreed / decidable, 6) if decidable else None
                ),
            },
            "reactant_set_id": {
                "consulted_because_entry_doi_could_not_decide": (
                    self.one_to_one_surrogate_agreed + self.one_to_one_undecidable
                ),
                "agreed": self.one_to_one_surrogate_agreed,
                "did_not_agree_or_absent": self.one_to_one_undecidable,
                "agreement_rate_when_consulted": (
                    round(
                        self.one_to_one_surrogate_agreed
                        / (self.one_to_one_surrogate_agreed + self.one_to_one_undecidable),
                        6,
                    )
                    if (self.one_to_one_surrogate_agreed + self.one_to_one_undecidable)
                    else None
                ),
            },
        }


def _classify_ambiguous(report: DiffReport, populations: LinkPopulations) -> None:
    """Split `ambiguous_slots` into one-to-one-undecidable and many-to-many.

    Derived from the unresolved rows the same report already carries, so no
    second matching pass is needed and nothing is recomputed by different rules.
    """
    removals: Counter[str] = Counter()
    additions: Counter[str] = Counter()
    for obs in report.unresolved_removal_rows:
        removals[obs.slot.serialised()] += 1
    for obs in report.unresolved_addition_rows:
        additions[obs.slot.serialised()] += 1
    for slot in report.ambiguous_slots:
        if removals.get(slot, 0) == 1 and additions.get(slot, 0) == 1:
            populations.one_to_one_undecidable += 1
        else:
            populations.many_to_many_slots += 1


@dataclass
class ShardedDiff:
    """Bounded summary of a whole-corpus diff. No observation lists retained."""

    shard_count: int
    shards_processed: int = 0
    unchanged: int = 0
    additions: int = 0
    removals: int = 0
    new_slots: int = 0
    removed_slots: int = 0
    correction_candidates: int = 0
    correction_candidates_by_link: Counter[str] = field(default_factory=Counter)
    identifier_conflicts: int = 0
    unresolved_additions: int = 0
    unresolved_removals: int = 0
    ambiguous_slots: int = 0
    by_type: TypeCounts = field(default_factory=TypeCounts)
    populations: LinkPopulations = field(default_factory=LinkPopulations)
    a_rows_in: int = 0
    b_rows_in: int = 0
    per_shard_peak_observations: int = 0
    seconds: float = 0.0
    peak_rss_bytes: int = 0

    def absorb(self, report: DiffReport, n_a: int, n_b: int) -> None:
        """Fold one shard's report into the running totals, then forget it."""
        self.shards_processed += 1
        self.a_rows_in += n_a
        self.b_rows_in += n_b
        self.per_shard_peak_observations = max(self.per_shard_peak_observations, n_a + n_b)
        self.unchanged += sum(report.unchanged.values())
        self.additions += sum(report.additions.values())
        self.removals += sum(report.removals.values())
        # Disjoint by slot across shards, so these add rather than union.
        self.new_slots += len(report.new_slots)
        self.removed_slots += len(report.removed_slots)
        self.correction_candidates += len(report.correction_candidates)
        for candidate in report.correction_candidates:
            self.correction_candidates_by_link[candidate.linked_by] += 1
            if candidate.linked_by == "entry_doi":
                self.populations.one_to_one_entry_doi_agreed += 1
            else:
                self.populations.one_to_one_surrogate_agreed += 1
        self.identifier_conflicts += len(report.identifier_conflicts)
        for conflict in report.identifier_conflicts:
            self.populations.one_to_one_entry_doi_disagreed += 1
            if conflict.surrogate_agreed:
                self.populations.one_to_one_entry_doi_disagreed_surrogate_agreed += 1
        self.unresolved_additions += sum(report.unresolved_additions.values())
        self.unresolved_removals += sum(report.unresolved_removals.values())
        self.ambiguous_slots += len(report.ambiguous_slots)
        self.by_type.absorb(report)
        _classify_ambiguous(report, self.populations)

    @property
    def a_reconstructed(self) -> int:
        return self.unchanged + self.removals

    @property
    def b_reconstructed(self) -> int:
        return self.unchanged + self.additions

    @property
    def reconciles(self) -> bool:
        return self.a_reconstructed == self.a_rows_in and self.b_reconstructed == self.b_rows_in

    def as_dict(self) -> dict[str, Any]:
        return {
            "shard_version": SHARD_VERSION,
            "shard_hash": SHARD_HASH,
            "normalisation_version": NORMALISATION_VERSION,
            "shard_count": self.shard_count,
            "shards_processed": self.shards_processed,
            "a_observations_in": self.a_rows_in,
            "b_observations_in": self.b_rows_in,
            "unchanged_observations": self.unchanged,
            "added_observations": self.additions,
            "removed_observations": self.removals,
            "reconciliation": {
                "a_equals_unchanged_plus_removals": self.a_reconstructed,
                "a_observations_in": self.a_rows_in,
                "b_equals_unchanged_plus_additions": self.b_reconstructed,
                "b_observations_in": self.b_rows_in,
                "reconciles": self.reconciles,
            },
            "new_slots": self.new_slots,
            "removed_slots": self.removed_slots,
            "correction_candidates": self.correction_candidates,
            "correction_candidates_by_link": dict(
                sorted(self.correction_candidates_by_link.items())
            ),
            "identifier_conflicts": self.identifier_conflicts,
            "unresolved_additions": self.unresolved_additions,
            "unresolved_removals": self.unresolved_removals,
            "ambiguous_slots": self.ambiguous_slots,
            "by_measurement_type": self.by_type.as_dict(),
            "identifier_agreement": self.populations.as_dict(),
            "per_shard_peak_observations": self.per_shard_peak_observations,
            "seconds": round(self.seconds, 1),
            "peak_rss_bytes": self.peak_rss_bytes,
            "peak_rss_gb": round(self.peak_rss_bytes / 2**30, 2),
        }


#: Detail streams written per shard and appended across shards. Named so the
#: report can reference them by digest rather than inlining millions of rows.
DETAIL_STREAMS = (
    "additions",
    "removals",
    "unresolved_additions",
    "unresolved_removals",
    "correction_candidates",
    "identifier_conflicts",
    "new_slots",
    "removed_slots",
    "ambiguous_slots",
)


def _stream_detail(report: DiffReport, sinks: dict[str, io.TextIOBase], shard: int) -> None:
    """Append this shard's detail to disk. Nothing is kept after this returns.

    Each record carries its measurement type and its source locators, so a
    reader never has to re-derive either from the slot string.
    """

    def emit(stream: str, record: dict[str, Any]) -> None:
        sinks[stream].write(json.dumps(record | {"shard": shard}, sort_keys=True) + "\n")

    for stream, rows in (
        ("additions", report._rows(report.addition_rows)),  # noqa: SLF001 - same package
        ("removals", report._rows(report.removal_rows)),  # noqa: SLF001
        ("unresolved_additions", report._rows(report.unresolved_addition_rows)),  # noqa: SLF001
        ("unresolved_removals", report._rows(report.unresolved_removal_rows)),  # noqa: SLF001
    ):
        for record in rows:
            emit(stream, record | {"measurement_type": _measurement_type(record["value"])})

    for candidate in sorted(
        report.correction_candidates,
        key=lambda c: (c.slot.serialised(), c.before.serialised(), c.after.serialised()),
    ):
        emit(
            "correction_candidates",
            {
                "slot": candidate.slot.serialised(),
                "before": candidate.before.serialised(),
                "before_original": candidate.before.value_original,
                "after": candidate.after.serialised(),
                "after_original": candidate.after.value_original,
                "measurement_type": candidate.before.measurement_type,
                "type_changed": candidate.before.measurement_type
                != candidate.after.measurement_type,
                "linked_by": candidate.linked_by,
                "identifier": candidate.identifier,
                "provisional": candidate.provisional,
            },
        )

    for conflict in sorted(
        report.identifier_conflicts,
        key=lambda c: (c.slot.serialised(), c.before_entry_doi, c.after_entry_doi),
    ):
        emit(
            "identifier_conflicts",
            {
                "slot": conflict.slot.serialised(),
                "before": conflict.before.serialised(),
                "after": conflict.after.serialised(),
                "measurement_type": conflict.before.measurement_type,
                "before_entry_doi": conflict.before_entry_doi,
                "after_entry_doi": conflict.after_entry_doi,
                "before_reactant_set_id": conflict.before_reactant_set_id,
                "after_reactant_set_id": conflict.after_reactant_set_id,
                "surrogate_agreed": conflict.surrogate_agreed,
                "note": (
                    "entry DOIs disagree, so no link was made. The surrogate agreed, "
                    "and was deliberately not allowed to decide."
                    if conflict.surrogate_agreed
                    else "entry DOIs disagree and the surrogate did not agree either."
                ),
            },
        )

    for stream, slots in (
        ("new_slots", report.new_slots),
        ("removed_slots", report.removed_slots),
        ("ambiguous_slots", report.ambiguous_slots),
    ):
        for slot in sorted(slots):
            emit(stream, {"slot": slot})


def diff_sharded(
    a_dir: str | Path,
    b_dir: str | Path,
    detail_dir: str | Path,
    shard_count: int,
    *,
    a_label: str = "a",
    b_label: str = "b",
    budget: int = MAX_SHARD_OBSERVATIONS,
) -> ShardedDiff:
    """Diff every shard pair, streaming detail out and keeping only summaries.

    Raises `ShardTooLarge` if a shard pair exceeds `budget`, rather than
    attempting it and being killed partway: the caller is expected to repartition
    at a larger shard count, which redistributes whole slots.

    The budget is applied **before** either file is materialised, from a counted
    byte scan, and the subsequent reads are themselves bounded -- so neither a
    stale count nor a wrong one can get past the bound.
    """
    a_dir, b_dir, detail_dir = Path(a_dir), Path(b_dir), Path(detail_dir)
    detail_dir.mkdir(parents=True, exist_ok=True)
    summary = ShardedDiff(shard_count=shard_count)
    started = time.time()
    with ExitStack() as stack:
        sinks = {
            stream: stack.enter_context(
                (detail_dir / f"{stream}.jsonl").open("w", encoding="utf-8")
            )
            for stream in DETAIL_STREAMS
        }
        for shard in range(shard_count):
            a_path = a_dir / f"{a_label}-{shard:04d}.jsonl"
            b_path = b_dir / f"{b_label}-{shard:04d}.jsonl"
            # Budget first, by counted scan, while nothing is in memory yet.
            declared = count_rows(a_path) + count_rows(b_path)
            if declared > budget:
                msg = (
                    f"shard {shard} holds {declared:,} observations, over the declared budget "
                    f"of {budget:,}. Repartition at a larger shard count; do not raise the "
                    f"budget to fit a shard that was measured as too large."
                )
                raise ShardTooLarge(msg)
            a_rows = _read_shard_bounded(a_path, budget)
            b_rows = _read_shard_bounded(b_path, budget - len(a_rows))
            report = diff_snapshots(a_rows, b_rows)
            summary.absorb(report, len(a_rows), len(b_rows))
            _stream_detail(report, sinks, shard)
            # Explicit: the whole point is not to accumulate these.
            del report, a_rows, b_rows
    summary.seconds = time.time() - started
    summary.peak_rss_bytes = peak_rss_bytes()
    return summary


# ===================================================================== equivalence


def canonical_from_detail(detail_dir: str | Path) -> dict[str, Any]:
    """Reassemble the sharded detail into the shape `DiffReport.to_json` emits.

    **For equivalence testing on small fixtures only.** It loads every streamed
    record into memory, which is exactly what the sharded run exists to avoid --
    calling it on the corpus would defeat the purpose. It exists so the sharded
    result can be compared against the unsharded one field by field, rather than
    only by summary totals.

    Merging needs no cross-shard grouping: `_rows` groups by
    (slot, value, entry_doi, reactant_set_id) and every slot lives in one shard,
    so records from different shards can never share a group key. Concatenating
    and re-sorting by that key reproduces the unsharded ordering.
    """
    detail_dir = Path(detail_dir)

    def load(stream: str) -> list[dict[str, Any]]:
        path = detail_dir / f"{stream}.jsonl"
        if not path.exists():
            return []
        with path.open(encoding="utf-8") as fh:
            rows = [json.loads(line) for line in fh if line.strip()]
        for row in rows:
            row.pop("shard", None)
            row.pop("measurement_type", None)
            row.pop("type_changed", None)
        return rows

    def sort_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return sorted(
            rows,
            key=lambda r: (
                r["slot"],
                r["value"],
                r.get("entry_doi") or "",
                r.get("reactant_set_id") or "",
            ),
        )

    return {
        "additions_detail": sort_rows(load("additions")),
        "removals_detail": sort_rows(load("removals")),
        "unresolved_additions_detail": sort_rows(load("unresolved_additions")),
        "unresolved_removals_detail": sort_rows(load("unresolved_removals")),
        "correction_candidates_detail": sorted(
            load("correction_candidates"), key=lambda r: (r["slot"], r["before"], r["after"])
        ),
        "identifier_conflicts_detail": sorted(
            load("identifier_conflicts"),
            key=lambda r: (r["slot"], r["before_entry_doi"], r["after_entry_doi"]),
        ),
        "new_slots": sorted(r["slot"] for r in load("new_slots")),
        "removed_slots": sorted(r["slot"] for r in load("removed_slots")),
        "ambiguous_slots_detail": sorted(r["slot"] for r in load("ambiguous_slots")),
    }


def iter_jsonl(path: str | Path) -> Iterator[dict[str, Any]]:
    """Stream a detail file, for readers that must not load it whole."""
    with Path(path).open(encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                yield json.loads(line)


# ================================== cross-slot candidate correspondences

#: Fields of the slot string, so a cross-slot analysis does not re-parse by index
#: in several places.
SLOT_INCHIKEY, SLOT_SEQUENCE, SLOT_PUBLICATION = 0, 1, 2

#: How many matched pairs to retain with their locators, for traceability. A
#: bounded sample: retaining all of them would defeat the point of streaming.
TRACEABLE_SAMPLE = 20


def _group_key(record: dict[str, Any]) -> tuple[str, str, str]:
    """(compound, target, canonical value) -- the value carries the type."""
    parts = record["slot"].split("\t")
    return (parts[SLOT_INCHIKEY], parts[SLOT_SEQUENCE], record["value"])


def _record_order(record: dict[str, Any]) -> tuple[str, str, str]:
    """Canonical order within a group, so nothing depends on file order."""
    return (
        record["slot"],
        str(record.get("entry_doi") or ""),
        str(record.get("reactant_set_id") or ""),
    )


def _publication_of(record: dict[str, Any]) -> str:
    return record["slot"].split("\t")[SLOT_PUBLICATION]


@dataclass
class PositionalSensitivity:
    """What identifier agreement *would* be if ambiguous groups were paired greedily.

    **Algorithm-dependent, and labelled as such wherever it appears.** It pairs
    the lexicographically-sorted removal records against the sorted addition
    records, consuming counts. That is deterministic, but the correspondence it
    asserts is an artifact of the sort order, not evidence about any individual
    observation. It exists so a reader can see how much the ambiguous groups
    could move the headline figure -- not as a result.
    """

    paired_occurrences: int = 0
    entry_doi_both_present: int = 0
    entry_doi_agreed: int = 0
    reactant_set_id_both_present: int = 0
    reactant_set_id_agreed: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": "ALGORITHM-DEPENDENT, not evidence about any individual observation",
            "method": (
                "lexicographic greedy pairing of sorted removal records against sorted "
                "addition records within each ambiguous group, consuming counts"
            ),
            "paired_occurrences": self.paired_occurrences,
            "entry_doi": {
                "both_present": self.entry_doi_both_present,
                "agreed": self.entry_doi_agreed,
                "agreement_rate": (
                    round(self.entry_doi_agreed / self.entry_doi_both_present, 6)
                    if self.entry_doi_both_present
                    else None
                ),
            },
            "reactant_set_id": {
                "both_present": self.reactant_set_id_both_present,
                "agreed": self.reactant_set_id_agreed,
                "agreement_rate": (
                    round(self.reactant_set_id_agreed / self.reactant_set_id_both_present, 6)
                    if self.reactant_set_id_both_present
                    else None
                ),
            },
        }


@dataclass
class MovedPopulation:
    """Candidate re-attributions: counted, cross-slot, occurrence-conserving.

    `publication_ref` is part of the slot, so a row that gains a PMID leaves its
    old slot and reappears at a new one. The matcher reports that as one removal
    plus one addition and is right to -- corrections are inferred only within a
    slot -- so these pairs are never link candidates and the matcher's
    identifier-agreement figures cannot describe them.

    Three properties this analysis has to have, and an earlier version of it had
    none of them:

    * **Each occurrence participates at most once.** The first version matched a
      removal against an addition without consuming the addition's count, so two
      removals sharing a key both "moved" into a single addition and the
      population was inflated.
    * **Nothing depends on file order.** The first version took the first
      eligible addition in file order, so reversing the detail file changed which
      record a removal was compared against, and with it the reported identifier
      agreement.
    * **Pairing never consults an identifier.** Grouping is on (compound, target,
      measurement type, canonical value) and slot inequality only. Using an
      identifier to choose the pairing and then reporting that identifier's
      agreement would be circular.

    **Unambiguous groups** hold exactly one removal record and one addition
    record at different slots, so the correspondence is forced and identifier
    agreement is well defined. **Ambiguous groups** hold more on either side; no
    correspondence is asserted for them. Their sizes and unmatched occurrences
    are reported instead, and the positional-pairing figure is kept separate and
    labelled algorithm-dependent.

    These are **candidate** re-attributions throughout. Equal compound, target,
    type and value is consistent with one measurement re-appearing under a new
    attribution; it does not establish it for any individual pair.
    """

    # --- unambiguous groups
    unambiguous_groups: int = 0
    matched_occurrences: int = 0
    unmatched_removal_occurrences: int = 0
    unmatched_addition_occurrences: int = 0
    by_type: Counter[str] = field(default_factory=Counter)
    publication_gained: int = 0
    publication_lost: int = 0
    publication_changed: int = 0
    publication_unchanged: int = 0
    entry_doi_both_present: int = 0
    entry_doi_agreed: int = 0
    reactant_set_id_both_present: int = 0
    reactant_set_id_agreed: int = 0
    pairs_with_locators_both_sides: int = 0
    traceable: list[dict[str, Any]] = field(default_factory=list)

    # --- ambiguous groups: counted, never paired
    ambiguous_groups: int = 0
    ambiguous_removal_occurrences: int = 0
    ambiguous_addition_occurrences: int = 0
    ambiguous_pairable_upper_bound: int = 0
    ambiguous_shapes: Counter[str] = field(default_factory=Counter)
    ambiguous_by_type: Counter[str] = field(default_factory=Counter)
    ambiguous_groups_with_identifier_disagreement: int = 0

    # --- same-slot guard: must stay zero
    same_slot_pairs_seen: int = 0

    sensitivity: PositionalSensitivity = field(default_factory=PositionalSensitivity)

    @property
    def total_removal_occurrences_considered(self) -> int:
        return (
            self.matched_occurrences
            + self.unmatched_removal_occurrences
            + self.ambiguous_removal_occurrences
        )

    @property
    def total_addition_occurrences_considered(self) -> int:
        return (
            self.matched_occurrences
            + self.unmatched_addition_occurrences
            + self.ambiguous_addition_occurrences
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "population": (
                "Cross-slot candidate correspondences: an occurrence removed from A and an "
                "occurrence added to B at a DIFFERENT slot, with the same compound, target, "
                "operator and canonical value. Grouped without reference to either "
                "identifier, and each occurrence participates at most once."
            ),
            "status": "CANDIDATE correspondences, NOT established experimental identity",
            "what_equality_establishes": (
                "Equal compound, target, operator and canonical value at two DIFFERENT "
                "slots establishes a candidate correspondence, not experimental identity. "
                "Nothing in the files says the two rows report the same experiment: they "
                "may be one measurement recorded again under a new attribution, or two "
                "independent measurements that happen to agree."
            ),
            "unambiguous": {
                "definition": (
                    "exactly one removal record and one addition record in the group, at "
                    "different slots, so the correspondence is forced"
                ),
                "groups": self.unambiguous_groups,
                "matched_occurrences": self.matched_occurrences,
                "unmatched_removal_occurrences": self.unmatched_removal_occurrences,
                "unmatched_addition_occurrences": self.unmatched_addition_occurrences,
                "by_measurement_type": dict(sorted(self.by_type.items())),
                "publication_ref": {
                    "gained_an_attribution": self.publication_gained,
                    "lost_an_attribution": self.publication_lost,
                    "changed_attribution": self.publication_changed,
                    "unchanged_so_moved_on_another_slot_field": self.publication_unchanged,
                },
                "entry_doi": {
                    "both_present": self.entry_doi_both_present,
                    "agreed": self.entry_doi_agreed,
                    "agreement_rate": (
                        round(self.entry_doi_agreed / self.entry_doi_both_present, 6)
                        if self.entry_doi_both_present
                        else None
                    ),
                },
                "reactant_set_id": {
                    "both_present": self.reactant_set_id_both_present,
                    "agreed": self.reactant_set_id_agreed,
                    "agreement_rate": (
                        round(self.reactant_set_id_agreed / self.reactant_set_id_both_present, 6)
                        if self.reactant_set_id_both_present
                        else None
                    ),
                },
                "pairs_with_locators_both_sides": self.pairs_with_locators_both_sides,
            },
            "ambiguous": {
                "definition": (
                    "more than one record on either side, so no individual correspondence is "
                    "asserted. Sizes and unmatched occurrences are reported instead."
                ),
                "groups": self.ambiguous_groups,
                "removal_occurrences": self.ambiguous_removal_occurrences,
                "addition_occurrences": self.ambiguous_addition_occurrences,
                "pairable_upper_bound": self.ambiguous_pairable_upper_bound,
                "record_count_shapes": dict(sorted(self.ambiguous_shapes.items())),
                "by_measurement_type": dict(sorted(self.ambiguous_by_type.items())),
                "groups_where_candidate_identifiers_disagree": (
                    self.ambiguous_groups_with_identifier_disagreement
                ),
            },
            "occurrence_accounting": {
                "removal_occurrences_considered": self.total_removal_occurrences_considered,
                "addition_occurrences_considered": self.total_addition_occurrences_considered,
                "note": (
                    "matched + unmatched + ambiguous, on each side. Every occurrence in a "
                    "group present on both sides is counted exactly once."
                ),
            },
            "same_slot_pairs_seen": self.same_slot_pairs_seen,
            "positional_pairing_sensitivity": self.sensitivity.as_dict(),
            "interpretation": (
                "Identifier agreement is reported only on the unambiguous groups, where the "
                "correspondence is forced rather than chosen. It is evidence of stability "
                "WITHIN that population and not proof of global stability: the population is "
                "selected by value equality, the case where stability is easiest to observe. "
                "A matched candidate pairs one removal with one addition, so the pair "
                "contributes nothing to a net count in either direction -- whatever the "
                "correspondence turns out to mean."
            ),
        }


def _load_groups(path: Path) -> dict[tuple[str, str, str], list[dict[str, Any]]]:
    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for record in iter_jsonl(path):
        groups.setdefault(_group_key(record), []).append(record)
    for records in groups.values():
        records.sort(key=_record_order)
    return groups


def _absorb_unambiguous(
    stats: MovedPopulation, removal: dict[str, Any], addition: dict[str, Any]
) -> None:
    matched = min(int(removal["count"]), int(addition["count"]))
    stats.unambiguous_groups += 1
    stats.matched_occurrences += matched
    stats.unmatched_removal_occurrences += int(removal["count"]) - matched
    stats.unmatched_addition_occurrences += int(addition["count"]) - matched
    if matched <= 0:
        return

    stats.by_type[_measurement_type(removal["value"])] += matched

    before, after = _publication_of(removal), _publication_of(addition)
    if before == "unattributed" and after != "unattributed":
        stats.publication_gained += matched
    elif before != "unattributed" and after == "unattributed":
        stats.publication_lost += matched
    elif before != after:
        stats.publication_changed += matched
    else:
        stats.publication_unchanged += matched

    if removal.get("entry_doi") and addition.get("entry_doi"):
        stats.entry_doi_both_present += matched
        if removal["entry_doi"] == addition["entry_doi"]:
            stats.entry_doi_agreed += matched
    if removal.get("reactant_set_id") and addition.get("reactant_set_id"):
        stats.reactant_set_id_both_present += matched
        if removal["reactant_set_id"] == addition["reactant_set_id"]:
            stats.reactant_set_id_agreed += matched

    r_locs, a_locs = removal.get("source_locators") or [], addition.get("source_locators") or []
    if r_locs and a_locs:
        stats.pairs_with_locators_both_sides += matched
        if len(stats.traceable) < TRACEABLE_SAMPLE:
            stats.traceable.append(
                {
                    "value": removal["value"],
                    "measurement_type": _measurement_type(removal["value"]),
                    "matched_occurrences": matched,
                    "removed": {
                        "slot": removal["slot"],
                        "entry_doi": removal.get("entry_doi"),
                        "reactant_set_id": removal.get("reactant_set_id"),
                        "count": removal["count"],
                        "source_locators": sorted(r_locs),
                    },
                    "added": {
                        "slot": addition["slot"],
                        "entry_doi": addition.get("entry_doi"),
                        "reactant_set_id": addition.get("reactant_set_id"),
                        "count": addition["count"],
                        "source_locators": sorted(a_locs),
                    },
                    "entry_doi_agreed": bool(
                        removal.get("entry_doi")
                        and removal.get("entry_doi") == addition.get("entry_doi")
                    ),
                    "reactant_set_id_agreed": bool(
                        removal.get("reactant_set_id")
                        and removal.get("reactant_set_id") == addition.get("reactant_set_id")
                    ),
                }
            )


def _absorb_ambiguous(
    stats: MovedPopulation, removals: list[dict[str, Any]], additions: list[dict[str, Any]]
) -> None:
    r_total = sum(int(r["count"]) for r in removals)
    a_total = sum(int(a["count"]) for a in additions)
    stats.ambiguous_groups += 1
    stats.ambiguous_removal_occurrences += r_total
    stats.ambiguous_addition_occurrences += a_total
    stats.ambiguous_pairable_upper_bound += min(r_total, a_total)
    stats.ambiguous_shapes[f"{len(removals)}x{len(additions)}"] += 1
    stats.ambiguous_by_type[_measurement_type(removals[0]["value"])] += min(r_total, a_total)

    identifiers = {
        (str(rec.get("entry_doi") or ""), str(rec.get("reactant_set_id") or ""))
        for rec in (*removals, *additions)
    }
    if len(identifiers) > 1:
        stats.ambiguous_groups_with_identifier_disagreement += 1

    # Deterministic positional pairing, retained only as a labelled sensitivity.
    r_queue = [[rec, int(rec["count"])] for rec in removals]
    a_queue = [[rec, int(rec["count"])] for rec in additions]
    ri = ai = 0
    while ri < len(r_queue) and ai < len(a_queue):
        rec_r, left_r = r_queue[ri]
        rec_a, left_a = a_queue[ai]
        if rec_r["slot"] == rec_a["slot"]:
            # Cannot pair within one slot; advance the side with less left.
            if left_r <= left_a:
                ri += 1
            else:
                ai += 1
            continue
        take = min(left_r, left_a)
        stats.sensitivity.paired_occurrences += take
        if rec_r.get("entry_doi") and rec_a.get("entry_doi"):
            stats.sensitivity.entry_doi_both_present += take
            if rec_r["entry_doi"] == rec_a["entry_doi"]:
                stats.sensitivity.entry_doi_agreed += take
        if rec_r.get("reactant_set_id") and rec_a.get("reactant_set_id"):
            stats.sensitivity.reactant_set_id_both_present += take
            if rec_r["reactant_set_id"] == rec_a["reactant_set_id"]:
                stats.sensitivity.reactant_set_id_agreed += take
        r_queue[ri][1] -= take
        a_queue[ai][1] -= take
        if r_queue[ri][1] == 0:
            ri += 1
        if a_queue[ai][1] == 0:
            ai += 1


def moved_observations(detail_dir: str | Path) -> MovedPopulation:
    """Counted cross-slot candidate re-attributions, order-independently.

    Both detail files are grouped in memory. Bounded by the number of *grouped*
    records -- 380k for this corpus, not the 6.4M observations -- and measured at
    well under a gigabyte.
    """
    detail_dir = Path(detail_dir)
    stats = MovedPopulation()
    additions = _load_groups(detail_dir / "additions.jsonl")
    removals = _load_groups(detail_dir / "removals.jsonl")

    for key in sorted(set(removals) & set(additions)):
        r_records, a_records = removals[key], additions[key]
        # A removal and an addition at the same (slot, value) cannot both carry
        # surplus -- `diff_snapshots` nets them -- so this should never fire. It
        # is counted rather than assumed so a violation would be visible.
        same_slot = {r["slot"] for r in r_records} & {a["slot"] for a in a_records}
        if same_slot:
            stats.same_slot_pairs_seen += len(same_slot)

        cross_r = [r for r in r_records if r["slot"] not in same_slot]
        cross_a = [a for a in a_records if a["slot"] not in same_slot]
        if not cross_r or not cross_a:
            continue
        if len(cross_r) == 1 and len(cross_a) == 1:
            _absorb_unambiguous(stats, cross_r[0], cross_a[0])
        else:
            _absorb_ambiguous(stats, cross_r, cross_a)
    return stats
