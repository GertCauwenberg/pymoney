"""HTTP, reduced to one call, so both clients and every test share it.

A :class:`Transport` takes ``(method, path, json_body, headers)`` and returns
``(status, headers, parsed_body)``. A statement upload is the one thing that is
not JSON, so the call also accepts ``files=`` -- a mapping of form field to
``(filename, bytes, content-type)`` -- and then sends ``body`` as the string
form fields alongside. Everything else passes ``files=None`` and does not
notice.

The default is urllib against a base URL with a bearer token; tests hand in
FastAPI's ``TestClient`` wrapped the same way, so the clients are exercised
against the real API without a socket.
"""

from __future__ import annotations

import json
import secrets
import urllib.error
import urllib.request
from collections.abc import Mapping
from typing import Any, Protocol

from .client import ApiError, OfflineError

__all__ = [
    "FilePart",
    "Response",
    "Transport",
    "UrllibTransport",
    "download",
    "raise_for_problem",
]

Response = tuple[int, Mapping[str, str], Any]
FilePart = tuple[str, bytes, str]
"""``(filename, content, content-type)`` for one part of a multipart upload."""

TIMEOUT = 30.0


class Transport(Protocol):
    """How a client talks to the server. One call; JSON in, JSON out -- unless
    ``files`` is given, and then the body is the string form fields."""

    def __call__(
        self,
        method: str,
        path: str,
        body: Any = None,
        headers: Mapping[str, str] = {},
        *,
        files: Mapping[str, FilePart] | None = None,
        raw: bool = False,
    ) -> Response: ...


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


def download(transport: Transport, path: str) -> bytes:
    """GET ``path`` and return the raw body, raising on a non-2xx answer.

    For binary responses -- attachment blobs -- where ``raise_for_problem``'s
    JSON assumptions do not hold; an error body is small enough to decode here.
    """
    status, _headers, content = transport("GET", path, raw=True)
    if not (200 <= status < 300):
        import json as _json

        parsed = None
        if isinstance(content, bytes | bytearray):
            try:
                parsed = _json.loads(content)
            except ValueError:
                parsed = None
        raise_for_problem(status, parsed)
    if isinstance(content, bytes):
        return content
    return bytes(content or b"")


def encode_multipart(
    fields: Mapping[str, str], files: Mapping[str, FilePart]
) -> tuple[bytes, str]:
    """A ``multipart/form-data`` body and its ``Content-Type`` header value."""
    boundary = f"----pymoney{secrets.token_hex(16)}"
    out = bytearray()
    for name, value in fields.items():
        out += f"--{boundary}\r\n".encode()
        out += f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode()
        out += value.encode("utf-8") + b"\r\n"
    for name, (filename, content, content_type) in files.items():
        out += f"--{boundary}\r\n".encode()
        out += (
            f'Content-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'
        ).encode()
        out += f"Content-Type: {content_type}\r\n\r\n".encode()
        out += content + b"\r\n"
    out += f"--{boundary}--\r\n".encode()
    return bytes(out), f"multipart/form-data; boundary={boundary}"


class UrllibTransport:
    """The real thing: a base URL and, once logged in, a bearer token."""

    def __init__(self, base_url: str, *, timeout: float = TIMEOUT) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.token: str | None = None

    def __call__(
        self,
        method: str,
        path: str,
        body: Any = None,
        headers: Mapping[str, str] = {},
        *,
        files: Mapping[str, FilePart] | None = None,
        raw: bool = False,
    ) -> Response:
        sent = {"Accept": "*/*" if raw else "application/json", **headers}
        if files is not None:
            data, content_type = encode_multipart(dict(body or {}), files)
            sent["Content-Type"] = content_type
        elif body is None:
            data = None
        else:
            data = json.dumps(body).encode("utf-8")
            sent["Content-Type"] = "application/json"
        if self.token:
            sent["Authorization"] = f"Bearer {self.token}"
        request = urllib.request.Request(
            self.base_url + path, data=data, method=method, headers=sent
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                content = response.read()
                if raw:
                    return response.status, dict(response.headers), content
                return (
                    response.status,
                    dict(response.headers),
                    json.loads(content) if content else None,
                )
        except urllib.error.HTTPError as exc:
            content = exc.read()
            try:
                parsed = json.loads(content) if content else None
            except json.JSONDecodeError:
                parsed = None
            return exc.code, dict(exc.headers), parsed
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as exc:
            raise OfflineError(f"{self.base_url}: {getattr(exc, 'reason', exc)}") from exc
