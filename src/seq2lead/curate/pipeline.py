"""Build the curated layer from the pinned raw releases.

**Restartable and memory-bounded.** Curation walks `raw_measurement.id` ascending
in batches, and each batch is one transaction that also advances
`curation_run.last_raw_id`. A crash rolls that batch back and the next run
resumes from the last committed id, so nothing is done twice and nothing is
skipped. Memory is bounded by the batch, not the release: no structure is held
for 3.2M rows, and `compound_source` doubles as the standardization cache so a
restart never re-standardizes work already committed.

**Reconciliation.** Every raw row in the release ends as either one or more
`activity` rows or exactly one `curation_exclusion` row. A row carrying both Ki
and IC50 produces two activity rows — the types are never merged — and the
`(raw_measurement_id, measurement_type)` unique key makes a double-write
impossible.
"""

from __future__ import annotations

import time
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from seq2lead.curate.parse import (
    CHAINS_KEY,
    HTML_DECODE_VERSION,
    MEASUREMENT_COLUMNS,
    SEQUENCE_KEY,
    VALUE_UNIT,
    decode_entities,
    parse_chain_count,
    parse_value,
    sequence_sha256,
    structure_sha256,
)
from seq2lead.curate.standardizer import STANDARDIZER_VERSION, standardize_batch
from seq2lead.db.m3_schema import M3_INDEX_SQL

if TYPE_CHECKING:
    import psycopg

# v2: inclusive and exclusive bounds are no longer collapsed.
# v3: compound identity keyed on the structure, not BindingDB's stereo-
#     insensitive InChI Key; all target aliases and organisms recorded.
#: `activity.assay_join_status` vocabulary. Named because the export counts
#: against these values, and an inline literal in two modules is a defect
#: waiting to happen -- the first version of the export counted `"joined"`,
#: which this pipeline never writes, and so reported zero matches.
ASSAY_MATCHED = "matched"
ASSAY_NO_RSID_MATCH = "no_rsid_match"

CURATOR_VERSION = f"m3/v3+{STANDARDIZER_VERSION}"

INCHIKEY_KEY = "Ligand InChI Key"
SMILES_KEY = "Ligand SMILES"
RSID_KEY = "BindingDB Reactant_set_id"
ORGANISM_KEY = "Target Source Organism According to Curator or DataSource"
UNIPROT_KEY = "UniProt (SwissProt) Primary ID of Target Chain 1"

ACTIVITY_COLUMNS = (
    "source_release_id",
    "raw_measurement_id",
    "reactant_set_id",
    "compound_id",
    "source_structure_sha256",
    "target_id",
    "assay_id",
    "assay_join_status",
    "publication_id",
    "measurement_type",
    "relation",
    "relation_raw",
    "bound_inclusive",
    "value_text",
    "value_numeric",
    "value_unit",
    "ph_text",
    "temp_c_text",
    "curation_source",
    "publication_date",
    "bindingdb_date",
    "n_protein_chains",
    "in_benchmark_scope",
    "scope_reason",
    "curator_version",
)


@dataclass
class CurationCounters:
    rows_seen: int = 0
    rows_curated: int = 0
    rows_excluded: int = 0
    activities: int = 0
    exclusions_by_rule: dict[str, int] = field(default_factory=dict)
    scope_by_reason: dict[str, int] = field(default_factory=dict)
    join_status: dict[str, int] = field(default_factory=dict)
    standardized: int = 0
    seconds: float = 0.0

    def bump(self, bucket: dict[str, int], key: str) -> None:
        bucket[key] = bucket.get(key, 0) + 1


# --------------------------------------------------------------------- assays


