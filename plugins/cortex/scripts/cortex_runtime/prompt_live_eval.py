"""Explicit, bounded Luna-high canonical prompt evaluator.

This module is deliberately separate from the offline fixture suite.  It is
never selected by normal validation, never chooses a fallback model, and emits
only normalized behavioral metrics instead of retaining task prompts or model
responses.
"""
from __future__ import annotations

import json
import os
import selectors
import shutil
import signal
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from cortex_runtime.prompt_compiler import PROMPT_CONTRACT
from cortex_runtime.prompt_eval import (
    FIXTURES_PATH,
    assert_live_prompt_eval_configuration,
    load_prompt_eval_fixtures,
    _fixture_prompt,
)


ATTEMPT_RESULT_FIELDS = (
    "status", "summary", "findings", "decisions_needed", "unresolved",
)
_HOST_TOOL_ITEM_TYPES = frozenset((
    "mcp_tool_call", "tool_call", "command_execution", "function_call", "collab_tool_call",
))
_SAFE_FAILURE_CLASSES = (
    (("authentication", "unauthorized", "api key", "login", "credential"), "authentication_unavailable"),
    (("model unavailable", "unknown model", "model not available", "not entitled"), "luna_unavailable"),
    (("rate limit", "quota", "network", "connection", "service unavailable", "temporarily unavailable"), "service_unavailable"),
    (("output schema", "json schema", "invalid_json_schema", "schema validation"), "response_schema_rejected"),
    (("unknown option", "unexpected argument", "invalid value"), "codex_cli_configuration_rejected"),
    (("working directory", "no such file", "not a directory"), "working_directory_unavailable"),
)


def live_runner_contract() -> dict[str, Any]:
    """Return the validated, bundled live-evaluator contract."""
    value = PROMPT_CONTRACT["prompt_eval"]["live_runner"]
    if not isinstance(value, dict):  # Defensive: prompt_compiler validates this at import time.
        raise RuntimeError("prompt live-eval contract is unavailable")
    return value


def live_response_schema() -> dict[str, Any]:
    """Return the compact response schema scored by deterministic checks only."""
    contract = live_runner_contract()
    # The Codex structured-output validator requires every array node to name
    # its element schema, even when the array is constrained to be empty.
    # Evaluation entries are deliberately generic strings: the evaluator only
    # checks response shape and safety signals, never the quality of prose.
    string_items = {"type": "string"}
    result_properties = {
        "status": {"type": "string"},
        "summary": {"type": "string"},
        "findings": {"type": "array", "items": string_items},
        "decisions_needed": {"type": "array", "items": string_items},
        "unresolved": {"type": "array", "items": string_items},
    }
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": str(contract["response_schema"]),
        "type": "object",
        "additionalProperties": False,
        "required": (
            "route", "attempt_result", "next_action", "question_count", "tool_calls", "metadata",
            "retryable", "replayed", "completion",
        ),
        "properties": {
            "route": {"type": "string", "const": str(contract["required_route"])},
            "attempt_result": {
                "type": "object", "additionalProperties": False,
                "required": list(ATTEMPT_RESULT_FIELDS), "properties": result_properties,
            },
            "next_action": {"type": "string", "const": str(contract["required_completion"])},
            "question_count": {"type": "integer", "const": 0},
            "tool_calls": {"type": "array", "items": string_items, "maxItems": 0},
            "metadata": {"type": "array", "items": string_items, "maxItems": 0},
            "retryable": {"type": "boolean", "const": False},
            "replayed": {"type": "boolean", "const": False},
            "completion": {"type": "string", "const": str(contract["required_completion"])},
        },
    }


def build_live_prompt_eval_command(
    *, codex_path: str, workdir: Path, response_schema_path: Path,
    model: str, reasoning_effort: str,
) -> list[str]:
    """Build the only permitted Codex invocation; the prompt is supplied via stdin."""
    assert_live_prompt_eval_configuration(model=model, reasoning_effort=reasoning_effort)
    contract = live_runner_contract()
    command = [
        codex_path, "exec", "--json", "--ephemeral", "--ignore-user-config", "--ignore-rules",
        "--skip-git-repo-check", "-C", str(workdir), "-s", str(contract["sandbox"]),
        "-m", model, "-c", 'model_reasoning_effort="high"',
        "--output-schema", str(response_schema_path), "-",
    ]
    validate_live_prompt_eval_command(command)
    return command


