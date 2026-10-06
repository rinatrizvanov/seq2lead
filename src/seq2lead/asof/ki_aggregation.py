"""KI-only pair aggregation over two snapshots, in bounded memory.

M11d matched all four measurement types because a descriptive diff must: a
type-filtered diff would report a measurement whose type changed as an
unexplained removal in one run and an unexplained addition in another. This step
is the opposite shape -- **one endpoint, through the real aggregation rules** --
and it introduces two constraints the slot-sharded matcher did not have.

**Eligibility is a property of a pair, not of a slot.** One compound-target pair
owns many slots, so a per-slot-shard eligibility figure would be computed on a
fraction of the pair's evidence and would be wrong by construction. The shards
here are keyed on the **pair**, which is sound in both directions: a slot's
compound and target determine its pair, so pair-sharding is a *coarsening* of
slot-sharding and keeps every slot whole as well as every pair.

**Types must not be merged, but classifications must not be re-derived either.**
`diff_snapshots` decides corrections, conflicts and ambiguity from a slot's whole
surplus *across all values at that slot* -- including other measurement types.
Feeding it a KI-only input would therefore change those decisions: the one
identifier conflict in the corpus is an `IC50` removal against an `EC50`
addition, which a KI-only diff could never see. So the diff runs on all types, as
in M11d, and the **outputs** are then restricted to KI. The classification each
row received is preserved; only the rows handed to the endpoint harness are
filtered.

Nothing here fits a model, computes a prediction metric, or scores anything.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import time
from collections import Counter
from contextlib import ExitStack
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from seq2lead.asof.bridge import build_increments
from seq2lead.asof.evaluation import (
    DEFAULT_DISCORDANCE_PKI,
    SCREENED_STATUSES,
    PairEvidence,
    evaluate_pair,
    exact_stats,
    full_b_status,
    training_label,
)
from seq2lead.asof.matching import DiffReport, diff_snapshots, slot_of
from seq2lead.asof.normalise import NORMALISATION_VERSION
from seq2lead.asof.sharded import peak_rss_bytes

if TYPE_CHECKING:
    from collections.abc import Iterable

#: Bumped when the aggregation or its outputs change shape.
AGGREGATION_VERSION = "m11e/ki-aggregation/v1"

#: The one endpoint this step aggregates. IC50, KD and EC50 are matched for the
#: descriptive diff and never merged into this harness.
KI = "KI"

#: Pair-shard key hash. Cryptographic and stable across processes, for the same
#: reason the slot shards use one: `hash()` is per-process randomised.
PAIR_SHARD_HASH = "blake2b-64"

#: Minimum per-class eligible pair counts a target must meet to be rankable.
#: Reported as a distribution rather than a single cut, so a cohort requirement
#: can be argued from the numbers instead of assumed before seeing them.
MIN_PER_CLASS = (1, 2, 5, 10, 20)


def pair_shard_index(inchikey: str, sequence_sha256: str, shard_count: int) -> int:
    """Which shard a (compound, target) pair belongs to."""
    body = f"{inchikey}\t{sequence_sha256}".encode()
    digest = hashlib.blake2b(body, digest_size=8).digest()
    return int.from_bytes(digest, "big") % shard_count


@dataclass
class PairPartitionStats:
    label: str
    source_path: str
    shard_count: int
    rows_read: int = 0
    rows_written: int = 0
    ki_rows: int = 0
    per_shard: list[int] = field(default_factory=list)
    seconds: float = 0.0

    @property
    def reconciles(self) -> bool:
        return self.rows_read == self.rows_written == sum(self.per_shard)

    def as_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "source_path": self.source_path,
            "shard_count": self.shard_count,
            "rows_read": self.rows_read,
            "rows_written": self.rows_written,
            "ki_rows": self.ki_rows,
            "reconciles": self.reconciles,
            "max_shard_rows": max(self.per_shard) if self.per_shard else 0,
            "min_shard_rows": min(self.per_shard) if self.per_shard else 0,
            "per_shard": list(self.per_shard),
            "seconds": round(self.seconds, 1),
        }


def partition_by_pair(
    export_path: str | Path, out_dir: str | Path, shard_count: int, label: str
) -> PairPartitionStats:
    """Stream an export into pair-keyed shards, writing each line verbatim.

    All four types are written through, because the diff inside each shard needs
    the full slot surplus to reach the same classifications M11d did.
    """
    export_path, out_dir = Path(export_path), Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stats = PairPartitionStats(
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
            slot = slot_of(row)
            index = pair_shard_index(slot.inchikey, slot.sequence_sha256, shard_count)
            sinks[index].write(line if line.endswith("\n") else line + "\n")
            stats.per_shard[index] += 1
            stats.rows_written += 1
            if str(row.get("measurement_type") or "").upper() == KI:
                stats.ki_rows += 1
    stats.seconds = time.time() - started
    return stats


# ================================================== type restriction of a report


def _value_type(value_serialised: str) -> str:
    return value_serialised.split("\t", 1)[0]


def restrict_report_to_type(report: DiffReport, mtype: str = KI) -> DiffReport:
    """A view of a full-type report holding only one type's rows.

    **The classifications are preserved, not recomputed.** Each row keeps the
    classification it received when the whole slot was visible; this only drops
    the rows belonging to other types. Re-running the diff on filtered input
    would instead *change* those decisions, because `_link` reads a slot's entire
    surplus.

    Slot-level sets (`new_slots`, `removed_slots`, `ambiguous_slots`) are copied
    unchanged: a slot being new is a fact about the slot, not about a type, and
    narrowing them would misreport a slot that is new but carries no KI.
    """
    out = DiffReport(
        is_single_snapshot_proxy=report.is_single_snapshot_proxy,
        proxy_note=report.proxy_note,
    )
    for name in (
        "unchanged",
        "additions",
        "removals",
        "unresolved_additions",
        "unresolved_removals",
    ):
        source: Counter[tuple[str, str]] = getattr(report, name)
        target: Counter[tuple[str, str]] = getattr(out, name)
        for (slot, value), n in source.items():
            if _value_type(value) == mtype:
                target[(slot, value)] += n
    for name in (
        "addition_rows",
        "removal_rows",
        "unresolved_addition_rows",
        "unresolved_removal_rows",
    ):
        getattr(out, name).extend(
            obs for obs in getattr(report, name) if obs.value.measurement_type == mtype
        )
    out.correction_candidates.extend(
        c
        for c in report.correction_candidates
        if c.before.measurement_type == mtype or c.after.measurement_type == mtype
    )
    out.identifier_conflicts.extend(
        c
        for c in report.identifier_conflicts
        if c.before.measurement_type == mtype or c.after.measurement_type == mtype
    )
    out.new_slots |= report.new_slots
    out.removed_slots |= report.removed_slots
    out.ambiguous_slots |= report.ambiguous_slots
    return out


def _is_type(row: dict[str, Any], mtype: str) -> bool:
    return str(row.get("measurement_type") or "").upper() == mtype


# ============================================================= the two arms


#: The two independent axes a cohort figure has to be qualified by.
#:
#: They are genuinely independent and were conflated in the first revision: a
#: pair's increment arm says which *evidence* counts, and its consistency branch
#: says whether snapshot B's own evidence contradicts itself. A figure quoted
#: without both is ambiguous, and the ambiguity is not harmless -- see
#: `BranchCohort`.
INCREMENT_ARMS = ("declared_increment", "cross_slot_excluded")
CONSISTENCY_BRANCHES = ("unscreened_sensitivity", "screened_primary")


@dataclass
class BranchCohort:
    """Pairs admitted to one consistency branch, with the figures a cohort needs.

    **Why this exists.** The first revision pooled every scoreable pair into one
    set of cohort figures and recorded branch membership separately, so the
    published target coverage, class balance and rankability described the
    *pre-screen pool* while being read as the cohort. The two differ in a way
    that matters: a target can hold five `active` pairs whose full-B evidence is
    self-contradictory and five clean `inactive` ones, look rankable at five per
    class in the pool, and have **no actives at all** once the consistency screen
    removes them.

    `unscreened_sensitivity` admits every scoreable pair -- it *is* the pre-screen
    pool, kept under a name that says so. `screened_primary` admits only pairs
    whose full-B status is not in `SCREENED_STATUSES`, and is the branch
    primary-cohort feasibility is judged on.
    """

    branch: str
    pairs: int = 0
    label_counts: Counter[str] = field(default_factory=Counter)
    targets: set[str] = field(default_factory=set)
    compounds: set[str] = field(default_factory=set)
    per_target: dict[str, Counter[str]] = field(default_factory=dict)

    def admit(self, sequence: str, inchikey: str, label: str) -> None:
        self.pairs += 1
        self.targets.add(sequence)
        self.compounds.add(inchikey)
        self.label_counts[label] += 1
        self.per_target.setdefault(sequence, Counter())[label] += 1

    def rankable_targets(self, minimum: int) -> int:
        """Targets with at least `minimum` admitted pairs of BOTH classes."""
        return sum(
            1
            for counts in self.per_target.values()
            if counts.get("active", 0) >= minimum and counts.get("inactive", 0) >= minimum
        )

    def as_dict(self) -> dict[str, Any]:
        active = self.label_counts.get("active", 0)
        inactive = self.label_counts.get("inactive", 0)
        decided = active + inactive
        return {
            "branch": self.branch,
            "admitted_pairs": self.pairs,
            "label_counts": dict(sorted(self.label_counts.items())),
            "class_balance": {
                "active": active,
                "inactive": inactive,
                "decided": decided,
                "positive_rate": round(active / decided, 6) if decided else None,
                "minority_count": None if decided == 0 else min(active, inactive),
            },
            "coverage": {
                "distinct_targets": len(self.targets),
                "distinct_compounds": len(self.compounds),
            },
            "rankability": {
                "definition": (
                    "a target is rankable at N when it has at least N ADMITTED pairs of both "
                    "classes in this branch; a target with one class cannot be ranked"
                ),
                "targets_with_any_admitted_pair": len(self.per_target),
                "targets_single_class_only": sum(
                    1
                    for c in self.per_target.values()
                    if bool(c.get("active", 0)) != bool(c.get("inactive", 0))
                ),
                "rankable_at": {str(n): self.rankable_targets(n) for n in MIN_PER_CLASS},
            },
        }


@dataclass
class ArmCounts:
    """One increment arm, reported across both consistency branches.

    Eligibility exclusions (`discordant`, `ambiguous_label`, the endpoint status
    failures) happen **before** the consistency screen and are therefore branch
    independent: they are counted once here rather than inside a branch.
    """

    name: str
    pairs: int = 0
    excluded_pairs_by_reason: Counter[str] = field(default_factory=Counter)
    strata: Counter[str] = field(default_factory=Counter)
    increment_label_counts: Counter[str] = field(default_factory=Counter)
    increment_observations: int = 0
    eligible_increment_observations: int = 0
    withheld_observations: int = 0
    excluded_cross_slot_observations: int = 0
    unparseable_dropped: int = 0
    pairs_with_no_increment: int = 0
    screened_out_pairs: int = 0
    screened_because: Counter[str] = field(default_factory=Counter)
    unscreened_sensitivity: BranchCohort = field(
        default_factory=lambda: BranchCohort(branch="unscreened_sensitivity")
    )
    screened_primary: BranchCohort = field(
        default_factory=lambda: BranchCohort(branch="screened_primary")
    )

    @property
    def scoreable_pairs(self) -> int:
        """Pairs passing eligibility, before the consistency screen."""
        return self.unscreened_sensitivity.pairs

    def as_dict(self) -> dict[str, Any]:
        unscreened = self.unscreened_sensitivity.as_dict()
        screened = self.screened_primary.as_dict()
        return {
            "increment_arm": self.name,
            "pairs_with_any_increment": self.pairs,
            "excluded_pairs_by_reason": dict(sorted(self.excluded_pairs_by_reason.items())),
            "excluded_pairs_total": sum(self.excluded_pairs_by_reason.values()),
            "eligibility_is_branch_independent": (
                "discordance, ambiguous labels and endpoint status failures are decided before "
                "the consistency screen, so these counts are the same in both branches"
            ),
            "pre_screen_pool": {
                "note": (
                    "every pair passing eligibility, which is exactly the unscreened sensitivity "
                    "branch. The first revision published these figures as the cohort; they are "
                    "the pool the cohort is drawn FROM."
                ),
                "scoreable_pairs": self.scoreable_pairs,
            },
            "branches": {
                "unscreened_sensitivity": unscreened,
                "screened_primary": screened,
                "screened_out_of_primary": self.screened_out_pairs,
                "screened_because": dict(sorted(self.screened_because.items())),
                "effect_of_the_screen": {
                    "pairs_removed": unscreened["admitted_pairs"] - screened["admitted_pairs"],
                    "targets_lost": (
                        unscreened["coverage"]["distinct_targets"]
                        - screened["coverage"]["distinct_targets"]
                    ),
                    "rankable_at_5_before": unscreened["rankability"]["rankable_at"]["5"],
                    "rankable_at_5_after": screened["rankability"]["rankable_at"]["5"],
                },
            },
            "strata": dict(sorted(self.strata.items())),
            "increment_label_counts_all_pairs": dict(sorted(self.increment_label_counts.items())),
            "observations": {
                "increment_before_withholding": self.increment_observations,
                "eligible_increment": self.eligible_increment_observations,
                "withheld_for_corrections": self.withheld_observations,
                "excluded_as_cross_slot_candidates": self.excluded_cross_slot_observations,
                "unparseable_dropped": self.unparseable_dropped,
            },
            "pairs_with_no_increment_after_filtering": self.pairs_with_no_increment,
        }


@dataclass
class ShardResult:
    """One pair-shard's contribution. Bounded: counters and small sets only."""

    shard: int
    a_rows: int = 0
    b_rows: int = 0
    a_ki_rows: int = 0
    b_ki_rows: int = 0
    pairs_seen: int = 0
    historical_pairs: int = 0
    new_pairs: int = 0
    removal_observations: int = 0
    pairs_with_removals: int = 0
    training_digest: str = ""
    training_only_digest: str = ""
    training_label_counts: Counter[str] = field(default_factory=Counter)
    # KI classification totals from the restricted report, so pair-sharding can be
    # checked against the slot-sharded KI column of the M11d run.
    ki_unchanged: int = 0
    ki_additions: int = 0
    ki_removals: int = 0
    ki_correction_candidates: int = 0
    b_only_pairs_excluded_from_training: int = 0
    # Increment-side figures over EVERY bridged pair, including pairs whose
    # whole increment was withheld and which therefore never reach an arm.
    ki_increment_measurements: int = 0
    ki_withheld_all_pairs: int = 0
    ki_unparseable_addition_rows: int = 0


