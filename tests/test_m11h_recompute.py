"""The recomputation entry point, and what it refuses. Nothing is fitted.

A recomputation that silently accepts broken inputs is worse than none: it
produces a report that looks like the original and describes something else. The
three failure modes exercised here all have that shape.

The alignment case is the subtle one. The prediction rows and the evaluation
table are joined **positionally**, so a reordered table does not fail on its own
-- every metric still computes, on scores attached to the wrong pairs. So the
test reorders the table while leaving its length and contents identical, which
is the case a row count cannot catch.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import numpy as np
import pytest

from seq2lead.asof.recompute import (
    RecomputeError,
    check_alignment,
    score_run,
    verify_and_load,
)

TAGS = ("B0-target-mean", "model-seed1", "model-seed2")
SEQ = "a" * 64


def sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def build_run(root: Path, *, n_targets: int = 3, per_target: int = 14) -> Path:
    """A minimal but structurally real run: a pair table, predictions, a manifest."""
    root.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(7)
    rows, pairs = [], []
    for t in range(n_targets):
        target = f"{t:064x}".upper()
        for c in range(per_target):
            pair = f"CMPD{t:02d}{c:03d}AAAAA-AAAAAAAAAA-N|{target}"
            pairs.append(pair)
            rows.append({
                "pair": pair,
                "stratum": "new_absent_from_a",
                "participated_in_model_selection": False,
                "arms": {
                    arm: {
                        "scoreable": True,
                        "label": "active" if c % 2 else "inactive",
                        "branches": {"screened_primary": True, "unscreened_sensitivity": True},
                        "audit_stratum": "new_pair",
                    }
                    for arm in ("declared_increment", "cross_slot_excluded")
                },
            })
    table = root / "evaluation-pairs.jsonl"
    table.write_text(
        "".join(json.dumps(r, sort_keys=True) + "\n" for r in rows), encoding="utf-8"
    )
    predictions = root / "predictions.npz"
    np.savez_compressed(
        predictions,
        pair=np.asarray(pairs, dtype=object),
        **{tag: rng.standard_normal(len(pairs)) for tag in TAGS},
    )
    (root / "training-records.json").write_text("[]\n", encoding="utf-8")
    (root / "manifest.json").write_text(
        json.dumps({
            "run_id": "test",
            "contract_version": "test-contract",
            "runner_version": "test-runner",
            "fitting_version": "test-fitting",
            "seeds": [1, 2],
            "inputs_verified": {},
            "roles_derived": {},
            "feature_bindings": {},
            "emitted_matches_pinned": {},
            "output_preflight": {"planned": {}, "planned_checkpoints": [], "collisions": []},
            "transform": {"path": str(root / "t.npz"), "sha256": "", "fitted_on": "a_train",
                          "n_fitted": 1},
            "training_records": {"path": str(root / "training-records.json"), "sha256": ""},
            "predictions": {
                "path": str(predictions),
                "sha256": sha256(predictions),
                "models": sorted(TAGS),
                "rows": len(pairs),
            },
            "evaluation_table": {"path": str(table), "sha256": sha256(table)},
            # the loader verifies this, so a tampered table is caught before scoring
            "checkpoints": {},
            "selection": {},
            "failures": [],
            "qualifications": [],
        }, indent=1, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return root


@pytest.fixture
def run_dir(tmp_path) -> Path:
    return build_run(tmp_path / "run")


def rewrite_predictions(root: Path, **arrays) -> None:
    """Replace the prediction archive WITHOUT touching the manifest's digest."""
    blob = np.load(root / "predictions.npz", allow_pickle=True)
    keep = {k: blob[k] for k in blob.files}
    keep.update(arrays)
    np.savez_compressed(root / "predictions.npz", **keep)


# ====================================================== the valid path works


def test_a_sound_run_verifies_loads_and_scores(run_dir) -> None:
    run = verify_and_load(run_dir)
    assert run.tags == sorted(TAGS)
    assert len(run.pairs) == len(run.table)
    results = score_run(run)
    assert results["metric_version"] == "m8/v2"
    head = results["cells"]["declared_increment/screened_primary"]["groups"]
    assert head["new_to_fitting"]["pairs"] == len(run.pairs)
    for tag in TAGS:
        assert head["new_to_fitting"]["by_model"][tag]["targets_scored"] == 3


def test_scoring_the_same_predictions_twice_is_identical(run_dir) -> None:
    run = verify_and_load(run_dir)
    assert json.dumps(score_run(run), sort_keys=True) == json.dumps(
        score_run(run), sort_keys=True
    )


def test_nothing_in_the_path_fits_or_loads_a_checkpoint(run_dir) -> None:
    """Checked structurally: no fitting symbol is reachable from the entry point."""
    import inspect

    from seq2lead.asof import recompute

    source = inspect.getsource(recompute)
    for forbidden in ("fit(", "load_checkpoint", "DualEncoder", "lightgbm", "torch"):
        assert forbidden not in source, forbidden


