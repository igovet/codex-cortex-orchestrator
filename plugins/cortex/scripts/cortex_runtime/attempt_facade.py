"""Worker-facing adapters for the database-centric attempt protocol.

The worker supplies semantic facts.  This facade resolves its already-issued
attempt, records the canonical result/event rows, verifies machine-side read
receipts, derives the workspace observation, and exposes only a regenerated
non-authoritative AttemptResult read view.
"""
from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

import cortex as _runtime

from cortex_runtime import attempt_protocol


_CLOSURE_GATES = {"review", "governance_activation", "governance_close", "close"}
_ACTIVE_ATTEMPT_STATUSES = {_runtime.AWAITING_HOST_SPAWN, "running"}
_PUBLIC_IDENTITY_FIELDS = {"project_root", "task_id", "attempt_id", "profile"}


def _coordinator_continuation(
    task_dir: Any,
    state: Mapping[str, Any],
    source: Mapping[str, Any],
    result_ref: str,
    *,
    lifecycle_status: object,
) -> tuple[dict[str, Any] | None, str | None]:
    """Return the sole server-derived success payload for the current wave.

    A coordinator may read historical predecessor results at any time. Only a
    finalized result owned by an active slot is safe to turn into a public
    continuation payload: deriving the relative step from the current plan
    avoids an LLM inferring the next wave number or copying a view reference.
    """
    if lifecycle_status != attempt_protocol.LIFECYCLE_COMPLETED:
        return None, "attempt_result_not_finalized"
    if source.get("invalidated"):
        return None, "attempt_result_not_current"
    plan = _runtime._load_orchestrate_plan(task_dir, dict(state))
    wave = _runtime._wave_for_gates(plan, _runtime.active_gates(dict(state)))
    if not isinstance(wave, Mapping):
        return None, "no_active_wave"
    # ``attempt_ids`` is the server-owned slot order.  In particular, do not
    # turn the result that happened to be read last into a singleton advance
    # for a parallel wave.  Every member must have its own final canonical
    # result and its own dispatch identity before the coordinator receives one
    # atomic continuation payload.
    wave_attempt_ids = [
        str(item).strip()
        for item in (wave.get("attempt_ids") or [])
        if str(item).strip()
    ]
    if not wave_attempt_ids:
        return None, "active_wave_attempts_unavailable"
    if len(set(wave_attempt_ids)) != len(wave_attempt_ids):
        return None, "active_wave_attempt_identity_conflict"
    source_attempt_id = str(source.get("attempt_id") or "").strip()
    if not source_attempt_id or source_attempt_id not in wave_attempt_ids:
        return None, "attempt_result_not_current"
    if source.get("status") not in _ACTIVE_ATTEMPT_STATUSES:
        return None, "attempt_result_not_current"
    attempts = {
        str(candidate.get("attempt_id") or "").strip(): candidate
        for candidate in state.get("attempts") or []
        if isinstance(candidate, Mapping)
    }
    wave_results: list[dict[str, Any]] = []
    result_refs: list[str] = []
    dispatch_refs: list[str] = []
    root = _runtime._task_document_root(task_dir, state["task_id"])
    for slot, attempt_id in enumerate(wave_attempt_ids, 1):
        attempt = attempts.get(attempt_id)
        if (
            not isinstance(attempt, Mapping)
            or attempt.get("invalidated")
            or attempt.get("status") not in _ACTIVE_ATTEMPT_STATUSES
        ):
            return None, "parallel_wave_attempt_not_current"
        dispatch_ref = str(attempt.get("dispatch_ref") or "").strip()
        result_ref = str(attempt.get("attempt_result_ref") or "").strip()
        if not dispatch_ref or not result_ref:
            return None, "parallel_wave_results_pending"
        canonical = attempt_protocol.get_attempt_result(
            root, task_id=str(state["task_id"]), attempt_id=attempt_id,
        )
        if (
            canonical is None
            or str(canonical.get("attempt_id") or "") != attempt_id
            or str(canonical.get("result_ref") or "") != result_ref
            or canonical.get("lifecycle_status") != attempt_protocol.LIFECYCLE_COMPLETED
        ):
            return None, "parallel_wave_results_pending"
        dispatch_refs.append(dispatch_ref)
        result_refs.append(result_ref)
        result: dict[str, Any] = {"attempt_result_ref": result_ref}
        if len(wave_attempt_ids) > 1:
            result["worker"] = slot
        wave_results.append(result)
    if len(set(dispatch_refs)) != len(dispatch_refs):
        return None, "parallel_wave_dispatch_identity_conflict"
    if len(set(result_refs)) != len(result_refs):
        return None, "parallel_wave_result_identity_conflict"
    wave_match = _runtime.re.search(r"(\d+)$", str(wave.get("wave_id") or ""))
    if wave_match is None:
        return None, "active_step_unavailable"
    return {
        "task_id": str(state["task_id"]),
        "step": int(wave_match.group(1)),
        "results": wave_results,
    }, None


