"""Black-box store guarantees for the domain command receipt boundary."""
from __future__ import annotations

import tempfile
import threading
import unittest
import ast
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from contextlib import contextmanager
from multiprocessing import get_context
from pathlib import Path
from unittest.mock import patch

from cortex_runtime.v12_contract import record_ref
from cortex_runtime.domain_kernel import DecisionAggregate
from cortex_runtime.filesystem_policy import assert_runtime_mutation_conformance
from cortex_runtime.v12_store import V12Store, V12StoreError, _ADMISSION_DEADLINE, _storage_error


_CORTEX_SCRIPT = Path(__file__).resolve().parents[1] / "plugins" / "cortex" / "scripts" / "cortex.py"
_V12_STORE_SOURCE = _CORTEX_SCRIPT.parent / "cortex_runtime" / "v12_store.py"


def _stdio_tool_call(
    home: str, tool_name: str, arguments: dict, ready: object, start: object, results: object,
) -> None:
    """Independent MCP runtime from process startup through shard admission."""
    env = dict(os.environ) | {"CODEX_HOME": home, "CORTEX_SOURCE_MODE": "1", "PYTHONDONTWRITEBYTECODE": "1"}
    env.pop("PYTHONPATH", None)
    # A test-only ``sitecustomize`` observer runs in the *exec'd* source MCP
    # process.  A parent-process monkeypatch would not observe this boundary,
    # while SQLite's C implementation remains deliberately outside the Python
    # filesystem wrappers being observed here.
    sidecar_guard = env.get("CORTEX_TEST_SIDECAR_MUTATION_GUARD")
    if sidecar_guard:
        env["PYTHONPATH"] = str(Path(sidecar_guard).parent)
    process = subprocess.Popen(
        [sys.executable, str(_CORTEX_SCRIPT)], stdin=subprocess.PIPE,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env,
    )
    reply: dict | None = None
    failure: tuple[str, str] | None = None
    ready_sent = False
    try:
        assert process.stdin is not None and process.stdout is not None
        def call(value: dict) -> dict:
            process.stdin.write(json.dumps(value) + "\n"); process.stdin.flush()
            line = process.stdout.readline()
            if not line.strip():
                diagnostic = ""
                if process.poll() is not None and process.stderr is not None:
                    diagnostic = process.stderr.read().strip()
                raise RuntimeError(
                    "source MCP process closed stdout before a JSON-RPC response"
                    + (f": {diagnostic[:500]}" if diagnostic else "")
                )
            return json.loads(line)
        call({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "d6", "version": "1"}}})
        process.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}}) + "\n"); process.stdin.flush()
        ready.put(True); ready_sent = True; start.wait(5)
        reply = call({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": tool_name, "arguments": arguments}})
    except BaseException as exc:  # parent assertion keeps source-process faults visible
        failure = (type(exc).__name__, str(exc))
        if not ready_sent:
            ready.put({"exception": failure[0], "message": failure[1]})
    finally:
        # ``cortex.py`` is a stdio server: EOF is its ordinary shutdown path.
        # Closing input first avoids manufacturing a SIGTERM-only process
        # lifetime into the two-independent-process admission measurement.
        if process.stdin is not None:
            process.stdin.close()
        forced_termination = False
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            forced_termination = True
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        stderr_text = process.stderr.read(512) if process.stderr is not None else ""
        metadata = {
            "exit_code": process.returncode,
            "forced_termination": forced_termination,
            "stderr_class": "present" if stderr_text.strip() else "empty",
        }
        if process.stdout is not None:
            process.stdout.close()
        if process.stderr is not None:
            process.stderr.close()
        if reply is not None:
            reply["_test_stdio"] = metadata
            results.put(reply)
        else:
            results.put({
                "exception": failure[0] if failure is not None else "source_process_failed",
                "message": failure[1] if failure is not None else "source MCP process did not return a response",
                "_test_stdio": metadata,
            })


def _source_stdio_tool_call(home: str, tool_name: str, arguments: dict) -> dict:
    """Issue one real source-mode MCP call from a fresh process."""
    context = get_context("fork")
    ready, results, start = context.Queue(), context.Queue(), context.Event()
    worker = context.Process(
        target=_stdio_tool_call,
        args=(home, tool_name, arguments, ready, start, results),
    )
    worker.start()
    try:
        assert ready.get(timeout=10) is True
        start.set()
        reply = results.get(timeout=15)
        worker.join(timeout=10)
        assert worker.exitcode == 0
        assert reply.get("_test_stdio", {}).get("exit_code") == 0, reply
        assert not reply.get("_test_stdio", {}).get("forced_termination"), reply
        return reply
    finally:
        for queue in (ready, results):
            queue.close()
            queue.join_thread()
        worker.close()


def _write_host_worker_receipt(
    plugin_data: str, worker_ref: str, *, authorize: bool = False,
    agent_id: str = "source-worker-a", turn_id: str = "source-worker-turn",
    session_id: str = "source-session",
) -> None:
    """Create one sanitized SubagentStart-equivalent host lease fixture."""
    import hashlib
    from cortex_runtime.audience_attestation import (
        authorize_worker_candidate_call,
        issue_worker_candidate,
    )

    dispatch = Path(plugin_data) / "activation" / "sessions" / "fixture" / "dispatch"
    dispatch.mkdir(mode=0o700, parents=True, exist_ok=True)
    for directory in (
        Path(plugin_data), Path(plugin_data) / "activation",
        Path(plugin_data) / "activation" / "sessions", dispatch.parent, dispatch,
    ):
        os.chmod(directory, 0o700)
    record = {
        "session_digest": hashlib.sha256(session_id.encode("utf-8")).hexdigest(),
        "assignment_ref_digest": hashlib.sha256(
            ("d_" + worker_ref[-12:]).encode("utf-8")
        ).hexdigest(),
        "worker_task_ref_digest": hashlib.sha256(
            worker_ref.encode("utf-8")
        ).hexdigest(),
        "worker_agent_digest": hashlib.sha256(agent_id.encode("utf-8")).hexdigest(),
        "worker_turn_digest": hashlib.sha256(turn_id.encode("utf-8")).hexdigest(),
        "worker_thread_digest": hashlib.sha256(agent_id.encode("utf-8")).hexdigest(),
    }
    record = issue_worker_candidate(Path(plugin_data), record)
    path = dispatch / ("dispatch-" + hashlib.sha256(worker_ref.encode("utf-8")).hexdigest() + ".json")
    path.write_text(
        json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=True),
        encoding="utf-8",
    )
    os.chmod(path, 0o600)
    if authorize:
        assert authorize_worker_candidate_call(
            Path(plugin_data), task_ref=worker_ref,
            agent_id=agent_id, turn_id=turn_id,
            session_id=session_id, tool_use_id="source-read-call",
        )


@contextmanager
def _source_stdio_session(
    home: str, *, host_identity: tuple[str, str, str] | None = None,
):
    """Keep one real source MCP connection alive across multiple calls."""
    env = dict(os.environ) | {
        "CODEX_HOME": home,
        "CORTEX_SOURCE_MODE": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    env.pop("PYTHONPATH", None)
    env.pop("CODEX_THREAD_ID", None)
    env.pop("CODEX_SESSION_ID", None)
    process = subprocess.Popen(
        [sys.executable, str(_CORTEX_SCRIPT)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, env=env,
    )
    request_id = 0
    notifications: list[dict] = []

    def rpc(method: str, params: dict) -> dict:
        nonlocal request_id
        request_id += 1
        assert process.stdin is not None and process.stdout is not None
        process.stdin.write(json.dumps({
            "jsonrpc": "2.0", "id": request_id,
            "method": method, "params": params,
        }) + "\n")
        process.stdin.flush()
        while True:
            line = process.stdout.readline()
            if not line.strip():
                diagnostic = process.stderr.read(512) if process.stderr is not None else ""
                raise AssertionError(
                    "source MCP session closed before replying"
                    + (f": {diagnostic}" if diagnostic else "")
                )
            payload = json.loads(line)
            if "id" not in payload and payload.get("method") == "notifications/tools/list_changed":
                notifications.append(payload)
                continue
            return payload

    initialize_result = rpc("initialize", {
        "protocolVersion": "2025-06-18", "capabilities": {},
        "clientInfo": {"name": "persistent-source-test", "version": "1"},
    })
    assert process.stdin is not None
    process.stdin.write(json.dumps({
        "jsonrpc": "2.0", "method": "notifications/initialized", "params": {},
    }) + "\n")
    process.stdin.flush()

    def call(tool_name: str, arguments: dict) -> dict:
        tool_use_id = f"source-call-{request_id + 1}"
        host_authorized = False
        plugin_data = env.get("PLUGIN_DATA") or str(Path(home) / "plugins" / "data" / "cortex-cortex")
        if (
            host_identity is not None
            and tool_name == "read_task"
            and arguments.get("view") in {None, "assignment"}
        ):
            from cortex_runtime.audience_attestation import authorize_worker_candidate_call
            agent_id, turn_id, session_id = host_identity
            host_authorized = authorize_worker_candidate_call(
                Path(plugin_data), task_ref=arguments.get("task_ref"),
                agent_id=agent_id, turn_id=turn_id,
                session_id=session_id, tool_use_id=tool_use_id,
            )
        result = rpc("tools/call", {"name": tool_name, "arguments": arguments})
        if host_authorized and result.get("result", {}).get("isError"):
            from cortex_runtime.audience_attestation import revoke_worker_candidate_call
            agent_id, turn_id, session_id = host_identity
            revoke_worker_candidate_call(
                Path(plugin_data), task_ref=arguments.get("task_ref"),
                agent_id=agent_id, turn_id=turn_id,
                session_id=session_id, tool_use_id=tool_use_id,
            )
        return result

    call.rpc = rpc  # type: ignore[attr-defined]
    call.notifications = notifications  # type: ignore[attr-defined]
    call.initialize_result = initialize_result  # type: ignore[attr-defined]

    try:
        yield call
    finally:
        if process.stdin is not None:
            process.stdin.close()
        process.wait(timeout=10)
        stderr_text = process.stderr.read(512) if process.stderr is not None else ""
        if process.stdout is not None:
            process.stdout.close()
        if process.stderr is not None:
            process.stderr.close()
        if process.returncode != 0:
            raise AssertionError(
                f"source MCP session exited {process.returncode}: {stderr_text}"
            )


class PublicPublicationFirstCallTests(unittest.TestCase):
    def test_signed_worker_candidate_accepts_exact_host_session_child_identity(self) -> None:
        from cortex_runtime.audience_attestation import (
            authorize_worker_candidate_call,
            claim_worker_candidate,
            release_worker_candidate_claim,
        )

        with tempfile.TemporaryDirectory(prefix="cortex-audience-session-") as plugin_data:
            worker_ref = "t_0123456789ab_" + "a" * 32
            _write_host_worker_receipt(plugin_data, worker_ref, authorize=False)
            self.assertIsNone(claim_worker_candidate(
                Path(plugin_data), task_ref=worker_ref,
                connection_nonce="premature-direct-client",
            ))
            self.assertTrue(authorize_worker_candidate_call(
                Path(plugin_data), task_ref=worker_ref,
                agent_id="source-worker-a", turn_id="source-worker-turn",
                session_id="source-session", tool_use_id="source-read-call",
            ))
            self.assertTrue(authorize_worker_candidate_call(
                Path(plugin_data), task_ref=worker_ref,
                agent_id="source-worker-a", turn_id="source-worker-turn",
                session_id="source-session", tool_use_id="corrected-read-call",
            ))
            receipt = next(Path(plugin_data).glob(
                "activation/sessions/*/dispatch/dispatch-*.json"
            ))
            self.assertEqual(
                json.loads(receipt.read_text(encoding="utf-8"))[
                    "authorized_tool_use_digest"
                ],
                hashlib.sha256(b"corrected-read-call").hexdigest(),
            )
            claim = claim_worker_candidate(
                Path(plugin_data), task_ref=worker_ref,
                connection_nonce="session-channel-regression",
            )
            self.assertIsNotNone(claim)
            self.assertEqual(
                claim["worker_task_ref_digest"],
                hashlib.sha256(worker_ref.encode("utf-8")).hexdigest(),
            )
            self.assertTrue(release_worker_candidate_claim(
                Path(plugin_data), claim=claim,
                connection_nonce="session-channel-regression",
            ))
            receipt = next(Path(plugin_data).glob(
                "activation/sessions/*/dispatch/dispatch-*.json"
            ))
            self.assertEqual(
                json.loads(receipt.read_text(encoding="utf-8"))["state"],
                "worker_call_authorized",
            )

    def test_signed_worker_candidate_tamper_is_rejected_without_state_change(self) -> None:
        from cortex_runtime.audience_attestation import claim_worker_candidate

        with tempfile.TemporaryDirectory(prefix="cortex-audience-tamper-") as plugin_data:
            worker_ref = "t_0123456789ab_" + "a" * 32
            _write_host_worker_receipt(plugin_data, worker_ref, authorize=False)
            path = next(Path(plugin_data).glob(
                "activation/sessions/*/dispatch/dispatch-*.json"
            ))
            record = json.loads(path.read_text(encoding="utf-8"))
            record["worker_task_ref_digest"] = "0" * 64
            path.write_text(json.dumps(record, sort_keys=True), encoding="utf-8")
            os.chmod(path, 0o600)
            self.assertIsNone(claim_worker_candidate(
                Path(plugin_data), task_ref=worker_ref,
                connection_nonce="tamper-regression",
            ))
            self.assertEqual(
                json.loads(path.read_text(encoding="utf-8"))["state"],
                "worker_candidate",
            )

    def test_stdio_tools_list_is_neutral_until_role_commitment(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cortex-audience-list-home-") as home, tempfile.TemporaryDirectory(
            prefix="cortex-audience-list-data-",
        ) as plugin_data:
            with _source_stdio_session(home) as coordinator:
                listed = coordinator.rpc("tools/list", {})  # type: ignore[attr-defined]
                names = {item["name"] for item in listed["result"]["tools"]}
                self.assertIn("open_assignment", names)
                self.assertIn("close_task", names)
                self.assertIn("read_task", names)
                self.assertIn("publish_plan", names)
                self.assertIn("publish_result", names)
                self.assertIn("publish_documentation", names)

            worker_ref = "t_0123456789ab_" + "a" * 32
            _write_host_worker_receipt(plugin_data, worker_ref, authorize=True)
            with patch.dict(os.environ, {"PLUGIN_DATA": plugin_data}), _source_stdio_session(
                home, host_identity=("source-worker-a", "source-worker-turn", "source-session"),
            ) as worker:
                listed = worker.rpc("tools/list", {})  # type: ignore[attr-defined]
                names = {item["name"] for item in listed["result"]["tools"]}
                self.assertIn("open_assignment", names)
                self.assertIn("publish_result", names)
                worker_read = next(
                    item for item in listed["result"]["tools"]
                    if item["name"] == "read_task"
                )
                self.assertEqual(
                    set(worker_read["inputSchema"]["properties"]),
                    {"task_ref", "continue"},
                )
                self.assertEqual(worker_read["inputSchema"]["required"], ["task_ref"])
                self.assertNotIn("const", worker_read["inputSchema"]["properties"]["task_ref"])
                self.assertFalse(worker_read["inputSchema"]["additionalProperties"])
                self.assertNotIn("worker_label", json.dumps(worker_read, sort_keys=True))

    def test_foreign_pre_spawn_hint_does_not_change_new_root_catalogue(self) -> None:
        from cortex_runtime.audience_attestation import issue_worker_catalogue_pending

        with tempfile.TemporaryDirectory(prefix="cortex-prespawn-list-home-") as home, tempfile.TemporaryDirectory(
            prefix="cortex-prespawn-list-data-",
        ) as plugin_data:
            worker_ref = "t_0123456789ab_" + "a" * 32
            dispatch = Path(plugin_data) / "activation" / "sessions" / "fixture" / "dispatch"
            dispatch.mkdir(mode=0o700, parents=True)
            for directory in (
                Path(plugin_data), Path(plugin_data) / "activation",
                Path(plugin_data) / "activation" / "sessions", dispatch.parent, dispatch,
            ):
                os.chmod(directory, 0o700)
            record = issue_worker_catalogue_pending(Path(plugin_data), {
                "session_digest": hashlib.sha256(b"source-session").hexdigest(),
                "assignment_ref_digest": hashlib.sha256(b"source-assignment").hexdigest(),
                "worker_task_ref_digest": hashlib.sha256(worker_ref.encode()).hexdigest(),
            })
            receipt = dispatch / "dispatch-prespawn.json"
            receipt.write_text(json.dumps(record, sort_keys=True), encoding="utf-8")
            os.chmod(receipt, 0o600)

            with patch.dict(os.environ, {"PLUGIN_DATA": plugin_data}), _source_stdio_session(home) as candidate:
                listed = candidate.rpc("tools/list", {})  # type: ignore[attr-defined]
                names = {item["name"] for item in listed["result"]["tools"]}
                self.assertIn("open_task", names)
                self.assertIn("open_assignment", names)
                self.assertIn("publish_result", names)
                rejected = candidate("read_task", {
                    "task_ref": worker_ref,
                })
                self.assertEqual(
                    rejected["result"]["structuredContent"]["error"]["code"],
                    "wrong_connection",
                )
                self.assertEqual(
                    json.loads(receipt.read_text(encoding="utf-8"))["state"],
                    "worker_catalogue_pending",
                )

    def test_parallel_candidate_connections_bind_only_after_exact_host_authorization(self) -> None:
        """Two candidate connections cannot consume each other's host-bound assignment."""
        with tempfile.TemporaryDirectory(prefix="cortex-exact-thread-home-") as home, tempfile.TemporaryDirectory(
            prefix="cortex-exact-thread-data-",
        ) as plugin_data:
            first_ref = "t_0123456789ab_" + "a" * 32
            second_ref = "t_0123456789ab_" + "b" * 32
            first_agent = "12345678-1234-4123-8123-123456789abc"
            second_agent = "22345678-1234-4123-8123-123456789abc"
            _write_host_worker_receipt(plugin_data, first_ref, agent_id=first_agent, turn_id="first-turn")
            _write_host_worker_receipt(plugin_data, second_ref, agent_id=second_agent, turn_id="second-turn")
            with patch.dict(os.environ, {"PLUGIN_DATA": plugin_data}):
                with _source_stdio_session(
                    home, host_identity=(first_agent, "first-turn", "source-session"),
                ) as first, _source_stdio_session(
                    home, host_identity=(second_agent, "second-turn", "source-session"),
                ) as second:
                    for connection in (first, second):
                        listed = connection.rpc("tools/list", {})  # type: ignore[attr-defined]
                        names = {item["name"] for item in listed["result"]["tools"]}
                        self.assertIn("open_assignment", names)
                        self.assertIn("publish_result", names)
                    wrong = first("read_task", {
                        "task_ref": second_ref,
                    })
                    self.assertEqual(wrong["result"]["structuredContent"]["error"]["code"], "wrong_connection")
                    first_read = first("read_task", {
                        "task_ref": first_ref,
                    })
                    second_read = second("read_task", {
                        "task_ref": second_ref,
                    })
                    self.assertNotEqual(first_read["result"]["structuredContent"].get("error", {}).get("code"), "wrong_connection")
                    self.assertNotEqual(second_read["result"]["structuredContent"].get("error", {}).get("code"), "wrong_connection")

    def test_failed_candidate_bootstrap_does_not_change_role_or_consume_assignment(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cortex-candidate-error-home-") as home, tempfile.TemporaryDirectory(
            prefix="cortex-candidate-error-data-",
        ) as plugin_data:
            worker_ref = "t_0123456789ab_" + "a" * 32
            _write_host_worker_receipt(plugin_data, worker_ref, authorize=True)
            with patch.dict(os.environ, {"PLUGIN_DATA": plugin_data}), _source_stdio_session(
                home, host_identity=("source-worker-a", "source-worker-turn", "source-session"),
            ) as worker:
                receipt = next(Path(plugin_data).glob(
                    "activation/sessions/*/dispatch/dispatch-*.json"
                ))
                before = receipt.read_bytes()
                malformed = worker("read_task", {
                    "task_ref": worker_ref,
                    "worker_label": "invented",
                })
                self.assertEqual(
                    malformed["result"]["structuredContent"]["error"]["code"],
                    "validation_error",
                )
                self.assertIn(
                    json.loads(receipt.read_text(encoding="utf-8"))["state"],
                    {"worker_candidate", "worker_call_authorized"},
                )
                from cortex_runtime.audience_attestation import authorize_worker_candidate_call
                self.assertTrue(authorize_worker_candidate_call(
                    Path(plugin_data), task_ref=worker_ref,
                    agent_id="source-worker-a", turn_id="source-worker-turn",
                    session_id="source-session", tool_use_id="source-read-retry",
                ))
                wrong_server_field = worker("read_task", {
                    "task_ref": worker_ref, "view": "state",
                })
                self.assertIn(
                    wrong_server_field["result"]["structuredContent"]["error"]["code"],
                    {"validation_error", "task_not_found"},
                )
                self.assertIn(
                    json.loads(receipt.read_text(encoding="utf-8"))["state"],
                    {"worker_candidate", "worker_call_authorized"},
                )
                rejected = worker("read_task", {
                    "task_ref": "t_0123456789ab_" + "b" * 32,
                                    })
                self.assertEqual(
                    rejected["result"]["structuredContent"]["error"]["code"],
                    "wrong_connection",
                )
                listed = worker.rpc("tools/list", {})  # type: ignore[attr-defined]
                names = {item["name"] for item in listed["result"]["tools"]}
                self.assertIn("open_assignment", names)
                self.assertIn("publish_result", names)
                premature = worker("publish_result", {"task_ref": worker_ref})
                self.assertIn(
                    premature["result"]["structuredContent"]["error"]["code"],
                    {"connection_lost", "wrong_connection"},
                )

    def test_late_desktop_candidate_malformed_bootstrap_restores_unknown_connection(self) -> None:
        """A post-initialize host attestation does not survive a malformed read."""
        with tempfile.TemporaryDirectory(prefix="cortex-late-candidate-home-") as home, tempfile.TemporaryDirectory(
            prefix="cortex-late-candidate-data-",
        ) as plugin_data:
            worker_ref = "t_0123456789ab_" + "a" * 32
            with patch.dict(os.environ, {"PLUGIN_DATA": plugin_data}), _source_stdio_session(
                home, host_identity=("source-worker-a", "source-worker-turn", "source-session"),
            ) as connection:
                # The MCP process is already initialized here, matching the
                # Desktop lifecycle ordering observed in production.
                _write_host_worker_receipt(plugin_data, worker_ref)
                receipt = next(Path(plugin_data).glob(
                    "activation/sessions/*/dispatch/dispatch-*.json"
                ))
                before = receipt.read_bytes()
                malformed = connection("read_task", {
                    "task_ref": worker_ref,
                    "worker_label": "invented",
                })
                self.assertEqual(
                    malformed["result"]["structuredContent"]["error"]["code"],
                    "validation_error",
                )
                self.assertEqual(receipt.read_bytes(), before)
                listed = connection.rpc("tools/list", {})  # type: ignore[attr-defined]
                names = {item["name"] for item in listed["result"]["tools"]}
                self.assertIn("open_assignment", names)
                self.assertIn("publish_result", names)

    def test_open_assignment_first_stdio_call_uses_one_complete_instruction_field(self) -> None:
        """A complete advertised assignment crosses validation without invented fields."""
        with tempfile.TemporaryDirectory(prefix="cortex-assignment-first-call-") as home:
            arguments = {
                "task_ref": "t_0123456789ab",
                "role": "Planner.",
                "profile_name": "planner",
                "model": "gpt-5.6-sol",
                "reasoning_effort": "high",
                "responsibility": "planning",
                "goal": "Produce one plan.",
                "scope": "Read-only planning.",
                "instructions": "Consume the assignment, plan once, and stop.",
                "report_policy": "none",
            }
            accepted = _source_stdio_tool_call(home, "open_assignment", arguments)
            self.assertTrue(accepted["result"].get("isError"), accepted)
            self.assertEqual(
                accepted["result"]["structuredContent"]["error"]["code"],
                "task_not_found",
                accepted,
            )

            stale_planning_shape = _source_stdio_tool_call(
                home,
                "open_assignment",
                {**arguments, "outcomes": ["Produce one plan."]},
            )
            self.assertTrue(stale_planning_shape["result"].get("isError"), stale_planning_shape)
            stale_error = stale_planning_shape["result"]["structuredContent"]["error"]
            self.assertEqual(stale_error["code"], "validation_error", stale_planning_shape)
            self.assertEqual(stale_error["details"]["path"], "$.outcomes", stale_planning_shape)

            complete_delivery_scope = _source_stdio_tool_call(
                home,
                "open_assignment",
                {**arguments, "responsibility": "delivery"},
            )
            self.assertTrue(complete_delivery_scope["result"].get("isError"), complete_delivery_scope)
            delivery_error = complete_delivery_scope["result"]["structuredContent"]["error"]
            self.assertEqual(delivery_error["code"], "task_not_found", complete_delivery_scope)

            server_derived_recovery_scope = _source_stdio_tool_call(
                home,
                "open_assignment",
                {
                    **arguments,
                    "responsibility": "delivery",
                    "loss_recovery": {
                        "state": "blocked",
                        "reason": "The predecessor connection was confirmed lost.",
                        "evidence": ["The host recorded an explicit terminal loss."],
                    },
                },
            )
            self.assertTrue(server_derived_recovery_scope["result"].get("isError"), server_derived_recovery_scope)
            recovery_error = server_derived_recovery_scope["result"]["structuredContent"]["error"]
            self.assertEqual(recovery_error["code"], "task_not_found", server_derived_recovery_scope)

            rejected = _source_stdio_tool_call(
                home,
                "open_assignment",
                {**arguments, "instructions_extra": "Invented supplementary instructions."},
            )
            self.assertTrue(rejected["result"].get("isError"), rejected)
            error = rejected["result"]["structuredContent"]["error"]
            self.assertEqual(error["code"], "validation_error", rejected)
            self.assertEqual(error["details"]["path"], "$", rejected)
            self.assertEqual(error["details"]["field"], "instructions_extra", rejected)

    def test_publish_plan_first_stdio_call_accepts_explicit_empty_unresolved(self) -> None:
        """The advertised complete shape must cross real MCP validation on its first call."""
        with tempfile.TemporaryDirectory(prefix="cortex-plan-first-call-") as home:
            arguments = {
                "task_ref": "t_0123456789ab_" + "a" * 32,
                "summary": "Plan.",
                "scope": "Bounded scope.",
                "stages": [{"owner": "implementation", "work": ["Build."], "verification": ["Run focused tests."]}],
                "verification_facts": [{"state": "not_run", "summary": "Execution belongs to implementation."}],
                "outcome_coverage": [{"outcome": "Build.", "status": "planned", "verification": ["Mapped to implementation."]}],
                "risks": [],
                "unresolved": [],
                "status": "completed",
            }
            accepted = _source_stdio_tool_call(home, "publish_plan", arguments)
            self.assertTrue(accepted["result"].get("isError"), accepted)
            self.assertEqual(
                accepted["result"]["structuredContent"]["error"]["code"],
                "wrong_connection",
                accepted,
            )

            rejected = _source_stdio_tool_call(
                home, "publish_plan", {key: value for key, value in arguments.items() if key != "unresolved"},
            )
            self.assertTrue(rejected["result"].get("isError"), rejected)
            error = rejected["result"]["structuredContent"]["error"]
            self.assertEqual(error["code"], "wrong_connection", rejected)
            self.assertNotIn("details", error)


def _cross_process_identical_receipt(
    root: str, home: str, ready: object, start: object, results: object,
) -> None:
    """One independent process participating in a shared receipt race."""
    try:
        os.environ["CODEX_HOME"] = home
        store = V12Store(Path(root))
        ready.put(True)
        start.wait(5)

        def mutate(connection):
            connection.execute(
                "INSERT INTO idempotency(operation,idempotency_key,payload_digest,result_json,created_at) VALUES ('cross-process-marker','one','digest','{}','now')"
            )
            return {"ok": True}

        value, replayed = store.run_command_receipt(
            aggregate_type="task", aggregate_id="task-cross-process",
            command_name="cross_process", logical_slot="task-cross-process/open",
            request={"same": True}, mutate=mutate,
        )
        results.put(("ok", value, replayed))
    except BaseException as exc:  # parent asserts the bounded public outcome
        ready.put(("error", type(exc).__name__, getattr(exc, "code", None)))
        results.put(("error", type(exc).__name__, getattr(exc, "code", None)))


def _abandon_sqlite_admission_lease(root: str, home: str, ready: object) -> None:
    """Exit while holding one lease; kernel close must release its flock."""
    os.environ["CODEX_HOME"] = home
    store = V12Store(Path(root))
    with store._sqlite_admission_lock(time.monotonic() + 5):
        ready.put(True)
        ready.close()
        ready.join_thread()
        os._exit(0)


class CommandReceiptTests(unittest.TestCase):
    def test_all_public_tools_complete_one_persistent_stdio_flow(self) -> None:
        """Every advertised operation crosses a real persistent stdio boundary."""
        outcome = {
            "outcome": "Deliver and document one checked change.",
            "acceptance": ["The change and its evidence are durable."],
            "constraints": ["Keep the test project isolated."],
            "verification": ["The focused check passes."],
        }

        def worker_ref(reply: dict) -> str:
            match = re.search(
                r'"task_ref":"(t_[0-9a-f]{12}_[0-9a-f]{32})"',
                reply["result"]["structuredContent"]["native_dispatch"]["message"],
            )
            self.assertIsNotNone(match, reply)
            return match.group(1)

        with tempfile.TemporaryDirectory(prefix="cortex-all-tools-home-") as home, tempfile.TemporaryDirectory(
            prefix="cortex-all-tools-project-",
        ) as project:
            plugin_data = str(Path(home) / "plugins" / "data" / "cortex-cortex")
            called: set[str] = set()

            with _source_stdio_session(home) as coordinator:
                catalogue = coordinator.rpc("tools/list", {})  # type: ignore[attr-defined]
                advertised = {item["name"] for item in catalogue["result"]["tools"]}

                def coordinator_call(name: str, arguments: dict) -> dict:
                    called.add(name)
                    reply = coordinator(name, arguments)
                    self.assertFalse(reply["result"].get("isError"), (name, reply))
                    return reply

                opened = coordinator_call("open_task", {
                    "project_root": project,
                    "request_original": "Deliver and document one checked change.",
                    "user_language": "en",
                    "outcomes": [outcome],
                    "constraints": outcome["constraints"],
                })
                task_ref = opened["result"]["structuredContent"]["task_ref"]
                coordinator_call("read_state", {"task_ref": task_ref})
                coordinator_call("read_scope", {
                    "task_ref": task_ref, "responsibility": "delivery",
                })
                coordinator_call("read_outcome", {
                    "task_ref": task_ref, "outcome": outcome["outcome"],
                })
                coordinator_call("read_continuations", {"task_ref": task_ref})
                coordinator_call("read_evidence", {
                    "task_ref": task_ref, "report_policy": "all_finalized",
                })
                coordinator_call("read_timeline", {"task_ref": task_ref})
                coordinator_call("open_clarification", {
                    "task_ref": task_ref,
                    "prompt": "Should the isolated checked change proceed?",
                    "prompt_language": "en",
                })
                coordinator_call("record_clarification", {
                    "task_ref": task_ref,
                    "response_original": "Proceed.",
                    "user_language": "en",
                })
                coordinator_call("assess_governance", {
                    "task_ref": task_ref, "mode": "light",
                    "rationale": "Exercise the complete public contract.",
                    "risk_factors": [],
                })
                planning = coordinator_call("open_assignment", {
                    "task_ref": task_ref, "role": "Planner",
                    "profile_name": "planner", "model": "gpt-5.6-luna",
                    "reasoning_effort": "high", "responsibility": "planning",
                    "goal": "Plan the isolated checked change.",
                    "scope": "The complete current contract.",
                    "instructions": "Consume the assignment and publish one terminal plan.",
                    "report_policy": "none",
                })
                planning_ref = worker_ref(planning)
                _write_host_worker_receipt(
                    plugin_data, planning_ref,
                    agent_id="all-tools-planner", turn_id="all-tools-plan-turn",
                    session_id="all-tools-session",
                )
                with _source_stdio_session(
                    home,
                    host_identity=("all-tools-planner", "all-tools-plan-turn", "all-tools-session"),
                ) as planner:
                    called.add("read_task")
                    consumed = planner("read_task", {"task_ref": planning_ref})
                    self.assertFalse(consumed["result"].get("isError"), consumed)
                    called.add("publish_plan")
                    published_plan = planner("publish_plan", {
                        "task_ref": planning_ref, "status": "completed",
                        "summary": "A bounded implementation plan is ready.",
                        "scope": "The single checked change.",
                        "stages": [{
                            "owner": "Implementation worker",
                            "work": ["Implement and document the checked change."],
                            "verification": ["Run the focused check."],
                        }],
                        "verification_facts": [{
                            "state": "not_run", "summary": "Implementation has not started.",
                        }],
                        "outcome_coverage": [{
                            "outcome": outcome["outcome"], "status": "planned",
                            "verification": ["The plan contains a focused check."],
                        }],
                        "risks": ["The user-visible change requires review."], "unresolved": [],
                    })
                    self.assertFalse(published_plan["result"].get("isError"), published_plan)

                coordinator_call("read_evidence", {
                    "task_ref": task_ref, "report_policy": "active_plan",
                })
                coordinator_call("open_plan_review", {
                    "task_ref": task_ref,
                    "prompt": "Approve the displayed bounded plan?",
                    "prompt_language": "en",
                })
                coordinator_call("record_plan_review", {
                    "task_ref": task_ref, "response_original": "Approved.",
                    "user_language": "en", "outcome": "approve",
                })
                coordinator_call("open_steering", {
                    "task_ref": task_ref,
                    "prompt": "Keep the approved scope unchanged?",
                    "prompt_language": "en",
                })
                coordinator_call("record_steering", {
                    "task_ref": task_ref, "response_original": "Keep it unchanged.",
                    "user_language": "en", "add": [], "retire": [],
                })
                delivery = coordinator_call("open_assignment", {
                    "task_ref": task_ref, "role": "Implementation worker",
                    "profile_name": "fullstack_dev", "model": "gpt-5.6-luna",
                    "reasoning_effort": "high", "responsibility": "delivery",
                    "goal": "Deliver and document the checked change.",
                    "scope": "The single current outcome.",
                    "instructions": "Consume the assignment, publish the result, then publish documentation impact.",
                    "report_policy": "active_plan",
                })
                delivery_ref = worker_ref(delivery)
                _write_host_worker_receipt(
                    plugin_data, delivery_ref,
                    agent_id="all-tools-worker", turn_id="all-tools-delivery-turn",
                    session_id="all-tools-session",
                )
                with _source_stdio_session(
                    home,
                    host_identity=("all-tools-worker", "all-tools-delivery-turn", "all-tools-session"),
                ) as worker:
                    called.add("read_task")
                    consumed = worker("read_task", {"task_ref": delivery_ref})
                    self.assertFalse(consumed["result"].get("isError"), consumed)
                    common = {
                        "task_ref": delivery_ref, "status": "completed",
                        "verification_facts": [{
                            "state": "executed", "summary": "The focused check passed.",
                        }],
                        "outcome_coverage": [{
                            "outcome": outcome["outcome"], "status": "complete",
                            "verification": ["The focused check passed."],
                        }],
                        "risks": [], "unresolved": [],
                    }
                    called.add("publish_result")
                    result = worker("publish_result", {
                        **common, "summary": "The checked change is complete.",
                        "outcome": "The isolated change works.", "changes": [],
                        "documentation_impact": "Documentation impact was assessed.",
                    })
                    self.assertFalse(result["result"].get("isError"), result)
                    called.add("publish_documentation")
                    documentation = worker("publish_documentation", {
                        **common, "summary": "Documentation impact is complete.",
                        "documentation_impact": "No additional documentation update is needed.",
                        "findings": [], "recommendations": [],
                    })
                    self.assertFalse(documentation["result"].get("isError"), documentation)

                coordinator_call("read_evidence", {
                    "task_ref": task_ref, "report_policy": "all_finalized",
                })
                coordinator_call("open_clarification", {
                    "task_ref": task_ref,
                    "prompt": "Review the current result: revise this task or close it?",
                    "prompt_language": "en", "purpose": "closure_review",
                    "options": ["revise", "close"],
                })
                coordinator_call("record_clarification", {
                    "task_ref": task_ref, "response_original": "Close it.",
                    "user_language": "en", "outcome": "close",
                })
                coordinator_call("close_task", {
                    "task_ref": task_ref, "verdict": "ready",
                })

                self.assertEqual(called, advertised)

    def test_source_stdio_steering_without_retirement_needs_no_contract_read(self) -> None:
        """A review answer with no retirement records immediately and exactly once."""
        outcome = {
            "outcome": "Preserve one steering outcome.",
            "acceptance": ["The current outcome remains exact."],
            "constraints": ["Do not infer contract state from a summary."],
            "verification": ["The steering record is accepted once."],
        }
        with tempfile.TemporaryDirectory(prefix="cortex-steer-home-") as home, tempfile.TemporaryDirectory(
            prefix="cortex-steer-project-",
        ) as project:
            with _source_stdio_session(home) as coordinator:
                opened = coordinator("open_task", {
                    "project_root": project,
                    "request_original": "Exercise same-connection steering admission.",
                    "user_language": "en", "outcomes": [outcome],
                    "constraints": outcome["constraints"],
                })
                task_ref = opened["result"]["structuredContent"]["task_ref"]

                before = coordinator("read_state", {"task_ref": task_ref})
                self.assertFalse(before["result"].get("isError"), before)
                pending = coordinator("open_steering", {
                    "task_ref": task_ref,
                    "prompt": "Keep the current outcome unchanged?",
                    "prompt_language": "en",
                })
                self.assertFalse(pending["result"].get("isError"), pending)

                recorded = coordinator("record_steering", {
                    "task_ref": task_ref, "response_original": "Keep it.",
                    "user_language": "en", "add": [], "retire": [],
                })
                self.assertFalse(recorded["result"].get("isError"), recorded)
                self.assertEqual(
                    recorded["result"]["structuredContent"]["state"],
                    "steering_recorded",
                )

                replay = coordinator("record_steering", {
                    "task_ref": task_ref, "response_original": "Keep it.",
                    "user_language": "en", "add": [], "retire": [],
                })
                self.assertFalse(replay["result"].get("isError"), replay)
                self.assertTrue(replay["result"]["structuredContent"]["replayed"])

    def test_source_stdio_point_steering_reads_only_scope_and_selected_outcome(self) -> None:
        """Retirement observes an exact name; replacement reads only that outcome."""
        outcome = {
            "outcome": "Preserve one exact steering outcome.",
            "acceptance": ["The original criterion remains."],
            "constraints": ["Do not load the complete contract."],
            "verification": ["The replacement is committed once."],
        }
        refined = {
            **outcome,
            "acceptance": [*outcome["acceptance"], "The refined criterion is added."],
        }
        with tempfile.TemporaryDirectory(prefix="cortex-point-steer-home-") as home, tempfile.TemporaryDirectory(
            prefix="cortex-point-steer-project-",
        ) as project:
            with _source_stdio_session(home) as coordinator:
                opened = coordinator("open_task", {
                    "project_root": project,
                    "request_original": "Refine one exact outcome.",
                    "user_language": "en", "outcomes": [outcome],
                    "constraints": outcome["constraints"],
                })
                task_ref = opened["result"]["structuredContent"]["task_ref"]
                pending = coordinator("open_steering", {
                    "task_ref": task_ref,
                    "prompt": "Add the refined criterion to the existing outcome?",
                    "prompt_language": "en",
                })
                self.assertFalse(pending["result"].get("isError"), pending)

                unobserved = coordinator("record_steering", {
                    "task_ref": task_ref, "response_original": "Apply the refinement.",
                    "user_language": "en", "add": [refined],
                    "retire": [outcome["outcome"]],
                })
                self.assertTrue(unobserved["result"].get("isError"), unobserved)
                self.assertEqual(
                    unobserved["result"]["structuredContent"]["error"]["code"],
                    "fresh_state_read_required",
                )
                scope = coordinator("read_scope", {
                    "task_ref": task_ref, "responsibility": "delivery",
                })
                self.assertFalse(scope["result"].get("isError"), scope)
                self.assertEqual(
                    [item["outcome"] for item in scope["result"]["structuredContent"]["data"]["outcomes"]],
                    [outcome["outcome"]],
                )
                exact = coordinator("read_outcome", {
                    "task_ref": task_ref, "outcome": outcome["outcome"],
                })
                self.assertFalse(exact["result"].get("isError"), exact)
                self.assertEqual(
                    exact["result"]["structuredContent"]["data"]["outcome"],
                    outcome,
                )
                recorded = coordinator("record_steering", {
                    "task_ref": task_ref, "response_original": "Apply the refinement.",
                    "user_language": "en", "add": [refined],
                    "retire": [outcome["outcome"]],
                })
                self.assertFalse(recorded["result"].get("isError"), recorded)

    def test_connection_loss_has_explicit_stdio_replacement_route(self) -> None:
        """A dead consumed worker is replaced only through linked loss evidence."""
        outcome = {
            "outcome": "Recover one lost stdio worker.",
            "acceptance": ["One linked replacement publishes terminal evidence."],
            "constraints": ["Never recover the dead connection's authority."],
            "verification": ["The predecessor is stale and the successor report exists."],
        }
        with tempfile.TemporaryDirectory(prefix="cortex-loss-home-") as home, tempfile.TemporaryDirectory(
            prefix="cortex-loss-project-",
        ) as project:
            plugin_data = str(Path(home) / "plugins" / "data" / "cortex-cortex")
            with _source_stdio_session(home) as coordinator:
                opened = coordinator("open_task", {
                    "project_root": project,
                    "request_original": "Recover one explicitly lost worker.",
                    "user_language": "en", "outcomes": [outcome],
                    "constraints": outcome["constraints"],
                })
                task_ref = opened["result"]["structuredContent"]["task_ref"]
                assessed = coordinator("assess_governance", {
                    "task_ref": task_ref, "mode": "minimal",
                    "rationale": "Process-bound recovery regression.",
                    "risk_factors": [],
                })
                self.assertFalse(assessed["result"].get("isError"), assessed)
                original = coordinator("open_assignment", {
                    "task_ref": task_ref, "role": "Original worker",
                    "profile_name": "backend_dev", "model": "gpt-5.6-luna",
                    "reasoning_effort": "high", "responsibility": "delivery",
                    "goal": "Complete the assigned recovery outcome.",
                    "scope": "Only the exact selected outcome.",
                    "instructions": "Consume the assignment and publish once.",
                    "outcomes": [outcome["outcome"]], "report_policy": "none",
                })
                original_ref = re.search(
                    r'"task_ref":"(t_[0-9a-f]{12}_[0-9a-f]{32})"',
                    original["result"]["structuredContent"]["native_dispatch"]["message"],
                ).group(1)
                _write_host_worker_receipt(plugin_data, original_ref)
                with _source_stdio_session(
                    home, host_identity=("source-worker-a", "source-worker-turn", "source-session"),
                ) as dead_worker:
                    consumed = dead_worker("read_task", {
                        "task_ref": original_ref,
                    })
                    self.assertFalse(consumed["result"].get("isError"), consumed)
                    self.assertIn("effective_contract", consumed["result"]["structuredContent"]["data"])

                lost = _source_stdio_tool_call(home, "read_task", {
                    "task_ref": original_ref,
                })
                self.assertEqual(
                    lost["result"]["structuredContent"]["error"]["code"],
                    "connection_lost",
                )
                recovery_state = coordinator("read_scope", {
                    "task_ref": task_ref, "responsibility": "delivery",
                })
                recovery_items = recovery_state["result"]["structuredContent"][
                    "data"
                ]["outcomes"]
                self.assertEqual(
                    recovery_items[0]["loss_recovery_outcomes"],
                    [outcome["outcome"]],
                )
                replacement = coordinator("open_assignment", {
                    "task_ref": task_ref, "role": "Replacement worker",
                    "profile_name": "backend_dev", "model": "gpt-5.6-luna",
                    "reasoning_effort": "high", "responsibility": "delivery",
                    "goal": "Complete the confirmed lost worker outcome.",
                    "scope": "Only the exact selected outcome.",
                    "instructions": "Consume the linked successor and publish once.",
                    "outcomes": [outcome["outcome"]], "report_policy": "none",
                    "loss_recovery": {
                        "state": "aborted",
                        "reason": "The original stdio worker process exited after consumption.",
                        "evidence": ["The process closed without a publication response."],
                    },
                })
                self.assertFalse(replacement["result"].get("isError"), replacement)
                replacement_ref = re.search(
                    r'"task_ref":"(t_[0-9a-f]{12}_[0-9a-f]{32})"',
                    replacement["result"]["structuredContent"]["native_dispatch"]["message"],
                ).group(1)
                _write_host_worker_receipt(plugin_data, replacement_ref)
                with _source_stdio_session(
                    home, host_identity=("source-worker-a", "source-worker-turn", "source-session"),
                ) as successor:
                    consumed = successor("read_task", {
                        "task_ref": replacement_ref,
                    })
                    self.assertFalse(consumed["result"].get("isError"), consumed)
                    published = successor("publish_result", {
                        "task_ref": replacement_ref,
                        "summary": "The linked replacement completed.",
                        "outcome": "The exact recovery outcome is complete.",
                        "changes": [],
                        "verification_facts": [{
                            "state": "executed",
                            "summary": "The predecessor was recorded aborted before successor creation.",
                        }],
                        "outcome_coverage": [{
                            "outcome": outcome["outcome"], "status": "complete",
                            "verification": ["The successor publication was accepted."],
                        }],
                        "documentation_impact": "No documentation change required.",
                        "risks": [], "unresolved": [], "status": "completed",
                    })
                    self.assertFalse(published["result"].get("isError"), published)

            prior = os.environ.get("CODEX_HOME")
            os.environ["CODEX_HOME"] = home
            try:
                store, task_id = V12Store.for_task_ref(task_ref)
                with store._connection() as connection:
                    self.assertEqual(connection.execute(
                        "SELECT COUNT(*) FROM assignment_losses WHERE task_id=?",
                        (task_id,),
                    ).fetchone()[0], 1)
                    self.assertEqual(connection.execute(
                        "SELECT COUNT(*) FROM report_operations WHERE task_id=?",
                        (task_id,),
                    ).fetchone()[0], 1)
            finally:
                if prior is None:
                    os.environ.pop("CODEX_HOME", None)
                else:
                    os.environ["CODEX_HOME"] = prior

    def setUp(self) -> None:
        self.state = tempfile.TemporaryDirectory(prefix="cortex-command-receipts-home-")
        self.prior_codex_home = os.environ.get("CODEX_HOME")
        os.environ["CODEX_HOME"] = self.state.name
        self.root = tempfile.TemporaryDirectory(prefix="cortex-command-receipts-")
        self.store = V12Store(Path(self.root.name))

    def tearDown(self) -> None:
        self.root.cleanup()
        if self.prior_codex_home is None:
            os.environ.pop("CODEX_HOME", None)
        else:
            os.environ["CODEX_HOME"] = self.prior_codex_home
        self.state.cleanup()

    def test_forward_migration_and_exact_replay(self) -> None:
        result, replayed = self.store.run_command_receipt(
            aggregate_type="task", aggregate_id="task-1", command_name="example",
            logical_slot="task-1/example", request={"value": 1},
            mutate=lambda connection: {"accepted": True, "value": 1}, build_id="build-a",
        )
        self.assertFalse(replayed)
        self.assertEqual(result["value"], 1)
        replay, was_replay = self.store.run_command_receipt(
            aggregate_type="task", aggregate_id="task-1", command_name="example",
            logical_slot="task-1/example", request={"value": 1},
            mutate=lambda connection: self.fail("replay must not invoke mutation"), build_id="build-a",
        )
        self.assertTrue(was_replay)
        self.assertEqual(replay, result)
        self.assertEqual(self.store.lookup_command_receipt("task-1/example")["status"], "completed")

    def test_changed_request_is_conflict_and_failed_admission_writes_nothing(self) -> None:
        self.store.run_command_receipt(
            aggregate_type="task", aggregate_id="task-1", command_name="example",
            logical_slot="task-1/example", request={"value": 1},
            mutate=lambda connection: {"accepted": True},
        )
        with self.assertRaises(V12StoreError) as conflict:
            self.store.run_command_receipt(
                aggregate_type="task", aggregate_id="task-1", command_name="example",
                logical_slot="task-1/example", request={"value": 2},
                mutate=lambda connection: {"accepted": True},
            )
        self.assertEqual(conflict.exception.code, "command_conflict")
        with self.assertRaises(V12StoreError):
            self.store.run_command_receipt(
                aggregate_type="task", aggregate_id="task-2", command_name="failed",
                logical_slot="task-2/failed", request={"value": 1},
                mutate=lambda connection: (_ for _ in ()).throw(V12StoreError("incomplete", code="incomplete")),
            )
        self.assertIsNone(self.store.lookup_command_receipt("task-2/failed"))

    def test_concurrent_identical_commands_have_one_mutation(self) -> None:
        calls = 0
        lock = threading.Lock()

        def mutate(connection):
            nonlocal calls
            with lock:
                calls += 1
            return {"ok": True}

        outputs: list[tuple[dict, bool]] = []
        errors: list[BaseException] = []

        def worker() -> None:
            try:
                outputs.append(V12Store(Path(self.root.name)).run_command_receipt(
                    aggregate_type="task", aggregate_id="task-3", command_name="concurrent",
                    logical_slot="task-3/concurrent", request={"same": True}, mutate=mutate,
                ))
            except BaseException as exc:  # pragma: no cover - assertion below reports it
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertFalse(errors)
        self.assertEqual(calls, 1)
        self.assertEqual(len(outputs), 4)
        self.assertEqual(sum(not replayed for _, replayed in outputs), 1)

    def test_cross_process_contention_converges_on_one_receipt(self) -> None:
        """Real independent writers converge through the central receipt runner."""
        # This worker opens SQLite directly.  Use an exec-style child rather
        # than inheriting Python/SQLite process state from the test process;
        # the stdio regressions below independently exercise the source MCP
        # executable itself.
        with tempfile.TemporaryDirectory(prefix="cortex-cross-process-home-") as home:
            context = get_context("forkserver")
            ready = context.Queue()
            results = context.Queue()
            start = context.Event()
            workers = [context.Process(
                target=_cross_process_identical_receipt,
                args=(self.root.name, home, ready, start, results),
            ) for _ in range(2)]
            for worker in workers:
                worker.start()
            self.assertEqual([ready.get(timeout=10) for _ in workers], [True, True])
            start.set()
            for worker in workers:
                worker.join(timeout=10)
                self.assertEqual(worker.exitcode, 0)
            observed = [results.get(timeout=3) for _ in workers]
            self.assertEqual([item[0] for item in observed], ["ok", "ok"])
            self.assertEqual(sum(bool(item[2]) for item in observed), 1)
            prior = os.environ.get("CODEX_HOME")
            os.environ["CODEX_HOME"] = home
            try:
                store = V12Store(Path(self.root.name))
                with store._connection() as connection:
                    self.assertEqual(connection.execute(
                        "SELECT COUNT(*) FROM idempotency WHERE operation='cross-process-marker'",
                    ).fetchone()[0], 1)
            finally:
                if prior is None: os.environ.pop("CODEX_HOME", None)
                else: os.environ["CODEX_HOME"] = prior

    def test_forced_busy_lost_response_restart_reconciles_without_mutation(self) -> None:
        """A committed receipt remains the sole recovery authority after busy."""
        calls = 0

        def mutate(connection):
            nonlocal calls
            calls += 1
            connection.execute(
                "INSERT INTO idempotency(operation,idempotency_key,payload_digest,result_json,created_at) VALUES ('forced-busy-marker','one','digest','{}','now')"
            )
            return {"accepted": True}

        original, replayed = self.store.run_command_receipt(
            aggregate_type="task", aggregate_id="task-forced-busy", command_name="forced_busy",
            logical_slot="task-forced-busy/open", request={"same": True}, mutate=mutate,
        )
        self.assertFalse(replayed)
        restarted = V12Store(Path(self.root.name))
        # Model a lost transport response where retry starts after a bounded
        # busy acquisition has already observed the original commit. The
        # executor must use its read-only reconciliation path, never mutate.
        with patch.object(
            restarted, "_write", side_effect=V12StoreError("busy", code="storage_busy"),
        ):
            recovered, was_replay = restarted.run_command_receipt(
                aggregate_type="task", aggregate_id="task-forced-busy", command_name="forced_busy",
                logical_slot="task-forced-busy/open", request={"same": True},
                mutate=lambda connection: self.fail("busy reconciliation must not mutate"),
            )
        self.assertTrue(was_replay)
        self.assertEqual(recovered, original)
        self.assertEqual(calls, 1)
        with self.assertRaises(V12StoreError) as changed:
            restarted.run_command_receipt(
                aggregate_type="task", aggregate_id="task-forced-busy", command_name="forced_busy",
                logical_slot="task-forced-busy/open", request={"same": False},
                mutate=lambda connection: self.fail("changed command must conflict before mutation"),
            )
        self.assertEqual(changed.exception.code, "command_conflict")

    def test_shared_admission_budget_retries_pre_receipt_busy_and_preserves_primary_error(self) -> None:
        """WAL/readiness/compact-ref admission shares receipt contention policy."""
        attempts = 0

        def transient() -> str:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise V12StoreError("busy", code="storage_busy")
            return "ready"

        self.assertEqual(self.store._with_storage_admission(transient), "ready")
        self.assertEqual(attempts, 2)
        # Closing an ordinary connection performs no WAL/SHM housekeeping, so
        # the primary typed error returns unchanged.
        with self.assertRaises(V12StoreError) as raised:
            with self.store._connection():
                raise V12StoreError("busy", code="storage_busy")
        self.assertEqual(raised.exception.code, "storage_busy")

    def test_production_wal_shm_paths_are_validation_only(self) -> None:
        """No latent source helper may mutate a live SQLite-owned sidecar."""
        source = _V12_STORE_SOURCE.read_text(encoding="utf-8")
        self.assertNotIn("_secure_sqlite_sidecar", source)
        tree = ast.parse(source)
        prohibited = ("os.open", "os.chmod", "os.fchmod", ".unlink(", "os.replace", ".truncate(", ".write(")
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            segment = ast.get_source_segment(source, node) or ""
            if "-wal" in segment or "-shm" in segment:
                self.assertFalse(any(token in segment for token in prohibited), node.name)

    def test_package_mutation_boundary_rejects_bypass_shapes(self) -> None:
        """The package policy rejects computed, aliased, indirect, and dynamic writes."""
        samples = {
            "computed.py": "import os\ndef probe(base):\n os.unlink(base + '-wal')\n",
            "import_alias.py": "from os import unlink as erase\ndef probe(path):\n erase(path)\n",
            "assignment_alias.py": "import os\nerase = os.unlink\ndef probe(path):\n erase(path)\n",
            "helper_alias.py": "import os\nerase = os.unlink\ndef helper(path):\n erase(path)\ndef probe(path):\n helper(path)\n",
            "pathlib_alias.py": "import pathlib as p\ndef probe(path):\n p.Path(path).unlink()\n",
            "shutil_alias.py": "from shutil import move as relocate\ndef probe(path):\n relocate(path, path + '.next')\n",
            "dynamic.py": "import os\ndef probe(path):\n getattr(os, 'unlink')(path)\n",
            "subscript_call.py": "import os\ndef probe(path):\n os.__dict__['unlink'](path)\n",
            "nested_subscript_call.py": "import os\ndef probe(path):\n os.__dict__['__dict__']['unlink'](path)\n",
            "global_storage.py": "import os\nERASE = os.unlink\n",
            "default_storage.py": "import os\ndef probe(path, erase=os.unlink):\n erase(path)\n",
            "closure_storage.py": "import os\ndef outer():\n erase = os.unlink\n def probe(path):\n  erase(path)\n return probe\n",
            "return_callable.py": "import os\ndef probe():\n return os.unlink\n",
            "partial_callable.py": "import os\nfrom functools import partial\ndef probe():\n return partial(os.unlink, 'x')\n",
            "callback_callable.py": "import os\ndef callback(fn):\n return fn\ndef probe():\n return callback(os.unlink)\n",
            "container_storage.py": "import os\nCALLBACKS = [os.unlink]\nMAPPING = {'erase': os.unlink}\n",
            "attribute_storage.py": "import os\ndef probe(target):\n target.callback = os.unlink\n",
            "yield_callable.py": "import os\ndef probe():\n yield os.unlink\n",
            "nested_return_call.py": "import os\ndef helper():\n return os.unlink\ndef probe(path):\n return helper()(path)\n",
            "returned_path_constructor.py": "import pathlib as p\ndef helper():\n return p.Path\ndef probe(path):\n helper()(path).unlink()\n",
            "returned_path_constructor_assigned.py": "import pathlib as p\ndef helper():\n return p.Path\nctor = helper()\ndef probe(path):\n ctor(path).unlink()\n",
            "returned_path_constructor_two_hop.py": "import pathlib as p\ndef first():\n return p.Path\ndef second():\n return first()\ndef probe(path):\n second()(path).unlink()\n",
            "returned_pathlib_module.py": "import pathlib as p\ndef helper():\n return p\ndef probe(path):\n helper().Path(path).unlink()\n",
            "returned_os_module.py": "import os\ndef helper():\n return os\ndef probe(path):\n helper().unlink(path)\n",
            "returned_shutil_module.py": "import shutil\ndef helper():\n return shutil\ndef probe(path):\n helper().rmtree(path)\n",
            "nested/bypass.py": "import os\ndef probe(path):\n os.unlink(path)\n",
        }
        for filename, body in samples.items():
            with self.subTest(filename=filename), tempfile.TemporaryDirectory() as directory:
                target = Path(directory, filename)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(body, encoding="utf-8")
                with self.assertRaises(AssertionError):
                    assert_runtime_mutation_conformance(Path(directory))
        assert_runtime_mutation_conformance(_V12_STORE_SOURCE.parent)

    def test_observation_lease_mutations_have_explicit_capabilities(self) -> None:
        """New lease/journal writes cannot silently bypass the central policy."""
        from cortex_runtime.filesystem_policy import _CAPABILITIES
        required = {
            "event_journal.EventJournal._open",
            "event_journal.EventJournal.emit",
            "event_journal.EventJournal.emit_server_ready",
            "observation_generation._locked",
            "observation_generation._write",
            "observation_generation.create_session_intent",
            "observation_generation.consume_intent",
            "observation_generation.claim_generation",
            "observation_generation.write_ready_receipt",
            "observation_generation.revoke_session",
        }
        self.assertTrue(required.issubset(_CAPABILITIES))
        assert_runtime_mutation_conformance(_V12_STORE_SOURCE.parent)

    def test_sqlite_admission_lock_is_reentrant_released_and_sequential(self) -> None:
        """The process-local guard never contends with its own descriptor lock."""
        deadline = time.monotonic() + 1
        with self.store._sqlite_admission_lock(deadline):
            with self.store._sqlite_admission_lock(deadline):
                self.assertTrue((self.store.root / ".sqlite-admission.lock").is_file())
        with self.assertRaisesRegex(RuntimeError, "injected"):
            with self.store._sqlite_admission_lock(deadline):
                raise RuntimeError("injected")
        with self.store._sqlite_admission_lock(time.monotonic() + 1):
            pass
        restarted = V12Store(Path(self.root.name))
        with restarted._sqlite_admission_lock(time.monotonic() + 1):
            pass

    def test_sqlite_admission_lease_recovers_after_process_exit_and_is_shard_local(self) -> None:
        """One abandoned shard lease cannot block its successor or another shard."""
        with tempfile.TemporaryDirectory(prefix="cortex-admission-home-") as home, tempfile.TemporaryDirectory(
            prefix="cortex-admission-other-root-"
        ) as other_root:
            context = get_context("forkserver")
            ready = context.Queue()
            worker = context.Process(
                target=_abandon_sqlite_admission_lease,
                args=(self.root.name, home, ready),
            )
            worker.start()
            self.assertTrue(ready.get(timeout=10))
            worker.join(timeout=10)
            self.assertEqual(worker.exitcode, 0)
            prior = os.environ.get("CODEX_HOME")
            os.environ["CODEX_HOME"] = home
            try:
                recovered = V12Store(Path(self.root.name))
                unrelated = V12Store(Path(other_root))
                with recovered._sqlite_admission_lock(time.monotonic() + 1):
                    with unrelated._sqlite_admission_lock(time.monotonic() + 1):
                        self.assertNotEqual(recovered.root, unrelated.root)
            finally:
                if prior is None: os.environ.pop("CODEX_HOME", None)
                else: os.environ["CODEX_HOME"] = prior

    def test_post_commit_write_never_normalizes_live_sqlite_sidecars(self) -> None:
        """Only serialized connection admission may touch an extant WAL/SHM."""
        statements: list[str] = []

        class Connection:
            def execute(self, statement: str) -> None:
                statements.append(statement)

        @contextmanager
        def admitted_connection():
            yield Connection()

        with patch.object(self.store, "_connection", admitted_connection), patch.object(
            self.store, "_protect_canonical_database"
        ), patch.object(
            self.store, "_protect_admitted_sidecars", side_effect=AssertionError("post-commit sidecar touch")
        ):
            self.assertEqual(self.store._write_once(lambda _connection: {"ok": True}), {"ok": True})
        self.assertEqual(statements, ["BEGIN IMMEDIATE", "COMMIT"])

    def test_admission_deadline_is_inherited_and_busy_uses_sqlite_code_not_text(self) -> None:
        observed: list[float | None] = []

        def outer() -> None:
            observed.append(_ADMISSION_DEADLINE.get())
            self.store._with_storage_admission(lambda: observed.append(_ADMISSION_DEADLINE.get()))

        self.store._with_storage_admission(outer)
        self.assertEqual(len(observed), 2)
        self.assertIsNotNone(observed[0])
        self.assertEqual(observed[0], observed[1])
        error = __import__("sqlite3").OperationalError("unrelated wording")
        error.sqlite_errorcode = __import__("sqlite3").SQLITE_BUSY
        self.assertEqual(_storage_error(error).code, "storage_busy")
        protocol = __import__("sqlite3").OperationalError("unrelated wording")
        protocol.sqlite_errorcode = __import__("sqlite3").SQLITE_PROTOCOL
        self.assertEqual(_storage_error(protocol).code, "storage_busy")
        not_busy = __import__("sqlite3").OperationalError("database is locked")
        self.assertEqual(_storage_error(not_busy).code, "storage_unavailable")

    def test_locator_refresh_failure_cannot_revoke_committed_canonical_result(self) -> None:
        """The locator sidecar is reconstructible, never mutation authority."""
        with patch.object(self.store, "_sync_record_locators", side_effect=V12StoreError("locator busy", code="storage_busy")):
            task, replayed = self.store.create_task(
                objective="Derived index.", user_request_original="Derived index.", user_language="en",
                requirements=["Keep canonical rows."], constraints=["Index is non-authoritative."],
                acceptance_criteria=["Restart resolves task."], verification_plan=["Restart resolves task."],
            )
        self.assertFalse(replayed)
        created = task["task"]
        self.assertIn("task_id", created)
        restarted = V12Store(Path(self.root.name))
        resolved, canonical = V12Store.for_task_ref(created["task_ref"])
        self.assertEqual(canonical, created["task_id"])
        self.assertEqual(resolved.project_hash, restarted.project_hash)

    def _decision_record_ref(self) -> tuple[dict, str]:
        """Create one canonical record suitable for cross-shard locator tests."""
        task, _ = self.store.create_task(
            objective="Derived locator record.", user_request_original="Derived locator record.", user_language="en",
            requirements=["Resolve canonical record."], constraints=["Locator is derived."],
            acceptance_criteria=["Fallback remains usable."], verification_plan=["Resolve after repair."],
        )
        task_id = task["task"]["task_id"]
        aggregate = DecisionAggregate(self.store)
        binding = aggregate.open_clarification(
            task_id=task_id, prompt="Repair the derived locator.", prompt_language="en",
        )
        created = aggregate.record_clarification(
            task_id=task_id, binding_ref=binding["binding"]["clarification_binding"],
            response_original="Continue.", user_language="en",
        )
        self.assertFalse(created["replayed"])
        identifier = created["decision"]["decision_id"]
        return task, record_ref(identifier)

    def test_canonical_fallback_survives_locator_repair_failure(self) -> None:
        """A verified record remains usable when only its accelerator repair fails."""
        _task, decision_ref = self._decision_record_ref()
        self.store._record_locator_path.unlink()
        with patch.object(V12Store, "_sync_record_locators", side_effect=V12StoreError("sidecar", code="storage_unavailable")):
            resolved, identifier = V12Store.for_record_ref(decision_ref, label="decision_id")
        self.assertEqual(record_ref(identifier), decision_ref)
        self.assertEqual(resolved.project_hash, self.store.project_hash)

    def test_malformed_locator_degrades_to_canonical_scan_then_repairs_for_restart(self) -> None:
        """Malformed derived bytes never become a canonical storage failure."""
        _task, decision_ref = self._decision_record_ref()
        self.store._record_locator_path.write_bytes(b"not a sqlite locator database")
        resolved, identifier = V12Store.for_record_ref(decision_ref, label="decision_id")
        self.assertEqual(record_ref(identifier), decision_ref)
        self.assertEqual(resolved.project_hash, self.store.project_hash)
        # A new process-equivalent resolver must now use the reconstructed
        # sidecar; any legacy scan here would prove repair was not durable.
        with patch.object(V12Store, "_legacy_record_ref_matches", side_effect=AssertionError("repair was not used")):
            restarted, repeated = V12Store.for_record_ref(decision_ref, label="decision_id")
        self.assertEqual(repeated, identifier)
        self.assertEqual(restarted.project_hash, self.store.project_hash)

    def test_bootstrap_locator_rebuild_failure_keeps_ready_canonical_database_usable(self) -> None:
        """A new store opens after canonical migration even if sidecar repair cannot run."""
        task, _ = self.store.create_task(
            objective="Bootstrap sidecar.", user_request_original="Bootstrap sidecar.", user_language="en",
            requirements=["Keep canonical database available."], constraints=["Sidecar is derived."],
            acceptance_criteria=["Restart opens canonical task."], verification_plan=["Resolve task after rebuild failure."],
        )
        with patch.object(V12Store, "_sync_record_locators", side_effect=V12StoreError("sidecar", code="storage_unavailable")):
            restarted = V12Store(Path(self.root.name))
        resolved, canonical = V12Store.for_task_ref(task["task"]["task_ref"])
        self.assertEqual(canonical, task["task"]["task_id"])
        self.assertEqual(resolved.project_hash, restarted.project_hash)

    def test_locator_repair_never_downgrades_canonical_schema_failure(self) -> None:
        """Only derived failures are best-effort after canonical readiness."""
        with patch.object(V12Store, "_sync_record_locators", side_effect=V12StoreError("schema", code="schema_unsupported")):
            with self.assertRaises(V12StoreError) as raised:
                V12Store(Path(self.root.name))
        self.assertEqual(raised.exception.code, "schema_unsupported")

    def test_two_process_stdio_identical_open_converges_through_full_admission(self) -> None:
        """Exercise source MCP startup, locator, shard readiness, and receipt."""
        with tempfile.TemporaryDirectory(prefix="cortex-d6-stdio-home-") as home:
            prior = os.environ.get("CODEX_HOME")
            os.environ["CODEX_HOME"] = home
            try:
                created, _ = V12Store(Path(self.root.name)).create_task(
                    objective="Stdio admission.", user_request_original="Stdio admission.", user_language="en",
                    requirements=["One binding."], constraints=["No duplicate."],
                    acceptance_criteria=["Both processes succeed."], verification_plan=["Both processes succeed."],
                )
            finally:
                if prior is None: os.environ.pop("CODEX_HOME", None)
                else: os.environ["CODEX_HOME"] = prior
            task = created["task"]
            context = get_context("fork")
            ready, results, start = context.Queue(), context.Queue(), context.Event()
            prompt = "Confirm shared admission."
            open_arguments = {"task_ref": task["task_ref"], "prompt": prompt, "prompt_language": "en", "purpose": "clarification"}
            workers = [context.Process(target=_stdio_tool_call, args=(home, "open_clarification", open_arguments, ready, start, results)) for _ in range(2)]
            for worker in workers: worker.start()
            self.assertEqual([ready.get(timeout=10) for _ in workers], [True, True])
            start.set()
            observed = [results.get(timeout=15) for _ in workers]
            for worker in workers:
                worker.join(timeout=10); self.assertEqual(worker.exitcode, 0)
            self.assertTrue(all("result" in item for item in observed), observed)
            self.assertTrue(all(item.get("_test_stdio", {}).get("exit_code") == 0 for item in observed), observed)
            self.assertTrue(all(not item.get("_test_stdio", {}).get("forced_termination") for item in observed), observed)
            values = [item["result"]["structuredContent"] for item in observed]
            self.assertTrue(all(not item["result"].get("isError") for item in observed), observed)
            prior = os.environ.get("CODEX_HOME"); os.environ["CODEX_HOME"] = home
            try:
                store, canonical = V12Store.for_task_ref(task["task_ref"])
                binding_count = store._read(lambda connection: connection.execute(
                    "SELECT COUNT(*) FROM clarification_bindings WHERE task_id=?", (canonical,),
                ).fetchone()[0])
                receipt_count = store._read(lambda connection: connection.execute(
                    "SELECT COUNT(*) FROM command_receipts WHERE project_hash=? AND aggregate_id=? AND command_name='open_clarification'",
                    (store.project_hash, canonical),
                ).fetchone()[0])
            finally:
                if prior is None: os.environ.pop("CODEX_HOME", None)
                else: os.environ["CODEX_HOME"] = prior
            self.assertEqual(binding_count, 1)
            self.assertEqual(receipt_count, 1)
            self.assertEqual({item["task_ref"] for item in values}, {task["task_ref"]}, observed)
            self.assertEqual({item["state"] for item in values}, {"pending_clarification"}, observed)
            recorded = _source_stdio_tool_call(home, "record_clarification", {
                "task_ref": task["task_ref"], "response_original": "Continue.", "user_language": "en",
            })
            self.assertFalse(recorded["result"].get("isError"))
            changed = _source_stdio_tool_call(home, "record_clarification", {
                "task_ref": task["task_ref"], "response_original": "Change the answer.", "user_language": "en",
            })
            self.assertTrue(changed["result"].get("isError"))
            self.assertEqual(changed["result"]["structuredContent"]["error"]["code"], "clarification_binding_stale")
            prior = os.environ.get("CODEX_HOME"); os.environ["CODEX_HOME"] = home
            try:
                store, canonical = V12Store.for_task_ref(task["task_ref"])
                self.assertEqual(store._read(lambda connection: connection.execute("SELECT COUNT(*) FROM clarification_bindings WHERE task_id=?", (canonical,)).fetchone()[0]), 1)
                self.assertEqual(store._read(lambda connection: connection.execute(
                    "SELECT COUNT(*) FROM command_receipts WHERE project_hash=? AND aggregate_id=? AND command_name='open_clarification'",
                    (store.project_hash, canonical),
                ).fetchone()[0]), 1)
                self.assertEqual(store._read(lambda connection: connection.execute(
                    "SELECT COUNT(*) FROM user_decisions WHERE task_id=?", (canonical,),
                ).fetchone()[0]), 1)
                self.assertEqual(store._read(lambda connection: connection.execute(
                    "SELECT COUNT(*) FROM command_receipts WHERE project_hash=? AND aggregate_id=? AND command_name='record_clarification'",
                    (store.project_hash, canonical),
                ).fetchone()[0]), 1)
            finally:
                if prior is None: os.environ.pop("CODEX_HOME", None)
                else: os.environ["CODEX_HOME"] = prior

    def test_source_stdio_copied_locator_cannot_rebind_consumed_worker(self) -> None:
        """Process B fails closed while the original process A publishes once."""
        outcome = {
            "outcome": "Publish after a source stdio reconnect.",
            "acceptance": ["Exactly one terminal report is durable."],
            "constraints": ["Do not expose private capability identity."],
            "verification": ["Inspect the coordinator evidence view."],
        }
        with tempfile.TemporaryDirectory(prefix="cortex-reconnect-stdio-home-") as home, tempfile.TemporaryDirectory(
            prefix="cortex-reconnect-stdio-project-",
        ) as project, tempfile.TemporaryDirectory(
            prefix="cortex-reconnect-plugin-data-",
        ) as plugin_data:
            opened = _source_stdio_tool_call(home, "open_task", {
                "project_root": project,
                "request_original": "Prove source stdio reconnect publication.",
                "user_language": "en",
                "outcomes": [outcome],
                "constraints": ["Use one exact assignment."],
            })
            self.assertFalse(opened["result"].get("isError"), opened)
            task_ref = opened["result"]["structuredContent"]["task_ref"]

            assessed = _source_stdio_tool_call(home, "assess_governance", {
                "task_ref": task_ref,
                "mode": "minimal",
                "rationale": "Bounded source regression.",
                "risk_factors": [],
            })
            self.assertFalse(assessed["result"].get("isError"), assessed)
            assigned = _source_stdio_tool_call(home, "open_assignment", {
                "task_ref": task_ref,
                "role": "Reconnect source worker",
                "profile_name": "backend_dev",
                "model": "gpt-5.6-luna",
                "reasoning_effort": "high",
                "responsibility": "delivery",
                "goal": "Publish the exact assigned result after reconnect.",
                "scope": "Only the reconnect source regression outcome.",
                "instructions": "Consume and publish on one exact source connection.",
                "outcomes": [outcome["outcome"]],
                "report_policy": "none",
            })
            self.assertFalse(assigned["result"].get("isError"), assigned)
            message = assigned["result"]["structuredContent"]["native_dispatch"]["message"]
            worker_ref = re.search(
                r'"task_ref":"(t_[0-9a-f]{12}_[0-9a-f]{32})"', message,
            ).group(1)
            with patch.dict(os.environ, {"PLUGIN_DATA": plugin_data}):
                direct = _source_stdio_tool_call(home, "read_task", {
                    "task_ref": worker_ref,
                                    })
            self.assertTrue(direct["result"].get("isError"), direct)
            self.assertEqual(
                direct["result"]["structuredContent"]["error"]["code"],
                "wrong_connection",
            )
            publication_arguments = {
                "task_ref": worker_ref,
                "summary": "Source same-connection publication completed.",
                "outcome": "The original stdio process retained exact consumed authority.",
                "changes": [],
                "verification_facts": [{
                    "state": "executed",
                    "summary": "The copied-locator process was rejected and process A published once.",
                }],
                "outcome_coverage": [{
                    "outcome": outcome["outcome"],
                    "status": "complete",
                    "verification": ["Coordinator evidence contains one report."],
                }],
                "documentation_impact": "The fail-closed connection contract is documented.",
                "risks": [],
                "unresolved": [],
                "status": "completed",
            }
            with patch.dict(os.environ, {"PLUGIN_DATA": plugin_data}), _source_stdio_session(
                home, host_identity=("source-worker-a", "source-worker-turn", "source-session"),
            ) as worker_a:
                # Codex Desktop starts the child's MCP stdio process before
                # SubagentStart issues its signed candidate.  The initial
                # catalogue is therefore a neutral superset: absence of a
                # candidate at initialize must not commit either role, and a
                # Desktop client that ignores list_changed must still know the
                # later worker publication operation.
                before_attestation = worker_a.rpc("tools/list", {})  # type: ignore[attr-defined]
                self.assertIn(
                    "open_assignment",
                    {item["name"] for item in before_attestation["result"]["tools"]},
                )
                self.assertIn(
                    "publish_result",
                    {item["name"] for item in before_attestation["result"]["tools"]},
                )
                _write_host_worker_receipt(plugin_data, worker_ref)
                consumed = worker_a("read_task", {
                    "task_ref": worker_ref,
                })
                self.assertFalse(consumed["result"].get("isError"), consumed)
                self.assertIn(
                    "effective_contract",
                    consumed["result"]["structuredContent"]["data"],
                )
                # Supported hosts may expose only TextContent to the worker
                # model while retaining structuredContent in lifecycle events.
                # The first successful one-shot bootstrap must therefore carry
                # the complete same canonical result in both channels.
                self.assertEqual(
                    json.loads(consumed["result"]["content"][-1]["text"]),
                    consumed["result"]["structuredContent"],
                )
                self.assertEqual(
                    consumed["result"]["structuredContent"]["data"]
                    ["effective_contract"]["revision"],
                    1,
                )

                with _source_stdio_session(home) as worker_b:
                    copied_read = worker_b("read_task", {
                        "task_ref": worker_ref,
                    })
                    self.assertTrue(copied_read["result"].get("isError"), copied_read)
                    self.assertEqual(
                        copied_read["result"]["structuredContent"]["error"]["code"],
                        "connection_lost",
                    )
                    copied_publication = worker_b(
                        "publish_result", publication_arguments,
                    )
                    self.assertTrue(
                        copied_publication["result"].get("isError"),
                        copied_publication,
                    )
                    self.assertEqual(
                        copied_publication["result"]["structuredContent"]
                        ["error"]["code"],
                        "connection_lost",
                    )

                prior = os.environ.get("CODEX_HOME")
                os.environ["CODEX_HOME"] = home
                try:
                    store, task_id = V12Store.for_task_ref(task_ref)
                    with store._connection() as connection:
                        self.assertEqual(
                            connection.execute(
                                "SELECT COUNT(*) FROM report_operations WHERE task_id=?",
                                (task_id,),
                            ).fetchone()[0],
                            0,
                        )
                finally:
                    if prior is None:
                        os.environ.pop("CODEX_HOME", None)
                    else:
                        os.environ["CODEX_HOME"] = prior

                published = worker_a("publish_result", publication_arguments)
                self.assertFalse(published["result"].get("isError"), published)
                publication = published["result"]["structuredContent"]
                self.assertEqual(publication["state"], "published")
                self.assertFalse(publication["replayed"])

            evidence = _source_stdio_tool_call(home, "read_evidence", {
                "task_ref": task_ref,
                "report_policy": "all_finalized",
            })
            self.assertFalse(evidence["result"].get("isError"), evidence)
            reports = evidence["result"]["structuredContent"]["data"]["reports"]
            self.assertEqual(len(reports), 1)
            self.assertIn("Source same-connection publication completed.", repr(reports[0]))

            prior = os.environ.get("CODEX_HOME")
            os.environ["CODEX_HOME"] = home
            try:
                store, task_id = V12Store.for_task_ref(task_ref)
                with store._connection() as connection:
                    capability_rows = connection.execute(
                        "SELECT state,continuation_ref FROM worker_capabilities WHERE task_id=?",
                        (task_id,),
                    ).fetchall()
                    operation_count = connection.execute(
                        "SELECT COUNT(*) FROM report_operations WHERE task_id=?",
                        (task_id,),
                    ).fetchone()[0]
            finally:
                if prior is None:
                    os.environ.pop("CODEX_HOME", None)
                else:
                    os.environ["CODEX_HOME"] = prior
            self.assertEqual(len(capability_rows), 1)
            self.assertEqual(capability_rows[0]["state"], "consumed")
            self.assertTrue(capability_rows[0]["continuation_ref"])
            self.assertEqual(operation_count, 1)

    def test_source_stdio_coordinator_role_cannot_switch_to_worker_audience(self) -> None:
        outcome = {
            "outcome": "Keep coordinator and worker audiences disjoint.",
            "acceptance": ["The coordinator connection cannot consume worker authority."],
            "constraints": ["Use the exact host-bound worker receipt."],
            "verification": ["A distinct worker connection consumes the assignment."],
        }
        with tempfile.TemporaryDirectory(prefix="cortex-role-home-") as home, tempfile.TemporaryDirectory(
            prefix="cortex-role-project-",
        ) as project:
            # Real plugin MCP processes receive CODEX_HOME, while hook
            # processes receive PLUGIN_DATA. Exercise the installed package
            # data fallback rather than a test-only shared environment value.
            plugin_data = str(Path(home) / "plugins" / "data" / "cortex-cortex")
            with _source_stdio_session(home) as coordinator:
                opened = coordinator("open_task", {
                    "project_root": project,
                    "request_original": "Prove monotonic MCP audiences.",
                    "user_language": "en",
                    "outcomes": [outcome],
                    "constraints": ["Keep roles disjoint."],
                })
                self.assertFalse(opened["result"].get("isError"), opened)
                task_ref = opened["result"]["structuredContent"]["task_ref"]
                assessed = coordinator("assess_governance", {
                    "task_ref": task_ref, "mode": "minimal",
                    "rationale": "Focused role boundary.", "risk_factors": [],
                })
                self.assertFalse(assessed["result"].get("isError"), assessed)
                assigned = coordinator("open_assignment", {
                    "task_ref": task_ref, "role": "Bound worker",
                    "profile_name": "backend_dev", "model": "gpt-5.6-luna",
                    "reasoning_effort": "high", "responsibility": "delivery",
                    "goal": "Consume only on the worker connection.",
                    "scope": "The exact role-bound outcome.",
                    "instructions": "Consume the complete assignment and publish only from the worker.",
                    "outcomes": [outcome["outcome"]], "report_policy": "none",
                })
                self.assertFalse(assigned["result"].get("isError"), assigned)
                worker_ref = re.search(
                    r'"task_ref":"(t_[0-9a-f]{12}_[0-9a-f]{32})"',
                    assigned["result"]["structuredContent"]["native_dispatch"]["message"],
                ).group(1)
                _write_host_worker_receipt(plugin_data, worker_ref)
                rejected = coordinator("read_task", {
                    "task_ref": worker_ref,
                })
                self.assertTrue(rejected["result"].get("isError"), rejected)
                self.assertEqual(
                    rejected["result"]["structuredContent"]["error"]["code"],
                    "wrong_connection",
                )

            with _source_stdio_session(
                home, host_identity=("source-worker-a", "source-worker-turn", "source-session"),
            ) as worker:
                consumed = worker("read_task", {
                    "task_ref": worker_ref,
                })
                self.assertFalse(consumed["result"].get("isError"), consumed)
                self.assertFalse(consumed["result"]["structuredContent"]["has_more"])

    def test_repeated_two_process_stdio_admission_converges_without_duplicate_open_mutation(self) -> None:
        """Bounded stress regression for SQLite WAL/SHM cleanup races."""
        with tempfile.TemporaryDirectory(prefix="cortex-d6-sidecar-guard-") as guard_directory:
            guard = Path(guard_directory)
            guard_log = guard / "mutations.jsonl"
            (guard / "sitecustomize.py").write_text(
                """# Process-local Python filesystem observer for the source MCP stress.
import builtins
import os

_log = os.environ.get('CORTEX_TEST_SIDECAR_MUTATION_GUARD')
_fds = {}
def _path(value):
    try:
        return os.path.realpath(os.fsdecode(os.fspath(value)))
    except TypeError:
        return None
def _record(operation, *values):
    for value in values:
        value = _path(value)
        if value and value.endswith(('-wal', '-shm')):
            with builtins.open(_log, 'a', encoding='utf-8') as stream:
                stream.write(operation + ':' + value + '\\n')
            return
def _wrap_path(name):
    original = getattr(os, name)
    def observed(path, *args, **kwargs):
        _record(name, path, *(args[:1] if name in {'replace', 'rename'} else ()))
        return original(path, *args, **kwargs)
    setattr(os, name, observed)
for _name in ('chmod', 'replace', 'unlink', 'remove', 'rename', 'truncate', 'mkdir', 'makedirs', 'rmdir', 'utime'):
    _wrap_path(_name)
_os_open = os.open
def _observed_open(path, *args, **kwargs):
    _record('open', path)
    fd = _os_open(path, *args, **kwargs)
    _fds[fd] = _path(path)
    return fd
os.open = _observed_open
_os_fchmod = os.fchmod
def _observed_fchmod(fd, *args, **kwargs):
    _record('fchmod', _fds.get(fd))
    return _os_fchmod(fd, *args, **kwargs)
os.fchmod = _observed_fchmod
def _wrap_fd(name):
    original = getattr(os, name)
    def observed(fd, *args, **kwargs):
        _record(name, _fds.get(fd))
        return original(fd, *args, **kwargs)
    setattr(os, name, observed)
for _name in ('write', 'pwrite', 'writev', 'pwritev', 'ftruncate'):
    if hasattr(os, _name):
        _wrap_fd(_name)
_os_dup = os.dup
def _observed_dup(fd, *args, **kwargs):
    duplicate = _os_dup(fd, *args, **kwargs)
    _fds[duplicate] = _fds.get(fd)
    return duplicate
os.dup = _observed_dup
_os_dup2 = os.dup2
def _observed_dup2(fd, target, *args, **kwargs):
    duplicate = _os_dup2(fd, target, *args, **kwargs)
    _fds[duplicate] = _fds.get(fd)
    return duplicate
os.dup2 = _observed_dup2
_os_close = os.close
def _observed_close(fd, *args, **kwargs):
    try:
        return _os_close(fd, *args, **kwargs)
    finally:
        _fds.pop(fd, None)
os.close = _observed_close
""",
                encoding="utf-8",
            )
            previous_guard = os.environ.get("CORTEX_TEST_SIDECAR_MUTATION_GUARD")
            os.environ["CORTEX_TEST_SIDECAR_MUTATION_GUARD"] = str(guard_log)
            try:
                for attempt in range(80):
                    with self.subTest(attempt=attempt), tempfile.TemporaryDirectory(prefix="cortex-d6-stdio-stress-home-") as home:
                        prior = os.environ.get("CODEX_HOME")
                        os.environ["CODEX_HOME"] = home
                        try:
                            created, _ = V12Store(Path(self.root.name)).create_task(
                                objective="Stdio sidecar stress.", user_request_original="Stdio sidecar stress.", user_language="en",
                                requirements=["One binding."], constraints=["No duplicate."],
                                acceptance_criteria=["Both processes succeed."], verification_plan=["Both processes succeed."],
                            )
                        finally:
                            if prior is None: os.environ.pop("CODEX_HOME", None)
                            else: os.environ["CODEX_HOME"] = prior
                        task = created["task"]
                        context = get_context("fork")
                        ready, results, start = context.Queue(), context.Queue(), context.Event()
                        arguments = {
                            "task_ref": task["task_ref"], "prompt": "Confirm shared sidecar admission.",
                            "prompt_language": "en", "purpose": "clarification",
                        }
                        workers = [context.Process(
                            target=_stdio_tool_call,
                            args=(home, "open_clarification", arguments, ready, start, results),
                        ) for _ in range(2)]
                        for worker in workers:
                            worker.start()
                        self.assertEqual([ready.get(timeout=10) for _ in workers], [True, True])
                        start.set()
                        observed = [results.get(timeout=15) for _ in workers]
                        for worker in workers:
                            worker.join(timeout=10)
                            self.assertEqual(worker.exitcode, 0)
                        self.assertTrue(all("result" in item for item in observed), observed)
                        self.assertTrue(all(item.get("_test_stdio", {}).get("exit_code") == 0 for item in observed), observed)
                        self.assertTrue(all(not item.get("_test_stdio", {}).get("forced_termination") for item in observed), observed)
                        self.assertTrue(all(not item["result"].get("isError") for item in observed), observed)
                        receipts = [item["result"]["structuredContent"] for item in observed]
                        prior = os.environ.get("CODEX_HOME")
                        os.environ["CODEX_HOME"] = home
                        try:
                            store, canonical = V12Store.for_task_ref(task["task_ref"])
                            self.assertEqual(store._read(lambda connection: connection.execute(
                                "SELECT COUNT(*) FROM clarification_bindings WHERE task_id=?", (canonical,),
                            ).fetchone()[0]), 1)
                            self.assertEqual(store._read(lambda connection: connection.execute(
                                "SELECT COUNT(*) FROM command_receipts WHERE project_hash=? AND aggregate_id=? AND command_name='open_clarification'",
                                (store.project_hash, canonical),
                            ).fetchone()[0]), 1)
                        finally:
                            if prior is None: os.environ.pop("CODEX_HOME", None)
                            else: os.environ["CODEX_HOME"] = prior
                        self.assertEqual({item["task_ref"] for item in receipts}, {task["task_ref"]}, observed)
                        self.assertEqual({item["state"] for item in receipts}, {"pending_clarification"}, observed)
                        self.assertEqual({item["replayed"] for item in receipts}, {False, True}, observed)
            finally:
                if previous_guard is None:
                    os.environ.pop("CORTEX_TEST_SIDECAR_MUTATION_GUARD", None)
                else:
                    os.environ["CORTEX_TEST_SIDECAR_MUTATION_GUARD"] = previous_guard
            self.assertFalse(guard_log.exists(), guard_log.read_text(encoding="utf-8") if guard_log.exists() else "")
            # Prove that this is an observer in the exec boundary rather than
            # a vacuous environment setting.  This probe is outside Cortex
            # and runs only after the stress assertion above has established
            # that no Cortex-originated sidecar mutation was observed.
            probe_path = guard / "observer-probe-wal"
            probe_path.write_text("probe", encoding="utf-8")
            probe_env = dict(os.environ) | {
                "PYTHONPATH": str(guard),
                "CORTEX_TEST_SIDECAR_MUTATION_GUARD": str(guard_log),
            }
            probe = subprocess.run(
                [
                    sys.executable, "-c",
                    "import os, sys; p=sys.argv[1]; os.chmod(p, 0o600); fd=os.open(p, os.O_WRONLY); "
                    "os.write(fd, b'x'); os.pwrite(fd, b'y', 0) if hasattr(os, 'pwrite') else None; "
                    "os.ftruncate(fd, 1); os.close(fd); os.truncate(p, 1)",
                    str(probe_path),
                ],
                capture_output=True, text=True, env=probe_env, timeout=10,
            )
            self.assertEqual(probe.returncode, 0, probe.stderr)
            observed_mutators = guard_log.read_text(encoding="utf-8")
            for mutation in ("chmod:", "write:", "ftruncate:", "truncate:"):
                self.assertIn(mutation, observed_mutators)
            if hasattr(os, "pwrite"):
                self.assertIn("pwrite:", observed_mutators)


if __name__ == "__main__":
    unittest.main()
