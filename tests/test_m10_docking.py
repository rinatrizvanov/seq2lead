"""M10 docking gate: the things that would silently invalidate a gate decision.

No test here runs AutoDock Vina. The engine is exercised once, for real, by the
recorded runs; what these tests protect is everything around it -- the sign of
the score, who is allowed into the cohort, what happens to a compound that
cannot be prepared, and whether a published decision can be recomputed.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from seq2lead.dock.artifacts import ArtifactCollision, preflight, run_paths, verify_manifest
from seq2lead.dock.config import ContractError, load_docking_config
from seq2lead.dock.gate import FAIL, INSUFFICIENT, PASS, bedroc, evaluate_gate
from seq2lead.dock.ligands import LigandFailure, prepare_ligand

CONTRACT = Path("configs/experiments/m10-docking-v1.yaml")
COHORT = Path("reports/results/m10_cohort.json")


@pytest.fixture(scope="module")
def config():
    return load_docking_config(CONTRACT)


# ============================================================ score direction


def test_the_ranking_score_negates_vina_energy(tmp_path) -> None:
    """Vina reports a free energy: more negative binds tighter.

    If this sign is ever flipped the gate silently inverts, and a protocol that
    ranks inactives first would pass.
    """
    from seq2lead.dock.engine import DockResult

    tight = DockResult(1, -11.0, tmp_path / "a.pdbqt", 1.0)
    weak = DockResult(2, -4.0, tmp_path / "b.pdbqt", 1.0)
    assert tight.ranking_score > weak.ranking_score
    assert tight.ranking_score == 11.0


def test_the_contract_states_the_score_direction(config) -> None:
    gate = config.raw["gate"]
    assert gate["score_direction"] == "lower_vina_energy_is_better"
    assert "-1" in gate["ranking_score"]
    assert gate["decision_rule"]["wrong_direction_is_failure"] is True


def test_separation_in_the_wrong_direction_fails() -> None:
    """The requirement a two-sided test alone cannot meet."""
    rng = np.random.default_rng(7)
    positive = np.concatenate([np.ones(200, bool), np.zeros(200, bool)])
    # inactives score HIGHER: strongly significant, and wrong
    scores = np.concatenate([rng.normal(0.0, 1.0, 200), rng.normal(2.0, 1.0, 200)])
    result = evaluate_gate(
        scores,
        positive,
        effect_threshold=0.70,
        resamples=500,
        level=0.95,
        seed=1,
        minimum_per_class=50,
    )
    assert result.auroc < 0.5
    assert result.decision == FAIL
    assert "wrong direction" in result.reason
    # and the two-sided test is wildly significant, which must not rescue it
    assert result.secondary["mannwhitney_p_two_sided"] < 1e-20


@pytest.mark.parametrize(
    ("shift", "expected"),
    [(2.0, PASS), (0.0, FAIL), (-2.0, FAIL)],
)
def test_the_decision_rule_as_declared(shift, expected) -> None:
    rng = np.random.default_rng(11)
    positive = np.concatenate([np.ones(250, bool), np.zeros(250, bool)])
    scores = np.concatenate([rng.normal(shift, 1.0, 250), rng.normal(0.0, 1.0, 250)])
    result = evaluate_gate(
        scores,
        positive,
        effect_threshold=0.70,
        resamples=500,
        level=0.95,
        seed=1,
        minimum_per_class=50,
    )
    assert result.decision == expected


def test_an_underpowered_cohort_is_inconclusive_not_a_pass() -> None:
    """A wide interval straddling the threshold is a real answer, not a pass."""
    rng = np.random.default_rng(1)
    positive = np.concatenate([np.ones(60, bool), np.zeros(60, bool)])
    scores = np.concatenate([rng.normal(0.55, 1.0, 60), rng.normal(0.0, 1.0, 60)])
    result = evaluate_gate(
        scores,
        positive,
        effect_threshold=0.70,
        resamples=800,
        level=0.95,
        seed=1,
        minimum_per_class=50,
    )
    # self-validating: assert the band first, so a drifting fixture fails loudly
    # here rather than quietly testing a different branch of the rule
    assert result.auroc < 0.70 < result.ci_high, "fixture no longer straddles the threshold"
    assert result.ci_low > 0.5
    assert result.decision == "inconclusive"


def test_too_few_survivors_refuses_to_score_at_all() -> None:
    positive = np.concatenate([np.ones(10, bool), np.zeros(300, bool)])
    scores = np.random.default_rng(0).normal(0, 1, 310)
    result = evaluate_gate(
        scores,
        positive,
        effect_threshold=0.70,
        resamples=100,
        level=0.95,
        seed=1,
        minimum_per_class=50,
    )
    assert result.decision == INSUFFICIENT
    assert result.auroc is None, "no metric should be reported below the usable minimum"


def test_a_fully_tied_ranking_scores_at_chance_not_at_zero() -> None:
    """exp(-alpha * midrank) is not the tie-average; BEDROC must not collapse."""
    positive = np.concatenate([np.ones(20, bool), np.zeros(180, bool)])
    tied = bedroc(np.zeros(200), positive)
    rng = np.random.default_rng(0)
    random_mean = float(np.mean([bedroc(rng.normal(0, 1, 200), positive) for _ in range(100)]))
    assert tied == pytest.approx(random_mean, abs=0.03)
    assert tied > 0.05, "a tied ranking collapsed to zero instead of chance level"


# ========================================================= cohort eligibility


def test_the_frozen_cohort_admits_only_decisive_measured_labels() -> None:
    raw = json.loads(COHORT.read_text(encoding="utf-8"))
    labels = {m["label"] for m in raw["members"]}
    assert labels == {"active", "inactive"}
    assert "ambiguous" not in labels and "none" not in labels
    evidence = {m["evidence"] for m in raw["members"]}
    assert evidence <= {"exact", "censored", "both"}


def test_censored_negatives_are_present_and_keep_their_bounds() -> None:
    """Dropping them would discard nearly half the negative class."""
    raw = json.loads(COHORT.read_text(encoding="utf-8"))
    censored = [m for m in raw["members"] if m["evidence"] == "censored"]
    assert len(censored) >= 100, "the censored negatives vanished from the cohort"
    assert all(m["label"] == "inactive" for m in censored)
    # the relation survives as a bound rather than being flattened to a point
    assert all(m["lo_pki"] is not None or m["hi_pki"] is not None for m in censored)
    assert all(m["exact_median_pki"] is None for m in censored)


@pytest.mark.requires_db
def test_the_eligibility_query_excludes_what_the_contract_excludes(config) -> None:
    from seq2lead.db import connect
    from seq2lead.dock.cohort import eligible_rows

    with connect() as conn:
        rows = eligible_rows(conn, config.endpoint_id, config.target_id)
        ineligible = conn.execute(
            """
            SELECT count(*) FROM pair_label
            WHERE endpoint_id=%s AND target_id=%s
              AND (status <> 'ok' OR excluded_from_eval OR NOT in_benchmark_scope
                   OR label NOT IN ('active','inactive'))
            """,
            (config.endpoint_id, config.target_id),
        ).fetchone()[0]
    assert ineligible > 0, "the fixture target has no ineligible rows, so this proves nothing"
    assert all(r["label"] in ("active", "inactive") for r in rows)
    ids = {r["compound_id"] for r in rows}
    assert len(ids) == len(rows), "a compound appeared twice in the eligible pool"


@pytest.mark.requires_db
def test_the_cohort_is_reproducible_from_its_seed(config) -> None:
    from seq2lead.db import connect
    from seq2lead.dock.cohort import build_cohort

    with connect() as conn:
        a = build_cohort(conn, config)
        b = build_cohort(conn, config)
    assert a.membership_sha256() == b.membership_sha256()
    frozen = json.loads(COHORT.read_text(encoding="utf-8"))
    assert [m["compound_id"] for m in frozen["members"]] == [m.compound_id for m in a.members], (
        "the frozen cohort no longer matches what the contract reproduces"
    )


def test_changing_the_protocol_does_not_invalidate_the_cohort(config, tmp_path) -> None:
    """Exhaustiveness does not decide who is in the cohort, so it must not rebind it."""
    text = CONTRACT.read_text(encoding="utf-8")
    altered = tmp_path / "altered.yaml"
    altered.write_text(text.replace("exhaustiveness: 8", "exhaustiveness: 32"), encoding="utf-8")
    other = load_docking_config(altered)
    assert other.config_sha256 != config.config_sha256
    assert other.selection_sha256() == config.selection_sha256()


def test_changing_the_endpoint_does_invalidate_the_cohort(config, tmp_path) -> None:
    text = CONTRACT.read_text(encoding="utf-8")
    altered = tmp_path / "altered.yaml"
    altered.write_text(text.replace("  name: ki-pki6-v2", "  name: ki-pki8-v2"), encoding="utf-8")
    assert load_docking_config(altered).selection_sha256() != config.selection_sha256()


# ======================================================== failed preparation


def test_an_unpreparable_ligand_is_reported_not_silently_dropped(tmp_path) -> None:
    out = prepare_ligand(99, "this is not a smiles", tmp_path / "x.pdbqt", seed=1)
    assert isinstance(out, LigandFailure)
    assert out.compound_id == 99
    assert out.stage == "parse"
    assert not (tmp_path / "x.pdbqt").exists(), "a failure left a file behind"


def test_a_failed_compound_is_never_scored_as_worst() -> None:
    """Filling failures with the minimum would invent the ordering under test."""
    scores = json.loads(Path("data/m10/runs/gate-600/scores.json").read_text(encoding="utf-8"))
    failures = json.loads(Path("data/m10/runs/gate-600/failures.json").read_text(encoding="utf-8"))
    scored_ids = {s["compound_id"] for s in scores}
    failed_ids = {f["compound_id"] for f in failures}
    assert not (scored_ids & failed_ids), "a failed compound also carries a score"


def test_attrition_is_reported_per_class() -> None:
    """A dropout that hits one class harder would bias the gate invisibly."""
    from seq2lead.dock.runner import attrition_by_class

    table = attrition_by_class(
        Path("data/m10/runs/gate-600/scores.json"),
        Path("data/m10/runs/gate-600/failures.json"),
    )
    assert set(table) == {"active", "inactive"}
    for cls in ("active", "inactive"):
        assert "scored" in table[cls] and "failed" in table[cls]


# ======================================================== artifact collisions


def test_an_occupied_run_refuses_before_any_docking(tmp_path) -> None:
    paths = run_paths("probe", tmp_path)
    paths.scores.parent.mkdir(parents=True, exist_ok=True)
    paths.scores.write_text("existing results", encoding="utf-8")
    before = paths.scores.read_bytes()
    with pytest.raises(ArtifactCollision, match="Refusing before any docking"):
        preflight(paths)
    assert paths.scores.read_bytes() == before


def test_a_run_with_existing_poses_refuses(tmp_path) -> None:
    paths = run_paths("probe", tmp_path)
    paths.pose_dir.mkdir(parents=True, exist_ok=True)
    (paths.pose_dir / "1.pdbqt").write_text("a pose", encoding="utf-8")
    with pytest.raises(ArtifactCollision):
        preflight(paths)


def test_a_clean_run_passes_preflight(tmp_path) -> None:
    preflight(run_paths("fresh", tmp_path))


def test_a_conflicting_cohort_rewrite_refuses(tmp_path) -> None:
    from seq2lead.dock.cohort import read_cohort, write_cohort

    cohort = read_cohort(COHORT)
    path = tmp_path / "cohort.json"
    write_cohort(cohort, path)
    before = path.read_bytes()
    write_cohort(cohort, path)  # identical: no-op
    assert path.read_bytes() == before
    cohort.members = cohort.members[:5]
    with pytest.raises(ArtifactCollision, match="different frozen cohort"):
        write_cohort(cohort, path)
    assert path.read_bytes() == before


@pytest.mark.requires_db
def test_a_mismatched_cohort_refuses_before_docking(config, tmp_path, monkeypatch) -> None:
    """A cohort selected under another endpoint is not this contract's cohort."""
    import dataclasses

    from seq2lead.dock import runner
    from seq2lead.dock.cohort import read_cohort
    from seq2lead.dock.receptor import PreparationError

    monkeypatch.setattr(
        runner,
        "build_receptor",
        lambda *a, **k: pytest.fail("the receptor was prepared despite a mismatched cohort"),
    )
    cohort = dataclasses.replace(read_cohort(COHORT), selection_sha256="f" * 64)
    with pytest.raises(PreparationError, match="not the one this contract describes"):
        runner.run_cohort(config, "probe", cohort=cohort, run_root=tmp_path)


