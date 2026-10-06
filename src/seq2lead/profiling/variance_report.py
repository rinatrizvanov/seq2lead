"""Render `reports/assay_variance.md`."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from seq2lead.analysis import assay_variance as av

if TYPE_CHECKING:
    import psycopg

REPORT_PATH = Path("reports/assay_variance.md")
TAIL_CUTOFFS = (0.3, 0.5, 1.0)


def _dist_row(name: str, d: av.Distribution) -> str:
    if not d.n:
        return f"| {name} | 0 | — | — | — | — |"
    return (
        f"| {name} | {d.n:,} | {d.p10:.3f} | **{d.median:.3f}** | {d.p90:.3f} | "
        f"{d.fraction_over_1:.1%} |"
    )


def render(conn: psycopg.Connection, endpoint_name: str) -> str:  # noqa: PLR0915
    report = av.run(conn, endpoint_name)
    eid = report.endpoint_id
    cov = report.coverage
    conf = report.confounding
    dup = av.duplicate_structure(conn, eid)
    matched = av.matched_comparison(conn, eid)
    matched_between_values = av.matched_between_values(conn)
    dedup = av.deduplicated_matched_comparison(conn, eid)

    lines: list[str] = []
    a = lines.append

    a("# M5 — assay-context variance")
    a("")
    a(f"Generated {datetime.now(UTC):%Y-%m-%d %H:%M} UTC. Analysis `{av.ANALYSIS_VERSION}`.")
    a("")
    a(
        f"Endpoint under analysis: **`{report.endpoint_name}`** (id {eid}, pKi >= "
        f"{report.threshold}). Superseded versions are refused by default, so the "
        "corrected `-v2` build is what these numbers describe."
    )
    a("")
    a(
        "The question M4 could not answer. `pair_regression` takes a median over every "
        "exact Ki observation for a pair, which assumes they measure the same quantity. "
        "If changing assay format shifts Ki systematically, that median averages across "
        "conditions rather than across noise. **Nothing here builds splits or trains "
        "anything, and no M4 data was altered.**"
    )
    a("")

    # ------------------------------------------------------------ denominators
    a("## Denominators")
    a("")
    a("| Population | Pairs | Share of regression pairs |")
    a("| --- | --- | --- |")
    base = max(cov["regression_pairs"], 1)
    for key, label in (
        ("label_pairs", "`pair_label` rows"),
        ("regression_pairs", "`pair_regression` rows (**the denominator below**)"),
        ("repeat_pairs", "with >= 2 exact observations"),
        ("multi_assay_pairs", "spanning more than one assay"),
        ("multi_publication_pairs", "spanning more than one publication"),
        ("pairs_with_assay_text", "with at least one usable assay description"),
        ("discordant_pairs", "discordant (exact spread > 1 pKi)"),
        ("unlabelled_pairs", "**no benchmark label** (all causes)"),
        ("status_exact_bound_conflict", "-- exact value outside its censored bounds"),
        ("status_empty_intersection", "-- censored bounds cannot all hold"),
        ("status_no_usable_evidence", "-- no usable magnitude or bound"),
    ):
        share = f"{cov[key] / base:.1%}" if key != "label_pairs" else "—"
        a(f"| {label} | {cov[key]:,} | {share} |")
    a("")
    a(
        f"The {cov['unlabelled_pairs']:,} unlabelled pairs are **not all contradictory**. "
        f"{cov['status_exact_bound_conflict']:,} are exact-versus-bound conflicts, "
        f"{cov['status_empty_intersection']:,} have censored bounds that cannot all hold, "
        f"and {cov['status_no_usable_evidence']:,} carry no usable evidence at all. An "
        "earlier revision labelled the whole total 'contradictory', which conflated "
        "three different failures."
    )
    a("")
    a(
        f"**Only {cov['repeat_pairs']:,} of {cov['regression_pairs']:,} pairs "
        f"({cov['repeat_pairs'] / base:.1%}) have any repeat observation at all.** Every "
        "variance statement below rests on that minority; the other 85% contribute a "
        "single measurement and say nothing about reproducibility."
    )
    a("")

    # ------------------------------------------------------------- missingness
    a("## Covariate missingness, measured first")
    a("")
    a(
        "Measured before attempting any decomposition, because a decomposition over "
        "covariates that are absent produces numbers with no referent."
    )
    a("")
    a("| Covariate | Present | Of | Coverage |")
    a("| --- | --- | --- | --- |")
    for label, n, total in report.missingness:
        a(f"| {label} | {n:,} | {total:,} | {n / max(total, 1):.2%} |")
    a("")
    a(
        "**pH and temperature are unusable as covariates.** At ~6% coverage, any "
        "condition effect they carry is unidentifiable for the other 94%, and "
        "conditioning on them would silently restrict the analysis to a "
        "self-selected subset. They are excluded from the decomposition."
    )
    a("")
    a(
        "A correction to the M1 pilot: it reported pH and temperature as **0%** "
        "populated. The full release has 6.41% and 5.65%. The pilot's figure was a "
        "property of the PDSP Ki subset, not of BindingDB, and generalising it was "
        "wrong."
    )
    a("")

    # ------------------------------------------------------------ confounding
    a("## What can and cannot be identified")
    a("")
    total_rep = max(conf["pairs_with_repeats"], 1)
    a("| Pairs with >= 2 exact observations | Count | Share |")
    a("| --- | --- | --- |")
    for key, label in (
        ("no_assay_id_at_all", "no assay id on any observation"),
        ("assay_id_on_some_only", "assay id on some observations only"),
        ("assay_varies_publication_fixed", "**assay varies, publication fixed**"),
        ("publication_varies_assay_fixed", "publication varies, assay fixed"),
        ("both_vary", "both vary (**confounded**)"),
        ("neither_varies", "neither varies"),
    ):
        a(f"| {label} | {conf[key]:,} | {conf[key] / total_rep:.1%} |")
    a(f"| **sum** | **{conf['bucket_sum']:,}** | |")
    a(f"| population | {conf['pairs_with_repeats']:,} | 100% |")
    a("")
    a(
        f"**The table now reconciles** ({conf['bucket_sum']:,} = "
        f"{conf['pairs_with_repeats']:,}). An earlier revision used "
        "`count(DISTINCT assay_id)`, which ignores NULLs, so the "
        f"{conf['no_assay_id_at_all']:,} pairs with no assay id and the "
        f"{conf['assay_id_on_some_only']:,} with partial coverage fell into no bucket "
        "and the total silently fell short."
    )
    a("")
    a(
        f"**A publication effect cannot be estimated at all.** Exactly "
        f"**{conf['publication_varies_assay_fixed']} pairs** hold the assay fixed while "
        "the publication changes. An earlier revision reported 849 here; that figure "
        "was an artifact of counting a NULL assay id as a single distinct assay, and it "
        "is withdrawn."
    )
    a("")
    a(
        f"In {conf['both_vary']:,} pairs ({conf['both_vary'] / total_rep:.0%}) assay and "
        "publication move together and cannot be separated: a difference there is "
        "jointly attributable to assay format, laboratory, compound batch, protein "
        "preparation and reporting convention."
    )
    a("")

    a("## How much replication is restatement")
    a("")
    a(
        f"Of {dup['observations']:,} exact Ki observations, **{dup['redundant_reports']:,} "
        f"({dup['redundant_reports'] / max(dup['observations'], 1):.2%}) are *potential* "
        "duplicate reports** — the same value, under the same assay id and the same "
        f"publication, for the same pair, across {dup['keys_with_redundancy']:,} keys. "
        "That pattern is consistent with one experiment written twice; it is **not "
        "proof of it**. A paper can legitimately report two runs of one assay that "
        "agree to the reported precision, and BindingDB's assay id is a curation "
        "grouping rather than an experiment identifier."
    )
    a("")
    zero_share = dup["groups_with_zero_spread"] / max(dup["within_assay_groups"], 1)
    a(
        f"Separately, {dup['groups_with_zero_spread']:,} of {dup['within_assay_groups']:,} "
        f"within-assay groups ({zero_share:.1%}) have a spread of exactly zero."
    )
    a("")
    a(
        "**A correction to the previous revision of this report.** It treated that "
        "zero-spread fraction as restatement and removed it from the primary analysis. "
        "That was wrong twice over. **Zero-spread groups are not all restatements** — "
        "two independent measurements can agree, and agreement is the outcome the "
        "analysis exists to detect. And the rate of even *potential* duplication is "
        f"{dup['redundant_reports'] / max(dup['observations'], 1):.2%}, not 41.6%. Removing "
        "agreement on suspicion deletes exactly the evidence that would support pooling, "
        "which biases the answer toward disagreement."
    )
    a("")
    a(
        "**Zero-spread observations are therefore kept in the primary analysis.** Only "
        "provenance-demonstrable duplicates are collapsed, and only in the sensitivity "
        "row below. Assay and publication ids remain an upper bound on independent "
        "replication, never a replicate count."
    )
    a("")

    a("## Within- against between-assay spread, on a matched population")
    a("")
    a(
        "Both quantities are computed from **the same pairs**. A pair qualifies only if "
        "it holds publication fixed, spans more than one assay, and has at least one "
        "assay carrying two or more observations — so within- and between-assay spread "
        "are both computable from that pair alone."
    )
    a("")
    a(
        "A previous revision drew the within-assay figure from every pair and the "
        "between-assay figure from the publication-fixed subset. Those were different "
        "populations, and their difference did not mean what it appeared to."
    )
    a("")
    a(
        f"**Pairs supporting both comparisons: {matched['pairs']:,}** — "
        f"{matched['pairs'] / max(cov['regression_pairs'], 1):.2%} of regression pairs "
        f"and {matched['pairs'] / max(conf['pairs_with_repeats'], 1):.1%} of pairs with "
        "any repeat. Everything in this section rests on that population."
    )
    a("")
    a("| Comparison | n | p10 | median | p90 | share > 1 pKi |")
    a("| --- | --- | --- | --- | --- | --- |")
    a(_dist_row("Within one assay", matched["within"]))
    a(_dist_row("Across assays, publication fixed", matched["between"]))
    a("")
    assoc = matched["between"].median - matched["within"].median
    a(
        f"**These two medians are not weighted alike, and their difference "
        f"({assoc:+.3f} pKi) is not an assay effect.** The within-assay row is a "
        f"distribution over {matched['within'].n:,} *(pair, assay) groups*; the "
        f"between-assay row is a distribution over {matched['between'].n:,} *pairs*. A "
        "pair spanning three qualifying assays contributes three within-assay values "
        "and one between-assay value, so pairs with more assay records are weighted "
        "more heavily on one side of the comparison than the other. The difference of "
        "medians across differently-weighted units is a descriptive contrast, not an "
        "estimate of anything."
    )
    a("")
    a(
        "**This is an association, not a causal estimate, and the wording in the "
        'previous revision ("changing assay alone shifts Ki") overstated it.** Holding '
        "publication fixed removes the between-paper contribution and nothing else. "
        "Remaining confounders inside a single publication include: different compound "
        "batches or lots; different protein constructs, preparations or suppliers; "
        "different sub-experiments reported together; differing numerical precision; and "
        "the assignment of assay ids itself, which is a curation act rather than an "
        "experimental fact. Any of these could produce the same association with no "
        "assay-format effect at all."
    )
    a("")
    a("### Sensitivity to duplicate handling")
    a("")
    a("| Population | Pairs | Within median | Between median | Association |")
    a("| --- | --- | --- | --- | --- |")
    a(
        f"| Primary (all observations kept) | {matched['pairs']:,} | "
        f"{matched['within'].median:.3f} | {matched['between'].median:.3f} | "
        f"**{assoc:+.3f}** |"
    )
    dedup_assoc = dedup["between"].median - dedup["within"].median
    a(
        f"| Potential duplicates collapsed (different cohort) | {dedup['pairs']:,} | "
        f"{dedup['within'].median:.3f} | {dedup['between'].median:.3f} | "
        f"**{dedup_assoc:+.3f}** |"
    )
    a("")
    a(
        f"**The two rows are not the same comparison.** Collapsing potential duplicate "
        f"reports drops {matched['pairs'] - dedup['pairs']:,} of the {matched['pairs']:,} "
        "qualifying pairs, because a pair whose repeats were all identical no longer has "
        "an assay group with two or more observations and stops qualifying. **The "
        "eligible cohort changes**, so the second row describes a different and smaller "
        "population, not the same population measured more carefully."
    )
    a("")
    a(
        f"Read with that caveat, the contrast moves from {assoc:+.3f} to "
        f"{dedup_assoc:+.3f} pKi and changes sign. Whether that reflects duplicate "
        "handling, cohort change, or both cannot be separated here. It is reported "
        "because a quantity this unstable under a defensible preprocessing choice "
        "should not be relied on in either direction — not because the second number "
        "is the better one."
    )
    a("")
    a("The tail, on the primary population:")
    a("")
    a("| Between-assay spread exceeds | Share of qualifying pairs | In Ki terms |")
    a("| --- | --- | --- |")
    for cutoff in TAIL_CUTOFFS:
        share = (
            sum(1 for v in matched_between_values if v > cutoff) / len(matched_between_values)
            if matched_between_values
            else 0.0
        )
        a(f"| {cutoff} pKi | {share:.1%} | {10**cutoff:.0f}x |")
    a("")

    # --------------------------------------- discordant / contradictory handling
    a("## Discordant and contradictory pairs")
    a("")
    a(
        "Both are included in the diagnostic above and identified separately here. They "
        "are excluded from *evaluation*, not from *understanding the data* — excluding "
        "them from the diagnostic would remove exactly the evidence the diagnostic "
        "exists to find."
    )
    a("")
    header = (
        "| Population | Pairs with >= 2 obs | Within-assay median | "
        "Across-assay median | share > 1 pKi |"
    )
    a(header)
    a("| --- | --- | --- | --- | --- |")
    for cmp in report.comparisons:
        wa, ba = cmp.within_assay, cmp.between_assay
        a(
            f"| {cmp.label} | {cmp.pairs_considered:,} | "
            f"{wa.median:.3f} | {ba.median:.3f} | {wa.fraction_over_1:.1%} |"
        )
    a("")
    a(
        "**The change when discordant pairs are removed is circular and must not be "
        "read as reassurance.** Discordant is *defined* as exact spread > 1 pKi, so "
        "removing those pairs mechanically drives every `share > 1 pKi` to 0.0%. That "
        "is arithmetic, not evidence of agreement. The only honest reading is the "
        "all-pairs row."
    )
    a("")

    # ---------------------------------------------------------------- examples
    if report.examples:
        a("## Traceable examples")
        a("")
        a(
            "Drawn from the **matched, publication-fixed** population. Every assay group "
            "shown carries at least two observations, so the claim that observations "
            "agree within an assay rests on actual repeats rather than on a single "
            "measurement trivially having zero spread with itself."
        )
        a("")
        for ex in report.examples:
            meta = ex["meta"]
            a("```")
            a(f"pair_regression {ex['pair_id']}  compound={meta[0]}  target={meta[1]}")
            a(
                f"  n_obs={meta[2]}  p_median={meta[3]:.3f}  p_spread={meta[4]:.3f}  "
                f"discordant={meta[5]}  excluded={meta[6]}"
            )
            a(
                f"  {ex['n_assays']} assays; between-assay spread "
                f"{ex['between_spread']:.3f} pKi; worst within-assay "
                f"{ex['worst_within']:.3f} pKi"
            )
            last_assay = None
            for (
                activity_id,
                assay_id,
                pub_id,
                pki,
                assay_name,
                raw_id,
                value_text,  # noqa: B007 - used below
            ) in ex["observations"]:
                if assay_id != last_assay:
                    a(f"    assay {assay_id}  pub {pub_id}  {assay_name!r}")
                    last_assay = assay_id
                a(f"      activity {activity_id} <- raw_measurement {raw_id}  pKi={float(pki):.3f}")
            a("```")
            a("")

    a("## Decision")
    a("")
    a("### The pre-declared rule, kept as historical evidence")
    a("")
    a(
        "Fixed in `assay_variance.py` before any number was computed, and expressed in "
        "pKi so it means something chemically (0.3 pKi is 2x in Ki, 1.0 is 10x):"
    )
    a("")
    a("```")
    a(f"if median(within-assay spread) >= {av.UNSUITABLE_WITHIN_PKI}:  endpoint unsuitable")
    a(f"elif median(across-assay) - median(within-assay) <= {av.POOL_MARGIN_PKI}:  pool")
    a(f"elif median(within-assay) < {av.HOMOGENEOUS_MAX_WITHIN_PKI}:  restrict to assay groups")
    a("else:  endpoint unsuitable")
    a("```")
    a("")
    a(f"Applied to the all-pairs comparison it returned: **{report.decision}**.")
    a("")
    a("| Margin (pKi) | Verdict |")
    a("| --- | --- |")
    for margin, decision in report.sensitivity:
        a(f"| {margin} | {decision} |")
    a("")
    a(
        "**That verdict is recorded, not relied on.** It was produced by a rule that "
        "compares medians and is blind to the tail, on a population that mixed "
        "matched and unmatched comparisons, before the duplicate question was posed "
        "correctly. A rule cannot validate the thing its failure mode concerns: the "
        "`pool` result is evidence about the rule, not about pooling."
    )
    a("")
    a("### What the corrected analysis supports")
    a("")
    a(
        f"On the matched, publication-fixed population of {matched['pairs']:,} pairs, the "
        f"descriptive contrast is {assoc:+.3f} pKi between differently-weighted units, "
        f"and it changes sign ({dedup_assoc:+.3f}) once potential duplicates are "
        "collapsed and the eligible cohort shifts with them. The evidence base is "
        "small, the contrast is unstable and not an effect estimate, and a publication "
        "effect cannot be estimated at all. **None of that demonstrates that assays "
        "disagree; none of it demonstrates that they agree.**"
    )
    a("")
    a("### Operative status for M6 and beyond")
    a("")
    a(f"```\n{av.OPERATIVE_STATUS}\n```")
    a("")
    a(av.OPERATIVE_STATUS_NOTE)
    a("")
    a(
        "This status is emitted by `seq2lead.profiling.status_banner` into every report "
        "built on pooled Ki, so a downstream number cannot be read without it. It is "
        "not a decision to pool; it is a decision to proceed **provisionally, for "
        "exploratory benchmarking**, with the limitation attached."
    )
    a("")
    a("### Assay-spread audits must be partitioned")
    a("")
    a(
        "The natural next step is to attach each pair's assay-to-assay spread so "
        "downstream code can see which medians rest on agreeing assays. Done naively "
        "that leaks: a spread computed over all observations of a pair summarises "
        "measurements that may land in validation or test, and any filtering, "
        "weighting or feature derived from it would carry held-out information into "
        "the model."
    )
    a("")
    a("The rule for M6, to be enforced in code rather than remembered:")
    a("")
    a(
        "1. **Model-facing spread statistics are computed within the training partition "
        "only.** A pair's spread as seen by a model is the spread of its *training* "
        "observations, even where more exist."
    )
    a(
        "2. **Audit-facing spread statistics may use every observation**, but are "
        "written to reports only and never joined into a feature, a filter or a "
        "sample weight."
    )
    a(
        "3. **The two are stored under different names** so a join cannot confuse them, "
        "and the partition-scoped one carries its `split_version`."
    )
    a(
        "4. **Pairs are the unit of partitioning.** Splitting observations of one pair "
        "across partitions would put a measurement of a test pair into training, which "
        "is the leak this rule exists to prevent."
    )
    a("")

    # ------------------------------------------------------------- limitations
    a("## Limitations")
    a("")
    a("| # | Limitation |")
    a("| --- | --- |")
    a(
        "| 1 | **Assay and publication are confounded in "
        f"{conf['both_vary'] / total_rep:.0%} of repeat pairs.** Only "
        f"{conf['assay_varies_publication_fixed']:,} pairs isolate the assay effect, and "
        f"only {conf['publication_varies_assay_fixed']:,} isolate the publication "
        "effect — too few for the latter to be estimated at all. |"
    )
    a(
        "| 2 | **pH and temperature are ~6% populated** and are excluded. Any "
        "temperature or buffer effect is unmeasured, not absent. |"
    )
    a(
        "| 3 | **Assay ids are not replicate counts.** 41.6% of within-assay groups are "
        "literal restatements. Independent replication is strictly less than the counts "
        "suggest, and how much less cannot be determined from the data. |"
    )
    a(
        "| 4 | **No random-effects model was fitted.** With confounded factors, "
        "restated observations and a covariate at 6% coverage, a variance-components "
        "model would attribute variance to factors the design cannot separate. The "
        "descriptive comparison is what the data supports. |"
    )
    a(
        "| 5 | **Only 15% of pairs have any repeat.** The other 85% are single "
        "measurements whose reproducibility is entirely unknown, and nothing here "
        "licenses an assumption about them. |"
    )
    a(
        "| 6 | **Assay description text was not parsed.** 95.75% of observations have "
        "one, but grouping assays into comparable formats from free text is its own "
        "piece of work and was not attempted; `assay_id` identity is the proxy used. |"
    )
    a("")
    return "\n".join(lines) + "\n"


def write(content: str, path: Path = REPORT_PATH) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path
