"""Loaders for the M2 auxiliary artifacts.

Three files sit beside the measurement TSV and none of them share its shape:

* **`BindingDB_Assays`** — Entry ID + Assay ID to plain-text assay description.
* **`BindingDB_rsid_eaids`** — Reactant_set_id to EntryID_AssayID. This is the
  join key: assay text is *not* in the measurement file, so reaching it means
  measurement -> rsid_eaids -> Assays.
* **`BindingDBTargetSequences.fasta`** — target sequences, as FASTA.

They reuse the same guarantees as the measurement loader — pinned digest,
immutable release, bounded memory, every input record either loaded or
quarantined with its original bytes — but write to `raw_record` and
`raw_sequence` rather than `raw_measurement`.
"""

from __future__ import annotations

import json
import tempfile
import time
import zipfile
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from seq2lead.db.schema import AUX_INDEX_SQL, create_schema
from seq2lead.ingest.bindingdb import (
    IngestReport,
    ReleaseState,
    UnpinnedSource,
    classify_release,
    complete_release,
    conflict_error,
    decode_header,
    drain_exclusions,
    existing_release,
    insert_release,
    iter_lines,
    noop_report,
    spool_exclusion,
    validate_header,
)

if TYPE_CHECKING:
    import psycopg

    from seq2lead.ingest.download import DownloadResult
    from seq2lead.ingest.sources import SourceFile


def _guard(
    conn: psycopg.Connection, source: SourceFile, download: DownloadResult
) -> IngestReport | None:
    """Shared pin / immutability checks. Returns a no-op report when already loaded."""
    if not source.is_pinned:
        raise UnpinnedSource(
            f"{source.source_name} {source.version}/{source.subset} has no "
            "expected_sha256 in the source manifest, so it must not be loaded.\n"
            f"Run:  seq2lead ingest inspect --subset {source.subset}\n"
            "then record the observed digest as expected_sha256 in "
            "src/seq2lead/ingest/sources.py and re-run this command."
        )

    create_schema(conn)
    existing = existing_release(conn, source)
    state = classify_release(existing, download.sha256)
    if state is ReleaseState.CONFLICT:
        assert existing is not None
        raise conflict_error(source, existing, download.sha256)
    if state is ReleaseState.INGESTED:
        assert existing is not None
        return noop_report(conn, source, existing.release_id)
    # ABSENT or UNPARSED: the caller proceeds to load. `_resume_id` tells it
    # whether to complete a registered row or insert a new one.
    return None


def _resume_id(conn: psycopg.Connection, source: SourceFile) -> int | None:
    """The registered-but-unparsed release this load should complete, if any."""
    existing = existing_release(conn, source)
    return existing.release_id if existing is not None and not existing.ingested else None


def _finalise(
    conn: psycopg.Connection,
    release_id: int,
    data_lines: int,
    loaded: int,
    excluded: int,
    field_total: int,
    copy_seconds: float,
    exclusion_seconds: float,
    index_seconds: dict[str, float] | None = None,
) -> None:
    conn.execute(
        "UPDATE source_release SET ingested_at = %s, data_lines = %s, rows_loaded = %s, "
        "rows_excluded = %s, field_total = %s, copy_seconds = %s, exclusion_seconds = %s, "
        "index_seconds = %s WHERE id = %s",
        (
            datetime.now(UTC),
            data_lines,
            loaded,
            excluded,
            field_total,
            copy_seconds,
            exclusion_seconds,
            json.dumps(index_seconds or {}),
            release_id,
        ),
    )


