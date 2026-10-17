"""Regression: the API must actually USE the Postgres store when
DATABASE_URL is set.

This guards against the exact production bug found on 2026-10-17: a stray
`store = SqliteStore(DB_PATH)` line below the selection if/else silently
overrode PostgresStore, so the API reported db=postgres while writing to
/tmp SQLite — every Render redeploy wiped all recorded runs while Neon
sat empty.
"""

import importlib

import pytest

from afrec.pg_store import PostgresStore


def test_postgres_store_selected_when_database_url_set(monkeypatch):
    import api.main as api_main
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pw@example.invalid/db")
    try:
        reloaded = importlib.reload(api_main)
        assert isinstance(reloaded.store, PostgresStore), (
            "DATABASE_URL is set but the API is not using PostgresStore — "
            "a stray SqliteStore assignment is overriding the selection again"
        )
        assert reloaded.DB_LABEL == "postgres"
    finally:
        monkeypatch.delenv("DATABASE_URL", raising=False)
        importlib.reload(api_main)


def test_sqlite_fallback_without_database_url(monkeypatch):
    import api.main as api_main
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("AFREC_API_DB", "/tmp/afrec_test_regression.sqlite3")
    try:
        reloaded = importlib.reload(api_main)
        assert not isinstance(reloaded.store, PostgresStore)
        assert reloaded.DB_LABEL == "/tmp/afrec_test_regression.sqlite3"
    finally:
        monkeypatch.delenv("AFREC_API_DB", raising=False)
        importlib.reload(api_main)


def test_exactly_one_store_assignment_after_selection(monkeypatch):
    """Static guard: after the if/else picks a store, nothing may assign to
    `store` again at module level. The 41363ce bug was a second assignment
    15 lines below the selection."""
    import inspect
    import api.main as api_main
    src = inspect.getsource(api_main)
    selection = src.index("if DATABASE_URL:")
    head = src[:selection]
    tail = src[selection:]
    assert "store" not in [l.split("=")[0].strip() for l in head.splitlines() if "=" in l and l.strip().startswith("store")], "no store assignment may precede the selection"
    # count module-level `store =` in the tail after the selection block
    after = tail[tail.index("app = FastAPI"):]
    assert "\nstore" not in after, "a second module-level `store` assignment would override the selection"
