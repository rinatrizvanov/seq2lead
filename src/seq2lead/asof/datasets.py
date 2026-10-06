"""Model-facing datasets for the as-of evaluation. Built from snapshot A alone.

This is the path a fit would actually take, so it is the path the leakage
guarantees have to be proved on. A digest function that takes one argument shows
a *signature* is safe; it does not show that the dataset handed to a fit excluded
validation evidence. This module is what decides that.

Three separations it enforces, and each is exercised with a mocked fit rather
than argued:

* **Regression targets come from A-train only.** The declared objective is
  exact-only pKi regression with MSE, so a pair with no exact measurement has no
  target -- however decisive its censored evidence. Fitted transforms see the
  same rows and no others.
* **A-validation supplies checkpoint-selection targets only.** It never reaches a
  fitted transform or a model parameter.
* **B never enters either.** The loader takes a membership file derived from A and
  has no parameter through which B could arrive.

Nothing here fits anything. `prepare_fitting` takes the fit and selection
callables from its caller, which is what lets a test pass mocks and inspect
exactly what each would have received.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from seq2lead.asof.contract import check_digest_set, check_settings
from seq2lead.asof.partition import TRAIN, VALIDATION

#: Bumped when the emitted dataset's contents or shape change.
DATASET_VERSION = "m11f/datasets/v1"

TRAIN_ROLE, VALIDATION_ROLE = TRAIN, VALIDATION


class DatasetError(RuntimeError):
    """The dataset cannot be built as specified."""


class PreflightError(RuntimeError):
    """A precondition failed. Raised BEFORE any fit or artifact publication."""


@dataclass
class Dataset:
    """Pairs and their regression targets, for one role.

    `pairs` and `targets` are positionally aligned, and the alignment is
    asserted rather than assumed -- a target without a pair would silently train
    on the wrong row.
    """

    role: str
    pairs: list[str] = field(default_factory=list)
    targets: list[float] = field(default_factory=list)
    compounds: set[str] = field(default_factory=set)
    sequences: set[str] = field(default_factory=set)
    excluded_no_regression_target: int = 0
    excluded_not_rmse_eligible: int = 0
    excluded_missing_feature: int = 0

    def __post_init__(self) -> None:
        if len(self.pairs) != len(self.targets):
            msg = f"{self.role}: {len(self.pairs)} pairs but {len(self.targets)} targets"
            raise DatasetError(msg)

    def __len__(self) -> int:
        return len(self.pairs)

    def digest(self) -> str:
        """Stable digest of exactly what this dataset would hand a fit."""
        body = "\n".join(
            f"{p}\t{t!r}" for p, t in sorted(zip(self.pairs, self.targets, strict=True))
        )
        return hashlib.sha256(body.encode("utf-8")).hexdigest()

    def as_dict(self) -> dict[str, Any]:
        return {
            "dataset_version": DATASET_VERSION,
            "role": self.role,
            "pairs": len(self.pairs),
            "distinct_compounds": len(self.compounds),
            "distinct_sequences": len(self.sequences),
            "digest": self.digest(),
            "excluded": {
                "no_regression_target": self.excluded_no_regression_target,
                "not_validation_rmse_eligible": self.excluded_not_rmse_eligible,
                "missing_or_unusable_feature": self.excluded_missing_feature,
            },
        }


def load_dataset(
    membership_path: str | Path,
    role: str,
    *,
    usable_compounds: set[str] | None = None,
    usable_sequences: set[str] | None = None,
) -> Dataset:
    """Emit the rows a fit or a selection would receive for `role`.

    `train` takes pairs with a regression target. `validation` takes the stricter
    `validation_rmse_eligible` set -- the endpoint's own evaluation rule -- rather
    than reusing training eligibility, because selecting a checkpoint on pairs the
    endpoint refuses to score would pick one on evidence that disagrees with
    itself.
    """
    if role not in (TRAIN_ROLE, VALIDATION_ROLE):
        msg = f"unknown role {role!r}; expected {TRAIN_ROLE!r} or {VALIDATION_ROLE!r}"
        raise DatasetError(msg)
    dataset = Dataset(role=role)
    with Path(membership_path).open(encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            rec = json.loads(line)
            if rec["partition"] != role:
                continue
            if not rec["regression_eligible"]:
                dataset.excluded_no_regression_target += 1
                continue
            if role == VALIDATION_ROLE and not rec["validation_rmse_eligible"]:
                dataset.excluded_not_rmse_eligible += 1
                continue
            compound, sequence = rec["pair"].split("|", 1)
            if (usable_compounds is not None and compound not in usable_compounds) or (
                usable_sequences is not None and sequence not in usable_sequences
            ):
                dataset.excluded_missing_feature += 1
                continue
            target = rec["regression_target_pki"]
            if target is None:
                # regression_eligible said otherwise; refuse rather than coerce.
                msg = f"{rec['pair']}: regression_eligible but no target"
                raise DatasetError(msg)
            dataset.pairs.append(rec["pair"])
            dataset.targets.append(float(target))
            dataset.compounds.add(compound)
            dataset.sequences.add(sequence)
    Dataset.__post_init__(dataset)
    return dataset


def verify_preconditions(
    *,
    expected_digests: dict[str, str],
    artifacts: dict[str, str | Path],
    config: dict[str, Any],
) -> None:
    """Refuse before any fit if an input, a digest or a setting is wrong.

    Called first, so a failure costs nothing and cannot leave a half-published
    artifact. Every check is a refusal, not a warning.

    Three earlier holes are closed here. An **empty** digest set used to pass,
    because the loop had nothing to iterate -- "nothing was checked" read as
    "everything verified". And `projection_dim` and `metric_version` were
    required to exist without being required to hold the frozen value, so a run
    at dim 256 under a different metric version satisfied the check while
    producing numbers that cannot be compared to M8/M9. The pinned values now
    come from `contract.py`, which a test holds against the published contract
    file.
    """
    faults = check_digest_set(expected_digests)
    if faults:
        msg = "precondition: the bound input set is wrong -- " + "; ".join(faults)
        raise PreflightError(msg)
    for name, expected in sorted(expected_digests.items()):
        path = artifacts.get(name)
        if path is None:
            msg = f"precondition: no artifact supplied for {name!r}"
            raise PreflightError(msg)
        actual = hashlib.sha256(Path(path).read_bytes()).hexdigest()
        if actual != expected:
            msg = (
                f"precondition: {name} digest {actual[:16]}\u2026 does not match the bound "
                f"{expected[:16]}\u2026; refusing before fitting"
            )
            raise PreflightError(msg)
    faults = check_settings(config)
    if faults:
        msg = (
            "precondition: configuration departs from the frozen contract -- "
            + "; ".join(faults)
        )
        raise PreflightError(msg)


def prepare_fitting(
    train: Dataset,
    validation: Dataset,
    *,
    fit_transforms: Callable[[Dataset], Any],
    fit_model: Callable[[Dataset, Any], Any],
    select_checkpoint: Callable[[Dataset, Any], Any],
    expected_digests: dict[str, str],
    artifacts: dict[str, str | Path],
    config: dict[str, Any],
) -> dict[str, Any]:
    """Run the intended execution path, with the fit supplied by the caller.

    The callables are parameters so a test can pass mocks and assert exactly what
    each received. The preconditions are **required** arguments: they defaulted to
    `None` and were skipped when omitted, so a caller could reach the fit without
    a single input having been checked. There is no longer a way to call this
    without declaring what the run is bound to.

    Order is load-bearing: preconditions are verified first, then
    transforms are fitted on **train only**, then the model on train, and only
    then is validation used -- for selection and nothing else.
    """
    verify_preconditions(
        expected_digests=expected_digests, artifacts=artifacts, config=config
    )
    if train.role != TRAIN_ROLE:
        msg = f"the fitting dataset must be {TRAIN_ROLE!r}, got {train.role!r}"
        raise DatasetError(msg)
    if validation.role != VALIDATION_ROLE:
        msg = f"the selection dataset must be {VALIDATION_ROLE!r}, got {validation.role!r}"
        raise DatasetError(msg)
    overlap = set(train.pairs) & set(validation.pairs)
    if overlap:
        msg = f"{len(overlap)} pairs appear in both train and validation: {sorted(overlap)[:5]}"
        raise DatasetError(msg)

    transform = fit_transforms(train)
    model = fit_model(train, transform)
    selection = select_checkpoint(validation, model)
    return {
        "train": train.as_dict(),
        "validation": validation.as_dict(),
        "transform_fitted_on": TRAIN_ROLE,
        "model_fitted_on": TRAIN_ROLE,
        "checkpoint_selected_on": VALIDATION_ROLE,
        "selection": selection,
    }
