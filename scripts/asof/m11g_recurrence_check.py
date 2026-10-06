"""Does the recurrence stratum agree with the set the runner actually fitted?

`stratify` reads the membership's partition and eligibility. The runner's
training set comes from the loader, which ALSO applies the feature allow-list.
Those two agree only while nothing is feature-excluded -- so the agreement is a
measurement, not a construction, and this checks it both with the extension and
without.
"""

from __future__ import annotations

import json
from pathlib import Path

from seq2lead.asof.datasets import TRAIN_ROLE, VALIDATION_ROLE, load_dataset
from seq2lead.asof.partition import RECURRENT, load_membership, stratify

W, G = Path("data/asof/m11f"), Path("data/asof/m11g")
AUDIT = Path("data/asof/m11e/pairs-pki6.jsonl")
ARMS = ("declared_increment", "cross_slot_excluded")


def main() -> None:
    membership = load_membership(W / "a-membership.jsonl")
    recompute_c = set(json.loads((G / "recompute-compounds.json").read_text()))
    recompute_t = set(json.loads((G / "recompute-targets.json").read_text()))

    role_c, role_t = {}, {}
    for role in (TRAIN_ROLE, VALIDATION_ROLE):
        role_c[role], role_t[role] = set(), set()
    from seq2lead.asof.partition import VALIDATION as VP

    for pair, m in membership.items():
        c, t = pair.split("|", 1)
        r = VALIDATION_ROLE if m.partition == VP else TRAIN_ROLE
        role_c[r].add(c)
        role_t[r].add(t)

    # with the extension: the allow-list is every role entity, all of them bound
    with_ext = load_dataset(
        W / "a-membership.jsonl", TRAIN_ROLE,
        usable_compounds=role_c[TRAIN_ROLE], usable_sequences=role_t[TRAIN_ROLE],
    )
    # without it: the pre-extension allow-list
    without_ext = load_dataset(
        W / "a-membership.jsonl", TRAIN_ROLE,
        usable_compounds=role_c[TRAIN_ROLE] - recompute_c,
        usable_sequences=role_t[TRAIN_ROLE] - recompute_t,
    )
    fitted_with, fitted_without = set(with_ext.pairs), set(without_ext.pairs)

    recurrent_pairs = set()
    for line in AUDIT.open(encoding="utf-8"):
        rec = json.loads(line)
        if not any(rec["arms"][a]["has_increment"] and rec["arms"][a]["is_scoreable"]
                   for a in ARMS):
            continue
        if stratify(membership.get(rec["pair"])) == RECURRENT:
            recurrent_pairs.add(rec["pair"])

    disagree_with = recurrent_pairs - fitted_with
    disagree_without = recurrent_pairs - fitted_without
    out = {
        "check": "m11g-recurrence-agreement",
        "question": (
            "is every pair the recurrence stratum calls RECURRENT actually in the set the "
            "runner handed to the model-fitting callback?"
        ),
        "recurrent_pairs": len(recurrent_pairs),
        "with_the_extension": {
            "runner_training_pairs": len(fitted_with),
            "recurrent_pairs_not_actually_fitted": len(disagree_with),
            "agrees": not disagree_with,
        },
        "without_the_extension": {
            "runner_training_pairs": len(fitted_without),
            "recurrent_pairs_not_actually_fitted": len(disagree_without),
            "agrees": not disagree_without,
            "examples": sorted(disagree_without)[:5],
        },
        "finding": (
            "the agreement is contingent on complete feature coverage, not structural. "
            "Without the extension the stratum would have called pairs recurrent that the "
            "loader had excluded, overstating exposure to fitting by that many pairs."
        ),
    }
    (G / "recurrence-agreement.json").write_text(
        json.dumps(out, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(out, indent=1, sort_keys=True))


if __name__ == "__main__":
    main()