# ============================================== engine and receptor identity


def test_an_engine_the_contract_does_not_name_is_refused(config, tmp_path) -> None:
    from seq2lead.dock.engine import EngineError, verify_engine

    impostor = tmp_path / "vina"
    impostor.write_bytes(b"not the engine")
    with pytest.raises(EngineError, match="Refusing to dock"):
        verify_engine(config, impostor)
    with pytest.raises(EngineError, match="no docking engine"):
        verify_engine(config, tmp_path / "absent")


def test_the_real_engine_matches_the_contract(config) -> None:
    from seq2lead.dock.engine import VINA_BIN, verify_engine

    if not VINA_BIN.exists():
        pytest.skip("engine not present")
    assert config.engine_version in verify_engine(config, VINA_BIN)


def test_the_prepared_receptor_kept_its_catalytic_metal() -> None:
    """Meeko drops atoms it cannot type; a zinc enzyme without its zinc is a hole."""
    from seq2lead.dock.receptor import receptor_has_atom_type

    pdbqt = Path("data/m10/runs/gate-600/receptor.pdbqt")
    if not pdbqt.exists():
        pytest.skip("no prepared receptor")
    assert receptor_has_atom_type(pdbqt, "Zn")


def test_cleaning_removes_the_declared_heteroatoms_and_keeps_the_cofactor(tmp_path) -> None:
    from seq2lead.dock.receptor import clean_structure

    source = Path("data/m10/structures/3k34.pdb")
    if not source.exists():
        pytest.skip("structure not present")
    report = clean_structure(source, tmp_path / "clean.pdb")
    assert report.kept_heteroatoms == {"ZN": 1}
    for dropped in ("HOH", "GOL", "HGB", "SUA"):
        assert report.dropped[dropped] > 0, f"{dropped} survived into the receptor"
    assert report.altloc_atoms_dropped > 0
    text = (tmp_path / "clean.pdb").read_text(encoding="utf-8")
    assert " SUA " not in text and " HOH " not in text


