"""The relational schema (decisions #3, #5, #7, #9).

SQLAlchemy Core only -- no ORM-mapped domain classes. The domain objects stay
frozen dataclasses that know nothing about databases; :mod:`pymoney.store.mapper`
translates. That costs a little typing and buys the ability to change either
side without touching the other.

Choices worth defending:

**Rationals are integer pairs, never NUMERIC.** A price is ``num``/``den``
(decision #3). ``NUMERIC`` would silently truncate ``10/3``, and a string would
neither sort nor aggregate.

**Booked amounts are BIGINT minor units.** Not ``NUMERIC``, never floats. A
split's ``quantity_minor`` is an exact count of the commodity's smallest unit.

**``post_date`` is denormalised onto splits.** A register, a balance and every
report filter on *account plus date*, and that is covered by the index
``(book_id, account_id, post_date)`` without touching ``transactions`` at all.
It is the most important decision in this file for performance; splits are only
ever written as part of their transaction, so the copy cannot drift.

**``*_key`` columns carry the case-folded form** used for uniqueness, because
neither SQLite nor PostgreSQL indexes ``lower(col)`` portably enough to rely on.
"""

from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    Date,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    SmallInteger,
    String,
    Table,
    Text,
    UniqueConstraint,
)

from .types import JsonDict, JsonList, UtcDateTime, Uuid

# Explicit naming convention: Alembic autogenerate only produces stable,
# readable migrations if constraints have deterministic names.
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s",
    "pk": "pk_%(table_name)s",
}

metadata = MetaData(naming_convention=NAMING_CONVENTION)

# Text columns carry no server default. The domain dataclasses already default
# them to "", so a default here would be redundant -- and empty-string defaults
# compare unreliably across dialects, which breaks the schema-drift test for no
# benefit. NOT NULL still holds: the application always supplies a value.
CODE = String(32)
NAME = String(255)
UTC = UtcDateTime()


def _audit() -> list[Column]:
    """The columns every entity carries (decision #5)."""
    return [
        Column("created_at", UTC, nullable=False),
        Column("updated_at", UTC, nullable=False),
        Column("revision", Integer, nullable=False),
    ]


books = Table(
    "books",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("name", NAME, nullable=False),
    # No FK: the base currency lives in `commodities`, which is scoped to a
    # book, so the two rows are mutually dependent and one order has to give.
    Column("base_currency_id", Uuid, nullable=True),
    # The watermark: the highest revision assigned in this book.
    Column("revision", Integer, nullable=False, server_default="0"),
    # The revision at which the book's own fields last changed. Separate,
    # because the watermark moves on every command and the settings do not.
    Column("settings_revision", Integer, nullable=False, server_default="0"),
    Column("created_at", UTC, nullable=False),
    Column("updated_at", UTC, nullable=False),
)

commodities = Table(
    "commodities",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("book_id", Uuid, ForeignKey("books.id", ondelete="CASCADE"), nullable=False),
    Column("kind", String(16), nullable=False),
    Column("code", CODE, nullable=False),
    Column("code_key", CODE, nullable=False),
    Column("name", NAME, nullable=False),
    Column("symbol", String(16)),
    Column("minor_unit_factor", BigInteger, nullable=False),
    Column("display_precision", SmallInteger),
    Column("isin", String(32)),
    Column("quote_source", String(64)),
    *_audit(),
    UniqueConstraint("book_id", "code_key", name="uq_commodities_book_id_code_key"),
    CheckConstraint("minor_unit_factor >= 1", name="minor_unit_factor_positive"),
)

accounts = Table(
    "accounts",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("book_id", Uuid, ForeignKey("books.id", ondelete="CASCADE"), nullable=False),
    Column("parent_id", Uuid, ForeignKey("accounts.id"), nullable=True),
    Column("type", String(24), nullable=False),
    Column("name", NAME, nullable=False),
    Column("name_key", NAME, nullable=False),
    Column("commodity_id", Uuid, ForeignKey("commodities.id"), nullable=False),
    Column("description", Text, nullable=False),
    Column("code", String(64), nullable=False),
    Column("closed", Boolean, nullable=False, server_default="0"),
    Column("placeholder", Boolean, nullable=False, server_default="0"),
    Column("notes", Text, nullable=False),
    Column("institution", NAME, nullable=False),
    Column("iban", String(64), nullable=False),
    *_audit(),
    # Sibling-name uniqueness is enforced in the domain, not here: SQL treats
    # NULLs as distinct, so a unique index on (book_id, parent_id, name_key)
    # would silently allow two top-level accounts called "Assets".
    Index("ix_accounts_book_id_parent_id", "book_id", "parent_id"),
)

payees = Table(
    "payees",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("book_id", Uuid, ForeignKey("books.id", ondelete="CASCADE"), nullable=False),
    Column("name", NAME, nullable=False),
    Column("name_key", NAME, nullable=False),
    Column("notes", Text, nullable=False),
    Column("address", Text, nullable=False),
    Column("match_keys", JsonList, nullable=False),
    Column("default_account_id", Uuid, ForeignKey("accounts.id"), nullable=True),
    *_audit(),
    UniqueConstraint("book_id", "name_key", name="uq_payees_book_id_name_key"),
)

