"""Load a BindingDB TSV into the raw layer.

No interpretation happens here. Values are not coerced, split on a relation
prefix, or standardized — that is M3's job.

**Fidelity.** What lands in the database is a parsed projection, not the original
bytes, and the projection is exactly reversible for every loaded row:

    "\\t".join(payload.get(c, "") for c in header) == the original decoded line

`reconstruct_line` implements that and the tests assert it against the real file.
The three ways a naive parser loses information are each handled explicitly:

* **Whitespace-only fields** are preserved. Only a field that is exactly `""` is
  omitted, because that is the one case the header makes recoverable.
* **Undecodable bytes** are never replaced. A line that is not valid UTF-8 is
  quarantined whole, with its original bytes, and is not loaded.
* **Malformed lines are never truncated.** The full original bytes go to
  `ingest_exclusion.raw_bytes`.

Lines are split on LF. A `\\r` from a CRLF file is retained in the final field
rather than stripped, so reconstruction stays exact either way.

**Immutability.** A release is written once. Re-ingesting identical bytes is a
verified no-op that preserves the release id and every raw row id; re-ingesting
different bytes under the same name raises `ReleaseConflict` rather than
replacing anything.
"""

from __future__ import annotations

import base64
import json
import tempfile
import time
import zipfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING, BinaryIO

from seq2lead.db.schema import INDEX_SQL, create_schema, drop_indexes

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    import psycopg

    from seq2lead.ingest.download import DownloadResult
    from seq2lead.ingest.sources import SourceFile

_READ_CHUNK = 1 << 20
_EXCLUSION_BATCH = 5_000


class ReleaseConflict(RuntimeError):
    """A release of this name already exists with different content."""


class UnpinnedSource(RuntimeError):
    """The manifest carries no expected digest for this source, so it cannot be loaded."""


class HeaderError(RuntimeError):
    """The file's header is unusable; loading would mis-assign every value."""


@dataclass
class IngestReport:
    subset: str
    source_release_id: int
    column_count: int
    data_lines: int
    loaded: int
    excluded: int
    field_total: int
    member_bytes: int
    copy_seconds: float = 0.0
    exclusion_seconds: float = 0.0
    index_seconds: dict[str, float] = field(default_factory=dict)
    total_seconds: float | None = None
    was_noop: bool = False

    @property
    def reconciles(self) -> bool:
        return self.data_lines == self.loaded + self.excluded

    @property
    def rows_per_second(self) -> float:
        return self.loaded / self.copy_seconds if self.copy_seconds else 0.0

    @property
    def mean_fields_per_row(self) -> float:
        return self.field_total / self.loaded if self.loaded else 0.0

    @property
    def index_seconds_total(self) -> float:
        return sum(self.index_seconds.values())


# --------------------------------------------------------------------------- parsing


def iter_lines(fh: BinaryIO, chunk_size: int = _READ_CHUNK) -> Iterator[bytes]:
    """Yield LF-separated lines as raw bytes, holding at most one chunk in memory."""
    carry = b""
    while chunk := fh.read(chunk_size):
        parts = (carry + chunk).split(b"\n")
        carry = parts.pop()
        yield from parts
    if carry:
        yield carry


def reconstruct_line(header: list[str], payload: dict[str, str]) -> str:
    """Rebuild a loaded row's original decoded line from its stored projection."""
    return "\t".join(payload.get(column, "") for column in header)


def validate_header(header: list[str], required: tuple[str, ...]) -> None:
    """Reject a header that would make column-to-value assignment unreliable.

    Note what this does *not* check: it never requires the header to equal any
    other subset's. The full release may legitimately carry different columns.
    """
    if not header:
        raise HeaderError("The file has no header line.")

    seen: dict[str, int] = {}
    duplicates: list[str] = []
    for name in header:
        seen[name] = seen.get(name, 0) + 1
        if seen[name] == 2:
            duplicates.append(name)
    if duplicates:
        raise HeaderError(
            "Duplicate column names would silently collide in the payload map: "
            + ", ".join(repr(d) for d in sorted(duplicates))
        )

    missing = [c for c in required if c not in seen]
    if missing:
        raise HeaderError(
            "Required columns are absent from this file: " + ", ".join(repr(m) for m in missing)
        )


def decode_header(raw: bytes, member: str) -> list[str]:
    try:
        return raw.decode("utf-8").split("\t")
    except UnicodeDecodeError as exc:
        raise HeaderError(f"The header line of {member} is not valid UTF-8: {exc}") from exc


def read_header(archive: Path, member: str) -> list[str]:
    """Return the TSV header without loading the body."""
    with zipfile.ZipFile(archive) as zf, zf.open(member) as fh:
        for raw in iter_lines(fh):
            return decode_header(raw, member)
    raise HeaderError(f"{member} is empty.")


# --------------------------------------------------------------------------- release


class ReleaseState(StrEnum):
    """What an existing `source_release` row means for a load request."""

    ABSENT = "absent"  # nothing registered: insert and load
    UNPARSED = "unparsed"  # registered provenance only: complete it
    INGESTED = "ingested"  # already loaded from these bytes: no-op
    CONFLICT = "conflict"  # registered from different bytes: refuse


