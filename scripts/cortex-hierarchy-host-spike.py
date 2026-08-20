#!/usr/bin/env python3
"""Development-only probe for the proposed hierarchical host contract.

This module deliberately does not import the Cortex runtime.  The static path
uses a small AST/JSON inspection seam to read source-declared constants, while
the contract runner accepts an adapter supplied by a fake host.  A fake host
can prove that the *shape* of the contract is sufficient, but it cannot prove
native Desktop behaviour.  The live mode is deliberately disabled because no
environment-controlled executable is admitted or run by this Stage 00 probe.

The output is intentionally a small, deterministic JSON document.  It never
contains prompts, tokens, reports, stderr, command output, or private host
metadata.  The script is not an installation, update, or deployment tool.
"""
from __future__ import annotations

import argparse
import ast
import json
import math
import multiprocessing
from numbers import Number
from pathlib import Path
import time
from typing import Any, Mapping, Sequence


SCHEMA = "cortex/hierarchy-host-spike/v1"
TARGET_MODEL = "gpt-5.6-terra"
# ``max`` is a Cortex policy value, but the existing source does not prove it
# is accepted by a native create_thread call.  The four values below are the
# only bounded coordinator-effort cases accepted by this Stage 00 harness.
SUPPORTED_COORDINATOR_EFFORTS = ("low", "medium", "high", "xhigh")
SOURCE_POLICY_EFFORTS = ("low", "medium", "high", "xhigh", "max")
DEFAULT_SIMULATION_EFFORTS = SUPPORTED_COORDINATOR_EFFORTS
# The fake-host harness needs a finite parent deadline.  Keep this deliberately
# small and named: it is a test-harness safety cap, not a host capability claim.
MAX_FAKE_HOST_TIMEOUT_SECONDS = 60.0
_USE_REQUEST_EFFORT = object()
SAFE_ID_CHARS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._:-")
FORBIDDEN_KEYS = frozenset(
    {
        "prompt",
        "message",
        "token",
        "tokens",
        "secret",
        "secrets",
        "report",
        "reports",
        "stderr",
        "stdout",
        "raw_stderr",
        "private",
        "private_metadata",
        "metadata",
    }
)
def _literal(node: ast.AST) -> Any:
    """Return a safe literal from an AST node, or ``None`` if it is dynamic."""
    if isinstance(node, ast.Constant) and isinstance(node.value, (str, int, float, bool, type(None))):
        return node.value
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        values = [_literal(item) for item in node.elts]
        if any(value is None and not isinstance(item, ast.Constant) for item, value in zip(node.elts, values)):
            return None
        if isinstance(node, ast.List):
            return values
        if isinstance(node, ast.Tuple):
            return tuple(values)
        return set(values)
    if isinstance(node, ast.Dict):
        result: dict[Any, Any] = {}
        for key, value_node in zip(node.keys, node.values):
            value = _literal(value_node)
            key_value = _literal(key) if key is not None else None
            if key_value is None or (value is None and not isinstance(value_node, ast.Constant)):
                return None
            result[key_value] = value
        return result
    return None


def _literal_candidate(value: Any) -> dict[str, Any]:
    """Record a safely decoded literal without retaining source text."""
    return {"status": "literal", "value": value}


def _dynamic_candidate() -> dict[str, Any]:
    return {"status": "dynamic", "value": None}


def _assignment_candidates(tree: ast.AST, name: str) -> list[dict[str, Any]]:
    """Collect every module-level assignment candidate, including ``if`` arms.

    The source projection deliberately does not evaluate conditions.  Each arm
    is therefore a separate candidate.  Restricting this walk to module-level
    statements prevents a local variable in an unrelated helper from being
    mistaken for a policy declaration.
    """
    candidates: list[dict[str, Any]] = []

    def collect(statements: Sequence[ast.stmt]) -> None:
        for statement in statements:
            if isinstance(statement, (ast.Assign, ast.AnnAssign)):
                targets = statement.targets if isinstance(statement, ast.Assign) else [statement.target]
                if any(isinstance(target, ast.Name) and target.id == name for target in targets):
                    value = _literal(statement.value)
                    candidates.append(
                        _literal_candidate(value)
                        if value is not None or isinstance(statement.value, ast.Constant)
                        else _dynamic_candidate()
                    )
            elif isinstance(statement, ast.If):
                collect(statement.body)
                collect(statement.orelse)
            elif isinstance(statement, (ast.Try, ast.TryStar)):
                collect(statement.body)
                for handler in statement.handlers:
                    collect(handler.body)
                collect(statement.orelse)
                collect(statement.finalbody)

    if isinstance(tree, ast.Module):
        collect(tree.body)
    return candidates


def _aggregate_candidates(candidates: Sequence[dict[str, Any]]) -> tuple[str, Any, list[dict[str, Any]]]:
    """Aggregate candidates only when every path proves the same literal."""
    normalized = [dict(candidate) for candidate in candidates]
    if not normalized:
        return "unavailable", None, []
    if any(candidate["status"] != "literal" for candidate in normalized):
        return "dynamic", None, normalized
    values = [candidate["value"] for candidate in normalized]
    if all(value == values[0] for value in values[1:]):
        return "literal", values[0], normalized
    return "conditional", None, normalized


def _assignment_state(tree: ast.AST, name: str) -> tuple[str, Any]:
    """Return a fail-closed aggregate for every top-level declaration path."""
    status, value, _ = _aggregate_candidates(_assignment_candidates(tree, name))
    return status, value


def _function(tree: ast.AST, name: str) -> ast.AST | None:
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    return None


def _literal_string_keys(node: ast.AST) -> list[str] | None:
    if not isinstance(node, ast.Dict):
        return None
    keys = [_literal(key) for key in node.keys]
    if any(not isinstance(key, str) for key in keys):
        return None
    return sorted(set(keys))


