"""Follow-up review of the M9 corrections.

Each test here covers a gap the first correction pass left: a validator that was
never wired in, a spec that was checked only by digest, a sidecar that belonged
to no particular checkpoint, and a preflight nothing proved the runner called.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

torch = pytest.importorskip("torch")

from seq2lead.models.binding import (  # noqa: E402
    BindingError,
    FeatureBinding,
    ProteinSpec,
    load_binding,
    protein_spec_from_params,
    read_sidecar,
    sidecar_path,
    write_sidecar,
)

CHECKPOINTS = Path("data/m9/final")


def _spec(**overrides) -> ProteinSpec:
    base = {
        "model": "facebook/esm2_t33_650M_UR50D",
        "model_revision": "08e4846e537177426273712802403f7ba8261b6c",
        "pooling": "mean_over_residues_excluding_special_tokens",
        "dtype": "float32",
        "length_policy": "full",
        "max_length": 40000,
        "training_window": 1022,
    }
    base.update(overrides)
    return ProteinSpec(**base)


def _binding(**overrides) -> FeatureBinding:
    base = {
        "binding_version": "m9-binding-v1",
        "compound_cache": "ecfp4-compound-42351e003acb",
        "compound_manifest_sha256": "a" * 64,
        "compound_storage_sha256": "b" * 64,
        "protein_cache": "esm2-target-48cfa09487ce",
        "protein_manifest_sha256": "c" * 64,
        "protein_storage_sha256": "d" * 64,
        "protein": _spec(),
        "experiment": "m9-dual-encoder-v1",
        "config_sha256": "e" * 64,
    }
    base.update(overrides)
    return FeatureBinding(**base)


# ============================================ 1. selection validation is strict


def _config(dims=(256, 512), splits=("random_pair-v3",)):
    class Split:
        def __init__(self, name):
            self.name = name

    class Config:
        version = "m9-dual-encoder-v1"
        config_sha256 = "a" * 64
        search = {"projection_dim": list(dims)}

    cfg = Config()
    cfg.splits = tuple(Split(s) for s in splits)
    return cfg


def _selection(**overrides) -> dict:
    base = {
        "experiment": "m9-dual-encoder-v1",
        "config_sha256": "a" * 64,
        "declared_splits": ["random_pair-v3"],
        "declared_projection_dims": [256, 512],
        "chosen": {"random_pair-v3": 512},
    }
    base.update(overrides)
    return base


def test_a_valid_selection_passes_with_no_legacy_notes() -> None:
    from seq2lead.models.m9_artifacts import check_selection_binding

    assert check_selection_binding(_selection(), _config(), (256, 512)) == []


@pytest.mark.parametrize("field", ["config_sha256", "declared_splits", "declared_projection_dims"])
def test_missing_binding_fields_are_rejected_not_skipped(field) -> None:
    """Absent used to mean "no constraint", switching the check off silently."""
    from seq2lead.models.m9_artifacts import check_selection_binding

    for empty in (None, "", []):
        with pytest.raises(ValueError, match="missing"):
            check_selection_binding(_selection(**{field: empty}), _config(), (256, 512))


@pytest.mark.parametrize("field", ["config_sha256", "declared_splits", "declared_projection_dims"])
def test_legacy_recovery_is_explicit_and_recorded(field) -> None:
    from seq2lead.models.m9_artifacts import check_selection_binding

    notes = check_selection_binding(
        _selection(**{field: None}), _config(), (256, 512), allow_legacy=True
    )
    assert notes and field in notes[0]
    assert "legacy" in notes[0].lower()


def test_chosen_must_cover_exactly_the_declared_splits() -> None:
    from seq2lead.models.m9_artifacts import check_selection_binding

    config = _config(splits=("random_pair-v3", "cold_protein-v3"))
    declared = ["random_pair-v3", "cold_protein-v3"]

    with pytest.raises(ValueError, match="missing"):
        check_selection_binding(
            _selection(declared_splits=declared, chosen={"random_pair-v3": 512}),
            config,
            (256, 512),
        )
    with pytest.raises(ValueError, match="unexpected"):
        check_selection_binding(
            _selection(
                declared_splits=declared,
                chosen={**dict.fromkeys(declared, 512), "chemistry_disjoint-v3": 512},
            ),
            config,
            (256, 512),
        )
    assert (
        check_selection_binding(
            _selection(declared_splits=declared, chosen=dict.fromkeys(declared, 512)),
            config,
            (256, 512),
        )
        == []
    )


def test_chosen_dimensions_must_have_been_searched() -> None:
    from seq2lead.models.m9_artifacts import check_selection_binding

    with pytest.raises(ValueError, match="never searched"):
        check_selection_binding(_selection(chosen={"random_pair-v3": 1024}), _config(), (256, 512))


def test_an_empty_chosen_map_is_rejected() -> None:
    from seq2lead.models.m9_artifacts import check_selection_binding

    with pytest.raises(ValueError, match="no chosen configuration"):
        check_selection_binding(_selection(chosen={}), _config(), (256, 512))


@pytest.mark.requires_db
def test_the_real_frozen_selection_passes_the_strict_check() -> None:
    from dataclasses import asdict

    from seq2lead.db import connect
    from seq2lead.eval import load_experiment
    from seq2lead.models.m9_artifacts import check_selection_binding
    from seq2lead.models.m9_runner import SELECTION_PATH, read_selection

    if not SELECTION_PATH.exists():
        pytest.skip("no frozen selection")
    with connect() as conn:
        config = load_experiment(conn, "m9-dual-encoder-v1")
    dims = tuple(config.search["projection_dim"])
    assert check_selection_binding(asdict(read_selection()), config, dims) == []


# ================================================= 2. protein representation


@pytest.mark.parametrize(
    "field", ["model", "model_revision", "pooling", "dtype", "length_policy", "max_length"]
)
def test_a_missing_protein_param_is_refused_not_defaulted(field) -> None:
    """Falling back to module constants describes today's settings, not the cache's."""
    params = {
        "model": "m",
        "model_revision": "r",
        "pooling": "p",
        "dtype": "float32",
        "length_policy": "full",
        "max_length": 40000,
        "extra": {"training_window": 1022},
    }
    params[field] = None
    with pytest.raises(BindingError, match="records no"):
        protein_spec_from_params("some-cache", params)


def test_a_missing_training_window_is_refused() -> None:
    with pytest.raises(BindingError, match="training_window"):
        protein_spec_from_params(
            "some-cache",
            {
                "model": "m",
                "model_revision": "r",
                "pooling": "p",
                "dtype": "float32",
                "length_policy": "full",
                "max_length": 40000,
            },
        )


@pytest.mark.requires_db
def test_correct_digests_do_not_excuse_a_different_query_representation() -> None:
    """Digests prove the vectors; the spec decides how a NEW query is embedded.

    A binding with the right cache bytes but a different pooling rule would score
    the library correctly and embed the query wrongly, so it is refused before
    ESM-2 loads.
    """
    import dataclasses

    from seq2lead.db import connect
    from seq2lead.models.binding import verify

    with connect() as conn:
        good = load_binding(sorted(CHECKPOINTS.glob("*.pt"))[0])
        assert verify(conn, good) == []

        for field, value in (
            ("pooling", "cls_token"),
            ("model_revision", "0" * 40),
            ("dtype", "float16"),
            ("length_policy", "truncate:1022"),
            ("max_length", 1022),
        ):
            drifted = dataclasses.replace(
                good, protein=dataclasses.replace(good.protein, **{field: value})
            )
            with pytest.raises(BindingError, match="does not match what the"):
                verify(conn, drifted)


# ===================================================== 3. sidecar ownership


@pytest.mark.requires_db
def test_a_sidecar_copied_beside_another_checkpoint_is_refused(tmp_path) -> None:
    """Without the digest a sidecar is just a file with a matching name."""
    checkpoints = sorted(CHECKPOINTS.glob("*.pt"))
    if len(checkpoints) < 2:  # noqa: PLR2004
        pytest.skip("need two checkpoints")
    first, second = checkpoints[0], checkpoints[1]

    impostor = tmp_path / second.name
    shutil.copy(second, impostor)
    shutil.copy(sidecar_path(first), sidecar_path(impostor))  # first's sidecar, second's bytes

    with pytest.raises(BindingError, match="was written for a checkpoint with digest"):
        load_binding(impostor)


@pytest.mark.requires_db
def test_every_recovered_sidecar_is_bound_to_its_own_checkpoint() -> None:
    from seq2lead.features.store import sha256_file

    checkpoints = sorted(CHECKPOINTS.glob("*.pt"))
    assert checkpoints
    for path in checkpoints:
        _binding_obj, envelope = read_sidecar(path)
        assert envelope["checkpoint_sha256"] == sha256_file(path)
        assert envelope["checkpoint_name"] == path.name
        assert "RECOVERED" in envelope["provenance"].upper()


def test_an_identical_sidecar_rewrite_is_a_no_op(tmp_path) -> None:
    checkpoint = tmp_path / "model.pt"
    checkpoint.write_bytes(b"weights")
    binding = _binding()
    first = write_sidecar(checkpoint, binding)
    body = first.read_bytes()
    write_sidecar(checkpoint, binding)
    assert first.read_bytes() == body


def test_a_conflicting_sidecar_rewrite_is_refused(tmp_path) -> None:
    checkpoint = tmp_path / "model.pt"
    checkpoint.write_bytes(b"weights")
    write_sidecar(checkpoint, _binding(compound_cache="original"))
    before = sidecar_path(checkpoint).read_bytes()
    with pytest.raises(BindingError, match="already exists with different content"):
        write_sidecar(checkpoint, _binding(compound_cache="replacement"))
    assert sidecar_path(checkpoint).read_bytes() == before


def test_a_sidecar_from_a_different_run_is_refused(tmp_path) -> None:
    """Digest matches, but the experiment identity does not."""
    checkpoint = tmp_path / "model.pt"
    checkpoint.write_bytes(b"weights")
    write_sidecar(checkpoint, _binding(experiment="some-other-experiment"))
    with pytest.raises(BindingError, match="describes a different run"):
        load_binding(checkpoint, {"experiment": "m9-dual-encoder-v1"})


def test_a_pre_envelope_sidecar_is_refused(tmp_path) -> None:
    """The old format carried no checkpoint digest, so it proves nothing."""
    checkpoint = tmp_path / "model.pt"
    checkpoint.write_bytes(b"weights")
    sidecar_path(checkpoint).write_text(json.dumps(_binding().to_dict()), encoding="utf-8")
    with pytest.raises(BindingError, match="predates the sidecar envelope"):
        load_binding(checkpoint)


# ======================================= 4. the runner actually calls preflight


@pytest.mark.requires_db
def test_the_runner_refuses_before_fitting_when_a_destination_is_occupied(
    tmp_path, monkeypatch
) -> None:
    """The unit test called `preflight()` directly; nothing proved the runner did.

    Occupy the third planned run's checkpoint, mock the fit so any call is an
    error, and assert the refusal happens with the existing artifact untouched.
    """
    from seq2lead.db import connect
    from seq2lead.eval import load_experiment
    from seq2lead.eval.features import FeatureBank
    from seq2lead.models import m9_runner
    from seq2lead.models.binding import from_experiment
    from seq2lead.models.m9_artifacts import ArtifactCollision, run_paths
    from seq2lead.models.m9_runner import SelectionRecord, score_test

    with connect() as conn:
        config = load_experiment(conn, "m9-dual-encoder-v1")
        bank = FeatureBank.load(conn, config)
        # score_test() now requires a binding, and checks it before preflighting.
        # Supply a real one so this test still reaches the collision it is about.
        binding = from_experiment(conn, config)

        seeds = (1, 2, 3)
        split = config.splits[0].name
        selection = SelectionRecord(
            experiment=config.version,
            config_sha256=config.config_sha256,
            declared_splits=[split],
            declared_projection_dims=[512],
            metric="validation_rmse",
            direction="minimise",
            min_validation_pairs=0,
            min_validation_targets=0,
            seed=1,
            chosen={split: 512},
        )

        occupied = run_paths(
            "final",
            m9_runner.MODEL_NAME,
            split,
            seeds[2],
            run_root=tmp_path / "m9",
            prediction_root=tmp_path / "pred",
        ).checkpoint
        occupied.parent.mkdir(parents=True, exist_ok=True)
        occupied.write_bytes(b"an existing run")
        before = occupied.read_bytes()

        def no_fitting(*_args, **_kwargs):
            raise AssertionError("score_test fitted a model despite the collision")

        monkeypatch.setattr(m9_runner, "fit", no_fitting)

        # Only this split is in the selection, so only its runs are planned.
        import dataclasses

        trimmed = dataclasses.replace(
            config, splits=tuple(s for s in config.splits if s.name == split)
        )

        with pytest.raises(ArtifactCollision, match="Refusing before any fitting"):
            score_test(
                conn,
                trimmed,
                bank,
                selection,
                seeds,
                run_root=tmp_path / "m9",
                prediction_dir=tmp_path / "pred",
                binding=binding,
            )

    assert occupied.read_bytes() == before, "the occupied artifact was modified"
    survivors = sorted(p.name for p in (tmp_path / "m9" / "final").iterdir())
    assert survivors == [occupied.name], f"other artifacts were created: {survivors}"


# ==================================== the superseded bound cache stays usable


@pytest.mark.requires_db
def test_a_superseded_bound_cache_is_still_loadable(tmp_path) -> None:
    """`verify()` says the checkpoint keeps using the cache it was trained on.

    `load_features()` refuses superseded caches by default, which made that
    promise unreachable. The identity is already verified by digest, so ranking
    passes `allow_superseded=True`.
    """
    from seq2lead.db import connect, transaction
    from seq2lead.features.store import FeatureCacheError, load_features
    from seq2lead.models.binding import verify

    binding = load_binding(sorted(CHECKPOINTS.glob("*.pt"))[0])
    with transaction() as conn:
        conn.execute(
            "UPDATE feature_version SET superseded_by=%s WHERE name=%s",
            ("pretend-newer", binding.compound_cache),
        )
        notes = verify(conn, binding)
        assert any("superseded" in n for n in notes)
        with pytest.raises(FeatureCacheError, match="superseded"):
            load_features(conn, binding.compound_cache)
        ids, _vectors = load_features(conn, binding.compound_cache, allow_superseded=True)
        assert ids.shape[0] > 0
        conn.rollback()

    with connect() as conn:
        still = conn.execute(
            "SELECT superseded_by FROM feature_version WHERE name=%s",
            (binding.compound_cache,),
        ).fetchone()[0]
    assert still is None, "the rollback did not restore the cache row"


def test_ranking_asks_for_the_superseded_cache_explicitly() -> None:
    source = Path("src/seq2lead/models/rank.py").read_text(encoding="utf-8")
    assert "allow_superseded=True" in source
