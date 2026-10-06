"""Result-version bugs: the selected source, and cross-version file ownership.

Both were reachable with every existing check passing. The first let a run
compare against one version while recording and superseding another; the second
let a publication overwrite a file another version owned, leaving that version
failing its own checksum.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from seq2lead.eval.results import (
    ResultSelectionError,
    check_publishable,
    load,
    owner_of,
    publish,
    read_index,
)


class Cfg:
    version = "baseline-v1"
    config_sha256 = "a" * 64


class OtherCfg:
    version = "baseline-v1"
    config_sha256 = "b" * 64


def _run(model: str, auroc: float, ap: float) -> dict:
    return {
        "model": model,
        "split": "random_pair-v3",
        "seed": None,
        "deterministic": True,
        "ranking": {
            "auroc": {"mean": auroc, "n_targets": 10},
            "average_precision": {"mean": ap, "n_targets": 10},
        },
        "regression": {"rmse": {"mean": 1.0, "n_targets": 10}},
    }


# ============================================ file ownership across versions


def test_a_version_cannot_take_a_filename_another_version_owns(tmp_path) -> None:
    """The reproducing case: v2 to v1's file left v1 failing its own checksum."""
    publish(
        [_run("B0", 0.5, 0.5)],
        version="v1",
        filename="shared.json",
        metric_version="m",
        config=Cfg(),
        directory=tmp_path,
    )
    with pytest.raises(ResultSelectionError, match="already registered to version 'v1'"):
        publish(
            [_run("B0", 0.9, 0.9)],
            version="v2",
            filename="shared.json",
            metric_version="m",
            config=Cfg(),
            directory=tmp_path,
        )
    runs, entry = load("v1", Cfg(), tmp_path)
    assert runs[0]["ranking"]["auroc"]["mean"] == pytest.approx(0.5)
    assert entry.version == "v1"
    assert owner_of("shared.json", tmp_path) == "v1"


def test_a_refused_publication_leaves_every_file_byte_identical(tmp_path) -> None:
    publish(
        [_run("B0", 0.5, 0.5)],
        version="v1",
        filename="shared.json",
        metric_version="m",
        config=Cfg(),
        directory=tmp_path,
    )
    before = {p.name: p.read_bytes() for p in sorted(tmp_path.iterdir())}
    with pytest.raises(ResultSelectionError):
        publish(
            [_run("B0", 0.9, 0.9)],
            version="v2",
            filename="shared.json",
            metric_version="m",
            config=Cfg(),
            directory=tmp_path,
        )
    after = {p.name: p.read_bytes() for p in sorted(tmp_path.iterdir())}
    assert after == before, "a refused publication changed files on disk"


def test_same_version_with_changed_content_is_refused(tmp_path) -> None:
    publish(
        [_run("B0", 0.5, 0.5)],
        version="v1",
        filename="a.json",
        metric_version="m",
        config=Cfg(),
        directory=tmp_path,
    )
    with pytest.raises(ResultSelectionError, match="different content"):
        publish(
            [_run("B0", 0.6, 0.5)],
            version="v1",
            filename="a.json",
            metric_version="m",
            config=Cfg(),
            directory=tmp_path,
        )


def test_same_version_with_changed_bindings_is_refused(tmp_path) -> None:
    runs = [_run("B0", 0.5, 0.5)]
    publish(
        runs, version="v1", filename="a.json", metric_version="m", config=Cfg(), directory=tmp_path
    )
    with pytest.raises(ResultSelectionError, match="metric version"):
        publish(
            runs,
            version="v1",
            filename="a.json",
            metric_version="different",
            config=Cfg(),
            directory=tmp_path,
        )
    with pytest.raises(ResultSelectionError, match="experiment"):
        publish(
            runs,
            version="v1",
            filename="a.json",
            metric_version="m",
            config=OtherCfg(),
            directory=tmp_path,
        )