@dataclass(frozen=True)
class ExistingRelease:
    release_id: int
    sha256: str
    ingested: bool


def existing_release(conn: psycopg.Connection, source: SourceFile) -> ExistingRelease | None:
    """The registered row for this (source, version, subset), if any.

    `ingested_at` is part of the answer. Reading only the digest conflated two
    very different situations: a release that was loaded from these exact bytes,
    and a release whose provenance was registered by the acquisition step but
    never parsed. The first is a verified no-op; the second is a load waiting to
    happen, and treating it as a no-op left it permanently at `rows_loaded = 0`.
    """
    row = conn.execute(
        "SELECT id, sha256, ingested_at FROM source_release "
        "WHERE source_name = %s AND version = %s AND subset = %s",
        (source.source_name, source.version, source.subset),
    ).fetchone()
    if row is None:
        return None
    return ExistingRelease(release_id=int(row[0]), sha256=str(row[1]), ingested=row[2] is not None)


def classify_release(existing: ExistingRelease | None, incoming_sha256: str) -> ReleaseState:
    """The three-way decision, in one place so both loaders agree."""
    if existing is None:
        return ReleaseState.ABSENT
    if existing.sha256 != incoming_sha256:
        return ReleaseState.CONFLICT
    return ReleaseState.INGESTED if existing.ingested else ReleaseState.UNPARSED


def conflict_error(
    source: SourceFile, existing: ExistingRelease, incoming_sha256: str
) -> ReleaseConflict:
    return ReleaseConflict(
        f"{source.source_name} {source.version}/{source.subset} is already registered "
        f"as release {existing.release_id} from a different artifact.\n"
        f"  registered sha256 {existing.sha256}\n"
        f"  incoming   sha256 {incoming_sha256}\n"
        "Raw releases are immutable. If upstream republished this release, add it to "
        "the manifest under a new version or subset name rather than overwriting the "
        "registered one."
    )


def complete_release(
    conn: psycopg.Connection,
    release_id: int,
    download: DownloadResult,
    header: list[str],
    member_bytes: int,
) -> int:
    """Fill in a registered-but-unparsed row, keeping its identity.

    The acquisition step already recorded the URL, both digests and the byte
    counts, and `activity.source_release_id` will reference this id. Inserting a
    second row would split one artifact across two identities, so the registered
    row is completed in place and only the parse-derived fields are written.
    """
    conn.execute(
        "UPDATE source_release SET column_names = %s, member_bytes = %s, "
        "md5_verified = %s, sha256_pinned = %s WHERE id = %s",
        (
            json.dumps(header),
            member_bytes,
            download.md5_verified,
            download.sha256_pinned,
            release_id,
        ),
    )
    return release_id


def noop_report(conn: psycopg.Connection, source: SourceFile, release_id: int) -> IngestReport:
    """Rebuild the original load's report from what the release recorded.

    This is why the timings live on the release row: a verified no-op must be able
    to report the real load rather than tempt anyone into re-loading to get numbers.
    """
    row = conn.execute(
        "SELECT jsonb_array_length(column_names), data_lines, rows_loaded, "
        "rows_excluded, field_total, member_bytes, copy_seconds, exclusion_seconds, "
        "index_seconds, total_seconds FROM source_release WHERE id = %s",
        (release_id,),
    ).fetchone()
    assert row is not None
    return IngestReport(
        subset=source.subset,
        source_release_id=release_id,
        column_count=int(row[0]),
        data_lines=int(row[1]),
        loaded=int(row[2]),
        excluded=int(row[3]),
        field_total=int(row[4]),
        member_bytes=int(row[5]),
        copy_seconds=float(row[6] or 0.0),
        exclusion_seconds=float(row[7] or 0.0),
        index_seconds={k: float(v) for k, v in (row[8] or {}).items()},
        total_seconds=float(row[9]) if row[9] is not None else None,
        was_noop=True,
    )


def record_total_seconds(conn: psycopg.Connection, release_id: int, seconds: float) -> None:
    """Store the end-to-end ingest time, measured by the caller after the commit."""
    conn.execute(
        "UPDATE source_release SET total_seconds = %s WHERE id = %s", (seconds, release_id)
    )


def insert_release(
    conn: psycopg.Connection,
    source: SourceFile,
    download: DownloadResult,
    header: list[str],
    member_bytes: int,
) -> int:
    row = conn.execute(
        """
        INSERT INTO source_release (
            source_name, version, subset, url, archive_member,
            sha256, md5, md5_verified, sha256_pinned, archive_bytes, member_bytes,
            license, downloaded_at, column_names
        )
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        RETURNING id
        """,
        (
            source.source_name,
            source.version,
            source.subset,
            source.url,
            # A plain file (FASTA) has no archive member; record the file itself.
            source.archive_member or source.filename,
            download.sha256,
            download.md5,
            download.md5_verified,
            download.sha256_pinned,
            download.archive_bytes,
            member_bytes,
            source.license,
            download.downloaded_at,
            json.dumps(header),
        ),
    ).fetchone()
    assert row is not None
    return int(row[0])


