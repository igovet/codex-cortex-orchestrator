"""Black-box store guarantees for the domain command receipt boundary."""
from __future__ import annotations

import tempfile
import threading
import unittest
import ast
import json
import os
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
    assert ready.get(timeout=10) is True
    start.set()
    reply = results.get(timeout=15)
    worker.join(timeout=10)
    assert worker.exitcode == 0
    assert reply.get("_test_stdio", {}).get("exit_code") == 0, reply
    assert not reply.get("_test_stdio", {}).get("forced_termination"), reply
    return reply


class PublicPublicationFirstCallTests(unittest.TestCase):
    def test_publish_plan_first_stdio_call_accepts_explicit_empty_unresolved(self) -> None:
        """The advertised complete shape must cross real MCP validation on its first call."""
        with tempfile.TemporaryDirectory(prefix="cortex-plan-first-call-") as home:
            arguments = {
                "task_ref": "t_0123456789ab_" + "a" * 32,
                "summary": "Plan.",
                "scope": "Bounded scope.",
                "review_policy": "required",
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
                "task_not_found",
                accepted,
            )

            rejected = _source_stdio_tool_call(
                home, "publish_plan", {key: value for key, value in arguments.items() if key != "unresolved"},
            )
            self.assertTrue(rejected["result"].get("isError"), rejected)
            error = rejected["result"]["structuredContent"]["error"]
            self.assertEqual(error["code"], "validation_error", rejected)
            self.assertEqual(error["details"]["path"], "$.unresolved", rejected)


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
            open_arguments = {"task_ref": task["task_ref"], "prompt": prompt, "prompt_language": "en"}
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
                            "prompt_language": "en",
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
