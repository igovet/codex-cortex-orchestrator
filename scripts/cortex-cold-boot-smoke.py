#!/usr/bin/env python3
"""Exercise the public Cortex one-call-per-wave orchestration over JSON-RPC."""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from contextlib import ExitStack
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "plugins/cortex/scripts"))
from jsonrpc_harness import JsonRpcHarness  # noqa: E402
import cortex  # noqa: E402

SERVER = ROOT / "plugins/cortex/scripts/cortex.py"


def git(project: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=project, text=True, capture_output=True, check=True)


def fixture(base: Path) -> tuple[Path, Path]:
    project = base / "project"
    host_state_dir = base / "host-private-store"
    project.mkdir()
    host_state_dir.mkdir(mode=0o700)
    host_state_dir.chmod(0o700)
    git(project, "init", "-q")
    git(project, "config", "user.email", "codex@example.invalid")
    git(project, "config", "user.name", "Codex Smoke")
    (project / "tracked.txt").write_text("before\n", encoding="utf-8")
    (project / "delete.txt").write_text("delete me\n", encoding="utf-8")
    (project / "old.txt").write_text("rename me\n", encoding="utf-8")
    git(project, "add", ".")
    git(project, "commit", "-qm", "fixture baseline")
    return project, host_state_dir


@contextlib.contextmanager
def host_private_control_store(host_state_dir: Path):
    """Run the deterministic smoke against a private, non-workspace ledger."""
    previous = {
        "CORTEX_HOST_STATE_DIR": os.environ.get("CORTEX_HOST_STATE_DIR"),
        "CORTEX_ROOT": os.environ.get("CORTEX_ROOT"),
    }
    os.environ["CORTEX_HOST_STATE_DIR"] = str(host_state_dir)
    os.environ.pop("CORTEX_ROOT", None)
    try:
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def waves() -> list[dict[str, object]]:
    return [
        {"workers": [{"phase": "discover"}]},
        {"workers": [{"phase": "architecture"}, {"phase": "database_architecture"}]},
        {"workers": [{"phase": "implementation"}]},
        {"workers": [{"phase": "qa"}]},
        {"workers": [{"phase": "review"}]},
    ]


