"""Runtime-edge adapters for the transitional RecordReport slice.

The legacy mutation already owns the active state-lock/SQLite lifecycle.  The
adapter intentionally delegates rather than opening another connection or
wrapping it in a nested transaction; later slices can replace this adapter
with repository-level calls without changing the application interface.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .domain import RecordReportCommand, RecordReportOutcome


class ExistingReportMutationAdapter:
    """Adapt the current canonical mutation function to a named port."""

    def __init__(self, mutation: Callable[[dict[str, Any]], dict[str, Any]]) -> None:
        self._mutation = mutation

    def mutate(self, command: RecordReportCommand) -> RecordReportOutcome:
        return RecordReportOutcome(self._mutation(command.params))


class ExistingProjectionRepairAdapter:
    """Adapt the current best-effort outbox materializer to a named port."""

    def __init__(self, repair: Callable[[dict[str, Any], dict[str, Any]], None]) -> None:
        self._repair = repair

    def restore(self, outcome: RecordReportOutcome, params: dict[str, Any]) -> None:
        self._repair(outcome.value, params)