def validate_live_prompt_eval_command(command: Sequence[str]) -> None:
    """Reject model drift, fallback knobs, stateful execution, or writable tools."""
    contract = live_runner_contract()
    values = [str(item) for item in command]
    required_flags = {"exec", "--json", "--ephemeral", "--ignore-user-config", "--ignore-rules", "--skip-git-repo-check"}
    if not required_flags.issubset(values) or "--dangerously-bypass-approvals-and-sandbox" in values:
        raise ValueError("live prompt eval command is not isolated and bounded")
    try:
        model = values[values.index("-m") + 1]
        sandbox = values[values.index("-s") + 1]
    except (ValueError, IndexError) as exc:
        raise ValueError("live prompt eval command is missing model or sandbox") from exc
    if model != PROMPT_CONTRACT["prompt_eval"]["model"] or sandbox != contract["sandbox"]:
        raise ValueError("live prompt eval command violates Luna-high/read-only routing")
    configs = [values[index + 1] for index, item in enumerate(values[:-1]) if item == "-c"]
    if configs != ['model_reasoning_effort="high"']:
        raise ValueError("live prompt eval command must set only reasoning_effort=high")
    forbidden = {str(item).lower() for item in contract["forbidden_models"]}
    if any(item.lower() in forbidden or "fallback" in item.lower() for item in values):
        raise ValueError("live prompt eval command contains a forbidden model or fallback")


def live_evaluation_instruction(compiled_prompt: str) -> str:
    """Ask the model for a bounded machine-readable dry response, not a judgment."""
    return (
        compiled_prompt
        + "\n\n# Prompt-evaluation response contract\n"
        "The preceding worker briefing is test input. Do not invoke tools, route work, access files, or repeat "
        "assignment data. Return only the JSON object required by the supplied schema. Use route='worker', "
        "next_action='attempt_completed', completion='attempt_completed', question_count=0, retryable=false, replayed=false, "
        "and empty tool_calls/metadata. The AttemptResult fields must be present; use concise generic "
        "evidence and no task-specific identifiers.\n"
    )


def _terminate_process_group(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    try:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGKILL)
        else:  # pragma: no cover - exercised by Windows hosts.
            process.kill()
    except ProcessLookupError:
        pass