def spool_exclusion(spool: BinaryIO, line_no: int, rule_code: str, detail: str, raw: bytes) -> None:
    record = {
        "line_no": line_no,
        "rule_code": rule_code,
        "detail": detail,
        "raw_b64": base64.b64encode(raw).decode("ascii"),
    }
    spool.write(json.dumps(record).encode("utf-8") + b"\n")


def drain_exclusions(conn: psycopg.Connection, spool: BinaryIO, release_id: int) -> None:
    """Insert spooled exclusions in bounded batches."""
    spool.flush()
    spool.seek(0)
    batch: list[tuple[int, int, str, str, bytes, int]] = []

    def flush() -> None:
        if not batch:
            return
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO ingest_exclusion "
                "(source_release_id, line_no, rule_code, detail, raw_bytes, byte_length) "
                "VALUES (%s,%s,%s,%s,%s,%s)",
                batch,
            )
        batch.clear()

    for line in spool:
        record = json.loads(line)
        raw = base64.b64decode(record["raw_b64"])
        batch.append(
            (release_id, record["line_no"], record["rule_code"], record["detail"], raw, len(raw))
        )
        if len(batch) >= _EXCLUSION_BATCH:
            flush()
    flush()


# --------------------------------------------------------------------------- ingest


def ingest(
    conn: psycopg.Connection,
    source: SourceFile,
    download: DownloadResult,
) -> IngestReport:
    """Load the archive's TSV member, reconciling every line.

    Returns a verified no-op if this release is already loaded from identical
    bytes. Raises `ReleaseConflict` if it is loaded from different bytes, and
    `UnpinnedSource` if the manifest has no expected digest.
    """
    # Enforced here rather than in the CLI so that no caller can bypass it. An
    # unpinned artifact cannot be re-verified later: whatever was downloaded on
    # the day would become the dataset definition by default.
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
    # ABSENT -> insert below; UNPARSED -> complete the registered row below.
    resume_id = existing.release_id if state is ReleaseState.UNPARSED else None

    data_lines = loaded = excluded = field_total = 0
    copy_seconds = exclusion_seconds = 0.0

    with zipfile.ZipFile(download.path) as zf:
        member_bytes = zf.getinfo(source.archive_member).file_size
        with zf.open(source.archive_member) as fh:
            lines = iter_lines(fh)
            try:
                raw_header = next(lines)
            except StopIteration:
                raise HeaderError(f"{source.archive_member} is empty.") from None
            header = decode_header(raw_header, source.archive_member)
            validate_header(header, source.required_columns)

            width = len(header)
            release_id = (
                complete_release(conn, resume_id, download, header, member_bytes)
                if resume_id is not None
                else insert_release(conn, source, download, header, member_bytes)
            )
            # Only worth dropping indexes when there is nothing else in the table.
            # With other releases already loaded, a drop-and-rebuild churns their
            # index for no benefit — and on a 3.2M-row table it dominates the cost
            # of loading a small one.
            estimate = conn.execute(
                "SELECT reltuples FROM pg_class WHERE relname = 'raw_measurement'"
            ).fetchone()
            rebuild_indexes = not estimate or float(estimate[0]) <= 0
            if rebuild_indexes:
                drop_indexes(conn)

            # Excluded lines are spooled to disk, not accumulated in memory: a
            # pathological file could otherwise hold millions of raw lines in RAM.
            with tempfile.TemporaryFile("w+b") as spool:
                copy_sql = "COPY raw_measurement (source_release_id, line_no, payload) FROM STDIN"
                started = time.perf_counter()
                with conn.cursor() as cur, cur.copy(copy_sql) as cp:
                    for offset, raw in enumerate(lines):
                        line_no = offset + 2  # 1-based; the header is line 1
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

                        # Omit only the exactly-empty field. A whitespace-only field
                        # is real content and is kept, or reconstruction would lie.
                        payload = {k: v for k, v in zip(header, fields, strict=True) if v != ""}
                        field_total += len(payload)
                        cp.write_row((release_id, line_no, json.dumps(payload, ensure_ascii=False)))
                        loaded += 1
                copy_seconds = time.perf_counter() - started

                t0 = time.perf_counter()
                drain_exclusions(conn, spool, release_id)
                exclusion_seconds = time.perf_counter() - t0

    index_seconds: dict[str, float] = {}
    for name, statement in INDEX_SQL.items():
        t0 = time.perf_counter()
        conn.execute(statement)  # IF NOT EXISTS: a no-op when we did not drop
        index_seconds[name] = time.perf_counter() - t0 if rebuild_indexes else 0.0

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
            json.dumps(index_seconds),
            release_id,
        ),
    )

    return IngestReport(
        subset=source.subset,
        source_release_id=release_id,
        column_count=width,
        data_lines=data_lines,
        loaded=loaded,
        excluded=excluded,
        field_total=field_total,
        member_bytes=member_bytes,
        copy_seconds=copy_seconds,
        exclusion_seconds=exclusion_seconds,
        index_seconds=index_seconds,
    )