def test_a_missing_reference_ligand_raises_rather_than_guessing(tmp_path) -> None:
    from seq2lead.dock.receptor import PreparationError, residue_centroid

    source = Path("data/m10/structures/3k34.pdb")
    if not source.exists():
        pytest.skip("structure not present")
    with pytest.raises(PreparationError, match="no heteroresidue"):
        residue_centroid(source, "XYZ")


# ================================================================ the contract


def test_the_contract_refuses_to_default_a_value_that_decides_the_gate(tmp_path) -> None:
    stripped = tmp_path / "c.yaml"
    stripped.write_text("version: x\ngate: {}\n", encoding="utf-8")
    config = load_docking_config(stripped)
    with pytest.raises(ContractError, match="does not declare gate.effect_threshold"):
        _ = config.effect_threshold


def test_the_box_matches_the_reference_ligand_it_claims_to_come_from(config) -> None:
    from seq2lead.dock.receptor import residue_centroid

    source = Path("data/m10/structures/3k34.pdb")
    if not source.exists():
        pytest.skip("structure not present")
    centroid = residue_centroid(source, "SUA", "1003")
    for declared, actual in zip(config.box_center, centroid, strict=True):
        assert declared == pytest.approx(actual, abs=0.01)


def test_the_box_contains_the_catalytic_zinc(config) -> None:
    """A box that excluded the metal would be docking into the wrong site."""
    from seq2lead.dock.receptor import residue_centroid

    source = Path("data/m10/structures/3k34.pdb")
    if not source.exists():
        pytest.skip("structure not present")
    zn = residue_centroid(source, "ZN")
    for axis, (centre, size) in enumerate(zip(config.box_center, config.box_size, strict=True)):
        assert centre - size / 2 <= zn[axis] <= centre + size / 2


