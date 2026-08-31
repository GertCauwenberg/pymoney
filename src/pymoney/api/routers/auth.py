"""Registration, activation, login, logout, password reset, and leaving.

Session cookies, with a bearer token for clients that cannot hold cookies. The
shape is chosen so OIDC can slot in later without changing any other router:
everything downstream depends on :class:`~pymoney.api.deps.Actor`, whose
``subject`` is the only thing grants are keyed on. An OIDC deployment replaces
this module and leaves the rest untouched.

Three rules about what the responses reveal, because an authentication
endpoint is also an oracle:

* ``/forgot-password`` and ``/resend-activation`` answer 202 whether or not the
  address is registered. The email is the only channel that confirms anything.
* A wrong password and an unknown address are the same 401.
* A correct password on a not-yet-activated account is a 403 that says so:
  the caller has proven they own the account, so telling them why it does not
  work yet reveals nothing.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import Engine, func, select, update

from ...core.ids import new_id
from ...i18n import translate
from ...store import identity, write_transaction
from ..deps import CurrentUser, get_engine, get_mailer, get_settings_dep
from ..mail import Mailer, Message
from ..ratelimit import THROTTLED_RESPONSE, Throttle
from ..schemas import (
    ActivateIn,
    DeleteMeIn,
    EmailIn,
    LoginIn,
    MePatch,
    PendingOut,
    RegisterIn,
    ResetPasswordIn,
    SessionOut,
    UserOut,
)
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


def _now() -> datetime:
    return datetime.now(timezone.utc)


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
    now = _now()
    expires = now + timedelta(hours=settings.session_lifetime_hours)
    with write_transaction(engine) as conn:
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


def _session_out(
    engine: Engine,
    settings: Settings,
    request: Request,
    response: Response,
    *,
    user_id,
    email,
    display_name,
    locale: str = "en",
) -> SessionOut:
    token, csrf, expires = _start_session(
        engine,
        settings,
        user_id=user_id,
        user_agent=request.headers.get("user-agent", ""),
        response=response,
    )
    return SessionOut(
        user=UserOut(id=user_id, email=email, display_name=display_name, locale=locale),
        csrf_token=csrf,
        token=token,
        expires_at=expires,
    )


# -- tokens in emails -----------------------------------------------------------
def _issue_token(engine: Engine, user_id: UUID, kind: str, hours: int) -> str:
    """Mint a single-use token, store only its digest, hand back the secret."""
    token = new_session_token()
    now = _now()
    with write_transaction(engine) as conn:
        # One live token per kind and user: asking again replaces the old link.
        conn.execute(
            identity.user_tokens.delete().where(
                identity.user_tokens.c.user_id == user_id,
                identity.user_tokens.c.kind == kind,
            )
        )
        conn.execute(
            identity.user_tokens.insert().values(
                id=new_id(),
                user_id=user_id,
                kind=kind,
                token_hash=hash_token(token),
                created_at=now,
                expires_at=now + timedelta(hours=hours),
                used_at=None,
            )
        )
    return token


def _consume_token(engine: Engine, token: str, kind: str):
    """The user a live token belongs to, marking the token used; None if no such token."""
    now = _now()
    with write_transaction(engine) as conn:
        row = conn.execute(
            select(
                identity.user_tokens.c.id,
                identity.user_tokens.c.user_id,
                identity.user_tokens.c.expires_at,
            )
            .where(
                identity.user_tokens.c.token_hash == hash_token(token),
                identity.user_tokens.c.kind == kind,
            )
            .where(identity.user_tokens.c.used_at.is_(None))
        ).one_or_none()
        if row is None or row.expires_at <= now:
            return None
        conn.execute(
            update(identity.user_tokens)
            .where(identity.user_tokens.c.id == row.id)
            .values(used_at=now)
        )
        user = conn.execute(
            select(identity.users).where(identity.users.c.id == row.user_id)
        ).one_or_none()
    return user


def _send_activation(
    mailer: Mailer, settings: Settings, email: str, token: str, locale: str
) -> None:
    link = f"{settings.public_url.rstrip('/')}/activate?token={token}"
    t = lambda key, **p: translate(key, locale, **p)  # noqa: E731
    mailer.send(
        Message(
            to=email,
            subject=t("Activate your PyMoney account"),
            body=(
                t("Someone -- we hope you -- registered this address with PyMoney.")
                + "\n\n"
                + t(
                    "To activate the account, open this link within {hours} hours:",
                    hours=settings.activation_hours,
                )
                + f"\n\n    {link}\n\n"
                + t(
                    "If that was not you, do nothing: the account will be deleted automatically."
                )
            ),
        )
    )


def _send_reset(
    mailer: Mailer, settings: Settings, email: str, token: str, locale: str
) -> None:
    link = f"{settings.public_url.rstrip('/')}/reset-password?token={token}"
    t = lambda key, **p: translate(key, locale, **p)  # noqa: E731
    mailer.send(
        Message(
            to=email,
            subject=t("Reset your PyMoney password"),
            body=(
                t("A password reset was requested for this PyMoney account.")
                + "\n\n"
                + t(
                    "To choose a new password, open this link within {hours} hours:",
                    hours=settings.reset_hours,
                )
                + f"\n\n    {link}\n\n"
                + t("If that was not you, do nothing; your password is unchanged.")
            ),
        )
    )


# -- registration and activation ------------------------------------------------------
@router.post(
    "/register",
    response_model=SessionOut | PendingOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create an account",
    responses={
        202: {
            "description": "Created, pending activation: a link was emailed.",
            "model": PendingOut,
        },
        **THROTTLED_RESPONSE,
    },
)
def register(
    body: RegisterIn,
    request: Request,
    response: Response,
    engine: Annotated[Engine, Depends(get_engine)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
    mailer: Annotated[Mailer, Depends(get_mailer)],
    throttle: Throttle,
) -> SessionOut | PendingOut:
    """With activation required (the default), the account exists but cannot
    log in until the emailed link is followed; the answer is 202 and no session.
    Otherwise the session starts at once (201).

    Registering an address that is pending activation does not replace the
    pending account -- that would let anyone reset a stranger's password and
    display name before they have followed their link -- it sends the
    activation link again, to the same 202. Choose the password on activation
    day; if it is forgotten in between, activate first and reset it after."""
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
    throttle.check(request, settings, login=False, who=email)
    key = email.casefold()
    now = _now()
    stored = hash_password(body.password)
    user_id = new_id()
    display_name = body.display_name or email.split("@")[0]
    with write_transaction(engine) as conn:
        taken = conn.execute(
            select(
                identity.users.c.id,
                identity.users.c.email,
                identity.users.c.activated_at,
                identity.users.c.locale,
            ).where(identity.users.c.email_key == key)
        ).one_or_none()
        if taken is not None and (
            taken.activated_at is not None or not settings.require_activation
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="that email address is already registered",
            )
        if taken is not None:
            # Pending: the row, its password and its name stay as whoever
            # registered first chose them; only the link is renewed. Proving
            # ownership of the mailbox is what activation is for.
            pending_id, pending_email, pending_locale = (
                taken.id,
                taken.email,
                taken.locale,
            )
    if taken is not None:
        _send_activation(
            mailer,
            settings,
            pending_email,
            _issue_token(engine, pending_id, "activate", settings.activation_hours),
            pending_locale,
        )
        response.status_code = status.HTTP_202_ACCEPTED
        return PendingOut(email=email)
    with write_transaction(engine) as conn:
        conn.execute(
            identity.users.insert().values(
                id=user_id,
                email=email,
                email_key=key,
                display_name=display_name,
                password_hash=stored.digest,
                password_salt=stored.salt,
                password_params=stored.params,
                active=True,
                activated_at=None if settings.require_activation else now,
                locale=body.locale,
                created_at=now,
                updated_at=now,
            )
        )
    if settings.require_activation:
        token = _issue_token(engine, user_id, "activate", settings.activation_hours)
        _send_activation(mailer, settings, email, token, body.locale)
        response.status_code = status.HTTP_202_ACCEPTED
        return PendingOut(email=email)
    return _session_out(
        engine,
        settings,
        request,
        response,
        user_id=user_id,
        email=email,
        display_name=display_name,
    )


@router.post(
    "/activate",
    response_model=SessionOut,
    summary="Activate an account with the emailed token",
)
def activate(
    body: ActivateIn,
    request: Request,
    response: Response,
    engine: Annotated[Engine, Depends(get_engine)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> SessionOut:
    """Proves the address; logs the new user in."""
    user = _consume_token(engine, body.token, "activate")
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="this activation link is invalid, expired or already used; register again or ask for a new one",
        )
    with write_transaction(engine) as conn:
        conn.execute(
            update(identity.users)
            .where(
                identity.users.c.id == user.id, identity.users.c.activated_at.is_(None)
            )
            .values(activated_at=_now(), updated_at=_now())
        )
    return _session_out(
        engine,
        settings,
        request,
        response,
        user_id=user.id,
        email=user.email,
        display_name=user.display_name,
        locale=user.locale,
    )


@router.post(
    "/resend-activation",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=PendingOut,
    summary="Send the activation link again",
    responses=THROTTLED_RESPONSE,
)
def resend_activation(
    body: EmailIn,
    request: Request,
    engine: Annotated[Engine, Depends(get_engine)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
    mailer: Annotated[Mailer, Depends(get_mailer)],
    throttle: Throttle,
) -> PendingOut:
    """Always 202: whether the address is registered is not revealed."""
    email = body.email.strip()
    throttle.check(request, settings, login=False, who=email)
    with engine.connect() as conn:
        user = conn.execute(
            select(
                identity.users.c.id,
                identity.users.c.email,
                identity.users.c.activated_at,
                identity.users.c.locale,
            ).where(identity.users.c.email_key == email.casefold())
        ).one_or_none()
    if user is not None and user.activated_at is None:
        _send_activation(
            mailer,
            settings,
            user.email,
            _issue_token(engine, user.id, "activate", settings.activation_hours),
            user.locale,
        )
    return PendingOut(email=email)


# -- login and logout ---------------------------------------------------------------------
@router.post(
    "/login", response_model=SessionOut, summary="Log in", responses=THROTTLED_RESPONSE
)
def login(
    body: LoginIn,
    request: Request,
    response: Response,
    engine: Annotated[Engine, Depends(get_engine)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
    throttle: Throttle,
) -> SessionOut:
    # Before the lookup and the hash: the limit is on attempts, and the cost of
    # an attempt is paid whether or not the address exists.
    throttle.check(request, settings, login=True, who=body.email)
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
    # Always verify, even when the user does not exist: verify_password runs the
    # full derivation against a decoy for a missing record, so an unknown
    # address costs the same hundred milliseconds as a wrong password and the
    # response time does not reveal which addresses are registered.
    ok = verify_password(body.password, stored)
    if row is None or not ok or not row.active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="wrong email or password",
        )
    if row.activated_at is None:
        # They own the account (the password was right), so saying why is safe.
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="this account has not been activated yet; follow the link in the email, or ask for a new one",
        )
    return _session_out(
        engine,
        settings,
        request,
        response,
        user_id=row.id,
        email=row.email,
        display_name=row.display_name,
        locale=row.locale,
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
    with write_transaction(engine) as conn:
        conn.execute(
            identity.sessions.delete().where(identity.sessions.c.id == actor.session_id)
        )
    response.delete_cookie(settings.cookie_name, domain=settings.cookie_domain, path="/")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/me", response_model=UserOut, summary="The current user")
def me(actor: CurrentUser) -> UserOut:
    return UserOut(
        id=actor.user_id,
        email=actor.subject,
        display_name=actor.display_name,
        locale=actor.locale,
    )


@router.patch(
    "/me", response_model=UserOut, summary="Change your display name or language"
)
def patch_me(
    body: MePatch,
    actor: CurrentUser,
    engine: Annotated[Engine, Depends(get_engine)],
) -> UserOut:
    """The language is stored with the account: every client of this user, and
    the emails the server sends, follow it."""
    fields = body.model_dump(exclude_unset=True, exclude_none=True)
    if fields:
        with write_transaction(engine) as conn:
            conn.execute(
                update(identity.users)
                .where(identity.users.c.id == actor.user_id)
                .values(**fields, updated_at=_now())
            )
    return UserOut(
        id=actor.user_id,
        email=actor.subject,
        display_name=fields.get("display_name", actor.display_name),
        locale=fields.get("locale", actor.locale),
    )


# -- forgotten passwords ---------------------------------------------------------------------
@router.post(
    "/forgot-password",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=PendingOut,
    summary="Email a password-reset link",
    responses=THROTTLED_RESPONSE,
)
def forgot_password(
    body: EmailIn,
    request: Request,
    engine: Annotated[Engine, Depends(get_engine)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
    mailer: Annotated[Mailer, Depends(get_mailer)],
    throttle: Throttle,
) -> PendingOut:
    """Always 202. A link is sent only to an activated account with that address."""
    email = body.email.strip()
    throttle.check(request, settings, login=False, who=email)
    with engine.connect() as conn:
        user = conn.execute(
            select(
                identity.users.c.id,
                identity.users.c.email,
                identity.users.c.activated_at,
                identity.users.c.active,
                identity.users.c.locale,
            ).where(identity.users.c.email_key == email.casefold())
        ).one_or_none()
    if user is not None and user.activated_at is not None and user.active:
        _send_reset(
            mailer,
            settings,
            user.email,
            _issue_token(engine, user.id, "reset", settings.reset_hours),
            user.locale,
        )
    return PendingOut(email=email)


@router.post(
    "/reset-password",
    response_model=SessionOut,
    summary="Set a new password with the emailed token",
)
def reset_password(
    body: ResetPasswordIn,
    request: Request,
    response: Response,
    engine: Annotated[Engine, Depends(get_engine)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> SessionOut:
    """Every existing session of the account is ended: whoever had the old
    password is logged out everywhere. The caller gets a fresh one."""
    user = _consume_token(engine, body.token, "reset")
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="this reset link is invalid, expired or already used; ask for a new one",
        )
    stored = hash_password(body.password)
    with write_transaction(engine) as conn:
        conn.execute(
            update(identity.users)
            .where(identity.users.c.id == user.id)
            .values(
                password_hash=stored.digest,
                password_salt=stored.salt,
                password_params=stored.params,
                updated_at=_now(),
            )
        )
        conn.execute(
            identity.sessions.delete().where(identity.sessions.c.user_id == user.id)
        )
    return _session_out(
        engine,
        settings,
        request,
        response,
        user_id=user.id,
        email=user.email,
        display_name=user.display_name,
        locale=user.locale,
    )


# -- leaving ------------------------------------------------------------------------------------
@router.delete(
    "/me",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete your account",
    responses={
        409: {
            "description": "You still own a book: delete it or hand it to another owner first."
        }
    },
)
def delete_me(
    body: DeleteMeIn,
    actor: CurrentUser,
    response: Response,
    engine: Annotated[Engine, Depends(get_engine)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> Response:
    """Irreversible. Refused while the user owns any book, because a book with
    no owner can never be administered again; grants on other people's books
    are simply dropped."""
    with engine.connect() as conn:
        row = conn.execute(
            select(identity.users).where(identity.users.c.id == actor.user_id)
        ).one()
        owned = conn.execute(
            select(func.count())
            .select_from(identity.book_grants)
            .where(
                identity.book_grants.c.subject == actor.subject,
                identity.book_grants.c.role == "owner",
            )
        ).scalar_one()
    stored = PasswordHash(
        digest=row.password_hash or b"",
        salt=row.password_salt or b"",
        params=row.password_params or "",
    )
    if not verify_password(body.password, stored):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="wrong password"
        )
    if owned:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"you still own {owned} book(s); delete them or make someone else their owner first",
        )
    with write_transaction(engine) as conn:
        conn.execute(
            identity.book_grants.delete().where(
                identity.book_grants.c.subject == actor.subject
            )
        )
        conn.execute(
            identity.user_tokens.delete().where(
                identity.user_tokens.c.user_id == actor.user_id
            )
        )
        conn.execute(
            identity.sessions.delete().where(identity.sessions.c.user_id == actor.user_id)
        )
        conn.execute(identity.users.delete().where(identity.users.c.id == actor.user_id))
    response.delete_cookie(settings.cookie_name, domain=settings.cookie_domain, path="/")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


__all__ = ["router"]
