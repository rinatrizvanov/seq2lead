"""Regenerate the evaluation contract at v4 and its manifest. Reads, never guesses."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from seq2lead.asof.contract import CONTRACT_VERSION, PINNED_SETTINGS, REQUIRED_DIGESTS
from seq2lead.asof.runner import RUNNER_VERSION

W, G = Path("data/asof/m11f"), Path("data/asof/m11g")
CONTRACT = Path("configs/m11f_evaluation_contract.json")
MANIFEST = Path("configs/manifests/m11g_preflight.json")
#: The v3-era manifest, which this step does not write. Lineage is read from it
#: rather than from the contract file, because the contract file is what this
#: script overwrites -- reading lineage from its own output made a second run
#: record v4 as superseding v4 and drop E1-E4 from the carried-forward list.
PRIOR_MANIFEST = Path("configs/manifests/m11f_preflight.json")
PRIOR_CONTRACT_VERSION = "m11f-asof-evaluation-contract-v4"
PRIOR_CORRECTION_VERSION = "m11g-correction-v1"


#: The v4 round's corrections, held as literals. An earlier revision read these
#: back from the contract file this script overwrites, so a second run recorded
#: them twice: once as this round's items and once as carried forward.
V4_ITEMS = [
            {
                "id": "E5",
                "defect": (
                    "prepare_fitting's preconditions were keyword arguments defaulting to "
                    "None and were skipped when omitted, so a caller could reach the fit "
                    "callbacks without a single input or setting having been checked."
                ),
                "fix": (
                    "they are required arguments, and a production entry point "
                    "(seq2lead.asof.runner.run) now owns the complete gate set. "
                    "tests/test_m11f_datasets.py asserts the parameters have no defaults."
                ),
            },
            {
                "id": "E6",
                "defect": (
                    "verify_preconditions accepted an EMPTY digest set: the loop had "
                    "nothing to iterate, so 'nothing was checked' read as 'all verified'. "
                    "The previous test suite asserted this as intended behaviour."
                ),
                "fix": (
                    "an empty set is refused, the complete pinned set is required, and an "
                    "unrecognised bound input is refused rather than ignored."
                ),
            },
            {
                "id": "E7",
                "defect": (
                    "projection_dim and metric_version were required to EXIST and never "
                    "compared, so a run at dim 256 under metric version m8/v1 passed the "
                    "gate and would have produced numbers incomparable with M8/M9."
                ),
                "fix": (
                    "every declared setting is compared to the frozen value, from one "
                    "shared module that a test holds against this contract file."
                ),
            },
            {
                "id": "E8",
                "defect": (
                    "the feature-completion pass resolved A-role entities only. The runner "
                    "then refused: 5,786 evaluation-cohort compounds and 59 targets had no "
                    "binding. The gate caught what the completion pass missed."
                ),
                "fix": (
                    "the evaluation role's entities are resolved in 202609 -- the accepted "
                    "cache's own schema, since snapshot B IS 202609 -- so no surrogate id "
                    "is equated across schemas. All 17,373 compounds and 1,060 targets now "
                    "bind, 6,016 and 59 of them newly mapped, none unresolvable."
                ),
            },
            {
                "id": "E9",
                "defect": (
                    "M11f reported missing_or_unusable_feature: 0 for both fitting roles, "
                    "but no allow-list was ever passed to the loader, so the figure meant "
                    "'no filter was applied' rather than 'nothing was missing'."
                ),
                "fix": (
                    "the runner passes the allow-list, PER ROLE rather than as a union. The "
                    "resulting zero is a consequence rather than independent evidence -- the "
                    "allow-list IS the verified entity set, and incomplete coverage refuses "
                    "the run before the loader is reached -- so the guarantee is stated as "
                    "what it is: every emitted pair has a verified vector for both its "
                    "compound and its target, or there is no run at all. What the extension "
                    "bought is measured separately, by running the same loader with the "
                    "pre-extension allow-list: 6,869 train and 1,205 validation pairs."
                ),
            },
]


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 22), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> None:
    import sys

    sys.path.insert(0, str(Path(__file__).parent))
    from m11g_bind import ARTIFACTS

    old = json.loads(CONTRACT.read_text())
    cohort = json.loads((G / "cohort-preflight.json").read_text())
    datasets = json.loads((G / "model-facing-datasets.json").read_text())
    record = json.loads((G / "runner-record.json").read_text())
    ext = json.loads((G / "feature-extension.json").read_text())
    resolution = json.loads((G / "resolution.json").read_text())

    new = dict(old)
    prior = json.loads(PRIOR_MANIFEST.read_text())
    this_round = json.loads(
        Path("data/asof/m11g/binding-defects.json").read_text()
    )["defects"]
    new["contract"] = CONTRACT_VERSION
    new["supersedes"] = PRIOR_CONTRACT_VERSION
    new.pop("regenerated_at", None)  # a timestamp here makes the digest unstable

    # ---- the one thing the runner validates settings against -----------------
    new["execution_settings"] = {
        "declared": dict(sorted(PINNED_SETTINGS.items())),
        "validated_by": "seq2lead.asof.contract.check_settings, called from the runner",
        "rule": (
            "every key must be present AND equal. An earlier revision required "
            "`projection_dim` and `metric_version` to exist without comparing them, so a "
            "run at dim 256 under metric version m8/v1 passed the gate."
        ),
        "source": "tests/test_m11g_contract.py asserts these against this file, both ways",
    }

    # ---- the complete bound input set ---------------------------------------
    new["input_digests"] = {
        f"{name}_sha256": sha256(path) for name, path in sorted(ARTIFACTS.items())
    }
    new["input_set"] = {
        "required": sorted(REQUIRED_DIGESTS),
        "paths": {name: str(path) for name, path in sorted(ARTIFACTS.items())},
        "rule": (
            "the complete set is required and an empty set is refused. The runner "
            "recomputes each digest from the bytes on disk before any fit callback runs."
        ),
    }

    # ---- the production entry point -----------------------------------------
    new["entry_point"] = {
        "callable": "seq2lead.asof.runner.run",
        "runner_version": RUNNER_VERSION,
        "gate_order": [
            "validate every declared execution setting against the frozen value",
            "verify the complete pinned input set, rehashed from disk",
            "derive the feature sources from the verified binding and resolution, "
            "verifying each reuse map's digest and cross-checking the two artifacts",
            "derive every role: the fitting roles from the verified membership over the "
            "rows the loader keeps, the evaluation role from the pinned entity artifact",
            "require complete coverage for exactly the three roles, missing and unusable "
            "counted apart",
            "re-verify the membership's bytes at the moment of consumption, then load "
            "both datasets from that artifact",
            "check the emitted datasets against the pinned model-facing record, by count "
            "and by digest",
            "refuse any train/validation overlap",
            "fit transforms on A-train, then the model on A-train",
            "select the checkpoint on A-validation, and nothing else",
        ],
        "nothing_consumed_is_a_parameter": {
            "rule": (
                "a value the run consumes may not arrive as a caller parameter beside a "
                "digest that does not govern it. Three did, and all three were "
                "exploitable with every gate reporting success."
            ),
            "derived_from": "see the derived_from map; the runner records it in its output",
            "evidence": "data/asof/m11g/binding-defects.json",
        },
        "why_not_prepare_fitting": (
            "prepare_fitting is an orchestration primitive whose fit callables are "
            "parameters, which is what lets a test inspect them. Its preconditions were "
            "optional, so a production caller could reach the fit with nothing checked."
        ),
        "exercised_with_fitting_mocked": "tests/test_m11g_runner.py",
        "demonstrated_refusals": [
            "a membership substituted, changed mid-run, or changed with its digest "
            "refreshed -- the last caught by the pinned-dataset check",
            "a reuse map swapped, with or without its resolution record refreshed",
            "an under-declared or missing role -- structurally impossible, since roles "
            "are derived",
            "an emitted dataset that is not the pinned one, by count or by digest",
            "missing features, per role, including the evaluation cohort",
            "any one of the pinned inputs altered after binding",
            "an incomplete or empty bound input set",
            "incompatible bindings: wrong stored dimension, wrong digest, a key in both "
            "the accepted cache and the extension, an extension with no digest",
            "any declared setting changed or dropped",
        ],
        "demonstrated_success_path": (
            "transforms and model fitted from A-train only; A-validation reaches checkpoint "
            "selection and no fit; B has no parameter to arrive through"
        ),
        "refusals_precede_dataset_reads": (
            "each refusal test points the membership path at a file that does not exist, so "
            "a skipped gate would surface as FileNotFoundError instead of PreflightError"
        ),
        "nothing_fitted": True,
    }

    # ---- features: accepted caches plus the registered extensions ------------
    new["feature_bindings"] = {
        kind: {
            "accepted": {
                "path": ext["accepted_caches"][kind]["path"],
                "storage_sha256": ext["accepted_caches"][kind]["sha256"],
                "preserved_byte_for_byte": True,
            },
            "extension": {
                "name": ext["extensions"][kind]["name"],
                "path": ext["extensions"][kind]["path"],
                "storage_sha256": ext["extensions"][kind]["storage_sha256"],
                "requested": ext["extensions"][kind]["requested"],
                "written": ext["extensions"][kind]["written"],
                "unusable": ext["extensions"][kind]["unusable"],
                "keyed_by": ext["extensions"][kind]["spec"]["keyed_by"],
            },
            "stored_dim": PINNED_SETTINGS["feature_stored_dims"][kind],
        }
        for kind in ("ecfp4", "esm2")
    }
    new["feature_bindings"]["identity_rule"] = ext["identity_rule"]
    new["feature_bindings"]["nothing_imputed"] = ext["nothing_imputed"]
    new["feature_bindings"]["cache_identity_rule"] = old["feature_visibility"][
        "cache_identity_rule"
    ]
    assert new["supersedes"] != CONTRACT_VERSION, "lineage must not point at itself"
    carried = {i["id"] for i in prior["corrections"]["items"] + V4_ITEMS}
    assert carried == {"E1", "E2", "E3", "E4", "E5", "E6", "E7", "E8", "E9"}, carried
    assert {i["id"] for i in this_round} == {"E10", "E11", "E12"}
    new["feature_resolution"] = resolution
    new["feature_coverage_measured"] = {
        "by_role": record["features"]["coverage"],
        "stored_dims_verified": record["features"]["stored_dims_verified"],
        "nothing_imputed": True,
        "measured_by": "the production runner, which refuses an incomplete role",
    }
    new["model_facing_datasets"] = {
        **datasets,
        "loader": "seq2lead.asof.datasets.load_dataset, called by the runner",
        "status": (
            "PLANNED fitting inputs. Emitted to mocked fit callables by the production "
            "entry point; no fit has consumed them."
        ),
    }

    # ---- feasibility, recomputed --------------------------------------------
    new["feasibility_after_partitioning_and_feature_exclusion"] = {
        "headline_cohort": cohort["headline_cohort"],
        "strictest_subgroup": cohort["strictest_subgroup"],
        "c1_to_c5": cohort["c1_to_c5_on_the_headline_cohort"],
        "c1_to_c5_strictest": cohort["c1_to_c5_on_the_strictest_subgroup"],
        "no_floor_was_changed_to_obtain_a_pass": True,
        "status": "PLANNED fitting inputs until fitting occurs",
        "feature_exclusions": cohort["feature_exclusions"],
        "changes_from_v4": {
            "note": (
                "recomputed from the datasets the production runner emitted, with every "
                "input it consumes now DERIVED from the pinned artifact set rather than "
                "supplied beside it. No feature vector was rebuilt."
            ),
            "headline_pairs": {
                "v4": old["feasibility_after_partitioning_and_feature_exclusion"][
                    "headline_cohort"
                ]["pairs"],
                "v5": cohort["headline_cohort"]["pairs"],
            },
            "headline_rankable_at_5": {
                "v4": old["feasibility_after_partitioning_and_feature_exclusion"][
                    "headline_cohort"
                ]["rankable_at"]["5"],
                "v5": cohort["headline_cohort"]["rankable_at"]["5"],
            },
            "headline_positive_rate": {
                "v4": old["feasibility_after_partitioning_and_feature_exclusion"][
                    "headline_cohort"
                ]["class_balance"]["positive_rate"],
                "v5": cohort["headline_cohort"]["class_balance"]["positive_rate"],
            },
        },
    }
    new["strata"] = {
        **old["strata"],
        "recurrence_defined_against": cohort["recurrence_defined_against"],
        "measured_difference": {
            **cohort["historical_presence_versus_fitted"],
        },
        "cells_note": "all four cells are reported in data/asof/m11g/cohort-preflight.json",
    }

    # ---- corrections this revision records ----------------------------------
    new["corrections"] = {
        "version": "m11g-correction-v2",
        "supersedes": PRIOR_CORRECTION_VERSION,
        "carried_forward": prior["corrections"]["items"] + V4_ITEMS,
        "items": this_round,
    }

    # ---- the two blocks this step makes stale -------------------------------
    new["execution_path_verified_without_training"] = {
        "how": (
            "the production entry point on the real artifacts, with the fit and selection "
            "callables replaced by mocks"
        ),
        "entry_point": "seq2lead.asof.runner.run",
        "proved": [
            "only A-training evidence supplies regression targets and fitted transforms",
            "A-validation supplies checkpoint-selection targets only and reaches no fit",
            "B evidence cannot enter either path -- the runner has no parameter for it",
            "missing features, altered inputs, incompatible bindings and changed "
            "configuration each refuse BEFORE any fit or artifact publication",
            "each refusal precedes even reading the datasets: the refusal tests point the "
            "membership path at a file that does not exist, so a skipped gate would raise "
            "FileNotFoundError instead",
        ],
        "tests": [
            "tests/test_m11g_runner.py",
            "tests/test_m11f_datasets.py",
            "tests/test_m11g_contract.py",
        ],
        "superseded_claim": (
            "v3 cited tests/test_m11f_datasets.py alone, whose precondition tests then "
            "asserted an empty digest set as verified."
        ),
    }
    new["unmet_preconditions"] = [
        {
            "id": "P1",
            "precondition": "every entity in every role has a usable vector",
            "status": "MET",
            "detail": (
                "the A roles' gap is closed by a registered extension: 3,422 distinct "
                "compounds and 13 targets computed under the pinned settings, 0 unusable. "
                "The evaluation role's entities were resolved in 202609, the accepted "
                "cache's own schema. The runner measures coverage per role and refuses an "
                "incomplete one; all six role/kind pairs are complete with 0 missing and "
                "0 unusable."
            ),
            "how_it_was_met": (
                "resolution by verified content identity, never by equating surrogate ids "
                "across schemas; the accepted caches are preserved byte for byte and each "
                "extension is registered under its own name, manifest and storage digest."
            ),
        }
    ]

    new["status"] = (
        "EXECUTABLE SPECIFICATION, EXERCISED WITH FITTING MOCKED. Nothing fitted, no "
        "prediction metric computed, no downloads, no docking, retrieval unbuilt, "
        "evaluation evidence display disabled, confirmatory freeze unsigned."
    )
    CONTRACT.write_text(json.dumps(new, indent=1, sort_keys=True) + "\n", encoding="utf-8")

    artifacts = {
        **{str(p): {"bytes": Path(p).stat().st_size, "sha256": sha256(Path(p))}
           for p in sorted(str(v) for v in ARTIFACTS.values())},
        **{str(p): {"bytes": p.stat().st_size, "sha256": sha256(p)}
           for p in sorted(G.glob("*.json"))},
    }
    MANIFEST.write_text(
        json.dumps(
            {
                "manifest": "m11g-preflight-v1",
                "contract": CONTRACT_VERSION,
                "contract_sha256": sha256(CONTRACT),
                "runner_version": RUNNER_VERSION,
                "recorded_at": datetime.now(UTC).isoformat(),
                "artifacts": artifacts,
                "accepted_caches_preserved_byte_for_byte": ext[
                    "accepted_caches_preserved_byte_for_byte"
                ],
                "nothing_fitted": True,
                "retrieval": "STAYS UNBUILT",
                "evaluation_evidence_display": "STAYS DISABLED",
                "confirmatory_freeze": "UNSIGNED",
                "qualifications_carried_forward": new["qualifications_carried_forward"],
            },
            indent=1, sort_keys=True,
        ) + "\n",
        encoding="utf-8",
    )
    print(f"wrote {CONTRACT} ({CONTRACT.stat().st_size:,} bytes)")
    print(f"wrote {MANIFEST} ({MANIFEST.stat().st_size:,} bytes)")
    print(f"contract sha256 {sha256(CONTRACT)[:24]}…")


if __name__ == "__main__":
    main()