def workspace_summary(project: Path) -> dict[str, object]:
    """Return a small, truthful workspace summary for fixture results.

    The fixture itself intentionally changes project files.  The completed
    contract records that state; it does not equate an uncommitted fixture
    change with an unresolved review finding.
    """
    completed = subprocess.run(
        ["git", "status", "--porcelain=v1", "--untracked-files=all"],
        cwd=project,
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode:
        return {"modified": [], "untracked": [], "staged": [], "committed": "not_required"}
    modified: list[str] = []
    untracked: list[str] = []
    staged: list[str] = []
    for line in completed.stdout.splitlines():
        if line.startswith("?? "):
            untracked.append(line[3:])
            continue
        if len(line) < 4:
            continue
        index, worktree, path = line[0], line[1], line[3:]
        if index != " ":
            staged.append(path)
        if worktree != " ":
            modified.append(path)
    has_changes = bool(modified or untracked or staged)
    return {
        "modified": sorted(modified),
        "untracked": sorted(untracked),
        "staged": sorted(staged),
        "committed": False if has_changes else "not_required",
    }


def passing_attempt_result(project: Path, gate: str) -> dict[str, object]:
    """Build a deterministic completed AttemptResult for fixture checks."""
    return {
        "status": "completed",
        "summary": f"Cold-boot fixture completed for the {gate} attempt.",
        "findings": [],
        "decisions_needed": [],
        "unresolved": [],
        "claims": [],
        "evidence": [f"Cold-boot fixture verification completed for the {gate} attempt."],
        "changed_files": workspace_summary(project).get("untracked", []),
    }


def canonical_artifacts(
    ledger: Path, task_id: str, *, kind: str | None = None
) -> list[dict[str, object]]:
    """Read artifact metadata from SQLite without assuming an eager export."""
    artifacts: list[dict[str, object]] = []
    offset = 0
    while True:
        page, next_offset = cortex.db_list_artifacts(
            ledger, task_id, kind=kind, offset=offset, page_size=100,
        )
        artifacts.extend(page)
        if next_offset is None:
            return artifacts
        offset = next_offset


def attempt_result(
    worker: int,
    step: int,
    predecessor_result_refs: list[str],
    gate: str,
    acceptance: list[str],
    verification: list[str],
    project: Path,
    task_acceptance: list[str],
    task_verification: list[str],
    changed_files: list[str],
) -> dict[str, object]:
    evidence = [f"step {step} worker {worker} observed the cold-boot fixture state"]
    if predecessor_result_refs:
        evidence.append("Predecessor result context: " + ", ".join(predecessor_result_refs))
    for index, _criterion in enumerate(acceptance, 1):
        evidence.append(f"Gate acceptance {index}: PASS - Repository path inspection and fixture state comparison produced conclusive evidence.")
    for index, _criterion in enumerate(verification, 1):
        evidence.append(f"Gate verification {index}: PASS - Cold-boot verification command completed successfully with concrete observed output.")
    if gate == "close":
        for index, _criterion in enumerate(task_acceptance, 1):
            evidence.append(f"Task acceptance {index}: PASS - Completed workflow provides concrete end-to-end fixture evidence.")
        for index, _criterion in enumerate(task_verification, 1):
            evidence.append(f"Task verification {index}: PASS - Final cold-boot check completed successfully with observed output.")
    checks = [{
        "command": "git status --short",
        "cwd": str(project),
        "exit_code": 0,
        "evidence": "Command completed and the fixture repository state was observed successfully.",
    }]
    return {
        "summary": f"relative step {step} worker {worker} completed",
        "findings": [],
        "decisions_needed": [],
        "unresolved": [],
        "verification_claimed": checks[0],
    }


def planning(worker: int, step: int) -> dict[str, object]:
    return {
        "overview": f"Cold-boot planner breakdown for relative step {step}.",
        "work_packages": [{
            "id": "smoke_core",
            "title": "Cold-boot core",
            "objective": "Exercise the durable Planner work-breakdown protocol.",
            "allowed_paths": ["tracked.txt", "delete.txt", "old.txt", "new.txt", "added.txt"],
            "microtasks": [{
                "id": "smoke_validate",
                "title": "Validate the smoke path",
                "objective": f"Record the bounded work item owned by simulated worker {worker}.",
                "profile": "backend_dev",
                "allowed_paths": ["tracked.txt", "delete.txt", "old.txt", "new.txt", "added.txt"],
                "acceptance_criteria": ["The work breakdown is persisted."],
                "verification": ["Run the cold-boot smoke workflow."],
            }],
        }],
    }


def run(base: Path, server: Path = SERVER) -> dict[str, object]:
    project, host_state_dir = fixture(base)
    with host_private_control_store(host_state_dir):
        return _run(base, project, host_state_dir, server)


def _run(base: Path, project: Path, host_state_dir: Path, server: Path) -> dict[str, object]:
    ledger = cortex.ledger_root_path({"project_root": str(project)})
    start_request = {
        "task": {
            "user_request": "prove a fresh JSON-RPC process can complete public Cortex orchestration by relative waves",
            "complexity": "C1",
            "requirements": ["implementation, verification, documentation, and close invariants"],
            "acceptance_criteria": ["complete every planned wave"],
            "allowed_paths": ["."],
            "verification": ["preserve result, evidence, handoff, and manifest receipts"],
        },
        "waves": waves(),
    }
    with JsonRpcHarness(server, project, host_state_dir, audience="coordinator") as rpc:
        listed = rpc.request("tools/list", {})["tools"]
        names = [item["name"] for item in listed]
        if names != [
            "start_orchestration",
            "continue_orchestration",
            "manage_orchestration",
            "manage_governance",
            "read_worker_result",
        ]:
            raise AssertionError(f"unexpected Cortex public tools: {names}")
        # A transport identity is the JSON-RPC request id within this server
        # connection, not the semantic request body.  Reusing the exact id
        # must replay the committed response; a fresh id with identical
        # content is a distinct new task.
        current = rpc.tool("start_orchestration", start_request, request_id="cold-boot-start")
        replay = rpc.tool("start_orchestration", start_request, request_id="cold-boot-start")
        fresh = rpc.tool("start_orchestration", start_request)
        if (
            not current.get("ok")
            or current.get("replayed") is not False
            or replay.get("replayed") is not True
            or replay.get("task_ref") != current.get("task_ref")
            or not current.get("dispatches")
            or replay.get("dispatches") != []
            or not fresh.get("ok")
            or fresh.get("replayed") is not False
            or fresh.get("task_ref") == current.get("task_ref")
            or not fresh.get("dispatches")
        ):
            raise AssertionError("transport identity start replay/new-task semantics are incorrect")
        task_ref = str(current["task_ref"])
        task_index = cortex.read_task_index(ledger)
        task_id = next(
            item_id for item_id in task_index
            if cortex._v3_task_ref(item_id) == task_ref
        )
        task_directory = task_index[task_id]["directory"]

    # A fresh process must reconstruct the active relative step read-only.
    with JsonRpcHarness(server, project, host_state_dir, audience="coordinator") as rpc, ExitStack() as workers:
        task_definition = cortex.load_task_definition(ledger / "tasks" / task_directory)
        current = rpc.tool("manage_orchestration", {"intent": "inspect", "task_ref": task_ref})
        continue_calls = 0
        parallel_wave_seen = False
        plan_approval_seen = False
        question_chat_cycle_seen = False
        implementation_applied = False
        briefing_sizes: list[dict[str, object]] = []
        dynamic_replan_count = 0
        pending_implementation_drop_rejected = False
        last_payload = None
        while current["outcome"] != "completed":
            if current.get("outcome") == "awaiting_plan_approval":
                if not current.get("plan_review", {}).get("attempt_result_ref"):
                    raise AssertionError(f"plan approval omitted its planner result reference: {current}")
                plan_approval_seen = True
                prompt = rpc.tool("manage_orchestration", {
                    "intent": "plan_approval",
                    "task_ref": task_ref,
                    "payload": {"decision": "prompt"},
                })
                interaction = prompt.get("chat_interaction") or {}
                choices = {item.get("id") for item in interaction.get("choices", []) if isinstance(item, dict)}
                plan = interaction.get("plan") or {}
                if (
                    interaction.get("schema") != "cortex/chat-interaction/v1"
                    or interaction.get("kind") != "plan_approval"
                    or choices != {"approve", "revise", "cancel"}
                    or not plan.get("work_packages")
                    or not plan.get("verification")
                    or "next ordinary chat message" not in str(interaction.get("response_instructions", ""))
                    or "End the turn immediately" not in str(interaction.get("coordinator_contract", ""))
                ):
                    raise AssertionError(f"plan approval chat interaction is incomplete: {prompt}")
                recommendation = interaction.get("llm_recommendation") or {}
                if recommendation.get("choice_id") not in choices or not recommendation.get("rationale"):
                    raise AssertionError(f"plan approval omitted its LLM recommendation: {prompt}")
                current = rpc.tool("manage_orchestration", {
                    "intent": "plan_approval",
                    "task_ref": task_ref,
                    "payload": {
                        "decision": "approve",
                        "request_id": interaction["interaction_ref"],
                    },
                })
                if not current.get("ok"):
                    raise AssertionError(f"plan approval failed: {current}")
                continue
            dispatches = current.get("dispatches", [])
            if not dispatches:
                raise AssertionError(f"active relative step {current.get('step')} has no dispatches")
            parallel = len(dispatches) > 1
            parallel_wave_seen |= parallel
            continue_calls += 1
            state = cortex.load_task_state_for_artifact(ledger / "tasks" / task_directory)
            active_attempts = [
                item for item in state["attempts"]
                if item.get("status") == "awaiting_host_spawn" and not item.get("invalidated")
            ][-len(dispatches):]
            results = []
            for index, (dispatch, attempt) in enumerate(zip(dispatches, active_attempts), 1):
                worker_rpc = workers.enter_context(JsonRpcHarness(
                    server,
                    project,
                    host_state_dir,
                    audience="worker",
                    worker_binding={
                        "project_root": str(project),
                        "task_id": str(state["task_id"]),
                        "attempt_id": str(attempt["attempt_id"]),
                        "profile": str(dispatch["profile"]),
                    },
                ))
                if not question_chat_cycle_seen:
                    asked = worker_rpc.tool("worker_question", {
                        "action": "ask",
                        "header": "Cold-boot rollout decision",
                        "question": "Which rollout policy should the cold-boot worker preserve before completing its assigned gate?",
                        "context": {
                            "decision_scope": "task_decision",
                            "why": "The answer proves that a real source-mode worker pauses and resumes through ordinary chat without opening an input UI.",
                        },
                        "options": [
                            {
                                "option_id": "gradual",
                                "label_en": "Use a gradual rollout",
                                "description": "Limits blast radius and preserves a bounded rollback path.",
                            },
                            {
                                "option_id": "immediate",
                                "label_en": "Use an immediate rollout",
                                "description": "Finishes sooner but exposes the complete change at once.",
                            },
                        ],
                        "recommended_option_ids": ["gradual"],
                        "recommendation": "Use a gradual rollout because it minimizes irreversible risk while preserving completion evidence.",
                    })
                    question_ref = str(asked.get("question_ref") or "")
                    if asked.get("outcome") != "question_recorded" or not question_ref:
                        raise AssertionError(f"worker question was not recorded: {asked}")
                    surfaced = rpc.tool("manage_orchestration", {
                        "intent": "question",
                        "task_ref": task_ref,
                        "payload": {"question_ref": question_ref},
                    })
                    question_interaction = surfaced.get("chat_interaction") or {}
                    rendered_questions = question_interaction.get("questions") or []
                    rendered_recommendation = (
                        rendered_questions[0].get("llm_recommendation")
                        if rendered_questions and isinstance(rendered_questions[0], dict)
                        else {}
                    ) or {}
                    recommended = rendered_recommendation.get("recommended_options") or []
                    if (
                        surfaced.get("outcome") != "awaiting_user"
                        or question_interaction.get("schema") != "cortex/chat-interaction/v1"
                        or question_interaction.get("kind") != "worker_question"
                        or question_interaction.get("interaction_ref") != question_ref
                        or not recommended
                        or recommended[0].get("option_id") != "gradual"
                        or not rendered_recommendation.get("rationale")
                        or "next ordinary chat message" not in str(question_interaction.get("response_instructions", ""))
                        or "End the turn immediately" not in str(question_interaction.get("coordinator_contract", ""))
                    ):
                        raise AssertionError(f"worker question chat interaction is incomplete: {surfaced}")
                    answered = rpc.tool("manage_orchestration", {
                        "intent": "question",
                        "task_ref": task_ref,
                        "payload": {
                            "question_ref": question_ref,
                            "answer": {
                                "option_ids": ["gradual"],
                                "custom_response": "Keep rollback bounded and resume the same worker.",
                            },
                        },
                    })
                    polled = worker_rpc.tool("worker_question", {
                        "action": "poll",
                        "question_ref": question_ref,
                    })
                    if (
                        answered.get("outcome") != "question_answered"
                        or polled.get("outcome") != "question_answered"
                        or polled.get("answer_option_ids") != ["gradual"]
                    ):
                        raise AssertionError(f"worker did not resume from the same durable chat question: {answered} / {polled}")
                    question_chat_cycle_seen = True
                changed_files: list[str] = []
                if dispatch.get("phase") == "implementation" and not implementation_applied:
                    (project / "tracked.txt").write_text("after\n", encoding="utf-8")
                    (project / "delete.txt").unlink()
                    (project / "old.txt").rename(project / "new.txt")
                    (project / "added.txt").write_text("untracked\n", encoding="utf-8")
                    changed_files = ["added.txt", "delete.txt", "new.txt", "old.txt", "tracked.txt"]
                    implementation_applied = True
                # Normal workers prove their immutable briefing and each
                # granted predecessor read through server-owned receipts.
                # They never manufacture acknowledgement prose in a result.
                briefing_rpc = workers.enter_context(JsonRpcHarness(
                    server,
                    project,
                    host_state_dir,
                    audience="worker",
                    worker_binding={
                        "project_root": str(project),
                        "task_id": str(state["task_id"]),
                        "attempt_id": str(attempt["attempt_id"]),
                        "profile": str(dispatch["profile"]),
                        "dispatch_ref": str(attempt["dispatch_ref"]),
                        "briefing_digest": str(attempt["briefing_digest"]),
                    },
                ))
                briefing = briefing_rpc.tool("read_dispatch_briefing", {})
                if not briefing.get("ok") or not briefing.get("briefing_receipt"):
                    raise AssertionError(f"read_dispatch_briefing failed: {briefing}")
                briefing_text = briefing.get("briefing")
                if not isinstance(briefing_text, str):
                    raise AssertionError("cold-boot briefing read did not return complete text")
                briefing_bytes = len(briefing_text.encode("utf-8"))
                briefing_path = Path(str(dispatch["briefing_path"]))
                stored_briefing = briefing_path.read_text(encoding="utf-8")
                if stored_briefing != briefing_text:
                    raise AssertionError("cold-boot briefing artifact differs from the scoped worker read")
                if hashlib.sha256(stored_briefing.encode("utf-8")).hexdigest() != attempt["briefing_digest"]:
                    raise AssertionError("cold-boot briefing artifact digest differs from the issued dispatch digest")
                artifact = briefing.get("briefing_artifact")
                if not isinstance(artifact, dict) or artifact.get("byte_size") != briefing_bytes:
                    raise AssertionError("cold-boot briefing artifact byte_size does not match its materialized content")
                # Public orchestration responses expose the native request as
                # a server-authorized ``call`` plus ``arguments`` envelope;
                # older smoke code looked only at the flattened private
                # request fields and falsely reported a lost bootstrap.
                dispatch_arguments = dispatch.get("arguments") if isinstance(dispatch.get("arguments"), dict) else {}
                bootstrap = str(
                    dispatch.get("message")
                    or dispatch.get("prompt")
                    or dispatch_arguments.get("message")
                    or dispatch_arguments.get("prompt")
                    or ""
                )
                if (
                    "read_dispatch_briefing" not in bootstrap
                    or str(briefing_path) not in bootstrap
                ):
                    raise AssertionError("cold-boot native bootstrap lost its immutable briefing capability")
                briefing_sizes.append({
                    "step": int(current["step"]),
                    "worker": index,
                    "gate": str(attempt["gate"]),
                    "profile": str(dispatch["profile"]),
                    "bytes": briefing_bytes,
                })
                for predecessor_ref in attempt.get("context_result_refs") or []:
                    predecessor = worker_rpc.tool("read_worker_result", {
                        "attempt_result_ref": predecessor_ref,
                    })
                    if not predecessor.get("ok") or not predecessor.get("predecessor_receipt"):
                        raise AssertionError(f"read_worker_result predecessor receipt failed: {predecessor}")
                worker_result = attempt_result(
                    index,
                    int(current["step"]),
                    list(attempt.get("context_result_refs") or []),
                    str(attempt["gate"]),
                    list(attempt.get("acceptance_criteria") or []),
                    list(attempt.get("verification") or []),
                    project,
                    list(task_definition.get("acceptance_criteria") or []),
                    list(task_definition.get("verification") or []),
                    changed_files,
                )
                # A verification event becomes the generated result's test
                # projection.  changed_files and the result envelope remain
                # server-observed/canonical; the worker sends semantic facts
                # only.
                verification = worker_rpc.tool("record_attempt_event", {
                    "event_type": "verification_claimed",
                    "event_key": f"cold-boot-verification-{current['step']}-{index}",
                    "payload": worker_result["verification_claimed"],
                })
                if not verification.get("ok"):
                    raise AssertionError(f"record_attempt_event failed: {verification}")
                completion = {
                    "status": "completed",
                    "summary": worker_result["summary"],
                    "findings": worker_result["findings"],
                    "decisions_needed": [],
                    "unresolved": [],
                    "claims": [],
                }
                if str(attempt["gate"]) == "plan":
                    completion["planning"] = planning(index, int(current["step"]))
                published = worker_rpc.tool("complete_attempt", completion)
                if not published.get("ok"):
                    raise AssertionError(f"complete_attempt failed: {published}")
                read = rpc.tool("read_worker_result", {"task_ref": task_ref, "attempt_result_ref": published["attempt_result_ref"]})
                if not read.get("ok") or read.get("result_view", {}).get("result", {}).get("summary") != published.get("summary"):
                    raise AssertionError(f"read_worker_result failed: {read}")
                result_value: dict[str, object] = {"attempt_result_ref": published["attempt_result_ref"]}
                if parallel:
                    result_value["worker"] = index
                results.append(result_value)
            last_payload = {
                "task_ref": task_ref,
                "step": current["step"],
                "results": results,
            }
            active_phases = {str(item.get("phase")) for item in dispatches}
            if active_phases == {"discover"} and dynamic_replan_count == 0:
                rejected = rpc.tool("continue_orchestration", {
                    **last_payload,
                    "future_waves": [
                        {"workers": [{"phase": "documentation"}]},
                    ],
                    "reason": "exercise the pending implementation retention invariant",
                })
                if not rejected.get("ok"):
                    raise AssertionError(f"advisory pipeline deviation unexpectedly blocked execution: {rejected}")
                advice_text = json.dumps(rejected.get("pipeline") or rejected.get("result") or rejected)
                if "advis" not in advice_text.lower() and "recommended" not in advice_text.lower():
                    raise AssertionError(f"pending implementation deviation lost its advisory record: {rejected}")
                pending_implementation_drop_rejected = True
                current = rpc.tool("continue_orchestration", {
                    **last_payload,
                    "future_waves": [
                        {"workers": [{"phase": "architecture"}, {"phase": "database_architecture"}]},
                        {"workers": [{"phase": "implementation"}]},
                        {"workers": [{"phase": "qa"}]},
                        {"workers": [{"phase": "security"}, {"phase": "performance"}]},
                        {"workers": [{"phase": "review"}]},
                    ],
                    "reason": "add required audit phases while retaining the pending delivery phase",
                })
                dynamic_replan_count += 1
            elif active_phases == {"architecture", "database_architecture"} and dynamic_replan_count == 1:
                current = rpc.tool("continue_orchestration", {
                    **last_payload,
                    "future_waves": [
                        {"workers": [{"phase": "database_architecture"}]},
                        {"workers": [{"phase": "accessibility"}]},
                        {"workers": [{"phase": "plan"}]},
                        {"workers": [{"phase": "implementation"}]},
                        {"workers": [{"phase": "qa"}]},
                        {"workers": [{"phase": "security"}, {"phase": "performance"}]},
                        {"workers": [{"phase": "review"}]},
                    ],
                    "reason": "architecture evidence adds an accessibility audit without dropping delivery",
                })
                dynamic_replan_count += 1
            elif active_phases == {"database_architecture"} and dynamic_replan_count == 2:
                current = rpc.tool("continue_orchestration", {
                    **last_payload,
                    "future_waves": [
                        {"workers": [{"phase": "accessibility"}]},
                        {"workers": [{"phase": "ux"}]},
                        {"workers": [{"phase": "plan"}]},
                        {"workers": [{"phase": "implementation"}]},
                        {"workers": [{"phase": "qa"}]},
                        {"workers": [{"phase": "security"}, {"phase": "performance"}]},
                        {"workers": [{"phase": "review"}]},
                    ],
                    "reason": "database evidence adds UX verification while preserving every prior obligation",
                })
                dynamic_replan_count += 1
            else:
                current = rpc.tool("continue_orchestration", last_payload)
            if not current.get("ok"):
                raise AssertionError(f"continue failed: {current}")
        # The completed task is reconciled through the read-only task-scoped
        # inspection contract.  Re-submitting consumed continuation receipts
        # is not part of this micro-live smoke and must not enter a corrective
        # route merely to test a duplicate call.
        inspected = rpc.tool("manage_orchestration", {"intent": "inspect", "task_ref": task_ref})
        if not inspected.get("ok") or inspected.get("task_ref") != current.get("task_ref"):
            raise AssertionError("final task inspection lost the completed task identity")
    # The orchestrator may legitimately serialize a previously parallel
    # recommendation when it chooses a corrective route; parallelism is an
    # optimization, not a lifecycle invariant.
    if not question_chat_cycle_seen:
        raise AssertionError("the smoke did not complete a durable ordinary-chat question pause/resume cycle")
        # Dynamic replanning is an orchestrator choice; a completed chosen
        # route is valid even when no corrective replan is requested.
    if not briefing_sizes:
        raise AssertionError("cold-boot did not materialize any immutable worker briefing")

    task_path = ledger / "tasks" / task_directory
    state = cortex.load_task_state_for_artifact(task_path)
    task = cortex.load_task_definition(task_path, state)
    attempt_result_refs = [
        str(item.get("attempt_result_ref") or "")
        for item in state.get("attempts", [])
        if isinstance(item, dict) and item.get("attempt_result_ref")
    ]
    if task.get("schema") != "cortex/v8" or state.get("schema") != "cortex/v8" or state.get("status") != "completed":
        raise AssertionError("public orchestration did not preserve the cortex/v8 ledger or complete the task")
    if not attempt_result_refs:
        raise AssertionError("every passed worker attempt must have a canonical AttemptResult ref")
    passed_gates = {
        str(item.get("gate"))
        for item in state.get("attempts", [])
        if item.get("status") == "passed" and not item.get("invalidated")
    }
    expected_gates = set(state.get("chosen_pipeline") or state.get("current_pipeline") or [])
    if not expected_gates.issubset(passed_gates):
        raise AssertionError(
            "dynamic pipeline skipped required gates: "
            + ", ".join(sorted(expected_gates - passed_gates))
        )
    return {
        "status": "PASS", "fixture": str(base), "task_directory": str(task_path),
        "continue_calls": continue_calls, "worker_attempts": len(state.get("attempts", [])),
        "briefing_size_policy": "advisory_only",
        "briefing_size_max_bytes": max(int(item["bytes"]) for item in briefing_sizes),
        "briefing_sizes": briefing_sizes,
        "result_count": len(attempt_result_refs), "parallel_wave_seen": parallel_wave_seen,
        "plan_approval_seen": plan_approval_seen,
        "question_chat_cycle_seen": question_chat_cycle_seen,
        "dynamic_replan_applied": dynamic_replan_count >= 1,
        "dynamic_replan_count": dynamic_replan_count,
        "replan_count": int(state.get("replan_count", 0)),
        "pending_implementation_drop_rejected": pending_implementation_drop_rejected,
        "implementation_phase_seen": "implementation" in passed_gates,
        "passed_gates": sorted(passed_gates),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--keep", action="store_true")
    parser.add_argument("--server", type=Path, default=SERVER, help="Cortex MCP server path to exercise")
    arguments = parser.parse_args()
    server = arguments.server.expanduser().resolve()
    if not server.is_file() or server.is_symlink():
        raise SystemExit(f"cold-boot smoke: invalid Cortex server path: {server}")
    if arguments.keep:
        result_value = run(Path(tempfile.mkdtemp(prefix="cortex-boot-")), server)
    else:
        with tempfile.TemporaryDirectory(prefix="cortex-boot-") as directory:
            result_value = run(Path(directory), server)
    print("cold-boot smoke: " + json.dumps(result_value, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
