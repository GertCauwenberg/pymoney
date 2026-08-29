"""The desktop client: one interface, an online and an offline implementation.

    from pymoney.desktop import OfflineClient, RemoteClient, UrllibTransport

    transport = UrllibTransport("http://127.0.0.1:8000")
    client = RemoteClient(transport)                       # every write is a request
    client = OfflineClient(transport, "~/.local/share/pymoney/replica.sqlite")   # local replica + outbox

The UI (:mod:`pymoney.desktop.ui`) only ever sees :class:`LedgerClient`.
"""

from __future__ import annotations

from .client import (
    ApiError,
    ConnectionStatus,
    LedgerClient,
    OfflineError,
    QueuedCommand,
    Rejection,
    ReportOptions,
)
from .offline import OfflineClient
from .remote import RemoteClient
from .transport import Transport, UrllibTransport

__all__ = [
    "ApiError",
    "ConnectionStatus",
    "LedgerClient",
    "OfflineClient",
    "OfflineError",
    "QueuedCommand",
    "Rejection",
    "RemoteClient",
    "ReportOptions",
    "Transport",
    "UrllibTransport",
]