def _return_value_candidates(node: ast.AST, state: Mapping[str, list[str] | None]) -> list[dict[str, Any]]:
    """Safely describe all values that one reachable return expression may emit."""
    if isinstance(node, ast.IfExp):
        return _return_value_candidates(node.body, state) + _return_value_candidates(node.orelse, state)
    if isinstance(node, ast.Name):
        keys = state.get(node.id)
        return [{"status": "literal", "value": keys}] if keys is not None else [_dynamic_candidate()]
    keys = _literal_string_keys(node)
    if keys is not None:
        return [{"status": "literal", "value": keys}]
    if isinstance(node, ast.DictComp):
        # `_v3_native_arguments` returns a filtered comprehension over a
        # locally constructed dictionary.  Preserve every possible input key;
        # filtering can only make a projection less affirmative, never prove a
        # key is accepted natively.
        for generator in node.generators:
            iterable = generator.iter
            if (
                isinstance(iterable, ast.Call)
                and isinstance(iterable.func, ast.Attribute)
                and iterable.func.attr == "items"
                and isinstance(iterable.func.value, ast.Name)
            ):
                keys = state.get(iterable.func.value.id)
                if keys is not None:
                    return [{"status": "literal", "value": keys}]
        return [_dynamic_candidate()]
    return [_dynamic_candidate()]


def _return_key_candidates(function: ast.AST | None) -> list[dict[str, Any]]:
    """Enumerate every reachable literal dictionary-key inventory.

    This small abstract interpreter follows named dictionary assignments and
    literal subscript additions through every ``if`` arm.  It does not attempt
    to execute source conditions or decode arbitrary expressions.
    """
    if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return []
    returned: list[dict[str, Any]] = []

    def copy_state(state: Mapping[str, list[str] | None]) -> dict[str, list[str] | None]:
        return {name: None if keys is None else list(keys) for name, keys in state.items()}

    def assign(state: Mapping[str, list[str] | None], statement: ast.stmt) -> dict[str, list[str] | None]:
        next_state = copy_state(state)
        if isinstance(statement, (ast.Assign, ast.AnnAssign)):
            targets = statement.targets if isinstance(statement, ast.Assign) else [statement.target]
            keys = _literal_string_keys(statement.value)
            for target in targets:
                if isinstance(target, ast.Name):
                    next_state[target.id] = None if keys is None else list(keys)
                elif (
                    isinstance(target, ast.Subscript)
                    and isinstance(target.value, ast.Name)
                    and target.value.id in next_state
                ):
                    key = _literal(target.slice)
                    current = next_state[target.value.id]
                    if not isinstance(key, str) or current is None:
                        next_state[target.value.id] = None
                    elif key not in current:
                        next_state[target.value.id] = sorted([*current, key])
        return next_state

    def walk(statements: Sequence[ast.stmt], states: list[dict[str, list[str] | None]]) -> list[dict[str, list[str] | None]]:
        active = states
        for statement in statements:
            following: list[dict[str, list[str] | None]] = []
            for state in active:
                if isinstance(statement, ast.Return):
                    returned.extend(_return_value_candidates(statement.value, state))
                    continue
                if isinstance(statement, ast.If):
                    following.extend(walk(statement.body, [copy_state(state)]))
                    following.extend(walk(statement.orelse, [copy_state(state)]))
                    continue
                if isinstance(statement, (ast.Try, ast.TryStar)):
                    following.extend(walk(statement.body, [copy_state(state)]))
                    for handler in statement.handlers:
                        following.extend(walk(handler.body, [copy_state(state)]))
                    following.extend(walk(statement.orelse, [copy_state(state)]))
                    continue
                following.append(assign(state, statement))
            active = following
        return active

    walk(function.body, [{}])
    return returned


def _safe_key_inventory(candidates: Sequence[dict[str, Any]]) -> tuple[str, list[str], list[dict[str, Any]]]:
    """Return all candidate inventories while allowing only unanimous support."""
    safe_candidates: list[dict[str, Any]] = []
    aggregate: set[str] = set()
    states: list[str] = []
    for candidate in candidates:
        status = candidate.get("status")
        value = candidate.get("value")
        if status == "literal" and isinstance(value, list) and all(isinstance(key, str) for key in value):
            keys = sorted(set(value))
            safe_candidates.append({"status": "literal", "keys": keys})
            aggregate.update(keys)
            states.append("literal")
        elif status == "unavailable":
            safe_candidates.append({"status": "unavailable", "keys": []})
            states.append("unavailable")
        else:
            safe_candidates.append({"status": "dynamic", "keys": []})
            states.append("dynamic")
    safe_candidates.sort(key=lambda candidate: (candidate["status"], tuple(candidate["keys"])))
    if not safe_candidates:
        return "unavailable", [], []
    literal_sets = [candidate["keys"] for candidate in safe_candidates if candidate["status"] == "literal"]
    if any(status == "dynamic" for status in states):
        return "dynamic", sorted(aggregate), safe_candidates
    if any(status == "unavailable" for status in states):
        return "unavailable", sorted(aggregate), safe_candidates
    if all(keys == literal_sets[0] for keys in literal_sets[1:]):
        return "literal", literal_sets[0], safe_candidates
    return "conditional", sorted(aggregate), safe_candidates


def _dict_keys_state(function: ast.AST | None, key: str) -> tuple[str, list[str], list[dict[str, Any]]]:
    """Inventory a named capability field across every reachable return path."""
    if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return "unavailable", [], []
    candidates: list[dict[str, Any]] = []
    for node in ast.walk(function):
        if not isinstance(node, ast.Return):
            continue
        values = [node.value]
        while values:
            value = values.pop()
            if isinstance(value, ast.IfExp):
                values.extend((value.body, value.orelse))
                continue
            if not isinstance(value, ast.Dict):
                candidates.append(_dynamic_candidate())
                continue
            matching = [item for item_key, item in zip(value.keys, value.values) if _literal(item_key) == key]
            if len(matching) != 1:
                candidates.append({"status": "unavailable", "value": []})
                continue
            decoded = _literal(matching[0])
            if isinstance(decoded, (list, tuple, set)) and all(isinstance(item, str) for item in decoded):
                candidates.append({"status": "literal", "value": sorted(set(decoded))})
            else:
                candidates.append(_dynamic_candidate())
    return _safe_key_inventory(candidates)


