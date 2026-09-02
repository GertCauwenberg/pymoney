"""Password hashing, session tokens and idempotency keys.

No dependencies. ``hashlib.scrypt`` is in the standard library, is a memory-hard
KDF, and is a perfectly respectable choice; reaching for argon2 would mean a
compiled dependency for a marginal improvement. The parameters are stored
alongside each hash so they can be raised later without invalidating anybody's
password.

Everything that compares secrets here uses :func:`hmac.compare_digest`. Ordinary
``==`` on a token leaks its prefix through timing, which is exactly how a
session gets forged.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import uuid
from dataclasses import dataclass

__all__ = [
    "SCRYPT_PARAMS",
    "PasswordHash",
    "hash_password",
    "hash_token",
    "idempotency_key_to_uuid",
    "new_csrf_token",
    "new_session_token",
    "verify_password",
]

# n=2**15, r=8, p=1: roughly 100ms and 32MB per hash on a small server. Raise
# `n` when that stops hurting; old hashes keep working because each row records
# the parameters it was made with.
SCRYPT_PARAMS = (2**15, 8, 1)
_MAX_PASSWORD_BYTES = 1024
"""A password longer than this is a denial-of-service attempt, not a password."""


@dataclass(frozen=True, slots=True)
class PasswordHash:
    digest: bytes
    salt: bytes
    params: str

    @property
    def parsed_params(self) -> tuple[int, int, int]:
        n, r, p = (int(part) for part in self.params.split(","))
        return n, r, p


def _derive(password: str, salt: bytes, params: tuple[int, int, int]) -> bytes:
    encoded = password.encode("utf-8")
    if len(encoded) > _MAX_PASSWORD_BYTES:
        raise ValueError(
            f"password is {len(encoded)} bytes; the limit is {_MAX_PASSWORD_BYTES}"
        )
    n, r, p = params
    return hashlib.scrypt(
        encoded, salt=salt, n=n, r=r, p=p, dklen=64, maxmem=256 * 1024 * 1024
    )


def hash_password(password: str) -> PasswordHash:
    salt = secrets.token_bytes(32)
    return PasswordHash(
        digest=_derive(password, salt, SCRYPT_PARAMS),
        salt=salt,
        params=",".join(str(part) for part in SCRYPT_PARAMS),
    )


# A fixed, meaningless record to verify against when there is no real one. It
# makes "no such user" cost exactly one scrypt derivation, the same as "wrong
# password", so the response time does not say which addresses are registered.
# The salt is constant and public on purpose: nothing is protected by it, the
# comparison below is against a digest that can never match.
_DECOY = PasswordHash(
    digest=b"\x00" * 64,
    salt=hashlib.sha256(b"pymoney decoy salt").digest(),
    params=",".join(str(part) for part in SCRYPT_PARAMS),
)


def verify_password(password: str, stored: PasswordHash | None) -> bool:
    """Constant-time check. ``None`` means "no password set", never "any password".

    Deliberately does the full derivation even when there is nothing to compare
    against: an early ``return False`` for an unknown address would answer a
    hundred times faster than a wrong password for a known one, and that
    difference is a user-enumeration oracle.
    """
    record = stored
    if record is None or not record.digest or not record.salt:
        record = _DECOY
    try:
        candidate = _derive(password, record.salt, record.parsed_params)
    except (ValueError, TypeError):
        return False
    return record is not _DECOY and hmac.compare_digest(candidate, record.digest)


def new_session_token() -> str:
    """A bearer token. 32 bytes of entropy; not guessable, not a user's secret."""
    return secrets.token_urlsafe(32)


def new_csrf_token() -> str:
    return secrets.token_urlsafe(24)


def hash_token(token: str) -> bytes:
    """What we store instead of the token itself.

    Unsalted SHA-256 on purpose: the token is already 256 bits of entropy, so
    there is nothing to brute-force and a KDF would only add latency to every
    request.
    """
    return hashlib.sha256(token.encode("utf-8")).digest()


_IDEMPOTENCY_NAMESPACE = uuid.UUID("6f9619ff-8b86-d011-b42d-00c04fc964ff")


def idempotency_key_to_uuid(key: str, *, subject: str | None = None) -> uuid.UUID:
    """Turn a client's ``Idempotency-Key`` header into a command id.

    The domain wants a UUID; mobile clients send whatever they like, so the key
    is hashed deterministically -- the same string always yields the same
    command id, which is the entire point of the header.

    With a ``subject`` the key is scoped to that caller. Two users of a shared
    book who both happen to send ``Idempotency-Key: 1`` are making two
    requests, not one, and the second must not be handed the first's result.
    The API always passes the subject; without one, a well-formed UUID is used
    as-is so a single-user script's key survives intact.
    """
    if subject is None:
        try:
            return uuid.UUID(key)
        except ValueError:
            return uuid.uuid5(_IDEMPOTENCY_NAMESPACE, key)
    return uuid.uuid5(_IDEMPOTENCY_NAMESPACE, f"{subject}\x00{key}")
