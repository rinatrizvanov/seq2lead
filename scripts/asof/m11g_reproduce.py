"""Reproduce the three binding holes through the real runner. No fixes here.

Each case passes a bound input set whose digests all match, so every gate the
runner has reports success -- and then substitutes the thing the runner actually
consumes. If a case reaches the mocked fit, the verification was decorative.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import numpy as np
from m11g_runner_fixture import Fixture  # noqa: E402

from seq2lead.asof.datasets import PreflightError  # noqa: E402
from seq2lead.asof.runner import FeatureSource, RoleEntities  # noqa: E402


def case(name: str, mutate) -> None:
    f = Fixture()
    detail = mutate(f)
    try:
        out = f.run()
    except PreflightError as exc:
        print(f"REFUSED  {name}\n         {exc}\n")
        return
    reached = {
        "fit_transforms": f.fit_transforms.called,
        "fit_model": f.fit_model.called,
        "select_checkpoint": f.select_checkpoint.called,
    }
    print(f"REACHED FITTING  {name}")
    print(f"         {detail}")
    print(f"         callbacks reached: {reached}")
    print(f"         train pairs {out['train']['pairs']}, "
          f"digest {out['train']['digest'][:16]}…")
    print(f"         coverage complete: "
          f"{all(c['complete'] for c in out['features']['coverage'].values())}\n")


def substitute_membership(f: Fixture) -> str:
    """Case 1: verify one membership file, load a different one."""
    other = f.root / "substituted-membership.jsonl"
    rows = f.membership.read_text().splitlines()
    # Keep one train pair, drop the rest, and move the target by 2 log units.
    kept = rows[0].replace('"regression_target_pki": 7.0', '"regression_target_pki": 9.0')
    other.write_text(kept + "\n" + rows[2] + "\n", encoding="utf-8")
    f.membership_override = other
    return (
        f"artifacts['a_membership'] = {f.membership.name} (digest verified), "
        f"membership_path = {other.name} (never checked)"
    )


def swap_feature_map(f: Fixture) -> str:
    """Case 2: swap two reuse-map entries. Same digests, same coverage, wrong vectors."""
    source = f.sources["ecfp4"]
    keys = sorted(source.reuse_map)
    a, b = keys[0], keys[1]
    swapped = dict(source.reuse_map)
    swapped[a], swapped[b] = swapped[b], swapped[a]
    f.sources["ecfp4"] = FeatureSource(
        kind="ecfp4",
        accepted_path=source.accepted_path,
        accepted_sha256=source.accepted_sha256,
        reuse_map=swapped,
        extension_path=source.extension_path,
        extension_sha256=source.extension_sha256,
    )
    return f"reuse_map entries for {a} and {b} swapped; storage digests untouched"


def drop_evaluation_role(f: Fixture) -> str:
    """Case 3a: omit the evaluation role entirely, skipping its coverage check."""
    from seq2lead.asof.runner import EVALUATION_ROLE

    f.roles.pop(EVALUATION_ROLE)
    return "roles = {train, validation} only; the evaluation cohort is never checked"


def shrink_train_role(f: Fixture) -> str:
    """Case 3b: declare a smaller train role, silently filtering fitting rows."""
    from seq2lead.asof.datasets import TRAIN_ROLE

    full = f.roles[TRAIN_ROLE]
    one = sorted(full.compounds)[:1]
    f.roles[TRAIN_ROLE] = RoleEntities(set(one), set(full.sequences))
    return (
        f"train role declares 1 of {len(full.compounds)} compounds; the rest become "
        "excluded_missing_feature instead of a refusal"
    )


def main() -> None:
    f = Fixture()
    out = f.run()
    print("BASELINE: the valid path reaches fitting, as it should")
    print(f"         train pairs {out['train']['pairs']}, "
          f"digest {out['train']['digest'][:16]}…\n")

    case("1. membership substitution", substitute_membership)
    case("2. feature-map swap", swap_feature_map)
    case("3a. evaluation role dropped", drop_evaluation_role)
    case("3b. train role shrunk", shrink_train_role)

    # Case 2 needs its own evidence that the vectors really changed.
    f2 = Fixture()
    before = f2.vector_for("ecfp4", sorted(f2.sources["ecfp4"].reuse_map)[0])
    swap_feature_map(f2)
    after = f2.vector_for("ecfp4", sorted(f2.sources["ecfp4"].reuse_map)[0])
    print("case 2 evidence: the vector bound to that key changed:",
          not np.array_equal(before, after))


if __name__ == "__main__":
    main()