def inspect_source_contract(source_root: str | Path | None = None) -> dict[str, Any]:
    """Read only safe, source-declared capability facts through an AST seam."""
    root = Path(source_root) if source_root is not None else Path(__file__).resolve().parents[1]
    cortex_path = root / "plugins" / "cortex" / "scripts" / "cortex.py"
    profiles_path = root / "plugins" / "cortex" / "profiles.json"
    try:
        source = cortex_path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(cortex_path))
    except (OSError, SyntaxError):
        return {
            "policy_models": [],
            "policy_efforts": [],
            "policy_models_status": "unavailable",
            "policy_efforts_status": "unavailable",
            "configured_default_model": None,
            "configured_default_model_status": "unavailable",
            "native_create_thread_models": [],
            "native_create_thread_models_status": "unavailable",
            "native_create_thread_models_candidates": [],
            "native_create_thread_arguments": [],
            "native_create_thread_arguments_status": "unavailable",
            "native_create_thread_arguments_candidates": [],
            "native_create_thread_supports_model": False,
            "native_create_thread_supports_reasoning_effort": False,
            "inspection": "unavailable",
        }

    policy_models_status, policy_models = _assignment_state(tree, "SUPPORTED_MODELS")
    policy_efforts_status, policy_efforts = _assignment_state(tree, "SUPPORTED_EFFORT_SEQUENCE")
    capabilities = _function(tree, "_v3_host_capabilities")
    native_arguments_function = _function(tree, "_v3_native_arguments")
    thread_models_status, thread_models, thread_models_candidates = _dict_keys_state(
        capabilities, "create_thread_models"
    )
    native_arguments_status, native_arguments, native_arguments_candidates = _safe_key_inventory(
        _return_key_candidates(native_arguments_function)
    )
    configured_default: str | None = None
    configured_default_status = "unavailable"
    try:
        profile_data = json.loads(profiles_path.read_text(encoding="utf-8"))
        routing = profile_data.get("model_routing", {})
        if isinstance(routing, dict) and isinstance(routing.get("configured_default_model"), str):
            configured_default = routing["configured_default_model"]
            configured_default_status = "literal"
        elif isinstance(routing, dict) and "configured_default_model" in routing:
            configured_default_status = "dynamic"
    except (OSError, ValueError):
        configured_default = None

    models = sorted(item for item in (policy_models or []) if isinstance(item, str))
    efforts = [item for item in (policy_efforts or ()) if isinstance(item, str)]
    return {
        "policy_models": models,
        "policy_efforts": efforts,
        "policy_models_status": policy_models_status,
        "policy_efforts_status": policy_efforts_status,
        "configured_default_model": configured_default,
        "configured_default_model_status": configured_default_status,
        "native_create_thread_models": thread_models,
        "native_create_thread_models_status": thread_models_status,
        "native_create_thread_models_candidates": thread_models_candidates,
        "native_create_thread_arguments": native_arguments,
        "native_create_thread_arguments_status": native_arguments_status,
        "native_create_thread_arguments_candidates": native_arguments_candidates,
        "native_create_thread_supports_model": native_arguments_status == "literal" and "model" in native_arguments,
        "native_create_thread_supports_reasoning_effort": (
            native_arguments_status == "literal" and "reasoning_effort" in native_arguments
        ),
        "inspection": "source",
    }


def _safe_id(value: Any) -> str | None:
    if not isinstance(value, str) or not value or len(value) > 256:
        return None
    if any(char not in SAFE_ID_CHARS for char in value):
        return None
    return value


def _safe_mapping(value: Any) -> bool:
    if not isinstance(value, Mapping):
        return False
    for key, item in value.items():
        if not isinstance(key, str) or key.lower() in FORBIDDEN_KEYS:
            return False
        if len(key) > 64 or any(char not in SAFE_ID_CHARS for char in key):
            return False
        if isinstance(item, Mapping) and not _safe_mapping(item):
            return False
        if isinstance(item, (list, tuple)) and any(not _safe_value(part) for part in item):
            return False
        if isinstance(item, str) and (len(item) > 512 or any(ord(char) < 32 for char in item if char not in "\t")):
            return False
        if not isinstance(item, (Mapping, list, tuple, str, int, float, bool, type(None))):
            return False
    return True


def _safe_value(value: Any) -> bool:
    if isinstance(value, Mapping):
        return _safe_mapping(value)
    if isinstance(value, (list, tuple)):
        return all(_safe_value(item) for item in value)
    if isinstance(value, str):
        return len(value) <= 512 and not any(ord(char) < 32 for char in value if char not in "\t")
    return isinstance(value, (int, float, bool, type(None)))


def _observation(name: str, status: str, reason: str, **fields: Any) -> dict[str, Any]:
    result = {"name": name, "status": status, "reason": reason}
    result.update(fields)
    return result


def _call_adapter(adapter: Any, method: str, *args: Any) -> tuple[Any, str | None]:
    function = getattr(adapter, method, None)
    if not callable(function):
        return None, "adapter_method_unavailable"
    try:
        return function(*args), None
    except TimeoutError:
        return None, "bounded_timeout"
    except BaseException:
        # Adapter errors are intentionally collapsed to a safe reason.  The
        # probe must never echo adapter exception text or raw host diagnostics.
        return None, "adapter_operation_failed"


def _validate_timeout_seconds(value: Any) -> tuple[float | None, str | None]:
    """Accept one finite, non-boolean timeout before allocating resources.

    The parent process performs this validation before it asks a multiprocessing
    context for either end of a Pipe or a Process.  Do not broaden this to
    strings merely because ``float`` happens to accept them: a caller must pass
    a numeric value, and a boolean is not a duration.
    """
    if isinstance(value, bool) or not isinstance(value, Number):
        return None, "invalid_timeout"
    try:
        timeout = float(value)
    except (TypeError, ValueError, OverflowError):
        return None, "invalid_timeout"
    if not math.isfinite(timeout) or timeout <= 0.0 or timeout > MAX_FAKE_HOST_TIMEOUT_SECONDS:
        return None, "invalid_timeout"
    return timeout, None


def _safe_effort_request(efforts: Any) -> list[str]:
    """Represent requested efforts without echoing arbitrary caller input."""
    if not isinstance(efforts, (list, tuple)):
        return ["invalid"]
    if all(isinstance(effort, str) and effort in SUPPORTED_COORDINATOR_EFFORTS for effort in efforts):
        return list(efforts)
    if any(effort == "max" for effort in efforts if isinstance(effort, str)):
        return ["policy-only"]
    return ["invalid"]