def test_an_identical_rerun_preserves_registered_metadata(tmp_path) -> None:
    runs = [_run("B0", 0.5, 0.5)]
    first = publish(
        runs, version="v1", filename="a.json", metric_version="m", config=Cfg(), directory=tmp_path
    )
    second = publish(
        runs, version="v1", filename="a.json", metric_version="m", config=Cfg(), directory=tmp_path
    )
    assert second.created_at == first.created_at
    assert second.sha256 == first.sha256
    assert load("v1", Cfg(), tmp_path)[0] == runs


def test_publishing_over_a_corrupted_artifact_is_refused(tmp_path) -> None:
    """Provenance is already broken; overwriting would hide that."""
    runs = [_run("B0", 0.5, 0.5)]
    publish(
        runs, version="v1", filename="a.json", metric_version="m", config=Cfg(), directory=tmp_path
    )
    (tmp_path / "a.json").write_text("[]", encoding="utf-8")
    with pytest.raises(ResultSelectionError, match="no longer matches its recorded digest"):
        publish(
            runs,
            version="v1",
            filename="a.json",
            metric_version="m",
            config=Cfg(),
            directory=tmp_path,
        )


def test_staged_companion_files_are_also_ownership_checked(tmp_path) -> None:
    """A fitting run writes a full record beside its summary; both are guarded."""
    publish(
        [_run("B0", 0.5, 0.5)],
        version="v1",
        filename="v1_runs.json",
        metric_version="m",
        config=Cfg(),
        directory=tmp_path,
    )
    with pytest.raises(ResultSelectionError, match="already registered to version 'v1'"):
        publish(
            [_run("B0", 0.9, 0.9)],
            version="v2",
            filename="v2_summary.json",
            metric_version="m",
            config=Cfg(),
            directory=tmp_path,
            staged={"v1_runs.json": "clobbered"},
        )
    assert (tmp_path / "v1_runs.json").read_text(encoding="utf-8") != "clobbered"
    assert not (tmp_path / "v2_summary.json").exists()


def test_check_publishable_writes_nothing(tmp_path) -> None:
    publish(
        [_run("B0", 0.5, 0.5)],
        version="v1",
        filename="a.json",
        metric_version="m",
        config=Cfg(),
        directory=tmp_path,
    )
    before = sorted(p.name for p in tmp_path.iterdir())
    check_publishable(
        version="v2",
        filename="b.json",
        body="[]",
        metric_version="m",
        config=Cfg(),
        directory=tmp_path,
    )
    assert sorted(p.name for p in tmp_path.iterdir()) == before


# ====================================== a fitting publication refused mid-flight


def test_a_refused_fitting_run_leaves_record_summary_and_index_unchanged(tmp_path) -> None:
    """The full record used to be written before the summary guard ran."""
    from dataclasses import dataclass, field
    from typing import Any

    from seq2lead.eval.runner import write_results

    @dataclass
    class FakeRun:
        model: str = "B0"
        split: str = "random_pair-v3"
        seed: int | None = None
        deterministic: bool = True
        ranking: dict[str, Any] = field(default_factory=lambda: {"auroc": {"mean": 0.5}})
        regression: dict[str, Any] = field(default_factory=lambda: {"rmse": {"mean": 1.0}})

    # v1 already owns the filename the v2 fitting run would use for its record.
    publish(
        [_run("B0", 0.5, 0.5)],
        version="v1",
        filename="m8_v2_runs.json",
        metric_version="m8/v2",
        config=Cfg(),
        directory=tmp_path,
    )
    before = {p.name: p.read_bytes() for p in sorted(tmp_path.iterdir())}

    with pytest.raises(ResultSelectionError):
        write_results(
            [FakeRun()], Cfg(), version="m8/v2", metric_version="m8/v2", directory=tmp_path
        )

    after = {p.name: p.read_bytes() for p in sorted(tmp_path.iterdir())}
    assert after == before, "a refused fitting publication left files behind"
    assert set(read_index(tmp_path)) == {"v1"}


# ================================================ the selected recompute source


