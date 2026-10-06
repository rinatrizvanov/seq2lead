"""The M9 correction pass: binding, censoring, artifact safety, length policy.

Each test corresponds to a way inference or a rerun could go wrong while looking
entirely normal: a model scored against a cache it never saw, a decisive
non-binder displayed as a strong one, a rerun that overwrites before it refuses,
or a query silently truncated.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import numpy as np
import pytest

torch = pytest.importorskip("torch")

from seq2lead.models.binding import (  # noqa: E402
    BindingError,
    FeatureBinding,
    ProteinSpec,
    load_binding,
    write_sidecar,
)
from seq2lead.models.rank import SequenceError, format_evidence, validate_sequence  # noqa: E402

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


# ======================================================= 1. feature binding


@pytest.mark.requires_db
def test_every_existing_checkpoint_has_a_recovered_binding() -> None:
    if not CHECKPOINTS.exists():
        pytest.skip("no checkpoints")
    checkpoints = sorted(CHECKPOINTS.glob("*.pt"))
    assert checkpoints
    for path in checkpoints:
        binding = load_binding(path)
        assert binding.compound_cache
        assert binding.protein.model_revision
        assert "RECOVERED" in binding.provenance.upper()


def test_a_checkpoint_without_a_binding_refuses_inference(tmp_path) -> None:
    """No binding means the caches would be 'whatever is current'. That is the bug."""
    orphan = tmp_path / "no_binding.pt"
    orphan.write_bytes(b"not really a checkpoint")
    with pytest.raises(BindingError, match="records no feature binding"):
        load_binding(orphan, extra={})


def test_a_binding_in_the_checkpoint_is_preferred_over_a_sidecar(tmp_path) -> None:
    path = tmp_path / "model.pt"
    path.write_bytes(b"x")
    write_sidecar(path, _binding(compound_cache="from-sidecar"))
    embedded = _binding(compound_cache="from-checkpoint")
    assert load_binding(path, {"feature_binding": embedded.to_dict()}).compound_cache == (
        "from-checkpoint"
    )
    assert load_binding(path).compound_cache == "from-sidecar"


@pytest.mark.requires_db
def test_a_newer_incompatible_cache_does_not_silently_replace_the_bound_one() -> None:
    """The discriminating case.

    A cache with the bound name but different digests is a different population.
    The old model must refuse, not quietly score against it -- and it must never
    fall back to whatever cache is newest.
    """
    from seq2lead.db import connect
    from seq2lead.models.binding import verify

    with connect() as conn:
        binding = load_binding(sorted(CHECKPOINTS.glob("*.pt"))[0])
        assert verify(conn, binding) is not None  # the real one is fine

        drifted = _binding(
            compound_cache=binding.compound_cache,
            compound_manifest_sha256="0" * 64,
            compound_storage_sha256=binding.compound_storage_sha256,
            protein_cache=binding.protein_cache,
            protein_manifest_sha256=binding.protein_manifest_sha256,
            protein_storage_sha256=binding.protein_storage_sha256,
        )
        with pytest.raises(BindingError, match="covers different inputs"):
            verify(conn, drifted)

        absent = _binding(compound_cache="ecfp4-that-never-existed")
        with pytest.raises(BindingError, match="not registered any more"):
            verify(conn, absent)


def test_there_is_no_current_cache_fallback_left() -> None:
    """The fallback that made the substitution possible is gone from the source."""
    source = Path("src/seq2lead/models/rank.py").read_text(encoding="utf-8")
    assert "_current_ecfp" not in source
    assert "binding.compound_cache" in source


@pytest.mark.requires_db
def test_unsupported_recorded_settings_are_refused_not_guessed() -> None:
    from seq2lead.models.rank import embed_sequence

    for overrides, pattern in (
        ({"pooling": "cls_token"}, "does not implement"),
        ({"dtype": "float16"}, "dtype"),
        ({"length_policy": "truncate:1022"}, "length policy"),
    ):
        with pytest.raises(BindingError, match=pattern):
            embed_sequence("MKVLSSAAWQR", _spec(**overrides))


# ======================================================= 2. censored evidence


@pytest.mark.parametrize(
    ("records", "expected"),
    [
        ([{"relation": "=", "value": 5.0, "measurement_type": "KI"}], "1 exact 5 nM"),
        (
            [
                {"relation": "=", "value": 0.2, "measurement_type": "KI"},
                {"relation": "=", "value": 0.7, "measurement_type": "KI"},
            ],
            "2 exact 0.2-0.7 nM",
        ),
        ([{"relation": ">", "value": 10000.0, "measurement_type": "KI"}], ">10000 nM"),
        ([{"relation": ">=", "value": 1000.0, "measurement_type": "KI"}], ">=1000 nM"),
        ([{"relation": "<", "value": 1.0, "measurement_type": "KI"}], "<1 nM"),
        ([{"relation": "<=", "value": 5.0, "measurement_type": "KI"}], "<=5 nM"),
    ],
)
def test_evidence_preserves_the_relation(records, expected) -> None:
    assert format_evidence(records) == expected


def test_a_censored_non_binder_is_never_shown_as_an_exact_value() -> None:
    """The bug: `>10000 nM` -- a decisive non-binder -- displayed as `10000 nM`."""
    rendered = format_evidence([{"relation": ">", "value": 10000.0, "measurement_type": "KI"}])
    assert rendered == ">10000 nM"
    assert not rendered.startswith("1 ")
    assert "exact" not in rendered


def test_mixed_evidence_is_not_collapsed_into_one_range() -> None:
    rendered = format_evidence(
        [
            {"relation": "=", "value": 3.0, "measurement_type": "KI"},
            {"relation": ">", "value": 10000.0, "measurement_type": "KI"},
        ]
    )
    assert "1 exact 3 nM" in rendered
    assert ">10000 nM" in rendered
    assert "3-10000" not in rendered


def test_conflicting_evidence_keeps_both_sides_visible() -> None:
    rendered = format_evidence(
        [
            {"relation": "=", "value": 0.5, "measurement_type": "KI"},
            {"relation": ">", "value": 10000.0, "measurement_type": "KI"},
            {"relation": "=", "value": 9000.0, "measurement_type": "KI"},
        ]
    )
    assert "2 exact 0.5-9000 nM" in rendered
    assert ">10000 nM" in rendered


def test_non_ki_measurement_types_are_labelled() -> None:
    rendered = format_evidence([{"relation": "=", "value": 4.0, "measurement_type": "IC50"}])
    assert rendered.startswith("IC50 ")


@pytest.mark.requires_db
def test_evidence_records_carry_activity_ids_for_traceability() -> None:
    from seq2lead.db import connect
    from seq2lead.models.rank import evidence_records

    with connect() as conn:
        row = conn.execute(
            "SELECT t.sequence, a.compound_id FROM activity a JOIN target t ON t.id=a.target_id "
            "WHERE a.measurement_type='KI' AND a.value_numeric IS NOT NULL LIMIT 1"
        ).fetchone()
        if row is None:
            pytest.skip("no measured activity")
        records = evidence_records(conn, [int(row[1])], str(row[0]))
    assert records
    for entries in records.values():
        for entry in entries:
            assert entry["activity_id"] > 0
            assert entry["relation"]
            assert entry["unit"]


# ================================================== 3. artifact protection


def test_a_rerun_refuses_before_writing_anything(tmp_path) -> None:
    """Refusal used to arrive after every artifact had already been overwritten."""
    from seq2lead.models.m9_artifacts import ArtifactCollision, preflight, run_paths

    runs = [
        run_paths(
            "final",
            "M9-dual-encoder",
            "random_pair-v3",
            seed,
            run_root=tmp_path / "m9",
            prediction_root=tmp_path / "pred",
        )
        for seed in (1, 2, 3)
    ]
    occupied = runs[2].checkpoint
    occupied.parent.mkdir(parents=True, exist_ok=True)
    occupied.write_bytes(b"existing")
    before = occupied.read_bytes()

    with pytest.raises(ArtifactCollision, match="Refusing before any fitting"):
        preflight(runs)
    assert occupied.read_bytes() == before
    assert not runs[0].checkpoint.exists(), "an earlier run's path was created anyway"


def test_run_scoped_paths_do_not_collide_across_labels(tmp_path) -> None:
    from seq2lead.models.m9_artifacts import preflight, run_paths

    first = run_paths(
        "final", "M", "s", 1, run_root=tmp_path / "m9", prediction_root=tmp_path / "p"
    )
    first.checkpoint.parent.mkdir(parents=True, exist_ok=True)
    first.checkpoint.write_bytes(b"x")
    second = run_paths(
        "rerun-2", "M", "s", 1, run_root=tmp_path / "m9", prediction_root=tmp_path / "p"
    )
    preflight([second])  # must not raise


def test_selection_is_bound_to_the_config_digest_not_just_the_name() -> None:
    from seq2lead.models.m9_artifacts import check_selection_binding

    class Config:
        version = "m9-dual-encoder-v1"
        config_sha256 = "a" * 64

        class _S:
            name = "random_pair-v3"

        splits = (_S(),)

    selection = {
        "experiment": "m9-dual-encoder-v1",
        "config_sha256": "a" * 64,
        "declared_splits": ["random_pair-v3"],
        "declared_projection_dims": [256, 512],
        # `chosen` is now required: the follow-up review found the checks were
        # skipped for records that could not prove where they came from.
        "chosen": {"random_pair-v3": 512},
    }
    check_selection_binding(selection, Config(), (256, 512))

    with pytest.raises(ValueError, match="different config file"):
        check_selection_binding({**selection, "config_sha256": "z" * 64}, Config(), (256, 512))
    with pytest.raises(ValueError, match="covered splits"):
        check_selection_binding(
            {**selection, "declared_splits": ["cold_protein-v3"]}, Config(), (256, 512)
        )
    with pytest.raises(ValueError, match="searched projection dims"):
        check_selection_binding(selection, Config(), (128, 256))


@pytest.mark.requires_db
def test_unsupported_search_settings_are_rejected_not_ignored() -> None:
    from seq2lead.db import connect
    from seq2lead.eval import load_experiment
    from seq2lead.models.m9_artifacts import effective_settings

    with connect() as conn:
        config = load_experiment(conn, "m9-dual-encoder-v1")
    settings = effective_settings(config, 512, 1)
    assert settings.projection_dim == 512  # noqa: PLR2004
    assert settings.max_epochs == config.search["max_epochs"]
    assert settings.patience == config.search["early_stopping_patience"]

    import dataclasses

    widened = dataclasses.replace(config, search={**config.search, "momentum": 0.9})
    with pytest.raises(ValueError, match="does not implement"):
        effective_settings(widened, 512, 1)


def test_the_frozen_run_manifest_verifies_without_fitting() -> None:
    from seq2lead.models.m9_artifacts import MANIFEST_PATH, verify_manifest

    if not MANIFEST_PATH.exists():
        pytest.skip("no run manifest")
    assert verify_manifest() == []
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    assert manifest["n_runs"] == 20  # noqa: PLR2004
    assert "retrospectively" in manifest["provenance"].lower()
    assert {r["n_parameters"] for r in manifest["runs"]} == {2230274}


def test_a_tampered_artifact_is_detected_by_the_manifest(tmp_path) -> None:
    from seq2lead.models.m9_artifacts import MANIFEST_PATH, verify_manifest

    if not MANIFEST_PATH.exists():
        pytest.skip("no run manifest")
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    victim = Path(manifest["runs"][0]["prediction"])
    if not victim.exists():
        pytest.skip("prediction missing")
    backup = tmp_path / "backup.npz"
    shutil.copy(victim, backup)
    try:
        with np.load(victim, allow_pickle=False) as data:
            arrays = {k: data[k] for k in data.files}
        arrays["prediction"] = arrays["prediction"] + 1.0
        np.savez_compressed(victim, **arrays)
        problems = verify_manifest()
        assert any("bytes changed" in p for p in problems)
    finally:
        shutil.copy(backup, victim)
    assert verify_manifest() == []


# ===================================================== 4. the length policy


def test_the_refusal_point_is_enforced_not_truncated() -> None:
    sequence, _notes = validate_sequence("A" * 40_000, 1022, 40_000)
    assert len(sequence) == 40_000  # noqa: PLR2004
    with pytest.raises(SequenceError, match="refusal point"):
        validate_sequence("A" * 40_001, 1022, 40_000)


def test_over_length_rejection_happens_before_any_model_load(monkeypatch) -> None:
    """An over-length query must cost nothing -- no ESM-2 load, no vectors read."""
    import seq2lead.models.rank as rank_module

    def explode(*_args, **_kwargs):
        raise AssertionError("the model was loaded despite an over-length query")

    monkeypatch.setattr(rank_module, "embed_sequence", explode)
    with pytest.raises(SequenceError, match="refusal point"):
        validate_sequence("A" * 50_000, 1022, 40_000)


def test_a_sequence_past_the_training_window_is_noted_not_refused() -> None:
    _sequence, notes = validate_sequence("A" * 2_000, 1022, 40_000)
    assert any("pre-training window" in n for n in notes)
