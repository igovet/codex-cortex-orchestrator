"""Bounded, secret-free gateway diagnostics."""

from __future__ import annotations

import json
import logging
from logging.handlers import RotatingFileHandler
import re
import uuid

_SAFE = re.compile(r"[^A-Za-z0-9_.:/-]")
MAX_FIELD = 96
MAX_LOG_BYTES = 256 * 1024
REQUIRED_OUTCOME_FIELDS = (
    "request_id",
    "request_kind",
    "original_model",
    "routed_model",
    "original_reasoning_effort",
    "routed_reasoning_effort",
    "config_revision",
    "http_status",
    "transport_outcome",
    "duration_ms",
)


def request_id() -> str:
    return uuid.uuid4().hex


def safe_value(value: object, default: str = "unspecified") -> str:
    if value is None or value == "":
        return default
    return _SAFE.sub("_", str(value))[:MAX_FIELD]


def generated_error(code: str, message: str, *, status: int = 400) -> tuple[int, bytes, dict[str, str]]:
    payload = {"error": {"type": "cortex_gateway_error", "code": safe_value(code), "message": message}}
    return status, json.dumps(payload, separators=(",", ":")).encode(), {"Content-Type": "application/json"}


def log_outcome(logger: logging.Logger, **fields: object) -> None:
    safe = {
        key: safe_value(fields.get(key))
        for key in REQUIRED_OUTCOME_FIELDS
    }
    safe.update({
        key: safe_value(value)
        for key, value in fields.items()
        if key not in REQUIRED_OUTCOME_FIELDS and key not in {"token", "authorization", "body", "payload"}
    })
    logger.info("gateway_outcome %s", " ".join(f"{key}={value}" for key, value in safe.items()))


def configure_file_logging(path: object) -> logging.Handler:
    """Install a private, bounded outcome log for the standalone gateway."""
    handler = RotatingFileHandler(path, mode="a", maxBytes=MAX_LOG_BYTES, backupCount=1, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger = logging.getLogger("cortex.gateway")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    logger.handlers.clear()
    logger.addHandler(handler)
    return handler
