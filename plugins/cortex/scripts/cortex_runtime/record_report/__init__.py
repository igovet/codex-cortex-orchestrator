"""RecordReport vertical slice.

The package is deliberately dependency-inverted: domain, ports, and use-case
code know nothing about the executable facade or the legacy report transport.
"""

from .facade import RecordReportFacade, build_compatibility_facade

__all__ = ["RecordReportFacade", "build_compatibility_facade"]
