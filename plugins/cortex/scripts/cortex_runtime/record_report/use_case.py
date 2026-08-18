"""Application service for canonical worker report recording."""
from __future__ import annotations

from typing import Any, Mapping

from .domain import RecordReportCommand
from .ports import ProjectionRepairPort, ReportMutationPort, ReportUnitOfWork


class RecordReportUseCase:
    """Preserve report protocol results while isolating runtime collaborators."""

    def __init__(self, *, mutation: ReportMutationPort, projections: ProjectionRepairPort,
                 unit_of_work: ReportUnitOfWork) -> None:
        self._mutation = mutation
        self._projections = projections
        self._unit_of_work = unit_of_work

    def execute(self, params: Mapping[str, Any]) -> dict[str, Any]:
        command = RecordReportCommand.from_params(params)
        with self._unit_of_work.atomic(command):
            outcome = self._mutation.mutate(command)
        if not outcome.was_rejected:
            self._projections.restore(outcome, command.params)
        return outcome.value