class AlignmentError(RuntimeError):
    """An increment measurement has no locator, or the other way round.

    Raised rather than tolerated: the cross-slot sensitivity selects occurrences
    *by locator*, so a misalignment would silently exclude the wrong measurements
    or stop short of the end of the list.
    """


def _label_digest(items: Iterable[str]) -> str:
    """Stable digest over canonical per-pair training rows."""
    body = "\n".join(sorted(items))
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _training_row(
    evidence: PairEvidence, threshold: float, discordance: float = DEFAULT_DISCORDANCE_PKI
) -> str:
    """One pair's training evidence, canonically, for the A-independence digest.

    **Status, label and a count are not enough.** Two different sets of A
    observations can share a label and a cardinality while differing in the
    values themselves, in whether they are exact or censored, and in whether
    they are discordant enough to deny the pair standing. A digest over only the
    label would pass while the training evidence underneath had changed, which is
    the opposite of what this check is for.

    So the row carries the **canonical A evidence** -- every measurement's
    relation and value, sorted -- together with the exact-spread statistics and
    the discordance verdict that the training reading actually depends on.
    """
    status, label = training_label(evidence, threshold)
    stats = exact_stats(evidence.in_a)
    measurements = sorted(f"{m.relation}{m.value_nm!r}" for m in evidence.in_a)
    parts = [
        evidence.pair,
        status,
        label,
        str(len(evidence.in_a)),
        f"n_exact={stats.n}",
        f"median={stats.median!r}",
        f"min={stats.minimum!r}",
        f"max={stats.maximum!r}",
        f"spread={stats.spread!r}",
        f"discordant={stats.is_discordant(discordance)}",
        "evidence=" + "|".join(measurements),
    ]
    return "\t".join(parts)


