"""Delegation persistence service behind the stable Cortex facade."""
from __future__ import annotations

import json
import hashlib
import stat
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from cortex_runtime.core.runtime_bindings import bind_symbols
from cortex_runtime.delegation import (
    delegation_lists,
    dispatch_context,
    select_profile as select_delegation_profile,
    spawn_request as build_spawn_request,
    task_kind_and_risk,
)


bind_symbols(
    "delegation_service",
    globals(),
    (
        "AGENTS",
        "AWAITING_HOST_SPAWN",
        "DOCUMENTATION_EVIDENCE_KINDS",
        "PROFILES",
        "QUESTION_SCHEMA",
        "REWORK_EFFORT_BY_PRIOR_FAILURES",
        "REWORK_TERRA_AFTER_FAILURES",
        "SCHEMA",
        "_contained_path",
        "_is_knowledge_harvest_task",
        "_project_knowledge_context",
        "_resolved_user_decisions",
        "_v3_task_ref",
        "_write_delegation_package",
        "active_gates",
        "authorize",
        "canonical_profile",
        "capture_project_manifest",
        "digest_text",
        "db_put_worker_session",
        "host_spawn_bootstrap",
        "host_spawn_prompt",
        "ledger_root",
        "load_state",
        "load_task_definition",
        "native_worker_task_name",
        "now",
        "primary_gate",
        "profiles_for_gate",
        "redact",
        "render_gate_briefing",
        "resolve_dispatch_route",
        "safe_id",
        "sanitize_structured",
        "save_state",
        "select_implementation_profile",
        "select_project_root",
        "state_lock",
        "store_immutable_artifact",
        "store_manifest_snapshot",
        "worker_display_name",
        "worker_module_label",
    ),
)
from cortex_runtime.projection_service import enqueue as enqueue_projection, materialize_job
from cortex_runtime.ledger_db import fail_projection_job, list_projection_jobs, get_task_document as db_get_task_document
from cortex_runtime import attempt_protocol, canonical_json


def _canonical_text_items(
    value: object,
    *,
    field: str,
    limit: int,
    item_chars: int,
) -> list[str]:
    """Validate a current canonical task text-array field without rewriting it.

    ``limit`` and ``item_chars`` are retained at this call boundary for
    compatibility with its former prompt-projection role.  A delegation
    package is not the canonical task definition, but silently shortening it
    before the immutable task-contract artifact is made would lose a valid
    fact.  Rendering owns byte limits and always marks a reduced projection.
    """
    if value is None:
        return []
    if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
        raise ValueError(f"{field} must be a current canonical text array")
    del field, limit, item_chars
    return list(value)


def _governance_dispatch_projection(
    task_definition: dict[str, Any],
    state: dict[str, Any],
    *,
    manifest_ref: str,
    manifest_digest: str,
) -> dict[str, Any] | None:
    """Build the server-owned governance facts carried by every governed dispatch.

    Governance is a task contract, not a special property of the activation and
    close reviewers.  Normal plan/delivery workers must see the same immutable
    policy, manifest, and resolved pipeline boundary; otherwise a successor can
    mistake a missing projection for an unresolved product decision and ask the
    user to re-decide it.  Keep an incomplete projection visible as incomplete:
    callers must not synthesize a policy snapshot or pipeline on the worker
    side.
    """
    task_governance = task_definition.get("governance")
    state_governance = state.get("governance")
    governance = (
        task_governance if isinstance(task_governance, dict) else
        state_governance if isinstance(state_governance, dict) else
        None
    )
    if not governance:
        return None
    effective_mode = str(governance.get("effective_mode") or "").strip().lower()
    if effective_mode not in {"light", "full"}:
        return None
    pipeline = [
        str(item).strip()
        for item in (state.get("current_pipeline") or [])
        if str(item).strip()
    ]
    snapshot = governance.get("policy_snapshot")
    snapshot_copy = json.loads(json.dumps(snapshot, ensure_ascii=False, sort_keys=True)) if isinstance(snapshot, dict) else {}
    supplied_snapshot_digest = str(governance.get("policy_snapshot_digest") or "").strip().lower()
    if snapshot_copy and supplied_snapshot_digest:
        expected_snapshot_digest = canonical_json.digest(snapshot_copy)
        if supplied_snapshot_digest != expected_snapshot_digest:
            raise ValueError(
                "governance policy_snapshot_digest does not match the exact canonical policy_snapshot"
            )
    return {
        "schema": governance.get("schema") or "cortex/governance/v1",
        "requested_mode": governance.get("requested_mode"),
        "effective_mode": effective_mode,
        "complexity": governance.get("complexity") or state.get("complexity"),
        "reasons": list(governance.get("reasons") or []),
        "trigger_evidence": list(governance.get("trigger_evidence") or []),
        "initiative_ref": governance.get("initiative_ref") or "",
        "autonomous_scope_ref": governance.get("autonomous_scope_ref") or "",
        "policy_snapshot": snapshot_copy,
        "policy_snapshot_digest": supplied_snapshot_digest or None,
        "manifest_ref": str(manifest_ref or "").strip(),
        "manifest_digest": str(manifest_digest or "").strip(),
        "current_pipeline": pipeline,
        "close_obligations": list(governance.get("close_obligations") or []),
    }


def _semantic_finding_text(value: object) -> str:
    """Select the short semantic fact from a canonical result/event value."""
    if isinstance(value, dict):
        value = (
            value.get("summary")
            or value.get("message")
            or value.get("finding")
            or value.get("detail")
            or ""
        )
    return redact(value, 500)


