"""Housekeeping the identity tables: what expires, expires.

Three kinds of rows outlive their purpose and would otherwise pile up forever:

* **sessions** past ``expires_at`` (an idle one is removed when next presented,
  but one never presented again stays);
* **activation and reset tokens** past ``expires_at`` or already used;
* **accounts that were never activated**, once the grace period is over. That
  is the other half of requiring activation: an address someone typed by
  mistake must not stay reserved, and the person it belongs to must be able to
  register later.

:func:`purge` is the unit of work; :class:`MaintenanceScheduler` runs it every
``PYMONEY_MAINTENANCE_INTERVAL_MINUTES`` inside the API process. It needs no
book lock: it touches no ledger table.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import Engine, delete, or_, select

from ..store import identity, write_transaction

__all__ = ["MaintenanceScheduler", "PurgeReport", "purge"]

log = logging.getLogger("pymoney.maintenance")


@dataclass(frozen=True, slots=True)
class PurgeReport:
    sessions: int
    tokens: int
    unactivated_users: int

    @property
    def total(self) -> int:
        return self.sessions + self.tokens + self.unactivated_users


def purge(
    engine: Engine, *, unactivated_after: timedelta, now: datetime | None = None
) -> PurgeReport:
    now = now or datetime.now(timezone.utc)
    with write_transaction(engine) as conn:
        sessions = conn.execute(
            delete(identity.sessions).where(identity.sessions.c.expires_at <= now)
        ).rowcount
        tokens = conn.execute(
            delete(identity.user_tokens).where(
                or_(
                    identity.user_tokens.c.expires_at <= now,
                    identity.user_tokens.c.used_at.is_not(None),
                )
            )
        ).rowcount
        stale = [
            row.id
            for row in conn.execute(
                select(identity.users.c.id).where(
                    identity.users.c.activated_at.is_(None),
                    identity.users.c.created_at <= now - unactivated_after,
                )
            )
        ]
        users = 0
        if stale:
            # Nothing else can reference a never-activated user, but be explicit.
            conn.execute(
                delete(identity.user_tokens).where(
                    identity.user_tokens.c.user_id.in_(stale)
                )
            )
            conn.execute(
                delete(identity.sessions).where(identity.sessions.c.user_id.in_(stale))
            )
            users = conn.execute(
                delete(identity.users).where(identity.users.c.id.in_(stale))
            ).rowcount
    return PurgeReport(
        sessions=int(sessions), tokens=int(tokens), unactivated_users=int(users)
    )


class MaintenanceScheduler:
    """Runs :func:`purge` every ``interval`` on a daemon thread."""

    def __init__(
        self, engine: Engine, *, interval: timedelta, unactivated_after: timedelta
    ) -> None:
        if interval <= timedelta(0):
            raise ValueError("the interval must be positive")
        self.engine = engine
        self.interval = interval
        self.unactivated_after = unactivated_after
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.runs = 0

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, name="pymoney-maintenance", daemon=True
        )
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)
            self._thread = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _loop(self) -> None:
        while not self._stop.is_set():
            self.tick()
            if self._stop.wait(self.interval.total_seconds()):
                return

    def tick(self) -> PurgeReport | None:
        try:
            report = purge(self.engine, unactivated_after=self.unactivated_after)
        except Exception:
            log.exception("maintenance failed")
            return None
        self.runs += 1
        if report.total:
            log.info(
                "maintenance: removed %d expired session(s), %d token(s), %d never-activated account(s)",
                report.sessions,
                report.tokens,
                report.unactivated_users,
            )
        return report
