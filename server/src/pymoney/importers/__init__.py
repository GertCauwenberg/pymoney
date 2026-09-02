"""Importers: getting existing data into PyMoney.

A migration is the first thing a new user does and the only chance to earn their
trust, so these are held to a higher standard than a convenience script: nothing
is dropped silently, every approximation is reported, and the result is verified
against what the source application displayed.
"""

from __future__ import annotations
