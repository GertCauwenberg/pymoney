"""Registration, login, logout, and who am I.

Session cookies, with a bearer token for clients that cannot hold cookies. The
shape is chosen so OIDC can slot in later without changing any other router:
everything downstream depends on :class:`~pymoney.api.deps.Actor`, whose
``subject`` is the only thing grants are keyed on. An OIDC deployment replaces
this module and leaves the rest untouched.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import Engine, select

from ...core.ids import new_id
from ...store import identity
from ..deps import CurrentUser, get_engine, get_settings_dep
from ..schemas import LoginIn, RegisterIn, SessionOut, UserOut
from ..security import (
    PasswordHash,
    hash_password,
    hash_token,
    new_csrf_token,
    new_session_token,
    verify_password,
)
from ..settings import Settings

# Starlette renamed this status constant; 422 is the number either way.
UNPROCESSABLE = 422

router = APIRouter(prefix="/auth", tags=["auth"])


def _set_cookie(
    response: Response, token: str, settings: Settings, expires: datetime
) -> None:
    response.set_cookie(
        settings.cookie_name,
        token,
        httponly=True,
        secure=settings.cookie_secure,
        samesite=settings.cookie_samesite,  # type: ignore[arg-type]
        domain=settings.cookie_domain,
        expires=expires,
        path="/",
    )


def _start_session(
    engine: Engine,
    settings: Settings,
    *,
    user_id,
    user_agent: str,
    response: Response,
) -> tuple[str, str, datetime]:
    token = new_session_token()
    csrf = new_csrf_token()
    now = datetime.now(timezone.utc)
    expires = now + timedelta(hours=settings.session_lifetime_hours)
    with engine.begin() as conn:
        conn.execute(
            identity.sessions.insert().values(
                id=new_id(),
                user_id=user_id,
                token_hash=hash_token(token),
                csrf_token=csrf,
                created_at=now,
                expires_at=expires,
                last_seen_at=now,
                user_agent=user_agent[:500],
            )
        )
    _set_cookie(response, token, settings, expires)
    return token, csrf, expires


@router.post(
    "/register",
    response_model=SessionOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create an account and log in",
)
def register(
    body: RegisterIn,
    request: Request,
    response: Response,
    engine: Annotated[Engine, Depends(get_engine)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> SessionOut:
    if not settings.allow_registration:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="registration is closed on this server",
        )
    email = body.email.strip()
    if "@" not in email:
        raise HTTPException(
            status_code=UNPROCESSABLE,
            detail="that does not look like an email address",
        )
    key = email.casefold()
    now = datetime.now(timezone.utc)
    stored = hash_password(body.password)
    user_id = new_id()
    with engine.begin() as conn:
        taken = conn.execute(
            select(identity.users.c.id).where(identity.users.c.email_key == key)
        ).scalar_one_or_none()
        if taken is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="that email address is already registered",
            )
        conn.execute(
            identity.users.insert().values(
                id=user_id,
                email=email,
                email_key=key,
                display_name=body.display_name or email.split("@")[0],
                password_hash=stored.digest,
                password_salt=stored.salt,
                password_params=stored.params,
                active=True,
                created_at=now,
                updated_at=now,
            )
        )
    token, csrf, expires = _start_session(
        engine,
        settings,
        user_id=user_id,
        user_agent=request.headers.get("user-agent", ""),
        response=response,
    )
    return SessionOut(
        user=UserOut(
            id=user_id, email=email, display_name=body.display_name or email.split("@")[0]
        ),
        csrf_token=csrf,
        token=token,
        expires_at=expires,
    )


@router.post("/login", response_model=SessionOut, summary="Log in")
def login(
    body: LoginIn,
    request: Request,
    response: Response,
    engine: Annotated[Engine, Depends(get_engine)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> SessionOut:
    with engine.connect() as conn:
        row = conn.execute(
            select(identity.users).where(
                identity.users.c.email_key == body.email.strip().casefold()
            )
        ).one_or_none()

    stored = (
        None
        if row is None
        else PasswordHash(
            digest=row.password_hash or b"",
            salt=row.password_salt or b"",
            params=row.password_params or "",
        )
    )
    # Always verify, even when the user does not exist, so the response time does
    # not reveal which addresses are registered.
    ok = verify_password(body.password, stored)
    if row is None or not ok or not row.active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="wrong email or password",
        )

    token, csrf, expires = _start_session(
        engine,
        settings,
        user_id=row.id,
        user_agent=request.headers.get("user-agent", ""),
        response=response,
    )
    return SessionOut(
        user=UserOut(id=row.id, email=row.email, display_name=row.display_name),
        csrf_token=csrf,
        token=token,
        expires_at=expires,
    )


@router.post(
    "/logout", status_code=status.HTTP_204_NO_CONTENT, summary="End this session"
)
def logout(
    actor: CurrentUser,
    response: Response,
    engine: Annotated[Engine, Depends(get_engine)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> Response:

    with engine.begin() as conn:
        conn.execute(
            identity.sessions.delete().where(identity.sessions.c.id == actor.session_id)
        )
    response.delete_cookie(settings.cookie_name, domain=settings.cookie_domain, path="/")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/me", response_model=UserOut, summary="The current user")
def me(actor: CurrentUser) -> UserOut:
    return UserOut(id=actor.user_id, email=actor.subject, display_name=actor.display_name)


__all__ = ["router"]
