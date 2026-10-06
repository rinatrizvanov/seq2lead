"""The evaluation pair universe, with per-cell membership and strata. No fitting.

One table, written once: every pair carrying an increment in either arm, with
its label per arm, its branch membership and its stratum. The four cells are
then slices of this table rather than four separate traversals, which is what
lets all of them be scored from the same fitted models.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path

from seq2lead.asof.partition import RECURRENT, load_membership, stratify

W, G = Path("data/asof/m11f"), Path("data/asof/m11g")
AUDIT = Path("data/asof/m11e/pairs-pki6.jsonl")
OUT = G / "evaluation-pairs.jsonl"
ARMS = ("declared_increment", "cross_slot_excluded")
BRANCHES = ("screened_primary", "unscreened_sensitivity")

#: M11e's per-arm stratum uses the vocabulary that predates M11f's E3
#: correction: `new_pair` for absent-from-A, and `recurrent` for ANY pair with A
#: evidence. E3 split that second group into three, because an A-present pair
#: whose reading gives no regression target was never supplied to fitting. So the
#: two are compared under this mapping; comparing the raw names reports ~22,000
#: "disagreements" that are a rename and a refinement, not a conflict.
M11E_EQUIVALENT = {
    "new_absent_from_a": "new_pair",
    "recurrent": "recurrent",
    "new_a_present_excluded_from_fitting": "recurrent",
    "new_reserved_for_validation": "recurrent",
}


def main() -> None:
    membership = load_membership(W / "a-membership.jsonl")
    rows, stats = [], Counter()
    with AUDIT.open(encoding="utf-8") as fh:
        for line in fh:
            rec = json.loads(line)
            scoreable = {
                arm: bool(rec["arms"][arm]["has_increment"])
                and bool(rec["arms"][arm]["is_scoreable"])
                for arm in ARMS
            }
            if not any(scoreable.values()):
                continue
            stratum = stratify(membership.get(rec["pair"]))
            row = {
                "pair": rec["pair"],
                "stratum": stratum,
                "participated_in_model_selection": stratum == "new_reserved_for_validation",
                "arms": {
                    arm: {
                        "scoreable": scoreable[arm],
                        "label": rec["arms"][arm]["increment_label"] if scoreable[arm] else None,
                        # `branch_membership` is absent on an arm a pair never
                        # entered, so it is read only where the pair is scoreable.
                        "branches": {
                            br: bool(rec["arms"][arm]["branch_membership"][br])
                            for br in BRANCHES
                        }
                        if scoreable[arm]
                        else dict.fromkeys(BRANCHES, False),
                        "audit_stratum": rec["arms"][arm].get("stratum"),
                    }
                    for arm in ARMS
                },
            }
            rows.append(row)
            stats[f"stratum/{stratum}"] += 1
            for arm in ARMS:
                if not scoreable[arm]:
                    continue
                audit = row["arms"][arm]["audit_stratum"]
                stats[f"{arm}/refined/{audit}->{stratum}"] += 1
                if audit != M11E_EQUIVALENT.get(stratum):
                    stats[f"stratum_conflict/{arm}"] += 1
            for arm in ARMS:
                if scoreable[arm]:
                    stats[f"{arm}/scoreable"] += 1
                    stats[f"{arm}/{row['arms'][arm]['label']}"] += 1

    rows.sort(key=lambda r: r["pair"])
    OUT.write_text(
        "".join(json.dumps(r, sort_keys=True) + "\n" for r in rows), encoding="utf-8"
    )
    digest = hashlib.sha256(OUT.read_bytes()).hexdigest()
    summary = {
        "artifact": "m11h-evaluation-pairs-v1",
        "pairs": len(rows),
        "sha256": digest,
        "counts": dict(sorted(stats.items())),
        "cells": [f"{a}/{b}" for a in ARMS for b in BRANCHES],
        "note": (
            "the four cells are slices of this one table, so every cell is scored from "
            "the same fitted models. `participated_in_model_selection` marks the "
            "validation-reserved pairs, which are reported but never folded into the "
            "new-to-fitting headline."
        ),
        "recurrent_pairs": stats[f"stratum/{RECURRENT}"],
        "stratum_conflicts": {
            k: v for k, v in sorted(stats.items()) if k.startswith("stratum_conflict/")
        }
        or (
            "none. Under the M11e->M11f mapping every scoreable pair's audit stratum "
            "agrees with the membership-derived one. The strata used here are the "
            "CORRECTED ones; M11e's `recurrent` is a superset that E3 split in three."
        ),
        "how_m11e_strata_refine": {
            k.split("/", 2)[2]: v
            for k, v in sorted(stats.items())
            if "/refined/" in k and k.startswith("declared_increment/")
        },
    }
    (G / "evaluation-pairs-summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, indent=1, sort_keys=True))


if __name__ == "__main__":
    main()