def _canonical_verification_checks(root: Path, task_id: str, attempt_id: str) -> list[str]:
    """Project only command/exit facts from AttemptEvent verification rows."""
    checks: list[str] = []
    for event in attempt_protocol.list_attempt_events(
        root, task_id=task_id, attempt_id=attempt_id,
    ):
        if event.get("event_type") != "verification_observed" or event.get("actor") != "cortex":
            continue
        payload = event.get("payload")
        if not isinstance(payload, dict):
            continue
        candidates = payload.get("tests") if isinstance(payload.get("tests"), list) else [payload]
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            command = redact(candidate.get("command") or candidate.get("check") or "", 420)
            if not command:
                continue
            exit_code = candidate.get("exit_code")
            rendered = f"{command} (exit {exit_code})" if isinstance(exit_code, int) and not isinstance(exit_code, bool) else command
            if rendered not in checks:
                checks.append(rendered)
    return checks


def _bounded_predecessor_results(
    root: Path,
    task_id: str,
    attempts: object,
    context_result_refs: list[str],
) -> list[dict[str, Any]]:
    """Build the dispatch's semantic predecessor basis from canonical facts.

    An AttemptResult plus its lossless AttemptEvent stream is the only source.
    Result refs are server-issued and mapped back to the exact persisted
    attempt before a semantic projection is constructed.  Generated views
    and indexes never contribute predecessor context.
    """
    result_attempts: dict[str, dict[str, Any]] = {}
    if isinstance(attempts, list):
        for item in attempts:
            if not isinstance(item, dict):
                continue
            raw_result_ref = str(item.get("attempt_result_ref") or "").strip()
            if not raw_result_ref:
                continue
            result_attempts[safe_id(raw_result_ref)] = item
    results: list[dict[str, Any]] = []
    for result_ref in context_result_refs:
        attempt = result_attempts.get(result_ref)
        if not isinstance(attempt, dict):
            raise ValueError("context_result_refs must name completed canonical AttemptResults from this task")
        attempt_id = safe_id(str(attempt.get("attempt_id") or ""))
        canonical = attempt_protocol.get_attempt_result(
            root, task_id=task_id, attempt_id=attempt_id,
        ) if attempt_id else None
        if (
            canonical is None
            or safe_id(str(canonical.get("result_ref") or "")) != result_ref
            or str(canonical.get("lifecycle_status") or "") != attempt_protocol.LIFECYCLE_COMPLETED
        ):
            raise ValueError("context_result_refs must name finalized canonical AttemptResults from this task")
        result_metadata = canonical.get("metadata") if isinstance(canonical.get("metadata"), dict) else {}
        identity = result_metadata.get("identity") if isinstance(result_metadata.get("identity"), dict) else {}
        findings = [
            _semantic_finding_text(item)
            for item in [*(canonical.get("unresolved") or []), *(canonical.get("findings") or [])]
        ]
        semantic_events = [
            {
                "event_type": event["event_type"],
                "actor": event["actor"],
                "payload": event["payload"],
            }
            for event in attempt_protocol.list_attempt_events(root, task_id=task_id, attempt_id=attempt_id)
            if event.get("actor") == "cortex" and event.get("event_type") in {
                "question_created", "question_answered", "decision_resolved",
            }
        ]
        results.append({
            # Public/context projections use the canonical wire name.  The
            # SQLite row keeps its private result_ref column, but that storage
            # detail must not leak into successor handoffs.
            "attempt_result_ref": result_ref,
            "attempt_id": attempt_id,
            "gate": redact(result_metadata.get("phase") or attempt.get("gate") or "", 80),
            "profile": redact(identity.get("profile") or attempt.get("profile") or "", 120),
            "summary": redact(canonical.get("summary") or "", 1200),
            "changed_files": _canonical_text_items(
                canonical.get("changed_files"), field="AttemptResult changed_files", limit=24, item_chars=300,
            ),
            "checks": _canonical_verification_checks(root, task_id, attempt_id),
            "unresolved_findings": [item for item in findings if item],
            "semantic_events": semantic_events,
            "semantic_source": "attempt_result",
        })
    return results


def _next_attempt_id(state: dict[str, Any], task_dir: Path, gate: str) -> str:
    """Allocate an attempt id from canonical state only.

    Briefing projections are derived outbox output.  Looking at their
    directory made an absent or stale export influence business identity and
    forced an eager filesystem dependency before the attempt existed.
    """
    del task_dir
    highest = len(state.get("attempts", []))
    return f"{gate}-{highest + 1:02d}"


def _mark_projection_failure(params: dict[str, Any], attempt_id: str, error: Exception) -> None:
    """Record a required-briefing failure after the outbox has been failed.

    The attempt was committed before filesystem work began.  Leaving it in
    ``awaiting_host_spawn`` after a failed required export would let a later
    coordinator return a dispatch that this call never made ready.
    """
    root = ledger_root(params)
    with state_lock(root):
        _, task_dir, state = load_state(str(params["task_id"]), params)
        attempt = next((item for item in state.get("attempts", []) if item.get("attempt_id") == attempt_id), None)
        if attempt is None or attempt.get("status") != AWAITING_HOST_SPAWN:
            return
        attempt["status"] = "failed"
        attempt["failure_reason"] = "required_dispatch_briefing_projection_failed"
        attempt["failure_detail"] = redact(str(error), 1000)
        attempt["failed_at"] = now()
        save_state(
            task_dir,
            task_dir / "state.sqlite",
            state,
            "delegation_projection_failed",
            f"{attempt_id}: required dispatch briefing projection failed",
        )


def _ensure_briefing_task_directory(task_dir: Path) -> None:
    """Create the task root only for the required briefing projection.

    New task records intentionally have no artifact directory.  A dispatched
    worker is the one exception: its immutable briefing must exist before the
    host receives the dispatch.  Never repurpose, empty, or follow a pre-made
    path while satisfying that requirement.
    """
    try:
        task_dir.mkdir(mode=0o700)
    except FileExistsError:
        pass
    info = task_dir.lstat()
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise ValueError("dispatch briefing task directory must be a real directory")
    task_dir.chmod(0o700, follow_symlinks=False)


