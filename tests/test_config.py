import pytest

from seq2lead.config import DEFAULTS, DbConfig


def test_defaults_apply_when_env_is_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in DEFAULTS:
        monkeypatch.delenv(key, raising=False)
    cfg = DbConfig.from_env()
    assert cfg.host == "localhost"
    assert cfg.port == 5433
    assert cfg.dbname == "seq2lead"


def test_env_overrides_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SEQ2LEAD_DB_HOST", "db.example")
    monkeypatch.setenv("SEQ2LEAD_DB_PORT", "6000")
    cfg = DbConfig.from_env()
    assert cfg.host == "db.example"
    assert cfg.port == 6000


def test_describe_never_leaks_the_password(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SEQ2LEAD_DB_PASSWORD", "hunter2-do-not-print")
    cfg = DbConfig.from_env()
    assert "hunter2-do-not-print" not in cfg.describe()
    assert cfg.connect_kwargs["password"] == "hunter2-do-not-print"


def test_password_with_dsn_metacharacters_survives_intact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A `key=value` DSN would mis-parse this; keyword arguments carry it verbatim."""
    awkward = "p a s s'w\\ord=x"
    monkeypatch.setenv("SEQ2LEAD_DB_PASSWORD", awkward)
    assert DbConfig.from_env().connect_kwargs["password"] == awkward


def test_connect_kwargs_covers_exactly_the_psycopg_parameters() -> None:
    cfg = DbConfig.from_env()
    assert set(cfg.connect_kwargs) == {"host", "port", "dbname", "user", "password"}
    assert isinstance(cfg.connect_kwargs["port"], int)
