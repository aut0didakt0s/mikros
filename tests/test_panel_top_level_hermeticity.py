"""Structural enforcement of the SDK-free invariant at the public surface.

The paired ``test_panel_adapters_hermeticity`` test asserts the invariant at
the ``megalos_panel.adapters`` package boundary. This test covers the
top-level ``megalos_panel`` module — the depth at which users consume the
public API via ``from megalos_panel import panel_query``. If ``__init__.py``
ever starts re-exporting an adapter class (or any module that imports a
provider SDK at top-level), this test will flag the regression.

The check runs in a fresh subprocess because pytest's own suite almost
certainly has the SDKs in ``sys.modules`` already — in-process inspection
would measure suite state, not import-surface state.
"""

import subprocess
import sys


def test_panel_top_level_import_is_sdk_free() -> None:
    script = (
        "import sys\n"
        "from megalos_panel import panel_query  # noqa: F401\n"
        "sdks = [m for m in ('anthropic', 'openai') if m in sys.modules]\n"
        "assert not sdks, f'megalos_panel transitively imported: {sdks}'\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"SDK-free invariant failed.\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )
