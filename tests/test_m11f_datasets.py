"""The intended execution path, exercised with mocked fits. Nothing is trained.

A digest function that takes one argument shows a signature is safe; it does not
show that the dataset handed to a fit excluded validation evidence. These tests
run the real loaders and the real orchestration, with the fit and selection
callables replaced by mocks, and then assert on exactly what each received.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from seq2lead.asof.contract import PINNED_SETTINGS, REQUIRED_DIGESTS
from seq2lead.asof.datasets import (
    TRAIN_ROLE,
    VALIDATION_ROLE,
    Dataset,
    DatasetError,
    PreflightError,
    load_dataset,
    prepare_fitting,
    verify_preconditions,
)
from seq2lead.asof.partition import TRAIN, VALIDATION

#: The frozen settings themselves, not a hand-written subset. A local copy would
#: let this suite keep passing after the contract moved under it.
CONFIG = dict(PINNED_SETTINGS)


def bound_inputs(tmp_path: Path) -> tuple[dict[str, str], dict[str, Path]]:
    """A complete pinned input set whose digests match the bytes on disk."""
    root = tmp_path / "bound"
    root.mkdir(parents=True, exist_ok=True)
    digests, artifacts = {}, {}
    for name in sorted(REQUIRED_DIGESTS):
        path = root / f"{name}.bin"
        path.write_bytes(name.encode())
        digests[name] = hashlib.sha256(name.encode()).hexdigest()
        artifacts[name] = path
    return digests, artifacts


def preconditions(tmp_path: Path) -> dict:
    """The precondition keywords `prepare_fitting` now requires."""
    digests, artifacts = bound_inputs(tmp_path)
    return {"expected_digests": digests, "artifacts": artifacts, "config": dict(CONFIG)}


def record(pair, partition, *, regression=True, rmse=True, classification=True, target=7.0):
    return {
        "pair": pair,
        "partition": partition,
        "classification_eligible": classification,
        "regression_eligible": regression,
        "validation_rmse_eligible": rmse,
        "regression_target_pki": target if regression else None,
    }


def membership(tmp_path: Path, records: list[dict]) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    p = tmp_path / "a-membership.jsonl"
    p.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in records), encoding="utf-8")
    return p


# ============================================== what each role actually receives


def test_training_takes_only_pairs_with_a_regression_target(tmp_path) -> None:
    """A decisive censored-only pair has a class label and nothing to regress on."""
    path = membership(
        tmp_path,
        [
            record("C1|S1", TRAIN, target=8.0),
            # decisive censored-only: classification yes, regression no
            record("C2|S1", TRAIN, regression=False, rmse=False),
            record("C3|S1", VALIDATION, target=6.5),
        ],
    )
    train = load_dataset(path, TRAIN_ROLE)
    assert train.pairs == ["C1|S1"]
    assert train.targets == [8.0]
    assert train.excluded_no_regression_target == 1
    assert "C2|S1" not in train.pairs


def test_validation_uses_the_stricter_rmse_rule_not_training_eligibility(tmp_path) -> None:
    """Selecting on pairs the endpoint refuses to score would pick a bad checkpoint."""
    path = membership(
        tmp_path,
        [
            record("C1|S1", VALIDATION, target=7.0),
            # has a regression target but is not RMSE-eligible (e.g. discordant)
            record("C2|S1", VALIDATION, rmse=False, target=5.0),
        ],
    )
    validation = load_dataset(path, VALIDATION_ROLE)
    assert validation.pairs == ["C1|S1"]
    assert validation.excluded_not_rmse_eligible == 1
    # the same row WOULD be admitted by the training rule, which is the point
    as_train = load_dataset(
        membership(tmp_path / "t", [record("C2|S1", TRAIN, rmse=False, target=5.0)]),
        TRAIN_ROLE,
    )
    assert as_train.pairs == ["C2|S1"]


def test_feature_exclusion_removes_a_pair_and_is_counted(tmp_path) -> None:
    path = membership(
        tmp_path,
        [
            record("C1|S1", TRAIN),
            record("C2|S2", TRAIN),
        ],
    )
    ds = load_dataset(path, TRAIN_ROLE, usable_compounds={"C1"}, usable_sequences={"S1"})
    assert ds.pairs == ["C1|S1"]
    assert ds.excluded_missing_feature == 1


def test_pairs_and_targets_stay_aligned(tmp_path) -> None:
    """A target without its pair would train on the wrong row."""
    with pytest.raises(DatasetError, match="pairs but"):
        Dataset(role=TRAIN_ROLE, pairs=["a", "b"], targets=[1.0])


def test_an_unknown_role_is_refused(tmp_path) -> None:
    path = membership(tmp_path, [record("C1|S1", TRAIN)])
    with pytest.raises(DatasetError, match="unknown role"):
        load_dataset(path, "test")


# ============================================ the execution path, with mocked fits


def _datasets(tmp_path):
    path = membership(
        tmp_path,
        [
            record("C1|S1", TRAIN, target=8.0),
            record("C2|S1", TRAIN, target=7.0),
            record("C9|S1", TRAIN, regression=False, rmse=False),
            record("V1|S1", VALIDATION, target=6.0),
            record("V2|S1", VALIDATION, rmse=False, target=4.0),
        ],
    )
    return load_dataset(path, TRAIN_ROLE), load_dataset(path, VALIDATION_ROLE)


def test_only_a_training_evidence_reaches_the_transform_and_the_model(tmp_path) -> None:
    train, validation = _datasets(tmp_path)
    fit_transforms, fit_model, select = MagicMock(), MagicMock(), MagicMock()

    prepare_fitting(
        train,
        validation,
        fit_transforms=fit_transforms,
        fit_model=fit_model,
        select_checkpoint=select,
        **preconditions(tmp_path),
    )

    fit_transforms.assert_called_once()
    (transform_arg,), _ = fit_transforms.call_args
    assert transform_arg.role == TRAIN_ROLE
    assert set(transform_arg.pairs) == {"C1|S1", "C2|S1"}

    fit_model.assert_called_once()
    (model_arg, _transform), _ = fit_model.call_args
    assert model_arg.role == TRAIN_ROLE
    assert set(model_arg.pairs) == {"C1|S1", "C2|S1"}
    # no validation pair reached either
    for arg in (transform_arg, model_arg):
        assert not set(arg.pairs) & set(validation.pairs)


def test_a_validation_supplies_selection_targets_only(tmp_path) -> None:
    train, validation = _datasets(tmp_path)
    fit_transforms, fit_model, select = MagicMock(), MagicMock(), MagicMock()
    prepare_fitting(
        train,
        validation,
        fit_transforms=fit_transforms,
        fit_model=fit_model,
        select_checkpoint=select,
        **preconditions(tmp_path),
    )
    select.assert_called_once()
    (selection_arg, _model), _ = select.call_args
    assert selection_arg.role == VALIDATION_ROLE
    assert selection_arg.pairs == ["V1|S1"], "the non-RMSE-eligible pair must not select"
    # and the selection dataset was never handed to a fit
    for mock in (fit_transforms, fit_model):
        for call in mock.call_args_list:
            assert all(a.role != VALIDATION_ROLE for a in call.args if hasattr(a, "role"))


def test_b_evidence_cannot_enter_either_path(tmp_path) -> None:
    """The loader has no parameter through which B could arrive.

    Checked structurally rather than by hoping: `load_dataset` takes a membership
    file derived from A and a feature allow-list, and nothing else. A B-derived
    record would have to be written into the A membership to appear at all.
    """
    import inspect

    params = set(inspect.signature(load_dataset).parameters)
    assert params == {"membership_path", "role", "usable_compounds", "usable_sequences"}
    prepare_params = set(inspect.signature(prepare_fitting).parameters)
    assert not any("b_" in p or "later" in p or "increment" in p for p in prepare_params)

    # and a membership file holding only A roles yields only A roles
    path = membership(tmp_path, [record("C1|S1", TRAIN), record("V1|S1", VALIDATION)])
    for role in (TRAIN_ROLE, VALIDATION_ROLE):
        ds = load_dataset(path, role)
        assert ds.role == role
        assert len(ds) == 1


def test_train_and_validation_may_not_overlap(tmp_path) -> None:
    train = Dataset(role=TRAIN_ROLE, pairs=["X|Y"], targets=[7.0])
    validation = Dataset(role=VALIDATION_ROLE, pairs=["X|Y"], targets=[7.0])
    with pytest.raises(DatasetError, match="both train and validation"):
        prepare_fitting(
            train,
            validation,
            fit_transforms=MagicMock(),
            fit_model=MagicMock(),
            select_checkpoint=MagicMock(),
            **preconditions(tmp_path),
        )


def test_the_roles_cannot_be_swapped(tmp_path) -> None:
    train, validation = _datasets(tmp_path)
    with pytest.raises(DatasetError, match="fitting dataset must be"):
        prepare_fitting(
            validation,
            train,
            fit_transforms=MagicMock(),
            fit_model=MagicMock(),
            select_checkpoint=MagicMock(),
            **preconditions(tmp_path),
        )


# ============================================ refusals happen before any fitting


def test_an_empty_digest_set_is_refused_not_treated_as_verified(tmp_path) -> None:
    """The defect this closes, as its own test.

    An earlier revision of this file asserted the opposite -- that
    `verify_preconditions(expected_digests={}, ...)` **passes** -- because the
    digest loop had nothing to iterate over and "nothing was checked" read as
    "everything verified". That made the strongest-looking gate in the path a
    no-op for any caller that supplied no digests.
    """
    with pytest.raises(PreflightError, match="an empty digest set is not a verified one"):
        verify_preconditions(expected_digests={}, artifacts={}, config=dict(CONFIG))


def test_an_incomplete_pinned_input_set_is_refused(tmp_path) -> None:
    digests, artifacts = bound_inputs(tmp_path)
    digests.pop("a_membership")
    with pytest.raises(PreflightError, match="missing 'a_membership'"):
        verify_preconditions(expected_digests=digests, artifacts=artifacts, config=dict(CONFIG))


def test_an_unrecognised_bound_input_is_refused_rather_than_ignored(tmp_path) -> None:
    """Silently ignoring an extra input would let a run claim a binding it never checked."""
    digests, artifacts = bound_inputs(tmp_path)
    digests["b_membership"] = "0" * 64
    with pytest.raises(PreflightError, match="unrecognised 'b_membership'"):
        verify_preconditions(expected_digests=digests, artifacts=artifacts, config=dict(CONFIG))


def test_a_wrong_input_digest_refuses_before_fitting(tmp_path) -> None:
    train, validation = _datasets(tmp_path)
    digests, artifacts = bound_inputs(tmp_path)
    digests["a_export"] = "0" * 64
    fit_transforms, fit_model, select = MagicMock(), MagicMock(), MagicMock()

    with pytest.raises(PreflightError, match="refusing before fitting"):
        prepare_fitting(
            train,
            validation,
            fit_transforms=fit_transforms,
            fit_model=fit_model,
            select_checkpoint=select,
            expected_digests=digests,
            artifacts=artifacts,
            config=dict(CONFIG),
        )
    fit_transforms.assert_not_called()
    fit_model.assert_not_called()
    select.assert_not_called()


def test_a_missing_artifact_refuses_before_fitting(tmp_path) -> None:
    train, validation = _datasets(tmp_path)
    digests, artifacts = bound_inputs(tmp_path)
    artifacts.pop("a_export")
    fit_model = MagicMock()
    with pytest.raises(PreflightError, match="no artifact supplied"):
        prepare_fitting(
            train,
            validation,
            fit_transforms=MagicMock(),
            fit_model=fit_model,
            select_checkpoint=MagicMock(),
            expected_digests=digests,
            artifacts=artifacts,
            config=dict(CONFIG),
        )
    fit_model.assert_not_called()


@pytest.mark.parametrize(
    ("mutate", "match"),
    [
        ({"threshold_pki": 7.0}, r"threshold_pki=7\.0"),
        ({"run_seeds": []}, r"run_seeds=\[\]"),
        ({"projection_dim": 256}, "projection_dim=256"),
        ({"metric_version": "m8/v1"}, "metric_version='m8/v1'"),
        ({"discordance_pki": 0.5}, "discordance_pki=0.5"),
        ({"checkpoint_selection_metric": "validation_loss"}, "checkpoint_selection_metric="),
        ({"retrieval_built": True}, "retrieval_built=True"),
        ({"evaluation_evidence_display_enabled": True}, "evaluation_evidence_display_enabled="),
        ({"feature_stored_dims": {"ecfp4": 2048, "esm2": 1280}}, "feature_stored_dims="),
        ({"projection_dim": None}, "projection_dim=None"),
    ],
)
def test_a_changed_setting_refuses_before_fitting(tmp_path, mutate, match) -> None:
    """Each departure is caught by **value**, not by the key merely existing.

    `projection_dim` and `metric_version` are the two the earlier checker got
    wrong: it required the keys and never compared them, so a run at dim 256
    under metric version m8/v1 passed and produced numbers that cannot be put
    beside M8/M9's.
    """
    train, validation = _datasets(tmp_path)
    digests, artifacts = bound_inputs(tmp_path)
    fit_model = MagicMock()
    with pytest.raises(PreflightError, match=match):
        prepare_fitting(
            train,
            validation,
            fit_transforms=MagicMock(),
            fit_model=fit_model,
            select_checkpoint=MagicMock(),
            expected_digests=digests,
            artifacts=artifacts,
            config={**CONFIG, **mutate},
        )
    fit_model.assert_not_called()


@pytest.mark.parametrize("dropped", sorted(PINNED_SETTINGS))
def test_every_pinned_setting_is_required(tmp_path, dropped) -> None:
    """Dropping any one declared setting refuses. No key is optional."""
    train, validation = _datasets(tmp_path)
    digests, artifacts = bound_inputs(tmp_path)
    fit_model = MagicMock()
    with pytest.raises(PreflightError, match=f"missing '{dropped}'"):
        prepare_fitting(
            train,
            validation,
            fit_transforms=MagicMock(),
            fit_model=fit_model,
            select_checkpoint=MagicMock(),
            expected_digests=digests,
            artifacts=artifacts,
            config={k: v for k, v in CONFIG.items() if k != dropped},
        )
    fit_model.assert_not_called()


def test_the_preconditions_cannot_be_omitted(tmp_path) -> None:
    """They were keyword arguments defaulting to None, so omitting them skipped every check."""
    import inspect

    sig = inspect.signature(prepare_fitting)
    for name in ("expected_digests", "artifacts", "config"):
        assert sig.parameters[name].default is inspect.Parameter.empty, (
            f"{name} has a default again, so a caller can reach the fit unchecked"
        )
    train, validation = _datasets(tmp_path)
    with pytest.raises(TypeError, match="expected_digests"):
        prepare_fitting(
            train,
            validation,
            fit_transforms=MagicMock(),
            fit_model=MagicMock(),
            select_checkpoint=MagicMock(),
        )


def test_matching_digests_and_the_frozen_settings_let_the_path_proceed(tmp_path) -> None:
    train, validation = _datasets(tmp_path)
    fit_model = MagicMock()
    out = prepare_fitting(
        train,
        validation,
        fit_transforms=MagicMock(),
        fit_model=fit_model,
        select_checkpoint=MagicMock(),
        **preconditions(tmp_path),
    )
    fit_model.assert_called_once()
    assert out["transform_fitted_on"] == TRAIN_ROLE
    assert out["model_fitted_on"] == TRAIN_ROLE
    assert out["checkpoint_selected_on"] == VALIDATION_ROLE


# ================================================== the real datasets on this corpus


@pytest.mark.skipif(
    not Path("data/asof/m11f/a-membership.jsonl").exists(),
    reason="the materialised A membership is not present",
)
def test_the_real_datasets_separate_the_two_eligibilities() -> None:
    W = Path("data/asof/m11f")
    train = load_dataset(W / "a-membership.jsonl", TRAIN_ROLE)
    validation = load_dataset(W / "a-membership.jsonl", VALIDATION_ROLE)
    assert len(train) == 353_957
    assert len(validation) == 60_981
    # the censored-only pairs are excluded and counted, not silently dropped
    assert train.excluded_no_regression_target == 55_653
    assert validation.excluded_not_rmse_eligible == 1_722
    assert not set(train.pairs) & set(validation.pairs)
