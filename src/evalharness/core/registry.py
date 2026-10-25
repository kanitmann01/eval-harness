"""Name to factory registry and dotted-path resolution.

Adapter factories receive two arguments: the identity the harness assigned
(candidate name, judge name, metric key, or 'source'/'sink') and the adapter
options mapping from the spec. Tests and production adapters use the same
registry.
"""

from __future__ import annotations

import importlib
from typing import Any, Callable, Mapping

from evalharness.errors import RegistryError

AdapterFactory = Callable[[str, Mapping[str, Any]], Any]
"""Build one adapter instance from an identity and its spec options."""

_FACTORIES: dict[str, AdapterFactory] = {}


def register(name: str, factory: AdapterFactory) -> None:
    """Register a factory under a short name; a later call may not replace it."""
    existing = _FACTORIES.get(name)
    if existing is not None and existing is not factory:
        raise RegistryError(
            name,
            "a different factory is already registered under this name",
            "choose a unique name or reuse the registered factory",
        )
    _FACTORIES[name] = factory


def resolve(path: str) -> AdapterFactory:
    """Resolve a registered name or a 'module:attr' dotted path to a factory."""
    if path in _FACTORIES:
        return _FACTORIES[path]
    if ":" not in path:
        raise RegistryError(
            path,
            "not a registered name and not a 'module:attr' dotted path",
            "register the adapter first or use a path such as 'pkg.module:build'",
        )
    module_name, _, attribute = path.partition(":")
    if not module_name or not attribute:
        raise RegistryError(
            path,
            "the dotted path needs both a module and an attribute",
            "use 'pkg.module:factory'",
        )
    try:
        module = importlib.import_module(module_name)
    except Exception as exc:  # import errors carry the actionable cause
        raise RegistryError(
            path,
            f"module {module_name!r} could not be imported: {exc}",
            "check the module path and that the package is installed",
        ) from exc
    try:
        factory = getattr(module, attribute)
    except AttributeError as exc:
        raise RegistryError(
            path,
            f"module {module_name!r} has no attribute {attribute!r}",
            "point at an existing factory function",
        ) from exc
    if not callable(factory):
        raise RegistryError(
            path,
            f"{attribute!r} is not callable",
            "point at a factory function",
        )
    return factory


def build(path: str, identity: str, options: Mapping[str, Any]) -> Any:
    """Resolve a path and call its factory with a shallow copy of the options."""
    factory = resolve(path)
    return factory(identity, dict(options))
