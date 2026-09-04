"""Host-neutral capability evidence and injected native coordination boundary.

No native API is invented here: the operator supplies a transport implementing
the advertised host contract. This module cannot spawn from the MCP backend.
Snapshots describe evidence, not qualification success or human authorship.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import os
from pathlib import Path
import stat
import re
from typing import Any, Mapping, Protocol


STATES = frozenset({"unverified", "declared", "configured", "observed", "unsupported"})
OPERATIONS = frozenset({"spawn", "list", "wait", "send_message", "interrupt"})
EFFORTS = frozenset({"low", "medium", "high", "xhigh", "max"})
CAPABILITIES = frozenset({
    "subagents.custom_agent", "subagents.packaged_profiles",
    "models.default", "models.gpt-5.6-terra", "models.gpt-5.6-sol",
    "efforts.default", "efforts.gpt-5.6-terra", "efforts.gpt-5.6-sol",
    "capacity.configured", "capacity.available", "capacity.observed",
    "hooks.events", "hooks.pre_tool_mcp", "hooks.pre_tool_agent",
    "hooks.subagent_start_fields", "hooks.subagent_stop_fields",
    "recovery.resume", "recovery.compact", "input.ordered_submissions",
    "input.direct_user_origin",
})


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def normalize_agent_projection(response: Any, arguments: Any) -> list[dict[str, str]] | None:
    """Normalize the supported complete native list without inferring absence.

    Partial, filtered, foreign-root and ambiguous lists cannot prove quiescence.
    Unknown native states remain present. Names are hashed before domain storage.
    This boundary parses host data; it does not attest the event's provenance.
    """
    if isinstance(response, str):
        # CLI PostToolUse transports native results as JSON text. Decode only
        # that bounded envelope; duplicate keys must not conceal another state.
        def unique_object(pairs):
            value = {}
            for key, item in pairs:
                if key in value:
                    raise ValueError("duplicate native projection key")
                value[key] = item
            return value
        try:
            if len(response.encode("utf-8")) > 256 * 1024:
                return None
            response = json.loads(response, object_pairs_hook=unique_object)
        except (ValueError, UnicodeError, RecursionError):
            return None
    if arguments != {} or not isinstance(response, Mapping) or set(response) != {"agents"}:
        return None
    agents = response["agents"]
    if not isinstance(agents, list) or not agents or len(agents) > 256:
        return None
    result, names = [], set()
    root_seen = False
    for agent in agents:
        if not isinstance(agent, Mapping):
            return None
        name, status = agent.get("agent_name"), agent.get("agent_status")
        completed = (isinstance(status, Mapping) and set(status) == {"completed"}
                     and (status["completed"] is None or isinstance(status["completed"], str)))
        if not isinstance(name, str) or re.fullmatch(r"/(?:[a-z0-9_-]+/)*[a-z0-9_-]+", name) is None or not (isinstance(status, str) or completed):
            return None
        if name != "/root" and not name.startswith("/root/"):
            return None
        root_seen |= name == "/root"
        short_name = name.rsplit("/", 1)[-1]
        if short_name in names:
            return None
        names.add(short_name)
        result.append({"name": hashlib.sha256(short_name.encode()).hexdigest(),
                       # A fresh list's Interrupted state follows TurnAborted
                       # in Codex, unlike interrupt()'s previous-state reply.
                       # It proves no current turn, not a successful result.
                       "state": "idle" if completed or status in ("idle", "interrupted") else "present"})
    return sorted(result, key=lambda item: item["name"]) if root_seen else None


@dataclass(frozen=True)
class Capability:
    state: str = "unverified"
    evidence_refs: tuple[str, ...] = ()
    value_json: str = "null"

    def __post_init__(self) -> None:
        if self.state not in STATES:
            raise ValueError("unknown capability state")
        if self.state != "unverified" and not self.evidence_refs:
            raise ValueError("capability requires evidence")
        if any(not isinstance(ref, str) or not ref for ref in self.evidence_refs):
            raise ValueError("invalid evidence reference")
        if canonical(json.loads(self.value_json)) != self.value_json:
            raise ValueError("capability value must be canonical JSON")


@dataclass(frozen=True)
class HostIdentity:
    host: str
    app_version: str
    engine_version: str
    payload_digest: str
    catalogue_digest: str
    config_digest: str
    connection_generation: str

    def __post_init__(self) -> None:
        if self.host not in {"cli", "desktop"}:
            raise ValueError("unsupported host")
        if any(not isinstance(value, str) or not value for value in asdict(self).values()):
            raise ValueError("host identity must be explicit")


@dataclass(frozen=True)
class ToolContract:
    operation: str
    native_name: str
    schema_json: str
    evidence: Capability

    def __post_init__(self) -> None:
        if self.operation not in OPERATIONS or not self.native_name:
            raise ValueError("invalid native operation")
        schema = json.loads(self.schema_json)
        if not isinstance(schema, dict) or canonical(schema) != self.schema_json:
            raise ValueError("tool schema must be canonical JSON object")


@dataclass(frozen=True)
class CodexHostCapabilities:
    identity: HostIdentity
    tools: tuple[ToolContract, ...]
    capabilities: tuple[tuple[str, Capability], ...]

    def __post_init__(self) -> None:
        if len({tool.operation for tool in self.tools}) != len(self.tools):
            raise ValueError("duplicate tool operation")
        if len({name for name, _ in self.capabilities}) != len(self.capabilities):
            raise ValueError("duplicate capability")

    @property
    def snapshot_digest(self) -> str:
        return digest(asdict(self))

    def capability(self, name: str) -> Capability:
        return dict(self.capabilities).get(name, Capability())

    def require_current(self, identity: HostIdentity) -> None:
        if self.identity != identity:
            raise ValueError("host capability snapshot is stale")


class CodexHostProbe:
    """Capture explicit sanitized declarations/observations without active calls.

    Evidence provenance must be checked by the supplying host observer. Passing
    a fixture or model assertion here does not make it host-attested evidence.
    """

    @staticmethod
    def capture(identity: HostIdentity, tools: tuple[ToolContract, ...],
                capabilities: Mapping[str, Capability]) -> CodexHostCapabilities:
        if set(capabilities) - CAPABILITIES:
            raise ValueError("unknown capability")
        return CodexHostCapabilities(identity, tuple(sorted(tools, key=lambda t: t.operation)),
                                     tuple((name, capabilities.get(name, Capability()))
                                           for name in sorted(CAPABILITIES)))

    @staticmethod
    def save(snapshot: CodexHostCapabilities, path: Path) -> None:
        # Exclusive creation in an owned private directory; an existing file
        # or symlink cannot be overwritten. Never follow symlinked ancestors.
        body = canonical({"digest": snapshot.snapshot_digest, "snapshot": asdict(snapshot)})
        if len(body.encode("utf-8")) > 1024 * 1024:
            raise ValueError("capability snapshot exceeds bound")
        if not path.is_absolute() or ".." in path.parts or any(p.is_symlink() for p in path.parents):
            raise ValueError("snapshot requires a lexical non-symlink directory")
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            info = os.fstat(directory)
            if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
                raise ValueError("snapshot directory must be owner-private")
            fd = os.open(path.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write(body + "\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.fsync(directory)
        finally:
            os.close(directory)


@dataclass(frozen=True)
class SpawnSpec:
    dispatch_ref: str
    message: str
    profile: str
    model_route: str = "default"
    effort: str = "medium"

    def __post_init__(self) -> None:
        if not self.dispatch_ref or not self.message or not self.profile:
            raise ValueError("spawn requires a bound dispatch")
        if self.model_route not in {"default", "gpt-5.6-terra", "gpt-5.6-sol"}:
            raise ValueError("Luna must use configured default, not an explicit model")
        if self.effort not in EFFORTS:
            raise ValueError("unsupported effort")

    def model_options(self) -> dict[str, str]:
        result = {"reasoning_effort": self.effort}
        if self.model_route != "default":
            result["model"] = self.model_route
        return result


@dataclass(frozen=True)
class HostObservation:
    operation: str
    status: str
    evidence_ref: str
    agent_refs: tuple[str, ...] = ()
    complete: bool = False
    quiescent: bool = False

    def __post_init__(self) -> None:
        allowed = {
            "spawn": {"started", "failed", "ambiguous"},
            "list": {"observed", "unverified"},
            "wait": {"completed", "attention", "timeout", "unverified", "failed"},
            "send_message": {"sent", "received", "ambiguous", "failed"},
            "interrupt": {"acknowledged", "stopped", "ambiguous", "failed"},
        }
        if self.operation not in allowed or self.status not in allowed[self.operation]:
            raise ValueError("invalid host observation")
        if not self.evidence_ref:
            raise ValueError("host observation requires evidence")
        if self.operation == "spawn" and self.status == "started" and len(self.agent_refs) != 1:
            raise ValueError("confirmed spawn requires exactly one agent")
        if self.quiescent and not (self.operation == "interrupt" and self.status == "stopped"):
            raise ValueError("acknowledgement is not quiescence")
        if self.complete and not (self.operation == "list" and self.status == "observed"):
            raise ValueError("completeness applies only to observed agent lists")


class NativeTransport(Protocol):
    async def invoke(self, contract: ToolContract, intent: Any) -> HostObservation: ...


class CodexHostAdapter:
    """Translate intent through injected host transport; never schedule or retry."""
    host: str

    @staticmethod
    def prepare_spawn(*, task_name: str, message: str, model_route: str, effort: str) -> dict[str, str]:
        """Encode the supported native call for the LLM-owned dispatch boundary.

        This is preparation, not execution. The existing host hook binds the
        actual native call and worker; no MCP operation waits for a child it
        cannot itself launch. Short discriminators precede the message.
        """
        if not isinstance(task_name, str) or not task_name or not isinstance(message, str) or not message:
            raise ValueError("native spawn identity and message are required")
        if len(message.encode("utf-8")) > 64 * 1024:
            raise ValueError("native spawn message exceeds bound")
        if model_route not in {"default", "gpt-5.6-terra", "gpt-5.6-sol"} or effort not in EFFORTS:
            raise ValueError("unsupported native model route or effort")
        arguments = {"fork_turns": "none", "task_name": task_name, "reasoning_effort": effort}
        if model_route != "default":
            arguments["model"] = model_route
        arguments["message"] = message
        return arguments

    def __init__(self, snapshot: CodexHostCapabilities, transport: NativeTransport):
        if snapshot.identity.host != self.host:
            raise ValueError("wrong host adapter")
        self.snapshot = snapshot
        self.transport = transport

    async def _invoke(self, operation: str, intent: Any, current: HostIdentity) -> HostObservation:
        self.snapshot.require_current(current)
        contract = next((t for t in self.snapshot.tools if t.operation == operation), None)
        if contract is None or contract.evidence.state not in {"declared", "observed"}:
            raise ValueError("native operation is unavailable or unverified")
        result = await self.transport.invoke(contract, intent)
        if not isinstance(result, HostObservation) or result.operation != operation:
            raise ValueError("transport returned mismatched observation")
        return result

    async def spawn(self, spec: SpawnSpec, current: HostIdentity) -> HostObservation:
        self.snapshot.require_current(current)
        route = self.snapshot.capability("models." + spec.model_route)
        effort = self.snapshot.capability("efforts." + spec.model_route)
        if route.state not in {"declared", "configured", "observed"}:
            raise ValueError("model route is unverified")
        allowed = json.loads(effort.value_json)
        if effort.state not in {"declared", "configured", "observed"} or not isinstance(allowed, list) or spec.effort not in allowed:
            raise ValueError("effort route is unverified")
        return await self._invoke("spawn", spec, current)

    async def list_agents(self, current: HostIdentity) -> HostObservation:
        return await self._invoke("list", None, current)

    async def wait(self, agent_refs: tuple[str, ...], current: HostIdentity) -> HostObservation:
        return await self._invoke("wait", agent_refs, current)

    async def send_message(self, agent_ref: str, message: str, current: HostIdentity) -> HostObservation:
        return await self._invoke("send_message", (agent_ref, message), current)

    async def interrupt(self, agent_ref: str, current: HostIdentity) -> HostObservation:
        return await self._invoke("interrupt", agent_ref, current)


class CodexCliHostAdapter(CodexHostAdapter):
    host = "cli"


class CodexDesktopHostAdapter(CodexHostAdapter):
    host = "desktop"

    @staticmethod
    def observe_item(item: Mapping[str, Any], *, sender_thread: str,
                     evidence_ref: str) -> HostObservation | None:
        """Decode the installed 0.153 app-server collab item contract.

        Only a trusted, session-bound app-server observer may supply this input.
        Item completion means tool completion, not task completion. Prompt/model
        text and embedded agent messages never leave this decoder. No private
        transcript fallback and no conversion from older item formats.
        """
        operations = {"spawnAgent": "spawn", "listAgents": "list", "wait": "wait",
                      "sendMessage": "send_message", "interruptAgent": "interrupt"}
        if not isinstance(item, Mapping) or item.get("type") != "collabAgentToolCall":
            return None
        if not sender_thread or item.get("senderThreadId") != sender_thread:
            return None
        operation = operations.get(item.get("tool")) if isinstance(item.get("tool"), str) else None
        phase = item.get("status")
        if operation is None or phase not in {"completed", "failed", "interrupted"}:
            return None
        receivers, states = item.get("receiverThreadIds"), item.get("agentsStates")
        if (not isinstance(item.get("id"), str) or not item["id"] or not isinstance(receivers, list)
                or len(receivers) > 256 or any(not isinstance(ref, str) or not ref for ref in receivers)
                or len(set(receivers)) != len(receivers) or not isinstance(states, Mapping) or len(states) > 256):
            return None
        refs = tuple(receivers)
        if phase != "completed":
            status = {"spawn": "ambiguous" if refs else "failed", "list": "unverified",
                      "wait": "failed", "send_message": "ambiguous", "interrupt": "ambiguous"}[operation]
        elif operation == "spawn":
            status = "started" if len(refs) == 1 else "ambiguous"
        elif operation == "wait":
            # Last-known state for an unrelated agent cannot settle a target.
            target_states = [states.get(ref) for ref in refs]
            completed = any(isinstance(value, Mapping) and value.get("status") == "completed" for value in target_states)
            attention = any(isinstance(value, Mapping) and value.get("status") in {"errored", "interrupted"} for value in target_states)
            status = "completed" if completed else "attention" if attention else "unverified"
        else:
            status = {"list": "observed", "send_message": "sent", "interrupt": "acknowledged"}[operation]
        return HostObservation(operation, status, evidence_ref, refs)
