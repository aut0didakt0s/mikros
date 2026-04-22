"""Permanent AST-based import allowlist guard for dryrun.py.

Anti-drift guard for the no-duplication architectural constraint: if a
future contributor quietly adds ``from megalos_server.tools import
_resolve_session`` or ``import megalos_server; megalos_server.tools.X(...)``,
this test fires. dryrun.py is allowed exactly one public entry point into
the server package: ``from megalos_server import create_app``.
"""

import ast
from pathlib import Path

ALLOWED_STDLIB = {"argparse", "asyncio", "os", "pathlib", "sys", "yaml"}
ALLOWED_MEGALOS_NAMES = {"create_app"}

DRYRUN_PATH = Path(__file__).resolve().parent.parent / "megalos_server" / "dryrun.py"


def test_dryrun_import_allowlist() -> None:
    """dryrun.py imports only from the stdlib + create_app allowlist."""
    source = DRYRUN_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)

    offenders: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.split(".")[0]
                if top == "megalos_server":
                    # Bare ``import megalos_server`` or ``import megalos_server.X``
                    # — both are backdoors for attribute reach-ins.
                    offenders.append(f"import {alias.name}")
                elif top not in ALLOWED_STDLIB:
                    offenders.append(f"import {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            if node.level > 0:
                # Relative imports (``from . import X``, ``from .tools import Y``)
                # bypass absolute-path classification — reject outright, no
                # legitimate use in dryrun.py.
                names = ", ".join(alias.name for alias in node.names)
                offenders.append(f"from {'.' * node.level}{node.module or ''} import {names}")
                continue
            module = node.module or ""
            if module == "megalos_server":
                for alias in node.names:
                    if alias.name not in ALLOWED_MEGALOS_NAMES:
                        offenders.append(f"from megalos_server import {alias.name}")
            elif module.startswith("megalos_server."):
                offenders.append(f"from {module} import ...")
            else:
                top = module.split(".")[0]
                if top and top not in ALLOWED_STDLIB:
                    offenders.append(f"from {module} import ...")

    assert not offenders, f"dryrun.py violates import allowlist: {offenders}"
