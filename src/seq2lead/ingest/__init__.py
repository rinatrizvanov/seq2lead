"""Ingestion: download pinned sources and load them into the immutable raw layer."""

from seq2lead.ingest.auxiliary import ingest_fasta, ingest_tsv
from seq2lead.ingest.bindingdb import (
    HeaderError,
    IngestReport,
    ReleaseConflict,
    UnpinnedSource,
    ingest,
    read_header,
    reconstruct_line,
    record_total_seconds,
    validate_header,
)
from seq2lead.ingest.download import ChecksumMismatch, DownloadResult, fetch
from seq2lead.ingest.inspection import Inspection, inspect
from seq2lead.ingest.sources import M2_SUBSETS, REGISTRY, SourceFile, get

__all__ = [
    "M2_SUBSETS",
    "REGISTRY",
    "ChecksumMismatch",
    "DownloadResult",
    "HeaderError",
    "IngestReport",
    "Inspection",
    "ReleaseConflict",
    "SourceFile",
    "UnpinnedSource",
    "fetch",
    "get",
    "ingest",
    "ingest_fasta",
    "ingest_tsv",
    "inspect",
    "read_header",
    "reconstruct_line",
    "record_total_seconds",
    "validate_header",
]