@pytest.mark.requires_db
def test_recompute_compares_against_the_selected_source_not_a_hardcoded_file(
    tmp_path, monkeypatch
) -> None:
    """The bug: `source_version` was accepted, recorded and used to supersede,
    while the comparison always read `baseline_summary.json`.

    Two registered sources hold deliberately different AP values. Choosing the
    second must compare against the second -- including when the old hardcoded
    filename exists and holds the first.
    """
    from seq2lead.db import connect
    from seq2lead.eval import load_experiment
    from seq2lead.eval.recompute import recompute_all
    from seq2lead.eval.verify import MANIFEST_PATH

    if not MANIFEST_PATH.exists():
        pytest.skip("no frozen manifest")
    raw = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    chosen = [r for r in raw["runs"] if r["split"] == "random_pair-v3"][:2]
    if len(chosen) < 2:  # noqa: PLR2004
        pytest.skip("not enough runs")

    predictions = tmp_path / "predictions"
    predictions.mkdir()
    for run in chosen:
        source = Path("data/predictions") / run["filename"]
        if not source.exists():
            pytest.skip("predictions missing")
        shutil.copy(source, predictions / run["filename"])
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps({**raw, "runs": chosen, "n_runs": len(chosen)}), encoding="utf-8"
    )

    with connect() as conn:
        config = load_experiment(conn)

    results = tmp_path / "results"
    results.mkdir()

    def source_runs(ap: float) -> list[dict]:
        return [
            {
                "model": r["model"],
                "split": r["split"],
                "seed": None if r["deterministic"] else r["seed"],
                "deterministic": r["deterministic"],
                "ranking": {
                    "auroc": {"mean": 0.5, "n_targets": 1},
                    "average_precision": {"mean": ap, "n_targets": 1},
                },
                "regression": {
                    "rmse": {"mean": 1.0},
                    "mae": {"mean": 1.0},
                    "spearman": {"mean": 0.0},
                },
                "fit_seconds": 1.0,
                "predict_seconds": 0.1,
                "n_train": 10,
                "fit_notes": {},
            }
            for r in chosen
        ]

    publish(
        source_runs(0.111),
        version="src/a",
        filename="a.json",
        metric_version="m8/v1",
        config=config,
        directory=results,
    )
    publish(
        source_runs(0.999),
        version="src/b",
        filename="b.json",
        metric_version="m8/v1",
        config=config,
        directory=results,
    )
    # The file the buggy version read unconditionally, holding the *wrong* source.
    shutil.copy(results / "a.json", results / "baseline_summary.json")

    with connect() as conn:
        _corrected, report = recompute_all(
            conn,
            config,
            "src/b",
            prediction_dir=predictions,
            result_dir=results,
            manifest_path=manifest_path,
        )

    assert report.source_version == "src/b"
    ap_rows = [c for c in report.comparisons if c.metric == "ranking.average_precision"]
    assert ap_rows
    assert all(c.old == pytest.approx(0.999) for c in ap_rows), (
        "comparison used the wrong source: it read the hardcoded file, not src/b"
    )
    assert report.source_sha256 == read_index(results)["src/b"].sha256


@pytest.mark.requires_db
def test_a_missing_or_mismatched_source_is_refused(tmp_path) -> None:
    from seq2lead.db import connect
    from seq2lead.eval import load_experiment
    from seq2lead.eval.recompute import recompute_all
    from seq2lead.eval.verify import MANIFEST_PATH

    if not MANIFEST_PATH.exists():
        pytest.skip("no frozen manifest")
    with connect() as conn:
        config = load_experiment(conn)
        results = tmp_path / "results"
        results.mkdir()
        with pytest.raises(ResultSelectionError, match="no result version"):
            recompute_all(conn, config, "src/absent", result_dir=results)

        # Registered, but describing runs the manifest does not expect.
        publish(
            [
                {
                    "model": "B9",
                    "split": "nowhere",
                    "seed": None,
                    "deterministic": True,
                    "ranking": {},
                    "regression": {},
                }
            ],
            version="src/wrong",
            filename="w.json",
            metric_version="m8/v1",
            config=config,
            directory=results,
        )
        with pytest.raises(ValueError, match="does not match the prediction manifest"):
            recompute_all(conn, config, "src/wrong", result_dir=results)
    assert not (results / "m8_v2_summary.json").exists()
