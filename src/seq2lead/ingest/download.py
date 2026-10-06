"""Download a pinned source file and check it against the frozen manifest.

Two independent checks, which answer different questions:

* **SHA-256 against `SourceFile.expected_sha256`** — *is this the artifact the
  manifest pins?* This is what fixes a dataset version, and it is checked before
  any database write.
* **MD5 against the publisher's `.md5`** — *did the bytes arrive intact?* Useful
  for transfer integrity only. It is re-published alongside the archive, so if
  upstream replaces a file under the same name the md5 moves with it.

Both outcomes are recorded on the release row.
"""

from __future__ import annotations

import hashlib
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from seq2lead.ingest.sources import SourceFile

_CHUNK = 1 << 20
_TIMEOUT = 600

DATA_ROOT = Path("data/raw")


class ChecksumMismatch(RuntimeError):
    """Downloaded bytes do not match a checksum they were required to match."""


@dataclass(frozen=True)
class DownloadResult:
    path: Path
    sha256: str
    md5: str
    md5_verified: bool
    sha256_pinned: bool
    archive_bytes: int
    downloaded_at: datetime
    reused_existing: bool


def _digests(path: Path) -> tuple[str, str]:
    sha, md5 = hashlib.sha256(), hashlib.md5()
    with path.open("rb") as fh:
        while chunk := fh.read(_CHUNK):
            sha.update(chunk)
            md5.update(chunk)
    return sha.hexdigest(), md5.hexdigest()


def _is_hex32(token: str) -> bool:
    return len(token) == 32 and all(c in "0123456789abcdef" for c in token.lower())


def _published_md5(source: SourceFile) -> str | None:
    """Fetch the publisher's md5, or None if it is not published for this file."""
    try:
        with urllib.request.urlopen(source.md5_url, timeout=60) as resp:  # noqa: S310
            if resp.status != 200:
                return None
            text = resp.read(256).decode("ascii", errors="replace").strip()
    except OSError:
        return None
    token = text.split()[0] if text else ""
    return token.lower() if _is_hex32(token) else None


def fetch(source: SourceFile, dest_dir: Path = DATA_ROOT) -> DownloadResult:
    """Download `source` unless already present, then verify it.

    Raises `ChecksumMismatch` if the manifest pins a digest and the bytes differ.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    path = dest_dir / source.filename
    reused = path.exists()

    if not reused:
        tmp = path.with_suffix(path.suffix + ".part")
        with urllib.request.urlopen(source.url, timeout=_TIMEOUT) as resp, tmp.open("wb") as out:  # noqa: S310
            while chunk := resp.read(_CHUNK):
                out.write(chunk)
        tmp.replace(path)

    sha256, md5 = _digests(path)

    if source.expected_sha256 is not None and sha256 != source.expected_sha256:
        raise ChecksumMismatch(
            f"{source.filename}: SHA-256 does not match the frozen manifest.\n"
            f"  expected {source.expected_sha256}\n"
            f"  observed {sha256}\n"
            "The manifest pins a specific artifact. Either the local file is corrupt "
            "(delete it and retry) or upstream replaced the release under the same "
            "name, which is a new dataset version and needs a new manifest entry."
        )

    published = _published_md5(source)
    if published is not None and published != md5:
        raise ChecksumMismatch(
            f"{source.filename}: publisher md5 {published} != downloaded {md5}. "
            "The transfer was corrupted; delete the file and retry."
        )

    return DownloadResult(
        path=path,
        sha256=sha256,
        md5=md5,
        md5_verified=published is not None,
        sha256_pinned=source.expected_sha256 is not None,
        archive_bytes=path.stat().st_size,
        downloaded_at=datetime.now(UTC),
        reused_existing=reused,
    )
