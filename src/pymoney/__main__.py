"""``python -m pymoney`` -- report what this installation can do.

There is no server yet, so the module entry point is a diagnostic rather than an
application.  That is deliberately useful: it is the smoke test for the
container image and for a fresh checkout, and it will not lie about components
that do not exist.
"""

from __future__ import annotations

import importlib
import platform
import sys
from collections.abc import Sequence

from pymoney import __version__

# Planned packages, in the order they are being built.  Each becomes importable
# as the corresponding phase lands; until then it is honestly reported missing.
_COMPONENTS = (
    ("pymoney.core", "domain model: accounts, transactions, prices, reports"),
    ("pymoney.store", "persistence: SQLAlchemy over SQLite and PostgreSQL"),
    ("pymoney.importers.kmymoney", "importer: KMyMoney .kmy files"),
    ("pymoney.quotes", "price quotes: ECB, Yahoo, plugins, scheduler"),
    ("pymoney.api", "HTTP API: FastAPI, commands in, revisions out"),
    ("pymoney.desktop.ui", "desktop client: PySide6 (python -m pymoney.desktop)"),
)


def _available(module: str) -> bool:
    """Whether a component can actually be used.

    Imports rather than just locating: ``pymoney.store`` ships in the wheel but
    needs the ``store`` extra to work, and reporting it present when importing it
    would fail is exactly the kind of lie this command exists not to tell.
    """
    try:
        importlib.import_module(module)
    except ImportError:
        return False
    return True


def main(argv: Sequence[str] | None = None) -> int:
    """Print a component inventory.  Always succeeds if the core imports."""
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in {"-V", "--version"}:
        print(__version__)
        return 0

    print(f"PyMoney {__version__}")
    print(f"Python {platform.python_version()} on {platform.system()}")
    print()
    for module, description in _COMPONENTS:
        mark = "ok     " if _available(module) else "missing"
        print(f"  [{mark}] {module:<26} {description}")
    print()
    print("Run the API with: uvicorn pymoney.api:app")
    print("Import from KMyMoney with: python -m pymoney.importers.kmymoney FILE")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
