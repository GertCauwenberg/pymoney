"""``python -m pymoney`` — the module's own smoke test and version probe.

A real CLI belongs in a later phase; this is deliberately tiny.  It exists so
that an image built from the current tree has *something* useful to run -- a
Docker ``HEALTHCHECK`` can invoke it, a CI job can use it as a final sanity
gate, and a user poking at a release can verify that the package imports and
its invariants hold.

The self-check posts a handful of transactions, runs the reports, and calls
``check_integrity``.  It is fast (<100 ms) and catches any packaging or
environment problem that would only otherwise surface when the server starts.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from fractions import Fraction

from . import __version__
from .core import (
    AccountType,
    Book,
    Commodity,
    LegSpec,
    ManualClock,
    balance_sheet,
    income_statement,
)


def self_check(verbose: bool = False) -> int:
    """Build a small book, run the reports, assert everything balances."""
    book = Book(
        name="self-check",
        base_currency=Commodity.currency("EUR", symbol="\u20ac"),
        clock=ManualClock(),
    )
    cash = book.add_account("Cash", AccountType.CASH)
    food = book.add_account("Food", AccountType.EXPENSE)
    rent = book.add_account("Rent", AccountType.EXPENSE)
    opening = book.add_account("Opening", AccountType.EQUITY)

    book.post(
        date(2026, 1, 1),
        [LegSpec.of(cash.id, "2500.00"), LegSpec.of(opening.id)],
        memo="Opening balance",
    )
    third = Fraction(10, 3)
    book.post(
        date(2026, 1, 2),
        [
            LegSpec.of(cash.id, "-10.00"),
            LegSpec.of(food.id, third),
            LegSpec.of(food.id, third),
            LegSpec.of(rent.id, third),
        ],
        memo="The 10/3 problem",
    )

    problems = book.check_integrity()
    if problems:
        print("integrity check failed:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1

    policy = book.default_policy(date(2026, 1, 31))
    sheet = balance_sheet(book, policy)
    pnl = income_statement(book, policy, start=date(2026, 1, 1))

    if verbose:
        print(f"pymoney {__version__}")
        print(f"  revision     {book.revision}")
        print(f"  transactions {len(book.transactions())}")
        print(f"  net worth    {sheet.net_worth}")
        print(f"  period net   {pnl.net}")
    else:
        print(f"pymoney {__version__}: OK")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m pymoney",
        description="PyMoney self-check and version probe.",
    )
    parser.add_argument("--version", action="store_true", help="print version and exit")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    if args.version:
        print(__version__)
        return 0
    return self_check(verbose=args.verbose)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
