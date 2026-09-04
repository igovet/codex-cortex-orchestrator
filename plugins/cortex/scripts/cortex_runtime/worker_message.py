"""Stateless trusted-policy/untrusted-data worker message renderer for V12."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import tomllib
from pathlib import Path
from collections.abc import Mapping
from typing import Any

try:  # prompt lint executes this file outside its package namespace
    from cortex_runtime.v12_contract import WORKER_MESSAGE_MAX_BYTES
except ModuleNotFoundError as exc:  # pragma: no cover - standalone renderer lint path
    if exc.name != "cortex_runtime":
        raise
    _contract_path = Path(__file__).with_name("v12_contract.py")
    _contract_spec = importlib.util.spec_from_file_location("_cortex_worker_contract", _contract_path)
    if _contract_spec is None or _contract_spec.loader is None:
        raise ImportError(f"cannot load contract module from {_contract_path}")
    _contract_module = importlib.util.module_from_spec(_contract_spec)
    _contract_spec.loader.exec_module(_contract_module)
    WORKER_MESSAGE_MAX_BYTES = _contract_module.WORKER_MESSAGE_MAX_BYTES


RENDERER_VERSION = "cortex/worker-message/v1"
_PLUGIN_ROOT = Path(__file__).resolve().parents[2]
_PROFILE_INDEX = _PLUGIN_ROOT / "profiles.json"
_AGENTS = _PLUGIN_ROOT / "agents"

# Native dispatch carries only the selected packaged profile plus the opaque
# worker locator. Mission, scope, current outcome revision, decisions, and
# predecessor report bodies are authoritative only in the first assignment
# read and are deliberately not duplicated into the spawn prompt.
# Native spawn output is a transport bootstrap, not the assignment itself.
# Keep it small enough that Codex can forward the complete closed projection
# without an ellipsized tool result.  The first assignment read below returns
# the full common policy, packaged profile, task contract, and predecessor
# evidence from the server-owned immutable snapshot.
_MINIMAL_WORKER_BOOTSTRAP = """# Cortex worker bootstrap

- First consume the server-owned assignment through the live advertised `read_task`;
  use the exact worker reference below and select assignment
  evidence. `open_assignment` creates assignments for a coordinator; it never
  reads or consumes a worker assignment.
- Build that finite first read from the live contract; correct it once only when diagnostics are unambiguous.
  Never repeat malformed input or guess authority.
- The assignment read is the sole authority for policy, scope, outcomes, and
  evidence. Do not load coordinator/orchestrator skills first; it returns the
  exact worker policy and packaged profile.
- You are a worker, not a coordinator. Coordinator-only operations are
  prohibited. Do no project work before successful consumption. If correction
  fails, stop without broadening the assignment.
"""

# The single common policy is delivered by assignment consumption. Native
# dispatch remains a compact authority bootstrap, not a second policy copy.
_MANDATORY_PROJECT_POLICY = """# Mandatory project-work invariants

- The consumed assignment fixes the exact node scope, execution mode, and
  terminal publication kind. Packaged profiles supply expertise, never a
  different authority or publication kind. Publish a plan only for a planning
  node, a documentation assessment only for a documentation node, and a result
  for every other node, including discovery performed by a planner or writer.
- Preserve every assigned node and its exact produced or verified subjects.
  Account for every assigned check once beneath its own subject and node;
  equal outcome names in different nodes do not merge their evidence. Keep
  planned checks distinct from observed results. Classify incomplete checks
  against the unchanged contract without inventing authority or success.
- Respect the assigned artifact procedure and mutation boundary. Observe the
  supplied target before work and again immediately before publication; keep
  the worker-generated manifests for independent comparisons. Wait for every
  bounded child process to terminate before the final observation. Never leave
  detached project mutators running. Reconciliation observes existing changes
  without claiming their authorship or undoing them.
- A superseded or snapshot-conflict non-publication response ends this route.
  Stop without retrying, guessing new scope, repairing the ledger, or publishing
  a substitute report; the coordinator owns reconciliation and replacement.
