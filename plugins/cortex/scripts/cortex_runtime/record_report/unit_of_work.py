"""Transaction ownership for the compatibility RecordReport boundary."""
from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from .domain import RecordReportCommand


class ExistingLedgerUnitOfWork:
    """Respect the existing mutation's state-lock and SQLite transaction owner.

    This is intentionally a no-op context manager.  Opening ``ledger_db``
    here would create a second connection around a legacy mutation that still
    coordinates state locking itself, altering both lock ordering and failure
    behaviour.  The use case makes this ownership explicit until repositories
    take over the full mutation in a later slice.
    """

    @contextmanager
    def atomic(self, command: RecordReportCommand) -> Iterator[None]:
        del command
        yield
