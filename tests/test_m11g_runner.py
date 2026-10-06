"""The production entry point, exercised with fitting mocked. Nothing is trained.

Everything the run consumes is **derived** from the bound artifact set, so a
mutation has to go through an artifact to reach the run at all. That is the point
of the design and it is what these tests exercise: there is no `membership_path`,
no `feature_sources` and no `roles` parameter to substitute.

Three holes closed here, each with its reproduction kept as a test:

* a verified `a_membership` while an independently supplied path was loaded;
* a caller-supplied `reuse_map`, where swapping two entries repointed those
  vectors while every storage digest, the coverage report and the emitted
  dataset digest stayed **identical**;
* caller-supplied `roles`, where dropping the evaluation role skipped its
  coverage check and shrinking the train role turned fitting rows into an
  exclusion count.

Several refusal tests leave the membership file holding unparseable JSON with a
matching digest. A gate that fires raises `PreflightError`; a skipped gate
reaches the derivation and raises `JSONDecodeError`, so "refused early" is a
measurement rather than a convention.
"""

from __future__ import annotations

import hashlib
import inspect
import json
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pytest

from seq2lead.asof.contract import (
    DERIVED_FROM,
    PINNED_SETTINGS,
    REQUIRED_DIGESTS,
    STORED_DIMS,
)
from seq2lead.asof.datasets import TRAIN_ROLE, VALIDATION_ROLE, PreflightError, load_dataset
from seq2lead.asof.partition import TRAIN, VALIDATION
from seq2lead.asof.runner import EVALUATION_ROLE, derive_roles, run

CONFIG = dict(PINNED_SETTINGS)

TRAIN_COMPOUNDS = ("CMPDAAAAAAAAAA-AAAAAAAAAA-N", "CMPDBBBBBBBBBB-BBBBBBBBBB-N")
VALIDATION_COMPOUNDS = ("CMPDCCCCCCCCCC-CCCCCCCCCC-N",)
SEQUENCES = ("a" * 64,)
#: Present in the accepted cache and the extension, but in no role.
SPARE_COMPOUND = "CMPDDDDDDDDDDD-DDDDDDDDDD-N"


def sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def vector(kind: str, dim: int, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    if kind == "ecfp4":
        return rng.integers(0, 256, size=dim, dtype=np.uint8)
    return rng.standard_normal(dim).astype(np.float32)


def write_json(path: Path, body) -> Path:
    path.write_text(json.dumps(body, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return path


class Harness:
    """A valid invocation whose every input is a file a test can rewrite."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

        # --- feature stores -------------------------------------------------
        cached = (*TRAIN_COMPOUNDS, SPARE_COMPOUND)
        ids = np.arange(1, len(cached) + 1, dtype=np.int64)
        self.ecfp4_accepted = write_npz(
            self.root / "ecfp4-accepted.npz", ids=ids,
            vectors=np.stack([vector("ecfp4", 256, n) for n in range(len(cached))]),
        )
        self.esm2_accepted = write_npz(
            self.root / "esm2-accepted.npz", ids=np.array([1], dtype=np.int64),
            vectors=np.stack([vector("esm2", 1280, 0)]),
        )
        self.ecfp4_extension = write_npz(
            self.root / "ecfp4-extension.npz",
            keys=np.asarray(VALIDATION_COMPOUNDS, dtype=object),
            vectors=np.stack([vector("ecfp4", 256, 900)]),
        )
        self.esm2_extension = write_npz(
            self.root / "esm2-extension.npz", keys=np.asarray([], dtype=object),
            vectors=np.zeros((0, 1280), dtype=np.float32),
        )

        # --- reuse maps, each its own file with its own digest ---------------
        self.reuse_paths = {
            "ecfp4": write_json(
                self.root / "reuse-compounds.json",
                {k: int(i) for k, i in zip(cached, ids, strict=True)},
            ),
            "esm2": write_json(self.root / "reuse-targets.json", {SEQUENCES[0]: 1}),
        }

        # --- membership -----------------------------------------------------
        rows = [
            (TRAIN_COMPOUNDS[0], TRAIN, True, True),
            (TRAIN_COMPOUNDS[1], TRAIN, True, True),
            (VALIDATION_COMPOUNDS[0], VALIDATION, True, True),
            # a decisive censored-only validation pair: no regression target, so
            # the loader excludes it and the derived role must not request it
            (SPARE_COMPOUND, VALIDATION, False, False),
        ]
        self.membership = self.root / "a-membership.jsonl"
        self.write_membership(rows)

        # --- the bound set --------------------------------------------------
        self.stub_dir = self.root / "bound"
        self.stub_dir.mkdir(exist_ok=True)
        self.artifacts: dict[str, Path] = {}
        for name in sorted(REQUIRED_DIGESTS):
            if name in self.special():
                continue
            path = self.stub_dir / f"{name}.bin"
            path.write_bytes(name.encode())
            self.artifacts[name] = path
        self.write_binding()
        self.write_resolution()
        self.write_evaluation_entities()
        self.artifacts["a_membership"] = self.membership
        self.write_pinned_datasets()
        self.refresh()

        self.config = dict(CONFIG)
        self.fit_transforms = MagicMock(name="fit_transforms")
        self.fit_model = MagicMock(name="fit_model")
        self.select_checkpoint = MagicMock(name="select_checkpoint")

    @staticmethod
    def special() -> set[str]:
        return {
            "a_membership", "feature_binding", "feature_resolution",
            "evaluation_entities", "model_facing_datasets",
        }

    def write_membership(self, rows) -> None:
        records = [
            {
                "pair": f"{c}|{SEQUENCES[0]}", "partition": p,
                "classification_eligible": True,
                "regression_eligible": regression,
                "validation_rmse_eligible": rmse,
                "regression_target_pki": 7.0 + n if regression else None,
            }
            for n, (c, p, regression, rmse) in enumerate(rows)
        ]
        self.membership.write_text(
            "".join(json.dumps(r, sort_keys=True) + "\n" for r in records), encoding="utf-8"
        )

    def write_binding(self, **override) -> None:
        sources = {
            kind: {
                "accepted_path": str(getattr(self, f"{kind}_accepted")),
                "accepted_sha256": sha256(getattr(self, f"{kind}_accepted")),
                "extension_path": str(getattr(self, f"{kind}_extension")),
                "extension_sha256": sha256(getattr(self, f"{kind}_extension")),
                "reuse_map_entries": len(json.loads(self.reuse_paths[kind].read_text())),
                "stored_dim": STORED_DIMS[kind],
                **override.get(kind, {}),
            }
            for kind in sorted(STORED_DIMS)
        }
        self.artifacts["feature_binding"] = write_json(
            self.root / "feature-binding.json",
            {"binding": "test", "sources": override.get("sources", sources)},
        )

    def write_resolution(self, **override) -> None:
        maps = {
            kind: {
                "path": str(path),
                "sha256": sha256(path),
                "entries": len(json.loads(path.read_text())),
                **override.get(kind, {}),
            }
            for kind, path in sorted(self.reuse_paths.items())
        }
        self.artifacts["feature_resolution"] = write_json(
            self.root / "resolution.json",
            {"resolution": "test", "reuse_maps": override.get("reuse_maps", maps)},
        )

    def write_evaluation_entities(self, **override) -> None:
        body = {
            "artifact": "test",
            "compounds": sorted({*TRAIN_COMPOUNDS, *VALIDATION_COMPOUNDS}),
            "sequences": sorted(SEQUENCES),
            **override,
        }
        self.artifacts["evaluation_entities"] = write_json(
            self.root / "evaluation-entities.json", body
        )

    def write_pinned_datasets(self, **override) -> None:
        """The pinned record, computed from what the loader actually emits."""
        roles = derive_roles(self.artifacts)
        body = {}
        for role in (TRAIN_ROLE, VALIDATION_ROLE):
            ds = load_dataset(
                self.membership, role,
                usable_compounds=roles[role].compounds,
                usable_sequences=roles[role].sequences,
            )
            body[role] = {
                "pairs": len(ds),
                "distinct_compounds": len(ds.compounds),
                "distinct_sequences": len(ds.sequences),
                "digest": ds.digest(),
                **override.get(role, {}),
            }
        self.artifacts["model_facing_datasets"] = write_json(
            self.root / "model-facing-datasets.json", body
        )

    def refresh(self) -> None:
        """Recompute the pinned digest set from the files as they now stand."""
        self.digests = {name: sha256(p) for name, p in self.artifacts.items()}

    def run(self, **overrides):
        kwargs = {
            "expected_digests": self.digests,
            "artifacts": dict(self.artifacts),
            "config": self.config,
            "fit_transforms": self.fit_transforms,
            "fit_model": self.fit_model,
            "select_checkpoint": self.select_checkpoint,
        }
        return run(**{**kwargs, **overrides})

    def assert_nothing_fitted(self) -> None:
        self.fit_transforms.assert_not_called()
        self.fit_model.assert_not_called()
        self.select_checkpoint.assert_not_called()

    def corrupt_membership_json(self) -> None:
        """Unparseable membership, digest refreshed: a skipped gate fails differently."""
        self.membership.write_text("{not json\n", encoding="utf-8")
        self.refresh()


def write_npz(path: Path, **arrays) -> Path:
    np.savez(path, **arrays)
    return path


@pytest.fixture
def harness(tmp_path) -> Harness:
    return Harness(tmp_path / "work")


# =================================================== valid inputs reach the fit


def test_valid_inputs_reach_transforms_and_the_model_from_a_train_only(harness) -> None:
    out = harness.run()

    harness.fit_transforms.assert_called_once()
    (transform_arg,), _ = harness.fit_transforms.call_args
    assert transform_arg.role == TRAIN_ROLE
    assert set(transform_arg.pairs) == {f"{c}|{SEQUENCES[0]}" for c in TRAIN_COMPOUNDS}

    harness.fit_model.assert_called_once()
    (model_arg, transform), _ = harness.fit_model.call_args
    assert model_arg.role == TRAIN_ROLE
    assert transform is harness.fit_transforms.return_value
    assert set(model_arg.pairs) == set(transform_arg.pairs)

    validation_pair = f"{VALIDATION_COMPOUNDS[0]}|{SEQUENCES[0]}"
    for arg in (transform_arg, model_arg):
        assert validation_pair not in arg.pairs
    assert out["transform_fitted_on"] == TRAIN_ROLE
    assert out["model_fitted_on"] == TRAIN_ROLE


def test_a_validation_is_used_for_checkpoint_selection_and_nothing_else(harness) -> None:
    out = harness.run()
    harness.select_checkpoint.assert_called_once()
    (selection_arg, model), _ = harness.select_checkpoint.call_args
    assert selection_arg.role == VALIDATION_ROLE
    assert selection_arg.pairs == [f"{VALIDATION_COMPOUNDS[0]}|{SEQUENCES[0]}"]
    assert model is harness.fit_model.return_value
    assert out["checkpoint_selected_on"] == VALIDATION_ROLE
    for mock in (harness.fit_transforms, harness.fit_model):
        for call in mock.call_args_list:
            assert all(a.role != VALIDATION_ROLE for a in call.args if hasattr(a, "role"))


def test_the_record_names_what_it_derived_and_what_it_verified(harness) -> None:
    out = harness.run()
    assert set(out["inputs_verified"]) == set(REQUIRED_DIGESTS)
    assert out["derived_from"] == dict(sorted(DERIVED_FROM.items()))
    assert set(out["roles_derived"]) == {TRAIN_ROLE, VALIDATION_ROLE, EVALUATION_ROLE}
    assert out["features"]["reuse_maps_verified"]["ecfp4"]["entries"] == 3
    assert out["emitted_matches_pinned"][TRAIN_ROLE]["pairs"] == 2


# ========================================== 1. membership substitution is closed


def test_there_is_no_membership_path_to_substitute(harness) -> None:
    """The reproduction: a verified artifact beside an independently loaded path.

    `run` verified `artifacts['a_membership']` and loaded `membership_path`, so a
    substituted file changed the fitted rows and their regression targets with
    every gate reporting success. The parameter is gone, so the substitution has
    nowhere to enter.
    """
    params = set(inspect.signature(run).parameters)
    assert "membership_path" not in params
    assert params == {
        "expected_digests", "artifacts", "config",
        "fit_transforms", "fit_model", "select_checkpoint",
    }
    out = harness.run()
    assert out["derived_from"]["membership"] == "a_membership"


def test_the_membership_is_re_verified_at_the_moment_of_consumption(
    harness, monkeypatch
) -> None:
    """A digest checked at the gate does not protect a file that changed since.

    The file has to change *between* the gate and the read for this to mean
    anything, so the swap is done from inside the run, after the role derivation
    the runner performs between those two points. Changing it beforehand only
    exercises the gate, which an earlier version of this test did.
    """
    import seq2lead.asof.runner as runner_mod

    real = runner_mod.derive_roles

    def derive_then_swap(artifacts):
        roles = real(artifacts)
        rows = harness.membership.read_text().splitlines()
        harness.membership.write_text(rows[0] + "\n" + rows[2] + "\n", encoding="utf-8")
        return roles

    monkeypatch.setattr(runner_mod, "derive_roles", derive_then_swap)
    with pytest.raises(PreflightError, match="changed between verification and use"):
        harness.run()
    harness.assert_nothing_fitted()


def test_a_substituted_membership_with_a_refreshed_digest_still_refuses(harness) -> None:
    """Rewriting the file AND its digest changes what is emitted, which is pinned."""
    harness.write_membership([
        (TRAIN_COMPOUNDS[0], TRAIN, True, True),
        (VALIDATION_COMPOUNDS[0], VALIDATION, True, True),
    ])
    harness.refresh()
    with pytest.raises(PreflightError, match="not the pinned ones"):
        harness.run()
    harness.assert_nothing_fitted()


def test_a_changed_regression_target_refuses(harness) -> None:
    """The targets are what the model learns, so a moved one must not slip through."""
    text = harness.membership.read_text().replace(
        '"regression_target_pki": 7.0', '"regression_target_pki": 9.0'
    )
    harness.membership.write_text(text, encoding="utf-8")
    harness.refresh()
    with pytest.raises(PreflightError, match=r"train\.digest"):
        harness.run()
    harness.assert_nothing_fitted()


# ========================================= 2. feature-map substitution is closed


def test_there_is_no_reuse_map_parameter_to_substitute(harness) -> None:
    params = set(inspect.signature(run).parameters)
    assert "feature_sources" not in params
    out = harness.run()
    assert out["derived_from"]["reuse_maps"] == "feature_resolution"
    assert out["derived_from"]["feature_caches"] == "feature_binding"


def test_a_swapped_reuse_map_refuses(harness) -> None:
    """The reproduction. Swapping two entries left every digest and the coverage
    report identical, and even the emitted dataset digest unchanged, because the
    dataset records pairs and targets rather than vectors."""
    path = harness.reuse_paths["ecfp4"]
    mapping = json.loads(path.read_text())
    a, b = TRAIN_COMPOUNDS
    mapping[a], mapping[b] = mapping[b], mapping[a]
    write_json(path, mapping)
    with pytest.raises(PreflightError, match="does not match the .* recorded in the verified"):
        harness.run()
    harness.assert_nothing_fitted()


def test_a_swapped_map_with_its_resolution_record_refreshed_still_refuses(harness) -> None:
    """Updating the map's recorded digest changes the resolution artifact itself."""
    path = harness.reuse_paths["ecfp4"]
    mapping = json.loads(path.read_text())
    a, b = TRAIN_COMPOUNDS
    mapping[a], mapping[b] = mapping[b], mapping[a]
    write_json(path, mapping)
    harness.write_resolution()  # re-records the new digest
    with pytest.raises(PreflightError, match="feature_resolution digest"):
        harness.run()
    harness.assert_nothing_fitted()


def test_a_swap_really_does_change_the_bound_vectors(harness) -> None:
    """Evidence the case matters: without this, refusing it would be ceremony."""
    from seq2lead.asof.features import load_binding

    path = harness.reuse_paths["ecfp4"]
    mapping = json.loads(path.read_text())

    def bound(m):
        write_json(path, m)
        harness.write_binding()
        harness.write_resolution()
        harness.refresh()
        b = load_binding(
            "ecfp4",
            accepted_path=harness.ecfp4_accepted,
            accepted_sha256=sha256(harness.ecfp4_accepted),
            reuse_map={k: int(v) for k, v in m.items()},
            stored_dim=256,
        )
        return b.vector(TRAIN_COMPOUNDS[0]).copy()

    before = bound(dict(mapping))
    swapped = dict(mapping)
    a, b = TRAIN_COMPOUNDS
    swapped[a], swapped[b] = swapped[b], swapped[a]
    assert not np.array_equal(before, bound(swapped))


def test_a_reuse_map_whose_size_disagrees_with_the_binding_refuses(harness) -> None:
    harness.write_binding(ecfp4={"reuse_map_entries": 99})
    harness.refresh()
    with pytest.raises(PreflightError, match="the verified binding declares 99"):
        harness.run()
    harness.assert_nothing_fitted()


def test_a_reuse_map_whose_size_disagrees_with_the_resolution_refuses(harness) -> None:
    harness.write_resolution(ecfp4={"entries": 99})
    harness.refresh()
    with pytest.raises(PreflightError, match="the verified resolution declares 99"):
        harness.run()
    harness.assert_nothing_fitted()


def test_a_binding_declaring_the_logical_width_refuses(harness) -> None:
    """2048 is the logical fingerprint width; 256 is the stored one."""
    harness.write_binding(ecfp4={"stored_dim": 2048})
    harness.refresh()
    with pytest.raises(PreflightError, match="the binding declares stored_dim 2048"):
        harness.run()
    harness.assert_nothing_fitted()


@pytest.mark.parametrize("missing_kind", sorted(STORED_DIMS))
def test_a_binding_missing_a_feature_kind_refuses(harness, missing_kind) -> None:
    kept = {k: v for k, v in sorted(STORED_DIMS.items()) if k != missing_kind}
    harness.write_binding(sources={k: {"stored_dim": v} for k, v in kept.items()})
    harness.refresh()
    with pytest.raises(PreflightError, match="must declare sources"):
        harness.run()
    harness.assert_nothing_fitted()


def test_a_resolution_missing_a_reuse_map_refuses(harness) -> None:
    harness.write_resolution(reuse_maps={"ecfp4": {}})
    harness.refresh()
    with pytest.raises(PreflightError, match="must record a reuse map per kind"):
        harness.run()
    harness.assert_nothing_fitted()


def test_a_tampered_accepted_cache_refuses(harness) -> None:
    harness.ecfp4_accepted.write_bytes(b"not an npz")
    with pytest.raises(PreflightError, match="accepted cache digest"):
        harness.run()
    harness.assert_nothing_fitted()


def test_a_key_in_both_the_accepted_cache_and_the_extension_refuses(harness) -> None:
    write_npz(
        harness.ecfp4_extension,
        keys=np.asarray([TRAIN_COMPOUNDS[0]], dtype=object),
        vectors=np.stack([vector("ecfp4", 256, 901)]),
    )
    harness.write_binding()
    harness.refresh()
    with pytest.raises(PreflightError, match="provenance is ambiguous"):
        harness.run()
    harness.assert_nothing_fitted()


# ======================================= 3. incomplete role declarations closed


def test_there_is_no_roles_parameter_to_under_declare(harness) -> None:
    """The reproduction: dropping the evaluation role skipped its coverage check,
    and a smaller train role turned fitting rows into an exclusion counter."""
    assert "roles" not in set(inspect.signature(run).parameters)
    out = harness.run()
    assert set(out["roles_derived"]) == {TRAIN_ROLE, VALIDATION_ROLE, EVALUATION_ROLE}


def test_the_derived_role_is_exactly_the_emitted_population(harness) -> None:
    """Wider would make the allow-list useless; narrower would filter silently."""
    roles = derive_roles(harness.artifacts)
    harness.run()
    (train_arg,), _ = harness.fit_transforms.call_args
    (sel_arg, _m), _ = harness.select_checkpoint.call_args
    assert roles[TRAIN_ROLE].compounds == train_arg.compounds
    assert roles[TRAIN_ROLE].sequences == train_arg.sequences
    assert roles[VALIDATION_ROLE].compounds == sel_arg.compounds
    # the censored-only validation pair's compound is excluded from both
    assert SPARE_COMPOUND not in roles[VALIDATION_ROLE].compounds
    assert SPARE_COMPOUND not in sel_arg.compounds


@pytest.mark.parametrize("dropped", ["compounds", "sequences"])
def test_pinned_evaluation_entities_missing_a_key_refuse(harness, dropped) -> None:
    body = json.loads(harness.artifacts["evaluation_entities"].read_text())
    body.pop(dropped)
    write_json(harness.artifacts["evaluation_entities"], body)
    harness.refresh()
    with pytest.raises(PreflightError, match=f"must list '{dropped}'"):
        harness.run()
    harness.assert_nothing_fitted()


@pytest.mark.parametrize("emptied", ["compounds", "sequences"])
def test_an_empty_evaluation_entity_list_refuses(harness, emptied) -> None:
    harness.write_evaluation_entities(**{emptied: []})
    harness.refresh()
    with pytest.raises(PreflightError, match=f"must list '{emptied}'"):
        harness.run()
    harness.assert_nothing_fitted()


def test_an_evaluation_entity_with_no_vector_refuses(harness) -> None:
    """No fit sees the evaluation cohort, and incomplete coverage still refuses."""
    harness.write_evaluation_entities(
        compounds=[*TRAIN_COMPOUNDS, *VALIDATION_COMPOUNDS, "CMPDZZZZZZZZZZ-ZZZZZZZZZZ-N"]
    )
    harness.refresh()
    with pytest.raises(PreflightError, match=r"evaluation/ecfp4: 1 missing"):
        harness.run()
    harness.assert_nothing_fitted()


def test_a_membership_entity_outside_the_reuse_map_refuses(harness) -> None:
    """A train compound with no verified vector refuses rather than being filtered."""
    mapping = json.loads(harness.reuse_paths["ecfp4"].read_text())
    mapping.pop(TRAIN_COMPOUNDS[0])
    write_json(harness.reuse_paths["ecfp4"], mapping)
    harness.write_binding()
    harness.write_resolution()
    harness.refresh()
    with pytest.raises(PreflightError, match=r"train/ecfp4: 1 missing"):
        harness.run()
    harness.assert_nothing_fitted()


# ============================== the emitted datasets must be the pinned ones


@pytest.mark.parametrize(
    ("role", "field", "value"),
    [
        (TRAIN_ROLE, "pairs", 99),
        (TRAIN_ROLE, "digest", "0" * 64),
        (TRAIN_ROLE, "distinct_compounds", 7),
        (VALIDATION_ROLE, "pairs", 99),
        (VALIDATION_ROLE, "digest", "0" * 64),
        (VALIDATION_ROLE, "distinct_sequences", 7),
    ],
)
def test_an_emitted_dataset_that_is_not_the_pinned_one_refuses(
    harness, role, field, value
) -> None:
    harness.write_pinned_datasets(**{role: {field: value}})
    harness.refresh()
    with pytest.raises(PreflightError, match=rf"{role}\.{field}"):
        harness.run()
    harness.assert_nothing_fitted()


@pytest.mark.parametrize("role", [TRAIN_ROLE, VALIDATION_ROLE])
def test_a_pinned_record_with_no_entry_for_a_role_refuses(harness, role) -> None:
    body = json.loads(harness.artifacts["model_facing_datasets"].read_text())
    body.pop(role)
    write_json(harness.artifacts["model_facing_datasets"], body)
    harness.refresh()
    with pytest.raises(PreflightError, match=f"{role}: the pinned record declares no dataset"):
        harness.run()
    harness.assert_nothing_fitted()


# ========================================================== altered inputs refuse


@pytest.mark.parametrize("altered", sorted(REQUIRED_DIGESTS))
def test_altering_any_bound_input_refuses(harness, altered) -> None:
    harness.artifacts[altered].write_bytes(b"tampered")
    with pytest.raises(PreflightError, match=f"{altered} digest"):
        harness.run()
    harness.assert_nothing_fitted()


def test_an_incomplete_pinned_input_set_refuses(harness) -> None:
    harness.corrupt_membership_json()
    harness.digests.pop("feature_extension")
    with pytest.raises(PreflightError, match="missing 'feature_extension'"):
        harness.run()
    harness.assert_nothing_fitted()


def test_an_empty_digest_set_refuses(harness) -> None:
    harness.corrupt_membership_json()
    with pytest.raises(PreflightError, match="an empty digest set is not a verified one"):
        harness.run(expected_digests={})
    harness.assert_nothing_fitted()


def test_a_missing_artifact_refuses(harness) -> None:
    harness.corrupt_membership_json()
    artifacts = dict(harness.artifacts)
    artifacts.pop("a_export")
    with pytest.raises(PreflightError, match="no artifact supplied"):
        harness.run(artifacts=artifacts)
    harness.assert_nothing_fitted()


def test_an_unreadable_bound_input_refuses_rather_than_raising_oserror(harness) -> None:
    harness.corrupt_membership_json()
    artifacts = dict(harness.artifacts)
    artifacts["a_export"] = harness.root / "does-not-exist.bin"
    with pytest.raises(PreflightError, match="could not be read"):
        harness.run(artifacts=artifacts)
    harness.assert_nothing_fitted()


def test_a_bound_input_that_is_not_json_refuses_rather_than_raising(harness) -> None:
    harness.artifacts["feature_binding"].write_text("{not json", encoding="utf-8")
    harness.refresh()
    with pytest.raises(PreflightError, match="could not be read as JSON"):
        harness.run()
    harness.assert_nothing_fitted()


# =================================================== changed configuration refuses


@pytest.mark.parametrize(
    ("mutate", "match"),
    [
        ({"threshold_pki": 7.0}, r"threshold_pki=7\.0"),
        ({"projection_dim": 256}, "projection_dim=256"),
        ({"metric_version": "m8/v1"}, "metric_version='m8/v1'"),
        ({"discordance_pki": 0.5}, "discordance_pki=0.5"),
        ({"validation_fraction": 0.2}, "validation_fraction=0.2"),
        ({"partition_seed": "another-seed"}, "partition_seed="),
        ({"run_seeds": [1]}, r"run_seeds=\[1\]"),
        ({"feature_stored_dims": {"ecfp4": 2048, "esm2": 1280}}, "feature_stored_dims="),
        ({"checkpoint_selection_metric": "validation_loss"}, "checkpoint_selection_metric="),
        ({"primary_consistency_branch": "unscreened_sensitivity"}, "primary_consistency_branch="),
        ({"retrieval_built": True}, "retrieval_built=True"),
        ({"evaluation_evidence_display_enabled": True}, "evaluation_evidence_display_enabled="),
        ({"censored_as_regression_target": True}, "censored_as_regression_target=True"),
    ],
)
def test_a_changed_setting_refuses(harness, mutate, match) -> None:
    harness.corrupt_membership_json()
    with pytest.raises(PreflightError, match=match):
        harness.run(config={**CONFIG, **mutate})
    harness.assert_nothing_fitted()


@pytest.mark.parametrize("dropped", sorted(PINNED_SETTINGS))
def test_every_declared_setting_is_required(harness, dropped) -> None:
    harness.corrupt_membership_json()
    with pytest.raises(PreflightError, match=f"missing '{dropped}'"):
        harness.run(config={k: v for k, v in CONFIG.items() if k != dropped})
    harness.assert_nothing_fitted()


def test_an_empty_configuration_refuses(harness) -> None:
    harness.corrupt_membership_json()
    with pytest.raises(PreflightError, match="departs from the frozen contract"):
        harness.run(config={})
    harness.assert_nothing_fitted()


# ================================================== the gates fire in a fixed order


def test_settings_are_checked_before_inputs_are_even_hashed(harness) -> None:
    """A run is never told its digests are bad when it asked for the wrong experiment."""
    harness.artifacts["a_export"].write_bytes(b"tampered")
    with pytest.raises(PreflightError, match="departs from the frozen contract"):
        harness.run(config={**CONFIG, "projection_dim": 256})
    harness.assert_nothing_fitted()


def test_inputs_are_checked_before_anything_is_derived(harness) -> None:
    """With an unparseable membership, a skipped input gate raises JSONDecodeError."""
    harness.corrupt_membership_json()
    harness.artifacts["a_export"].write_bytes(b"tampered")
    with pytest.raises(PreflightError, match="a_export digest"):
        harness.run()
    harness.assert_nothing_fitted()


def test_a_skipped_gate_would_fail_differently(harness) -> None:
    """The discriminator the ordering tests rest on, asserted directly."""
    harness.corrupt_membership_json()
    with pytest.raises(json.JSONDecodeError):
        harness.run()
    harness.assert_nothing_fitted()


def test_coverage_is_checked_before_the_datasets_are_loaded(harness) -> None:
    harness.write_evaluation_entities(
        compounds=[*TRAIN_COMPOUNDS, *VALIDATION_COMPOUNDS, "CMPDZZZZZZZZZZ-ZZZZZZZZZZ-N"]
    )
    harness.write_pinned_datasets(train={"pairs": 99})
    harness.refresh()
    with pytest.raises(PreflightError, match=r"evaluation/ecfp4: 1 missing"):
        harness.run()
    harness.assert_nothing_fitted()
