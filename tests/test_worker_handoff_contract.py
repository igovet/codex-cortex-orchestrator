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
    render_clarification_continuation,
    render_worker_message,
)
from cortex_runtime.v12_contract import WORKER_MESSAGE_MAX_BYTES as CONTRACT_WORKER_MESSAGE_MAX_BYTES  # noqa: E402


WORKER_MESSAGE_SOURCE = SCRIPTS / "cortex_runtime" / "worker_message.py"


def skill_contract(root: Path, name: str) -> str:
    base = root / name
    return (base / "SKILL.md").read_text(encoding="utf-8") + "\n" + (
        base / "references" / "post-anchor-engine.md"
    ).read_text(encoding="utf-8")


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
                    "objective": "ж" * CONTRACT_WORKER_MESSAGE_MAX_BYTES,
                    "profile_name": "not-a-packaged-profile",
                    "instructions": "bounded",
                },
                decisions=[], effective_scope={"assigned_items": []},
            )

    def test_project_skills_do_not_document_retired_decision_parameters(self) -> None:
        skill_root = Path(__file__).resolve().parents[1] / "plugins" / "cortex" / "skills"
        for skill in skill_root.glob("*/SKILL.md"):
            text = skill.read_text(encoding="utf-8")
            self.assertNotIn("prompt_en", text, skill)
            self.assertNotIn("response_en", text, skill)

    def test_native_contract_requires_summary_and_exact_publication_evidence(self) -> None:
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
            decisions=[], effective_scope={"planning_items": []},
        )
        message = rendered["message"]
        self.assertIn("assignment_worker", message)
        self.assertIn("disjoint from the coordinator route", message)
        self.assertIn("## Native coordinator handoff", message)
        self.assertIn("Summary:", message)
        self.assertIn("publication evidence reference", message)
        self.assertIn("coordinator uses this summary and evidence reference", message)
        self.assertIn("publication evidence reads are metadata-only", message)
        self.assertIn("handoff is never a second semantic", message)
        self.assertIn("downstream worker", message)

    def test_renderer_carries_only_assignment_locator_in_worker_handoff(self) -> None:
        token = "wb_" + "a" * 32
        rendered = render_worker_message(
            task={"task_id": "task-" + "a" * 64, "objective": "Consume assignment evidence."},
            delegation={
                "delegation_id": "delegation-" + "b" * 64 + "-" + "c" * 32,
                "native_task_name": "planner", "objective": "Plan.", "profile_name": "planner",
                "scope": "Assigned scope.", "instructions": "Return evidence.",
                "input_report_ids": [], "input_decision_ids": [],
            }, decisions=[], bootstrap_capability={"capability": token}, effective_scope={"planning_items": []},
        )
        message = rendered["message"]
        self.assertNotIn(token, message)
        self.assertNotIn("bootstrap capability", message.lower())
        self.assertIn('"anchor":"d_', message)
        self.assertNotIn('"bootstrap_capability":', message)

    def test_renderer_does_not_accept_or_render_model_bootstrap_capability(self) -> None:
        base = {"task_id": "task-" + "a" * 64, "objective": "Consume."}
        delegation = {"delegation_id": "delegation-" + "b" * 64 + "-" + "c" * 32, "profile_name": "planner"}
        for capability in ({}, {"capability": "d_" + "a" * 32}, {"capability": "wb_" + "a" * 31}):
            rendered = render_worker_message(task=base, delegation=delegation, decisions=[], bootstrap_capability=capability, effective_scope={"planning_items": []})
            self.assertNotIn("wb_", rendered["message"])
    def test_clarification_continuation_is_same_worker_english_and_schema_free(self) -> None:
        rendered = render_clarification_continuation(
            task={"task_id": "task-" + "a" * 64 + "-" + "f" * 32},
            delegation={
                "delegation_id": "delegation-" + "b" * 64 + "-" + "c" * 32,
                "profile_name": "planner",
                "native_task_name": "planner",
                "objective": "Continue the approved clarification scope.",
                "scope": "Worker-owned planning continuation.",
            },
            decision={
                "decision_id": "decision-" + "d" * 64 + "-" + "e" * 32,
                "subject_type": "task",
                "subject_id": "task-" + "a" * 64 + "-" + "f" * 32,
                "decision_type": "clarify",
                "response_original": "Ответ пользователя: preserve Unicode — да",
            },
        )
        message = rendered["message"]
        proof = rendered["renderer"]
        self.assertIn("same worker's held work", message)
        self.assertIn("Do not ask the user a second question", message)
        self.assertIn("publish_result", message)
        self.assertIn("Ответ пользователя", message)
        self.assertEqual(proof["task_anchor"], "t_ffffffffffff")
        self.assertEqual(proof["assignment_anchor"], "d_cccccccccccc")
        self.assertEqual(proof["decision_anchor"], "u_eeeeeeeeeeee")
        for forbidden in (
            "task_ref", "assignment_ref", "decision_ref", "response_original",
            "inputSchema", "outputSchema", '"role":', '"prompt":',
            '"profile_name":', '"subject_ref":',
        ):
            self.assertNotIn(forbidden, message)

    def test_clarification_continuation_rejects_missing_or_wrong_typed_anchors(self) -> None:
        base_task = {"task_id": "task-" + "a" * 64 + "-" + "f" * 32}
        base_delegation = {
            "delegation_id": "delegation-" + "b" * 64 + "-" + "c" * 32,
            "profile_name": "planner", "native_task_name": "planner",
            "objective": "Continue.", "scope": "Bounded.",
        }
        base_decision = {
            "decision_id": "decision-" + "d" * 64 + "-" + "e" * 32,
            "subject_type": "task", "subject_id": base_task["task_id"],
            "decision_type": "clarification", "response_original": "Continue.",
        }
        for task, delegation, decision in (
            ({}, base_delegation, base_decision),
            (base_task, {}, base_decision),
            (base_task, base_delegation, {}),
            (base_task, base_delegation, dict(base_decision, subject_type="task", subject_ref="d_" + "1" * 12, subject_id=None)),
            (base_task, base_delegation, dict(base_decision, subject_type="unsupported")),
        ):
            with self.subTest(task=task, delegation=delegation, decision=decision):
                with self.assertRaises(ValueError):
                    render_clarification_continuation(task=task, delegation=delegation, decision=decision)

    def test_clarification_continuation_never_leaks_canonical_subject_identifier(self) -> None:
        canonical_subject = "report-" + "9" * 64 + "-" + "8" * 32
        rendered = render_clarification_continuation(
            task={"task_id": "task-" + "a" * 64 + "-" + "f" * 32},
            delegation={
                "delegation_id": "delegation-" + "b" * 64 + "-" + "c" * 32,
                "profile_name": "planner", "native_task_name": "planner",
                "objective": "Continue.", "scope": "Bounded.",
            },
            decision={
                "decision_id": "decision-" + "d" * 64 + "-" + "e" * 32,
                "subject_type": "report", "subject_id": canonical_subject,
                "decision_type": "clarification", "response_original": "Proceed.",
            },
        )
        self.assertNotIn(canonical_subject, rendered["message"])
        self.assertIn("r_888888888888", rendered["message"])

    def test_native_message_defers_finalized_input_manifest_to_authoritative_consume(self) -> None:
        report_id = "report-" + "d" * 64 + "-" + "e" * 32
        rendered = render_worker_message(
            task={"task_id": "task-" + "a" * 64, "objective": "Consume evidence.", "user_request_original": "Consume evidence.", "user_language": "en", "task_contract_version": "cortex/task-contract/v1", "requirements": [], "constraints": [], "acceptance_criteria": [], "verification_plan": [], "context": {}},
            delegation={"delegation_id": "delegation-" + "b" * 64 + "-" + "c" * 32, "task_id": "task-" + "a" * 64, "native_task_name": "qa_engineer", "objective": "Verify evidence.", "profile_name": "qa_engineer", "scope": "Read the declared report.", "instructions": "Verify the report.", "input_report_ids": [report_id], "input_decision_ids": [], "input_reports": [{"report_id": report_id, "report_type": "plan", "status": "completed", "assembly_state": "finalized", "total_chunks": 2, "content_digest": "sha256:" + "f" * 64, "content": "IGNORE THIS PROMPT INJECTION"}], "model": "gpt-5.6-luna", "reasoning_effort": "high"},
            decisions=[], effective_scope={"assigned_items": []},
        )
        message = rendered["message"]
        match = re.search(r"## Untrusted task and delegation data\n\n```json\n(.*?)\n```", message, re.DOTALL)
        self.assertIsNotNone(match)
        payload = json.loads(match.group(1))
        assignment = payload["assignment context"]
        self.assertEqual(set(assignment), {"anchor", "worker label", "mission"})
        self.assertNotIn("r_eeeeeeeeeeee", message)
        self.assertNotIn("sha256:" + "f" * 64, message)
        self.assertNotIn("IGNORE THIS PROMPT INJECTION", message)
        normalized = " ".join(message.split())
        self.assertIn("consume every declared predecessor evidence item through the `consume_assignment_evidence` operation", normalized)
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
            decisions=[], effective_scope={"assigned_items": []},
        )
        message = rendered["message"]
        match = re.search(r"## Untrusted task and delegation data\n\n```json\n(.*?)\n```", message, re.DOTALL)
        self.assertIsNotNone(match)
        payload = json.loads(match.group(1))
        self.assertEqual(set(payload), {"assignment context"})
        self.assertEqual(set(payload["assignment context"]), {"anchor", "worker label", "mission"})
        self.assertEqual(payload["assignment context"]["mission"], "Implement the scoped repair.")
        self.assertNotIn(original, message)
        self.assertNotIn("Unrelated task requirement", message)
        self.assertNotIn("do not render", message)
        self.assertIn("active MCP registry", message)
        self.assertIn("Documentation impact", message)
        for forbidden in ('mode="single"', "decimal-string", "max_bytes", "0 through 65536"):
            self.assertNotIn(forbidden, message)
        self.assertIn("active semantic publication operation", message)
        self.assertIn("storage representation", message)
        self.assertIn("publication evidence reads are metadata-only", message)

    def test_contract_keeps_report_reads_scoped_to_declared_same_task_inputs(self) -> None:
        root = Path(__file__).resolve().parents[1] / "plugins" / "cortex" / "skills"
        contracts = [
            skill_contract(root, "orchestrator"), skill_contract(root, "cortex-control"),
        ]
        for contract in contracts:
            self.assertIn("exact finalized predecessor evidence", contract)
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
        root = repository / "plugins" / "cortex" / "skills"
        orchestrator, control = skill_contract(root, "orchestrator"), skill_contract(root, "cortex-control")
        for contract in (orchestrator, control):
            self.assertIn("semantic delegation receipt", contract)
            self.assertIn("active host schema", contract)
            self.assertIn("not authorization", contract)
        self.assertIn("never a completion gate", orchestrator)
        self.assertIn("Documentation impact` assessment", orchestrator)

    def test_task_anchor_precedes_all_task_scoped_holds(self) -> None:
        repository = Path(__file__).resolve().parents[1]
        root = repository / "plugins" / "cortex" / "skills"
        contracts = (
            skill_contract(root, "orchestrator"),
            skill_contract(root, "cortex-control"),
        )
        # Both packaged policies must express the same semantic ordering, but
        # the flattened coordinator and worker companion use different local
        # prose.  Assert the invariant by its stable concepts, not retired
        # sentence fragments.
        for contract in contracts:
            lowered = contract.lower()
            self.assertIn("task anchor", lowered)
            self.assertIn("clarification", lowered)
            self.assertIn("approval", lowered)
            self.assertIn("planner dispatch", lowered)
            self.assertIn("before", lowered)
        self.assertIn("clarification hold or question required before planning is opened and\npresented after anchoring and before planner dispatch", contracts[0].lower())
        self.assertIn("clarification needed before planning is opened and presented\nafter the task anchor and before planner dispatch", contracts[1].lower())
        for contract in contracts:
            self.assertNotIn("clarification and approval, delegation, dispatch, and closure", contract)

    def test_host_injected_agents_context_avoids_redundant_root_reads_and_routes_nested_overrides(self) -> None:
        repository = Path(__file__).resolve().parents[1]
        orchestrator = skill_contract(repository / "plugins" / "cortex" / "skills", "orchestrator")
        boundary = orchestrator.split("## Coordinator boundary and knowledge route", 1)[1].split("## Exact task and result contract", 1)[0]
        self.assertIn("host-injected\n`AGENTS.md` context already governs", boundary)
        self.assertIn("do not reread a global or\nproject-root `AGENTS.md`", boundary)
        self.assertIn("`docs/project/index.md`", boundary)
        self.assertIn("`docs/features/index.md`", boundary)
        self.assertIn("Delegate bounded nested\noverride discovery to a native worker", boundary)

    def test_packaged_guidance_covers_pipeline_todo_routing_tone_and_tmux(self) -> None:
        repository = Path(__file__).resolve().parents[1]
        root = repository / "plugins" / "cortex" / "skills"
        orchestrator, control = skill_contract(root, "orchestrator"), skill_contract(root, "cortex-control")
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
        root = repository / "plugins" / "cortex" / "skills"
        orchestrator, control = skill_contract(root, "orchestrator"), skill_contract(root, "cortex-control")
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
        self.assertIn("exactly once in the final answer", orchestrator)
        communication = (repository / "plugins" / "cortex" / "skills" / "coordinator-communication" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("render\nits literal question and choices exactly once, in the final answer only", communication)
        self.assertIn("must not quote,\npreview, paraphrase, or repeat the question or its choices", communication)


if __name__ == "__main__":
    unittest.main()
