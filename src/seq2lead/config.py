"""Configuration, read from the environment with documented defaults.

Deliberately dependency-free: config must be importable before anything heavy
(torch, rdkit) is loaded, so that `seq2lead db ping` stays fast.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

DEFAULTS: dict[str, str] = {
    "SEQ2LEAD_DB_HOST": "localhost",
    "SEQ2LEAD_DB_PORT": "5433",
    "SEQ2LEAD_DB_NAME": "seq2lead",
    "SEQ2LEAD_DB_USER": "seq2lead",
    "SEQ2LEAD_DB_PASSWORD": "seq2lead",
    # Which schema tables resolve to. Tests that write fixtures point this at a
    # throwaway schema so they never touch the curated corpus.
    "SEQ2LEAD_DB_SCHEMA": "public",
}


def _env(key: str) -> str:
    return os.environ.get(key, DEFAULTS[key])


@dataclass(frozen=True)
class DbConfig:
    host: str
    port: int
    dbname: str
    user: str
    password: str
    schema: str = "public"

    @classmethod
    def from_env(cls) -> DbConfig:
        return cls(
            host=_env("SEQ2LEAD_DB_HOST"),
            port=int(_env("SEQ2LEAD_DB_PORT")),
            dbname=_env("SEQ2LEAD_DB_NAME"),
            user=_env("SEQ2LEAD_DB_USER"),
            password=_env("SEQ2LEAD_DB_PASSWORD"),
            schema=_env("SEQ2LEAD_DB_SCHEMA"),
        )

    @property
    def connect_kwargs(self) -> dict[str, str | int]:
        """Parameters for `psycopg.connect()`.

        Returned as keyword arguments rather than a DSN string on purpose: a
        password containing a space, a quote or a backslash would be mis-parsed
        or silently truncated if interpolated into `key=value` text.
        """
        return {
            "host": self.host,
            "port": self.port,
            "dbname": self.dbname,
            "user": self.user,
            "password": self.password,
        }

    @property
    def is_isolated(self) -> bool:
        """True when pointed at a throwaway schema rather than the corpus."""
        return self.schema != "public"

    def describe(self) -> str:
        """Connection target without the password — safe to print or log."""
        target = f"{self.user}@{self.host}:{self.port}/{self.dbname}"
        return target if self.schema == "public" else f"{target} (schema {self.schema})"