- A fresh native worker derives its finite first assignment read only from the
  live advertised contract and exact server-rendered authority. It performs no
  project action before that read succeeds. One materially corrected attempt is
  permitted only after a deterministic caller-shape rejection with bounded,
  unambiguous diagnostics; never repeat the unchanged malformed request or
  guess identity or authority. A second deterministic failure, incomplete
  diagnostics, or a correction requiring guesses ends the assignment. Ambiguous
  transport permits only identical reconciliation.
- Continue only when the immediately preceding otherwise-identical assignment
  read explicitly reports more data, and continue immediately. Never repeat a
  terminal assignment read during normal execution. After host context
  compaction or reset, immediately restart the same assignment from the
  beginning on this authenticated connection, complete every page again, and
  rebuild exact publication coverage only from the fresh server-owned
  reconciliation projection; this is the sole terminal-read exception and
  grants no new authority. After terminal consumption, perform bounded role
  work and publish exactly one matching terminal outcome; unresolved evidence
  produces an honest partial or blocked publication instead of a read loop.
- A confirmed successful terminal-publication response ends all worker tool
  activity. Never call any tool or repeat/reconcile that mutation after success;
  immediately emit the compact native coordinator handoff and stop. Identical
  reconciliation is reserved only for an actually ambiguous transport result.
- Every native worker and packaged profile is worker-only. Coordinator-only
  operations, including governance assessment, remain prohibited for planners,
  replacements, rework, and repeated-planning assignments.
- Before structural project-code discovery or local repository search, use
  Codebase Memory as the preferred first evidence route when it is available and
  bind it to the exact canonical `project_root` returned in the server-owned
  assignment context. If it is unavailable, denied, times out, errors, or returns
  unusable or insufficient evidence, record that bounded limitation and use
  exactly one safe assignment-scoped repository-native enumeration or text-search
  fallback. Do not silently skip an available usable graph, begin with broad
  `rg`/`find`/directory enumeration, or chain fallback searches. Its absence alone
  is not a blocked publication cause.
- A plan publication does not choose or declare its review disposition. The
  server derives it from authoritative assessment and complete plan evidence.
  A complete risk-free minimal or ordinary-light plan is informational and
  continues without a user hold. Material high risk, a genuine product or
  authority choice, credentials, or explicit user-requested review requires
  a decision packet. Uncertainty or incomplete work alone calls for bounded
  discovery, not automatic approval. A planner cannot self-attest a
  downgrade or omit evidence to affect classification.
- Accepted semantic steering revokes every nonterminal worker authority bound
  to an earlier contract revision. An assignment-stale read or publication ends
  this assignment without retry, downstream effect, or inferred loss; the
  coordinator must create a fresh current-contract assignment.
- Never ask the user directly or emit a context-free question or approval
  request, and never wait for an execution-time decision. Resolve facts and
  routine blockers autonomously within the assigned boundary. If a genuine
  high-risk branch, credential/API-key/ENV prerequisite, external action, or
  product choice remains, publish an honest partial or blocked outcome with the
  established facts, available safe choices, the material consequence or
  stopping condition of each choice, and the exact
  plan-review material the coordinator must present; do not stop a safe
  in-scope route merely because another branch needs approval. The active
  advertised publication contract is the sole payload authority: never add
  private lineage, predecessor references, or other metadata.
- An assignment containing a planning node completes all bounded discovery before publishing one
  terminal plan, then stops project/tool work and never publishes a
  supplementary result or documentation outcome. Later material evidence uses
  a separate evidence assignment followed by a fresh planning revision.
