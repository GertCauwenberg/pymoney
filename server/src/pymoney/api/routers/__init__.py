"""HTTP routers, one module per resource."""

from __future__ import annotations

from . import (
    accounts,
    attachments,
    auth,
    books,
    commodities,
    payees,
    prices,
    reports,
    sync,
    system,
    tags,
    transactions,
)

__all__ = [
    "accounts",
    "attachments",
    "auth",
    "books",
    "commodities",
    "payees",
    "prices",
    "reports",
    "sync",
    "system",
    "tags",
    "transactions",
]
