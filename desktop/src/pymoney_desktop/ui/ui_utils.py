"""Every call to the client runs off the UI thread through :class:`Executor`; a
test passes a synchronous one so the window can be driven step by step.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from pymoney.api.schemas import AmountOut
from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

from ..i18n import current_locale

# Thousands and decimal separators per language; the digits never go through a float.
SEPARATORS = {"en": (",", "."), "nl": (".", ",")}

CREDIT_NORMAL = {"LIABILITY", "EQUITY", "INCOME"}


# -- running client calls off the UI thread -------------------------------------
class Executor:
    def run(
        self,
        work: Callable[[], Any],
        done: Callable[[Any], None],
        failed: Callable[[Exception], None],
    ) -> None:
        raise NotImplementedError


class SyncExecutor(Executor):
    """For tests: run it now, call back now."""

    def run(self, work, done, failed) -> None:
        try:
            result = work()
        except Exception as exc:
            failed(exc)
            return
        done(result)


class _Signals(QObject):
    done = Signal(object)
    failed = Signal(object)


class _Job(QRunnable):
    def __init__(self, work: Callable[[], Any], signals: _Signals) -> None:
        super().__init__()
        self.work, self.signals = work, signals

    def run(self) -> None:
        try:
            self.signals.done.emit(self.work())
        except Exception as exc:
            self.signals.failed.emit(exc)


class ThreadedExecutor(Executor):
    """One worker thread: the clients are not thread-safe, and one call at a time is plenty."""

    def __init__(self) -> None:
        self.pool = QThreadPool()
        self.pool.setMaxThreadCount(1)
        self._live: list[_Signals] = []

    def run(self, work, done, failed) -> None:
        signals = _Signals()
        self._live.append(signals)

        def finish(callback, value):
            self._live.remove(signals)
            callback(value)

        signals.done.connect(lambda value: finish(done, value))
        signals.failed.connect(lambda exc: finish(failed, exc))
        self.pool.start(_Job(work, signals))


def natural_sign(account_class: str) -> int:
    return -1 if account_class in CREDIT_NORMAL else 1


def format_amount(amount: AmountOut, *, sign: int = 1) -> str:
    digits = len(str(amount.scale)) - 1
    minor = amount.minor * sign
    negative = minor < 0
    s = str(abs(minor)).rjust(digits + 1, "0")
    whole, frac = (s[:-digits], s[-digits:]) if digits else (s, "")
    group, point = SEPARATORS.get(current_locale(), SEPARATORS["en"])
    whole = f"{int(whole):,}".replace(",", group)
    text = f"{whole}{point}{frac}" if frac else whole
    return f"-{text}" if negative else text
