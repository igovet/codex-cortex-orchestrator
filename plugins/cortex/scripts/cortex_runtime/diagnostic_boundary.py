"""Small fail-open boundary for passive, value-free diagnostics.

This boundary is intentionally opt-in and lane-A-only.  Callers identify the
passive operation being attempted; protected storage, authorization, evidence,
publication, cleanup and audit work must never be wrapped here.  A diagnostic
failure can therefore preserve execution continuity without becoming a success
or acceptance signal.
"""
from __future__ import annotations

import json
import re
import sys
from collections.abc import Callable

PASSIVE_OPERATIONS = frozenset({
    "observation",
    "logging",
    "telemetry",
    "diagnostic_serialization",
})
MAX_DIAGNOSTIC_BYTES = 512
_SAFE_NAME = re.compile(r"[^A-Za-z0-9_.:-]")


def _safe_name(value: object, fallback: str) -> str:
    """Return only a short identifier; never include exception text or paths."""
    if not isinstance(value, str) or not value:
        return fallback
    return _SAFE_NAME.sub("_", value)[:96] or fallback


def diagnostic_failure(operation: str, exc: Exception) -> dict[str, object]:
    """Build a bounded, value-free passive-diagnostic finding."""
    return {
        "diagnostic_outcome": "failed",
        "diagnostic_operation": _safe_name(operation, "passive"),
        "diagnostic_code": "passive_diagnostic_failure",
        "error_type": _safe_name(type(exc).__name__, "Exception"),
        "execution_continued": True,
        "evidence_admissibility": "unverified",
    }


def emit_bounded_diagnostic(stream, finding: dict[str, object]) -> None:
    """Best-effort private-safe emission for a passive diagnostic finding."""
    payload = json.dumps(finding, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=True, allow_nan=False)
    stream.write("Cortex passive diagnostic: " + payload[:MAX_DIAGNOSTIC_BYTES] + "\n")
    flush = getattr(stream, "flush", None)
    if callable(flush):
        flush()


def run_passive_diagnostic(action: Callable[[], object], *, operation: str,
                           emit: Callable[[dict[str, object]], object] | None = None,
                           fallback_stream=None) -> dict[str, object]:
    """Run one explicitly passive action and return a neutral continuation result.

    Only the callback supplied for an allowlisted passive operation is covered.
    All exceptions from the passive action are diagnostic, including storage and
    permission failures while writing telemetry. Protected operations must never
    be passed as callbacks. The fallback emission is attempted once and every
    exception from that best-effort emitter is also non-blocking.
    No exception message, path, request value, or result body is retained.
    """
    if operation not in PASSIVE_OPERATIONS:
        raise ValueError("operation is not a passive diagnostic")
    try:
        action()
    except Exception as exc:
        finding = diagnostic_failure(operation, exc)
        emitter = emit
        if emitter is None and fallback_stream is not None:
            emitter = lambda value: emit_bounded_diagnostic(fallback_stream, value)
        if emitter is None:
            emitter = lambda value: emit_bounded_diagnostic(sys.stderr, value)
        try:
            emitter(finding)
        except Exception:
            # A broken logger/stream is still passive.  The neutral result keeps
            # the protected caller's execution and its original outcome intact;
            # emitter-side StoreError/PermissionError are not protected actions.
            pass
        return {"execution_continued": True, "diagnostic": finding}
    return {"execution_continued": True, "diagnostic": None}
