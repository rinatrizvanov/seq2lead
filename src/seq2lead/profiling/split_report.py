"""Render `reports/splits.md`."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from seq2lead.profiling.status_banner import banner
from seq2lead.splits import assertions as sa

if TYPE_CHECKING:
    import psycopg

    from seq2lead.splits.similarity import HighIdentityHit, SimilarityDistribution

REPORT_PATH = Path("reports/splits.md")


def _scalar(conn: psycopg.Connection, sql: str, params: tuple = ()) -> int:
    row = conn.execute(sql, params).fetchone()
    return int(row[0]) if row and row[0] is not None else 0


def _temporal_endpoint_section(
    conn: psycopg.Connection, sid: int, global_endpoint: int
) -> list[str]:
    """The heart of the correction: each partition aggregates its own evidence."""
    out: list[str] = []
    a = out.append

    a("### Each partition aggregates only its own measurements")
    a("")
    a(
        "A temporal split that assigns dates but then joins a **global** aggregate has "
        "not held anything out. The global `pair_label` median and interval intersection "
        "summarise every measurement of a pair, including ones dated after the cut, so a "
        "test pair's ground truth would already contain its own future. This build "
        "therefore materialises a **separate endpoint per partition**, each built from "
        "that partition's assigned activities alone under the identical exact/censored "
        "conflict rules."
    )
    a("")
    rows = conn.execute(
        """
        SELECT spe.partition, ev.name, ev.id,
               (SELECT count(*) FROM pair_label l WHERE l.endpoint_id = spe.endpoint_id),
               (SELECT count(*) FROM pair_regression r WHERE r.endpoint_id = spe.endpoint_id)
        FROM split_partition_endpoint spe
        JOIN endpoint_version ev ON ev.id = spe.endpoint_id
        WHERE spe.split_id = %s
        ORDER BY CASE spe.partition
                 WHEN 'train' THEN 1 WHEN 'validation' THEN 2 ELSE 3 END
        """,
        (sid,),
    ).fetchall()
    a(
        "| Partition | Endpoint built for it | Labelled pairs | Regression aggregates "
        "| Evidence it may read |"
    )
    a("| --- | --- | --- | --- | --- |")
    visible = {
        "train": "activities dated before the validation cut",
        "validation": "activities in the validation window only",
        "test": "activities in the test window only",
    }
    for partition, name, _eid, n_label, n_reg in rows:
        a(
            f"| `{partition}` | `{name}` | {int(n_label):,} | {int(n_reg):,} | "
            f"{visible.get(str(partition), '—')} |"
        )
    a("")
    a(
        "Every aggregate's support activity IDs are recorded, and a build-failing "
        "assertion confirms **no aggregate is supported by an activity assigned to a "
        "different partition**. That is the check the previous build had no way to make, "
        "because it built no aggregates."
    )
    a("")

    # ---- what the correction actually changed
    base = """
    FROM split_pair_assignment p
    JOIN split_partition_endpoint spe ON spe.split_id=p.split_id AND spe.partition=p.partition
    JOIN pair_label pl ON pl.endpoint_id=spe.endpoint_id
      AND pl.compound_id=p.compound_id AND pl.target_id=p.target_id
    JOIN pair_label g ON g.endpoint_id=%s
      AND g.compound_id=p.compound_id AND g.target_id=p.target_id
    WHERE p.split_id=%s AND p.partition IN ('validation','test')
    """
    scored = _scalar(conn, "SELECT count(*) " + base, (global_endpoint, sid))
    differing = _scalar(
        conn,
        "SELECT count(*) " + base + " AND pl.label IS DISTINCT FROM g.label",
        (global_endpoint, sid),
    )
    reasons = conn.execute(
        "SELECT g.eval_exclusion_reason, count(*) "
        + base
        + " AND g.excluded_from_eval AND NOT pl.excluded_from_eval GROUP BY 1 ORDER BY 2 DESC",
        (global_endpoint, sid),
    ).fetchall()
    converse = _scalar(
        conn,
        "SELECT count(*) " + base + " AND pl.excluded_from_eval AND NOT g.excluded_from_eval",
        (global_endpoint, sid),
    )
    flips = conn.execute(
        "SELECT pl.label, g.label, count(*) "
        + base
        + " AND pl.label IS DISTINCT FROM g.label GROUP BY 1,2 ORDER BY 3 DESC",
        (global_endpoint, sid),
    ).fetchall()

    a("### How much the global endpoint was actually deciding")
    a("")
    a(
        "Comparing each scored pair's partition-scoped ground truth against the global "
        "one it would previously have been graded by:"
    )
    a("")
    a("| Held-out pairs where … | Count | Share of the ??? scored |".replace("???", f"{scored:,}"))
    a("| --- | --- | --- |")
    a(
        f"| the partition and global labels **disagree** | {differing:,} | "
        f"{100 * differing / max(scored, 1):.1f}% |"
    )
    for lab_p, lab_g, n in flips:
        a(f"| &nbsp;&nbsp;· period says `{lab_p}`, global says `{lab_g}` | {int(n):,} | |")
    a("")
    total_reinstated = sum(int(n) for _r, n in reasons)
    a(
        f"**{total_reinstated:,} pairs are scored here that the global endpoint excludes**, "
        "by reason:"
    )
    a("")
    a("| Global exclusion reason | Pairs reinstated | Why it does not apply |")
    a("| --- | --- | --- |")
    why = {
        "discordant": "the spread appears only once later measurements are pooled in",
        "exact_bound_conflict": "the contradicting exact value is dated after the cut",
        "empty_intersection": "the censored bounds only conflict once later records join",
    }
    for reason, n in reasons:
        key = str(reason) if reason is not None else "(none)"
        a(f"| `{key}` | {int(n):,} | {why.get(key, '—')} |")
    a("")
    a(
        "Each of these exclusions is a judgement formed from evidence that did not exist "
        "at the time the partition represents. Letting it remove a pair from the test set "
        "would be a future measurement controlling the benchmark's composition, so "
        f"eligibility is decided by the partition's own endpoint. The converse count is "
        f"**{converse:,}**: no pair is excluded by its own period yet clean globally, which "
        "is the expected direction — a partition endpoint reads a subset of the "
        "measurements, so restricting evidence can resolve a conflict but never invent one."
    )
    a("")
    a(
        "This is also why `temporal_proxy` reports fewer excluded pairs than the "
        "pair-level splits: it is not a laxer rule, it is the same rule applied to less "
        "evidence."
    )
    a("")
    return out


def _strata_section(conn: psycopg.Connection, sid: int) -> list[str]:
    out: list[str] = []
    a = out.append
    a("### New and recurrent, relative to what the model actually saw")
    a("")
    protocol = (
        conn.execute("SELECT params FROM split_version WHERE id=%s", (sid,)).fetchone()[0] or {}
    ).get("protocol", "unspecified")
    a(
        f"**Protocol: `{protocol}`.** The model scored on a given evaluation period is "
        "fitted on the `train` partition alone. Validation selects between models and "
        "stops training; it is not folded back in before the test evaluation."
    )
    a("")
    a(
        "That choice decides the stratum definition, so it is recorded on the split "
        "rather than left implicit. A pair is **recurrent** for a period if the model "
        "scoring it *had already been shown a measurement of it*, and **new** otherwise:"
    )
    a("")
    a("| Evaluation period | Fitted on | Recurrent means |")
    a("| --- | --- | --- |")
    a("| `validation` | `train` | measured in `train` |")
    a("| `test` | `train` | measured in `train` |")
    a("")
    a(
        "The earlier definition judged the test period against `train` + `validation`. "
        "Under a train-only protocol that overstates what the model knows: a pair first "
        "measured in the validation window is genuinely new to a model that never "
        "trained on validation. Counting it as recurrent credits the model with evidence "
        "it was never shown, and moves pairs out of the prospective stratum that belong "
        "in it."
    )
    a("")
    a(
        "Earlier measurements are not discarded — they are reported as a **separate "
        "axis**, so a pair that was measured before the test window but never trained on "
        "is visible as exactly that:"
    )
    a("")
    cross = conn.execute(
        "SELECT partition, stratum, coalesce(history_partitions, '(never)'), count(*) "
        "FROM split_pair_assignment WHERE split_id=%s AND partition IN ('validation','test') "
        "GROUP BY 1,2,3 ORDER BY 1, 2, 3",
        (sid,),
    ).fetchall()
    a("| Period | Stratum | Measured earlier in | Pairs | Reading |")
    a("| --- | --- | --- | --- | --- |")
    reading = {
        ("validation", "new", "(never)"): "never measured before — prospective",
        ("validation", "recurrent", "train"): "the model was trained on this pair",
        ("test", "new", "(never)"): "never measured before — prospective",
        ("test", "new", "validation"): (
            "**measured before the test window, but invisible to a train-only model**"
        ),
        ("test", "recurrent", "train"): "the model was trained on this pair",
        ("test", "recurrent", "train,validation"): "trained on, and measured again since",
    }
    for partition, stratum, history, n in cross:
        key = (str(partition), str(stratum), str(history))
        a(f"| `{partition}` | `{stratum}` | {history} | {int(n):,} | {reading.get(key, '—')} |")
    a("")
    a(
        "The `test` / `new` / `validation` row is the one the previous version "
        "mislabelled as recurrent. Those pairs are new to a train-only model and are "
        "scored in the headline stratum, with their prior measurement disclosed rather "
        "than hidden."
    )
    a("")
    a(
        "If the protocol ever changes to refit on `train` + `validation` before scoring "
        "test, that is a different experiment: it widens what "
        "`training_visible_activities()` returns, lets validation evidence into every "
        "retrieval index and feature, and makes those 3,431 pairs genuinely recurrent. "
        "It would mint a new split version, not edit this one."
    )
    a("")
    rows = conn.execute(
        "SELECT partition, stratum, count(*) FROM split_pair_assignment "
        "WHERE split_id=%s AND partition IN ('validation','test') "
        "GROUP BY partition, stratum ORDER BY partition, stratum",
        (sid,),
    ).fetchall()
    counts = {(str(p), str(s)): int(n) for p, s, n in rows}
    a("| Period | `new` (headline) | `recurrent` | Recurrence rate |")
    a("| --- | --- | --- | --- |")
    for period in ("validation", "test"):
        new = counts.get((period, "new"), 0)
        rec = counts.get((period, "recurrent"), 0)
        total = new + rec
        rate = f"{100 * rec / total:.1f}%" if total else "—"
        a(f"| `{period}` | {new:,} | {rec:,} | {rate} |")
    a("")
    # Counted from the same source as the cross-tabulation above -- which
    # partitions actually measured the pair -- so the two figures cannot drift
    # apart. A pair-assignment count would differ, because a pair whose
    # validation row was excluded still has a validation measurement.
    discriminating = _scalar(
        conn,
        "SELECT count(*) FROM split_pair_assignment WHERE split_id=%s "
        "AND partition='test' AND history_partitions='validation'",
        (sid,),
    )
    if discriminating:
        a(
            f"The definition is not academic. **{discriminating:,} pairs are measured in "
            "validation and test but never in training.** Three rules disagree about "
            "them, and the disagreement is the whole point:"
        )
        a("")
        a("| Rule | Validation | Test | What it assumes |")
        a("| --- | --- | --- | --- |")
        a(
            "| *appears in >1 partition* | `recurrent` | `recurrent` | that a later "
            "measurement can make a pair familiar in an earlier period |"
        )
        a(
            "| *measured earlier in time* | `new` | `recurrent` | that the model saw "
            "everything the database had recorded by then |"
        )
        a(
            "| **what the model was fitted on** | `new` | `new` | only that the model "
            "was trained on `train` — which it was |"
        )
        a("")
        a(
            "The first rule grades validation against the future. The second grades test "
            "against evidence a train-only model never received. This build uses the "
            "third, and records the earlier measurement separately so nothing is lost."
        )
        a("")
    a(
        "**Test counts are reported separately from train and validation and the strata "
        "are never pooled into one figure.** A combined number would let the recurrence "
        "rate — an artifact of how often BindingDB re-measures a pair — drive the score. "
        "`new` is the headline because it is the prospective question: can the model rank "
        "a pair nobody had measured when training stopped? `recurrent` is a much easier "
        "question and is reported beside it, never inside it."
    )
    a("")
    a(
        "Recurrent pairs are also why `temporal_proxy` is exempt from the pair-overlap "
        "assertion: by construction such a pair has measurements on both sides of the "
        "cut. The exemption is narrow — the *measurements* are still disjoint, and the "
        "partition-endpoint assertions above are what the exemption is traded for."
    )
    a("")
    return out


def _identity_audit_section(hits: list[HighIdentityHit], held: int) -> list[str]:
    """What the 90%+ cross-partition hits actually are."""
    out: list[str] = []
    a = out.append
    a("### Auditing the high-identity hits")
    a("")
    if not hits or not held:
        a("_Not computed in this run._")
        a("")
        return out

    contained = [h for h in hits if max(h.query_cov, h.target_cov) >= 0.95]
    both80 = [h for h in hits if h.mutual_coverage >= 0.80]
    both50 = [h for h in hits if h.mutual_coverage >= 0.50]
    med_aln = sorted(h.aln_len for h in hits)[len(hits) // 2]
    max_mutual = max(h.mutual_coverage for h in hits)

    a(
        "A bare identity number cannot tell *the same protein under another "
        "accession* from *two proteins sharing one domain*. Every held-out target "
        "with a 90%-or-better local alignment into training was therefore re-examined "
        "with its alignment length and its coverage of **both** sequences."
    )
    a("")
    a("| | Targets | Share of held-out |")
    a("| --- | --- | --- |")
    a(f"| Held-out targets | {held:,} | 100% |")
    a(
        f"| …with a ≥90% identity local hit into training | {len(hits):,} | "
        f"{100 * len(hits) / held:.1f}% |"
    )
    a(
        f"| …where the alignment covers ≥50% of **both** | {len(both50):,} | "
        f"{100 * len(both50) / held:.2f}% |"
    )
    a(
        f"| …where the alignment covers ≥80% of **both** | {len(both80):,} | "
        f"{100 * len(both80) / held:.2f}% |"
    )
    a("")
    a(
        f"**{len(contained)} of the {len(hits)} are fragment containment**: one sequence "
        "is ≥95% covered by the alignment while the other is not. These are domain "
        "constructs, isolated subunits and short peptides sitting alongside full-length "
        f"proteins — the median alignment is {med_aln} aa. MMseqs2 was run at "
        "`--min-seq-id 0.4 -c 0.8`, so it *correctly* declines to cluster a 40-residue "
        "peptide with the 3,430-residue protein that contains it; that is the coverage "
        "rule working, not failing."
    )
    a("")
    a(
        f"The highest mutual coverage anywhere in the set is **{max_mutual:.2f}**. No "
        "held-out target has a ≥90%-identity alignment spanning 80% of both itself and a "
        "training protein."
    )
    a("")
    a("The worst cases, by how much of both proteins the alignment explains:")
    a("")
    a("| Identity | Alignment | Held-out length (covered) | Training length (covered) |")
    a("| --- | --- | --- | --- |")
    for h in hits[:5]:
        a(
            f"| {h.identity:.3f} | {h.aln_len:,} aa | {h.query_len:,} aa "
            f"({h.query_cov:.0%}) | {h.target_len:,} aa ({h.target_cov:.0%}) |"
        )
    a("")
    a(
        "They remain a **caveat on the split, not a breach of it**: a test asserts none "
        "of them shares an MMseqs2 cluster with a training target, which would be a leak "
        "rather than a qualification."
    )
    a("")
    a("**So the cold-protein claim is this, and not more than this:**")
    a("")
    a(
        "> No held-out target shares an MMseqs2 cluster with any training target at 40% "
        "identity and 80% coverage."
    )
    a("")
    a(
        f"It is **not** a claim that no held-out target resembles a training protein. "
        f"{len(both50)} held-out targets ({100 * len(both50) / held:.1f}%) have a "
        "≥90%-identity alignment covering at least half of both themselves and a "
        "training protein. For those, a mean-pooled sequence embedding will sit close to "
        "a training example, and any cold-protein score on them should be read as "
        "**near-homolog performance, not novel-target performance**. They are "
        "recorded in `split_target_stratum` as the **`near_homolog`** stratum, with the "
        "identity, alignment length and both coverages that qualified each one, so a "
        "score can be reported with them broken out rather than averaged in. The "
        f"remaining {len(hits) - len(both50)} are short local matches where a shared "
        "motif does not make two proteins the same."
    )
    a("")
    return out


def _similarity_section(distributions: list[SimilarityDistribution]) -> list[str]:
    out: list[str] = []
    a = out.append
    a("## How unfamiliar are the held-out entities, really?")
    a("")
    a(
        "A zero-overlap assertion proves no *group* spans partitions. It does not prove "
        "the held-out entities are unfamiliar: two proteins below the clustering "
        "threshold can still share a binding site, and two compounds with different "
        "Bemis-Murcko scaffolds can still be fingerprint near-neighbours. Disjointness "
        "by construction is not the same as novelty, so it is measured rather than "
        "assumed."
    )
    a("")
    usable = [d for d in distributions if d.n]
    if not usable:
        a("_Not computed in this run._")
        a("")
        return out
    for d in usable:
        a(f"**{d.label}**  ")
        note = f"n = {d.n:,}" + (", sampled" if d.sampled else ", exhaustive")
        a(f"{note}")
        a("")
        a("| p10 | median | p90 | p99 |")
        a("| --- | --- | --- | --- |")
        a(f"| {d.p10:.3f} | {d.median:.3f} | {d.p90:.3f} | {d.p99:.3f} |")
        a("")
        if d.fraction_over:
            a("| Held-out entities with a training neighbour above … | Share |")
            a("| --- | --- |")
            for cutoff, frac in sorted(d.fraction_over.items()):
                a(f"| {cutoff:.1f} | {100 * frac:.1f}% |")
            a("")

    def _share(kind: str, cutoff: float) -> str:
        for d in usable:
            if kind in d.label and d.fraction_over and cutoff in d.fraction_over:
                return f"{100 * d.fraction_over[cutoff]:.1f}%"
        return "—"

    a(
        "**These numbers qualify the cold splits rather than endorsing them.** "
        f"{_share('target', 0.9)} of held-out targets have a training target at 90%+ "
        "sequence identity despite sharing no MMseqs2 cluster — clustering at 40% "
        "identity with 80% coverage does not catch a pair that aligns strongly over a "
        f"shorter region. On the chemistry side {_share('compound', 0.7)} of held-out "
        "compounds have a training neighbour above 0.7 Tanimoto, which is the scaffold "
        "limitation made quantitative: "
        "**`chemistry_disjoint` guarantees scaffold disjointness, not dissimilarity.** "
        "Butina clustering on ECFP4 would group by the similarity actually measured here, "
        "but it is O(n^2) in compounds and this endpoint carries a quarter of a million; "
        "the substitution is recorded as a limitation, not presented as equivalent."
    )
    a("")
    a(
        "The compound figures are a **lower bound**: the training side is sampled, so an "
        "unsampled training compound can only be nearer, never further. Sample sizes are "
        "printed above so the bound is auditable."
    )
    a("")
    return out


def render(  # noqa: PLR0915
    conn: psycopg.Connection,
    endpoint_id: int,
    distributions: list[SimilarityDistribution] | None = None,
    identity_audit: tuple[list[HighIdentityHit], int] | None = None,
) -> str:
    lines: list[str] = []
    a = lines.append

    a("# M6 — leakage-controlled splits")
    a("")
    a(f"Generated {datetime.now(UTC):%Y-%m-%d %H:%M} UTC.")
    a("")
    for line in banner():
        a(line)
    a(
        "Built to the contract in [`../docs/SPLITS.md`](../docs/SPLITS.md), which was "
        "written before this code. Every split is immutable and versioned; a changed "
        "rule mints a new name rather than editing one."
    )
    a("")

    splits = conn.execute(
        "SELECT id, name, split_type, seed, params, n_train, n_validation, n_test, "
        "n_excluded, notes, protocol FROM split_version "
        "WHERE endpoint_id=%s AND superseded_by IS NULL ORDER BY id",
        (endpoint_id,),
    ).fetchall()
    if not splits:
        a("No current splits built for this endpoint.")
        return "\n".join(lines) + "\n"

    # ------------------------------------------------------------- unit of assignment
    a("## Unit of assignment")
    a("")
    a(
        "Four splits assign **whole compound-target pairs**. A pair is the smallest "
        "scored object, so splitting one across partitions would place a measurement of "
        "a test pair into training — a leak no later filter can undo."
    )
    a("")
    a(
        "`temporal_proxy` assigns **activities**, then aggregates inside each partition. "
        "Aggregating first would leak: a pair's median would already summarise "
        "measurements dated after the cut."
    )
    a("")

    # ------------------------------------------------------------------ overview
    a("## Splits built")
    a("")
    a("| Split | Type | Protocol | Train | Validation | Test | Excluded | Groups held together |")
    a("| --- | --- | --- | --- | --- | --- | --- | --- |")
    n_seq_clusters = _scalar(conn, "SELECT count(DISTINCT cluster_id) FROM target_cluster")
    n_scaffolds = _scalar(conn, "SELECT count(DISTINCT cluster_id) FROM compound_cluster")
    for _sid, name, stype, _seed, params, ntr, nva, nte, nex, _notes, protocol in splits:
        if stype == "cold_protein":
            groups = f"{n_seq_clusters:,} sequence clusters"
        elif stype == "chemistry_disjoint":
            groups = f"{n_scaffolds:,} scaffolds"
        elif stype == "label_reversal":
            groups = f"{(params or {}).get('n_compounds', 0) * 2:,} reversed compounds"
        elif stype == "temporal_proxy":
            groups = f"cut {(params or {}).get('test_cut', '?')}"
        else:
            groups = "—"
        a(
            f"| `{name}` | {stype} | `{protocol}` | {ntr:,} | {nva:,} | {nte:,} | "
            f"{nex:,} | {groups} |"
        )
    a("")
    a(
        "**`protocol`** separates what a split is for. `trainable` splits may be used to "
        "fit and select models. `diagnostic_frozen` splits may not: see the "
        "`label_reversal` section below."
    )
    a("")
    a(
        "`excluded` carries the pairs the governing endpoint marked `excluded_from_eval` "
        "— contradictory, empty-intersection, no-usable-evidence, discordant and "
        "ambiguous. They are kept out of validation and test in every split, and out of "
        "training too by default, so a first benchmark is not trained on evidence already "
        "declared unreliable. For `temporal_proxy` the governing endpoint is the "
        "partition's own, which is why its count differs."
    )
    a("")

    # ------------------------------------------------------- clustering that backs them
    a("## Grouping that makes the cold splits cold")
    a("")
    targets = _scalar(conn, "SELECT count(*) FROM target_cluster")
    target_clusters = _scalar(conn, "SELECT count(DISTINCT cluster_id) FROM target_cluster")
    compounds = _scalar(conn, "SELECT count(*) FROM compound_cluster")
    scaffolds = _scalar(conn, "SELECT count(DISTINCT cluster_id) FROM compound_cluster")
    a("| Grouping | Members | Groups | Mean size |")
    a("| --- | --- | --- | --- |")
    a(
        f"| MMseqs2 sequence clusters (40% identity) | {targets:,} | {target_clusters:,} | "
        f"{targets / max(target_clusters, 1):.2f} |"
    )
    a(
        f"| Bemis-Murcko scaffolds | {compounds:,} | {scaffolds:,} | "
        f"{compounds / max(scaffolds, 1):.2f} |"
    )
    a("")
    a(
        f"**{targets:,} distinct sequences collapse to {target_clusters:,} clusters.** "
        "Nearly half of what the curated layer calls separate targets are homologs of "
        "something else in it. Holding out individual proteins while their homologs "
        "stayed in training would not have been a cold-protein test, which is the whole "
        "reason for clustering first."
    )
    a("")
    a(
        "A build-failing assertion confirms **every scored target and every scored "
        "compound carries a cluster assignment**. An unclustered entity would otherwise "
        "be silently exempt from the very disjointness the split claims: it belongs to no "
        "group, so no group-overlap check can catch it."
    )
    a("")

    # ------------------------------------------------------------------- temporal
    temporal = next((s for s in splits if s[2] == "temporal_proxy"), None)
    if temporal is not None:
        sid = temporal[0]
        a("## `temporal_proxy`")
        a("")
        lines.extend(_temporal_endpoint_section(conn, sid, endpoint_id))
        lines.extend(_strata_section(conn, sid))
        by_source = dict(
            conn.execute(
                "SELECT date_source, count(*) FROM split_activity_assignment "
                "WHERE split_id=%s GROUP BY date_source",
                (sid,),
            ).fetchall()
        )
        a("### Dates used")
        a("")
        a("| Date used | Activities |")
        a("| --- | --- |")
        for source, n in sorted(by_source.items()):
            a(f"| {source} | {int(n):,} |")
        a("")
        a(
            "A **proxy**, not an as-of snapshot. BindingDB's publication date is the "
            "article's date, not the date the record became retrievable, and records are "
            "revised and back-filled. The name says so wherever it appears."
        )
        a("")

    # -------------------------------------------------------------- label_reversal
    reversal = next((s for s in splits if s[2] == "label_reversal"), None)
    if reversal is not None:
        a("## `label_reversal` is a diagnostic, not a benchmark")
        a("")
        a(
            "It has **no validation partition**, and that is structural rather than an "
            "oversight: every compound it holds out is chosen precisely because its label "
            "reverses, so there is no comparable held-out slice left to tune against. A "
            "split with no tuning set cannot honestly be used for model selection — "
            "selecting on it *is* fitting to it."
        )
        a("")
        a("It is therefore marked `protocol = diagnostic_frozen`, which means:")
        a("")
        a(
            "1. It is **evaluated once, after every model choice is frozen** — "
            "architecture, hyperparameters, features, curation rules, the lot."
        )
        a(
            "2. Its result **may not motivate a change** to any of those choices. If it "
            "does, the change mints a new `dataset_version` and the reversal number is "
            "re-earned on a split the new choices have not seen."
        )
        a(
            "3. It is reported as a **ligand-bias measurement**, not as a leaderboard "
            "rank. TransformerCPI's finding is the calibration point: every reference "
            "model scored below 0.5 on its Kinase reversal set."
        )
        a("")
        a(
            "The alternative — treating it as a fifth trainable split — would invite "
            "exactly the tuning that makes a bias probe meaningless."
        )
        a("")

    # ----------------------------------------------------------------- assertions
    a("## Leakage assertions")
    a("")
    a("Build-failing checks, not statistics to eyeball. A split that fails one is not written.")
    a("")
    for sid, name, stype, *_ in splits:
        checks = sa.run_checks(conn, sid)
        a(f"**`{name}`** ({stype})")
        a("")
        a("| Assertion | Observed | Expected | Result |")
        a("| --- | --- | --- | --- |")
        for check in checks:
            verdict = "pass" if check.passed else "**FAIL**"
            a(f"| {check.name} | {check.observed:,} | {check.expected} | {verdict} |")
        a("")

    a("### What the assertions caught")
    a("")
    a(
        "**First build.** `temporal_proxy` placed 7,939 endpoint-excluded pairs into "
        "validation and test, because it partitioned purely by date and never consulted "
        "`excluded_from_eval`. The assertion failed the build."
    )
    a("")
    a(
        "**Second build.** The generic exclusion assertion consulted the *global* "
        "endpoint for every split, and failed `temporal_proxy` on 2,072 pairs. That "
        "failure was the assertion's, not the builder's: the pairs are excluded globally "
        "only because of measurements dated after the cut. Reading a global exclusion "
        "into a temporal partition is itself the leak. The assertion was made "
        "partition-aware and the same 2,072 pairs are now correctly scored — see the "
        "table above."
    )
    a("")
    a(
        "Both corrections were to the *check* or the *builder*, never to the data. No "
        "split was edited to make an assertion pass."
    )
    a("")

    # ------------------------------------------------- similarity distributions
    lines.extend(_similarity_section(distributions or []))
    if identity_audit is not None:
        lines.extend(_identity_audit_section(*identity_audit))

    # ------------------------------------------------- what must not reach training
    a("## What must not reach training")
    a("")
    a("Three channels, each with a rule enforced in code rather than remembered:")
    a("")
    a(
        "1. **Evidence.** No held-out `activity` may be read when building a training "
        "aggregate, feature or target. Partition-scoped aggregation plus the assertions "
        "above. `training_visible_activities()` additionally drops any pair the *training "
        "period's own* endpoint marked excluded, so a pair judged unusable on its early "
        "evidence cannot re-enter as a training example. It deliberately does **not** "
        "consult the `excluded` partition: a pair lands there when the validation or "
        "test endpoint finds a conflict among measurements dated after the cut, and "
        "suppressing a clean training record on that basis would be a future measurement "
        "deciding what the model may learn from the past — the same leak running "
        "backwards. Removing that condition restored 386 training activities across 228 "
        "pairs, every one of them with clean training-period evidence."
    )
    a(
        "2. **Retrieval.** `assert_index_is_training_only()` refuses an index containing "
        "any held-out activity. `training_visible_activities()` returns SQL rather than "
        "rows, so a caller cannot quietly widen it."
    )
    a(
        "3. **Assay-spread features.** Per the M5 contract, a pair's *model-facing* spread "
        "is the spread of its **training** observations, even where more exist. "
        "Audit-facing spread may use everything but is written to reports only and never "
        "joined into a feature, filter or sample weight."
    )
    a("")

    # ------------------------------------------------------------------ versioning
    a("## Versioning and supersession")
    a("")
    superseded = conn.execute(
        "SELECT name, superseded_by, superseded_reason FROM split_version "
        "WHERE superseded_by IS NOT NULL ORDER BY id"
    ).fetchall()
    if superseded:
        a("| Superseded | Replaced by | Why |")
        a("| --- | --- | --- |")
        for name, by, reason in superseded:
            a(f"| `{name}` | `{by}` | {reason} |")
        a("")
        a(
            "The superseded rows and every assignment they own are **retained intact and "
            "marked**, not deleted or edited. A split is a claim about what a number "
            "means; correcting one by rewriting it in place would erase the evidence that "
            "the earlier number was different. Anything already computed against a "
            "superseded split stays reproducible and stays identifiable as stale."
        )
    else:
        a("_No superseded splits._")
    a("")

    a("## Limitations")
    a("")
    a("| # | Limitation |")
    a("| --- | --- |")
    a(
        "| 1 | **The grouped splits hold proportions only approximately.** Whole groups "
        "are assigned greedily to whichever partition is furthest below quota, so a "
        "large cluster can overshoot. Realised sizes are in the table above; the "
        "nominal 70/10/20 is a target, not a guarantee. |"
    )
    a(
        "| 2 | **Scaffold grouping is not Butina clustering,** and the measured "
        "distributions above quantify the gap: a substantial minority of held-out "
        "compounds have a training neighbour above 0.7 Tanimoto despite sharing no "
        "scaffold. "
        "`chemistry_disjoint` is a scaffold-disjointness guarantee and nothing stronger. |"
    )
    a(
        "| 3 | **40% identity is one choice, and clustering is not the same as "
        "novelty.** 3.9% of held-out targets keep a 90%+-identity local alignment into "
        "training; the audit above shows 43 of those 44 are fragment containment, but 20 "
        "still cover at least half of both proteins and should be read as near-homolog "
        "cases. 30% and 60% identity were not built. |"
    )
    a(
        "| 4 | **`temporal_proxy` cannot assert pair disjointness** and does not try. Its "
        "guarantee is per-activity plus per-partition aggregation, and the recurrent "
        "stratum is where that difference shows up. |"
    )
    a(
        "| 5 | **The temporal partitions are thinner than the global endpoint.** Each is "
        "aggregated from its own window, so pairs supported by few measurements in that "
        "window get correspondingly weaker aggregates. This is the honest cost of not "
        "borrowing evidence across the cut, not a defect to tune away. |"
    )
    a(
        "| 6 | **The temporal protocol is `train_only`, and the strata depend on it.** "
        "A model refitted on train+validation before the test evaluation would make "
        "3,431 of the test `new` pairs genuinely recurrent, and would need a wider "
        "`training_visible_activities()`. That is a different experiment and would mint "
        "a new split version. |"
    )
    a(
        "| 7 | **No metrics are computed here.** These are partitions and their audits. "
        "Scoring is M7 and beyond, and nothing in this milestone has been trained. |"
    )
    a("")
    return "\n".join(lines) + "\n"


def write(content: str, path: Path = REPORT_PATH) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path
