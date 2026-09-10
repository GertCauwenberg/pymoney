"""Shared hardening for multipart file uploads.

Two endpoints take a file from an untrusted client -- the KMyMoney importer and
the attachment store -- and both want the same guard: read in bounded chunks,
stop the moment the stream exceeds the configured limit, and never trust the
``Content-Length`` header (it is the client's claim, not a fact).
"""

from __future__ import annotations

from pathlib import Path
from typing import IO

from fastapi import HTTPException

__all__ = ["CHUNK", "read_limited", "stream_to_file"]

CHUNK = 1024 * 1024


def _too_large(limit_bytes: int) -> HTTPException:
    megabytes = max(1, limit_bytes // (1024 * 1024))
    return HTTPException(
        status_code=413, detail=f"the file is larger than {megabytes} MB"
    )


def read_limited(source: IO[bytes], limit_bytes: int) -> bytes:
    """Read ``source`` fully into memory, or 413 once it passes ``limit_bytes``.

    For uploads small enough to hold in memory (a receipt, a scan). The check
    is on bytes actually read, so a lying ``Content-Length`` changes nothing.
    """
    out = bytearray()
    while chunk := source.read(CHUNK):
        out.extend(chunk)
        if len(out) > limit_bytes:
            raise _too_large(limit_bytes)
    return bytes(out)


def stream_to_file(source: IO[bytes], destination: Path, limit_bytes: int) -> int:
    """Copy ``source`` to ``destination`` in chunks; 413 once past the limit.

    Returns the number of bytes written. For uploads too big to want in memory.
    """
    size = 0
    with destination.open("wb") as out:
        while chunk := source.read(CHUNK):
            size += len(chunk)
            if size > limit_bytes:
                raise _too_large(limit_bytes)
            out.write(chunk)
    return size
