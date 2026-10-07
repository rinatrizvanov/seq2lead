"""The standalone inference path: bundle integrity, input handling, shortlisting.

These build a tiny synthetic bundle rather than the real 59 MB one, so they run
anywhere and exercise the logic rather than the artifact. Agreement with the
database-backed path is a separate, artifact-dependent check.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

import numpy as np
import pytest

from seq2lead.inference.bundle import (
    BUNDLE_VERSION,
    BundleError,
    load_bundle,
    resolve_bundle,
    verify_bundle,
)
from seq2lead.inference.rank import RankedRow
from seq2lead.inference.sequence import SequenceError, parse_fasta, read_query, validate


def _bundle(root: Path, *, n: int = 6, dim: int = 4) -> Path:
    """A minimal but structurally complete bundle."""
    rng = np.random.default_rng(0)
    (root / "model").mkdir(parents=True)
    (root / "library").mkdir(parents=True)
    np.savez(
        root / "model/protein_tower.npz",
        **{
            "0.weight": rng.normal(size=(dim, 8)).astype(np.float32),
            "0.bias": np.zeros(dim, dtype=np.float32),
            "2.weight": rng.normal(size=(dim, dim)).astype(np.float32),
            "2.bias": np.zeros(dim, dtype=np.float32),
        },
        scale=np.asarray(2.0, dtype=np.float32),
        offset=np.asarray(7.0, dtype=np.float32),
    )
    np.savez(
        root / "model/compound_tower.npz",
        **{"0.weight": rng.normal(size=(dim, 8)).astype(np.float32)},
    )
    np.savez(
        root / "model/protein_transform.npz",
        mean=np.zeros((1, 8), dtype=np.float32),
        scale=np.ones((1, 8), dtype=np.float32),
        n_fitted=np.asarray(10),
    )
    np.save(root / "library/projections.npy", rng.normal(size=(n, dim)).astype(np.float32))
    with (root / "library/compounds.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["row", "compound_id", "smiles", "flags"])
        for i in range(n):
            w.writerow([i, f"C{i}", "CCO" if i % 2 else "c1ccccc1", ""])

    from seq2lead.inference.bundle import BUNDLE_FILES, sha256_file

    manifest = {
        "bundle_version": BUNDLE_VERSION,
        "model": {"projection_dim": dim},
        "representation": {
            "protein": {
                "model": "facebook/esm2_t33_650M_UR50D",
                "model_revision": "deadbeef",
                "pooling": "mean_over_residues_excluding_special_tokens",
                "dtype": "float32",
                "length_policy": "full",
                "max_length": 40000,
                "training_window": 1022,
            }
        },
        "library": {"name": "test-lib"},
        "counts": {"compounds": n, "projection_dim": dim},
        "files": {
            name: {"bytes": (root / name).stat().st_size, "sha256": sha256_file(root / name)}
            for name in BUNDLE_FILES
        },
    }
    (root / "manifest.json").write_text(json.dumps(manifest, indent=1) + "\n")
    return root


# ================================================= 1. bundle integrity


def test_a_complete_bundle_verifies_and_loads(tmp_path: Path) -> None:
    root = _bundle(tmp_path / "b")
    assert verify_bundle(root)["bundle_version"] == BUNDLE_VERSION
    loaded = load_bundle(root)
    assert loaded.n_compounds == 6
    assert loaded.projection_dim == 4
    assert loaded.scale == pytest.approx(2.0)
    assert loaded.offset == pytest.approx(7.0)


def test_a_changed_byte_is_refused(tmp_path: Path) -> None:
    """The whole point of the manifest: altered contents must not silently score."""
    root = _bundle(tmp_path / "b")
    target = root / "library/projections.npy"
    blob = bytearray(target.read_bytes())
    blob[-1] ^= 0x01
    target.write_bytes(bytes(blob))
    with pytest.raises(BundleError, match="do not match the manifest"):
        verify_bundle(root)


def test_a_missing_file_is_refused(tmp_path: Path) -> None:
    root = _bundle(tmp_path / "b")
    (root / "model/protein_transform.npz").unlink()
    with pytest.raises(BundleError, match="incomplete"):
        verify_bundle(root)


def test_a_future_bundle_version_is_refused(tmp_path: Path) -> None:
    root = _bundle(tmp_path / "b")
    manifest = json.loads((root / "manifest.json").read_text())
    manifest["bundle_version"] = "seq2lead-inference-bundle-v99"
    (root / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(BundleError, match="version"):
        verify_bundle(root)


def test_metadata_and_projections_must_describe_the_same_library(tmp_path: Path) -> None:
    """A row-count mismatch means the two halves came from different exports."""
    root = _bundle(tmp_path / "b")
    from seq2lead.inference.bundle import sha256_file

    with (root / "library/compounds.csv").open("a", newline="", encoding="utf-8") as fh:
        csv.writer(fh).writerow([99, "C99", "CCO", ""])
    manifest = json.loads((root / "manifest.json").read_text())
    manifest["files"]["library/compounds.csv"]["sha256"] = sha256_file(
        root / "library/compounds.csv"
    )
    (root / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(BundleError, match="different libraries"):
        load_bundle(root)


def test_a_directory_without_a_manifest_names_the_problem(tmp_path: Path) -> None:
    with pytest.raises(BundleError, match="no manifest.json"):
        verify_bundle(tmp_path)


def test_resolution_reports_where_it_looked(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("SEQ2LEAD_BUNDLE", raising=False)
    monkeypatch.chdir(tmp_path)
    with pytest.raises(BundleError, match=r"SEQ2LEAD_BUNDLE"):
        resolve_bundle(None)


# ================================================= 2. one sequence, explicitly


def test_two_fasta_records_are_refused_not_silently_narrowed() -> None:
    with pytest.raises(SequenceError, match="2 FASTA records"):
        parse_fasta(">a\nMKV\n>b\nMKA\n", "x.fasta")


def test_a_single_record_is_read_with_its_header() -> None:
    q = parse_fasta(">sp|P00918 CA2\nMKV\nLAA\n", "x.fasta")
    assert q.header.startswith("sp|P00918")
    assert q.sequence == "MKVLAA"


def test_bare_sequence_without_a_header_is_accepted() -> None:
    assert parse_fasta("MKVL\nAA\n", "pasted").sequence == "MKVLAA"


def test_exactly_one_input_source() -> None:
    with pytest.raises(SequenceError, match="either"):
        read_query(sequence="MKV", path=Path("x"))
    with pytest.raises(SequenceError, match="either"):
        read_query()


@pytest.mark.parametrize(
    ("sequence", "message"),
    [
        ("ATGCGTACGTACGTACGTACGTACG", "nucleotide"),
        ("MKV", "residues"),
        ("MKVLAAJJJ" + "A" * 20, "not amino-acid codes"),
    ],
)
def test_unusable_sequences_say_what_is_wrong(sequence: str, message: str) -> None:
    q = parse_fasta(sequence, "pasted")
    with pytest.raises(SequenceError, match=message):
        validate(q, training_window=1022, max_length=40000)


def test_over_length_is_refused_rather_than_truncated() -> None:
    q = parse_fasta("M" * 120, "pasted")
    with pytest.raises(SequenceError, match="refused rather than"):
        validate(q, training_window=50, max_length=100)


def test_past_the_training_window_is_a_note_not_a_refusal() -> None:
    q = parse_fasta("M" * 120, "pasted")
    cleaned, notes = validate(q, training_window=50, max_length=40000)
    assert len(cleaned) == 120
    assert any("pre-training window" in n for n in notes)


def test_ambiguous_codes_are_flagged() -> None:
    q = parse_fasta("MKVLAAXXBZ" + "A" * 20, "pasted")
    _, notes = validate(q, training_window=1022, max_length=40000)
    assert any("ambiguous" in n for n in notes)


# ================================================= 3. shortlisting is a view


def _rows() -> list[RankedRow]:
    return [
        RankedRow(rank=1, original_rank=1, compound_id="A", smiles="c1ccccc1", score_pki=9.0),
        RankedRow(
            rank=2, original_rank=2, compound_id="B", smiles="CCCCCCCCCCCCCCCCCC", score_pki=8.0
        ),
        RankedRow(rank=3, original_rank=3, compound_id="C", smiles="c1ccccc1C", score_pki=7.0),
    ]


def test_property_filter_keeps_scores_and_original_ranks() -> None:
    from seq2lead.inference.shortlist import filter_by_properties

    # benzene 78.1, toluene 92.1, octadecane 254.5 -- the window keeps the first two
    out = filter_by_properties(_rows(), max_mw=100.0)
    assert [r.compound_id for r in out.kept] == ["A", "C"]
    assert [(r.compound_id, r.original_rank) for r in out.removed] == [("B", 2)]
    assert all(r.score_pki > 0 for r in out.removed)
    assert all(r.smiles for r in out.removed), "a removal must keep the structure it removed"


def test_no_filter_requested_means_no_filtering() -> None:
    from seq2lead.inference.shortlist import filter_by_properties

    out = filter_by_properties(_rows())
    assert len(out.kept) == 3
    assert out.removed == []
    assert out.applied == []


def test_diversity_states_its_method_and_reports_removals() -> None:
    from seq2lead.inference.shortlist import select_diverse

    out = select_diverse(_rows(), n=2, threshold=0.5)
    assert out.method["similarity"] == "Tanimoto on those bits"
    assert "Morgan" in out.method["fingerprint"]
    assert len(out.kept) <= 2
    for gone in out.removed:
        assert gone.reason in {"too_similar", "beyond_requested_count", "unparseable_structure"}
        assert gone.detail


def test_diversity_never_promotes_a_lower_score_above_a_higher_one() -> None:
    from seq2lead.inference.shortlist import select_diverse

    out = select_diverse(_rows(), n=3, threshold=0.99)
    scores = [r.score_pki for r in out.kept]
    assert scores == sorted(scores, reverse=True)


def test_an_unparseable_structure_is_reported_separately_from_a_failed_filter() -> None:
    from seq2lead.inference.shortlist import filter_by_properties

    rows = [
        RankedRow(rank=1, original_rank=1, compound_id="X", smiles="not-a-smiles", score_pki=9.0)
    ]
    out = filter_by_properties(rows, max_mw=500.0)
    assert [r.reason for r in out.removed] == ["unparseable_structure"]


def test_drug_likeness_and_alerts_are_not_applied() -> None:
    """Nothing may silently exclude on PAINS, Lipinski or similar."""
    import seq2lead.inference.shortlist as module

    source = Path(module.__file__).read_text()
    for forbidden in ("PAINS", "Lipinski", "qed", "FilterCatalog"):
        assert f"{forbidden}(" not in source and f".{forbidden}" not in source


# ================================================= 4. reporting


def test_csv_carries_the_framing_and_every_column(tmp_path: Path) -> None:
    from seq2lead.inference.rank import Ranking
    from seq2lead.inference.report import CSV_COLUMNS, write_csv

    result = Ranking(
        sequence_length=30,
        sequence_sha256="a" * 64,
        header="",
        bundle_version=BUNDLE_VERSION,
        library_name="test-lib",
        library_members=3,
        projection_dim=4,
        device="cpu",
        rows=_rows(),
    )
    path = write_csv(tmp_path / "r.csv", result)
    text = path.read_text(encoding="utf-8")
    assert "not a binding probability" in text
    assert "calibrated confidence" in text
    body = [line for line in text.splitlines() if not line.startswith("#")]
    assert body[0].split(",")[: len(CSV_COLUMNS)] == CSV_COLUMNS
    assert len(body) == 1 + 3


def test_the_table_never_calls_results_validated(tmp_path: Path) -> None:
    from seq2lead.inference.rank import Ranking
    from seq2lead.inference.report import render

    result = Ranking(
        sequence_length=30,
        sequence_sha256="a" * 64,
        header="",
        bundle_version=BUNDLE_VERSION,
        library_name="test-lib",
        library_members=3,
        projection_dim=4,
        device="cpu",
        rows=_rows(),
    )
    text = render(result).lower()
    assert "prioritised candidates" in text
    for forbidden in ("validated lead", "confirmed", "binding probability", "confidence"):
        assert f" {forbidden}" not in text.replace("not a binding probability", "").replace(
            "not a calibrated confidence", ""
        )