def _worker_context(params: Mapping[str, Any]) -> tuple[Any, Any, dict[str, Any], dict[str, Any], str]:
    for field in _PUBLIC_IDENTITY_FIELDS:
        if not str(params.get(field) or "").strip():
            raise ValueError(f"{field} is required; copy the exact value from this worker's Cortex briefing")
    project = _runtime.select_project_root({"project_root": params["project_root"]})
    task_id = _runtime.safe_id(str(params["task_id"]))
    attempt_id = _runtime.safe_id(str(params["attempt_id"]))
    profile = _runtime.canonical_profile(params["profile"])
    _, task_dir, state = _runtime.load_state(task_id, {"project_root": str(project)})
    attempt = _runtime._attempt(state, attempt_id)
    if attempt.get("invalidated"):
        raise ValueError("attempt is invalidated")
    if attempt.get("profile") != profile:
        raise ValueError("profile does not match the exact dispatched worker")
    if attempt.get("status") not in _ACTIVE_ATTEMPT_STATUSES:
        existing = attempt_protocol.get_attempt_result(
            _runtime.ledger_root({"project_root": str(project)}),
            task_id=task_id,
            attempt_id=attempt_id,
        )
        if existing is None:
            raise ValueError("attempt is not active and has no canonical result")
    return project, task_dir, state, attempt, profile


def _public_failure(operation: str, exc: Exception, *, finalization: bool = False) -> dict[str, Any]:
    message = _runtime.redact(str(exc), 1000)
    code = "attempt_finalization_pending" if finalization else f"{operation}_invalid"
    return {
        "schema": _runtime.PUBLIC_ORCHESTRATION_SCHEMA,
        "ok": False,
        "outcome": "finalization_pending" if finalization else "needs_correction",
        "code": code,
        "diagnostics": [{"code": code, "message": message}],
        "retryable": True,
        "attempt_budget_consumed": False,
        "worker_replacement_authorized": False,
        "next_action": (
            "Retry complete_attempt on this same completed attempt; Cortex retained the canonical AttemptResult and events. "
            "Do not spawn, request, or authorize a replacement worker."
            if finalization else
            f"Correct only the named {operation} field and retry this same attempt."
        ),
    }


