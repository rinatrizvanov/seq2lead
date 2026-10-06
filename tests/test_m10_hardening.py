"""The checks that stand between a tampered artifact and a published result.

Three defects motivate this file, all confirmed on disk before anything changed:

* the cohort's identity ignored SMILES, so swapping a member's structure for
  `CCO` left every recorded digest intact and the runner docked it;
* the manifest recorded the ligand and pose directory digests but never checked
  them, and an empty `runs` list with `n_runs: 3` verified clean;
* `render()` printed whatever `m10_gate.json` said, so editing that file to
  `decision: pass, auroc: 0.99` published a pass with 578 contradicting scores
  sitting beside it.

No test here runs AutoDock Vina, and none re-docks anything.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import pytest

from seq2lead.dock.artifacts import verify_manifest, verify_manifest_scoped
from seq2lead.dock.cohort import COHORT_PATH, read_cohort
from seq2lead.dock.config import ContractError, load_docking_config
from seq2lead.dock.receptor import PreparationError
from seq2lead.dock.runner import verify_cohort
from seq2lead.dock.verify import EvidenceMismatch, check_published, recompute_from_saved

CONTRACT = Path("configs/experiments/m10-docking-v1.yaml")
MANIFEST = Path("configs/manifests/m10_docking.json")
RUN = Path("data/m10/runs/gate-600")
GATE = Path("reports/results/m10_gate.json")


@pytest.fixture(scope="module")
def config():
    return load_docking_config(CONTRACT)


@pytest.fixture
def cohort():
    return read_cohort(COHORT_PATH)


# =================================================== 1. cohort content identity


def test_the_real_cohort_passes_every_check(cohort, config) -> None:
    verify_cohort(cohort, config)
    assert cohort.validate() == []


def test_the_content_digest_is_not_the_membership_digest(cohort) -> None:
    """The membership digest is SMILES-blind; that is the defect, kept visible."""
    swapped = dataclasses.replace(cohort.members[0], smiles="CCO")
    tampered = dataclasses.replace(cohort, members=[swapped, *cohort.members[1:]])
    assert tampered.membership_sha256() == cohort.membership_sha256()
    assert tampered.content_sha256() != cohort.content_sha256()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("smiles", "CCO"),
        ("evidence", "censored"),
        ("lo_pki", 1.0),
        ("hi_pki", 99.0),
        ("exact_median_pki", 1.0),
        ("n_exact", 999),
        ("n_censored", 999),
        ("n_heavy_atoms", 1),
    ],
)
def test_a_changed_member_field_refuses_before_preparation(
    cohort, config, field, value, monkeypatch
) -> None:
    from seq2lead.dock import runner

    monkeypatch.setattr(
        runner,
        "build_receptor",
        lambda *a, **k: pytest.fail("the receptor was prepared despite a tampered cohort"),
    )
    monkeypatch.setattr(
        runner,
        "prepare_ligand",
        lambda *a, **k: pytest.fail("a ligand was prepared despite a tampered cohort"),
    )
    tampered = dataclasses.replace(
        cohort,
        members=[dataclasses.replace(cohort.members[0], **{field: value}), *cohort.members[1:]],
    )
    with pytest.raises(PreparationError, match="do not match the contract's pin"):
        verify_cohort(tampered, config)


def test_a_changed_label_refuses(cohort, config) -> None:
    """Relabelling changes the declared class counts, which is caught first."""
    flipped = dataclasses.replace(cohort.members[0], label="inactive")
    tampered = dataclasses.replace(cohort, members=[flipped, *cohort.members[1:]])
    with pytest.raises(PreparationError, match="not internally consistent|do not match"):
        verify_cohort(tampered, config)


def test_a_label_outside_the_gate_classes_refuses(cohort, config) -> None:
    bad = dataclasses.replace(cohort.members[0], label="ambiguous")
    tampered = dataclasses.replace(cohort, members=[bad, *cohort.members[1:]])
    problems = tampered.validate()
    assert any("labels outside" in p for p in problems)
    with pytest.raises(PreparationError, match="not internally consistent"):
        verify_cohort(tampered, config)


def test_a_missing_member_refuses(cohort, config) -> None:
    short = dataclasses.replace(cohort, members=cohort.members[:-1])
    with pytest.raises(PreparationError, match="not internally consistent"):
        verify_cohort(short, config)
    assert any("declared" in p for p in short.validate())


def test_a_duplicate_compound_id_refuses(cohort, config) -> None:
    duped = dataclasses.replace(
        cohort, members=[cohort.members[0], *cohort.members[1:-1], cohort.members[0]]
    )
    assert any("duplicate compound ids" in p for p in duped.validate())
    with pytest.raises(PreparationError, match="not internally consistent"):
        verify_cohort(duped, config)


def test_a_member_with_no_structure_refuses(cohort, config) -> None:
    empty = dataclasses.replace(cohort.members[0], smiles="")
    tampered = dataclasses.replace(cohort, members=[empty, *cohort.members[1:]])
    assert any("no SMILES" in p for p in tampered.validate())
    with pytest.raises(PreparationError):
        verify_cohort(tampered, config)


def test_an_injected_cohort_faces_the_same_checks_as_a_loaded_one(
    config, tmp_path, monkeypatch
) -> None:
    """The file was guarded; an object handed straight in was not."""
    from seq2lead.dock import runner

    monkeypatch.setattr(
        runner,
        "build_receptor",
        lambda *a, **k: pytest.fail("prepared a receptor for an injected tampered cohort"),
    )
    loaded = read_cohort(COHORT_PATH)
    injected = dataclasses.replace(
        loaded,
        members=[dataclasses.replace(loaded.members[0], smiles="CCO"), *loaded.members[1:]],
    )
    with pytest.raises(PreparationError, match="do not match the contract's pin"):
        runner.run_cohort(config, "probe", cohort=injected, run_root=tmp_path)


def test_the_contract_refuses_to_default_the_cohort_pin(tmp_path) -> None:
    stripped = tmp_path / "c.yaml"
    stripped.write_text("version: x\n", encoding="utf-8")
    with pytest.raises(ContractError, match="does not declare cohort_pin"):
        _ = load_docking_config(stripped).expected_cohort_content_sha256


def test_the_pin_is_not_derived_from_the_cohort_being_checked(config, cohort) -> None:
    """Expected comes from the contract, actual from the cohort: different inputs."""
    text = CONTRACT.read_text(encoding="utf-8")
    assert config.expected_cohort_content_sha256 in text, (
        "the expected digest must be a literal in the contract, not recomputed"
    )
    assert config.expected_cohort_content_sha256 == cohort.content_sha256()


def test_the_pin_does_not_perturb_the_selection_identity(config, cohort) -> None:
    """Adding an attestation about the output must not restate the recipe."""
    assert cohort.selection_sha256 == config.selection_sha256()


# ===================================================== 2. manifest completeness


def test_the_real_manifest_verifies_completely() -> None:
    scope = verify_manifest_scoped(MANIFEST, expected_runs=("gate-600", "box-15A", "box-25A"))
    assert scope.problems == []
    assert scope.complete, f"not complete: {scope.unavailable}"
    assert len(scope.verified) > 20


def test_an_empty_run_list_no_longer_passes(tmp_path) -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    manifest["runs"] = []
    path = tmp_path / "m.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    problems = verify_manifest(path)
    assert any("declares n_runs=3 but holds 0" in p for p in problems)
    assert any("records no runs at all" in p for p in problems)


def test_a_duplicate_run_label_is_a_problem(tmp_path) -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    manifest["runs"].append(dict(manifest["runs"][0]))
    manifest["n_runs"] = len(manifest["runs"])
    path = tmp_path / "m.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    assert any("duplicate run labels" in p for p in verify_manifest(path))


def test_a_missing_expected_run_is_a_problem(tmp_path) -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    manifest["runs"] = [r for r in manifest["runs"] if r["run"] != "box-25A"]
    manifest["n_runs"] = len(manifest["runs"])
    path = tmp_path / "m.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    problems = verify_manifest(path, expected_runs=("gate-600", "box-15A", "box-25A"))
    assert any("box-25A" in p for p in problems)


@pytest.mark.parametrize("tree", ["ligand_dir", "pose_dir"])
def test_a_modified_file_in_a_referenced_directory_is_caught(tree, tmp_path) -> None:
    """A changed file inside a referenced tree must be caught.

    Run against a mirrored copy of the tree. The previous version edited a real
    ligand or pose file and restored it afterwards, so an interrupt left docking
    evidence altered on disk.
    """
    import m10_mirror

    before = m10_mirror.real_digests()
    mirror = m10_mirror.build(tmp_path / "mirror", trees=True)
    directory = mirror.run_value(tree)
    if not directory.exists():
        pytest.skip("directory not present")
    victim = sorted(p for p in directory.iterdir() if p.is_file())[0]
    victim.write_bytes(victim.read_bytes() + b"\n# injected\n")

    problems = verify_manifest(mirror.manifest)
    assert any("contents changed" in p for p in problems), problems
    assert m10_mirror.real_digests() == before


@pytest.mark.parametrize("tree", ["ligand_dir", "pose_dir"])
def test_an_added_file_in_a_referenced_directory_is_caught(tree, tmp_path) -> None:
    import m10_mirror

    before = m10_mirror.real_digests()
    mirror = m10_mirror.build(tmp_path / "mirror", trees=True)
    directory = mirror.run_value(tree)
    if not directory.exists():
        pytest.skip("directory not present")
    (directory / "_injected_probe.pdbqt").write_text("not part of the run", encoding="utf-8")

    problems = verify_manifest(mirror.manifest)
    assert any("holds" in p and "files" in p for p in problems), problems
    assert any("contents changed" in p for p in problems), problems
    assert m10_mirror.real_digests() == before


@pytest.mark.parametrize("tree", ["ligand_dir", "pose_dir"])
def test_a_removed_file_from_a_referenced_directory_is_caught(tree, tmp_path) -> None:
    import m10_mirror

    before = m10_mirror.real_digests()
    mirror = m10_mirror.build(tmp_path / "mirror", trees=True)
    directory = mirror.run_value(tree)
    if not directory.exists():
        pytest.skip("directory not present")
    victim = sorted(p for p in directory.iterdir() if p.is_file())[0]
    victim.unlink()

    problems = verify_manifest(mirror.manifest)
    assert problems, "removing a file from a referenced tree must be caught"
    assert m10_mirror.real_digests() == before


def test_an_absent_directory_is_a_problem_locally_and_named_in_zip_scope(tmp_path) -> None:
    """A review ZIP omits these; that must read differently from them being wrong."""
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    for run in manifest["runs"]:
        run["ligand_dir"] = str(tmp_path / "absent" / "ligands")
        run["pose_dir"] = str(tmp_path / "absent" / "poses")
    path = tmp_path / "m.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")

    strict = verify_manifest_scoped(path)
    assert any("missing at" in p for p in strict.problems)

    lenient = verify_manifest_scoped(path, allow_missing_trees=True)
    assert lenient.problems == []
    assert len(lenient.unavailable) == 6
    assert not lenient.complete, "an incomplete check must not report itself as complete"
    assert "not present locally" in lenient.summary()


def test_a_missing_tree_digest_cannot_be_skipped(tmp_path) -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    del manifest["runs"][0]["pose_dir_sha256"]
    path = tmp_path / "m.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    assert any("cannot be verified" in p for p in verify_manifest(path, allow_missing_trees=True))


def test_an_altered_contract_is_caught(tmp_path) -> None:
    altered = tmp_path / "contract.yaml"
    altered.write_text(
        CONTRACT.read_text(encoding="utf-8").replace("exhaustiveness: 8", "exhaustiveness: 32"),
        encoding="utf-8",
    )
    problems = verify_manifest(MANIFEST, config_path=altered)
    assert any("contract has changed" in p for p in problems)


def test_an_undeclared_run_contract_is_caught(tmp_path) -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    manifest["runs"][0]["config_sha256"] = "d" * 64
    path = tmp_path / "m.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    problems = verify_manifest(path)
    assert any("neither declares as current nor lists in superseded" in p for p in problems)


def test_the_superseded_contract_is_declared_not_rewritten() -> None:
    """A run keeps the digest it was produced under; the amendment is declared."""
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    declared = {c["sha256"] for c in manifest.get("superseded_contracts", [])}
    assert declared, "the earlier contract digest is not declared"
    for run in manifest["runs"]:
        assert run["config_sha256"] in declared | {manifest["config_sha256"]}
    # and the archived predecessor manifest is kept
    assert MANIFEST.with_name("m10_docking.v1.json").exists()


@pytest.mark.parametrize("key", ["gate", "qa", "sensitivity"])
def test_an_altered_published_result_is_caught(key, tmp_path) -> None:
    """An edited published result must be caught, on a mirror.

    The previous version wrote the injected key into the real
    `reports/results/m10_*.json` and relied on a `finally` block to put it back.
    """
    import m10_mirror

    before = m10_mirror.real_digests()
    mirror = m10_mirror.build(tmp_path / "mirror")
    victim = mirror.published(key)
    payload = json.loads(victim.read_text(encoding="utf-8"))
    payload["_injected"] = True
    victim.write_text(json.dumps(payload), encoding="utf-8")

    problems = verify_manifest(mirror.manifest, allow_missing_trees=True)
    assert any(f"published {key} bytes changed" in p for p in problems), problems
    assert m10_mirror.real_digests() == before


def test_a_clean_mirror_verifies_clean(tmp_path) -> None:
    """Without this the refusals above could pass on a mirror that never verified."""
    import m10_mirror

    mirror = m10_mirror.build(tmp_path / "mirror", trees=True)
    assert verify_manifest(mirror.manifest) == []


def test_the_recorded_result_follows_from_the_saved_scores(cohort, config) -> None:
    recomputed = recompute_from_saved(RUN / "scores.json", RUN / "failures.json", cohort, config)
    assert recomputed.ok, recomputed.problems
    published = json.loads(GATE.read_text(encoding="utf-8"))["gate"]
    assert check_published(published, recomputed) == []
    assert recomputed.decision == published["decision"]


def test_a_forged_decision_refuses_before_writing_anything(tmp_path) -> None:
    """The exact defect: decision=pass, auroc=0.99, published without a murmur."""
    from seq2lead.dock.report import write

    payload = json.loads(GATE.read_text(encoding="utf-8"))
    payload["gate"]["decision"] = "pass"
    payload["gate"]["auroc"] = 0.99
    forged = tmp_path / "forged.json"
    forged.write_text(json.dumps(payload), encoding="utf-8")
    target = tmp_path / "docking.md"
    with pytest.raises(EvidenceMismatch) as excinfo:
        write(target, gate_path=forged)
    assert "published decision 'pass'" in str(excinfo.value)
    assert "0.99" in str(excinfo.value)
    assert not target.exists(), "a refused report still wrote a file"


@pytest.mark.parametrize("metric", ["auroc", "ci_low", "ci_high"])
def test_a_nudged_metric_refuses(metric, tmp_path) -> None:
    from seq2lead.dock.report import write

    payload = json.loads(GATE.read_text(encoding="utf-8"))
    payload["gate"][metric] = payload["gate"][metric] + 0.02
    forged = tmp_path / "forged.json"
    forged.write_text(json.dumps(payload), encoding="utf-8")
    target = tmp_path / "docking.md"
    with pytest.raises(EvidenceMismatch, match=metric):
        write(target, gate_path=forged)
    assert not target.exists()


def test_a_relabelled_score_file_refuses(cohort, config, tmp_path) -> None:
    """Flipping labels in the scores would measure a different cohort."""
    rows = json.loads((RUN / "scores.json").read_text(encoding="utf-8"))
    rows[0]["label"] = "inactive" if rows[0]["label"] == "active" else "active"
    scores = tmp_path / "scores.json"
    scores.write_text(json.dumps(rows), encoding="utf-8")
    recomputed = recompute_from_saved(scores, RUN / "failures.json", cohort, config)
    assert not recomputed.ok
    assert any("labels disagree with the cohort" in p for p in recomputed.problems)


def test_a_flipped_score_direction_refuses(cohort, config, tmp_path) -> None:
    rows = json.loads((RUN / "scores.json").read_text(encoding="utf-8"))
    rows[0]["ranking_score"] = rows[0]["affinity_kcal_per_mol"]  # same sign: wrong
    scores = tmp_path / "scores.json"
    scores.write_text(json.dumps(rows), encoding="utf-8")
    recomputed = recompute_from_saved(scores, RUN / "failures.json", cohort, config)
    assert any("not the negated Vina energy" in p for p in recomputed.problems)


def test_an_incomplete_reconciliation_refuses(cohort, config, tmp_path) -> None:
    """Scored plus failed must account for the cohort exactly once each."""
    rows = json.loads((RUN / "scores.json").read_text(encoding="utf-8"))
    scores = tmp_path / "scores.json"
    scores.write_text(json.dumps(rows[:-5]), encoding="utf-8")
    recomputed = recompute_from_saved(scores, RUN / "failures.json", cohort, config)
    assert any("neither scored nor recorded as failed" in p for p in recomputed.problems)


def test_a_double_counted_compound_refuses(cohort, config, tmp_path) -> None:
    rows = json.loads((RUN / "scores.json").read_text(encoding="utf-8"))
    rows.append(dict(rows[0]))
    scores = tmp_path / "scores.json"
    scores.write_text(json.dumps(rows), encoding="utf-8")
    recomputed = recompute_from_saved(scores, RUN / "failures.json", cohort, config)
    assert any("more than once as scored" in p for p in recomputed.problems)


def test_a_compound_both_scored_and_failed_refuses(cohort, config, tmp_path) -> None:
    rows = json.loads((RUN / "scores.json").read_text(encoding="utf-8"))
    failures = json.loads((RUN / "failures.json").read_text(encoding="utf-8"))
    failures.append({**failures[0], "compound_id": rows[0]["compound_id"]})
    failed = tmp_path / "failures.json"
    failed.write_text(json.dumps(failures), encoding="utf-8")
    recomputed = recompute_from_saved(RUN / "scores.json", failed, cohort, config)
    assert any("both scored and recorded as failed" in p for p in recomputed.problems)


def test_a_mismatched_cohort_identity_refuses_publication(tmp_path) -> None:
    from seq2lead.dock.report import write

    swapped = tmp_path / "cohort.json"
    payload = json.loads(COHORT_PATH.read_text(encoding="utf-8"))
    payload["members"][0]["smiles"] = "CCO"
    swapped.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    target = tmp_path / "docking.md"
    with pytest.raises(EvidenceMismatch, match="contract pin"):
        write(target, cohort_path=swapped)
    assert not target.exists()


def test_a_mismatched_contract_identity_refuses_publication(tmp_path) -> None:
    from seq2lead.dock.report import write

    altered = tmp_path / "contract.yaml"
    altered.write_text(
        CONTRACT.read_text(encoding="utf-8").replace(
            "effect_threshold: 0.70", "effect_threshold: 0.55"
        ),
        encoding="utf-8",
    )
    target = tmp_path / "docking.md"
    with pytest.raises(EvidenceMismatch):
        write(target, config_path=altered)
    assert not target.exists()


def test_the_published_report_records_what_it_verified() -> None:
    body = Path("reports/docking.md").read_text(encoding="utf-8")
    assert "## Verified before publishing" in body
    for claim in (
        "cohort contents match the contract pin",
        "ranking_score == -affinity for every scored row",
        "each exactly once",
    ):
        assert claim in body, f"the report does not record the check: {claim!r}"


# ================================================= 4. reporting corrections


def test_the_element_attrition_total_is_unique_not_summed() -> None:
    payload = json.loads(GATE.read_text(encoding="utf-8"))
    chemistry = payload["attrition_chemistry"]
    element_rows = {k: v for k, v in chemistry.items() if "exotic" not in k and "SMILES" not in k}
    assert sum(element_rows.values()) == 19, "the unique element-related total moved"
    assert sum(chemistry.values()) == 22
    # the per-element sum is the figure that was wrong; keep the distinction visible
    per_element = sum(v * len(k.split(",")) for k, v in element_rows.items())
    assert per_element == 21
    body = Path("reports/docking.md").read_text(encoding="utf-8")
    assert "19 of the 22 dropped compounds" in body


def test_the_report_scopes_the_toolchain_claim() -> None:
    body = Path("reports/docking.md").read_text(encoding="utf-8")
    assert "meeko 0.8.0" in body and "1.2.7" in body
    assert "not a claim about AutoDock file formats in general" in body


def test_the_report_qualifies_the_bootstrap() -> None:
    """The qualification, in the wording the later correction pass settled on.

    An earlier version asserted the report called the true interval "wider".
    That claim was not supported -- the direction of the miscalibration is not
    known -- so it was removed, and this test now pins its replacement.
    """
    body = Path("reports/docking.md").read_text(encoding="utf-8")
    assert "resamples **individual compounds**" in body
    assert "congeneric" in body
    assert "does not account for dependence among chemical analogues" in body
    assert "calibration under that dependence is unknown" in body
    assert "true interval is **wider**" not in body
    assert "lower bound on the true tail" not in body


def test_the_report_labels_the_ranking_score_as_negated_energy() -> None:
    body = Path("reports/docking.md").read_text(encoding="utf-8")
    assert "−1 × best-pose Vina energy" in body
    assert "kcal/mol Vina energy" in body
    assert "affinity_kcal_per_mol" in body


def test_the_predeclared_rule_is_unchanged(config) -> None:
    """No new pass criterion, and the headline stays the 20 Å box."""
    gate = config.raw["gate"]
    assert gate["effect_threshold"] == 0.70
    assert gate["decision_rule"]["pass"] == "ci_low > 0.5 AND auroc >= 0.70"
    assert gate["decision_rule"]["fail"] == "ci_high < 0.70"
    assert config.box_size == (20.0, 20.0, 20.0)
    published = json.loads(GATE.read_text(encoding="utf-8"))["gate"]
    assert published["decision"] == "fail"
    assert published["effect_threshold"] == 0.70


def test_the_headline_numbers_are_byte_for_byte_what_was_docked() -> None:
    """The hardening pass must not have moved a single score."""
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    archived = json.loads(MANIFEST.with_name("m10_docking.v1.json").read_text(encoding="utf-8"))
    before = {r["run"]: r for r in archived["runs"]}
    for run in manifest["runs"]:
        for key in (
            "scores_sha256",
            "failures_sha256",
            "receptor_sha256",
            "ligand_dir_sha256",
            "pose_dir_sha256",
            "n_scored",
            "n_failed",
        ):
            assert run[key] == before[run["run"]][key], f"{run['run']}.{key} changed"
