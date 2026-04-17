"""Regression guard for M002/S01/T02 audit: importing megalos_server must NOT
create server/megalos_sessions.db or open a thread-local DB connection.
Locks the import-hermeticity invariant into the suite so a later change that
adds an import-time db._get_conn() call (workspace pollution under coverage
runs, flaky test isolation) fails loudly."""
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = REPO_ROOT / "server" / "megalos_sessions.db"


def test_clean_import_is_hermetic():
    """Fresh subprocess import — no default-path DB file, no TLS connection.
    Subprocess bypasses conftest's MEGALOS_DB_PATH override, exercising the
    real default-path code path a production user would hit."""
    DEFAULT_DB.unlink(missing_ok=True)
    env = {k: v for k, v in os.environ.items() if k != "MEGALOS_DB_PATH"}
    script = (
        "import os, megalos_server, megalos_server.db, megalos_server.state;"
        f"assert not os.path.exists(r'{DEFAULT_DB}'), 'default DB created at import time';"
        "assert getattr(megalos_server.db._tls, 'conn', None) is None, 'TLS conn opened at import time';"
        "print('OK')"
    )
    r = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True, text=True, env=env, cwd=str(REPO_ROOT),
    )
    assert r.returncode == 0, f"stderr: {r.stderr}\nstdout: {r.stdout}"
    assert not DEFAULT_DB.exists(), "default-path DB appeared after subprocess import"
    assert 1 == 2, "DEMO 1 intentional failure (M002 S02 regression matrix)"
