"""The desktop's translator: ``_()`` bound to one locale for the process.

Qt has its own machinery (``.ts`` files, ``lupdate``, ``QTranslator``); we do
not use it, because the desktop shares its catalogue with the web app and the
server's emails (:mod:`pymoney.i18n`), and one dictionary beats three.

The locale comes from the login dialog or QSettings, and once logged in from
the user's account; :func:`set_locale` changes it. Widgets read ``_()`` when
they are built, so a change takes effect for windows opened afterwards.
"""

from __future__ import annotations

from pymoney.i18n import DEFAULT_LOCALE, translate

__all__ = ["_", "current_locale", "set_locale"]

_locale = DEFAULT_LOCALE


def set_locale(locale: str) -> None:
    global _locale
    _locale = locale


def current_locale() -> str:
    return _locale


def _(key: str, **params: object) -> str:
    return translate(key, _locale, **params)
