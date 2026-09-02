"""Import a KMyMoney ``.kmy`` file.

    from pymoney.importers.kmymoney import import_kmymoney
    from pymoney.store import create_engine, create_schema, open_book

    engine = create_engine("sqlite+pysqlite:///ledger.sqlite")
    create_schema(engine)
    book = open_book(engine)
    report = import_kmymoney("~/money.kmy", book)
    print(report.summary())

Or from the command line::

    python -m pymoney.importers.kmymoney ~/money.kmy --database ledger.sqlite

The guarantee is the one that matters for a migration: the same accounts exist,
and every balance matches the figure KMyMoney displayed, to the minor unit. Where
that needed a correcting entry -- because KMyMoney stores split amounts as exact
fractions and PyMoney books whole cents -- the report says so, per account, with
both numbers.
"""

from __future__ import annotations

from .importer import ImportOptions, KmyImporter, import_kmymoney
from .model import (
    KmyAccount,
    KmyCommodity,
    KmyPayee,
    KmyPrice,
    KmySplit,
    KmyTransaction,
)
from .parser import KmyFile, KmyFormatError, detect_format
from .report import Adjustment, ImportReport, Level, Problem

__all__ = [
    "Adjustment",
    "ImportOptions",
    "ImportReport",
    "KmyAccount",
    "KmyCommodity",
    "KmyFile",
    "KmyFormatError",
    "KmyImporter",
    "KmyPayee",
    "KmyPrice",
    "KmySplit",
    "KmyTransaction",
    "Level",
    "Problem",
    "detect_format",
    "import_kmymoney",
]