def training_only_labels(
    a_rows: list[dict[str, Any]],
    threshold: float,
    mtype: str = KI,
    discordance: float = DEFAULT_DISCORDANCE_PKI,
) -> tuple[str, Counter[str]]:
    """A's training labels, computed from A alone and nothing else.

    Takes one snapshot on purpose. The as-of claim is that training evidence is
    what an analyst standing at T_train would have had, and the cheapest way to
    keep that true is a path on which B cannot appear. Its digest is compared
    against the one produced by the full two-snapshot run, so "A is unchanged by
    B" is checked rather than asserted.
    """
    from seq2lead.asof.bridge import to_measurement
    from seq2lead.asof.matching import observation_of

    by_pair: dict[tuple[str, str], list] = {}
    for row in a_rows:
        if not _is_type(row, mtype):
            continue
        obs = observation_of(row)
        m = to_measurement(obs)
        if m is None:
            continue
        by_pair.setdefault((obs.slot.inchikey, obs.slot.sequence_sha256), []).append(m)

    rows: list[str] = []
    counts: Counter[str] = Counter()
    for key, measurements in by_pair.items():
        # in_b is deliberately left EMPTY: nothing on this path may read B, and
        # the training reading does not consult it.
        evidence = PairEvidence(pair=f"{key[0]}|{key[1]}", in_a=measurements, in_b=[])
        _status, label = training_label(evidence, threshold)
        rows.append(_training_row(evidence, threshold, discordance))
        counts[label] += 1
    return (_label_digest(rows), counts)


