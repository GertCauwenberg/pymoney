"""Translations, shared by the server's emails, the web app and the desktop.

Keys are the English source strings, so code reads as English and English needs
no catalogue. One JSON file per other language lives beside this module
(``nl.json``); ``scripts/i18n.py`` copies it into the frontend and checks that
every string the code asks for has a translation.

    from pymoney.i18n import translate
    translate("Check your email", "nl")                 -> "Controleer uw e-mail"
    translate("Hello {name}", "nl", name="Ann")         -> "Hallo Ann"

A missing key falls back to English, so a new string is never a crash, only a
gap the check script reports.
"""

from __future__ import annotations

import json
from functools import cache
from pathlib import Path

__all__ = ["DEFAULT_LOCALE", "SUPPORTED", "catalogue", "normalise_locale", "translate"]

DEFAULT_LOCALE = "en"
SUPPORTED: tuple[str, ...] = ("en", "nl")
LANGUAGE_NAMES = {"en": "English", "nl": "Nederlands"}
_HERE = Path(__file__).resolve().parent


@cache
def catalogue(locale: str) -> dict[str, str]:
    """The key -> translation table for ``locale``; empty for English."""
    if locale == DEFAULT_LOCALE:
        return {}
    path = _HERE / f"{locale}.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def normalise_locale(value: str | None) -> str:
    """``"nl-BE"`` -> ``"nl"``; anything unknown -> English."""
    if not value:
        return DEFAULT_LOCALE
    short = value.strip().lower().replace("_", "-").split("-", 1)[0]
    return short if short in SUPPORTED else DEFAULT_LOCALE


def translate(key: str, locale: str | None, **params: object) -> str:
    text = catalogue(normalise_locale(locale)).get(key, key)
    if params:
        for name, value in params.items():
            text = text.replace("{" + name + "}", str(value))
    return text