def build_assays(
    conn: psycopg.Connection, assays_release: int, rsid_release: int
) -> tuple[int, int]:
    """Populate `assay` and `assay_link` from the two mapping artifacts.

    Both the raw and entity-decoded forms of the assay text are stored, so the
    transform is reversible and auditable.
    """
    rows = conn.execute(
        "SELECT payload->>'ENTRYID', payload->>'ASSAYID', payload->>'ASSAY_NAME', "
        "payload->>'DESCRIPTION' FROM raw_record WHERE source_release_id = %s",
        (assays_release,),
    ).fetchall()

    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO assay (source_release_id, entry_id, assay_id_src, name_raw, "
            "description_raw, name_text, description_text, decode_version) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s) "
            "ON CONFLICT (source_release_id, entry_id, assay_id_src) DO NOTHING",
            [
                (
                    assays_release,
                    r[0],
                    r[1],
                    r[2],
                    r[3],
                    decode_entities(r[2]),
                    decode_entities(r[3]),
                    HTML_DECODE_VERSION,
                )
                for r in rows
            ],
        )

    # Scoped to both release ids on purpose: `raw_record` holds two artifacts and
    # an unscoped join would silently cross them.
    linked = conn.execute(
        """
        INSERT INTO assay_link (reactant_set_id, assay_id)
        SELECT m.payload->>'REACTANT_SET_ID', a.id
        FROM raw_record m
        JOIN assay a
          ON a.source_release_id = %s
         AND a.entry_id     = split_part(m.payload->>'ENTRYID_ASSAYID', '_', 1)
         AND a.assay_id_src = split_part(m.payload->>'ENTRYID_ASSAYID', '_', 2)
        WHERE m.source_release_id = %s
        ON CONFLICT (reactant_set_id) DO NOTHING
        """,
        (assays_release, rsid_release),
    ).rowcount
    return len(rows), linked


# ------------------------------------------------------------------ resolvers


def _resolve_compounds(
    conn: psycopg.Connection,
    pool: ProcessPoolExecutor | None,
    wanted: dict[str, tuple[str, str]],
    n_workers: int = 1,
) -> tuple[dict[str, int], int]:
    """Map structure hash -> compound id, standardizing only what is new.

    Keyed on the SMILES itself. BindingDB's `Ligand InChI Key` cannot serve as
    identity: it is stereo-insensitive, so enantiomers and epimers share one key
    while standardizing to different parents. Keying on it made a raw row inherit
    whichever structure happened to be seen first.

    `wanted` maps sha256(SMILES) -> (smiles, source_inchikey).
    """
    if not wanted:
        return {}, 0

    keys = list(wanted)
    known = dict(
        conn.execute(
            "SELECT source_smiles_sha256, compound_id FROM compound_source "
            "WHERE standardizer_version = %s AND source_smiles_sha256 = ANY(%s)",
            (STANDARDIZER_VERSION, keys),
        ).fetchall()
    )
    missing = [(k, wanted[k][0]) for k in keys if k not in known]
    if not missing:
        return known, 0

    if pool is not None and len(missing) > 64:
        chunk = max(1, len(missing) // max(n_workers * 4, 1))
        shards = [missing[i : i + chunk] for i in range(0, len(missing), chunk)]
        results = [item for shard in pool.map(standardize_batch, shards) for item in shard]
    else:
        results = standardize_batch(missing)

    usable = [(k, s) for k, s in results if s is not None]
    if usable:
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO compound (inchikey, canonical_smiles, standardizer_version, "
                "n_heavy_atoms) VALUES (%s,%s,%s,%s) "
                "ON CONFLICT (inchikey, standardizer_version) DO NOTHING",
                [
                    (s.inchikey, s.canonical_smiles, STANDARDIZER_VERSION, s.n_heavy_atoms)
                    for _, s in usable
                ],
            )
        ids = dict(
            conn.execute(
                "SELECT inchikey, id FROM compound WHERE standardizer_version = %s "
                "AND inchikey = ANY(%s)",
                (STANDARDIZER_VERSION, [s.inchikey for _, s in usable]),
            ).fetchall()
        )
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO compound_source (source_smiles_sha256, standardizer_version, "
                "source_smiles, source_inchikey, compound_id) VALUES (%s,%s,%s,%s,%s) "
                "ON CONFLICT (source_smiles_sha256, standardizer_version) DO NOTHING",
                [
                    (k, STANDARDIZER_VERSION, wanted[k][0], wanted[k][1], ids[s.inchikey])
                    for k, s in usable
                ],
            )
        for k, s in usable:
            known[k] = ids[s.inchikey]
    return known, len(missing)


def _resolve_targets(
    conn: psycopg.Connection, wanted: dict[str, tuple[str, str | None, str | None]]
) -> dict[str, int]:
    """Map sequence SHA-256 -> target id, recording *all* aliases and organisms.

    The previous version attached aliases only when a sequence was first created,
    so a sequence later seen with a second UniProt id kept only the first. 22
    sequences carry more than one id and 1,863 carry more than one organism
    annotation, so the first sighting is not authoritative and is not treated as
    such: `target.organism` stays NULL when annotations disagree, and every
    observed value is kept in `target_organism`.
    """
    if not wanted:
        return {}
    keys = list(wanted)
    known = dict(
        conn.execute(
            "SELECT sequence_sha256, id FROM target WHERE sequence_sha256 = ANY(%s)", (keys,)
        ).fetchall()
    )
    missing = [k for k in keys if k not in known]
    if missing:
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO target (sequence_sha256, sequence, length) "
                "VALUES (%s,%s,%s) ON CONFLICT (sequence_sha256) DO NOTHING",
                [(k, wanted[k][0], len(wanted[k][0])) for k in missing],
            )
        known.update(
            dict(
                conn.execute(
                    "SELECT sequence_sha256, id FROM target WHERE sequence_sha256 = ANY(%s)",
                    (missing,),
                ).fetchall()
            )
        )

    # Every sighting, not only the first.
    aliases = [(known[k], "UniProt", wanted[k][2]) for k in keys if wanted[k][2] and k in known]
    if aliases:
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO target_alias (target_id, source, source_id) VALUES (%s,%s,%s) "
                "ON CONFLICT DO NOTHING",
                aliases,
            )
    organisms = [(known[k], wanted[k][1]) for k in keys if wanted[k][1] and k in known]
    if organisms:
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO target_organism (target_id, organism, n_rows) VALUES (%s,%s,1) "
                "ON CONFLICT (target_id, organism) DO UPDATE "
                "SET n_rows = target_organism.n_rows + 1",
                organisms,
            )
    return known


