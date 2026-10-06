"""Observation exports: one curated measurement per row, with its provenance.

The as-of matcher compares two snapshots. It cannot read two database schemas at
once, and one of those snapshots is **accepted, frozen data that must not be
rebuilt**, so the comparison is fed from exports rather than from live tables.

Three properties make an export usable as evidence:

* **Read-only by construction.** Every export runs inside a `READ ONLY`
  transaction, so the database itself refuses a write. That is what lets the
  September snapshot be exported from the accepted schema without the export
  being able to disturb it, by accident or by a later edit to this file.
* **Release identity that survives the schema it came from.** `source_release_id`
  is a per-schema surrogate -- the same September `all` artifact is id 117 in the
  accepted schema and would be some other integer anywhere else. The export
  therefore also carries `source_release`, the `name/version/subset` triple that
  identifies the artifact itself.
* **Byte-reproducible output.** The gzip member pins both `mtime` and the
  embedded filename, so re-exporting the same rows yields the same digest.
  Without that, a recorded SHA-256 would certify when and where the file was
  written rather than what is in it.
* **A locator that points into the source file, not into our database.**
  `raw_row` is the line number in the distributed TSV, so a disputed observation
  can be checked against the archived bytes. `raw_measurement_id` is kept beside
  it for convenience but is only a local surrogate.

**Entry DOI is lifted here, not stored on `activity`.** BindingDB publishes two
unrelated DOI columns -- `Article DOI`, the publication, and `BindingDB Entry
DOI`, an externally minted identifier for the curated *entry* a row belongs to.
Only the first has a home in the curated schema (`publication.doi`). Lifting the
second at export time, from the preserved raw payload, keeps the two distinct and
means both snapshots obtain it by the identical query -- the September side
cannot acquire a column the January side lacks, or vice versa, which is the
failure that would make a diff between them meaningless.
"""

from __future__ import annotations

import gzip
import io
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from seq2lead.curate.pipeline import ASSAY_MATCHED

if TYPE_CHECKING:
    from collections.abc import Iterator

    import psycopg

#: The source TSV column holding BindingDB's per-entry DOI. Named once: it is a
#: literal from a third-party file format, and a typo would silently export an
#: all-null column that still reconciles against itself.
ENTRY_DOI_COLUMN = "BindingDB Entry DOI"

#: The publication DOI column, named only to document that it is a *different*
#: field and is deliberately not what `entry_doi` is read from.
ARTICLE_DOI_COLUMN = "Article DOI"

#: Every key `asof.matching.observation_of` consults, plus the provenance it
#: records. Asserted against the matcher in the tests so the two cannot drift.
OBSERVATION_COLUMNS = (
    "source_release",
    "source_release_id",
    "raw_row",
    "raw_measurement_id",
    "inchikey",
    "sequence_sha256",
    "pmid",
    "doi",
    "patent_number",
    "ph_text",
    "temp_c_text",
    "curation_source",
    "measurement_type",
    "relation",
    "value_text",
    "entry_doi",
    "reactant_set_id",
    "assay_join_status",
)

_OBSERVATION_SQL = f"""
SELECT
    %(release_key)s               AS source_release,
    a.source_release_id           AS source_release_id,
    rm.line_no                    AS raw_row,
    a.raw_measurement_id          AS raw_measurement_id,
    c.inchikey                    AS inchikey,
    t.sequence_sha256             AS sequence_sha256,
    COALESCE(p.pmid, '')          AS pmid,
    COALESCE(p.doi, '')           AS doi,
    COALESCE(p.patent_number, '') AS patent_number,
    a.ph_text                     AS ph_text,
    a.temp_c_text                 AS temp_c_text,
    a.curation_source             AS curation_source,
    a.measurement_type            AS measurement_type,
    a.relation                    AS relation,
    a.value_text                  AS value_text,
    NULLIF(btrim(rm.payload ->> '{ENTRY_DOI_COLUMN}'), '') AS entry_doi,
    a.reactant_set_id             AS reactant_set_id,
    a.assay_join_status           AS assay_join_status
FROM activity a
JOIN raw_measurement rm ON rm.id = a.raw_measurement_id
JOIN compound c         ON c.id  = a.compound_id
JOIN target t           ON t.id  = a.target_id
LEFT JOIN publication p ON p.id  = a.publication_id
WHERE a.source_release_id = %(release_id)s
ORDER BY a.id
"""