def _validate_efforts(efforts: Any) -> tuple[list[str], list[dict[str, Any]], bool]:
    """Validate the bounded effort vocabulary before invoking an adapter."""
    observations: list[dict[str, Any]] = []
    if not isinstance(efforts, (list, tuple)):
        return [], [_observation("requested_efforts", "FAIL", "malformed_effort")], False
    requested = list(efforts)
    if not requested:
        return [], [_observation("requested_efforts", "FAIL", "missing_effort")], False
    if any(not isinstance(effort, str) for effort in requested):
        observations.append(_observation("requested_efforts", "FAIL", "malformed_effort"))
    if any(isinstance(effort, str) and effort == "max" for effort in requested):
        observations.append(_observation("requested_efforts", "FAIL", "policy_only_effort"))
    if any(isinstance(effort, str) and effort not in SOURCE_POLICY_EFFORTS for effort in requested):
        observations.append(_observation("requested_efforts", "FAIL", "unsupported_effort"))
    strings = [effort for effort in requested if isinstance(effort, str)]
    if len(strings) != len(set(strings)):
        observations.append(_observation("requested_efforts", "FAIL", "duplicate_effort"))
    if observations:
        observations.append(_observation("requested_efforts", "FAIL", "invalid_or_unsupported_effort"))
        return requested, observations, False
    return requested, [], True


def _receipt_error(value: Any, thread_id: str, *, required: Mapping[str, Any] | None = None) -> str | None:
    """Validate a bounded lifecycle receipt and its exact thread correlation."""
    if not isinstance(value, Mapping) or not _safe_mapping(value):
        return "malformed_or_unsanitized_receipt"
    if value.get("thread_id") != thread_id:
        return "cross_thread_receipt"
    if required is not None:
        for key, expected in required.items():
            if value.get(key) != expected:
                return "receipt_not_attested"
    return None


