"""Artifact verification, result selection, and the stratum footer.

Each test corresponds to a way the published leaderboard could be wrong while
every number in it looked reasonable: predictions that are not the ones the
numbers came from, a stale result version rendered after a newer run, or a
denominator borrowed from a different population.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import numpy as np
import pytest

from seq2lead.db import connect
from seq2lead.eval import load_experiment
from seq2lead.eval.verify import (
    MANIFEST_PATH,
    VerificationError,
    load_manifest,
    verify_predictions,
)

PREDICTIONS = Path("data/predictions")


@pytest.fixture(scope="module")
def experiment():
    with connect() as conn:
        yield load_experiment(conn)


@pytest.fixture
def sandbox(tmp_path):
    """A copy of a few real prediction files, plus a manifest naming exactly them."""
    if not MANIFEST_PATH.exists() or not PREDICTIONS.exists():
        pytest.skip("no frozen manifest or predictions")
    raw = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    chosen = [r for r in raw["runs"] if r["split"] == "random_pair-v3"][:3]
    if len(chosen) < 3:  # noqa: PLR2004
        pytest.skip("not enough runs to sandbox")
    directory = tmp_path / "predictions"
    directory.mkdir()
    for run in chosen:
        source = PREDICTIONS / run["filename"]
        if not source.exists():
            pytest.skip(f"{run['filename']} missing")
        shutil.copy(source, directory / run["filename"])
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps({**raw, "runs": chosen, "n_runs": len(chosen)}, indent=2), encoding="utf-8"
    )
    return directory, load_manifest(manifest_path)


@pytest.mark.requires_db
def test_the_real_artifacts_verify(experiment) -> None:
    if not MANIFEST_PATH.exists():
        pytest.skip("no frozen manifest")
    manifest = load_manifest()
    with connect() as conn:
        report = verify_predictions(conn, experiment, PREDICTIONS, manifest)
    assert report.n_verified == report.n_expected == len(manifest.runs)
    assert report.checks


@pytest.mark.requires_db
def test_a_missing_prediction_file_is_refused(experiment, sandbox) -> None:
    directory, manifest = sandbox
    (directory / manifest.runs[0].filename).unlink()
    with connect() as conn, pytest.raises(VerificationError, match="missing"):
        verify_predictions(conn, experiment, directory, manifest)


@pytest.mark.requires_db
def test_an_unexpected_extra_run_is_refused(experiment, sandbox) -> None:
    """A stray file must not silently join the result set."""
    directory, manifest = sandbox
    shutil.copy(
        directory / manifest.runs[0].filename, directory / "B9-not-in-the-manifest__x__seeddet.npz"
    )
    with connect() as conn, pytest.raises(VerificationError, match="not in the manifest"):
        verify_predictions(conn, experiment, directory, manifest)


@pytest.mark.requires_db
def test_modified_bytes_are_refused(experiment, sandbox) -> None:
    """The digest check is the point: these must be the bytes behind the numbers."""
    directory, manifest = sandbox
    victim = directory / manifest.runs[0].filename
    with np.load(victim, allow_pickle=False) as data:
        arrays = {k: data[k] for k in data.files}
    arrays["prediction"] = arrays["prediction"] + 0.001
    np.savez_compressed(victim, **arrays)
    with connect() as conn, pytest.raises(VerificationError, match="frozen digest"):
        verify_predictions(conn, experiment, directory, manifest)


@pytest.mark.requires_db
@pytest.mark.parametrize(
    ("mutation", "pattern"),
    [
        ("drop_array", "has no `prediction` array"),
        ("length_mismatch", "mismatched lengths"),
        ("duplicate_pairs", "duplicate ranking pair ids"),
        ("bad_label", "labels outside"),
        ("non_finite", "non-finite"),
        ("unknown_pair", "not in the test cohort"),
        ("wrong_label", "disagree with the test cohort"),
    ],
)
def test_malformed_or_miscohorted_artifacts_are_refused(
    experiment, sandbox, mutation, pattern
) -> None:
    directory, manifest = sandbox
    victim = directory / manifest.runs[0].filename
    with np.load(victim, allow_pickle=False) as data:
        arrays = {k: data[k] for k in data.files}

    if mutation == "drop_array":
        arrays.pop("prediction")
    elif mutation == "length_mismatch":
        arrays["prediction"] = arrays["prediction"][:-1]
    elif mutation == "duplicate_pairs":
        arrays["compound_id"] = arrays["compound_id"].copy()
        arrays["target_id"] = arrays["target_id"].copy()
        arrays["compound_id"][1] = arrays["compound_id"][0]
        arrays["target_id"][1] = arrays["target_id"][0]
    elif mutation == "bad_label":
        arrays["label"] = arrays["label"].astype("<U16").copy()
        arrays["label"][0] = "maybe"
    elif mutation == "non_finite":
        arrays["prediction"] = arrays["prediction"].copy()
        arrays["prediction"][0] = np.nan
    elif mutation == "unknown_pair":
        arrays["compound_id"] = arrays["compound_id"].copy()
        arrays["compound_id"][0] = 999_999_999
    elif mutation == "wrong_label":
        arrays["label"] = arrays["label"].astype("<U16").copy()
        flip = {"active": "inactive", "inactive": "active"}
        arrays["label"][0] = flip[str(arrays["label"][0])]

    np.savez_compressed(victim, **arrays)
    # Re-point the manifest at the mutated bytes so the digest check passes and
    # the *content* checks are what fire.
    import dataclasses

    from seq2lead.features.store import sha256_file

    runs = list(manifest.runs)
    runs[0] = dataclasses.replace(runs[0], sha256=sha256_file(victim))
    manifest.runs = tuple(runs)

    with connect() as conn, pytest.raises(VerificationError, match=pattern):
        verify_predictions(conn, experiment, directory, manifest)


@pytest.mark.requires_db
def test_a_verification_failure_publishes_nothing(experiment, tmp_path) -> None:
    """The whole point: a bad artifact must leave existing results untouched."""
    from seq2lead.eval.recompute import recompute_all

    results = tmp_path / "results"
    results.mkdir()
    before = sorted(p.name for p in results.iterdir())
    empty_predictions = tmp_path / "empty"
    empty_predictions.mkdir()
    with connect() as conn, pytest.raises(VerificationError):
        recompute_all(
            conn, experiment, "m8/v1", prediction_dir=empty_predictions, result_dir=results
        )
    assert sorted(p.name for p in results.iterdir()) == before, "output was written anyway"


@pytest.mark.requires_db
def test_the_manifest_is_not_derived_from_the_files_it_checks() -> None:
    """Expected digests must predate the verification, or the check is circular."""
    if not MANIFEST_PATH.exists():
        pytest.skip("no frozen manifest")
    raw = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    provenance = raw["provenance"].lower()
    assert "correction_v2.json" in provenance or "artifacts.md" in provenance
    assert "not regenerated" in provenance or "not be regenerated" in provenance
    assert raw["n_runs"] == len(raw["runs"])
    assert len({r["filename"] for r in raw["runs"]}) == raw["n_runs"]
