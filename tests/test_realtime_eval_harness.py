"""Deterministic acceptance tests for the source-mode realtime evaluator."""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import signal
import sqlite3
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/cortex-luna-high-eval.py"


def load_harness():
    spec = importlib.util.spec_from_file_location("cortex_luna_high_eval", SCRIPT)
    if spec is None or spec.loader is None:
        raise AssertionError(f"unable to load {SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


FAKE_CHILD = r'''
import json
from pathlib import Path
import subprocess
import sys
import time

mode, marker = sys.argv[1], Path(sys.argv[2])
if mode == "emit":
    print(json.dumps({
        "type": "item",
        "item": {
            "type": "mcp_tool_call",
            "tool": "mcp__cortex__record_report",
            "status": "completed",
            "arguments": {"prompt": "SECRET_PROMPT", "report": "SECRET_REPORT"},
            "result": {"structured_content": {"ok": True, "token": "SECRET_TOKEN"}},
        },
    }), flush=True)
    time.sleep(0.45)
    marker.write_text("child-finished", encoding="utf-8")
elif mode == "silence":
    time.sleep(1.3)
    marker.write_text("child-finished", encoding="utf-8")
elif mode == "secrets":
    print(json.dumps({
        "type": "item",
        "item": {"type": "agent_message", "text": "SECRET_PROMPT SECRET_REPORT"},
    }), flush=True)
    print(json.dumps({
        "type": "item",
        "item": {
            "type": "mcp_tool_call",
            "tool": "mcp__cortex__start_orchestration",
            "status": "completed",
            "arguments": {"prompt": "SECRET_PROMPT"},
            "result": {"structured_content": {"ok": False, "report": "SECRET_REPORT"}},
        },
    }), flush=True)
    print("SECRET_STDERR", file=sys.stderr, flush=True)
    marker.write_text("child-finished", encoding="utf-8")
elif mode == "group":
    grandchild_code = (
        "from pathlib import Path; import time; "
        f"Path({str(marker)!r}).write_text('grandchild-alive'); time.sleep(60)"
    )
    grandchild = subprocess.Popen([sys.executable, "-c", grandchild_code])
    marker.with_suffix(".pid").write_text(str(grandchild.pid), encoding="utf-8")
    time.sleep(60)
elif mode == "exited_parent":
    grandchild_code = (
        "from pathlib import Path; import time; "
        f"Path({str(marker)!r}).write_text('grandchild-alive'); time.sleep(60)"
    )
    grandchild = subprocess.Popen([sys.executable, "-c", grandchild_code])
    marker.with_suffix(".pid").write_text(str(grandchild.pid), encoding="utf-8")
    # The direct parent exits successfully while its inherited pipe holder remains.
    raise SystemExit(0)
'''


WRAPPER = r'''
import importlib.util
import json
from pathlib import Path
import sys

script, child, project, mode, marker, heartbeat, timeout = sys.argv[1:]
spec = importlib.util.spec_from_file_location("cortex_luna_high_eval_wrapper", script)
if spec is None or spec.loader is None:
    raise SystemExit("unable to load harness")
harness = importlib.util.module_from_spec(spec)
spec.loader.exec_module(harness)
harness.HEARTBEAT_SECONDS = float(heartbeat)
result = harness.run_live_command(
    [sys.executable, child, mode, marker],
    Path(project),
    "fixture",
    timeout_seconds=float(timeout),
)
print(json.dumps({"final": result}, sort_keys=True), flush=True)
'''


class RealtimeEvalHarnessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.harness = load_harness()

    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory(prefix="cortex-realtime-test-")
        self.root = Path(self.tempdir.name)
        self.project = self.root / "project"
        self.project.mkdir()
        self.child = self.root / "fake_child.py"
        self.child.write_text(FAKE_CHILD, encoding="utf-8")

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def run_wrapper(
        self,
        mode: str,
        *,
        heartbeat: float = 15,
        timeout: float = 5,
        send_sigterm: bool = False,
    ) -> tuple[subprocess.Popen[str], Path, str]:
        marker = self.root / f"{mode}.marker"
        command = [
            sys.executable,
            "-c",
            WRAPPER,
            str(SCRIPT),
            str(self.child),
            str(self.project),
            mode,
            str(marker),
            str(heartbeat),
            str(timeout),
        ]
        environment = os.environ.copy()
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        process = subprocess.Popen(
            command,
            cwd=ROOT,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        if send_sigterm:
            return process, marker, ""
        output, error = process.communicate(timeout=15)
        self.assertEqual(process.returncode, 0, error)
        return process, marker, output

    def assert_pid_stopped(self, pid_path: Path) -> None:
        self.assertTrue(pid_path.exists(), f"fake child did not record grandchild PID: {pid_path}")
        pid = int(pid_path.read_text(encoding="utf-8"))
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return
            # A killed descendant can briefly remain as a zombie while init reaps it.
            stat_path = Path("/proc") / str(pid) / "stat"
            if stat_path.exists():
                state = stat_path.read_text(encoding="utf-8", errors="replace").split()[2:3]
                if state == ["Z"]:
                    return
            time.sleep(0.05)
        self.fail(f"grandchild process {pid} survived process-group termination")

    def parse_lines(self, output: str) -> list[dict[str, object]]:
        lines = output.splitlines()
        self.assertTrue(lines)
        parsed = [json.loads(line) for line in lines]
        self.assertTrue(all(isinstance(item, dict) for item in parsed))
        return parsed

    def test_child_output_is_streamed_before_child_exit(self) -> None:
        marker = self.root / "emit.marker"
        command = [
            sys.executable,
            "-c",
            WRAPPER,
            str(SCRIPT),
            str(self.child),
            str(self.project),
            "emit",
            str(marker),
            "15",
            "5",
        ]
        environment = os.environ.copy()
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        process = subprocess.Popen(
            command,
            cwd=ROOT,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        assert process.stdout is not None
        first = json.loads(process.stdout.readline())
        second = json.loads(process.stdout.readline())
        self.assertEqual(first["event"], "parent_started")
        self.assertEqual(second["event"], "cortex_mcp_call")
        self.assertFalse(marker.exists(), "child output was delayed until after child exit")
        output, error = process.communicate(timeout=15)
        self.assertEqual(process.returncode, 0, error)
        self.assertEqual(json.loads(output.splitlines()[-1])["final"]["returncode"], 0)

    def test_heartbeat_reports_last_activity_during_child_silence(self) -> None:
        _process, _marker, output = self.run_wrapper("silence", heartbeat=0.05)
        events = self.parse_lines(output)
        heartbeats = [item for item in events if item.get("event") == "heartbeat"]
        self.assertTrue(heartbeats)
        heartbeat = heartbeats[0]
        self.assertIn("last_activity", heartbeat)
        self.assertIn("last_activity_seconds_ago", heartbeat)
        self.assertIsInstance(heartbeat["last_activity"], str)
        self.assertIsInstance(heartbeat["last_activity_seconds_ago"], int)
        self.assertTrue(heartbeat["process_running"])

    def test_stream_metadata_is_sanitized_and_bounded(self) -> None:
        _process, _marker, output = self.run_wrapper("secrets", heartbeat=0.05)
        self.assertNotIn("SECRET_PROMPT", output)
        self.assertNotIn("SECRET_REPORT", output)
        self.assertNotIn("SECRET_TOKEN", output)
        self.assertNotIn("SECRET_STDERR", output)
        events = self.parse_lines(output)
        for event in events:
            self.assertLessEqual(len(json.dumps(event, sort_keys=True)), 1000)
            self.assertNotIn("arguments", event)
            self.assertNotIn("result", event)
        calls = [item for item in events if item.get("event") == "cortex_mcp_call"]
        self.assertEqual(calls[0]["tool"], "start_orchestration")
        self.assertEqual(calls[0]["ok"], False)

    def test_native_collaboration_events_expose_only_safe_tool_status(self) -> None:
        line = json.dumps({
            "type": "item",
            "item": {
                "type": "collab_tool_call",
                "tool": "spawn_agent",
                "status": "completed",
                "prompt": "SECRET_PROMPT",
                "agents_states": {"child": {"message": "SECRET_REPORT"}},
            },
        })
        event = self.harness.sanitize_codex_stream_line(line)
        self.assertEqual(event, {
            "event": "native_tool_call",
            "tool": "spawn_agent",
            "status": "completed",
            "outcome": "other_terminal_message",
            "agent_statuses": {"unknown": 1},
        })
        self.assertNotIn("SECRET_PROMPT", json.dumps(event))
        self.assertNotIn("SECRET_REPORT", json.dumps(event))

        closed = self.harness.sanitize_codex_stream_line(json.dumps({
            "item": {"type": "collab_tool_call", "tool": "close_agent", "status": "completed"},
        }))
        self.assertEqual(closed["tool"], "close_agent")

    def test_stream_classifies_native_report_and_known_lifecycle_failure(self) -> None:
        native = self.harness.sanitize_codex_stream_line(json.dumps({
            "item": {
                "type": "collab_tool_call",
                "tool": "wait",
                "status": "completed",
                "agents_states": {
                    "child": {
                        "status": "completed",
                        "message": "REPORT_RECORDED report_ref=SECRET_REF\nSECRET_SUMMARY",
                    },
                },
            },
        }))
        self.assertEqual(native["outcome"], "report_recorded")
        self.assertEqual(native["agent_statuses"], {"completed": 1})
        self.assertNotIn("SECRET_REF", json.dumps(native))
        validation = self.harness.classified_native_outcome({
            "child": {"message": "record_report returned report_validation_failed for SECRET_PATH"},
        })
        self.assertEqual(validation, "report_validation_failed")
        lifecycle = self.harness.sanitize_codex_stream_line(json.dumps({
            "item": {
                "type": "mcp_tool_call",
                "tool": "mcp__cortex__continue_orchestration",
                "status": "completed",
                "result": {
                    "structuredContent": {
                        "ok": False,
                        "error": "passed completion requires report_ref from SECRET_REPORT",
                    },
                },
            },
        }))
        self.assertEqual(lifecycle["failure_class"], "reportless_success")
        self.assertNotIn("SECRET_REPORT", json.dumps(lifecycle))

    def test_isolated_codex_runtime_uses_minimal_private_environment_and_cleans_up(self) -> None:
        base = self.root / "runtime-base"
        base.mkdir()
        source_home = self.root / "source-codex-home"
        source_home.mkdir(mode=0o700)
        source_auth = source_home / "auth.json"
        source_auth.write_text("fixture credential", encoding="utf-8")
        source_auth.chmod(0o600)
        environment = {
            "CODEX_HOME": str(source_home),
            "PATH": os.environ.get("PATH", "/usr/bin"),
            "LANG": "C",
            "UNRELATED_HOST_SECRET": "must-not-reach-child",
        }
        with mock.patch.dict(self.harness.os.environ, environment, clear=True):
            with self.harness.isolated_codex_runtime(base) as child_environment:
                private_home = Path(child_environment["CODEX_HOME"])
                self.assertEqual(Path(child_environment["HOME"]), private_home)
                self.assertEqual(private_home.parent, base)
                self.assertEqual(stat.S_IMODE(private_home.stat().st_mode), 0o700)
                self.assertNotIn("UNRELATED_HOST_SECRET", child_environment)
                self.assertNotIn("CODEX_SESSION_ID", child_environment)
                self.assertEqual(child_environment["PATH"], environment["PATH"])
                self.assertEqual(child_environment["LANG"], "C")
                self.assertTrue((private_home / "auth.json").is_file())
                self.assertFalse((private_home / "auth.json").is_symlink())
                self.assertEqual(stat.S_IMODE((private_home / "auth.json").stat().st_mode), 0o600)
                self.assertFalse((private_home / "config.toml").exists())
            self.assertFalse(private_home.exists(), "private runtime home was not cleaned")

    def test_auth_copy_failure_removes_partial_destination_and_preserves_error(self) -> None:
        source = self.root / "source-auth.json"
        source.write_text("fixture credential", encoding="utf-8")
        source.chmod(0o600)
        cases = (
            (
                "read",
                mock.patch.object(self.harness.os, "read", side_effect=OSError("fixture read failure")),
                OSError,
                "fixture read failure",
            ),
            (
                "write",
                mock.patch.object(self.harness.os, "write", return_value=0),
                OSError,
                "unable to write private Codex authentication file",
            ),
            (
                "oversize",
                mock.patch.object(
                    self.harness.os, "read", return_value=b"x" * (self.harness.MAX_AUTH_FILE_BYTES + 1),
                ),
                RuntimeError,
                "exceeds the evaluator size limit",
            ),
        )
        for label, replacement, error_type, error_text in cases:
            with self.subTest(label=label):
                destination = self.root / f"partial-{label}.json"
                with replacement, self.assertRaisesRegex(error_type, error_text):
                    self.harness.copy_private_regular_file(source, destination)
                self.assertFalse(destination.exists(), "partial private credential file survived")

    def test_normal_completion_closes_evaluator_pipe_handles(self) -> None:
        marker = self.root / "closed-normal.marker"
        captured: dict[str, subprocess.Popen[str]] = {}
        real_popen = subprocess.Popen

        def capture_popen(*args: object, **kwargs: object) -> subprocess.Popen[str]:
            process = real_popen(*args, **kwargs)
            captured["process"] = process
            return process

        with mock.patch.object(self.harness.subprocess, "Popen", side_effect=capture_popen):
            result = self.harness.run_live_command(
                [sys.executable, str(self.child), "emit", str(marker)],
                self.project, "fixture", timeout_seconds=5,
            )
        self.assertEqual(result["returncode"], 0)
        process = captured["process"]
        self.assertTrue(process.stdout is not None and process.stdout.closed)
        self.assertTrue(process.stderr is not None and process.stderr.closed)

    def test_selector_setup_exception_closes_pipes_and_restores_handlers(self) -> None:
        marker = self.root / "closed-exception.marker"
        captured: dict[str, subprocess.Popen[str]] = {}
        real_popen = subprocess.Popen
        previous_term = signal.getsignal(signal.SIGTERM)
        previous_int = signal.getsignal(signal.SIGINT)

        def capture_popen(*args: object, **kwargs: object) -> subprocess.Popen[str]:
            process = real_popen(*args, **kwargs)
            captured["process"] = process
            return process

        with (
            mock.patch.object(self.harness.subprocess, "Popen", side_effect=capture_popen),
            mock.patch.object(self.harness.selectors, "DefaultSelector", side_effect=OSError("selector setup failed")),
        ):
            with self.assertRaisesRegex(OSError, "selector setup failed"):
                self.harness.run_live_command(
                    [sys.executable, str(self.child), "silence", str(marker)],
                    self.project, "fixture", timeout_seconds=5,
                )
        process = captured["process"]
        self.assertTrue(process.stdout is not None and process.stdout.closed)
        self.assertTrue(process.stderr is not None and process.stderr.closed)
        self.assertEqual(signal.getsignal(signal.SIGTERM), previous_term)
        self.assertEqual(signal.getsignal(signal.SIGINT), previous_int)

    def test_ledger_progress_exposes_only_bounded_lifecycle_metadata(self) -> None:
        database_dir = self.project / ".codex" / "cortex"
        database_dir.mkdir(parents=True)
        database = database_dir / "cortex.db"
        connection = sqlite3.connect(database)
        connection.executescript(
            """
            CREATE TABLE tasks(status TEXT, state_json TEXT);
            CREATE TABLE logical_artifacts(kind TEXT);
            CREATE TABLE worker_sessions(status TEXT);
            CREATE TABLE ledger_events(event_id INTEGER, event TEXT);
            """
        )
        connection.execute(
            "INSERT INTO tasks VALUES (?, ?)",
            ("active", json.dumps({"attempts": [{"status": "running", "gate": "review", "report": "SECRET_REPORT"}]})),
        )
        connection.execute("INSERT INTO logical_artifacts VALUES ('worker_report')")
        connection.execute("INSERT INTO worker_sessions VALUES ('running')")
        connection.execute("INSERT INTO ledger_events VALUES (1, 'worker_report')")
        connection.commit()
        connection.close()

        progress = self.harness.safe_ledger_progress(self.project)
        self.assertIsNotNone(progress)
        assert progress is not None
        serialized = json.dumps(progress, sort_keys=True)
        self.assertNotIn("SECRET_REPORT", serialized)
        self.assertEqual(set(progress), {
            "tasks", "task_statuses", "attempt_statuses", "gates",
            "worker_reports", "worker_sessions", "latest_ledger_event",
        })
        self.assertEqual(progress["gates"], {"review": 1})
        self.assertEqual(progress["latest_ledger_event"], "worker_report")

    def test_timeout_terminates_and_reaps_fake_process_group(self) -> None:
        _process, _marker, output = self.run_wrapper("group", heartbeat=0.05, timeout=0.2)
        events = self.parse_lines(output)
        final = events[-1]["final"]
        self.assertEqual(final["termination"], "timeout")
        self.assertEqual(final["returncode"], -signal.SIGTERM)
        self.assert_pid_stopped(self.root / "group.pid")

    def test_exited_parent_descendant_is_terminated_and_reaped(self) -> None:
        _process, _marker, output = self.run_wrapper("exited_parent", heartbeat=0.05, timeout=0.2)
        events = self.parse_lines(output)
        final = events[-1]["final"]
        self.assertEqual(final["termination"], "timeout")
        self.assertEqual(final["returncode"], 0)
        self.assert_pid_stopped(self.root / "exited_parent.pid")

    def test_external_sigterm_terminates_and_reaps_fake_process_group(self) -> None:
        process, marker, _ = self.run_wrapper("group", timeout=5, send_sigterm=True)
        assert process.stdout is not None
        pid_path = marker.with_suffix(".pid")
        deadline = time.monotonic() + 3
        while not pid_path.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        process.send_signal(signal.SIGTERM)
        output, error = process.communicate(timeout=15)
        self.assertEqual(process.returncode, 0, error)
        events = self.parse_lines(output)
        final = events[-1]["final"]
        self.assertEqual(final["termination"], f"signal_{signal.SIGTERM}")
        self.assert_pid_stopped(pid_path)

    def test_final_json_is_parseable_and_source_mode_has_no_global_mutations(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cortex-realtime-home-") as home:
            home_path = Path(home)
            environment = os.environ.copy()
            environment.update({
                "HOME": str(home_path),
                "CODEX_HOME": str(home_path / ".codex"),
                "PYTHONDONTWRITEBYTECODE": "1",
            })
            before = sorted(path.relative_to(home_path).as_posix() for path in home_path.rglob("*"))
            completed = subprocess.run(
                [sys.executable, str(SCRIPT)],
                cwd=ROOT,
                env=environment,
                text=True,
                capture_output=True,
                timeout=30,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            payload = json.loads(completed.stdout)
            self.assertEqual(payload["status"], "PASS")
            after = sorted(path.relative_to(home_path).as_posix() for path in home_path.rglob("*"))
            self.assertEqual(before, after, "fixture mode mutated global Codex configuration")

    def test_live_command_uses_this_checkout_source_mcp_without_install_commands(self) -> None:
        expected_server = (ROOT / "plugins/cortex/scripts/cortex.py").resolve()
        self.assertEqual(self.harness.SERVER.resolve(), expected_server)
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertIn('mcp_servers.cortex.command="{sys.executable}"', source)
        self.assertIn('mcp_servers.cortex.args=["{SERVER}"]', source)
        self.assertNotRegex(source, r"codex\s+(?:plugin\s+)?(?:install|add|update|remove)\b")

    def fake_codex(self) -> Path:
        bin_dir = self.root / "bin"
        bin_dir.mkdir()
        codex = bin_dir / "codex"
        codex.write_text(
            "#!/usr/bin/env python3\n"
            "import json\n"
            "print(json.dumps({'type':'item','item':{'type':'mcp_tool_call',"
            "'tool':'mcp__cortex__record_report','status':'completed',"
            "'arguments':{'prompt':'SECRET_PROMPT'},"
            "'result':{'structured_content':{'ok':False,'report':'SECRET_REPORT'}}}}), flush=True)\n",
            encoding="utf-8",
        )
        codex.chmod(0o755)
        return bin_dir

    def isolated_probe_codex(self, mode: str = "normal") -> Path:
        """Create a fake Codex that records only safe metadata outside its home."""
        bin_dir = self.root / "isolated-bin"
        bin_dir.mkdir()
        codex = bin_dir / "codex"
        codex.write_text(
            "#!/usr/bin/env python3\n"
            "import json, os, stat, sys, time\n"
            "from pathlib import Path\n"
            f"MODE = {mode!r}\n"
            "args = sys.argv[1:]\n"
            "project = Path(args[args.index('-C') + 1])\n"
            "probe = project.parent / 'codex-probe.json'\n"
            "codex_home = Path(os.environ['CODEX_HOME'])\n"
            "record = {'argv': args, 'env': {key: os.environ.get(key) for key in ("
            "'HOME', 'CODEX_HOME', 'XDG_CONFIG_HOME', 'XDG_STATE_HOME')}, "
            "'codex_home_exists': codex_home.exists(), "
            "'codex_home_is_symlink': codex_home.is_symlink()}\n"
            "if codex_home.exists() and not codex_home.is_symlink():\n"
            "    record['codex_home_mode'] = stat.S_IMODE(codex_home.stat().st_mode)\n"
            "    auth = codex_home / 'auth.json'\n"
            "    fd = os.open(auth, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)\n"
            "    with os.fdopen(fd, 'w', encoding='utf-8') as stream: stream.write('AUTH_SECRET_VALUE')\n"
            "    record['auth_mode'] = stat.S_IMODE(auth.stat().st_mode)\n"
            "    record['auth_path'] = str(auth)\n"
            "probe.write_text(json.dumps(record, sort_keys=True), encoding='utf-8')\n"
            "print(json.dumps({'type':'item','item':{'type':'mcp_tool_call',"
            "'tool':'mcp__cortex__record_report','status':'completed',"
            "'arguments':{'prompt':'AUTH_SECRET_VALUE'},"
            "'result':{'structured_content':{'ok':False,'report':'AUTH_SECRET_VALUE'}}}}), flush=True)\n"
            "if MODE in {'timeout', 'sigterm'}: time.sleep(60)\n",
            encoding="utf-8",
        )
        codex.chmod(0o755)
        return bin_dir

    def isolated_parent_environment(self, bin_dir: Path) -> tuple[dict[str, str], Path, Path, Path]:
        global_root = self.root / "global-state"
        global_home = global_root / "home"
        global_home.mkdir(parents=True)
        global_codex_target = global_root / "codex-target"
        global_codex_target.mkdir()
        global_codex_link = global_root / "codex-link"
        global_codex_link.symlink_to(global_codex_target, target_is_directory=True)
        (global_home / ".codex-config.toml").write_text("global-sentinel", encoding="utf-8")
        (global_codex_target / "registry.json").write_text("global-registry-sentinel", encoding="utf-8")
        global_auth = global_codex_target / "auth.json"
        global_auth.write_text("GLOBAL_AUTH_SENTINEL", encoding="utf-8")
        global_auth.chmod(0o600)
        environment = os.environ.copy()
        environment.update({
            "PATH": f"{bin_dir}{os.pathsep}{environment.get('PATH', '')}",
            "HOME": str(global_home),
            "CODEX_HOME": str(global_codex_link),
            "PYTHONDONTWRITEBYTECODE": "1",
        })
        return environment, global_home, global_codex_target, global_codex_link

    def run_isolated_probe(self, *, mode: str = "normal") -> tuple[list[dict[str, object]], dict[str, object], Path, Path]:
        base = self.root / f"isolated-{mode}"
        base.mkdir()
        environment, global_home, global_codex_target, global_codex_link = self.isolated_parent_environment(
            self.isolated_probe_codex(mode)
        )
        with mock.patch.dict(os.environ, environment, clear=True), contextlib.redirect_stdout(io.StringIO()) as output:
            results = self.harness.live_eval(
                base, ("automatic_sequential",), timeout_seconds=0.2,
            )
        probe_path = base / "codex-probe.json"
        self.assertTrue(probe_path.is_file(), f"fake Codex did not write probe; output={output.getvalue()!r}")
        probe = json.loads(probe_path.read_text(encoding="utf-8"))
        self.assertIsInstance(probe, dict)
        return results, probe, global_home, global_codex_target

    def assert_isolated_probe_clean(
        self,
        results: list[dict[str, object]],
        probe: dict[str, object],
        global_home: Path,
        global_codex_target: Path,
    ) -> None:
        self.assertEqual(len(results), 1)
        result = results[0]
        self.assertEqual(result.get("failure_metadata"), "not_retained")
        serialized = json.dumps(result, sort_keys=True)
        self.assertNotIn("AUTH_SECRET_VALUE", serialized)
        self.assertTrue(probe["codex_home_exists"])
        self.assertFalse(probe["codex_home_is_symlink"])
        self.assertEqual(probe["codex_home_mode"], 0o700)
        self.assertEqual(probe["auth_mode"], 0o600)
        private_codex_home = Path(str(probe["env"]["CODEX_HOME"]))
        self.assertFalse(private_codex_home.exists(), "isolated Codex home survived evaluator cleanup")
        self.assertNotEqual(private_codex_home, global_codex_target)
        self.assertNotEqual(private_codex_home, global_home / ".codex")
        global_auth = global_codex_target / "auth.json"
        self.assertEqual(global_auth.read_text(encoding="utf-8"), "GLOBAL_AUTH_SENTINEL")
        self.assertEqual(os.stat(global_auth).st_mode & 0o777, 0o600)
        self.assertEqual((global_home / ".codex-config.toml").read_text(encoding="utf-8"), "global-sentinel")
        self.assertEqual((global_codex_target / "registry.json").read_text(encoding="utf-8"), "global-registry-sentinel")
        command = " ".join(str(item) for item in probe["argv"])
        self.assertIn(f'mcp_servers.cortex.command="{sys.executable}"', command)
        self.assertIn(f'mcp_servers.cortex.args=["{self.harness.SERVER}"]', command)

    def test_live_eval_uses_private_codex_home_and_cleans_normal_run(self) -> None:
        results, probe, global_home, global_codex_target = self.run_isolated_probe()
        self.assert_isolated_probe_clean(results, probe, global_home, global_codex_target)
        for key in ("HOME", "CODEX_HOME"):
            self.assertNotEqual(probe["env"][key], str(global_home if key == "HOME" else global_codex_target))
            self.assertTrue(Path(str(probe["env"][key])).is_absolute())

    def test_live_eval_cleans_private_codex_home_after_timeout(self) -> None:
        results, probe, global_home, global_codex_target = self.run_isolated_probe(mode="timeout")
        self.assertEqual(results[0].get("termination"), "timeout")
        self.assert_isolated_probe_clean(results, probe, global_home, global_codex_target)

    def test_live_eval_rejects_symlinked_auth_without_retaining_runtime_home(self) -> None:
        base = self.root / "isolated-symlink-auth"
        base.mkdir()
        bin_dir = self.isolated_probe_codex("normal")
        environment, global_home, global_codex_target, _global_codex_link = self.isolated_parent_environment(bin_dir)
        global_auth = global_codex_target / "auth.json"
        outside_secret = self.root / "outside-auth.json"
        outside_secret.write_text("OUTSIDE_AUTH_SECRET", encoding="utf-8")
        outside_secret.chmod(0o600)
        global_auth.unlink()
        global_auth.symlink_to(outside_secret)
        with mock.patch.dict(os.environ, environment, clear=True), contextlib.redirect_stdout(io.StringIO()) as output:
            with self.assertRaisesRegex(RuntimeError, "regular non-symlink"):
                self.harness.live_eval(base, ("automatic_sequential",), timeout_seconds=10)
        self.assertNotIn("OUTSIDE_AUTH_SECRET", output.getvalue())
        self.assertTrue(global_auth.is_symlink())
        self.assertEqual(outside_secret.read_text(encoding="utf-8"), "OUTSIDE_AUTH_SECRET")
        self.assertEqual(list(base.glob("cortex-luna-high-codex-*")), [])
        self.assertEqual((global_home / ".codex-config.toml").read_text(encoding="utf-8"), "global-sentinel")

    def test_live_eval_cleans_private_codex_home_after_external_sigterm(self) -> None:
        base = self.root / "isolated-sigterm"
        base.mkdir()
        bin_dir = self.isolated_probe_codex("sigterm")
        environment, global_home, global_codex_target, _global_codex_link = self.isolated_parent_environment(bin_dir)
        wrapper = (
            "import importlib.util, json, pathlib, sys\n"
            "script, base = sys.argv[1:]\n"
            "spec = importlib.util.spec_from_file_location('harness', script)\n"
            "module = importlib.util.module_from_spec(spec)\n"
            "assert spec.loader is not None\n"
            "spec.loader.exec_module(module)\n"
            "result = module.live_eval(pathlib.Path(base), ('automatic_sequential',), timeout_seconds=30)\n"
            "print(json.dumps(result, sort_keys=True), flush=True)\n"
        )
        process = subprocess.Popen(
            [sys.executable, "-c", wrapper, str(SCRIPT), str(base)],
            cwd=ROOT,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        probe_path = base / "codex-probe.json"
        deadline = time.monotonic() + 10
        while not probe_path.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertTrue(probe_path.exists(), "SIGTERM fixture did not reach fake Codex")
        process.send_signal(signal.SIGTERM)
        stdout, stderr = process.communicate(timeout=20)
        self.assertEqual(process.returncode, 0, stderr)
        self.assertNotIn("AUTH_SECRET_VALUE", stdout)
        results = json.loads(stdout.splitlines()[-1])
        self.assertEqual(results[0].get("termination"), "signal_15")
        probe = json.loads(probe_path.read_text(encoding="utf-8"))
        self.assertFalse(Path(str(probe["env"]["CODEX_HOME"])).exists())
        global_auth = global_codex_target / "auth.json"
        self.assertEqual(global_auth.read_text(encoding="utf-8"), "GLOBAL_AUTH_SENTINEL")
        self.assertEqual(os.stat(global_auth).st_mode & 0o777, 0o600)
        self.assertEqual((global_home / ".codex-config.toml").read_text(encoding="utf-8"), "global-sentinel")

    def run_failed_live_eval(self, *, retain: bool) -> list[dict[str, object]]:
        base = self.root / ("retained-eval" if retain else "default-eval")
        base.mkdir()
        bin_dir = self.fake_codex()
        environment = os.environ.copy()
        environment["PATH"] = f"{bin_dir}{os.pathsep}{environment.get('PATH', '')}"
        with mock.patch.dict(os.environ, environment, clear=True):
            return self.harness.live_eval(
                base,
                ("automatic_sequential",),
                timeout_seconds=10,
                retain_failure_metadata=retain,
            )

    def test_live_failure_metadata_is_not_retained_by_default(self) -> None:
        real_mkdtemp = tempfile.mkdtemp

        def reject_failure_retention(*, prefix: str = "", dir: str | None = None) -> str:
            if prefix == "cortex-luna-high-failure-":
                raise AssertionError("default failure path retained metadata")
            return real_mkdtemp(prefix=prefix, dir=dir)

        with mock.patch.object(
            self.harness.tempfile,
            "mkdtemp",
            side_effect=reject_failure_retention,
        ):
            results = self.run_failed_live_eval(retain=False)
        self.assertEqual(len(results), 1)
        result = results[0]
        self.assertEqual(result["failure_metadata"], "not_retained")
        self.assertNotIn("failure_artifacts", result)
        self.assertNotIn("SECRET_PROMPT", json.dumps(result, sort_keys=True))
        self.assertNotIn("SECRET_REPORT", json.dumps(result, sort_keys=True))

    def test_live_failure_metadata_requires_opt_in_and_is_sanitized(self) -> None:
        retention_dir = self.root / "retained-failure"
        real_mkdtemp = tempfile.mkdtemp

        def make_retention_dir(*, prefix: str = "", dir: str | None = None) -> str:
            if prefix != "cortex-luna-high-failure-":
                return real_mkdtemp(prefix=prefix, dir=dir)
            retention_dir.mkdir()
            return str(retention_dir)

        with mock.patch.object(self.harness.tempfile, "mkdtemp", side_effect=make_retention_dir):
            results = self.run_failed_live_eval(retain=True)
        self.assertEqual(len(results), 1)
        result = results[0]
        self.assertEqual(Path(result["failure_artifacts"]).resolve(), retention_dir.resolve())
        progress_file = retention_dir / "progress.json"
        self.assertTrue(progress_file.is_file())
        progress = json.loads(progress_file.read_text(encoding="utf-8"))
        serialized = json.dumps(progress, sort_keys=True)
        self.assertNotIn("SECRET_PROMPT", serialized)
        self.assertNotIn("SECRET_REPORT", serialized)
        self.assertLessEqual(len(progress["events"]), 100)
        self.assertEqual(progress["events"][0]["tool"], "record_report")
        self.assertEqual(progress["events"][0]["ok"], False)


if __name__ == "__main__":
    unittest.main()
