"""Regression tests for the M10 correction pass, driven through production paths.

Three defects, all reproduced before being fixed:

* `check_published()` compared the primary fields and ignored the secondary
  metrics it already had in hand, so editing `bedroc_alpha20` to 0.99 published
  `0.990`;
* a duplicated **failure** row passed reconciliation and produced the false
  summary "578 scored + 23 failed = 600 cohort members, each exactly once",
  because only the scored list was deduplicated and the arithmetic was asserted
  rather than checked;
* deleting the `published` section and two runs and setting `n_runs: 1` verified
  clean, because the CLI called `verify_manifest()` with no independent
  expectation of what the manifest should contain.

These go through `report.write()` and the Typer CLI, not the helpers underneath,
because that is where the defects actually lived.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from seq2lead.cli import app
from seq2lead.dock.artifacts import (
    VerificationContractError,
    load_verification_contract,
    verify_manifest_scoped,
)
from seq2lead.dock.cohort import COHORT_PATH, read_cohort
from seq2lead.dock.config import load_docking_config
from seq2lead.dock.report import write
from seq2lead.dock.verify import (
    DISPLAYED_SECONDARY,
    EvidenceMismatch,
    recompute_attrition,
    recompute_from_saved,
    verify_sensitivity,
)

CONTRACT = Path("configs/experiments/m10-docking-v1.yaml")
MANIFEST = Path("configs/manifests/m10_docking.json")
VERIFICATION = Path("configs/manifests/m10_verification.json")
GATE = Path("reports/results/m10_gate.json")
RUN = Path("data/m10/runs/gate-600")
SENSITIVITY = {
    "15 Å (0.75×)": Path("reports/results/m10_sensitivity_box-15A.json"),
    "25 Å (1.25×)": Path("reports/results/m10_sensitivity_box-25A.json"),
}


@pytest.fixture(scope="module")
def config():
    return load_docking_config(CONTRACT)


@pytest.fixture
def cohort():
    return read_cohort(COHORT_PATH)


def _forge_gate(tmp_path: Path, mutate) -> Path:
    payload = json.loads(GATE.read_text(encoding="utf-8"))
    mutate(payload)
    forged = tmp_path / "forged_gate.json"
    forged.write_text(json.dumps(payload), encoding="utf-8")
    return forged


# ========================================= 1. every displayed result is checked


@pytest.mark.parametrize("metric", DISPLAYED_SECONDARY)
def test_a_forged_secondary_metric_refuses_through_the_publisher(metric, tmp_path) -> None:
    """The exact defect: bedroc_alpha20 -> 0.99 published 0.990."""
    forged = _forge_gate(tmp_path, lambda p: p["gate"]["secondary"].__setitem__(metric, 0.99))
    target = tmp_path / "docking.md"
    with pytest.raises(EvidenceMismatch, match=metric):
        write(target, gate_path=forged, sensitivity_paths=SENSITIVITY)
    assert not target.exists(), "a refused report still wrote a file"


@pytest.mark.parametrize("metric", ["bedroc_alpha20", "ef_1pct", "mannwhitney_u"])
def test_a_missing_secondary_metric_refuses(metric, tmp_path) -> None:
    forged = _forge_gate(tmp_path, lambda p: p["gate"]["secondary"].pop(metric))
    with pytest.raises(EvidenceMismatch, match=f"required value '{metric}'"):
        write(tmp_path / "d.md", gate_path=forged, sensitivity_paths=SENSITIVITY)


@pytest.mark.parametrize("bad", [None, float("nan"), float("inf")])
def test_a_non_finite_required_value_refuses(bad, tmp_path) -> None:
    def mutate(payload):
        payload["gate"]["secondary"]["bedroc_alpha20"] = bad

    forged = _forge_gate(tmp_path, mutate)
    with pytest.raises(EvidenceMismatch, match="is null|is not finite|bedroc"):
        write(tmp_path / "d.md", gate_path=forged, sensitivity_paths=SENSITIVITY)


def test_the_real_secondary_metrics_all_recompute(cohort, config) -> None:
    recomputed = recompute_from_saved(RUN / "scores.json", RUN / "failures.json", cohort, config)
    published = json.loads(GATE.read_text(encoding="utf-8"))["gate"]["secondary"]
    for name in DISPLAYED_SECONDARY:
        assert published[name] == pytest.approx(recomputed.secondary[name], abs=1e-9), name


# ---- attrition


def test_a_forged_attrition_table_refuses(tmp_path) -> None:
    def mutate(payload):
        payload["attrition"]["active"]["failed"] = 0

    forged = _forge_gate(tmp_path, mutate)
    with pytest.raises(EvidenceMismatch, match="attrition table"):
        write(tmp_path / "d.md", gate_path=forged, sensitivity_paths=SENSITIVITY)


def test_a_forged_element_breakdown_refuses(tmp_path) -> None:
    """The 19-vs-21 correction is only durable if the breakdown is checked."""

    def mutate(payload):
        payload["attrition_chemistry"]["Se"] = 13

    forged = _forge_gate(tmp_path, mutate)
    with pytest.raises(EvidenceMismatch, match="attrition chemistry"):
        write(tmp_path / "d.md", gate_path=forged, sensitivity_paths=SENSITIVITY)


def test_a_forged_attrition_bound_refuses(tmp_path) -> None:
    def mutate(payload):
        payload["attrition_bounds"]["best_case_auroc"] = 0.95

    forged = _forge_gate(tmp_path, mutate)
    with pytest.raises(EvidenceMismatch, match="attrition bound best_case_auroc"):
        write(tmp_path / "d.md", gate_path=forged, sensitivity_paths=SENSITIVITY)


def test_the_real_attrition_recomputes(cohort) -> None:
    recomputed = recompute_attrition(RUN / "scores.json", RUN / "failures.json", cohort)
    published = json.loads(GATE.read_text(encoding="utf-8"))
    assert published["attrition"] == recomputed["attrition"]
    assert published["attrition_chemistry"] == recomputed["attrition_chemistry"]
    element = {
        k: v
        for k, v in recomputed["attrition_chemistry"].items()
        if "exotic" not in k and "SMILES" not in k
    }
    assert sum(element.values()) == 19


# ---- sensitivity rows


def test_each_sensitivity_row_verifies_against_its_own_run(cohort, config) -> None:
    for label, path in SENSITIVITY.items():
        assert verify_sensitivity(path, cohort, config) == [], label


def test_a_forged_sensitivity_result_refuses_through_the_publisher(tmp_path) -> None:
    source = SENSITIVITY["15 Å (0.75×)"]
    payload = json.loads(source.read_text(encoding="utf-8"))
    payload["gate"]["auroc"] = 0.95
    forged = tmp_path / "forged_sens.json"
    forged.write_text(json.dumps(payload), encoding="utf-8")
    target = tmp_path / "docking.md"
    with pytest.raises(EvidenceMismatch, match="sensitivity"):
        write(
            target,
            sensitivity_paths={**SENSITIVITY, "15 Å (0.75×)": forged},
        )
    assert not target.exists()


def test_a_sensitivity_row_pointing_at_a_missing_run_refuses(cohort, config, tmp_path) -> None:
    payload = json.loads(SENSITIVITY["25 Å (1.25×)"].read_text(encoding="utf-8"))
    payload["record"]["run"] = "box-no-such-run"
    forged = tmp_path / "s.json"
    forged.write_text(json.dumps(payload), encoding="utf-8")
    problems = verify_sensitivity(forged, cohort, config)
    assert any("scores.json is absent" in p for p in problems)


def test_a_sensitivity_row_claiming_the_primary_box_refuses(cohort, config, tmp_path) -> None:
    payload = json.loads(SENSITIVITY["25 Å (1.25×)"].read_text(encoding="utf-8"))
    payload["record"]["box_size"] = list(config.box_size)
    forged = tmp_path / "s.json"
    forged.write_text(json.dumps(payload), encoding="utf-8")
    problems = verify_sensitivity(forged, cohort, config)
    assert any("not a sensitivity run" in p for p in problems)


def test_a_sensitivity_row_whose_scale_disagrees_with_its_record_refuses(
    cohort, config, tmp_path
) -> None:
    payload = json.loads(SENSITIVITY["15 Å (0.75×)"].read_text(encoding="utf-8"))
    payload["scale"] = 1.1
    forged = tmp_path / "s.json"
    forged.write_text(json.dumps(payload), encoding="utf-8")
    assert any("claims scale" in p for p in verify_sensitivity(forged, cohort, config))


# ---- QA


def test_a_tampered_qa_artifact_refuses_through_the_publisher(tmp_path) -> None:
    qa = Path("reports/results/m10_qa_redock.json")
    backup = qa.read_bytes()
    target = tmp_path / "docking.md"
    try:
        payload = json.loads(backup)
        payload["top_pose_rmsd"] = 0.1
        qa.write_text(json.dumps(payload), encoding="utf-8")
        with pytest.raises(EvidenceMismatch, match="QA artifact"):
            write(target, sensitivity_paths=SENSITIVITY)
    finally:
        qa.write_bytes(backup)
    assert not target.exists()


def test_the_qa_check_now_recalculates_rather_than_caveating() -> None:
    """Supersedes an earlier test that asserted the RMSD was *not* recalculated.

    That caveat rested on the claim that recalculation needed re-docking, which
    was wrong: the saved pose and the crystal coordinates suffice. The check was
    strengthened rather than reworded, so the report should now record an
    independent recalculation.
    """
    from seq2lead.dock.verify import verify_qa

    problems, checks, unavailable = verify_qa(Path("reports/results/m10_qa_redock.json"), MANIFEST)
    assert problems == []
    assert unavailable == [], "all recalculation inputs are present locally"
    assert any("independently recalculated" in c for c in checks)
    body = Path("reports/docking.md").read_text(encoding="utf-8")
    assert "independently recalculated from the saved pose" in body
    assert "verified by digest only" not in body


# ================================== 2. scored/failed reconciliation


def test_a_duplicate_failure_row_refuses(cohort, config, tmp_path) -> None:
    """The exact defect: no problems, plus a false arithmetic claim."""
    failures = json.loads((RUN / "failures.json").read_text(encoding="utf-8"))
    failures.append(dict(failures[0]))
    path = tmp_path / "failures.json"
    path.write_text(json.dumps(failures), encoding="utf-8")
    recomputed = recompute_from_saved(RUN / "scores.json", path, cohort, config)
    assert not recomputed.ok
    assert any("more than once as failed" in p for p in recomputed.problems)
    assert any("which is not the cohort's" in p for p in recomputed.problems)
    assert not any("each exactly once" in c for c in recomputed.checks), (
        "the false arithmetic claim survived"
    )


def test_a_duplicate_scored_row_refuses(cohort, config, tmp_path) -> None:
    scores = json.loads((RUN / "scores.json").read_text(encoding="utf-8"))
    scores.append(dict(scores[0]))
    path = tmp_path / "scores.json"
    path.write_text(json.dumps(scores), encoding="utf-8")
    recomputed = recompute_from_saved(path, RUN / "failures.json", cohort, config)
    assert any("more than once as scored" in p for p in recomputed.problems)


def test_a_failure_with_the_wrong_label_refuses(cohort, config, tmp_path) -> None:
    failures = json.loads((RUN / "failures.json").read_text(encoding="utf-8"))
    failures[0]["label"] = "inactive" if failures[0]["label"] == "active" else "active"
    path = tmp_path / "failures.json"
    path.write_text(json.dumps(failures), encoding="utf-8")
    recomputed = recompute_from_saved(RUN / "scores.json", path, cohort, config)
    assert any("failure labels disagree with the cohort" in p for p in recomputed.problems)


def test_a_failure_with_an_unknown_id_refuses(cohort, config, tmp_path) -> None:
    failures = json.loads((RUN / "failures.json").read_text(encoding="utf-8"))
    failures[0]["compound_id"] = -1
    path = tmp_path / "failures.json"
    path.write_text(json.dumps(failures), encoding="utf-8")
    recomputed = recompute_from_saved(RUN / "scores.json", path, cohort, config)
    assert any("failed compounds absent from the cohort" in p for p in recomputed.problems)


def test_the_arithmetic_identity_is_checked_not_asserted(cohort, config, tmp_path) -> None:
    """Drop one failure: coverage breaks and the count identity must catch it."""
    failures = json.loads((RUN / "failures.json").read_text(encoding="utf-8"))
    path = tmp_path / "failures.json"
    path.write_text(json.dumps(failures[:-1]), encoding="utf-8")
    recomputed = recompute_from_saved(RUN / "scores.json", path, cohort, config)
    assert any("which is not the cohort's" in p for p in recomputed.problems)
    assert any("neither scored nor recorded as failed" in p for p in recomputed.problems)


def test_invalid_data_is_rejected_before_metrics_are_computed(cohort, config, tmp_path) -> None:
    failures = json.loads((RUN / "failures.json").read_text(encoding="utf-8"))
    failures.append(dict(failures[0]))
    path = tmp_path / "failures.json"
    path.write_text(json.dumps(failures), encoding="utf-8")
    recomputed = recompute_from_saved(RUN / "scores.json", path, cohort, config)
    assert recomputed.decision == "unverified"
    assert recomputed.auroc is None, "a metric was computed on data known to be invalid"


def test_the_real_reconciliation_reports_true_arithmetic(cohort, config) -> None:
    recomputed = recompute_from_saved(RUN / "scores.json", RUN / "failures.json", cohort, config)
    claim = next(c for c in recomputed.checks if "each exactly once" in c)
    import re

    scored, failed, total = map(int, re.findall(r"\d+", claim)[:3])
    assert scored + failed == total == len(cohort.members)


# ======================= 3. manifest completeness, through production paths


def test_the_verification_contract_exists_and_declares_what_is_required() -> None:
    contract = load_verification_contract(VERIFICATION)
    assert contract.primary_runs == ("gate-600",)
    assert set(contract.sensitivity_runs) == {"box-15A", "box-25A"}
    assert set(contract.required_publications) == {"gate", "qa", "sensitivity"}
    assert contract.required_publications["sensitivity"]["count"] == 2


def test_the_contract_lives_outside_the_manifest_it_checks() -> None:
    assert VERIFICATION != MANIFEST
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert "expected_runs" not in manifest, "the manifest must not declare its own completeness"


def test_a_missing_contract_refuses_rather_than_guessing(tmp_path) -> None:
    with pytest.raises(VerificationContractError, match="refuses to infer"):
        load_verification_contract(tmp_path / "absent.json")


@pytest.mark.parametrize("field", ["expected_runs", "required_publications"])
def test_an_incomplete_contract_refuses(field, tmp_path) -> None:
    raw = json.loads(VERIFICATION.read_text(encoding="utf-8"))
    del raw[field]
    path = tmp_path / "c.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(VerificationContractError):
        load_verification_contract(path)


def _run_cli_verify(manifest_payload, tmp_path) -> int:
    """Drive the real CLI against a doctored manifest and return its exit code.

    An earlier version of this helper monkeypatched `artifacts.MANIFEST_PATH`,
    which does nothing: the path is a default argument bound at definition time,
    so every doctored manifest silently verified the real one and the tests
    passed for the wrong reason. The CLI takes an explicit path instead.
    """
    doctored = tmp_path / "m10_docking.json"
    doctored.write_text(json.dumps(manifest_payload, indent=2, sort_keys=True), encoding="utf-8")
    result = CliRunner().invoke(app, ["dock", "verify-manifest", "--manifest", str(doctored)])
    return result.exit_code


def test_the_cli_verifies_the_real_manifest() -> None:
    result = CliRunner().invoke(app, ["dock", "verify-manifest"])
    assert result.exit_code == 0, result.output
    assert "m10-verification-v1" in result.output


def test_the_cli_rejects_the_gutted_manifest(tmp_path) -> None:
    """The exact defect: published deleted, two runs dropped, n_runs lowered to match."""
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    del manifest["published"]
    manifest["runs"] = [r for r in manifest["runs"] if r["run"] == "gate-600"]
    manifest["n_runs"] = 1
    assert _run_cli_verify(manifest, tmp_path) == 1


def test_the_cli_rejects_a_missing_publication_group(tmp_path) -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    del manifest["published"]["qa"]
    assert _run_cli_verify(manifest, tmp_path) == 1


def test_the_cli_rejects_an_empty_publication_group(tmp_path) -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    manifest["published"]["sensitivity"] = []
    assert _run_cli_verify(manifest, tmp_path) == 1


def test_the_cli_rejects_a_short_sensitivity_group(tmp_path) -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    manifest["published"]["sensitivity"] = manifest["published"]["sensitivity"][:1]
    assert _run_cli_verify(manifest, tmp_path) == 1


def test_the_cli_rejects_duplicate_publication_entries(tmp_path) -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    manifest["published"]["sensitivity"] = [manifest["published"]["sensitivity"][0]] * 2
    assert _run_cli_verify(manifest, tmp_path) == 1


def test_the_cli_rejects_an_unexpected_publication_group(tmp_path) -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    manifest["published"]["invented"] = [{"path": "x", "sha256": "y"}]
    assert _run_cli_verify(manifest, tmp_path) == 1


def test_the_cli_rejects_a_missing_primary_run(tmp_path) -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    manifest["runs"] = [r for r in manifest["runs"] if r["run"] != "gate-600"]
    manifest["n_runs"] = len(manifest["runs"])
    assert _run_cli_verify(manifest, tmp_path) == 1


def test_zip_scope_still_names_unavailable_directories(tmp_path) -> None:
    """The archive allowance must stay explicit, not become a blanket pass."""
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    for run in manifest["runs"]:
        run["ligand_dir"] = str(tmp_path / "absent" / "ligands")
        run["pose_dir"] = str(tmp_path / "absent" / "poses")
    path = tmp_path / "m.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    contract = load_verification_contract(VERIFICATION)
    scope = verify_manifest_scoped(path, contract=contract, allow_missing_trees=True)
    assert scope.problems == []
    assert len(scope.unavailable) == 6
    assert not scope.complete
    strict = verify_manifest_scoped(path, contract=contract)
    assert strict.problems, "without the allowance an absent directory must be a problem"


def test_the_publisher_refuses_a_manifest_that_fails_the_contract(tmp_path) -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    backup = MANIFEST.read_bytes()
    target = tmp_path / "docking.md"
    try:
        del manifest["published"]["sensitivity"]
        MANIFEST.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
        with pytest.raises(EvidenceMismatch, match="manifest"):
            write(target, sensitivity_paths=SENSITIVITY)
    finally:
        MANIFEST.write_bytes(backup)
    assert not target.exists()


# ============================ 4. corrected claims stay corrected


def test_the_uncertainty_claim_is_the_corrected_wording() -> None:
    body = Path("reports/docking.md").read_text(encoding="utf-8")
    assert "does not account for dependence among chemical analogues" in body
    assert "calibration under that dependence is unknown" in body
    for removed in ("true interval is **wider**", "lower bound on the true tail"):
        assert removed not in body, f"the unsupported claim survived: {removed!r}"


def test_the_provenance_overclaim_is_gone() -> None:
    for path in (CONTRACT, Path("reports/results/m10_corrections.md")):
        text = path.read_text(encoding="utf-8")
        assert "produced from exactly this content" not in text, path
    record = Path("reports/results/m10_corrections.md").read_text(encoding="utf-8")
    assert "does **not** establish that the recorded SMILES were" in record
    assert "structure-to-docking provenance" in record


def test_the_predeclared_statistics_and_decision_are_untouched(config) -> None:
    published = json.loads(GATE.read_text(encoding="utf-8"))["gate"]
    assert published["decision"] == "fail"
    assert published["auroc"] == pytest.approx(0.6240600153271386, abs=1e-15)
    assert published["ci_low"] == pytest.approx(0.578107936583964, abs=1e-15)
    assert published["ci_high"] == pytest.approx(0.670240953395919, abs=1e-15)
    assert config.effect_threshold == 0.70
    assert config.box_size == (20.0, 20.0, 20.0)


# ================================================== closeout: QA verification


def test_a_qa_override_is_verified_not_trusted(tmp_path) -> None:
    """The defect: render() took a qa_path, verification checked a hardcoded one.

    Pointing the report at a forged QA artifact published 0.01 Å while the real
    file sat untouched and verification passed.
    """
    qa = json.loads(Path("reports/results/m10_qa_redock.json").read_text(encoding="utf-8"))
    qa["top_pose_rmsd"] = 0.01
    qa["passed"] = True
    forged = tmp_path / "forged_qa.json"
    forged.write_text(json.dumps(qa), encoding="utf-8")
    target = tmp_path / "docking.md"
    with pytest.raises(EvidenceMismatch) as excinfo:
        write(target, qa_path=forged, sensitivity_paths=SENSITIVITY)
    message = str(excinfo.value)
    assert "binds no QA digest" in message or "recomputing it from" in message
    assert not target.exists(), "a refused report still wrote a file"


def test_a_qa_artifact_with_a_forged_rmsd_is_caught_by_recalculation(tmp_path) -> None:
    """Even bound and digest-consistent, a wrong RMSD must not survive."""
    from seq2lead.dock.verify import verify_qa

    qa_real = Path("reports/results/m10_qa_redock.json")
    backup = qa_real.read_bytes()
    try:
        payload = json.loads(backup)
        payload["top_pose_rmsd"] = 0.5
        payload["passed"] = True
        qa_real.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        problems, _checks, _unavailable = verify_qa(qa_real, MANIFEST)
        assert any("recomputing it from" in p for p in problems), problems
    finally:
        qa_real.write_bytes(backup)
    problems, checks, _ = verify_qa(qa_real, MANIFEST)
    assert problems == []
    assert any("independently recalculated" in c for c in checks)


def test_the_pose_rmsd_recalculates_without_re_docking() -> None:
    """The corrected claim: saved pose + crystal coordinates + a mapping suffice."""
    from seq2lead.dock.verify import recalculate_pose_rmsd

    pose = Path("data/m10/runs/qa-redock/SUA_redock.pdbqt")
    structure = Path("data/m10/structures/3k34.pdb")
    if not (pose.exists() and structure.exists()):
        pytest.skip("pose or structure not present locally")
    rmsd, missing = recalculate_pose_rmsd(pose, structure, "SUA")
    assert missing == []
    published = json.loads(Path("reports/results/m10_qa_redock.json").read_text(encoding="utf-8"))[
        "top_pose_rmsd"
    ]
    assert rmsd == pytest.approx(published, abs=1e-3)


@pytest.mark.parametrize("absent", ["pose", "structure"])
def test_a_missing_recalculation_input_is_named_individually(absent, tmp_path) -> None:
    """A ZIP-only reviewer must learn which file is missing, not just that one is."""
    from seq2lead.dock.verify import recalculate_pose_rmsd

    pose = Path("data/m10/runs/qa-redock/SUA_redock.pdbqt")
    structure = Path("data/m10/structures/3k34.pdb")
    if absent == "pose":
        pose = tmp_path / "absent_pose.pdbqt"
    else:
        structure = tmp_path / "absent_structure.pdb"
    rmsd, missing = recalculate_pose_rmsd(pose, structure, "SUA")
    assert rmsd is None
    assert len(missing) == 1
    assert str(pose if absent == "pose" else structure) in missing[0]


def test_zip_scope_qa_reports_digest_only_and_says_what_is_missing(tmp_path) -> None:
    from seq2lead.dock.verify import verify_qa

    problems, checks, unavailable = verify_qa(
        Path("reports/results/m10_qa_redock.json"),
        MANIFEST,
        pose_path=tmp_path / "absent.pdbqt",
        structure_path=tmp_path / "absent.pdb",
    )
    assert problems == []
    assert any("digest the manifest bound" in c for c in checks)
    assert any("absent.pdbqt" in u for u in unavailable)
    assert any("absent.pdb" in u for u in unavailable)
    assert any("Re-docking is NOT" in u for u in unavailable), (
        "the archive note must not repeat the corrected claim that re-docking is needed"
    )


def test_the_published_report_does_not_claim_rmsd_needs_re_docking() -> None:
    """Checked on the published report, where such a claim would actually mislead.

    The change record necessarily still contains the phrase, because it quotes
    and retracts it. A substring search cannot tell an assertion from its
    retraction, so this looks at the document that makes claims rather than the
    one that corrects them.
    """
    body = Path("reports/docking.md").read_text(encoding="utf-8")
    for phrasing in ("require re-docking", "requires re-docking"):
        assert phrasing not in body, f"the published report still claims: {phrasing!r}"
    record = Path("reports/results/m10_corrections.md").read_text(encoding="utf-8")
    assert "Pose RMSD does not require re-docking" in record
    assert "That was wrong" in record
