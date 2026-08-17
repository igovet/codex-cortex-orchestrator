"""Explicit composition-root bindings for extracted runtime modules.

This is deliberately a tiny dependency-injection boundary.  It never imports
the executable facade, so domain/application modules can be imported without
creating a reverse dependency or a second ``cortex`` module during importlib
validation.  The facade binds its already-initialized helpers exactly once.
"""
from __future__ import annotations

from collections.abc import Mapping, MutableMapping, Sequence
from typing import Any


_bindings: Mapping[str, Any] | None = None


def bind_runtime_dependencies(source: Mapping[str, Any]) -> None:
    """Capture the facade-owned collaborators after composition is complete.

    Binding a different object set later is rejected because changing callable
    identity halfway through an orchestration would make a process-local
    transaction boundary ambiguous.
    """
    global _bindings
    if _bindings is None:
        # Keep the composition-root namespace live while its remaining
        # runtime adapters finish importing.  Components still copy only
        # their declared symbols into their own module namespace.
        _bindings = source
        return
    if _bindings is not source:
        raise RuntimeError("Cortex runtime dependencies are already bound")


def bind_symbols(
    component: str,
    namespace: MutableMapping[str, Any],
    names: Sequence[str],
) -> None:
    """Inject a component's declared collaborators into its module namespace."""
    if _bindings is None:
        raise RuntimeError(
            f"Cortex runtime component {component!r} was imported before the composition root bound dependencies"
        )
    missing = [name for name in names if name not in _bindings]
    if missing:
        raise RuntimeError(
            f"Cortex runtime component {component!r} requires unavailable dependencies: {', '.join(missing)}"
        )
    namespace.update({name: _bindings[name] for name in names})


def bound_symbol(component: str, name: str) -> Any:
    """Resolve one late-bound collaborator from the composition root.

    Most runtime services copy their declared dependencies at import time with
    :func:`bind_symbols`.  This narrow accessor is reserved for host seams
    which are intentionally replaceable at runtime (for example the MCP
    elicitation adapter used by tests and host integrations).  It keeps that
    behavior without importing the executable facade from a runtime module.
    """
    if _bindings is None:
        raise RuntimeError(
            f"Cortex runtime component {component!r} was imported before the composition root bound dependencies"
        )
    if name not in _bindings:
        raise RuntimeError(
            f"Cortex runtime component {component!r} requires unavailable dependency: {name}"
        )
    return _bindings[name]
