"""Closed P1 semantic capability and evidence-admissibility contract.

This is repository policy interpretation.  It emits bounded decisions and can
reject malformed requests before a repository-owned adapter calls an executor,
but it does not claim that an installed host invokes it or supplies token,
filesystem, process, or egress enforcement.
"""
from __future__ import annotations

import hashlib
from itertools import islice
import json
import math
from collections.abc import Mapping
import re
from pathlib import Path


SCHEMA = "capability-manifest-v1"
POLICY_VERSION = 1
PERMISSION_DENIED = "PERMISSION_DENIED"
UNVERIFIED = "unverified"
POLICY_DECLARED = "policy_declared"
POLICY_OBSERVED = "policy_observed"
HOST_ENFORCED = "host_enforced"
_IDENTIFIER = re.compile(r"[a-z0-9][a-z0-9_.:-]{0,127}\Z")
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_ROUTES = frozenset({"native_cli_qualification", "native_desktop_qualification"})
_TARGETS = frozenset({"cli", "desktop"})
_PHASES = frozenset({"bound_task"})
_CAPABILITIES = {
    "cortex.report.read": ("cortex", "read", "report", "task_bound", "allow", 1),
    "cortex.task.read": ("cortex", "read", "task", "task_bound", "allow", 1),
    "cortex.runtime.execute": ("cortex", "execute", "runtime", "task_bound", "allow", 1),
    "cortex.runtime.observe": ("cortex", "observe", "runtime", "task_bound", "allow", 1),
    "cortex.artifact.inspect": ("cortex", "inspect", "artifact", "artifact_bound", "allow", 1),
    "cortex.task.write": ("cortex", "write", "task", "task_bound", "deny", 1),
    "cortex.orchestrator.mutate": ("cortex", "mutate", "orchestrator", "task_bound", "deny", 1),
    "cortex.release.promote": ("cortex", "promote", "release", "artifact_bound", "deny", 1),
}
_REQUEST_KEYS = frozenset({
    "policy_version", "actor_kind", "profile_id", "profile_version", "target", "route",
    "phase", "task_id", "assignment_receipt", "candidate_payload_sha256", "artifact_revision",
    "acceptance_boundary", "check_identity", "operation", "resource",
})
_OPERATION_KEYS = frozenset({"namespace", "verb", "resource_class", "scope", "effect", "version", "opaque"})
_RESOURCE_KEYS = frozenset({"resource_class", "scope", "opaque"})
_BINDING_KEYS = frozenset({
    "candidate_payload_sha256", "artifact_revision", "acceptance_boundary", "check_identity",
    "task_id", "assignment_receipt", "route", "target", "phase",
})
_SEMANTIC_IDENTITY_KEYS = _BINDING_KEYS | frozenset({
    "policy_version", "actor_kind", "profile_id", "profile_version", "capability",
    "operation", "resource",
})
_IDENTITY_OPERATION_KEYS = frozenset({"namespace", "verb", "resource_class", "scope", "effect", "version"})
_IDENTITY_RESOURCE_KEYS = frozenset({"resource_class", "scope"})
_POLICY_LABELS = frozenset({"semantic_policy", "path_boundary", "evidence_binding"})
ADVISORY_EVIDENCE_FIELDS = (
    "delivery_state", "acceptance_boundary", "causal_model_delta",
    "predecessor_rollout", "retry_discriminator", "receipt_references",
)
_ADVISORY_MAX_DEPTH = 3
_ADVISORY_MAX_ITEMS = 32
_ADVISORY_MAX_TEXT = 512
_ADVISORY_MAX_INTEGER = (1 << 53) - 1
_ADVISORY_MAX_FLOAT = 1e308
_REVIEW_IDENTITY_FIELDS = ("artifact_revision", "acceptance_boundary", "check_identity")
_REVIEW_MAX_CHECKS = 128


def manifest_path() -> Path:
    return Path(__file__).resolve().parents[2] / "policy" / "capability-manifest-v1.json"


