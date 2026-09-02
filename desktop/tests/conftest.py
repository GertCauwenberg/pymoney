"""Fixtures for the desktop suite.

The desktop is tested against the real API, in process: the same
:func:`pymoney.api.testing.test_app` the server's own suite uses, so a client
here talks to exactly what it would talk to in production. Qt runs offscreen.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pymoney.api.testing import Client, test_app


@pytest.fixture
def api(tmp_path: Path):
    """An application on a fresh database, with a fixed clock."""
    with test_app(tmp_path) as client:
        yield client


__all__ = ["Client", "api"]
