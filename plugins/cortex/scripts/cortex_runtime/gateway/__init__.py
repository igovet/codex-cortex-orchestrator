"""Cortex Model Gateway runtime components.

The gateway is deliberately separate from the MCP server.  The small pure
modules in this package are usable without an HTTP runtime, which keeps the
policy contract easy to test and audit.
"""

from .classifier import is_compaction_request
from .config import ConfigError, ConfigLoader, ConfigManager, PolicySnapshot
from .transform import TransformError, transform_compaction

__all__ = [
    "ConfigError",
    "ConfigLoader",
    "ConfigManager",
    "PolicySnapshot",
    "TransformError",
    "is_compaction_request",
    "transform_compaction",
]
