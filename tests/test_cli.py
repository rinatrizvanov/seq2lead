from typer.testing import CliRunner

from cli_help import documented, rendered, squash, switches
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
    # What the command declares, read from Click rather than from a rendering, so
    # no terminal participates -- see tests/cli_help.py for why that matters.
    assert {"--release-id", "--out"} <= switches(app, "asof", "export")
    assert "READ ONLY" in documented(app, "asof", "export")

    # And it reaches a help screen a user can actually read, which declarations
    # alone cannot show: a parameter can be declared and hidden.
    status, help_screen = rendered("asof", "export")
    assert status == 0, help_screen
    shown = squash(help_screen)
    for text in ("--release-id", "--out", "READ ONLY"):
        assert squash(text) in shown, text


def test_asof_export_requires_both_arguments() -> None:
    result = runner.invoke(app, ["asof", "export"])
    assert result.exit_code != 0