def aggregate_shard(
    a_rows: list[dict[str, Any]],
    b_rows: list[dict[str, Any]],
    *,
    shard: int,
    threshold: float,
    discordance: float = DEFAULT_DISCORDANCE_PKI,
    excluded_locators: frozenset[str] = frozenset(),
    mtype: str = KI,
    primary: ArmCounts | None = None,
    sensitivity: ArmCounts | None = None,
    pair_records: list[dict[str, Any]] | None = None,
) -> ShardResult:
    """Aggregate one pair-shard through the bridge and the M4 endpoint rules.

    The diff runs on **all** types so its classifications match M11d's, then its
    outputs are restricted to `mtype` before anything reaches the harness.
    """
    result = ShardResult(shard=shard, a_rows=len(a_rows), b_rows=len(b_rows))
    diff_all = diff_snapshots(a_rows, b_rows)
    restricted = restrict_report_to_type(diff_all, mtype)
    del diff_all

    a_ki = [r for r in a_rows if _is_type(r, mtype)]
    b_ki = [r for r in b_rows if _is_type(r, mtype)]
    result.a_ki_rows, result.b_ki_rows = len(a_ki), len(b_ki)

    result.ki_unchanged = sum(restricted.unchanged.values())
    result.ki_additions = sum(restricted.additions.values())
    result.ki_removals = sum(restricted.removals.values())
    result.ki_correction_candidates = sum(
        1 for c in restricted.correction_candidates if c.before.measurement_type == mtype
    )

    # Exact, and independent of the bridge: an addition row whose value will not
    # normalise never enters any increment. `prov.n_unparseable_dropped` cannot be
    # used for this -- it also counts drops from in_a and in_b.
    from seq2lead.asof.bridge import to_measurement

    result.ki_unparseable_addition_rows = sum(
        1 for obs in restricted.addition_rows if to_measurement(obs) is None
    )

    bridged, _ = build_increments(a_ki, b_ki, report=restricted)
    result.pairs_seen = len(bridged)

    training_rows: list[str] = []
    for item in bridged:
        evidence, prov = item.evidence, item.provenance
        if evidence.in_a:
            result.historical_pairs += 1
        else:
            result.new_pairs += 1
        if evidence.removals:
            result.pairs_with_removals += 1
            result.removal_observations += len(evidence.removals)

        tr_status, tr_label = training_label(evidence, threshold)
        # Only pairs that HAVE A evidence belong in the training digest. A pair
        # that appears first in B has an empty `in_a` and no training evidence at
        # all; including it would compare the two-snapshot run against an A-only
        # path that correctly never enumerates it, and the digests would differ
        # for a reason that says nothing about whether A changed.
        if evidence.in_a:
            training_rows.append(_training_row(evidence, threshold, discordance))
            result.training_label_counts[tr_label] += 1
        else:
            result.b_only_pairs_excluded_from_training += 1

        increment = list(evidence.increment or [])
        locators = list(prov.increment_locators)
        result.ki_increment_measurements += len(increment)
        result.ki_withheld_all_pairs += prov.n_correction_candidates_withheld
        inchikey, sequence = evidence.pair.split("|", 1)

        # One locator per increment measurement, by construction: the bridge
        # appends to both lists in the same loop iteration. `strict=True` turns a
        # future divergence into an error instead of a silently shortened
        # exclusion set -- `strict=False` would drop the tail of whichever list
        # is longer and under-apply the sensitivity without saying so.
        if len(increment) != len(locators):
            msg = (
                f"{evidence.pair}: {len(increment)} increment measurements but "
                f"{len(locators)} locators; the exclusion set cannot be applied safely"
            )
            raise AlignmentError(msg)

        # ---- primary arm: the declared increment semantics, unchanged
        primary_outcome = None
        if increment:
            primary_outcome = evaluate_pair(evidence, threshold, discordance)
            if primary is not None:
                _absorb(primary, primary_outcome, evidence, prov, inchikey, sequence, excluded=0)

        # ---- sensitivity arm: drop cross-slot candidate addition occurrences
        sensitivity_outcome = None
        kept = [
            m for m, loc in zip(increment, locators, strict=True) if loc not in excluded_locators
        ]
        dropped = len(increment) - len(kept)
        reduced = evidence
        if kept:
            reduced = PairEvidence(
                pair=evidence.pair,
                in_a=evidence.in_a,
                in_b=evidence.in_b,
                increment=kept,
                removals=evidence.removals,
            )
            sensitivity_outcome = evaluate_pair(reduced, threshold, discordance)
            if sensitivity is not None:
                _absorb(
                    sensitivity,
                    sensitivity_outcome,
                    reduced,
                    prov,
                    inchikey,
                    sequence,
                    excluded=dropped,
                )
        elif increment and sensitivity is not None:
            sensitivity.pairs_with_no_increment += 1
            sensitivity.excluded_cross_slot_observations += dropped

        # Audit retained for every pair that has a decision worth auditing: an
        # increment in either arm, an increment that was entirely withheld
        # against corrections, one entirely removed by the cross-slot
        # sensitivity, or explicit removals. A pair with none of those is
        # unchanged between the snapshots and has nothing to explain -- emitting
        # 500k such records would bury the ones that do.
        has_decision = bool(
            locators or prov.n_correction_candidates_withheld or (evidence.removals or [])
        )
        if pair_records is not None and has_decision:
            pair_records.append(
                _audit_record(
                    pair=evidence.pair,
                    shard=shard,
                    evidence=evidence,
                    prov=prov,
                    threshold=threshold,
                    discordance=discordance,
                    primary_outcome=primary_outcome,
                    sensitivity_outcome=sensitivity_outcome,
                    excluded_locators=excluded_locators,
                    sensitivity_increment=kept,
                )
            )

    result.training_digest = _label_digest(training_rows)
    result.training_only_digest, _ = training_only_labels(a_rows, threshold, mtype, discordance)
    return result


