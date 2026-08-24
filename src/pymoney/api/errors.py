"""Domain errors as HTTP responses.

One mapping, in one place, so a new handler cannot invent its own status code
for "not found". Every error body has the same shape, because a client should be
able to parse failures without special-casing the endpoint:

    {"error": {"type": "ConflictError", "message": "...", "status": 409}}

The mapping is ordered most specific first; subclasses of ValidationError must
come before it.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

from ..core.errors import (
    CommandError,
    CommodityMismatchError,
    ConflictError,
    ImmutableError,
    NoPriceError,
    NotFoundError,
    PyMoneyError,
    UnbalancedTransactionError,
    ValidationError,
)

__all__ = ["STATUS_FOR", "install_error_handlers", "problem"]

# Starlette renamed this status constant; 422 is the number either way.
UNPROCESSABLE = 422

STATUS_FOR: tuple[tuple[type[Exception], int], ...] = (
    (NotFoundError, status.HTTP_404_NOT_FOUND),
    # A reconciled transaction is not "bad input": the request was understood and
    # refused because of the ledger's state.
    (ImmutableError, status.HTTP_409_CONFLICT),
    (ConflictError, status.HTTP_409_CONFLICT),
    (UnbalancedTransactionError, UNPROCESSABLE),
    (CommodityMismatchError, UNPROCESSABLE),
    (NoPriceError, UNPROCESSABLE),
    (ValidationError, UNPROCESSABLE),
    (CommandError, status.HTTP_400_BAD_REQUEST),
    (PyMoneyError, status.HTTP_500_INTERNAL_SERVER_ERROR),
)


def status_for(error: Exception) -> int:
    for kind, code in STATUS_FOR:
        if isinstance(error, kind):
            return code
    return status.HTTP_500_INTERNAL_SERVER_ERROR  # pragma: no cover


def problem(error: Exception, *, status_code: int | None = None) -> JSONResponse:
    code = status_code or status_for(error)
    return JSONResponse(
        status_code=code,
        content={
            "error": {
                "type": type(error).__name__,
                "message": str(error),
                "status": code,
            }
        },
    )


def install_error_handlers(app: FastAPI) -> None:
    async def handle(request: Request, exc: Exception) -> JSONResponse:
        del request
        return problem(exc)

    # Registered against the base class: FastAPI dispatches on the most specific
    # registered type, and one handler covers the tree.
    app.add_exception_handler(PyMoneyError, handle)

    # ValueError is what the store raises for a rate that will not fit in a
    # BIGINT column, which is the client's problem, not a server fault.
    async def handle_value_error(request: Request, exc: Exception) -> JSONResponse:
        del request
        return problem(exc, status_code=UNPROCESSABLE)

    app.add_exception_handler(ValueError, handle_value_error)


ErrorHandler = Callable[[Request, Exception], Awaitable[JSONResponse]]