# ========================================================= tampered predictions


def test_tampered_predictions_refuse(run_dir) -> None:
    rewrite_predictions(run_dir, **{"model-seed1": np.zeros(42)})
    with pytest.raises(RecomputeError, match="saved predictions have changed"):
        verify_and_load(run_dir)


def test_a_tampered_value_refuses_even_when_the_shape_is_right(run_dir) -> None:
    """A single altered score must not pass: the digest covers the bytes."""
    blob = np.load(run_dir / "predictions.npz", allow_pickle=True)
    altered = np.array(blob["model-seed1"])
    altered[0] += 1e-9
    rewrite_predictions(run_dir, **{"model-seed1": altered})
    with pytest.raises(RecomputeError, match="saved predictions have changed"):
        verify_and_load(run_dir)


def test_the_digest_check_can_be_waived_only_explicitly(run_dir) -> None:
    """An operator inspecting a deliberately altered run must say so."""
    blob = np.load(run_dir / "predictions.npz", allow_pickle=True)
    altered = np.array(blob["model-seed1"])
    altered[0] += 0.5
    rewrite_predictions(run_dir, **{"model-seed1": altered})
    with pytest.raises(RecomputeError):
        verify_and_load(run_dir)
    run = verify_and_load(run_dir, expect_digest=False)
    assert run.scores["model-seed1"][0] == pytest.approx(altered[0])


# ========================================================= missing model arrays


def test_a_missing_model_array_refuses(run_dir) -> None:
    blob = np.load(run_dir / "predictions.npz", allow_pickle=True)
    kept = {k: blob[k] for k in blob.files if k != "model-seed2"}
    np.savez_compressed(run_dir / "predictions.npz", **kept)
    manifest = json.loads((run_dir / "manifest.json").read_text())
    manifest["predictions"]["sha256"] = sha256(run_dir / "predictions.npz")
    (run_dir / "manifest.json").write_text(json.dumps(manifest, indent=1, sort_keys=True))
    with pytest.raises(RecomputeError, match="are absent from the archive"):
        verify_and_load(run_dir)


def test_an_unexpected_model_array_refuses(run_dir) -> None:
    """An extra model the manifest never named is also a mismatch, not a bonus."""
    blob = np.load(run_dir / "predictions.npz", allow_pickle=True)
    kept = {k: blob[k] for k in blob.files}
    kept["model-seed9"] = np.zeros(len(blob["pair"]))
    np.savez_compressed(run_dir / "predictions.npz", **kept)
    manifest = json.loads((run_dir / "manifest.json").read_text())
    manifest["predictions"]["sha256"] = sha256(run_dir / "predictions.npz")
    (run_dir / "manifest.json").write_text(json.dumps(manifest, indent=1, sort_keys=True))
    with pytest.raises(RecomputeError, match="the manifest does not name"):
        verify_and_load(run_dir)


def test_a_prediction_archive_with_no_pair_array_refuses(run_dir) -> None:
    blob = np.load(run_dir / "predictions.npz", allow_pickle=True)
    kept = {k: blob[k] for k in blob.files if k != "pair"}
    np.savez_compressed(run_dir / "predictions.npz", **kept)
    manifest = json.loads((run_dir / "manifest.json").read_text())
    manifest["predictions"]["sha256"] = sha256(run_dir / "predictions.npz")
    (run_dir / "manifest.json").write_text(json.dumps(manifest, indent=1, sort_keys=True))
    with pytest.raises(RecomputeError, match="no `pair` array"):
        verify_and_load(run_dir)


# ============================================================= misaligned pairs


def rewrite_table(run_dir: Path, rows: list[dict], *, refresh_digest: bool) -> None:
    """Rewrite the table, optionally re-recording its digest in the manifest.

    With the digest refreshed, the digest gate passes and the ALIGNMENT gate is
    what must fire. Both layers matter: the digest catches a changed file, and
    alignment catches a file that is internally consistent but does not line up
    with the predictions.
    """
    path = run_dir / "evaluation-pairs.jsonl"
    path.write_text(
        "".join(json.dumps(r, sort_keys=True) + "\n" for r in rows), encoding="utf-8"
    )
    if refresh_digest:
        manifest = json.loads((run_dir / "manifest.json").read_text())
        manifest["evaluation_table"]["sha256"] = sha256(path)
        (run_dir / "manifest.json").write_text(json.dumps(manifest, indent=1, sort_keys=True))


def test_a_changed_pair_table_refuses_on_its_digest_first(run_dir) -> None:
    """The digest gate fires before alignment, so a changed table is caught early."""
    rows = [json.loads(line) for line in (run_dir / "evaluation-pairs.jsonl").open()]
    rows[0], rows[1] = rows[1], rows[0]
    rewrite_table(run_dir, rows, refresh_digest=False)
    with pytest.raises(RecomputeError, match="evaluation table has changed"):
        verify_and_load(run_dir)


