"""Render `reports/curation.md` for the M3 curated layer."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import psycopg

REPORT_PATH = Path("reports/curation.md")


def _standardizer_version() -> str:
    from seq2lead.curate.standardizer import STANDARDIZER_VERSION

    return STANDARDIZER_VERSION


# Measured before the join was implemented, on the pinned releases, scoped by
# source_release_id so the shared raw_record table could not cross artifacts.
JOIN_DIAGNOSTICS = {
    "measurement_rows": 3_237_046,
    "with_reactant_id": 3_237_046,
    "distinct_reactant_ids": 3_237_046,
    "rsid_rows": 3_179_005,
    "rsid_distinct_keys": 3_179_005,
    "assay_rows": 224_430,
    "assay_distinct_keys": 224_430,
    "hop1_matched": 3_178_980,
    "hop1_multi": 0,
    "hop2_reached": 3_178_980,
    "hop2_multi": 0,
    "left_join_rows": 3_237_046,
    "rsid_orphans": 25,
    "assay_unreferenced": 0,
    "dangling_eaids": 0,
}


def _rows(conn: psycopg.Connection, sql: str, params: tuple = ()) -> list[tuple]:
    return conn.execute(sql, params).fetchall()


def _scalar(conn: psycopg.Connection, sql: str, params: tuple = ()) -> int:
    row = conn.execute(sql, params).fetchone()
    return int(row[0]) if row and row[0] is not None else 0


def render(conn: psycopg.Connection, release_id: int) -> str:
    lines: list[str] = []
    a = lines.append
    p = (release_id,)

    a("# M3 — curation")
    a("")
    a(f"Generated {datetime.now(UTC):%Y-%m-%d %H:%M} UTC.")
    a("")
    a(
        "The derived layer: `compound`, `target`, `assay`, `publication` and a "
        "one-row-per-measurement `activity` table, built from the pinned raw "
        "releases. **No aggregation happens here** — Ki, IC50, Kd and EC50 keep "
        "their own rows, and pair-level labels (`pair_regression`, `pair_label`) "
        "are M4 and deliberately absent."
    )
    a("")

    # ------------------------------------------------------------- provenance
    from seq2lead.profiling.status_banner import banner

    for line in banner():
        a(line)

    a("## Source releases")
    a("")
    a("| Subset | Release | Rows loaded | SHA-256 |")
    a("| --- | --- | --- | --- |")
    for r in _rows(
        conn,
        "SELECT subset, id, rows_loaded, sha256 FROM source_release "
        "WHERE source_name='BindingDB' ORDER BY id",
    ):
        a(f"| `{r[0]}` | {r[1]} | {r[2]:,} | `{r[3][:16]}…` |")
    a("")

    run = conn.execute(
        "SELECT curator_version, rows_seen, rows_curated, rows_excluded, "
        "activities_written, started_at, finished_at FROM curation_run "
        "WHERE source_release_id=%s",
        p,
    ).fetchone()
    if run:
        a(f"Curator version: `{run[0]}`")
        a("")

    # ------------------------------------------------- join diagnostics first
    a("## Assay join, measured before it was implemented")
    a("")
    a(
        "Assay descriptions are not in the measurement file. Reaching them is a "
        "two-hop join, and the shape of that join was measured first — scoped by "
        "`source_release_id`, because `raw_record` holds both mapping artifacts and "
        "an unscoped join would silently cross them."
    )
    a("")
    a("```")
    a("raw_measurement 'BindingDB Reactant_set_id'")
    a("   -> rsid_eaids 'REACTANT_SET_ID' -> 'ENTRYID_ASSAYID'   e.g. '285_1'")
    a("   -> assays ('ENTRYID','ASSAYID') = ('285','1')          -> DESCRIPTION")
    a("```")
    a("")
    d = JOIN_DIAGNOSTICS
    a("| Hop | Measurement | Count |")
    a("| --- | --- | --- |")
    a(f"| 0 | measurement rows | {d['measurement_rows']:,} |")
    a(
        f"| 0 | with a reactant id | {d['with_reactant_id']:,} "
        f"({d['with_reactant_id'] / d['measurement_rows']:.2%}) |"
    )
    a(f"| 0 | distinct reactant ids | {d['distinct_reactant_ids']:,} (1:1 with rows) |")
    rsid_keys = f"{d['rsid_rows']:,} / {d['rsid_distinct_keys']:,}"
    a(f"| 1 | `rsid_eaids` rows / distinct keys | {rsid_keys} |")
    a(
        f"| 1 | **matched** | {d['hop1_matched']:,} "
        f"({d['hop1_matched'] / d['measurement_rows']:.2%}) |"
    )
    a(f"| 1 | unmatched | {d['measurement_rows'] - d['hop1_matched']:,} |")
    a(f"| 1 | rows matching >1 mapping | **{d['hop1_multi']:,}** |")
    a(f"| 2 | `assays` rows / distinct keys | {d['assay_rows']:,} / {d['assay_distinct_keys']:,} |")
    a(f"| 2 | reach an assay description | {d['hop2_reached']:,} |")
    a(f"| 2 | lost at this hop | {d['hop1_matched'] - d['hop2_reached']:,} |")
    a(f"| 2 | rows matching >1 assay | **{d['hop2_multi']:,}** |")
    a("")
    a(
        f"**The join cannot multiply activity rows.** No key is duplicated in either "
        f"mapping artifact, no measurement matches more than one mapping or more than "
        f"one assay, and a LEFT JOIN across both hops returns exactly "
        f"{d['left_join_rows']:,} rows — the measurement row count. This was verified "
        "before any curation code was written, not assumed."
    )
    a("")
    a(
        f"Orphans, recorded rather than ignored: {d['rsid_orphans']} `rsid_eaids` rows "
        f"reference reactant ids absent from the measurement file; "
        f"{d['assay_unreferenced']} assay rows are never referenced; "
        f"{d['dangling_eaids']} referenced assay keys have no assay row."
    )
    a("")

    # ---------------------------------------------------------- reconciliation
    a("## Reconciliation")
    a("")
    curated_rows = _scalar(
        conn,
        "SELECT count(DISTINCT raw_measurement_id) FROM activity WHERE source_release_id=%s",
        p,
    )
    excluded_rows = _scalar(
        conn, "SELECT count(*) FROM curation_exclusion WHERE source_release_id=%s", p
    )
    raw_rows = _scalar(conn, "SELECT rows_loaded FROM source_release WHERE id=%s", p)
    activities = _scalar(conn, "SELECT count(*) FROM activity WHERE source_release_id=%s", p)

    a("| | Count |")
    a("| --- | --- |")
    a(f"| Raw measurement rows in the release | {raw_rows:,} |")
    a(f"| Curated (>=1 activity) | {curated_rows:,} |")
    a(f"| Excluded with a rule code | {excluded_rows:,} |")
    a(f"| **curated + excluded** | **{curated_rows + excluded_rows:,}** |")
    a(
        f"| **Reconciles** | **"
        f"{'yes' if curated_rows + excluded_rows == raw_rows else 'NO — investigate'}** |"
    )
    a(f"| `activity` rows written | {activities:,} |")
    a("")
    a(
        "`activity` exceeds the curated row count because a measurement row carrying "
        "more than one affinity type yields one row per type. The "
        "`(raw_measurement_id, measurement_type)` unique key makes a double-write "
        "impossible."
    )
    a("")

    a("### Exclusions by rule")
    a("")
    a("| Rule | Rows | Share of release |")
    a("| --- | --- | --- |")
    for r in _rows(
        conn,
        "SELECT rule_code, count(*) FROM curation_exclusion WHERE source_release_id=%s "
        "GROUP BY rule_code ORDER BY count(*) DESC",
        p,
    ):
        a(f"| `{r[0]}` | {r[1]:,} | {r[1] / max(raw_rows, 1):.3%} |")
    a("")

    # ------------------------------------------------------------ distributions
    a("## Measurement types and relations")
    a("")
    a(
        "Kept distinct by construction. These are counts of what the source says, not "
        "a curation decision. M4 applies a predeclared threshold; whether Ki may be "
        "pooled across assays at all is M5's question."
    )
    a("")
    a("| Type | Total | `=` | `<` | `<=` | `>` | `>=` | `~` | `?` | no magnitude |")
    a("| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    for mtype in ("KI", "IC50", "KD", "EC50"):
        total = _scalar(
            conn,
            "SELECT count(*) FROM activity WHERE source_release_id=%s AND measurement_type=%s",
            (release_id, mtype),
        )
        if not total:
            continue
        rel = dict(
            _rows(
                conn,
                "SELECT relation, count(*) FROM activity WHERE source_release_id=%s "
                "AND measurement_type=%s GROUP BY relation",
                (release_id, mtype),
            )
        )
        nonnum = _scalar(
            conn,
            "SELECT count(*) FROM activity WHERE source_release_id=%s AND measurement_type=%s "
            "AND value_numeric IS NULL",
            (release_id, mtype),
        )
        cells = " | ".join(f"{rel.get(r, 0):,}" for r in ("=", "<", "<=", ">", ">=", "~", "?"))
        a(f"| {mtype} | {total:,} | {cells} | {nonnum:,} |")
    a("")
    a("### Inclusive and exclusive bounds are kept apart")
    a("")
    a(
        "An earlier revision collapsed `>=` into `>` and `<=` into `<`. That is wrong, "
        "and wrong precisely where it matters. Taking the activity threshold as pKi 6.0 "
        "-- Ki = 1,000 nM -- a record reading `>1000` excludes equality, so its whole "
        "admissible range sits below the threshold and it is decisively **inactive**. A "
        "record reading `>=1000` admits exactly 1,000 nM, which is pKi 6.0 and therefore "
        "**active**, so it cannot decide at all. Collapsing the two converts ambiguity "
        "into a confident label."
    )
    a("")
    a("The contract M4 will aggregate over, at Ki = 1,000 nM:")
    a("")
    a("| Record | Constraint on pKi | Verdict |")
    a("| --- | --- | --- |")
    a("| `= 1000` | `pKi == 6.0` | active |")
    a("| `< 1000` | `pKi > 6.0` | active |")
    a("| `<= 1000` | `pKi >= 6.0` | active |")
    a("| `> 1000` | `pKi < 6.0` | inactive |")
    a("| `>= 1000` | `pKi <= 6.0` | **ambiguous** |")
    a("| `~ 1000` | unspecified | ambiguous |")
    a("| `? 1000` | operator not understood | ambiguous |")
    a("")
    a(
        "`activity.relation` holds the canonical operator, `relation_raw` the operator "
        "exactly as the source wrote it, and `bound_inclusive` states inclusivity "
        "explicitly rather than leaving it to be inferred. Approximate (`~`) and "
        "unrecognized (`?`) relations are never folded into `=`."
    )
    a("")
    observed = dict(
        _rows(
            conn,
            "SELECT relation, count(*) FROM activity WHERE source_release_id=%s GROUP BY relation",
            p,
        )
    )
    absent = [r for r in ("<=", ">=", "~", "?") if not observed.get(r)]
    if absent:
        a(
            "Worth recording: "
            + ", ".join(f"`{r}`" for r in absent)
            + " do not occur in this release at all, so the collapse had no effect on "
            "these numbers. That is a property of this data, not of the old code."
        )
        a("")
    a("All values carry the unit recorded on the source column: **nM**.")
    a("")

    # ------------------------------------------------------------ join coverage
    a("## Assay coverage of the curated activities")
    a("")
    a("| Status | Activities | Share |")
    a("| --- | --- | --- |")
    for r in _rows(
        conn,
        "SELECT assay_join_status, count(*) FROM activity WHERE source_release_id=%s "
        "GROUP BY assay_join_status ORDER BY count(*) DESC",
        p,
    ):
        a(f"| `{r[0]}` | {r[1]:,} | {r[1] / max(activities, 1):.2%} |")
    a("")
    no_assay = _scalar(
        conn,
        "SELECT count(*) FROM activity WHERE source_release_id=%s AND assay_id IS NULL",
        p,
    )
    no_assay_rows = _scalar(
        conn,
        "SELECT count(DISTINCT raw_measurement_id) FROM activity "
        "WHERE source_release_id=%s AND assay_id IS NULL",
        p,
    )
    a(
        f"**{no_assay:,} activity rows ({no_assay_rows:,} measurement rows) are retained "
        f"with no assay description** — {no_assay / max(activities, 1):.2%} of the "
        "curated layer. They keep `assay_id IS NULL` and a status saying why. Nothing "
        "is dropped for want of assay context: a measurement without its assay text is "
        "still a measurement, and silently discarding it would bias the curated set "
        "toward whatever BindingDB happens to have mapped."
    )
    a("")

    # ------------------------------------------------------------------- scope
    a("## Benchmark scope")
    a("")
    in_scope = _scalar(
        conn,
        "SELECT count(*) FROM activity WHERE source_release_id=%s AND in_benchmark_scope",
        p,
    )
    a("| | Activities | Share |")
    a("| --- | --- | --- |")
    a(f"| In single-protein scope | {in_scope:,} | {in_scope / max(activities, 1):.2%} |")
    a(
        f"| Out of scope (retained, flagged) | {activities - in_scope:,} | "
        f"{(activities - in_scope) / max(activities, 1):.2%} |"
    )
    a("")
    a("| Reason | Activities |")
    a("| --- | --- |")
    for r in _rows(
        conn,
        "SELECT split_part(scope_reason,':',1), count(*) FROM activity "
        "WHERE source_release_id=%s AND scope_reason IS NOT NULL "
        "GROUP BY 1 ORDER BY count(*) DESC",
        p,
    ):
        a(f"| `{r[0]}` | {r[1]:,} |")
    a("")
    a(
        "Out-of-scope rows are **flagged, not deleted**. The single-protein scope is a "
        "benchmark decision, and keeping the rows means it stays reversible and "
        "auditable rather than baked into the data."
    )
    a("")

    # --------------------------------------------------------------- entities
    a("## Entities")
    a("")
    a("| Table | Rows |")
    a("| --- | --- |")
    a(f"| `compound` (standardized parents) | {_scalar(conn, 'SELECT count(*) FROM compound'):,} |")
    a(
        f"| `compound_source` (source structures mapped) | "
        f"{_scalar(conn, 'SELECT count(*) FROM compound_source'):,} |"
    )
    a(f"| `target` (distinct sequences) | {_scalar(conn, 'SELECT count(*) FROM target'):,} |")
    a(f"| `target_alias` | {_scalar(conn, 'SELECT count(*) FROM target_alias'):,} |")
    a(f"| `assay` | {_scalar(conn, 'SELECT count(*) FROM assay'):,} |")
    a(f"| `assay_link` | {_scalar(conn, 'SELECT count(*) FROM assay_link'):,} |")
    a(f"| `publication` | {_scalar(conn, 'SELECT count(*) FROM publication'):,} |")
    a("")

    a("## Structure standardization")
    a("")
    a(f"Version string: `{_standardizer_version()}`.")
    a("")
    a(
        "Changing any step changes that string, which is part of the `compound` "
        "uniqueness key and of the cache key in `compound_source`, so results from two "
        "different pipelines can never silently mix."
    )
    a("")
    a("| Step | What it does | Why it is here |")
    a("| --- | --- | --- |")
    a(
        "| `MolFromSmiles` | parse and sanitize | a structure RDKit cannot read is "
        "excluded as `invalid_smiles` rather than guessed at |"
    )
    a(
        "| `Cleanup` | normalize functional groups, disconnect metals, reionize | "
        "removes drawing conventions that would otherwise split one substance in two |"
    )
    a(
        "| `FragmentParent` | keep the largest organic fragment | strips salts and "
        "solvates, so a hydrochloride and its free base are one compound |"
    )
    a(
        "| `Uncharger` | neutralize what can be neutralized | a carboxylate and its "
        "acid are one compound, not two |"
    )
    a("")
    a(
        "**Deliberately not done: tautomer canonicalization.** RDKit's enumeration is "
        "slow, and its canonical choice is a convention rather than a chemical fact, so "
        "tautomers remain distinct compounds here. Recorded as a limitation, not hidden."
    )
    a("")
    a("### Traceability to the original structure")
    a("")
    a(
        "Standardization is lossy by design, so nothing is allowed to depend on "
        "reversing it. `compound_source` keeps, for every source structure seen:"
    )
    a("")
    a("| Column | Holds |")
    a("| --- | --- |")
    a("| `source_inchikey` | the compound identifier **as BindingDB gave it** |")
    a("| `source_smiles` | the **original SMILES**, unmodified |")
    a("| `compound_id` | the standardized parent it resolved to |")
    a("| `standardizer_version` | which pipeline produced that mapping |")
    a("")
    a(
        "Every curated activity therefore reaches its original structure and source "
        "identifier in one join, and reaches the untouched source row via "
        "`activity.raw_measurement_id` in another. No standardization decision is "
        "irreversible at the record level."
    )
    a("")
    a("### Collisions")
    a("")
    n_compound = _scalar(conn, "SELECT count(*) FROM compound")
    n_source = _scalar(conn, "SELECT count(*) FROM compound_source")
    collided = _scalar(
        conn,
        "SELECT count(*) FROM (SELECT compound_id FROM compound_source "
        "GROUP BY compound_id HAVING count(*) > 1) x",
    )
    a("| | Count |")
    a("| --- | --- |")
    a(f"| Distinct source structures standardized | {n_source:,} |")
    a(f"| Distinct standardized parents (`compound`) | {n_compound:,} |")
    a(f"| **Source structures collapsed onto a shared parent** | **{n_source - n_compound:,}** |")
    a(f"| Parents carrying more than one source structure | {collided:,} |")
    a("")
    if collided:
        a("Largest collision groups — salts, solvates and charge variants of one substance:")
        a("")
        a("| Standardized parent | Source structures |")
        a("| --- | --- |")
        for r in _rows(
            conn,
            "SELECT c.inchikey, count(*) FROM compound_source cs JOIN compound c "
            "ON c.id = cs.compound_id GROUP BY c.inchikey ORDER BY count(*) DESC LIMIT 5",
        ):
            a(f"| `{r[0]}` | {r[1]:,} |")
        a("")
    a(
        "A collision is the pipeline working, not a fault: two source records differing "
        "only by counter-ion, solvate or protonation become one compound. Both remain "
        "individually addressable in `compound_source`."
    )
    a("")

    lens = conn.execute(
        "SELECT min(length), percentile_cont(0.5) WITHIN GROUP (ORDER BY length), "
        "max(length), count(*) FILTER (WHERE length > 1022) FROM target"
    ).fetchone()
    if lens:
        a(
            f"Target sequence length: min {lens[0]:,}, median {lens[1]:,.0f}, "
            f"max {lens[2]:,}. {lens[3]:,} exceed the 1,022-residue ESM-2 limit — "
            "the M7 long-sequence decision recorded in `profile.md`."
        )
        a("")

    # ------------------------------------------------------------- ambiguities
    a("## Unresolved ambiguities")
    a("")
    a("| # | Issue |")
    a("| --- | --- |")
    a(
        "| 1 | **Tautomers are distinct compounds.** The standardizer does not "
        "canonicalize tautomers: RDKit's enumeration is slow and its canonical choice "
        "is a convention rather than a chemical fact. Two tautomers of one substance "
        "therefore get two `compound` rows. |"
    )
    a(
        "| 2 | **Stereochemistry is preserved as given.** No attempt is made to "
        "reconcile a racemate with its enantiomers, or to infer unspecified centres. "
        "Whether that is right depends on the assay, which M5 will have to look at. |"
    )
    a(
        "| 3 | **`publication` identity is weak.** Rows are keyed on "
        "(PMID, DOI, patent, date) with empty strings for missing parts, so one paper "
        "recorded inconsistently across entries becomes more than one publication row. |"
    )
    a(
        "| 4 | **Assay text decoding is one-way.** `description_text` is "
        "`html.unescape(description_raw)`; both are stored so the transform is "
        "auditable, but entities that were themselves literal text are now "
        "indistinguishable from decoded ones. |"
    )
    a(
        "| 5 | **Only chain 1 defines the target.** Multi-chain rows are flagged out of "
        "scope, but the sequence recorded for them is still chain 1's, which does not "
        "represent the complex. Do not read `target_id` on an out-of-scope row as the "
        "thing that was assayed. |"
    )
    a(
        "| 6 | **Target sequence length spans implausible extremes** — 7 residues at "
        "one end and 34,350 at the other. Neither is a plausible single druggable "
        "protein: the short one cannot form a pocket and the long one is likely a "
        "concatenated or mis-parsed entry. Both are retained without judgement here, "
        "because a length cutoff is an outcome-independent scope choice that belongs "
        "to M5 with the distribution in front of us. |"
    )
    a(
        "| 7 | **`ph` and `Temp (C)` are carried verbatim and largely empty.** The M1 "
        "pilot found them 100% unpopulated; how much the full release populates them "
        "is a question for the M5 assay-comparability analysis. |"
    )
    a("")
    return "\n".join(lines) + "\n"


def write(content: str, path: Path = REPORT_PATH) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path
