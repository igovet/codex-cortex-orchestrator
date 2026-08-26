"""Verification evidence identities, provenance, and obligation policy.

Cortex can observe workspace manifests and native lifecycle events.  It does
not observe the worker's command, browser, console, or network activity.  Such
semantic checks therefore remain worker attestations even after the server
validates their shape and binds their storage receipt to an immutable result.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from typing import Any


VERIFICATION_EVIDENCE_SCHEMA = "cortex/verification-evidence/v3"
VERIFICATION_KINDS = frozenset({
    "functional_browser",
    "responsive_layout",
    "keyboard_accessibility",
    "console_clean",
    "local_only_network",
    "manifest_reconciliation",
})
WORKER_VERIFICATION_KINDS = frozenset(
    VERIFICATION_KINDS - {"manifest_reconciliation"}
)
_SHA256_RE = re.compile(r"(?:sha256:)?([0-9a-f]{64})")
_MACHINE_FACT_RE = re.compile(r"(?:^|[\s;,])([a-z][a-z0-9_]*)=([^\s;,]+)")
_KIND_FACT = {
    "functional_browser": ("passed_tests", 1),
    "responsive_layout": ("viewports", 2),
    "keyboard_accessibility": ("keyboard_checks", 1),
    "console_clean": ("console_errors", 0),
    "local_only_network": ("external_requests", 0),
}


def sha256_hex(value: object) -> str:
    match = _SHA256_RE.fullmatch(str(value or "").strip().lower())
    return match.group(1) if match else ""


def required_verification_kinds(phase_kind: object, operation_kind: object) -> tuple[str, ...]:
    """Return the server policy obligations for one compiled occurrence."""
    if str(operation_kind or "") not in {"verify", "close"}:
        return ()
    phase = str(phase_kind or "").strip()
    required = {"manifest_reconciliation"}
    if phase == "qa":
        required.update({
            "functional_browser", "responsive_layout", "console_clean", "local_only_network",
        })
    elif phase == "accessibility":
        required.add("keyboard_accessibility")
    elif phase in {"performance", "ux"}:
        required.add("responsive_layout")
    return tuple(sorted(required))


def worker_machine_evidence(
    verification_kind: object,
    text: object,
) -> tuple[dict[str, Any], str]:
    """Validate one language-neutral machine fact and derive its digest.

    Workers may add arbitrary Unicode context, but a prose claim alone is not
    evidence.  The small key=value grammar is deliberately flat and stable so
    the backend can validate the observation without trusting wording or a
    worker-supplied digest.
    """
    kind = str(verification_kind or "").strip()
    if kind not in WORKER_VERIFICATION_KINDS:
        raise ValueError("verification_kind is not worker-observable")
    exact_text = str(text or "").strip()
    if not exact_text:
        raise ValueError("worker verification attestation text is required")
    facts = {
        key: value for key, value in _MACHINE_FACT_RE.findall(exact_text)
    }
    if facts.get("status") != "passed":
        raise ValueError("worker verification attestation requires status=passed")
    metric, threshold = _KIND_FACT[kind]
    raw_value = facts.get(metric)
    if raw_value is None or re.fullmatch(r"[0-9]+", raw_value) is None:
        raise ValueError(
            f"{kind} verification requires the integer machine fact {metric}=<number>"
        )
    value = int(raw_value)
    if (threshold == 0 and value != 0) or (threshold > 0 and value < threshold):
        comparator = "0" if threshold == 0 else f">={threshold}"
        raise ValueError(f"{metric} must be {comparator} for a passed observation")
    evidence = {
        "schema": "cortex/verification-machine-evidence/v1",
        "verification_kind": kind,
        "status": "passed",
        "metric": metric,
        "value": value,
        "text": exact_text,
    }
    encoded = json.dumps(
        evidence, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return evidence, hashlib.sha256(encoded).hexdigest()


def _exact_assignment_binding(
    attempt: Mapping[str, Any],
    task_id: str,
    *,
    task_revision: object | None = None,
) -> dict[str, Any]:
    binding = {
        "task_id": str(task_id or "").strip(),
        "attempt_id": str(attempt.get("attempt_id") or "").strip(),
        # dispatch_ref is the server-issued assignment capability reference in
        # the current runtime.  The wire contract calls this assignment_ref.
        "assignment_ref": str(attempt.get("dispatch_ref") or "").strip(),
        "phase_ref": str(attempt.get("phase_ref") or "").strip(),
        "wave_ref": str(attempt.get("wave_ref") or "").strip(),
        "plan_revision": attempt.get("plan_revision"),
        "plan_digest": sha256_hex(attempt.get("plan_digest")),
        "task_revision": task_revision,
    }
    if (
        not all(binding[key] for key in ("task_id", "attempt_id", "assignment_ref", "phase_ref", "wave_ref", "plan_digest"))
        or isinstance(binding["plan_revision"], bool)
        or not isinstance(binding["plan_revision"], int)
        or binding["plan_revision"] < 1
        or isinstance(binding["task_revision"], bool)
        or not isinstance(binding["task_revision"], int)
        or binding["task_revision"] < 1
    ):
        raise ValueError("verification evidence requires exact compiled assignment and plan identity")
    return binding


def pending_verification_evidence_payload(
    *,
    task_id: str,
    attempt: Mapping[str, Any],
    verification_kind: str,
    verification_id: str,
    task_revision: int,
    workspace_digest: str,
    server_receipt: Mapping[str, Any],
    tests: list[dict[str, Any]],
) -> dict[str, Any]:
    if verification_kind not in VERIFICATION_KINDS:
        raise ValueError("verification_kind is unsupported")
    binding = _exact_assignment_binding(
        attempt, task_id, task_revision=task_revision,
    )
    binding["workspace_digest"] = sha256_hex(workspace_digest)
    if not binding["workspace_digest"]:
        raise ValueError("verification evidence requires an exact workspace digest")
    receipt = dict(server_receipt)
    source = str(receipt.get("source") or "")
    evidence_digest = sha256_hex(receipt.get("evidence_digest"))
    if receipt.get("status") != "recorded" or not evidence_digest:
        raise ValueError("verification evidence requires a valid server receipt")
    if source == "server_manifest":
        evidence_class = "server_observed"
        if (
            receipt.get("schema") != "cortex/server-observation-receipt/v1"
            or receipt.get("receipt_scope") != "manifest_reconciliation"
            or verification_kind != "manifest_reconciliation"
            or evidence_digest != binding["workspace_digest"]
        ):
            raise ValueError("server manifest evidence does not match the observed workspace")
    elif source == "worker_attestation":
        evidence_class = "worker_attested"
        if (
            receipt.get("schema") != "cortex/server-storage-receipt/v1"
            or receipt.get("receipt_scope") != "identity_digest_storage"
        ):
            raise ValueError("worker attestation requires an identity/digest/storage receipt")
        machine = receipt.get("machine_evidence")
        if not isinstance(machine, Mapping):
            raise ValueError("worker attestation requires canonical machine evidence")
        checked, checked_digest = worker_machine_evidence(
            verification_kind, machine.get("text"),
        )
        if checked != dict(machine) or checked_digest != evidence_digest:
            raise ValueError("worker attestation digest is invalid")
    else:
        raise ValueError("verification evidence receipt source is unsupported")
    return {
        "schema": VERIFICATION_EVIDENCE_SCHEMA,
        "evidence_class": evidence_class,
        "binding_status": "pending_result",
        "verification_kind": verification_kind,
        "verification_id": str(verification_id),
        "binding": binding,
        "server_receipt": receipt,
        "tests": [dict(item) for item in tests],
    }


def bind_verification_evidence_payload(
    *,
    source_event_ref: str,
    pending: Mapping[str, Any],
    task_id: str,
    attempt: Mapping[str, Any],
    result: Mapping[str, Any],
) -> dict[str, Any] | None:
    """Return a result-bound observation, or None for any identity mismatch."""
    if (
        pending.get("schema") != VERIFICATION_EVIDENCE_SCHEMA
        or pending.get("evidence_class") not in {"worker_attested", "server_observed"}
        or pending.get("binding_status") != "pending_result"
        or str(pending.get("verification_kind") or "") not in VERIFICATION_KINDS
        or not str(source_event_ref or "").strip()
    ):
        return None
    binding = pending.get("binding")
    if not isinstance(binding, Mapping):
        return None
    try:
        metadata = result.get("metadata")
        result_task_revision = (
            metadata.get("task_revision") if isinstance(metadata, Mapping) else None
        )
        if result_task_revision is None and str(attempt.get("operation_kind") or "") == "close":
            result_task_revision = attempt.get("assignment_task_revision")
        expected = _exact_assignment_binding(
            attempt, task_id, task_revision=result_task_revision,
        )
    except ValueError:
        return None
    if any(binding.get(key) != value for key, value in expected.items()):
        return None
    workspace = result.get("workspace_observation")
    if not isinstance(workspace, Mapping) or workspace.get("complete") is not True:
        return None
    workspace_digest = sha256_hex(workspace.get("current_digest_sha256"))
    result_digest = sha256_hex(result.get("content_digest"))
    result_ref = str(result.get("result_ref") or "").strip()
    if (
        not workspace_digest
        or binding.get("workspace_digest") != workspace_digest
        or not result_digest
        or not result_ref
        or str(result.get("task_id") or "") != str(task_id)
        or str(result.get("attempt_id") or "") != expected["attempt_id"]
    ):
        return None
    return {
        **dict(pending),
        "binding_status": "bound",
        "source_event_ref": str(source_event_ref),
        "binding": {
            **dict(binding),
            "result_ref": result_ref,
            "result_digest": result_digest,
        },
    }


def validated_bound_kind(
    event: Mapping[str, Any],
    *,
    task_id: str,
    attempt: Mapping[str, Any],
    result: Mapping[str, Any],
) -> str | None:
    payload = event.get("payload")
    if not isinstance(payload, Mapping):
        return None
    evidence_class = payload.get("evidence_class")
    expected_envelope = (
        ("verification_observed", "cortex")
        if evidence_class == "server_observed"
        else ("verification_claimed", "worker")
        if evidence_class == "worker_attested"
        else None
    )
    if (
        expected_envelope is None
        or (event.get("event_type"), event.get("actor")) != expected_envelope
        or payload.get("binding_status") != "bound"
    ):
        return None
    source_ref = str(payload.get("source_event_ref") or "").strip()
    if not source_ref:
        return None
    # Re-evaluate the exact binding by converting the immutable bound receipt
    # back to its pending core.  No caller-provided count or prose participates.
    pending = dict(payload)
    pending["binding_status"] = "pending_result"
    pending["binding"] = {
        key: value for key, value in dict(payload.get("binding") or {}).items()
        if key not in {"result_ref", "result_digest"}
    }
    rebound = bind_verification_evidence_payload(
        source_event_ref=source_ref,
        pending=pending,
        task_id=task_id,
        attempt=attempt,
        result=result,
    )
    if rebound is None or rebound != dict(payload):
        return None
    return str(payload.get("verification_kind"))


def validated_bound_evidence(
    events: list[dict[str, Any]],
    *,
    task_id: str,
    attempt: Mapping[str, Any],
    result: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Return result-bound evidence without changing its original provenance."""
    pending_by_ref = {
        str(event.get("event_ref") or ""): event
        for event in events
        if isinstance(event.get("payload"), Mapping)
        and event["payload"].get("schema") == VERIFICATION_EVIDENCE_SCHEMA
        and event["payload"].get("binding_status") == "pending_result"
        and (
            (
                event["payload"].get("evidence_class") == "server_observed"
                and event.get("event_type") == "verification_observed"
                and event.get("actor") == "cortex"
            )
            or (
                event["payload"].get("evidence_class") == "worker_attested"
                and event.get("event_type") == "verification_claimed"
                and event.get("actor") == "worker"
            )
        )
    }
    metadata = result.get("metadata")
    raw_enumerated_refs = (
        metadata.get("verification_evidence_refs")
        if isinstance(metadata, Mapping) else None
    )
    if (
        not isinstance(raw_enumerated_refs, list)
        or any(not isinstance(item, str) or not item.strip() for item in raw_enumerated_refs)
        or len(raw_enumerated_refs) != len(set(raw_enumerated_refs))
    ):
        return []
    enumerated_refs = set(raw_enumerated_refs)
    validated: list[dict[str, Any]] = []
    for event in events:
        payload = event.get("payload")
        if not isinstance(payload, Mapping):
            continue
        source_ref = str(payload.get("source_event_ref") or "")
        source = pending_by_ref.get(source_ref)
        if source is None or source_ref not in enumerated_refs:
            continue
        expected = bind_verification_evidence_payload(
            source_event_ref=source_ref,
            pending=source["payload"],
            task_id=task_id,
            attempt=attempt,
            result=result,
        )
        kind = validated_bound_kind(
            event, task_id=task_id, attempt=attempt, result=result,
        )
        if expected is None or expected != dict(payload) or kind is None:
            continue
        binding = dict(payload.get("binding") or {})
        receipt = payload.get("server_receipt")
        evidence_digest = (
            sha256_hex(receipt.get("evidence_digest"))
            if isinstance(receipt, Mapping) else ""
        )
        if not evidence_digest:
            continue
        validated.append({
            "event_ref": str(event.get("event_ref") or ""),
            "source_event_ref": source_ref,
            "evidence_class": str(payload.get("evidence_class") or ""),
            "verification_kind": kind,
            "attempt_id": binding["attempt_id"],
            "assignment_ref": binding["assignment_ref"],
            "phase_ref": binding["phase_ref"],
            "wave_ref": binding["wave_ref"],
            "plan_revision": binding["plan_revision"],
            "plan_digest": binding["plan_digest"],
            "result_ref": binding["result_ref"],
            "result_digest": binding["result_digest"],
            "workspace_digest": binding["workspace_digest"],
            "evidence_digest": evidence_digest,
        })
    return validated