def load_manifest(path: Path | None = None) -> dict:
    """Load only the exact versioned policy document; malformed data fails closed."""
    try:
        value = json.loads((path or manifest_path()).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    if not isinstance(value, dict) or set(value) != {
            "schema", "version", "profiles", "routes", "targets", "phases", "host_enforcement"}:
        return {}
    if (value["schema"] != SCHEMA or value["version"] != POLICY_VERSION
            or not isinstance(value["profiles"], dict)
            or value["routes"] != sorted(_ROUTES)
            or value["targets"] != sorted(_TARGETS)
            or value["phases"] != sorted(_PHASES)
            or value["host_enforcement"] != "required_host_attestation"):
        return {}
    for profile, policy in value["profiles"].items():
        if not _identifier(profile) or not isinstance(policy, dict) or set(policy) != {"profile_version", "allow", "deny"}:
            return {}
        if (not _identifier(policy["profile_version"]) or not _closed_capability_list(policy["allow"])
                or not _closed_deny_list(policy["deny"])):
            return {}
    return value


def _identifier(value) -> bool:
    return isinstance(value, str) and bool(_IDENTIFIER.fullmatch(value))


def _closed_capability_list(value) -> bool:
    return isinstance(value, list) and bool(value) and len(value) == len(set(value)) and all(item in _CAPABILITIES for item in value)


def _closed_deny_list(value) -> bool:
    return isinstance(value, list) and bool(value) and len(value) == len(set(value)) and all(
        item in _CAPABILITIES or item in {"cortex.internal.*", "host.cache.inspect", "host.registry.inspect", "host.plugin.inspect", "host.debug.inspect", "host.state.read"}
        for item in value)


def _binding_valid(value) -> bool:
    return (isinstance(value, dict) and set(value) == _BINDING_KEYS
            and all(_identifier(value[key]) for key in _BINDING_KEYS - {"candidate_payload_sha256"})
            and isinstance(value["candidate_payload_sha256"], str)
            and bool(_DIGEST.fullmatch(value["candidate_payload_sha256"])))


def _semantic_identity(request: dict, capability: str | None) -> dict | None:
    """Return the complete operand-free identity for one canonical policy call."""
    if capability not in _CAPABILITIES or not isinstance(request, dict):
        return None
    if not _binding_valid({key: request.get(key) for key in _BINDING_KEYS}):
        return None
    operation = request.get("operation")
    resource = request.get("resource")
    if canonical_capability(operation, resource) != capability:
        return None
    identity = {key: request[key] for key in _BINDING_KEYS}
    identity.update(
        policy_version=request.get("policy_version"), actor_kind=request.get("actor_kind"),
        profile_id=request.get("profile_id"), profile_version=request.get("profile_version"),
        capability=capability,
        operation={key: operation[key] for key in _IDENTITY_OPERATION_KEYS},
        resource={key: resource[key] for key in _IDENTITY_RESOURCE_KEYS},
    )
    return identity if _semantic_identity_valid(identity) else None


def _semantic_identity_valid(value) -> bool:
    if not isinstance(value, dict) or set(value) != _SEMANTIC_IDENTITY_KEYS:
        return False
    if (value.get("policy_version") != POLICY_VERSION or value.get("actor_kind") != "native_worker"
            or not _identifier(value.get("profile_id")) or not _identifier(value.get("profile_version"))
            or value.get("capability") not in _CAPABILITIES):
        return False
    if not _binding_valid({key: value.get(key) for key in _BINDING_KEYS}):
        return False
    operation, resource = value.get("operation"), value.get("resource")
    if (not isinstance(operation, dict) or set(operation) != _IDENTITY_OPERATION_KEYS
            or not isinstance(resource, dict) or set(resource) != _IDENTITY_RESOURCE_KEYS):
        return False
    return _CAPABILITIES[value["capability"]] == (
        operation.get("namespace"), operation.get("verb"), operation.get("resource_class"),
        operation.get("scope"), operation.get("effect"), operation.get("version"),
    ) and operation.get("resource_class") == resource.get("resource_class") and operation.get("scope") == resource.get("scope")


def _decision_id(identity: dict) -> str:
    return hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:24]


def _decision(allowed: bool, reason: str, capability: str | None = None, request: dict | None = None) -> dict:
    safe = {
        "allowed": allowed,
        "code": None if allowed else PERMISSION_DENIED,
        "reason": reason,
        "policy_version": POLICY_VERSION,
        "capability": capability,
        "enforcement_state": POLICY_DECLARED,
    }
    if request is not None:
        # The ID binds every semantic dimension as well as qualification context,
        # so one capability's receipt cannot replay as another's evidence.
        identity = _semantic_identity(request, capability)
        if identity is not None:
            safe["decision_id"] = _decision_id(identity)
            safe["binding"] = identity
    return safe


