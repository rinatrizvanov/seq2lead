"""The three-way as-of partition, materialised from snapshot A alone.

Training and validation are both carved out of **A**, because model selection
needs held-out data that is still historical: selecting on B's increment would
tune the model on the thing being evaluated, and selecting on the training
evidence would not be held out at all.

Three properties this module is built around:

* **B is unavailable on this path.** `build_partition` reads the A shards and
  nothing else. The as-of claim is that training evidence is what an analyst
  standing at `T_train` would have had, and the cheapest way to keep that true is
  a function that cannot see B.
* **Whole pairs move together, by construction.** The assignment is a hash of the
  pair key itself, so every observation of a pair lands in the same partition
  without any grouping step that could be got wrong.
* **The assignment is reproducible from the recorded configuration.** A seeded
  cryptographic hash, not `random` and not `hash()`: the first is stateful and the
  second is per-process randomised, and either would make the partition
  irreproducible from `(fraction, seed)` alone.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from seq2lead.asof.evaluation import (
    DEFAULT_DISCORDANCE_PKI,
    PairEvidence,
    exact_stats,
    training_label,
)
from seq2lead.asof.ki_aggregation import KI, _is_type, _training_row
from seq2lead.asof.matching import observation_of

if TYPE_CHECKING:
    from collections.abc import Iterator

#: Bumped when the assignment rule or the recorded fields change.
PARTITION_VERSION = "m11f/partition/v1"

#: The declared validation fraction. Pinned here rather than passed at the call
#: site so that a run cannot quietly use a different one than the contract says.
VALIDATION_FRACTION = 0.15

#: The declared seed. A string, hashed with the pair key -- an integer seed fed to
#: `random` would make the assignment depend on call order.
PARTITION_SEED = "m11f-partition-v1"

#: The hash behind the assignment, named so a partition can be reproduced.
PARTITION_HASH = "blake2b-64"

TRAIN, VALIDATION = "train", "validation"

#: A decisive classification label. Both exact points and censored bounds can
#: produce one -- a `> 10000 nM` record puts the whole admissible range below
#: pKi 6.0 and is decisively inactive.
CLASSIFICATION_STATUS_OK = "ok"
CLASSIFICATION_LABELS = frozenset({"active", "inactive"})


class InvalidPartitionConfig(ValueError):
    """The declared fraction or seed cannot produce a reproducible partition."""


def validate_config(fraction: float, seed: str) -> None:
    """Refuse a configuration that cannot be reproduced or cannot partition."""
    if not isinstance(fraction, (int, float)) or isinstance(fraction, bool):
        msg = f"validation fraction must be a number, got {fraction!r}"
        raise InvalidPartitionConfig(msg)
    if not 0.0 <= float(fraction) <= 1.0:
        msg = f"validation fraction must lie in [0, 1], got {fraction!r}"
        raise InvalidPartitionConfig(msg)
    if not isinstance(seed, str) or not seed:
        msg = f"seed must be a non-empty string, got {seed!r}"
        raise InvalidPartitionConfig(msg)


def pair_partition(
    inchikey: str,
    sequence_sha256: str,
    *,
    fraction: float = VALIDATION_FRACTION,
    seed: str = PARTITION_SEED,
) -> str:
    """Which partition a pair belongs to. Deterministic, seeded, pair-keyed.

    Uniform in [0, 1) from the leading 8 bytes of a keyed digest, so the realised
    fraction converges on `fraction` without a global shuffle or a sort. The
    comparison is strict `<`, which makes `fraction=0` send everything to train
    and `fraction=1` send everything to validation.
    """
    validate_config(fraction, seed)
    body = f"{seed}\x00{inchikey}\x00{sequence_sha256}".encode()
    digest = hashlib.blake2b(body, digest_size=8).digest()
    draw = int.from_bytes(digest, "big") / float(1 << 64)
    return VALIDATION if draw < fraction else TRAIN


def classification_eligible(status: str, label: str) -> bool:
    """Whether A's evidence gives this pair a decisive class label.

    Censored-only evidence can satisfy this: a decisive bound is a real class
    label, which is what `censored_used_for_ranking: true` declares.
    """
    return status == CLASSIFICATION_STATUS_OK and label in CLASSIFICATION_LABELS


def regression_target(measurements: list[Any]) -> float | None:
    """The pair's exact-only regression target, or `None` if it has none.

    **The established endpoint rule, not a new one.** `pair_regression` is built
    from exact, positive, finite `=` records only, and the target is the median
    of their pKi values (`endpoint/build._regression_row`). A censoring boundary
    is not a point estimate and is never substituted for one: `exact_stats`
    ignores non-exact measurements, so a censored-only pair returns `None` here
    while still carrying a classification label.

    This is the distinction an earlier revision collapsed. It counted decisive
    censored-only pairs as supplied to fitting, while the contract declares
    exact-only pKi regression with MSE -- so those pairs had no target to
    regress on and could not have been fitted at all.
    """
    return exact_stats(measurements).median


def regression_eligible(measurements: list[Any]) -> bool:
    """Whether the pair can supply a regression target at all."""
    return regression_target(measurements) is not None


def validation_rmse_eligible(
    measurements: list[Any], status: str, discordance: float = DEFAULT_DISCORDANCE_PKI
) -> bool:
    """Whether the pair may contribute to validation RMSE.

    **Not the same as training eligibility, and deliberately not derived from
    it.** Checkpoint selection is an evaluation, so it uses the endpoint's own
    evaluation rule: `_regression_row` marks a pair `eval_excluded` when its
    label status is not `ok` or when its exact values are discordant. Reusing
    training eligibility here would select checkpoints on pairs the endpoint
    itself refuses to score, including replicate exacts four logs apart.
    """
    if not regression_eligible(measurements):
        return False
    if status != CLASSIFICATION_STATUS_OK:
        return False
    return not exact_stats(measurements).is_discordant(discordance)


@dataclass
class PartitionSummary:
    """Counts and digests for the partition. Bounded: no per-pair lists retained."""

    threshold: float
    fraction: float
    seed: str
    shard_count: int
    a_observations: int = 0
    a_ki_observations: int = 0
    pairs: int = 0
    by_partition: Counter[str] = field(default_factory=Counter)
    classification_eligible_by_partition: Counter[str] = field(default_factory=Counter)
    regression_eligible_by_partition: Counter[str] = field(default_factory=Counter)
    validation_rmse_eligible_by_partition: Counter[str] = field(default_factory=Counter)
    censored_only_decisive_by_partition: Counter[str] = field(default_factory=Counter)
    label_by_partition: dict[str, Counter[str]] = field(default_factory=dict)
    status_by_partition: dict[str, Counter[str]] = field(default_factory=dict)
    discordant_by_partition: Counter[str] = field(default_factory=Counter)
    observations_by_partition: Counter[str] = field(default_factory=Counter)
    #: Per-partition digest over the canonical A evidence of its eligible pairs.
    evidence_digests: dict[str, str] = field(default_factory=dict)
    #: Per-partition digest over membership alone, so a later run can prove it
    #: produced the same split without re-deriving the evidence.
    membership_digests: dict[str, str] = field(default_factory=dict)
    seconds: float = 0.0

    @property
    def realised_validation_fraction(self) -> float | None:
        return self.by_partition[VALIDATION] / self.pairs if self.pairs else None

    @property
    def fitted_pairs(self) -> int:
        """What model fitting actually receives: train pairs WITH a regression target.

        The declared objective is exact-only pKi regression with MSE, so a pair
        with no exact measurement has nothing to regress on however decisive its
        censored evidence is.
        """
        return self.regression_eligible_by_partition[TRAIN]

    def as_dict(self) -> dict[str, Any]:
        return {
            "partition_version": PARTITION_VERSION,
            "partition_hash": PARTITION_HASH,
            "endpoint": KI,
            "threshold_pki": self.threshold,
            "declared_validation_fraction": self.fraction,
            "seed": self.seed,
            "realised_validation_fraction": (
                round(self.realised_validation_fraction, 6)
                if self.realised_validation_fraction is not None
                else None
            ),
            "b_was_unavailable_on_this_path": True,
            "shard_count": self.shard_count,
            "a_observations_read": self.a_observations,
            "a_ki_observations": self.a_ki_observations,
            "pairs_with_ki_evidence_in_a": self.pairs,
            "pairs_by_partition": dict(sorted(self.by_partition.items())),
            "observations_by_partition": dict(sorted(self.observations_by_partition.items())),
            "eligibility": {
                "note": (
                    "three different questions, kept apart. An earlier revision used the "
                    "classification figure as 'supplied to fitting', which overcounted: the "
                    "declared objective is exact-only pKi regression with MSE, so a decisive "
                    "censored-only pair has a class label and no regression target."
                ),
                "classification_by_partition": dict(
                    sorted(self.classification_eligible_by_partition.items())
                ),
                "regression_by_partition": dict(
                    sorted(self.regression_eligible_by_partition.items())
                ),
                "validation_rmse_by_partition": dict(
                    sorted(self.validation_rmse_eligible_by_partition.items())
                ),
                "censored_only_decisive_by_partition": dict(
                    sorted(self.censored_only_decisive_by_partition.items())
                ),
                "classification_rule": "A-only status `ok` and label in {active, inactive}",
                "regression_rule": (
                    "median of the pair's exact pKi values, the established "
                    "`pair_regression` rule. Exact, positive, finite `=` records only; a "
                    "censoring boundary is never substituted for a point."
                ),
                "validation_rmse_rule": (
                    "regression-eligible AND label status `ok` AND not discordant -- the "
                    "endpoint's own `eval_excluded` rule. NOT derived from training "
                    "eligibility: selecting checkpoints on pairs the endpoint refuses to "
                    "score would pick a checkpoint on evidence that disagrees with itself."
                ),
                "censored_evidence_retained_for": "ranking eligibility and audit",
            },
            "discordance_treatment": {
                "training": (
                    "discordant pairs REMAIN available for training. M4 leaves them available "
                    "and denies them only scoring standing (docs/M11.md §7a (h)); excluding "
                    "them here would be a different rule than the endpoint declares."
                ),
                "validation_rmse": (
                    "discordant pairs are EXCLUDED from checkpoint selection, because selection "
                    "is an evaluation and the endpoint's own eval rule refuses them. Replicate "
                    "exacts four logs apart cannot arbitrate which checkpoint is better."
                ),
                "discordant_regression_eligible_by_partition": dict(
                    sorted(self.discordant_by_partition.items())
                ),
            },
            "pairs_supplied_to_model_fitting": self.fitted_pairs,
            "pairs_supplied_to_model_fitting_definition": (
                "train-partition pairs with a regression target"
            ),
            "evidence_support_digests": dict(sorted(self.evidence_digests.items())),
            "membership_digests": dict(sorted(self.membership_digests.items())),
            "seconds": round(self.seconds, 1),
        }


def iter_a_pairs(
    a_dir: str | Path,
    shard_count: int,
    *,
    threshold: float,
    fraction: float = VALIDATION_FRACTION,
    seed: str = PARTITION_SEED,
    discordance: float = DEFAULT_DISCORDANCE_PKI,
    label: str = "a",
    mtype: str = KI,
) -> Iterator[dict[str, Any]]:
    """Stream A's pairs with their A-only reading. Reads the A shards and nothing else.

    `fraction` and `seed` are threaded through to every assignment. An earlier
    revision accepted them on `build_partition` and then let this function use the
    module defaults, so a caller asking for a different split silently got the
    default one -- the parameters were honoured in the record and ignored in the
    assignment.
    """
    from seq2lead.asof.bridge import to_measurement

    validate_config(fraction, seed)
    a_dir = Path(a_dir)
    for shard in range(shard_count):
        by_pair: dict[tuple[str, str], list] = {}
        path = a_dir / f"{label}-{shard:04d}.jsonl"
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                row = json.loads(line)
                if not _is_type(row, mtype):
                    continue
                obs = observation_of(row)
                m = to_measurement(obs)
                if m is None:
                    continue
                key = (obs.slot.inchikey, obs.slot.sequence_sha256)
                by_pair.setdefault(key, []).append(m)
        for (inchikey, sequence), measurements in by_pair.items():
            # in_b is left EMPTY on purpose: nothing on this path may read B.
            evidence = PairEvidence(pair=f"{inchikey}|{sequence}", in_a=measurements, in_b=[])
            status, lab = training_label(evidence, threshold)
            stats = exact_stats(measurements)
            target = regression_target(measurements)
            yield {
                "pair": evidence.pair,
                "inchikey": inchikey,
                "sequence_sha256": sequence,
                "shard": shard,
                "partition": pair_partition(inchikey, sequence, fraction=fraction, seed=seed),
                "n_in_a": len(measurements),
                "n_exact": stats.n,
                "n_censored": len(measurements) - stats.n,
                "status": status,
                "label": lab,
                # three separate eligibilities, because they answer three questions
                "classification_eligible": classification_eligible(status, lab),
                "regression_eligible": target is not None,
                "regression_target_pki": target,
                "validation_rmse_eligible": validation_rmse_eligible(
                    measurements, status, discordance
                ),
                "is_discordant": stats.is_discordant(discordance),
                "exact": stats.to_dict(),
                "evidence_row": _training_row(evidence, threshold, discordance),
            }
        del by_pair


def build_partition(
    a_dir: str | Path,
    shard_count: int,
    *,
    threshold: float,
    membership_path: str | Path | None = None,
    fraction: float = VALIDATION_FRACTION,
    seed: str = PARTITION_SEED,
) -> PartitionSummary:
    """Materialise the train/validation partition within A and record its digests."""
    validate_config(fraction, seed)
    summary = PartitionSummary(
        threshold=threshold, fraction=fraction, seed=seed, shard_count=shard_count
    )
    evidence_rows: dict[str, list[str]] = {TRAIN: [], VALIDATION: []}
    membership_rows: dict[str, list[str]] = {TRAIN: [], VALIDATION: []}
    started = time.time()
    sink = Path(membership_path).open("w", encoding="utf-8") if membership_path else None
    try:
        for record in iter_a_pairs(
            a_dir, shard_count, threshold=threshold, fraction=fraction, seed=seed
        ):
            part = record["partition"]
            summary.pairs += 1
            summary.by_partition[part] += 1
            summary.observations_by_partition[part] += record["n_in_a"]
            summary.label_by_partition.setdefault(part, Counter())[record["label"]] += 1
            summary.status_by_partition.setdefault(part, Counter())[record["status"]] += 1
            if record["classification_eligible"]:
                summary.classification_eligible_by_partition[part] += 1
            if record["validation_rmse_eligible"]:
                summary.validation_rmse_eligible_by_partition[part] += 1
            if record["classification_eligible"] and not record["regression_eligible"]:
                # a decisive bound and no exact: a class label, no regression target
                summary.censored_only_decisive_by_partition[part] += 1
            if record["regression_eligible"]:
                summary.regression_eligible_by_partition[part] += 1
                # Evidence support covers the pairs that actually reach fitting or
                # selection; a pair with no regression target contributes none.
                evidence_rows[part].append(record["evidence_row"])
                if record["is_discordant"]:
                    summary.discordant_by_partition[part] += 1
            membership_rows[part].append(
                f"{record['pair']}\t{int(record['classification_eligible'])}"
                f"\t{int(record['regression_eligible'])}"
            )
            if sink:
                sink.write(json.dumps(record, sort_keys=True) + "\n")
    finally:
        if sink:
            sink.close()
    for part in (TRAIN, VALIDATION):
        summary.evidence_digests[part] = hashlib.sha256(
            "\n".join(sorted(evidence_rows[part])).encode("utf-8")
        ).hexdigest()
        summary.membership_digests[part] = hashlib.sha256(
            "\n".join(sorted(membership_rows[part])).encode("utf-8")
        ).hexdigest()
    summary.seconds = time.time() - started
    return summary


# ===================================== strata, relative to what fitting receives

#: A cohort pair's relation to the pairs actually supplied to model fitting.
#:
#: **Recurrence is defined against the fitted set, not against historical presence
#: in A.** Those differ, and the difference is not small: a pair can have A
#: evidence and still never reach fitting, by being reserved for validation or by
#: having A evidence that yields no usable label.
RECURRENT = "recurrent"
NEW_ABSENT_FROM_A = "new_absent_from_a"
NEW_RESERVED_FOR_VALIDATION = "new_reserved_for_validation"
NEW_A_PRESENT_NOT_FITTED = "new_a_present_excluded_from_fitting"

NEW_TO_FITTING = (
    NEW_ABSENT_FROM_A,
    NEW_RESERVED_FOR_VALIDATION,
    NEW_A_PRESENT_NOT_FITTED,
)

#: Why the validation subgroup cannot be called untouched.
VALIDATION_EXPOSURE_NOTE = (
    "A pair reserved for validation is NOT untouched by model selection. Its A evidence "
    "is what the declared checkpoint-selection rule reads, so the selected checkpoint is "
    "a function of it. The pair is new to *fitting* and exposed to *selection*, and a "
    "result on it is weaker evidence than a result on a pair absent from A entirely."
)


@dataclass(frozen=True)
class Membership:
    """A pair's place in the A partition. `None` means the pair is absent from A.

    Both eligibilities are carried because they answer different questions and an
    earlier revision carried only one: `classification_eligible` says the pair has
    a decisive class label, `regression_eligible` says it has an exact-only
    regression target. Fitting receives the second.
    """

    partition: str
    classification_eligible: bool
    regression_eligible: bool


def stratify(membership: Membership | None) -> str:
    """Which stratum a cohort pair belongs to, relative to what fitting receives.

    `recurrent` means the pair is in the set the **model-facing training loader
    emits** -- train partition *and* a regression target. A pair with a decisive
    censored-only label is not in that set, because the declared objective has
    nothing to regress it on.
    """
    if membership is None:
        return NEW_ABSENT_FROM_A
    if membership.partition == VALIDATION:
        # Its own subgroup regardless of eligibility: validation exposure is what
        # distinguishes it, and an ineligible validation pair is still a pair the
        # selection rule could have read.
        return NEW_RESERVED_FOR_VALIDATION
    if membership.regression_eligible:
        return RECURRENT
    return NEW_A_PRESENT_NOT_FITTED


def load_membership(path: str | Path) -> dict[str, Membership]:
    """Read the materialised A membership. One entry per pair with A evidence."""
    out: dict[str, Membership] = {}
    with Path(path).open(encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            rec = json.loads(line)
            out[rec["pair"]] = Membership(
                partition=rec["partition"],
                classification_eligible=bool(rec["classification_eligible"]),
                regression_eligible=bool(rec["regression_eligible"]),
            )
    return out
