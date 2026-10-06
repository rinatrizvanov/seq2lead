"""Publication integrity: what the real recomputation and publication paths refuse.

Three holes, each reproduced here as a regression test, each exercised through
`verify_and_load` / `publish` rather than against a reimplementation:

1. **a tampered evaluation table that preserves pair identity and order.**
   Flipping labels this way moved a published AUROC by 0.027 and was accepted,
   because only the pair column was checked. Labels, strata, eligibility and
   branch membership all live in that file, so its digest is what covers them.
2. **a stale PASSED verification record.** It described the inputs it saw, not
   the ones on disk, and authorised a report built from altered ones.
3. **expected digests refreshed during rendering.** The publisher recomputed the
   fit's recorded digests and wrote the new values in, so the change vanished
   into the record meant to catch it.

Every case must refuse **before** the results, the report or the manifest bytes
change, which is asserted by snapshotting all three and comparing afterwards.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import numpy as np
import pytest

from seq2lead.asof.publish import PublicationRefused, check_authorisation, publish
from seq2lead.asof.recompute import RecomputeError, score_run, verify_and_load
from seq2lead.asof.verify_results import verify

REAL = Path("data/asof/m11h/run-20261005T134842Z")
pytestmark = pytest.mark.skipif(not REAL.exists(), reason="the real run is not present")

COPIED = (
    "manifest.json",
    "predictions.npz",
    "evaluation-pairs.jsonl",
    "training-records.json",
    "protein-transform.npz",
)


def sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


@pytest.fixture
def run_dir(tmp_path) -> Path:
    """A self-contained clone of the real run, with the manifest repointed into it."""
    root = tmp_path / "run"
    root.mkdir()
    for name in COPIED:
        shutil.copy2(REAL / name, root / name)
    manifest = json.loads((root / "manifest.json").read_text())
    manifest["predictions"]["path"] = str(root / "predictions.npz")
    manifest["evaluation_table"]["path"] = str(root / "evaluation-pairs.jsonl")
    manifest["training_records"]["path"] = str(root / "training-records.json")
    manifest["transform"]["path"] = str(root / "protein-transform.npz")
    manifest["checkpoints"] = {}
    (root / "manifest.json").write_text(json.dumps(manifest, indent=1, sort_keys=True))
    return root


@pytest.fixture
def published(run_dir, tmp_path, monkeypatch) -> dict:
    """A clone taken all the way through the three gates, then snapshotted.

    The report path is redirected via `monkeypatch` so it is restored after the
    test. Assigning the module constant directly leaked the temp path into every
    later test in the session, which would have had a published report written
    somewhere nobody was looking.
    """
    from seq2lead.asof import results_report

    report = tmp_path / "report.md"
    manifest = tmp_path / "m11h_fit.json"
    monkeypatch.setattr(results_report, "REPORT", report)
    results = score_run(verify_and_load(run_dir))
    (run_dir / "results.json").write_text(
        json.dumps(results, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (run_dir / "verification.json").write_text(
        json.dumps(verify(run_dir), indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    publish(run_dir, manifest_path=manifest)
    return {
        "root": run_dir,
        "report": report,
        "manifest": manifest,
        "snapshot": {
            "results": (run_dir / "results.json").read_bytes(),
            "report": report.read_bytes(),
            "manifest": manifest.read_bytes(),
        },
    }


def assert_unchanged(published: dict) -> None:
    """Nothing published may have moved."""
    assert (published["root"] / "results.json").read_bytes() == published["snapshot"]["results"]
    assert published["report"].read_bytes() == published["snapshot"]["report"]
    assert published["manifest"].read_bytes() == published["snapshot"]["manifest"]


def flip_labels(root: Path, n: int = 50) -> int:
    """Change labels, keeping every pair identity and every row position."""
    rows = [json.loads(line) for line in (root / "evaluation-pairs.jsonl").open()]
    order_before = [r["pair"] for r in rows]
    flipped = 0
    for rec in rows:
        arm = rec["arms"]["declared_increment"]
        if flipped < n and arm["scoreable"] and arm["label"] in ("active", "inactive"):
            arm["label"] = "inactive" if arm["label"] == "active" else "active"
            flipped += 1
    (root / "evaluation-pairs.jsonl").write_text(
        "".join(json.dumps(r, sort_keys=True) + "\n" for r in rows), encoding="utf-8"
    )
    after = [json.loads(line)["pair"] for line in (root / "evaluation-pairs.jsonl").open()]
    assert after == order_before, "the test must preserve pair order to be the real case"
    return flipped


def mutate_table(root: Path, mutate) -> None:
    rows = [json.loads(line) for line in (root / "evaluation-pairs.jsonl").open()]
    mutate(rows)
    (root / "evaluation-pairs.jsonl").write_text(
        "".join(json.dumps(r, sort_keys=True) + "\n" for r in rows), encoding="utf-8"
    )


# ===================================== 1. the table's contents, not just its keys


def test_the_baseline_clone_publishes(published) -> None:
    """The valid path must still work, or the refusals below prove nothing."""
    assert published["report"].exists()
    manifest = json.loads(published["manifest"].read_text())
    assert manifest["authorised_by"]["passed"] is True
    assert manifest["expected_fit_artifacts"]["digests"]


def test_flipped_labels_refuse_although_pair_order_is_intact(published) -> None:
    """The reproduction: 400 flipped labels moved a published AUROC by 0.027."""
    flipped = flip_labels(published["root"], 400)
    assert flipped == 400
    with pytest.raises(RecomputeError, match="evaluation table has changed"):
        verify_and_load(published["root"])
    with pytest.raises((PublicationRefused, RecomputeError)):
        publish(published["root"], manifest_path=published["manifest"])
    assert_unchanged(published)


def test_flipping_a_single_label_refuses(published) -> None:
    assert flip_labels(published["root"], 1) == 1
    with pytest.raises(RecomputeError, match="evaluation table has changed"):
        verify_and_load(published["root"])
    assert_unchanged(published)


def test_an_altered_stratum_refuses(published) -> None:
    def mutate(rows):
        rows[0]["stratum"] = "recurrent"

    mutate_table(published["root"], mutate)
    with pytest.raises(RecomputeError, match="strata"):
        verify_and_load(published["root"])
    assert_unchanged(published)


def test_an_altered_branch_flag_refuses(published) -> None:
    def mutate(rows):
        rows[0]["arms"]["declared_increment"]["branches"]["screened_primary"] = False

    mutate_table(published["root"], mutate)
    with pytest.raises(RecomputeError, match="branch membership"):
        verify_and_load(published["root"])
    assert_unchanged(published)


def test_altered_eligibility_refuses(published) -> None:
    def mutate(rows):
        rows[0]["arms"]["declared_increment"]["scoreable"] = False

    mutate_table(published["root"], mutate)
    with pytest.raises(RecomputeError, match="eligibility"):
        verify_and_load(published["root"])
    assert_unchanged(published)


def test_an_altered_selection_flag_refuses(published) -> None:
    def mutate(rows):
        rows[0]["participated_in_model_selection"] = True

    mutate_table(published["root"], mutate)
    with pytest.raises(RecomputeError, match="evaluation table has changed"):
        verify_and_load(published["root"])
    assert_unchanged(published)


# ============================================ 2. a stale PASSED record authorises nothing


def test_a_stale_passed_record_does_not_authorise_publication(published) -> None:
    """The record says passed; it was written before the table changed."""
    record = json.loads((published["root"] / "verification.json").read_text())
    assert record["passed"] is True
    flip_labels(published["root"], 400)
    with pytest.raises(PublicationRefused, match="verification record is stale"):
        check_authorisation(verify_and_load(published["root"], expect_digest=False))
    with pytest.raises((PublicationRefused, RecomputeError)):
        publish(published["root"], manifest_path=published["manifest"])
    assert_unchanged(published)


def test_a_stale_record_is_refused_even_when_the_results_alone_changed(published) -> None:
    """Rescoring without re-verifying leaves the record bound to the old results."""
    results = json.loads((published["root"] / "results.json").read_text())
    results["model_tags"] = sorted(results["model_tags"])[:3]
    (published["root"] / "results.json").write_text(
        json.dumps(results, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    with pytest.raises(PublicationRefused, match="results_sha256"):
        publish(published["root"], manifest_path=published["manifest"])
    assert published["manifest"].read_bytes() == published["snapshot"]["manifest"]
    assert published["report"].read_bytes() == published["snapshot"]["report"]


def test_a_record_without_a_binding_is_refused(published) -> None:
    """A pre-binding record cannot be shown to describe the current inputs."""
    record = json.loads((published["root"] / "verification.json").read_text())
    record.pop("bound_to")
    (published["root"] / "verification.json").write_text(json.dumps(record, indent=2))
    with pytest.raises(PublicationRefused, match="carries no `bound_to`"):
        publish(published["root"], manifest_path=published["manifest"])
    assert_unchanged(published)


def test_a_failed_record_is_refused(published) -> None:
    record = json.loads((published["root"] / "verification.json").read_text())
    record["passed"] = False
    record["failures"] = ["deliberate"]
    (published["root"] / "verification.json").write_text(
        json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    # Re-bind so only the verdict, not the binding, is at issue.
    from seq2lead.asof.recompute import current_digests

    record["bound_to"] = current_digests(verify_and_load(published["root"]))
    (published["root"] / "verification.json").write_text(
        json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    with pytest.raises(PublicationRefused, match="did not pass"):
        publish(published["root"], manifest_path=published["manifest"])


def test_a_missing_record_is_refused(published) -> None:
    (published["root"] / "verification.json").unlink()
    with pytest.raises(PublicationRefused, match="requires a verification record"):
        publish(published["root"], manifest_path=published["manifest"])
    assert_unchanged(published)


def test_results_marked_not_publishable_are_refused(published) -> None:
    results = json.loads((published["root"] / "results.json").read_text())
    results["NOT_PUBLISHABLE"] = "scored with checks waived"
    (published["root"] / "results.json").write_text(
        json.dumps(results, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    with pytest.raises(PublicationRefused, match="not publishable"):
        publish(published["root"], manifest_path=published["manifest"])


# ================================ 3. expected digests are compared, never refreshed


def test_publication_does_not_rewrite_the_fits_recorded_digests(published) -> None:
    """The laundering case: a changed artifact must not acquire a fresh digest."""
    fit = json.loads((published["root"] / "manifest.json").read_text())
    manifest = json.loads(published["manifest"].read_text())
    expected = manifest["expected_fit_artifacts"]["digests"]
    assert expected[fit["predictions"]["path"]] == fit["predictions"]["sha256"]
    assert expected[fit["evaluation_table"]["path"]] == fit["evaluation_table"]["sha256"]
    assert "never recomputed here" in manifest["expected_fit_artifacts"]["source"]


def test_a_tampered_prediction_array_refuses_before_the_manifest_moves(published) -> None:
    blob = np.load(published["root"] / "predictions.npz", allow_pickle=True)
    kept = {k: blob[k] for k in blob.files}
    kept["dual-encoder-seed20260930"] = kept["dual-encoder-seed20260930"] + 1e-6
    np.savez_compressed(published["root"] / "predictions.npz", **kept)
    with pytest.raises(RecomputeError, match="saved predictions have changed"):
        verify_and_load(published["root"])
    with pytest.raises((PublicationRefused, RecomputeError)):
        publish(published["root"], manifest_path=published["manifest"])
    assert_unchanged(published)


def test_a_deliberate_bump_preserves_the_manifest_it_supersedes(published) -> None:
    """A version change is explicit and keeps history, unlike a silent refresh."""
    out = publish(published["root"], bump="m11h-fit-v9", manifest_path=published["manifest"])
    preserved = Path(out["previous_manifest_preserved_as"])
    assert preserved.exists()
    assert preserved.read_bytes() == published["snapshot"]["manifest"]
    now = json.loads(published["manifest"].read_text())
    assert now["manifest"] == "m11h-fit-v9"
    assert now["supersedes"] == "m11h-fit-v2"


# ======================================= the verification's own strength


def test_verification_compares_against_the_published_result(published) -> None:
    """Altering the published results must fail the check, not pass by self-agreement."""
    results = json.loads((published["root"] / "results.json").read_text())
    cell = results["cells"]["declared_increment/screened_primary"]
    cell["groups"]["new_to_fitting"]["by_model"]["dual-encoder-seed20260930"]["metrics"][
        "auroc"
    ] = 0.99
    (published["root"] / "results.json").write_text(
        json.dumps(results, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    verdict = verify(published["root"])
    assert verdict["checks"]["published_results_reproduce_from_saved_predictions"] is False
    assert verdict["passed"] is False
    assert any("do not reproduce the published" in f for f in verdict["failures"])


def test_untouched_predictions_reproduce_the_published_results(published) -> None:
    verdict = verify(published["root"])
    assert verdict["checks"]["published_results_reproduce_from_saved_predictions"] is True
    assert verdict["checks"]["scoring_is_deterministic"] is True
    assert verdict["passed"] is True


def test_absent_artifacts_are_named_not_counted_as_checked(published, tmp_path) -> None:
    """A ZIP-only copy must say which checks could not run."""
    zip_like = tmp_path / "zip-only"
    zip_like.mkdir()
    for name in (*COPIED, "results.json", "verification.json"):
        shutil.copy2(published["root"] / name, zip_like / name)
    manifest = json.loads((zip_like / "manifest.json").read_text())
    for key in ("predictions", "evaluation_table", "training_records", "transform"):
        manifest[key]["path"] = str(zip_like / Path(manifest[key]["path"]).name)
    manifest["checkpoints"] = {str(zip_like / "checkpoints" / "absent.pt"): "0" * 64}
    (zip_like / "manifest.json").write_text(json.dumps(manifest, indent=1, sort_keys=True))
    verdict = verify(zip_like)
    assert verdict["checks"]["artifact_digests_unavailable"] >= 1
    assert verdict["checks"]["artifact_digests_stale"] == 0
    named = verdict["checks_not_performed"]
    assert "artifact_digests_not_checked_because_the_file_is_absent" in named
    assert any("absent.pt" in str(v) for v in named.values())