def _absorb(
    arm: ArmCounts,
    outcome: Any,
    evidence: PairEvidence,
    prov: Any,
    inchikey: str,
    sequence: str,
    *,
    excluded: int,
) -> None:
    """Fold one pair into one increment arm, across both consistency branches.

    The order matters and was wrong before: cohort figures are now recorded
    **inside** a branch, so no figure describes a population the screen has not
    been applied to while being read as one that it has.
    """
    arm.pairs += 1
    arm.strata[outcome.stratum] += 1
    arm.increment_label_counts[outcome.increment_label] += 1
    arm.increment_observations += len(evidence.increment or []) + excluded
    arm.eligible_increment_observations += len(evidence.increment or [])
    arm.withheld_observations += prov.n_correction_candidates_withheld
    arm.excluded_cross_slot_observations += excluded
    arm.unparseable_dropped += prov.n_unparseable_dropped

    if not outcome.is_scoreable:
        arm.excluded_pairs_by_reason[outcome.eligibility_reason or "unknown"] += 1
        return

    # Eligible. The unscreened sensitivity branch admits every eligible pair; the
    # screened primary branch admits only those B's own evidence does not
    # contradict.
    arm.unscreened_sensitivity.admit(sequence, inchikey, outcome.increment_label)
    if outcome.in_primary:
        arm.screened_primary.admit(sequence, inchikey, outcome.increment_label)
    else:
        arm.screened_out_pairs += 1
        if outcome.screened_because:
            arm.screened_because[outcome.screened_because] += 1


#: Why an increment arm produced no outcome for a pair. Distinguished because
#: they mean different things: nothing was added, something was added and
#: attributed to a correction, or something was added and set aside by the
#: pre-registered cross-slot sensitivity.
ABSENT_NO_ADDITIONS = "no_additions_at_this_pair"
ABSENT_ALL_WITHHELD = "all_additions_withheld_as_correction_candidates"
ABSENT_ALL_EXCLUDED = "all_remaining_additions_excluded_by_cross_slot_sensitivity"
ABSENT_ALL_UNPARSEABLE = "all_additions_had_values_that_would_not_normalise"


def _absence_reason(
    *, n_locators: int, n_withheld: int, n_unparseable_additions: int, arm: str
) -> str:
    """Name the reason an arm has no increment, rather than reporting a blank."""
    if n_locators and arm == "cross_slot_excluded":
        return ABSENT_ALL_EXCLUDED
    if n_withheld:
        return ABSENT_ALL_WITHHELD
    if n_unparseable_additions:
        return ABSENT_ALL_UNPARSEABLE
    return ABSENT_NO_ADDITIONS


