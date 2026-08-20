"""Deterministic fake-host coverage for the Stage 00 host-contract probe."""
from __future__ import annotations

import importlib.util
import json
import multiprocessing
import os
from decimal import Decimal
from pathlib import Path
import subprocess
import tempfile
import time
from unittest import mock
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "cortex-hierarchy-host-spike.py"
SPEC = importlib.util.spec_from_file_location("cortex_hierarchy_host_spike", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
PROBE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PROBE)


FORBIDDEN = {
    "prompt",
    "message",
    "token",
    "tokens",
    "report",
    "reports",
    "stderr",
    "stdout",
    "raw_stderr",
    "private",
    "private_metadata",
    "metadata",
}


def assert_safe_machine_fields(test: unittest.TestCase, value: object) -> None:
    """Ensure tests only consume safe contract fields, never host diagnostics."""
    if isinstance(value, dict):
        for key, item in value.items():
            test.assertNotIn(str(key).lower(), FORBIDDEN)
            assert_safe_machine_fields(test, item)
    elif isinstance(value, list):
        for item in value:
            assert_safe_machine_fields(test, item)
    elif isinstance(value, str):
        test.assertLessEqual(len(value), 512)
        test.assertNotIn("host-private-diagnostic", value)


class _InjectedBaseException(BaseException):
    """Deliberately non-Exception fault used to prove containment."""


class _FaultConnection:
    def __init__(
        self,
        *,
        poll_result: bool = False,
        poll_error: BaseException | None = None,
        recv_error: BaseException | None = None,
        close_error: BaseException | None = None,
    ) -> None:
        self.poll_result = poll_result
        self.poll_error = poll_error
        self.recv_error = recv_error
        self.close_error = close_error
        self.close_calls = 0
        self.poll_calls = 0
        self.poll_timeouts: list[float] = []
        self.recv_calls = 0

    def poll(self, timeout: float) -> bool:
        self.poll_calls += 1
        self.poll_timeouts.append(timeout)
        if self.poll_error is not None:
            raise self.poll_error
        return self.poll_result

    def recv(self) -> object:
        self.recv_calls += 1
        if self.recv_error is not None:
            raise self.recv_error
        return {"result": None}

    def close(self) -> None:
        self.close_calls += 1
        if self.close_error is not None:
            raise self.close_error


class _FaultProcess:
    """Small non-launching process seam that records every reaper operation."""

    def __init__(
        self,
        *,
        terminate_error: BaseException | None = None,
        kill_error: BaseException | None = None,
        join_error: BaseException | None = None,
        liveness_error: BaseException | None = None,
    ) -> None:
        self.terminate_error = terminate_error
        self.kill_error = kill_error
        self.join_error = join_error
        self.liveness_error = liveness_error
        self.daemon = False
        self.alive = True
        self.start_calls = 0
        self.terminate_calls = 0
        self.kill_calls = 0
        self.join_calls = 0
        self.liveness_calls = 0

    def start(self) -> None:
        self.start_calls += 1

    def join(self, timeout: float) -> None:
        del timeout
        self.join_calls += 1
        if self.join_error is not None:
            raise self.join_error

    def is_alive(self) -> bool:
        self.liveness_calls += 1
        if self.liveness_error is not None:
            raise self.liveness_error
        return self.alive

    def terminate(self) -> None:
        self.terminate_calls += 1
        if self.terminate_error is not None:
            raise self.terminate_error

    def kill(self) -> None:
        self.kill_calls += 1
        if self.kill_error is not None:
            raise self.kill_error
        self.alive = False


class _FaultContext:
    def __init__(self, parent: _FaultConnection, child: _FaultConnection, process: _FaultProcess) -> None:
        self.parent = parent
        self.child = child
        self.process = process
        self.pipe_calls = 0
        self.process_calls = 0

    def Pipe(self, *, duplex: bool) -> tuple[_FaultConnection, _FaultConnection]:
        self.pipe_calls += 1
        if duplex:
            raise AssertionError("the probe must request a one-way Pipe")
        return self.parent, self.child

    def Process(self, **kwargs: object) -> _FaultProcess:
        self.process_calls += 1
        self.process_kwargs = kwargs
        return self.process


