"""Black-box V12 release and protocol gate for the Cortex plugin.

The gate intentionally runs the packaged stdio MCP server. Its behavioural
checks must not call the ledger service directly: the public tool schema is the
installable contract. SQLite inspection below is restricted to proof of
durability, project isolation, and atomic concurrent writes.
"""
from __future__ import annotations

import ast
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import re
import runpy
import selectors
import sqlite3
import stat
import subprocess
import sys
import threading
import time
import tomllib
from typing import Any, Mapping


EXPECTED_TOOLS = (
    "create_task",
    "inspect_task",
    "create_delegation",
    "read_delegation",
    "submit_report",
    "read_reports",
    "set_governance_mode",
    "record_initiative",
    "inspect_governance",
    "submit_governance_closure",
    "record_user_decision",
)

EXPECTED_INPUT_FIELDS = {
    "create_task": {
        "project_root", "objective", "user_request_original", "user_language", "task_contract_version",
        "requirements", "constraints", "acceptance_criteria", "verification_plan", "context",
        "idempotency_key",
    },
    "inspect_task": {"task_ref", "after_sequence", "limit"},
    "create_delegation": {
        "task_ref", "objective", "role", "profile_name", "scope", "instructions",
        "parent_delegation_ref", "input_report_refs", "input_decision_refs", "approval_decision_ref", "model", "reasoning_effort",
        "idempotency_key",
    },
    "read_delegation": {"delegation_ref", "after_sequence", "limit"},
    "submit_report": {
        "delegation_ref", "mode", "report_type", "status", "content", "report_ref", "chunk_index",
        "section", "expected_chunk_count", "expected_content_digest", "abort_reason_en", "supersedes_report_ref",
        "review_policy", "idempotency_key",
    },
    "read_reports": {"report_refs", "sections", "cursor", "max_bytes", "consumer_delegation_ref", "reader_kind"},
    "set_governance_mode": {
        "task_ref", "mode", "rationale", "reason", "risk_factors", "source", "initiative_ref", "idempotency_key",
    },
    "record_initiative": {
        "task_ref", "goal", "initiative_ref", "parent_initiative_ref", "risk", "status", "dependency_refs",
        "linked_task_refs", "linked_delegation_refs", "linked_report_refs", "linked_decision_refs", "notes", "idempotency_key",
    },
    "inspect_governance": {"task_ref", "initiative_ref", "after_sequence", "limit"},
    "submit_governance_closure": {
        "task_ref", "subject_type", "subject_ref", "verdict", "evidence", "unresolved_risks", "follow_ups",
        "initiative_status", "completion_notes", "idempotency_key",
    },
    "record_user_decision": {
        "task_ref", "subject_type", "subject_ref", "subject_digest", "decision_type", "prompt_en",
        "response_original", "response_en", "user_language", "approval_handle", "approval_view_content_digest",
        "approval_view_source_sequence", "supersedes_decision_ref", "idempotency_key",
    },
}

EXPECTED_REQUIRED_FIELDS = {
    "create_task": {
        "project_root", "objective", "user_request_original", "user_language",
        "task_contract_version", "requirements", "constraints",
        "acceptance_criteria", "verification_plan",
    },
    "inspect_task": {"task_ref"},
    "create_delegation": {"task_ref", "objective", "role", "profile_name", "scope", "instructions", "model", "reasoning_effort"},
    "read_delegation": {"delegation_ref", "after_sequence"},
    "submit_report": {"delegation_ref"},
    "read_reports": {"report_refs"},
    "set_governance_mode": {"task_ref", "mode"},
    "record_initiative": {"task_ref", "goal"},
    "inspect_governance": {"task_ref"},
    "submit_governance_closure": {"task_ref", "subject_type", "subject_ref", "verdict", "evidence"},
    "record_user_decision": {
        "task_ref", "subject_type", "subject_ref", "decision_type", "prompt_en", "response_original", "response_en",
        "user_language",
    },
}

MODELS = ("gpt-5.6-luna", "gpt-5.6-terra", "gpt-5.6-sol")
EFFORTS = ("low", "medium", "high", "xhigh", "max")
EXPECTED_PROFILE_NAMES = (
    "accessibility_auditor", "accessibility_fixer", "architect", "backend_dev", "build_verification",
    "code_reviewer", "data_engineer", "database_architect", "debugger", "devops_engineer", "explorer",
    "frontend_dev", "fullstack_dev", "general", "mobile_dev", "performance_engineer", "planner",
    "qa_engineer", "refactorer", "security_auditor", "technical_writer", "ux_designer",
)
KNOWLEDGE_CONTRACT_INSTRUCTIONS = """Knowledge contract:
Documents to consume first: docs/project/index.md and docs/features/index.md; use only task-relevant linked pages.
Applicable requirements: Preserve the V12 public MCP contract.
Verification contract: Run the focused release gate from the project root and report its exit status.
Ownership constraints: Change only the assigned bounded surface; do not perform external actions.
Known documentation state: none known.
Further documentation discovery: not authorized.

Return a compact durable report without lifecycle gates."""


def _markdown_timeline_index(path: Path) -> tuple[int | None, list[dict[str, object]]]:
    """Read server-derived timeline fields without treating Markdown as JSON."""
    text = path.read_text(encoding="utf-8")
    latest_match = re.search(r"\*\*latest_sequence:\*\*\s+(\d+)", text)
    pages: list[dict[str, object]] = []
    for line in text.splitlines():
        match = re.search(r"\*\*path:\*\*\s+(pages/(\d+))-(\d+)\.md", line)
        if match:
            pages.append({
                "path": f"{match.group(1)}-{match.group(3)}.md",
                "first_sequence": int(match.group(2)),
                "last_sequence": int(match.group(3)),
            })
            continue
        match = re.search(r"\*\*events:\*\*\s+(\d+)", line)
        if match and pages:
            pages[-1]["events"] = int(match.group(1))
    return (None if latest_match is None else int(latest_match.group(1))), pages


def _markdown_timeline_sequences(path: Path) -> list[int]:
    return [
        int(value)
        for value in re.findall(r"\*\*sequence:\*\*\s+(\d+)", path.read_text(encoding="utf-8"))
    ]


def _markdown_timeline_events(path: Path) -> list[tuple[int, str, str]]:
    """Extract the three canonical event labels from an inert Markdown page."""
    text = path.read_text(encoding="utf-8")
    sequences = re.findall(r"\*\*sequence:\*\*\s+(\d+)", text)
    event_types = re.findall(r"\*\*event_type:\*\*\s+([^\n]+)", text)
    entity_ids = re.findall(r"\*\*entity_id:\*\*\s+([^\n]+)", text)
    unescape = lambda value: value.replace("\\-", "-").replace("\\_", "_").replace("\\.", ".")
    return [(int(sequence), unescape(event_type), unescape(entity_id)) for sequence, event_type, entity_id in zip(sequences, event_types, entity_ids)]


def require(condition: object, label: str) -> None:
    if not condition:
        raise AssertionError(label)


def _content_text(result: Mapping[str, Any]) -> str:
    content = result.get("content")
    if not isinstance(content, list):
        return ""
    return "\n".join(
        str(item.get("text") or "")
        for item in content
        if isinstance(item, Mapping)
    )


_TASK_ID_RE = re.compile(r"^task-[0-9a-f]{64}-([0-9a-f]{32})$")
_RECORD_ID_RE = re.compile(r"^(delegation|report|initiative|decision)-[0-9a-f]{64}-([0-9a-f]{32})$")
_RECORD_PREFIX = {"delegation": "d", "report": "r", "initiative": "i", "decision": "u"}


def _task_ref(value: object) -> object:
    if not isinstance(value, str):
        return value
    match = _TASK_ID_RE.fullmatch(value)
    return value if match is None else f"t_{match.group(1)[-12:]}"


def _record_ref(value: object, kind: str) -> object:
    if not isinstance(value, str):
        return value
    match = _RECORD_ID_RE.fullmatch(value)
    if match is None:
        return value
    require(match.group(1) == kind, f"test fixture {kind} ID has its declared record type")
    return f"{_RECORD_PREFIX[kind]}_{match.group(2)[-12:]}"


def _ref_list(value: object, kind: str) -> object:
    return value if not isinstance(value, list) else [_record_ref(item, kind) for item in value]


