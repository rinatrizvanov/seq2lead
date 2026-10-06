import os

import pytest


def _db_tests_disabled() -> bool:
    return os.environ.get("SEQ2LEAD_SKIP_DB_TESTS") == "1"


def pytest_runtest_setup(item: pytest.Item) -> None:
    """Allow opting out of DB-backed tests where no Postgres exists."""
    if "requires_db" in item.keywords and _db_tests_disabled():
        pytest.skip("SEQ2LEAD_SKIP_DB_TESTS=1")


@pytest.fixture(scope="session", autouse=True)
def _schema() -> None:
    """Create the raw-layer tables once, so read-only tests do not depend on ordering.

    A fresh CI database has no tables until something creates them. Without this,
    whether a read-only test passes would depend on an earlier test in the same
    run having written first.
    """
    if _db_tests_disabled():
        return
    from seq2lead.db import DatabaseUnreachable, transaction
    from seq2lead.db.schema import create_schema

    try:
        with transaction() as conn:
            create_schema(conn)
    except DatabaseUnreachable:
        # The requires_db tests report this themselves, with a remedy.
        return