def _run_host_contract_core(
    adapter: Any,
    *,
    efforts: Sequence[str] = DEFAULT_SIMULATION_EFFORTS,
    environment: str = "local",
    timeout_seconds: float = 10.0,
) -> dict[str, Any]:
    """Evaluate a narrow child-thread adapter with fail-closed semantics.

    Every effort is requested once.  The positive fake-host case therefore
    yields four distinct thread IDs by default.  A result is support evidence
    only when all model, effort, identity, lifecycle, and sanitization checks
    pass; a ``SKIP`` result is never promoted to ``PASS``.
    """
    requested_efforts, effort_observations, efforts_are_well_formed = _validate_efforts(efforts)
    validated_timeout, timeout_error = _validate_timeout_seconds(timeout_seconds)
    observations: list[dict[str, Any]] = []
    threads: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    deadline = time.monotonic() + (validated_timeout or 0.0)
    valid_efforts = efforts_are_well_formed and timeout_error is None
    observations.extend(effort_observations)
    if environment not in {"local", "worktree"}:
        valid_efforts = False
        observations.append(_observation("thread_environment", "FAIL", "unsupported_environment"))
    if timeout_error is not None:
        observations.append(_observation("bounded_timeout", "FAIL", timeout_error))

    # Invalid request vocabulary is rejected before any adapter method is
    # called.  There is no fallback effort and therefore no host resource to
    # clean up in this branch.
    if valid_efforts:
        for ordinal, effort in enumerate(requested_efforts, 1):
            if time.monotonic() > deadline:
                observations.append(_observation("thread_timeout", "FAIL", "bounded_timeout", ordinal=ordinal))
                break
            title = f"cortex-stage00-child-{ordinal}"
            request = {
                "title": title,
                "environment": environment,
                "model": TARGET_MODEL,
                "reasoning_effort": effort,
            }
            response, error = _call_adapter(adapter, "create_thread", request)
            if error is not None or response is None:
                observations.append(_observation("create_thread", "FAIL", error or "lost_create_response", ordinal=ordinal))
                continue
            if not _safe_mapping(response):
                observations.append(_observation("create_thread", "FAIL", "unsanitized_adapter_observation", ordinal=ordinal))
                continue
            thread_id = _safe_id(response.get("thread_id"))
            if thread_id is None:
                observations.append(_observation("thread_identity", "FAIL", "missing_or_invalid_thread_id", ordinal=ordinal))
                continue
            if thread_id in seen_ids:
                observations.append(_observation("thread_identity", "FAIL", "duplicate_thread_id", ordinal=ordinal))
                continue
            seen_ids.add(thread_id)
            effective_model = response.get("effective_model")
            effective_effort = response.get("effective_reasoning_effort")
            model_ok = effective_model == TARGET_MODEL
            effort_ok = effective_effort == effort and effort in SUPPORTED_COORDINATOR_EFFORTS
            environment_ok = response.get("environment") == environment
            title_ok = response.get("title") == title
            if not model_ok:
                observations.append(_observation("effective_model", "FAIL", "model_substitution_or_missing", ordinal=ordinal))
            if not effort_ok:
                observations.append(_observation("effective_reasoning_effort", "FAIL", "effort_substitution_or_missing", ordinal=ordinal))
            if not environment_ok:
                observations.append(_observation("thread_environment", "FAIL", "environment_not_attested", ordinal=ordinal))
            if not title_ok:
                observations.append(_observation("thread_title", "FAIL", "title_not_attested", ordinal=ordinal))
            if not (model_ok and effort_ok and environment_ok and title_ok):
                threads.append({"ordinal": ordinal, "thread_id": thread_id, "status": "FAIL"})
                continue

            lifecycle_failed = False
            observations.extend(
                [
                    _observation("create_thread", "PASS", "create_receipt_correlated", ordinal=ordinal),
                    _observation("thread_identity", "PASS", "thread_identity_attested", ordinal=ordinal),
                    _observation("thread_title", "PASS", "title_attested", ordinal=ordinal),
                    _observation("thread_environment", "PASS", "environment_attested", ordinal=ordinal),
                ]
            )
            worker_request = {"thread_id": thread_id, "worker_ordinal": ordinal}
            worker_response, error = _call_adapter(adapter, "spawn_worker", thread_id, worker_request)
            worker_error = error
            if worker_error is None:
                if not isinstance(worker_response, Mapping) or not _safe_mapping(worker_response):
                    worker_error = "child_worker_spawn_unavailable"
                elif "thread_id" not in worker_response:
                    worker_error = "child_worker_spawn_unavailable"
                elif worker_response.get("thread_id") != thread_id:
                    worker_error = "cross_thread_receipt"
                elif not _safe_id(worker_response.get("worker_id")):
                    worker_error = "child_worker_spawn_unavailable"
            if worker_error is not None:
                observations.append(_observation("child_worker_spawn", "FAIL", worker_error, ordinal=ordinal))
                lifecycle_failed = True
            else:
                observations.append(_observation("child_worker_spawn", "PASS", "child_worker_spawned", ordinal=ordinal))

            follow_response, error = _call_adapter(adapter, "follow_up", thread_id)
            follow_error = error
            if follow_error is None:
                if not isinstance(follow_response, Mapping) or not _safe_mapping(follow_response):
                    follow_error = "follow_up_unavailable"
                elif follow_response.get("thread_id") != thread_id:
                    follow_error = "cross_thread_receipt"
                elif follow_response.get("accepted") is not True:
                    follow_error = "follow_up_unavailable"
            if follow_error is not None:
                observations.append(_observation("follow_up", "FAIL", follow_error if error is None else error, ordinal=ordinal))
                lifecycle_failed = True
            else:
                observations.append(_observation("follow_up", "PASS", "follow_up_available", ordinal=ordinal))

            resume_response, error = _call_adapter(adapter, "resume_thread", thread_id)
            resume_error = error
            if resume_error is None:
                if not isinstance(resume_response, Mapping) or not _safe_mapping(resume_response):
                    resume_error = "resume_unavailable"
                elif resume_response.get("thread_id") != thread_id:
                    resume_error = "cross_thread_receipt"
                elif resume_response.get("resumed") is not True:
                    resume_error = "resume_unavailable"
            if resume_error is not None:
                observations.append(_observation("resume", "FAIL", resume_error if error is None else error, ordinal=ordinal))
                lifecycle_failed = True
            else:
                observations.append(_observation("resume", "PASS", "resume_available", ordinal=ordinal))

            receipt_checks = (
                ("completion", "observe_completion", {"status": "completed"}, "completion_unavailable", "completion_observed"),
                ("failure", "observe_failure", {"status": "failed"}, "failure_unavailable", "failure_observed"),
                ("question", "observe_question", {"status": "question"}, "question_unavailable", "question_observed"),
                ("termination", "observe_termination", {"status": "terminated"}, "termination_unavailable", "termination_observed"),
            )
            for name, method, required, unavailable_reason, pass_reason in receipt_checks:
                receipt, error = _call_adapter(adapter, method, thread_id)
                receipt_error = error
                if receipt_error is None:
                    if not isinstance(receipt, Mapping) or not _safe_mapping(receipt):
                        receipt_error = unavailable_reason
                    elif receipt.get("thread_id") != thread_id:
                        receipt_error = "cross_thread_receipt"
                    elif any(receipt.get(key) != expected for key, expected in required.items()):
                        receipt_error = unavailable_reason
                if receipt_error is not None:
                    observations.append(_observation(name, "FAIL", receipt_error if error is None else unavailable_reason, ordinal=ordinal))
                    lifecycle_failed = True
                else:
                    observations.append(_observation(name, "PASS", pass_reason, ordinal=ordinal))
            threads.append({"ordinal": ordinal, "thread_id": thread_id, "status": "FAIL" if lifecycle_failed else "PASS"})
            if not lifecycle_failed:
                observations.extend(
                    [
                        _observation("effective_model", "PASS", "effective_model_attested", ordinal=ordinal),
                        _observation("effective_reasoning_effort", "PASS", "effective_effort_attested", ordinal=ordinal),
                    ]
                )

    cleanup_response = None
    cleanup_error: str | None = None
    cleanup_ok = not efforts_are_well_formed
    if efforts_are_well_formed:
        cleanup_response, cleanup_error = _call_adapter(adapter, "cleanup")
        cleanup_error = cleanup_error
        if cleanup_error is None:
            if not isinstance(cleanup_response, Mapping) or not _safe_mapping(cleanup_response):
                cleanup_error = "cleanup_incomplete"
            elif cleanup_response.get("thread_id") != "cleanup":
                cleanup_error = "cross_thread_receipt"
            elif cleanup_response.get("cleaned") is not True:
                cleanup_error = "cleanup_incomplete"
        # Cleanup is global, but it must still identify exactly the threads it
        # claims to have cleaned.  FakeHost supplies this bounded correlation.
        if cleanup_error is None and cleanup_response.get("thread_ids") != sorted(seen_ids):
            cleanup_error = "cleanup_not_correlated"
        cleanup_ok = cleanup_error is None
    observations.append(
        _observation(
            "cleanup",
            "PASS" if cleanup_ok else "FAIL",
            "cleanup_complete" if cleanup_ok else (cleanup_error or "cleanup_incomplete"),
        )
    )
    if not cleanup_ok:
        valid_efforts = False
    expected_count = len(requested_efforts)
    distinct_count = len({thread["thread_id"] for thread in threads})
    all_threads_pass = len(threads) == expected_count and all(thread["status"] == "PASS" for thread in threads)
    if distinct_count != expected_count:
        observations.append(_observation("thread_identity", "FAIL", "distinct_thread_ids_required"))
    if valid_efforts and set(requested_efforts) != set(SUPPORTED_COORDINATOR_EFFORTS):
        valid_efforts = False
        observations.append(_observation("requested_efforts", "FAIL", "complete_effort_set_required"))
    if len(threads) != 4:
        valid_efforts = False
        observations.append(_observation("thread_identity", "FAIL", "four_distinct_threads_required"))
    passed = bool(valid_efforts and all_threads_pass and cleanup_ok and not any(item["status"] == "FAIL" for item in observations))
    return {
        "schema": SCHEMA,
        "mode": "fake-host",
        "status": "PASS" if passed else "FAIL",
        "decision": "GO" if passed else "NO-GO",
        "support_evidence": passed,
        "request": {"model": TARGET_MODEL, "reasoning_efforts": _safe_effort_request(efforts), "environment": environment},
        "observed": {
            "effective_model": TARGET_MODEL if passed else None,
            "effective_reasoning_efforts": requested_efforts if passed else [],
            "thread_count": len(threads),
            "distinct_thread_ids": distinct_count,
        },
        "threads": threads,
        "observations": observations,
    }


