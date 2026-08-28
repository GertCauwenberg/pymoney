"""Which providers exist: the built-in ones, plus whatever the user plugged in.

Two ways to add a provider, both ending in a :class:`QuoteProvider` subclass:

* **An installed package** declaring an entry point in the group
  ``pymoney.quote_providers``::

      [project.entry-points."pymoney.quote_providers"]
      mybroker = "mybroker_quotes:MyBrokerProvider"

* **A dotted reference** in ``PYMONEY_QUOTE_PLUGINS`` (``module:Attribute``,
  comma-separated or a JSON list), for a file in the deployment that is not a
  package.

A plugin that fails to import is reported, not fatal: the server must not refuse
to start because a price source is broken.
"""

from __future__ import annotations

import importlib
from collections.abc import Iterable, Iterator
from importlib import metadata

from .ecb import EcbProvider
from .provider import QuoteProvider
from .yahoo import YahooProvider

__all__ = ["ENTRY_POINT_GROUP", "ProviderRegistry"]

ENTRY_POINT_GROUP = "pymoney.quote_providers"


class ProviderRegistry:
    """Providers by name, in registration order."""

    def __init__(self, providers: Iterable[QuoteProvider] = ()) -> None:
        self._providers: dict[str, QuoteProvider] = {}
        self.problems: list[str] = []
        """Plugins that could not be loaded, one line each."""
        for provider in providers:
            self.add(provider)

    @classmethod
    def builtin(cls) -> ProviderRegistry:
        return cls([EcbProvider(), YahooProvider()])

    @classmethod
    def discover(cls, plugins: Iterable[str] = ()) -> ProviderRegistry:
        """The built-ins, then entry points, then explicit ``module:attr`` refs."""
        registry = cls.builtin()
        for entry in metadata.entry_points(group=ENTRY_POINT_GROUP):
            try:
                registry.add(_instantiate(entry.load(), entry.name))
            except Exception as exc:
                registry.problems.append(f"entry point {entry.name!r}: {exc}")
        for reference in plugins:
            try:
                registry.add(_instantiate(_resolve(reference), reference))
            except Exception as exc:
                registry.problems.append(f"plugin {reference!r}: {exc}")
        return registry

    def add(self, provider: QuoteProvider) -> None:
        name = getattr(provider, "name", "")
        if not isinstance(name, str) or not name or name != name.lower() or " " in name:
            raise ValueError(f"provider {provider!r} needs a short lower-case name")
        if name in self._providers:
            raise ValueError(f"a provider called {name!r} is already registered")
        self._providers[name] = provider

    def get(self, name: str) -> QuoteProvider | None:
        return self._providers.get(name)

    def __contains__(self, name: object) -> bool:
        return name in self._providers

    def __iter__(self) -> Iterator[QuoteProvider]:
        return iter(self._providers.values())

    def __len__(self) -> int:
        return len(self._providers)

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(self._providers)


def _resolve(reference: str) -> object:
    module_name, sep, attribute = reference.strip().partition(":")
    if not sep or not module_name or not attribute:
        raise ValueError("expected module:Attribute")
    return getattr(importlib.import_module(module_name), attribute)


def _instantiate(obj: object, label: str) -> QuoteProvider:
    provider = obj() if isinstance(obj, type) else obj
    if not isinstance(provider, QuoteProvider):
        raise TypeError(f"{label} is not a QuoteProvider")
    return provider