def record_attempt_event(params: dict[str, Any]) -> dict[str, Any]:
    """Persist one bounded semantic checkpoint for the active worker."""
    try:
        allowed = _PUBLIC_IDENTITY_FIELDS | {"event_type", "payload", "event_key"}
        unknown = sorted(set(params) - allowed)
        if unknown:
            raise ValueError("unsupported record_attempt_event fields: " + ", ".join(unknown))
        project, _task_dir, state, attempt, profile = _worker_context(params)
        if attempt.get("status") not in _ACTIVE_ATTEMPT_STATUSES:
            raise ValueError("attempt event stream is closed")
        event_type = str(params.get("event_type") or "").strip()
        if "payload" not in params:
            raise ValueError("payload is required")
        result = attempt_protocol.record_attempt_event(
            _runtime.ledger_root({"project_root": str(project)}),
            task_id=state["task_id"],
            attempt_id=str(attempt["attempt_id"]),
            event_type=event_type,
            payload=params["payload"],
            event_key=(str(params.get("event_key") or "").strip() or None),
        )
        return {
            "schema": _runtime.PUBLIC_ORCHESTRATION_SCHEMA,
            "ok": True,
            "outcome": "attempt_event_recorded",
            "event_ref": result["event"]["event_ref"],
            "event_type": result["event"]["event_type"],
            "attempt_id": attempt["attempt_id"],
            "profile": profile,
            "idempotent": bool(result.get("idempotent")),
            "next_action": "Continue this attempt; checkpoint another material fact when useful, then call complete_attempt once.",
        }
    except (ValueError, TypeError, OSError) as exc:
        return _public_failure("record_attempt_event", exc)


def _path_is_allowed(path: str, allowed_paths: Sequence[object]) -> bool:
    for raw in allowed_paths:
        allowed = str(raw or "").strip().rstrip("/")
        if allowed == "." or path == allowed or (allowed and path.startswith(allowed + "/")):
            return True
    return False


def _workspace_observation(project: Any, task_dir: Any, state: Mapping[str, Any], attempt: Mapping[str, Any]) -> dict[str, Any]:
    baseline = _runtime.attempt_manifest_baseline(task_dir, dict(attempt))
    current = _runtime.capture_project_manifest(project, policy=baseline.get("policy"))
    comparison = _runtime.compare_manifests(baseline, current)
    changed = list(comparison.get("changed_paths") or [])
    allowed = attempt.get("allowed_paths") if isinstance(attempt.get("allowed_paths"), list) else []
    peers = [
        item for item in state.get("attempts", [])
        if isinstance(item, dict)
        and item.get("attempt_id") != attempt.get("attempt_id")
        and not item.get("invalidated")
        and item.get("status") in _ACTIVE_ATTEMPT_STATUSES
    ]
    peer_overlap = any(
        any(_path_is_allowed(path, peer.get("allowed_paths") or []) for path in changed)
        for peer in peers
    )
    safe = bool(comparison.get("complete")) and not peer_overlap and all(
        _path_is_allowed(path, allowed) for path in changed
    )
    return {
        "baseline_ref": attempt.get("result_baseline_ref"),
        "current_digest_sha256": str(current.get("digest") or ""),
        "complete": bool(comparison.get("complete")),
        "safe_to_attribute": safe,
        "changed_files": changed if safe else [],
    }


def _receipt_guard(root: Any, state: Mapping[str, Any], attempt: Mapping[str, Any]) -> dict[str, Any]:
    receipts = attempt_protocol.attempt_receipts(
        root,
        task_id=str(state["task_id"]),
        attempt_id=str(attempt["attempt_id"]),
    )
    missing: list[str] = []
    if not receipts.get("briefing_receipt"):
        missing.append("briefing_read")
    required = {str(item) for item in attempt.get("context_result_refs") or []}
    observed = {str(item) for item in (receipts.get("predecessor_receipts") or {})}
    missing.extend("predecessor:" + item for item in sorted(required - observed))
    if missing:
        raise ValueError("server read receipts are incomplete: " + ", ".join(missing))
    return receipts


def _mark_attempt(
    project: Any,
    task_id: str,
    attempt_id: str,
    *,
    lifecycle_status: str,
    result: Mapping[str, Any],
    terminal_status: str | None = None,
    finalization_error: str | None = None,
) -> None:
    """Project one canonical result without leaving its native worker live.

    The task projection intentionally remains ``running`` until the
    coordinator accepts the exact result through ``continue_orchestration``.
    That is a gate-consumption state, not evidence that the native worker is
    still runnable.  Terminal AttemptResult lifecycles therefore reconcile the
    server-owned worker-session row here, before the result can be surfaced to
    the coordinator.  A missing session fails closed instead of manufacturing
    a host identity or allowing a stale ``awaiting_spawn``/``running`` row to
    survive a terminal result.
    """
    root = _runtime.ledger_root({"project_root": str(project)})
    with _runtime.state_lock(root):
        _, task_dir, state = _runtime.load_state(task_id, {"project_root": str(project)})
        attempt = _runtime._attempt(state, attempt_id)
        result_lifecycle = str(result.get("lifecycle_status") or "").upper()
        terminal_lifecycles = {
            attempt_protocol.LIFECYCLE_COMPLETED,
            attempt_protocol.LIFECYCLE_BLOCKED,
            attempt_protocol.LIFECYCLE_FAILED,
        }
        if result_lifecycle in terminal_lifecycles:
            terminal_at = (
                result.get("completed_at")
                or result.get("work_completed_at")
                or _runtime.now()
            )
            _runtime.db_reconcile_terminal_worker_session(
                root,
                task_id=state["task_id"],
                attempt_id=attempt_id,
                terminated_at=str(terminal_at),
            )
            # Do not assert an observed SubagentStop: a host stop hook may
            # arrive later. This state records only the server-side terminal
            # reconciliation that prevents a durable session from being
            # recovered as live work.
            attempt["worker_session_reconciled_at"] = str(terminal_at)
            attempt["worker_session_terminal_status"] = "completed"
        attempt["lifecycle_status"] = lifecycle_status
        attempt["attempt_result_ref"] = result.get("result_ref")
        attempt["work_completed_at"] = result.get("work_completed_at")
        attempt["host_resumable"] = False
        if finalization_error:
            attempt["finalization_error_code"] = finalization_error
        else:
            attempt.pop("finalization_error_code", None)
        if terminal_status:
            attempt["status"] = terminal_status
            attempt["finalized_at"] = _runtime.now()
            attempt["finalization_reason"] = f"semantic_attempt_{terminal_status}"
        _runtime.save_state(
            task_dir,
            task_dir / "state.sqlite",
            state,
            "attempt_result_state",
            f"{attempt_id}: {lifecycle_status}",
        )