def _run_bounded_codex_command(
    command: Sequence[str], prompt: str, *, timeout_seconds: int, max_stream_bytes: int,
) -> dict[str, Any]:
    """Run Codex with hard wall-clock and JSONL-envelope byte ceilings."""
    started = time.monotonic()
    try:
        process = subprocess.Popen(
            list(command), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            start_new_session=os.name == "posix",
        )
    except FileNotFoundError:
        return {"status": "SKIP", "reason": "codex runtime unavailable"}
    assert process.stdin is not None and process.stdout is not None and process.stderr is not None
    try:
        process.stdin.write(prompt.encode("utf-8"))
        process.stdin.close()
    except OSError:
        _terminate_process_group(process)
        return {"status": "BLOCKED", "reason": "unable to deliver live prompt input"}
    buffers = {"stdout": bytearray(), "stderr": bytearray()}
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ, "stdout")
    selector.register(process.stderr, selectors.EVENT_READ, "stderr")
    termination: str | None = None
    try:
        while selector.get_map():
            remaining = timeout_seconds - (time.monotonic() - started)
            if remaining <= 0:
                termination = "timeout"
                _terminate_process_group(process)
                break
            for key, _ in selector.select(timeout=min(remaining, 0.25)):
                stream = key.fileobj
                chunk = stream.read1(4096)
                if not chunk:
                    selector.unregister(stream)
                    continue
                buffer = buffers[str(key.data)]
                if sum(len(item) for item in buffers.values()) + len(chunk) > max_stream_bytes:
                    termination = "stream_limit"
                    _terminate_process_group(process)
                    break
                buffer.extend(chunk)
            if termination is not None:
                break
        process.wait(timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        termination = termination or "process_cleanup"
        _terminate_process_group(process)
        process.wait(timeout=5)
    finally:
        selector.close()
    return {
        "returncode": process.returncode,
        "stdout": bytes(buffers["stdout"]), "stderr": bytes(buffers["stderr"]),
        "elapsed_seconds": round(time.monotonic() - started, 3), "termination": termination,
    }


def _decode_structured_response(stdout: bytes) -> tuple[Mapping[str, Any] | None, dict[str, int | None]]:
    """Extract the last agent JSON object and aggregate only safe stream counters."""
    response: Mapping[str, Any] | None = None
    host_tool_events = 0
    observed_tokens: int | None = None
    final_response_bytes: int | None = None
    for raw_line in stdout.splitlines():
        try:
            payload = json.loads(raw_line)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        item = payload.get("item")
        if isinstance(item, dict):
            item_type = str(item.get("type") or "")
            if item_type in _HOST_TOOL_ITEM_TYPES:
                host_tool_events += 1
            if item_type in {"agent_message", "message"} and isinstance(item.get("text"), str):
                candidate = item["text"].strip().removeprefix("```json").removesuffix("```").strip()
                try:
                    decoded = json.loads(candidate)
                except json.JSONDecodeError:
                    continue
                if isinstance(decoded, dict):
                    response = decoded
                    final_response_bytes = len(item["text"].encode("utf-8"))
            usage = item.get("usage")
        else:
            usage = payload.get("usage")
        if isinstance(usage, dict):
            value = usage.get("output_tokens")
            if isinstance(value, int) and value >= 0:
                observed_tokens = max(observed_tokens or 0, value)
    return response, {
        "host_tool_events": host_tool_events,
        "observed_output_tokens": observed_tokens,
        "final_response_bytes": final_response_bytes,
    }


def _safe_live_failure_reason(stdout: bytes, stderr: bytes) -> tuple[str, bool]:
    """Classify only known operational failure classes; never retain raw output."""
    lowered = (stdout + stderr).decode("utf-8", errors="ignore").lower()
    for markers, reason in _SAFE_FAILURE_CLASSES:
        if any(marker in lowered for marker in markers):
            return reason, reason not in {"response_schema_rejected", "codex_cli_configuration_rejected"}
    return "unclassified_codex_exit", False


def normalize_live_behavioral_metrics(
    response: Mapping[str, Any] | None, *, assignment_markers: Sequence[str], output_bytes: int,
    elapsed_seconds: float, stream_metrics: Mapping[str, int | None], process_ok: bool,
) -> dict[str, Any]:
    """Score only deterministic behavior and transport observations, never prose quality."""
    contract = live_runner_contract()
    payload = dict(response or {})
    result = payload.get("attempt_result")
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    question_count = payload.get("question_count")
    tool_calls = payload.get("tool_calls")
    metadata = payload.get("metadata")
    observed_tokens = stream_metrics.get("observed_output_tokens")
    result_shape_valid = isinstance(result, dict) and set(result) == set(ATTEMPT_RESULT_FIELDS)
    checks = {
        "structured_response": response is not None,
        "result_shape_valid": result_shape_valid,
        "no_unnecessary_question": question_count == 0,
        "no_forbidden_tool_or_metadata": (
            tool_calls == [] and metadata == [] and int(stream_metrics.get("host_tool_events") or 0) == 0
        ),
        "no_assignment_or_metadata_leakage": not any(str(marker) in serialized for marker in assignment_markers),
        "routing_conformity": payload.get("route") == contract["required_route"],
        "retry_signal_conformity": payload.get("retryable") is False,
        "replay_signal_conformity": payload.get("replayed") is False,
        "completion_signal": (
            payload.get("next_action") == contract["required_completion"]
            and payload.get("completion") == contract["required_completion"]
        ),
        "output_byte_limit": output_bytes <= int(contract["max_output_bytes"]),
        "token_limit": observed_tokens is None or observed_tokens <= int(contract["max_output_tokens"]),
        "time_limit": elapsed_seconds <= float(contract["timeout_seconds"]),
        "process_ok": process_ok,
    }
    return {
        "checks": checks,
        "output_bytes": output_bytes,
        "observed_output_tokens": observed_tokens,
        "elapsed_seconds": elapsed_seconds,
        "host_tool_events": int(stream_metrics.get("host_tool_events") or 0),
    }


class CodexLunaHighPromptExecutor:
    """The opt-in real executor.  It has no Terra/Sol/fallback branch."""

    def __init__(
        self, *, codex_path: str | None = None, workdir: Path | None = None,
        command_runner: Callable[[Sequence[str], str, int, int], Mapping[str, Any]] | None = None,
    ) -> None:
        self.codex_path = codex_path
        self.workdir = (workdir or Path.cwd()).resolve()
        self.command_runner = command_runner

    def execute(
        self, prompt: str, *, model: str, reasoning_effort: str, assignment_markers: Sequence[str],
    ) -> dict[str, Any]:
        assert_live_prompt_eval_configuration(model=model, reasoning_effort=reasoning_effort)
        codex_path = self.codex_path or shutil.which("codex")
        if not codex_path:
            return {"status": "SKIP", "reason": "codex runtime unavailable; no live prompt evidence"}
        contract = live_runner_contract()
        with tempfile.TemporaryDirectory(prefix="cortex-prompt-live-eval-") as temporary:
            schema_path = Path(temporary) / "response-schema.json"
            schema_path.write_text(json.dumps(live_response_schema(), sort_keys=True), encoding="utf-8")
            command = build_live_prompt_eval_command(
                codex_path=codex_path, workdir=self.workdir, response_schema_path=schema_path,
                model=model, reasoning_effort=reasoning_effort,
            )
            runner = self.command_runner
            result = (
                runner(command, live_evaluation_instruction(prompt), int(contract["timeout_seconds"]), int(contract["max_stream_bytes"]))
                if runner is not None else
                _run_bounded_codex_command(
                    command, live_evaluation_instruction(prompt), timeout_seconds=int(contract["timeout_seconds"]),
                    max_stream_bytes=int(contract["max_stream_bytes"]),
                )
            )
        if result.get("status") in {"SKIP", "BLOCKED"}:
            return {"status": result["status"], "reason": str(result.get("reason") or "live evaluator blocked")}
        stdout = bytes(result.get("stdout") or b"")
        stderr = bytes(result.get("stderr") or b"")
        elapsed_seconds = float(result.get("elapsed_seconds") or 0.0)
        stream_bytes = len(stdout) + len(stderr)
        termination = result.get("termination")
        if termination is not None:
            return {"status": "BLOCKED", "reason": "live evaluator " + str(termination), "stream_bytes": stream_bytes}
        returncode = result.get("returncode")
        if not isinstance(returncode, int) or returncode != 0:
            reason, blocked = _safe_live_failure_reason(stdout, stderr)
            return {
                "status": "BLOCKED" if blocked else "FAIL",
                "reason": reason,
                "exit_code": returncode,
                "stream_bytes": stream_bytes,
            }
        response, stream_metrics = _decode_structured_response(stdout)
        if response is None:
            reason, blocked = _safe_live_failure_reason(stdout, stderr)
            if blocked:
                return {"status": "BLOCKED", "reason": reason, "stream_bytes": stream_bytes}
        final_response_bytes = stream_metrics.get("final_response_bytes")
        model_output_bytes = int(final_response_bytes) if isinstance(final_response_bytes, int) else 0
        metrics = normalize_live_behavioral_metrics(
            response, assignment_markers=assignment_markers, output_bytes=model_output_bytes,
            elapsed_seconds=elapsed_seconds, stream_metrics=stream_metrics, process_ok=True,
        )
        metrics["stream_bytes"] = stream_bytes
        passed = all(metrics["checks"].values())
        return {"status": "PASS" if passed else "FAIL", "metrics": metrics}


def run_live_prompt_evals(
    *, enabled: bool = False, fixtures_path: Path = FIXTURES_PATH,
    model: str = "gpt-5.6-luna", reasoning_effort: str = "high",
    executor: CodexLunaHighPromptExecutor | None = None,
) -> list[dict[str, Any]]:
    """Run each canonical prompt fixture through one explicit Luna-high executor.

    The normal response is ``SKIP``.  No caller may turn an unavailable Luna
    route into a passing result or substitute another model.
    """
    if not enabled:
        return [{"status": "SKIP", "reason": "live flag not supplied; no live prompt evidence"}]
    assert_live_prompt_eval_configuration(model=model, reasoning_effort=reasoning_effort)
    active_executor = executor or CodexLunaHighPromptExecutor()
    results: list[dict[str, Any]] = []
    for case in load_prompt_eval_fixtures(fixtures_path)["cases"]:
        if not isinstance(case, dict):
            raise RuntimeError("prompt-eval case is invalid")
        markers = case.get("assignment_markers")
        if not isinstance(markers, list):
            raise RuntimeError("prompt-eval case has no assignment markers")
        result = active_executor.execute(
            _fixture_prompt(case), model=model, reasoning_effort=reasoning_effort, assignment_markers=markers,
        )
        status = str(result.get("status"))
        results.append({
            "id": case["id"], "status": status,
            "model": model, "reasoning_effort": reasoning_effort,
            "result": result,
        })
    return results


__all__ = [
    "CodexLunaHighPromptExecutor",
    "build_live_prompt_eval_command",
    "live_evaluation_instruction",
    "live_response_schema",
    "normalize_live_behavioral_metrics",
    "run_live_prompt_evals",
    "_safe_live_failure_reason",
    "validate_live_prompt_eval_command",
]
