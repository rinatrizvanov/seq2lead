"""Render `reports/endpoint.md` for an M4 Ki endpoint version."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import psycopg

REPORT_PATH = Path("reports/endpoint.md")


def _rows(conn: psycopg.Connection, sql: str, params: tuple = ()) -> list[tuple]:
    return conn.execute(sql, params).fetchall()


def _scalar(conn: psycopg.Connection, sql: str, params: tuple = ()) -> int:
    row = conn.execute(sql, params).fetchone()
    return int(row[0]) if row and row[0] is not None else 0


def render(conn: psycopg.Connection, endpoint_id: int) -> str:  # noqa: PLR0915
    lines: list[str] = []
    a = lines.append
    e = (endpoint_id,)

    meta = conn.execute(
        "SELECT name, measurement_type, threshold_pki, discordance_pki, source_release_id, "
        "curator_version, builder_version, n_activities_in, n_unusable_magnitude, "
        "n_no_constraint FROM endpoint_version WHERE id=%s",
        e,
    ).fetchone()
    assert meta is not None

    a("# M4 — Ki endpoint")
    a("")
    a(f"Generated {datetime.now(UTC):%Y-%m-%d %H:%M} UTC.")
    a("")
    a(
        "Provisional Ki endpoint tables built from the curated `activity` layer. "
        "**Ki only** — IC50, Kd and EC50 remain in `activity`, unmerged and untouched. "
        "No train/test partition is assigned here; `eval_exclusion_reason` records why "
        "a pair *would* be held out, and partitioning is M6."
    )
    a("")

    from seq2lead.profiling.status_banner import banner

    for line in banner():
        a(line)

    a("## Version")
    a("")
    a("| Field | Value |")
    a("| --- | --- |")
    a(f"| Endpoint | `{meta[0]}` (id {endpoint_id}) |")
    a(f"| Measurement type | {meta[1]} |")
    a(f"| Classification threshold | **pKi >= {meta[2]}** |")
    a(f"| Discordance threshold | {meta[3]} pKi units |")
    a(f"| Source release | {meta[4]} |")
    a(f"| Curator | `{meta[5]}` |")
    a(f"| Builder | `{meta[6]}` |")
    a("")
    a(
        "The threshold is **predeclared**: fixed before any split exists and before any "
        "model is trained. Sensitivity versions are built as separate named endpoints "
        "rather than by mutating this one, so no threshold can be retrofitted to a "
        "result."
    )
    a("")
    superseded = conn.execute(
        "SELECT name, builder_version, superseded_reason FROM endpoint_version "
        "WHERE superseded_by = %s",
        (meta[0],),
    ).fetchone()
    if superseded is not None:
        a("### Supersedes a corrected version")
        a("")
        a(
            f"This version replaces `{superseded[0]}` (builder `{superseded[1]}`). "
            "The old version is **left intact and marked superseded**, not edited, so "
            "the correction is auditable."
        )
        a("")
        a(f"> {superseded[2]}")
        a("")
        before = conn.execute(
            "SELECT id FROM endpoint_version WHERE name = %s", (superseded[0],)
        ).fetchone()
        if before is not None:
            a("| Count | Before (`" + superseded[0] + "`) | After (`" + meta[0] + "`) |")
            a("| --- | --- | --- |")
            for field_label, sql in (
                ("label = active", "label='active'"),
                ("label = inactive", "label='inactive'"),
                ("label = ambiguous", "label='ambiguous'"),
                ("label = none", "label='none'"),
                ("status = ok", "status='ok'"),
                ("status = exact_bound_conflict", "status='exact_bound_conflict'"),
                (
                    "**ok despite an exact outside bounds**",
                    "status='ok' AND n_exact_outside_bounds>0",
                ),
            ):
                b = _scalar(
                    conn,
                    f"SELECT count(*) FROM pair_label WHERE endpoint_id=%s AND {sql}",  # noqa: S608
                    (before[0],),
                )
                aft = _scalar(
                    conn,
                    f"SELECT count(*) FROM pair_label WHERE endpoint_id=%s AND {sql}",  # noqa: S608
                    e,
                )
                a(f"| {field_label} | {b:,} | {aft:,} |")
            a("")
            a(
                "The old rule tested only the **median** of the exact values against the "
                "censored interval. A pair with exact values at pKi 5 and pKi 9 under a "
                "bound of `pKi > 6` has a median of 7, which sits inside the interval, so "
                "the pair was labelled confidently — while the observation at pKi 5 flatly "
                "contradicts the bound. The rule now tests **every** exact observation."
            )
            a("")

    others = _rows(
        conn,
        "SELECT name, threshold_pki FROM endpoint_version WHERE id <> %s "
        "AND superseded_by IS NULL ORDER BY threshold_pki",
        e,
    )
    if others:
        a("Sibling versions: " + ", ".join(f"`{n}` (pKi >= {t})" for n, t in others) + ".")
        a("")

    # ------------------------------------------------------------------ input
    a("## Input")
    a("")
    a("| | Count |")
    a("| --- | --- |")
    a(f"| Ki activities read | {meta[7]:,} |")
    a(f"| Rejected: magnitude not positive and finite | {meta[8]:,} |")
    a(f"| Rejected: relation bounds nothing (`~`, `?`) | {meta[9]:,} |")
    n_pairs = _scalar(conn, "SELECT count(*) FROM pair_label WHERE endpoint_id=%s", e)
    a(f"| Distinct (compound, target) pairs | {n_pairs:,} |")
    a("")
    a(
        "Zero magnitudes occur in the source as `0.000` and `>0.000`. `log10(0)` is "
        "`-inf`, and `Ki > 0` is true of every compound ever made, so such records "
        "reach neither the regression nor the interval logic. They remain in "
        "`activity` with their original text."
    )
    a("")
    a(
        "`n_no_constraint` counts records whose relation bounds nothing — `~` "
        "(approximate) and `?` (operator not understood). In builder `m4/v1` this "
        "counter was declared but never incremented, so it always read zero and said "
        "nothing; it is now wired. It still reads zero here, but for a real reason: "
        "this release contains no `~` or `?` Ki records at all. On another source it "
        "would not."
    )
    a("")

    # ------------------------------------------------------------- regression
    a("## `pair_regression` — exact values only")
    a("")
    reg_n = _scalar(conn, "SELECT count(*) FROM pair_regression WHERE endpoint_id=%s", e)
    reg_support = _scalar(
        conn,
        "SELECT count(*) FROM pair_regression_support s JOIN pair_regression r "
        "ON r.id=s.pair_id WHERE r.endpoint_id=%s",
        e,
    )
    disc = _scalar(
        conn, "SELECT count(*) FROM pair_regression WHERE endpoint_id=%s AND is_discordant", e
    )
    stats = conn.execute(
        "SELECT min(p_median), percentile_cont(0.5) WITHIN GROUP (ORDER BY p_median), "
        "max(p_median), avg(n_obs), max(n_obs) FROM pair_regression WHERE endpoint_id=%s",
        e,
    ).fetchone()
    a("| | Count |")
    a("| --- | --- |")
    a(f"| Pairs | {reg_n:,} |")
    a(f"| Contributing activities | {reg_support:,} |")
    a(f"| **Discordant (spread > {meta[3]} pKi)** | **{disc:,}** ({disc / max(reg_n, 1):.2%}) |")
    if stats and stats[0] is not None:
        a(f"| pKi median: min / median / max | {stats[0]:.2f} / {stats[1]:.2f} / {stats[2]:.2f} |")
        a(f"| Observations per pair: mean / max | {stats[3]:.2f} / {stats[4]:,} |")
    a("")
    a(
        "Only exact `=` records with a positive finite magnitude, converted as "
        "`pKi = 9 - log10(Ki_nM)`. **Censored records never enter a median**: "
        "`Ki > 10000` is not a measurement of 10000, and averaging censoring "
        "thresholds manufactures a value no experiment produced."
    )
    a("")
    a("Observations per pair:")
    a("")
    a("| n_obs | Pairs |")
    a("| --- | --- |")
    for r in _rows(
        conn,
        "SELECT CASE WHEN n_obs >= 10 THEN '10+' ELSE n_obs::text END AS bucket, count(*) "
        "FROM pair_regression WHERE endpoint_id=%s GROUP BY 1 "
        "ORDER BY min(n_obs)",
        e,
    ):
        a(f"| {r[0]} | {r[1]:,} |")
    a("")
    a(
        f"Discordant pairs are flagged with `eval_exclusion_reason = 'discordant'`. That "
        f"records the intent to keep them out of validation and test — a pair whose "
        f"replicates disagree by more than {meta[3]} pKi unit cannot arbitrate a "
        "prediction. **No partition is assigned.**"
    )
    a("")

    # ------------------------------------------------------------------ label
    a("## `pair_label` — exact points and censored bounds")
    a("")
    a("| Label | Pairs | Share |")
    a("| --- | --- | --- |")
    total_lab = _scalar(conn, "SELECT count(*) FROM pair_label WHERE endpoint_id=%s", e)
    for r in _rows(
        conn,
        "SELECT label, count(*) FROM pair_label WHERE endpoint_id=%s GROUP BY 1 "
        "ORDER BY count(*) DESC",
        e,
    ):
        a(f"| `{r[0]}` | {r[1]:,} | {r[1] / max(total_lab, 1):.2%} |")
    a("")
    a("| Status | Pairs | Meaning |")
    a("| --- | --- | --- |")
    meanings = {
        "ok": "evidence resolved to a label",
        "exact_bound_conflict": "an exact value falls outside its own censored bounds",
        "empty_intersection": "censored bounds cannot all hold at once",
        "no_usable_evidence": "every record had an unusable magnitude or no bound",
    }
    for r in _rows(
        conn,
        "SELECT status, count(*) FROM pair_label WHERE endpoint_id=%s GROUP BY 1 "
        "ORDER BY count(*) DESC",
        e,
    ):
        a(f"| `{r[0]}` | {r[1]:,} | {meanings.get(r[0], '')} |")
    a("")
    a("| Evidence | Pairs |")
    a("| --- | --- |")
    for r in _rows(
        conn,
        "SELECT evidence, count(*) FROM pair_label WHERE endpoint_id=%s GROUP BY 1 "
        "ORDER BY count(*) DESC",
        e,
    ):
        a(f"| `{r[0]}` | {r[1]:,} |")
    a("")
    contradictory = _scalar(
        conn,
        "SELECT count(*) FROM pair_label WHERE endpoint_id=%s AND status <> 'ok'",
        e,
    )
    a(
        f"**{contradictory:,} pairs get no benchmark label**, and the reason is recorded "
        "rather than resolved. Censored bounds are combined by interval intersection on "
        "pKi — never by a median of censoring thresholds. Where the intersection is "
        "empty, or where the exact evidence sits outside it, the contradiction is the "
        "finding; inventing a label would hide it."
    )
    a("")

    # ------------------------------------------------------- the operator contract
    a("### The operator contract at the threshold")
    a("")
    a(
        f"`pKi = 9 - log10(Ki_nM)` is monotonically *decreasing*, so a lower bound on Ki "
        f"is an upper bound on pKi. At the threshold (Ki = {10 ** (9 - meta[2]):,.0f} nM "
        f"= pKi {meta[2]}):"
    )
    a("")
    a("| Ki record | Constraint on pKi | Verdict |")
    a("| --- | --- | --- |")
    a(f"| `= {10 ** (9 - meta[2]):,.0f}` | `pKi == {meta[2]}` | active |")
    a(f"| `< {10 ** (9 - meta[2]):,.0f}` | `pKi > {meta[2]}` | active |")
    a(f"| `<= {10 ** (9 - meta[2]):,.0f}` | `pKi >= {meta[2]}` | active |")
    a(f"| `> {10 ** (9 - meta[2]):,.0f}` | `pKi < {meta[2]}` | inactive |")
    a(f"| `>= {10 ** (9 - meta[2]):,.0f}` | `pKi <= {meta[2]}` | **ambiguous** |")
    a("")
    a(
        "The two lower bounds on Ki disagree exactly at the threshold: `>` excludes the "
        "endpoint so its whole range is inactive, while `>=` admits the endpoint, which "
        "is itself active, and therefore decides nothing. The two upper bounds agree, "
        "because the active side is the inclusive one. This is why inclusive and "
        "exclusive relations are not collapsed."
    )
    a("")

    # ------------------------------------------------------------ target coverage
    a("## Target coverage")
    a("")
    targets = _scalar(
        conn, "SELECT count(DISTINCT target_id) FROM pair_label WHERE endpoint_id=%s", e
    )
    compounds = _scalar(
        conn, "SELECT count(DISTINCT compound_id) FROM pair_label WHERE endpoint_id=%s", e
    )
    a(f"{targets:,} targets and {compounds:,} compounds appear in this endpoint.")
    a("")
    a(
        "Counts below use **measured** actives and inactives only. No negatives are "
        "fabricated: an unmeasured pair is absent, not inactive."
    )
    a("")
    a("| Measured actives and inactives per target | Targets |")
    a("| --- | --- |")
    for label, predicate in (
        (">= 1 active and >= 1 inactive", "n_active >= 1 AND n_inactive >= 1"),
        (">= 10 each", "n_active >= 10 AND n_inactive >= 10"),
        (">= 25 each", "n_active >= 25 AND n_inactive >= 25"),
        (">= 50 each", "n_active >= 50 AND n_inactive >= 50"),
        (">= 100 each", "n_active >= 100 AND n_inactive >= 100"),
    ):
        n = _scalar(
            conn,
            "SELECT count(*) FROM (SELECT target_id, "
            "count(*) FILTER (WHERE label='active') AS n_active, "
            "count(*) FILTER (WHERE label='inactive') AS n_inactive "
            "FROM pair_label WHERE endpoint_id=%s AND in_benchmark_scope "
            f"GROUP BY target_id) t WHERE {predicate}",  # noqa: S608
            e,
        )
        a(f"| {label} | {n:,} |")
    a("")
    a(
        "Restricted to `in_benchmark_scope` pairs (single-protein targets). Whether any "
        "of this is poolable across assays is **M5's question**, not settled here."
    )
    a("")

    a("## Evaluation eligibility")
    a("")
    a(
        "`excluded_from_eval` is an **explicit column on both tables**. Downstream code "
        "does not have to join `pair_label` to discover that a pair is unusable, and a "
        "regression pair whose label is contradictory is flagged on `pair_regression` "
        "too. Excluded means **out of validation and test**; whether a pair may be used "
        "for training is a separate decision for M6."
    )
    a("")
    a("| Table | Excluded | Share |")
    a("| --- | --- | --- |")
    for table in ("pair_regression", "pair_label"):
        tot = _scalar(conn, f"SELECT count(*) FROM {table} WHERE endpoint_id=%s", e)  # noqa: S608
        exc = _scalar(
            conn,
            f"SELECT count(*) FROM {table} WHERE endpoint_id=%s AND excluded_from_eval",  # noqa: S608
            e,
        )
        a(f"| `{table}` | {exc:,} | {exc / max(tot, 1):.2%} |")
    a("")
    a("| Reason | `pair_label` | `pair_regression` |")
    a("| --- | --- | --- |")
    reasons = _rows(
        conn,
        "SELECT eval_exclusion_reason, count(*) FROM pair_label WHERE endpoint_id=%s "
        "AND eval_exclusion_reason IS NOT NULL GROUP BY 1 ORDER BY count(*) DESC",
        e,
    )
    for reason, n in reasons:
        m = _scalar(
            conn,
            "SELECT count(*) FROM pair_regression WHERE endpoint_id=%s "
            "AND eval_exclusion_reason=%s",
            (endpoint_id, reason),
        )
        a(f"| `{reason}` | {n:,} | {m:,} |")
    a("")
    a(
        "**Precedence.** A pair can be both contradictory and discordant. Contradiction "
        "outranks discordance, because contradictory evidence cannot be reconciled at "
        "all while discordant evidence merely disagrees — and a contradictory pair has "
        "no label to score against in the first place. The full order is "
        "`exact_bound_conflict` > `empty_intersection` > `no_usable_evidence` > "
        "`discordant` > `ambiguous_label`."
    )
    a("")
    a(
        "Discordant pairs keep their label and their statistics: they are retained for "
        "audit and for the M5 assay-variance analysis, and may later be considered for "
        "training. They must not silently arbitrate validation or test, which is what "
        "the flag prevents."
    )
    a("")

    # ------------------------------------------------- standardization / replication
    a("## Audits")
    a("")
    collapsed = _scalar(
        conn,
        "SELECT count(*) FROM pair_label p WHERE p.endpoint_id=%s AND p.compound_id IN "
        "(SELECT compound_id FROM compound_source GROUP BY compound_id HAVING count(*) > 1)",
        e,
    )
    a(
        f"**Pairs on a collapsed compound: {collapsed:,}.** Their compound was reached "
        "from more than one source structure — salts, solvates or charge variants "
        "standardized to one parent. Every contributing source structure remains "
        "individually addressable in `compound_source`."
    )
    a("")
    multi_assay = _scalar(
        conn, "SELECT count(*) FROM pair_regression WHERE endpoint_id=%s AND n_assays > 1", e
    )
    multi_pub = _scalar(
        conn, "SELECT count(*) FROM pair_regression WHERE endpoint_id=%s AND n_publications > 1", e
    )
    disc_multi = _scalar(
        conn,
        "SELECT count(*) FROM pair_regression WHERE endpoint_id=%s AND is_discordant "
        "AND n_assays > 1",
        e,
    )
    multi_assay_scoped = _scalar(
        conn,
        "SELECT count(*) FROM pair_regression WHERE endpoint_id=%s AND n_assays > 1 "
        "AND in_benchmark_scope",
        e,
    )
    disc_multi_scoped = _scalar(
        conn,
        "SELECT count(*) FROM pair_regression WHERE endpoint_id=%s AND is_discordant "
        "AND n_assays > 1 AND in_benchmark_scope",
        e,
    )
    a(
        f"Denominator for every row below: **{reg_n:,} `pair_regression` rows** for this "
        "endpoint. Two scopes are given because they answer different questions, and "
        "quoting one number without saying which is how they get confused."
    )
    a("")
    a("| Replication | All pairs | In benchmark scope | Query |")
    a("| --- | --- | --- | --- |")
    a(f"| More than one assay | {multi_assay:,} | {multi_assay_scoped:,} | `n_assays > 1` |")
    a(f"| More than one publication | {multi_pub:,} | — | `n_publications > 1` |")
    a(
        f"| Discordant **and** multi-assay | {disc_multi:,} | {disc_multi_scoped:,} | "
        "`is_discordant AND n_assays > 1` |"
    )
    a("")
    a(
        "All counts are `WHERE endpoint_id = <this endpoint>`; the scoped column adds "
        "`AND in_benchmark_scope`."
    )
    a("")
    a(
        "That last row is the one M5 needs: replicate disagreement that coincides with "
        "an assay change is exactly the signal that Ki may not be poolable across assay "
        "formats. Quantifying it is M5's job; M4 only records the counts."
    )
    a("")

    # --------------------------------------------------------------- traceability
    a("## Traceability")
    a("")
    a(
        "Every pair reaches its supporting activities, and every activity reaches the "
        "raw row it came from. Worked example:"
    )
    a("")
    example = conn.execute(
        "SELECT p.id, p.compound_id, p.target_id, p.n_obs, p.p_median, p.p_spread "
        "FROM pair_regression p WHERE p.endpoint_id=%s AND p.n_obs BETWEEN 2 AND 4 "
        "AND p.is_discordant ORDER BY p.id LIMIT 1",
        e,
    ).fetchone()
    if example is not None:
        a("```")
        a(f"pair_regression id={example[0]}  compound={example[1]}  target={example[2]}")
        a(f"  n_obs={example[3]}  p_median={example[4]:.3f}  spread={example[5]:.3f}")
        for r in _rows(
            conn,
            "SELECT a.id, a.raw_measurement_id, a.relation, a.value_text, a.value_unit, "
            "a.assay_id, a.source_structure_sha256 "
            "FROM pair_regression_support s JOIN activity a ON a.id=s.activity_id "
            "WHERE s.pair_id=%s ORDER BY a.id",
            (example[0],),
        ):
            a(
                f"    activity {r[0]} <- raw_measurement {r[1]}  "
                f"{r[2]}{r[3]} {r[4]}  assay={r[5]}  structure={r[6][:12] if r[6] else None}…"
            )
        a("```")
        a("")
        a(
            "`raw_measurement_id` resolves into the immutable raw layer, whose release "
            "carries the artifact's SHA-256. `source_structure_sha256` identifies the "
            "exact SMILES **that row** carried, so no row inherits another's compound."
        )
        a("")

    # ------------------------------------------------------------- what M5 needs
    a("## Scientific decisions still open for M5")
    a("")
    a("| # | Decision |")
    a("| --- | --- |")
    a(
        "| 1 | **Is Ki poolable across assay formats at all?** A pair measured by "
        "radioligand displacement and by a functional assay may legitimately differ. "
        f"{multi_assay:,} pairs span assays and {disc_multi:,} of those are discordant. "
        "Until that variance is decomposed, pooling is an assumption. |"
    )
    a(
        "| 2 | **Does the threshold survive sensitivity?** 6.0 is predeclared and 7.0 / "
        "8.0 are built as siblings. Whether conclusions hold across them is an M5 "
        "reading, not an M4 claim. |"
    )
    a(
        "| 3 | **What to do with discordant pairs.** Flagged, not removed. Excluding "
        "them from validation and test is the recorded intent; whether they belong in "
        "training is undecided. |"
    )
    a(
        "| 4 | **Whether `ambiguous` pairs carry usable signal.** They have bounds that "
        "straddle the threshold. They are not negatives and must not be recruited as "
        "such. |"
    )
    a(
        "| 5 | **Assay context is largely absent.** `pH` and `Temp (C)` are sparse, so "
        "the variance decomposition may have to lean on assay-description text and "
        "publication identity instead. |"
    )
    a(
        "| 6 | **Organism conflicts.** 1,276 targets carry more than one organism "
        "annotation and `target.organism` is deliberately NULL for them. Whether those "
        "sequences are one target or several is unresolved. |"
    )
    a("")
    return "\n".join(lines) + "\n"


def write(content: str, path: Path = REPORT_PATH) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path