"""


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _task_ref(value: object) -> str | None:
    """Expose only the compact call locator in agent-facing task data."""
    if not isinstance(value, str):
        return None
    match = re.fullmatch(r"task-([0-9a-f]{64})-([0-9a-f]{32})", value)
    return None if match is None else f"t_{match.group(2)[-12:]}"


def _worker_task_ref(task_id: object, delegation_id: object) -> str | None:
    task = _task_ref(task_id)
    if task is None or not isinstance(delegation_id, str):
        return None
    match = re.fullmatch(r"delegation-[0-9a-f]{64}-([0-9a-f]{32})", delegation_id)
    return None if match is None else f"{task}_{match.group(1)}"


def _packaged_profiles() -> dict[str, str]:
    """Return the verified packaged profile-name/filename mapping only."""
    try:
        profiles = json.loads(_PROFILE_INDEX.read_text(encoding="utf-8"))
        raw_profiles = profiles.get("profiles") if isinstance(profiles, Mapping) else None
        if not isinstance(raw_profiles, list):
            return {}
        mapping: dict[str, str] = {}
        for item in raw_profiles:
            if not isinstance(item, Mapping):
                return {}
            name, filename = item.get("name"), item.get("filename")
            if (
                not isinstance(name, str)
                or not name
                or not isinstance(filename, str)
                or Path(filename).name != filename
                or name in mapping
            ):
                return {}
            mapping[name] = filename
        return mapping
    except (OSError, UnicodeDecodeError, ValueError, json.JSONDecodeError):
        return {}


def packaged_profile_names() -> tuple[str, ...]:
    """Expose the exact packaged profile enum for the public MCP schema."""
    return tuple(sorted(_packaged_profiles()))




def assignment_worker_policy(profile_name: object) -> dict[str, str] | None:
    """Return the full trusted policy delivered by the first assignment read.

    Native spawn carries only the compact immutable bootstrap.  The worker
    receives this package-owned policy after the server has atomically bound
    and consumed its exact assignment evidence, avoiding a large model-copied
    spawn message while preserving the complete advisory profile.
    """
    loaded_name, instructions, profile_digest = _profile(profile_name)
    if loaded_name is None or instructions is None or profile_digest is None:
        return None
    return {
        "common_policy": _MANDATORY_PROJECT_POLICY.strip(),
        "profile_name": loaded_name,
        "profile_instructions": instructions.strip(),
        "profile_digest": profile_digest,
    }


def _profile(profile_name: object) -> tuple[str | None, str | None, str | None]:
    """Load only an explicit package-owned profile selected by the coordinator."""
    if not isinstance(profile_name, str):
        return None, None, None
    try:
        filename = _packaged_profiles().get(profile_name)
        if filename is None:
            return None, None, None
        path = _AGENTS / filename
        raw = path.read_bytes()
        parsed = tomllib.loads(raw.decode("utf-8"))
        instructions = parsed.get("developer_instructions")
        if not isinstance(instructions, str):
            return None, None, None
        return profile_name, instructions, "sha256:" + hashlib.sha256(raw).hexdigest()
    except (OSError, UnicodeDecodeError, ValueError, tomllib.TOMLDecodeError, json.JSONDecodeError):
        return None, None, None


def render_worker_message(*, task: Mapping[str, Any], delegation: Mapping[str, Any]) -> dict[str, Any]:
    """Produce one deterministic native message and sanitised profile proof.

    The trusted handoff carries the exact assignment anchor but never a
    bearer bootstrap token. The server resolves its private one-time lease
    when the worker consumes assignment evidence.
    """
    profile_name, instructions, profile_digest = _profile(delegation.get("profile_name"))
    profile_state = "loaded" if instructions is not None else "unavailable"
    worker_task_ref = _worker_task_ref(task.get("task_id"), delegation.get("delegation_id"))
    if worker_task_ref is None:
        raise ValueError("worker task reference is invalid")
    bootstrap = {
        "assignment context": {
            "task_ref": worker_task_ref,
        },
    }
    message = "\n\n".join((
        _MINIMAL_WORKER_BOOTSTRAP.strip(),
        "## Server-bound worker context\n\n```json\n" + _canonical(bootstrap).replace("```", "\\u0060\\u0060\\u0060") + "\n```",
    ))
    if len(message.encode("utf-8")) > WORKER_MESSAGE_MAX_BYTES:
        raise ValueError("worker message exceeds the advertised UTF-8 byte limit")
    return {
        "message": message,
        "renderer": {
            "version": RENDERER_VERSION,
            "profile_name": profile_name,
            "profile_state": profile_state,
            "profile_digest": profile_digest,
            "common_policy_digest": "sha256:" + hashlib.sha256(_MANDATORY_PROJECT_POLICY.encode("utf-8")).hexdigest(),
        },
    }
