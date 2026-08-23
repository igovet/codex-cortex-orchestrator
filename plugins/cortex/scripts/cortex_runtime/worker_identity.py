"""Server-bound identity for the worker-facing MCP surface.

Worker tools run on a channel that is already bound to one dispatched attempt.
The model therefore supplies semantic fields only; this module keeps the
identity tuple in a launch/context-owned ``ContextVar`` and injects it only
inside the trusted server adapter.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
import json
import os
from typing import Any, Callable, Iterator, Mapping
import hashlib


SERVER_OWNED_FIELDS = frozenset({
    "project_root", "task_id", "task_ref", "attempt_id", "profile",
    "dispatch_ref", "briefing_digest", "session_id",
})
WORKER_CAPABILITY_FIELD = "worker_capability"

_CURRENT: ContextVar[dict[str, str] | None] = ContextVar("cortex_worker_binding", default=None)
_BINDING_PROVIDER: Callable[[str], Mapping[str, Any] | None] | None = None


class WorkerBindingError(ValueError):
    """Raised when a worker call is not bound to one immutable dispatch."""


def _clean(value: object, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise WorkerBindingError(f"worker binding {field} is missing")
    return text


def normalize_binding(value: Mapping[str, Any]) -> dict[str, str]:
    if not isinstance(value, Mapping):
        raise WorkerBindingError("worker binding must be an object")
    required = ("project_root", "task_id", "attempt_id", "profile")
    result = {field: _clean(value.get(field), field) for field in required}
    for field in ("task_ref", "dispatch_ref", "briefing_digest", "session_id", "worker_capability_digest", "worker_capability"):
        if value.get(field) is not None and str(value.get(field)).strip():
            result[field] = str(value[field]).strip()
    return result


def current_binding() -> dict[str, str] | None:
    value = _CURRENT.get()
    return dict(value) if value is not None else None


def require_binding(capability: str | None = None) -> dict[str, str]:
    value = current_binding()
    if value is not None and capability:
        expected = str(value.get("worker_capability_digest") or "").strip().lower()
        if not expected or not hashlib.sha256(capability.encode("utf-8")).hexdigest() == expected:
            raise WorkerBindingError("worker capability does not match the bound dispatch")
    if value is None and _BINDING_PROVIDER is not None:
        # Native worker MCP processes can start before the host's
        # SubagentStart hook has persisted the binding.  Resolve lazily on
        # each first worker operation so that this small race does not turn
        # into an unbound worker attempt.  The provider is installed only by
        # the executable server; unit-test imports remain fail-closed.
        try:
            if not capability:
                raise WorkerBindingError("worker capability is required for native worker calls")
            candidate = _BINDING_PROVIDER(capability)
            if candidate is not None:
                value = normalize_binding(candidate)
                _CURRENT.set(value)
        except (OSError, ValueError, TypeError):
            value = None
    if value is None:
        raise WorkerBindingError(
            "worker operation requires a server-bound worker session; no worker binding is available"
        )
    return value


def set_binding_provider(provider: Callable[[str], Mapping[str, Any] | None] | None) -> None:
    """Install the trusted host-session resolver for a server process.

    This is deliberately process configuration, never a public tool field.
    The resolver may only return an exact durable worker binding; an absent or
    ambiguous match remains fail-closed.
    """
    global _BINDING_PROVIDER
    _BINDING_PROVIDER = provider


def bind_worker(value: Mapping[str, Any] | None) -> object:
    return _CURRENT.set(normalize_binding(value or {})) if value is not None else _CURRENT.set(None)


def reset_worker(token: object) -> None:
    _CURRENT.reset(token)  # type: ignore[arg-type]


@contextmanager
def worker_binding(value: Mapping[str, Any] | None) -> Iterator[None]:
    token = bind_worker(value)
    try:
        yield
    finally:
        reset_worker(token)


def bind_semantic_params(params: Mapping[str, Any], *, require: bool = True) -> dict[str, Any]:
    """Merge trusted identity into a semantic request.

    Explicit identity is never accepted as an assertion.  It is rejected even
    when it happens to match, preventing callers from accidentally treating
    transport metadata as authorable result content.
    """
    raw_capability = str(params.get(WORKER_CAPABILITY_FIELD) or "").strip()
    binding = require_binding(raw_capability) if require else current_binding()
    explicit = sorted(set(params) & SERVER_OWNED_FIELDS)
    if explicit:
        raise WorkerBindingError(
            "worker identity is server-owned; omit " + ", ".join(explicit)
        )
    if binding is None:
        return dict(params)
    # Capability transport authenticates the call at this boundary; it is not
    # semantic input for downstream worker operations.  Do not leak either
    # the bearer value or its digest into their additionalProperties=False
    # validators (worker_question is the first strict consumer).
    merged = {
        key: value for key, value in binding.items()
        if key not in {WORKER_CAPABILITY_FIELD, "worker_capability_digest"}
    }
    merged.update({
        key: value for key, value in dict(params).items()
        if key not in {WORKER_CAPABILITY_FIELD, "worker_capability_digest"}
    })
    return merged


def binding_from_environment() -> dict[str, str] | None:
    """Read one host-owned JSON binding at process startup, if supplied."""
    raw = os.environ.get("CORTEX_WORKER_BINDING_JSON", "").strip()
    if not raw:
        return None
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise WorkerBindingError("CORTEX_WORKER_BINDING_JSON is not valid JSON") from exc
    return normalize_binding(value)
