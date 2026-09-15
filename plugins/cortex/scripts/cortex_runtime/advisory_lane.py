"""Optional, model-owned Rubber Duck advisory lane.

This module deliberately contains no routing or acceptance authority.  It turns
the live host catalogue into a small, privacy-safe consultation helper that is
also convenient for offline/fake-MCP tests.
"""
from __future__ import annotations

import hashlib
from itertools import islice
import json
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping

QUALIFIED_TOOL = "mcp__rubber_duck__ask_duck"
_MAX_PACKET = 6000
_MAX_REASON = 96
_NEGATIVE = {"routine", "reversible", "read", "verification", "incident", "recovery", "repository-evidence"}
_POSITIVE = {"consequential", "contradictory", "repeated-failure"}


@dataclass(frozen=True)
class Capability:
    qualified_name: str
    declaration: Mapping[str, Any]
    provider: str | None = None
    model: str | None = None


def _identity(entry: Mapping[str, Any]) -> bool:
    """Require a real qualified name and an explicit Rubber Duck identity."""
    if entry.get("name") != QUALIFIED_TOOL:
        return False
    # Only canonical identity fields qualify.  Metadata such as provider is
    # never an identity token, and every supplied identity alias must agree.
    if entry.get("server") != "mcp-rubber-duck":
        return False
    return all(entry.get(key, "mcp-rubber-duck") == "mcp-rubber-duck"
               for key in ("mcp", "provider_id"))


def discover(catalogue: Iterable[Mapping[str, Any]], *, cache: dict | None = None) -> Capability | None:
    """Bounded, once-per-context discovery; absence is a normal no-op."""
    if cache is not None and "rubber_duck" in cache:
        return cache["rubber_duck"]
    found = None
    for entry in islice(catalogue, 128):
        if isinstance(entry, Mapping) and _identity(entry):
            schema = entry.get("inputSchema", entry.get("schema", {}))
            props = schema.get("properties", {}) if isinstance(schema, Mapping) else {}
            required = schema.get("required", []) if isinstance(schema, Mapping) else []
            if not isinstance(schema, Mapping) or schema.get("type") != "object":
                continue
            if not isinstance(required, list) or "prompt" not in required:
                continue
            prompt_schema = props.get("prompt")
            prompt_enum = prompt_schema.get("enum") if isinstance(prompt_schema, Mapping) else None
            if (not isinstance(props, Mapping) or not isinstance(prompt_schema, Mapping)
                    or prompt_schema.get("type") != "string"
                    or (prompt_enum is not None and (not isinstance(prompt_enum, list) or not prompt_enum
                        or any(not isinstance(item, str) or len(item) > _MAX_PACKET for item in prompt_enum)))):
                continue
            # Metadata is optional.  Preserve only values explicitly exposed.
            provider = entry.get("provider") if isinstance(entry.get("provider"), str) else None
            model = entry.get("model") if isinstance(entry.get("model"), str) else None
            found = Capability(QUALIFIED_TOOL, dict(entry), provider, model)
            break
    if cache is not None:
        cache["rubber_duck"] = found
    return found


def packet_identity(packet: Mapping[str, Any]) -> str:
    """Hash canonical packet content without exposing its contents in telemetry."""
    encoded = json.dumps(packet, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode()).hexdigest()


def _schema_value(value: Any, declaration: Mapping[str, Any], key: str) -> bool:
    """Accept only simple, explicitly declared JSON-schema constraints."""
    kind = declaration.get("type")
    if kind == "string":
        if not isinstance(value, str) or len(value) > (_MAX_PACKET if key == "prompt" else 4096):
            return False
    elif kind == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 2:
            return False
    elif kind == "array" and key == "images":
        if not isinstance(value, list) or len(value) > 4 or any(not isinstance(item, str) or len(item) > 4096 for item in value):
            return False
    else:
        return False
    enum = declaration.get("enum")
    if enum is not None and (not isinstance(enum, list) or value not in enum):
        return False
    return True


def consider_advisory_lane(transition: str, *, question: str | None = None) -> dict[str, Any]:
    """Return model-owned consult/skip guidance; never invokes a tool."""
    transition = str(transition)
    if transition in _POSITIVE and question:
        return {"decision": "consult", "transition": transition, "reason": "material-uncertainty"}
    reason = "negative-control" if transition in _NEGATIVE else "not-eligible"
    return {"decision": "skip", "transition": transition, "reason": reason}


def consult(capability: Capability | None, packet: Mapping[str, Any], *, invoke: Callable[..., Any] | None = None,
            seen: set[str] | None = None, timeout: float = 5.0) -> dict[str, Any]:
    """Perform at most one bounded optional call and degrade quietly on errors."""
    if capability is None:
        return {"status": "unavailable", "reason": "capability-absent", "advisory_only": True}
    if not isinstance(packet, Mapping) or not isinstance(packet.get("prompt"), str) or not packet["prompt"].strip():
        return {"status": "skipped", "reason": "malformed-packet", "advisory_only": True}
    schema = capability.declaration.get("inputSchema", capability.declaration.get("schema", {}))
    props = schema.get("properties", {}) if isinstance(schema, Mapping) else {}
    if not isinstance(props, Mapping) or "prompt" not in props:
        return {"status": "unavailable", "reason": "invalid-schema", "advisory_only": True}
    prompt = packet["prompt"]
    prompt_schema = props.get("prompt")
    if len(prompt) > _MAX_PACKET:
        return {"status": "skipped", "reason": "oversized-prompt", "advisory_only": True}
    if not isinstance(prompt_schema, Mapping) or not _schema_value(prompt, prompt_schema, "prompt"):
        return {"status": "skipped", "reason": "invalid-prompt", "advisory_only": True}
        return {"status": "skipped", "reason": "oversized-prompt", "advisory_only": True}
    body = {"prompt": prompt}
    for key in ("provider", "model", "temperature", "images"):
        if key in packet and packet[key] is not None:
            declaration = props.get(key)
            if not isinstance(declaration, Mapping) or not _schema_value(packet[key], declaration, key):
                return {"status": "skipped", "reason": "undeclared-field", "advisory_only": True}
            body[key] = packet[key]
    identity = packet_identity(body)
    if seen is not None and identity in seen:
        return {"status": "skipped", "reason": "duplicate-packet", "packet_id": identity, "advisory_only": True}
    if seen is not None:
        seen.add(identity)
    if invoke is None:
        return {"status": "unavailable", "reason": "no-invoker", "packet_id": identity, "advisory_only": True}
    try:
        result = invoke(QUALIFIED_TOOL, body, timeout=timeout)
        return {"status": "consulted", "packet_id": identity, "result": result, "advisory_only": True,
                "automatic_routing": False, "acceptance_gate": False}
    except TimeoutError:
        return {"status": "failed", "reason": "timeout", "packet_id": identity, "advisory_only": True}
    except Exception:
        return {"status": "failed", "reason": "tool-error", "packet_id": identity, "advisory_only": True}


# Descriptive aliases used by integrations and tests.
discover_capability = discover
consult_advisory_lane = consult