def complete_attempt(params: dict[str, Any]) -> dict[str, Any]:
    """Persist semantic completion and finalize its canonical result view."""
    project: Any = None
    state: dict[str, Any] | None = None
    attempt: dict[str, Any] | None = None
    root: Any = None
    try:
        allowed = _PUBLIC_IDENTITY_FIELDS | {
            "status", "summary", "findings", "decisions_needed", "unresolved", "claims",
            "planning",
        }
        unknown = sorted(set(params) - allowed)
        if unknown:
            raise ValueError("unsupported complete_attempt fields: " + ", ".join(unknown))
        project, task_dir, state, attempt, profile = _worker_context(params)
        plan_attempt = profile == "planner" and str(attempt.get("gate") or "") == "plan"
        if "planning" in params and not plan_attempt:
            raise ValueError("planning is supported only for planner attempts on the plan gate")
        root = _runtime.ledger_root({"project_root": str(project)})
        _receipt_guard(root, state, attempt)
        semantic_result = {
            "status": params.get("status"),
            "summary": params.get("summary"),
            "findings": params.get("findings", []),
            "decisions_needed": params.get("decisions_needed", []),
            "unresolved": params.get("unresolved", []),
            "claims": params.get("claims", []),
        }
        existing = attempt_protocol.get_attempt_result(
            root, task_id=state["task_id"], attempt_id=attempt["attempt_id"],
        )
        observation = (
            {
                **existing["workspace_observation"],
                "changed_files": existing.get("changed_files") or [],
            }
            if existing is not None else
            _workspace_observation(project, task_dir, state, attempt)
        )
        completed = attempt_protocol.complete_attempt(
            root,
            task_id=state["task_id"],
            attempt_id=attempt["attempt_id"],
            result=semantic_result,
            submission_id=(existing.get("submission_id") if existing else None),
            workspace_observation=observation,
        )
        canonical = completed["result"]
        terminal = canonical.get("result_status") or canonical["status"]
        if terminal != "completed":
            _mark_attempt(
                project, state["task_id"], attempt["attempt_id"],
                lifecycle_status=canonical["lifecycle_status"].lower(),
                result=canonical,
                terminal_status=terminal,
            )
            return {
                "schema": _runtime.PUBLIC_ORCHESTRATION_SCHEMA,
                "ok": True,
                "outcome": f"attempt_{terminal}",
                "attempt_result_ref": canonical["result_ref"],
                "dispatch_ref": attempt.get("dispatch_ref"),
                "status": terminal,
                "summary": canonical["summary"],
                "idempotent": bool(completed.get("idempotent")),
                "worker_replacement_authorized": False,
                "next_action": "Return this semantic non-success to the coordinator; Cortex retained the attempt facts.",
            }
        if plan_attempt:
            if "planning" in params:
                _runtime.materialize_planning_payload(
                    task_dir,
                    state,
                    attempt,
                    str(canonical["result_ref"]),
                    params["planning"],
                )
            else:
                current = _runtime.current_planning_manifest(task_dir)
                if not isinstance(current, dict) or current.get("source_result_ref") != canonical.get("result_ref"):
                    raise ValueError("planner plan attempts require a planning payload")
        _mark_attempt(
            project, state["task_id"], attempt["attempt_id"],
            lifecycle_status="work_completed",
            result=canonical,
        )
        if canonical["lifecycle_status"] == attempt_protocol.LIFECYCLE_WORK_COMPLETED:
            attempt_protocol.begin_attempt_finalization(
                root, task_id=state["task_id"], attempt_id=attempt["attempt_id"],
            )
        projection = attempt_protocol.build_attempt_result_view(
            root, task_id=state["task_id"], attempt_id=attempt["attempt_id"],
        )
        finalized = attempt_protocol.finalize_attempt(
            root, task_id=state["task_id"], attempt_id=attempt["attempt_id"],
        )["result"]
        _mark_attempt(
            project, state["task_id"], attempt["attempt_id"],
            lifecycle_status="result_finalized",
            result=finalized,
        )
        return {
            "schema": _runtime.PUBLIC_ORCHESTRATION_SCHEMA,
            "ok": True,
            "outcome": "attempt_completed",
            # Put the bearer lookup token first and label the generated view
            # separately.  Native parents frequently serialize this object
            # verbatim; keeping the canonical token first reduces accidental
            # selection of the non-authoritative projection ref while the
            # prompt/schema still require the field name to be copied exactly.
            "attempt_result_ref": canonical["result_ref"],
            "projection_ref": projection["projection_ref"],
            "summary": canonical["summary"],
            "idempotent": bool(completed.get("idempotent")),
            "worker_replacement_authorized": False,
            "next_action": "Return only ATTEMPT_COMPLETED, attempt_result_ref, and at most a two-sentence summary to the coordinator.",
        }
    except (ValueError, TypeError, OSError, RuntimeError, json.JSONDecodeError) as exc:
        if root is not None and state is not None and attempt is not None:
            existing = attempt_protocol.get_attempt_result(
                root, task_id=state["task_id"], attempt_id=attempt["attempt_id"],
            )
            if existing is not None and str(existing.get("result_status") or existing.get("status") or "") == "completed":
                try:
                    attempt_protocol.record_finalization_failure(
                        root,
                        task_id=state["task_id"],
                        attempt_id=attempt["attempt_id"],
                        reason_code="generated_projection_failed",
                    )
                    _mark_attempt(
                        project, state["task_id"], attempt["attempt_id"],
                        lifecycle_status="work_completed",
                        result=existing,
                        finalization_error="generated_projection_failed",
                    )
                except (ValueError, OSError, RuntimeError):
                    pass
                return _public_failure("complete_attempt", exc, finalization=True)
        return _public_failure("complete_attempt", exc)


