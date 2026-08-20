"""Exception hierarchy for the PyMoney core domain.

Every error raised by the domain layer derives from :class:`PyMoneyError`, so a
transport layer (HTTP API, desktop app) can map the whole tree onto its own
error model without catching bare ``Exception``.
"""

from __future__ import annotations

__all__ = [
    "CommandError",
    "CommodityMismatchError",
    "ConflictError",
    "ImmutableError",
    "NoPriceError",
    "NotFoundError",
    "PyMoneyError",
    "UnbalancedTransactionError",
    "ValidationError",
]


class PyMoneyError(Exception):
    """Base class for all domain errors."""


class ValidationError(PyMoneyError):
    """An entity or command failed an invariant check."""


class NotFoundError(PyMoneyError):
    """A referenced entity does not exist in this book."""


class ConflictError(PyMoneyError):
    """The operation conflicts with existing data (e.g. duplicate name)."""


class ImmutableError(PyMoneyError):
    """The target is protected from modification (e.g. reconciled splits)."""


class CommodityMismatchError(ValidationError):
    """An operation mixed two different commodities."""


class UnbalancedTransactionError(ValidationError):
    """The split values of a transaction do not sum to zero."""


class NoPriceError(PyMoneyError):
    """No price path could be found between two commodities."""


class CommandError(PyMoneyError):
    """The command could not be dispatched or applied."""
