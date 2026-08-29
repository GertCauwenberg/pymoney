"""HTTP, reduced to one function, so both clients and every test share it.

A :data:`Transport` takes ``(method, path, json_body, headers)`` and returns
``(status, headers, parsed_body)``. The default is urllib against a base URL
with a bearer token; tests hand in FastAPI's ``TestClient`` wrapped the same way,
so the clients are tested against the real API without a socket.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from typing import Any

from .client import ApiError, OfflineError

__all__ = ["Response", "Transport", "UrllibTransport", "raise_for_problem"]

Response = tuple[int, Mapping[str, str], Any]
Transport = Callable[[str, str, Any, Mapping[str, str]], Response]

TIMEOUT = 15.0


def raise_for_problem(status: int, body: Any) -> None:
    """Turn a non-2xx answer into :class:`ApiError` with the server's words."""
    if 200 <= status < 300:
        return
    type_, message = "HttpError", f"HTTP {status}"
    if isinstance(body, dict):
        problem = body.get("error")
        if isinstance(problem, dict):
            type_ = str(problem.get("type", type_))
            message = str(problem.get("message", message))
        elif "detail" in body:
            detail = body["detail"]
            type_ = "RequestValidationError" if isinstance(detail, list) else "HttpError"
            message = (
                "; ".join(
                    f"{'.'.join(str(p) for p in d.get('loc', [])[1:])}: {d.get('msg')}"
                    for d in detail
                )
                if isinstance(detail, list)
                else str(detail)
            )
    raise ApiError(status, type_, message)


class UrllibTransport:
    """The real thing: a base URL and, once logged in, a bearer token."""

    def __init__(self, base_url: str, *, timeout: float = TIMEOUT) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.token: str | None = None

    def __call__(
        self, method: str, path: str, body: Any, headers: Mapping[str, str]
    ) -> Response:
        data = None if body is None else json.dumps(body).encode("utf-8")
        sent = {"Accept": "application/json", **headers}
        if data is not None:
            sent["Content-Type"] = "application/json"
        if self.token:
            sent["Authorization"] = f"Bearer {self.token}"
        request = urllib.request.Request(
            self.base_url + path, data=data, method=method, headers=sent
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read()
                return (
                    response.status,
                    dict(response.headers),
                    json.loads(raw) if raw else None,
                )
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            try:
                parsed = json.loads(raw) if raw else None
            except json.JSONDecodeError:
                parsed = None
            return exc.code, dict(exc.headers), parsed
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as exc:
            raise OfflineError(f"{self.base_url}: {getattr(exc, 'reason', exc)}") from exc
