"""Guards that must hold before the full release can be loaded."""

from __future__ import annotations

import hashlib
import json
import zipfile
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from typer.testing import CliRunner

from cli_help import documented, rendered, squash, switches
from seq2lead.cli import app
from seq2lead.db import connect, transaction
from seq2lead.ingest import auxiliary
from seq2lead.ingest import inspection as inspect_mod
from seq2lead.ingest.bindingdb import UnpinnedSource, ingest
from seq2lead.ingest.download import DownloadResult
from seq2lead.ingest.sources import M2_SUBSETS, REGISTRY, SourceFile, get


def _db_disabled() -> bool:
    import os

    return os.environ.get("SEQ2LEAD_SKIP_DB_TESTS") == "1"


@pytest.fixture(scope="module", autouse=True)
def _isolated_schema():
    """Every fixture in this module writes into a throwaway schema.

    These tests ingest synthetic releases and curate them. Run against the
    corpus they leave entities behind that no provenance query can find, which
    is how two test molecules and two test proteins reached the M7 caches.
    """
    from seq2lead.db.isolation import isolated_schema

    if _db_disabled():
        yield None
        return
    with isolated_schema("seq2lead_test_safeguards") as schema:
        yield schema


if TYPE_CHECKING:
    from pathlib import Path

runner = CliRunner()


def _zip(tmp_path: Path, rows: list[list[str]], member: str = "m.tsv") -> Path:
    path = tmp_path / "a.zip"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(member, "\n".join("\t".join(r) for r in rows) + "\n")
    return path


def _result(path: Path) -> DownloadResult:
    return DownloadResult(
        path=path,
        sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        md5="0" * 32,
        md5_verified=False,
        sha256_pinned=False,
        archive_bytes=path.stat().st_size,
        downloaded_at=datetime.now(UTC),
        reused_existing=False,
    )


# ======================================================== 1. unpinned refusal


UNPINNED = SourceFile(
    source_name="TestUnpinned",
    version="0",
    subset="unit-test-unpinned",
    filename="a.zip",
    archive_member="m.tsv",
    license="x",
    expected_sha256=None,
)


def test_every_registered_artifact_is_pinned() -> None:
    """The end state: nothing loadable may be missing a digest.

    These tests deliberately exercise the *mechanism* against a synthetic unpinned
    source rather than against a registry entry, so that pinning a real artifact
    cannot quietly turn them into no-ops — which is exactly what happened when the
    full release was pinned.
    """
    unpinned = [name for name, s in REGISTRY.items() if not s.is_pinned]
    assert unpinned == [], f"unpinned entries: {unpinned}"


@pytest.mark.requires_db
def test_unpinned_source_cannot_be_ingested(tmp_path: Path) -> None:
    """An unpinned source must not reach the database."""
    path = _zip(tmp_path, [["a"], ["1"]])
    with pytest.raises(UnpinnedSource, match="expected_sha256"), transaction() as conn:
        ingest(conn, UNPINNED, _result(path))

    with connect() as conn:
        row = conn.execute(
            "SELECT count(*) FROM source_release WHERE subset = %s", (UNPINNED.subset,)
        ).fetchone()
    assert row is not None
    assert row[0] == 0


@pytest.mark.requires_db
def test_unpinned_auxiliary_source_cannot_be_ingested(tmp_path: Path) -> None:
    """The guard lives in the auxiliary loader too, not only the measurement one.

    Uses a locally-built source rather than a registry entry, so pinning a real
    artifact later cannot quietly turn this test into a no-op.
    """
    source = SourceFile(
        source_name="TestAux",
        version="0",
        subset="unit-test-aux",
        filename="a.zip",
        archive_member="m.tsv",
        license="x",
        expected_sha256=None,
    )
    path = _zip(tmp_path, [["a", "b"], ["1", "2"]])
    with pytest.raises(UnpinnedSource), transaction() as conn:
        auxiliary.ingest_tsv(conn, source, _result(path))