_EPHEMERAL_SPAWN_FIELDS = frozenset({"briefing_path", "message", "prompt"})


def _durable_spawn_request(request: dict[str, Any]) -> dict[str, Any]:
    """Keep host-private artifact paths out of durable attempt state.

    The host payload is a one-shot projection.  Persisting its rendered
    message used to retain absolute host-control paths, so a relocated ledger
    could replay a stale path after recovery.  The
    immutable dispatch identity below is enough to recreate the transport
    payload against the current ledger root.
    """
    return {key: value for key, value in request.items() if key not in _EPHEMERAL_SPAWN_FIELDS}


def rehydrate_dispatch_spawn_request(
    task_dir: Path,
    task_definition: dict[str, Any],
    attempt: dict[str, Any],
) -> dict[str, Any]:
    """Rebuild one transient native host payload from durable relative refs."""
    request = dict(attempt.get("spawn_request") or {})
    task_id = str(attempt.get("task_id") or task_definition.get("task_id") or "")
    attempt_id = str(attempt.get("attempt_id") or "")
    dispatch_ref = str(attempt.get("dispatch_ref") or "")
    profile = str(attempt.get("profile") or attempt.get("agent") or "")
    briefing_file = str(attempt.get("briefing_file") or "")
    briefing_digest = str(attempt.get("briefing_digest") or "")
    project_root = Path(str(task_definition.get("project_root") or ""))
    relative = Path(briefing_file)
    if (
        not task_id or not attempt_id or not dispatch_ref or not profile or not briefing_digest
        or not project_root.is_absolute() or relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts)
    ):
        raise ValueError("attempt is missing its durable dispatch identity")
    briefing_path = _contained_path(task_dir, task_dir / relative, "dispatch briefing")
    intent_relative = Path(str(task_definition.get("user_intent_artifact_path") or "intent/user-request.txt"))
    if intent_relative.is_absolute() or any(part in {"", ".", ".."} for part in intent_relative.parts):
        raise ValueError("task user-intent artifact path is unsafe")
    intent_path = _contained_path(task_dir, task_dir / intent_relative, "user intent artifact")
    plan_relative_raw = str(attempt.get("plan_unit_file") or "")
    plan_path: Path | None = None
    if plan_relative_raw:
        plan_relative = Path(plan_relative_raw)
        if plan_relative.is_absolute() or any(part in {"", ".", ".."} for part in plan_relative.parts):
            raise ValueError("attempt compiled-plan artifact path is unsafe")
        plan_path = _contained_path(task_dir, task_dir / plan_relative, "compiled plan unit")
    request["briefing_file"] = briefing_file
    request["briefing_path"] = str(briefing_path)
    request["briefing_digest"] = briefing_digest
    request["dispatch_ref"] = dispatch_ref
    request["message"] = host_spawn_bootstrap(
        profile, briefing_path, briefing_digest, dispatch_ref, task_id, attempt_id, project_root,
        intent_path=str(intent_path),
        intent_digest=str(task_definition.get("user_request_digest") or ""),
        plan_unit_path=str(plan_path) if plan_path is not None else None,
        plan_unit_digest=str(attempt.get("plan_unit_digest") or ""),
    )
    if request.get("host_tool") == "create_thread":
        request["prompt"] = request["message"]
        request.setdefault("title", str(attempt.get("display_name") or request.get("task_name") or "Cortex worker"))
    return request