def _resolve_publications(
    conn: psycopg.Connection, wanted: set[tuple[str, str, str, str]]
) -> dict[tuple[str, str, str, str], int]:
    if not wanted:
        return {}
    rows = list(wanted)
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO publication (pmid, doi, patent_number, publication_date) "
            "VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING",
            rows,
        )
    found = conn.execute(
        "SELECT pmid, doi, patent_number, publication_date, id FROM publication "
        "WHERE (pmid, doi, patent_number, publication_date) IN "
        "(SELECT * FROM unnest(%s::text[], %s::text[], %s::text[], %s::text[]))",
        ([r[0] for r in rows], [r[1] for r in rows], [r[2] for r in rows], [r[3] for r in rows]),
    ).fetchall()
    return {(f[0], f[1], f[2], f[3]): f[4] for f in found}


# ------------------------------------------------------------------- batching


def _fetch_batch(
    conn: psycopg.Connection, release_id: int, after_id: int, size: int
) -> list[tuple[int, dict[str, str]]]:
    rows = conn.execute(
        "SELECT id, payload FROM raw_measurement WHERE source_release_id = %s "
        "AND id > %s ORDER BY id LIMIT %s",
        (release_id, after_id, size),
    ).fetchall()
    return [(int(r[0]), r[1]) for r in rows]


def _row_scope(chains: int | None) -> tuple[bool, str | None]:
    """Single-protein benchmark scope. Out-of-scope rows are flagged, not dropped."""
    if chains is None:
        return False, "unknown_chain_count"
    if chains != 1:
        return False, f"multi_chain:{chains}"
    return True, None