def canonical_capability(operation, resource) -> str | None:
    """Resolve a closed canonical semantic operation, never aliases or encodings."""
    if not isinstance(operation, dict) or set(operation) != _OPERATION_KEYS:
        return None
    if not isinstance(resource, dict) or set(resource) != _RESOURCE_KEYS:
        return None
    if operation.get("opaque") is not False or resource.get("opaque") is not False:
        return None
    if operation.get("version") != POLICY_VERSION:
        return None
    if (operation.get("resource_class") != resource.get("resource_class")
            or operation.get("scope") != resource.get("scope")):
        return None
    candidate = ".".join((operation.get("namespace", ""), operation.get("resource_class", ""), operation.get("verb", "")))
    expected = _CAPABILITIES.get(candidate)
    if expected != (operation.get("namespace"), operation.get("verb"), operation.get("resource_class"),
                    operation.get("scope"), operation.get("effect"), operation.get("version")):
        return None
    return candidate


def authorize_semantic_request(request: dict, manifest: dict | None = None) -> dict:
    """Resolve verified profile policy and bound request data without host claims."""
    if not isinstance(request, dict) or set(request) != _REQUEST_KEYS:
        return _decision(False, "malformed_or_ambiguous_request")
    if (request["policy_version"] != POLICY_VERSION or request["actor_kind"] != "native_worker"
            or request["target"] not in _TARGETS or request["route"] not in _ROUTES
            or request["phase"] not in _PHASES or not _binding_valid({key: request[key] for key in _BINDING_KEYS})):
        return _decision(False, "malformed_or_ambiguous_request", request=request)
    capability = canonical_capability(request["operation"], request["resource"])
    if capability is None:
        return _decision(False, "unknown_or_opaque_operation", request=request)
    policy = (manifest if manifest is not None else load_manifest()).get("profiles", {}).get(request["profile_id"])
    if not isinstance(policy, dict) or policy.get("profile_version") != request["profile_version"]:
        return _decision(False, "unverified_profile", capability, request)
    if capability in policy.get("deny", ()) or capability.startswith("cortex.internal."):
        return _decision(False, "capability_denied", capability, request)
    if capability not in policy.get("allow", ()):
        return _decision(False, "capability_not_granted", capability, request)
    return _decision(True, "policy_capability_granted", capability, request)


def policy_receipt(decision: dict, *, observed: bool = False) -> dict:
    """Return a local bounded evidence receipt; local code cannot attest a host."""
    binding = decision.get("binding") if isinstance(decision, dict) else None
    state = POLICY_OBSERVED if observed else POLICY_DECLARED
    return {
        "decision_id": decision.get("decision_id") if isinstance(decision, dict) else None,
        "status": "complete" if isinstance(decision, dict) and _semantic_identity_valid(binding) else UNVERIFIED,
        "allowed": decision.get("allowed") is True if isinstance(decision, dict) else False,
        "code": decision.get("code") if isinstance(decision, dict) else PERMISSION_DENIED,
        "reason": decision.get("reason") if isinstance(decision, dict) else "malformed_or_ambiguous_request",
        "policy_version": POLICY_VERSION,
        "capability": decision.get("capability") if isinstance(decision, dict) else None,
        "enforcement_state": state,
        "binding": binding if _semantic_identity_valid(binding) else None,
        "truncated": False,
        "replaced": False,
    }