def _failed_contract_result(
    *,
    efforts: Any,
    environment: str,
    reason: str,
    cleanup_reason: str,
    observations: Sequence[dict[str, Any]] = (),
) -> dict[str, Any]:
    """Return the only parent-side failure shape; it never copies adapter data."""
    failure_observations = list(observations)
    failure_observations.extend(
        [
            _observation("process_isolation", "FAIL", reason),
            _observation("cleanup", "FAIL", cleanup_reason),
        ]
    )
    return {
        "schema": SCHEMA,
        "mode": "fake-host",
        "status": "FAIL",
        "decision": "NO-GO",
        "support_evidence": False,
        "request": {
            "model": TARGET_MODEL,
            "reasoning_efforts": _safe_effort_request(efforts),
            "environment": environment if isinstance(environment, str) else "invalid",
        },
        "observed": {
            "effective_model": None,
            "effective_reasoning_efforts": [],
            "thread_count": 0,
            "distinct_thread_ids": 0,
        },
        "threads": [],
        "observations": failure_observations,
    }


def _fork_context() -> Any | None:
    """Select only a fork boundary, whose adapter object remains in one child."""
    try:
        return multiprocessing.get_context("fork")
    except (ValueError, OSError):
        return None


def _child_contract_entry(
    connection: Any,
    adapter: Any,
    efforts: Sequence[str],
    environment: str,
    timeout_seconds: float,
) -> None:
    """Run the complete adapter evaluation in the killable child process."""
    try:
        result = _run_host_contract_core(
            adapter,
            efforts=efforts,
            environment=environment,
            timeout_seconds=timeout_seconds,
        )
        connection.send({"result": result})
    except BaseException:
        # A child can never return an exception, trace, or adapter diagnostic.
        try:
            connection.send({"result": None})
        except BaseException:
            pass
    finally:
        try:
            connection.close()
        except BaseException:
            pass


def _decode_child_result(value: Any) -> dict[str, Any] | None:
    """Accept only the exact, already-sanitized contract result shape."""
    if not isinstance(value, Mapping) or set(value) != {"result"}:
        return None
    result = value.get("result")
    if not isinstance(result, Mapping) or not _safe_mapping(result):
        return None
    if result.get("schema") != SCHEMA or result.get("mode") != "fake-host":
        return None
    if result.get("status") not in {"PASS", "FAIL"}:
        return None
    if result.get("decision") not in {"GO", "NO-GO"}:
        return None
    if not isinstance(result.get("support_evidence"), bool):
        return None
    if not isinstance(result.get("observations"), list) or not isinstance(result.get("threads"), list):
        return None
    return dict(result)


def _close_endpoint(endpoint: Any | None) -> bool:
    """Attempt one endpoint close without exposing an adapter or OS exception."""
    if endpoint is None:
        return True
    try:
        endpoint.close()
        return True
    except BaseException:
        return False


def _join_for(process: Any, deadline: float) -> str:
    """Return ``stopped``, ``alive``, or ``unverified`` after one bounded join."""
    try:
        remaining = max(0.0, deadline - time.monotonic())
        process.join(min(0.05, remaining))
    except BaseException:
        return "unverified"
    try:
        return "alive" if bool(process.is_alive()) else "stopped"
    except BaseException:
        return "unverified"


def _process_operation(process: Any, name: str) -> bool:
    """Attempt a terminal process operation exactly once and contain all errors."""
    try:
        operation = getattr(process, name)
        if not callable(operation):
            return False
        operation()
        return True
    except BaseException:
        return False


def _reap_started_process(
    process: Any,
    parent_connection: Any | None,
    child_connection: Any | None,
    deadline: float,
) -> bool:
    """Make every feasible bounded post-start cleanup attempt.

    This owner intentionally keeps trying after endpoint, liveness, terminate,
    kill, or join errors.  It can report verified cleanup only if every required
    operation and liveness check succeeds.  A process that has already exited
    after the first bounded join does not receive infeasible terminate/kill
    requests, but it still has both endpoints closed and is joined.
    """
    cleanup_verified = _close_endpoint(parent_connection)
    cleanup_verified = _close_endpoint(child_connection) and cleanup_verified

    joined = _join_for(process, deadline)
    if joined == "stopped":
        return cleanup_verified
    if joined == "unverified":
        cleanup_verified = False

    # An unsuccessful join includes unavailable or raising liveness.  In that
    # case the parent cannot prove the child is gone, so it must attempt both
    # terminal operations rather than trust a partial observation.
    terminated = _process_operation(process, "terminate")
    cleanup_verified = terminated and cleanup_verified
    joined_after_terminate = _join_for(process, deadline)
    if joined_after_terminate == "stopped":
        return cleanup_verified
    if joined_after_terminate == "unverified":
        cleanup_verified = False

    killed = _process_operation(process, "kill")
    cleanup_verified = killed and cleanup_verified
    joined_after_kill = _join_for(process, deadline)
    cleanup_verified = joined_after_kill == "stopped" and cleanup_verified
    return cleanup_verified