def _process_batch(
    conn: psycopg.Connection,
    release_id: int,
    rows: list[tuple[int, dict[str, str]]],
    pool: ProcessPoolExecutor | None,
    n_workers: int,
    counters: CurationCounters,
) -> None:
    structures: dict[str, tuple[str, str]] = {}
    sequences: dict[str, tuple[str, str | None, str | None]] = {}
    pubs: set[tuple[str, str, str, str]] = set()
    for _, payload in rows:
        key, smiles = payload.get(INCHIKEY_KEY), payload.get(SMILES_KEY)
        if smiles:
            # Keyed on the structure. Two rows sharing BindingDB's InChI Key but
            # carrying different SMILES are different compounds, not one.
            structures.setdefault(structure_sha256(smiles), (smiles, key or ""))
        seq = payload.get(SEQUENCE_KEY)
        if seq:
            sequences.setdefault(
                sequence_sha256(seq), (seq, payload.get(ORGANISM_KEY), payload.get(UNIPROT_KEY))
            )
        pubs.add(
            (
                payload.get("PMID", "") or "",
                payload.get("Article DOI", "") or "",
                payload.get("Patent Number", "") or "",
                payload.get("Date of publication", "") or "",
            )
        )

    compounds, standardized = _resolve_compounds(conn, pool, structures, n_workers)
    counters.standardized += standardized
    targets = _resolve_targets(conn, sequences)
    publications = _resolve_publications(conn, pubs)

    rsids = [payload.get(RSID_KEY) for _, payload in rows if payload.get(RSID_KEY)]
    links = dict(
        conn.execute(
            "SELECT reactant_set_id, assay_id FROM assay_link WHERE reactant_set_id = ANY(%s)",
            (rsids,),
        ).fetchall()
    )

    activities: list[tuple] = []
    exclusions: list[tuple] = []

    for raw_id, payload in rows:
        counters.rows_seen += 1
        rsid = payload.get(RSID_KEY) or ""
        key, smiles = payload.get(INCHIKEY_KEY), payload.get(SMILES_KEY)
        struct_hash = structure_sha256(smiles) if smiles else None
        seq = payload.get(SEQUENCE_KEY)

        measured = [
            (mtype, parsed)
            for mtype, column in MEASUREMENT_COLUMNS.items()
            if (parsed := parse_value(payload.get(column))) is not None
        ]

        rule: str | None = None
        if not smiles:
            rule, detail = "missing_structure", "no Ligand SMILES"
        elif struct_hash not in compounds:
            rule, detail = "invalid_smiles", f"RDKit could not standardize: {smiles[:120]}"
        elif not seq:
            rule, detail = "missing_target_sequence", "chain 1 sequence empty"
        elif not measured:
            rule, detail = "no_measurement_value", "no Ki/IC50/Kd/EC50 value present"

        if rule is not None:
            counters.rows_excluded += 1
            counters.bump(counters.exclusions_by_rule, rule)
            exclusions.append((release_id, raw_id, rule, detail, CURATOR_VERSION))
            continue

        target_id = targets[sequence_sha256(seq)]
        assay_id = links.get(rsid)
        status = ASSAY_MATCHED if assay_id is not None else ASSAY_NO_RSID_MATCH
        counters.bump(counters.join_status, status)

        chains = parse_chain_count(payload.get(CHAINS_KEY))
        in_scope, scope_reason = _row_scope(chains)
        if scope_reason:
            counters.bump(counters.scope_by_reason, scope_reason.split(":")[0])

        pub_id = publications.get(
            (
                payload.get("PMID", "") or "",
                payload.get("Article DOI", "") or "",
                payload.get("Patent Number", "") or "",
                payload.get("Date of publication", "") or "",
            )
        )

        for mtype, parsed in measured:
            activities.append(
                (
                    release_id,
                    raw_id,
                    rsid,
                    compounds[struct_hash],
                    struct_hash,
                    target_id,
                    assay_id,
                    status,
                    pub_id,
                    mtype,
                    parsed.relation,
                    parsed.relation_raw,
                    parsed.bound_inclusive,
                    parsed.value_text,
                    parsed.value_numeric,
                    VALUE_UNIT,
                    payload.get("pH"),
                    payload.get("Temp (C)"),
                    payload.get("Curation/DataSource"),
                    payload.get("Date of publication"),
                    payload.get("Date in BindingDB"),
                    chains,
                    in_scope,
                    scope_reason,
                    CURATOR_VERSION,
                )
            )
        counters.rows_curated += 1
        counters.activities += len(measured)

    if activities:
        columns = ", ".join(ACTIVITY_COLUMNS)
        with conn.cursor() as cur, cur.copy(f"COPY activity ({columns}) FROM STDIN") as cp:
            for record in activities:
                cp.write_row(record)
    if exclusions:
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO curation_exclusion (source_release_id, raw_measurement_id, "
                "rule_code, detail, curator_version) VALUES (%s,%s,%s,%s,%s) "
                "ON CONFLICT (raw_measurement_id, curator_version) DO NOTHING",
                exclusions,
            )


