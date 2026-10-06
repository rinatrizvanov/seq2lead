"""Download and examine an artifact without touching the database.

This is the only sanctioned route for an unpinned source. It answers the
questions needed to write a manifest entry — what the bytes hash to, what is
inside the archive, what the header looks like — and deliberately cannot write a
row, so an unverified artifact can never become the dataset definition by
accident.

Workflow:

    seq2lead ingest inspect --subset all     # observe the digest
    # record it as expected_sha256 in sources.py
    seq2lead ingest bindingdb --subset all   # re-verifies against the frozen pin
"""

from __future__ import annotations

import zipfile
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from seq2lead.ingest.bindingdb import decode_header, iter_lines

if TYPE_CHECKING:
    from pathlib import Path

    from seq2lead.ingest.download import DownloadResult
    from seq2lead.ingest.sources import SourceFile


@dataclass
class MemberInfo:
    name: str
    compressed_bytes: int
    uncompressed_bytes: int


@dataclass
class Inspection:
    source: SourceFile
    download: DownloadResult
    members: list[MemberInfo] = field(default_factory=list)
    header: list[str] | None = None
    sample_rows: list[list[str]] = field(default_factory=list)
    fasta_records: int | None = None
    fasta_first_header: str | None = None
    pin_matches: bool | None = None

    @property
    def compression_ratio(self) -> float:
        total = sum(m.uncompressed_bytes for m in self.members)
        return total / self.download.archive_bytes if self.download.archive_bytes else 0.0


def _inspect_zip(
    path: Path, member: str | None, sample: int
) -> tuple[list[MemberInfo], list[str] | None, list[list[str]]]:
    with zipfile.ZipFile(path) as zf:
        members = [MemberInfo(i.filename, i.compress_size, i.file_size) for i in zf.infolist()]
        target = member or (members[0].name if members else None)
        if target is None:
            return members, None, []
        header: list[str] | None = None
        rows: list[list[str]] = []
        with zf.open(target) as fh:
            for offset, raw in enumerate(iter_lines(fh)):
                if offset == 0:
                    header = decode_header(raw, target)
                    continue
                if offset > sample:
                    break
                rows.append(raw.decode("utf-8", errors="replace").split("\t"))
    return members, header, rows


def _inspect_fasta(path: Path) -> tuple[int, str | None]:
    count = 0
    first: str | None = None
    with path.open("rb") as fh:
        for raw in iter_lines(fh):
            if raw.startswith(b">"):
                count += 1
                if first is None:
                    first = raw.decode("utf-8", errors="replace")
    return count, first


def inspect(source: SourceFile, download: DownloadResult, sample: int = 3) -> Inspection:
    """Examine a downloaded artifact. Performs no database access."""
    result = Inspection(source=source, download=download)

    if source.expected_sha256 is not None:
        result.pin_matches = download.sha256 == source.expected_sha256

    if source.kind == "fasta":
        count, first = _inspect_fasta(download.path)
        result.fasta_records = count
        result.fasta_first_header = first
        result.members = [
            MemberInfo(source.filename, download.archive_bytes, download.archive_bytes)
        ]
        return result

    members, header, rows = _inspect_zip(download.path, source.archive_member, sample)
    result.members = members
    result.header = header
    result.sample_rows = rows
    return result