def _audit_record(
    *,
    pair: str,
    shard: int,
    evidence: PairEvidence,
    prov: Any,
    threshold: float,
    discordance: float,
    primary_outcome: Any | None,
    sensitivity_outcome: Any | None,
    excluded_locators: frozenset[str],
    sensitivity_increment: list[Any],
    n_unparseable_additions: int = 0,
) -> dict[str, Any]:
    """Everything needed to re-derive a pair's decisions, for both arms.

    **The two audit readings are computed from their own evidence, not borrowed
    from an arm outcome.** `training_from_a` reads `in_a` and `full_b` reads
    `in_b`, both directly. An earlier version took them from whichever arm
    happened to produce a `PairOutcome`, so a pair whose entire increment was
    withheld against a correction candidate — exactly the case example (b) of
    §7a describes — lost its historical A status, label and exact statistics and
    its actual-B reading as well. Those two readings do not depend on the
    increment and must not vanish with it.

    Where an arm has no increment, the reason is **named**: no additions at all,
    every addition withheld as a correction candidate, or every remaining
    addition excluded by the cross-slot sensitivity. Those are three different
    facts and a blank cannot tell them apart.
    """

    def arm_view(outcome: Any | None, increment: list[Any], excluded_here: int, arm: str):
        if outcome is None:
            return {
                "has_increment": False,
                "absence_reason": _absence_reason(
                    n_locators=len(prov.increment_locators),
                    n_withheld=prov.n_correction_candidates_withheld,
                    n_unparseable_additions=n_unparseable_additions,
                    arm=arm,
                ),
                "n_increment": 0,
                "n_excluded_cross_slot": excluded_here,
                "entered_this_arm": False,
            }
        return {
            "has_increment": True,
            "entered_this_arm": True,
            "absence_reason": None,
            "n_increment": len(increment),
            "n_excluded_cross_slot": excluded_here,
            "increment_status": outcome.increment_status,
            "increment_label": outcome.increment_label,
            "increment_exact": (
                outcome.increment_exact.to_dict() if outcome.increment_exact else None
            ),
            "eligibility_reason": outcome.eligibility_reason,
            "is_scoreable": outcome.is_scoreable,
            "screened_because": outcome.screened_because,
            "branch_membership": {
                "unscreened_sensitivity": bool(outcome.is_scoreable),
                "screened_primary": bool(outcome.in_primary),
            },
            "stratum": outcome.stratum,
        }

    locators = list(prov.increment_locators)
    excluded_now = [loc for loc in locators if loc in excluded_locators]

    # --- the two readings, computed from their own evidence
    tr_status, tr_label = training_label(evidence, threshold)
    tr_exact = exact_stats(evidence.in_a)
    b_status, b_label = full_b_status(evidence, threshold)
    b_exact = exact_stats(evidence.in_b)

    return {
        "pair": pair,
        "shard": shard,
        "n_in_a": len(evidence.in_a),
        "n_in_b": len(evidence.in_b),
        "n_removals": len(evidence.removals or []),
        "n_withheld_corrections": prov.n_correction_candidates_withheld,
        "n_unparseable_dropped": prov.n_unparseable_dropped,
        "n_identifier_conflicts": prov.n_identifier_conflicts,
        "slots_touched": len(set(prov.slots)),
        # Independent of either arm: A's evidence is historical and B's is
        # current, and neither is a function of what survived into an increment.
        "training_from_a": {
            "source": "in_a, read directly",
            "status": tr_status,
            "label": tr_label,
            "exact": tr_exact.to_dict(),
            "is_discordant": tr_exact.is_discordant(discordance),
            "has_evidence": bool(evidence.in_a),
        },
        "full_b_audit": {
            "source": "in_b, read directly -- complete actual B, corrections included",
            "status": b_status,
            "label": b_label,
            "exact": b_exact.to_dict(),
            "is_discordant": b_exact.is_discordant(discordance),
            "would_be_screened": b_status in SCREENED_STATUSES,
            "has_evidence": bool(evidence.in_b),
        },
        "arms": {
            "declared_increment": arm_view(
                primary_outcome, list(evidence.increment or []), 0, "declared_increment"
            ),
            "cross_slot_excluded": arm_view(
                sensitivity_outcome,
                sensitivity_increment,
                len(excluded_now),
                "cross_slot_excluded",
            ),
        },
        "increment_locators": locators,
        "increment_locators_excluded_by_sensitivity": excluded_now,
        "withheld_locators": list(prov.withheld_locators),
        "removed_locators": list(prov.removed_locators),
    }