# ============================================================= recomputation


def test_the_gate_recomputes_from_saved_scores_without_docking(config) -> None:
    """The point of saving per-ligand scores: no result needs re-docking to check."""
    scores_path = Path("data/m10/runs/gate-600/scores.json")
    published = Path("reports/results/m10_gate.json")
    if not (scores_path.exists() and published.exists()):
        pytest.skip("no recorded gate run")
    rows = json.loads(scores_path.read_text(encoding="utf-8"))
    recomputed = evaluate_gate(
        np.array([r["ranking_score"] for r in rows], dtype=np.float64),
        np.array([r["label"] == "active" for r in rows], dtype=bool),
        effect_threshold=config.effect_threshold,
        resamples=config.bootstrap_resamples,
        level=config.bootstrap_level,
        seed=config.bootstrap_seed,
        minimum_per_class=config.minimum_usable_per_class,
    )
    reported = json.loads(published.read_text(encoding="utf-8"))["gate"]
    assert recomputed.decision == reported["decision"]
    assert recomputed.auroc == pytest.approx(reported["auroc"], abs=1e-12)
    assert recomputed.ci_low == pytest.approx(reported["ci_low"], abs=1e-12)
    assert recomputed.ci_high == pytest.approx(reported["ci_high"], abs=1e-12)


