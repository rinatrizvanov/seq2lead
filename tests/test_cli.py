from typer.testing import CliRunner

from seq2lead import __version__
from seq2lead.cli import app

runner = CliRunner()


def test_version_command() -> None:
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert __version__ in result.stdout


def test_env_check_reports_toolchain() -> None:
    result = runner.invoke(app, ["env", "check"])
    assert result.exit_code == 0
    for field in ("python", "platform", "torch", "rdkit", "db target"):
        assert field in result.stdout


def test_db_ping_exits_nonzero_when_unreachable(monkeypatch: object) -> None:
    result = runner.invoke(
        app,
        ["db", "ping"],
        env={"SEQ2LEAD_DB_HOST": "127.0.0.1", "SEQ2LEAD_DB_PORT": "1"},
    )
    assert result.exit_code == 1


def test_asof_export_is_registered_and_documents_its_read_only_guarantee() -> None:
    """The export must be reachable from the repo, not only from a scratch script.

    An artifact that two snapshots are compared from is not reproducible if the
    only way to regenerate it is a script that was never committed.
    """
    result = runner.invoke(app, ["asof", "export", "--help"])
    assert result.exit_code == 0
    assert "--release-id" in result.stdout
    assert "--out" in result.stdout
    assert "READ ONLY" in result.stdout


def test_asof_export_requires_both_arguments() -> None:
    result = runner.invoke(app, ["asof", "export"])
    assert result.exit_code != 0