def evidence_admissibility(expected_request: dict, receipt: dict) -> dict:
    """Fail closed on absent, stale, denied, or mismatched policy evidence."""
    expected_capability = (canonical_capability(expected_request.get("operation"), expected_request.get("resource"))
                           if isinstance(expected_request, dict) else None)
    expected_identity = _semantic_identity(expected_request, expected_capability)
    if expected_identity is None or not isinstance(receipt, dict):
        return {"admissible": False, "status": UNVERIFIED, "reason": "malformed_binding"}
    if (set(receipt) != {"decision_id", "status", "allowed", "code", "reason", "policy_version",
                         "capability", "enforcement_state", "binding", "truncated", "replaced"}
            or receipt.get("status") != "complete" or receipt.get("allowed") is not True
            or receipt.get("code") is not None or receipt.get("truncated") is not False
            or receipt.get("replaced") is not False or not _semantic_identity_valid(receipt.get("binding"))):
        return {"admissible": False, "status": UNVERIFIED, "reason": "incomplete_or_denied_receipt"}
    # A host-enforced state is valid only from a separate authenticated host
    # boundary receipt. This repository deliberately has no such verifier, so
    # a locally supplied or forged state must not become admissible evidence.
    if receipt.get("enforcement_state") not in {POLICY_DECLARED, POLICY_OBSERVED}:
        return {"admissible": False, "status": UNVERIFIED, "reason": "host_attestation_unverified"}
    if (receipt["binding"] != expected_identity or receipt.get("capability") != expected_capability
            or receipt.get("decision_id") != _decision_id(expected_identity)):
        return {"admissible": False, "status": UNVERIFIED, "reason": "binding_mismatch"}
    return {"admissible": True, "status": "admissible", "reason": "bound_policy_observed",
            "enforcement_state": receipt.get("enforcement_state", POLICY_DECLARED)}


def one_call_label_accounting(decision: dict, labels) -> dict:
    """Retain multiple bounded diagnostics without inflating one call into many."""
    if (not isinstance(decision, dict) or not isinstance(decision.get("decision_id"), str)
            or not isinstance(labels, (list, tuple, set))
            or not labels or any(label not in _POLICY_LABELS for label in labels)):
        return {"call_count": 0, "labels": [], "status": UNVERIFIED}
    return {"call_id": decision["decision_id"], "call_count": 1,
            "labels": sorted(set(labels)), "status": "complete"}


def quiet_wait_delta(previous: dict | None, current: dict) -> dict | None:
    """Emit no message/event when bounded evidence and model state are unchanged."""
    if not isinstance(current, dict) or set(current) != {"state", "evidence_cursor"}:
        return None
    if previous == current:
        return None
    if not _identifier(current["state"]) or not _identifier(current["evidence_cursor"]):
        return None
    return {"state": current["state"], "evidence_cursor": current["evidence_cursor"]}


def _bounded_advisory_value(value, depth=0):
    """Copy small JSON-like advisory values without retaining unbounded model data."""
    if value is None or type(value) is bool:
        return value
    if type(value) is int:
        return value if abs(value) <= _ADVISORY_MAX_INTEGER else None
    if type(value) is float:
        return value if math.isfinite(value) and abs(value) <= _ADVISORY_MAX_FLOAT else None
    if isinstance(value, str):
        return value[:_ADVISORY_MAX_TEXT]
    if depth >= _ADVISORY_MAX_DEPTH:
        return None
    if isinstance(value, dict):
        result = {}
        for key, item in islice(value.items(), _ADVISORY_MAX_ITEMS):
            if not isinstance(key, str) or not key or len(key) > _ADVISORY_MAX_TEXT:
                continue
            result[key[:_ADVISORY_MAX_TEXT]] = _bounded_advisory_value(item, depth + 1)
        return result
    if isinstance(value, (list, tuple)):
        return [_bounded_advisory_value(item, depth + 1)
                for item in islice(value, _ADVISORY_MAX_ITEMS)]
    return None


def _has_advisory_value(value):
    """Treat null/empty values as absent while preserving false and zero as data."""
    return value is not None and value != "" and value != [] and value != {} and value != ()


def _has_justification(value):
    """Recognize a non-empty fresh-evidence or rerun-reason explanation."""
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (dict, list, tuple, set)):
        return bool(value)
    return value is not None and value is not False and value != 0


def structured_advisory_evidence(fields=None, **values) -> dict:
    """Return model-authored evidence fields and retained warning metadata.

    This is deliberately a document/guidance helper rather than a write contract.
    Missing fields are reported as advisory diagnostics and cannot affect routing,
    acceptance, or command status.
    """
    # Select six allowlisted keys directly. In particular, do not call
    # ``dict(fields)``: a caller may provide a very large mapping, and this
    # helper's boundedness must apply before any top-level materialization.
    supplied = {}
    if isinstance(fields, Mapping):
        for key in ADVISORY_EVIDENCE_FIELDS:
            try:
                supplied[key] = fields[key]
            except (KeyError, IndexError, TypeError):
                pass
    for key in ADVISORY_EVIDENCE_FIELDS:
        if key in values and values[key] is not None:
            supplied[key] = values[key]
    result = {key: _bounded_advisory_value(supplied.get(key))
              for key in ADVISORY_EVIDENCE_FIELDS}
    missing = [key for key in ADVISORY_EVIDENCE_FIELDS
               if not _has_advisory_value(result[key])]
    result.update(
        missing_fields=missing,
        diagnostics=[
            {"code": "missing_advisory_evidence_field", "field": key,
             "severity": "advisory", "advisory_only": True}
            for key in missing
        ],
        advisory_only=True,
        automatic_routing=False,
        acceptance_gate=False,
    )
    return result


