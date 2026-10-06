"""The M11f preflight record: the contract's pins, and the figures it rests on."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest

CONTRACT = Path("configs/m11f_evaluation_contract.json")
MANIFEST = Path("configs/manifests/m11f_preflight.json")
REPORT = Path("reports/m11f_preflight.md")
REVIEW = Path("reports/results/m11e_worked_example_review.json")


@pytest.fixture(scope="module")
def contract() -> dict:
    return json.loads(CONTRACT.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def manifest() -> dict:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def report() -> str:
    return re.sub(r"\s+", " ", REPORT.read_text(encoding="utf-8"))


# ======================================================== the contract is executable


def test_nothing_was_executed(contract, manifest) -> None:
    """Asserted on the substance, not on one phrase.

    This pinned the literal string "NOT EXECUTED", which v4 rephrased to
    "EXERCISED WITH FITTING MOCKED. Nothing fitted, ...". The claim that matters
    is that no fit, metric, download or docking happened, so that is what is
    checked now -- a phrasing change should not fail, and a fit should.
    """
    status = contract["status"]
    for claim in ("Nothing fitted", "no prediction metric", "no docking", "freeze unsigned"):
        assert claim in status, claim
    assert contract["entry_point"]["nothing_fitted"] is True
    assert "PLANNED fitting inputs" in contract["model_facing_datasets"]["status"]
    for forbidden in ("model fitting", "prediction metrics of any kind", "docking"):
        assert forbidden in manifest["not_run"], forbidden


def test_every_required_setting_is_pinned(contract) -> None:
    """A specification with a hole in it is a draft, not an executable contract."""
    assert contract["partition"]["validation_fraction_declared"] == 0.15
    assert contract["partition"]["assignment_hash"] == "blake2b-64"
    assert contract["partition"]["seed"] == "m11f-partition-v1"
    assert contract["model"]["projection_dim"] == 512
    assert len(contract["run_seeds"]) == 5
    assert contract["checkpoint_selection"]["metric"] == "validation_rmse"
    assert contract["checkpoint_selection"]["direction"] == "minimise"
    assert contract["metric_version"] == "m8/v2"
    assert contract["discordance_pki"] == 1.0
    for key in ("learning_rate", "batch_size", "max_epochs", "early_stopping_patience"):
        assert key in contract["model"], key


def test_there_is_no_hyperparameter_sweep(contract) -> None:
    assert "No grid, no re-sweep" in contract["no_hyperparameter_sweep"]
    assert "carried over from M9" in contract["model"]["projection_dim_source"]
    assert isinstance(contract["model"]["projection_dim"], int), "a list would be a sweep"


def test_the_primary_cell_and_thresholds_are_as_declared(contract) -> None:
    assert contract["cells"]["primary"] == {
        "increment_arm": "declared_increment",
        "consistency_branch": "screened_primary",
    }
    assert len(contract["cells"]["sensitivities"]) == 3
    assert contract["threshold"]["primary_pki"] == 6.0
    assert contract["threshold"]["retained_sensitivities_pki"] == [7.0, 8.0]


def test_the_input_digests_are_live(contract) -> None:
    """A binding that does not match the artifact binds nothing.

    The paths come from the contract's own `input_set.paths` rather than a copy
    kept here. The copy went stale the moment an artifact moved from
    `data/asof/m11f/` to `data/asof/m11g/`, which is a maintenance failure in the
    test, not a finding about the contract.
    """
    digests = contract["input_digests"]
    paths = contract["input_set"]["paths"]
    assert len(paths) >= 13, "the pinned input set shrank"
    stale = []
    for name, path in sorted(paths.items()):
        key = f"{name}_sha256"
        assert key in digests, key
        actual = hashlib.sha256(Path(path).read_bytes()).hexdigest()
        if digests[key] != actual:
            stale.append(f"{name} -> {path}")
    assert not stale, stale


# ================================================== the feature-visibility correction


def test_representations_may_be_computed_for_every_entity(contract) -> None:
    """The v1 draft was wrong, and the record says why rather than quietly changing."""
    fv = contract["feature_visibility"]
    assert "An earlier draft declared ALL features train_only" in fv["correction"]
    assert "carry no label information" in fv["correction"]
    may = fv["may_be_computed_for_training_validation_and_evaluation_entities"]
    assert "ecfp4" in may and "esm2" in may
    assert "never read" in may["condition"]


def test_fitted_things_remain_training_only(contract) -> None:
    must = contract["feature_visibility"]["must_be_fitted_on_training_evidence_only"]
    joined = " ".join(must)
    for thing in ("scaler", "whitening", "activity-derived", "calibrator", "model parameters"):
        assert thing in joined, thing
    fv = contract["feature_visibility"]
    assert "select checkpoints" in fv["validation_may"]
    assert "STAYS UNBUILT" in fv["retrieval"]
    assert "STAYS DISABLED" in fv["evaluation_evidence_display"]


def test_chirality_and_the_protein_pin_are_preserved(contract) -> None:
    from seq2lead.features.ecfp import CHIRALITY, N_BITS, RADIUS
    from seq2lead.features.plm import MODEL_REVISION, POOLING

    preserved = contract["feature_visibility"]["preserved"]
    assert preserved["chiral_fingerprints"] is True is CHIRALITY
    assert preserved["pinned_protein_representation"] == MODEL_REVISION
    ecfp = contract["feature_visibility"][
        "may_be_computed_for_training_validation_and_evaluation_entities"
    ]["ecfp4"]
    assert (ecfp["radius"], ecfp["n_bits"]) == (RADIUS, N_BITS)
    esm = contract["feature_visibility"][
        "may_be_computed_for_training_validation_and_evaluation_entities"
    ]["esm2"]
    assert esm["pooling"] == POOLING
    assert esm["length_policy"] == "full", "no truncation, as M7 pinned"


def test_feature_coverage_was_measured_before_any_metric(contract, manifest) -> None:
    cov = manifest["feature_coverage"]
    assert cov["compounds"]["coverage"] == 1.0
    assert cov["targets"]["coverage"] == 1.0
    assert cov["compounds"]["failures_by_reason"] == {}
    assert cov["targets"]["over_training_window_1022"] == 107
    assert "flagged, not excluded" in cov["targets"]["over_window_note"]
    # v4 reports coverage per ROLE, measured by the runner, which refuses an
    # incomplete role rather than recording an exclusion count beside it.
    by_role = contract["feature_coverage_measured"]["by_role"]
    assert len(by_role) == 6, "three roles x two feature kinds"
    for role_kind, c in by_role.items():
        assert c["missing"] == 0 and c["unusable"] == 0, role_kind
        assert c["complete"] is True, role_kind
        assert c["nothing_imputed"] is True, role_kind


# ===================================================================== the partition


def test_the_configuration_reaches_every_assignment(contract) -> None:
    """build_partition accepted fraction and seed and then ignored them."""
    assert contract["partition"]["configuration_threaded_through_every_assignment"] is True
    # Found by id, not by position: this read `items[1]`, and v4 moved the
    # earlier corrections into `carried_forward`, so the index pointed at a
    # different defect entirely.
    corrections = contract["corrections"]
    everything = corrections["items"] + corrections.get("carried_forward", [])
    e2 = next(item for item in everything if item["id"] == "E2")
    verified = e2["default_config_membership_verified_unchanged"]
    assert verified["pairs_whose_partition_changed"] == 0
    assert verified["assignment"] == "UNCHANGED"
    assert verified["membership_digest_changed"] is True
    assert "not because any pair moved" in verified["why_the_digest_changed"]


def test_the_partition_is_within_a_and_b_was_unavailable(contract) -> None:
    p = contract["partition"]
    assert p["b_unavailable_on_this_path"] is True
    assert "carved out of snapshot A" in p["scope"]
    assert "the key IS the pair" in p["whole_pairs_together"]
    assert "per-process randomised" in p["why_not_random_or_hash"]
    assert abs(p["validation_fraction_realised"] - 0.15) < 0.01


def test_both_digest_families_are_recorded(contract) -> None:
    p = contract["partition"]
    for family in ("membership_digests", "evidence_support_digests"):
        assert set(p[family]) == {"train", "validation"}
        assert all(len(v) == 64 for v in p[family].values())
    assert p["membership_digests"] != p["evidence_support_digests"]


def test_the_three_eligibilities_are_kept_apart(contract) -> None:
    """Classification, regression and validation-RMSE answer three questions."""
    e = contract["eligibility"]
    assert e["classification_by_partition"]["train"] == 401_818
    assert e["regression_by_partition"]["train"] == 353_957
    assert e["validation_rmse_by_partition"]["validation"] == 60_981
    assert e["censored_only_decisive_by_partition"]["train"] == 50_007
    assert "exact" in e["regression_rule"]
    assert "never substituted" in e["regression_rule"]
    assert "NOT derived from" in e["validation_rmse_rule"]

    t = contract["training_eligibility"]
    assert t["pairs_supplied_to_model_fitting"] == 353_957, (
        "fitting receives pairs with a regression target, not pairs with a class label"
    )
    assert "regression target" in t["pairs_supplied_to_model_fitting_definition"]


def test_discordance_is_treated_differently_for_training_and_selection(contract) -> None:
    d = contract["discordance_treatment"]
    assert "REMAIN available for training" in d["training"]
    assert "EXCLUDED from checkpoint selection" in d["validation_rmse"]
    assert "§7a (h)" in d["training"]


# ======================================================================= the strata


def test_recurrence_is_against_the_fitted_set_and_the_gap_is_measured(manifest) -> None:
    s = manifest["strata"]
    assert "NOT historical presence in A" in s["recurrence_defined_against"]
    gap = s["measured_difference"]
    assert gap["declared_arm_eligible_a_present"] == 6_904
    assert gap["declared_arm_eligible_fitted"] == 4_170
    assert gap["a_present_but_not_fitted"] == 2_734
    assert gap["a_present_but_not_fitted"] > 0, "the contract forbids assuming this is zero"
    assert (
        gap["declared_arm_eligible_fitted"] + gap["a_present_but_not_fitted"]
        == gap["declared_arm_eligible_a_present"]
    )


def test_the_three_new_to_fitting_subgroups_are_separate(manifest) -> None:
    subs = manifest["strata"]["subgroups"]
    assert set(subs) == {
        "new_absent_from_a",
        "new_reserved_for_validation",
        "new_a_present_excluded_from_fitting",
    }
    by_arm = manifest["strata"]["by_arm"]["declared_increment"]
    assert sum(by_arm.values()) == 26_421, "the strata must partition the eligible pool"
    assert by_arm["recurrent"] == 4_170


def test_validation_exposed_pairs_are_not_called_untouched(manifest, report) -> None:
    note = manifest["strata"]["validation_exposure"]
    assert "NOT untouched by model selection" in note
    assert "weaker evidence" in note
    assert "not untouched by model selection" in report.lower()


# ================================================================== the feasibility


def test_c1_to_c5_were_reassessed_on_the_headline_cohort(manifest) -> None:
    f = manifest["feasibility"]
    for key in ("c1_to_c5_headline", "c1_to_c5_strictest"):
        assessed = f[key]
        assert set(assessed) == {
            "C1_at_least_50_rankable_targets",
            "C2_at_least_2000_pairs",
            "C3_positive_rate_in_0.20_0.80",
            "C4_all_four_cells_reported",
            "C5_not_argued_from_observation_fraction",
        }
        for cid, v in assessed.items():
            assert v["met"] is True, f"{key}/{cid} did not pass and must be reported as failing"
    assert f["c1_to_c5_headline"]["C1_at_least_50_rankable_targets"]["measured"] == 123
    assert f["c1_to_c5_strictest"]["C1_at_least_50_rankable_targets"]["measured"] == 98


def test_nothing_was_changed_to_obtain_a_pass(manifest) -> None:
    n = manifest["feasibility"]["nothing_was_changed_to_obtain_a_pass"]
    assert n["threshold_unchanged"] == 6.0
    assert n["cohort_definition_unchanged"] is True
    assert n["floors_unchanged"] is True
    # and the cost of the restriction is reported rather than hidden
    cost = n["cost_of_restricting_to_new_to_fitting"]
    assert cost["full_primary_cell_rankable_at_5"] == 142
    assert cost["headline_rankable_at_5"] < cost["full_primary_cell_rankable_at_5"]
    assert cost["strictest_rankable_at_5"] < cost["headline_rankable_at_5"]


def test_every_cell_and_stratum_is_reported(manifest) -> None:
    cells = manifest["cells_by_stratum"]
    assert len(cells) == 4
    for key, strata in cells.items():
        assert len(strata) == 4, f"{key} is missing a stratum"
        for stratum, c in strata.items():
            for field in ("pairs", "targets", "class_balance", "rankable_at"):
                assert field in c, f"{key}/{stratum} missing {field}"


def test_the_a_present_not_fitted_stratum_is_reported_with_its_own_floors(manifest) -> None:
    """It grew from 17 pairs to 1,686 once eligibility was corrected.

    An earlier revision described it as unrankable at any floor. That was true of
    the 17-pair version; with 1,686 pairs over 106 targets it clears ≥5 per class
    on 3 targets -- far below the cohort floor, which is the honest statement
    rather than "cannot be ranked".
    """
    cell = manifest["cells_by_stratum"]["declared_increment/screened_primary"]
    stratum = cell["new_a_present_excluded_from_fitting"]
    assert stratum["pairs"] == 1_686
    assert stratum["targets"] == 106
    assert stratum["rankable_at"]["5"] == 3
    assert stratum["rankable_at"]["5"] < 50, "nowhere near the accepted C1 floor"


# ============================================= worked examples vs population counts


def test_the_worked_example_review_is_included_and_inspectable() -> None:
    body = json.loads(REVIEW.read_text(encoding="utf-8"))
    assert len(body["examples"]) == 8
    observed = [e for e in body["examples"].values() if e["real_example"].get("observed") is True]
    assert len(observed) == 7
    for e in observed:
        real = e["real_example"]
        assert isinstance(real["matching_pairs_in_audit"], int)
        assert real["pair"]
        # locators are what make the predicate checkable by hand
        assert any(
            real.get(k) for k in ("increment_locators", "withheld_locators", "removed_locators")
        )


def test_population_counts_are_distinguished_from_individual_examples(manifest, report) -> None:
    d = manifest["worked_examples_versus_population_counts"]
    assert "a population count" in d["distinction"]
    assert "ONE pair" in d["distinction"]
    assert "not evidence about the population" in d["distinction"]
    assert "by construction, not by rarity" in d["example_a_caveat"]
    assert "two different kinds of claim" in report


def test_the_report_keeps_the_qualifications(report) -> None:
    assert "not signed" in report
    assert "exploratory" in report
    assert "provisional-pooling" in report
    assert "Nothing was fitted" in report
