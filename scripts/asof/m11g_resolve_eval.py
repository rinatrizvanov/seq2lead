"""Resolve the EVALUATION-role entities too, which the A-role pass did not cover.

The completion step resolved the entities the A roles need. The runner then
refused, because the evaluation cohort is drawn from the B increment and 5,786 of
its compounds and 59 of its targets had no entry in the A-role reuse map.

That refusal is the gate working, and the fix is not a cross-schema shortcut.
These identities come from snapshot B, which **is** 202609 -- the accepted
cache's own schema -- so each one is resolved by looking its content identity up
in that schema and taking the surrogate id the cache is already keyed by. No id
is equated across schemas, because no crossing happens.

An entity already carried by the A-role extension is deliberately NOT added to
the reuse map: two vectors for one content key is the provenance conflict
`load_binding` refuses, and the extension is the more specific source.
"""

from __future__ import annotations

import json
import os
from collections import Counter
from pathlib import Path

import numpy as np

G = Path("data/asof/m11g")
AUDIT = Path("data/asof/m11e/pairs-pki6.jsonl")
ARMS = ("declared_increment", "cross_slot_excluded")


def evaluation_entities() -> tuple[dict[str, tuple[set, set]], set, set]:
    """Per-arm entity sets and their union. The union is what coverage must cover."""
    per_arm = {arm: (set(), set()) for arm in ARMS}
    with AUDIT.open(encoding="utf-8") as fh:
        for line in fh:
            rec = json.loads(line)
            compound, target = rec["pair"].split("|", 1)
            for arm in ARMS:
                if rec["arms"][arm]["has_increment"]:
                    per_arm[arm][0].add(compound)
                    per_arm[arm][1].add(target)
    union_c = set().union(*(v[0] for v in per_arm.values()))
    union_t = set().union(*(v[1] for v in per_arm.values()))
    return per_arm, union_c, union_t


def resolve_202609(keys: set[str], kind: str) -> dict[str, int]:
    """Content identity -> 202609 surrogate id, read-only, in the accepted schema."""
    os.environ.pop("SEQ2LEAD_DB_SCHEMA", None)
    import importlib

    from seq2lead.db import connection as cm

    importlib.reload(cm)
    from seq2lead.db import connect

    column, table = (
        ("inchikey", "compound") if kind == "compound" else ("sequence_sha256", "target")
    )
    # The slot normalisation uppercases the sequence hash; the table stores it
    # lowercase. Look up the stored form, key by the cohort's form.
    wanted = {k.lower(): k for k in keys} if kind == "target" else {k: k for k in keys}
    with connect() as conn:
        conn.execute("SET TRANSACTION READ ONLY")
        rows = conn.execute(
            f"SELECT {column}, id FROM {table} WHERE {column} = ANY(%s)",  # noqa: S608
            (sorted(wanted),),
        ).fetchall()
        conn.rollback()
    return {wanted[str(k)]: int(i) for k, i in rows if str(k) in wanted}


def main() -> None:
    per_arm, union_c, union_t = evaluation_entities()
    for arm, (c, t) in per_arm.items():
        print(f"  {arm:<22} {len(c):>6,} compounds  {len(t):>5,} targets")
    print(f"  {'union over arms':<22} {len(union_c):>6,} compounds  {len(union_t):>5,} targets")

    reuse_c = {k: int(v) for k, v in json.loads((G / "reuse-compounds.json").read_text()).items()}
    reuse_t = {k: int(v) for k, v in json.loads((G / "reuse-targets.json").read_text()).items()}
    ext_c = set(json.loads((G / "recompute-compounds.json").read_text()))
    ext_t = set(json.loads((G / "recompute-targets.json").read_text()))
    ext = json.loads((G / "feature-extension.json").read_text())

    report = {}
    for kind, union, reuse, extension, cache_key in (
        ("compound", union_c, reuse_c, ext_c, "ecfp4"),
        ("target", union_t, reuse_t, ext_t, "esm2"),
    ):
        already = union & reuse.keys()
        in_extension = union & extension
        unmapped = union - reuse.keys() - extension
        print(f"\n{kind}: {len(already):,} already mapped, {len(in_extension):,} in the "
              f"extension, {len(unmapped):,} to resolve")
        resolved = resolve_202609(unmapped, kind) if unmapped else {}
        no_id = sorted(unmapped - resolved.keys())

        # Only an id the accepted cache actually holds is a usable binding.
        data = np.load(ext["accepted_caches"][cache_key]["path"], allow_pickle=False)
        held = {int(v) for v in np.asarray(data["ids"])}
        in_cache = {k: v for k, v in resolved.items() if v in held}
        resolved_not_in_cache = sorted(resolved.keys() - in_cache.keys())
        print(f"  resolved {len(resolved):,} ids; {len(in_cache):,} are in the accepted cache; "
              f"{len(no_id):,} have no 202609 id; {len(resolved_not_in_cache):,} resolved but "
              f"absent from the cache")
        reuse.update(in_cache)
        report[kind] = {
            "evaluation_role_entities": len(union),
            "already_in_the_a_role_reuse_map": len(already),
            "supplied_by_the_a_role_extension": len(in_extension),
            "newly_resolved_against_202609": len(in_cache),
            "no_202609_id": len(no_id),
            "resolved_but_absent_from_the_accepted_cache": len(resolved_not_in_cache),
            "still_unresolvable": sorted(set(no_id) | set(resolved_not_in_cache)),
            "per_arm_entity_counts": {
                arm: len(per_arm[arm][0 if kind == "compound" else 1]) for arm in ARMS
            },
        }

    (G / "reuse-compounds.json").write_text(
        json.dumps(dict(sorted(reuse_c.items())), indent=0, sort_keys=True), encoding="utf-8"
    )
    (G / "reuse-targets.json").write_text(
        json.dumps(dict(sorted(reuse_t.items())), indent=0, sort_keys=True), encoding="utf-8"
    )
    resolution = json.loads((G / "resolution.json").read_text())
    resolution["evaluation_role"] = {
        **report,
        "why": (
            "the A-role pass resolved only what the fitting roles need, and the runner "
            "refused when the evaluation cohort's vectors could not be bound. These "
            "identities come from snapshot B, which IS 202609, so each is resolved in the "
            "accepted cache's own schema. No surrogate id is equated across schemas."
        ),
        "entity_set_rule": (
            "the union over both increment arms of pairs with `has_increment`, a superset "
            "of either arm, so coverage covers every cell that could be reported."
        ),
    }
    resolution["reuse_map_sizes_after_the_evaluation_pass"] = {
        "compounds": len(reuse_c), "targets": len(reuse_t),
    }
    (G / "resolution.json").write_text(
        json.dumps(resolution, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    total_unresolved = Counter(
        {k: len(v["still_unresolvable"]) for k, v in report.items()}
    )
    print(f"\nstill unresolvable: {dict(total_unresolved)}")
    print(f"reuse maps now {len(reuse_c):,} compounds / {len(reuse_t):,} targets")


if __name__ == "__main__":
    main()
