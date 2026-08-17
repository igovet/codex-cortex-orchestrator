"""Pure values used by the RecordReport application use case."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class RecordReportCommand:
    """An immutable request passed across the RecordReport boundary."""

    params: dict[str, Any]

    @classmethod
    def from_params(cls, params: Mapping[str, Any]) -> "RecordReportCommand":
        if not isinstance(params, Mapping):
            raise ValueError("record_report parameters must be an object")
        # Copying prevents adapters from retaining a caller-owned mutable map
        # while leaving nested protocol payloads untouched for compatibility.
        return cls(params=dict(params))


@dataclass(frozen=True)
class RecordReportOutcome:
    """The compatibility result returned by the established report protocol."""

    value: dict[str, Any]

    @property
    def was_rejected(self) -> bool:
        return self.value.get("recorded") is False
