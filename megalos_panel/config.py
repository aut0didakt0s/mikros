"""Panel run-output directory configuration.

runs_dir() returns the directory under which per-run JSON-lines record files
are written. Defaults to `./runs/` relative to the caller's current working
directory; override via the MEGALOS_PANEL_RUNS_DIR environment variable for
test fixtures, CI, or alternate on-disk layouts. The directory is gitignored
by project convention; callers are responsible for ensuring it exists before
writing.
"""

import os
from pathlib import Path


def runs_dir() -> Path:
    override = os.environ.get("MEGALOS_PANEL_RUNS_DIR")
    if override:
        return Path(override)
    return Path("./runs/")
