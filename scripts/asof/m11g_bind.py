"""Write the artifacts the runner now DERIVES from, then run it twice.

No feature vectors are rebuilt. This step only rewires: the reuse maps gain a
digest-bound home in the resolution artifact, the evaluation cohort's entities
become a pinned artifact of their own, and the feature binding is written in the
shape the runner reads. The .npz caches and extensions are untouched.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections import Counter, defaultdict
from pathlib import Path
from unittest.mock import MagicMock

from seq2lead.asof.contract import PINNED_SETTINGS, STORED_DIMS
from seq2lead.asof.datasets import TRAIN_ROLE, VALIDATION_ROLE, load_dataset
from seq2lead.asof.ki_aggregation import MIN_PER_CLASS
from seq2lead.asof.partition import (
    NEW_ABSENT_FROM_A,
    NEW_RESERVED_FOR_VALIDATION,
    NEW_TO_FITTING,
    RECURRENT,
    VALIDATION_EXPOSURE_NOTE,
    load_membership,
    stratify,
)
from seq2lead.asof.runner import derive_roles, run

W, G = Path("data/asof/m11f"), Path("data/asof/m11g")
AUDIT = Path("data/asof/m11e/pairs-pki6.jsonl")
ARMS = ("declared_increment", "cross_slot_excluded")
BRANCHES = ("screened_primary", "unscreened_sensitivity")
PRIMARY_CELL = ("declared_increment", "screened_primary")

ARTIFACTS = {
    "a_export": Path("data/asof/observations-202601.jsonl.gz"),
    "b_export": Path("data/asof/observations-202609.jsonl.gz"),
    "a_membership": W / "a-membership.jsonl",
    "partition_summary": W / "partition-summary.json",
    "cross_slot_exclusion_set": Path("data/asof/m11e/excluded-locators-ki.json"),
    "sensitivity_spec": Path("configs/m11e_cross_slot_sensitivity.json"),
    "cohort_preflight": G / "cohort-preflight.json",
    "feature_binding": G / "feature-binding.json",
    "feature_coverage": G / "feature-coverage.json",
    "feature_resolution": G / "resolution.json",
    "feature_extension": G / "feature-extension.json",
    "model_facing_datasets": G / "model-facing-datasets.json",
    "evaluation_entities": G / "evaluation-entities.json",
    "m9_config": Path("configs/experiments/m9-dual-encoder-v1.yaml"),
}
REGENERATED = ("cohort_preflight", "feature_coverage", "model_facing_datasets")

REUSE_MAP_PATHS = {
    "ecfp4": G / "reuse-compounds.json",
    "esm2": G / "reuse-targets.json",
}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 22), b""):
            h.update(chunk)
    return h.hexdigest()


def write_evaluation_entities() -> dict:
    """The evaluation cohort's entities, as a pinned artifact the runner reads."""
    compounds, targets = set(), set()
    with AUDIT.open(encoding="utf-8") as fh:
        for line in fh:
            rec = json.loads(line)
            if not any(rec["arms"][a]["has_increment"] for a in ARMS):
                continue
            c, t = rec["pair"].split("|", 1)
            compounds.add(c)
            targets.add(t)
    body = {
        "artifact": "m11g-evaluation-entities-v1",
        "rule": (
            "the union over both increment arms of pairs with `has_increment`, a superset "
            "of either arm, so coverage covers every cell that could be reported"
        ),
        "derived_from": str(AUDIT),
        "derived_from_sha256": sha256(AUDIT),
        "compounds": sorted(compounds),
        "sequences": sorted(targets),
        "counts": {"compounds": len(compounds), "sequences": len(targets)},
        "why_pinned": (
            "the runner used to take role entities as a parameter, so dropping the "
            "evaluation role skipped its coverage check entirely. The entities are now "
            "derived from this artifact, whose digest is in the pinned input set."
        ),
    }
    out = G / "evaluation-entities.json"
    out.write_text(json.dumps(body, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"evaluation entities: {len(compounds):,} compounds / {len(targets):,} targets")
    return body


def write_binding_and_resolution() -> None:
    """Give the reuse maps a digest-bound home and write the binding the runner reads."""
    ext = json.loads((G / "feature-extension.json").read_text())
    entries = {}
    for kind, path in REUSE_MAP_PATHS.items():
        entries[kind] = len(json.loads(path.read_text()))

    binding = {
        "binding": "m11g-feature-binding-v2",
        "supersedes": "m11g-feature-binding-v1",
        "sources": {
            kind: {
                "accepted_path": ext["accepted_caches"][kind]["path"],
                "accepted_sha256": ext["accepted_caches"][kind]["sha256"],
                "extension_path": ext["extensions"][kind]["path"],
                "extension_sha256": ext["extensions"][kind]["storage_sha256"],
                "reuse_map_entries": entries[kind],
                "stored_dim": STORED_DIMS[kind],
                "keyed_by": "content identity; no surrogate id is equated across schemas",
            }
            for kind in sorted(STORED_DIMS)
        },
        "verified_by": "seq2lead.asof.features.load_binding, called by the runner",
        "why_v2": (
            "v1 recorded the caches only. The runner took the reuse map as a caller "
            "parameter, so swapping two entries repointed those vectors while every "
            "storage digest, the coverage report and even the emitted dataset digest "
            "stayed identical. The map is now loaded from the resolution artifact and "
            "cross-checked against the entry count declared here."
        ),
    }
    (G / "feature-binding.json").write_text(
        json.dumps(binding, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    resolution = json.loads((G / "resolution.json").read_text())
    resolution["reuse_maps"] = {
        kind: {
            "path": str(path),
            "sha256": sha256(path),
            "entries": entries[kind],
            "keyed_by": "inchikey" if kind == "ecfp4" else "sequence_sha256",
        }
        for kind, path in sorted(REUSE_MAP_PATHS.items())
    }
    resolution["reuse_maps_note"] = (
        "the mapping from content identity to the accepted cache's surrogate id. Pinned "
        "here because it decides which cached row each key receives: a caller-supplied "
        "map could repoint every vector without changing any digest the run reports."
    )
    (G / "resolution.json").write_text(
        json.dumps(resolution, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print("reuse maps pinned: " + ", ".join(
        f"{k} {v['entries']:,} @ {v['sha256'][:12]}…"
        for k, v in resolution["reuse_maps"].items()
    ))


class Cell:
    def __init__(self) -> None:
        self.labels: Counter[str] = Counter()
        self.per_target: dict[str, Counter[str]] = defaultdict(Counter)
        self.compounds: set[str] = set()

    def admit(self, target: str, compound: str, label: str) -> None:
        self.labels[label] += 1
        self.per_target[target][label] += 1
        self.compounds.add(compound)

    def rankable(self, n: int) -> int:
        return sum(
            1 for c in self.per_target.values()
            if c.get("active", 0) >= n and c.get("inactive", 0) >= n
        )

    def as_dict(self) -> dict:
        a, i = self.labels.get("active", 0), self.labels.get("inactive", 0)
        decided = a + i
        return {
            "pairs": sum(self.labels.values()),
            "labels": dict(sorted(self.labels.items())),
            "class_balance": {
                "active": a, "inactive": i, "decided": decided,
                "positive_rate": round(a / decided, 6) if decided else None,
                "minority_count": min(a, i) if decided else None,
            },
            "targets": len(self.per_target),
            "compounds": len(self.compounds),
            "single_class_targets": sum(
                1 for c in self.per_target.values()
                if bool(c.get("active", 0)) != bool(c.get("inactive", 0))
            ),
            "rankable_at": {str(n): self.rankable(n) for n in MIN_PER_CLASS},
        }


def assess(cell: dict) -> dict:
    cb = cell["class_balance"]
    r5 = cell["rankable_at"]["5"]
    return {
        "C1_at_least_50_rankable_targets": {"measured": r5, "met": r5 >= 50},
        "C2_at_least_2000_pairs": {"measured": cell["pairs"], "met": cell["pairs"] >= 2000},
        "C3_positive_rate_in_0.20_0.80": {
            "measured": cb["positive_rate"],
            "met": cb["positive_rate"] is not None and 0.20 <= cb["positive_rate"] <= 0.80,
        },
        "C4_all_four_cells_reported": {"met": True},
        "C5_not_argued_from_observation_fraction": {"met": True},
    }


def main() -> None:
    t0 = time.time()
    evaluation = write_evaluation_entities()
    write_binding_and_resolution()

    roles = derive_roles(ARTIFACTS)
    print("\nroles DERIVED by the runner (loader-kept rows only):")
    for r, e in sorted(roles.items()):
        print(f"  {r:<12} {len(e.compounds):>7,} compounds  {len(e.sequences):>6,} sequences")

    digests = {name: sha256(path) for name, path in ARTIFACTS.items()}
    ft, fm, fs = MagicMock(), MagicMock(), MagicMock()
    out = run(
        expected_digests=digests, artifacts=dict(ARTIFACTS), config=dict(PINNED_SETTINGS),
        fit_transforms=ft, fit_model=fm, select_checkpoint=fs,
    )
    (train_arg,), _ = ft.call_args
    (sel_arg, _m), _ = fs.call_args
    print(f"\nrunner reached the mocks: fit on {len(train_arg):,} {train_arg.role} pairs, "
          f"selection on {len(sel_arg):,} {sel_arg.role} pairs")
    for key, cov in sorted(out["features"]["coverage"].items()):
        print(f"  {key:<24} requested {cov['requested']:>7,}  usable {cov['usable']:>7,}  "
              f"missing {cov['missing']:>3}  unusable {cov['unusable']:>3}  "
              f"accepted {cov['from_accepted_cache']:>7,}  ext {cov['from_extension']:>5,}")
    # The derived role must be exactly the emitted population, or the allow-list
    # is either wider than the fit (useless) or narrower (silently filtering).
    assert roles[TRAIN_ROLE].compounds == train_arg.compounds
    assert roles[VALIDATION_ROLE].sequences == sel_arg.sequences
    print("derived role entities == emitted dataset entities: True")

    # ---- cohorts, recurrence, feasibility, from the emitted training set -----
    membership = load_membership(W / "a-membership.jsonl")
    eval_c, eval_t = set(evaluation["compounds"]), set(evaluation["sequences"])
    fitted = set(train_arg.pairs)
    cells: dict[tuple, dict[str, Cell]] = {
        (arm, br): defaultdict(Cell) for arm in ARMS for br in BRANCHES
    }
    excluded_for_features: Counter[str] = Counter()
    recurrent_pairs: set[str] = set()
    strata_union: Counter[str] = Counter()
    strata_by_arm: dict[str, Counter[str]] = {a: Counter() for a in ARMS}
    with AUDIT.open(encoding="utf-8") as fh:
        for line in fh:
            rec = json.loads(line)
            compound, target = rec["pair"].split("|", 1)
            stratum = stratify(membership.get(rec["pair"]))
            counted = False
            if stratum == RECURRENT and any(
                rec["arms"][a]["has_increment"] and rec["arms"][a]["is_scoreable"] for a in ARMS
            ):
                recurrent_pairs.add(rec["pair"])
            for arm in ARMS:
                v = rec["arms"][arm]
                if not v["has_increment"] or not v["is_scoreable"]:
                    continue
                if compound not in eval_c or target not in eval_t:
                    excluded_for_features[arm] += 1
                    continue
                strata_by_arm[arm][stratum] += 1
                if not counted:
                    strata_union[stratum] += 1
                    counted = True
                for br in BRANCHES:
                    if br == "screened_primary" and not v["branch_membership"][br]:
                        continue
                    cells[(arm, br)][stratum].admit(target, compound, v["increment_label"])

    before = {
        role: load_dataset(
            W / "a-membership.jsonl", role,
            usable_compounds=roles[role].compounds - set(
                json.loads((G / "recompute-compounds.json").read_text())
            ),
            usable_sequences=roles[role].sequences - set(
                json.loads((G / "recompute-targets.json").read_text())
            ),
        )
        for role in (TRAIN_ROLE, VALIDATION_ROLE)
    }
    after = {TRAIN_ROLE: train_arg, VALIDATION_ROLE: sel_arg}

    body = {
        "preflight": "m11g-cohort-v2",
        "supersedes": "m11g-cohort-v1",
        "threshold_pki": 6.0,
        "primary_cell": {"increment_arm": PRIMARY_CELL[0], "consistency_branch": PRIMARY_CELL[1]},
        "status": (
            "PLANNED fitting inputs. Nothing has been fitted. These are the pairs the "
            "production runner DID emit to mocked fit callables, not a reconstruction."
        ),
        "emitted_by": {
            "entry_point": "seq2lead.asof.runner.run",
            "runner_version": out["runner_version"],
            "contract_version": out["contract_version"],
            "derived_from": out["derived_from"],
            "roles_derived": out["roles_derived"],
            "fitting": "mocked",
        },
        "recurrence_defined_against": (
            "the exact pair set the production runner handed to the model-fitting callback: "
            "train-partition pairs with an exact-only regression target, after eligibility "
            "AND the derived feature allow-list. NOT historical presence in A."
        ),
        "validation_exposure_note": VALIDATION_EXPOSURE_NOTE,
        "feature_exclusions": {
            "evaluation_cohort_pairs_excluded_per_arm": dict(excluded_for_features),
            "fitting_roles": {
                role: {
                    "pairs_excluded_for_missing_or_unusable_feature":
                        after[role].excluded_missing_feature,
                    "pairs_emitted": len(after[role]),
                }
                for role in (TRAIN_ROLE, VALIDATION_ROLE)
            },
            "counterfactual_without_the_extension": {
                role: {
                    "pairs_excluded_for_missing_or_unusable_feature":
                        before[role].excluded_missing_feature,
                    "pairs_that_would_remain": len(before[role]),
                }
                for role in (TRAIN_ROLE, VALIDATION_ROLE)
            },
            "note": (
                "the fitting-role zeros are a CONSEQUENCE, not independent evidence: the "
                "allow-list is derived from the rows the loader keeps, so it cannot exclude "
                "one. The guarantee is that incomplete coverage refuses the run."
            ),
        },
        "strata_eligible_by_arm": {
            arm: dict(sorted(c.items())) for arm, c in strata_by_arm.items()
        },
        "strata_eligible_union_over_arms": dict(sorted(strata_union.items())),
        "historical_presence_versus_fitted": {
            "note": (
                "the contract forbids assuming this difference is zero, so it is measured."
            ),
            "declared_arm_eligible_a_present": (
                strata_by_arm["declared_increment"][RECURRENT]
                + strata_by_arm["declared_increment"][NEW_RESERVED_FOR_VALIDATION]
                + strata_by_arm["declared_increment"]["new_a_present_excluded_from_fitting"]
            ),
            "declared_arm_eligible_fitted": strata_by_arm["declared_increment"][RECURRENT],
            "a_present_but_not_fitted": (
                strata_by_arm["declared_increment"][NEW_RESERVED_FOR_VALIDATION]
                + strata_by_arm["declared_increment"]["new_a_present_excluded_from_fitting"]
            ),
        },
        "recurrence_check": {
            "runner_training_pairs": len(fitted),
            "stratify_recurrent_total": strata_union[RECURRENT],
            "recurrent_pairs_not_in_the_runners_training_set": len(recurrent_pairs - fitted),
            "agrees": not (recurrent_pairs - fitted),
        },
        "cells": {
            f"{arm}/{br}": {
                stratum: cells[(arm, br)][stratum].as_dict()
                for stratum in sorted(cells[(arm, br)])
            }
            for arm in ARMS for br in BRANCHES
        },
    }

    primary = cells[PRIMARY_CELL]
    headline = Cell()
    for stratum in NEW_TO_FITTING:
        c = primary.get(stratum)
        if c is None:
            continue
        for tgt, counts in c.per_target.items():
            for lab, n in counts.items():
                headline.labels[lab] += n
                headline.per_target[tgt][lab] += n
        headline.compounds |= c.compounds
    body["headline_cohort"] = {
        "definition":
            f"{PRIMARY_CELL[0]} x {PRIMARY_CELL[1]}, new to fitting (all three subgroups)",
        **headline.as_dict(),
    }
    strict = primary.get(NEW_ABSENT_FROM_A)
    body["strictest_subgroup"] = {
        "definition": f"{PRIMARY_CELL[0]} x {PRIMARY_CELL[1]}, absent from A entirely",
        "why": "the only subgroup with no exposure to fitting OR selection",
        **(strict.as_dict() if strict else {}),
    }
    body["c1_to_c5_on_the_headline_cohort"] = assess(body["headline_cohort"])
    body["c1_to_c5_on_the_strictest_subgroup"] = (
        assess(body["strictest_subgroup"]) if strict else None
    )

    (G / "cohort-preflight.json").write_text(
        json.dumps(body, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (G / "model-facing-datasets.json").write_text(
        json.dumps(
            {
                role: {
                    **after[role].as_dict(),
                    "digest": after[role].digest(),
                    "emitted_by": "seq2lead.asof.runner.run, fitting mocked",
                    "feature_allow_list": "derived from the verified membership",
                }
                for role in (TRAIN_ROLE, VALIDATION_ROLE)
            },
            indent=2, sort_keys=True,
        ) + "\n",
        encoding="utf-8",
    )
    (G / "feature-coverage.json").write_text(
        json.dumps(
            {
                "coverage": "m11g-feature-coverage-v2",
                "supersedes": "m11g-feature-coverage-v1",
                "by_role": out["features"]["coverage"],
                "stored_dims_verified": out["features"]["stored_dims_verified"],
                "reuse_maps_verified": out["features"]["reuse_maps_verified"],
                "roles_derived": out["roles_derived"],
                "nothing_imputed": True,
                "measured_by": (
                    "the production runner, which derives every role from the pinned "
                    "artifacts and refuses any role whose coverage is incomplete."
                ),
                "why_the_requested_counts_changed": (
                    "v1 declared role entities over EVERY membership pair in the partition; "
                    "the runner now derives them over the rows the loader keeps, so the "
                    "requested counts equal the emitted datasets' distinct entities exactly. "
                    "No vector was rebuilt and no cache changed."
                ),
            },
            indent=2, sort_keys=True,
        ) + "\n",
        encoding="utf-8",
    )

    # ---- pass 2: re-bind to what pass 1 just wrote --------------------------
    final = {name: sha256(path) for name, path in ARTIFACTS.items()}
    ft2, fm2, fs2 = MagicMock(), MagicMock(), MagicMock()
    out2 = run(
        expected_digests=final, artifacts=dict(ARTIFACTS), config=dict(PINNED_SETTINGS),
        fit_transforms=ft2, fit_model=fm2, select_checkpoint=fs2,
    )
    assert out2["train"]["digest"] == out["train"]["digest"]
    assert out2["validation"]["digest"] == out["validation"]["digest"]
    print(f"\npass 2 bound to the regenerated artifacts; datasets identical: "
          f"train {out2['train']['digest'][:16]}… "
          f"validation {out2['validation']['digest'][:16]}…")
    (G / "runner-record.json").write_text(
        json.dumps(
            {
                **{k: v for k, v in out2.items() if k != "selection"},
                "pass_1_bound_the_superseded_artifacts": sorted(REGENERATED),
                "pass_2_emitted_identical_datasets": True,
            },
            indent=2, sort_keys=True, default=str,
        ) + "\n",
        encoding="utf-8",
    )
    print(f"\nheadline: {body['headline_cohort']['pairs']:,} pairs, "
          f"{body['headline_cohort']['targets']:,} targets, "
          f"r@5 {body['headline_cohort']['rankable_at']['5']}, "
          f"pos {body['headline_cohort']['class_balance']['positive_rate']}")
    print(f"recurrence agrees: {body['recurrence_check']['agrees']}")
    print(f"{time.time() - t0:.1f}s")


if __name__ == "__main__":
    main()
