"""The frozen contract, in one place so two callers cannot disagree about it.

`verify_preconditions` and the production runner both have to know what the
pinned input set and the declared settings are. Holding those values in either
module would let the other drift from them, which is how the defect this closes
arose: the checker knew that `projection_dim` and `metric_version` had to
*exist* and never knew what they had to *be*.

These values are read from `configs/m11f_evaluation_contract.json` and asserted
against it by a test, so the constants here and the published contract cannot
part company silently.
"""

from __future__ import annotations

from typing import Any

#: Bumped when the required input set or any pinned value changes.
CONTRACT_VERSION = "m11f-asof-evaluation-contract-v5"

#: Every input a run is bound to, named without the `_sha256` suffix the
#: contract file uses. A run that cannot name all of these is refused: a result
#: whose inputs are not all pinned is not reproducible, and an empty set is not
#: a verified one.
REQUIRED_DIGESTS = frozenset(
    {
        "a_export",
        "b_export",
        "a_membership",
        "partition_summary",
        "cross_slot_exclusion_set",
        "sensitivity_spec",
        "cohort_preflight",
        "feature_binding",
        "feature_coverage",
        "feature_resolution",
        "feature_extension",
        "model_facing_datasets",
        "evaluation_entities",
        "m9_config",
    }
)

#: Every declared execution setting with the value it must equal. Checked by
#: comparison, never by presence.
PINNED_SETTINGS: dict[str, Any] = {
    "threshold_pki": 6.0,
    "discordance_pki": 1.0,
    "projection_dim": 512,
    "metric_version": "m8/v2",
    "feature_stored_dims": {"ecfp4": 256, "esm2": 1280},
    "validation_fraction": 0.15,
    "partition_seed": "m11f-partition-v1",
    "run_seeds": [20260930, 20260931, 20260932, 20260933, 20260934],
    "learning_rate": 0.001,
    "batch_size": 512,
    "max_epochs": 20,
    "early_stopping_patience": 5,
    "optimizer": "adam",
    "objective_loss": "mse",
    "objective_target": "pair_regression.p_median",
    "censored_as_regression_target": False,
    "checkpoint_selection_metric": "validation_rmse",
    "checkpoint_selection_direction": "minimise",
    "primary_increment_arm": "declared_increment",
    "primary_consistency_branch": "screened_primary",
    "retrieval_built": False,
    "evaluation_evidence_display_enabled": False,
}

#: Stored widths, not logical ones. ECFP4's 2048 bits live in 256 packed bytes,
#: so a check against 2048 calls every vector in the accepted cache unusable.
STORED_DIMS = {"ecfp4": 256, "esm2": 1280}

#: Which bound artifact supplies each thing the run consumes. Nothing the runner
#: consumes may arrive as a caller parameter beside a digest that does not govern
#: it: verifying `a_membership` while loading an independently supplied path made
#: that verification decorative, and a swapped reuse map changed every vector
#: while leaving storage digests and coverage valid.
DERIVED_FROM = {
    "membership": "a_membership",
    "feature_caches": "feature_binding",
    "reuse_maps": "feature_resolution",
    "evaluation_entities": "evaluation_entities",
    "emitted_datasets": "model_facing_datasets",
}


class ContractError(RuntimeError):
    """A declared setting or bound input departs from the frozen contract."""


def check_settings(config: dict[str, Any]) -> list[str]:
    """Return every way `config` departs from the pinned settings. Empty is clean."""
    faults = [f"missing {key!r}" for key in sorted(PINNED_SETTINGS.keys() - config.keys())]
    faults += [
        f"{key}={config[key]!r} (frozen: {want!r})"
        for key, want in sorted(PINNED_SETTINGS.items())
        if key in config and config[key] != want
    ]
    return faults


def check_digest_set(expected: dict[str, str]) -> list[str]:
    """Return every way the bound input set departs from the required one."""
    if not expected:
        return [
            "no input digests were supplied; an empty digest set is not a verified one "
            f"({len(REQUIRED_DIGESTS)} pinned inputs are required)"
        ]
    faults = [f"missing {name!r}" for name in sorted(REQUIRED_DIGESTS - expected.keys())]
    faults += [f"unrecognised {name!r}" for name in sorted(expected.keys() - REQUIRED_DIGESTS)]
    return faults
