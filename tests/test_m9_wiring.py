"""Production wiring: bindings reaching the runner, and artifacts protected.

These exercise the paths the CLI actually takes. Fitting is mocked throughout --
the point is which arguments reach the runner and which destinations are checked,
not whether a model trains.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

torch = pytest.importorskip("torch")

from seq2lead.models.binding import BindingError, load_binding, sidecar_path  # noqa: E402
from seq2lead.models.m9_artifacts import (  # noqa: E402
    MANIFEST_PATH,
    ArtifactCollision,
    verify_manifest,
)
from seq2lead.models.m9_runner import (  # noqa: E402
    SelectionRecord,
    selection_record_path,
    write_selection,
)

CHECKPOINTS = Path("data/m9/final")


@pytest.fixture(scope="module")
def experiment():
    from seq2lead.db import connect
    from seq2lead.eval import load_experiment

    with connect() as conn:
        yield load_experiment(conn, "m9-dual-encoder-v1")


def _selection(config, split: str, dim: int = 512) -> SelectionRecord:
    return SelectionRecord(
        experiment=config.version,
        metric="validation_rmse",
        direction="minimise",
        min_validation_pairs=0,
        min_validation_targets=0,
        seed=1,
        config_sha256=config.config_sha256,
        declared_splits=[split],
        declared_projection_dims=[dim],
        chosen={split: dim},
    )


# ====================================== 1. the binding reaches production fitting


@pytest.mark.requires_db
def test_score_test_refuses_without_a_binding(experiment, tmp_path) -> None:
    """A checkpoint saved with `feature_binding: None` is unusable at inference."""
    import dataclasses

    from seq2lead.db import connect
    from seq2lead.eval.features import FeatureBank
    from seq2lead.models.m9_runner import score_test

    split = experiment.splits[0].name
    trimmed = dataclasses.replace(
        experiment, splits=tuple(s for s in experiment.splits if s.name == split)
    )
    with connect() as conn:
        bank = FeatureBank.load(conn, experiment)
        with pytest.raises(BindingError, match="requires a feature binding"):
            score_test(
                conn,
                trimmed,
                bank,
                _selection(experiment, split),
                (1,),
                run_root=tmp_path / "m9",
                prediction_dir=tmp_path / "pred",
            )
    assert not (tmp_path / "m9").exists(), "a destination was created despite the refusal"


@pytest.mark.requires_db
@pytest.mark.parametrize(
    ("field", "value", "pattern"),
    [
        ("experiment", "some-other-experiment", "built for experiment"),
        ("config_sha256", "f" * 64, "different config file"),
    ],
)
def test_a_mismatched_binding_refuses_before_fitting(
    experiment, tmp_path, monkeypatch, field, value, pattern
) -> None:
    import dataclasses

    from seq2lead.db import connect
    from seq2lead.eval.features import FeatureBank
    from seq2lead.models import m9_runner
    from seq2lead.models.binding import from_experiment
    from seq2lead.models.m9_runner import score_test

    def no_fitting(*_args, **_kwargs):
        raise AssertionError("a model was fitted despite a mismatched binding")

    monkeypatch.setattr(m9_runner, "fit", no_fitting)
    split = experiment.splits[0].name
    trimmed = dataclasses.replace(
        experiment, splits=tuple(s for s in experiment.splits if s.name == split)
    )
    with connect() as conn:
        bank = FeatureBank.load(conn, experiment)
        binding = dataclasses.replace(from_experiment(conn, experiment), **{field: value})
        with pytest.raises(BindingError, match=pattern):
            score_test(
                conn,
                trimmed,
                bank,
                _selection(experiment, split),
                (1,),
                run_root=tmp_path / "m9",
                prediction_dir=tmp_path / "pred",
                binding=binding,
            )


@pytest.mark.requires_db
def test_the_cli_passes_a_binding_into_the_runner(experiment, monkeypatch) -> None:
    """The defect: `m9 test` called `score_test()` without one.

    Captures what the CLI hands the runner, without fitting anything.
    """
    from typer.testing import CliRunner

    from seq2lead.cli import app
    from seq2lead.models import m9_runner

    captured: dict = {}

    def capture(conn, config, bank, selection, seeds, **kwargs):  # noqa: ARG001
        captured["binding"] = kwargs.get("binding")
        raise SystemExit("stop after capture")

    monkeypatch.setattr(m9_runner, "score_test", capture)
    monkeypatch.setattr("seq2lead.cli.score_test", capture, raising=False)
    CliRunner().invoke(app, ["m9", "test", "--seeds", "1"])

    binding = captured.get("binding")
    assert binding is not None, "the CLI still calls score_test() without a binding"
    assert binding.experiment == experiment.version
    assert binding.config_sha256 == experiment.config_sha256
    assert binding.compound_cache == experiment.caches["ecfp4"].name
    assert binding.protein_cache == experiment.caches["esm2"].name


@pytest.mark.requires_db
def test_a_newly_saved_checkpoint_resolves_its_own_binding(experiment, tmp_path) -> None:
    """No sidecar needed: the binding travels inside the checkpoint."""
    import numpy as np

    from seq2lead.db import connect
    from seq2lead.models.binding import from_experiment
    from seq2lead.models.dual_encoder import (
        DualEncoder,
        DualEncoderConfig,
        ProteinTransform,
        save_checkpoint,
    )

    with connect() as conn:
        binding = from_experiment(conn, experiment, provenance="fit-time, this test")

    model = DualEncoder(DualEncoderConfig(projection_dim=8, seed=1))
    model.transform = ProteinTransform.fit(
        np.zeros((2, model.config.protein_dim), dtype=np.float32)
    )
    path = save_checkpoint(
        model,
        tmp_path / "fresh.pt",
        extra={
            "experiment": experiment.version,
            "config_digest": experiment.config_sha256,
            "feature_binding": binding.to_dict(),
        },
    )
    assert not sidecar_path(path).exists(), "the test wrote a sidecar; it must not need one"

    from seq2lead.models.dual_encoder import load_checkpoint

    _restored, extra = load_checkpoint(path)
    resolved = load_binding(path, extra)
    assert resolved.compound_cache == binding.compound_cache
    assert resolved.protein.model_revision == binding.protein.model_revision


# ======================================= 2. the selection phase is protected


@pytest.mark.requires_db
def test_a_collision_on_a_later_selection_record_stops_every_fit(
    experiment, tmp_path, monkeypatch
) -> None:
    """Occupy the third planned record; assert zero fits and untouched files."""
    from seq2lead.db import connect
    from seq2lead.eval.features import FeatureBank
    from seq2lead.models import m9_runner
    from seq2lead.models.m9_runner import select

    def no_fitting(*_args, **_kwargs):
        raise AssertionError("select() fitted a model despite the collision")

    monkeypatch.setattr(m9_runner, "fit", no_fitting)

    dims = (256, 512)
    planned = [
        selection_record_path("probe", split.name, dim, 1, tmp_path / "m9")
        for split in experiment.splits
        for dim in dims
    ]
    victim = planned[2]
    victim.parent.mkdir(parents=True, exist_ok=True)
    victim.write_text("an existing selection record", encoding="utf-8")
    before = victim.read_bytes()

    with connect() as conn:
        bank = FeatureBank.load(conn, experiment)
        with pytest.raises(ArtifactCollision, match="Refusing before any selection fit"):
            select(
                conn,
                experiment,
                bank,
                dims,
                1,
                run_root=tmp_path / "m9",
                label="probe",
                selection_path=tmp_path / "selection.json",
            )

    assert victim.read_bytes() == before
    survivors = sorted(p.name for p in victim.parent.iterdir())
    assert survivors == [victim.name], f"other records were created: {survivors}"


@pytest.mark.requires_db
def test_an_occupied_frozen_selection_destination_stops_every_fit(
    experiment, tmp_path, monkeypatch
) -> None:
    from seq2lead.db import connect
    from seq2lead.eval.features import FeatureBank
    from seq2lead.models import m9_runner
    from seq2lead.models.m9_runner import select

    monkeypatch.setattr(
        m9_runner,
        "fit",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("fitted despite collision")),
    )
    destination = tmp_path / "selection.json"
    destination.write_text("an existing frozen selection", encoding="utf-8")
    before = destination.read_bytes()

    with connect() as conn:
        bank = FeatureBank.load(conn, experiment)
        with pytest.raises(ArtifactCollision, match="Refusing before any selection fit"):
            select(
                conn,
                experiment,
                bank,
                (256, 512),
                1,
                run_root=tmp_path / "m9",
                label="probe",
                selection_path=destination,
            )
    assert destination.read_bytes() == before


def test_write_selection_refuses_a_conflicting_replacement(tmp_path) -> None:
    class Config:
        version = "x"
        config_sha256 = "a" * 64

    record = _selection(Config(), "random_pair-v3")
    path = tmp_path / "selection.json"
    write_selection(record, path)
    before = path.read_bytes()

    write_selection(record, path)  # identical: a no-op
    assert path.read_bytes() == before

    record.chosen = {"random_pair-v3": 256}
    with pytest.raises(ArtifactCollision, match="already holds a different frozen selection"):
        write_selection(record, path)
    assert path.read_bytes() == before

    write_selection(record, path, overwrite=True)
    assert path.read_bytes() != before


def test_selection_records_are_scoped_by_label(tmp_path) -> None:
    first = selection_record_path("default", "random_pair-v3", 512, 1, tmp_path)
    second = selection_record_path("rerun", "random_pair-v3", 512, 1, tmp_path)
    assert first != second
    assert "default" in str(first)
    assert "rerun" in str(second)


# ================================= 3. training records are verified


def test_the_manifest_records_a_training_record_path() -> None:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    for run in manifest["runs"]:
        assert run.get("record"), f"{run['run']} has no training-record path"
        assert run.get("record_sha256")
        assert Path(run["record"]).exists()


def test_a_changed_training_record_fails_verification(tmp_path) -> None:
    """The defect: the digest was stored but never checked against anything."""
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    victim = Path(manifest["runs"][0]["record"])
    backup = tmp_path / "backup.json"
    shutil.copy(victim, backup)
    try:
        payload = json.loads(victim.read_text(encoding="utf-8"))
        payload["best_validation_rmse"] = 99.0
        victim.write_text(json.dumps(payload), encoding="utf-8")
        problems = verify_manifest()
        assert any("training record" in p and "bytes changed" in p for p in problems)
    finally:
        shutil.copy(backup, victim)
    assert verify_manifest() == []


def test_a_missing_training_record_fails_verification(tmp_path) -> None:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    victim = Path(manifest["runs"][0]["record"])
    backup = tmp_path / "backup.json"
    shutil.copy(victim, backup)
    try:
        victim.unlink()
        problems = verify_manifest()
        assert any("training record missing" in p for p in problems)
    finally:
        shutil.copy(backup, victim)
    assert verify_manifest() == []


def test_a_required_artifact_without_a_path_is_a_problem_not_a_skip(tmp_path) -> None:
    """Silently passing an unverifiable run is how it looks identical to a verified one."""
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    manifest["runs"] = manifest["runs"][:1]
    del manifest["runs"][0]["record"]
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    problems = verify_manifest(path)
    assert any("cannot be verified" in p and "record" in p for p in problems)


def test_an_optional_artifact_without_a_path_is_skipped(tmp_path) -> None:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    manifest["runs"] = manifest["runs"][:1]
    manifest["runs"][0]["binding_sidecar"] = None
    manifest["runs"][0]["binding_sha256"] = None
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    assert verify_manifest(path) == []


def test_the_previous_manifest_is_preserved() -> None:
    archive = MANIFEST_PATH.with_name("m9_runs.v1.json")
    assert archive.exists(), "the pre-update manifest was not kept"
    previous = json.loads(archive.read_text(encoding="utf-8"))
    current = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    assert "record" not in previous["runs"][0]
    assert "record" in current["runs"][0]
    # The re-freeze must not quietly drop what an earlier freeze disclosed.
    provenance = current["provenance"].lower()
    assert "retrospectively" in provenance, "the retrospective origin was dropped"
    assert "drift baseline" in provenance, "the drift-baseline caveat was dropped"
    assert "record" in provenance, "the re-freeze does not say what it changed"
    # Digests carried over, not re-derived: that is what makes the update honest.
    by_run = {r["run"]: r for r in previous["runs"]}
    for run in current["runs"]:
        prior = by_run[run["run"]]
        for key in ("checkpoint_sha256", "prediction_sha256", "record_sha256", "binding_sha256"):
            assert prior[key] == run[key], f"{run['run']}: {key} changed during the update"