def test_the_bootstrap_is_deterministic_for_a_fixed_seed() -> None:
    rng = np.random.default_rng(5)
    positive = np.concatenate([np.ones(100, bool), np.zeros(100, bool)])
    scores = np.concatenate([rng.normal(1, 1, 100), rng.normal(0, 1, 100)])
    kw = dict(effect_threshold=0.70, resamples=400, level=0.95, minimum_per_class=50)
    a = evaluate_gate(scores, positive, seed=42, **kw)
    b = evaluate_gate(scores, positive, seed=42, **kw)
    assert (a.ci_low, a.ci_high) == (b.ci_low, b.ci_high)


def test_the_docking_manifest_verifies() -> None:
    from seq2lead.dock.artifacts import MANIFEST_PATH

    if not MANIFEST_PATH.exists():
        pytest.skip("no docking manifest yet")
    assert verify_manifest() == []


def test_a_changed_score_file_fails_manifest_verification(tmp_path) -> None:
    """A changed score file must fail verification, proven on a mirror.

    The previous version edited the real `scores.json` under `data/m10/runs/`
    and restored it in a `finally` block, so an interrupt left a docking score
    file altered.
    """
    import m10_mirror

    if not m10_mirror.REAL_MANIFEST.exists():
        pytest.skip("no docking manifest yet")
    before = m10_mirror.real_digests()
    mirror = m10_mirror.build(tmp_path / "mirror")
    victim = mirror.run_value("scores_path")
    rows = json.loads(victim.read_text(encoding="utf-8"))
    rows[0]["ranking_score"] = 999.0
    victim.write_text(json.dumps(rows), encoding="utf-8")

    problems = verify_manifest(mirror.manifest, allow_missing_trees=True)
    assert any("scores" in p and "bytes changed" in p for p in problems), problems
    assert m10_mirror.real_digests() == before


def test_corrupted_mirrored_evidence_is_refused_for_each_artifact_kind(tmp_path) -> None:
    """One discriminating case per artifact kind, all on temporary copies.

    Each mutation must be refused for its own stated reason, so a single
    catch-all error cannot make the whole set pass.
    """
    import m10_mirror

    if not m10_mirror.REAL_MANIFEST.exists():
        pytest.skip("no docking manifest yet")
    before = m10_mirror.real_digests()

    def bump_score(rows):
        rows[0] = {**rows[0], "ranking_score": 1.0}

    def add_failure(rows):
        rows.append({"ligand": "X", "reason": "injected"})

    cases = [
        ("scores_path", "scores", bump_score),
        ("failures_path", "failures", add_failure),
    ]
    for key, needle, mutate in cases:
        mirror = m10_mirror.build(tmp_path / f"mirror-{key}")
        victim = mirror.run_value(key)
        payload = json.loads(victim.read_text(encoding="utf-8"))
        mutate(payload)
        victim.write_text(json.dumps(payload), encoding="utf-8")
        problems = verify_manifest(mirror.manifest, allow_missing_trees=True)
        assert any(needle in p and "bytes changed" in p for p in problems), (key, problems)
    assert m10_mirror.real_digests() == before


def test_an_exception_after_tampering_leaves_the_real_tree_untouched(tmp_path) -> None:
    """The property the old `finally` blocks could only hope for.

    A tampering test that raises mid-way must still leave every real artifact
    byte-identical. With the mirror that holds by construction, because nothing
    real is ever opened for writing -- so the exception is raised deliberately
    here and the real digests checked afterwards.
    """
    import m10_mirror

    if not m10_mirror.REAL_MANIFEST.exists():
        pytest.skip("no docking manifest yet")
    before = m10_mirror.real_digests()
    assert before, "there should be real M10 artifacts to protect"

    class Boom(RuntimeError):
        pass

    with pytest.raises(Boom):
        mirror = m10_mirror.build(tmp_path / "mirror")
        victim = mirror.qa()
        victim.write_text('{"ruined": true}', encoding="utf-8")
        raise Boom("interrupted immediately after tampering, with no restore")

    after = m10_mirror.real_digests()
    assert after == before, sorted(k for k in before if before[k] != after.get(k))


