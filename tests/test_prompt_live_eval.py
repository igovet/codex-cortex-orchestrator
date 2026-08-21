"""Unit coverage for the opt-in Luna-high prompt A/B runner (no network/model calls)."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "plugins/cortex/scripts"))

from cortex_runtime import prompt_live_eval  # noqa: E402


def valid_response() -> dict[str, object]:
    return {
        "route": "worker",
        "report": {
            "summary": "generic", "findings": [], "questions": [], "changed_files": [],
            "tests": [], "evidence": [], "uncertainty": [],
        },
        "next_action": "report_ready", "question_count": 0, "tool_calls": [], "metadata": [],
        "retryable": False, "replayed": False, "completion": "report_ready",
    }


class PromptLiveEvalTests(unittest.TestCase):
    def test_response_schema_is_strictly_typed_for_const_and_array_nodes(self) -> None:
        schema = prompt_live_eval.live_response_schema()
        properties = schema["properties"]
        for field in ("route", "next_action", "question_count", "retryable", "replayed", "completion"):
            self.assertIn("type", properties[field])
            self.assertIn("const", properties[field])

        def assert_array_items(node: object) -> None:
            if not isinstance(node, dict):
                return
            if node.get("type") == "array":
                self.assertIn("items", node)
                self.assertIsInstance(node["items"], dict)
            for child in node.values():
                if isinstance(child, dict):
                    assert_array_items(child)
                elif isinstance(child, list):
                    for item in child:
                        assert_array_items(item)

        assert_array_items(schema)

    def test_invalid_response_schema_is_a_runner_failure_not_environment_block(self) -> None:
        reason, blocked = prompt_live_eval._safe_live_failure_reason(
            b"", b"400 invalid_json_schema: properties.metadata must define items",
        )
        self.assertEqual(reason, "response_schema_rejected")
        self.assertFalse(blocked)

    def test_command_is_luna_high_read_only_and_rejects_model_or_fallback_drift(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            command = prompt_live_eval.build_live_prompt_eval_command(
                codex_path="/usr/bin/codex", workdir=Path(directory),
                response_schema_path=Path(directory) / "schema.json",
                model="gpt-5.6-luna", reasoning_effort="high",
            )
        self.assertIn("--ephemeral", command)
        self.assertIn("--ignore-user-config", command)
        self.assertIn("--ignore-rules", command)
        self.assertEqual(command[command.index("-m") + 1], "gpt-5.6-luna")
        self.assertEqual(command[command.index("-s") + 1], "read-only")
        with self.assertRaisesRegex(ValueError, "model=gpt-5.6-luna and reasoning_effort=high"):
            prompt_live_eval.build_live_prompt_eval_command(
                codex_path="/usr/bin/codex", workdir=ROOT, response_schema_path=ROOT / "schema.json",
                model="gpt-5.6-terra", reasoning_effort="high",
            )
        with self.assertRaisesRegex(ValueError, "forbidden model or fallback"):
            prompt_live_eval.validate_live_prompt_eval_command([
                "/usr/bin/codex", "exec", "--json", "--ephemeral", "--ignore-user-config", "--ignore-rules",
                "--skip-git-repo-check", "-C", str(ROOT), "-s", "read-only", "-m", "gpt-5.6-luna",
                "-c", 'model_reasoning_effort="high"', "--output-schema", "schema.json", "-", "fallback",
            ])

    def test_behavioral_normalization_never_scores_prose_subjectively(self) -> None:
        metrics = prompt_live_eval.normalize_live_behavioral_metrics(
            valid_response(), assignment_markers=["hostile-marker"], output_bytes=256,
            elapsed_seconds=0.2, stream_metrics={"host_tool_events": 0, "observed_output_tokens": 32}, process_ok=True,
        )
        self.assertTrue(all(metrics["checks"].values()))
        leaked = valid_response()
        leaked["metadata"] = ["hostile-marker"]
        failed = prompt_live_eval.normalize_live_behavioral_metrics(
            leaked, assignment_markers=["hostile-marker"], output_bytes=256,
            elapsed_seconds=0.2, stream_metrics={"host_tool_events": 1, "observed_output_tokens": 32}, process_ok=True,
        )
        self.assertFalse(failed["checks"]["no_forbidden_tool_or_metadata"])
        self.assertFalse(failed["checks"]["no_assignment_or_metadata_leakage"])

    def test_missing_codex_is_skip_not_pass(self) -> None:
        executor = prompt_live_eval.CodexLunaHighPromptExecutor()
        with mock.patch.object(prompt_live_eval.shutil, "which", return_value=None):
            result = executor.execute(
                "fixture", model="gpt-5.6-luna", reasoning_effort="high", assignment_markers=["marker"],
            )
        self.assertEqual(result["status"], "SKIP")
        self.assertNotEqual(result["status"], "PASS")

    def test_luna_or_auth_failure_is_blocked_not_pass(self) -> None:
        executor = prompt_live_eval.CodexLunaHighPromptExecutor(
            codex_path="/usr/bin/codex", workdir=ROOT,
            command_runner=lambda *_args: {
                "returncode": 1, "stdout": b"", "stderr": b"authentication required", "elapsed_seconds": 0.1,
            },
        )
        result = executor.execute(
            "fixture", model="gpt-5.6-luna", reasoning_effort="high", assignment_markers=["marker"],
        )
        self.assertEqual(result["status"], "BLOCKED")
        self.assertNotEqual(result["status"], "PASS")

    def test_executor_uses_bounded_luna_high_command_and_normalizes_response(self) -> None:
        observed: dict[str, object] = {}

        def fake_runner(command: object, prompt: str, timeout: int, output_limit: int) -> dict[str, object]:
            observed.update({"command": command, "prompt": prompt, "timeout": timeout, "output_limit": output_limit})
            event = {"item": {"type": "agent_message", "text": json.dumps(valid_response())}}
            return {"returncode": 0, "stdout": (json.dumps(event) + "\n").encode(), "stderr": b"", "elapsed_seconds": 0.1}

        executor = prompt_live_eval.CodexLunaHighPromptExecutor(
            codex_path="/usr/bin/codex", workdir=ROOT, command_runner=fake_runner,
        )
        result = executor.execute(
            "compiled fixture", model="gpt-5.6-luna", reasoning_effort="high", assignment_markers=["marker"],
        )
        self.assertEqual(result["status"], "PASS")
        self.assertIn("Prompt-evaluation response contract", str(observed["prompt"]))
        command = observed["command"]
        self.assertIsInstance(command, list)
        self.assertEqual(command[command.index("-m") + 1], "gpt-5.6-luna")
        self.assertEqual(command[command.index("-s") + 1], "read-only")

    def test_jsonl_envelope_budget_is_separate_from_final_model_output_budget(self) -> None:
        def fake_runner(_command: object, _prompt: str, _timeout: int, stream_limit: int) -> dict[str, object]:
            self.assertGreater(stream_limit, 16_384)
            envelope = b'{"type":"turn.started"}\n' + (b"x" * 20_000) + b"\n"
            final = {"item": {"type": "agent_message", "text": json.dumps(valid_response())}}
            return {
                "returncode": 0, "stdout": envelope + json.dumps(final).encode() + b"\n",
                "stderr": b"", "elapsed_seconds": 0.1,
            }

        executor = prompt_live_eval.CodexLunaHighPromptExecutor(
            codex_path="/usr/bin/codex", workdir=ROOT, command_runner=fake_runner,
        )
        result = executor.execute(
            "compiled fixture", model="gpt-5.6-luna", reasoning_effort="high", assignment_markers=["marker"],
        )
        self.assertEqual(result["status"], "PASS")
        metrics = result["metrics"]
        self.assertGreater(metrics["stream_bytes"], 16_384)
        self.assertLess(metrics["output_bytes"], 16_384)

    def test_live_ab_is_disabled_by_default_and_uses_one_luna_high_executor_when_enabled(self) -> None:
        self.assertEqual(prompt_live_eval.run_live_prompt_ab_evals(), [{
            "status": "SKIP", "reason": "live flag not supplied; no live prompt evidence",
        }])

        class FakeExecutor:
            def __init__(self) -> None:
                self.calls: list[tuple[str, str]] = []

            def execute(self, prompt: str, *, model: str, reasoning_effort: str, assignment_markers: object) -> dict[str, object]:
                self.calls.append((model, reasoning_effort))
                return {"status": "PASS", "metrics": {"checks": {"structured_response": True}}}

        fake = FakeExecutor()
        results = prompt_live_eval.run_live_prompt_ab_evals(enabled=True, executor=fake)  # type: ignore[arg-type]
        self.assertEqual(results[0]["status"], "PASS")
        self.assertEqual(fake.calls, [("gpt-5.6-luna", "high"), ("gpt-5.6-luna", "high")])


if __name__ == "__main__":
    unittest.main()