def curate_release(
    transaction_factory,
    release_id: int,
    *,
    batch_size: int = 20_000,
    workers: int = 1,
    max_batches: int | None = None,
    progress=None,
) -> CurationCounters:
    """Curate a raw release into the derived layer. Resumes where it left off."""
    counters = CurationCounters()
    started = time.perf_counter()

    with transaction_factory() as conn:
        conn.execute(
            "INSERT INTO curation_run (source_release_id, curator_version) VALUES (%s,%s) "
            "ON CONFLICT (source_release_id, curator_version) DO NOTHING",
            (release_id, CURATOR_VERSION),
        )
        state = conn.execute(
            "SELECT last_raw_id, rows_seen, rows_curated, rows_excluded, activities_written "
            "FROM curation_run WHERE source_release_id=%s AND curator_version=%s",
            (release_id, CURATOR_VERSION),
        ).fetchone()
    last_id = int(state[0]) if state else 0
    prior = (int(state[1]), int(state[2]), int(state[3]), int(state[4])) if state else (0, 0, 0, 0)

    pool = ProcessPoolExecutor(max_workers=workers) if workers > 1 else None
    try:
        batches = 0
        while max_batches is None or batches < max_batches:
            with transaction_factory() as conn:
                rows = _fetch_batch(conn, release_id, last_id, batch_size)
                if not rows:
                    conn.execute(
                        "UPDATE curation_run SET finished_at=now(), updated_at=now() "
                        "WHERE source_release_id=%s AND curator_version=%s",
                        (release_id, CURATOR_VERSION),
                    )
                    break
                _process_batch(conn, release_id, rows, pool, workers, counters)
                last_id = rows[-1][0]
                conn.execute(
                    "UPDATE curation_run SET last_raw_id=%s, rows_seen=%s, rows_curated=%s, "
                    "rows_excluded=%s, activities_written=%s, updated_at=now() "
                    "WHERE source_release_id=%s AND curator_version=%s",
                    (
                        last_id,
                        prior[0] + counters.rows_seen,
                        prior[1] + counters.rows_curated,
                        prior[2] + counters.rows_excluded,
                        prior[3] + counters.activities,
                        release_id,
                        CURATOR_VERSION,
                    ),
                )
            batches += 1
            if progress is not None:
                progress(counters, last_id)
    finally:
        if pool is not None:
            pool.shutdown()

    counters.seconds = time.perf_counter() - started
    return counters


def build_indexes(conn: psycopg.Connection) -> dict[str, float]:
    timings: dict[str, float] = {}
    for name, statement in M3_INDEX_SQL.items():
        t0 = time.perf_counter()
        conn.execute(statement)
        timings[name] = time.perf_counter() - t0
    return timings


def finalize_targets(conn: psycopg.Connection) -> dict[str, int]:
    """Summarize each target's annotations without electing a winner.

    `target.organism` is populated only where the sequence carries exactly one
    organism annotation. Where annotations disagree it stays NULL and every
    observed value remains in `target_organism`: picking the first sighting would
    be an arbitrary choice presented as a fact.
    """
    conn.execute(
        """
        UPDATE target t SET
            n_organisms   = COALESCE(o.n, 0),
            n_uniprot_ids = COALESCE(a.n, 0),
            organism      = CASE WHEN COALESCE(o.n, 0) = 1 THEN o.only_one ELSE NULL END
        FROM (SELECT id FROM target) base
        LEFT JOIN (
            SELECT target_id, count(*) AS n, min(organism) AS only_one
            FROM target_organism GROUP BY target_id
        ) o ON o.target_id = base.id
        LEFT JOIN (
            SELECT target_id, count(DISTINCT source_id) AS n
            FROM target_alias WHERE source = 'UniProt' GROUP BY target_id
        ) a ON a.target_id = base.id
        WHERE t.id = base.id
        """
    )
    row = conn.execute(
        "SELECT count(*) FILTER (WHERE n_organisms > 1), "
        "count(*) FILTER (WHERE n_uniprot_ids > 1), "
        "count(*) FILTER (WHERE organism IS NULL), count(*) FROM target"
    ).fetchone()
    assert row is not None
    return {
        "organism_conflicts": int(row[0]),
        "uniprot_conflicts": int(row[1]),
        "organism_unset": int(row[2]),
        "targets": int(row[3]),
    }


def clear_derived(conn: psycopg.Connection, release_id: int) -> dict[str, int]:
    """Drop the derived M3 layer for a release so it can be rebuilt.

    Only derived data: the pinned raw releases are never touched, so the rebuild
    reads exactly the same checksummed bytes. Used when an identity fix changes
    compound or target ids and the layer must be regenerated rather than patched.
    """
    cleared: dict[str, int] = {}
    for table in ("activity", "curation_exclusion", "curation_run"):
        cleared[table] = conn.execute(
            f"DELETE FROM {table} WHERE source_release_id = %s",  # noqa: S608
            (release_id,),
        ).rowcount
    for table in ("compound_source", "compound", "target_alias", "target_organism"):
        cleared[table] = conn.execute(f"DELETE FROM {table}").rowcount  # noqa: S608
    return cleared