def test_cli_refuses_to_ingest_an_unpinned_subset(monkeypatch: pytest.MonkeyPatch) -> None:
    """And it refuses before downloading — the check is ahead of the fetch."""
    monkeypatch.setitem(REGISTRY, UNPINNED.subset, UNPINNED)
    called = False

    def _boom(*_a: object, **_k: object) -> None:
        nonlocal called
        called = True
        raise AssertionError("fetch must not run for an unpinned source")

    monkeypatch.setattr("seq2lead.ingest.fetch", _boom)
    result = runner.invoke(app, ["ingest", "bindingdb", "--subset", UNPINNED.subset])
    assert result.exit_code == 1
    assert called is False


def test_unpinned_refusal_names_the_inspect_command(tmp_path: Path) -> None:
    """The error has to say what to do next, or it just blocks people."""
    path = _zip(tmp_path, [["a"], ["1"]])
    with pytest.raises(UnpinnedSource) as excinfo:
        ingest(None, UNPINNED, _result(path))  # type: ignore[arg-type]
    assert "ingest inspect" in str(excinfo.value)


# ======================================================== 2. inspect path


def test_inspect_reports_members_and_header_without_a_database(tmp_path: Path) -> None:
    rows = [["col a", "col b"], ["1", "2"], ["3", "4"]]
    path = _zip(tmp_path, rows)
    source = SourceFile(
        source_name="T",
        version="0",
        subset="t",
        filename="a.zip",
        archive_member="m.tsv",
        license="x",
    )
    info = inspect_mod.inspect(source, _result(path), sample=2)
    assert [m.name for m in info.members] == ["m.tsv"]
    assert info.header == ["col a", "col b"]
    assert info.sample_rows == [["1", "2"], ["3", "4"]]
    assert info.pin_matches is None  # unpinned source


def test_inspect_flags_a_pin_mismatch(tmp_path: Path) -> None:
    path = _zip(tmp_path, [["a"], ["1"]])
    source = SourceFile(
        source_name="T",
        version="0",
        subset="t",
        filename="a.zip",
        archive_member="m.tsv",
        license="x",
        expected_sha256="f" * 64,
    )
    assert inspect_mod.inspect(source, _result(path)).pin_matches is False


def test_inspect_counts_fasta_records(tmp_path: Path) -> None:
    path = tmp_path / "s.fasta"
    path.write_text(">one desc\nMKV\nAAA\n>two desc\nMM\n")
    source = SourceFile(
        source_name="T",
        version="0",
        subset="t",
        filename="s.fasta",
        license="x",
        kind="fasta",
    )
    info = inspect_mod.inspect(source, _result(path))
    assert info.fasta_records == 2
    assert info.fasta_first_header == ">one desc"


# ======================================================== 3. compaction opt-in


def test_profile_report_does_not_compact_by_default() -> None:
    """VACUUM FULL locks the table; on a full release that is not a default."""
    import inspect as _inspect

    from seq2lead.cli import profile_report

    option = _inspect.signature(profile_report).parameters["compact"].default
    assert option.default is False  # typer OptionInfo


def test_compact_flag_warns_about_the_lock() -> None:
    # Read from Click rather than from a rendering, so no terminal participates.
    # Absence is asserted here and not against help text, because a flag missing
    # from a help screen may be hidden rather than undeclared.
    declared = switches(app, "profile", "report")
    assert "--compact" in declared
    assert "--no-compact" not in declared  # opt-in, not opt-out
    assert "ACCESS EXCLUSIVE" in documented(app, "profile", "report")

    # And the warning reaches a help screen a user can actually read.
    status, help_screen = rendered("profile", "report")
    assert status == 0, help_screen
    shown = squash(help_screen)
    assert squash("--compact") in shown
    assert squash("ACCESS EXCLUSIVE") in shown


# ================================================= 3b. release inventory


