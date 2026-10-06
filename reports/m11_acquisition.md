# M11c acquisition — January 2026 deposit

**Scope: acquisition, archive inspection and provenance registration only.**
Nothing was parsed, curated, matched, fitted or scored. The confirmatory freeze
is **not signed**, and the study remains **exploratory**.

Machine-readable record: `configs/manifests/m11_acquisition_202601.json`.

## Verified facts

Each of these was measured, not assumed.

### The three artifacts

| Subset | File | Downloaded bytes | SHA-1 verified | SHA-256 |
| --- | --- | ---: | --- | --- |
| `all` | `BindingDB_All_202601_tsv.zip` | 571,717,360 | **yes** | `dd419291190632b6…` |
| `assays` | `BindingDB_Assays_202601_tsv.zip` | 10,384,151 | **yes** | `67636b4417f96a8e…` |
| `rsid_eaids` | `BindingDB_rsid_eaids_202601_tsv.zip` | 7,377,798 | **yes** | `af4968955143a793…` |

Every byte size matched the size recorded in the inventory before download, and
every SHA-1 matched the digest recorded there. **No expected checksum was
adjusted to fit what arrived.** 589.5 MB total, fetched in 335 seconds.

Deposit: DOI `10.6075/j0v40w61`, version date **2026-01-01**, DAMS object
`bb6055793n`.

### Archive integrity and members

All three archives pass a full CRC check (`testzip()` returns no bad member).
Members, listed **before any parsing**:

| Archive | Member | Uncompressed | CRC32 |
| --- | --- | ---: | --- |
| `All` | `BindingDB_All.tsv` | 8,685,561,734 | `56070a8a` |
| `Assays` | `BindingDB_Assays.tsv` | 46,210,205 | `1d2c7094` |
| `rsid_eaids` | `BindingDB_rsid_eaids.tsv` | 54,275,527 | `9906de7f` |

8.79 GB uncompressed. **The member names match what our ingest expects**, which
closes a blocker that was previously recorded as an assumption: the archive
metadata described only the deposited ZIPs, so member names had been inferred
from the filename convention rather than observed.

### Release structure

One dated deposit, three artifact identities. The existing schema already models
this and needed no change: `source_release` is unique on
`(source_name, version, subset)`, so

- the **deposit** is the shared `version = '202601'`;
- each **artifact** is its own row, carrying its own `subset`, `url`,
  `archive_member`, `sha256`, `archive_bytes` and `member_bytes`.

This is the same shape the accepted 202609 release already uses across its five
subsets, so the two snapshots are directly comparable at the provenance level.

| id | version/subset | member | archive bytes | member bytes | `rows_loaded` | `ingested_at` |
| ---: | --- | --- | ---: | ---: | ---: | --- |
| 1203 | 202601/`all` | `BindingDB_All.tsv` | 571,717,360 | 8,685,561,734 | 0 | **NULL** |
| 1204 | 202601/`assays` | `BindingDB_Assays.tsv` | 10,384,151 | 46,210,205 | 0 | **NULL** |
| 1205 | 202601/`rsid_eaids` | `BindingDB_rsid_eaids.tsv` | 7,377,798 | 54,275,527 | 0 | **NULL** |

`ingested_at IS NULL` with `rows_loaded = 0` is the unparsed marker: these rows
are registered provenance, **not** a successful ingest.

**On the digest columns.** The archive publishes SHA-1; our pipeline pins
SHA-256. Both are stored. The `md5` column is `NOT NULL`, so rather than put a
SHA-1 where an MD5 is expected it holds the value prefixed `sha1:` — the
algorithm is explicit and cannot be misread. A first version of this
registration did put the bare SHA-1 in that column, which was corrected.

### Registration behaviour

| Behaviour | Result |
| --- | --- |
| Rerunning with identical bytes | **no-op**, all three reported as already registered |
| Registering conflicting bytes for a registered subset | **refused**, with the stored digest left unchanged (verified afterwards) |

### Accepted artifacts unchanged

The five 202609 `source_release` rows are untouched, and the M8/M9/M10 artifact
digests all verify. Nothing in the existing corpus was modified; three new rows
were added for a new `version`.

## Remaining assumptions

- **TSV structure is unread.** `column_names` is `[]` because the header has not
  been parsed. Whether 202601's columns match 202609's is unknown, and a column
  change would break the matcher's slot construction before it broke anything
  else.
- **Row counts are unknown.** The uncompressed size is measured; the number of
  measurements in it is not. The inventory's "about 3.18 million" is rounded
  prose from the deposit description, not a count.
- **Licence at deposit level only.** CC BY 4.0 comes from the DataCite record.
  The deposit's own README (component 1) was deliberately **not** downloaded, so
  whether it states a narrower grant for ChEMBL-derived rows is still unverified.
- **Entry DOI and locator columns are assumed present.** 202609 carries
  `BindingDB Entry DOI` in its raw payload; that 202601 does too is an assumption
  until the header is read.
- **Curation compatibility is inferred from file presence.** Both auxiliary files
  are present and integral, which is necessary for our three-way assay join. That
  the join will actually succeed depends on unread content.

## What this does not resolve

The deposit is **historical** — cut 2026-01-01, eight months before the corpus we
have already read. Acquiring it does nothing for the requirement in `docs/M11.md`
§7: a genuinely confirmatory evaluation needs a release cut **after** a published
freeze, and no such release exists. This pilot validates machinery and measures
diff behaviour. It is exploratory, and it stays labelled so.