class HierarchyHostContractTests(unittest.TestCase):
    def run_host(self, **kwargs):
        efforts = kwargs.pop("efforts", PROBE.DEFAULT_SIMULATION_EFFORTS)
        timeout_seconds = kwargs.pop("timeout_seconds", 10.0)
        return PROBE.run_host_contract(
            PROBE.FakeHost(**kwargs), efforts=efforts, timeout_seconds=timeout_seconds
        )

    def run_post_start_fault(
        self,
        *,
        parent: _FaultConnection | None = None,
        child: _FaultConnection | None = None,
        process: _FaultProcess | None = None,
    ) -> tuple[dict[str, object], _FaultConnection, _FaultConnection, _FaultProcess]:
        parent = parent or _FaultConnection(poll_error=RuntimeError("poll failure"))
        child = child or _FaultConnection()
        process = process or _FaultProcess()
        context = _FaultContext(parent, child, process)
        with mock.patch.object(PROBE, "_fork_context", return_value=context):
            result = PROBE.run_host_contract(PROBE.FakeHost(), timeout_seconds=0.5)
        return result, parent, child, process

    def test_static_inventory_is_deterministic_and_fails_closed(self):
        first = PROBE.static_inventory(ROOT)
        second = PROBE.static_inventory(ROOT)
        self.assertEqual(first, second)
        self.assertEqual(first["status"], "FAIL")
        self.assertEqual(first["decision"], "NO-GO")
        self.assertFalse(first["support_evidence"])
        self.assertEqual(first["request"]["model"], "gpt-5.6-terra")
        self.assertIsNone(first["observed"]["effective_model"])
        self.assertIsNone(first["observed"]["effective_reasoning_effort"])
        self.assertEqual(first["source_inventory"]["native_create_thread_models"], ["gpt-5.6-luna"])
        self.assertEqual(
            {name: item["status"] for name, item in first["capabilities"].items()},
            {
                "model": "UNPROVEN",
                "effort": "UNPROVEN",
                "identity": "UNPROVEN",
                "lifecycle": "UNPROVEN",
                "worktree": "UNPROVEN",
                "recovery": "UNPROVEN",
                "child_worker": "UNPROVEN",
            },
        )
        self.assert_safe(first)

    def test_static_inventory_fixtures_distinguish_literal_unavailable_and_dynamic(self):
        literal_source = '''
SUPPORTED_MODELS = ("gpt-5.6-terra",)
SUPPORTED_EFFORT_SEQUENCE = ("low", "medium")
def _v3_host_capabilities():
    return {"create_thread_models": ("gpt-5.6-terra",)}
def _v3_native_arguments():
    return {"prompt": "ignored", "title": "ignored", "target": "local"}
'''
        dynamic_source = '''
SUPPORTED_MODELS = get_models()
SUPPORTED_EFFORT_SEQUENCE = get_efforts()
def _v3_host_capabilities():
    models = get_models()
    return {"create_thread_models": models}
def _v3_native_arguments():
    args = get_args()
    return args
'''
        with tempfile.TemporaryDirectory(prefix="stage00-fixture-") as temp_dir:
            root = Path(temp_dir)
            source_path = root / "plugins" / "cortex" / "scripts" / "cortex.py"
            profiles_path = root / "plugins" / "cortex" / "profiles.json"
            source_path.parent.mkdir(parents=True)
            profiles_path.parent.mkdir(parents=True, exist_ok=True)
            source_path.write_text(literal_source, encoding="utf-8")
            profiles_path.write_text('{"model_routing": {"configured_default_model": "gpt-5.6-luna"}}', encoding="utf-8")
            literal = PROBE.inspect_source_contract(root)
            self.assertEqual(literal["policy_models_status"], "literal")
            self.assertEqual(literal["native_create_thread_models_status"], "literal")
            self.assertEqual(literal["native_create_thread_arguments_status"], "literal")
            self.assertEqual(literal["configured_default_model_status"], "literal")

            source_path.write_text(dynamic_source, encoding="utf-8")
            profiles_path.write_text('{"model_routing": {"configured_default_model": null}}', encoding="utf-8")
            dynamic = PROBE.inspect_source_contract(root)
            self.assertEqual(dynamic["policy_models_status"], "dynamic")
            self.assertEqual(dynamic["policy_efforts_status"], "dynamic")
            self.assertEqual(dynamic["native_create_thread_models_status"], "dynamic")
            self.assertEqual(dynamic["native_create_thread_arguments_status"], "dynamic")
            self.assert_safe(dynamic)
            self.assertNotIn("get_models", json.dumps(dynamic, sort_keys=True))

            source_path.unlink()
            unavailable = PROBE.inspect_source_contract(root)
            self.assertEqual(unavailable["inspection"], "unavailable")
            self.assertEqual(unavailable["policy_models_status"], "unavailable")
            self.assertEqual(unavailable["native_create_thread_arguments_status"], "unavailable")

    def test_static_inventory_conditional_paths_are_complete_and_fail_closed(self):
        conditional_source = '''
SUPPORTED_MODELS = ("gpt-5.6-terra",)
if feature_enabled:
    SUPPORTED_EFFORT_SEQUENCE = ("low",)
else:
    SUPPORTED_EFFORT_SEQUENCE = ("high",)
def _v3_host_capabilities():
    if feature_enabled:
        return {"create_thread_models": ("gpt-5.6-terra",)}
    if legacy_route:
        return {"create_thread_models": ("gpt-5.6-luna",)}
    return {"spawn_agent_models": ("gpt-5.6-luna",)}
def _v3_native_arguments():
    if first_route:
        return {"prompt": "private-prompt", "title": "ignored", "target": "local", "model": "gpt-5.6-terra"}
    if second_route:
        return {"prompt": "private-prompt", "title": "ignored", "target": "local", "reasoning_effort": "high"}
    arguments = get_args()
    return arguments
'''
        with tempfile.TemporaryDirectory(prefix="stage00-conditional-") as temp_dir:
            root = Path(temp_dir)
            source_path = root / "plugins" / "cortex" / "scripts" / "cortex.py"
            profiles_path = root / "plugins" / "cortex" / "profiles.json"
            source_path.parent.mkdir(parents=True)
            profiles_path.parent.mkdir(parents=True, exist_ok=True)
            source_path.write_text(conditional_source, encoding="utf-8")
            profiles_path.write_text('{"model_routing": {"configured_default_model": "gpt-5.6-luna"}}', encoding="utf-8")

            source = PROBE.inspect_source_contract(root)
            self.assertEqual(source["policy_efforts_status"], "conditional")
            self.assertEqual(source["native_create_thread_models_status"], "unavailable")
            self.assertEqual(source["native_create_thread_models"], ["gpt-5.6-luna", "gpt-5.6-terra"])
            self.assertEqual(
                source["native_create_thread_models_candidates"],
                [
                    {"status": "literal", "keys": ["gpt-5.6-luna"]},
                    {"status": "literal", "keys": ["gpt-5.6-terra"]},
                    {"status": "unavailable", "keys": []},
                ],
            )
            self.assertEqual(source["native_create_thread_arguments_status"], "dynamic")
            self.assertEqual(
                source["native_create_thread_arguments_candidates"],
                [
                    {"status": "dynamic", "keys": []},
                    {"status": "literal", "keys": ["model", "prompt", "target", "title"]},
                    {"status": "literal", "keys": ["prompt", "reasoning_effort", "target", "title"]},
                ],
            )
            self.assertFalse(source["native_create_thread_supports_model"])
            self.assertFalse(source["native_create_thread_supports_reasoning_effort"])
            static = PROBE.static_inventory(root)
            self.assertEqual(static["status"], "FAIL")
            self.assertEqual(static["decision"], "NO-GO")
            self.assertFalse(static["support_evidence"])
            encoded = json.dumps(static, sort_keys=True)
            self.assertNotIn("private-prompt", encoded)
            self.assertNotIn("get_args", encoded)
            self.assert_safe(static)

    def test_positive_fake_host_requires_four_distinct_threads_and_full_lifecycle(self):
        result = self.run_host()
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["decision"], "GO")
        self.assertTrue(result["support_evidence"])
        self.assertEqual(result["observed"]["thread_count"], 4)
        self.assertEqual(result["observed"]["distinct_thread_ids"], 4)
        self.assertEqual(result["observed"]["effective_model"], "gpt-5.6-terra")
        self.assertEqual(result["observed"]["effective_reasoning_efforts"], list(PROBE.DEFAULT_SIMULATION_EFFORTS))
        self.assertEqual({item["thread_id"] for item in result["threads"]}, {f"fake-thread-{i}" for i in range(1, 5)})
        self.assertTrue(all(item["status"] == "PASS" for item in result["threads"]))
        self.assert_safe(result)

    def test_each_supported_coordinator_effort_is_requested_once(self):
        result = self.run_host()
        observed = result["observed"]["effective_reasoning_efforts"]
        self.assertEqual(observed, list(PROBE.SUPPORTED_COORDINATOR_EFFORTS))
        self.assertEqual(len(observed), len(set(observed)))

    def test_duplicate_thread_ids_fail(self):
        result = self.run_host(duplicate_ids=True)
        self.assertEqual(result["status"], "FAIL")
        self.assertTrue(any(item["reason"] == "duplicate_thread_id" for item in result["observations"]))

    def test_missing_create_response_fails(self):
        result = self.run_host(lose_create=True)
        self.assertEqual(result["status"], "FAIL")
        self.assertTrue(any(item["reason"] == "lost_create_response" for item in result["observations"]))

    def test_child_worker_spawn_gap_fails(self):
        result = self.run_host(worker_available=False)
        self.assertEqual(result["status"], "FAIL")
        self.assertTrue(any(item["reason"] == "child_worker_spawn_unavailable" for item in result["observations"]))

    def test_follow_up_gap_fails(self):
        result = self.run_host(follow_up_available=False)
        self.assertEqual(result["status"], "FAIL")
        self.assertTrue(any(item["reason"] == "follow_up_unavailable" for item in result["observations"]))

    def test_resume_gap_fails(self):
        result = self.run_host(resume_available=False)
        self.assertEqual(result["status"], "FAIL")
        self.assertTrue(any(item["reason"] == "resume_unavailable" for item in result["observations"]))

    def test_completion_gap_fails(self):
        result = self.run_host(completion_available=False)
        self.assertEqual(result["status"], "FAIL")
        self.assertTrue(any(item["reason"] == "completion_unavailable" for item in result["observations"]))

    def test_worktree_must_be_attested(self):
        # The adapter only advertises local environments; a requested
        # worktree therefore fails closed instead of being inferred.
        result = PROBE.run_host_contract(PROBE.FakeHost(environment="local"), environment="worktree")
        self.assertEqual(result["status"], "FAIL")
        self.assertTrue(any(item["reason"] == "environment_not_attested" for item in result["observations"]))

    def test_terra_is_required_and_luna_sol_or_default_substitution_fails(self):
        for model in ("gpt-5.6-luna", "gpt-5.6-sol", "configured-default", None):
            with self.subTest(model=model):
                result = self.run_host(effective_model=model)
                self.assertEqual(result["status"], "FAIL")
                self.assertTrue(any(item["reason"] == "model_substitution_or_missing" for item in result["observations"]))
                self.assertFalse(result["support_evidence"])

    def test_missing_invalid_and_unsupported_effort_fail(self):
        for effective_effort in (None, "", "unsupported", "low"):
            with self.subTest(effective_effort=effective_effort):
                # Request xhigh for every child; low is a silent downgrade.
                result = self.run_host(effective_effort=effective_effort, efforts=("xhigh",))
                self.assertEqual(result["status"], "FAIL")
                self.assertTrue(any(item["reason"] == "effort_substitution_or_missing" for item in result["observations"]))

    def test_unsupported_requested_effort_fails(self):
        result = self.run_host(efforts=("unsupported",))
        self.assertEqual(result["status"], "FAIL")
        self.assertTrue(any(item["reason"] == "invalid_or_unsupported_effort" for item in result["observations"]))

    def test_policy_only_missing_malformed_and_duplicate_efforts_are_rejected_before_adapter_use(self):
        for efforts, reason in (
            (("max",), "policy_only_effort"),
            ((), "missing_effort"),
            (("xhigh", None), "malformed_effort"),
            (("low", "low"), "duplicate_effort"),
        ):
            with self.subTest(efforts=efforts):
                host = PROBE.FakeHost()
                result = PROBE.run_host_contract(host, efforts=efforts)
                self.assertEqual(result["status"], "FAIL")
                self.assertFalse(result["support_evidence"])
                self.assertTrue(any(item["reason"] == reason for item in result["observations"]))
                self.assertEqual(host.created, 0)

    def test_incomplete_valid_effort_set_cannot_be_native_pass(self):
        result = self.run_host(efforts=("xhigh",))
        self.assertEqual(result["status"], "FAIL")
        self.assertTrue(any(item["reason"] == "complete_effort_set_required" for item in result["observations"]))
        self.assertTrue(any(item["reason"] == "four_distinct_threads_required" for item in result["observations"]))

    def test_nonfinite_and_excessive_timeouts_are_rejected_before_process_creation(self):
        invalid_timeouts = (
            True,
            "10",
            Decimal("sNaN"),
            complex(1, 1),
            float("nan"),
            float("inf"),
            float("-inf"),
            0,
            -1,
            PROBE.MAX_FAKE_HOST_TIMEOUT_SECONDS + 0.001,
        )
        for timeout_seconds in invalid_timeouts:
            with self.subTest(timeout_seconds=repr(timeout_seconds)):
                host = PROBE.FakeHost()
                parent = _FaultConnection()
                child = _FaultConnection()
                process = _FaultProcess()
                context = _FaultContext(parent, child, process)
                with mock.patch.object(PROBE, "_fork_context", return_value=context):
                    result = PROBE.run_host_contract(host, timeout_seconds=timeout_seconds)
                self.assertEqual(result["status"], "FAIL")
                self.assertEqual(result["decision"], "NO-GO")
                self.assertFalse(result["support_evidence"])
                self.assertTrue(any(item["reason"] == "invalid_timeout" for item in result["observations"]))
                self.assertEqual(context.pipe_calls, 0)
                self.assertEqual(context.process_calls, 0)
                self.assertEqual(process.start_calls, 0)
                self.assertEqual(host.created, 0)
                self.assert_safe(result)

    def test_default_timeout_creates_a_finite_monotonic_deadline(self):
        parent = _FaultConnection(poll_error=RuntimeError("poll failure"))
        result, parent, _, process = self.run_post_start_fault(parent=parent)
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(process.start_calls, 1)
        self.assertEqual(len(parent.poll_timeouts), 1)
        self.assertGreater(parent.poll_timeouts[0], 0.0)
        self.assertLessEqual(parent.poll_timeouts[0], 10.0)

    def test_blocking_fake_adapter_is_cancelled_within_deadline(self):
        if PROBE._fork_context() is None:
            self.skipTest("fork-process isolation is unavailable on this platform")
        context = multiprocessing.get_context("fork")

        class BlockingHost(PROBE.FakeHost):
            def __init__(self):
                super().__init__()
                self.cleanup_calls = context.Value("i", 0)
                self.child_pid = context.Value("i", 0)

            def create_thread(self, request):
                self.child_pid.value = os.getpid()
                time.sleep(0.75)
                return super().create_thread(request)

            def cleanup(self):
                self.cleanup_calls.value += 1
                return super().cleanup()

        host = BlockingHost()
        started = time.monotonic()
        result = PROBE.run_host_contract(host, timeout_seconds=0.05)
        elapsed = time.monotonic() - started

        self.assertLess(elapsed, 0.40)
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(result["decision"], "NO-GO")
        self.assertFalse(result["support_evidence"])
        self.assertTrue(any(item["reason"] == "bounded_timeout" for item in result["observations"]))
        self.assertTrue(any(item["reason"] == "cleanup_incomplete_after_timeout" for item in result["observations"]))
        self.assertEqual(host.cleanup_calls.value, 0)
        self.assertNotEqual(host.child_pid.value, 0)
        self.assertFalse(any(child.pid == host.child_pid.value for child in multiprocessing.active_children()))
        self.assert_safe(result)

    def test_post_start_exceptions_attempt_bounded_reaping(self):
        cases = (
            (
                "poll_exception",
                _FaultConnection(poll_error=RuntimeError("poll failure")),
                _FaultConnection(),
                _FaultProcess(),
            ),
            (
                "recv_exception",
                _FaultConnection(poll_result=True, recv_error=OSError("recv failure")),
                _FaultConnection(),
                _FaultProcess(),
            ),
            (
                "base_exception",
                _FaultConnection(poll_error=_InjectedBaseException()),
                _FaultConnection(),
                _FaultProcess(),
            ),
        )
        for name, parent, child, process in cases:
            with self.subTest(name=name):
                result, parent, child, process = self.run_post_start_fault(
                    parent=parent, child=child, process=process
                )
                self.assertEqual(result["status"], "FAIL")
                self.assertEqual(result["decision"], "NO-GO")
                self.assertFalse(result["support_evidence"])
                self.assertGreaterEqual(parent.close_calls, 1)
                self.assertGreaterEqual(child.close_calls, 1)
                self.assertGreaterEqual(process.terminate_calls, 1)
                self.assertGreaterEqual(process.kill_calls, 1)
                self.assertGreaterEqual(process.join_calls, 3)
                self.assert_safe(result)

    def test_cleanup_operation_failures_continue_remaining_attempts(self):
        cases = (
            (
                "parent_close_failure",
                _FaultConnection(poll_error=RuntimeError("poll failure"), close_error=OSError("parent close")),
                _FaultConnection(),
                _FaultProcess(),
            ),
            (
                "child_close_failure",
                _FaultConnection(poll_error=RuntimeError("poll failure")),
                _FaultConnection(close_error=OSError("child close")),
                _FaultProcess(),
            ),
            (
                "terminate_failure",
                _FaultConnection(poll_error=RuntimeError("poll failure")),
                _FaultConnection(),
                _FaultProcess(terminate_error=OSError("terminate failure")),
            ),
            (
                "kill_failure",
                _FaultConnection(poll_error=RuntimeError("poll failure")),
                _FaultConnection(),
                _FaultProcess(kill_error=OSError("kill failure")),
            ),
            (
                "join_failure",
                _FaultConnection(poll_error=RuntimeError("poll failure")),
                _FaultConnection(),
                _FaultProcess(join_error=OSError("join failure")),
            ),
            (
                "raising_liveness",
                _FaultConnection(poll_error=RuntimeError("poll failure")),
                _FaultConnection(),
                _FaultProcess(liveness_error=_InjectedBaseException()),
            ),
        )
        for name, parent, child, process in cases:
            with self.subTest(name=name):
                result, parent, child, process = self.run_post_start_fault(
                    parent=parent, child=child, process=process
                )
                self.assertEqual(result["status"], "FAIL")
                self.assertEqual(result["decision"], "NO-GO")
                self.assertFalse(result["support_evidence"])
                self.assertGreaterEqual(parent.close_calls, 1)
                self.assertGreaterEqual(child.close_calls, 1)
                self.assertGreaterEqual(process.terminate_calls, 1)
                self.assertGreaterEqual(process.kill_calls, 1)
                self.assertGreaterEqual(process.join_calls, 3)
                self.assertTrue(any(item["reason"] == "cleanup_unverified" for item in result["observations"]))
                self.assert_safe(result)

    def test_unavailable_liveness_after_start_fails_without_escaping(self):
        process = _FaultProcess()
        process.is_alive = None  # type: ignore[method-assign]
        result, parent, child, process = self.run_post_start_fault(process=process)
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(result["decision"], "NO-GO")
        self.assertFalse(result["support_evidence"])
        self.assertGreaterEqual(parent.close_calls, 1)
        self.assertGreaterEqual(child.close_calls, 1)
        self.assertGreaterEqual(process.terminate_calls, 1)
        self.assertGreaterEqual(process.kill_calls, 1)
        self.assertGreaterEqual(process.join_calls, 3)
        self.assertTrue(any(item["reason"] == "cleanup_unverified" for item in result["observations"]))
        self.assert_safe(result)

    def test_fake_adapter_isolation_unavailable_fails_closed(self):
        host = PROBE.FakeHost()
        with mock.patch.object(PROBE, "_fork_context", return_value=None):
            result = PROBE.run_host_contract(host)
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(result["decision"], "NO-GO")
        self.assertFalse(result["support_evidence"])
        self.assertEqual(host.created, 0)
        self.assertTrue(any(item["reason"] == "process_isolation_unavailable" for item in result["observations"]))
        self.assertTrue(any(item["reason"] == "cleanup_disabled" for item in result["observations"]))
        self.assert_safe(result)

    def test_malformed_child_result_transport_fails_closed(self):
        with mock.patch.object(PROBE, "_decode_child_result", return_value=None):
            result = self.run_host()
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(result["decision"], "NO-GO")
        self.assertFalse(result["support_evidence"])
        self.assertTrue(any(item["reason"] == "child_result_transport_invalid" for item in result["observations"]))
        self.assertTrue(any(item["reason"] == "cleanup_unverified" for item in result["observations"]))
        self.assert_safe(result)

    def test_cleanup_is_required(self):
        result = self.run_host(cleanup_complete=False)
        self.assertEqual(result["status"], "FAIL")
        self.assertTrue(any(item["reason"] == "cleanup_incomplete" for item in result["observations"]))

    def test_failure_question_termination_and_cross_thread_receipts_are_required(self):
        for kwargs, reason in (
            ({"failure_available": False}, "failure_unavailable"),
            ({"question_available": False}, "question_unavailable"),
            ({"termination_available": False}, "termination_unavailable"),
            ({"cross_thread_receipts": True}, "cross_thread_receipt"),
        ):
            with self.subTest(kwargs=kwargs):
                result = self.run_host(**kwargs)
                self.assertEqual(result["status"], "FAIL")
                self.assertFalse(result["support_evidence"])
                self.assertTrue(any(item["reason"] == reason for item in result["observations"]))

    def test_positive_fake_host_contains_all_correlated_lifecycle_receipts(self):
        result = self.run_host()
        names = {item["name"] for item in result["observations"] if item["status"] == "PASS"}
        self.assertTrue(
            {
                "create_thread",
                "thread_identity",
                "thread_title",
                "thread_environment",
                "child_worker_spawn",
                "follow_up",
                "resume",
                "completion",
                "failure",
                "question",
                "termination",
                "cleanup",
            }
            <= names
        )

    def test_unsanitized_adapter_output_fails_without_echoing_it(self):
        result = self.run_host(unsanitized=True)
        self.assertEqual(result["status"], "FAIL")
        encoded = json.dumps(result, sort_keys=True)
        self.assertNotIn("host-private-diagnostic", encoded)
        self.assertTrue(any(item["reason"] == "unsanitized_adapter_observation" for item in result["observations"]))
        self.assert_safe(result)

    def test_live_is_disabled_and_never_launches_for_environment_input(self):
        commands = (
            None,
            "/bin/true",
            "/bin/true; /bin/false",
            "sh -c 'echo hostile'",
            "FOO=bar /bin/true",
            "git commit",
            "/definitely/unresolved-stage00-adapter",
            "$(touch should-never-run)",
        )
        expected = PROBE._live_result(1.0)
        self.assertEqual(expected["status"], "SKIP")
        self.assertEqual(expected["decision"], "NO-GO")
        self.assertFalse(expected["support_evidence"])
        self.assertEqual(expected["observations"][0]["reason"], "live_probe_disabled")
        for command in commands:
            with self.subTest(command=command), mock.patch.object(subprocess, "Popen") as popen:
                if command is None:
                    with mock.patch.dict(os.environ, {}, clear=False):
                        os.environ.pop("CORTEX_HIERARCHY_HOST_COMMAND", None)
                        result = PROBE._live_result(1.0)
                else:
                    with mock.patch.dict(os.environ, {"CORTEX_HIERARCHY_HOST_COMMAND": command}):
                        result = PROBE._live_result(1.0)
                self.assertEqual(result, expected)
                popen.assert_not_called()
                self.assert_safe(result)

    def assert_safe(self, result):
        assert_safe_machine_fields(self, result)


if __name__ == "__main__":
    unittest.main()