@dataclass
class KiAggregation:
    """Whole-corpus KI aggregation result. Bounded summaries only."""

    shard_count: int
    threshold: float
    discordance: float
    shards_processed: int = 0
    a_rows: int = 0
    b_rows: int = 0
    a_ki_rows: int = 0
    b_ki_rows: int = 0
    pairs_seen: int = 0
    historical_pairs: int = 0
    new_pairs: int = 0
    pairs_with_removals: int = 0
    removal_observations: int = 0
    ki_unchanged: int = 0
    ki_additions: int = 0
    ki_removals: int = 0
    ki_correction_candidates: int = 0
    b_only_pairs_excluded_from_training: int = 0
    ki_increment_measurements: int = 0
    ki_withheld_all_pairs: int = 0
    ki_unparseable_addition_rows: int = 0
    training_label_counts: Counter[str] = field(default_factory=Counter)
    training_digests: list[str] = field(default_factory=list)
    training_only_digests: list[str] = field(default_factory=list)
    primary: ArmCounts = field(default_factory=lambda: ArmCounts(name="primary"))
    sensitivity: ArmCounts = field(default_factory=lambda: ArmCounts(name="cross_slot_sensitivity"))
    seconds: float = 0.0
    peak_rss_bytes: int = 0
    shard_observations_max: int = 0

    @property
    def training_unchanged_by_b(self) -> bool:
        """A's labels from the two-snapshot run must equal A's labels from A alone."""
        return self.training_digests == self.training_only_digests

    def absorb(self, result: ShardResult) -> None:
        self.shards_processed += 1
        self.a_rows += result.a_rows
        self.b_rows += result.b_rows
        self.a_ki_rows += result.a_ki_rows
        self.b_ki_rows += result.b_ki_rows
        self.pairs_seen += result.pairs_seen
        self.historical_pairs += result.historical_pairs
        self.new_pairs += result.new_pairs
        self.pairs_with_removals += result.pairs_with_removals
        self.removal_observations += result.removal_observations
        self.ki_unchanged += result.ki_unchanged
        self.ki_additions += result.ki_additions
        self.ki_removals += result.ki_removals
        self.ki_correction_candidates += result.ki_correction_candidates
        self.b_only_pairs_excluded_from_training += result.b_only_pairs_excluded_from_training
        self.ki_increment_measurements += result.ki_increment_measurements
        self.ki_withheld_all_pairs += result.ki_withheld_all_pairs
        self.ki_unparseable_addition_rows += result.ki_unparseable_addition_rows
        self.training_label_counts.update(result.training_label_counts)
        self.training_digests.append(result.training_digest)
        self.training_only_digests.append(result.training_only_digest)

    def as_dict(self) -> dict[str, Any]:
        primary, sens = self.primary.as_dict(), self.sensitivity.as_dict()
        return {
            "aggregation_version": AGGREGATION_VERSION,
            "normalisation_version": NORMALISATION_VERSION,
            "endpoint": KI,
            "threshold_pki": self.threshold,
            "discordance_pki": self.discordance,
            "shard_count": self.shard_count,
            "shards_processed": self.shards_processed,
            "sharded_on": "compound-target pair, so every pair is complete in one shard",
            "observations": {
                "a_all_types": self.a_rows,
                "b_all_types": self.b_rows,
                "a_ki": self.a_ki_rows,
                "b_ki": self.b_ki_rows,
                "ki_removals": self.removal_observations,
            },
            "ki_classifications_from_the_restricted_report": {
                "unchanged": self.ki_unchanged,
                "additions": self.ki_additions,
                "removals": self.ki_removals,
                "correction_candidates": self.ki_correction_candidates,
                "a_equals_unchanged_plus_removals": self.ki_unchanged + self.ki_removals,
                "b_equals_unchanged_plus_additions": self.ki_unchanged + self.ki_additions,
                "note": (
                    "These must equal the KI column of the slot-sharded M11d run. Pair-sharding "
                    "is a coarsening of slot-sharding, so the classifications cannot differ -- "
                    "and the equality is checked rather than assumed."
                ),
            },
            "pairs": {
                "with_ki_evidence_in_either_snapshot": self.pairs_seen,
                "present_in_a": self.historical_pairs,
                "absent_from_a": self.new_pairs,
                "with_ki_removals": self.pairs_with_removals,
                "note": (
                    "'present_in_a' is historical presence in snapshot A. It is NOT recurrence "
                    "relative to an eventual fitted training set: which pairs a model is "
                    "actually trained on depends on split construction that has not been run, "
                    "so recurrence against a fitted set cannot be reported here."
                ),
            },
            "training_evidence_from_a": {
                "label_counts": dict(sorted(self.training_label_counts.items())),
                "unchanged_by_b": self.training_unchanged_by_b,
                "how_checked": (
                    "per shard, the (pair, status, label, n) digest from the two-snapshot run is "
                    "compared against the same digest computed on a path that never receives B. "
                    "Restricted to pairs that HAVE A evidence: a pair appearing first in B has an "
                    "empty in_a and no training evidence, and the A-only path correctly never "
                    "enumerates it."
                ),
                "shards_compared": len(self.training_digests),
                "pairs_first_seen_in_b_excluded_from_the_digest": (
                    self.b_only_pairs_excluded_from_training
                ),
            },
            "arms": {
                "declared_increment": primary,
                "cross_slot_excluded": sens,
                "axes": {
                    "increment_arm": list(INCREMENT_ARMS),
                    "consistency_branch": list(CONSISTENCY_BRANCHES),
                    "note": (
                        "Two independent axes. The increment arm says which evidence counts; "
                        "the consistency branch says whether snapshot B's own evidence "
                        "contradicts itself. Every cohort figure below is qualified by both, "
                        "because the first revision pooled them and published pre-screen "
                        "figures as the cohort."
                    ),
                },
            },
            "cohort_cells": {
                f"{arm_name}/{branch}": (arm_body["branches"][branch] | {"increment_arm": arm_name})
                for arm_name, arm_body in (
                    ("declared_increment", primary),
                    ("cross_slot_excluded", sens),
                )
                for branch in CONSISTENCY_BRANCHES
            },
            "primary_cohort_feasibility_is_judged_on": (
                "the screened_primary branch -- pairs whose own B evidence is self-consistent. "
                "Reported for both increment arms; the cross_slot_excluded arm is the "
                "conservative one."
            ),
            "increment_observation_accounting": {
                "ki_additions_from_the_matcher": self.ki_additions,
                "increment_measurements_all_pairs": self.ki_increment_measurements,
                "withheld_for_corrections_all_pairs": self.ki_withheld_all_pairs,
                "unparseable_addition_rows": self.ki_unparseable_addition_rows,
                "sum": (
                    self.ki_increment_measurements
                    + self.ki_withheld_all_pairs
                    + self.ki_unparseable_addition_rows
                ),
                "reconciles": (
                    self.ki_increment_measurements
                    + self.ki_withheld_all_pairs
                    + self.ki_unparseable_addition_rows
                    == self.ki_additions
                ),
                "note": (
                    "Every KI addition the matcher counted is either a measurement in some "
                    "pair's increment, withheld against a correction candidate, or an addition "
                    "row whose value would not normalise. Counted over ALL bridged pairs, "
                    "including those whose whole increment was withheld and which therefore "
                    "never appear in an arm -- which is why the arm-level figures are smaller."
                ),
            },
            "sensitivity_effect": {
                "axis": "increment arm, holding the consistency branch fixed",
                "screened_primary": {
                    "admitted_declared": primary["branches"]["screened_primary"]["admitted_pairs"],
                    "admitted_cross_slot_excluded": sens["branches"]["screened_primary"][
                        "admitted_pairs"
                    ],
                    "targets_declared": primary["branches"]["screened_primary"]["coverage"][
                        "distinct_targets"
                    ],
                    "targets_cross_slot_excluded": sens["branches"]["screened_primary"]["coverage"][
                        "distinct_targets"
                    ],
                    "rankable_at_5_declared": primary["branches"]["screened_primary"][
                        "rankability"
                    ]["rankable_at"]["5"],
                    "rankable_at_5_cross_slot_excluded": sens["branches"]["screened_primary"][
                        "rankability"
                    ]["rankable_at"]["5"],
                },
                "unscreened_sensitivity": {
                    "admitted_declared": primary["branches"]["unscreened_sensitivity"][
                        "admitted_pairs"
                    ],
                    "admitted_cross_slot_excluded": sens["branches"]["unscreened_sensitivity"][
                        "admitted_pairs"
                    ],
                },
                "observations_excluded": sens["observations"]["excluded_as_cross_slot_candidates"],
            },
            "resources": {
                "seconds": round(self.seconds, 1),
                "peak_rss_bytes": self.peak_rss_bytes,
                "peak_rss_gb": round(self.peak_rss_bytes / 2**30, 2),
                "largest_shard_pair_observations": self.shard_observations_max,
                "budget_observations_per_shard_pair": MAX_SHARD_OBSERVATIONS,
                "budget_enforced_before_materialising": True,
            },
            "qualifications": [
                "Exploratory. Snapshot B is our already-inspected 202609 and the M8/M9 results "
                "were produced from it, so this validates machinery and measures rates; it is "
                "not a confirmatory evaluation.",
                "Provisional pooling: whether Ki may be pooled across assay contexts at all is "
                "M5's open question. These counts assume the current pooling rule and would "
                "change if it were narrowed.",
                "No model was fitted, no prediction metric was computed, nothing was docked.",
            ],
        }