def ingest_tsv(
    conn: psycopg.Connection,
    source: SourceFile,
    download: DownloadResult,
) -> IngestReport:
    """Load a plain TSV archive member into `raw_record`."""
    noop = _guard(conn, source, download)
    if noop is not None:
        return noop

    data_lines = loaded = excluded = field_total = 0
    copy_seconds = exclusion_seconds = 0.0

    with zipfile.ZipFile(download.path) as zf:
        member = source.archive_member or zf.infolist()[0].filename
        member_bytes = zf.getinfo(member).file_size
        with zf.open(member) as fh:
            lines = iter_lines(fh)
            try:
                raw_header = next(lines)
            except StopIteration as exc:
                raise ValueError(f"{member} is empty.") from exc
            header = decode_header(raw_header, member)
            validate_header(header, source.required_columns)
            width = len(header)

            resume = _resume_id(conn, source)
            release_id = (
                complete_release(conn, resume, download, header, member_bytes)
                if resume is not None
                else insert_release(conn, source, download, header, member_bytes)
            )

            with tempfile.TemporaryFile("w+b") as spool:
                copy_sql = "COPY raw_record (source_release_id, line_no, payload) FROM STDIN"
                started = time.perf_counter()
                with conn.cursor() as cur, cur.copy(copy_sql) as cp:
                    for offset, raw in enumerate(lines):
                        line_no = offset + 2
                        data_lines += 1
                        try:
                            text = raw.decode("utf-8")
                        except UnicodeDecodeError as exc:
                            excluded += 1
                            spool_exclusion(spool, line_no, "invalid_utf8", str(exc), raw)
                            continue
                        fields = text.split("\t")
                        if len(fields) != width:
                            excluded += 1
                            spool_exclusion(
                                spool,
                                line_no,
                                "field_count_mismatch",
                                f"expected {width} fields, found {len(fields)}",
                                raw,
                            )
                            continue
                        payload = {k: v for k, v in zip(header, fields, strict=True) if v != ""}
                        field_total += len(payload)
                        cp.write_row((release_id, line_no, json.dumps(payload, ensure_ascii=False)))
                        loaded += 1
                copy_seconds = time.perf_counter() - started

                t0 = time.perf_counter()
                drain_exclusions(conn, spool, release_id)
                exclusion_seconds = time.perf_counter() - t0

    index_seconds: dict[str, float] = {}
    for name, statement in AUX_INDEX_SQL.items():
        t0 = time.perf_counter()
        conn.execute(statement)
        index_seconds[name] = time.perf_counter() - t0

    _finalise(
        conn,
        release_id,
        data_lines,
        loaded,
        excluded,
        field_total,
        copy_seconds,
        exclusion_seconds,
        index_seconds,
    )
    return IngestReport(
        subset=source.subset,
        source_release_id=release_id,
        column_count=width,
        index_seconds=index_seconds,
        data_lines=data_lines,
        loaded=loaded,
        excluded=excluded,
        field_total=field_total,
        member_bytes=member_bytes,
        copy_seconds=copy_seconds,
        exclusion_seconds=exclusion_seconds,
    )


def ingest_fasta(
    conn: psycopg.Connection,
    source: SourceFile,
    download: DownloadResult,
) -> IngestReport:
    """Load a FASTA file into `raw_sequence`.

    A "record" here is one `>` header plus the residues that follow it, so the
    reconciliation unit is the record, not the line.
    """
    noop = _guard(conn, source, download)
    if noop is not None:
        return noop

    member_bytes = download.path.stat().st_size
    resume = _resume_id(conn, source)
    release_id = (
        complete_release(conn, resume, download, ["description", "sequence"], member_bytes)
        if resume is not None
        else insert_release(conn, source, download, ["description", "sequence"], member_bytes)
    )

    records = loaded = excluded = decode_failures = 0
    residue_total = 0
    started = time.perf_counter()

    with tempfile.TemporaryFile("w+b") as spool, download.path.open("rb") as fh:
        copy_sql = (
            "COPY raw_sequence (source_release_id, ordinal, description, sequence, length) "
            "FROM STDIN"
        )
        with conn.cursor() as cur, cur.copy(copy_sql) as cp:
            description: str | None = None
            chunks: list[str] = []
            first_line_no = 0

            def emit() -> None:
                nonlocal loaded, excluded, residue_total
                if description is None:
                    return
                sequence = "".join(chunks)
                if not sequence:
                    excluded += 1
                    spool_exclusion(
                        spool,
                        first_line_no,
                        "empty_sequence",
                        "FASTA header with no residues",
                        description.encode("utf-8"),
                    )
                    return
                loaded += 1
                residue_total += len(sequence)
                cp.write_row((release_id, loaded, description, sequence, len(sequence)))

            for line_no, raw in enumerate(iter_lines(fh), start=1):
                try:
                    text = raw.decode("utf-8")
                except UnicodeDecodeError as exc:
                    excluded += 1
                    decode_failures += 1
                    spool_exclusion(spool, line_no, "invalid_utf8", str(exc), raw)
                    continue
                if text.startswith(">"):
                    emit()
                    records += 1
                    description = text[1:]
                    chunks = []
                    first_line_no = line_no
                elif description is not None:
                    chunks.append(text.strip())
            emit()

        copy_seconds = time.perf_counter() - started
        t0 = time.perf_counter()
        drain_exclusions(conn, spool, release_id)
        exclusion_seconds = time.perf_counter() - t0

    # A FASTA "record" is a header plus its residues, but an undecodable line is
    # its own excluded unit, so both count toward the reconciliation total.
    data_lines = records + decode_failures
    _finalise(
        conn,
        release_id,
        data_lines,
        loaded,
        excluded,
        residue_total,
        copy_seconds,
        exclusion_seconds,
    )
    return IngestReport(
        subset=source.subset,
        source_release_id=release_id,
        column_count=2,
        data_lines=data_lines,
        loaded=loaded,
        excluded=excluded,
        field_total=residue_total,
        member_bytes=member_bytes,
        copy_seconds=copy_seconds,
        exclusion_seconds=exclusion_seconds,
    )
