"""Pure, intentionally narrow Remote Compaction V2 classifier."""

from collections.abc import Mapping


def is_compaction_request(body: object) -> bool:
    """Return true only for a direct ``compaction_trigger`` input item.

    No text, token count, context-management value, lifecycle event, or
    nested history is inspected.  Callers must first establish the method and
    canonical target path.
    """

    if not isinstance(body, Mapping):
        return False
    items = body.get("input")
    if not isinstance(items, list):
        return False
    return any(
        isinstance(item, Mapping) and item.get("type") == "compaction_trigger"
        for item in items
    )


def has_unsupported_control(body: object) -> bool:
    """Detect direct controls which could conflict with request-level policy."""

    if not isinstance(body, Mapping) or not isinstance(body.get("input"), list):
        return False
    return any(
        isinstance(item, Mapping) and item.get("type") == "configuration_update"
        for item in body["input"]
    )