#: Declared budget for one pair-shard PAIR, both sides combined. Pair shards hold
#: all four measurement types because the diff needs the whole slot surplus, so
#: the budget is sized on the measured ~4,674 bytes per observation with room for
#: the bridge's own structures on top.
MAX_SHARD_OBSERVATIONS = 400_000


class ShardTooLarge(MemoryError):
    """A pair-shard pair exceeds the declared budget. Repartition; do not raise it."""


def count_rows(path: Path) -> int:
    """How many rows a shard holds, without materialising any of them."""
    total = 0
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 22), b""):
            total += chunk.count(b"\n")
    return total


def _read_shard(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def _read_shard_bounded(path: Path, limit: int) -> list[dict[str, Any]]:
    """Read at most `limit` rows, then refuse.

    The counted pre-check is the first line; this is the second. A stale count --
    a shard rewritten between the scan and the read -- or simply a wrong one would
    otherwise sail past the budget, and the allocation that exhausts memory would
    already have happened.
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


def aggregate(
    a_dir: str | Path,
    b_dir: str | Path,
    shard_count: int,
    *,
    threshold: float,
    discordance: float = DEFAULT_DISCORDANCE_PKI,
    excluded_locators: frozenset[str] = frozenset(),
    a_label: str = "a",
    b_label: str = "b",
    pair_detail_path: str | Path | None = None,
    budget: int = MAX_SHARD_OBSERVATIONS,
) -> KiAggregation:
    """Aggregate every pair-shard, streaming pair detail and keeping summaries.

    The budget is applied **before** either shard file is materialised, from a
    counted scan, and the reads that follow are themselves bounded.
    """
    a_dir, b_dir = Path(a_dir), Path(b_dir)
    agg = KiAggregation(shard_count=shard_count, threshold=threshold, discordance=discordance)
    started = time.time()
    with ExitStack() as stack:
        sink = (
            stack.enter_context(Path(pair_detail_path).open("w", encoding="utf-8"))
            if pair_detail_path
            else None
        )
        for shard in range(shard_count):
            a_path = a_dir / f"{a_label}-{shard:04d}.jsonl"
            b_path = b_dir / f"{b_label}-{shard:04d}.jsonl"
            # Budget first, from a counted byte scan, while nothing is resident.
            declared = count_rows(a_path) + count_rows(b_path)
            if declared > budget:
                msg = (
                    f"pair-shard {shard} holds {declared:,} observations, over the declared "
                    f"budget of {budget:,}. Repartition at a larger shard count; do not raise "
                    f"the budget to fit a shard measured as too large."
                )
                raise ShardTooLarge(msg)
            a_rows = _read_shard_bounded(a_path, budget)
            b_rows = _read_shard_bounded(b_path, budget - len(a_rows))
            agg.shard_observations_max = max(agg.shard_observations_max, len(a_rows) + len(b_rows))
            records: list[dict[str, Any]] | None = [] if sink else None
            result = aggregate_shard(
                a_rows,
                b_rows,
                shard=shard,
                threshold=threshold,
                discordance=discordance,
                excluded_locators=excluded_locators,
                primary=agg.primary,
                sensitivity=agg.sensitivity,
                pair_records=records,
            )
            agg.absorb(result)
            if sink and records:
                for record in records:
                    sink.write(json.dumps(record, sort_keys=True) + "\n")
            del a_rows, b_rows, records, result
    agg.seconds = time.time() - started
    agg.peak_rss_bytes = peak_rss_bytes()
    return agg


def unambiguous_cross_slot_addition_locators(
    detail_dir: str | Path, mtype: str = KI
) -> frozenset[str]:
    """Addition-side locators of unambiguous cross-slot candidates, for one type.

    These are the occurrences the pre-registered sensitivity excludes. Only
    *unambiguous* groups contribute: ambiguous ones are counted and reported but
    never resolved, so no occurrence of theirs is excluded in either arm.
    """
    from seq2lead.asof.sharded import _load_groups

    detail_dir = Path(detail_dir)
    additions = _load_groups(detail_dir / "additions.jsonl")
    removals = _load_groups(detail_dir / "removals.jsonl")
    out: set[str] = set()
    for key in sorted(set(removals) & set(additions)):
        if _value_type(key[2]) != mtype:
            continue
        r_records, a_records = removals[key], additions[key]
        same_slot = {r["slot"] for r in r_records} & {a["slot"] for a in a_records}
        cross_r = [r for r in r_records if r["slot"] not in same_slot]
        cross_a = [a for a in a_records if a["slot"] not in same_slot]
        if len(cross_r) == 1 and len(cross_a) == 1:
            matched = min(int(cross_r[0]["count"]), int(cross_a[0]["count"]))
            # Occurrence-level: take as many addition locators as were matched,
            # in sorted order so the selection is deterministic.
            out.update(sorted(cross_a[0].get("source_locators") or [])[:matched])
    return frozenset(out)
