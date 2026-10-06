"""The matching record: does the report agree with the manifest, and reconcile?

The figures here are the only ones anyone will read -- the detail streams are
380 MB and referenced by digest. So the arithmetic that makes them trustworthy
is asserted rather than left to a careful reader.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

MANIFEST = Path("configs/manifests/m11d_matching_202601_202609.json")
REPORT = Path("reports/m11d_matching.md")

A_OBSERVATIONS = 3_136_836
B_OBSERVATIONS = 3_233_963


@pytest.fixture(scope="module")
def manifest() -> dict:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def report() -> str:
    return REPORT.read_text(encoding="utf-8")


# ========================================================= the two reconciliations


def test_input_accounting_closes_on_both_sides(manifest) -> None:
    """A = unchanged + removals, B = unchanged + additions. Exactly."""
    acc = manifest["input_accounting"]
    assert acc["a_equals_unchanged_plus_removals"] == acc["a_observations_in"] == A_OBSERVATIONS
    assert acc["b_equals_unchanged_plus_additions"] == acc["b_observations_in"] == B_OBSERVATIONS
    assert acc["reconciles"] is True
    totals = manifest["totals"]
    assert totals["unchanged_observations"] + totals["removed_observations"] == A_OBSERVATIONS
    assert totals["unchanged_observations"] + totals["added_observations"] == B_OBSERVATIONS


def test_a_linked_correction_consumes_one_surplus_on_each_side(manifest) -> None:
    """The second identity, and the one most likely to be got wrong.

    `diff_snapshots` skips the unresolved accumulation for a slot whose surplus
    it linked, so unresolved + corrections must equal the surplus on each side.
    If the sharded summary double-counted a linked row it would show up here.
    """
    acc = manifest["surplus_accounting"]
    assert acc["unresolved_additions_plus_corrections"] == acc["additions"]
    assert acc["unresolved_removals_plus_corrections"] == acc["removals"]
    assert acc["reconciles"] is True


def test_per_type_counts_sum_to_the_overall_counts(manifest) -> None:
    """The endpoint boundary must not leak observations."""
    by_type = manifest["by_measurement_type"]
    assert set(by_type) == {"KI", "IC50", "KD", "EC50"}
    totals = manifest["totals"]
    assert sum(v["a_observations"] for v in by_type.values()) == A_OBSERVATIONS
    assert sum(v["b_observations"] for v in by_type.values()) == B_OBSERVATIONS
    assert sum(v["unchanged"] for v in by_type.values()) == totals["unchanged_observations"]
    assert sum(v["additions"] for v in by_type.values()) == totals["added_observations"]
    assert sum(v["removals"] for v in by_type.values()) == totals["removed_observations"]
    # per type, the same two identities hold
    for mtype, v in by_type.items():
        assert v["unchanged"] + v["removals"] == v["a_observations"], mtype
        assert v["unchanged"] + v["additions"] == v["b_observations"], mtype


# ================================================================ shard discipline


def test_the_shard_configuration_is_recorded_and_was_within_budget(manifest) -> None:
    cfg = manifest["shard_configuration"]
    assert cfg["shard_hash"] == "blake2b-64", "a process-randomised hash is not reproducible"
    assert cfg["within_budget"] is True
    assert cfg["pair_max"] <= cfg["budget_observations_per_pair"]
    assert manifest["versions"]["shard_version"] == "m11d/shard/v1"
    assert manifest["versions"]["normalisation_version"] == "m11-normalise-v2"


def test_the_pilot_measured_the_worst_case_before_the_full_run(manifest) -> None:
    """The budget came from a 100k-row estimate; the pilot checked the real maximum."""
    pilot = manifest["pilot"]
    cfg = manifest["shard_configuration"]
    assert pilot["observations"] == cfg["pair_max"], "the pilot must use the LARGEST shard"
    assert pilot["bytes_per_observation_at_peak"] > 0
    # The pilot's measured cost must still fit the declared budget.
    assert pilot["bytes_per_observation_at_peak"] * cfg["budget_observations_per_pair"] < 2 * 2**30


def test_peak_memory_stayed_far_below_the_machine(manifest) -> None:
    res = manifest["resources"]
    assert res["diff_peak_rss_bytes"] < manifest["machine"]["physical_memory_bytes"] / 4
    assert res["reduction_factor"] > 10, "sharding must actually buy something"


# ======================================================== claims that must not drift


def test_identifier_agreement_is_reported_per_population_and_not_as_global_proof(
    manifest,
) -> None:
    """Two populations, two rates, neither a general claim.

    A single blended "entry DOI agreement" figure would merge a population where
    agreement was asked (link candidates) with one where it was not (slot moves),
    and would read as proof of stability the data cannot support.
    """
    ids = manifest["identifier_agreement"]
    assert "proof of global identifier stability" in ids["framing"]
    assert "WITHIN a defined population" in ids["framing"]
    assert "provisional" in ids["framing"]

    link = ids["link_candidate_population"]
    assert link["entry_doi"]["both_present"] == link["one_to_one_slots"]
    assert (
        link["entry_doi"]["agreed"] + link["entry_doi"]["disagreed"]
        == (link["entry_doi"]["both_present"])
    )

    moved = ids["moved_observation_population"]
    assert "without reference to either identifier" in moved["population"]
    assert "not proof of global stability" in moved["interpretation"]
    assert "CANDIDATE" in moved["status"]

    # Agreement is reported ONLY where the correspondence is forced.
    unambiguous = moved["unambiguous"]
    assert "forced" in unambiguous["definition"]
    assert unambiguous["entry_doi"]["agreed"] <= unambiguous["entry_doi"]["both_present"]
    assert (
        unambiguous["reactant_set_id"]["agreed"] <= (unambiguous["reactant_set_id"]["both_present"])
    )
    assert unambiguous["entry_doi"]["both_present"] <= unambiguous["matched_occurrences"]

    # Ambiguous groups are counted, never paired, and carry no agreement figure.
    ambiguous = moved["ambiguous"]
    assert "no individual correspondence is asserted" in ambiguous["definition"]
    assert "entry_doi" not in ambiguous, "an ambiguous group must not carry an agreement rate"
    assert ambiguous["pairable_upper_bound"] <= min(
        ambiguous["removal_occurrences"], ambiguous["addition_occurrences"]
    )

    # Occurrence accounting: each occurrence counted exactly once per side.
    acc = moved["occurrence_accounting"]
    assert acc["removal_occurrences_considered"] == (
        unambiguous["matched_occurrences"]
        + unambiguous["unmatched_removal_occurrences"]
        + ambiguous["removal_occurrences"]
    )
    assert acc["addition_occurrences_considered"] == (
        unambiguous["matched_occurrences"]
        + unambiguous["unmatched_addition_occurrences"]
        + ambiguous["addition_occurrences"]
    )
    assert moved["same_slot_pairs_seen"] == 0, (
        "a removal and an addition at one (slot, value) cannot both carry surplus"
    )

    # The positional sensitivity must be labelled, and kept out of the headline.
    sens = moved["positional_pairing_sensitivity"]
    assert "ALGORITHM-DEPENDENT" in sens["status"]
    assert sens["entry_doi"]["agreement_rate"] != unambiguous["entry_doi"]["agreement_rate"], (
        "if these ever coincide, check the sensitivity is still being computed separately"
    )


def test_the_ec50_decline_separates_demonstrated_from_inferred(manifest) -> None:
    """The finding most tempting to round into an explanation.

    One mechanism is demonstrated on a source-verified example and covers 7.7% of
    the removals. Presenting it as the cause would be the error; so would dropping
    it because it is partial.
    """
    ec50 = manifest["ec50_decline"]
    assert ec50["net"] == ec50["additions"] - ec50["removals"] == -10_137
    assert ec50["corrections_that_changed_measurement_type"] == 0
    assert ec50["unambiguous_candidate_share_of_removals"] < 0.10, (
        "if this ever exceeds a small fraction the wording must be revisited"
    )
    assert (
        ec50["removals_at_unattributed_slot"] + ec50["removals_at_attributed_slot"]
        == ec50["removals"]
    )
    # The ceiling must bound the unambiguous share, not replace it.
    assert (
        ec50["candidate_ceiling_share_of_removals"]
        >= (ec50["unambiguous_candidate_share_of_removals"])
    )
    assert ec50["net_contribution_of_cross_slot_candidates"] == 0, (
        "a matched candidate pairs one removal with one addition, so it is net-neutral"
    )
    statement = ec50["statement"]
    # Candidate, not established: the wording must not promote equality to identity.
    assert "CANDIDATE" in statement
    assert "NOT experimental identity" in statement
    assert "net-neutral by construction" in statement
    assert "cannot explain the net decline" in statement
    assert "no causal explanation is claimed" in statement


def test_the_scope_excludes_eligibility_and_says_why(manifest) -> None:
    """Eligibility is not computable per shard, and the reason is structural."""
    assert "no pair-eligibility calculation" in manifest["scope"]
    not_done = " ".join(manifest["not_done"])
    assert "evidence in several shards" in not_done
    assert "item 8 stays OPEN" in not_done


def test_large_detail_files_are_referenced_by_digest(manifest) -> None:
    """The report must not inline 380 MB, and must let a reader verify it."""
    files = manifest["detail_files"]
    assert files, "no detail files recorded"
    for stream, info in files.items():
        assert len(info["sha256"]) == 64, stream
        assert info["records"] >= 0
    assert files["additions"]["records"] == manifest["totals"]["added_observations"]
    assert files["removals"]["records"] == manifest["totals"]["removed_observations"]
    assert files["correction_candidates"]["records"] == manifest["totals"]["correction_candidates"]
    assert files["identifier_conflicts"]["records"] == manifest["totals"]["identifier_conflicts"]


# ============================================================== the report itself


def test_the_report_states_the_reconciliations_and_the_measured_resources(report) -> None:
    for figure in (
        "3,136,836",
        "3,233,963",
        "2,995,596",
        "238,367",
        "141,240",
        "0.90 GB",
        "25.3 GB",
        "200,060",
        "blake2b-64",
    ):
        assert figure in report, f"the report does not state: {figure}"


def test_the_report_keeps_corrections_provisional_and_the_study_exploratory(report) -> None:
    assert "provisional" in report
    assert "not signed" in report
    assert "exploratory" in report
    assert "No causal explanation is claimed" in report


def test_the_report_records_the_traceable_verified_example(report) -> None:
    """A source-verified locator is what makes a demonstrated transition auditable."""
    assert "BindingDB/202601/all#391578" in report
    assert "BindingDB/202609/all#390114" in report
    assert "pmid:20126400" in report
    assert "10.7270/Q2ZG6XSW" in report


def test_the_report_does_not_claim_the_shard_count_changed_anything(report) -> None:
    assert "does not change the result" in report or "must not change a scientific result" in report


# ============================================================== the correction pass


def test_the_correction_pass_records_that_the_main_diff_was_not_rerun(manifest) -> None:
    """The headline totals must be the same object they were before the fix.

    The defects were in a downstream analysis. Rerunning the matcher would have
    been both unnecessary and a way to quietly change the main result while
    claiming to fix something else.
    """
    corr = manifest["corrections"]
    assert corr["main_diff_unchanged"] is True
    assert "was NOT rerun" in corr["scope"]
    assert len(corr["items"]) >= 4
    joined = " ".join(corr["items"])
    assert "never consumed" in joined
    assert "file order" in joined
    assert "bounded" in joined


def test_old_and_corrected_values_are_both_recorded(manifest) -> None:
    """A correction that only shows the new number is not auditable."""
    fields = manifest["corrections"]["comparison"]["fields"]
    for name, pair in fields.items():
        assert "old" in pair and "corrected" in pair, name
    assert fields["matched_or_moved_occurrences"]["old"] == 30_699
    assert fields["entry_doi_agreement_rate"]["old"] == 0.969054
    assert fields["reactant_set_id_agreement_rate"]["old"] == 0.952604
    # and the corrected figures agree with the live population record
    u = manifest["identifier_agreement"]["moved_observation_population"]["unambiguous"]
    assert fields["matched_or_moved_occurrences"]["corrected"] == u["matched_occurrences"]
    assert fields["entry_doi_agreement_rate"]["corrected"] == u["entry_doi"]["agreement_rate"]


def test_the_direction_of_the_change_is_explained_not_just_stated(manifest) -> None:
    """Agreement went UP; a reader is owed the reason, which is not flattering."""
    comparison = manifest["corrections"]["comparison"]
    direction = comparison["direction_of_the_change"]
    assert "UNDERSTATED" in direction
    assert "arbitrarily" in direction
    assert "Blending the two populations was the error" in direction
    assert "what_the_old_number_actually_was" in comparison


def test_the_report_carries_the_comparison_table(report) -> None:
    assert "## 0. Correction pass" in report
    for figure in ("30,699", "28,231", "96.91%", "99.87%", "1,673"):
        assert figure in report, f"the report does not state: {figure}"
    assert "net-neutral" in report
    assert "ALGORITHM-DEPENDENT" in report or "algorithm-dependent" in report


def test_the_report_keeps_the_individual_example_apart_from_the_population(report) -> None:
    """The example is evidence about one row; the counts are evidence about the corpus."""
    assert "evidence about this case" in report
    assert "not** a population-wide claim" in report
    # And the population wording must not promote candidacy to identity.
    assert "candidate correspondences, not established re-attributions" in report
    assert "not a claim about what happened to a row" in report