def _review_identity(value):
    if not isinstance(value, dict):
        return None
    identity = []
    for key in _REVIEW_IDENTITY_FIELDS:
        item = value.get(key)
        # Identity comparison must stay exact. Oversized or structured values
        # are not truncated into a possible false duplicate; they are simply
        # outside this bounded diagnostic's comparison surface.
        if type(item) is not str or not item or len(item) > _ADVISORY_MAX_TEXT:
            return None
        identity.append(item)
    return tuple(identity)


def duplicate_review_reuse_diagnostic(previous_checks, current_check, *,
                                      fresh_evidence=None, rerun_reason=None,
                                      max_checks=_REVIEW_MAX_CHECKS) -> dict | None:
    """Detect one exact review/verification reuse without turning it into a gate.

    Only complete identities are comparable. The scan is capped at 128 records
    (and a caller may choose a smaller bound); malformed, null, justified, and
    nonmatching entries are ignored. The returned finding is informational only.
    """
    if type(max_checks) is not int or max_checks < 1:
        return None
    identity = _review_identity(current_check)
    if identity is None or _has_justification(fresh_evidence) or _has_justification(rerun_reason):
        return None
    if isinstance(previous_checks, dict):
        candidates = [previous_checks]
    elif isinstance(previous_checks, (list, tuple)):
        candidates = islice(previous_checks, min(max_checks, _REVIEW_MAX_CHECKS))
    else:
        return None
    if not any(_review_identity(candidate) == identity for candidate in candidates):
        return None
    return {
        "code": "duplicate_review_reuse",
        "identity": dict(zip(_REVIEW_IDENTITY_FIELDS, identity)),
        "severity": "advisory",
        "advisory_only": True,
        "automatic_routing": False,
        "acceptance_gate": False,
    }


def advisory_replanning_metadata(*, failed_canary=None, review=None, cost=None,
                                 evidence=None, delivery_state=None,
                                 acceptance_boundary=None,
                                 causal_model_delta=None,
                                 predecessor_rollout=None,
                                 retry_discriminator=None,
                                 receipt_references=None,
                                 previous_checks=None, current_check=None,
                                 fresh_evidence=None, rerun_reason=None,
                                 incident_decision=None, incident_trace=None) -> dict:
    """Bounded null-safe metadata: informational only, never a gate or route choice."""
    result = {
        # Keep the public legacy keys and ordinary scalar values intact while
        # applying the same bounded, strict-JSON-safe normalization used by the
        # structured evidence extension, including nested containers.
        "failed_canary": _bounded_advisory_value(failed_canary),
        "review": _bounded_advisory_value(review),
        "cost": _bounded_advisory_value(cost),
        "advisory_only": True,
        "automatic_routing": False,
        "acceptance_gate": False,
    }
    direct_values = {
        "delivery_state": delivery_state,
        "acceptance_boundary": acceptance_boundary,
        "causal_model_delta": causal_model_delta,
        "predecessor_rollout": predecessor_rollout,
        "retry_discriminator": retry_discriminator,
        "receipt_references": receipt_references,
    }
    if evidence is not None or any(value is not None for value in direct_values.values()):
        overrides = {key: value for key, value in direct_values.items()
                     if value is not None}
        result["evidence"] = structured_advisory_evidence(evidence, **overrides)
    duplicate = duplicate_review_reuse_diagnostic(
        previous_checks, current_check, fresh_evidence=fresh_evidence,
        rerun_reason=rerun_reason,
    )
    if duplicate is not None:
        result["diagnostics"] = [duplicate]
    if incident_decision is not None:
        result['incident_decision'] = _bounded_advisory_value(incident_decision)
    if incident_trace is not None:
        from .incident_quality import evaluate_trace
        result['incident_quality'] = evaluate_trace(incident_trace)
    return result