def _public_arguments(name: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    """Translate fixture-only durable IDs to the actual compact MCP contract.

    SQLite assertions intentionally retain canonical IDs.  Every request sent
    across the public JSON-RPC boundary must instead contain only the compact
    typed refs emitted by ``structuredContent.handles``.
    """
    result = dict(arguments)
    task_id = result.pop("task_id", None)
    if task_id is not None and name not in {"create_task", "read_delegation", "submit_report", "read_reports"}:
        result.setdefault("task_ref", _task_ref(task_id))
    rename = {
        "delegation_id": ("delegation_ref", "delegation"),
        "report_id": ("report_ref", "report"),
        "supersedes_report_id": ("supersedes_report_ref", "report"),
        "initiative_id": ("initiative_ref", "initiative"),
        "parent_initiative_id": ("parent_initiative_ref", "initiative"),
        "parent_delegation_id": ("parent_delegation_ref", "delegation"),
        "supersedes_decision_id": ("supersedes_decision_ref", "decision"),
        "consumer_delegation_id": ("consumer_delegation_ref", "delegation"),
    }
    for old, (new, kind) in rename.items():
        if old in result:
            result.setdefault(new, _record_ref(result.pop(old), kind))
    list_rename = {
        "report_ids": ("report_refs", "report"),
        "input_report_ids": ("input_report_refs", "report"),
        "input_decision_ids": ("input_decision_refs", "decision"),
        "dependencies": ("dependency_refs", "initiative"),
        "linked_delegation_ids": ("linked_delegation_refs", "delegation"),
        "linked_report_ids": ("linked_report_refs", "report"),
        "linked_decision_ids": ("linked_decision_refs", "decision"),
    }
    for old, (new, kind) in list_rename.items():
        if old in result:
            result.setdefault(new, _ref_list(result.pop(old), kind))
    if "linked_task_ids" in result:
        result.setdefault("linked_task_refs", [_task_ref(item) for item in result.pop("linked_task_ids")])
    if "approval_decision_id" in result:
        result.setdefault("approval_decision_ref", _record_ref(result.pop("approval_decision_id"), "decision"))
    if "subject_id" in result:
        subject = result.pop("subject_id")
        result.setdefault(
            "subject_ref",
            _task_ref(subject) if result.get("subject_type") == "task" else _record_ref(subject, "initiative" if result.get("subject_type") == "initiative" else "report" if result.get("subject_type") in {"plan", "report"} else "delegation"),
        )
    if name == "read_delegation":
        result.setdefault("after_sequence", 0)
    return result


class McpServer:
    """Small JSON-RPC client for one source-candidate V12 MCP process."""

    def __init__(self, *, entrypoint: Path, cwd: Path, env: Mapping[str, str], suppress_bytecode: bool = True) -> None:
        command = [sys.executable, *(["-B"] if suppress_bytecode else []), str(entrypoint)]
        self._process = subprocess.Popen(
            command,
            cwd=cwd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            env=dict(env),
        )
        self._selector = selectors.DefaultSelector()
        require(self._process.stdout is not None, "MCP stdout is available")
        self._selector.register(self._process.stdout, selectors.EVENT_READ)
        self._counter = 0
        self._output_schemas: dict[str, Mapping[str, Any]] = {}
        initialized = self.rpc(
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "cortex-v12-release-gate", "version": "1"},
            },
        )
        require(isinstance(initialized.get("result"), Mapping), "MCP initialize succeeds")
        self.notify("notifications/initialized", {})

    def close(self) -> None:
        try:
            if self._process.stdin is not None:
                self._process.stdin.close()
        finally:
            if self._process.poll() is None:
                self._process.terminate()
            try:
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait(timeout=5)
            self._selector.close()

    def rpc(self, method: str, params: Mapping[str, Any]) -> dict[str, Any]:
        self._counter += 1
        request_id = self._counter
        require(self._process.stdin is not None, "MCP stdin is available")
        self._process.stdin.write(
            json.dumps(
                {"jsonrpc": "2.0", "id": request_id, "method": method, "params": dict(params)},
                ensure_ascii=False,
            )
            + "\n"
        )
        self._process.stdin.flush()
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            ready = self._selector.select(max(0.05, deadline - time.monotonic()))
            if not ready:
                continue
            require(self._process.stdout is not None, "MCP stdout remains available")
            line = self._process.stdout.readline()
            if not line:
                stderr = self._process.stderr.read() if self._process.stderr is not None else ""
                raise AssertionError("MCP process stopped before replying: " + stderr[-1000:])
            payload = json.loads(line)
            if payload.get("id") == request_id:
                require(isinstance(payload, dict), "MCP response is an object")
                return payload
        raise AssertionError(f"MCP request timed out: {method}")

    def notify(self, method: str, params: Mapping[str, Any]) -> None:
        require(self._process.stdin is not None, "MCP stdin is available for notification")
        self._process.stdin.write(
            json.dumps({"jsonrpc": "2.0", "method": method, "params": dict(params)}, ensure_ascii=False) + "\n"
        )
        self._process.stdin.flush()

    def raw_jsonl(self, line: str, *, expected_id: object = None) -> dict[str, Any]:
        """Write one deliberately raw JSONL physical frame and await its reply."""
        require("\n" not in line and "\r" not in line, "raw JSONL helper receives one physical line")
        require(self._process.stdin is not None, "MCP stdin is available for raw JSONL")
        self._process.stdin.write(line + "\n")
        self._process.stdin.flush()
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            ready = self._selector.select(max(0.05, deadline - time.monotonic()))
            if not ready:
                continue
            require(self._process.stdout is not None, "MCP stdout remains available")
            response = self._process.stdout.readline()
            if not response:
                stderr = self._process.stderr.read() if self._process.stderr is not None else ""
                raise AssertionError("MCP process stopped after raw JSONL frame: " + stderr[-1000:])
            payload = json.loads(response)
            if payload.get("id") == expected_id:
                require(isinstance(payload, dict), "raw JSONL reply is an object")
                return payload
        raise AssertionError("MCP raw JSONL request timed out")

    @staticmethod
    def _structured(response: Mapping[str, Any]) -> tuple[dict[str, Any], Mapping[str, Any]]:
        result = response.get("result")
        require(isinstance(result, Mapping), "tool reply uses an MCP result envelope")
        structured = result.get("structuredContent")
        require(isinstance(structured, dict), f"tool reply includes structured content: {_content_text(result)}")
        return structured, result

    def tool(self, name: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
        public_arguments = _public_arguments(name, arguments)
        response = self.rpc("tools/call", {"name": name, "arguments": public_arguments})
        raw_result = response.get("result")
        require(isinstance(raw_result, Mapping) and raw_result.get("isError") is not True, f"{name} succeeds with {public_arguments!r}: {_content_text(raw_result) if isinstance(raw_result, Mapping) else response!r}")
        structured, result = self._structured(response)
        require(structured.get("ok") is not False, f"{name} returns a successful protocol receipt")
        text = _content_text(result)
        require(bool(text), f"{name} success includes compact handle TextContent")
        try:
            text_value = json.loads(text.split("\n", 1)[0])
        except json.JSONDecodeError as error:
            raise AssertionError(f"{name} success TextContent starts with handles JSON: {text}") from error
        require(text_value == {"handles": structured.get("handles")}, f"{name} compact TextContent mirrors only callable handles")
        require("Copy only structuredContent.handles compact typed refs" in text, f"{name} success TextContent states the compact handle rule")
        schema = self._output_schemas.get(name)
        if schema is not None:
            _assert_json_schema_conforms(schema, structured, path=f"{name}.structuredContent")
        return structured

    def tool_error(self, name: str, arguments: Mapping[str, Any]) -> tuple[dict[str, Any], str]:
        response = self.rpc("tools/call", {"name": name, "arguments": _public_arguments(name, arguments)})
        result = response.get("result")
        require(isinstance(result, Mapping), f"{name} correctable failure uses an MCP tool result")
        require(result.get("isError") is True, f"{name} must reject the invalid call")
        require("structuredContent" not in result, f"{name} correctable failure never advertises a success output payload")
        text = _content_text(result)
        match = re.fullmatch(
            r"Cortex tool error \[([a-z0-9_]+)\]: .+ Action: .+ Retryable (?:now: yes\.|unchanged: no; correct the request first\.)",
            text,
        )
        require(match is not None, f"{name} correctable failure has a safe code, action, and retry guidance: {text}")
        return {"code": match.group(1), "text": text}, text

    def tool_rpc_error(self, name: str, arguments: Mapping[str, Any], *, cortex_code: str) -> Mapping[str, Any]:
        response = self.rpc("tools/call", {"name": name, "arguments": _public_arguments(name, arguments)})
        require("result" not in response, f"{name} server-state failure is not a correctable tool result")
        error = response.get("error")
        require(
            isinstance(error, Mapping)
            and error.get("code") == -32603
            and error.get("message") == "Cortex server state is unavailable",
            f"{name} server-state failure uses sanitized JSON-RPC -32603",
        )
        data = error.get("data")
        require(isinstance(data, Mapping) and data.get("cortex_code") == cortex_code, f"{name} preserves only its bounded Cortex server-state code")
        return error


def _schema_type_matches(value: object, expected: object) -> bool:
    choices = expected if isinstance(expected, list) else [expected]
    for choice in choices:
        if choice == "object" and isinstance(value, Mapping):
            return True
        if choice == "array" and isinstance(value, list):
            return True
        if choice == "string" and isinstance(value, str):
            return True
        if choice == "integer" and isinstance(value, int) and not isinstance(value, bool):
            return True
        if choice == "number" and isinstance(value, (int, float)) and not isinstance(value, bool):
            return True
        if choice == "boolean" and isinstance(value, bool):
            return True
        if choice == "null" and value is None:
            return True
    return False


def _assert_json_schema_conforms(schema: Mapping[str, Any], value: object, *, path: str) -> None:
    """Independently enforce the advertised JSON-Schema subset used by MCP outputs."""
    expected_type = schema.get("type")
    if expected_type is not None:
        require(_schema_type_matches(value, expected_type), f"{path} conforms to advertised output type")
    if "const" in schema:
        require(value == schema["const"], f"{path} matches advertised output constant")
    enum = schema.get("enum")
    if isinstance(enum, list):
        require(value in enum, f"{path} belongs to advertised output enum")
    for keyword, require_all in (("allOf", True), ("anyOf", False), ("oneOf", False)):
        branches = schema.get(keyword)
        if isinstance(branches, list):
            matches = 0
            for branch in branches:
                if not isinstance(branch, Mapping):
                    continue
                try:
                    _assert_json_schema_conforms(branch, value, path=path)
                except AssertionError:
                    continue
                matches += 1
            if require_all:
                require(matches == len(branches), f"{path} satisfies every advertised output branch")
            elif keyword == "oneOf":
                require(matches == 1, f"{path} satisfies exactly one advertised output branch")
            else:
                require(matches >= 1, f"{path} satisfies one advertised output branch")
    if isinstance(value, Mapping):
        properties = schema.get("properties")
        property_map = properties if isinstance(properties, Mapping) else {}
        required = schema.get("required")
        for key in required if isinstance(required, list) else []:
            require(key in value, f"{path} includes advertised required output field {key}")
        if schema.get("additionalProperties") is False:
            extras = set(value) - set(property_map)
            require(not extras, f"{path} omits unadvertised output fields: {sorted(extras)}")
        for key, item in value.items():
            child = property_map.get(key)
            if isinstance(child, Mapping):
                _assert_json_schema_conforms(child, item, path=f"{path}.{key}")
    if isinstance(value, list):
        items = schema.get("items")
        if isinstance(items, Mapping):
            for index, item in enumerate(value):
                _assert_json_schema_conforms(items, item, path=f"{path}[{index}]")


def _task_id(receipt: Mapping[str, Any]) -> str:
    task = receipt.get("task")
    require(isinstance(task, Mapping), "task receipt contains task")
    value = task.get("task_id")
    require(isinstance(value, str) and value, "task receipt contains stable task_id")
    return value


def _delegation_id(receipt: Mapping[str, Any]) -> str:
    delegation = receipt.get("delegation")
    require(isinstance(delegation, Mapping), "delegation receipt contains delegation")
    value = delegation.get("delegation_id")
    require(isinstance(value, str) and value, "delegation receipt contains stable delegation_id")
    return value


def _report_id(receipt: Mapping[str, Any]) -> str:
    report = receipt.get("report")
    require(isinstance(report, Mapping), "report receipt contains report")
    value = report.get("report_id")
    require(isinstance(value, str) and value, "report receipt contains stable report_id")
    return value


def _initiative_id(receipt: Mapping[str, Any]) -> str:
    initiative = receipt.get("initiative")
    require(isinstance(initiative, Mapping), "initiative receipt contains initiative")
    value = initiative.get("initiative_id")
    require(isinstance(value, str) and value, "initiative receipt contains stable initiative_id")
    return value


def _decision_id(receipt: Mapping[str, Any]) -> str:
    decision = receipt.get("decision")
    require(isinstance(decision, Mapping), "decision receipt contains decision")
    value = decision.get("decision_id")
    require(isinstance(value, str) and value, "decision receipt contains stable decision_id")
    return value


def _approval_binding(receipt: Mapping[str, Any]) -> dict[str, Any]:
    """Copy one exact ready approval-view binding into a plan decision call."""
    view = receipt.get("approval_view")
    require(isinstance(view, Mapping) and view.get("status") == "ready", "a full finalized plan read returns a ready approval view")
    handle = view.get("approval_handle")
    digest = view.get("content_digest")
    sequence = view.get("source_sequence")
    require(
        isinstance(handle, str) and handle
        and isinstance(digest, str) and re.fullmatch(r"sha256:[0-9a-f]{64}", digest) is not None
        and isinstance(sequence, int) and sequence > 0,
        "a ready approval view carries one opaque handle, exact view digest, and source sequence",
    )
    return {
        "approval_handle": handle,
        "approval_view_content_digest": digest,
        "approval_view_source_sequence": sequence,
    }


def _error_code(receipt: Mapping[str, Any]) -> str:
    value = receipt.get("code") or receipt.get("error")
    if isinstance(value, Mapping):
        value = value.get("code")
    if not value:
        value = receipt.get("error_code")
    return str(value or "")


def _require_idempotency_conflict(server: McpServer, name: str, arguments: Mapping[str, Any]) -> None:
    receipt, _ = server.tool_error(name, arguments)
    require(_error_code(receipt) == "idempotency_conflict", f"{name} idempotency conflict is nonmutating")


def _list_tools(server: McpServer) -> dict[str, Mapping[str, Any]]:
    response = server.rpc("tools/list", {})
    result = response.get("result")
    require(isinstance(result, Mapping), "tools/list returns MCP result")
    raw_tools = result.get("tools")
    require(isinstance(raw_tools, list), "tools/list returns tool list")
    by_name = {
        item.get("name"): item
        for item in raw_tools
        if isinstance(item, Mapping) and isinstance(item.get("name"), str)
    }
    require(tuple(by_name) == EXPECTED_TOOLS, "tools/list order is the canonical V12 catalog")
    require(len(raw_tools) == len(EXPECTED_TOOLS), "tools/list contains exactly eleven V12 tools")
    output_schemas = {
        name: item.get("outputSchema")
        for name, item in by_name.items()
        if isinstance(item.get("outputSchema"), Mapping)
    }
    require(set(output_schemas) == set(EXPECTED_TOOLS), "every public tool advertises an output schema")
    server._output_schemas = {name: schema for name, schema in output_schemas.items() if isinstance(schema, Mapping)}
    return by_name


def _assert_tool_schemas(tools: Mapping[str, Mapping[str, Any]]) -> None:
    for name in EXPECTED_TOOLS:
        schema = tools[name].get("inputSchema")
        require(isinstance(schema, Mapping), f"{name} publishes an input schema")
        require(schema.get("type") == "object", f"{name} input schema is an object")
        require(schema.get("additionalProperties") is False, f"{name} input schema is closed")
        properties = schema.get("properties")
        require(isinstance(properties, Mapping), f"{name} input properties are present")
        require(set(properties) == EXPECTED_INPUT_FIELDS[name], f"{name} has the exact V12 fields")
        require(set(schema.get("required") or ()) == EXPECTED_REQUIRED_FIELDS[name], f"{name} has the exact V12 required fields")
        if name == "create_task":
            require("project_root" in properties, "task creation is the sole public project-root entry point")
        elif name in {"read_delegation", "submit_report", "read_reports"}:
            require("project_root" not in properties and "task_id" not in properties and "task_ref" not in properties, f"{name} is entity-derived and accepts no task anchor")
        else:
            require("project_root" not in properties and "task_id" not in properties, f"{name} rejects caller-supplied roots and durable task IDs")
            require("task_ref" in properties and "task_ref" in set(schema.get("required") or ()), f"{name} is anchored by a required compact task ref")
        output = tools[name].get("outputSchema")
        require(isinstance(output, Mapping), f"{name} publishes an output schema")
        require(output.get("type") == "object", f"{name} output schema is an object")
        require(output.get("additionalProperties") is False, f"{name} output schema is closed")
        output_properties = output.get("properties")
        output_required = output.get("required")
        require(
            isinstance(output_properties, Mapping)
            and isinstance(output_required, list)
            and set(output_required).issubset(set(output_properties)),
            f"{name} output schema exposes each required success receipt field",
        )

    task_locator = tools["inspect_task"]["inputSchema"]["properties"]["task_ref"]
    require(isinstance(task_locator, Mapping), "task refs are described by the public schema")
    require(task_locator.get("type") == "string" and isinstance(task_locator.get("pattern"), str), "task refs are opaque compact locators")

    for tool_name, field_name in (
        ("create_task", "context"),
        ("submit_report", "content"),
        ("record_initiative", "notes"),
        ("submit_governance_closure", "evidence"),
        ("submit_governance_closure", "completion_notes"),
    ):
        field_schema = tools[tool_name]["inputSchema"]["properties"][field_name]
        require(
            isinstance(field_schema, Mapping)
            and isinstance(field_schema.get("maxBytes"), int)
            and field_schema["maxBytes"] > 0,
            f"{tool_name}.{field_name} advertises a finite encoded-JSON bound",
        )
    for tool_name, field_name in (
        ("read_reports", "report_refs"),
        ("read_reports", "sections"),
        ("record_initiative", "dependency_refs"),
        ("record_initiative", "linked_task_refs"),
        ("record_initiative", "linked_report_refs"),
    ):
        field_schema = tools[tool_name]["inputSchema"]["properties"][field_name]
        require(isinstance(field_schema, Mapping) and field_schema.get("uniqueItems") is True, f"{tool_name}.{field_name} rejects duplicate identifiers")
    for field_name in ("input_report_refs", "input_decision_refs"):
        field_schema = tools["create_delegation"]["inputSchema"]["properties"][field_name]
        require(isinstance(field_schema, Mapping) and field_schema.get("uniqueItems") is False, f"create_delegation.{field_name} accepts duplicates for first-seen canonicalization")

    delegation_scope = tools["create_delegation"]["inputSchema"]["properties"]["scope"]
    require(isinstance(delegation_scope, Mapping), "delegation scope schema is published")
    require(delegation_scope.get("type") == "string", "delegation scope is textual rather than arbitrary JSON")
    require(delegation_scope.get("minLength") == 1, "delegation scope is non-empty")
    maximum_scope_length = delegation_scope.get("maxLength")
    require(isinstance(maximum_scope_length, int) and maximum_scope_length >= 1, "delegation scope has a finite maximum length")

    delegation_profile = tools["create_delegation"]["inputSchema"]["properties"]["profile_name"]
    require(
        isinstance(delegation_profile, Mapping)
        and tuple(delegation_profile.get("enum") or ()) == EXPECTED_PROFILE_NAMES,
        "create_delegation publishes the exact packaged profile enum independently from its human role label",
    )

    closure_subject_ref = tools["submit_governance_closure"]["inputSchema"]["properties"]["subject_ref"]
    require(isinstance(closure_subject_ref, Mapping), "closure subject ref schema is published")
    require(closure_subject_ref.get("type") == "string" and closure_subject_ref.get("minLength") == 1, "closure subject ref is a required non-empty string")

    reports = tools["submit_report"]["inputSchema"]["properties"]
    require(
        isinstance(reports["mode"], Mapping) and set(reports["mode"].get("enum") or ()) == {"single", "begin", "append", "finalize", "abort"},
        "report upload exposes the five bounded state-machine modes",
    )
    require(
        isinstance(reports["report_type"], Mapping) and "plan" in set(reports["report_type"].get("enum") or ()),
        "reports expose plan evidence without a twelfth tool",
    )
    require(
        isinstance(reports["section"], Mapping)
        and reports["section"].get("type") == "string"
        and reports["section"].get("minLength") == 1
        and isinstance(reports["section"].get("maxLength"), int),
        "chunk sections are bounded non-empty routing labels",
    )
    read_reports = tools["read_reports"]["inputSchema"]["properties"]
    require(
        isinstance(read_reports["max_bytes"], Mapping)
        and read_reports["max_bytes"].get("minimum") == 0
        and isinstance(read_reports["max_bytes"].get("maximum"), int)
        and read_reports["max_bytes"]["maximum"] <= 65_536,
        "bounded report reads advertise a metadata-only zero budget and finite response budget",
    )
    decision = tools["record_user_decision"]["inputSchema"]["properties"]
    require(
        isinstance(decision["subject_digest"], Mapping)
        and isinstance(decision["subject_digest"].get("pattern"), str)
        and isinstance(decision["response_original"], Mapping)
        and decision["response_original"].get("minLength") == 0,
        "user decisions bind immutable revisions while preserving arbitrary-Unicode original responses",
    )
    require(
        tools["submit_report"]["inputSchema"].get("additionalProperties") is False,
        "report modes remain closed at the public schema boundary",
    )
    closure_schema = tools["submit_governance_closure"]["inputSchema"]
    require(closure_schema.get("additionalProperties") is False, "closure subjects remain closed at the public schema boundary")


def _runtime_environment(home: Path) -> dict[str, str]:
    env = os.environ.copy()
    env["HOME"] = str(home)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env.pop("CORTEX_HOST_STATE_DIR", None)
    env.pop("CODEX_HOME", None)
    return env


def _seed_known_pre_human_views_v12_shard(
    *,
    home: Path,
    project: Path,
    report_content: Mapping[str, Any] | None = None,
    report_content_json: str | None = None,
) -> dict[str, Any]:
    """Create the exact released initial V12 layout for migration coverage.

    In particular, legacy task rows deliberately have no ``project_root``
    column.  The real host shard had this shape; adding that column to a test
    fixture would hide the compatibility failure this regression protects.
    """
    project_root = str(project.resolve())
    project_hash = hashlib.sha256(project_root.encode("utf-8")).hexdigest()
    task_id = f"task-{project_hash}-" + ("c" * 32)
    delegation_id = f"delegation-{project_hash}-" + ("d" * 32)
    report_id = f"report-{project_hash}-" + ("e" * 32)
    database = home / ".codex" / "cortex" / "v12" / "projects" / f"p-{project_hash}" / "cortex.db"
    database.parent.mkdir(parents=True)
    timestamp = "2026-08-27T00:00:00+00:00"
    content = dict(report_content or {"summary": "Pre-human-view V12 evidence remains durable."})
    rendered_content = json.dumps(content, ensure_ascii=False, separators=(",", ":")) if report_content_json is None else report_content_json
    with sqlite3.connect(database) as connection:
        connection.executescript(
            """
            CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY,name TEXT NOT NULL,applied_at TEXT NOT NULL);
            CREATE TABLE v12_metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);
            CREATE TABLE timeline(sequence INTEGER PRIMARY KEY AUTOINCREMENT,occurred_at TEXT NOT NULL,event_type TEXT NOT NULL,entity_type TEXT NOT NULL,entity_id TEXT NOT NULL,task_id TEXT,delegation_id TEXT,report_id TEXT,initiative_id TEXT,assessment_id TEXT,closure_id TEXT,payload_json TEXT NOT NULL);
            CREATE TABLE tasks(task_id TEXT PRIMARY KEY,project_hash TEXT NOT NULL,objective TEXT NOT NULL,context_json TEXT NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,created_sequence INTEGER NOT NULL,updated_sequence INTEGER NOT NULL);
            CREATE TABLE delegations(delegation_id TEXT PRIMARY KEY,project_hash TEXT NOT NULL,task_id TEXT NOT NULL REFERENCES tasks(task_id),parent_delegation_id TEXT REFERENCES delegations(delegation_id),objective TEXT NOT NULL,role TEXT NOT NULL,scope TEXT NOT NULL,instructions TEXT NOT NULL,input_report_ids_json TEXT NOT NULL,model TEXT NOT NULL,reasoning_effort TEXT NOT NULL,created_at TEXT NOT NULL,created_sequence INTEGER NOT NULL);
            CREATE TABLE reports(report_id TEXT PRIMARY KEY,project_hash TEXT NOT NULL,task_id TEXT NOT NULL REFERENCES tasks(task_id),delegation_id TEXT NOT NULL REFERENCES delegations(delegation_id),report_type TEXT NOT NULL,status TEXT NOT NULL,content_json TEXT NOT NULL,created_at TEXT NOT NULL,created_sequence INTEGER NOT NULL);
            CREATE TABLE idempotency(operation TEXT NOT NULL,idempotency_key TEXT NOT NULL,payload_digest TEXT NOT NULL,result_json TEXT NOT NULL,created_at TEXT NOT NULL,PRIMARY KEY(operation,idempotency_key));
            CREATE TABLE governance_assessments(assessment_id TEXT PRIMARY KEY,project_hash TEXT NOT NULL,task_id TEXT NOT NULL REFERENCES tasks(task_id),initiative_id TEXT,mode TEXT NOT NULL,source TEXT NOT NULL,rationale TEXT,risk_factors_json TEXT NOT NULL,created_at TEXT NOT NULL,created_sequence INTEGER NOT NULL);
            CREATE TABLE initiatives(initiative_id TEXT PRIMARY KEY,project_hash TEXT NOT NULL,goal TEXT NOT NULL,risk TEXT,status TEXT NOT NULL,notes_json TEXT NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,latest_revision INTEGER NOT NULL,created_sequence INTEGER NOT NULL,updated_sequence INTEGER NOT NULL);
            CREATE TABLE initiative_revisions(revision_id INTEGER PRIMARY KEY AUTOINCREMENT,initiative_id TEXT NOT NULL REFERENCES initiatives(initiative_id),revision_number INTEGER NOT NULL,project_hash TEXT NOT NULL,occurred_at TEXT NOT NULL,sequence INTEGER NOT NULL,payload_json TEXT NOT NULL,UNIQUE(initiative_id,revision_number));
            CREATE TABLE initiative_links(link_id INTEGER PRIMARY KEY AUTOINCREMENT,initiative_id TEXT NOT NULL REFERENCES initiatives(initiative_id),project_hash TEXT NOT NULL,relationship TEXT NOT NULL,target_id TEXT NOT NULL,is_resolved INTEGER NOT NULL,warnings_json TEXT NOT NULL,created_at TEXT NOT NULL,UNIQUE(initiative_id,relationship,target_id));
            CREATE TABLE governance_closures(closure_id TEXT PRIMARY KEY,project_hash TEXT NOT NULL,subject_type TEXT NOT NULL,subject_id TEXT NOT NULL,verdict TEXT NOT NULL,evidence_json TEXT NOT NULL,unresolved_risks_json TEXT NOT NULL,follow_ups_json TEXT NOT NULL,initiative_status TEXT,completion_notes_json TEXT,created_at TEXT NOT NULL,created_sequence INTEGER NOT NULL);
            CREATE INDEX timeline_task_sequence ON timeline(task_id,sequence);
            CREATE INDEX timeline_delegation_sequence ON timeline(delegation_id,sequence);
            CREATE INDEX timeline_initiative_sequence ON timeline(initiative_id,sequence);
            CREATE INDEX reports_task_created ON reports(task_id,created_sequence);
            CREATE INDEX reports_delegation_created ON reports(delegation_id,created_sequence);
            CREATE INDEX assessments_task_created ON governance_assessments(task_id,created_sequence);
            CREATE INDEX initiative_links_source ON initiative_links(initiative_id,relationship);
            """
        )
        connection.execute(f"PRAGMA application_id = {int('43563132', 16)}")
        connection.execute("PRAGMA user_version = 1")
        connection.execute("INSERT INTO schema_migrations(version,name,applied_at) VALUES (1, 'v12-initial', ?)", (timestamp,))
        connection.executemany(
            "INSERT INTO v12_metadata(key,value) VALUES (?, ?)",
            (("project_hash", project_hash), ("project_root_digest", hashlib.sha256(project_root.encode("utf-8")).hexdigest())),
        )
        connection.execute(
            "INSERT INTO tasks(task_id,project_hash,objective,context_json,created_at,updated_at,created_sequence,updated_sequence) VALUES (?, ?, ?, ?, ?, ?, 1, 1)",
            (task_id, project_hash, "Keep legacy V12 evidence durable.", "null", timestamp, timestamp),
        )
        connection.execute(
            "INSERT INTO delegations(delegation_id,project_hash,task_id,parent_delegation_id,objective,role,scope,instructions,input_report_ids_json,model,reasoning_effort,created_at,created_sequence) VALUES (?, ?, ?, NULL, ?, ?, ?, ?, '[]', ?, ?, ?, 2)",
            (delegation_id, project_hash, task_id, "Produce legacy evidence.", "qa_engineer", "bounded legacy scope", "Submit a compact result.", "gpt-5.6-luna", "high", timestamp),
        )
        connection.execute(
            "INSERT INTO reports(report_id,project_hash,task_id,delegation_id,report_type,status,content_json,created_at,created_sequence) VALUES (?, ?, ?, ?, 'result', 'completed', ?, ?, 3)",
            (report_id, project_hash, task_id, delegation_id, rendered_content, timestamp),
        )
        connection.executemany(
            "INSERT INTO timeline(sequence,occurred_at,event_type,entity_type,entity_id,task_id,delegation_id,report_id,initiative_id,assessment_id,closure_id,payload_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL, ?)",
            (
                (1, timestamp, "task_created", "task", task_id, task_id, None, None, json.dumps({"task_id": task_id}, separators=(",", ":"))),
                (2, timestamp, "delegation_created", "delegation", delegation_id, task_id, delegation_id, None, json.dumps({"delegation_id": delegation_id}, separators=(",", ":"))),
                (3, timestamp, "report_submitted", "report", report_id, task_id, delegation_id, report_id, json.dumps({"report_id": report_id}, separators=(",", ":"))),
            ),
        )
        connection.commit()
    for directory in (home / ".codex" / "cortex" / "v12", home / ".codex" / "cortex" / "v12" / "projects", database.parent):
        os.chmod(directory, 0o700)
    os.chmod(database, 0o600)
    return {
        "database": database,
        "project_hash": project_hash,
        "project_root": project_root,
        "task_id": task_id,
        "delegation_id": delegation_id,
        "report_id": report_id,
        "content": content,
    }


def _delegation_payload(
    task_id: str,
    *,
    model: str,
    effort: str,
    key: str,
    profile_name: str = "general",
) -> dict[str, Any]:
    return {
        "task_id": task_id,
        "objective": "Produce durable evidence for the coordinator.",
        "role": "implementation",
        "profile_name": profile_name,
        "scope": "bounded V12 protocol evidence",
        "instructions": KNOWLEDGE_CONTRACT_INSTRUCTIONS,
        "model": model,
        "reasoning_effort": effort,
        "idempotency_key": key,
    }


def _task_payload(project_root: str | Path, *, objective: str, key: str, original: str | None = None, language: str = "en") -> dict[str, Any]:
    """Return the smallest complete V12 task/result contract for a test task."""
    return {
        "project_root": str(project_root),
        "objective": objective,
        "user_request_original": objective if original is None else original,
        "user_language": language,
        "task_contract_version": "cortex/task-contract/v1",
        "requirements": ["Preserve the bounded V12 task contract."],
        "constraints": ["No additional constraints."],
        "acceptance_criteria": ["The task ledger records one complete contract."],
        "verification_plan": ["Inspect the returned task receipt and report any mismatch."],
        "idempotency_key": key,
    }


def test_cortex_v12_plugin_is_publishable_and_nonblocking(tmp_path: Path) -> None:
    source_repository = Path(__file__).resolve().parents[1]
    support_scripts = source_repository / "scripts"
    if str(support_scripts) not in sys.path:
        sys.path.insert(0, str(support_scripts))
    from cortex_release_candidate import build_source_candidate, validate_candidate_tree

    repository = tmp_path / "source-candidate"
    release_manifest = build_source_candidate(source_repository, repository)
    validate_candidate_tree(repository, release_manifest)
    plugin = repository / "plugins" / "cortex"
    scripts = plugin / "scripts"
    entrypoint = scripts / "cortex.py"

    manifest = json.loads((plugin / ".codex-plugin/plugin.json").read_text(encoding="utf-8"))
    require(manifest.get("name") == "cortex", "marketplace manifest name")
    require(str(manifest.get("version") or "").startswith("12.0.0"), "manifest is the V12 major release")
    require(isinstance(json.loads((plugin / ".mcp.json").read_text(encoding="utf-8")), dict), "MCP manifest is valid JSON")
    hook_manifest = plugin / "hooks" / "hooks.json"
    if hook_manifest.exists():
        hooks = json.loads(hook_manifest.read_text(encoding="utf-8"))
        require(not hooks.get("hooks"), "V12 hook manifest has no enabled hooks")
    require(not (scripts / "cortex_hook.py").exists(), "V12 does not ship lifecycle hook code")

    maintenance_module = scripts / "cortex_runtime" / "v12_maintenance.py"
    require(maintenance_module.is_file(), "V12 ships the closed task-anchored maintenance module")
    source_files = [entrypoint, *sorted((scripts / "cortex_runtime").glob("*.py"))]
    for source in source_files:
        source_text = source.read_text(encoding="utf-8")
        ast.parse(source_text, filename=str(source))
        compile(source_text, str(source), "exec", dont_inherit=True)
    runtime_source = "\n".join(source.read_text(encoding="utf-8") for source in source_files)
    for retired in ("reliability_recovery_target", "Luna-to-Terra-to-Sol", "SubagentStop", "read_worker_wave", "wait_agent"):
        require(retired not in runtime_source, f"V12 runtime removes retired control-plane token: {retired}")
    maintenance_source = maintenance_module.read_text(encoding="utf-8")
    require(
        all(option not in maintenance_source for option in ("--project-root", "--database", "--backup-path", "--path")),
        "maintenance CLI never accepts a project root or arbitrary filesystem path",
    )
    require(
        "_target(task_id)" in maintenance_source
        and "task_shard_hash(task_id)" in maintenance_source
        and "projection_files WHERE task_id=?" in maintenance_source
        and "os.walk" not in maintenance_source,
        "maintenance derives every target from a task shard and prunes only registered exact-task projections",
    )

    def maintenance_cli(home: Path, *arguments: str) -> tuple[subprocess.CompletedProcess[str], dict[str, Any]]:
        maintenance_env = _runtime_environment(home)
        maintenance_env["PYTHONDONTWRITEBYTECODE"] = "1"
        completed = subprocess.run(
            [sys.executable, "-B", "-m", "cortex_runtime.v12_maintenance", *arguments],
            cwd=scripts,
            env=maintenance_env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        payload: dict[str, Any] = {}
        if completed.stdout.strip():
            value = json.loads(completed.stdout)
            require(isinstance(value, dict), "maintenance CLI emits one JSON object")
            payload = value
        return completed, payload

    ordinary_entrypoint_home = tmp_path / "ordinary-entrypoint-home"
    ordinary_entrypoint_home.mkdir()
    ordinary_entrypoint_env = os.environ.copy()
    ordinary_entrypoint_env["HOME"] = str(ordinary_entrypoint_home)
    ordinary_entrypoint_env.pop("CORTEX_HOST_STATE_DIR", None)
    ordinary_entrypoint_env.pop("CODEX_HOME", None)
    ordinary_entrypoint_env.pop("PYTHONDONTWRITEBYTECODE", None)
    ordinary_entrypoint_env.pop("PYTHONPYCACHEPREFIX", None)
    ordinary_entrypoint = McpServer(
        entrypoint=entrypoint,
        cwd=repository,
        env=ordinary_entrypoint_env,
        suppress_bytecode=False,
    )
    try:
        _assert_tool_schemas(_list_tools(ordinary_entrypoint))
    finally:
        ordinary_entrypoint.close()
    require(
        not list(plugin.rglob("__pycache__")) and not list(plugin.rglob("*.pyc")),
        "an ordinary entrypoint launch suppresses bytecode before Cortex runtime imports without cleanup",
    )

    lint = subprocess.run(
        [sys.executable, "-B", str(repository / "scripts" / "cortex-prompt-lint.py")],
        cwd=repository,
        capture_output=True,
        text=True,
        timeout=20,
    )
    require(lint.returncode == 0, f"authoritative skill/profile contract lint passes: {(lint.stdout + lint.stderr)[-1000:]}")

    lint_namespace = runpy.run_path(str(repository / "scripts" / "cortex-prompt-lint.py"))
    authority_violations = lint_namespace["coordinator_authority_violations"]
    protocol_violations = lint_namespace["coordinator_protocol_violations"]
    unsafe_boundary_prompts = (
        "The coordinator may use shell commands to find knowledge-routing documents.",
        "Knowledge routing can invoke rg against the repository before delegation.",
        "The routing exception is authorized to use graph search for applicable instructions.",
        "The coordinator should inspect project-local artifacts to prove they are unchanged.",
        "The coordinator can check .codex when the user asks whether it exists.",
    )
    for unsafe_prompt in unsafe_boundary_prompts:
        require(
            authority_violations(unsafe_prompt),
            f"prompt lint rejects coordinator project-tool authority: {unsafe_prompt}",
        )
    for safe_prompt in (
        "The coordinator must never use shell, rg, find, glob, or graph search for routing.",
        "A worker must check project-local artifact absence and unchanged-state.",
    ):
        require(
            not authority_violations(safe_prompt),
            f"prompt lint accepts the worker-only boundary: {safe_prompt}",
        )

    unsafe_protocol_prompts = (
        "The coordinator may construct the next task ID by appending a remembered suffix to the returned ID.",
        "The coordinator should call submit_report for a synthesis worker after the native child stops.",
        "The coordinator may submit a ready closure before the synthesis report is finalized.",
        "For documentation not required, the coordinator should submit a task-subject closure.",
        "The coordinator can call read_mcp_resource to fetch skill://cortex/orchestrator.",
        "The coordinator may create the final initiative with report links only and omit the task link.",
        "The coordinator may call create_task with empty requirements and verification arrays.",
        "The coordinator should dispatch a worker with missing six-part knowledge sections.",
        "The coordinator may assemble an ad-hoc worker prompt instead of copying the returned dispatch payload.",
        "The coordinator may reuse one native worker across multiple durable delegations.",
        "The coordinator may spawn a worker with fork_turns=all and omit reasoning_effort.",
        "The coordinator may omit the model override for Terra.",
        "A native worker may write commentary and final responses in the user's Russian language.",
        "The coordinator may assert documentation_not_required without a worker-owned documentation-impact report.",
        "The coordinator may use a free-form role label as loaded profile proof.",
    )
    for unsafe_prompt in unsafe_protocol_prompts:
        require(
            protocol_violations(unsafe_prompt),
            f"prompt lint rejects a live-failed coordinator protocol mutation: {unsafe_prompt}",
        )
    for safe_prompt in (
        "The coordinator must never parse, reconstruct, or suffix an ID or digest.",
        "The coordinator never calls submit_report; the owning worker submits its report.",
        "Never submit a ready closure before required worker evidence has settled.",
        "For documentation not required, the coordinator must not use a task-subject closure.",
        "The coordinator must never call read_mcp_resource for a skill:// URI.",
        "The coordinator must never create a report-only final initiative or omit its exact task link.",
        "The coordinator must never call create_task with empty requirements or verification arrays.",
        "The coordinator must not dispatch a worker with missing six-part knowledge sections.",
        "The coordinator must never assemble an ad-hoc spawn or rewrite the returned native-dispatch payload.",
        "The coordinator must never reuse one native worker across multiple durable delegations.",
        "The coordinator must never spawn with fork_turns=all or omit reasoning_effort.",
        "The coordinator must never omit the model override for Terra or Sol and must not pass one for Luna.",
        "A native worker must never localize commentary or final responses into the user's Russian language.",
        "The coordinator must never assert documentation_not_required without a worker-owned documentation-impact report.",
        "The coordinator must never use a free-form role label as loaded profile proof.",
    ):
        require(
            not protocol_violations(safe_prompt),
            f"prompt lint accepts an exact coordinator protocol prohibition: {safe_prompt}",
        )

    coordinator_skill = " ".join((plugin / "skills" / "orchestrator" / "SKILL.md").read_text(encoding="utf-8").split())
    control_skill = " ".join((plugin / "skills" / "cortex-control" / "SKILL.md").read_text(encoding="utf-8").split())
    for marker in (
        "The root coordinator is for orchestration only.",
        "Every project-facing task uses at least one native worker.",
        "The coordinator alone compiles one per-delegation knowledge contract",
        "Only `create_task` receives `project_root`.",
        "calls use returned `handles.task_ref`",
        "These entity-derived public calls accept neither `task_ref` nor `task_id`",
        "only a clickable Markdown link in the exact form",
        "After the initiative closure write succeeds, use its returned `next_action`",
        "This distinct task closure is mandatory whenever the task has an initiative",
        "documentation-impact delegation is still an ordinary post-approval delegation",
    ):
        require(marker in coordinator_skill, f"orchestrator preserves current knowledge and worker-only invariant: {marker}")
    for marker in (
        "Only `create_task` accepts the canonical absolute `project_root`.",
            "All later task-anchored creation and governance calls use `task_ref`",
        "orchestrator, which is also the single authority for the delegation knowledge contract.",
        "Workers consume that supplied contract rather than recreating the route.",
        "The orchestrator's project-read exception is a closed direct-read allowlist, not tool or discovery authority.",
        "A user request for such a check must become a delegation rather than coordinator access.",
        "Activated bundled skill bodies are host-supplied context, not MCP resources",
        "opaque immutable return data for every model caller",
        "The coordinator never calls `submit_report`",
        "Never create a report-only final initiative.",
        "Each successful durable delegation returns one native-dispatch payload.",
        "makes exactly one corresponding host spawn",
            "copying `native_dispatch.task_name` and the nested native arguments byte-for-byte.",
        "Every native worker commentary/update, inter-worker message, final response, tool-authored durable string, and report is English",
        "The coordinator selects an exact packaged `profile_name` independently from the bounded human-readable `role`",
        "self-asserted `documentation_not_required` value without linked and cited worker evidence is invalid",
    ):
        require(marker in control_skill, f"control skill preserves current task-root and knowledge-contract authority: {marker}")

    profiles = sorted((plugin / "agents").glob("*.toml"))
    require(len(profiles) == 22, "V12 ships exactly twenty-two advisory role profiles")
    for profile in profiles:
        parsed_profile = tomllib.loads(profile.read_text(encoding="utf-8"))
        profile_instructions = parsed_profile.get("developer_instructions")
        require(isinstance(profile_instructions, str), f"{profile.name} contains parsed developer instructions")
        instructions = " ".join(profile_instructions.split())
        require("supplied knowledge contract" in instructions, f"{profile.name} consumes the coordinator-supplied contract")
        require(
            "English-normalized objective" in instructions
            and (
                "Use English for all work" in instructions
                or "Use English for messages, reports, ledger content, and artifacts." in instructions
            ),
            f"{profile.name} applies the English-only durable-worker content boundary",
        )
        for heading in (
            "## Mission and authority", "## Supplied inputs", "## Workflow",
            "## Quality invariants", "## Evidence report", "## Stop and escalate",
        ):
            require(heading in profile_instructions, f"{profile.name} retains the semantic Markdown profile topology: {heading}")
        require(
            not any(literal in instructions for literal in ("`AGENTS.md`", "docs/project/index.md", "docs/features/index.md")),
            f"{profile.name} does not duplicate orchestrator-owned knowledge paths",
        )

    home = tmp_path / "home"
    home.mkdir()
    project_a = tmp_path / "project-a"
    project_b = tmp_path / "project-b"
    project_a.mkdir()
    project_b.mkdir()
    env = _runtime_environment(home)
    v11_database = home / ".codex" / "cortex" / "projects" / "p-v11-sentinel" / "cortex.db"
    v11_database.parent.mkdir(parents=True)
    with sqlite3.connect(v11_database) as connection:
        connection.execute("CREATE TABLE sentinel(value TEXT NOT NULL)")
        connection.execute("INSERT INTO sentinel(value) VALUES ('v11 remains untouched')")
        connection.commit()
    v11_digest_before = hashlib.sha256(v11_database.read_bytes()).hexdigest()
    # A project may still contain an old local V11 artifact.  V12 must neither
    # reuse it nor create a competing project-local V12 state directory.
    v11_local_root = project_a / ".codex" / "cortex"
    v11_local_database = v11_local_root / "cortex.db"
    v11_local_task = v11_local_root / "tasks" / "task-v11-sentinel.md"
    v11_local_task.parent.mkdir(parents=True)
    v11_local_database.write_bytes(b"project-local V11 sentinel database")
    v11_local_task.write_text("project-local V11 sentinel task\n", encoding="utf-8")
    v11_local_digests = {
        path.relative_to(project_a): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in (v11_local_database, v11_local_task)
    }

    server = McpServer(entrypoint=entrypoint, cwd=project_a, env=env)
    try:
        tools = _list_tools(server)
        _assert_tool_schemas(tools)
        for method, params in (
            ("resources/list", {}),
            ("resources/templates/list", {}),
            ("resources/read", {"uri": "skill://cortex/orchestrator"}),
        ):
            resource_reply = server.rpc(method, params)
            resource_error = resource_reply.get("error")
            require(
                isinstance(resource_error, Mapping)
                and resource_error.get("code") == -32601
                and resource_error.get("message") == "Method not found",
                f"{method} remains unadvertised and unavailable through the fixed tool-only MCP surface",
            )

        oversized_marker = "OVERSIZED-JSONL-FRAME-MUST-NOT-BE-ECHOED-"
        oversized_line = (
            '{"jsonrpc":"2.0","id":987,"method":"ping","params":{"padding":"'
            + oversized_marker
            + ("x" * (256 * 1024))
            + '"}}'
        )
        oversized_reply = server.raw_jsonl(oversized_line)
        oversized_error = oversized_reply.get("error")
        require(
            isinstance(oversized_error, Mapping)
            and oversized_error.get("code") == -32700
            and oversized_error.get("message") == "Parse error"
            and set(oversized_error) == {"code", "message"},
            "oversized physical JSONL frame receives only a sanitized parse error",
        )
        require(oversized_marker not in json.dumps(oversized_reply), "oversized JSONL content is never echoed")
        pong = server.rpc("ping", {})
        require(pong.get("result") == {}, "server survives oversized JSONL frame and answers the next ping")
        _assert_tool_schemas(_list_tools(server))

        task_args = {
            "project_root": str(project_a),
            "objective": "Preserve a durable V12 coordination audit trail.",
            "user_request_original": "Провести проверку архитектуры Cortex V12.",
            "user_language": "ru",
            "task_contract_version": "cortex/task-contract/v1",
            "requirements": ["Preserve the eleven-tool public MCP contract."],
            "constraints": ["Keep all ledger and human-view content host-private."],
            "acceptance_criteria": ["The bounded release gate completes successfully."],
            "verification_plan": ["Run the packaged source-candidate MCP protocol gate."],
            "context": "The report content remains private to the ledger.",
            "idempotency_key": "task-a-create",
        }
        created = server.tool("create_task", task_args)
        task_a = _task_id(created)
        canonical_project_a = str(project_a.resolve())
        task_record = created.get("task")
        require(
            isinstance(task_record, Mapping) and task_record.get("project_root") == canonical_project_a,
            "task creation canonicalizes and durably records its sole project-root input",
        )
        require(
            task_record.get("objective") == task_args["objective"]
            and task_record.get("user_request_original") == task_args["user_request_original"]
            and task_record.get("user_language") == "ru"
            and task_record.get("requirements") == task_args["requirements"]
            and task_record.get("constraints") == task_args["constraints"]
            and task_record.get("acceptance_criteria") == task_args["acceptance_criteria"]
            and task_record.get("verification_plan") == task_args["verification_plan"],
            "task stores English-normalized worker context separately from exact original user wording",
        )
        for contract_field in ("requirements", "constraints", "acceptance_criteria", "verification_plan"):
            contract_values = task_record.get(contract_field)
            require(
                isinstance(contract_values, list)
                and bool(contract_values)
                and all(isinstance(item, str) and bool(item.strip()) for item in contract_values),
                f"live task shape retains non-empty meaningful {contract_field}",
            )
        task_id_match = re.fullmatch(r"task-([0-9a-f]{64})-([0-9a-f]{32})", task_a)
        require(task_id_match is not None, "task ID contains the opaque parseable project shard and record suffix")
        require(
            task_id_match.group(1) == hashlib.sha256(canonical_project_a.encode("utf-8")).hexdigest(),
            "task ID shard matches the canonical task-stored project root",
        )
        replayed_task = server.tool("create_task", task_args)
        require(_task_id(replayed_task) == task_a and replayed_task.get("replayed") is True, "task replay returns original task")
        _require_idempotency_conflict(
            server, "create_task", {**task_args, "objective": "Different payload for the same idempotency key."},
        )

        ledger_database = home / ".codex" / "cortex" / "v12" / "projects" / f"p-{hashlib.sha256(canonical_project_a.encode('utf-8')).hexdigest()}" / "cortex.db"

        busy_arguments = {
            "task_id": task_a,
            "mode": "minimal",
            "source": "model",
            "rationale": "Bounded storage-busy retry evidence.",
            "idempotency_key": "storage-busy-same-key",
        }
        with sqlite3.connect(ledger_database, timeout=0, isolation_level=None) as lock_connection:
            lock_connection.execute("PRAGMA busy_timeout = 0")
            lock_connection.execute("BEGIN EXCLUSIVE")
            storage_busy, storage_busy_text = server.tool_error("set_governance_mode", busy_arguments)
            lock_connection.execute("ROLLBACK")
        require(
            _error_code(storage_busy) == "storage_busy"
            and "Retry after: 100 ms." in storage_busy_text
            and "Mutation: set_governance_mode." in storage_busy_text
            and "Action: Retry this same mutation once with the same idempotency_key" in storage_busy_text
            and "Retryable now: yes." in storage_busy_text,
            "SQLite contention is a correctable, actionable same-idempotency storage_busy tool error",
        )
        require(
            isinstance(server.tool("set_governance_mode", busy_arguments).get("assessment"), Mapping),
            "the identical idempotency-keyed mutation succeeds after transient SQLite contention clears",
        )

        def contract_artifact_counts() -> tuple[int, int, int, int, int]:
            with sqlite3.connect(ledger_database) as connection:
                return tuple(
                    int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
                    for table in ("tasks", "delegations", "timeline", "idempotency", "projection_jobs")
                )

        before_invalid_task_contract = server.tool("inspect_task", {"task_id": task_a})
        task_artifacts_before_rejection = contract_artifact_counts()
        empty_task_contract, _ = server.tool_error(
            "create_task",
            {
                **task_args,
                "requirements": [],
                "constraints": [],
                "acceptance_criteria": [],
                "verification_plan": [],
                "idempotency_key": "task-empty-contract",
            },
        )
        require(
            _error_code(empty_task_contract) == "validation_error",
            "the live empty four-array task contract is rejected at the public schema boundary",
        )
        unknown_language_task, _ = server.tool_error(
            "create_task",
            {**task_args, "user_language": "und", "idempotency_key": "task-unknown-language"},
        )
        require(
            _error_code(unknown_language_task) == "validation_error",
            "a task requires a concrete asserted user language rather than an unknown-language placeholder",
        )
        after_invalid_task_contract = server.tool("inspect_task", {"task_id": task_a})
        require(
            after_invalid_task_contract.get("delegations") == before_invalid_task_contract.get("delegations")
            and after_invalid_task_contract.get("timeline") == before_invalid_task_contract.get("timeline")
            and contract_artifact_counts() == task_artifacts_before_rejection,
            "rejected task contracts leave no task timeline, delegation, projection, or idempotency artifact",
        )

        before_invalid_scope = server.tool("inspect_task", {"task_id": task_a})
        invalid_scope_args = {
            **_delegation_payload(task_a, model="gpt-5.6-luna", effort="xhigh", key="delegation-object-scope"),
            "scope": {"invalid": "scope values are never structured project instructions"},
        }
        invalid_scope, invalid_scope_text = server.tool_error("create_delegation", invalid_scope_args)
        require(_error_code(invalid_scope) == "validation_error", "object delegation scope is rejected by the public schema")
        require(
            "Location: $.scope." in invalid_scope_text
            and "Expected:" in invalid_scope_text,
            "object scope fails at the MCP schema boundary with an actionable field path",
        )
        after_invalid_scope = server.tool("inspect_task", {"task_id": task_a})
        require(
            after_invalid_scope.get("delegations") == before_invalid_scope.get("delegations")
            and after_invalid_scope.get("timeline") == before_invalid_scope.get("timeline"),
            "schema-rejected object scope leaves the task ledger unchanged",
        )

        luna_args = _delegation_payload(
            task_a,
            model="gpt-5.6-luna",
            effort="xhigh",
            key="delegation-luna",
            profile_name="qa_engineer",
        )
        luna_args["role"] = "qa_engineer"
        luna_delegation = server.tool("create_delegation", luna_args)
        delegation_a = _delegation_id(luna_delegation)
        delegation_view = luna_delegation.get("delegation")
        require(isinstance(delegation_view, Mapping), "delegation receipt is durable")
        require(delegation_view.get("scope") == luna_args["scope"], "textual delegation scope round-trips in durable delegation")
        worker_brief = luna_delegation.get("worker_brief")
        require(isinstance(worker_brief, Mapping) and worker_brief.get("scope") == luna_args["scope"], "textual delegation scope round-trips in worker brief")
        require(worker_brief.get("project_root") == canonical_project_a, "worker brief carries the canonical task-stored project root")
        require(worker_brief.get("instructions") == KNOWLEDGE_CONTRACT_INSTRUCTIONS, "worker brief preserves the coordinator's structured knowledge contract")
        require("knowledge_requirements" not in worker_brief, "worker brief does not synthesize a second generic knowledge-routing block")
        worker_brief_json = json.dumps(worker_brief, ensure_ascii=False)
        require(
            '"docs/project/"' not in worker_brief_json and '"docs/features/"' not in worker_brief_json,
            "worker brief contains no runtime-generated generic documentation directory paths",
        )
        for field in (
            "Documents to consume first:", "Applicable requirements:", "Verification contract:",
            "Ownership constraints:", "Known documentation state:", "Further documentation discovery:",
        ):
            instruction_lines = str(worker_brief.get("instructions") or "").splitlines()
            matches = [line for line in instruction_lines if line.startswith(field)]
            require(
                len(matches) == 1 and bool(matches[0].partition(":")[2].strip()),
                f"worker brief retains exactly one non-empty knowledge-contract field: {field}",
            )
        renderer = worker_brief.get("renderer") if isinstance(worker_brief, Mapping) else None
        worker_message = worker_brief.get("worker_message") if isinstance(worker_brief, Mapping) else None
        require(
            isinstance(worker_message, str)
            and "## Trusted operating policy" in worker_message
            and "## Trusted advisory profile" in worker_message
            and "## Untrusted task and delegation data" in worker_message,
            "worker renderer separates immutable policy and advisory profile from untrusted task data",
        )
        normalized_worker_message = " ".join(worker_message.split())
        for exact_reference in (
            f'"task_ref":"{_task_ref(task_a)}"',
            f'"delegation_ref":"{_record_ref(delegation_a, "delegation")}"',
            '"input_report_refs":[]',
            '"profile_name":"qa_engineer"',
            '"model":"gpt-5.6-luna"',
            '"reasoning_effort":"xhigh"',
        ):
            require(
                exact_reference in worker_message,
                f"worker renderer preserves exact native dispatch evidence: {exact_reference}",
            )
        for marker in (
            "Work only in English.",
            "Every commentary/update, message to another worker, final response, tool-authored durable string, and report must be English",
            "regardless of the user's language.",
            "You own report submission for this exact delegation.",
            "Call `submit_report` yourself with the exact `delegation_ref` supplied below",
                "Never alter the delegation ID, submit for another delegation, or ask the coordinator to submit a plan, result, verification, synthesis, or documentation-impact report for you.",
            "If submission is unavailable, return honest sanitized native evidence.",
        ):
            require(marker in normalized_worker_message, f"worker renderer preserves report-call ownership: {marker}")
        require(
            isinstance(renderer, Mapping)
            and renderer.get("version") == "cortex/worker-message/v1"
            and renderer.get("profile_name") == "qa_engineer"
            and renderer.get("profile_state") == "loaded"
            and isinstance(renderer.get("profile_digest"), str)
            and re.fullmatch(r"sha256:[0-9a-f]{64}", str(renderer.get("profile_digest"))) is not None
            and isinstance(renderer.get("common_policy_digest"), str)
            and re.fullmatch(r"sha256:[0-9a-f]{64}", str(renderer.get("common_policy_digest"))) is not None,
            "worker brief carries verifiable profile and common-policy renderer attestations",
        )
        require(delegation_view.get("model") == "gpt-5.6-luna", "coordinator Luna selection is preserved")
        require(delegation_view.get("reasoning_effort") == "xhigh", "coordinator effort selection is preserved")
        native_dispatch = worker_brief.get("native_dispatch")
        require(isinstance(native_dispatch, Mapping), "delegation receipt returns one native dispatch projection")
        native_selection = native_dispatch.get("selection") if isinstance(native_dispatch, Mapping) else None
        native_arguments = native_dispatch.get("native_arguments") if isinstance(native_dispatch, Mapping) else None
        require(
            native_selection == {"model": "gpt-5.6-luna", "reasoning_effort": "xhigh"},
            "native dispatch preserves the exact logical model/effort selection",
        )
        require(
            isinstance(native_arguments, Mapping)
            and native_arguments.get("message") == worker_message
            and native_arguments.get("reasoning_effort") == "xhigh"
            and native_arguments.get("fork_turns") == "none"
            and "model" not in native_arguments
            and isinstance(native_arguments.get("task_name"), str)
            and bool(native_arguments.get("task_name")),
            "Luna native dispatch is host-ready, isolated, effort-explicit, and byte-exact to the rendered brief",
        )
        replayed_delegation = server.tool("create_delegation", luna_args)
        require(_delegation_id(replayed_delegation) == delegation_a and replayed_delegation.get("replayed") is True, "delegation replay is idempotent")
        require(
            replayed_delegation.get("worker_brief", {}).get("native_dispatch") == native_dispatch,
            "idempotent delegation replay preserves the byte-identical native dispatch payload",
        )
        _require_idempotency_conflict(server, "create_delegation", {**luna_args, "scope": "Conflicting delegated scope."})

        first_report_args = {
            "task_id": task_a, "delegation_id": delegation_a, "report_type": "progress", "status": "partial",
            "content": "The first independent worker made bounded progress.", "idempotency_key": "report-a-progress",
        }
        first_report_receipt = server.tool("submit_report", first_report_args)
        report_a = _report_id(first_report_receipt)
        submitted_report = first_report_receipt.get("report")
        require(
            isinstance(submitted_report, Mapping)
            and "content" not in submitted_report
            and first_report_args["content"] not in json.dumps(submitted_report, ensure_ascii=False),
            "submit_report returns only compact report acknowledgement metadata",
        )
        require(_report_id(server.tool("submit_report", first_report_args)) == report_a, "report replay returns original report")
        _require_idempotency_conflict(server, "submit_report", {**first_report_args, "content": "Conflicting report payload."})
        before_oversized_report = server.tool("inspect_task", {"task_id": task_a})
        oversized_report, oversized_report_text = server.tool_error(
            "submit_report",
            {
                "task_id": task_a,
                "delegation_id": delegation_a,
                "report_type": "progress",
                "status": "partial",
                "content": "x" * 70_000,
                "idempotency_key": "oversized-report-content",
            },
        )
        require(_error_code(oversized_report) == "validation_error", "oversized report content is rejected at the MCP schema boundary")
        require(
            "Location: $.content." in oversized_report_text
            and "Expected:" in oversized_report_text,
            "oversized report content reports bounded actionable schema guidance",
        )
        after_oversized_report = server.tool("inspect_task", {"task_id": task_a})
        require(
            after_oversized_report.get("timeline") == before_oversized_report.get("timeline"),
            "schema-rejected oversized report content leaves the task ledger unchanged",
        )
        second_delegation = server.tool(
            "create_delegation",
            {
                **_delegation_payload(task_a, model="gpt-5.6-terra", effort="high", key="delegation-terra", profile_name="planner"),
                "role": "review", "scope": "Use supplied evidence without a mandatory read receipt.", "input_report_ids": [report_a],
            },
        )
        delegation_b = _delegation_id(second_delegation)
        report_b = _report_id(server.tool(
            "submit_report",
            {
                "task_id": task_a, "delegation_id": delegation_b, "report_type": "result", "status": "completed",
                "content": "Independent review completed without a predecessor lifecycle gate.", "idempotency_key": "report-b-result",
            },
        ))

        plan_begin_args = {
            "task_id": task_a, "delegation_id": delegation_b, "mode": "begin", "report_type": "plan",
            "review_policy": "required", "idempotency_key": "plan-begin",
        }
        plan_started = server.tool("submit_report", plan_begin_args)
        plan_report = plan_started.get("report")
        plan_id = _report_id(plan_started)
        require(
            isinstance(plan_report, Mapping)
            and plan_report.get("report_type") == "plan"
            and plan_report.get("review_policy") == "required"
            and plan_report.get("assembly_state") == "assembling"
            and plan_started.get("next_chunk_index") == 0,
            "chunked plan begin mints a stable assembling report without semantic completion",
        )
        require(server.tool("submit_report", plan_begin_args).get("replayed") is True, "chunked report begin is idempotent")
        _require_idempotency_conflict(server, "submit_report", {**plan_begin_args, "review_policy": "informational"})

        plan_chunk_zero_args = {
            "task_id": task_a, "delegation_id": delegation_b, "mode": "append", "report_id": plan_id,
            "chunk_index": 0, "section": "plan.overview",
            "content": {"summary": "Run the eleven-tool V12 release gate.", "owner": "qa"},
            "idempotency_key": "plan-append-0",
        }
        plan_chunk_zero = server.tool("submit_report", plan_chunk_zero_args)
        require(
            plan_chunk_zero.get("accepted_chunk_index") == 0
            and plan_chunk_zero.get("next_chunk_index") == 1
            and isinstance(plan_chunk_zero.get("chunk_digest"), str)
            and isinstance(plan_chunk_zero.get("current_content_digest"), str),
            "chunk append returns resumable ordered acknowledgement metadata without a body echo",
        )
        require(server.tool("submit_report", plan_chunk_zero_args).get("replayed") is True, "chunk append is idempotent")
        out_of_order_chunk, _ = server.tool_error(
            "submit_report",
            {
                **plan_chunk_zero_args,
                "chunk_index": 2,
                "section": "plan.verification",
                "content": {"command": "python3 -m pytest -q"},
                "idempotency_key": "plan-append-out-of-order",
            },
        )
        require(_error_code(out_of_order_chunk) == "report_chunk_out_of_order", "chunk upload rejects a skipped index without mutation")
        plan_chunk_one_args = {
            "task_id": task_a, "delegation_id": delegation_b, "mode": "append", "report_id": plan_id,
            "chunk_index": 1, "section": "plan.verification",
            "content": {"command": "python3 -m pytest -q", "expected": "exit 0"},
            "idempotency_key": "plan-append-1",
        }
        plan_chunk_one = server.tool("submit_report", plan_chunk_one_args)
        plan_digest = plan_chunk_one.get("current_content_digest")
        require(isinstance(plan_digest, str) and re.fullmatch(r"sha256:[0-9a-f]{64}", plan_digest), "chunk append returns the whole immutable manifest digest")

        plan_first_page = server.tool(
            "read_reports",
            {"task_id": task_a, "report_ids": [plan_id], "max_bytes": plan_chunk_zero.get("chunk_bytes")},
        )
        require(
            plan_first_page.get("has_more") is True
            and isinstance(plan_first_page.get("next_cursor"), str)
            and plan_first_page.get("returned_content_bytes") == plan_chunk_zero.get("chunk_bytes"),
            "bounded report reads stop before the next complete JSON chunk and issue a scoped cursor",
        )
        plan_second_page = server.tool(
            "read_reports",
            {"task_id": task_a, "report_ids": [plan_id], "cursor": plan_first_page["next_cursor"], "max_bytes": 65_536},
        )
        second_chunks = plan_second_page.get("reports", [{}])[0].get("chunks") if isinstance(plan_second_page.get("reports"), list) else []
        require(
            isinstance(second_chunks, list) and [item.get("chunk_index") for item in second_chunks if isinstance(item, Mapping)] == [1],
            "report cursor resumes from the next complete chunk without duplication",
        )
        mismatched_cursor, _ = server.tool_error(
            "read_reports",
            {
                "task_id": task_a, "report_ids": [plan_id], "sections": ["plan.verification"],
                "cursor": plan_first_page["next_cursor"], "max_bytes": 65_536,
            },
        )
        require(_error_code(mismatched_cursor) == "report_cursor_scope_mismatch", "report cursors cannot be reused for a different section scope")

        cursor_report = _report_id(server.tool(
            "submit_report",
            {
                "task_id": task_a, "delegation_id": delegation_b, "mode": "begin", "report_type": "result",
                "idempotency_key": "cursor-stale-begin",
            },
        ))
        cursor_chunk_zero = server.tool(
            "submit_report",
            {
                "task_id": task_a, "delegation_id": delegation_b, "mode": "append", "report_id": cursor_report,
                "chunk_index": 0, "section": "cursor.first", "content": {"part": "first"},
                "idempotency_key": "cursor-stale-append-zero",
            },
        )
        server.tool(
            "submit_report",
            {
                "task_id": task_a, "delegation_id": delegation_b, "mode": "append", "report_id": cursor_report,
                "chunk_index": 1, "section": "cursor.second", "content": {"part": "second"},
                "idempotency_key": "cursor-stale-append-one",
            },
        )
        cursor_first_page = server.tool(
            "read_reports",
            {"task_id": task_a, "report_ids": [cursor_report], "max_bytes": cursor_chunk_zero["chunk_bytes"]},
        )
        cursor_token = cursor_first_page.get("next_cursor")
        require(isinstance(cursor_token, str) and cursor_first_page.get("has_more") is True, "paged report reads bind an opaque v2 snapshot cursor")
        server.tool(
            "submit_report",
            {
                "task_id": task_a, "delegation_id": delegation_b, "mode": "append", "report_id": cursor_report,
                "chunk_index": 2, "section": "cursor.third", "content": {"part": "third"},
                "idempotency_key": "cursor-stale-append-two",
            },
        )
        stale_cursor, stale_cursor_text = server.tool_error(
            "read_reports",
            {"task_id": task_a, "report_ids": [cursor_report], "cursor": cursor_token, "max_bytes": 65_536},
        )
        require(
            _error_code(stale_cursor) == "report_cursor_stale"
            and "Field: cursor." in stale_cursor_text
            and "Expected: restart_without_cursor." in stale_cursor_text
            and "Action: Restart read_reports without cursor" in stale_cursor_text,
            "a changed selected report invalidates the bound continuation cursor with corrective restart guidance",
        )
        restarted_cursor_read = server.tool(
            "read_reports", {"task_id": task_a, "report_ids": [cursor_report], "max_bytes": 65_536},
        )
        require(
            [item.get("chunk_index") for item in restarted_cursor_read.get("reports", [{}])[0].get("chunks", []) if isinstance(item, Mapping)] == [0, 1, 2],
            "restart without a stale cursor returns the current report snapshot without a gap",
        )

        incomplete_plan = server.tool("read_reports", {"task_id": task_a, "report_ids": [plan_id], "max_bytes": 0})
        incomplete_metadata = incomplete_plan.get("reports", [{}])[0] if isinstance(incomplete_plan.get("reports"), list) else {}
        require(
            isinstance(incomplete_metadata, Mapping)
            and incomplete_metadata.get("assembly_state") == "assembling"
            and incomplete_metadata.get("next_chunk_index") == 2
            and incomplete_plan.get("returned_content_bytes") == 0,
            "metadata-only report reads recover a resumable assembly without leaking chunks",
        )
        stale_finalize, _ = server.tool_error(
            "submit_report",
            {
                "task_id": task_a, "delegation_id": delegation_b, "mode": "finalize", "report_id": plan_id,
                "status": "completed", "expected_chunk_count": 2, "expected_content_digest": "sha256:" + ("0" * 64),
                "idempotency_key": "plan-finalize-stale",
            },
        )
        require(_error_code(stale_finalize) == "report_manifest_mismatch", "stale plan finalize digest is rejected without finalizing evidence")
        plan_finalize_args = {
            "task_id": task_a, "delegation_id": delegation_b, "mode": "finalize", "report_id": plan_id,
            "status": "completed", "expected_chunk_count": 2, "expected_content_digest": plan_digest,
            "idempotency_key": "plan-finalize",
        }
        plan_finalized = server.tool("submit_report", plan_finalize_args)
        final_plan = plan_finalized.get("report")
        require(
            isinstance(final_plan, Mapping)
            and final_plan.get("assembly_state") == "finalized"
            and final_plan.get("status") == "completed"
            and final_plan.get("content_digest") == plan_digest,
            "plan finalization atomically binds status, chunk count, and manifest digest",
        )
        require(server.tool("submit_report", plan_finalize_args).get("replayed") is True, "finalized plan acknowledgement is idempotent")
        append_after_finalize, _ = server.tool_error("submit_report", plan_chunk_one_args | {"idempotency_key": "plan-append-after-finalize"})
        require(_error_code(append_after_finalize) == "report_state_conflict", "terminal reports reject later chunk appends")

        race_started = server.tool(
            "submit_report",
            {
                "task_id": task_a, "delegation_id": delegation_b, "mode": "begin", "report_type": "result",
                "idempotency_key": "chunk-race-begin",
            },
        )
        race_report_id = _report_id(race_started)

        def concurrent_chunk(value: str, key: str) -> tuple[str, Mapping[str, Any]]:
            child = McpServer(entrypoint=entrypoint, cwd=project_a, env=env)
            try:
                    response = child.rpc(
                    "tools/call",
                    {
                        "name": "submit_report",
                            "arguments": {
                                "delegation_ref": _record_ref(delegation_b, "delegation"), "mode": "append", "report_ref": _record_ref(race_report_id, "report"),
                            "chunk_index": 0, "section": "findings", "content": {"winner": value}, "idempotency_key": key,
                        },
                        },
                    )
                    result = response.get("result")
                    require(isinstance(result, Mapping), "concurrent append returns an MCP tool result")
                    if result.get("isError") is True:
                        require("structuredContent" not in result, "concurrent correctable append conflict has no success payload")
                        text = _content_text(result)
                        match = re.match(r"Cortex tool error \[([a-z0-9_]+)\]:", text)
                        require(match is not None, "concurrent append conflict has a stable error code")
                        return "error", {"code": match.group(1)}
                    structured, _ = child._structured(response)
                    return "ok", structured
            finally:
                child.close()

        with ThreadPoolExecutor(max_workers=2) as executor:
            competing_chunks = list(executor.map(lambda item: concurrent_chunk(*item), (("first", "chunk-race-first"), ("second", "chunk-race-second"))))
        require(
            [state for state, _receipt in competing_chunks].count("ok") == 1
            and [state for state, _receipt in competing_chunks].count("error") == 1
            and _error_code(next(receipt for state, receipt in competing_chunks if state == "error")) == "report_chunk_conflict",
                "concurrent competing next chunks serialize to one accepted immutable chunk and one deterministic conflict",
        )
        race_metadata = server.tool("read_reports", {"task_id": task_a, "report_ids": [race_report_id], "max_bytes": 0})
        require(
            race_metadata.get("reports", [{}])[0].get("next_chunk_index") == 1
            and race_metadata.get("reports", [{}])[0].get("total_chunks") == 1,
            "concurrent chunk conflict does not create a duplicate or skipped append",
        )
        server.tool(
            "submit_report",
            {
                "task_id": task_a, "delegation_id": delegation_b, "mode": "abort", "report_id": race_report_id,
                "abort_reason_en": "Concurrent alternatives leave one partial, non-final evidence record.", "idempotency_key": "chunk-race-abort",
            },
        )

        quota_started = server.tool(
            "submit_report",
            {
                "task_id": task_a, "delegation_id": delegation_b, "mode": "begin", "report_type": "progress",
                "idempotency_key": "chunk-quota-begin",
            },
        )
        quota_report_id = _report_id(quota_started)
        oversized_chunk, _ = server.tool_error(
            "submit_report",
            {
                "task_id": task_a, "delegation_id": delegation_b, "mode": "append", "report_id": quota_report_id,
                "chunk_index": 0, "section": "evidence", "content": "x" * 33_000, "idempotency_key": "chunk-quota-oversized",
            },
        )
        require(_error_code(oversized_chunk) == "report_chunk_too_large", "chunk upload enforces the 32 KiB bounded-content quota below the MCP frame limit")
        quota_metadata = server.tool("read_reports", {"task_id": task_a, "report_ids": [quota_report_id], "max_bytes": 0})
        require(quota_metadata.get("reports", [{}])[0].get("next_chunk_index") == 0, "quota-rejected chunk leaves an assembling report unchanged")
        server.tool(
            "submit_report",
            {
                "task_id": task_a, "delegation_id": delegation_b, "mode": "abort", "report_id": quota_report_id,
                "abort_reason_en": "A bounded upload rejected the oversized chunk.", "idempotency_key": "chunk-quota-abort",
            },
        )

        aborted_begin = server.tool(
            "submit_report",
            {
                "task_id": task_a, "delegation_id": delegation_b, "mode": "begin", "report_type": "synthesis",
                "idempotency_key": "aborted-report-begin",
            },
        )
        aborted_id = _report_id(aborted_begin)
        server.tool(
            "submit_report",
            {
                "task_id": task_a, "delegation_id": delegation_b, "mode": "append", "report_id": aborted_id,
                "chunk_index": 0, "section": "findings", "content": {"finding": "Incomplete evidence."},
                "idempotency_key": "aborted-report-append",
            },
        )
        aborted = server.tool(
            "submit_report",
            {
                "task_id": task_a, "delegation_id": delegation_b, "mode": "abort", "report_id": aborted_id,
                "abort_reason_en": "The worker stopped before final evidence was complete.", "idempotency_key": "aborted-report-abort",
            },
        )
        require(aborted.get("report", {}).get("assembly_state") == "aborted", "explicit abort preserves durable incomplete evidence")
        aborted_metadata = server.tool("read_reports", {"task_id": task_a, "report_ids": [aborted_id], "max_bytes": 0})
        require(
            aborted_metadata.get("reports", [{}])[0].get("assembly_state") == "aborted",
            "metadata-only reads expose aborted state honestly instead of hiding it as final evidence",
        )
        aborted_append, _ = server.tool_error(
            "submit_report",
            {
                "task_id": task_a, "delegation_id": delegation_b, "mode": "append", "report_id": aborted_id,
                "chunk_index": 1, "section": "next", "content": {"later": "must reject"},
                "idempotency_key": "aborted-report-append-after-terminal",
            },
        )
        require(_error_code(aborted_append) == "report_state_conflict", "aborted reports cannot be resumed implicitly")

        missing_plan_digest, _ = server.tool_error(
            "record_user_decision",
            {
                "task_id": task_a, "subject_type": "plan", "subject_id": plan_id, "decision_type": "approve",
                "prompt_en": "Approve this plan revision?", "response_original": "Да", "response_en": "Yes", "user_language": "ru",
                "idempotency_key": "plan-decision-missing-digest",
            },
        )
        require(_error_code(missing_plan_digest) == "invalid_argument", "plan decisions require the exact finalized manifest digest")
        wrong_plan_digest, _ = server.tool_error(
            "record_user_decision",
            {
                "task_id": task_a, "subject_type": "plan", "subject_id": plan_id, "subject_digest": "sha256:" + ("1" * 64),
                "decision_type": "approve", "prompt_en": "Approve this plan revision?", "response_original": "Да",
                "response_en": "Yes", "user_language": "ru", "idempotency_key": "plan-decision-wrong-digest",
            },
        )
        require(_error_code(wrong_plan_digest) == "decision_subject_digest_mismatch", "plan decisions reject a stale or substituted revision digest")
        plan_decision_args = {
            "task_id": task_a, "subject_type": "plan", "subject_id": plan_id, "subject_digest": plan_digest,
            "decision_type": "approve", "prompt_en": "Approve this plan revision?", "response_original": "Да, согласовано.",
            "response_en": "Yes, approved.", "user_language": "ru", "idempotency_key": "plan-decision-approve",
        }
        plan_decision_args.update(_approval_binding(server.tool(
            "read_reports", {"task_id": task_a, "report_ids": [plan_id], "max_bytes": 65_536},
        )))
        plan_decision = server.tool("record_user_decision", plan_decision_args)
        decision_id = _decision_id(plan_decision)
        decision_record = plan_decision.get("decision")
        require(
            isinstance(decision_record, Mapping)
            and decision_record.get("attribution") == "user_via_coordinator"
            and decision_record.get("response_en_excerpt") == plan_decision_args["response_en"]
            and "response_original" not in decision_record
            and plan_decision_args["response_original"] not in json.dumps(decision_record, ensure_ascii=False),
            "user-decision receipts preserve safe attribution and English context without echoing verbatim original user wording",
        )
        require(server.tool("record_user_decision", plan_decision_args).get("replayed") is True, "user decision replay is idempotent")
        _require_idempotency_conflict(server, "record_user_decision", plan_decision_args | {"response_en": "No."})
        revised_plan = server.tool(
            "submit_report",
            {
                "task_id": task_a, "delegation_id": delegation_b, "report_type": "plan", "status": "completed",
                "content": {"summary": "A revised plan needs its own explicit review."}, "supersedes_report_id": plan_id,
                "review_policy": "required", "idempotency_key": "plan-revision-single",
            },
        )
        revised_plan_record = revised_plan.get("report")
        revised_plan_id = _report_id(revised_plan)
        require(
            isinstance(revised_plan_record, Mapping)
            and revised_plan_record.get("supersedes_report_id") == plan_id
            and revised_plan_record.get("review_policy") == "required"
            and revised_plan_record.get("content_digest") != plan_digest,
            "a plan revision is a new immutable report linked to, but never approved through, its predecessor",
        )
        decision_consumer = server.tool(
            "create_delegation",
            {
                **_delegation_payload(task_a, model="gpt-5.6-terra", effort="high", key="delegation-decision-consumer"),
                "role": "plan-review", "scope": "Consume the approved plan decision as bounded evidence.",
                "input_decision_ids": [decision_id],
            },
        )
        decision_brief = decision_consumer.get("worker_brief")
        require(
            decision_consumer.get("delegation", {}).get("input_decision_ids") == [decision_id]
            and isinstance(decision_brief, Mapping)
            and decision_brief.get("input_decision_ids") == [decision_id],
            "delegation handoff preserves selected decision IDs without reopening task-wide decision discovery",
        )
        brief_decisions = decision_brief.get("input_decisions") if isinstance(decision_brief, Mapping) else None
        require(
            isinstance(brief_decisions, list)
            and len(brief_decisions) == 1
            and isinstance(brief_decisions[0], Mapping)
            and brief_decisions[0].get("decision_id") == decision_id
            and brief_decisions[0].get("subject_type") == "plan"
            and brief_decisions[0].get("subject_id") == plan_id
            and brief_decisions[0].get("response_en") == plan_decision_args["response_en"]
            and brief_decisions[0].get("user_language") == "ru"
            and "response_original" not in brief_decisions[0]
            and plan_decision_args["response_original"] not in json.dumps(brief_decisions, ensure_ascii=False),
            "worker brief carries only selected English-normalized user-decision evidence, never verbatim original wording",
        )
        decision_message = decision_brief.get("worker_message") if isinstance(decision_brief, Mapping) else None
        decision_renderer = decision_brief.get("renderer") if isinstance(decision_brief, Mapping) else None
        require(
            isinstance(decision_message, str)
            and "selected_user_decisions" in decision_message
            and plan_decision_args["response_original"] not in decision_message
            and plan_decision_args["response_en"] in decision_message,
            "trusted renderer labels selected decisions as untrusted data and excludes their original-language response",
        )
        require(
            isinstance(decision_renderer, Mapping)
            and decision_renderer.get("version") == "cortex/worker-message/v1"
            and isinstance(decision_renderer.get("common_policy_digest"), str),
            "decision-consumer brief retains renderer attestation with its direct stable fields",
        )

        read_delegation = server.tool("read_delegation", {"delegation_id": delegation_a, "after_sequence": 0})
        require(read_delegation.get("delegation", {}).get("delegation_id") == delegation_a, "read_delegation returns requested delegation")
        requested_order = server.tool("read_reports", {"task_id": task_a, "report_ids": [report_b, report_a]})
        ordered_reports = requested_order.get("reports")
        require(isinstance(ordered_reports, list), "read_reports returns reports")
        require([item.get("report_id") for item in ordered_reports if isinstance(item, Mapping)] == [report_b, report_a], "read_reports preserves requested report order")
        require(
            [item.get("content") for item in ordered_reports if isinstance(item, Mapping)]
            == ["Independent review completed without a predecessor lifecycle gate.", first_report_args["content"]],
            "read_reports is the sole public operation that returns exact stored report bodies",
        )
        duplicate_report_ids, duplicate_report_text = server.tool_error(
            "read_reports", {"task_id": task_a, "report_ids": [report_a, report_a]},
        )
        require(_error_code(duplicate_report_ids) == "validation_error", "duplicate report IDs are rejected at the MCP schema boundary")
        require(
                "Location: $.report_refs[1]." in duplicate_report_text
            and "Expected:" in duplicate_report_text,
            "duplicate report IDs identify the repeated array item with an actionable schema error",
        )
        task_snapshot = server.tool("inspect_task", {"task_id": task_a})
        require(task_snapshot.get("task", {}).get("task_id") == task_a, "inspect_task returns durable task")
        require(len(task_snapshot.get("delegations") or []) >= 2 and len(task_snapshot.get("reports") or []) >= 2, "inspect_task returns chronology")
        require(
            all("content" not in item for item in task_snapshot.get("reports") or [] if isinstance(item, Mapping)),
            "inspect_task exposes compact report metadata without report bodies",
        )
        task_decisions = task_snapshot.get("decisions")
        require(
            isinstance(task_decisions, list)
            and any(item.get("decision_id") == decision_id for item in task_decisions if isinstance(item, Mapping))
            and all(
                plan_decision_args["response_original"] not in json.dumps(item, ensure_ascii=False)
                and "response_original" not in item
                for item in task_decisions
                if isinstance(item, Mapping)
            ),
            "task inspection exposes compact English decision evidence without copying original user wording",
        )
        expected_task_sequences = [
            item.get("sequence") for item in task_snapshot.get("timeline") or [] if isinstance(item, Mapping)
        ]
        require(expected_task_sequences == sorted(expected_task_sequences), "default task chronology is sequence ordered")
        paged_task_sequences: list[int] = []
        task_cursor = 0
        while True:
            task_page = server.tool("inspect_task", {"task_id": task_a, "after_sequence": task_cursor, "limit": 1})
            task_page_sequences = [
                item.get("sequence") for item in task_page.get("timeline") or [] if isinstance(item, Mapping)
            ]
            require(len(task_page_sequences) <= 1, "task timeline page observes the requested limit")
            require(all(isinstance(sequence, int) and sequence > task_cursor for sequence in task_page_sequences), "task timeline page advances beyond its cursor")
            paged_task_sequences.extend(task_page_sequences)
            next_sequence = task_page.get("next_sequence")
            require(isinstance(next_sequence, int), "task timeline page returns a numeric continuation cursor")
            if not task_page.get("has_more"):
                require(next_sequence == (task_page_sequences[-1] if task_page_sequences else task_cursor), "terminal task page retains a stable cursor")
                break
            require(task_page_sequences and next_sequence == task_page_sequences[-1], "nonterminal task page advances to its final event")
            task_cursor = next_sequence
        require(paged_task_sequences == expected_task_sequences, "task pagination returns every chronology event once without gaps")
        terminal_task_page = server.tool(
            "inspect_task", {"task_id": task_a, "after_sequence": paged_task_sequences[-1], "limit": 1},
        )
        require(
            terminal_task_page.get("timeline") == []
            and terminal_task_page.get("next_sequence") == paged_task_sequences[-1]
            and terminal_task_page.get("has_more") is False,
            "empty task page preserves the supplied terminal cursor",
        )

        override_args = {
            "task_id": task_a, "mode": "minimal", "source": "user_override",
            "rationale": "The user explicitly selected minimal governance.",
            "risk_factors": ["security-shaped request acknowledged by model"], "idempotency_key": "mode-user-minimal",
        }
        override = server.tool("set_governance_mode", override_args)
        require(override.get("assessment", {}).get("mode") == "minimal" and override.get("assessment", {}).get("source") == "user_override", "user override is not rewritten")
        require(server.tool("set_governance_mode", override_args).get("replayed") is True, "mode replay is idempotent")
        _require_idempotency_conflict(server, "set_governance_mode", {**override_args, "mode": "light"})
        full_mode = server.tool(
            "set_governance_mode",
            {
                "task_id": task_a, "mode": "full", "source": "model",
                "rationale": "New evidence warrants deeper verification.", "risk_factors": ["security review evidence"],
                "idempotency_key": "mode-model-full",
            },
        )
        require(full_mode.get("assessment", {}).get("mode") == "full", "later model revision is stored")
        post_full_plan_decision_args = {
            "task_id": task_a,
            "subject_type": "plan",
            "subject_id": plan_id,
            "subject_digest": plan_digest,
            "decision_type": "approve",
            "prompt_en": "Approve the plan for the elevated full-governance gate?",
            "response_original": "Yes, approved after governance escalation.",
            "response_en": "Yes, approved after governance escalation.",
            "user_language": "en",
            "idempotency_key": "plan-decision-approve-after-full",
        }
        post_full_plan_decision_args.update(_approval_binding(server.tool(
            "read_reports", {"task_id": task_a, "report_ids": [plan_id], "max_bytes": 65_536},
        )))
        post_full_decision_id = _decision_id(server.tool("record_user_decision", post_full_plan_decision_args))

        initiative_args = {
            "task_id": task_a, "goal": "Coordinate several related V12 tasks without a completion gate.",
            "risk": "high", "status": "proposed", "linked_task_ids": [task_a], "linked_report_ids": [report_a, report_b],
            "notes": "Project-level initiative evidence.", "idempotency_key": "initiative-create",
        }
        initiative_a = _initiative_id(server.tool("record_initiative", initiative_args))
        require(server.tool("record_initiative", initiative_args).get("replayed") is True, "initiative replay is idempotent")
        _require_idempotency_conflict(server, "record_initiative", {**initiative_args, "goal": "Conflicting initiative payload."})
        for index, status in enumerate(("active", "paused", "completed", "closed", "cancelled", "active"), start=1):
            revision = server.tool(
                "record_initiative",
                {
                    "task_id": task_a, "goal": initiative_args["goal"], "initiative_id": initiative_a, "risk": "high",
                    "status": status, "linked_task_ids": [task_a], "linked_report_ids": [report_a],
                    "notes": f"Free model-owned status transition {index}.", "idempotency_key": f"initiative-transition-{index}",
                },
            )
            require(_initiative_id(revision) == initiative_a, "initiative revision keeps stable identity")

        initiative_b = _initiative_id(server.tool(
            "record_initiative",
            {
                "task_id": task_a, "goal": "Second side of a cycle.",
                "dependencies": [initiative_a], "status": "active", "idempotency_key": "initiative-b-create",
            },
        ))
        cycle = server.tool(
            "record_initiative",
            {
                "task_id": task_a, "goal": initiative_args["goal"], "initiative_id": initiative_a,
                "dependencies": [initiative_b], "linked_task_ids": [task_a],
                "linked_report_ids": [report_a, report_b], "notes": "Retain a cyclic dependency as a model-visible warning.",
                "idempotency_key": "initiative-cycle",
            },
        )
        require("cyclic_dependency" in json.dumps(cycle.get("warnings") or []), "cyclic dependency is stored as warning")
        governance_snapshot = server.tool("inspect_governance", {"task_id": task_a})
        assessments = governance_snapshot.get("assessments")
        require(isinstance(assessments, list) and len(assessments) >= 2, "governance assessment history is append-only")
        assessment_modes = {
            item.get("assessment_id"): item.get("mode")
            for item in assessments
            if isinstance(item, Mapping) and isinstance(item.get("assessment_id"), str)
        }
        chronology_modes = [
            assessment_modes.get(item.get("assessment_id"))
            for item in governance_snapshot.get("timeline") or []
            if isinstance(item, Mapping) and item.get("assessment_id") in assessment_modes
        ]
        require(
            chronology_modes[-2:] == ["minimal", "full"]
            and chronology_modes.count("minimal") >= 2,
            "governance inspection retains the storage-busy retry and later user/model assessments in append-only chronology",
        )
        initiatives = governance_snapshot.get("initiatives")
        require(isinstance(initiatives, list) and any(item.get("initiative_id") == initiative_a for item in initiatives if isinstance(item, Mapping)), "project initiative is inspectable")
        projection = governance_snapshot.get("projection")
        require(
            isinstance(projection, Mapping)
            and projection.get("effective_mode") == "minimal"
            and projection.get("override_active") is True
            and isinstance(projection.get("latest_user_override"), Mapping)
            and projection["latest_user_override"].get("mode") == "minimal"
            and isinstance(projection.get("latest_model_assessment"), Mapping)
            and projection["latest_model_assessment"].get("mode") == "full",
            "governance projection preserves the user override while retaining later model evidence",
        )
        revisions = governance_snapshot.get("initiative_revisions")
        require(
            isinstance(revisions, list)
            and any(
                isinstance(item, Mapping)
                and item.get("initiative_id") == initiative_a
                and isinstance(item.get("payload"), Mapping)
                and item["payload"].get("status") in {"proposed", "active", "paused", "completed", "closed", "cancelled"}
                for item in revisions
            ),
            "governance inspection returns append-only initiative revision payloads",
        )

        closures_before_invalid_subject = server.tool("inspect_governance", {"task_id": task_a}).get("closures")
        missing_subject_args = {
            "task_id": task_a, "subject_type": "task", "verdict": "not_ready",
            "evidence": [report_a], "idempotency_key": "closure-missing-subject",
        }
        missing_subject, missing_subject_text = server.tool_error("submit_governance_closure", missing_subject_args)
        require(_error_code(missing_subject) == "validation_error", "closure without subject ID is rejected by the public schema")
        require(
            "Location: $." in missing_subject_text
            and "Expected:" in missing_subject_text,
            "missing closure subject ID fails at the MCP schema boundary with a corrective path",
        )
        empty_subject, _ = server.tool_error(
            "submit_governance_closure",
            {**missing_subject_args, "subject_id": "", "idempotency_key": "closure-empty-subject"},
        )
        require(_error_code(empty_subject) == "validation_error", "closure subject ID cannot be empty")
        closures_after_invalid_subject = server.tool("inspect_governance", {"task_id": task_a}).get("closures")
        require(closures_after_invalid_subject == closures_before_invalid_subject, "schema-rejected closure subject IDs create no closure")

        qa_closure_delegation = _delegation_id(server.tool(
            "create_delegation",
            {
                **_delegation_payload(task_a, model="gpt-5.6-terra", effort="high", key="closure-qa-delegation", profile_name="qa_engineer"),
                "role": "independent verification",
                "scope": "Independently verify the approved plan and primary result.",
                "input_report_ids": [plan_id, report_b],
                "input_decision_ids": [post_full_decision_id],
            },
        ))
        qa_closure_report = _report_id(server.tool(
            "submit_report",
            {
                "task_id": task_a,
                "delegation_id": qa_closure_delegation,
                "report_type": "result",
                "status": "completed",
                "content": "Independent QA completed against the approved plan and primary result.",
                "idempotency_key": "closure-qa-report",
            },
        ))
        docs_closure_delegation = _delegation_id(server.tool(
            "create_delegation",
            {
                **_delegation_payload(task_a, model="gpt-5.6-terra", effort="high", key="closure-docs-delegation", profile_name="technical_writer"),
                "role": "documentation impact",
                "scope": "Assess documentation impact after approval using all completed results.",
                "input_report_ids": [plan_id, report_b, qa_closure_report],
                "input_decision_ids": [post_full_decision_id],
            },
        ))
        docs_closure_report = _report_id(server.tool(
            "submit_report",
            {
                "task_id": task_a,
                "delegation_id": docs_closure_delegation,
                "report_type": "result",
                "status": "completed",
                "content": "Documentation-impact assessment completed with no unrecorded documentation changes.",
                "idempotency_key": "closure-docs-report",
            },
        ))
        server.tool("read_reports", {"task_id": task_a, "report_ids": [docs_closure_report]})

        initiative_closure = server.tool(
            "submit_governance_closure",
            {
                "task_id": task_a, "subject_type": "initiative", "subject_id": initiative_a,
                "verdict": "ready_with_risks", "evidence": {"basis": "release review", "artifacts": [report_a]},
                "unresolved_risks": ["Cyclic dependency remains informational."], "initiative_status": "closed",
                "completion_notes": "Model accepts residual dependency risk.", "idempotency_key": "initiative-closure-with-risk",
            },
        )
        require(initiative_closure.get("closure", {}).get("verdict") == "ready_with_risks", "initiative closure permits unresolved dependency")
        require(
            initiative_closure.get("closure", {}).get("subject_type") == "initiative"
            and initiative_closure.get("closure", {}).get("subject_id") == initiative_a,
            "initiative closure uses documented subject_type and subject_id arguments on first closure call",
        )
        task_scoped_after_initiative_closure = server.tool("inspect_governance", {"task_id": task_a, "limit": 200})
        task_scoped_links = task_scoped_after_initiative_closure.get("links") or []
        require(
            any(
                isinstance(item, Mapping)
                and item.get("initiative_id") == initiative_a
                and item.get("relationship") == "task"
                and item.get("target_id") == task_a
                for item in task_scoped_links
            ),
            "task-scoped governance surfaces the final initiative through its exact task relationship",
        )
        require(
            any(
                isinstance(item, Mapping)
                and item.get("subject_type") == "initiative"
                and item.get("subject_id") == initiative_a
                and item.get("verdict") == "ready_with_risks"
                for item in task_scoped_after_initiative_closure.get("closures") or []
            ),
            "task-scoped governance verifies the initiative closure after its successful write",
        )
        initiative_scoped_after_closure = server.tool(
            "inspect_governance", {"task_id": task_a, "initiative_id": initiative_a, "limit": 200},
        )
        initiative_scoped_links = initiative_scoped_after_closure.get("links") or []
        require(
            {
                (item.get("relationship"), item.get("target_id"))
                for item in initiative_scoped_links
                if isinstance(item, Mapping)
            }
            >= {("task", task_a), ("report", report_a), ("report", report_b)},
            "initiative-scoped governance verifies the exact task and every required report link",
        )
        initiative_latest_closure = (initiative_scoped_after_closure.get("projection") or {}).get("latest_closure")
        require(
            isinstance(initiative_latest_closure, Mapping)
            and initiative_latest_closure.get("subject_type") == "initiative"
            and initiative_latest_closure.get("subject_id") == initiative_a
            and initiative_latest_closure.get("verdict") == "ready_with_risks",
            "initiative-scoped governance verifies the latest closure subject and verdict",
        )

        closures_before_mixed_task_fields = server.tool("inspect_governance", {"task_id": task_a}).get("closures")
        mixed_task_fields, _ = server.tool_error(
            "submit_governance_closure",
            {
                "task_id": task_a, "subject_type": "task", "subject_id": task_a, "verdict": "ready",
                "evidence": [report_a, report_b], "initiative_status": "closed",
                "completion_notes": "Initiative-only fields must never accompany a task closure.",
                "idempotency_key": "task-closure-with-initiative-fields",
            },
        )
        require(
                _error_code(mixed_task_fields) == "invalid_closure_subject",
                "task closure rejects initiative-only status while allowing opaque completion notes",
        )
        require(
            server.tool("inspect_governance", {"task_id": task_a}).get("closures")
            == closures_before_mixed_task_fields,
            "rejected task/initiative closure field mixing creates no closure",
        )

        closure_args = {
            "task_id": task_a, "subject_type": "task", "subject_id": task_a, "verdict": "not_ready",
            "evidence": [report_a, report_b], "unresolved_risks": ["Independent review remains useful."],
            "follow_ups": ["Create a rework delegation."],
            "idempotency_key": "task-not-ready",
        }
        not_ready = server.tool("submit_governance_closure", closure_args)
        require(not_ready.get("closure", {}).get("verdict") == "not_ready", "not-ready closure is recorded")
        require(
            not_ready.get("closure", {}).get("subject_type") == "task"
            and not_ready.get("closure", {}).get("subject_id") == task_a,
            "task closure uses documented subject_type and subject_id arguments on first closure call",
        )
        require(server.tool("submit_governance_closure", closure_args).get("replayed") is True, "closure replay is idempotent")
        _require_idempotency_conflict(server, "submit_governance_closure", {**closure_args, "verdict": "ready"})
        rework = server.tool(
            "create_delegation",
            {
                **_delegation_payload(task_a, model="gpt-5.6-sol", effort="xhigh", key="delegation-rework"),
                "role": "rework", "scope": "Address advisory not-ready finding.",
                "instructions": KNOWLEDGE_CONTRACT_INSTRUCTIONS,
                "input_report_ids": [plan_id, report_b],
                "approval_decision_id": post_full_decision_id,
            },
        )
        rework_id = _delegation_id(rework)
        require(_report_id(server.tool(
            "submit_report",
            {
                "task_id": task_a, "delegation_id": rework_id, "report_type": "synthesis", "status": "completed",
                "content": "Rework delegation was allowed after advisory not-ready closure.", "idempotency_key": "rework-report",
            },
        )), "report remains allowed after not-ready closure")

        secret_marker = "SENSITIVE-REPORT-CONTENT-MUST-NOT-LEAK"
        task_b = _task_id(server.tool("create_task", _task_payload(
            project_b, objective=secret_marker, key="task-b-create",
        )))
        injected_root, _ = server.tool_error("inspect_task", {"task_id": task_a, "project_root": str(project_b)})
        require(_error_code(injected_root) == "validation_error", "post-creation project-root injection is rejected at the public schema")
        inspect_b = server.tool("inspect_task", {"task_id": task_b})
        require(inspect_b.get("task", {}).get("task_id") == task_b, "task ID alone resolves the correct project shard")
        require(inspect_b.get("delegations") == [], "rejected foreign reference does not mutate destination ledger")
        task_b_governance = server.tool("inspect_governance", {"task_id": task_b})
        require(task_b_governance.get("initiatives") == [] and task_b_governance.get("links") == [], "task governance projection excludes initiatives unrelated to the selected task")

        # Human-readable Markdown remains a derived host-private view.  The
        # source project can retain V11 artifacts, but the V12 server must not
        # create or modify anything there.
        require(not (project_b / ".codex").exists(), "a clean project root receives no V12 local artifact directory")
        for relative, digest in v11_local_digests.items():
            path = project_a / relative
            require(path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == digest, "V12 leaves a pre-existing project-local V11 artifact byte-for-byte untouched")
        require(not (project_a / ".codex" / "cortex" / "v12").exists(), "V12 never creates a competing project-local ledger namespace")

        shard_root = home / ".codex" / "cortex" / "v12" / "projects" / f"p-{task_id_match.group(1)}"
        task_views = shard_root / "tasks" / _task_ref(task_a)
        expected_views = (
            task_views / "index.md",
            task_views / "task.md",
            task_views / "delegations" / f"{delegation_a}.md",
            task_views / "reports" / f"{report_a}.md",
            task_views / "plans" / "revisions" / f"{plan_id}.md",
            task_views / "plans" / "revisions" / f"{revised_plan_id}.md",
            task_views / "plans" / "current.md",
            task_views / "decisions" / f"{decision_id}.md",
            task_views / "timeline" / "index.md",
        )
        timeline_index_path = task_views / "timeline" / "index.md"
        timeline_latest, timeline_index_pages = _markdown_timeline_index(timeline_index_path)
        require(
            timeline_index_path.read_text(encoding="utf-8").startswith("# Timeline")
            and isinstance(timeline_index_pages, list)
            and timeline_index_pages,
            "timeline Markdown index records a bounded current range-page map",
        )
        timeline_pages: list[Path] = []
        page_ranges: list[tuple[int, int]] = []
        for item in timeline_index_pages:
            relative = item.get("path")
            first, last, events = item.get("first_sequence"), item.get("last_sequence"), item.get("events")
            require(
                isinstance(relative, str)
                and re.fullmatch(r"pages/\d+-\d+\.md", relative) is not None
                and isinstance(first, int)
                and isinstance(last, int)
                and first <= last
                and (events is None or (isinstance(events, int) and 1 <= events <= 100))
                and relative == f"pages/{first}-{last}.md",
                "timeline index references deterministic bounded sequence-range pages",
            )
            timeline_pages.append(task_views / "timeline" / relative)
            page_ranges.append((first, last))
        require(
            timeline_latest == page_ranges[-1][1]
            and page_ranges == sorted(page_ranges)
            and all(previous[1] < current[0] for previous, current in zip(page_ranges, page_ranges[1:])),
            "timeline range pages are chronologically ordered and never overlap",
        )
        require(not (task_views / "timeline" / "0001.md").exists(), "current projections never depend on unstable ordinal timeline filenames")
        expected_views = (*expected_views, *timeline_pages)
        view_directories = {task_views, *(path.parent for path in expected_views)}
        for directory in view_directories:
            require(directory.is_dir() and not directory.is_symlink(), "human-view directories are regular host-private directories")
            require(stat.S_IMODE(directory.stat().st_mode) == 0o700, "human-view directories are owner-only")
        for view in expected_views:
            require(view.is_file() and not view.is_symlink(), "human-view output is a regular Markdown file")
            require(stat.S_IMODE(view.stat().st_mode) == 0o600, "human-view Markdown is owner-only")
            require(view.resolve().is_relative_to(shard_root.resolve()), "human-view paths remain contained below the host-private V12 shard")
            require(not view.resolve().is_relative_to(project_a.resolve()), "human-view paths never resolve within the project root")

        verified_index = server.tool("read_reports", {"task_id": task_a, "report_ids": [report_a]})
        index_view = verified_index.get("human_view")
        require(
            isinstance(index_view, Mapping)
            and index_view.get("status") == "ready"
            and index_view.get("path") == str(task_views / "index.md")
            and Path(str(index_view.get("path"))).is_absolute(),
            "public responses publish only a verified absolute host-private Markdown view",
        )

        altered_report_view = task_views / "reports" / f"{report_a}.md"
        altered_bytes = b"# Externally altered host-private projection\n"
        altered_report_view.write_bytes(altered_bytes)
        conflict_replay = server.tool("submit_report", first_report_args)
        conflict_view = conflict_replay.get("human_view")
        require(
            conflict_replay.get("replayed") is True
            and isinstance(conflict_view, Mapping)
            and conflict_view.get("status") == "conflict"
            and conflict_view.get("path") is None,
            "idempotent canonical replay dynamically re-observes an altered derived view as conflict",
        )
        require(altered_report_view.read_bytes() == altered_bytes, "projection conflict preserves the externally altered Markdown file")
        conflict_read = server.tool("read_reports", {"task_id": task_a, "report_ids": [report_a]})
        require(
            conflict_read.get("reports", [{}])[0].get("content") == first_report_args["content"],
            "projection conflict never rolls back or blocks canonical report reads",
        )
    finally:
        server.close()

    def concurrent_mutations(index: int) -> tuple[str, str]:
        child = McpServer(entrypoint=entrypoint, cwd=project_a, env=env)
        try:
            delegation = child.tool(
                "create_delegation",
                {
                    **_delegation_payload(task_a, model="gpt-5.6-terra", effort="high", key=f"concurrent-delegation-{index}"),
                    "role": "concurrent-review", "scope": f"Independent atomic write {index}.",
                    "input_report_ids": [plan_id, report_b],
                    "approval_decision_id": post_full_decision_id,
                },
            )
            report_id = _report_id(child.tool(
                "submit_report",
                {
                    "task_id": task_a, "delegation_id": _delegation_id(delegation), "report_type": "progress", "status": "completed",
                    "content": f"Concurrent report {index}.", "idempotency_key": f"concurrent-report-{index}",
                },
            ))
            assessment = child.tool(
                "set_governance_mode",
                {
                    "task_id": task_a, "mode": "light", "source": "model",
                    "rationale": f"Concurrent assessment {index}.", "idempotency_key": f"concurrent-assessment-{index}",
                },
            )
            assessment_id = assessment.get("assessment", {}).get("assessment_id")
            require(isinstance(assessment_id, str) and assessment_id, "concurrent assessment has stable identity")
            initiative = child.tool(
                "record_initiative",
                {
                    "task_id": task_a, "initiative_id": initiative_a, "goal": initiative_args["goal"],
                    "status": "active", "notes": f"Concurrent initiative revision {index}.",
                    "idempotency_key": f"concurrent-initiative-{index}",
                },
            )
            require(_initiative_id(initiative) == initiative_a, "concurrent initiative revision keeps stable identity")
            return report_id, assessment_id
        finally:
            child.close()

    with ThreadPoolExecutor(max_workers=3) as executor:
        concurrent_results = list(executor.map(concurrent_mutations, range(3)))
    concurrent_report_ids = [item[0] for item in concurrent_results]
    concurrent_assessment_ids = [item[1] for item in concurrent_results]
    require(len(set(concurrent_report_ids)) == 3, "concurrent reports retain distinct durable identities")
    require(len(set(concurrent_assessment_ids)) == 3, "concurrent assessments retain distinct durable identities")
    verifier = McpServer(entrypoint=entrypoint, cwd=project_a, env=env)
    try:
        final_snapshot = verifier.tool("inspect_task", {"task_id": task_a, "limit": 200})
        stored_ids = {item.get("report_id") for item in final_snapshot.get("reports") or [] if isinstance(item, Mapping)}
        require(set(concurrent_report_ids).issubset(stored_ids), "all concurrent reports are visible after commit")
        sequence = [item.get("sequence") for item in final_snapshot.get("timeline") or [] if isinstance(item, Mapping)]
        require(sequence == sorted(sequence) and len(sequence) == len(set(sequence)), "timeline remains atomically ordered")
        final_governance = verifier.tool("inspect_governance", {"task_id": task_a, "after_sequence": 0, "limit": 100})
        assessment_ids = {
            item.get("assessment_id") for item in final_governance.get("assessments") or [] if isinstance(item, Mapping)
        }
        require(set(concurrent_assessment_ids).issubset(assessment_ids), "all concurrent assessments are visible after commit")
        initiative_governance = verifier.tool(
            "inspect_governance", {"task_id": task_a, "initiative_id": initiative_a},
        )
        concurrent_initiative = next(
            item for item in initiative_governance.get("initiatives") or []
            if isinstance(item, Mapping) and item.get("initiative_id") == initiative_a
        )
        require(int(concurrent_initiative.get("latest_revision") or 0) >= 11, "concurrent initiative revisions commit without loss")
    finally:
        verifier.close()

    v12_databases = list((home / ".codex" / "cortex" / "v12" / "projects").glob("p-*/cortex.db"))
    require(len(v12_databases) >= 2, "V12 creates one separate ledger namespace per project")
    v12_root = home / ".codex" / "cortex" / "v12"
    for directory in (v12_root, v12_root / "projects", *(database.parent for database in v12_databases)):
        require(stat.S_IMODE(directory.stat().st_mode) == 0o700, "V12 state directories are owner-only")
    for database in v12_databases:
        with sqlite3.connect(database) as connection:
            integrity = connection.execute("PRAGMA integrity_check").fetchone()
        require(integrity == ("ok",), "V12 concurrent ledger passes SQLite integrity check")
        for ledger_file in (database, Path(f"{database}-wal"), Path(f"{database}-shm")):
            require(ledger_file.is_file() and not ledger_file.is_symlink(), "V12 ledger files remain regular files")
            require(stat.S_IMODE(ledger_file.stat().st_mode) == 0o600, "V12 ledger files are owner-only")
    require(hashlib.sha256(v11_database.read_bytes()).hexdigest() == v11_digest_before, "V12 leaves V11 database byte-for-byte untouched")

    symlink_home = tmp_path / "symlink-home"
    symlink_home.mkdir()
    symlink_target = tmp_path / "external-ledger-target.bin"
    symlink_target.write_bytes(b"external sentinel must not be opened or modified")
    symlink_digest = hashlib.sha256(symlink_target.read_bytes()).hexdigest()
    symlink_hash = hashlib.sha256(canonical_project_a.encode("utf-8")).hexdigest()
    symlink_database = symlink_home / ".codex" / "cortex" / "v12" / "projects" / f"p-{symlink_hash}" / "cortex.db"
    symlink_database.parent.mkdir(parents=True)
    symlink_database.symlink_to(symlink_target)
    symlink_server = McpServer(entrypoint=entrypoint, cwd=project_a, env=_runtime_environment(symlink_home))
    try:
        symlink_failure = symlink_server.tool_rpc_error(
            "create_task",
            _task_payload(project_a, objective="A symlinked ledger must be rejected.", key="symlink-ledger"),
            cortex_code="storage_unavailable",
        )
        require("external sentinel" not in json.dumps(symlink_failure, ensure_ascii=False), "symlink rejection never exposes target contents")
        require(symlink_server.rpc("ping", {}).get("result") == {}, "server survives rejected symlinked ledger access")
    finally:
        symlink_server.close()
    require(hashlib.sha256(symlink_target.read_bytes()).hexdigest() == symlink_digest, "rejected symlink target remains byte-for-byte untouched")

    # The sole additive V12 schema-v1 migration upgrades the exact released
    # pre-human-view V12 layout on the first normal path-bearing create_task.
    # Legacy task rows intentionally have no project_root column.
    legacy_home = tmp_path / "legacy-home"
    legacy_home.mkdir()
    legacy_project = tmp_path / "legacy-project"
    legacy_project.mkdir()
    legacy_project_root = str(legacy_project.resolve())
    legacy_hash = hashlib.sha256(legacy_project_root.encode("utf-8")).hexdigest()
    legacy_task_id = f"task-{legacy_hash}-" + ("c" * 32)
    legacy_delegation_id = f"delegation-{legacy_hash}-" + ("d" * 32)
    legacy_report_id = f"report-{legacy_hash}-" + ("e" * 32)
    legacy_database = legacy_home / ".codex" / "cortex" / "v12" / "projects" / f"p-{legacy_hash}" / "cortex.db"
    legacy_database.parent.mkdir(parents=True)
    legacy_timestamp = "2026-08-27T00:00:00+00:00"
    legacy_content: dict[str, str] = {"summary": "A pre-human-view V12 report remains readable after migration."}
    with sqlite3.connect(legacy_database) as connection:
        connection.executescript(
            """
            CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY,name TEXT NOT NULL,applied_at TEXT NOT NULL);
            CREATE TABLE v12_metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);
            CREATE TABLE timeline(sequence INTEGER PRIMARY KEY AUTOINCREMENT,occurred_at TEXT NOT NULL,event_type TEXT NOT NULL,entity_type TEXT NOT NULL,entity_id TEXT NOT NULL,task_id TEXT,delegation_id TEXT,report_id TEXT,initiative_id TEXT,assessment_id TEXT,closure_id TEXT,payload_json TEXT NOT NULL);
            CREATE TABLE tasks(task_id TEXT PRIMARY KEY,project_hash TEXT NOT NULL,objective TEXT NOT NULL,context_json TEXT NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,created_sequence INTEGER NOT NULL,updated_sequence INTEGER NOT NULL);
            CREATE TABLE delegations(delegation_id TEXT PRIMARY KEY,project_hash TEXT NOT NULL,task_id TEXT NOT NULL REFERENCES tasks(task_id),parent_delegation_id TEXT REFERENCES delegations(delegation_id),objective TEXT NOT NULL,role TEXT NOT NULL,scope TEXT NOT NULL,instructions TEXT NOT NULL,input_report_ids_json TEXT NOT NULL,model TEXT NOT NULL,reasoning_effort TEXT NOT NULL,created_at TEXT NOT NULL,created_sequence INTEGER NOT NULL);
            CREATE TABLE reports(report_id TEXT PRIMARY KEY,project_hash TEXT NOT NULL,task_id TEXT NOT NULL REFERENCES tasks(task_id),delegation_id TEXT NOT NULL REFERENCES delegations(delegation_id),report_type TEXT NOT NULL,status TEXT NOT NULL,content_json TEXT NOT NULL,created_at TEXT NOT NULL,created_sequence INTEGER NOT NULL);
            CREATE TABLE idempotency(operation TEXT NOT NULL,idempotency_key TEXT NOT NULL,payload_digest TEXT NOT NULL,result_json TEXT NOT NULL,created_at TEXT NOT NULL,PRIMARY KEY(operation,idempotency_key));
            CREATE TABLE governance_assessments(assessment_id TEXT PRIMARY KEY,project_hash TEXT NOT NULL,task_id TEXT NOT NULL REFERENCES tasks(task_id),initiative_id TEXT,mode TEXT NOT NULL,source TEXT NOT NULL,rationale TEXT,risk_factors_json TEXT NOT NULL,created_at TEXT NOT NULL,created_sequence INTEGER NOT NULL);
            CREATE TABLE initiatives(initiative_id TEXT PRIMARY KEY,project_hash TEXT NOT NULL,goal TEXT NOT NULL,risk TEXT,status TEXT NOT NULL,notes_json TEXT NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,latest_revision INTEGER NOT NULL,created_sequence INTEGER NOT NULL,updated_sequence INTEGER NOT NULL);
            CREATE TABLE initiative_revisions(revision_id INTEGER PRIMARY KEY AUTOINCREMENT,initiative_id TEXT NOT NULL REFERENCES initiatives(initiative_id),revision_number INTEGER NOT NULL,project_hash TEXT NOT NULL,occurred_at TEXT NOT NULL,sequence INTEGER NOT NULL,payload_json TEXT NOT NULL,UNIQUE(initiative_id,revision_number));
            CREATE TABLE initiative_links(link_id INTEGER PRIMARY KEY AUTOINCREMENT,initiative_id TEXT NOT NULL REFERENCES initiatives(initiative_id),project_hash TEXT NOT NULL,relationship TEXT NOT NULL,target_id TEXT NOT NULL,is_resolved INTEGER NOT NULL,warnings_json TEXT NOT NULL,created_at TEXT NOT NULL,UNIQUE(initiative_id,relationship,target_id));
            CREATE TABLE governance_closures(closure_id TEXT PRIMARY KEY,project_hash TEXT NOT NULL,subject_type TEXT NOT NULL,subject_id TEXT NOT NULL,verdict TEXT NOT NULL,evidence_json TEXT NOT NULL,unresolved_risks_json TEXT NOT NULL,follow_ups_json TEXT NOT NULL,initiative_status TEXT,completion_notes_json TEXT,created_at TEXT NOT NULL,created_sequence INTEGER NOT NULL);
            CREATE INDEX timeline_task_sequence ON timeline(task_id,sequence);
            CREATE INDEX timeline_delegation_sequence ON timeline(delegation_id,sequence);
            CREATE INDEX timeline_initiative_sequence ON timeline(initiative_id,sequence);
            CREATE INDEX reports_task_created ON reports(task_id,created_sequence);
            CREATE INDEX reports_delegation_created ON reports(delegation_id,created_sequence);
            CREATE INDEX assessments_task_created ON governance_assessments(task_id,created_sequence);
            CREATE INDEX initiative_links_source ON initiative_links(initiative_id,relationship);
            """
        )
        connection.execute(f"PRAGMA application_id = {int('43563132', 16)}")
        connection.execute("PRAGMA user_version = 1")
        connection.execute("INSERT INTO schema_migrations(version,name,applied_at) VALUES (1, 'v12-initial', ?)", (legacy_timestamp,))
        connection.executemany(
            "INSERT INTO v12_metadata(key,value) VALUES (?, ?)",
            (
                ("project_hash", legacy_hash),
                ("project_root_digest", hashlib.sha256(legacy_project_root.encode("utf-8")).hexdigest()),
            ),
        )
        connection.execute(
            "INSERT INTO tasks(task_id,project_hash,objective,context_json,created_at,updated_at,created_sequence,updated_sequence) VALUES (?, ?, ?, ?, ?, ?, 1, 1)",
            (legacy_task_id, legacy_hash, "Keep legacy V12 evidence durable.", "null", legacy_timestamp, legacy_timestamp),
        )
        connection.execute(
            "INSERT INTO delegations(delegation_id,project_hash,task_id,parent_delegation_id,objective,role,scope,instructions,input_report_ids_json,model,reasoning_effort,created_at,created_sequence) VALUES (?, ?, ?, NULL, ?, ?, ?, ?, '[]', ?, ?, ?, 2)",
            (legacy_delegation_id, legacy_hash, legacy_task_id, "Produce legacy evidence.", "qa_engineer", "bounded legacy scope", "Submit a compact result.", "gpt-5.6-luna", "high", legacy_timestamp),
        )
        connection.execute(
            "INSERT INTO reports(report_id,project_hash,task_id,delegation_id,report_type,status,content_json,created_at,created_sequence) VALUES (?, ?, ?, ?, 'result', 'completed', ?, ?, 3)",
            (legacy_report_id, legacy_hash, legacy_task_id, legacy_delegation_id, json.dumps(legacy_content, ensure_ascii=False, separators=(",", ":")), legacy_timestamp),
        )
        connection.executemany(
            "INSERT INTO timeline(sequence,occurred_at,event_type,entity_type,entity_id,task_id,delegation_id,report_id,initiative_id,assessment_id,closure_id,payload_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL, ?)",
            (
                (1, legacy_timestamp, "task_created", "task", legacy_task_id, legacy_task_id, None, None, json.dumps({"task_id": legacy_task_id}, separators=(",", ":"))),
                (2, legacy_timestamp, "delegation_created", "delegation", legacy_delegation_id, legacy_task_id, legacy_delegation_id, None, json.dumps({"delegation_id": legacy_delegation_id}, separators=(",", ":"))),
                (3, legacy_timestamp, "report_submitted", "report", legacy_report_id, legacy_task_id, legacy_delegation_id, legacy_report_id, json.dumps({"report_id": legacy_report_id}, separators=(",", ":"))),
            ),
        )
        connection.commit()
    for directory in (
        legacy_home / ".codex" / "cortex" / "v12",
        legacy_home / ".codex" / "cortex" / "v12" / "projects",
        legacy_database.parent,
    ):
        os.chmod(directory, 0o700)
    os.chmod(legacy_database, 0o600)

    legacy_health_process, legacy_health = maintenance_cli(
        legacy_home, "health", "--task-id", legacy_task_id,
    )
    require(
        legacy_health_process.returncode == 0
        and legacy_health.get("ok") is True
        and legacy_health.get("healthy") is False,
        "maintenance health remains read-only and reports an unexpanded V12 schema as unhealthy",
    )
    with sqlite3.connect(legacy_database) as connection:
        legacy_before_mcp_migrations = connection.execute("SELECT version,name FROM schema_migrations ORDER BY version").fetchall()
        legacy_before_mcp_chunks = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='report_chunks'"
        ).fetchall()
    require(
        legacy_before_mcp_migrations == [(1, "v12-initial")] and legacy_before_mcp_chunks == [],
        "read-only maintenance health neither bootstraps nor migrates an existing legacy V12 shard",
    )

    legacy_server = McpServer(entrypoint=entrypoint, cwd=legacy_project, env=_runtime_environment(legacy_home))
    try:
        # Each public call constructs a store.  The first succeeds only when
        # the path-bearing service flow migrates the exact legacy shard; the
        # second proves an already-migrated normal open is idempotent.
        migrated_task_one = _task_id(legacy_server.tool(
            "create_task",
            _task_payload(legacy_project_root, objective="First automatic migration task.", key="legacy-migrate-one"),
        ))
        migrated_task_two = _task_id(legacy_server.tool(
            "create_task",
            _task_payload(legacy_project_root, objective="Second idempotent migration-open task.", key="legacy-migrate-two"),
        ))
        require(len({legacy_task_id, migrated_task_one, migrated_task_two}) == 3, "automatic migration preserves legacy and newly allocated task identities")
        migrated = legacy_server.tool("read_reports", {"task_id": legacy_task_id, "report_ids": [legacy_report_id]})
        migrated_report = migrated.get("reports", [{}])[0]
        require(
            migrated_report.get("assembly_state") == "finalized"
            and migrated_report.get("next_chunk_index") == 1
            and migrated_report.get("total_chunks") == 1
            and migrated_report.get("content") == legacy_content
            and migrated_report.get("chunks", [{}])[0].get("content") == legacy_content,
            "the real MCP migration exposes the legacy report as one finalized verified chunk",
        )
        first_delegation = _delegation_id(legacy_server.tool(
            "create_delegation", _delegation_payload(migrated_task_one, model="gpt-5.6-luna", effort="high", key="legacy-migration-first-delegation"),
        ))
        second_delegation = _delegation_id(legacy_server.tool(
            "create_delegation", _delegation_payload(migrated_task_two, model="gpt-5.6-luna", effort="high", key="legacy-migration-second-delegation"),
        ))
        first_report = _report_id(legacy_server.tool(
            "submit_report",
            {"task_id": migrated_task_one, "delegation_id": first_delegation, "report_type": "result", "status": "completed", "content": "First migrated task report.", "idempotency_key": "legacy-migration-first-report"},
        ))
        second_report = _report_id(legacy_server.tool(
            "submit_report",
            {"task_id": migrated_task_two, "delegation_id": second_delegation, "report_type": "result", "status": "completed", "content": "Second migrated task report.", "idempotency_key": "legacy-migration-second-report"},
        ))
        first_snapshot = legacy_server.tool("inspect_task", {"task_id": migrated_task_one})
        second_snapshot = legacy_server.tool("inspect_task", {"task_id": migrated_task_two})
        require(
            len({legacy_report_id, first_report, second_report}) == 3
            and [item.get("report_id") for item in first_snapshot.get("reports", []) if isinstance(item, Mapping)] == [first_report]
            and [item.get("report_id") for item in second_snapshot.get("reports", []) if isinstance(item, Mapping)] == [second_report],
            "separate migrated task/report IDs cannot cross-contaminate",
        )
    finally:
        legacy_server.close()
    with sqlite3.connect(legacy_database) as connection:
        migrations = connection.execute("SELECT version,name FROM schema_migrations ORDER BY version").fetchall()
        schema_version = connection.execute("PRAGMA user_version").fetchone()
        migrated_chunk = connection.execute("SELECT chunk_index,section,content_json,content_digest,content_bytes FROM report_chunks WHERE report_id=?", (legacy_report_id,)).fetchone()
        migrated_header = connection.execute("SELECT assembly_state,next_chunk_index,total_chunks,content_digest FROM reports WHERE report_id=?", (legacy_report_id,)).fetchone()
        migrated_root = connection.execute("SELECT project_root FROM tasks WHERE task_id=?", (legacy_task_id,)).fetchone()
        migrated_profile = connection.execute("SELECT profile_name FROM delegations WHERE delegation_id=?", (legacy_delegation_id,)).fetchone()
        migrated_task_count = connection.execute("SELECT COUNT(*) FROM tasks").fetchone()
        integrity = connection.execute("PRAGMA integrity_check").fetchone()
    require(
        migrations == [
            (1, "v12-initial"),
            (2, "v12-schema-v1-human-views"),
            (3, "v12-explicit-profile-binding"),
            (4, "v12-durable-native-task-name"),
            (5, "v12-report-consumption-receipts"),
            (6, "v12-durable-governance-gate"),
            (7, "v12-ready-approval-handles"),
        ],
        "legacy V12 shard records each additive V12 migration exactly once",
    )
    require(schema_version == (1,), "additive human-view migration preserves the public V12 schema-version value")
    require(
        migrated_chunk is not None
        and migrated_chunk[0] == 0
        and migrated_chunk[1] == "body"
        and json.loads(str(migrated_chunk[2])) == legacy_content
        and isinstance(migrated_chunk[3], str)
        and re.fullmatch(r"sha256:[0-9a-f]{64}", str(migrated_chunk[3])) is not None
        and int(migrated_chunk[4]) > 0
        and migrated_header is not None
        and migrated_header[:3] == ("finalized", 1, 1)
        and isinstance(migrated_header[3], str)
        and re.fullmatch(r"sha256:[0-9a-f]{64}", str(migrated_header[3])) is not None
        and integrity == ("ok",),
        "migration atomically persists an immutable one-chunk manifest and leaves the V12 ledger valid",
    )
    require(
        migrated_root == (legacy_project_root,)
        and migrated_profile == ("general",)
        and migrated_task_count == (3,)
        and stat.S_IMODE(legacy_database.stat().st_mode) == 0o600
        and not (legacy_project / ".codex").exists(),
        "automatic migration preserves legacy rows, binds their verified root, and never writes in the project root",
    )

    # Two independent normal MCP callers racing to open the exact older V12
    # layout must serialize the automatic transaction, not return
    # schema_unsupported or a SQLite lock failure.
    concurrent_home = tmp_path / "legacy-concurrent-home"
    concurrent_home.mkdir()
    concurrent_project = tmp_path / "legacy-concurrent-project"
    concurrent_project.mkdir()
    concurrent_legacy = _seed_known_pre_human_views_v12_shard(home=concurrent_home, project=concurrent_project)
    concurrent_barrier = threading.Barrier(2)

    def concurrent_legacy_open(index: int) -> str:
        child = McpServer(entrypoint=entrypoint, cwd=concurrent_project, env=_runtime_environment(concurrent_home))
        try:
            concurrent_barrier.wait(timeout=20)
            return _task_id(child.tool(
                "create_task",
                _task_payload(
                    str(concurrent_legacy["project_root"]),
                    objective=f"Concurrent legacy migration task {index}.",
                    key=f"legacy-concurrent-{index}",
                ),
            ))
        finally:
            child.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        concurrent_task_ids = list(executor.map(concurrent_legacy_open, range(2)))
    require(len(set(concurrent_task_ids)) == 2, "concurrent automatic legacy opens create distinct task IDs without failures")
    with sqlite3.connect(Path(concurrent_legacy["database"])) as connection:
        concurrent_migrations = connection.execute("SELECT version,name FROM schema_migrations ORDER BY version").fetchall()
        concurrent_task_count = connection.execute("SELECT COUNT(*) FROM tasks").fetchone()
        concurrent_integrity = connection.execute("PRAGMA integrity_check").fetchone()
    require(
        concurrent_migrations == [
            (1, "v12-initial"),
            (2, "v12-schema-v1-human-views"),
            (3, "v12-explicit-profile-binding"),
            (4, "v12-durable-native-task-name"),
            (5, "v12-report-consumption-receipts"),
            (6, "v12-durable-governance-gate"),
            (7, "v12-ready-approval-handles"),
        ]
        and concurrent_task_count == (3,)
        and concurrent_integrity == ("ok",)
        and not (concurrent_project / ".codex").exists(),
        "concurrent first opens apply one migration without loss or project-local writes",
    )

    # A corrupt legacy report fails only after migration DDL begins.  The
    # explicit transaction rollback must leave the old schema fully intact.
    rollback_home = tmp_path / "legacy-rollback-home"
    rollback_home.mkdir()
    rollback_project = tmp_path / "legacy-rollback-project"
    rollback_project.mkdir()
    rollback_legacy = _seed_known_pre_human_views_v12_shard(
        home=rollback_home, project=rollback_project, report_content_json="{invalid-json",
    )
    rollback_server = McpServer(entrypoint=entrypoint, cwd=rollback_project, env=_runtime_environment(rollback_home))
    try:
        rollback_failure = rollback_server.tool_rpc_error(
            "create_task",
            _task_payload(str(rollback_legacy["project_root"]), objective="Rollback migration fault.", key="legacy-rollback"),
            cortex_code="ledger_corrupt",
        )
    finally:
        rollback_server.close()
    with sqlite3.connect(Path(rollback_legacy["database"])) as connection:
        rollback_migrations = connection.execute("SELECT version,name FROM schema_migrations ORDER BY version").fetchall()
        rollback_columns = [row[1] for row in connection.execute("PRAGMA table_info(tasks)")]
        rollback_chunks = connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='report_chunks'").fetchall()
        rollback_task_count = connection.execute("SELECT COUNT(*) FROM tasks").fetchone()
        rollback_integrity = connection.execute("PRAGMA integrity_check").fetchone()
    require(
        rollback_failure.get("data") == {"cortex_code": "ledger_corrupt"}
        and "invalid-json" not in json.dumps(rollback_failure, ensure_ascii=False)
        and rollback_migrations == [(1, "v12-initial")]
        and "project_root" not in rollback_columns
        and rollback_chunks == []
        and rollback_task_count == (1,)
        and rollback_integrity == ("ok",),
        "faulted automatic migration rolls back all DDL and returns only sanitized data",
    )

    # A nearby-looking but unknown layout must remain fail-closed and avoid
    # creating even one expansion object.
    unsupported_home = tmp_path / "legacy-unsupported-home"
    unsupported_home.mkdir()
    unsupported_project = tmp_path / "legacy-unsupported-project"
    unsupported_project.mkdir()
    unsupported_legacy = _seed_known_pre_human_views_v12_shard(home=unsupported_home, project=unsupported_project)
    with sqlite3.connect(Path(unsupported_legacy["database"])) as connection:
        connection.execute("CREATE TABLE unrecognized_v12_extension(marker TEXT)")
        connection.commit()
    unsupported_server = McpServer(entrypoint=entrypoint, cwd=unsupported_project, env=_runtime_environment(unsupported_home))
    try:
        unsupported_failure = unsupported_server.tool_rpc_error(
            "create_task",
            _task_payload(str(unsupported_legacy["project_root"]), objective="Unknown V12 layout must remain closed.", key="legacy-unsupported"),
            cortex_code="schema_unsupported",
        )
    finally:
        unsupported_server.close()
    with sqlite3.connect(Path(unsupported_legacy["database"])) as connection:
        unsupported_migrations = connection.execute("SELECT version,name FROM schema_migrations ORDER BY version").fetchall()
        unsupported_chunks = connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='report_chunks'").fetchall()
    require(
        unsupported_failure.get("data") == {"cortex_code": "schema_unsupported"}
        and "unrecognized_v12_extension" not in json.dumps(unsupported_failure, ensure_ascii=False)
        and unsupported_migrations == [(1, "v12-initial")]
        and unsupported_chunks == [],
        "unknown V12 layouts fail closed as a sanitized JSON-RPC server-state error",
    )

    # The maintenance surface is deliberately a closed local CLI, never an
    # eleventh-plus MCP escape hatch.  Invalid/V11-looking anchors must fail
    # before even opening or creating a host-state path.
    unopened_home = tmp_path / "maintenance-unopened-home"
    for invalid_anchor in ("task-v11-sentinel", "not-a-v12-task"):
        invalid_process, invalid_result = maintenance_cli(unopened_home, "health", "--task-id", invalid_anchor)
        require(
            invalid_process.returncode == 1
            and invalid_result == {"code": "maintenance_task_id_invalid", "ok": False},
            "maintenance rejects malformed and V11-looking task anchors with a sanitized failure",
        )
    syntactically_valid_unknown = "task-" + ("0" * 64) + "-" + ("1" * 32)
    unknown_process, unknown_result = maintenance_cli(
        unopened_home, "health", "--task-id", syntactically_valid_unknown,
    )
    require(
        unknown_process.returncode == 1
        and unknown_result == {"code": "maintenance_storage_unavailable", "ok": False}
        and not unopened_home.exists(),
        "unknown valid-shape anchors never bootstrap a home, project, or ledger path",
    )
    unknown_argument, unknown_payload = maintenance_cli(
        home, "health", "--task-id", task_a, "--project-root", "/untrusted-path",
    )
    require(
        unknown_argument.returncode == 2 and unknown_payload == {} and "unrecognized arguments" in unknown_argument.stderr,
        "maintenance CLI rejects project-root injection because its subcommands are closed",
    )

    maintenance_health_process, maintenance_health = maintenance_cli(home, "health", "--task-id", task_a)
    maintenance_health_text = maintenance_health_process.stdout + maintenance_health_process.stderr
    require(
        maintenance_health_process.returncode == 0
        and maintenance_health.get("ok") is True
        and maintenance_health.get("operation") == "health"
        and maintenance_health.get("task_id") == task_a
        and maintenance_health.get("healthy") is True
        and canonical_project_a not in maintenance_health_text
        and str(shard_root) not in maintenance_health_text,
        "read-only maintenance health reports only bounded status and no private filesystem path",
    )

    maintenance_database = shard_root / "cortex.db"
    os.chmod(maintenance_database, 0o644)
    insecure_mode_process, insecure_mode = maintenance_cli(home, "health", "--task-id", task_a)
    require(
        insecure_mode_process.returncode == 1
        and insecure_mode == {"code": "maintenance_storage_unsafe", "ok": False},
        "maintenance rejects a non-owner-only V12 database before SQLite opens it",
    )
    os.chmod(maintenance_database, 0o600)

    unsafe_home = tmp_path / "maintenance-unsafe-home"
    unsafe_root = unsafe_home / ".codex" / "cortex" / "v12" / "projects" / f"p-{task_id_match.group(1)}"
    unsafe_root.mkdir(parents=True)
    for directory in (
        unsafe_home / ".codex" / "cortex" / "v12",
        unsafe_home / ".codex" / "cortex" / "v12" / "projects",
        unsafe_root,
    ):
        os.chmod(directory, 0o700)
    unsafe_target = tmp_path / "maintenance-external-target.bin"
    unsafe_target.write_bytes(b"must remain untouched")
    os.chmod(unsafe_target, 0o600)
    unsafe_target_digest = hashlib.sha256(unsafe_target.read_bytes()).hexdigest()
    unsafe_database = unsafe_root / "cortex.db"
    unsafe_database.symlink_to(unsafe_target)
    symlink_maintenance_process, symlink_maintenance = maintenance_cli(unsafe_home, "health", "--task-id", task_a)
    require(
        symlink_maintenance_process.returncode == 1
        and symlink_maintenance == {"code": "maintenance_storage_unsafe", "ok": False}
        and hashlib.sha256(unsafe_target.read_bytes()).hexdigest() == unsafe_target_digest,
        "maintenance rejects a symlinked ledger without opening or changing its target",
    )
    unsafe_database.unlink()
    os.mkfifo(unsafe_database, 0o600)
    os.chmod(unsafe_database, 0o600)
    nonregular_process, nonregular_result = maintenance_cli(unsafe_home, "health", "--task-id", task_a)
    require(
        nonregular_process.returncode == 1
        and nonregular_result == {"code": "maintenance_storage_unsafe", "ok": False},
        "maintenance rejects non-regular ledger nodes before SQLite access",
    )

    backup_id = "backup-maintenance-primary"
    backup_process, backup_result = maintenance_cli(
        home, "backup", "--task-id", task_a, "--confirm-action", "BACKUP", "--backup-id", backup_id,
    )
    backup_bundle = shard_root / "backups" / task_a / backup_id
    backup_database = backup_bundle / "cortex.db"
    backup_manifest = backup_bundle / "manifest.json"
    require(
        backup_process.returncode == 0
        and backup_result.get("ok") is True
        and backup_result.get("operation") == "backup"
        and backup_result.get("backup_scope") == "project_shard"
        and backup_result.get("anchor_task_id") == task_a
        and backup_result.get("backup_id") == backup_id
        and canonical_project_a not in backup_process.stdout
        and str(shard_root) not in backup_process.stdout,
        "explicit backup returns a sanitized project-shard receipt anchored to exactly one task",
    )
    backup_metadata = json.loads(backup_manifest.read_text(encoding="utf-8"))
    require(
        backup_bundle.is_dir()
        and backup_database.is_file()
        and backup_manifest.is_file()
        and stat.S_IMODE((shard_root / "backups").stat().st_mode) == 0o700
        and stat.S_IMODE((shard_root / "backups" / task_a).stat().st_mode) == 0o700
        and stat.S_IMODE(backup_bundle.stat().st_mode) == 0o700
        and stat.S_IMODE(backup_database.stat().st_mode) == 0o600
        and stat.S_IMODE(backup_manifest.stat().st_mode) == 0o600
        and backup_metadata.get("format") == "cortex/v12-maintenance-backup/v1"
        and backup_metadata.get("state") == "complete"
        and backup_metadata.get("backup_id") == backup_id
        and backup_metadata.get("anchor_task_id") == task_a
        and backup_metadata.get("project_hash") == task_id_match.group(1)
        and backup_metadata.get("database_sha256") == "sha256:" + hashlib.sha256(backup_database.read_bytes()).hexdigest()
        and int(backup_metadata.get("database_bytes") or 0) == backup_database.stat().st_size,
        "SQLite online backup emits an owner-private database bundle and sealed manifest",
    )
    with sqlite3.connect(backup_database) as connection:
        backup_integrity = connection.execute("PRAGMA integrity_check").fetchone()
    require(backup_integrity == ("ok",), "maintenance backup is an independently valid SQLite copy")

    missing_confirmation, missing_confirmation_payload = maintenance_cli(home, "checkpoint", "--task-id", task_a)
    require(
        missing_confirmation.returncode == 2 and missing_confirmation_payload == {},
        "write-capable maintenance commands require explicit parser-level confirmation",
    )
    checkpoint_process, checkpoint_result = maintenance_cli(
        home, "checkpoint", "--task-id", task_a, "--confirm-action", "CHECKPOINT", "--mode", "FULL",
    )
    optimize_process, optimize_result = maintenance_cli(
        home, "optimize", "--task-id", task_a, "--confirm-action", "OPTIMIZE",
    )
    vacuum_process, vacuum_result = maintenance_cli(
        home, "vacuum", "--task-id", task_a, "--confirm-action", "VACUUM",
    )
    require(
        checkpoint_process.returncode == 0
        and checkpoint_result.get("ok") is True
        and checkpoint_result.get("operation") == "checkpoint"
        and checkpoint_result.get("mode") == "FULL"
        and optimize_process.returncode == 0
        and optimize_result == {"ok": True, "operation": "optimize", "task_id": task_a}
        and vacuum_process.returncode == 0
        and vacuum_result.get("ok") is True
        and vacuum_result.get("operation") == "vacuum",
        "checkpoint, optimize, and vacuum run only after their distinct explicit confirmations",
    )

    restore_mismatch_process, restore_mismatch = maintenance_cli(
        home,
        "restore", "--task-id", task_a, "--backup-id", backup_id, "--confirm-action", "RESTORE",
        "--confirm-task-id", task_a, "--confirm-shard", "p-" + ("0" * 64),
        "--confirm-service-stopped", "MCP_STOPPED",
    )
    require(
        restore_mismatch_process.returncode == 1
        and restore_mismatch == {"code": "maintenance_confirmation_mismatch", "ok": False},
        "restore rejects a mismatched task/shard acknowledgement before mutating the ledger",
    )
    restore_process, restore_result = maintenance_cli(
        home,
        "restore", "--task-id", task_a, "--backup-id", backup_id, "--confirm-action", "RESTORE",
        "--confirm-task-id", task_a, "--confirm-shard", f"p-{task_id_match.group(1)}",
        "--confirm-service-stopped", "MCP_STOPPED",
    )
    recovery_backup_id = restore_result.get("recovery_backup_id")
    require(
        restore_process.returncode == 0
        and restore_result.get("ok") is True
        and restore_result.get("operation") == "restore"
        and restore_result.get("restored_backup_id") == backup_id
        and isinstance(recovery_backup_id, str)
        and recovery_backup_id.startswith("backup-")
        and (shard_root / "backups" / task_a / recovery_backup_id / "cortex.db").is_file(),
        "restore requires RESTORE, exact task/shard, MCP_STOPPED, and first creates a recovery backup",
    )

    require(altered_report_view.read_bytes() == altered_bytes, "maintenance work does not overwrite a conflicting derived Markdown artifact")
    altered_report_view.unlink()
    regenerate_process, regenerate_result = maintenance_cli(
        home, "projection-regenerate", "--task-id", task_a, "--confirm-action", "REGENERATE_PROJECTIONS",
    )
    require(
        regenerate_process.returncode == 0
        and regenerate_result.get("ok") is True
        and regenerate_result.get("operation") == "projection-regenerate"
        and int(regenerate_result.get("rendered_count") or 0) > 0
        and altered_report_view.is_file()
        and stat.S_IMODE(altered_report_view.stat().st_mode) == 0o600,
        "explicit regeneration repairs a missing registered projection from canonical ledger state",
    )
    unmanaged_projection = task_views / "unmanaged.md"
    unmanaged_projection.write_text("This host-private file was never registered by Cortex.\n", encoding="utf-8")
    os.chmod(unmanaged_projection, 0o600)
    prune_dry_process, prune_dry = maintenance_cli(home, "projection-prune", "--task-id", task_a, "--dry-run")
    prune_missing_confirmation_process, prune_missing_confirmation = maintenance_cli(home, "projection-prune", "--task-id", task_a, "--apply")
    prune_apply_process, prune_apply = maintenance_cli(
        home, "projection-prune", "--task-id", task_a, "--apply", "--confirm-action", "PRUNE_PROJECTIONS",
    )
    require(
        prune_dry_process.returncode == 0
        and prune_dry.get("ok") is True
        and prune_dry.get("dry_run") is True
        and prune_dry.get("applied") is False
        and prune_missing_confirmation_process.returncode == 1
        and prune_missing_confirmation == {"code": "maintenance_confirmation_required", "ok": False}
        and prune_apply_process.returncode == 0
        and prune_apply.get("ok") is True
        and prune_apply.get("applied") is True
        and unmanaged_projection.read_text(encoding="utf-8").startswith("This host-private file")
        and (task_views / "task.md").is_file(),
        "projection prune defaults to dry-run and an explicit apply never walks or removes unregistered task files",
    )

    retention_dry_process, retention_dry = maintenance_cli(
        home, "retention", "--task-id", task_a, "--dry-run", "--backup-id", backup_id,
    )
    retention_missing_confirmation_process, retention_missing_confirmation = maintenance_cli(
        home, "retention", "--task-id", task_a, "--apply", "--backup-id", recovery_backup_id,
    )
    retention_apply_process, retention_apply = maintenance_cli(
        home, "retention", "--task-id", task_a, "--apply", "--backup-id", recovery_backup_id,
        "--confirm-action", "RETENTION",
    )
    require(
        retention_dry_process.returncode == 0
        and retention_dry.get("ok") is True
        and retention_dry.get("backup_scope") == "project_shard"
        and retention_dry.get("eligible_count") == 1
        and retention_dry.get("canonical_data_retained") is True
        and retention_missing_confirmation_process.returncode == 1
        and retention_missing_confirmation == {"code": "maintenance_confirmation_required", "ok": False}
        and retention_apply_process.returncode == 0
        and retention_apply.get("ok") is True
        and retention_apply.get("removed_count") == 1
        and retention_apply.get("canonical_data_retained") is True
        and backup_bundle.is_dir()
        and not (shard_root / "backups" / task_a / recovery_backup_id).exists(),
            "retention names exact sealed backup IDs, defaults to dry-run, and never prunes canonical ledger data",
    )
    for relative, digest in v11_local_digests.items():
        path = project_a / relative
        require(path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == digest, "maintenance never changes project-local V11 artifacts")
    require(hashlib.sha256(v11_database.read_bytes()).hexdigest() == v11_digest_before, "maintenance never opens or changes the V11 host database")
    require(not (project_b / ".codex").exists() and not (legacy_project / ".codex").exists(), "maintenance never creates any project-local Cortex state")

    sys.path.insert(0, str(scripts))
    from cortex_runtime.model_routing import native_spawn_arguments

    for model in MODELS:
        for effort in EFFORTS:
            native = native_spawn_arguments(model=model, reasoning_effort=effort, task_name="v12-model-transport", message="Use selected model and effort exactly.")
            require(native.get("reasoning_effort") == effort, f"{model}/{effort} preserves effort")
            require(native.get("fork_turns") == "none", f"{model}/{effort} retains isolated native dispatch")
            if model == "gpt-5.6-luna":
                require("model" not in native, f"{model}/{effort} omits default-model override")
            else:
                require(native.get("model") == model, f"{model}/{effort} carries explicit native model")
    for bad_model, bad_effort in (("gpt-5.6-unknown", "high"), ("gpt-5.6-luna", "ultra")):
        try:
            native_spawn_arguments(model=bad_model, reasoning_effort=bad_effort, task_name="invalid-model-effort", message="This must reject without fallback.")
        except ValueError:
            pass
        else:
            raise AssertionError("invalid model/effort must reject without server fallback")


def test_v12_production_task_acceptance_reconciles_live_task_failures(tmp_path: Path) -> None:
    """Exercise the public eleven-tool ledger as one realistic worker-owned task.

    This is deliberately not a service-unit fixture.  It starts the packaged
    stdio MCP process, then reconciles the public receipts against canonical
    SQLite and the host-private projections.  The scenario is the repaired
    counterpart of the observed live task: four complete task-contract lists,
    four worker delegations with the six-part knowledge contract, report and
    decision handoff, material documentation evidence, and a task-linked
    initiative closure.
    """
    source_repository = Path(__file__).resolve().parents[1]
    support_scripts = source_repository / "scripts"
    if str(support_scripts) not in sys.path:
        sys.path.insert(0, str(support_scripts))
    from cortex_release_candidate import build_source_candidate, validate_candidate_tree

    repository = tmp_path / "production-candidate"
    manifest = build_source_candidate(source_repository, repository)
    validate_candidate_tree(repository, manifest)
    entrypoint = repository / "plugins" / "cortex" / "scripts" / "cortex.py"

    home = tmp_path / "production-home"
    project = tmp_path / "production-project"
    home.mkdir()
    project.mkdir()
    v11_database = home / ".codex" / "cortex" / "projects" / "p-v11-production-sentinel" / "cortex.db"
    v11_database.parent.mkdir(parents=True)
    v11_database.write_bytes(b"V11 production sentinel must remain byte-identical")
    v11_digest = hashlib.sha256(v11_database.read_bytes()).hexdigest()

    server = McpServer(entrypoint=entrypoint, cwd=project, env=_runtime_environment(home))
    try:
        _assert_tool_schemas(_list_tools(server))
        task_payload = {
            "project_root": str(project),
            "objective": "Deliver an English-normalized V12 production acceptance record.",
            "user_request_original": "Проверьте готовность координационного журнала к выпуску.",
            "user_language": "ru",
            "task_contract_version": "cortex/task-contract/v1",
            "requirements": ["Preserve public MCP compatibility.", "Use worker-owned durable evidence."],
            "constraints": ["Never write Cortex state below project_root."],
            "acceptance_criteria": ["Every semantic mutation has canonical task-scoped chronology."],
            "verification_plan": ["Reconcile public receipts, SQLite rows, and host-private projections."],
            "context": {"release": "12.0.0", "scenario": "production acceptance"},
            "idempotency_key": "production-task-create",
        }
        task_receipt = server.tool("create_task", task_payload)
        task_id = _task_id(task_receipt)
        task_match = re.fullmatch(r"task-([0-9a-f]{64})-([0-9a-f]{32})", task_id)
        require(task_match is not None, "production task keeps the exact opaque shard-addressable task ID")
        task = task_receipt.get("task")
        require(
            isinstance(task, Mapping)
            and all(task.get(field) == task_payload[field] for field in ("requirements", "constraints", "acceptance_criteria", "verification_plan"))
            and all(task.get(field) for field in ("requirements", "constraints", "acceptance_criteria", "verification_plan")),
            "production task cannot retain the live-task empty contract arrays",
        )
        require(
            server.tool("create_task", task_payload).get("replayed") is True,
            "production task create replay is a receipt replay rather than a duplicate durable mutation",
        )

        before_invalid = server.tool("inspect_task", {"task_id": task_id, "limit": 100})
        invalid_scope, _ = server.tool_error(
            "create_delegation",
            {
                **_delegation_payload(task_id, model="gpt-5.6-luna", effort="high", key="production-invalid-scope"),
                "scope": {"not": "text"},
            },
        )
        require(_error_code(invalid_scope) == "validation_error", "invalid delegation shape is rejected before a durable worker artifact exists")
        invalid_closure, _ = server.tool_error(
            "submit_governance_closure",
            {
                "task_id": task_id,
                "subject_type": "task",
                "subject_id": task_id,
                "verdict": "ready",
                "initiative_status": "closed",
                "idempotency_key": "production-invalid-task-closure",
            },
        )
        require(_error_code(invalid_closure) == "validation_error", "initiative-only task closure fields are rejected before durable closure dispatch")
        after_invalid = server.tool("inspect_task", {"task_id": task_id, "limit": 100})
        require(
            after_invalid.get("delegations") == before_invalid.get("delegations")
            and after_invalid.get("timeline") == before_invalid.get("timeline"),
            "invalid public calls leave no delegation, closure, or timeline artifact",
        )

        def delegation(
            *,
            role: str,
            profile_name: str,
            objective: str,
            model: str,
            effort: str,
            key: str,
            input_report_ids: list[str] | None = None,
            input_decision_ids: list[str] | None = None,
            approval_decision_id: str | None = None,
        ) -> tuple[str, Mapping[str, Any]]:
            arguments = {
                **_delegation_payload(task_id, model=model, effort=effort, key=key, profile_name=profile_name),
                "role": role,
                "objective": objective,
                "scope": f"{role} owns only its bounded production acceptance evidence.",
                "input_report_ids": [] if input_report_ids is None else input_report_ids,
                "input_decision_ids": [] if input_decision_ids is None else input_decision_ids,
            }
            if approval_decision_id is not None:
                arguments["approval_decision_id"] = approval_decision_id
            receipt = server.tool(
                "create_delegation",
                arguments,
            )
            identifier = _delegation_id(receipt)
            brief = receipt.get("worker_brief")
            require(isinstance(brief, Mapping), "every production delegation returns a worker-owned handoff brief")
            require(
                brief.get("project_root") == str(project.resolve())
                and brief.get("instructions") == KNOWLEDGE_CONTRACT_INSTRUCTIONS,
                "every worker receives the canonical root and the exact coordinator-compiled knowledge contract",
            )
            for header in (
                "Documents to consume first:", "Applicable requirements:", "Verification contract:",
                "Ownership constraints:", "Known documentation state:", "Further documentation discovery:",
            ):
                require(header in str(brief.get("instructions") or ""), f"{role} receives knowledge-contract header: {header}")
            require(
                brief.get("model") == model and brief.get("reasoning_effort") == effort,
                "worker brief preserves the coordinator-selected logical model and effort exactly",
            )
            renderer = brief.get("renderer")
            require(
                brief.get("profile_name") == profile_name
                and isinstance(renderer, Mapping)
                and renderer.get("profile_name") == profile_name
                and renderer.get("profile_state") == "loaded"
                and re.fullmatch(r"sha256:[0-9a-f]{64}", str(renderer.get("profile_digest") or "")) is not None,
                "every normal production delegation binds an explicit packaged profile and a loaded renderer digest",
            )
            return identifier, brief

        plan_delegation, _plan_brief = delegation(
            role="planner",
            profile_name="planner",
            objective="Create a reviewed, chunked plan for the V12 release acceptance work.",
            model="gpt-5.6-luna",
            effort="high",
            key="production-plan-delegation",
        )
        plan_started = server.tool(
            "submit_report",
            {
                "task_id": task_id,
                "delegation_id": plan_delegation,
                "mode": "begin",
                "report_type": "plan",
                "review_policy": "required",
                "idempotency_key": "production-plan-begin",
            },
        )
        plan_id = _report_id(plan_started)
        plan_append_zero = server.tool(
            "submit_report",
            {
                "task_id": task_id,
                "delegation_id": plan_delegation,
                "mode": "append",
                "report_id": plan_id,
                "chunk_index": 0,
                "section": "plan.scope",
                "content": {"owner": "planner", "summary": "Delegate implementation and documentation evidence."},
                "idempotency_key": "production-plan-append-zero",
            },
        )
        plan_append_one = server.tool(
            "submit_report",
            {
                "task_id": task_id,
                "delegation_id": plan_delegation,
                "mode": "append",
                "report_id": plan_id,
                "chunk_index": 1,
                "section": "plan.verification",
                "content": {"command": "python3 -m pytest -q", "expected": "zero exit status"},
                "idempotency_key": "production-plan-append-one",
            },
        )
        plan_digest = plan_append_one.get("current_content_digest")
        require(
            isinstance(plan_digest, str) and re.fullmatch(r"sha256:[0-9a-f]{64}", plan_digest) is not None,
            "the multi-chunk plan returns one canonical manifest digest",
        )
        plan_final = server.tool(
            "submit_report",
            {
                "task_id": task_id,
                "delegation_id": plan_delegation,
                "mode": "finalize",
                "report_id": plan_id,
                "status": "completed",
                "expected_chunk_count": 2,
                "expected_content_digest": plan_digest,
                "idempotency_key": "production-plan-finalize",
            },
        )
        require(
            (plan_final.get("report") or {}).get("content_digest") == plan_digest
            and server.tool(
                "submit_report",
                {
                    "task_id": task_id,
                    "delegation_id": plan_delegation,
                    "mode": "finalize",
                    "report_id": plan_id,
                    "status": "completed",
                    "expected_chunk_count": 2,
                    "expected_content_digest": plan_digest,
                    "idempotency_key": "production-plan-finalize",
                },
            ).get("replayed") is True,
            "final plan is immutable and an identical finalize is idempotent",
        )
        primary_assessment = server.tool(
            "set_governance_mode",
            {
                "task_id": task_id,
                "mode": "full",
                "source": "model",
                "rationale": "Cross-surface release evidence requires an explicit audit trail.",
                "risk_factors": ["durable chronology", "documentation impact"],
                "idempotency_key": "production-governance-primary",
            },
        )
        require(isinstance((primary_assessment.get("assessment") or {}).get("assessment_id"), str), "primary governance write has a durable identity")
        plan_read = server.tool("read_reports", {"task_id": task_id, "report_ids": [plan_id], "max_bytes": 65_536})
        plan_record = (plan_read.get("reports") or [{}])[0]
        require(
            isinstance(plan_record, Mapping)
            and plan_record.get("content_digest") == plan_digest
            and [item.get("chunk_index") for item in plan_record.get("chunks") or [] if isinstance(item, Mapping)] == [0, 1],
                "only the report reader exposes both verified chunks in canonical order",
            )
        production_approval_binding = _approval_binding(plan_read)

        decision = server.tool(
            "record_user_decision",
            {
                "task_id": task_id,
                "subject_type": "plan",
                "subject_id": plan_id,
                "subject_digest": plan_digest,
                "decision_type": "approve",
                "prompt_en": "Approve the bounded worker plan?",
                "response_original": "Одобряю план.",
                "response_en": "I approve the plan.",
                "user_language": "ru",
                "idempotency_key": "production-plan-approval",
                **production_approval_binding,
            },
        )
        decision_id = _decision_id(decision)
        require(re.fullmatch(r"decision-[0-9a-f]{64}-[0-9a-f]{32}", decision_id) is not None, "decision IDs remain opaque sharded identifiers")

        implementation_delegation, implementation_brief = delegation(
            role="backend_dev",
            profile_name="backend_dev",
            objective="Produce worker-owned implementation evidence from the approved plan.",
            model="gpt-5.6-terra",
            effort="xhigh",
            key="production-implementation-delegation",
            input_report_ids=[plan_id],
            input_decision_ids=[decision_id],
        )
        require(
            implementation_brief.get("input_report_ids") == [plan_id]
            and implementation_brief.get("input_decision_ids") == [decision_id]
            and all("response_original" not in item for item in implementation_brief.get("input_decisions") or [] if isinstance(item, Mapping)),
            "implementation handoff preserves exact report and decision IDs while withholding original-language text from the worker",
        )
        implementation_args = {
            "task_id": task_id,
            "delegation_id": implementation_delegation,
            "report_type": "result",
            "status": "completed",
            "content": {"owner": "backend_dev", "result": "Implementation evidence is complete.", "verification": "passed"},
            "idempotency_key": "production-implementation-report",
        }
        implementation_id = _report_id(server.tool("submit_report", implementation_args))
        require(
            _report_id(server.tool("submit_report", implementation_args)) == implementation_id,
            "worker-owned implementation report replay preserves its exact opaque report ID",
        )
        qa_delegation, _qa_brief = delegation(
            role="qa_engineer",
            profile_name="qa_engineer",
            objective="Produce an independent completed result for the approved release evidence.",
            model="gpt-5.6-luna",
            effort="high",
            key="production-qa-delegation",
            input_report_ids=[plan_id, implementation_id],
            input_decision_ids=[decision_id],
            approval_decision_id=decision_id,
        )
        qa_worker_read = server.tool(
            "read_reports",
            {
                "task_id": task_id,
                "report_ids": [plan_id, implementation_id],
                "consumer_delegation_id": qa_delegation,
                "reader_kind": "worker",
                "max_bytes": 65_536,
            },
        )
        require(len(qa_worker_read.get("consumption_receipts") or []) == 2, "independent QA durably reads the approved plan and primary result")
        qa_id = _report_id(server.tool(
            "submit_report",
            {
                "task_id": task_id,
                "delegation_id": qa_delegation,
                "report_type": "result",
                "status": "completed",
                "content": {"owner": "qa_engineer", "result": "Independent QA evidence is complete."},
                "idempotency_key": "production-qa-report",
            },
        ))

        documentation_delegation, _documentation_brief = delegation(
            role="technical_writer",
            profile_name="technical_writer",
            objective="Record explicit material documentation-impact evidence from implementation evidence.",
            model="gpt-5.6-sol",
            effort="high",
            key="production-documentation-delegation",
            input_report_ids=[plan_id, implementation_id, qa_id],
            input_decision_ids=[decision_id],
            approval_decision_id=decision_id,
        )
        documentation_worker_read = server.tool(
            "read_reports",
            {
                "task_id": task_id,
                "report_ids": [plan_id, implementation_id, qa_id],
                "consumer_delegation_id": documentation_delegation,
                "reader_kind": "worker",
                "max_bytes": 65_536,
            },
        )
        require(
            len(documentation_worker_read.get("consumption_receipts") or []) == 3,
            "post-approval technical writer durably reads the approved plan plus primary and independent results",
        )
        documentation_id = _report_id(server.tool(
            "submit_report",
            {
                "task_id": task_id,
                "delegation_id": documentation_delegation,
                "report_type": "result",
                "status": "completed",
                "content": {
                    "owner": "technical_writer",
                    "documentation_impact": "material",
                    "affected_paths": ["docs/features/orchestration-ledger/index.md"],
                    "evidence": "The public timeline and projection contract changed.",
                },
                "idempotency_key": "production-documentation-impact-report",
            },
        ))
        documentation_coordinator_read = server.tool(
            "read_reports", {"task_id": task_id, "report_ids": [documentation_id], "max_bytes": 65_536},
        )
        require(
            (documentation_coordinator_read.get("reports") or [{}])[0].get("report_id") == documentation_id,
            "the coordinator reads the finalized post-approval documentation-impact report before closure",
        )

        verification_delegation, _verification_brief = delegation(
            role="build_verification",
            profile_name="build_verification",
            objective="Verify the linked implementation and documentation evidence independently.",
            model="gpt-5.6-luna",
            effort="max",
            key="production-verification-delegation",
            input_report_ids=[plan_id, implementation_id, qa_id, documentation_id],
            input_decision_ids=[decision_id],
            approval_decision_id=decision_id,
        )
        verification_id = _report_id(server.tool(
            "submit_report",
            {
                "task_id": task_id,
                "delegation_id": verification_delegation,
                "report_type": "synthesis",
                "status": "completed",
                "content": {"owner": "build_verification", "result": "All linked evidence verified.", "exit_status": 0},
                "idempotency_key": "production-verification-report",
            },
        ))

        initiative = server.tool(
            "record_initiative",
            {
                "task_id": task_id,
                "goal": "Release the repaired V12 public ledger safely.",
                "risk": "Incomplete evidence would require disclosed follow-up.",
                "status": "active",
                "linked_task_ids": [task_id],
                    "linked_report_ids": [plan_id, implementation_id, qa_id, documentation_id, verification_id],
                "notes": {"documentation_impact_report_id": documentation_id},
                "idempotency_key": "production-initiative",
            },
        )
        initiative_id = _initiative_id(initiative)
        require(re.fullmatch(r"initiative-[0-9a-f]{64}-[0-9a-f]{32}", initiative_id) is not None, "initiative IDs remain opaque sharded identifiers")
        closure = server.tool(
            "submit_governance_closure",
            {
                "task_id": task_id,
                "subject_type": "initiative",
                "subject_id": initiative_id,
                "verdict": "ready",
                "evidence": {
                    "implementation_report_id": implementation_id,
                    "qa_report_id": qa_id,
                    "documentation_impact_report_id": documentation_id,
                    "verification_report_id": verification_id,
                    "documentation_impact": "material",
                },
                "initiative_status": "closed",
                "completion_notes": "Worker-owned documentation impact and verification evidence are finalized and linked.",
                "idempotency_key": "production-initiative-closure",
            },
        )
        closure_id = (closure.get("closure") or {}).get("closure_id")
        require(isinstance(closure_id, str) and closure_id.startswith("closure-"), "initiative closure has one opaque durable identity")
        task_closure = server.tool(
            "submit_governance_closure",
            {
                "task_id": task_id,
                "subject_type": "task",
                "subject_id": task_id,
                "verdict": "ready",
                "evidence": {"initiative_closure_id": closure_id, "documentation_impact_report_id": documentation_id},
                "idempotency_key": "production-task-closure",
            },
        )
        require((task_closure.get("next_action") or {}).get("state") == "task_closed", "initiative closure is followed by its distinct task closure")

        def concurrent_governance(index: int) -> str:
            child = McpServer(entrypoint=entrypoint, cwd=project, env=_runtime_environment(home))
            try:
                receipt = child.tool(
                    "set_governance_mode",
                    {
                        "task_id": task_id,
                        "mode": "light" if index == 0 else "minimal",
                        "source": "model",
                        "rationale": f"Independent post-closure audit mutation {index}.",
                        "risk_factors": [],
                        "idempotency_key": f"production-concurrent-governance-{index}",
                    },
                )
                value = (receipt.get("assessment") or {}).get("assessment_id")
                require(isinstance(value, str), "concurrent governance mutation returns a durable assessment ID")
                return value
            finally:
                child.close()

        with ThreadPoolExecutor(max_workers=2) as executor:
            concurrent_assessments = list(executor.map(concurrent_governance, range(2)))
        require(len(set(concurrent_assessments)) == 2, "concurrent semantic writes cannot collapse or lose governance evidence")

        profile_task = _task_id(server.tool(
            "create_task",
            {
                **task_payload,
                "objective": "Validate every packaged worker profile without granting profile-selected authority.",
                "user_request_original": "Validate the packaged profile catalogue.",
                "user_language": "en",
                "idempotency_key": "production-profile-catalogue-task",
            },
        ))
        profile_before = server.tool("inspect_task", {"task_id": profile_task, "limit": 100})
        missing_profile, _ = server.tool_error(
            "create_delegation",
            {
                key: value
                for key, value in _delegation_payload(
                    profile_task,
                    model="gpt-5.6-luna",
                    effort="high",
                    key="production-profile-missing",
                ).items()
                if key != "profile_name"
            },
        )
        unknown_profile, _ = server.tool_error(
            "create_delegation",
            {
                **_delegation_payload(
                    profile_task,
                    model="gpt-5.6-luna",
                    effort="high",
                    key="production-profile-unknown",
                    profile_name="unpackaged_profile",
                ),
                "role": "Free-form human reviewer title",
            },
        )
        profile_after_rejection = server.tool("inspect_task", {"task_id": profile_task, "limit": 100})
        require(
            _error_code(missing_profile) == "validation_error"
            and _error_code(unknown_profile) == "validation_error"
            and profile_after_rejection.get("delegations") == profile_before.get("delegations")
            and profile_after_rejection.get("timeline") == profile_before.get("timeline"),
            "missing or unavailable packaged profiles fail before a durable delegation, renderer, or idempotency artifact exists",
        )
        profile_digests: dict[str, str] = {}
        for index, profile_name in enumerate(EXPECTED_PROFILE_NAMES):
            profile_receipt = server.tool(
                "create_delegation",
                {
                    **_delegation_payload(
                        profile_task,
                        model="gpt-5.6-luna",
                        effort="high",
                        key=f"production-profile-{index}",
                        profile_name=profile_name,
                    ),
                    "role": f"Human release evidence role {index}",
                    "objective": f"Render packaged profile {profile_name} for bounded evidence acceptance.",
                },
            )
            profile_delegation = profile_receipt.get("delegation") or {}
            profile_brief = profile_receipt.get("worker_brief") or {}
            profile_renderer = profile_brief.get("renderer") if isinstance(profile_brief, Mapping) else None
            require(
                profile_delegation.get("role") == f"Human release evidence role {index}"
                and profile_delegation.get("profile_name") == profile_name
                and isinstance(profile_renderer, Mapping)
                and profile_renderer.get("profile_name") == profile_name
                and profile_renderer.get("profile_state") == "loaded"
                and re.fullmatch(r"sha256:[0-9a-f]{64}", str(profile_renderer.get("profile_digest") or "")) is not None,
                "a free-form human role remains separate from one loaded packaged profile with an attested digest",
            )
            profile_digests[profile_name] = str(profile_renderer["profile_digest"])
        require(
            tuple(profile_digests) == EXPECTED_PROFILE_NAMES and len(set(profile_digests.values())) == len(EXPECTED_PROFILE_NAMES),
            "all twenty-two exact packaged profiles load successfully with distinct renderer attestations",
        )

        task_snapshot = server.tool("inspect_task", {"task_id": task_id, "after_sequence": 0, "limit": 100})
        governance_snapshot = server.tool("inspect_governance", {"task_id": task_id, "after_sequence": 0, "limit": 100})
        require(
            any(item.get("initiative_id") == initiative_id for item in governance_snapshot.get("initiatives") or [] if isinstance(item, Mapping))
            and any(
                item.get("closure_id") == closure_id and item.get("subject_id") == initiative_id
                for item in governance_snapshot.get("closures") or [] if isinstance(item, Mapping)
            ),
            "task-scoped governance exposes the final initiative and its closure rather than report-only orphan state",
        )
        links = governance_snapshot.get("links") or []
        exact_links = {
            (item.get("relationship"), item.get("target_id"))
            for item in links
            if isinstance(item, Mapping) and item.get("initiative_id") == initiative_id
        }
        require(
            exact_links >= {("task", task_id), *( ("report", value) for value in (plan_id, implementation_id, documentation_id, verification_id) )},
            "final initiative links the exact task and every finalized worker-owned report, including documentation impact",
        )
    finally:
        server.close()

    shard_root = home / ".codex" / "cortex" / "v12" / "projects" / f"p-{task_match.group(1)}"
    database = shard_root / "cortex.db"
    with sqlite3.connect(database) as connection:
        table_counts = {
            "tasks": connection.execute("SELECT COUNT(*) FROM tasks WHERE task_id=?", (task_id,)).fetchone()[0],
            "delegations": connection.execute("SELECT COUNT(*) FROM delegations WHERE task_id=?", (task_id,)).fetchone()[0],
            "reports": connection.execute("SELECT COUNT(*) FROM reports WHERE task_id=?", (task_id,)).fetchone()[0],
            "chunks": connection.execute("SELECT COUNT(*) FROM report_chunks c JOIN reports r ON r.report_id=c.report_id WHERE r.task_id=?", (task_id,)).fetchone()[0],
            "decisions": connection.execute("SELECT COUNT(*) FROM user_decisions WHERE task_id=?", (task_id,)).fetchone()[0],
            "assessments": connection.execute("SELECT COUNT(*) FROM governance_assessments WHERE task_id=?", (task_id,)).fetchone()[0],
            "initiatives": connection.execute("SELECT COUNT(*) FROM initiatives WHERE initiative_id=?", (initiative_id,)).fetchone()[0],
            "initiative_revisions": connection.execute("SELECT COUNT(*) FROM initiative_revisions WHERE initiative_id=?", (initiative_id,)).fetchone()[0],
            "closures": connection.execute("SELECT COUNT(*) FROM governance_closures WHERE project_hash=?", (task_match.group(1),)).fetchone()[0],
        }
        timeline_rows = connection.execute(
            "SELECT sequence,event_type,entity_id,task_id,delegation_id,report_id,initiative_id,closure_id,payload_json FROM timeline WHERE task_id=? ORDER BY sequence",
            (task_id,),
        ).fetchall()
        report_rows = connection.execute(
            "SELECT report_id,delegation_id,task_id,assembly_state,total_chunks,content_digest FROM reports WHERE task_id=? ORDER BY created_sequence",
            (task_id,),
        ).fetchall()
        link_rows = connection.execute(
            "SELECT relationship,target_id,is_resolved FROM initiative_links WHERE initiative_id=? ORDER BY relationship,target_id",
            (initiative_id,),
        ).fetchall()
        integrity = connection.execute("PRAGMA integrity_check").fetchone()
    require(
        table_counts == {
            "tasks": 1, "delegations": 5, "reports": 5, "chunks": 6, "decisions": 1,
            "assessments": 3, "initiatives": 1, "initiative_revisions": 2, "closures": 2,
        },
        "production task has exhaustive canonical entity counts rather than the broken live task's partial ledger",
    )
    require(
        integrity == ("ok",)
        and [(row[0], row[1]) for row in report_rows] == [
            (plan_id, plan_delegation), (implementation_id, implementation_delegation),
            (qa_id, qa_delegation), (documentation_id, documentation_delegation),
            (verification_id, verification_delegation),
        ]
        and all(row[2] == task_id and row[3] == "finalized" and isinstance(row[5], str) and re.fullmatch(r"sha256:[0-9a-f]{64}", row[5]) for row in report_rows)
        and [row[4] for row in report_rows] == [2, 1, 1, 1, 1],
        "every durable report has exact task/delegation ownership, terminal state, chunk count, and canonical digest",
    )
    expected_prefix = [
        "task_created", "delegation_created", "report_started", "report_chunk_appended", "report_chunk_appended", "report_submitted",
        "governance_mode_set", "report_read", "user_decision_recorded", "delegation_created", "report_submitted",
        "delegation_created", "report_read", "report_read", "report_submitted", "delegation_created",
        "report_read", "report_read", "report_read", "report_submitted", "report_read", "delegation_created",
        "report_submitted", "initiative_created", "initiative_revised_by_closure", "governance_closure_submitted",
        "governance_closure_submitted",
    ]
    event_types = [str(row[1]) for row in timeline_rows]
    sequences = [int(row[0]) for row in timeline_rows]
    require(
        event_types[: len(expected_prefix)] == expected_prefix
        and event_types[len(expected_prefix):] == ["governance_mode_set", "governance_mode_set"]
        and len(timeline_rows) == len(expected_prefix) + 2
        and sequences == sorted(sequences)
        and len(sequences) == len(set(sequences))
        and all(row[3] == task_id for row in timeline_rows),
        "every successful production mutation has one ordered task-scoped canonical timeline event, including chunks, report-read receipts, and two-stage closure",
    )
    require(
        all(not json.loads(str(row[8])).get("backfill") for row in timeline_rows)
        and {(row[0], row[1], bool(row[2])) for row in link_rows}
        >= {("task", task_id, True), *( ("report", value, True) for value in (plan_id, implementation_id, qa_id, documentation_id, verification_id) )},
        "new production writes need no repair marker and retain exact resolved initiative links",
    )

    task_views = shard_root / "tasks" / f"t_{task_match.group(2)[-12:]}"
    timeline_latest, pages = _markdown_timeline_index(task_views / "timeline" / "index.md")
    index_text = (task_views / "index.md").read_text(encoding="utf-8")
    initiative_marker = initiative_id
    require(
        isinstance(pages, list)
        and timeline_latest == sequences[-1]
        and sum(
            len(_markdown_timeline_sequences(task_views / "timeline" / str(item["path"])))
            for item in pages
            if isinstance(item, Mapping) and isinstance(item.get("path"), str)
        ) == len(timeline_rows)
        and initiative_marker in index_text,
        "task projection advertises the exact final initiative and every current canonical timeline event",
    )
    projected_events: list[Mapping[str, Any]] = []
    for page in pages:
        require(isinstance(page, Mapping) and isinstance(page.get("path"), str), "timeline index uses structured server-derived page paths")
        page_path = task_views / "timeline" / str(page["path"])
        event_sequences = _markdown_timeline_sequences(page_path)
        require(event_sequences == sorted(event_sequences), "timeline Markdown page has ordered event sequence labels")
        projected_events.extend(
            {"sequence": sequence, "event_type": event_type, "entity_id": entity_id}
            for sequence, event_type, entity_id in _markdown_timeline_events(page_path)
        )
    require(
        [(item.get("sequence"), item.get("event_type"), item.get("entity_id")) for item in projected_events]
        == [(row[0], row[1], row[2]) for row in timeline_rows]
        and not (project / ".codex").exists()
        and hashlib.sha256(v11_database.read_bytes()).hexdigest() == v11_digest,
        "host-private projections match the complete canonical chronology without V11 or project-local mutation",
    )


def test_v12_public_timeline_backfill_repairs_only_unambiguous_live_shape(tmp_path: Path) -> None:
    """Repair a real-MCP 4D/4R ledger whose only surviving event is task_created."""
    source_repository = Path(__file__).resolve().parents[1]
    support_scripts = source_repository / "scripts"
    if str(support_scripts) not in sys.path:
        sys.path.insert(0, str(support_scripts))
    from cortex_release_candidate import build_source_candidate, validate_candidate_tree

    repository = tmp_path / "timeline-backfill-candidate"
    manifest = build_source_candidate(source_repository, repository)
    validate_candidate_tree(repository, manifest)
    entrypoint = repository / "plugins" / "cortex" / "scripts" / "cortex.py"
    home, project = tmp_path / "timeline-backfill-home", tmp_path / "timeline-backfill-project"
    home.mkdir()
    project.mkdir()
    v11_sentinel = home / ".codex" / "cortex" / "projects" / "p-v11-backfill-sentinel" / "cortex.db"
    v11_sentinel.parent.mkdir(parents=True)
    v11_sentinel.write_bytes(b"V11 must remain untouched by V12 timeline repair")
    v11_digest = hashlib.sha256(v11_sentinel.read_bytes()).hexdigest()

    server = McpServer(entrypoint=entrypoint, cwd=project, env=_runtime_environment(home))
    try:
        _assert_tool_schemas(_list_tools(server))
        task_id = _task_id(server.tool(
            "create_task",
            _task_payload(project, objective="Repair an old V12 task chronology without guessing task lineage.", key="backfill-task"),
        ))

        def delegation(
            *,
            profile_name: str,
            model: str,
            effort: str,
            key: str,
            objective: str,
            input_report_ids: list[str] | None = None,
            input_decision_ids: list[str] | None = None,
            approval_decision_id: str | None = None,
        ) -> str:
            arguments = {
                **_delegation_payload(task_id, model=model, effort=effort, key=key, profile_name=profile_name),
                "role": f"Legacy-shaped {profile_name} worker",
                "objective": objective,
                "input_report_ids": [] if input_report_ids is None else input_report_ids,
                "input_decision_ids": [] if input_decision_ids is None else input_decision_ids,
            }
            if approval_decision_id is not None:
                arguments["approval_decision_id"] = approval_decision_id
            return _delegation_id(server.tool(
                "create_delegation",
                arguments,
            ))

        plan_delegation = delegation(
            profile_name="planner", model="gpt-5.6-luna", effort="high", key="backfill-d1",
            objective="Produce one finalized plan report for durable repair evidence.",
        )
        plan = server.tool(
            "submit_report",
            {
                "task_id": task_id, "delegation_id": plan_delegation, "report_type": "plan", "status": "completed",
                "content": {"owner": "planner", "summary": "Repair the retained V12 chronology."}, "review_policy": "informational",
                "idempotency_key": "backfill-r1",
            },
        )
        plan_id = _report_id(plan)
        plan_digest = (plan.get("report") or {}).get("content_digest")
        require(isinstance(plan_digest, str), "legacy-shaped plan has a durable canonical digest")
        implementation_delegation = delegation(
            profile_name="backend_dev", model="gpt-5.6-terra", effort="high", key="backfill-d2",
            objective="Produce one finalized implementation report for the repaired task.",
        )
        implementation_id = _report_id(server.tool(
            "submit_report",
            {
                "task_id": task_id, "delegation_id": implementation_delegation, "report_type": "result", "status": "completed",
                "content": {"owner": "backend_dev", "result": "implementation evidence"}, "idempotency_key": "backfill-r2",
            },
        ))
        verification_delegation = delegation(
            profile_name="build_verification", model="gpt-5.6-luna", effort="max", key="backfill-d4",
            objective="Produce one streamed finalized verification report for the repaired task.",
        )
        verification_started = server.tool(
            "submit_report",
            {"task_id": task_id, "delegation_id": verification_delegation, "mode": "begin", "report_type": "synthesis", "idempotency_key": "backfill-r4-begin"},
        )
        verification_id = _report_id(verification_started)
        verification_append = server.tool(
            "submit_report",
            {
                "task_id": task_id, "delegation_id": verification_delegation, "mode": "append", "report_id": verification_id,
                "chunk_index": 0, "section": "verification.result", "content": {"owner": "build_verification", "status": "passed"},
                "idempotency_key": "backfill-r4-append",
            },
        )
        verification_digest = verification_append.get("current_content_digest")
        require(isinstance(verification_digest, str), "streamed repair fixture returns its final digest")
        server.tool(
            "submit_report",
            {
                "task_id": task_id, "delegation_id": verification_delegation, "mode": "finalize", "report_id": verification_id,
                "status": "completed", "expected_chunk_count": 1, "expected_content_digest": verification_digest,
                "idempotency_key": "backfill-r4-finalize",
            },
        )
        server.tool(
            "set_governance_mode",
            {"task_id": task_id, "mode": "full", "source": "model", "rationale": "Retained task requires an advisory repair audit.", "idempotency_key": "backfill-governance"},
        )
        backfill_approval_binding = _approval_binding(server.tool(
            "read_reports", {"task_id": task_id, "report_ids": [plan_id], "max_bytes": 65_536},
        ))
        decision_id = _decision_id(server.tool(
            "record_user_decision",
            {
                "task_id": task_id, "subject_type": "plan", "subject_id": plan_id, "subject_digest": plan_digest,
                "decision_type": "approve", "prompt_en": "Approve the durable repair plan?", "response_original": "Approve.",
                "response_en": "I approve the plan.", "user_language": "en", "idempotency_key": "backfill-decision", **backfill_approval_binding,
            },
        ))
        qa_delegation = delegation(
            profile_name="qa_engineer", model="gpt-5.6-luna", effort="high", key="backfill-d3-qa",
            objective="Produce an independent completed result for the repaired task.",
            input_report_ids=[plan_id, implementation_id],
            input_decision_ids=[decision_id],
            approval_decision_id=decision_id,
        )
        qa_worker_read = server.tool(
            "read_reports",
            {
                "task_id": task_id,
                "report_ids": [plan_id, implementation_id],
                "consumer_delegation_id": qa_delegation,
                "reader_kind": "worker",
                "max_bytes": 65_536,
            },
        )
        require(len(qa_worker_read.get("consumption_receipts") or []) == 2, "backfill QA durably reads the approved plan and primary result")
        qa_id = _report_id(server.tool(
            "submit_report",
            {
                "task_id": task_id, "delegation_id": qa_delegation, "report_type": "result", "status": "completed",
                "content": {"owner": "qa_engineer", "result": "Independent QA evidence is complete."}, "idempotency_key": "backfill-r3-qa",
            },
        ))
        documentation_delegation = delegation(
            profile_name="technical_writer", model="gpt-5.6-sol", effort="high", key="backfill-d3-post-approval",
            objective="Produce the post-approval documentation-impact report for the repaired task.",
            input_report_ids=[plan_id, implementation_id, qa_id],
            input_decision_ids=[decision_id],
            approval_decision_id=decision_id,
        )
        documentation_worker_read = server.tool(
            "read_reports",
            {
                "task_id": task_id,
                "report_ids": [plan_id, implementation_id, qa_id],
                "consumer_delegation_id": documentation_delegation,
                "reader_kind": "worker",
                "max_bytes": 65_536,
            },
        )
        require(
            len(documentation_worker_read.get("consumption_receipts") or []) == 3,
            "post-approval technical writer durably reads the approved plan plus primary and independent results",
        )
        documentation_id = _report_id(server.tool(
            "submit_report",
            {
                "task_id": task_id, "delegation_id": documentation_delegation, "report_type": "result", "status": "completed",
                "content": {"owner": "technical_writer", "documentation_impact": "no-impact", "evidence": "No user-visible document changes."},
                "idempotency_key": "backfill-r3-post-approval",
            },
        ))
        documentation_coordinator_read = server.tool(
            "read_reports", {"task_id": task_id, "report_ids": [documentation_id], "max_bytes": 65_536},
        )
        require(
            (documentation_coordinator_read.get("reports") or [{}])[0].get("report_id") == documentation_id,
            "the coordinator reads the finalized post-approval documentation-impact report before closure",
        )
        initiative_id = _initiative_id(server.tool(
            "record_initiative",
            {
                "task_id": task_id, "goal": "Retain repaired V12 evidence.", "status": "active",
                    "linked_report_ids": [plan_id, implementation_id, qa_id, documentation_id, verification_id],
                "notes": {"documentation_impact_report_id": documentation_id}, "idempotency_key": "backfill-initiative",
            },
        ))
        closure_id = (server.tool(
            "submit_governance_closure",
            {
                "task_id": task_id, "subject_type": "initiative", "subject_id": initiative_id, "verdict": "ready",
                "evidence": {"documentation_impact_report_id": documentation_id, "qa_report_id": qa_id, "verification_report_id": verification_id},
                "initiative_status": "closed", "completion_notes": "Repair evidence is finalized.", "idempotency_key": "backfill-closure",
            },
        ).get("closure") or {}).get("closure_id")
        require(isinstance(closure_id, str), "legacy-shaped fixture has an initiative closure")
        task_closure_id = (server.tool(
            "submit_governance_closure",
            {
                "task_id": task_id, "subject_type": "task", "subject_id": task_id, "verdict": "ready",
                "evidence": {"initiative_closure_id": closure_id, "documentation_impact_report_id": documentation_id},
                "idempotency_key": "backfill-task-closure",
            },
        ).get("closure") or {}).get("closure_id")
        require(isinstance(task_closure_id, str), "legacy-shaped fixture has the distinct mandatory task closure")
    finally:
        server.close()

    task_match = re.fullmatch(r"task-([0-9a-f]{64})-[0-9a-f]{32}", task_id)
    require(task_match is not None, "backfill fixture task ID is opaque and sharded")
    shard = home / ".codex" / "cortex" / "v12" / "projects" / f"p-{task_match.group(1)}"
    database = shard / "cortex.db"
    with sqlite3.connect(database) as connection:
        connection.execute("DELETE FROM timeline WHERE task_id=? AND event_type<>'task_created'", (task_id,))
        connection.execute("DELETE FROM initiative_links WHERE initiative_id=? AND relationship='task'", (initiative_id,))
        connection.execute("DELETE FROM v12_metadata WHERE key='timeline_backfill_v1'")
        connection.commit()
        surviving = connection.execute("SELECT event_type FROM timeline WHERE task_id=? ORDER BY sequence", (task_id,)).fetchall()
    require(surviving == [("task_created",)], "the test fixture exactly mirrors a live task with entities but only task_created in its chronology")

    repair_server = McpServer(entrypoint=entrypoint, cwd=project, env=_runtime_environment(home))
    try:
        repaired_task = repair_server.tool("inspect_task", {"task_id": task_id, "after_sequence": 0, "limit": 100})
        repaired_governance = repair_server.tool("inspect_governance", {"task_id": task_id, "after_sequence": 0, "limit": 100})
        require(
            any(item.get("initiative_id") == initiative_id for item in repaired_governance.get("initiatives") or [] if isinstance(item, Mapping))
            and any(item.get("closure_id") == closure_id for item in repaired_governance.get("closures") or [] if isinstance(item, Mapping)),
            "public task-scoped governance discovers the repaired report-only initiative and closure",
        )
        repair_timeline = repaired_task.get("timeline") or []
        require(
            all(isinstance(item, Mapping) and item.get("task_id") == task_id for item in repair_timeline)
            and any(item.get("event_type") == "initiative_task_link_derived" for item in repair_timeline if isinstance(item, Mapping)),
            "public inspection exposes only task-owned backfilled events including the derived task link",
        )
    finally:
        repair_server.close()

    with sqlite3.connect(database) as connection:
        repaired_rows = connection.execute(
            "SELECT sequence,event_type,task_id,initiative_id,closure_id,payload_json FROM timeline WHERE task_id=? ORDER BY sequence", (task_id,),
        ).fetchall()
        repaired_links = connection.execute(
            "SELECT relationship,target_id,is_resolved FROM initiative_links WHERE initiative_id=? ORDER BY relationship,target_id", (initiative_id,),
        ).fetchall()
        repair_marker = connection.execute("SELECT value FROM v12_metadata WHERE key='timeline_backfill_v1'").fetchone()
        integrity = connection.execute("PRAGMA integrity_check").fetchone()
    expected_events = [
        "task_created", "delegation_created", "report_submitted", "delegation_created", "report_submitted",
        "delegation_created", "report_started", "report_chunk_appended", "report_submitted",
        "governance_mode_set", "user_decision_recorded", "delegation_created", "report_submitted",
        "delegation_created", "report_submitted", "initiative_created", "initiative_revised_by_closure",
        "governance_closure_submitted", "governance_closure_submitted", "initiative_task_link_derived",
    ]
    require(
        [row[1] for row in repaired_rows] == expected_events
        and all(row[2] == task_id for row in repaired_rows)
        and all(json.loads(str(row[5])).get("backfill", {}).get("derived") is True for row in repaired_rows[1:])
        and {tuple(row) for row in repaired_links} >= {("report", documentation_id, 1), ("task", task_id, 1)}
        and repair_marker == ("cortex/v12-timeline-backfill/v1",)
        and integrity == ("ok",),
        "automatic V12 backfill restores the complete ordered 4D/4R task chronology with an exact derived task link",
    )
    repaired_sequences = [row[0] for row in repaired_rows]
    idempotent_server = McpServer(entrypoint=entrypoint, cwd=project, env=_runtime_environment(home))
    try:
        idempotent_server.tool("inspect_task", {"task_id": task_id, "after_sequence": 0, "limit": 100})
    finally:
        idempotent_server.close()
    with sqlite3.connect(database) as connection:
        repeat_sequences = [row[0] for row in connection.execute("SELECT sequence FROM timeline WHERE task_id=? ORDER BY sequence", (task_id,)).fetchall()]
    require(repeat_sequences == repaired_sequences, "a completed automatic repair is idempotent and never duplicates timeline evidence")

    ambiguity_server = McpServer(entrypoint=entrypoint, cwd=project, env=_runtime_environment(home))
    try:
        other_task = _task_id(ambiguity_server.tool(
            "create_task",
            _task_payload(project, objective="Supply an unrelated same-shard report for no-guess lineage coverage.", key="backfill-other-task"),
        ))
        other_delegation = _delegation_id(ambiguity_server.tool(
            "create_delegation",
            {
                **_delegation_payload(other_task, model="gpt-5.6-luna", effort="high", key="backfill-other-delegation", profile_name="general"),
                "objective": "Produce one unrelated report for conservative lineage repair.",
            },
        ))
        other_report = _report_id(ambiguity_server.tool(
            "submit_report",
            {
                "task_id": other_task, "delegation_id": other_delegation, "report_type": "result", "status": "completed",
                "content": {"owner": "general", "result": "unrelated same-shard evidence"}, "idempotency_key": "backfill-other-report",
            },
        ))
        ambiguous_initiative = _initiative_id(ambiguity_server.tool(
            "record_initiative",
            {
                "task_id": task_id, "goal": "Do not guess a task from cross-task report evidence.", "status": "active",
                "linked_report_ids": [implementation_id, other_report], "idempotency_key": "backfill-ambiguous-initiative",
            },
        ))
    finally:
        ambiguity_server.close()
    with sqlite3.connect(database) as connection:
        connection.execute("DELETE FROM timeline WHERE initiative_id=?", (ambiguous_initiative,))
        connection.execute("DELETE FROM initiative_links WHERE initiative_id=? AND relationship='task'", (ambiguous_initiative,))
        connection.execute("DELETE FROM v12_metadata WHERE key='timeline_backfill_v1'")
        connection.commit()
    no_guess_server = McpServer(entrypoint=entrypoint, cwd=project, env=_runtime_environment(home))
    try:
        no_guess_server.tool("inspect_task", {"task_id": task_id, "after_sequence": 0, "limit": 100})
    finally:
        no_guess_server.close()
    with sqlite3.connect(database) as connection:
        ambiguous_task_links = connection.execute(
            "SELECT target_id FROM initiative_links WHERE initiative_id=? AND relationship='task'", (ambiguous_initiative,),
        ).fetchall()
        ambiguous_warnings = connection.execute(
            "SELECT warnings_json FROM initiative_links WHERE initiative_id=? ORDER BY link_id", (ambiguous_initiative,),
        ).fetchall()
        ambiguous_events = connection.execute(
            "SELECT event_type FROM timeline WHERE initiative_id=?", (ambiguous_initiative,),
        ).fetchall()
    require(
        ambiguous_task_links == []
        and all("timeline_backfill_task_conflict" in json.loads(str(row[0])) for row in ambiguous_warnings)
        and ambiguous_events == []
        and hashlib.sha256(v11_sentinel.read_bytes()).hexdigest() == v11_digest
        and not (project / ".codex").exists(),
        "ambiguous report-only lineage records a conflict without guessing a task link, scoped chronology, V11 mutation, or project-local state",
    )
