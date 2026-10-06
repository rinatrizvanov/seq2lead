"""How many role targets exceed the 1,022-residue ESM-2 training window?

The length policy is `full` with no truncation, recorded in M7 as a provisional
choice. The report should state the measured count per role, not an arithmetic
guess assembled from two different populations.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

W, G = Path("data/asof/m11f"), Path("data/asof/m11g")
AUDIT = Path("data/asof/m11e/pairs-pki6.jsonl")
WINDOW = 1022
ARMS = ("declared_increment", "cross_slot_excluded")


def main() -> None:
    from seq2lead.asof.partition import VALIDATION as VP
    from seq2lead.asof.partition import load_membership

    membership = load_membership(W / "a-membership.jsonl")
    roles: dict[str, set[str]] = {"train": set(), "validation": set(), "evaluation": set()}
    for pair, m in membership.items():
        _c, t = pair.split("|", 1)
        roles["validation" if m.partition == VP else "train"].add(t)
    with AUDIT.open(encoding="utf-8") as fh:
        for line in fh:
            rec = json.loads(line)
            if any(rec["arms"][a]["has_increment"] for a in ARMS):
                roles["evaluation"].add(rec["pair"].split("|", 1)[1])

    os.environ.pop("SEQ2LEAD_DB_SCHEMA", None)
    from seq2lead.db import connect

    everything = sorted(set().union(*roles.values()))
    wanted = {k.lower(): k for k in everything}
    with connect() as conn:
        conn.execute("SET TRANSACTION READ ONLY")
        rows = conn.execute(
            "SELECT sequence_sha256, length FROM target WHERE sequence_sha256 = ANY(%s)",
            (sorted(wanted),),
        ).fetchall()
        conn.rollback()
    length = {wanted[str(k)]: int(v) for k, v in rows if str(k) in wanted}
    # The 13 recomputed targets have no 202609 row -- that is why they were
    # recomputed. Their sequences are local, so their lengths are measured from
    # the sequence itself rather than left as a hole in the count.
    recomputed = json.loads((G / "recompute-targets.json").read_text())
    from_sequence = {k: len(v) for k, v in recomputed.items() if k not in length}
    length.update(from_sequence)
    print(f"{len(from_sequence)} lengths taken from the local sequence, not 202609")

    out = {
        "check": "m11g-length-window",
        "training_window": WINDOW,
        "length_policy": "full -- no truncation; flagged, not excluded",
        "by_role": {
            role: {
                "targets": len(keys),
                "lengths_resolved": sum(1 for k in keys if k in length),
                "over_the_training_window": sum(1 for k in keys if length.get(k, 0) > WINDOW),
                "max_length": max((length[k] for k in keys if k in length), default=None),
            }
            for role, keys in sorted(roles.items())
        },
        "distinct_over_the_window_across_all_roles": len(
            {k for keys in roles.values() for k in keys if length.get(k, 0) > WINDOW}
        ),
        "distinct_targets_across_all_roles": len(set().union(*roles.values())),
        "lengths_from_the_local_sequence": len(from_sequence),
        "unresolved_lengths": sorted(
            {k for keys in roles.values() for k in keys if k not in length}
        ),
    }
    (G / "length-window.json").write_text(
        json.dumps(out, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(out, indent=1, sort_keys=True))


if __name__ == "__main__":
    main()
