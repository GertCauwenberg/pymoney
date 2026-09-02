"""Command handlers, one module per aggregate.

A handler is a plain function ``(book, command) -> entity``. It validates, then
mutates through ``book.put`` / ``book.drop`` / ``book.set_book_field``, and the
:class:`~pymoney.core.book.Book` wraps the whole call in one unit of work and one
revision. Splitting them out of ``book.py`` keeps each aggregate's rules in a
file you can read in one sitting, and makes the Book what it should be: the
consistency boundary and the dispatcher, not a 1,600-line catch-all.

The registry is built here so a handler module never imports the Book at
runtime, only for type checking.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .. import commands as cmd
from . import accounts, commodities, payees, prices, settings, transactions

__all__ = ["HANDLERS", "handler_for"]

HANDLERS: dict[type[cmd.Command], Callable[..., Any]] = {
    # book
    cmd.InitialiseBook: settings.initialise_book,
    cmd.SetBaseCurrency: settings.set_base_currency,
    cmd.RenameBook: settings.rename_book,
    # commodities
    cmd.CreateCommodity: commodities.create_commodity,
    cmd.UpdateCommodity: commodities.update_commodity,
    cmd.DeleteCommodity: commodities.delete_commodity,
    # accounts
    cmd.CreateAccount: accounts.create_account,
    cmd.UpdateAccount: accounts.update_account,
    cmd.RenameAccount: accounts.rename_account,
    cmd.SetAccountCommodity: accounts.set_account_commodity,
    cmd.MoveAccount: accounts.move_account,
    cmd.SetAccountClosed: accounts.set_account_closed,
    cmd.MergeAccounts: accounts.merge_accounts,
    cmd.DeleteAccount: accounts.delete_account,
    # payees
    cmd.CreatePayee: payees.create_payee,
    cmd.UpdatePayee: payees.update_payee,
    cmd.MergePayees: payees.merge_payees,
    cmd.DeletePayee: payees.delete_payee,
    # transactions
    cmd.PostTransaction: transactions.post_transaction,
    cmd.UpdateTransaction: transactions.update_transaction,
    cmd.DeleteTransaction: transactions.delete_transaction,
    cmd.SetSplitReconcileState: transactions.set_split_reconcile_state,
    cmd.ReconcileAccount: transactions.reconcile_account,
    # prices
    cmd.AddPrice: prices.add_price,
    cmd.DeletePrice: prices.delete_price,
}


def handler_for(command: cmd.Command) -> Callable[..., Any] | None:
    return HANDLERS.get(type(command))