def _inventory_module():
    """Load the generator by path; scripts/ is not an importable package."""
    import importlib.util
    from pathlib import Path as _Path  # Path is a TYPE_CHECKING-only import here

    path = _Path(__file__).resolve().parents[1] / "scripts/release/build_inventory.py"
    spec = importlib.util.spec_from_file_location("build_inventory", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_every_shipped_file_carries_a_licence_assignment() -> None:
    """The release inventory must account for every file git would ship.

    `build()` performs the refusals: a tracked file with no assignment, an
    assignment naming an untracked path, a self-excluded manifest that is also an
    entry, or a licence label with no `licence_basis` entry each raise SystemExit.
    Calling it here is what keeps that reconciliation true after this commit
    rather than only at the moment it was written.
    """
    built = _inventory_module().build()  # raises SystemExit if anything is unaccounted for
    recon = built["reconciliation"]
    assert recon["accounts_for_every_tracked_file"]
    assert recon["entries"] + len(recon["self_excluded"]) == recon["tracked_at_head"]
    # Every label is a single licence. Folding a reason into the label -- the old
    # "CC-BY-3.0 (verbatim licence text)" -- is what made the labels stop summing.
    assert recon["licence_labels_sum"] == recon["entries"]
    assert sum(built["by_licence"].values()) == built["files"]
    for licence in built["by_licence"]:
        assert licence in built["licence_basis"], licence
        assert "(" not in licence, f"compound licence label: {licence}"


def test_committed_inventory_matches_the_bytes_it_describes() -> None:
    """An inventory whose digests do not match its files records nothing.

    Two files were once edited after the inventory was generated, so it described
    bytes that no longer existed. This fails if that happens again.
    """
    module = _inventory_module()
    built = module.build()
    committed = json.loads(module.INVENTORY.read_text())
    fresh = {e["path"]: (e["sha256"], e["bytes"]) for e in built["entries"]}
    stored = {e["path"]: (e["sha256"], e["bytes"]) for e in committed["entries"]}
    assert set(fresh) == set(stored), {
        "only_on_disk": sorted(set(fresh) - set(stored)),
        "only_in_inventory": sorted(set(stored) - set(fresh)),
    }
    drifted = {
        p: {"recorded": stored[p], "actual": fresh[p]} for p in fresh if fresh[p] != stored[p]
    }
    assert not drifted, drifted


# ======================================================== 4. migrations


@pytest.mark.requires_db
def test_migrations_are_recorded_and_idempotent() -> None:
    from seq2lead.db.migrations import MIGRATIONS, applied_versions, apply_pending

    with transaction() as conn:
        apply_pending(conn)
    with connect() as conn:
        done = applied_versions(conn)
    assert {m.version for m in MIGRATIONS} <= done

    with transaction() as conn:
        again = apply_pending(conn)
    assert again == []  # nothing re-applied


@pytest.mark.requires_db
def test_baseline_migration_does_not_recreate_existing_tables() -> None:
    """Migration 0001 must never drop or rewrite a table that already exists."""
    from seq2lead.db.migrations import MIGRATIONS

    baseline = next(m for m in MIGRATIONS if m.version == 1)
    assert baseline.baseline_probe is not None
    with connect() as conn:
        row = conn.execute(baseline.baseline_probe).fetchone()
        recorded = conn.execute(
            "SELECT baselined FROM schema_migration WHERE version = 1"
        ).fetchone()
    assert row is not None and row[0] is not None  # table exists
    assert recorded is not None and recorded[0] is True  # recorded, not run


# ======================================================== manifest coherence


def test_m2_subsets_are_all_registered() -> None:
    for subset in M2_SUBSETS:
        assert subset in REGISTRY


def test_fasta_entry_uses_the_non_downloads_base_path() -> None:
    """The FASTA is not under /downloads/; getting this wrong 404s."""
    fasta = get("target_sequences")
    assert fasta.url == "https://www.bindingdb.org/rwd/bind/BindingDBTargetSequences.fasta"
    assert fasta.kind == "fasta"


def test_rolling_fasta_filename_is_flagged_in_its_note() -> None:
    """Its name carries no release marker, so the pin is the only version tie."""
    assert "no release marker" in get("target_sequences").note
