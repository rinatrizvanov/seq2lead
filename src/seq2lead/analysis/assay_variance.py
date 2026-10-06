"""M5: does Ki pool across assays?

The question M4 could not answer. `pair_regression` takes a median over every
exact Ki observation for a pair, which assumes those observations are measuring
the same quantity. If changing assay format shifts Ki systematically, that median
is averaging across conditions rather than across noise.

**What this module does not assume.** Two observations sharing an `assay_id` are
the same assay *record*, which is not the same as an independent biological
replicate — BindingDB rows can restate one experiment. Counts of assays and
publications are therefore treated as an upper bound on independent replication,
never as a replicate count. Nothing here is a random-effects model; the data does
not support one, and §confounding says why.

**Order of operations.** Covariate missingness is measured *first*. A variance
decomposition over covariates that are absent would produce numbers with no
referent, so the missingness table gates what can be attempted at all.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import psycopg

ANALYSIS_VERSION = "m5/v2"

#: The status M6 and everything after it must honour. Not "pooling is validated":
#: the pre-declared rule returned `pool`, but that verdict was produced by a rule
#: whose failure mode was discovered afterwards, so it cannot serve as validation
#: of the thing it failed to test.
OPERATIVE_STATUS = "provisional_pooled_for_exploratory_benchmark"

OPERATIVE_STATUS_NOTE = (
    "Ki is pooled across assays **provisionally, for exploratory benchmarking only**. "
    "The pre-declared decision rule returned `pool`, but it compared medians and was "
    "blind to a tail in which a quarter of assay-spanning pairs differ by more than "
    "tenfold. Pooling is therefore an operating assumption carried forward under "
    "protest, not a validated conclusion. Any result computed on pooled Ki inherits "
    "this limitation and must state it."
)

#: Pre-declared decision rule. Fixed here, before the numbers are seen, and
#: deliberately expressed in pKi units so it means something chemically:
#: 0.3 pKi is a factor of 2 in Ki, 0.5 is ~3x, 1.0 is 10x.
POOL_MARGIN_PKI = 0.3
HOMOGENEOUS_MAX_WITHIN_PKI = 0.5
UNSUITABLE_WITHIN_PKI = 1.0

DECISION_POOL = "pool"
DECISION_RESTRICT = "restrict_to_assay_homogeneous_groups"
DECISION_UNSUITABLE = "endpoint_unsuitable"

#: Reported alongside the decision so its fragility is visible.
SENSITIVITY_MARGINS = (0.2, 0.3, 0.5)


class SupersededEndpoint(RuntimeError):
    """The named endpoint has been superseded and must not be analysed by default."""


@dataclass
class Distribution:
    n: int = 0
    p10: float | None = None
    median: float | None = None
    p90: float | None = None
    mean: float | None = None
    fraction_over_1: float | None = None

    @classmethod
    def of(cls, values: list[float]) -> Distribution:
        if not values:
            return cls()
        ordered = sorted(values)
        return cls(
            n=len(ordered),
            p10=ordered[int(0.10 * (len(ordered) - 1))],
            median=statistics.median(ordered),
            p90=ordered[int(0.90 * (len(ordered) - 1))],
            mean=statistics.fmean(ordered),
            fraction_over_1=sum(1 for v in ordered if v > 1.0) / len(ordered),
        )


@dataclass
class SpreadComparison:
    """Within-assay against between-assay spread, on one population of pairs."""

    label: str
    pairs_considered: int
    within_assay: Distribution
    between_assay: Distribution
    within_publication: Distribution
    between_publication: Distribution

    @property
    def assay_excess(self) -> float | None:
        """How much median spread changing assay adds over staying inside one."""
        if self.within_assay.median is None or self.between_assay.median is None:
            return None
        return self.between_assay.median - self.within_assay.median


@dataclass
class VarianceReport:
    endpoint_id: int
    endpoint_name: str
    threshold: float
    coverage: dict[str, int] = field(default_factory=dict)
    missingness: list[tuple[str, int, int]] = field(default_factory=list)
    confounding: dict[str, int] = field(default_factory=dict)
    comparisons: list[SpreadComparison] = field(default_factory=list)
    examples: list[dict] = field(default_factory=list)
    decision: str = ""
    decision_detail: str = ""
    sensitivity: list[tuple[float, str]] = field(default_factory=list)


# --------------------------------------------------------------------------- sql

_OBS_DDL = """
CREATE TEMP TABLE IF NOT EXISTS m5_obs AS
SELECT r.id            AS pair_id,
       r.is_discordant,
       r.excluded_from_eval,
       a.id            AS activity_id,
       a.assay_id,
       a.publication_id,
       a.value_text,
       9.0 - log(a.value_numeric) AS pki
