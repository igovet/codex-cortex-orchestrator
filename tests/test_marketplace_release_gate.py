"""Single black-box marketplace release gate for Cortex.

This is intentionally one gate, not a unit-test suite.  It validates the
published shape and exercises the source-mode Start/Stop completion contract.
"""
from __future__ import annotations

import ast
import json
import os
import py_compile
import subprocess
import selectors
import sys
import tempfile
import time
import re
from pathlib import Path


def test_cortex_plugin_is_publishable_and_operational(tmp_path: Path) -> None:
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
    require = lambda ok, label: (_ for _ in ()).throw(AssertionError(label)) if not ok else None

    def contains_key(value: object, key: str) -> bool:
        if isinstance(value, dict):
            return key in value or any(contains_key(item, key) for item in value.values())
        if isinstance(value, list):
            return any(contains_key(item, key) for item in value)
        return False

    manifest = json.loads((plugin / ".codex-plugin/plugin.json").read_text(encoding="utf-8"))
    require(manifest.get("name") == "cortex", "marketplace manifest name")
    require(re.fullmatch(r"11\.0\.1\+codex\.[0-9]+", str(manifest.get("version", ""))) is not None, "release keeps v11.0.1 and changes only numeric cache hash")
    hooks = json.loads((plugin / "hooks/hooks.json").read_text(encoding="utf-8")).get("hooks", {})
    require(set(hooks) == {"SessionStart", "SubagentStart", "SubagentStop"}, "only active native lifecycle hooks are registered")

    contracts = json.loads((plugin / ".mcp.json").read_text(encoding="utf-8"))
    require(isinstance(contracts, dict), "public MCP contract is valid JSON")
    source_files = [scripts / "cortex.py", *sorted((scripts / "cortex_runtime").glob("*.py"))]
    for source in source_files:
        ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
        py_compile.compile(str(source), doraise=True)

    question_text = (plugin / "scripts/cortex_runtime/questions.py").read_text(encoding="utf-8")

    # Run one long-lived public JSON-RPC server against an isolated project.
    # Every lifecycle operation below is sent through this process; no direct
    # runtime imports or test-only schema copies are used.
    with tempfile.TemporaryDirectory(prefix="cortex-release-gate-") as isolated:
        project_root = Path(isolated)
        host_state = project_root.parent / f"cortex-host-state-{project_root.name}"
        host_state.mkdir(mode=0o700)
        host_state.chmod(0o700)
        runtime_env = os.environ.copy()
        runtime_env["CORTEX_HOST_STATE_DIR"] = str(host_state)
        process = subprocess.Popen(
            [sys.executable, str(scripts / "cortex.py")], cwd=project_root,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, bufsize=1, env=runtime_env,
        )
        counter = 0
        selector = selectors.DefaultSelector()
        selector.register(process.stdout, selectors.EVENT_READ)

        def rpc(method: str, params: dict[str, object]) -> dict[str, object]:
            nonlocal counter
            counter += 1
            assert process.stdin is not None and process.stdout is not None
            process.stdin.write(json.dumps({"jsonrpc": "2.0", "id": counter, "method": method, "params": params}, ensure_ascii=False) + "\n")
            process.stdin.flush()
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                ready = selector.select(max(0.05, deadline - time.monotonic()))
                if not ready:
                    continue
                line = process.stdout.readline()
                if not line:
                    break
                payload = json.loads(line)
                if payload.get("id") == counter:
                    return payload
            raise AssertionError(f"MCP request timed out: {method}")

        def notify(method: str, params: dict[str, object]) -> None:
            process.stdin.write(json.dumps({"jsonrpc": "2.0", "method": method, "params": params}, ensure_ascii=False) + "\n")
            process.stdin.flush()

        def structured(payload: dict[str, object]) -> dict[str, object]:
            result = payload.get("result")
            require(isinstance(result, dict), "MCP tool result envelope")
            value = result.get("structuredContent")
            require(isinstance(value, dict), "structured public Cortex response")
            return value

        def tool(name: str, arguments: dict[str, object], thread: str) -> dict[str, object]:
            payload = rpc("tools/call", {"name": name, "arguments": arguments, "_meta": {"threadId": thread}})
            value = structured(payload)
            result = payload.get("result") if isinstance(payload, dict) else None
            content = result.get("content") if isinstance(result, dict) else None
            text = str(content[0].get("text") or "") if (
                isinstance(content, list) and content and isinstance(content[0], dict)
            ) else ""
            dispatches = value.get("dispatches")
            if isinstance(dispatches, list) and dispatches:
                calls = {str(item.get("call") or "") for item in dispatches if isinstance(item, dict)}
                require(value.get("next_native_action") == "wait_agent", f"{name} dispatch requires wait_agent next")
                require(value.get("read_worker_wave_allowed") is False, f"{name} forbids premature wave read")
                require(value.get("wait_policy") == "repeat_until_all_bound_children_terminal", f"{name} dispatch has a closed repeat-wait policy")
                require("wait_agent" in text and "do not call any Cortex read or lifecycle tool, including read_worker_wave" in text, f"{name} text leads with dispatch-wait-read order")
                for marker in ('"No agents completed yet"', "timeout", "empty completion set", "still-working", "pendingInit", "running", "interrupted", "completed", "errored", "shutdown", "notFound"):
                    require(marker in text, f"{name} wait summary classifies native status: {marker}")
                require(text.index("wait_agent") < text.index("read_worker_wave"), f"{name} text orders wait before read")
                if calls == {"spawn_agent"}:
                    require("exact child identifier" in text, f"{name} preserves host-returned spawn child identity")
                elif calls == {"followup_task"}:
                    require("same-child followup_task" in text and "exact bound child" in text, f"{name} preserves same-child followup identity")
                else:
                    raise AssertionError(f"{name} returned an unsupported mixed native dispatch wave")
            elif value.get("action") == "wait_for_bound_workers":
                require(value.get("next_native_action") == "wait_agent" and value.get("read_worker_wave_allowed") is False, f"{name} wait state forbids premature read")
                require(value.get("outcome") == "waiting_workers" and value.get("wait_policy") == "repeat_until_all_bound_children_terminal", f"{name} wait state is an expected repeat-until-terminal success")
                require(value.get("ok") is True and value.get("state_mutated") is False, f"{name} premature/nonterminal wait route is successful and nonmutating")
                for marker in ('"No agents completed yet"', "timeout", "empty completion set", "NONTERMINAL", "Immediately invoke wait_agent again", "do not call any Cortex read or lifecycle tool"):
                    require(marker in text, f"{name} repeat-wait summary is explicit: {marker}")
            elif value.get("ok") is True and value.get("action") == "read_worker_wave":
                require(value.get("next_native_action") == "read_worker_wave" and value.get("read_worker_wave_allowed") is True, f"{name} terminal state explicitly permits wave read")
                require("wait_policy" not in value, f"{name} terminal read permission carries no repeat-wait policy")
            elif value.get("ok") is True and value.get("action") == "continue":
                require("continue_orchestration" in str(value.get("content") or ""), f"{name} continue action names the exact next operation")
                require("wait_agent" in str(value.get("content") or "") and "read_worker_wave" in str(value.get("content") or ""), f"{name} continue action forbids the deadlocking alternatives")
            return value

        def read_all_text_pages(name: str, arguments: dict[str, object], thread: str, label: str) -> str:
            parts: list[str] = []
            cursor: str | None = None
            while True:
                page_arguments = {**arguments, **({"cursor": cursor} if cursor else {})}
                page = tool(name, page_arguments, thread)
                require(page.get("ok") is True, f"{label} page succeeds")
                parts.append(str(page.get("content") or page.get("report") or ""))
                next_cursor = page.get("next_cursor")
                if isinstance(next_cursor, str) and next_cursor:
                    require(page.get("complete") is False, f"{label} nonterminal page is explicitly incomplete")
                    cursor = next_cursor
                    continue
                require(page.get("complete") is True, f"{label} terminal page is explicitly complete")
                if not isinstance(next_cursor, str) or not next_cursor:
                    return "".join(parts)

        def read_briefing_predecessors(dispatch_ref: str, thread: str, label: str) -> str:
            briefing_text = read_all_text_pages(
                "read_dispatch_briefing", {"dispatch_ref": dispatch_ref}, thread, f"{label} briefing",
            )
            predecessor_refs = list(dict.fromkeys(
                re.findall(r"report-v1-[0-9a-f]{64}", briefing_text)
            ))
            require(predecessor_refs, f"{label} briefing carries predecessor result authority")
            for predecessor_ref in predecessor_refs:
                read_all_text_pages(
                    "read_predecessor_result",
                    {"dispatch_ref": dispatch_ref, "report_ref": predecessor_ref},
                    thread,
                    f"{label} predecessor result",
                )
            return briefing_text

        initialized = rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "release-gate", "version": "1"}})
        require("result" in initialized, "MCP initialize succeeds")
        server_instructions = str(initialized.get("result", {}).get("instructions") or "")
        require(
            "wait_agent for its exact bound child" in server_instructions
            and '"No agents completed yet"' in server_instructions
            and "do not call any Cortex read or lifecycle tool, including read_worker_wave" in server_instructions,
            "coordinator server instructions enforce the repeat-until-terminal dispatch-wait-read order",
        )
        notify("notifications/initialized", {})
        tools = rpc("tools/list", {})
        listed = tools.get("result", {}).get("tools", []) if isinstance(tools.get("result"), dict) else []
        names = {item.get("name") for item in listed if isinstance(item, dict)}
        listed_by_name = {
            item.get("name"): item for item in listed
            if isinstance(item, dict) and isinstance(item.get("name"), str)
        }
        start_input = listed_by_name["start_orchestration"].get("inputSchema", {})
        start_props = start_input.get("properties", {}) if isinstance(start_input, dict) else {}
        require("project_root" not in start_props, "host workspace is not model-generated")
        require("governance_mode" in start_props, "governance mode remains public")
        require(set(start_props["governance_mode"].get("enum", [])) == {"auto", "required", "minimal"}, "governance mode enum is current")
        worker_schema = start_props.get("waves", {}).get("items", {}).get("properties", {}).get("workers", {}).get("items", {})
        worker_props = worker_schema.get("properties", {}) if isinstance(worker_schema, dict) else {}
        require("allowed_paths" not in worker_props, "removed worker ACL is not public")
        require({"model", "reasoning_effort"}.issubset(worker_props), "orchestrator controls worker model and effort")
        require(
            worker_props["model"].get("enum") == ["gpt-5.6-luna", "gpt-5.6-terra", "gpt-5.6-sol"],
            "worker model enum is derived from native capabilities",
        )
        require(
            worker_props["reasoning_effort"].get("enum") == ["low", "medium", "high", "xhigh", "max"],
            "worker effort enum is independent from model recommendations",
        )
        continue_schema = listed_by_name["continue_orchestration"].get("inputSchema", {})
        require(set(continue_schema.get("required", [])) == {"task_ref", "coordinator_ref"}, "continue uses only task and coordinator authority")
        revise_schema = listed_by_name["revise_future_pipeline"].get("inputSchema", {})
        require("current_step" not in revise_schema.get("properties", {}), "future revision derives current frontier")
        # Growing public reads must all expose the same optional opaque cursor.
        growing = {
            str(item["name"]): item for item in listed
            if isinstance(item, dict) and isinstance(item.get("name"), str)
            and any(token in str(item.get("description", "")).lower() for token in ("page", "pagination", "continuation cursor"))
        }
        for name, item in growing.items():
            schema = item.get("inputSchema", {})
            require("cursor" in schema.get("properties", {}), f"growing read exposes cursor: {name}")
        for name, item in listed_by_name.items():
            if "_lane" in str(name) or "_resource" in str(name):
                props = item.get("inputSchema", {}).get("properties", {})
                require(all(isinstance(value, dict) and value.get("type") not in {"object", "array"} for value in props.values()), f"lane/resource input is flat: {name}")
        require("step" not in listed_by_name.get("read_worker_wave", {}).get("inputSchema", {}).get("properties", {}), "wave read derives active step server-side")
        for name in ("start_orchestration", "continue_orchestration", "answer_orchestration_question", "read_worker_wave"):
            description = str(listed_by_name.get(name, {}).get("description") or "")
            require("wait_agent" in description and "read_worker_wave" in description, f"{name} description states wait-before-read lifecycle")
        require("only after wait_agent has reported every exact bound child terminal" in str(listed_by_name["read_worker_wave"].get("description") or ""), "read_worker_wave description states its exact legality precondition")
        require("continue means call continue_orchestration immediately" in str(listed_by_name["read_worker_wave"].get("description") or ""), "read_worker_wave description exposes the orphaned-followup recovery transition")
        require("final action=continue means call continue_orchestration exactly once" in str(listed_by_name["inspect_orchestration"].get("description") or ""), "inspect description exposes only the final-page orphaned-followup recovery transition")
        require("exactly once when inspect_orchestration/read_worker_wave returns continue" in str(listed_by_name["continue_orchestration"].get("description") or ""), "continue description authorizes the exact cold-resume transition")
        control_skill = (plugin / "skills/cortex-control/SKILL.md").read_text(encoding="utf-8")
        orchestrator_skill = (plugin / "skills/orchestrator/SKILL.md").read_text(encoding="utf-8")
        require("read_worker_wave` is forbidden until" in control_skill and "exact bound child terminal" in control_skill, "runtime skill states dispatch-wait-read order")
        require("Do not call `read_worker_wave` until `wait_agent`" in orchestrator_skill, "orchestrator skill matches wait-before-read contract")
        require("result_refs" not in listed_by_name.get("start_follow_up", {}).get("inputSchema", {}).get("properties", {}), "follow-up derives canonical results server-side")
        question_schema = listed_by_name["ask_worker_question"].get("inputSchema", {})
        question_props = question_schema.get("properties", {}) if isinstance(question_schema, dict) else {}
        require(question_schema.get("additionalProperties") is False, "worker question input is closed")
        require(set(question_schema.get("required", [])) == {"dispatch_ref", "question_category", "question_text"}, "worker question requires one flat semantic category")
        require(set(question_props) == {"dispatch_ref", "question_category", "question_text"}, "worker question exposes only flat current fields")
        require(set(question_props["question_category"].get("enum", [])) == {"product", "requirement", "scope", "acceptance", "destructive_authorization", "external_authorization"}, "worker question categories are real user-decision boundaries")
        require(all(item.get("type") == "string" for item in question_props.values()), "worker question input remains flat text")
        # Keep these assertions derived from the active public worker contract.
        # Ordinary completion, governance closure, and patch repair are three
        # distinct tools; a closure worker must never fall back to the general
        # submit branch merely to satisfy this release gate.
        require({"start_orchestration", "read_dispatch_briefing", "list_worker_reports", "read_predecessor_result", "record_attempt_event", "submit_attempt", "submit_governance_closure", "repair_attempt", "read_worker_wave"}.issubset(names), "public lifecycle tools are listed")
        report_list_schema = listed_by_name["list_worker_reports"].get("inputSchema", {})
        require(set(report_list_schema.get("required", [])) == {"dispatch_ref"}, "report catalog requires only worker authority")
        require(set(report_list_schema.get("properties", {})) == {"dispatch_ref", "cursor"}, "report catalog is flat and paginated")
        predecessor_schema = listed_by_name["read_predecessor_result"].get("inputSchema", {})
        require(set(predecessor_schema.get("required", [])) == {"dispatch_ref", "report_ref"}, "predecessor reader requires opaque report selection")
        require(set(predecessor_schema.get("properties", {})) == {"dispatch_ref", "report_ref", "cursor"}, "predecessor reader is flat and paginated with no legacy result field")
        require(predecessor_schema.get("properties", {}).get("report_ref", {}).get("pattern") == r"^report-v1-[0-9a-f]{64}$", "worker report references are opaque server capabilities")
        closure_schema = listed_by_name["submit_governance_closure"].get("inputSchema", {})
        closure_props = closure_schema.get("properties", {}) if isinstance(closure_schema, dict) else {}
        require(closure_schema.get("type") == "object" and closure_schema.get("additionalProperties") is False, "governance closure input is a closed object")
        require(set(closure_schema.get("required", [])) == {"dispatch_ref", "closure_outcome", "blocking_gaps_text", "report"}, "governance closure required fields are current")
        require(set(closure_props) == {"dispatch_ref", "closure_outcome", "blocking_gaps_text", "report"}, "governance closure has only its flat public fields")
        require(all(isinstance(value, dict) and value.get("type") == "string" for value in closure_props.values()), "governance closure input is flat scalar text")
        require(set(closure_props["closure_outcome"].get("enum", [])) == {"verified", "blocked"}, "governance closure outcomes are current")
        require(closure_props["blocking_gaps_text"].get("minLength") == 0 and "Unicode" in closure_props["blocking_gaps_text"].get("description", ""), "governance closure gap text is explicit arbitrary Unicode")
        ordinary_submit_props = listed_by_name["submit_attempt"].get("inputSchema", {}).get("properties", {})
        require("closure_outcome" not in ordinary_submit_props and "blocking_gaps_text" not in ordinary_submit_props, "ordinary completion exposes no governance-close branch")
        root_thread = "root-release-thread"
        bad = tool("start_orchestration", {"user_request": "Unicode smoke: Проверка 🚀", "waves": [{"phase_kind": "implementation", "workers": [{"objective": "Read-only release gate worker", "profile": "general", "model": "gpt-5.6-luna", "reasoning_effort": "medium", "operation_kind": "inspect"}]}]}, root_thread)
        require(
            bad.get("ok") is False
            and bad.get("error_code") == "host_workspace_unavailable"
            and bad.get("retryable") is True
            and bad.get("state_mutated") is False
            , "start fails closed without a trusted SessionStart workspace",
        )
        session_event = {"cwd": str(project_root), "hook_event_name": "SessionStart", "model": "gpt-5", "permission_mode": "default", "session_id": root_thread, "source": "startup", "transcript_path": "/tmp/release-gate-session"}
        subprocess.run([sys.executable, str(scripts / "cortex_hook.py")], input=json.dumps(session_event), text=True, capture_output=True, check=True, env=runtime_env)
        invalid_before = len(list((host_state / "projects").glob("*/tasks/*")))
        for invalid_model, invalid_effort in (
            ("gpt-5.6-luna", "ultra"),
            ("gpt-5.6-terra", "ultra"),
            ("gpt-5.6-sol", "ultra"),
            ("gpt-5.6-unknown", "high"),
            ("gpt-5.6-terra", "none"),
        ):
            invalid_pair = tool("start_orchestration", {"user_request": "Invalid pair must not mutate", "waves": [{"phase_kind": "discover", "workers": [{"objective": "Reject invalid pair", "profile": "explorer", "model": invalid_model, "reasoning_effort": invalid_effort, "operation_kind": "inspect"}]}]}, root_thread)
            require(
                invalid_pair.get("ok") is False
                and invalid_pair.get("state_mutated") is False,
                f"invalid model/effort is rejected nonmutating: {invalid_model}/{invalid_effort}",
            )
        require(len(list((host_state / "projects").glob("*/tasks/*"))) == invalid_before, "invalid model/effort creates no task")

        capability_probe = subprocess.run(
            [sys.executable, "-c", """
import json, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import cortex
from cortex_runtime.assignment_compiler import reliability_recovery_target
from cortex_runtime.model_routing import model_effort_pair_is_allowed
profile_contract = json.loads((Path(sys.argv[1]).parent / 'profiles.json').read_text(encoding='utf-8'))
profiles = {item['name']: item for item in profile_contract['profiles']}
cases = {
    'gpt-5.6-luna': ('low', 'medium', 'high', 'xhigh', 'max'),
    'gpt-5.6-terra': ('low', 'medium', 'high', 'xhigh', 'max'),
    'gpt-5.6-sol': ('low', 'medium', 'high', 'xhigh', 'max'),
}
for model, efforts in cases.items():
    for effort in efforts:
        assert model_effort_pair_is_allowed(cortex.MODEL_EFFORTS, model, effort)
        request = {'host_tool':'spawn_agent','model':model,'expected_model':model,'configured_default_model':'gpt-5.6-luna','task_name':'probe','message':'probe','reasoning_effort':effort,'fork_turns':'none'}
        args = cortex._v11_native_arguments(request)
        assert args['reasoning_effort'] == effort
        if model == 'gpt-5.6-luna':
            assert 'model' not in args
        else:
            assert args['model'] == model
source = {'operation_kind':'inspect','selected_model':'gpt-5.6-luna','resolved_profile':'general','selected_reasoning_effort':'high'}
target = reliability_recovery_target(source, profiles, cortex.MODEL_EFFORTS, cortex.MODEL_RECOMMENDED_EFFORTS)
assert target['model'] == 'gpt-5.6-terra'
assert target['reasoning_effort'] == 'high'
assert target['effort_resolution_reason'] == 'requested_effort_preserved'
print('native-capability-and-fallback-pass')
""", str(scripts)],
            text=True, capture_output=True, check=True, env=runtime_env,
        )
        require("native-capability-and-fallback-pass" in capability_probe.stdout, "native serializer and technical fallback preserve independent effort")
        recovery_context_probe = subprocess.run(
            [sys.executable, "-c", r'''
import sys
sys.path.insert(0, sys.argv[1])
import cortex
from cortex_runtime import orchestration_engine as engine
from cortex_runtime.attempt_facade import _public_result_projection
from cortex_runtime.mcp_api import private_lifecycle_response, project_public_response
from cortex_runtime.v11_responses import ResponseValidationError, validate_private_response

canonical_a = {
    "result_ref": "attempt-result-source",
    "status": "completed",
    "summary": "A" * 9000,
    "findings": [{"summary": "defect"}],
    "decisions_needed": [{"summary": "decision"}],
    "unresolved": [{"summary": "open"}],
    "claims": [{"summary": "claim"}],
    "changed_files": ["src/a.py"],
    "changed_files_status": "observed",
}
canonical_b = {**canonical_a, "result_ref": "attempt-result-corrective", "summary": "corrected"}
digest = lambda value: "sha256:" + engine.digest_text(engine.canonical_json.dumps(value))
source = {
    "attempt_id": "qa-01", "attempt_result_ref": canonical_a["result_ref"],
    "acceptance_evaluation": {
        "acceptance_status": "needs_rework", "failure_class": "technical",
        "reasons": ["verification_evidence_required"],
        "missing_verification_kinds": ["focused_tests", "diff_review"],
    },
    "native_terminal_stop": {"observed": True, "result_digest": engine.digest_text(canonical_a["result_ref"])},
    "selected_model": "gpt-5.6-luna", "selected_reasoning_effort": "high",
    "profile": "qa_engineer", "operation_kind": "verify",
    "worker_host_thread_id": "host-child", "spawn_request": {"task_name": "qa-child"},
    "logical_delegation_key": "qa-01-phase-0001", "plan_assignment_lineage_digest": "sha256:" + "a" * 64,
}
state = {"task_id": "task-probe", "attempts": [source], "reliability_recovery_receipts": []}
engine._ledger_root_for_artifact = lambda _path: None
engine.attempt_protocol.get_attempt_result = lambda *_args, **_kwargs: canonical_a
engine.ledger_db.list_worker_sessions = lambda *_args, **_kwargs: [{
    "attempt_id": "qa-01", "host_agent_id": "host-child", "host_task_name": "qa-child",
}]
target = engine._same_child_deficit_repair_target(None, state, source, {
    "kind": "verification_evidence_required",
})
assert target["stage"] == "same_child_deficit_repair"
assert target["model"] == "gpt-5.6-luna" and target["reasoning_effort"] == "high"
assert target["same_child_source_attempt_id"] == "qa-01"
source["same_child_deficit_repair_consumed"] = True
assert engine._same_child_deficit_repair_target(None, state, source, {}) is None
source["same_child_deficit_repair_consumed"] = False

corrective = {
    "attempt_id": "repair-02", "attempt_result_ref": canonical_b["result_ref"],
    "wave_ref": "wave-02", "phase_ref": "phase-0002",
    "logical_delegation_key": "implementation-01-phase-0002",
    "plan_assignment_lineage_digest": "sha256:" + "b" * 64,
}
state["attempts"].append(corrective)
route = {
    "status": "active", "source_result_ref": canonical_a["result_ref"],
    "source_result_digest": digest(canonical_a),
    "corrective_wave_ref": corrective["wave_ref"], "corrective_phase_ref": corrective["phase_ref"],
    "corrective_logical_delegation_key": corrective["logical_delegation_key"],
    "corrective_plan_assignment_lineage_digest": corrective["plan_assignment_lineage_digest"],
    "corrective_result_ref": canonical_b["result_ref"], "corrective_result_digest": digest(canonical_b),
    "verifier_wave_ref": "wave-03", "verifier_phase_ref": "phase-0003",
    "verifier_logical_delegation_key": "qa-01-phase-0003",
    "verifier_plan_assignment_lineage_digest": "sha256:" + "c" * 64,
}
state["product_rework_routes"] = {"route": route}
engine.attempt_protocol.get_attempt_result = lambda _root, *, attempt_id, **_kwargs: (
    canonical_a if attempt_id == "qa-01" else canonical_b
)
verifier = {
    "wave_ref": route["verifier_wave_ref"], "phase_ref": route["verifier_phase_ref"],
    "logical_delegation_key": route["verifier_logical_delegation_key"],
    "plan_assignment_lineage_digest": route["verifier_plan_assignment_lineage_digest"],
}
assert engine._product_rework_context_refs(None, state, verifier) == [
    canonical_a["result_ref"], canonical_b["result_ref"],
]
route["source_result_digest"] = "sha256:" + "0" * 64
try:
    engine._product_rework_context_refs(None, state, verifier)
except ValueError:
    pass
else:
    raise AssertionError("tampered product-rework source digest must fail closed")

view = _public_result_projection({
    "result": canonical_a,
    "events": [{"event_type": "verification_observation", "payload": {"kind": "focused_tests"}}],
    "server_evaluation": source["acceptance_evaluation"],
})
assert len(view["summary"]) > 8000 and view["findings"] and view["changed_files"]
assert view["verification_observations"] == [{"kind": "focused_tests"}]
assert view["server_evaluation"]["missing_verification_kinds"] == ["focused_tests", "diff_review"]

private_followup = private_lifecycle_response(
    {
        "ok": True,
        "state": "ready_to_spawn",
        "spawn_requests": [{
            "native_call": "followup_task",
            "followup_target": "qa-child",
            "dispatch_ref": "dispatch-" + "d" * 24,
            "message": "Read the exact recovery source and submit only the missing evidence.",
        }],
    },
    "task-" + "e" * 12,
    native_arguments=lambda _request: {},
    public_schema="cortex/orchestration/v11",
    coordinator_lock="server",
)
assert private_followup["outcome"] == "ready_to_spawn"
assert private_followup["action"] == {"kind": "invoke_dispatches"}
assert private_followup["dispatches"][0]["call"] == "followup_task"
public_followup = project_public_response(
    "continue_orchestration", private_followup, arguments={},
)
assert public_followup["action"] == "invoke_dispatches"
assert public_followup["dispatches"][0]["call"] == "followup_task"
assert public_followup["next_native_action"] == "wait_agent"
assert public_followup["read_worker_wave_allowed"] is False
continue_projection = project_public_response(
    "read_worker_result",
    {
        "ok": True,
        "action": "continue",
        "task_ref": "task-" + "e" * 12,
        "content": (
            "Call continue_orchestration now. Do not call wait_agent or read_worker_wave first."
        ),
        "complete": True,
        "state_mutated": False,
    },
    arguments={"action": "read_wave"},
)
assert continue_projection["ok"] is True and continue_projection["action"] == "continue"
assert "continue_orchestration" in continue_projection["content"]
assert continue_projection["complete"] is True
inspect_continue_projection = project_public_response(
    "manage_orchestration",
    {
        "ok": True, "outcome": "management_read", "action": {"kind": "continue"},
        "content": "Call continue_orchestration exactly once now.",
        "report": "current lifecycle", "complete": True,
    },
    arguments={"action": "inspect"},
)
assert inspect_continue_projection["action"] == "continue"
assert inspect_continue_projection["complete"] is True
inspect_nonfinal_projection = project_public_response(
    "manage_orchestration",
    {
        "ok": True, "outcome": "management_read", "report": "page",
        "complete": False, "next_cursor": "c11p." + "A" * 16,
    },
    arguments={"action": "inspect"},
)
assert inspect_nonfinal_projection["action"] == "read_more"
assert inspect_nonfinal_projection["complete"] is False
malformed_followup = {
    **private_followup,
    "dispatches": [{
        **private_followup["dispatches"][0],
        "arguments": {
            "target": "qa-child", "message": "repair", "task_name": "cross-call-field",
        },
    }],
}
try:
    validate_private_response("private.coordinator.lifecycle", malformed_followup)
except ResponseValidationError:
    pass
else:
    raise AssertionError("private followup dispatch accepted a spawn-only field")

response_source = __import__("inspect").getsource(cortex._v11_response)
assert response_source.index("validated_response = render_private_lifecycle_response") < response_source.index(
    'attempt["dispatch_delivery_status"] = "delivered"'
)
print("recovery-context-and-product-chain-pass")
''', str(scripts)],
            text=True, capture_output=True, check=True, env=runtime_env,
        )
        require(
            "recovery-context-and-product-chain-pass" in recovery_context_probe.stdout,
            "same-child deficit recovery and product rework carry exact paginated evidence authority",
        )
        long_objective = ("Unicode briefing payload Проверка 🚀 Ελληνικά 日本語 العربية — " * 260).strip()
        initial_worker_model = "gpt-5.6-luna"
        initial_close_model = "gpt-5.6-luna"
        start = tool("start_orchestration", {"user_request": "Unicode smoke: Проверка 🚀", "governance_mode": "required", "complexity": "C3", "waves": [{"phase_kind": "implementation", "workers": [{"objective": long_objective, "profile": "backend_dev", "model": initial_worker_model, "reasoning_effort": "high", "operation_kind": "modify"}]}, {"phase_kind": "governance_close", "workers": [{"objective": "Close governance after evidence", "profile": "code_reviewer", "model": initial_close_model, "reasoning_effort": "medium", "operation_kind": "close"}]}]}, root_thread)
        require(
            start.get("ok") is True
            and start.get("action") == "invoke_dispatches"
            and isinstance(start.get("dispatches"), list)
            and start.get("dispatches"),
            "valid start returns native dispatch lifecycle",
        )
        dispatches = start.get("dispatches")
        require(isinstance(dispatches, list) and len(dispatches) == 1, "exact first dispatch")
        dispatch = dispatches[0] if isinstance(dispatches[0], dict) else {}
        dispatch_ref = str(dispatch.get("dispatch_ref") or "")
        require(dispatch_ref.startswith("dispatch-"), "server dispatch identity")
        require(
            dispatch.get("arguments", {}).get("reasoning_effort") == "high"
            and "model" not in dispatch.get("arguments", {}),
            "Luna/high is accepted and native Luna omits the model override",
        )
        worker_thread = "worker-release-thread-1"
        common = {"cwd": str(project_root), "permission_mode": "default", "session_id": root_thread, "transcript_path": "/tmp/release-gate-transcript", "agent_id": worker_thread, "agent_type": "explorer"}
        start_event = {**common, "model": initial_worker_model, "hook_event_name": "SubagentStart", "turn_id": "worker-turn-1"}
        # Source-mode hook contract simulation; native spawn_agent/wait_agent
        # behavior is proven separately by the live host gate.
        subprocess.run([sys.executable, str(scripts / "cortex_hook.py")], input=json.dumps(start_event), text=True, capture_output=True, check=True, env=runtime_env)
        briefing = tool("read_dispatch_briefing", {"dispatch_ref": dispatch_ref}, worker_thread)
        require(briefing.get("ok") is True, "worker reads canonical briefing")
        # Read every briefing page while the attempt is still active.  The
        # worker may acknowledge the briefing only after the final page.
        briefing_pages = [briefing]
        briefing_cursor = briefing.get("next_cursor")
        while briefing_cursor:
            next_page = tool("read_dispatch_briefing", {"dispatch_ref": dispatch_ref, "cursor": briefing_cursor}, worker_thread)
            require(next_page.get("ok") is True, "briefing continuation succeeds")
            briefing_pages.append(next_page)
            briefing_cursor = next_page.get("next_cursor")
        require(len(briefing_pages) > 1, "briefing is genuinely multi-page")
        briefing_text = "".join(
            __import__("base64").b64decode(page["content"]).decode("utf-8")
            if page.get("encoding") == "base64" else str(page.get("content", ""))
            for page in briefing_pages
        )
        require(len(briefing_text) > 10_000 and "Проверка" in briefing_text and "🚀" in briefing_text, "multi-page Unicode briefing reconstructs exactly")
        require(re.fullmatch(r"[0-9a-f]{64}", __import__("hashlib").sha256(briefing_text.encode("utf-8")).hexdigest()), "reconstructed briefing digest is canonical")
        require(tool("read_dispatch_briefing", {"dispatch_ref": dispatch_ref, "cursor": "c11p.invalid"}, worker_thread).get("ok") is False, "invalid briefing cursor is rejected")
        # This is a mutating assignment, so exercise the production writer
        # acceptance path with one real isolated-project change after the
        # occurrence baseline was captured. Runtime/host state lives outside
        # project_root and must never masquerade as the worker's mutation.
        (project_root / "writer-occurrence-output.txt").write_text(
            "Occurrence-bound Unicode writer output: Проверка 🚀\n",
            encoding="utf-8",
        )
        # Assert the backend joined the worker MCP metadata to the native
        # Start event. Only booleans are asserted; identifiers never leave the
        # private check.
        import sqlite3
        ledger_candidates = list((host_state / "projects").glob("*/cortex.db"))
        require(len(ledger_candidates) == 1, "exact private host ledger exists after start")
        ledger_db = ledger_candidates[0]
        with sqlite3.connect(ledger_db) as connection:
            row = connection.execute("SELECT state_json FROM tasks LIMIT 1").fetchone()
            state_snapshot = json.loads(row[0]) if row else {}
        attempts_snapshot = state_snapshot.get("attempts", []) if isinstance(state_snapshot, dict) else []
        require(any(isinstance(item, dict) and item.get("worker_host_thread_id") == worker_thread for item in attempts_snapshot), "native Start and worker _meta are joined by backend")
        invalid = tool("submit_attempt", {"dispatch_ref": dispatch_ref, "status": "completed", "report": " "}, worker_thread)
        require(
            invalid.get("ok") is False
            and invalid.get("action") == "repair_patch_only"
            and invalid.get("retryable") is True
            and invalid.get("state_mutated") is False,
            "invalid completion returns flat patch-only repair",
        )
        repair_changes = invalid.get("repair_changes") if isinstance(invalid.get("repair_changes"), list) else []
        require(invalid.get("repair_capsule") and invalid.get("base_payload_digest") and repair_changes, "flat completion repair fields are complete")
        repair_change = repair_changes[0] if isinstance(repair_changes[0], dict) else {}
        repair_patch = {"op": repair_change.get("op"), "path": repair_change.get("path")}
        if repair_patch["op"] != "remove":
            repair_patch["value"] = "Unicode worker completed"
        repaired = tool("repair_attempt", {"dispatch_ref": dispatch_ref, "repair_capsule": invalid["repair_capsule"], "base_payload_digest": invalid["base_payload_digest"], "patches": [repair_patch]}, worker_thread)
        require(repaired.get("ok") is True and repaired.get("terminal") is True, "exact opaque repair completes the attempt")
        before_stop = tool("read_worker_wave", {"task_ref": start["task_ref"], "coordinator_ref": start["coordinator_ref"]}, root_thread)
        require(
            before_stop.get("ok") is True
            and before_stop.get("action") == "wait_for_bound_workers"
            and before_stop.get("outcome") == "waiting_workers"
            and before_stop.get("state_mutated") is False,
            "premature result read gracefully returns a nonmutating wait lifecycle success",
        )
        with sqlite3.connect(ledger_db) as connection:
            state_before_nonterminal_replay = str(connection.execute("SELECT state_json FROM tasks LIMIT 1").fetchone()[0])
        after_nonterminal_wait = tool("read_worker_wave", {"task_ref": start["task_ref"], "coordinator_ref": start["coordinator_ref"]}, root_thread)
        with sqlite3.connect(ledger_db) as connection:
            state_after_nonterminal_replay = str(connection.execute("SELECT state_json FROM tasks LIMIT 1").fetchone()[0])
        require(after_nonterminal_wait == before_stop and state_after_nonterminal_replay == state_before_nonterminal_replay, "before-wait and after-nonterminal-wait reads repeat the same zero-mutation wait directive")
        stop_event = {**common, "model": initial_worker_model, "hook_event_name": "SubagentStop", "turn_id": "worker-turn-stop", "agent_transcript_path": None, "last_assistant_message": None, "stop_hook_active": False}
        subprocess.run([sys.executable, str(scripts / "cortex_hook.py")], input=json.dumps(stop_event), text=True, capture_output=True, check=True, env=runtime_env)
        after_stop = tool("read_worker_wave", {"task_ref": start["task_ref"], "coordinator_ref": start["coordinator_ref"]}, root_thread)
        require(after_stop.get("ok") is True and after_stop.get("action") == "revise_or_continue", "wave read advances after terminal Stop without replacement")
        result_refs = after_stop.get("result_refs") if isinstance(after_stop.get("result_refs"), list) else []
        require(result_refs and all(str(item).startswith("attempt-result-") for item in result_refs), "canonical result refs are returned after terminal Stop")
        replayed_wave = tool("read_worker_wave", {"task_ref": start["task_ref"], "coordinator_ref": start["coordinator_ref"]}, root_thread)
        require(
            replayed_wave.get("ok") is True
            and replayed_wave.get("action") == "revise_or_continue"
            and replayed_wave.get("result_refs") == result_refs
            and not replayed_wave.get("dispatches"),
            "terminal wave read is idempotent and never creates a replacement attempt",
        )

        # The first completed wave may change only future work.  Replay the
        # exact decision and prove a stale revision is rejected.
        activation_model = "gpt-5.6-terra"
        revised_close_model = "gpt-5.6-terra"
        revision_args = {"task_ref": start["task_ref"], "coordinator_ref": start["coordinator_ref"], "evidence_result_refs": result_refs, "waves": [{"phase_kind": "governance_activation", "workers": [{"objective": "Activate governance from first-wave evidence", "profile": "code_reviewer", "model": activation_model, "reasoning_effort": "high", "operation_kind": "verify"}]}, {"phase_kind": "governance_close", "workers": [{"objective": "Close governance after activation", "profile": "code_reviewer", "model": revised_close_model, "reasoning_effort": "high", "operation_kind": "close"}]}], "reason": "First-wave evidence requires governance activation before close."}
        revised = tool("revise_future_pipeline", revision_args, root_thread)
        require(revised.get("ok") is True, "pending future wave revision succeeds")
        replay = tool("revise_future_pipeline", revision_args, root_thread)
        require(replay.get("ok") is True, "future revision replay is idempotent")
        stale = tool("revise_future_pipeline", {**revision_args, "waves": [{"phase_kind": "governance_close", "workers": [{"objective": "Conflicting stale revision", "profile": "code_reviewer", "model": "gpt-5.6-luna", "reasoning_effort": "medium", "operation_kind": "close"}]}], "reason": "stale conflict"}, root_thread)
        require(stale.get("ok") is False, "stale future revision is rejected")

        # Revision is a state mutation only; the next native dispatch is
        # returned by the lifecycle continuation of the revised frontier.
        continued_to_activation = tool("continue_orchestration", {"task_ref": start["task_ref"], "coordinator_ref": start["coordinator_ref"]}, root_thread)
        require(continued_to_activation.get("ok") is True, "continuation enters the revised future wave")
        next_dispatches = continued_to_activation.get("dispatches") if isinstance(continued_to_activation.get("dispatches"), list) else []
        require(next_dispatches, "revised pipeline returns a native dispatch")
        next_arguments = next_dispatches[0].get("arguments", {}) if isinstance(next_dispatches[0], dict) else {}
        require(
            next_arguments.get("model") == activation_model
            and next_arguments.get("reasoning_effort") == "high",
            "explicit coordinator-selected Terra/high remains exact in native dispatch",
        )
        second_ref = str(next_dispatches[0].get("dispatch_ref") or "") if isinstance(next_dispatches[0], dict) else ""
        second_thread = "worker-release-thread-2"
        common2 = {**common, "agent_id": second_thread, "agent_type": "general"}
        subprocess.run([sys.executable, str(scripts / "cortex_hook.py")], input=json.dumps({**common2, "model": activation_model, "hook_event_name": "SubagentStart", "turn_id": "worker-turn-2"}), text=True, capture_output=True, check=True, env=runtime_env)
        read_briefing_predecessors(second_ref, second_thread, "second worker")
        require(tool("submit_attempt", {"dispatch_ref": second_ref, "status": "completed", "report": "Governance activation recorded."}, second_thread).get("ok") is True, "second worker reaches terminal completion")
        subprocess.run([sys.executable, str(scripts / "cortex_hook.py")], input=json.dumps({**common2, "model": activation_model, "hook_event_name": "SubagentStop", "turn_id": "worker-turn-2-stop", "agent_transcript_path": None, "last_assistant_message": None, "stop_hook_active": False}), text=True, capture_output=True, check=True, env=runtime_env)

        activation_wave = tool("read_worker_wave", {"task_ref": start["task_ref"], "coordinator_ref": start["coordinator_ref"]}, root_thread)
        require(activation_wave.get("ok") is True, "governance activation wave is readable")
        activation_results = activation_wave.get("result_refs") if isinstance(activation_wave.get("result_refs"), list) else []
        require(activation_results, "governance activation canonical result exists")
        continued_activation = tool("continue_orchestration", {"task_ref": start["task_ref"], "coordinator_ref": start["coordinator_ref"]}, root_thread)
        require(continued_activation.get("ok") is True, "governance activation continuation succeeds")
        close_dispatches = continued_activation.get("dispatches") if isinstance(continued_activation.get("dispatches"), list) else []
        require(close_dispatches, "governance close is dispatched by lifecycle continuation")
        close_ref = str(close_dispatches[0].get("dispatch_ref") or "") if isinstance(close_dispatches[0], dict) else ""
        close_thread = "worker-governance-close"
        close_common = {**common, "agent_id": close_thread, "agent_type": "general"}
        subprocess.run([sys.executable, str(scripts / "cortex_hook.py")], input=json.dumps({**close_common, "model": revised_close_model, "hook_event_name": "SubagentStart", "turn_id": "close-turn-1"}), text=True, capture_output=True, check=True, env=runtime_env)
        close_briefing_preview = read_all_text_pages(
            "read_dispatch_briefing", {"dispatch_ref": close_ref}, close_thread,
            "governance close worker briefing",
        )
        close_catalog_text = read_all_text_pages(
            "list_worker_reports", {"dispatch_ref": close_ref}, close_thread,
            "governance close report catalog",
        )
        close_catalog = [
            json.loads(line) for line in close_catalog_text.splitlines() if line.strip()
        ]
        require(
            any(item.get("required") is True for item in close_catalog)
            and any(item.get("required") is False for item in close_catalog),
            "report catalog separates the minimal required frontier from optional history",
        )
        optional_close_report = next(
            str(item.get("report_ref") or "") for item in close_catalog
            if item.get("required") is False
        )
        optional_close_text = read_all_text_pages(
            "read_predecessor_result",
            {"dispatch_ref": close_ref, "report_ref": optional_close_report},
            close_thread,
            "optional historical report",
        )
        require(optional_close_text, "worker may select one optional historical report")
        unread_predecessors = tool("submit_governance_closure", {"dispatch_ref": close_ref, "closure_outcome": "verified", "blocking_gaps_text": "", "report": "Governance close verified."}, close_thread)
        require(
            unread_predecessors.get("ok") is False
            and unread_predecessors.get("action") == "read_required_context_then_retry"
            and unread_predecessors.get("error_code") == "attempt_read_receipts_incomplete"
            and unread_predecessors.get("retryable") is True
            and unread_predecessors.get("state_mutated") is False,
            "missing predecessor receipts return executable cross-tool recovery",
        )
        close_briefing_text = read_briefing_predecessors(close_ref, close_thread, "governance close worker")
        require(close_briefing_text == close_briefing_preview, "governance close briefing replay is exact")
        require("task_revision" not in close_briefing_text, "governance-close briefing exposes no task_revision")

        # The closure authority is server-derived from the exact active plan
        # receipt.  Assert that the immutable receipt and the task's current
        # plan are byte-semantically equal before trusting the bound basis.
        with sqlite3.connect(ledger_db) as connection:
            task_rows = connection.execute(
                "SELECT definition_json, state_json, plan_json FROM tasks ORDER BY created_at"
            ).fetchall()
            require(len(task_rows) == 1, "exact governance task remains durable")
            task_row = task_rows[0]
            definition_snapshot = json.loads(task_row[0])
            close_state_snapshot = json.loads(task_row[1])
            current_plan = json.loads(task_row[2])
            receipt_row = connection.execute(
                "SELECT plan_revision, plan_json FROM plan_revisions WHERE task_id = ? AND status = 'active'",
                (str(close_state_snapshot["task_id"]),),
            ).fetchone()
        require(receipt_row is not None, "exact active plan receipt exists")
        receipt_plan = json.loads(receipt_row[1])
        require(receipt_plan == current_plan, "active plan receipt equals the exact current plan")
        canonical_plan_digest = "sha256:" + __import__("hashlib").sha256(
            json.dumps(current_plan, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
        ).hexdigest()
        definition_governance = definition_snapshot.get("governance") if isinstance(definition_snapshot, dict) else {}
        state_governance = close_state_snapshot.get("governance") if isinstance(close_state_snapshot, dict) else {}
        require(definition_governance.get("effective_mode") == "full", "task definition uses the effective governance mode")
        require(state_governance.get("effective_mode") == "full", "task state uses the effective governance mode")
        close_attempt = next(
            item for item in close_state_snapshot.get("attempts", [])
            if isinstance(item, dict) and item.get("dispatch_ref") == close_ref
        )
        closure_basis = close_attempt.get("governance_closure_basis") if isinstance(close_attempt, dict) else {}
        require(not contains_key(closure_basis, "task_revision"), "governance-close basis exposes no task_revision")
        contract_candidates = list((host_state / "projects").glob(
            f"*/tasks/*/task-contract/{close_attempt.get('attempt_id')}.json"
        ))
        require(len(contract_candidates) == 1, "exact governance-close task contract exists")
        close_task_contract = json.loads(contract_candidates[0].read_text(encoding="utf-8"))
        require(not contains_key(close_task_contract, "task_revision"), "governance-close task contract exposes no task_revision")
        require(
            isinstance(closure_basis, dict)
            and closure_basis.get("complete") is True
            and closure_basis.get("effective_mode") == "full",
            "closure authority is complete and uses the effective mode",
        )
        require(
            int(closure_basis.get("plan_revision") or -1) == int(receipt_row[0])
            and closure_basis.get("plan_digest") == canonical_plan_digest,
            "closure authority is bound to the exact current-plan receipt",
        )

        # General completion is fail-closed for this dispatch and must not
        # consume or mutate the dedicated closure authority.
        revision_before_wrong_tool = int(close_state_snapshot.get("revision") or 0)
        wrong_close_tool = tool("submit_attempt", {"dispatch_ref": close_ref, "status": "completed", "report": "Governance close verified."}, close_thread)
        require(
            wrong_close_tool.get("ok") is False
            and wrong_close_tool.get("error_code") == "governance_closure_tool_required"
            and wrong_close_tool.get("retryable") is True
            and wrong_close_tool.get("state_mutated") is False,
            "ordinary completion fails closed for governance closure",
        )
        with sqlite3.connect(ledger_db) as connection:
            unchanged_row = connection.execute(
                "SELECT state_json FROM tasks WHERE task_id = ?", (str(close_state_snapshot["task_id"]),)
            ).fetchone()
        require(unchanged_row is not None and int(json.loads(unchanged_row[0]).get("revision") or 0) == revision_before_wrong_tool, "wrong closure tool leaves durable state unchanged")
        closed = tool("submit_governance_closure", {"dispatch_ref": close_ref, "closure_outcome": "verified", "blocking_gaps_text": "", "report": "Governance close verified."}, close_thread)
        require(closed.get("ok") is True and closed.get("terminal") is True, "dedicated governance closure completes")
        with sqlite3.connect(ledger_db) as connection:
            result_row = connection.execute(
                "SELECT metadata_json FROM attempt_results WHERE attempt_id = ?",
                (str(close_attempt.get("attempt_id") or ""),),
            ).fetchone()
        require(result_row is not None, "governance-close canonical result metadata exists")
        close_result_metadata = json.loads(result_row[0])
        require(not contains_key(close_result_metadata, "task_revision"), "governance-close canonical metadata exposes no task_revision")
        # Directly exercise the production private-revision validator against
        # this real isolated close result. Canonical/public metadata omits the
        # revision; only the exact private occurrence and immutable ledger
        # receipts can authorize it.
        if str(scripts) not in sys.path:
            sys.path.insert(0, str(scripts))
        from cortex_runtime import ledger_db as production_ledger_db

        production_ledger_db.validate_close_assignment_revision_authority(
            ledger_db.parent,
            task_id=str(close_state_snapshot["task_id"]),
            attempt=close_attempt,
            result_metadata=close_result_metadata,
        )
        import copy
        stale_close_attempt = copy.deepcopy(close_attempt)
        stale_close_attempt["assignment_task_revision"] = int(
            stale_close_attempt["assignment_task_revision"]
        ) + 1
        wrong_close_attempt = copy.deepcopy(close_attempt)
        wrong_close_attempt["attempt_id"] = str(wrong_close_attempt["attempt_id"]) + "-wrong"
        wrong_plan_attempt = copy.deepcopy(close_attempt)
        wrong_plan_attempt["plan_revision"] = int(wrong_plan_attempt["plan_revision"]) + 1
        for invalid_attempt, label in (
            (stale_close_attempt, "stale private close revision"),
            (wrong_close_attempt, "wrong close attempt identity"),
            (wrong_plan_attempt, "wrong close plan authority"),
        ):
            try:
                production_ledger_db.validate_close_assignment_revision_authority(
                    ledger_db.parent,
                    task_id=str(close_state_snapshot["task_id"]),
                    attempt=invalid_attempt,
                    result_metadata=close_result_metadata,
                )
            except ValueError:
                pass
            else:
                raise AssertionError(label + " must fail closed")
        close_before_stop = tool("read_worker_wave", {"task_ref": start["task_ref"], "coordinator_ref": start["coordinator_ref"]}, root_thread)
        require(
            close_before_stop.get("ok") is True
            and close_before_stop.get("action") == "wait_for_bound_workers",
            "handoff remains fail-closed until governance native Stop",
        )
        subprocess.run([sys.executable, str(scripts / "cortex_hook.py")], input=json.dumps({**close_common, "model": revised_close_model, "hook_event_name": "SubagentStop", "turn_id": "close-turn-stop", "agent_transcript_path": None, "last_assistant_message": None, "stop_hook_active": False}), text=True, capture_output=True, check=True, env=runtime_env)
        close_wave = tool("read_worker_wave", {"task_ref": start["task_ref"], "coordinator_ref": start["coordinator_ref"]}, root_thread)
        require(close_wave.get("ok") is True, "governance close wave is readable")
        close_results = close_wave.get("result_refs") if isinstance(close_wave.get("result_refs"), list) else []
        require(close_results, "governance close canonical result exists")
        final_lifecycle = tool("continue_orchestration", {"task_ref": start["task_ref"], "coordinator_ref": start["coordinator_ref"]}, root_thread)
        final_report = final_lifecycle.get("report")
        handoff_receipt = json.loads(final_report) if isinstance(final_report, str) and final_report else {}
        require(
            final_lifecycle.get("ok") is True
            and final_lifecycle.get("action") == "deliver_handoff"
            and handoff_receipt.get("close_verified") is True,
            "terminal lifecycle delivers only a close-verified governance handoff",
        )
        with sqlite3.connect(ledger_db) as connection:
            final_state_row = connection.execute(
                "SELECT state_json FROM tasks WHERE task_id = ?", (str(close_state_snapshot["task_id"]),)
            ).fetchone()
        final_state = json.loads(final_state_row[0]) if final_state_row else {}
        require(final_state.get("close_verified") is True and final_state.get("handoff_created") is True, "durable handoff is bound to verified closure authority")

        # Durable question flow: arbitrary Unicode text, incomplete Stop,
        # answer, same-child resumed Start/new turn, poll, completion, Stop.
        question_worker_model = "gpt-5.6-luna"
        question_start = tool("start_orchestration", {"user_request": "Unicode question flow 🚀", "waves": [{"phase_kind": "implementation", "workers": [{"objective": "Ask one durable question", "profile": "general", "model": question_worker_model, "reasoning_effort": "medium", "operation_kind": "modify"}]}]}, root_thread)
        qdispatches = question_start.get("dispatches") if isinstance(question_start.get("dispatches"), list) else []
        require(qdispatches, "question scenario dispatches a worker")
        qref = str(qdispatches[0].get("dispatch_ref") or "") if isinstance(qdispatches[0], dict) else ""
        qthread = "worker-question-thread"
        qcommon = {**common, "agent_id": qthread, "agent_type": "general"}
        subprocess.run([sys.executable, str(scripts / "cortex_hook.py")], input=json.dumps({**qcommon, "model": question_worker_model, "hook_event_name": "SubagentStart", "turn_id": "question-turn-1"}), text=True, capture_output=True, check=True, env=runtime_env)
        require(tool("read_dispatch_briefing", {"dispatch_ref": qref}, qthread).get("ok") is True, "question worker reads briefing")
        internal_question = tool("ask_worker_question", {"dispatch_ref": qref, "question_category": "runtime_recovery", "question_text": "Повторить внутреннее восстановление Cortex?"}, qthread)
        require(
            internal_question.get("ok") is False
            and internal_question.get("action") == "server_recovery"
            and internal_question.get("error_code") == "internal_worker_question_forbidden"
            and internal_question.get("retryable") is False
            and internal_question.get("state_mutated") is False,
            "internal recovery cannot be recorded as a user question and returns executable recovery",
        )
        with sqlite3.connect(ledger_db) as connection:
            internal_count = connection.execute(
                "SELECT COUNT(*) FROM durable_questions WHERE dispatch_ref = ?", (qref,)
            ).fetchone()[0]
        require(internal_count == 0, "rejected internal question is nonmutating")
        question = tool("ask_worker_question", {"dispatch_ref": qref, "question_category": "product", "question_text": "Выберите режим 🚀 — допустим любой язык и любой Unicode ответ."}, qthread)
        require(question.get("ok") is True and question.get("question_ref"), "arbitrary Unicode question is recorded")
        subprocess.run([sys.executable, str(scripts / "cortex_hook.py")], input=json.dumps({**qcommon, "model": question_worker_model, "hook_event_name": "SubagentStop", "turn_id": "question-stop", "agent_transcript_path": None, "last_assistant_message": None, "stop_hook_active": False}), text=True, capture_output=True, check=True, env=runtime_env)
        answered = tool("answer_orchestration_question", {"task_ref": question_start["task_ref"], "coordinator_ref": question_start["coordinator_ref"], "question_ref": question["question_ref"], "answer": "Ответ 🚀 принят: безопасный режим."}, root_thread)
        resume_dispatches = answered.get("dispatches") if isinstance(answered.get("dispatches"), list) else []
        resume_dispatch = resume_dispatches[0] if len(resume_dispatches) == 1 and isinstance(resume_dispatches[0], dict) else {}
        resume_arguments = resume_dispatch.get("arguments") if isinstance(resume_dispatch.get("arguments"), dict) else {}
        original_task_name = str(qdispatches[0].get("arguments", {}).get("task_name") or "")
        require(
            answered.get("ok") is True
            and answered.get("action") == "resume_bound_worker"
            and resume_dispatch.get("call") == "followup_task"
            and resume_arguments.get("target") == original_task_name
            and resume_arguments.get("target") != qthread
            and set(resume_arguments) == {"target", "message"},
            "plain Unicode answer returns one safe exact same-child followup_task",
        )
        subprocess.run([sys.executable, str(scripts / "cortex_hook.py")], input=json.dumps({**qcommon, "model": question_worker_model, "hook_event_name": "SubagentStart", "turn_id": "question-turn-2"}), text=True, capture_output=True, check=True, env=runtime_env)
        polled = tool("poll_worker_question", {"dispatch_ref": qref, "question_ref": question["question_ref"]}, qthread)
        require(polled.get("ok") is True and polled.get("content") == "Ответ 🚀 принят: безопасный режим.", "same-child resumed turn polls the exact free-text answer")
        with sqlite3.connect(ledger_db) as connection:
            connection.execute(
                """INSERT INTO durable_questions(
                       question_ref,task_id,attempt_id,dispatch_ref,profile,task_revision,
                       attempt_generation,submission_id,question_category,question_text,status,
                       content_digest,published_sequence,answer,answer_submission_id,answer_digest,
                       answered_sequence,created_at,answered_at,superseded_at)
                   SELECT 'question-injected-internal',task_id,attempt_id,dispatch_ref,profile,
                          task_revision,attempt_generation,'injected-internal',NULL,
                          'internal runtime recovery', 'open',content_digest,published_sequence + 100,
                          NULL,NULL,NULL,NULL,created_at,NULL,NULL
                     FROM durable_questions WHERE question_ref = ?""",
                (question["question_ref"],),
            )
            connection.commit()
        require(tool("submit_attempt", {"dispatch_ref": qref, "status": "completed", "report": "Question answered."}, qthread).get("ok") is True, "question worker completes")
        subprocess.run([sys.executable, str(scripts / "cortex_hook.py")], input=json.dumps({**qcommon, "model": question_worker_model, "hook_event_name": "SubagentStop", "turn_id": "question-stop-2", "agent_transcript_path": None, "last_assistant_message": None, "stop_hook_active": False}), text=True, capture_output=True, check=True, env=runtime_env)
        question_wave = tool("read_worker_wave", {"task_ref": question_start["task_ref"], "coordinator_ref": question_start["coordinator_ref"]}, root_thread)
        require(question_wave.get("ok") is True, "legacy or injected internal question cannot authorize a user stop")

        # Reopen the durable SQLite ledger and assert the active schema is
        # present without the retired question-batch storage.
        require(ledger_db.is_file(), "durable private host ledger remains available")
        with sqlite3.connect(ledger_db) as connection:
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            require("schema_migrations" in tables and "tasks" in tables, "current database tables persist")
            require("obsolete_records" not in tables, "obsolete question storage is absent")

        try:
            process.stdin.close()
        finally:
            if process.poll() is None:
                process.terminate()
            process.wait(timeout=5)

    # Exercise the actual hook decoder shapes: Start binds a worker thread to
    # the coordinator session; Stop is the only terminal completion authority.
    hook_script = scripts / "cortex_hook.py"
    hook_env = os.environ.copy()
    common = {
        "cwd": str(repository), "model": "gpt-5.6-terra", "permission_mode": "default",
        "session_id": "root-session", "transcript_path": "/tmp/transcript",
    }
    start = {**common, "hook_event_name": "SubagentStart", "agent_id": "worker-thread", "agent_type": "general", "turn_id": "turn-1"}
    stop = {**common, "hook_event_name": "SubagentStop", "agent_id": "worker-thread", "agent_type": "general", "turn_id": "turn-2", "agent_transcript_path": None, "last_assistant_message": None, "stop_hook_active": False}
    for event in (start, stop):
        result = subprocess.run([sys.executable, str(hook_script)], input=json.dumps(event), text=True, capture_output=True, check=True, env=hook_env)
        require(result.stderr == "", "hook emits no stderr")
        json.loads(result.stdout)

    # Ensure question payloads remain arbitrary Unicode plain text and do not
    # reintroduce the retired answer field or language blockers.
    require('"answer"' in question_text, "canonical question answer field is present")
    require("English-only" not in question_text and "English only" not in question_text, "questions are language-neutral")

    # Release gate itself must remain the sole test module/case.
    test_files = sorted((source_repository / "tests").glob("test_*.py"))
    require(test_files == [source_repository / "tests/test_marketplace_release_gate.py"], "only marketplace release gate remains")
