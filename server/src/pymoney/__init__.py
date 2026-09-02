"""PyMoney: double-entry accounting that runs everywhere.

``pymoney.core`` is the pure-Python domain layer: no database, no network, no UI.
Later packages (``pymoney.store``, ``pymoney.api``, and the separate ``pymoney_desktop`` distribution) build on
it without changing its rules.
"""

from __future__ import annotations

__version__ = "0.1.0"