def read_worker_result(params: dict[str, Any]) -> dict[str, Any]:
    """Read one canonical result view for the coordinator or assigned successor.

    The result reference is resolved against the selected task's attempt rows;
    no secondary artifact or editable body participates in
    authorization or recovery.
    """
    try:
        allowed = {"project_root", "task_ref", "attempt_result_ref", "attempt_id", "profile"}
        unknown = sorted(set(params) - allowed)
        if unknown:
            raise ValueError("unsupported read_worker_result fields: " + ", ".join(unknown))
        resolved = _runtime._v3_resolve_task(params, require_task_ref=True)
        if isinstance(resolved, dict):
            return resolved
        task_dir, state, _project, task_ref = resolved
        result_ref = _runtime.safe_id(str(params.get("attempt_result_ref") or ""))
        if not result_ref:
            raise ValueError("attempt_result_ref is required")
        raw_attempt_id = str(params.get("attempt_id") or "").strip()
        raw_profile = str(params.get("profile") or "").strip()
        if bool(raw_attempt_id) != bool(raw_profile):
            raise ValueError("successor worker result reads require both attempt_id and profile")
        worker_context = bool(raw_attempt_id)
        if worker_context:
            attempt_id = _runtime.safe_id(raw_attempt_id)
            attempt = _runtime._attempt(state, attempt_id)
            profile = _runtime.canonical_profile(raw_profile)
            if attempt.get("invalidated") or attempt.get("status") not in _ACTIVE_ATTEMPT_STATUSES:
                raise ValueError("successor worker result reads require an active, non-invalidated attempt")
            if attempt.get("profile") != profile:
                raise ValueError("successor worker profile does not match the delegated attempt")
            allowed_refs = {str(item) for item in attempt.get("context_result_refs") or []}
            if result_ref not in allowed_refs:
                raise ValueError("successor worker may read only predecessor result refs supplied in its dispatch")
        source = next(
            (candidate for candidate in state.get("attempts") or []
             if str(candidate.get("attempt_result_ref") or "") == result_ref),
            None,
        )
        if not isinstance(source, Mapping):
            raise ValueError("attempt_result_ref does not belong to the selected Cortex task")
        root = _runtime._task_document_root(task_dir, state["task_id"])
        view = attempt_protocol.build_attempt_result_view(
            root, task_id=state["task_id"], attempt_id=str(source["attempt_id"]),
        )
        if view["attempt_result_ref"] != result_ref:
            raise ValueError("attempt_result_ref does not match its canonical result")
        continuation, continuation_unavailable_reason = _coordinator_continuation(
            task_dir,
            state,
            source,
            result_ref,
            lifecycle_status=view.get("lifecycle_status"),
        )
        result = {
            "schema": _runtime.PUBLIC_ORCHESTRATION_SCHEMA,
            "ok": True,
            "task_ref": task_ref,
            "attempt_result_ref": result_ref,
            "phase": view["phase"],
            "profile": view["producer"],
            "result_view": view,
            "complete": True,
        }
        if worker_context:
            result["predecessor_receipt"] = attempt_protocol.record_predecessor_read(
                root, task_id=state["task_id"], attempt_id=attempt_id, predecessor_result_ref=result_ref,
            )
            result["continuation_unavailable_reason"] = continuation_unavailable_reason
            result["next_action"] = (
                "Use this canonical predecessor result as scoped evidence context. Cortex recorded the complete "
                "read receipt; distinguish worker verification claims from server verification observations."
            )
        else:
            if continuation is not None:
                result["continuation"] = continuation
                result["next_action"] = (
                    "Keep the existing task_ref, verify continuation.task_id against this task, and copy "
                    "continuation.step and continuation.results verbatim into the next continue_orchestration call. "
                    "Do not increment step or use projection_ref, formatted ref text, dispatch_ref, reason, "
                    "next_strategy, or worker for this singleton success."
                )
            else:
                result["continuation_unavailable_reason"] = continuation_unavailable_reason
                result["next_action"] = (
                    "This result is not the finalized current active worker slot, so it cannot authorize a continuation. "
                    "Use it only as canonical read context and follow the active task state."
                )
        return result
    except (ValueError, TypeError, OSError) as exc:
        return _public_failure("read_worker_result", exc)