FROM pair_regression r
JOIN pair_regression_support s ON s.pair_id = r.id
JOIN activity a ON a.id = s.activity_id
WHERE r.endpoint_id = %(endpoint)s;
"""

#: Every query below reads this. The observation set is ~540k rows and was
#: previously rebuilt by a CTE on each call, which made the analysis quadratic in
#: the number of questions asked.
_OBS_CTE = "WITH obs AS (SELECT * FROM m5_obs) "


def materialize(conn: psycopg.Connection, endpoint_id: int) -> int:
    """Build the per-connection observation table. Safe to call repeatedly."""
    conn.execute("DROP TABLE IF EXISTS m5_obs")
    conn.execute(_OBS_DDL, {"endpoint": endpoint_id})
    conn.execute("CREATE INDEX ON m5_obs (pair_id)")
    conn.execute("CREATE INDEX ON m5_obs (pair_id, assay_id)")
    conn.execute("ANALYZE m5_obs")
    row = conn.execute("SELECT count(*) FROM m5_obs").fetchone()
    return int(row[0]) if row else 0


def _population_filter(population: str) -> str:
    if population == "all":
        return ""
    if population == "eval_eligible":
        return "AND NOT excluded_from_eval"
    if population == "no_discordant":
        return "AND NOT is_discordant"
    raise ValueError(f"unknown population {population!r}")


def resolve_endpoint(
    conn: psycopg.Connection, name: str, *, allow_superseded: bool = False
) -> tuple[int, str, float]:
    """Look up an endpoint, refusing superseded versions unless told otherwise."""
    row = conn.execute(
        "SELECT id, name, threshold_pki, superseded_by, superseded_reason "
        "FROM endpoint_version WHERE name = %s",
        (name,),
    ).fetchone()
    if row is None:
        raise LookupError(f"endpoint {name!r} does not exist")
    if row[3] and not allow_superseded:
        raise SupersededEndpoint(
            f"endpoint {name!r} was superseded by {row[3]!r} and must not be analysed.\n"
            f"Reason recorded: {row[4]}\n"
            f"Analyse {row[3]!r} instead, or pass allow_superseded=True to study the "
            "superseded version deliberately."
        )
    return int(row[0]), str(row[1]), float(row[2])


def collect_coverage(conn: psycopg.Connection, endpoint_id: int) -> dict[str, int]:
    """Denominators. Everything later is a fraction of one of these."""
    p = {"endpoint": endpoint_id}
    out: dict[str, int] = {}
    out["label_pairs"] = conn.execute(
        "SELECT count(*) FROM pair_label WHERE endpoint_id=%(endpoint)s", p
    ).fetchone()[0]
    out["regression_pairs"] = conn.execute(
        "SELECT count(*) FROM pair_regression WHERE endpoint_id=%(endpoint)s", p
    ).fetchone()[0]
    out["repeat_pairs"] = conn.execute(
        "SELECT count(*) FROM pair_regression WHERE endpoint_id=%(endpoint)s AND n_obs >= 2", p
    ).fetchone()[0]
    out["multi_assay_pairs"] = conn.execute(
        "SELECT count(*) FROM pair_regression WHERE endpoint_id=%(endpoint)s AND n_assays > 1", p
    ).fetchone()[0]
    out["multi_publication_pairs"] = conn.execute(
        "SELECT count(*) FROM pair_regression WHERE endpoint_id=%(endpoint)s "
        "AND n_publications > 1",
        p,
    ).fetchone()[0]
    out["discordant_pairs"] = conn.execute(
        "SELECT count(*) FROM pair_regression WHERE endpoint_id=%(endpoint)s AND is_discordant", p
    ).fetchone()[0]
    # Broken out by status: an earlier revision reported the whole `status <> 'ok'`
    # total as "contradictory", but it also contains empty intersections and pairs
    # with no usable evidence, which are different failures.
    for status in ("exact_bound_conflict", "empty_intersection", "no_usable_evidence"):
        out[f"status_{status}"] = conn.execute(
            "SELECT count(*) FROM pair_label WHERE endpoint_id=%(endpoint)s AND status=%(s)s",
            {**p, "s": status},
        ).fetchone()[0]
    out["unlabelled_pairs"] = conn.execute(
        "SELECT count(*) FROM pair_label WHERE endpoint_id=%(endpoint)s AND status <> 'ok'", p
    ).fetchone()[0]
    out["pairs_with_assay_text"] = conn.execute(
        _OBS_CTE + "SELECT count(DISTINCT pair_id) FROM obs o "
        "JOIN assay y ON y.id = o.assay_id "
        "WHERE coalesce(btrim(y.description_text), '') <> ''",
        p,
    ).fetchone()[0]
    return out


def collect_missingness(conn: psycopg.Connection, endpoint_id: int) -> list[tuple[str, int, int]]:
    """Per-observation covariate availability, measured before any decomposition."""
    p = {"endpoint": endpoint_id}
    total = conn.execute(_OBS_CTE + "SELECT count(*) FROM obs", p).fetchone()[0]
    checks = [
        ("assay_id linked", "o.assay_id IS NOT NULL"),
        (
            "assay description non-empty",
            "EXISTS (SELECT 1 FROM assay y WHERE y.id=o.assay_id "
            "AND coalesce(btrim(y.description_text),'') <> '')",
        ),
        (
            "assay name non-empty",
            "EXISTS (SELECT 1 FROM assay y WHERE y.id=o.assay_id "
            "AND coalesce(btrim(y.name_text),'') <> '')",
        ),
        ("publication linked", "o.publication_id IS NOT NULL"),
        (
            "pH recorded",
            "EXISTS (SELECT 1 FROM activity a WHERE a.id=o.activity_id "
            "AND coalesce(btrim(a.ph_text),'') <> '')",
        ),
        (
            "temperature recorded",
            "EXISTS (SELECT 1 FROM activity a WHERE a.id=o.activity_id "
            "AND coalesce(btrim(a.temp_c_text),'') <> '')",
        ),
        (
            "curation source recorded",
            "EXISTS (SELECT 1 FROM activity a WHERE a.id=o.activity_id "
            "AND coalesce(btrim(a.curation_source),'') <> '')",
        ),
        (
            "publication date recorded",
            "EXISTS (SELECT 1 FROM activity a WHERE a.id=o.activity_id "
            "AND coalesce(btrim(a.publication_date),'') <> '')",
        ),
    ]
    rows: list[tuple[str, int, int]] = []
    for label, predicate in checks:
        n = conn.execute(
            _OBS_CTE + f"SELECT count(*) FROM obs o WHERE {predicate}",  # noqa: S608
            p,
        ).fetchone()[0]
        rows.append((label, int(n), int(total)))
    return rows


def collect_confounding(conn: psycopg.Connection, endpoint_id: int) -> dict[str, int]:
    """Which effects a pair can distinguish — reconciling **every** repeated pair.

    An earlier version used `count(DISTINCT assay_id)`, which ignores NULLs, so
    pairs whose observations carry no assay id fell into none of the buckets and
    the table did not sum to the population. Missing assay ids are now their own
    category rather than a silent omission.
    """
    row = conn.execute(
        _OBS_CTE
        + """
        , per_pair AS (
            SELECT pair_id,
                   count(*)                       AS n_obs,
                   count(assay_id)                AS n_obs_with_assay,
                   count(DISTINCT assay_id)       AS n_assay,
                   count(DISTINCT publication_id) AS n_pub
            FROM obs GROUP BY pair_id
        )
        SELECT count(*) FILTER (WHERE n_obs >= 2),
               count(*) FILTER (WHERE n_obs >= 2 AND n_obs_with_assay = 0),
               count(*) FILTER (WHERE n_obs >= 2 AND n_obs_with_assay > 0
                                AND n_obs_with_assay < n_obs),
               count(*) FILTER (WHERE n_obs >= 2 AND n_obs_with_assay = n_obs
                                AND n_assay > 1 AND n_pub = 1),
               count(*) FILTER (WHERE n_obs >= 2 AND n_obs_with_assay = n_obs
                                AND n_assay = 1 AND n_pub > 1),
               count(*) FILTER (WHERE n_obs >= 2 AND n_obs_with_assay = n_obs
                                AND n_assay > 1 AND n_pub > 1),
               count(*) FILTER (WHERE n_obs >= 2 AND n_obs_with_assay = n_obs
                                AND n_assay = 1 AND n_pub = 1)
        FROM per_pair
        """,
        {"endpoint": endpoint_id},
    ).fetchone()
    buckets = {
        "pairs_with_repeats": int(row[0]),
        "no_assay_id_at_all": int(row[1]),
        "assay_id_on_some_only": int(row[2]),
        "assay_varies_publication_fixed": int(row[3]),
        "publication_varies_assay_fixed": int(row[4]),
        "both_vary": int(row[5]),
        "neither_varies": int(row[6]),
    }
    buckets["bucket_sum"] = sum(v for k, v in buckets.items() if k != "pairs_with_repeats")
    return buckets


def _build_groups(conn: psycopg.Connection, *, deduplicate: bool) -> None:
    """Materialize per-(pair, assay) groups and the qualifying pair set.

    Built once as temp tables. Expressing this as a CTE chain meant every
    sub-query rebuilt the whole thing, which turned a seconds-long analysis into
    a ten-minute one.
    """
    source = "m5_obs"
    conn.execute("DROP TABLE IF EXISTS m5_src")
    if deduplicate:
        # Collapse only demonstrable redundant reports: identical value under the
        # same assay and publication for the same pair.
        conn.execute(
            "CREATE TEMP TABLE m5_src AS SELECT DISTINCT pair_id, assay_id, "
            "publication_id, value_text, pki FROM m5_obs WHERE assay_id IS NOT NULL"
        )
    else:
        conn.execute(
            "CREATE TEMP TABLE m5_src AS SELECT pair_id, assay_id, publication_id, "
            "value_text, pki FROM m5_obs WHERE assay_id IS NOT NULL"
        )
    conn.execute("CREATE INDEX ON m5_src (pair_id, assay_id)")
    conn.execute("ANALYZE m5_src")
    source = "m5_src"

    conn.execute("DROP TABLE IF EXISTS m5_grp")
    conn.execute(
        f"""
        CREATE TEMP TABLE m5_grp AS
        SELECT pair_id, assay_id, count(*) AS n,
               max(pki) - min(pki) AS within,
               percentile_cont(0.5) WITHIN GROUP (ORDER BY pki) AS med
        FROM {source} GROUP BY 1, 2
        """  # noqa: S608
    )
    conn.execute("CREATE INDEX ON m5_grp (pair_id)")
    conn.execute("DROP TABLE IF EXISTS m5_qualified")
    conn.execute(
        f"""
        CREATE TEMP TABLE m5_qualified AS
        SELECT g.pair_id FROM m5_grp g
        JOIN (SELECT pair_id, count(DISTINCT assay_id) AS na,
                     count(DISTINCT publication_id) AS np
              FROM {source} GROUP BY pair_id) pp ON pp.pair_id = g.pair_id
        WHERE pp.na > 1 AND pp.np = 1 AND g.n >= 2
        GROUP BY g.pair_id
        """  # noqa: S608
    )
    conn.execute("CREATE INDEX ON m5_qualified (pair_id)")
    conn.execute("ANALYZE m5_grp")
    conn.execute("ANALYZE m5_qualified")


def _matched(conn: psycopg.Connection, *, deduplicate: bool) -> dict[str, object]:
    _build_groups(conn, deduplicate=deduplicate)
    n_pairs = conn.execute("SELECT count(*) FROM m5_qualified").fetchone()[0]
    within = [
        float(r[0])
        for r in conn.execute(
            "SELECT g.within FROM m5_grp g JOIN m5_qualified q ON q.pair_id = g.pair_id "
            "WHERE g.n >= 2"
        ).fetchall()
    ]
    between = [
        float(r[0])
        for r in conn.execute(
            "SELECT max(g.med) - min(g.med) FROM m5_grp g "
            "JOIN m5_qualified q ON q.pair_id = g.pair_id GROUP BY g.pair_id"
        ).fetchall()
    ]
    return {
        "pairs": int(n_pairs),
        "within": Distribution.of(within),
        "between": Distribution.of(between),
    }


def matched_between_values(conn: psycopg.Connection) -> list[float]:
    """Raw between-assay spreads for the matched population, for tail reporting.

    Assumes `matched_comparison` has already built the group tables.
    """
    rows = conn.execute(
        "SELECT max(g.med) - min(g.med) FROM m5_grp g "
        "JOIN m5_qualified q ON q.pair_id = g.pair_id GROUP BY g.pair_id"
    ).fetchall()
    return [float(r[0]) for r in rows]


def matched_comparison(conn: psycopg.Connection, endpoint_id: int) -> dict[str, object]:
    """Within- and between-assay spread measured on **the same pairs**.

    The earlier comparison drew its within-assay figure from every pair and its
    between-assay figure from the publication-fixed subset, so the two numbers
    described different populations and their difference meant little.

    A pair qualifies only if it holds publication fixed, spans more than one
    assay, and has at least one assay carrying two or more observations — so both
    quantities are computable from that pair alone. **Zero-spread observations are
    kept**: an identical value is not proof of restatement, and deleting agreement
    on suspicion would bias the comparison toward disagreement.
    """
    del endpoint_id  # observations are already materialized
    return _matched(conn, deduplicate=False)


def _spreads(
    conn: psycopg.Connection, endpoint_id: int, population: str, key: str
) -> tuple[list[float], list[float]]:
    """Return (within-group spreads, between-group spreads) for `key`."""
    p = {"endpoint": endpoint_id}
    flt = _population_filter(population)
    within = conn.execute(
        _OBS_CTE
        + f"""
        SELECT max(pki) - min(pki)
        FROM obs
        WHERE {key} IS NOT NULL {flt}
        GROUP BY pair_id, {key}
        HAVING count(*) >= 2
        """,  # noqa: S608
        p,
    ).fetchall()
    between = conn.execute(
        _OBS_CTE
        + f"""
        , per_group AS (
            SELECT pair_id, {key} AS grp,
                   percentile_cont(0.5) WITHIN GROUP (ORDER BY pki) AS med
            FROM obs WHERE {key} IS NOT NULL {flt}
            GROUP BY pair_id, {key}
        )
        SELECT max(med) - min(med) FROM per_group
        GROUP BY pair_id HAVING count(*) >= 2
        """,  # noqa: S608
        p,
    ).fetchall()
    return [float(r[0]) for r in within], [float(r[0]) for r in between]


def compare_spreads(
    conn: psycopg.Connection, endpoint_id: int, population: str, label: str
) -> SpreadComparison:
    wa, ba = _spreads(conn, endpoint_id, population, "assay_id")
    wp, bp = _spreads(conn, endpoint_id, population, "publication_id")
    flt = _population_filter(population)
    considered = conn.execute(
        _OBS_CTE + f"SELECT count(*) FROM (SELECT pair_id FROM obs WHERE TRUE {flt} "  # noqa: S608
        "GROUP BY pair_id HAVING count(*) >= 2) x",
        {"endpoint": endpoint_id},
    ).fetchone()[0]
    return SpreadComparison(
        label=label,
        pairs_considered=int(considered),
        within_assay=Distribution.of(wa),
        between_assay=Distribution.of(ba),
        within_publication=Distribution.of(wp),
        between_publication=Distribution.of(bp),
    )


def decide(comparison: SpreadComparison, margin: float = POOL_MARGIN_PKI) -> tuple[str, str]:
    """Apply the pre-declared rule. Uses only measurement metadata, never model results."""
    within = comparison.within_assay.median
    between = comparison.between_assay.median
    if within is None or between is None:
        return (
            DECISION_UNSUITABLE,
            "Neither within-assay nor between-assay spread could be measured, so "
            "pooling cannot be justified by evidence.",
        )
    if within >= UNSUITABLE_WITHIN_PKI:
        return (
            DECISION_UNSUITABLE,
            f"Replicates inside a single assay already differ by a median of {within:.2f} "
            f"pKi (>= {UNSUITABLE_WITHIN_PKI}), a factor of {10**within:.0f} in Ki. The "
            "endpoint is not reproducible enough to benchmark regardless of pooling.",
        )
    excess = between - within
    if excess <= margin:
        return (
            DECISION_POOL,
            f"Changing assay adds {excess:+.2f} pKi to the median spread "
            f"({between:.2f} between vs {within:.2f} within), within the pre-declared "
            f"margin of {margin} pKi.",
        )
    if within < HOMOGENEOUS_MAX_WITHIN_PKI:
        return (
            DECISION_RESTRICT,
            f"Changing assay adds {excess:+.2f} pKi to the median spread "
            f"({between:.2f} between vs {within:.2f} within), beyond the {margin} pKi "
            f"margin, while within-assay agreement stays tight ({within:.2f} < "
            f"{HOMOGENEOUS_MAX_WITHIN_PKI}). Pool only inside an assay.",
        )
    return (
        DECISION_UNSUITABLE,
        f"Between-assay spread exceeds the margin ({excess:+.2f} pKi) and within-assay "
        f"agreement is itself poor ({within:.2f} pKi), so no grouping rescues it.",
    )


def collect_examples(conn: psycopg.Connection, endpoint_id: int, limit: int = 3) -> list[dict]:
    """Traceable pairs from the matched, publication-fixed population.

    Every assay group shown carries at least two observations, so the claim that
    observations agree *within* an assay rests on actual repeats rather than on a
    single measurement having zero spread with itself.
    """
    p = {"endpoint": endpoint_id, "limit": limit}
    pairs = conn.execute(
        _OBS_CTE
        + """
        , with_assay AS (SELECT * FROM obs WHERE assay_id IS NOT NULL)
        , per_pair AS (
            SELECT pair_id, count(DISTINCT assay_id) na, count(DISTINCT publication_id) np
            FROM with_assay GROUP BY pair_id
        )
        , grp AS (
            SELECT pair_id, assay_id, count(*) n, max(pki) - min(pki) AS within,
                   percentile_cont(0.5) WITHIN GROUP (ORDER BY pki) AS med
            FROM with_assay GROUP BY 1, 2
        )
        SELECT g.pair_id, count(*) AS n_assays,
               max(g.med) - min(g.med) AS between_spread, max(g.within) AS worst_within
        FROM grp g JOIN per_pair pp ON pp.pair_id = g.pair_id
        WHERE pp.na > 1 AND pp.np = 1 AND g.n >= 2
        GROUP BY g.pair_id
        HAVING count(*) >= 2 AND max(g.med) - min(g.med) > 1.0 AND max(g.within) < 0.3
        ORDER BY max(g.med) - min(g.med) DESC
        LIMIT %(limit)s
        """,
        p,
    ).fetchall()

    out: list[dict] = []
    for pair_id, n_assays, between, worst_within in pairs:
        obs = conn.execute(
            _OBS_CTE + "SELECT o.activity_id, o.assay_id, o.publication_id, o.pki, "
            "left(coalesce(y.name_text, ''), 48), a.raw_measurement_id, a.value_text "
            "FROM obs o LEFT JOIN assay y ON y.id = o.assay_id "
            "JOIN activity a ON a.id = o.activity_id "
            "WHERE o.pair_id = %(pair)s AND o.assay_id IS NOT NULL "
            "ORDER BY o.assay_id, o.pki",
            {"endpoint": endpoint_id, "pair": pair_id},
        ).fetchall()
        meta = conn.execute(
            "SELECT compound_id, target_id, n_obs, p_median, p_spread, is_discordant, "
            "eval_exclusion_reason FROM pair_regression WHERE id=%s",
            (pair_id,),
        ).fetchone()
        out.append(
            {
                "pair_id": pair_id,
                "n_assays": n_assays,
                "between_spread": float(between),
                "worst_within": float(worst_within),
                "meta": meta,
                "observations": obs,
            }
        )
    return out


def run(conn: psycopg.Connection, name: str, *, allow_superseded: bool = False) -> VarianceReport:
    endpoint_id, resolved, threshold = resolve_endpoint(
        conn, name, allow_superseded=allow_superseded
    )
    materialize(conn, endpoint_id)
    report = VarianceReport(endpoint_id=endpoint_id, endpoint_name=resolved, threshold=threshold)
    report.coverage = collect_coverage(conn, endpoint_id)
    report.missingness = collect_missingness(conn, endpoint_id)
    report.confounding = collect_confounding(conn, endpoint_id)
    for population, label in (
        ("all", "All pairs (diagnostic, includes discordant and contradictory)"),
        ("no_discordant", "Discordant pairs removed"),
        ("eval_eligible", "Evaluation-eligible pairs only"),
    ):
        report.comparisons.append(compare_spreads(conn, endpoint_id, population, label))
    report.examples = collect_examples(conn, endpoint_id)

    primary = report.comparisons[0]
    report.decision, report.decision_detail = decide(primary)
    report.sensitivity = [(margin, decide(primary, margin)[0]) for margin in SENSITIVITY_MARGINS]
    return report


# ------------------------------------------------- duplicate-aware refinements
#
# The median spread across all "replicate" groups is near zero, which looks like
# excellent agreement and is not. 41.6% of within-assay groups have a spread of
# exactly zero: BindingDB restates the same measurement across rows, and those
# restatements are not independent observations. Left in, they drag every median
# toward zero and make pooling look safe by construction.
#
# Two refinements follow. Both make the test *stricter*, never more permissive.


def duplicate_structure(conn: psycopg.Connection, endpoint_id: int) -> dict[str, int]:
    """Separate *demonstrable* restatement from merely identical values.

    A zero spread is not by itself evidence of restatement: two independent
    measurements can agree. What provenance can demonstrate is a redundant
    *report* — the same value, under the same assay and the same publication,
    appearing on more than one row. Those are one measurement written twice.

    Identical values that differ in assay or publication are left alone: they may
    be genuine agreement, and removing them would quietly strengthen the case for
    pooling by deleting the evidence for it.
    """
    row = conn.execute(
        _OBS_CTE
        + """
        , v AS (
            SELECT o.pair_id, o.assay_id, o.publication_id, a.value_text
            FROM obs o JOIN activity a ON a.id = o.activity_id
        )
        , keyed AS (
            SELECT pair_id, assay_id, publication_id, value_text, count(*) AS n
            FROM v GROUP BY 1, 2, 3, 4
        )
        SELECT (SELECT count(*) FROM v),
               COALESCE(sum(n - 1) FILTER (WHERE n > 1), 0),
               count(*) FILTER (WHERE n > 1)
        FROM keyed
        """,
        {"endpoint": endpoint_id},
    ).fetchone()
    zero = conn.execute(
        _OBS_CTE
        + """
        SELECT count(*), count(*) FILTER (WHERE spread = 0)
        FROM (SELECT pair_id, assay_id, max(pki) - min(pki) AS spread FROM obs
              WHERE assay_id IS NOT NULL GROUP BY 1, 2 HAVING count(*) >= 2) g
        """,
        {"endpoint": endpoint_id},
    ).fetchone()
    return {
        "observations": int(row[0]),
        "redundant_reports": int(row[1]),
        "keys_with_redundancy": int(row[2]),
        "within_assay_groups": int(zero[0]),
        "groups_with_zero_spread": int(zero[1]),
    }


def deduplicated_matched_comparison(
    conn: psycopg.Connection, endpoint_id: int
) -> dict[str, object]:
    """Sensitivity analysis: the matched comparison with redundant reports collapsed.

    Only observations that provenance shows to be the same report are removed —
    identical value, same assay, same publication, same pair. Identical values
    that differ in assay or publication are kept, because those may be genuine
    agreement and removing them would quietly manufacture support for pooling.
    """
    del endpoint_id
    return _matched(conn, deduplicate=True)


def nonzero_within_assay(conn: psycopg.Connection, endpoint_id: int) -> Distribution:
    """Within-assay spread among groups that actually differ."""
    rows = conn.execute(
        _OBS_CTE
        + """
        SELECT max(pki) - min(pki) FROM obs WHERE assay_id IS NOT NULL
        GROUP BY pair_id, assay_id HAVING count(*) >= 2 AND max(pki) - min(pki) > 0
        """,
        {"endpoint": endpoint_id},
    ).fetchall()
    return Distribution.of([float(r[0]) for r in rows])


def identifiable_assay_effect(conn: psycopg.Connection, endpoint_id: int) -> Distribution:
    """Assay-to-assay spread where the publication is held fixed.

    This is the only clean estimate available: everywhere else an assay change is
    confounded with a publication change, so the two effects cannot be told apart.
    """
    rows = conn.execute(
        _OBS_CTE
        + """
        , pp AS (
            SELECT pair_id, count(DISTINCT assay_id) AS na,
                   count(DISTINCT publication_id) AS np
            FROM obs GROUP BY pair_id
        )
        , clean AS (
            SELECT o.* FROM obs o JOIN pp ON pp.pair_id = o.pair_id
            WHERE pp.na > 1 AND pp.np = 1 AND o.assay_id IS NOT NULL
        )
        , grp AS (
            SELECT pair_id, assay_id,
                   percentile_cont(0.5) WITHIN GROUP (ORDER BY pki) AS med
            FROM clean GROUP BY 1, 2
        )
        SELECT max(med) - min(med) FROM grp GROUP BY pair_id HAVING count(*) >= 2
        """,
        {"endpoint": endpoint_id},
    ).fetchall()
    return Distribution.of([float(r[0]) for r in rows])


def fraction_over(conn: psycopg.Connection, endpoint_id: int, cutoff: float) -> float:
    """Share of identifiable assay comparisons exceeding `cutoff` pKi."""
    dist = identifiable_assay_effect(conn, endpoint_id)
    if not dist.n:
        return 0.0
    rows = conn.execute(
        _OBS_CTE
        + """
        , pp AS (SELECT pair_id, count(DISTINCT assay_id) na,
                        count(DISTINCT publication_id) np FROM obs GROUP BY pair_id)
        , clean AS (SELECT o.* FROM obs o JOIN pp ON pp.pair_id = o.pair_id
                    WHERE pp.na > 1 AND pp.np = 1 AND o.assay_id IS NOT NULL)
        , grp AS (SELECT pair_id, assay_id,
                  percentile_cont(0.5) WITHIN GROUP (ORDER BY pki) med FROM clean GROUP BY 1, 2)
        , spread AS (SELECT max(med) - min(med) AS s FROM grp GROUP BY pair_id
                     HAVING count(*) >= 2)
        SELECT count(*) FILTER (WHERE s > %(cutoff)s)::float / count(*) FROM spread
        """,
        {"endpoint": endpoint_id, "cutoff": cutoff},
    ).fetchone()
    return float(rows[0]) if rows and rows[0] is not None else 0.0
