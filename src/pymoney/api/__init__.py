"""The PyMoney HTTP API.

    from pymoney.api import create_app
    app = create_app()

    uvicorn pymoney.api:app --host 0.0.0.0 --port 8000

The domain does not know this package exists. Every endpoint is a thin shell
around a command or a query on :class:`~pymoney.core.book.Book`, and the wire
schemas in :mod:`pymoney.api.schemas` are deliberately separate from the domain
objects, because serialisation has different concerns from a ledger.
"""

from __future__ import annotations

from .app import create_app
from .registry import BookRegistry
from .settings import Settings

__all__ = ["BookRegistry", "Settings", "create_app"]

# Importing the submodule above also bound it as the package attribute ``app``,
# which would shadow the lazy application below and hand uvicorn a module
# ("TypeError: 'module' object is not callable"). Unbind it; the submodule stays
# importable through sys.modules.
del app  # type: ignore[name-defined]  # noqa: F821


def __getattr__(name: str):
    """Lazily build the default app, so `uvicorn pymoney.api:app` works.

    A module-level ``app = create_app()`` would open a database connection on
    import, which is wrong for anything that merely imports this package -- a
    test, a migration, ``python -m pymoney``.
    """
    if name == "app":
        application = create_app()
        globals()["app"] = application
        return application
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
