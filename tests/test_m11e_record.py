"""The KI aggregation record: does the report agree with the manifest, and reconcile?"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

MANIFEST = Path("configs/manifests/m11e_ki_aggregation.json")
SPEC = Path("configs/m11e_cross_slot_sensitivity.json")
REPORT = Path("reports/m11e_ki_aggregation.md")

KI_ADDITIONS = 33_953
KI_A = 614_755
KI_B = 619_931


@pytest.fixture(scope="module")
def manifest() -> dict:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def spec() -> dict:
    return json.loads(SPEC.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def report() -> str:
    """The report with newlines collapsed.

    Markdown wraps prose at 80 columns, so asserting on a phrase that happens to
    straddle a line break fails for a reason that has nothing to do with the
    claim. Collapsing whitespace makes these assertions about the text rather
    than about where it wrapped.
    """
    import re

    return re.sub(r"\s+", " ", REPORT.read_text(encoding="utf-8"))


# ============================================================ the pre-registration


def test_the_sensitivity_was_declared_before_any_result(spec, manifest) -> None:
    """A sensitivity chosen after seeing the primary result is a selection."""
    assert spec["declared_before_any_eligibility_figure_was_computed"] is True
    assert "not a sensitivity, it is a selection" in spec["why_declared_first"]
    assert manifest["sensitivity_spec"]["declared_before_results"] is True
    # the digest in the manifest must be the digest of the spec as it now stands
    import hashlib

    assert (
        manifest["sensitivity_spec"]["sha256"] == hashlib.sha256(SPEC.read_bytes()).hexdigest()
    ), "the spec changed after it was declared"


def test_the_spec_forbids_greedy_resolution_and_upper_bounds_as_counts(spec) -> None:
    amb = spec["ambiguous_candidates"]
    assert amb["no_greedy_resolution"] is True
    assert "NOT presented as an observed count" in amb["treatment"]
    assert "not experimental identity" in spec["what_the_comparison_can_and_cannot_show"]["cannot"]


def test_the_spec_forbids_resting_feasibility_on_the_observation_fraction(spec) -> None:
    assert spec["reporting_requirements"]["feasibility_must_not_rest_on"] == (
        "the fraction of added observations alone"
    )


# ================================================================ reconciliations


def test_every_ki_addition_is_accounted_for(manifest) -> None:
    acc = manifest["increment_observation_accounting"]
    assert acc["ki_additions_from_the_matcher"] == KI_ADDITIONS
    assert (
        acc["increment_measurements_all_pairs"]
        + acc["withheld_for_corrections_all_pairs"]
        + acc["unparseable_addition_rows"]
        == KI_ADDITIONS
    )
    assert acc["reconciles"] is True


def test_the_ki_classifications_equal_the_descriptive_run(manifest) -> None:
    """Pair-sharding is a coarsening of slot-sharding, so these cannot differ."""
    eq = manifest["equivalence_to_m11d"]
    assert eq["match"] is True
    for key in ("unchanged", "additions", "removals"):
        assert (
            eq["ki_column_from_this_pair_sharded_run"][key]
            == eq["ki_column_from_the_slot_sharded_run"][key]
        ), key
    assert "coarsening" in eq["why_it_must_match"]
    assert "IC50 removal against an EC50 addition" in eq["why_the_diff_still_runs_on_all_types"]


def test_a_training_evidence_is_unchanged_by_b(manifest) -> None:
    tr = manifest["training_evidence_from_a"]
    assert tr["unchanged_by_b"] is True
    assert tr["shards_compared"] == manifest["shard_configuration"]["shard_count"]
    # pairs first seen in B have no training evidence and must be excluded, counted
    assert (
        tr["pairs_first_seen_in_b_excluded_from_the_digest"] == manifest["pairs"]["absent_from_a"]
    )
    assert sum(tr["label_counts"].values()) == manifest["pairs"]["present_in_a"]


def test_eligible_plus_excluded_equals_pairs_with_an_increment(manifest) -> None:
    for threshold, arms in manifest["results_by_threshold"].items():
        for arm in ("declared_increment", "cross_slot_excluded"):
            a = arms[arm]
            pool = a["pre_screen_pool"]["scoreable_pairs"]
            assert (
                pool + sum(a["excluded_pairs_by_reason"].values()) == a["pairs_with_any_increment"]
            ), f"{threshold}/{arm}"
            # the pre-screen pool IS the unscreened sensitivity branch
            assert a["branches"]["unscreened_sensitivity"]["admitted_pairs"] == pool
            # and the screened primary branch plus the screened-out pairs recover it
            assert (
                a["branches"]["screened_primary"]["admitted_pairs"]
                + a["branches"]["screened_out_of_primary"]
                == pool
            ), f"{threshold}/{arm}"


def test_observations_and_pairs_are_reported_separately(manifest) -> None:
    assert manifest["observations"]["a_ki"] == KI_A
    assert manifest["observations"]["b_ki"] == KI_B
    assert manifest["pairs"]["with_ki_evidence_in_either_snapshot"] == (
        manifest["pairs"]["present_in_a"] + manifest["pairs"]["absent_from_a"]
    )


def test_strata_are_defined_against_a_not_against_a_fitted_set(manifest) -> None:
    """Historical presence is not recurrence; the record must not blur them."""
    note = manifest["pairs"]["note"]
    assert "NOT recurrence relative to an eventual fitted training set" in note
    assert "split construction that has not been run" in note
    primary6 = manifest["results_by_threshold"]["pki6"]["declared_increment"]
    assert primary6["strata"]["new_pair"] == manifest["pairs"]["absent_from_a"]


# ==================================================== the sensitivity and the cohort


def test_the_sensitivity_effect_is_reported_for_every_threshold(manifest) -> None:
    for threshold, arms in manifest["results_by_threshold"].items():
        eff = arms["sensitivity_effect"]["screened_primary"]
        assert eff["admitted_cross_slot_excluded"] <= eff["admitted_declared"], threshold
        assert eff["rankable_at_5_cross_slot_excluded"] <= eff["rankable_at_5_declared"]
        lost = eff["admitted_declared"] - eff["admitted_cross_slot_excluded"]
        assert 0 < lost / eff["admitted_declared"] < 0.5, threshold


def test_the_primary_arm_excludes_nothing_on_cross_slot_grounds(manifest) -> None:
    """The declared semantics are not re-specified by this step."""
    for arms in manifest["results_by_threshold"].values():
        assert arms["declared_increment"]["observations"]["excluded_as_cross_slot_candidates"] == 0


def test_the_cohort_requirement_is_judged_on_the_conservative_arm(manifest) -> None:
    cohort = manifest["accepted_minimum_evaluation_cohort"]
    assert "ACCEPTED" in cohort["status"]
    assert "screened_primary" in cohort["judged_on"]
    assert "cross_slot_excluded" in cohort["judged_on"]
    assert "most conservative" in cohort["judged_on"]
    # and the criteria must not be dressed up as a power calculation
    assert "NOT a power calculation" in cohort["these_are_pragmatic_feasibility_criteria"]
    assert "ACCEPTED" in cohort["status"], "accepted in the closeout instruction"
    ids = {r["id"] for r in cohort["requirements"]}
    assert ids == {"C1", "C2", "C3", "C4", "C5"}
    for req in cohort["requirements"]:
        assert req["met"] is True, req["id"]
        assert req["why"], req["id"]


def test_the_cohort_requirement_does_not_rest_on_the_observation_fraction(manifest) -> None:
    """C5 exists because 33,953 added observations says nothing about rankability."""
    c5 = next(
        r for r in manifest["accepted_minimum_evaluation_cohort"]["requirements"] if r["id"] == "C5"
    )
    assert "says nothing about whether" in c5["why"]
    assert "668 of 1,016 targets carry only one class" in c5["why"]
    c1 = next(
        r for r in manifest["accepted_minimum_evaluation_cohort"]["requirements"] if r["id"] == "C1"
    )
    assert "both classes" in c1["rankable_means"].lower() or "BOTH" in c1["rankable_means"]


def test_the_threshold_is_not_moved_after_seeing_class_balance(manifest) -> None:
    """pKi 7.0 looks better conditioned. That is exactly why 6.0 stays."""
    verdict = manifest["accepted_minimum_evaluation_cohort"]["verdict"]
    assert "primary threshold STAYS 6.0" in verdict
    assert "pre-registered" in verdict
    assert "reported, not acted on" in verdict


def test_rankable_targets_are_far_fewer_than_eligible_pairs(manifest) -> None:
    """The finding the cohort proposal turns on."""
    for arms in manifest["results_by_threshold"].values():
        for arm in ("declared_increment", "cross_slot_excluded"):
            for branch in ("unscreened_sensitivity", "screened_primary"):
                cell = arms[arm]["branches"][branch]
                rk = cell["rankability"]
                assert rk["targets_single_class_only"] > rk["rankable_at"]["5"], (
                    "more targets should be single-class than rankable at 5, which is the point"
                )
                assert rk["rankable_at"]["5"] < cell["admitted_pairs"] / 50


# ============================================================== the report itself


def test_the_report_states_the_key_figures(report) -> None:
    for figure in (
        "614,755",
        "619,931",
        "33,953",
        "33,941",
        "26,421",
        "22,939",
        "27,498",
        "1,041",
        "678",
        "118",
        "13.2%",
        "0.48 GB",
    ):
        assert figure in report, f"the report does not state: {figure}"


def test_the_report_corrects_the_candidate_wording(report) -> None:
    assert "candidate correspondence, not experimental identity" in report
    assert "shorthand for a counting operation" in report


def test_the_report_keeps_the_qualifications(report) -> None:
    assert "not signed" in report
    assert "exploratory" in report
    assert "provisional-pooling" in report
    assert "No model was fitted" in report
    assert "no prediction metric was computed" in report


def test_the_report_records_the_failed_check_it_had_to_fix(report) -> None:
    """The training check reported False first, and the fault was in the check."""
    assert "reported **False**" in report
    assert "the fault was in the check" in report


def test_the_report_states_what_remains_unavailable(report) -> None:
    assert "## 13. What remains unavailable" in report
    for item in ("post-freeze snapshot", "fitted training set", "pooled across assay contexts"):
        assert item in report, item


# ============================================================ the correction pass


def test_the_correction_record_is_versioned_and_states_what_was_rerun(manifest) -> None:
    corr = manifest["corrections"]
    assert corr["version"] == "m11e-correction-v1"
    assert manifest["run"] == "m11e-ki-aggregation-v3"
    assert "m11e-ki-aggregation-v1" in manifest["supersedes"]
    assert corr["what_was_rerun"] and corr["what_was_not_rerun"]
    joined = " ".join(corr["what_was_not_rerun"])
    assert "matcher" in joined
    assert "digest is unchanged" in joined or "digests were re-verified" in joined


def test_all_five_defects_are_recorded_with_their_fixes(manifest) -> None:
    defects = {d["id"]: d for d in manifest["corrections"]["defects"]}
    assert set(defects) == {"D1", "D2", "D3", "D4", "D5"}
    for d in defects.values():
        assert d["defect"] and d["fix"]
    assert "BEFORE consulting outcome.in_primary" in defects["D1"]["defect"]
    assert "discriminator" in defects["D1"]
    assert "no actives at all" in defects["D1"]["discriminator"]
    assert "zip(strict=False)" in defects["D3"]["defect"]
    assert "before either side is read" in defects["D4"]["fix"]
    assert "canonical A evidence" in defects["D5"]["fix"]


def test_the_published_figures_are_recomputed_not_asserted_to_survive(manifest) -> None:
    """The old numbers were pre-screen figures; the new ones are the cohort."""
    rec = manifest["corrections"]["published_figures_recomputed"]
    old = rec["pki6_conservative_arm"]["previously_published_as_the_cohort"]
    new = rec["pki6_conservative_arm"]["recomputed_cohort"]
    assert old["rankable_at_5"] == 118
    assert new["rankable_at_5"] == 117, "the recomputed figure must be stated, not inferred"
    assert old["eligible_pairs"] == 22939
    assert new["admitted_pairs"] == 22834
    assert "pre-screen pool" in old["population"]
    assert "screened primary branch" in new["population"]
    # and the recomputed figure must agree with the live results
    cell = manifest["results_by_threshold"]["pki6"]["cross_slot_excluded"]["branches"][
        "screened_primary"
    ]
    assert new["admitted_pairs"] == cell["admitted_pairs"]
    assert new["rankable_at_5"] == cell["rankability"]["rankable_at"]["5"]
    # the honest framing: the corpus was barely sensitive, which is not an excuse
    assert (
        "is a measurement, not a reason the defect did not matter"
        in (rec["pki6_conservative_arm"]["change"])
    )


def test_the_audit_reconstruction_was_checked_on_the_corpus(manifest) -> None:
    rec = manifest["corrections"]["audit_reconstruction"]
    assert rec["checked"] is True
    assert rec["reconstructs"] is True
    for field in ("admitted pairs", "label counts", "rankability at 5", "screened-out counts"):
        assert field in rec["what_was_checked"], field


def test_all_four_cells_are_present_at_every_threshold(manifest) -> None:
    for threshold, arms in manifest["results_by_threshold"].items():
        for arm in ("declared_increment", "cross_slot_excluded"):
            for branch in ("unscreened_sensitivity", "screened_primary"):
                cell = arms[arm]["branches"][branch]
                for key in (
                    "admitted_pairs",
                    "label_counts",
                    "class_balance",
                    "coverage",
                    "rankability",
                ):
                    assert key in cell, f"{threshold}/{arm}/{branch} missing {key}"


def test_the_screened_branch_never_exceeds_the_pool(manifest) -> None:
    from seq2lead.asof.ki_aggregation import MIN_PER_CLASS

    for arms in manifest["results_by_threshold"].values():
        for arm in ("declared_increment", "cross_slot_excluded"):
            u = arms[arm]["branches"]["unscreened_sensitivity"]
            sc = arms[arm]["branches"]["screened_primary"]
            assert sc["admitted_pairs"] <= u["admitted_pairs"]
            assert sc["coverage"]["distinct_targets"] <= u["coverage"]["distinct_targets"]
            for n in MIN_PER_CLASS:
                assert (
                    sc["rankability"]["rankable_at"][str(n)]
                    <= u["rankability"]["rankable_at"][str(n)]
                )


def test_the_report_distinguishes_pre_screen_from_admitted(report) -> None:
    assert "pre-screen pool" in report.lower()
    assert "admitted" in report
    assert "screened primary" in report.lower()
    assert "Pragmatic feasibility criteria, not a power calculation" in report
    assert "Accepted on those terms" in report


def test_the_report_states_the_recomputed_cohort_figures(report) -> None:
    assert "Published as the cohort (v1)" in report
    assert "Recomputed cohort (v2)" in report
    assert "is a measurement, not" in report


# ==================================================================== the closeout

CONTRACT = Path("configs/m11f_evaluation_contract.json")
REVIEW = Path("data/asof/m11e/worked-example-review.json")


@pytest.fixture(scope="module")
def contract() -> dict:
    return json.loads(CONTRACT.read_text(encoding="utf-8"))


def test_the_closeout_is_versioned_and_states_what_moved(manifest) -> None:
    c = manifest["closeout"]
    assert c["version"] == "m11e-closeout-v1"
    assert manifest["run"] == "m11e-ki-aggregation-v3"
    assert "m11e-ki-aggregation-v2" in manifest["supersedes"]
    joined = " ".join(c["what_changed"])
    assert "reads in_a directly" in joined
    assert "reads in_b" in joined
    assert "names the reason" in joined
    assert "unresolved_heterogeneity" in joined


def test_the_closeout_claims_the_figures_did_not_move_and_says_why(manifest) -> None:
    """An unchanged summary is the check that the change was confined to the audit."""
    fig = manifest["closeout"]["figures_unchanged"]
    assert "output, not an input" in fig["claim"]
    cell = manifest["results_by_threshold"]["pki6"]["cross_slot_excluded"]["branches"][
        "screened_primary"
    ]
    assert fig["pki6_conservative_cell"]["admitted_pairs"] == cell["admitted_pairs"] == 22834
    assert (
        fig["pki6_conservative_cell"]["rankable_at_5"]
        == (cell["rankability"]["rankable_at"]["5"])
        == 117
    )


def test_all_eight_worked_examples_were_reviewed(manifest) -> None:
    review = manifest["worked_example_review"]
    assert review["all_eight_match_the_specification"] is True
    assert review["no_real_example_was_manufactured"] is True
    body = json.loads(REVIEW.read_text(encoding="utf-8"))
    assert len(body["examples"]) == 8
    expected = {
        "a_unchanged",
        "b_correction",
        "c_withdrawal",
        "d_repeated_rows",
        "e_clean_then_contradictory",
        "f_opposite_bounds",
        "g_withdrawal_makes_b_cleaner",
        "h_discordant_replicates",
    }
    assert set(body["examples"]) == expected
    for name, ex in body["examples"].items():
        assert ex["expected_from_docs_M11_7a"], name
        assert ex["implemented_on_the_synthetic_fixture"], name
        assert "real_example" in ex, name


def test_patterns_absent_from_the_real_data_are_stated_not_invented(manifest) -> None:
    """The instruction allowed for 'not observed'; on this pair nothing needed it."""
    review = manifest["worked_example_review"]
    assert review["patterns_not_observed"] == []
    # (a) is absent from the AUDIT by construction, which is a different claim
    assert "a_unchanged" in review["pattern_not_in_the_audit_by_construction"]
    note = review["pattern_not_in_the_audit_by_construction"]["a_unchanged"]
    assert "no audit record is emitted" in note
    assert "585,978" in note
    # and the observed patterns carry real counts
    observed = review["patterns_observed_in_the_real_snapshots"]
    assert len(observed) == 7
    assert all(isinstance(n, int) and n > 0 for n in observed.values())


def test_the_only_discrepancy_was_in_the_document(manifest) -> None:
    found = manifest["worked_example_review"]["only_discrepancy_found"]
    assert "in the DOCUMENT, not the implementation" in found
    assert "exact_bound_conflict" in found
    # The stale name must survive ONLY in the correction note that records it --
    # a correction that cannot name what it corrected is not auditable.
    doc = Path("docs/M11.md").read_text(encoding="utf-8")
    # The stale name may survive only where the rename is being RECORDED: inside
    # the blockquote that documents it, or on a line that also names the
    # implemented status. What must not survive is a line that still USES it as
    # the status. A blanket "must not appear" would forbid the correction notice
    # itself, which is the opposite of auditable.
    mentions = [line for line in doc.splitlines() if "unresolved_heterogeneity" in line]
    assert mentions, "the correction must name what it corrected"
    for line in mentions:
        records_the_rename = line.lstrip().startswith(">") or "exact_bound_conflict" in line
        assert records_the_rename, f"stale status name still in use: {line[:80]}"


def test_the_cohort_criteria_are_accepted_on_the_stated_terms(manifest) -> None:
    cohort = manifest["accepted_minimum_evaluation_cohort"]
    assert "ACCEPTED" in cohort["status"]
    acc = cohort["acceptance"]
    assert acc["accepted_by"] == "the user"
    assert acc["accepted_as"] == "pragmatic feasibility floors for this exploratory study"
    assert "a power calculation" in acc["explicitly_not"]
    assert "a guarantee of informative results" in acc["explicitly_not"]
    assert acc["retained"]["primary_threshold_pki"] == 6.0
    assert set(acc["retained"]["sensitivity_axes"]) == {"increment_arm", "consistency_branch"}
    assert acc["closes_checklist_item"] == 8
    assert "proposed_minimum_evaluation_cohort" not in manifest


# ========================================================= the evaluation contract


def test_the_manifest_records_the_draft_that_m11f_superseded(manifest) -> None:
    """M11e recorded a draft; M11f replaced it with the executable specification.

    The eight structure assertions that stood here were written against that
    draft. Keeping them would mean two places asserting the same file, and one
    going stale -- which is what happened. `tests/test_m11f_record.py` owns the
    live contract's pins; this test owns the supersession, so the chain from one
    record to the next stays checkable.
    """
    import hashlib

    pointer = manifest["next_evaluation_contract"]
    assert pointer["status"] == "SPECIFIED, NOT EXECUTED"
    assert pointer["path"] == "configs/m11f_evaluation_contract.json"

    live = json.loads(CONTRACT.read_text(encoding="utf-8"))
    prefix = "m11f-asof-evaluation-contract-v"
    assert live["contract"].startswith(prefix)
    # The chain must move forward, not start at a fixed point. This asserted the
    # live contract superseded **v1** specifically, which held only until v4.
    superseded = (
        live["supersedes"] if isinstance(live["supersedes"], list) else [live["supersedes"]]
    )
    assert superseded, "a successor must name what it supersedes"
    assert all(str(x).startswith(prefix) for x in superseded)
    assert int(live["contract"].removeprefix(prefix)) > max(
        int(str(x).removeprefix(prefix).split()[0]) for x in superseded
    ), "the version must increase"
    assert "Nothing fitted" in live["status"], "the successor must still be unexecuted"
    assert "freeze unsigned" in live["status"]

    # The live binding is whichever manifest claims it; the superseded one must
    # say so rather than appear to bind a contract it no longer matches.
    m11f = json.loads(Path("configs/manifests/m11f_preflight.json").read_text(encoding="utf-8"))
    digest = hashlib.sha256(CONTRACT.read_bytes()).hexdigest()
    if m11f["contract"]["sha256"] != digest:
        # Follow the pointer rather than requiring the historical note to name the
        # current version: that would mean editing a past record on every bump,
        # which is the opposite of what a record is for.
        onward = m11f["contract"]["superseded_by"]
        assert onward["version"].startswith(prefix)
        assert int(onward["version"].removeprefix(prefix)) > int(
            m11f["contract"]["version"].removeprefix(prefix)
        ), "the successor named must be later than what this manifest recorded"
        current = json.loads(Path(onward["live_binding"]).read_text(encoding="utf-8"))
        assert current["contract"] == live["contract"], "the live manifest names another contract"
        assert current["contract_sha256"] == digest, "the live manifest's binding is stale"