def run_host_contract(
    adapter: Any,
    *,
    efforts: Sequence[str] = DEFAULT_SIMULATION_EFFORTS,
    environment: str = "local",
    timeout_seconds: float = 10.0,
) -> dict[str, Any]:
    """Run the entire fake-host evaluation behind a finite fork-process boundary.

    A native adapter is never selected by this harness.  The fake adapter is
    evaluated in a single child so all of its lifecycle state is preserved
    there, while a blocked call cannot hold the parent past its deadline.  A
    thread-based timeout is intentionally not used because it cannot safely
    cancel a blocking host call.
    """
    requested_efforts, validation_observations, efforts_are_well_formed = _validate_efforts(efforts)
    bounded_timeout, timeout_error = _validate_timeout_seconds(timeout_seconds)
    valid_request = (
        efforts_are_well_formed
        and environment in {"local", "worktree"}
        and timeout_error is None
        and bounded_timeout is not None
    )
    if not valid_request:
        observations = list(validation_observations)
        if environment not in {"local", "worktree"}:
            observations.append(_observation("thread_environment", "FAIL", "unsupported_environment"))
        if timeout_error is not None:
            observations.append(_observation("bounded_timeout", "FAIL", timeout_error))
        return _failed_contract_result(
            efforts=efforts,
            environment=environment,
            reason="invalid_contract_request",
            cleanup_reason="cleanup_not_attempted",
            observations=observations,
        )

    context = _fork_context()
    if context is None:
        return _failed_contract_result(
            efforts=efforts,
            environment=environment,
            reason="process_isolation_unavailable",
            cleanup_reason="cleanup_disabled",
        )

    try:
        parent_connection, child_connection = context.Pipe(duplex=False)
        process = context.Process(
            target=_child_contract_entry,
            args=(child_connection, adapter, tuple(requested_efforts), environment, bounded_timeout),
        )
        process.daemon = True
    except BaseException:
        # No child has started in this branch.  Close any allocated endpoint,
        # but never claim that a process cleanup occurred.
        _close_endpoint(locals().get("parent_connection"))
        _close_endpoint(locals().get("child_connection"))
        return _failed_contract_result(
            efforts=efforts,
            environment=environment,
            reason="process_isolation_unavailable",
            cleanup_reason="cleanup_not_attempted",
        )
    deadline = time.monotonic() + bounded_timeout
    try:
        process.start()
    except BaseException:
        _close_endpoint(parent_connection)
        _close_endpoint(child_connection)
        return _failed_contract_result(
            efforts=efforts,
            environment=environment,
            reason="process_isolation_unavailable",
            cleanup_reason="cleanup_not_attempted",
        )
    received: Any = None
    post_start_reason: str | None = None
    cleanup_verified = False
    try:
        try:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not parent_connection.poll(max(0.0, remaining)):
                post_start_reason = "bounded_timeout"
            else:
                try:
                    received = parent_connection.recv()
                except BaseException:
                    post_start_reason = "child_receive_failed"
        except BaseException:
            post_start_reason = "child_receive_failed"
    finally:
        # Once start() returns, no return or exception path can avoid this
        # finalizer.  It owns both Pipe ends and all feasible terminal process
        # operations, and it never propagates their exceptions.
        cleanup_verified = _reap_started_process(
            process, parent_connection, child_connection, time.monotonic() + 0.20
        )

    if post_start_reason == "bounded_timeout":
        return _failed_contract_result(
            efforts=efforts,
            environment=environment,
            reason="bounded_timeout" if cleanup_verified else "bounded_timeout_process_join_unverified",
            cleanup_reason="cleanup_incomplete_after_timeout",
        )
    if post_start_reason is not None:
        return _failed_contract_result(
            efforts=efforts,
            environment=environment,
            reason=post_start_reason,
            cleanup_reason="cleanup_unverified",
        )
    if not cleanup_verified:
        return _failed_contract_result(
            efforts=efforts,
            environment=environment,
            reason="post_start_cleanup_unverified",
            cleanup_reason="cleanup_unverified",
        )
    result = _decode_child_result(received)
    if result is None:
        return _failed_contract_result(
            efforts=efforts,
            environment=environment,
            reason="child_result_transport_invalid",
            cleanup_reason="cleanup_unverified",
        )
    return result


class FakeHost:
    """Deterministic adapter used by the test module and never by ``--live``."""

    def __init__(
        self,
        *,
        effective_model: str = TARGET_MODEL,
        effective_effort: Any = _USE_REQUEST_EFFORT,
        environment: str = "local",
        duplicate_ids: bool = False,
        lose_create: bool = False,
        worker_available: bool = True,
        follow_up_available: bool = True,
        resume_available: bool = True,
        completion_available: bool = True,
        failure_available: bool = True,
        question_available: bool = True,
        termination_available: bool = True,
        cross_thread_receipts: bool = False,
        cleanup_complete: bool = True,
        unsanitized: bool = False,
    ) -> None:
        self.effective_model = effective_model
        self.effective_effort = effective_effort
        self.environment = environment
        self.duplicate_ids = duplicate_ids
        self.lose_create = lose_create
        self.worker_available = worker_available
        self.follow_up_available = follow_up_available
        self.resume_available = resume_available
        self.completion_available = completion_available
        self.failure_available = failure_available
        self.question_available = question_available
        self.termination_available = termination_available
        self.cross_thread_receipts = cross_thread_receipts
        self.cleanup_complete = cleanup_complete
        self.unsanitized = unsanitized
        self.created = 0
        self.thread_ids: list[str] = []

    def create_thread(self, request: Mapping[str, Any]) -> Mapping[str, Any] | None:
        self.created += 1
        if self.lose_create:
            return None
        thread_id = "fake-thread-1" if self.duplicate_ids else f"fake-thread-{self.created}"
        response: dict[str, Any] = {
            "thread_id": thread_id,
            "title": request.get("title"),
            "environment": self.environment,
            "effective_model": self.effective_model,
            "effective_reasoning_effort": (
                request.get("reasoning_effort")
                if self.effective_effort is _USE_REQUEST_EFFORT
                else self.effective_effort
            ),
        }
        self.thread_ids.append(thread_id)
        if self.unsanitized:
            response["stderr"] = "host-private-diagnostic"
        return response

    def spawn_worker(self, thread_id: str, request: Mapping[str, Any]) -> Mapping[str, Any]:
        receipt_id = "fake-thread-cross" if self.cross_thread_receipts else thread_id
        return {"thread_id": receipt_id, "worker_id": f"fake-worker-{thread_id}"} if self.worker_available else {}

    def follow_up(self, thread_id: str) -> Mapping[str, Any]:
        receipt_id = "fake-thread-cross" if self.cross_thread_receipts else thread_id
        return {"thread_id": receipt_id, "accepted": self.follow_up_available}

    def resume_thread(self, thread_id: str) -> Mapping[str, Any]:
        receipt_id = "fake-thread-cross" if self.cross_thread_receipts else thread_id
        return {"thread_id": receipt_id, "resumed": self.resume_available}

    def observe_completion(self, thread_id: str) -> Mapping[str, Any]:
        receipt_id = "fake-thread-cross" if self.cross_thread_receipts else thread_id
        return {"thread_id": receipt_id, "status": "completed" if self.completion_available else "unknown"}

    def observe_failure(self, thread_id: str) -> Mapping[str, Any]:
        receipt_id = "fake-thread-cross" if self.cross_thread_receipts else thread_id
        return {"thread_id": receipt_id, "status": "failed" if self.failure_available else "unknown"}

    def observe_question(self, thread_id: str) -> Mapping[str, Any]:
        receipt_id = "fake-thread-cross" if self.cross_thread_receipts else thread_id
        return {"thread_id": receipt_id, "status": "question" if self.question_available else "unknown"}

    def observe_termination(self, thread_id: str) -> Mapping[str, Any]:
        receipt_id = "fake-thread-cross" if self.cross_thread_receipts else thread_id
        return {"thread_id": receipt_id, "status": "terminated" if self.termination_available else "unknown"}

    def cleanup(self) -> Mapping[str, Any]:
        return {"thread_id": "cleanup", "cleaned": self.cleanup_complete, "thread_ids": sorted(self.thread_ids)}