def record_delegation(params: dict[str, Any]) -> dict[str, Any]:
    root = ledger_root(params)
    prepared: dict[str, Any]
    compiled_plan_job: dict[str, Any] | None = None
    compiled_plan_path: Path | None = None
    compiled_plan_digest: str | None = None
    intent_job: dict[str, Any] | None = None
    intent_path: Path | None = None
    intent_digest: str | None = None
    task_contract_job: dict[str, Any] | None = None
    task_contract_path: Path | None = None
    task_contract_digest: str | None = None
    compiled_relative: str | None = None
    with state_lock(root):
        _, task_dir, state = load_state(str(params["task_id"]), params)
        authorize(state, params)
        requested_revision = params.get("expected_revision")
        revision_correction = (
            {"requested": requested_revision, "used": state["revision"]}
            if requested_revision is not None and requested_revision != state["revision"] else None
        )
        expected_status_receipt = "status-" + digest_text(canonical_json.dumps({
            "task_id": state["task_id"],
            "principal": state.get("principal", "local"),
            "revision": state["revision"],
        }))[:24]
        status_receipt = str(params.get("status_receipt") or "").strip()
        observed = (
            {"status_receipt": expected_status_receipt, "revision": state["revision"]}
            if status_receipt == expected_status_receipt else None
        )
        receipt_correction = observed is None
        status_receipt = expected_status_receipt
        wave = active_gates(state)
        gate = str(primary_gate(state) or "")
        requested_gate = str(params.get("gate") or gate)
        if requested_gate in wave:
            gate = requested_gate
        task_definition = load_task_definition(task_dir, state)
        requested_agent = canonical_profile(params.get("agent") or "")
        agent, selection_reason, _ = select_delegation_profile(
            params,
            gate=gate,
            task_definition=task_definition,
            agents=AGENTS,
            canonical_profile=canonical_profile,
            select_implementation_profile=select_implementation_profile,
            profiles_for_gate=profiles_for_gate,
        )
        agent_correction = ({"requested": requested_agent or None, "used": agent} if requested_agent != agent else None)
        if state["status"] != "active":
            raise ValueError(f"cannot delegate while task status is '{state['status']}'")
        if gate == "documentation" and agent != "technical_writer":
            raise ValueError("documentation gate must be delegated to technical_writer")
        retry = int(params.get("retry", 0))
        if retry < 0:
            raise ValueError("retry must be non-negative")
        if gate == "documentation":
            # A facade documentation wave may intentionally contain several
            # parallel writers.  Their orchestration keys are the identity of
            # each slot; treating any active documentation attempt as a
            # duplicate prevents the third and later writers from ever being
            # materialized.  Direct/non-facade delegation has no slot key and
            # keeps the single-documentation-attempt retry guard.
            delegation_key = str(params.get("orchestration_delegation_key") or "").strip()
            active_documentation = [
                item for item in state["attempts"]
                if item.get("gate") == gate
                and item.get("status") in {AWAITING_HOST_SPAWN, "running", "passed"}
                and not item.get("invalidated")
            ]
            existing = (
                [
                    item for item in active_documentation
                    if item.get("orchestration_delegation_key") == delegation_key
                ]
                if delegation_key
                else active_documentation
            )
            if existing:
                evidence = [
                    item for item in state.get("evidence", [])
                    if item.get("gate") == gate
                    and item.get("attempt_id") in {attempt["attempt_id"] for attempt in existing}
                    and item.get("kind") in DOCUMENTATION_EVIDENCE_KINDS
                    and item.get("decision") in {"updated", "not_applicable"}
                ]
                return {
                    "recorded": False,
                    "reason": "documentation_attempt_already_available",
                    "candidate_attempt_ids": [item["attempt_id"] for item in existing],
                    "next_action": (
                        "confirm_host_spawn"
                        if any(item.get("status") == AWAITING_HOST_SPAWN for item in existing)
                        else "record_gate_outcome" if evidence else "record_evidence"
                    ),
                    "recoverable": True,
                    "state": state,
                }
        prior_failed_attempts = [
            attempt for attempt in state["attempts"]
            if attempt["gate"] == gate and attempt["status"] == "failed"
        ]
        requested_strategy = redact(params.get("strategy", ""), 1000) or "default"
        recorded_failures = int((state.get("orchestrate_gate_failure_counts") or {}).get(gate, 0))
        active_rework_iterations = [
            max(0, int(item.get("iteration") or 1) - 1)
            for item in (state.get("closure_rework") or {}).values()
            if isinstance(item, dict)
            and item.get("status") == "rework_required"
            and item.get("target_gate") == gate
        ]
        prior_failure_count = max(
            len(prior_failed_attempts),
            recorded_failures,
            max(active_rework_iterations, default=0),
        )
        briefing = render_gate_briefing(gate, task_definition.get("user_request", ""), agent)
        ownership = str(params.get("ownership", "")).strip() or briefing["ownership"]
        objective = str(params.get("objective", "")).strip() or briefing["objective"]
        requested_task_kind = str(params.get("task_kind") or "").strip()
        requested_risk = str(params.get("risk") or "").strip().lower()
        task_kind, risk = task_kind_and_risk(params, gate)
        route_params = dict(params)
        effort_order = {name: index for index, name in enumerate(("low", "medium", "high", "xhigh", "max"))}
        automatic_model_escalated = False
        if prior_failure_count:
            effort_floor = (
                REWORK_EFFORT_BY_PRIOR_FAILURES["1"]
                if prior_failure_count == 1 else
                REWORK_EFFORT_BY_PRIOR_FAILURES["2"]
                if prior_failure_count == 2 else
                REWORK_EFFORT_BY_PRIOR_FAILURES["3+"]
            )
            requested_effort = str(route_params.get("requested_reasoning_effort") or "").strip().lower()
            route_params["requested_reasoning_effort"] = max(
                (requested_effort, effort_floor) if requested_effort in effort_order else (effort_floor,),
                key=effort_order.__getitem__,
            )
            explicit_user_model = str(route_params.get("user_requested_model") or "").strip()
            if (
                prior_failure_count >= REWORK_TERRA_AFTER_FAILURES
                and agent not in {"explorer", "security_auditor"}
                and gate != "security"
                and not explicit_user_model
            ):
                route_params["requested_model"] = "gpt-5.6-terra"
                automatic_model_escalated = True
            selection_reason += (
                f" Rework escalation after {prior_failure_count} unresolved attempt(s): "
                f"minimum effort {route_params['requested_reasoning_effort']}"
                + (" with Terra." if route_params.get("requested_model") == "gpt-5.6-terra" else ".")
            )
        dispatch_mode, luna_fallback, route, thread_environment = dispatch_context(
            route_params,
            gate=gate,
            agent=agent,
            task_kind=task_kind,
            complexity=str(state.get("complexity", "C1")),
            resolve_dispatch_route=resolve_dispatch_route,
        )
        route["rework_escalation"] = {
            "prior_failure_count": prior_failure_count,
            "unbounded_rework": True,
            "effort_floor": route.get("selected_reasoning_effort"),
            "model_escalated": bool(
                automatic_model_escalated
                and route.get("selected_model") == "gpt-5.6-terra"
            ),
        }
        required_lists = delegation_lists(params, task_definition, briefing)
        context_result_refs = [safe_id(str(item)) for item in params.get("context_result_refs", [])]
        available_results: set[str] = set()
        for item in state.get("attempts", []):
            if not isinstance(item, dict):
                continue
            raw_result_ref = str(item.get("attempt_result_ref") or "").strip()
            if raw_result_ref:
                available_results.add(safe_id(raw_result_ref))
        if (
            len(context_result_refs) != len(set(context_result_refs))
            or not set(context_result_refs).issubset(available_results)
        ):
            raise ValueError("context_result_refs must be unique finalized AttemptResults from this task")
        predecessor_results = _bounded_predecessor_results(
            root,
            state["task_id"],
            state.get("attempts"),
            context_result_refs,
        )
        all_resolved_user_decisions = _resolved_user_decisions(task_dir, state)
        resolved_user_decisions_digest = digest_text(canonical_json.dumps(all_resolved_user_decisions))
        # Preserve every resolved user decision in the immutable briefing.
        # Concision is prompt guidance, never a backend byte quota.
        resolved_user_decisions = list(all_resolved_user_decisions)
        attempt_id = _next_attempt_id(state, task_dir, gate)
        # The role label remains canonical, but the native task key must be
        # unique per task/attempt.  Keeping only ``agent`` here lets the host
        # mistake a fresh dispatch for a continuation of an older child.
        module = worker_module_label(
            task_definition.get("user_request") or objective,
            required_lists["allowed_paths"],
            gate,
        )
        display_name = worker_display_name(agent, module)
        task_name = native_worker_task_name(agent, state["task_id"], attempt_id, module)
        visible_thread = dispatch_mode == "visible_thread"
        spawn_request = build_spawn_request(
            dispatch_mode=dispatch_mode,
            gate=gate,
            agent=agent,
            display_name=display_name,
            task_name=task_name,
            profiles=PROFILES,
            selection_reason=selection_reason,
            route=route,
            thread_environment=thread_environment,
        )
        facade_managed = bool(params.get("facade_managed", False))
        question_route = (
            {"mode": "native_parent", "answer_location": "main_chat"}
            if facade_managed else
            {
                "mode": "pull",
                "worker_tool": "cortex.question",
                "publish_tool": "publish_worker_question",
                "updates_tool": "get_worker_question_updates",
                "coordinator_list_tool": "list_worker_questions",
                "coordinator_answer_tool": "answer_worker_question",
                "coordinator_surface": "ordinary_final_chat_message",
                "pause_until": "next_user_message",
                "answer_location": "main_chat",
            }
        )
        orchestration_wave_id = str(params.get("orchestration_wave_id", "")).strip() or None
        orchestration_delegation_key = str(params.get("orchestration_delegation_key", "")).strip() or None
        project_root = select_project_root(params)
        context_files, knowledge_index_files = _project_knowledge_context(project_root, params.get("context_files"))
        result_baseline = capture_project_manifest(project_root)
        result_baseline_ref = store_manifest_snapshot(task_dir, result_baseline)
        governance_context = _governance_dispatch_projection(
            task_definition,
            state,
            manifest_ref=result_baseline_ref,
            manifest_digest=str(result_baseline.get("digest") or ""),
        )
        dispatch_ref = "dispatch-" + digest_text(
            "\0".join((state["task_id"], attempt_id, agent, task_name))
        )[:24]
        briefing_file = f"delegations/{attempt_id}.{dispatch_ref}.briefing.md"
        briefing_path = _contained_path(task_dir, task_dir / briefing_file, "dispatch briefing")
        # Keep the complete ask history and the latest steer distinct.  The
        # immutable user-intent artifact is intentionally the original text;
        # using it as the only briefing input would make a later override
        # invisible to a replacement worker.  Requirements retain every ask,
        # while current_user_intent is the authoritative latest override.
        revision_history = task_definition.get("active_steers")
        if not isinstance(revision_history, list):
            revision_history = []
        task_requirements = _canonical_text_items(task_definition.get("requirements"), field="requirements", limit=100, item_chars=1000)
        task_constraints = _canonical_text_items(task_definition.get("constraints"), field="constraints", limit=100, item_chars=1000)
        task_scope = _canonical_text_items(task_definition.get("scope"), field="scope", limit=100, item_chars=500)
        task_acceptance = _canonical_text_items(task_definition.get("acceptance_criteria"), field="acceptance_criteria", limit=100, item_chars=1000)
        task_verification = _canonical_text_items(task_definition.get("verification"), field="verification", limit=100, item_chars=1000)
        pause_conditions = _canonical_text_items(task_definition.get("pause_conditions"), field="pause_conditions", limit=100, item_chars=1000)
        package = {"schema": SCHEMA, "task_id": state["task_id"], "task_ref": _v3_task_ref(state["task_id"]), "gate": gate, "attempt_id": attempt_id, "agent": agent, "profile": agent, "display_name": display_name, "selection_reason": redact(selection_reason, 1000), "spawn_request": spawn_request, **route, "luna_fallback": luna_fallback, "retry": retry, "parallel": bool(params.get("parallel", False)), "mode": "harvest" if _is_knowledge_harvest_task(task_definition) else "ordinary", "strategy": requested_strategy, "task_requirements": task_requirements, "task_constraints": task_constraints, "task_scope": task_scope, "task_acceptance_criteria": task_acceptance, "task_verification": task_verification, "current_user_intent": redact(task_definition.get("current_user_intent") or task_definition.get("user_request", ""), 4000), "current_user_intent_revision": int(task_definition.get("current_user_intent_revision") or task_definition.get("task_revision") or state.get("task_revision") or 1), "user_intent_revisions": sanitize_structured(revision_history), "budget": redact(task_definition.get("budget", ""), 500), "pause_conditions": pause_conditions, "plan_feedback": redact(params.get("plan_feedback", ""), 2000) or None, "objective": redact(objective, 4000), "ownership": redact(ownership, 1000), "depends_on_phases": [redact(item, 64) for item in params.get("context_gates", [])], "context_files": [redact(item, 500) for item in context_files], "knowledge_index_files": knowledge_index_files, "context_result_refs": context_result_refs, "predecessor_results": predecessor_results, "predecessor_selection": {"available": len(context_result_refs)}, "resolved_user_decisions": resolved_user_decisions, "resolved_user_decision_count": len(all_resolved_user_decisions), "resolved_user_decisions_digest": resolved_user_decisions_digest, "resolved_user_decisions_truncated": False, "plan_tracker_ref": "sqlite:task_documents/plan_tracker_current", "result_baseline_ref": result_baseline_ref, "allowed_paths": [redact(item, 500) for item in required_lists["allowed_paths"]], "acceptance_criteria": [redact(item, 1000) for item in required_lists["acceptance_criteria"]], "verification": [redact(item, 1000) for item in required_lists["verification"]], "governance_context": governance_context, "project_root": str(project_root), "coordinator_principal": state.get("principal", "local"), "coordinator_thread_id": state.get("thread_id", ""), "internal_language": "en", "visibility": "visible" if visible_thread else "hidden", "user_facing": visible_thread, "user_owned_thread": visible_thread, "thread_environment": thread_environment, "question_route": question_route, "escalation_route": "main_chat", "handoff_route": "main_chat", "subdelegation": "forbidden_unless_explicitly_authorized", "question_contract": QUESTION_SCHEMA, "facade_managed": facade_managed, "orchestration_wave_id": orchestration_wave_id, "orchestration_delegation_key": orchestration_delegation_key, "status_receipt": status_receipt, "dispatch_correlation": "host_spawn_required", "spawn_status": "requested", "created_at": now()}
        package["dispatch_ref"] = dispatch_ref
        package["briefing_file"] = briefing_file
        package["pause_conditions"] = pause_conditions
        tracker = db_get_task_document(root, state["task_id"], "plan_tracker_current")
        if isinstance(tracker, dict) and tracker.get("schema") == "cortex/plan-tracker/v1":
            package["plan_tracker"] = {
                "schema": tracker.get("schema"),
                "revision": tracker.get("revision"),
                "task_revision": tracker.get("task_revision"),
                "items": [
                    {
                        "id": item.get("id"), "kind": item.get("kind"),
                        "status": item.get("status"), "order": item.get("order"),
                        "gates": list(item.get("gates") or []),
                        "depends_on": list(item.get("depends_on") or []),
                    }
                    for item in tracker.get("items", []) if isinstance(item, dict)
                ],
            }
        if isinstance(task_definition.get("follow_up"), dict):
            package["follow_up"] = sanitize_structured(task_definition["follow_up"])
        package["user_intent"] = {
            "projection": redact(
                task_definition.get("user_request_projection")
                or task_definition.get("user_request")
                or task_definition.get("user_request", ""),
                1600,
            ),
            "artifact_ref": task_definition.get("user_intent_artifact_ref"),
            "artifact_path": str(
                _contained_path(
                    task_dir,
                    task_dir / str(task_definition.get("user_intent_artifact_path") or "intent/user-request.txt"),
                    "user intent artifact",
                )
            ),
            "digest_sha256": task_definition.get("user_request_digest"),
            "byte_size": task_definition.get("user_intent_byte_size"),
        }
        # A dispatch prompt is complete, while a task definition
        # is not.  Give every worker a digest-bound immutable task-contract
        # artifact before rendering the prompt, so reducing an oversized
        # array/scalar is a projection rather than data loss or a reason to
        # strand a post-result continuation.
        task_contract_relative = f"task-contract/{attempt_id}.json"
        task_contract_path = _contained_path(
            task_dir, task_dir / task_contract_relative, "task contract"
        )
        task_contract = {
            "schema": "cortex/task-contract/v1",
            "task_id": state["task_id"],
            "task_ref": _v3_task_ref(state["task_id"]),
            "task_revision": state.get("task_revision") or state.get("revision"),
            "user_request": task_definition.get("user_request"),
            "current_user_intent": task_definition.get("current_user_intent"),
            "requirements": task_definition.get("requirements"),
            "constraints": task_definition.get("constraints"),
            "scope": task_definition.get("scope"),
            "allowed_paths": task_definition.get("allowed_paths"),
            "acceptance_criteria": task_definition.get("acceptance_criteria"),
            "verification": task_definition.get("verification"),
            "pause_conditions": task_definition.get("pause_conditions"),
            "resolved_user_decisions": all_resolved_user_decisions,
            # Governance is part of the immutable task contract for every
            # governed gate, not just the two lifecycle review gates.  The
            # dispatch assignment may compact this projection, but the full
            # policy/pipeline/manifest basis remains digest-bound here.
            "governance_context": governance_context,
        }
        task_contract_artifact = store_immutable_artifact(
            task_dir, state["task_id"], kind="task_contract",
            title=task_contract_relative, mime_type="application/json",
            content=json.dumps(task_contract, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            export_path=task_contract_relative,
        )
        task_contract_digest = str(task_contract_artifact["digest_sha256"])
        task_contract_job = enqueue_projection(
            root=root, task_id=state["task_id"],
            artifact_id=task_contract_artifact["artifact_ref"],
            projection_type="task_contract", export_path=task_contract_relative,
            required=True,
        )
        package["task_contract"] = {
            "schema": "cortex/task-contract-ref/v1",
            "artifact_ref": task_contract_artifact["artifact_ref"],
            "artifact_path": str(task_contract_path),
            "digest_sha256": task_contract_digest,
            "byte_size": task_contract_artifact["byte_size"],
            "read_required": True,
        }
        intent_path = Path(str(package["user_intent"]["artifact_path"]))
        intent_digest = str(package["user_intent"].get("digest_sha256") or "")
        intent_jobs = [
            job for job in list_projection_jobs(root, task_id=state["task_id"], limit=100)
            if job.get("projection_type") == "user_intent"
            and job.get("artifact_id") == package["user_intent"].get("artifact_ref")
        ]
        if len(intent_jobs) != 1:
            raise ValueError("exactly one immutable user-intent projection is required before dispatch")
        intent_job = intent_jobs[0]
        if isinstance(params.get("plan_unit"), dict):
            # ``plan_unit`` is server-derived by the approved-plan compiler,
            # not an arbitrary worker field.  It has already passed
            # the planning schema and is the canonical implementation input.
            # Do not run it through the general diagnostic sanitizer: that
            # helper shortens every scalar/list for log-safe projections,
            # which silently changed the full immutable plan the worker was
            # authorized to read.  JSON canonicalization gives this artifact
            # a detached value without altering its approved content.
            try:
                compiled_plan = json.loads(json.dumps(
                    params["plan_unit"], ensure_ascii=False, sort_keys=True,
                ))
            except (TypeError, ValueError) as exc:
                raise ValueError("compiled plan unit must be JSON-serializable") from exc
            compiled_relative = f"planning/compiled/{attempt_id}.json"
            compiled_plan_path = _contained_path(
                task_dir, task_dir / compiled_relative, "compiled plan unit"
            )
            compiled_artifact = store_immutable_artifact(
                task_dir,
                state["task_id"],
                kind="compiled_plan_unit",
                title=compiled_relative,
                mime_type="application/json",
                content=json.dumps(compiled_plan, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
                export_path=compiled_relative,
            )
            compiled_plan_digest = str(compiled_artifact["digest_sha256"])
            compiled_plan_job = enqueue_projection(
                root=root,
                task_id=state["task_id"],
                artifact_id=compiled_artifact["artifact_ref"],
                projection_type="compiled_plan_unit",
                export_path=compiled_relative,
                required=True,
            )
            package["plan_unit"] = {
                "schema": "cortex/compiled-plan-unit-ref/v1",
                "plan_revision": compiled_plan.get("plan_revision"),
                "source_result_ref": compiled_plan.get("source_result_ref"),
                "artifact_ref": compiled_artifact["artifact_ref"],
                "artifact_path": str(compiled_plan_path),
                "digest_sha256": compiled_plan_digest,
                "byte_size": compiled_artifact["byte_size"],
                "microtask_count": len(compiled_plan.get("microtasks") or []),
                "package_count": len(compiled_plan.get("package_ids") or []),
                # The complete package id list remains inside the immutable
                # compiled-plan artifact.  A digest keeps the compact
                # briefing auditable without making plan cardinality consume
                # its transport budget.
                "package_ids_digest": digest_text(canonical_json.dumps(
                    [str(item) for item in compiled_plan.get("package_ids") or []],
                )),
                "read_required": True,
            }
        package["intent_clarification_required"] = bool(task_definition.get("intent_clarification_required", False))
        package["intent_clarification_reason"] = redact(
            task_definition.get("intent_clarification_reason", ""), 500
        ) or None
        full_briefing = host_spawn_prompt(agent, package)
        briefing_bytes = len(full_briefing.encode("utf-8"))
        package["briefing_bytes"] = briefing_bytes
        spawn_requested_at = now()
        spawn_lease_expires_at = (
            datetime.now(timezone.utc) + timedelta(minutes=10)
        ).isoformat()
        package["spawn_requested_at"] = spawn_requested_at
        package["spawn_lease_expires_at"] = spawn_lease_expires_at
        package["lifecycle_status"] = "awaiting_spawn_ack"
        briefing_artifact = store_immutable_artifact(
            task_dir, state["task_id"], kind="dispatch_briefing", title=briefing_file,
            mime_type="text/markdown", content=full_briefing, export_path=briefing_file,
        )
        # The immutable artifact and outbox row are committed by their own
        # SQLite transactions before materialization begins.  The export is
        # never written through a direct filesystem writer.
        briefing_digest = str(briefing_artifact["digest_sha256"])
        # Required dispatch exports are an outbox barrier.  The task attempt,
        # canonical briefing artifact, and its intent must commit before a
        # filesystem worker can see or materialize the projection.  Do not
        # call the materializer from this state-lock transaction.
        briefing_job = enqueue_projection(
            root=ledger_root(params), task_id=state["task_id"],
            artifact_id=briefing_artifact["artifact_ref"],
            projection_type="dispatch_briefing", export_path=briefing_file,
            required=True,
        )
        package["briefing_digest"] = briefing_digest
        package["briefing_artifact_ref"] = briefing_artifact["artifact_ref"]
        spawn_request["dispatch_ref"] = dispatch_ref
        spawn_request["briefing_file"] = briefing_file
        spawn_request["briefing_path"] = str(briefing_path)
        spawn_request["briefing_digest"] = briefing_digest
        spawn_request["message"] = host_spawn_bootstrap(
            agent, briefing_path, briefing_digest, dispatch_ref, state["task_id"], attempt_id, project_root,
            intent_path=package["user_intent"]["artifact_path"],
            intent_digest=package["user_intent"]["digest_sha256"],
            plan_unit_path=(package.get("plan_unit") or {}).get("artifact_path"),
            plan_unit_digest=(package.get("plan_unit") or {}).get("digest_sha256"),
            task_contract_path=(package.get("task_contract") or {}).get("artifact_path"),
            task_contract_digest=(package.get("task_contract") or {}).get("digest_sha256"),
        )
        if visible_thread:
            # create_thread calls this field `prompt`; retaining `message`
            # keeps the package readable by existing coordinator adapters.
            spawn_request["prompt"] = spawn_request["message"]
            spawn_request["title"] = display_name
        # The package and state are both durable.  Retain only the logical
        # dispatch contract there; absolute briefing paths and the rendered
        # host prompt are recreated after a restart/host-store relocation.
        package["spawn_request"] = _durable_spawn_request(spawn_request)
        _write_delegation_package(task_dir, state["task_id"], attempt_id, package)
        state["attempts"].append({"attempt_id": attempt_id, "gate": gate, "agent": agent, "profile": agent, "display_name": display_name, "dispatch_ref": dispatch_ref, "briefing_file": briefing_file, "briefing_digest": briefing_digest, "briefing_artifact_ref": briefing_artifact["artifact_ref"], "plan_unit_file": compiled_relative, "plan_unit_digest": compiled_plan_digest, "spawn_request": _durable_spawn_request(spawn_request), **route, "luna_fallback": luna_fallback, "strategy": package["strategy"], "ownership": package["ownership"], "result_baseline_ref": result_baseline_ref, "result_baseline_digest": result_baseline.get("digest"), "allowed_paths": package["allowed_paths"], "acceptance_criteria": package["acceptance_criteria"], "verification": package["verification"], "context_files": package["context_files"], "knowledge_index_files": knowledge_index_files, "context_result_refs": context_result_refs, "visibility": package["visibility"], "user_facing": visible_thread, "user_owned_thread": visible_thread, "thread_environment": thread_environment, "return_route": "main_chat", "facade_managed": facade_managed, "orchestration_wave_id": orchestration_wave_id, "orchestration_delegation_key": orchestration_delegation_key, "status": AWAITING_HOST_SPAWN, "lifecycle_status": "awaiting_spawn_ack", "spawn_requested_at": spawn_requested_at, "spawn_lease_expires_at": spawn_lease_expires_at, "parallel": bool(params.get("parallel", False)), "evidence_ids": [], "created_at": now()})
        db_put_worker_session(ledger_root(params), {
            "task_id": state["task_id"],
            "attempt_id": attempt_id,
            "host_task_name": task_name,
            "host_tool": str(spawn_request.get("host_tool") or "spawn_agent"),
            "status": "awaiting_spawn",
            "resumable": True,
            "started_at": spawn_requested_at,
        })
        save_state(task_dir, task_dir / "state.sqlite", state, "delegation", f"{gate} → {agent} ({attempt_id})")
        prepared = {
            "delegation_ref": f"dispatch:{attempt_id}",
            "briefing_file": str(briefing_path),
            "briefing_digest": briefing_digest,
            "dispatch_ref": dispatch_ref,
            "attempt_id": attempt_id,
            "spawn_request": spawn_request,
            "state": state,
            "gate_correction": ({"requested": requested_gate, "used": gate} if requested_gate != gate else None),
            "revision_correction": revision_correction,
            "receipt_correction": receipt_correction,
            "agent_correction": agent_correction,
            "task_kind_correction": ({"requested": requested_task_kind or None, "used": task_kind} if requested_task_kind != task_kind else None),
            "risk_correction": ({"requested": requested_risk or None, "used": risk} if requested_risk != risk else None),
        }

    # This must remain outside ``state_lock``: state_lock owns a re-entrant
    # SQLite transaction, whereas projection materialization performs fsync
    # and replace operations on the filesystem.  A failure is persisted as a
    # recoverable outbox failure and deliberately returns no spawn request.
    projection_key = str(briefing_job["projection_key"])
    active_projection_key = projection_key
    try:
        _ensure_briefing_task_directory(briefing_path.parent.parent)
        if intent_job is not None and intent_path is not None and intent_digest:
            active_projection_key = str(intent_job["projection_key"])
            intent_materialized = (
                intent_job
                if intent_job.get("status") == "ready"
                else materialize_job(root, {**intent_job}, worker_id=f"intent-{dispatch_ref}")
            )
            if (
                intent_materialized.get("status") != "ready"
                or str(intent_materialized.get("materialized_digest") or "") != intent_digest
                or not intent_path.is_file()
                or hashlib.sha256(intent_path.read_bytes()).hexdigest() != intent_digest
            ):
                raise ValueError("required immutable user-intent projection is not ready")
            intent_path.chmod(0o400)
        if compiled_plan_job is not None and compiled_plan_path is not None and compiled_plan_digest is not None:
            active_projection_key = str(compiled_plan_job["projection_key"])
            compiled_materialized = materialize_job(
                root, {**compiled_plan_job}, worker_id=f"compiled-plan-{dispatch_ref}",
            )
            if (
                compiled_materialized.get("status") != "ready"
                or str(compiled_materialized.get("materialized_digest") or "") != compiled_plan_digest
                or hashlib.sha256(compiled_plan_path.read_bytes()).hexdigest() != compiled_plan_digest
            ):
                raise ValueError("required compiled plan projection is not ready")
            compiled_plan_path.chmod(0o400)
        if task_contract_job is not None and task_contract_path is not None and task_contract_digest is not None:
            active_projection_key = str(task_contract_job["projection_key"])
            contract_materialized = materialize_job(
                root, {**task_contract_job}, worker_id=f"task-contract-{dispatch_ref}",
            )
            if (
                contract_materialized.get("status") != "ready"
                or str(contract_materialized.get("materialized_digest") or "") != task_contract_digest
                or not task_contract_path.is_file()
                or hashlib.sha256(task_contract_path.read_bytes()).hexdigest() != task_contract_digest
            ):
                raise ValueError("required immutable task-contract projection is not ready")
            task_contract_path.chmod(0o400)
        active_projection_key = projection_key
        materialized = materialize_job(
            root, {**briefing_job}, worker_id=f"dispatch-{dispatch_ref}",
        )
        if (
            materialized.get("status") != "ready"
            or str(materialized.get("materialized_digest") or "") != briefing_digest
        ):
            raise ValueError("required dispatch briefing projection is not ready")
        payload = briefing_path.read_bytes()
        if hashlib.sha256(payload).hexdigest() != briefing_digest:
            raise ValueError("required dispatch briefing projection digest is invalid")
        briefing_path.chmod(0o400)
    except Exception as exc:
        try:
            fail_projection_job(root, active_projection_key, str(exc))
        except Exception:
            # Preserve the materialization failure; a lease that cannot be
            # marked failed is still recoverable when it expires.
            pass
        _mark_projection_failure(params, attempt_id, exc)
        raise
    return prepared