# ===================================================== attrition can bias a gate


def test_the_attrition_bound_is_tight_when_nothing_was_dropped() -> None:
    rng = np.random.default_rng(2)
    positive = np.concatenate([np.ones(50, bool), np.zeros(50, bool)])
    scores = np.concatenate([rng.normal(1, 1, 50), rng.normal(0, 1, 50)])
    from seq2lead.dock.gate import attrition_bounds

    bounds = attrition_bounds(scores, positive, 50, 50)
    assert bounds["unknown_pairs"] == 0
    assert bounds["best_case_auroc"] == pytest.approx(bounds["observed_auroc"])
    assert bounds["worst_case_auroc"] == pytest.approx(bounds["observed_auroc"])


def test_the_attrition_bound_brackets_the_observed_value() -> None:
    from seq2lead.dock.gate import attrition_bounds

    rng = np.random.default_rng(4)
    positive = np.concatenate([np.ones(40, bool), np.zeros(45, bool)])
    scores = np.concatenate([rng.normal(1, 1, 40), rng.normal(0, 1, 45)])
    bounds = attrition_bounds(scores, positive, 50, 50)
    assert bounds["worst_case_auroc"] < bounds["observed_auroc"] < bounds["best_case_auroc"]
    assert bounds["unknown_pairs"] == 50 * 50 - 40 * 45


def test_the_bound_refuses_an_impossible_cohort_size() -> None:
    from seq2lead.dock.gate import attrition_bounds

    positive = np.concatenate([np.ones(40, bool), np.zeros(40, bool)])
    scores = np.random.default_rng(0).normal(0, 1, 80)
    with pytest.raises(ValueError, match="more pairs scored"):
        attrition_bounds(scores, positive, 10, 10)


def test_the_recorded_gate_survives_its_own_attrition_bound() -> None:
    """If the optimistic bound reached the threshold, the FAIL would not be safe."""
    published = Path("reports/results/m10_gate.json")
    if not published.exists():
        pytest.skip("no recorded gate run")
    payload = json.loads(published.read_text(encoding="utf-8"))
    bounds = payload.get("attrition_bounds")
    if not bounds:
        pytest.skip("no attrition bounds recorded")
    gate = payload["gate"]
    if gate["decision"] != FAIL:
        pytest.skip("only meaningful for a failing gate")
    assert bounds["best_case_auroc"] < gate["effect_threshold"], (
        "the dropped compounds could have changed the decision; the gate is not a clean FAIL"
    )


def test_preparation_and_engine_failures_have_distinct_stage_names() -> None:
    """Both used to be called `parse`, which made the attrition table ambiguous."""
    from seq2lead.dock import engine, ligands

    prep_stages = {"parse", "embed", "optimise", "pdbqt"}
    engine_source = Path(engine.__file__).read_text(encoding="utf-8")
    for stage in prep_stages:
        assert f'DockFailure(compound_id, "{stage}"' not in engine_source, (
            f"the engine reports stage {stage!r}, which ligand preparation also uses"
        )
    assert "engine_output" in engine_source
    ligand_source = Path(ligands.__file__).read_text(encoding="utf-8")
    assert "engine_output" not in ligand_source


def test_the_recorded_failures_use_unambiguous_stages() -> None:
    path = Path("data/m10/runs/gate-600/failures.json")
    if not path.exists():
        pytest.skip("no recorded run")
    rows = json.loads(path.read_text(encoding="utf-8"))
    for row in rows:
        if "mode table" in row["reason"]:
            assert row["stage"] == "engine_output"
        if "could not parse the curated SMILES" in row["reason"]:
            assert row["stage"] == "parse"


def test_the_report_renders_from_the_recorded_artifacts_alone() -> None:
    from seq2lead.dock.report import render

    if not Path("reports/results/m10_gate.json").exists():
        pytest.skip("no recorded gate run")
    body = render()
    assert "Gate decision:" in body
    # prose quotes meeko's literal `atom number N has None type`, so only table
    # cells are checked -- a bare None there means a missing value was formatted
    bare = [
        line
        for line in body.splitlines()
        if line.startswith("|")
        and any(cell.strip().strip("*`") == "None" for cell in line.split("|"))
    ]
    assert not bare, f"the report rendered a bare None in a table cell: {bare}"
    # the headline must state the decision the recorded run actually made
    decision = json.loads(Path("reports/results/m10_gate.json").read_text())["gate"]["decision"]
    assert decision.split("_")[0].upper() in body


