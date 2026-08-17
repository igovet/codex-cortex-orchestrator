"""Compatibility facade wiring RecordReport ports at the runtime edge."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .sqlite_adapter import ExistingProjectionRepairAdapter, ExistingReportMutationAdapter
from .unit_of_work import ExistingLedgerUnitOfWork
from .use_case import RecordReportUseCase


class RecordReportFacade:
    """Stable callable facade used by the existing MCP transport module."""

    def __init__(self, use_case: RecordReportUseCase) -> None:
        self._use_case = use_case

    def record_report(self, params: dict[str, Any]) -> dict[str, Any]:
        return self._use_case.execute(params)


def build_compatibility_facade(
    *,
    mutation: Callable[[dict[str, Any]], dict[str, Any]],
    restore_projections: Callable[[dict[str, Any], dict[str, Any]], None],
) -> RecordReportFacade:
    """Wire existing helpers without importing the executable facade here."""
    return RecordReportFacade(RecordReportUseCase(
        mutation=ExistingReportMutationAdapter(mutation),
        projections=ExistingProjectionRepairAdapter(restore_projections),
        unit_of_work=ExistingLedgerUnitOfWork(),
    ))
