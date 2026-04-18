"""Schema + transaction tests for megalos_server.db."""

import pytest  # type: ignore[import-not-found]

from megalos_server import db

EXPECTED_COLUMNS = {
    "session_id": "TEXT",
    "workflow_type": "TEXT",
    "current_step": "TEXT",
    "step_data": "TEXT",
    "retry_counts": "TEXT",
    "step_visit_counts": "TEXT",
    "escalation": "TEXT",
    "artifact_checkpoints": "TEXT",
    "created_at": "TEXT",
    "updated_at": "TEXT",
    "completed_at": "TEXT",
    "called_session": "TEXT",
    "parent_session_id": "TEXT",
}


@pytest.fixture(autouse=True)
def fresh_schema_db(monkeypatch, _isolated_db):  # depends on conftest's _isolated_db → runs after it
    monkeypatch.setenv("MEGALOS_DB_PATH", ":memory:")
    db._reset_for_test()
    yield
    db._reset_for_test()


def _column_info():
    conn = db._get_conn()
    rows = conn.execute("PRAGMA table_info(sessions)").fetchall()
    return {row[1]: row[2] for row in rows}


def test_schema_has_expected_columns():
    db.init_schema()
    cols = _column_info()
    assert cols == EXPECTED_COLUMNS


def test_init_schema_is_idempotent():
    db.init_schema()
    db.init_schema()
    assert _column_info() == EXPECTED_COLUMNS


def _insert_row(conn, session_id):
    conn.execute(
        "INSERT INTO sessions (session_id, workflow_type, current_step, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (session_id, "wf", "step1", "2026-01-01", "2026-01-01"),
    )


def test_transaction_commits_on_clean_exit():
    db.init_schema()
    with db.transaction() as conn:
        _insert_row(conn, "s-commit")
    conn = db._get_conn()
    rows = conn.execute("SELECT session_id FROM sessions WHERE session_id=?", ("s-commit",)).fetchall()
    assert rows == [("s-commit",)]


def test_transaction_rolls_back_on_exception():
    db.init_schema()
    with pytest.raises(RuntimeError):
        with db.transaction() as conn:
            _insert_row(conn, "s-rollback")
            raise RuntimeError("boom")
    conn = db._get_conn()
    rows = conn.execute("SELECT session_id FROM sessions WHERE session_id=?", ("s-rollback",)).fetchall()
    assert rows == []
