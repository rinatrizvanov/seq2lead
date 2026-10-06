"""The constants and the published contract, held against each other both ways.

`contract.py` exists so the checker and the runner cannot disagree about what is
frozen. That only helps if the module and the published contract file stay in
step, which is what this asserts -- in both directions, so neither a setting
added to the module nor one added to the file can slip through unpaired.

Also here: the qualifications and floors that must survive this step unchanged.
A revision that quietly relaxed a feasibility floor or switched the primary cell
would otherwise be indistinguishable from one that earned its verdict.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from seq2lead.asof.contract import (
    CONTRACT_VERSION,
    PINNED_SETTINGS,
    REQUIRED_DIGESTS,
    STORED_DIMS,
)

CONTRACT = Path("configs/m11f_evaluation_contract.json")
MANIFEST = Path("configs/manifests/m11g_preflight.json")
PREFLIGHT = Path("data/asof/m11g/cohort-preflight.json")

pytestmark = pytest.mark.skipif(
    not CONTRACT.exists(), reason="the evaluation contract is not present"
)


@pytest.fixture(scope="module")
def contract() -> dict:
    return json.loads(CONTRACT.read_text())


def test_the_module_and_the_file_declare_the_same_settings(contract) -> None:
    declared = contract["execution_settings"]["declared"]
    assert declared == PINNED_SETTINGS, (
        "the frozen settings in contract.py and in the published contract have parted "
        "company; whichever is right, the runner now validates against only one of them"
    )


def test_the_module_and_the_file_require_the_same_inputs(contract) -> None:
    assert set(contract["input_set"]["required"]) == set(REQUIRED_DIGESTS)
    assert set(contract["input_digests"]) == {f"{n}_sha256" for n in REQUIRED_DIGESTS}
    assert set(contract["input_set"]["paths"]) == set(REQUIRED_DIGESTS)


def test_every_bound_input_still_matches_the_bytes_on_disk(contract) -> None:
    """The contract's own digests, re-verified. A stale one is a broken binding."""
    stale = []
    for name, path in sorted(contract["input_set"]["paths"].items()):
        p = Path(path)
        if not p.exists():
            stale.append(f"{name}: {path} is missing")
            continue
        h = hashlib.sha256()
        with p.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 22), b""):
                h.update(chunk)
        if h.hexdigest() != contract["input_digests"][f"{name}_sha256"]:
            stale.append(f"{name}: {path} no longer matches its bound digest")
    assert not stale, stale


def test_the_stored_dimensions_are_the_packed_widths(contract) -> None:
    """256, not 2048. Checking the logical width calls every vector unusable."""
    assert STORED_DIMS == {"ecfp4": 256, "esm2": 1280}
    assert PINNED_SETTINGS["feature_stored_dims"] == STORED_DIMS
    assert contract["execution_settings"]["declared"]["feature_stored_dims"] == STORED_DIMS
    for kind, dim in STORED_DIMS.items():
        assert contract["feature_bindings"][kind]["stored_dim"] == dim


def test_the_contract_version_is_the_one_the_module_names(contract) -> None:
    assert contract["contract"] == CONTRACT_VERSION
    assert contract["supersedes"] != CONTRACT_VERSION


# ===================================== what must survive this step unchanged


def test_the_declared_threshold_and_primary_cell_are_unchanged(contract) -> None:
    assert contract["execution_settings"]["declared"]["threshold_pki"] == 6.0
    assert contract["cells"]["primary"] == {
        "increment_arm": "declared_increment",
        "consistency_branch": "screened_primary",
    }
    assert len(contract["cells"]["sensitivities"]) == 3
    arms = {
        (c["increment_arm"], c["consistency_branch"])
        for c in contract["cells"]["sensitivities"]
    }
    assert ("declared_increment", "screened_primary") not in arms, (
        "the primary cell must not also be listed as a sensitivity"
    )
    assert len(arms) == 3


def test_all_four_cells_are_still_reported(contract) -> None:
    cells = json.loads(PREFLIGHT.read_text())["cells"]
    assert set(cells) == {
        f"{arm}/{branch}"
        for arm in ("declared_increment", "cross_slot_excluded")
        for branch in ("screened_primary", "unscreened_sensitivity")
    }


def test_the_feasibility_floors_were_not_moved(contract) -> None:
    """C1-C5 are the floors the user accepted. A pass earned by lowering one is not a pass."""
    f = contract["feasibility_after_partitioning_and_feature_exclusion"]
    assert f["no_floor_was_changed_to_obtain_a_pass"] is True
    for key in ("c1_to_c5", "c1_to_c5_strictest"):
        assert set(f[key]) == {
            "C1_at_least_50_rankable_targets",
            "C2_at_least_2000_pairs",
            "C3_positive_rate_in_0.20_0.80",
            "C4_all_four_cells_reported",
            "C5_not_argued_from_observation_fraction",
        }
    assert f["c1_to_c5"]["C1_at_least_50_rankable_targets"]["measured"] >= 50
    assert f["c1_to_c5"]["C2_at_least_2000_pairs"]["measured"] >= 2000
    assert 0.20 <= f["c1_to_c5"]["C3_positive_rate_in_0.20_0.80"]["measured"] <= 0.80


def test_retrieval_stays_unbuilt_and_evidence_display_stays_off(contract) -> None:
    declared = contract["execution_settings"]["declared"]
    assert declared["retrieval_built"] is False
    assert declared["evaluation_evidence_display_enabled"] is False
    assert "UNBUILT" in contract["feature_visibility"]["retrieval"]
    assert "DISABLED" in contract["feature_visibility"]["evaluation_evidence_display"]


def test_the_exploratory_and_pooling_qualifications_are_intact(contract) -> None:
    text = " ".join(contract["qualifications_carried_forward"])
    assert "EXPLORATORY" in text
    assert "PROVISIONAL POOLING" in text
    assert "NOT signed" in text


def test_nothing_was_fitted(contract) -> None:
    assert contract["entry_point"]["nothing_fitted"] is True
    assert "Nothing fitted" in contract["status"]
    assert "PLANNED fitting inputs" in contract["model_facing_datasets"]["status"]
    assert json.loads(MANIFEST.read_text())["nothing_fitted"] is True
    assert json.loads(MANIFEST.read_text())["confirmatory_freeze"] == "UNSIGNED"


def test_the_accepted_caches_are_recorded_as_preserved(contract) -> None:
    for kind in STORED_DIMS:
        assert contract["feature_bindings"][kind]["accepted"]["preserved_byte_for_byte"] is True
    assert json.loads(MANIFEST.read_text())[
        "accepted_caches_preserved_byte_for_byte"
    ] is True


def test_the_corrections_record_the_defects_this_step_closed(contract) -> None:
    """This round's items, and every earlier one carried forward exactly once.

    `items` is this revision alone; everything closed before it belongs in
    `carried_forward`. Both are asserted as exact sets because the generator
    twice read its lineage from the file it overwrites, which duplicated the
    earlier items into both lists on a second run.
    """
    ids = [item["id"] for item in contract["corrections"]["items"]]
    carried = [item["id"] for item in contract["corrections"]["carried_forward"]]
    assert set(ids) == {"E10", "E11", "E12"}, ids
    assert set(carried) == {"E1", "E2", "E3", "E4", "E5", "E6", "E7", "E8", "E9"}, carried
    assert len(ids) == len(set(ids)) and len(carried) == len(set(carried)), "duplicated"
    assert not set(ids) & set(carried), "an item is both current and carried forward"


def test_the_binding_defects_are_recorded_with_their_reproductions(contract) -> None:
    """Each of this round's defects must carry measured before/after evidence."""
    for item in contract["corrections"]["items"]:
        assert item["before"]["reached_fitting"] is True, item["id"]
        assert item["after"]["tests"], item["id"]
    by_id = {i["id"]: i for i in contract["corrections"]["items"]}
    # The reuse-map swap is the one that changed nothing in the record.
    swap = by_id["E11"]["before"]
    assert swap["train_digest_changed"] is False
    assert swap["coverage_reported_complete"] is True
    assert swap["bound_vectors_actually_changed"] is True


def test_missing_and_unusable_are_reported_apart(contract) -> None:
    """Either refuses the run, and neither is imputed, so both must be countable."""
    for role_kind, cov in contract["feature_coverage_measured"]["by_role"].items():
        assert "missing" in cov and "unusable" in cov, role_kind
        assert cov["nothing_imputed"] is True
        assert cov["missing"] == 0 and cov["unusable"] == 0, role_kind