# A free-form label put on a transaction's legs. Small and cached like payees;
# referenced by id from `split_tags`, so a rename is one row.
tags = Table(
    "tags",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("book_id", Uuid, ForeignKey("books.id", ondelete="CASCADE"), nullable=False),
    Column("name", NAME, nullable=False),
    Column("name_key", NAME, nullable=False),
    Column("notes", Text, nullable=False),
    Column("colour", String(16)),
    Column("closed", Boolean, nullable=False, server_default="0"),
    *_audit(),
    UniqueConstraint("book_id", "name_key", name="uq_tags_book_id_name_key"),
)

# A recurring transaction: a date-less template, a recurrence rule, and a
# cursor. Small and read constantly (the "what is due?" panel), so cached like
# payees. `next_due` is denormalised from `occurrences_done` purely so the due
# query is an indexed range scan.
schedules = Table(
    "schedules",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("book_id", Uuid, ForeignKey("books.id", ondelete="CASCADE"), nullable=False),
    Column("name", NAME, nullable=False),
    Column("recurrence", JsonDict, nullable=False),
    Column("template", JsonDict, nullable=False),
    Column("occurrences_done", Integer, nullable=False),
    Column("next_due", Date, nullable=True),
    Column("last_occurrence", Date, nullable=True),
    Column("auto_post", Boolean, nullable=False),
    Column("paused", Boolean, nullable=False),
    *_audit(),
    Index("ix_schedules_book_id_next_due", "book_id", "next_due"),
)

transactions = Table(
    "transactions",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("book_id", Uuid, ForeignKey("books.id", ondelete="CASCADE"), nullable=False),
    # A plain date: postings have no timezone (decision #10).
    Column("post_date", Date, nullable=False),
    Column("currency_id", Uuid, ForeignKey("commodities.id"), nullable=False),
    Column("memo", Text, nullable=False),
    Column("payee_id", Uuid, ForeignKey("payees.id"), nullable=True),
    Column("number", String(64), nullable=False),
    Column("rounding", String(16), nullable=False),
    Column("entered_at", UTC, nullable=True),
    Column("imported_id", String(128), nullable=True),
    *_audit(),
    Index("ix_transactions_book_id_post_date", "book_id", "post_date"),
    Index("ix_transactions_book_id_payee_id", "book_id", "payee_id"),
    # Import dedup must be enforced by the database, not only by the command:
    # two concurrent imports of the same statement would otherwise both pass
    # their check and both insert.
    UniqueConstraint(
        "book_id", "imported_id", name="uq_transactions_book_id_imported_id"
    ),
)

splits = Table(
    "splits",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("book_id", Uuid, ForeignKey("books.id", ondelete="CASCADE"), nullable=False),
    Column(
        "transaction_id",
        Uuid,
        ForeignKey("transactions.id", ondelete="CASCADE"),
        nullable=False,
    ),
    # Splits are ordered within a transaction: the user's leg order is
    # meaningful (the residual leg is usually last) and must survive a round
    # trip.
    Column("position", SmallInteger, nullable=False),
    Column("account_id", Uuid, ForeignKey("accounts.id"), nullable=False),
    # Denormalised from the parent transaction; see the module docstring.
    Column("post_date", Date, nullable=False),
    Column("quantity_commodity_id", Uuid, ForeignKey("commodities.id"), nullable=False),
    Column("quantity_minor", BigInteger, nullable=False),
    # The value commodity is always the transaction's currency -- an invariant
    # Transaction.validate enforces -- so it is not stored again.
    Column("value_minor", BigInteger, nullable=False),
    Column("memo", Text, nullable=False),
    Column("payee_id", Uuid, ForeignKey("payees.id"), nullable=True),
    Column("reconcile_state", SmallInteger, nullable=False, server_default="0"),
    Column("reconcile_date", Date, nullable=True),
    Column("action", String(32), nullable=False),
    Column("number", String(64), nullable=False),
    Column("price_num", BigInteger, nullable=True),
    Column("price_den", BigInteger, nullable=True),
    # The hot path: every register, balance and report is "this account, up to
    # this date".
    #
    # `quantity_minor` is in the index not to filter on but to *cover*: with it,
    # `SELECT account_id, SUM(quantity_minor) ... GROUP BY account_id` is answered
    # from the index alone, already in group order. Measured on 50k transactions
    # (benchmarks/bench_store.py): every balance in the book 173ms -> 16ms, one
    # account's balance 9.1ms -> 1.0ms, for 0.6% more disk. Without it SQLite
    # walks the index and then fetches each row, which is slower than the table
    # scan it was trying to avoid.
    Index(
        "ix_splits_book_id_account_id_post_date",
        "book_id",
        "account_id",
        "post_date",
        "quantity_minor",
    ),
    Index("ix_splits_transaction_id", "transaction_id"),
    Index("ix_splits_book_id_payee_id", "book_id", "payee_id"),
    CheckConstraint(
        "(price_num IS NULL) = (price_den IS NULL)", name="price_pair_complete"
    ),
    CheckConstraint("price_den IS NULL OR price_den > 0", name="price_den_positive"),
)

