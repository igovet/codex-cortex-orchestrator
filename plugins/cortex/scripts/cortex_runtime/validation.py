"""Small shared collector for public tool boundary validation.

Boundary validation is deliberately side-effect free.  Individual field
checks are reported in declaration/path order; callers may then run
cross-field checks only when the independent checks pass.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any


class ValidationFailure(ValueError):
    """A retryable validation failure carrying all independent diagnostics."""

    def __init__(self, diagnostics: list[dict[str, Any]]) -> None:
        self.diagnostics = diagnostics
        super().__init__("; ".join(str(item.get("message") or "validation failed") for item in diagnostics))


def collect_validations(
    checks: Iterable[tuple[str, Callable[[], str | None]]],
    *,
    code: str,
) -> None:
    """Run independent checks and raise one stable, path-aware failure."""
    diagnostics: list[dict[str, Any]] = []
    for path, check in checks:
        message = check()
        if message:
            diagnostics.append({"code": code, "path": path, "message": message})
    if diagnostics:
        raise ValidationFailure(diagnostics)

