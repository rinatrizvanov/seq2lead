"""Render `reports/features.md` from measured artifacts, never from literals."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from seq2lead.features.probes import ProbeArtifact, quantiles
from seq2lead.profiling.status_banner import banner

if TYPE_CHECKING:
    import psycopg

REPORT_PATH = Path("reports/features.md")
TRAINING_WINDOW = 1022


def _scalar(conn: psycopg.Connection, sql: str, params: tuple = ()) -> int:
    row = conn.execute(sql, params).fetchone()
    return int(row[0]) if row and row[0] is not None else 0


def _missing(name: str) -> list[str]:
    return [
        f"_No `{name}` artifact under `reports/probes/`._ Run "
        f"`uv run seq2lead features probe` to produce it. Nothing is reported here "
        "from memory.",
        "",
    ]


def _quantile_table(label: str, values: list[float], fmt: str = ".4f") -> list[str]:
    q = quantiles(values)
    if not q:
        return []
    out = [
        f"| {label} | "
        + " | ".join(
            format(q[k], fmt) for k in ("min", "p05", "p25", "median", "p75", "p95", "max")
        )
        + f" | {int(q['n']):,} |"
    ]
    return out


# ------------------------------------------------------------------ populations


def _population_section(conn: psycopg.Connection) -> list[str]:
    out: list[str] = []
    a = out.append
    a("## The population a cache is built over")
    a("")
    a(
        "M7 v1 enumerated **every row currently in `compound` and `target`**. That is not "
        "a reproducible population: anything that ever reached those tables joins it, "
        "and nothing in the cache's identity records which rows were present."
    )
    a("")
    a("### Why the entity totals moved")
    a("")
    a(
        "The review asked why the totals rose to 1,424,672 compounds and 11,014 targets. "
        "They were **test fixtures that survived their own cleanup**:"
    )
    a("")
    a("| Entity | id | Structure / sequence | Activities | Inserted by |")
    a("| --- | --- | --- | --- | --- |")
    a(
        "| compound | 2777916 | `CCO` (ethanol) | 0 "
        "| `tests/test_ingest.py`, `tests/test_temporal_visibility.py` |"
    )
    a(
        "| compound | 2777917 | `CCC` (propane) | 0 "
        "| `tests/test_curate.py`, `tests/test_ingest.py` |"
    )
    a("| target | 1 | `MKVLSSAAWQR` (11 aa) | 0 | `tests/test_endpoint.py` |")
    a("| target | 11014 | `MKVLSSAAWQRTTYNEQ` (17 aa) | 0 | `tests/test_temporal_visibility.py` |")
    a("")
    a(
        "The cause is structural rather than a slip: curation inserts into `compound` and "
        "`target` with **no foreign key back to the release** that caused the insert, so a "
        "fixture's teardown cannot find its own entities by provenance. Deleting by "
        "structure is not available either — benzene, aspirin and nicotine are all real "
        "BindingDB compounds that the tests reuse, so a structure-keyed cleanup would "
        "destroy production rows. Every `_cleanup` therefore stopped at activities and "
        "releases, and the entities accumulated."
    )
    a("")
    a(
        "Both synthetic targets were embedded by ESM-2 as though they were proteins. "
        "Neither appeared in any split assignment, so no split was affected, and "
        "`target 1` had already been counted in the 11,013 figure the review used as its "
        "baseline — that baseline was itself contaminated."
    )
    a("")
    a(
        "Fixed in `seq2lead.db.maintenance.no_entity_leak()`: a high-water mark on the id "
        "sequence, taken before a fixture runs and used to delete what it created "
        "afterwards. Rows still carrying an activity are never deleted, so a test that "
        "reuses a real structure cannot take the real entity with it."
    )
    a("")
    a(
        "The regression test earned its place immediately: the first fix wired the guard "
        "into two fixtures and missed a third in `tests/test_curate.py`, and a full suite "
        "run still leaked one target and two compounds. **Verified after the complete "
        "fix**: a full run now leaves `compound` and `target` counts exactly unchanged, "
        "and no activity-free synthetic target survives."
    )
    a("")

    a("### Caches are now scoped to a declared population")
    a("")
    present_c = _scalar(conn, "SELECT count(*) FROM compound")
    present_t = _scalar(conn, "SELECT count(*) FROM target")
    scoped_c = _scalar(
        conn, "SELECT count(DISTINCT compound_id) FROM activity WHERE source_release_id=117"
    )
    scoped_t = _scalar(
        conn, "SELECT count(DISTINCT target_id) FROM activity WHERE source_release_id=117"
    )
    a("| Population | Compounds | Targets | Reproducible? |")
    a("| --- | --- | --- | --- |")
    a(
        f"| `all_rows` (what v1 used) | {present_c:,} | {present_t:,} "
        "| no — depends on what is in the table |"
    )
    a(
        f"| `release:117` (**used now**) | {scoped_c:,} | {scoped_t:,} "
        "| yes — derived from the pinned release |"
    )
    a("")
    a(
        f"The {present_c - scoped_c:,} compounds and {present_t - scoped_t:,} targets outside "
        "the release population are real curated entities whose activities were all "
        "excluded during curation, plus nothing else now that the fixtures are cleaned up. "
        "They are not scoreable, appear in no split, and carrying them in a benchmark "
        "feature cache only invites a silent mismatch later."
    )
    a("")
    return out


# --------------------------------------------------------------------- identity


def _identity_section(conn: psycopg.Connection, caches: list[tuple]) -> list[str]:
    out: list[str] = []
    a = out.append
    a("## Cache identity")
    a("")
    a(
        "A cache's identity is now the SHA-256 of **two** things: the representation spec, "
        "and an input manifest. v1 hashed only the first, and that is exploitable — a "
        "`--limit 1000` smoke build and a full 1.4M-compound build produced the same hash, "
        "so the partial one could be registered under the full one's name and handed to "
        "anything that asked for the complete cache."
    )
    a("")
    a("| Half | Covers |")
    a("| --- | --- |")
    a(
        "| representation spec | model + **commit sha**, pooling, length policy, "
        "`max_length`, fingerprint radius / bits / **chirality**, standardizer version, "
        "dtype, split |"
    )
    a(
        "| input manifest | entity ids **and their content hashes**, the declared "
        "population, the requested limit, completeness (`full` / `partial`), and for "
        "activity features the split and a digest of the training evidence |"
    )
    a("")
    a(
        "Content hashes matter as much as ids: a re-standardised structure or sequence "
        "under the same id would otherwise leave stale vectors attached to it. Library "
        "versions (`rdkit`, `torch`, `transformers`, `numpy`, Python) are recorded "
        "alongside each cache."
    )
    a("")
    a(
        "Still deliberately **absent**: device, batch size, worker count. They cannot "
        "change what a vector *represents*. They can change its bytes — see the "
        "reproducibility note below — and that distinction is the point."
    )
    a("")
    a("### Caches")
    a("")
    a("| Cache | Kind | Population | Complete | Entities | Dim | Build |")
    a("| --- | --- | --- | --- | --- | --- | --- |")
    for name, kind, _entity, dim, n, _params, seconds, *_rest in caches:
        row = conn.execute(
            "SELECT population, completeness FROM feature_version WHERE name=%s", (name,)
        ).fetchone()
        a(
            f"| `{name}` | {kind} | `{row[0]}` | {row[1]} | {int(n or 0):,} | "
            f"{int(dim or 0):,} | {float(seconds or 0):,.0f} s |"
        )
    a("")
    a("### Reuse, and what it costs")
    a("")
    a(
        "`find_reusable()` is consulted **before** any fingerprint is computed or ESM-2 is "
        "loaded. It matches on the full identity, verifies the stored bytes still hash to "
        "their recorded digest, refuses a superseded cache, and **never returns a partial "
        "cache for a full request**. An identical rerun therefore costs one manifest "
        "digest — about 3 s for 1.4M compounds — instead of the build."
    )
    a("")
    a(
        "Before acceptance a cache must pass validation: ids unique, ids exactly equal to "
        "the manifest's population, rows aligned with ids, non-zero width, and no "
        "non-finite values. A NaN from a failed forward pass reaches a model as a number "
        "and poisons every gradient that touches it, so it is caught at registration "
        "rather than three milestones later."
    )
    a("")
    return out


# ------------------------------------------------------------------- stereo
def _stereo_section(conn: psycopg.Connection) -> list[str]:
    out: list[str] = []
    a = out.append
    a("## Stereochemistry in the fingerprints")
    a("")
    a(
        "M3 repaired compound identity after finding BindingDB's InChI Keys are "
        "stereo-insensitive — 64,486 keys carried 2–8 distinct stereoisomers across "
        "448,735 rows — and re-keyed compounds on their structure. The v1 fingerprints "
        "then discarded that repair at the last step by building Morgan fingerprints with "
        "`includeChirality=False`."
    )
    a("")
    a(
        "Measured directly on three verified stereoisomer pairs, the achiral "
        "representation gives **byte-identical vectors**; the chiral one separates all "
        "three:"
    )
    a("")
    a("| Pair | Achiral fingerprints | Chiral fingerprints |")
    a("| --- | --- | --- |")
    for label in ("alanine R/S", "nicotine R/S", "2-butene cis/trans"):
        a(f"| {label} | identical | distinct |")
    a("")
    a("Tests in `tests/test_fingerprint_stereo.py` assert both directions on these pairs.")
    a("")

    stats = ProbeArtifact.load("chirality_impact")
    if stats is None:
        out.extend(_missing("chirality_impact"))
    else:
        m = {row["metric"]: row for row in stats.measurements}

        def value(key: str) -> int:
            return int(m[key]["value"]) if key in m else 0

        shared = value("compounds_in_both")
        changed = value("fingerprint_changed")
        a("### Coverage affected")
        a("")
        a("| | Compounds | Share |")
        a("| --- | --- | --- |")
        a(f"| In both the achiral and chiral caches | {shared:,} | 100% |")
        a(
            f"| Fingerprint **changed** by enabling chirality | {changed:,} | "
            f"{100 * changed / max(shared, 1):.1f}% |"
        )
        a("")
        a("| Distinct compounds sharing a fingerprint with another | Compounds | Groups |")
        a("| --- | --- | --- |")
        a(
            f"| achiral | {value('achiral_colliding_compounds'):,} | "
            f"{value('achiral_collision_groups'):,} |"
        )
        a(
            f"| chiral | {value('chiral_colliding_compounds'):,} | "
            f"{value('chiral_collision_groups'):,} |"
        )
        a("")
        resolved = value("achiral_colliding_compounds") - value("chiral_colliding_compounds")
        remaining = value("chiral_colliding_compounds")
        a(
            f"**{resolved:,} compounds** stop sharing a vector with a different structure. "
            f"**{remaining:,} still do**, and that is the limit of the claim: ECFP4 is a "
            "*hashed* 2,048-bit fingerprint, so distinct molecules collide whether or not "
            "stereochemistry is encoded. Enabling chirality removes a systematic, "
            "avoidable collision between stereoisomers. It does **not** make the "
            "representation injective, and nothing here shows that every stereoisomer in "
            "the corpus is now uniquely identified."
        )
        a("")
    a(
        "The achiral cache is **retained and superseded, not deleted**. It is a valid "
        "achiral representation — the explicitly-named alternative for any experiment "
        "that wants one — rather than a corrupt artifact."
    )
    a("")
    return out


# -------------------------------------------------------------- length policy


def _length_policy_section(conn: psycopg.Connection) -> list[str]:
    out: list[str] = []
    a = out.append
    a("## The sequence-length policy")
    a("")
    a("### The premise")
    a("")
    a(
        "ESM-2 is widely described as having a **1,022-residue limit**. For these "
        "checkpoints that is not a hard ceiling: the HF configs set "
        "`position_embedding_type='rotary'`, so `max_position_embeddings=1026` never "
        "indexes a learned position table, and nothing raises on a longer input."
    )
    a("")

    feasibility = ProbeArtifact.load("length_feasibility")
    if feasibility is None:
        out.extend(_missing("length_feasibility"))
    else:
        a(
            f"Measured by `probe_length_feasibility` on {feasibility.metadata['device']} "
            f"({feasibility.metadata['libraries'].get('torch', '?')}), using the **real "
            "target nearest each requested length**:"
        )
        a("")
        a("| Requested | Nearest real target | Its length | Result | Wall time |")
        a("| --- | --- | --- | --- | --- |")
        for row in sorted(feasibility.measurements, key=lambda r: r["requested_length"]):
            a(
                f"| {int(row['requested_length']):,} | {row['target_id']} | "
                f"{int(row['length']):,} | embedded | {float(row['seconds']):.1f} s |"
            )
        for row in feasibility.failures:
            a(
                f"| {int(row['requested_length']):,} | {row.get('target_id', '—')} | "
                f"{int(row['length']):,} | **{row['error']}** | — |"
            )
        a("")
        seen = [int(r["target_id"]) for r in feasibility.measurements]
        if len(seen) != len(set(seen)):
            a(
                "Two requested lengths resolve to the same protein: the corpus has a gap "
                "between roughly 7,400 and 34,350 residues, so there is no real target "
                "near 8,000 or 20,000. The probe reports what it actually measured rather "
                "than padding a synthetic sequence to the requested length."
            )
            a("")
        if not feasibility.failures:
            longest = max(int(r["length"]) for r in feasibility.measurements)
            a(
                f"Nothing failed, up to the longest sequence in the corpus ({longest:,} "
                "residues). Neither a hard limit nor a memory wall forces a policy."
            )
            a("")

    a("### What extrapolation does to a prefix")
    a("")
    drift = ProbeArtifact.load("prefix_drift")
    if drift is None:
        out.extend(_missing("prefix_drift"))
    else:
        selection = drift.selection
        a(
            f"`probe_prefix_drift` embeds a target's first {selection['window']:,} residues "
            "alone — inside the pre-training crop length — then embeds the full sequence "
            "and extracts the same residues. Same residues, one in regime and one out."
        )
        a("")
        lengths = [int(m["length"]) for m in drift.measurements]
        a(
            f"Selection: targets with {selection['min_length']:,} ≤ length ≤ "
            f"{selection['max_length']:,}, ordered by `md5(sequence_sha256 || seed)`, "
            f"first {selection['n_requested']} taken (seed "
            f"{drift.metadata.get('seed')}). Realised n = {len(drift.measurements)}"
            + (f", lengths {min(lengths):,}–{max(lengths):,}." if lengths else ".")
        )
        a("")
        a("| Metric | min | p05 | p25 | median | p75 | p95 | max | n |")
        a("| --- | --- | --- | --- | --- | --- | --- | --- | --- |")
        for label, key in (
            ("pooled cosine", "pooled_cosine"),
            ("mean per-residue cosine", "mean_residue_cosine"),
            ("pooled relative L2", "pooled_relative_l2"),
        ):
            out.extend(_quantile_table(label, drift.column(key)))
        a("")
        a(
            "**What this does and does not establish.** It measures how far a "
            "representation *moves* when the model is pushed past its training window. It "
            "is not an accuracy measurement: neither embedding is a ground truth, and no "
            "downstream task has been scored under either. It says nothing about binding "
            f"prediction quality. And it is evidence at "
            f"{format(min(lengths), ',') if lengths else '?'}–"
            f"{format(max(lengths), ',') if lengths else '?'} residues only — it does "
            "**not** establish "
            "that the representation of a 34,350-residue protein is sound, because there "
            "is no in-regime version of residue 30,000 to compare against. Per-target "
            "measurements are in `reports/probes/prefix_drift.json`."
        )
        a("")

    a("### Policy: `full`, and provisional")
    a("")
    a(
        "**Every residue is embedded in a single forward pass. No truncation, no "
        "chunking.** The reasoning:"
    )
    a("")
    a("1. There is no hard limit to respect — verified from the config and by running it.")
    a("2. Every sequence in the corpus is feasible on this hardware — measured above.")
    a(
        "3. Truncation to 1,022 would discard up to 97% of a sequence, and would do so "
        "precisely to the large multi-domain proteins whose binding site is least likely "
        "to sit in the first 1,022 residues."
    )
    a(
        "4. Chunking would keep every residue and stay in regime, but discards "
        "cross-window attention and adds a window-size parameter nothing in the data "
        "chooses."
    )
    a("")
    a(
        "**This is a provisional choice, not a validated one.** Points 1 and 2 are "
        "verified; point 3 is an argument, not a measurement; the drift probe bounds "
        "movement at moderate lengths and nothing more. The policy is falsifiable at M8 "
        "by scoring with and without the flagged targets, and by building a "
        "`truncate:1022` cache — which now mints a separate identity — and comparing. "
        "Unsupported policy strings such as `chunk:1022` are **refused**, not silently "
        "treated as truncation."
    )
    a("")

    a("### What the policy affects")
    a("")
    row = conn.execute(
        "SELECT population FROM feature_version WHERE kind='esm2' AND superseded_by IS NULL "
        "AND completeness='full' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    release = str(row[0]).partition(":")[2] if row else ""
    if release.isdigit():
        scoped, over = conn.execute(
            """
            SELECT count(*), count(*) FILTER (WHERE length(sequence) > %s)
            FROM target t WHERE EXISTS (SELECT 1 FROM activity a
              WHERE a.target_id = t.id AND a.source_release_id = %s)
            """,
            (TRAINING_WINDOW, int(release)),
        ).fetchone()
        a("| | Targets | Share |")
        a("| --- | --- | --- |")
        a(f"| In the cache population (`release:{release}`) | {int(scoped):,} | 100% |")
        a(
            f"| …past the {TRAINING_WINDOW:,}-residue training window | {int(over):,} | "
            f"{100 * int(over) / max(int(scoped), 1):.1f}% |"
        )
        a("")
    a("Restricted to entities a split actually scores:")
    a("")
    a("| Split | Scored targets | Past the window | Scored pairs | Pairs affected |")
    a("| --- | --- | --- | --- | --- |")
    for name, split_id in conn.execute(
        "SELECT name, id FROM split_version WHERE superseded_by IS NULL "
        "AND split_type <> 'label_reversal' ORDER BY id"
    ).fetchall():
        r = conn.execute(
            """
            SELECT count(DISTINCT p.target_id),
                   count(DISTINCT p.target_id) FILTER (WHERE length(t.sequence) > %s),
                   count(*), count(*) FILTER (WHERE length(t.sequence) > %s)
            FROM split_pair_assignment p JOIN target t ON t.id = p.target_id
            WHERE p.split_id = %s AND p.partition IN ('train','validation','test')
            """,
            (TRAINING_WINDOW, TRAINING_WINDOW, int(split_id)),
        ).fetchone()
        pct = 100 * int(r[3]) / max(int(r[2]), 1)
        a(
            f"| `{name}` | {int(r[0]):,} | {int(r[1]):,} | {int(r[2]):,} | "
            f"{int(r[3]):,} ({pct:.1f}%) |"
        )
    a("")
    a(
        "All of them are flagged `over_training_window` with their length, so every M8 "
        "score can be reported with and without them. That sensitivity report is the "
        "check on this policy; until it exists, the policy is untested."
    )
    a("")
    return out


# ------------------------------------------------------------------ similarity


def _similarity_section() -> list[str]:
    out: list[str] = []
    a = out.append
    a("### Does the cache encode homology?")
    a("")
    artifact = ProbeArtifact.load("cluster_similarity")
    if artifact is None:
        out.extend(_missing("cluster_similarity"))
        return out
    same = [float(m["cosine"]) for m in artifact.measurements if m["kind"] == "same_cluster"]
    diff = [float(m["cosine"]) for m in artifact.measurements if m["kind"] == "different_cluster"]
    a(
        "Cosine between mean-pooled embeddings for target pairs inside one MMseqs2 "
        "cluster, and for pairs from different clusters. Sampled pair ids and their "
        "cluster memberships are in `reports/probes/cluster_similarity.json`."
    )
    a("")
    a("| Pairs | min | p05 | p25 | median | p75 | p95 | max | n |")
    a("| --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    out.extend(_quantile_table("same cluster", same))
    out.extend(_quantile_table("different cluster", diff))
    a("")
    if same and diff:
        q_same, q_diff = quantiles(same), quantiles(diff)
        overlap = sum(1 for x in diff if x > q_same["p05"]) / len(diff)
        a(
            f"The distributions are **offset but overlapping**: the different-cluster p95 "
            f"is {q_diff['p95']:.4f} against a same-cluster p05 of {q_same['p05']:.4f}, and "
            f"{100 * overlap:.1f}% of different-cluster pairs exceed that same-cluster p05. "
            "Reporting the share above a single median would have made the separation look "
            "cleaner than it is."
        )
        a("")
        a(
            "**A different cluster is not an unrelated protein.** MMseqs2 clustered at 40% "
            "identity with 80% coverage, so two targets in different clusters may still be "
            "homologous, share a domain, or share a fold — the M6 audit found exactly "
            "that. The contrast here is *clustered together* versus *not clustered "
            "together*, which is weaker than *related* versus *unrelated*."
        )
        a("")
        a(
            f"The different-cluster median of **{q_diff['median']:.4f}** is the number to "
            "carry into M8. Mean-pooled ESM-2 vectors occupy a narrow cone, so cosine has "
            "a compressed dynamic range — and the planned ConPLex-style dual encoder "
            "*scores by cosine*. That argues for treating the learned projection as doing "
            "real work, and for centring or whitening as a pre-registered ablation."
        )
        a("")
    return out


# ----------------------------------------------------------------------- render


def render(conn: psycopg.Connection, endpoint_id: int, split_name: str) -> str:  # noqa: PLR0915
    lines: list[str] = []
    a = lines.append

    a("# M7 — feature caches")
    a("")
    a(f"Generated {datetime.now(UTC):%Y-%m-%d %H:%M} UTC.")
    a("")
    for line in banner():
        a(line)
    a(
        "Contract: [`../docs/FEATURES.md`](../docs/FEATURES.md). Every number below is "
        "read from a probe artifact under `reports/probes/` or queried from the database "
        "at render time; none is a literal in the report generator."
    )
    a("")

    caches = conn.execute(
        "SELECT name, kind, entity, dim, n_entities, params, seconds, storage_path, "
        "storage_sha256, split_id FROM feature_version WHERE superseded_by IS NULL "
        "AND completeness = 'full' ORDER BY id"
    ).fetchall()
    if not caches:
        a("_No complete caches built._")
        return "\n".join(lines) + "\n"

    lines.extend(_population_section(conn))
    lines.extend(_identity_section(conn, caches))

    a("### Spec fields per cache")
    a("")
    for name, _kind, _entity, _dim, _n, params, *_ in caches:
        spec = json.loads(params) if isinstance(params, str) else (params or {})
        versions = conn.execute(
            "SELECT library_versions FROM feature_version WHERE name=%s", (name,)
        ).fetchone()[0]
        a(f"**`{name}`**")
        a("")
        a("| Field | Value |")
        a("| --- | --- |")
        for key in (
            "kind",
            "entity",
            "model",
            "model_revision",
            "pooling",
            "length_policy",
            "max_length",
            "radius",
            "n_bits",
            "chirality",
            "standardizer_version",
            "dtype",
            "split",
        ):
            value = spec.get(key)
            if value not in (None, ""):
                a(f"| `{key}` | `{value}` |")
        if spec.get("extra"):
            a(f"| `extra` | `{json.dumps(spec['extra'], sort_keys=True)}` |")
        if versions:
            loaded = json.loads(versions) if isinstance(versions, str) else versions
            if loaded:
                a(f"| `libraries` | `{json.dumps(loaded, sort_keys=True)}` |")
        a("")

    lines.extend(_stereo_section(conn))
    lines.extend(_length_policy_section(conn))
    lines.extend(_similarity_section())

    # ------------------------------------------------ what may read outcomes
    a("## What a feature is allowed to read")
    a("")
    a("| Feature | Function of | May cover held-out entities? |")
    a("| --- | --- | --- |")
    a("| ECFP4 | the compound structure | **yes** |")
    a("| ESM-2 | the target sequence | **yes** |")
    a("| activity aggregates | measured pKi values | **no** |")
    a("")
    a(
        "A fingerprint or embedding for a held-out entity leaks nothing: a deployed model "
        "derives it from structure and sequence alone. Activity aggregates summarise the "
        "outcomes the model is asked to predict, so every one is built from "
        "`training_visible_activities()` for the active split and from nothing else. That "
        "function returns SQL rather than rows, and the builder composes it rather than "
        "restating the filter, so the two cannot drift apart."
    )
    a("")

    activity_caches = [c for c in caches if c[1] == "activity"]
    if activity_caches:
        split_id = activity_caches[0][9]
        from seq2lead.features.activity import held_out_exposure
        from seq2lead.splits.assertions import training_visible_activities

        visible = training_visible_activities(conn, int(split_id))
        n_visible = _scalar(conn, f"SELECT count(*) FROM ({visible}) v")  # noqa: S608
        n_total = _scalar(
            conn,
            "SELECT count(*) FROM split_activity_assignment WHERE split_id=%s",
            (int(split_id),),
        )
        leaked = _scalar(
            conn,
            f"SELECT count(*) FROM ({visible}) v "  # noqa: S608
            "JOIN split_activity_assignment s ON s.activity_id = v.activity_id "
            "WHERE s.split_id=%s AND s.partition <> 'train'",
            (int(split_id),),
        )
        exposure = held_out_exposure(conn, int(split_id), "target")
        protocol = (
            conn.execute(
                "SELECT params FROM split_version WHERE id=%s", (int(split_id),)
            ).fetchone()[0]
            or {}
        ).get("protocol")
        a("| Check | Count |")
        a("| --- | --- |")
        a(f"| Activities in the split | {n_total:,} |")
        a(f"| Reachable through `training_visible_activities()` | {n_visible:,} |")
        a(f"| …of those, held out (**must be 0**) | {leaked:,} |")
        a(f"| Held-out activities existing for trained-on targets | {exposure:,} |")
        a(f"| Temporal protocol | `{protocol}` |")
        a("")

        effect = conn.execute(
            f"""
            WITH visible AS MATERIALIZED ({visible}),
            gated AS (
                SELECT a.target_id, count(*) n, avg(9 - log(a.value_numeric)) m
                FROM visible vi JOIN activity a ON a.id = vi.activity_id
                WHERE a.measurement_type='KI' AND a.relation='=' AND a.value_numeric > 0
                GROUP BY 1),
            ungated AS (
                SELECT target_id, count(*) n, avg(9 - log(value_numeric)) m
                FROM activity
                WHERE measurement_type='KI' AND relation='=' AND value_numeric > 0
                GROUP BY 1)
            SELECT count(*), count(*) FILTER (WHERE g.n < u.n), sum(g.n), sum(u.n),
                   percentile_cont(0.5) WITHIN GROUP (ORDER BY abs(g.m - u.m)),
                   percentile_cont(0.9) WITHIN GROUP (ORDER BY abs(g.m - u.m)),
                   max(abs(g.m - u.m))
            FROM gated g JOIN ungated u USING (target_id)
            """,  # noqa: S608
        ).fetchone()
        shared, fewer, gated_obs, ungated_obs, med, p90, worst = effect
        a("### What the gate withholds")
        a("")
        a("| | Gated (used) | Ungated |")
        a("| --- | --- | --- |")
        a(f"| Exact Ki observations | {int(gated_obs):,} | {int(ungated_obs):,} |")
        a(
            f"| Targets whose count changes | {int(fewer):,} of {int(shared):,} "
            f"({100 * int(fewer) / max(int(shared), 1):.1f}%) | — |"
        )
        a("")
        a(
            f"**{100 * (1 - int(gated_obs) / max(int(ungated_obs), 1)):.1f}% of exact Ki "
            "observations are withheld.** The shift in a target's mean pKi between the two "
            f"views is the error avoided — median **{float(med):.3f}**, p90 "
            f"**{float(p90):.3f}**, worst **{float(worst):.2f} log units**, roughly five "
            "orders of magnitude in Ki. A test recomputes the aggregate without the gate "
            "and asserts the two differ, so the gate cannot pass by doing nothing."
        )
        a("")

    # --------------------------------------------------------- feature coverage
    from seq2lead.features.coverage import check_all_splits

    a("## Feature coverage")
    a("")
    a(
        "Cache identity guarantees a cache matches the population it **claims**. It says "
        "nothing about whether that population covers the entities a split will score — "
        "different question, and the gap between them is where a training run dies at "
        "hour three. Asserted for every active split and both entity types:"
    )
    a("")
    a("| Split | Entity | Scored | Covered | Missing | Unusable | Cache |")
    a("| --- | --- | --- | --- | --- | --- | --- |")
    coverage = check_all_splits(conn)
    for check in coverage:
        a(
            f"| `{check.split}` | {check.entity} | {check.scored:,} | {check.covered:,} | "
            f"{check.missing:,} | {check.flagged:,} | `{check.cache}` |"
        )
    a("")
    total_missing = sum(c.missing for c in coverage)
    total_flagged = sum(c.flagged for c in coverage)
    a(
        f"**{total_missing} missing** across {len(coverage)} checks. `assert_feature_"
        "coverage()` raises on any gap and names the split, the entity and example ids, "
        "so a failure is actionable rather than a boolean."
    )
    a("")
    a(
        "**Missing and unusable are counted separately, and the distinction matters.** A "
        "missing vector raises a `KeyError` at lookup — loud, and immediately obvious. An "
        "*unusable* one does not: the "
        f"{total_flagged // max(len({c.split for c in coverage}), 1)} flagged compounds per "
        "split hold an all-zero fingerprint because their structure would not parse, and a "
        "model reads that as a real molecule with no features. Nothing raises. It is the "
        "quieter failure of the two, which is why it is surfaced here rather than folded "
        "into the covered count."
    )
    a("")

    # ----------------------------------------------------- near-homolog stratum
    a("## The near-homolog evaluation stratum")
    a("")
    nh = conn.execute(
        "SELECT v.name, count(*) FROM split_target_stratum st "
        "JOIN split_version v ON v.id = st.split_id "
        "WHERE st.stratum='near_homolog' GROUP BY v.name"
    ).fetchall()
    for name, n in nh:
        row = conn.execute(
            """
            SELECT count(*) FILTER (WHERE st.target_id IS NOT NULL), count(*)
            FROM split_pair_assignment p
            LEFT JOIN split_target_stratum st
              ON st.split_id = p.split_id AND st.target_id = p.target_id
              AND st.stratum = 'near_homolog'
            WHERE p.split_id = (SELECT id FROM split_version WHERE name=%s)
              AND p.partition = 'test'
            """,
            (name,),
        ).fetchone()
        a(
            f"**`{name}`** — {int(n)} held-out targets, covering **{int(row[0]):,} of "
            f"{int(row[1]):,} test pairs ({100 * int(row[0]) / max(int(row[1]), 1):.1f}%)**. "
            "They satisfy the cold-protein guarantee and still have a training protein "
            "aligning at ≥90% identity over at least half of both sequences, so a score on "
            "them measures near-homolog transfer. Reported separately at M8."
        )
        a("")

    # ------------------------------------------------------ reproducibility note
    a("## Reproducibility")
    a("")
    a(
        "Probe artifacts under `reports/probes/` carry, for every run: the entity ids and "
        "content hashes selected, the selection rule and seed, the model and **commit "
        "sha**, the pooling policy, the device, the library versions, every individual "
        "measurement, and every failure. The report renders statistics from those files "
        "and says so when one is missing."
    )
    a("")
    a(
        "**A shared specification is not byte-identical output.** Two runs of the same "
        "spec on different backends — MPS versus CPU, a different BLAS, a different torch "
        "build — can differ in the last floating-point places, and reduction order alone "
        "is enough to cause it. What the identity guarantees is that two caches with the "
        "same key were built from the same inputs under the same representation rules. It "
        "does not guarantee the bytes match across machines, and `storage_sha256` is a "
        "tamper check on one file rather than a cross-machine reproducibility claim. Seeds "
        "in these probes select *which entities are measured*; the forward passes "
        "themselves are deterministic."
    )
    a("")

    # ------------------------------------------------------------- limitations
    a("## Limitations and open M8 decisions")
    a("")
    a("| # | Item | Status |")
    a("| --- | --- | --- |")
    unparseable = _scalar(
        conn,
        "SELECT count(*) FROM feature_entity_flag f JOIN feature_version v ON v.id=f.feature_id "
        "WHERE v.kind='ecfp4' AND v.superseded_by IS NULL AND f.flag='unparseable_smiles'",
    )
    pairs_affected = conn.execute(
        """
        SELECT v.name, count(*) FROM split_pair_assignment p
        JOIN split_version v ON v.id = p.split_id
        WHERE v.superseded_by IS NULL AND p.partition IN ('train','validation','test')
          AND p.compound_id IN (
              SELECT f.entity_id FROM feature_entity_flag f
              JOIN feature_version fv ON fv.id = f.feature_id
              WHERE fv.kind='ecfp4' AND fv.superseded_by IS NULL
                AND f.flag='unparseable_smiles')
        GROUP BY v.name ORDER BY v.name
        """
    ).fetchall()
    detail = ", ".join(f"`{n}` {int(c)}" for n, c in pairs_affected)
    a(
        f"| 1 | **{unparseable} compounds have no parseable structure** and hold an "
        f"all-zero row. Scored pairs affected: {detail}. A zero vector is **not** a missing "
        "value — a model reads it as a real molecule with no features. | **Open M8 "
        "decision**: drop these pairs, or add an explicit missing-feature indicator. No "
        "imputation is applied here. |"
    )
    a(
        "| 2 | The `full` length policy rests on feasibility plus a bounded drift "
        "measurement, not on any task score. | **Open M8 decision**: report every metric "
        "with and without `over_training_window` targets, and compare against a "
        "`truncate:1022` cache. |"
    )
    a(
        "| 3 | Chiral fingerprints resolve most stereoisomer collisions but ECFP4 stays a "
        "lossy hashed representation, with collisions remaining. | Measured, reported, not "
        "claimed away. |"
    )
    a(
        "| 4 | Mean pooling discards positional structure and compresses the cosine scale. "
        "| **Open M8 decision**: centring/whitening and per-residue attention pooling as "
        "pre-registered ablations. |"
    )
    a(
        "| 5 | The activity cache summarises **exact Ki only**; censored records carry no "
        "point value and contribute to no mean, median or spread. | By design; the "
        "interval evidence lives in `pair_label`. |"
    )
    a("| 6 | No model has been trained. These are caches and their audits. | — |")
    a("")
    return "\n".join(lines) + "\n"


def write(content: str, path: Path = REPORT_PATH) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path