# Which tags are on which legs. A junction rather than a JSON column on
# `splits`, so "every transaction with this tag" is an indexed lookup that
# agrees across SQLite and PostgreSQL, not a portable-only-by-luck scan of JSON
# text. `split_id` is not a foreign key: splits are rewritten wholesale with
# their transaction, so these rows are owned and replaced by `transaction_id`.
split_tags = Table(
    "split_tags",
    metadata,
    Column("book_id", Uuid, ForeignKey("books.id", ondelete="CASCADE"), nullable=False),
    Column(
        "transaction_id",
        Uuid,
        ForeignKey("transactions.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("split_id", Uuid, nullable=False),
    Column("tag_id", Uuid, ForeignKey("tags.id"), nullable=False),
    # The order the user added them, so a round trip preserves it.
    Column("position", SmallInteger, nullable=False),
    Index("ix_split_tags_book_id_tag_id", "book_id", "tag_id"),
    Index("ix_split_tags_transaction_id", "transaction_id"),
    UniqueConstraint("split_id", "tag_id", name="uq_split_tags_split_id_tag_id"),
)

prices = Table(
    "prices",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("book_id", Uuid, ForeignKey("books.id", ondelete="CASCADE"), nullable=False),
    Column("base_id", Uuid, ForeignKey("commodities.id"), nullable=False),
    Column("quote_id", Uuid, ForeignKey("commodities.id"), nullable=False),
    Column("num", BigInteger, nullable=False),
    Column("den", BigInteger, nullable=False),
    # Bitemporal (decision #9): the date the rate applies to, and the instant we
    # learned it. A correction is a new row, not an update.
    Column("effective_date", Date, nullable=False),
    Column("as_of", UTC, nullable=False),
    Column("source", String(64), nullable=False),
    *_audit(),
    Index(
        "ix_prices_book_id_base_id_quote_id_effective_date",
        "book_id",
        "base_id",
        "quote_id",
        "effective_date",
    ),
    CheckConstraint("num > 0 AND den > 0", name="rate_positive"),
)

changes = Table(
    "changes",
    metadata,
    Column("book_id", Uuid, ForeignKey("books.id", ondelete="CASCADE"), nullable=False),
    Column("revision", Integer, nullable=False),
    # Position within the revision: one command can change many rows and the
    # order it did so in is part of the record.
    Column("seq", Integer, nullable=False),
    Column("at", UTC, nullable=False),
    Column("kind", String(16), nullable=False),
    Column("entity_id", Uuid, nullable=False),
    Column("op", String(8), nullable=False),
    Column("command", String(64), nullable=False),
    Column("command_id", Uuid, nullable=True),
    Column("actor", String(128), nullable=False),
    Column("note", Text, nullable=False),
    # The sync watermark scan: `WHERE book_id = ? AND revision > ?`.
    Index("ix_changes_book_id_revision_seq", "book_id", "revision", "seq", unique=True),
    Index("ix_changes_book_id_entity_id", "book_id", "entity_id"),
)

# One row per applied command that carried a command_id: the durable half of
# Idempotency-Key. Written in the command's own transaction, so a retry after a
# crash or a restart finds it exactly when the command's effects are there.
command_results = Table(
    "command_results",
    metadata,
    Column("book_id", Uuid, ForeignKey("books.id", ondelete="CASCADE"), nullable=False),
    Column("command_id", Uuid, nullable=False),
    # SHA-256 of the command's intent, so a key reused for a different payload
    # is refused rather than answered with the first payload's result.
    Column("fingerprint", String(64), nullable=False),
    Column("revision", Integer, nullable=False),
    Column("command", String(64), nullable=False),
    Column("entity_kind", String(16), nullable=True),
    Column("entity_id", Uuid, nullable=True),
    Column("at", UTC, nullable=False),
    UniqueConstraint(
        "book_id", "command_id", name="uq_command_results_book_id_command_id"
    ),
)

# The column mapping that turns a bank's CSV into this account's transactions.
# Not a ledger table and not synced: it is UI convenience -- which column is
# the date, how the amount is written -- kept so next week's statement needs
# no remapping. One per account; replaced wholesale when the user adjusts it.
import_mappings = Table(
    "import_mappings",
    metadata,
    Column("book_id", Uuid, ForeignKey("books.id", ondelete="CASCADE"), nullable=False),
    Column("account_id", Uuid, nullable=False),
    Column("mapping", JsonDict, nullable=False),
    Column("updated_at", UtcDateTime, nullable=False),
    UniqueConstraint(
        "book_id", "account_id", name="uq_import_mappings_book_id_account_id"
    ),
)

ALL_TABLES = (
    books,
    commodities,
    accounts,
    payees,
    tags,
    schedules,
    transactions,
    splits,
    split_tags,
    prices,
    changes,
    command_results,
    import_mappings,
)