@dataclass
class ExportSummary:
    """What the export actually wrote, counted while writing it.

    Counted in the same pass that produces the file rather than by a second
    query, so the figures describe the exported bytes and not a re-read of the
    table that might have been filtered differently.
    """

    source_release: str
    source_release_id: int
    path: Path
    rows: int = 0
    with_entry_doi: int = 0
    with_locator: int = 0
    with_publication_doi: int = 0
    with_pmid: int = 0
    assay_joined: int = 0
    distinct_entry_dois: int = 0
    measurement_types: dict[str, int] = field(default_factory=dict)
    assay_join_status: dict[str, int] = field(default_factory=dict)

    @property
    def entry_doi_coverage(self) -> float:
        return self.with_entry_doi / self.rows if self.rows else 0.0

    @property
    def locator_coverage(self) -> float:
        return self.with_locator / self.rows if self.rows else 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "source_release": self.source_release,
            "source_release_id": self.source_release_id,
            "path": str(self.path),
            "rows": self.rows,
            "with_entry_doi": self.with_entry_doi,
            "distinct_entry_dois": self.distinct_entry_dois,
            "entry_doi_coverage": round(self.entry_doi_coverage, 6),
            "with_locator": self.with_locator,
            "locator_coverage": round(self.locator_coverage, 6),
            "with_publication_doi": self.with_publication_doi,
            "with_pmid": self.with_pmid,
            "assay_joined": self.assay_joined,
            "measurement_types": dict(sorted(self.measurement_types.items())),
            "assay_join_status": dict(sorted(self.assay_join_status.items())),
        }


def release_key(conn: psycopg.Connection, source_release_id: int) -> str:
    """The artifact's own identity, independent of the schema holding it."""
    row = conn.execute(
        "SELECT source_name, version, subset FROM source_release WHERE id = %s",
        (source_release_id,),
    ).fetchone()
    if row is None:
        msg = f"no source_release with id {source_release_id}"
        raise LookupError(msg)
    return f"{row[0]}/{row[1]}/{row[2]}"


def iter_observations(
    conn: psycopg.Connection, source_release_id: int, *, batch: int = 50_000
) -> Iterator[dict[str, Any]]:
    """Stream one dict per curated observation.

    Server-side cursor: the January `all` release alone is 3.1M observations, and
    materialising that client-side is gigabytes of dictionaries for no reason.
    """
    key = release_key(conn, source_release_id)
    params = {"release_key": key, "release_id": source_release_id}
    with conn.cursor(name=f"observations_{source_release_id}") as cur:
        cur.itersize = batch
        cur.execute(_OBSERVATION_SQL, params)
        columns = [d.name for d in cur.description or []]
        for row in cur:
            yield dict(zip(columns, row, strict=True))


def export_observations(
    conn: psycopg.Connection,
    source_release_id: int,
    path: str | Path,
    *,
    batch: int = 50_000,
) -> ExportSummary:
    """Write every curated observation of one release as gzipped JSON Lines.

    The caller is responsible for opening the connection read-only
    (`read_only_transaction`); this function only reads, but saying so in a
    docstring is not an enforcement mechanism.
    """
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    summary = ExportSummary(
        source_release=release_key(conn, source_release_id),
        source_release_id=source_release_id,
        path=out,
    )
    seen_entry_dois: set[str] = set()
    # Two header fields would otherwise make identical rows produce different
    # digests: `mtime`, which defaults to now, and `FNAME`, which GzipFile copies
    # from the file object's path. Pinning both means a recorded SHA-256 certifies
    # the content rather than when and where the file happened to be written.
    with (
        out.open("wb") as raw,
        gzip.GzipFile(filename="", fileobj=raw, mode="wb", mtime=0) as gz,
        io.TextIOWrapper(gz, encoding="utf-8", newline="\n") as fh,
    ):
        for row in iter_observations(conn, source_release_id, batch=batch):
            fh.write(json.dumps(row, sort_keys=True, default=str))
            fh.write("\n")
            summary.rows += 1
            if row.get("entry_doi"):
                summary.with_entry_doi += 1
                seen_entry_dois.add(str(row["entry_doi"]))
            if row.get("raw_row") is not None and row.get("source_release"):
                summary.with_locator += 1
            if row.get("doi"):
                summary.with_publication_doi += 1
            if row.get("pmid"):
                summary.with_pmid += 1
            status = str(row.get("assay_join_status") or "")
            summary.assay_join_status[status] = summary.assay_join_status.get(status, 0) + 1
            if status == ASSAY_MATCHED:
                summary.assay_joined += 1
            mtype = str(row.get("measurement_type") or "")
            summary.measurement_types[mtype] = summary.measurement_types.get(mtype, 0) + 1
    summary.distinct_entry_dois = len(seen_entry_dois)
    return summary


def read_only_transaction(conn: psycopg.Connection) -> None:
    """Make the database refuse writes for the rest of this transaction.

    Used before exporting the accepted September snapshot. A comment promising
    not to write is not evidence; a transaction that raises on an INSERT is.
    """
    conn.execute("SET TRANSACTION READ ONLY")
