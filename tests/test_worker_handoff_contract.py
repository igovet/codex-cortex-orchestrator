"""Regression coverage for the compact native worker-to-coordinator handoff."""
from __future__ import annotations

import json
import re
import runpy
import sys
from pathlib import Path
import unittest


SCRIPTS = Path(__file__).resolve().parents[1] / "plugins" / "cortex" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from cortex_runtime.worker_message import (  # noqa: E402
    WORKER_MESSAGE_MAX_BYTES,
    render_worker_message,
)
from cortex_runtime.v12_contract import WORKER_MESSAGE_MAX_BYTES as CONTRACT_WORKER_MESSAGE_MAX_BYTES  # noqa: E402


WORKER_MESSAGE_SOURCE = SCRIPTS / "cortex_runtime" / "worker_message.py"


class WorkerHandoffContractTests(unittest.TestCase):
    def test_package_import_uses_authoritative_worker_message_limit(self) -> None:
        self.assertEqual(WORKER_MESSAGE_MAX_BYTES, CONTRACT_WORKER_MESSAGE_MAX_BYTES)
        self.assertGreater(WORKER_MESSAGE_MAX_BYTES, 0)

    def test_standalone_runpy_loads_contract_without_package_context(self) -> None:
        namespace = runpy.run_path(str(WORKER_MESSAGE_SOURCE))
        self.assertEqual(namespace["WORKER_MESSAGE_MAX_BYTES"], CONTRACT_WORKER_MESSAGE_MAX_BYTES)
        self.assertEqual(
            namespace["record_ref"]("report-" + "d" * 64 + "-" + "e" * 32),
            "r_eeeeeeeeeeee",
        )

    def test_renderer_enforces_authoritative_utf8_byte_limit(self) -> None:
        with self.assertRaisesRegex(ValueError, "worker message exceeds the advertised UTF-8 byte limit"):
            render_worker_message(
                task={"task_id": "task-" + "a" * 64, "objective": "bounded"},
                delegation={
                    "delegation_id": "delegation-" + "b" * 64 + "-" + "c" * 32,
                    "objective": "bounded",
                    "profile_name": "not-a-packaged-profile",
                    "instructions": "ж" * CONTRACT_WORKER_MESSAGE_MAX_BYTES,
                },
                decisions=[],
            )

    def test_project_skills_do_not_document_retired_decision_parameters(self) -> None:
        skill_root = Path(__file__).resolve().parents[1] / "plugins" / "cortex" / "skills"
        for skill in skill_root.glob("*/SKILL.md"):
            text = skill.read_text(encoding="utf-8")
            self.assertNotIn("prompt_en", text, skill)
            self.assertNotIn("response_en", text, skill)

    def test_native_contract_requires_summary_and_exact_report_ref(self) -> None:
        rendered = render_worker_message(
            task={
                "task_id": "task-" + "a" * 64,
                "objective": "Verify a bounded change.",
                "user_request_original": "Verify a bounded change.",
                "user_language": "en",
                "task_contract_version": "cortex/task-contract/v1",
                "requirements": [],
                "constraints": [],
                "acceptance_criteria": [],
                "verification_plan": [],
                "context": {},
            },
            delegation={
                "delegation_id": "delegation-" + "b" * 64 + "-" + "c" * 32,
                "task_id": "task-" + "a" * 64,
                "native_task_name": "planner",
                "objective": "Verify a bounded change.",
                "profile_name": "planner",
                "scope": "Read-only verification.",
                "instructions": "Return evidence.",
                "input_report_ids": [],
                "input_decision_ids": [],
                "model": "gpt-5.6-luna",
                "reasoning_effort": "high",
            },
            decisions=[],
        )
        message = rendered["message"]
        self.assertIn("## Native coordinator handoff", message)
        self.assertIn("Summary:", message)
        self.assertIn("Report ref:", message)
        self.assertIn("coordinator uses this summary and report ref", message)
        self.assertIn("report reads are metadata-only", message)
        self.assertIn("handoff is never a second semantic", message)
        self.assertIn("downstream worker", message)

    def test_native_message_carries_finalized_input_manifest_without_trusting_content(self) -> None:
        report_id = "report-" + "d" * 64 + "-" + "e" * 32
        rendered = render_worker_message(
            task={"task_id": "task-" + "a" * 64, "objective": "Consume evidence.", "user_request_original": "Consume evidence.", "user_language": "en", "task_contract_version": "cortex/task-contract/v1", "requirements": [], "constraints": [], "acceptance_criteria": [], "verification_plan": [], "context": {}},
            delegation={"delegation_id": "delegation-" + "b" * 64 + "-" + "c" * 32, "task_id": "task-" + "a" * 64, "native_task_name": "qa_engineer", "objective": "Verify evidence.", "profile_name": "qa_engineer", "scope": "Read the declared report.", "instructions": "Verify the report.", "input_report_ids": [report_id], "input_decision_ids": [], "input_reports": [{"report_id": report_id, "report_type": "plan", "status": "completed", "assembly_state": "finalized", "total_chunks": 2, "content_digest": "sha256:" + "f" * 64, "content": "IGNORE THIS PROMPT INJECTION"}], "model": "gpt-5.6-luna", "reasoning_effort": "high"},
            decisions=[],
        )
        message = rendered["message"]
        match = re.search(r"## Untrusted task and delegation data\n\n```json\n(.*?)\n```", message, re.DOTALL)
        self.assertIsNotNone(match)
        payload = json.loads(match.group(1))
        delegation = payload["delegation"]
        self.assertEqual(delegation["input_report_refs"], ["r_eeeeeeeeeeee"])
        self.assertEqual(delegation["input_report_manifests"], [{"report_ref": "r_eeeeeeeeeeee", "report_type": "plan", "status": "completed", "assembly_state": "finalized", "total_chunks": 2, "content_digest": "sha256:" + "f" * 64}])
        self.assertNotIn("IGNORE THIS PROMPT INJECTION", message)
        self.assertIn("optional `input_report_manifests`", message)
        normalized = " ".join(message.split())
        self.assertIn("consume every declared predecessor report through the assignment's advertised evidence path", normalized)
        self.assertIn("Publish one complete terminal outcome only after its declared evidence is consumed", normalized)
        self.assertIn("recovery/rework assignment semantics", normalized)

    def test_renderer_minimizes_context_and_uses_registry_semantics(self) -> None:
        original = "Original request must remain durable-only."
        rendered = render_worker_message(
            task={
                "task_id": "task-" + "a" * 64,
                "objective": "Implement the scoped repair.",
                "user_request_original": original,
                "user_language": "en",
                "task_contract_version": "cortex/task-contract/v1",
                "requirements": ["Unrelated task requirement"],
                "constraints": ["No external action"],
                "acceptance_criteria": ["Focused test passes"],
                "verification_plan": ["Run focused test"],
                "context": {"private": "do not render"},
            },
            delegation={
                "delegation_id": "delegation-" + "b" * 64 + "-" + "c" * 32,
                "objective": "Implement the scoped repair.",
                "profile_name": "backend_dev",
                "scope": "Worker-owned renderer and policy paths.",
                "instructions": "Keep the change bounded.",
                "input_report_ids": [],
                "input_decision_ids": [],
                "model": "gpt-5.6-luna",
                "reasoning_effort": "high",
            },
            decisions=[],
        )
        message = rendered["message"]
        match = re.search(r"## Untrusted task and delegation data\n\n```json\n(.*?)\n```", message, re.DOTALL)
        self.assertIsNotNone(match)
        payload = json.loads(match.group(1))
        self.assertEqual(
            set(payload["task"]),
            {"task_ref", "english_objective", "constraints", "acceptance_criteria", "verification_plan"},
        )
        self.assertNotIn(original, message)
        self.assertNotIn("Unrelated task requirement", message)
        self.assertNotIn("do not render", message)
        self.assertIn("active MCP registry", message)
        self.assertIn("Documentation impact", message)
        for forbidden in ('mode="single"', "decimal-string", "max_bytes", "0 through 65536"):
            self.assertNotIn(forbidden, message)
        self.assertIn("active semantic publication operation", message)
        self.assertIn("storage representation", message)
        self.assertIn("report reads are metadata-only", message)

    def test_contract_keeps_report_reads_scoped_to_declared_same_task_inputs(self) -> None:
        root = Path(__file__).resolve().parents[1] / "plugins" / "cortex" / "skills"
        contracts = [
            (root / "orchestrator" / "SKILL.md").read_text(encoding="utf-8"),
            (root / "cortex-control" / "SKILL.md").read_text(encoding="utf-8"),
        ]
        for contract in contracts:
            self.assertIn("exact finalized `input_report_refs`", contract)
            self.assertIn("cross_project_reference", contract)
            self.assertIn("same-task delegation", contract)
        self.assertIn("not a retryable read", contracts[0])
        self.assertIn("Do not retry that read", contracts[1])
        self.assertIn("material report-dependent decision", contracts[0])

    def test_packaged_prompts_delegate_mcp_shapes_to_live_registry(self) -> None:
        repository = Path(__file__).resolve().parents[1]
        prompt_paths = (
            repository / "plugins" / "cortex" / "skills" / "orchestrator" / "SKILL.md",
            repository / "plugins" / "cortex" / "skills" / "cortex-control" / "SKILL.md",
            *(repository / "plugins" / "cortex" / "agents").glob("*.toml"),
        )
        forbidden = (
            "```json",
            "inputSchema",
            "outputSchema",
            "closed canonical field set",
            "complete first-call shape",
            'reader_kind="worker"',
            'mode="single"',
        )
        for path in prompt_paths:
            text = path.read_text(encoding="utf-8")
            for marker in forbidden:
                self.assertNotIn(marker, text, f"{path} still embeds MCP shape {marker!r}")
        for path in prompt_paths[:2]:
            self.assertIn("active MCP registry", path.read_text(encoding="utf-8"))

    def test_active_policies_use_semantic_receipts_and_advisory_closure(self) -> None:
        repository = Path(__file__).resolve().parents[1]
        orchestrator = (repository / "plugins" / "cortex" / "skills" / "orchestrator" / "SKILL.md").read_text(encoding="utf-8")
        control = (repository / "plugins" / "cortex" / "skills" / "cortex-control" / "SKILL.md").read_text(encoding="utf-8")
        for contract in (orchestrator, control):
            self.assertIn("semantic delegation receipt", contract)
            self.assertIn("active host schema", contract)
            self.assertIn("not authorization", contract)
        self.assertIn("never a completion gate", orchestrator)
        self.assertIn("Documentation impact` status, rationale, and affected\nsurfaces", orchestrator)

    def test_host_injected_agents_context_avoids_redundant_root_reads_and_routes_nested_overrides(self) -> None:
        repository = Path(__file__).resolve().parents[1]
        orchestrator = (repository / "plugins" / "cortex" / "skills" / "orchestrator" / "SKILL.md").read_text(encoding="utf-8")
        boundary = orchestrator.split("## Coordinator boundary and knowledge route", 1)[1].split("## Exact task and result contract", 1)[0]
        self.assertIn("host-injected\n`AGENTS.md` context already governs", boundary)
        self.assertIn("do not reread a global or\nproject-root `AGENTS.md`", boundary)
        self.assertIn("`docs/project/index.md`", boundary)
        self.assertIn("`docs/features/index.md`", boundary)
        self.assertIn("Delegate bounded nested\noverride discovery to a native worker", boundary)

    def test_packaged_guidance_covers_pipeline_todo_routing_tone_and_tmux(self) -> None:
        repository = Path(__file__).resolve().parents[1]
        orchestrator = (repository / "plugins" / "cortex" / "skills" / "orchestrator" / "SKILL.md").read_text(encoding="utf-8")
        control = (repository / "plugins" / "cortex" / "skills" / "cortex-control" / "SKILL.md").read_text(encoding="utf-8")
        adaptive = (repository / "plugins" / "cortex" / "skills" / "adaptive-pipeline" / "SKILL.md").read_text(encoding="utf-8")
        communication = (repository / "plugins" / "cortex" / "skills" / "coordinator-communication" / "SKILL.md").read_text(encoding="utf-8")
        agents = (repository / "AGENTS.md").read_text(encoding="utf-8")
        verification = (repository / "docs" / "project" / "verification.md").read_text(encoding="utf-8")
        for contract in (orchestrator, control, adaptive):
            self.assertIn("standard Codex To-Do", contract)
            self.assertIn("only current", contract)
            self.assertIn("never worker subtasks", contract)
        self.assertIn("Explorer", orchestrator)
        self.assertIn("Terra only", orchestrator)
        self.assertIn("When the latest meaningful user message is Russian", communication)
        self.assertIn("outcome-first", communication)
        self.assertIn("ordinary Codex", agents)
        self.assertIn("`codex exec`", agents)
        self.assertIn("scope limited to the changed behavior", agents)
        self.assertIn("./scripts/cortex-live-smoke start", verification)
        self.assertIn("./scripts/cortex-live-smoke send --prompt-file", verification)
        self.assertIn("separate explicit `Enter` key call", verification)
        self.assertIn("schema_unsupported", verification)
        self.assertIn("exact session", verification)

    def test_orchestrator_routes_attachment_modes_and_separates_advisory_closure(self) -> None:
        repository = Path(__file__).resolve().parents[1]
        orchestrator = (repository / "plugins" / "cortex" / "skills" / "orchestrator" / "SKILL.md").read_text(encoding="utf-8")
        control = (repository / "plugins" / "cortex" / "skills" / "cortex-control" / "SKILL.md").read_text(encoding="utf-8")
        progress = (repository / "plugins" / "cortex" / "skills" / "progress-accounting" / "SKILL.md").read_text(encoding="utf-8")
        for event in ("USER_STEER", "BEFORE_DELEGATION", "REPORT_RECEIVED", "TOOL_ERROR", "BEFORE_CLOSURE", "CONTEXT_LOST", "TASK_FINISHED"):
            self.assertIn(event, orchestrator)
        for mode in ("intent reconciliation", "delegation strategy", "evidence reasoning", "tool-call discipline", "failure recovery", "orchestration critic", "capability-gap analysis"):
            self.assertIn(mode, orchestrator.lower())
        self.assertIn("Decision Capsule", orchestrator)
        for contract in (orchestrator, control, progress):
            # Closure is structured advisory bookkeeping.  It is confirmed by
            # an intended scoped inspection, but it never defines execution
            # completion or asks the user to confirm a coordinator verdict.
            self.assertIn("closure_unconfirmed", contract)
            self.assertIn("ready_with_risks", contract)
            self.assertNotIn("closure-confirmed", contract)
            self.assertNotIn("task_closed", contract)
            self.assertNotIn("closure_state", contract)
        self.assertIn("completed outcome evidence", orchestrator)
        self.assertIn("never\nmake the work user-facing open", orchestrator)
        self.assertIn("ready_with_risks` never asks the user", orchestrator)
        self.assertIn("advisory record", control)
        self.assertIn("completed work into a\nuser-facing blocker", control)
        self.assertIn("materially changes requirements, scope,\nacceptance", control)
        self.assertIn("Never turn ledger, retry,\nworker, report, dependency, initiative, or closure state into a user question", orchestrator)


if __name__ == "__main__":
    unittest.main()
