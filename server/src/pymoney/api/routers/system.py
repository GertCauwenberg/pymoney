"""Health and version. No authentication: a load balancer has no session."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import Engine, func, select

from ... import __version__
from ...store import schema
from ..deps import get_engine
from ..schemas import HealthOut

router = APIRouter(tags=["system"])


@router.get("/health", response_model=HealthOut, summary="Is this server working?")
def health(engine: Annotated[Engine, Depends(get_engine)]) -> HealthOut:
    """Touches the database, because a server that cannot reach its ledger is
    not healthy however well the process is running."""
    try:
        with engine.connect() as conn:
            books = int(
                conn.execute(select(func.count()).select_from(schema.books)).scalar_one()
            )
        state: str = "ok"
    except Exception:
        books, state = 0, "degraded"
    return HealthOut(
        status=state,  # type: ignore[arg-type]
        version=__version__,
        database=engine.url.render_as_string(hide_password=True),
        books=books,
    )


__all__ = ["router"]
