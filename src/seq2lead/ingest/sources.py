"""Frozen source manifest.

Every ingest names a release here rather than accepting a URL, so a dataset can
always be traced to an exact artifact.

**What pins a dataset version.** `expected_sha256` is the pin: a digest observed
once, recorded here, and checked before anything touches the database. The
publisher's `.md5` is *also* fetched and compared, but it only attests that the
bytes arrived intact — it is re-published alongside the archive, so if upstream
replaces a file under the same name the md5 moves with it and nothing is caught.
Only the frozen digest in this file detects that.

`expected_sha256 = None` means "not yet pinned". Such a source **cannot be
ingested** — `seq2lead ingest bindingdb` refuses it. Use
`seq2lead ingest inspect` to download and examine it without writing to the
database, then record the observed digest here.

**Version coherence.** Most BindingDB artifacts carry the release in the
filename (`..._202609_tsv.zip`). `BindingDBTargetSequences.fasta` does not: it is
a rolling file at a different base path. Its `Last-Modified` was 2026-08-30,
matching the 202609 archives (the downloads page's "updated 2026-05-01" label is
stale), but the filename carries no version, so the SHA-256 pin is the only thing
tying it to this release. That is recorded on the manifest entry.

Two further BindingDB quirks:

* The downloads page links each file through an interstitial JSP
  (`SDFdownload.jsp?download_file=...`) which returns HTML, not the archive. The
  direct URL is what serves bytes.
* The page publishes `.md5` links only for the SDF and database-dump files, but
  an `.md5` exists at the predictable path for the TSVs too.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

BINDINGDB_DOWNLOADS = "https://www.bindingdb.org/rwd/bind/downloads"
BINDINGDB_ROOT = "https://www.bindingdb.org/rwd/bind"

# BindingDB-curated records are CC-BY 3.0; rows drawn from ChEMBL are CC-BY-SA 3.0.
# The applicable term is per-row and is resolved from 'Curation/DataSource' at M3.
BINDINGDB_LICENSE = "CC-BY-3.0 (BindingDB-curated); CC-BY-SA-3.0 (ChEMBL-derived rows)"

# Long-term citable deposit. The collection is quarterly and is the right citation
# for a published dataset version; per-file 202609 artifacts were not resolvable
# from it programmatically, so the live artifacts are pinned by observed SHA-256.
BINDINGDB_ARCHIVE = "https://library.ucsd.edu/dc/collection/bb03870458"

# Columns M3 needs from the measurement table. Checked by name, not position, and
# the header is *not* required to match any other subset's.
BINDINGDB_REQUIRED_COLUMNS: tuple[str, ...] = (
    "BindingDB Reactant_set_id",
    "Ligand SMILES",
    "Ligand InChI Key",
    "Target Name",
    "Number of Protein Chains in Target (>1 implies a multichain complex)",
    "BindingDB Target Chain Sequence 1",
    "Date of publication",
    "Date in BindingDB",
)


@dataclass(frozen=True)
class SourceFile:
    source_name: str
    version: str
    subset: str
    filename: str
    license: str
    archive_member: str | None = None
    base_url: str = BINDINGDB_DOWNLOADS
    kind: str = "tsv_zip"  # tsv_zip | fasta
    expected_sha256: str | None = None
    required_columns: tuple[str, ...] = ()
    note: str = ""

    @property
    def url(self) -> str:
        return f"{self.base_url}/{self.filename}"

    @property
    def md5_url(self) -> str:
        return f"{self.base_url}/{self.filename.rsplit('.', 1)[0]}.md5"

    @property
    def is_pinned(self) -> bool:
        return self.expected_sha256 is not None


BINDINGDB_202609_PDSPKI = SourceFile(
    source_name="BindingDB",
    version="202609",
    subset="pdspki",
    filename="BindingDB_PDSPKi_202609_tsv.zip",
    archive_member="BindingDB_PDSPKi.tsv",
    license=BINDINGDB_LICENSE,
    expected_sha256="5a212e99f495e5c11befb09baba264435201dfced1cada926be13df73f971ac3",
    required_columns=(*BINDINGDB_REQUIRED_COLUMNS, "Ki (nM)"),
    note="M1 pilot: Ki-only subset from the PDSP Ki database. Digest observed 2026-09-28.",
)

BINDINGDB_202609_ALL = SourceFile(
    source_name="BindingDB",
    version="202609",
    subset="all",
    filename="BindingDB_All_202609_tsv.zip",
    archive_member="BindingDB_All.tsv",
    license=BINDINGDB_LICENSE,
    expected_sha256="4c04e0fec46fadab8a48465a7ac2344cf4e2b3887c6b69acc968676130b73e16",
    required_columns=BINDINGDB_REQUIRED_COLUMNS,
    note=(
        "M2 full release. Digest observed 2026-09-28 via `ingest inspect`, not guessed. "
        "593,578,299 bytes compressed; 8,984,698,089 uncompressed (15.14x); 640 columns, "
        "the same header as the PDSP pilot. Publisher md5 19db177dc630e556bd1449db408767ca."
    ),
)

# --------------------------------------------------------------- 202601 (M11c)
#
# The January 2026 archival deposit, DOI 10.6075/j0v40w61, acquired from the UC
# San Diego Library DAMS rather than bindingdb.org -- past monthly files are not
# served, so the archive is the only route to an earlier snapshot. Digests below
# were observed at acquisition (2026-10-02) and cross-checked against the
# SHA-1 the archive publishes; see configs/manifests/m11_acquisition_202601.json
# and reports/m11_acquisition.md. These are NEW pins for a new release; the
# 202609 pins are untouched.
_DEPOSIT_202601 = "file://" + str(
    Path(__file__).resolve().parents[3] / "data" / "raw" / "bindingdb-202601"
)

BINDINGDB_202601_ALL = SourceFile(
    source_name="BindingDB",
    version="202601",
    subset="all",
    filename="BindingDB_All_202601_tsv.zip",
    archive_member="BindingDB_All.tsv",
    base_url=_DEPOSIT_202601,
    license=BINDINGDB_LICENSE,
    expected_sha256="dd419291190632b6dfe883b08b55b6374a815583345e33faa64f93a04f474ef6",
    required_columns=BINDINGDB_REQUIRED_COLUMNS,
    note="M11c exploratory pilot, earlier snapshot. Archival deposit 2026-01-01.",
)

BINDINGDB_202601_ASSAYS = SourceFile(
    source_name="BindingDB",
    version="202601",
    subset="assays",
    filename="BindingDB_Assays_202601_tsv.zip",
    archive_member="BindingDB_Assays.tsv",
    base_url=_DEPOSIT_202601,
    license=BINDINGDB_LICENSE,
    expected_sha256="67636b4417f96a8e25da9493f3902ccfb3008b9b9be0f2b6aa1c935c9dbc4c29",
    required_columns=("ENTRYID", "ASSAYID", "ASSAY_NAME", "DESCRIPTION"),
    note="M11c exploratory pilot. Archival deposit 2026-01-01.",
)

BINDINGDB_202601_RSID = SourceFile(
    source_name="BindingDB",
    version="202601",
    subset="rsid_eaids",
    filename="BindingDB_rsid_eaids_202601_tsv.zip",
    archive_member="BindingDB_rsid_eaids.tsv",
    base_url=_DEPOSIT_202601,
    license=BINDINGDB_LICENSE,
    expected_sha256="af4968955143a793257c6584e99e5da7be270f474cb7463400b763b980fa5aa8",
    required_columns=("REACTANT_SET_ID", "ENTRYID_ASSAYID"),
    note="M11c exploratory pilot. Archival deposit 2026-01-01.",
)


BINDINGDB_202609_ASSAYS = SourceFile(
    source_name="BindingDB",
    version="202609",
    subset="assays",
    filename="BindingDB_Assays_202609_tsv.zip",
    archive_member="BindingDB_Assays.tsv",
    license=BINDINGDB_LICENSE,
    expected_sha256="b9949b6271de7a3d2e4269498fffc9a5374b0e45d72202c4c347d798876b345e",
    required_columns=("ENTRYID", "ASSAYID", "ASSAY_NAME", "DESCRIPTION"),
    note=(
        "Entry ID + Assay ID -> plain-text assay description. Digest observed 2026-09-28. "
        "DESCRIPTION carries HTML entities (e.g. &#181; for micro); the raw layer keeps "
        "them verbatim and M3 decides on decoding."
    ),
)

BINDINGDB_202609_RSID = SourceFile(
    source_name="BindingDB",
    version="202609",
    subset="rsid_eaids",
    filename="BindingDB_rsid_eaids_202609_tsv.zip",
    archive_member="BindingDB_rsid_eaids.tsv",
    license=BINDINGDB_LICENSE,
    expected_sha256="0c967f8a34ab21d766a5e9ca327a02ad45970321f691816f1d27109ef79c7cf9",
    required_columns=("REACTANT_SET_ID", "ENTRYID_ASSAYID"),
    note=(
        "Reactant_set_id -> EntryID_AssayID, the join key between measurements and "
        "assay text. ENTRYID_ASSAYID is ENTRYID and ASSAYID joined by '_' (e.g. "
        "'285_1' -> ENTRYID 285, ASSAYID 1). Digest observed 2026-09-28."
    ),
)

BINDINGDB_202609_FASTA = SourceFile(
    source_name="BindingDB",
    version="202609",
    subset="target_sequences",
    filename="BindingDBTargetSequences.fasta",
    archive_member=None,
    base_url=BINDINGDB_ROOT,  # NOT under /downloads/
    kind="fasta",
    license=BINDINGDB_LICENSE,
    expected_sha256="811507d7d61175a31b0f298ac8848c37e0d7058652af68fb5e6724f06fd261b5",
    note=(
        "Rolling filename with no release marker. Last-Modified 2026-08-30 matches the "
        "202609 archives; the SHA-256 pin is the only thing tying it to this release. "
        "No publisher md5 exists for this file. 11,527 records; headers look like "
        "'>p1 mol:protein length:376 Thymidine kinase'. Digest observed 2026-09-28."
    ),
)

REGISTRY: dict[str, SourceFile] = {
    "pdspki": BINDINGDB_202609_PDSPKI,
    "all": BINDINGDB_202609_ALL,
    "assays": BINDINGDB_202609_ASSAYS,
    "rsid_eaids": BINDINGDB_202609_RSID,
    "target_sequences": BINDINGDB_202609_FASTA,
}

# The artifacts M2 loads, in dependency order.
M2_SUBSETS: tuple[str, ...] = ("all", "assays", "rsid_eaids", "target_sequences")


def get(subset: str) -> SourceFile:
    try:
        return REGISTRY[subset]
    except KeyError:
        known = ", ".join(sorted(REGISTRY))
        raise KeyError(f"Unknown subset {subset!r}. Known: {known}") from None