def static_inventory(source_root: str | Path | None = None) -> dict[str, Any]:
    """Return deterministic source inventory and the current hard-gate result."""
    source = inspect_source_contract(source_root)
    native_model_declared = (
        source["native_create_thread_models_status"] == "literal"
        and TARGET_MODEL in source["native_create_thread_models"]
    )
    model_argument_declared = source["native_create_thread_supports_model"]
    effort_argument_declared = source["native_create_thread_supports_reasoning_effort"]
    observations = [
        _observation(
            "native_create_thread_model_catalog",
            "PASS" if native_model_declared else "FAIL",
            (
                "terra_declared"
                if native_model_declared
                else (
                    "terra_not_declared"
                    if source["native_create_thread_models_status"] == "literal"
                    else f"terra_{source['native_create_thread_models_status']}"
                )
            ),
        ),
        _observation(
            "native_model_argument",
            "PASS" if source["native_create_thread_supports_model"] else "FAIL",
            (
                "model_argument_declared"
                if source["native_create_thread_supports_model"]
                else f"model_argument_{source['native_create_thread_arguments_status']}"
            ),
        ),
        _observation(
            "native_reasoning_effort_argument",
            "PASS" if source["native_create_thread_supports_reasoning_effort"] else "FAIL",
            (
                "effort_argument_declared"
                if source["native_create_thread_supports_reasoning_effort"]
                else f"effort_argument_{source['native_create_thread_arguments_status']}"
            ),
        ),
        _observation("effective_model_attestation", "FAIL", "native_effective_model_observation_unavailable"),
        _observation("effective_reasoning_effort_attestation", "FAIL", "native_effective_effort_observation_unavailable"),
        _observation("child_worker_lifecycle", "FAIL", "native_child_lifecycle_unavailable"),
    ]
    capabilities = {
            "model": {
                "status": "UNPROVEN",
                "source_projection": "declared" if native_model_declared else source["native_create_thread_models_status"],
            "reason": "native_effective_model_observation_unavailable",
        },
        "effort": {
            "status": "UNPROVEN",
            "source_projection": "declared" if effort_argument_declared else source["native_create_thread_arguments_status"],
            "reason": "native_effective_effort_observation_unavailable",
        },
        "identity": {"status": "UNPROVEN", "source_projection": "unavailable", "reason": "native_thread_identity_unavailable"},
        "lifecycle": {"status": "UNPROVEN", "source_projection": "unavailable", "reason": "native_lifecycle_receipts_unavailable"},
        "worktree": {"status": "UNPROVEN", "source_projection": "unavailable", "reason": "native_worktree_ownership_unavailable"},
        "recovery": {"status": "UNPROVEN", "source_projection": "unavailable", "reason": "native_recovery_receipts_unavailable"},
        "child_worker": {"status": "UNPROVEN", "source_projection": "unavailable", "reason": "native_child_worker_receipt_unavailable"},
    }
    return {
        "schema": SCHEMA,
        "mode": "static",
        "status": "FAIL",
        "decision": "NO-GO",
        "support_evidence": False,
        "request": {"model": TARGET_MODEL, "reasoning_effort": "xhigh", "environment": "local"},
        "observed": {"effective_model": None, "effective_reasoning_effort": None},
        "source_inventory": {
            "inspection": source["inspection"],
            "policy_models": source["policy_models"],
            "policy_models_status": source["policy_models_status"],
            "policy_efforts": source["policy_efforts"],
            "policy_efforts_status": source["policy_efforts_status"],
            "configured_default_model": source["configured_default_model"],
            "configured_default_model_status": source["configured_default_model_status"],
            "native_create_thread_models": source["native_create_thread_models"],
            "native_create_thread_models_status": source["native_create_thread_models_status"],
            "native_create_thread_models_candidates": source["native_create_thread_models_candidates"],
            "native_create_thread_arguments": source["native_create_thread_arguments"],
            "native_create_thread_arguments_status": source["native_create_thread_arguments_status"],
            "native_create_thread_arguments_candidates": source["native_create_thread_arguments_candidates"],
        },
        "capabilities": capabilities,
        "observations": observations,
    }


def _live_base(status: str, name: str, reason: str) -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "mode": "live",
        "status": status,
        "decision": "NO-GO",
        "support_evidence": False,
        "request": {"model": TARGET_MODEL, "reasoning_effort": "xhigh", "environment": "local"},
        "observed": {"effective_model": None, "effective_reasoning_effort": None},
        "observations": [_observation(name, status, reason)],
    }


def _live_result(timeout_seconds: float) -> dict[str, Any]:
    """Return a non-evidence result without reading configuration or launching a process."""
    del timeout_seconds
    return _live_base("SKIP", "native_adapter", "live_probe_disabled")


def _json_dump(value: Mapping[str, Any]) -> None:
    print(json.dumps(value, sort_keys=True, separators=(",", ":")))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the isolated Stage 00 host-contract inventory.")
    parser.add_argument("--json", action="store_true", help="emit deterministic machine-readable output")
    parser.add_argument("--live", action="store_true", help="report the disabled, non-evidence live probe mode")
    parser.add_argument("--timeout", type=float, default=10.0, help="retained for CLI compatibility; ignored by disabled live mode")
    args = parser.parse_args(argv)
    if args.live:
        result = _live_result(args.timeout)
    else:
        result = static_inventory()
    if args.json or args.live:
        _json_dump(result)
    else:
        parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