def test_a_reordered_pair_table_refuses_on_alignment(run_dir) -> None:
    """Same rows, same count, different order -- the case a row count cannot catch."""
    rows = [json.loads(line) for line in (run_dir / "evaluation-pairs.jsonl").open()]
    rows[0], rows[1] = rows[1], rows[0]
    rewrite_table(run_dir, rows, refresh_digest=True)
    with pytest.raises(RecomputeError, match="not in the same order"):
        verify_and_load(run_dir)


def test_a_resized_pair_table_refuses_on_alignment(run_dir) -> None:
    rows = [json.loads(line) for line in (run_dir / "evaluation-pairs.jsonl").open()]
    rewrite_table(run_dir, rows[:-1], refresh_digest=True)
    with pytest.raises(RecomputeError, match="the join is positional"):
        verify_and_load(run_dir)


def test_a_missing_pair_table_refuses(run_dir) -> None:
    (run_dir / "evaluation-pairs.jsonl").unlink()
    with pytest.raises(RecomputeError, match="cannot be attached to pairs"):
        verify_and_load(run_dir)


def test_a_wrong_length_prediction_array_refuses(run_dir) -> None:
    pairs = ["a|b", "c|d"]
    table = [{"pair": p, "stratum": "x", "participated_in_model_selection": False,
              "arms": {}} for p in pairs]
    with pytest.raises(RecomputeError, match="expected \\(2,\\)"):
        check_alignment(pairs, table, {"m": np.zeros(3)})


def test_a_non_finite_prediction_refuses(run_dir) -> None:
    pairs = ["a|b", "c|d"]
    table = [{"pair": p, "stratum": "x", "participated_in_model_selection": False,
              "arms": {}} for p in pairs]
    with pytest.raises(RecomputeError, match="non-finite"):
        check_alignment(pairs, table, {"m": np.array([1.0, np.nan])})


# ================================================= missing or broken manifest


def test_a_run_without_a_manifest_refuses(run_dir) -> None:
    (run_dir / "manifest.json").unlink()
    with pytest.raises(RecomputeError, match="no manifest.json"):
        verify_and_load(run_dir)


def test_a_manifest_naming_absent_predictions_refuses(run_dir) -> None:
    (run_dir / "predictions.npz").unlink()
    with pytest.raises(RecomputeError, match="which does not exist"):
        verify_and_load(run_dir)


# ============================================ the real run, when it is present


REAL = Path("data/asof/m11h/run-20261005T134842Z")


@pytest.mark.skipif(not REAL.exists(), reason="the real run directory is not present")
def test_the_real_run_verifies_and_its_predictions_are_unchanged() -> None:
    run = verify_and_load(REAL)
    assert len(run.tags) == 22
    assert len(run.pairs) == 26_444
    # The manifest was written by the fit and has not been rewritten since, so a
    # successful digest check is evidence the predictions are the original ones.
    assert run.manifest["predictions"]["sha256"] == sha256(REAL / "predictions.npz")


@pytest.mark.skipif(not REAL.exists(), reason="the real run directory is not present")
def test_the_real_runs_seeds_did_not_produce_identical_predictions() -> None:
    """The claim this closeout corrected, pinned so it cannot be reasserted."""
    from seq2lead.asof.recompute import prediction_differences

    run = verify_and_load(REAL)
    diffs = prediction_differences(run)
    for family in ("B1-ligand-ecfp4-lgbm", "B2-protein-esm2-lgbm"):
        d = diffs[family]
        assert d["comparable"]
        assert d["bit_identical_predictions"] is False, family
        assert d["max_abs_delta_pki"] > 0.0, family
        assert d["rows_differing_from_the_first_seed"] == d["rows"], family
    # B2's ranking metric is pinned by construction even so.
    assert diffs["B2-protein-esm2-lgbm"]["constant_within_target"] is True


@pytest.mark.skipif(not REAL.exists(), reason="the real run directory is not present")
def test_a_copy_of_the_real_run_refuses_when_its_predictions_are_touched(tmp_path) -> None:
    copy = tmp_path / "copy"
    copy.mkdir()
    for name in ("manifest.json", "predictions.npz", "evaluation-pairs.jsonl",
                 "training-records.json"):
        shutil.copy2(REAL / name, copy / name)
    manifest = json.loads((copy / "manifest.json").read_text())
    manifest["predictions"]["path"] = str(copy / "predictions.npz")
    manifest["evaluation_table"]["path"] = str(copy / "evaluation-pairs.jsonl")
    (copy / "manifest.json").write_text(json.dumps(manifest, indent=1, sort_keys=True))
    assert verify_and_load(copy).tags  # the copy is sound
    blob = np.load(copy / "predictions.npz", allow_pickle=True)
    kept = {k: blob[k] for k in blob.files}
    kept["dual-encoder-seed20260930"] = kept["dual-encoder-seed20260930"] + 1e-6
    np.savez_compressed(copy / "predictions.npz", **kept)
    with pytest.raises(RecomputeError, match="saved predictions have changed"):
        verify_and_load(copy)