def test_every_scored_compound_has_a_pose_on_disk() -> None:
    """A score with no pose would be a number nobody can check."""
    run = Path("data/m10/runs/gate-600")
    if not (run / "scores.json").exists():
        pytest.skip("no recorded run")
    scored = {str(s["compound_id"]) for s in json.loads((run / "scores.json").read_text())}
    poses = {p.stem for p in (run / "poses").iterdir() if p.is_file()}
    assert not (scored - poses), "a compound was scored without a pose being written"
    # the converse is allowed exactly once: vina can write a pose and still emit
    # no parsable mode table, and that compound is dropped rather than scored
    for extra in poses - scored:
        failures = json.loads((run / "failures.json").read_text())
        assert any(str(f["compound_id"]) == extra for f in failures), (
            f"pose {extra} has neither a score nor a recorded failure"
        )


def test_the_directory_digest_notices_a_changed_file(tmp_path) -> None:
    """Poses are referenced by one digest rather than shipped; it must be sensitive."""
    from seq2lead.dock.artifacts import sha256_tree

    d = tmp_path / "poses"
    d.mkdir()
    (d / "a.pdbqt").write_text("one", encoding="utf-8")
    (d / "b.pdbqt").write_text("two", encoding="utf-8")
    first, n = sha256_tree(d)
    assert n == 2
    (d / "b.pdbqt").write_text("two!", encoding="utf-8")
    assert sha256_tree(d)[0] != first, "a changed file did not change the digest"
    (d / "b.pdbqt").write_text("two", encoding="utf-8")
    assert sha256_tree(d)[0] == first, "the digest is not reproducible"
    (d / "c.pdbqt").write_text("three", encoding="utf-8")
    assert sha256_tree(d)[0] != first, "an added file did not change the digest"
    assert sha256_tree(tmp_path / "absent") == ("", 0)


def _bedroc_random_expectation(n: int, n_pos: int, alpha: float = 20.0) -> float:
    """Closed form for a random ranking, derived independently of the implementation.

    A fully tied ranking gives every row the mean exponential weight over all N
    ranks, which makes Truchon & Bayly's RIE equal exactly 1. BEDROC is then the
    scaling factor plus the offset, with the scores dropping out entirely.
    """
    import math

    ra = n_pos / n
    factor = ra * math.sinh(alpha / 2) / (math.cosh(alpha / 2) - math.cosh(alpha / 2 - alpha * ra))
    return factor + 1.0 / (1.0 - math.exp(alpha * (1.0 - ra)))


@pytest.mark.parametrize(("n", "n_pos"), [(200, 20), (500, 10), (400, 100), (200, 100)])
def test_a_tied_ranking_equals_the_random_expectation_at_any_prevalence(n, n_pos) -> None:
    """The old docstring said "the positive prevalence", true only at 50%.

    Checked against a closed form derived from the definition, not against the
    implementation's own output, and at prevalences where prevalence and the true
    expectation differ by up to 200%.
    """
    positive = np.concatenate([np.ones(n_pos, bool), np.zeros(n - n_pos, bool)])
    expected = _bedroc_random_expectation(n, n_pos)
    assert bedroc(np.zeros(n), positive) == pytest.approx(expected, rel=1e-9)


def test_the_random_expectation_is_not_the_prevalence_when_skewed() -> None:
    """Guards the claim the corrected docstring makes."""
    assert _bedroc_random_expectation(200, 100) == pytest.approx(0.5, abs=1e-3)
    skewed = _bedroc_random_expectation(500, 10)
    assert skewed > 3 * (10 / 500), "the skewed case should diverge sharply from prevalence"


def test_a_random_ranking_averages_to_the_tied_value() -> None:
    """Sanity: averaging real random rankings lands on the same closed form."""
    n, n_pos = 200, 20
    positive = np.concatenate([np.ones(n_pos, bool), np.zeros(n - n_pos, bool)])
    rng = np.random.default_rng(0)
    mean = float(np.mean([bedroc(rng.normal(0, 1, n), positive) for _ in range(400)]))
    assert mean == pytest.approx(_bedroc_random_expectation(n, n_pos), abs=0.01)
