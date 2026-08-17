"""Ports for RecordReport; implementations live at the runtime edge."""
from __future__ import annotations

from contextlib import AbstractContextManager
from pathlib import Path
from typing import Any, Protocol

from .domain import RecordReportCommand, RecordReportOutcome


class ReportMutationPort(Protocol):
    """Owns the established validation, idempotency, artifact and finding flow."""

    def mutate(self, command: RecordReportCommand) -> RecordReportOutcome: ...


class ProjectionRepairPort(Protocol):
    """Restores replaceable exports after canonical report persistence."""

    def restore(self, outcome: RecordReportOutcome, params: dict[str, Any]) -> None: ...


class ReportUnitOfWork(Protocol):
    """Coordinates a report mutation without creating an extra ledger owner."""

    def atomic(self, command: RecordReportCommand) -> AbstractContextManager[None]: ...


class LedgerRootPort(Protocol):
    """Optional boundary for adapters that need an authoritative ledger root."""

    def root_for(self, params: dict[str, Any]) -> Path: ...
