"""Throttling the authentication endpoints.

Every one of them is a lever for someone who is not logged in: ``/login`` runs
a deliberately slow password hash per attempt, so it is both a brute-force
target and a cheap CPU/memory amplifier; ``/register``, ``/forgot-password`` and
``/resend-activation`` each send an email to whatever address is in the body.
None of that needs to be fast, so none of it may be unlimited.

The limiter is a sliding window per key, in process memory. That is the right
size for this server: it runs as a single worker (see the README on one writer
per book), so there is no second process to share a counter with, and a
restart forgetting the counters costs an attacker nothing worth having. Two
keys are checked for every attempt:

* the **client address**, so one machine cannot hammer many accounts;
* the **address in the request** (an email, casefolded), so many machines
  cannot hammer one account, and nobody can flood one mailbox.

A refused request is a 429 with ``Retry-After``. The limits are settings
(``PYMONEY_AUTH_RATE_*``); zero turns a limit off, for a test that needs to log
in five hundred times.

Behind a reverse proxy every client shares the proxy's address, which would
throttle the whole household as one user. ``PYMONEY_CLIENT_IP_HEADER`` names
the header the proxy sets (``X-Forwarded-For``); it is trusted only when set,
because a client can write that header too and must not be allowed to pick its
own bucket.
"""

from __future__ import annotations

import threading
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from math import ceil
from time import monotonic
from typing import Annotated, Any

from fastapi import Depends, HTTPException, Request, status

from .settings import Settings

__all__ = [
    "THROTTLED_RESPONSE",
    "AuthThrottle",
    "RateLimiter",
    "client_address",
    "get_throttle",
]

THROTTLED_RESPONSE: dict[int | str, dict[str, Any]] = {
    429: {
        "description": (
            "Too many attempts from this client or against this address; "
            "`Retry-After` says when to try again."
        )
    },
}
"""For a route's ``responses=``: the documented shape of a refusal."""


@dataclass
class RateLimiter:
    """At most ``limit`` hits per key in any window of ``seconds``."""

    limit: int
    seconds: float
    now: Callable[[], float] = monotonic
    _hits: dict[str, deque[float]] = field(default_factory=dict, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    @property
    def enabled(self) -> bool:
        return self.limit > 0

    def retry_after(self, key: str) -> float:
        """Seconds until ``key`` may try again; 0 when it may right now.

        Does not record a hit: a refused attempt must not extend the penalty,
        or an attacker could lock the genuine user out for good by retrying.
        """
        if not self.enabled:
            return 0.0
        with self._lock:
            window = self._window(key)
            if len(window) < self.limit:
                return 0.0
            return max(0.0, window[0] + self.seconds - self.now())

    def hit(self, key: str) -> None:
        """Record an attempt for ``key``."""
        if not self.enabled:
            return
        with self._lock:
            self._window(key).append(self.now())

    def _window(self, key: str) -> deque[float]:
        cutoff = self.now() - self.seconds
        if len(self._hits) > 10_000:
            # Keep the table from growing with every address ever seen.
            for stale in [k for k, w in self._hits.items() if not w or w[-1] <= cutoff]:
                del self._hits[stale]
        window = self._hits.setdefault(key, deque())
        while window and window[0] <= cutoff:
            window.popleft()
        return window


class AuthThrottle:
    """The limiters the auth router consults, built once per application."""

    def __init__(self, settings: Settings, *, now: Callable[[], float] = monotonic):
        minute = 60.0
        self.login_by_client = RateLimiter(
            settings.auth_rate_login_per_client, minute, now
        )
        self.login_by_account = RateLimiter(
            settings.auth_rate_login_per_account, minute, now
        )
        self.mail_by_client = RateLimiter(settings.auth_rate_mail_per_client, minute, now)
        self.mail_by_address = RateLimiter(
            settings.auth_rate_mail_per_address, minute, now
        )

    def check(
        self, request: Request, settings: Settings, *, login: bool, who: str
    ) -> None:
        """Refuse with 429 if either bucket is full; otherwise count the attempt.

        Counted *before* the work, not after a failure: the cost we are
        protecting -- a password hash, an email -- is paid whether or not the
        attempt succeeds, and so is the information it would leak.
        """
        by_client = self.login_by_client if login else self.mail_by_client
        by_subject = self.login_by_account if login else self.mail_by_address
        client = client_address(request, settings)
        subject = who.strip().casefold()
        wait = max(by_client.retry_after(client), by_subject.retry_after(subject))
        if wait > 0:
            seconds = max(1, ceil(wait))
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"too many attempts; try again in {seconds} second(s)",
                headers={"Retry-After": str(seconds)},
            )
        by_client.hit(client)
        by_subject.hit(subject)


def client_address(request: Request, settings: Settings) -> str:
    """Who is asking, as far as we can tell.

    The socket's peer address, unless ``client_ip_header`` is configured, in
    which case the first address in that header -- the one the proxy received
    the connection from, by the X-Forwarded-For convention.
    """
    header = settings.client_ip_header
    if header:
        forwarded = request.headers.get(header)
        if forwarded:
            return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def get_throttle(request: Request) -> AuthThrottle:
    return request.app.state.throttle


Throttle = Annotated[AuthThrottle, Depends(get_throttle)]
