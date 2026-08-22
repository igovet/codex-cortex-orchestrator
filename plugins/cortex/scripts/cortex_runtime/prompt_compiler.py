"""Deterministic Cortex Prompt Contract Architecture v3 compiler.

The worker briefing has two deliberately different content classes.  Stable
protocol policy is emitted as canonical Markdown sections.  Dispatch-specific
values are emitted once, inside a fenced JSON assignment block, and are always
untrusted data.  Keeping those classes separate prevents a task value from
becoming a heading, a tool instruction, or a second source of authority.
"""
from __future__ import annotations

import ast
import hashlib
import json
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping


PLUGIN_ROOT = Path(__file__).resolve().parents[2]
PROMPT_CONTRACT_PATH = PLUGIN_ROOT / "prompt-contracts.json"
_ALLOWED_SECTION_KEYS = frozenset((
    "authority", "hard_constraints", "assignment", "role", "mode", "gate",
    "context", "tool_protocol", "output_contract", "stopping",
))
_V3_REQUIRED_KEYS = frozenset((
    "authority", "hard_constraints", "assignment", "role", "tool_protocol",
    "output_contract", "stopping",
))
_EXPECTED_GATES = frozenset((
    "scope", "plan", "discover", "architecture", "database_architecture",
    "implementation", "qa", "security", "performance", "accessibility", "ux",
    "review", "documentation", "close", "governance_activation", "governance_close",
))
_COORDINATOR_COMPLETION_CONTRACT = (
    "A native spawn or wait is never completion evidence. Every ready_to_spawn response authorizes only its returned "
    "dispatch.call with unmodified dispatch.arguments; a generic collaboration spawn, self-authored task name, or "
    "replacement child cannot bind to or advance a Cortex attempt. For every terminal worker, the coordinator must read its exact "
    "canonical AttemptResult with read_worker_result, then call continue_orchestration only from that server-returned "
    "continuation or failed-result route, then close_agent for that completed child before any successor dispatch. Only "
    "the resulting successful server lifecycle outcome is the continuation or terminal audit; before it, the coordinator "
    "must neither present completion nor close the worker as consumed. A successful continue is one-shot: follow only "
    "its returned dispatch/wait/terminal outcome; never call continue again with the same step/results, request artifacts, "
    "add future_waves, or spawn a replacement. A retryable=false task-identity or step-mismatch diagnostic is terminal: "
    "stop and result the blocker."
)


@dataclass(frozen=True)
class PromptSection:
    """A single canonical prompt section supplied by a compiler caller."""

    key: str
    body: str
    heading: str | None = None
    required: bool = False


def _contract_digest(contract: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(contract, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _validate_contract(payload: object) -> dict[str, Any]:
    if not isinstance(payload, dict) or payload.get("schema") != "cortex/prompt-contract/v3":
        raise RuntimeError("bundled Cortex prompt contract schema is invalid")
    compiler = payload.get("compiler")
    if not isinstance(compiler, dict):
        raise RuntimeError("bundled Cortex prompt compiler section is invalid")
    order = compiler.get("section_order")
    headings = compiler.get("section_headings")
    if (
        not isinstance(order, list)
        or len(order) != len(_ALLOWED_SECTION_KEYS)
        or len(order) != len(set(order))
        or set(order) != _ALLOWED_SECTION_KEYS
        or not isinstance(headings, dict)
        or set(headings) != set(order)
        or not all(isinstance(item, str) and item.strip() for item in headings.values())
    ):
        raise RuntimeError("bundled Cortex prompt section order is invalid")
    if compiler.get("format") != "markdown" or compiler.get("assignment_boundary") != "json":
        raise RuntimeError("bundled Cortex prompt format contract is invalid")
    if compiler.get("untrusted_assignment") is not True or not isinstance(compiler.get("xml_boundaries"), list):
        raise RuntimeError("bundled Cortex assignment boundary is invalid")
    ownership = payload.get("ownership")
    if not isinstance(ownership, dict) or set(ownership) != _ALLOWED_SECTION_KEYS:
        raise RuntimeError("bundled Cortex prompt ownership matrix is invalid")
    for section, owner in ownership.items():
        if not isinstance(owner, dict) or not isinstance(owner.get("source"), str) or not owner["source"].strip():
            raise RuntimeError(f"bundled Cortex prompt owner is invalid: {section}")
        if owner.get("task_data") is not (section == "assignment"):
            raise RuntimeError(f"bundled Cortex prompt data boundary is invalid: {section}")
    assignment_owner = ownership["assignment"]
    if assignment_owner.get("boundary") != "fenced_json":
        raise RuntimeError("bundled Cortex assignment must use a fenced JSON boundary")
    sources = payload.get("sources")
    if not isinstance(sources, dict) or not all(isinstance(value, str) and value.strip() for value in sources.values()):
        raise RuntimeError("bundled Cortex prompt sources are invalid")
    v3 = payload.get("v3")
    if (
        not isinstance(v3, dict)
        or not isinstance(v3.get("title"), str)
        or not v3["title"].strip()
        or set(v3.get("required_sections") or []) != _V3_REQUIRED_KEYS
        or set(v3.get("conditional_sections") or []) != {"mode", "gate", "context"}
    ):
        raise RuntimeError("bundled Cortex v3 prompt contract is invalid")
    attempt_result_contract = payload.get("attempt_result_contract")
    if (
        not isinstance(attempt_result_contract, dict)
        or attempt_result_contract.get("coordinator_completion") != _COORDINATOR_COMPLETION_CONTRACT
    ):
        raise RuntimeError("bundled Cortex coordinator completion contract is invalid")
    budgets = payload.get("budgets")
    if not isinstance(budgets, dict) or not all(isinstance(value, int) and value > 0 for value in budgets.values()):
        raise RuntimeError("bundled Cortex prompt budgets are invalid")
    prompt_eval = payload.get("prompt_eval")
    if not isinstance(prompt_eval, dict):
        raise RuntimeError("bundled Cortex prompt-eval contract is invalid")
    if prompt_eval.get("model") != "gpt-5.6-luna" or prompt_eval.get("reasoning_effort") != "high":
        raise RuntimeError("prompt evals must be pinned to Luna high")
    if prompt_eval.get("allow_model_fallback") is not False or prompt_eval.get("offline_default") is not True:
        raise RuntimeError("prompt evals must be offline by default and fail closed without fallback")
    live_runner = prompt_eval.get("live_runner")
    if (
        not isinstance(live_runner, dict)
        or live_runner.get("command") != "codex exec"
        or live_runner.get("sandbox") != "read-only"
        or live_runner.get("response_schema") != "cortex/prompt-live-eval-response/v1"
        or live_runner.get("required_route") != "worker"
        or live_runner.get("required_completion") != "attempt_completed"
        or live_runner.get("live_default") is not False
        or set(live_runner.get("forbidden_models") or []) != {"gpt-5.6-terra", "gpt-5.6-sol"}
        or not all(
            isinstance(live_runner.get(key), int) and live_runner[key] > 0
            for key in ("timeout_seconds", "max_stream_bytes", "max_output_bytes", "max_output_tokens")
        )
    ):
        raise RuntimeError("prompt live-eval runner contract is invalid")
    return payload


def load_prompt_contract(path: Path = PROMPT_CONTRACT_PATH) -> dict[str, Any]:
    """Load the versioned, machine-readable prompt ownership contract."""
    try:
        return _validate_contract(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("bundled Cortex prompt contract is unreadable") from exc


PROMPT_CONTRACT = load_prompt_contract()
PROMPT_CONTRACT_DIGEST = _contract_digest(PROMPT_CONTRACT)
PROMPT_VOLUME_GUIDANCE = str(
    PROMPT_CONTRACT["v3"].get("prompt_volume_guidance") or ""
).strip()


def prompt_contract_digest(contract: Mapping[str, Any] | None = None) -> str:
    """Return the digest for the exact contract used in a compiled prompt."""
    return _contract_digest(contract or PROMPT_CONTRACT)


def assignment_json_block(assignment: Mapping[str, Any]) -> str:
    """Serialize untrusted task data in a fence that cannot be closed by it."""
    if not isinstance(assignment, Mapping):
        raise TypeError("prompt assignment must be a mapping")
    payload = json.dumps(assignment, ensure_ascii=False, sort_keys=True, indent=2)
    longest_tick_run = max((len(run) for run in re.findall(r"`+", payload)), default=0)
    fence = "`" * max(3, longest_tick_run + 1)
    return f"{fence}json\n{payload}\n{fence}"


def _as_section(value: PromptSection | Mapping[str, Any]) -> PromptSection:
    if isinstance(value, PromptSection):
        return value
    if not isinstance(value, Mapping):
        raise TypeError("prompt section must be a PromptSection or mapping")
    return PromptSection(
        key=str(value.get("key") or ""),
        body=str(value.get("body") or ""),
        heading=str(value.get("heading")) if value.get("heading") is not None else None,
        required=bool(value.get("required", False)),
    )


def compile_prompt(
    sections: Iterable[PromptSection | Mapping[str, Any]],
    *,
    title: str,
    contract: Mapping[str, Any] | None = None,
    include_receipt: bool = True,
) -> str:
    """Assemble unique sections in contract order and fail closed on drift."""
    active_contract = _validate_contract(dict(contract or PROMPT_CONTRACT))
    compiler = active_contract["compiler"]
    order = tuple(compiler["section_order"])
    headings = compiler["section_headings"]
    seen: set[str] = set()
    normalized: dict[str, PromptSection] = {}
    for raw in sections:
        item = _as_section(raw)
        if item.key not in order:
            raise ValueError(f"unknown prompt section: {item.key}")
        if item.key in seen:
            raise ValueError(f"duplicate prompt section: {item.key}")
        seen.add(item.key)
        normalized[item.key] = item
    required = set(active_contract["v3"]["required_sections"])
    missing = sorted(required - set(normalized))
    if missing:
        raise ValueError("missing canonical prompt sections: " + ", ".join(missing))
    lines = [f"# {title}"]
    if include_receipt:
        lines.append(
            f"<!-- prompt-contract: {active_contract['schema']} digest={prompt_contract_digest(active_contract)} "
            "assembly=conditional assignment-boundary=json -->"
        )
    for key in order:
        item = normalized.get(key)
        if item is None or not item.body.strip():
            if item is not None and item.required:
                raise ValueError(f"required prompt section is empty: {key}")
            continue
        lines.extend(("", f"## {item.heading or headings[key]}", item.body.strip()))
    return "\n".join(lines).rstrip() + "\n"


def compile_v3_briefing(
    *,
    assignment: Mapping[str, Any],
    authority: str,
    hard_constraints: str,
    role_delta: str,
    mode_delta: str = "",
    gate_delta: str = "",
    context_delta: str = "",
    tool_protocol: str,
    output_contract: str,
    stopping: str,
) -> str:
    """Compile the only artifact-backed v3 worker briefing shape.

    Task values are accepted only as ``assignment``.  All other parameters are
    policy strings selected by the runtime from canonical bundled sources.
    """
    # Volume targets belong to the worker prompt as advisory behavior.  Keep
    # the rule in the canonical v3 compiler so every profile receives the
    # same no-loss contract; it must never be implemented as a backend gate.
    effective_constraints = " ".join(
        part for part in (str(hard_constraints).strip(), PROMPT_VOLUME_GUIDANCE)
        if part
    )
    return compile_prompt(
        (
            PromptSection("authority", authority, required=True),
            PromptSection("hard_constraints", effective_constraints, required=True),
            PromptSection("assignment", assignment_json_block(assignment), required=True),
            PromptSection("role", role_delta, required=True),
            PromptSection("mode", mode_delta),
            PromptSection("gate", gate_delta),
            PromptSection("context", context_delta),
            PromptSection("tool_protocol", tool_protocol, required=True),
            PromptSection("output_contract", output_contract, required=True),
            PromptSection("stopping", stopping, required=True),
        ),
        title=PROMPT_CONTRACT["v3"]["title"],
    )


def _find_function(tree: ast.AST, name: str) -> ast.FunctionDef | None:
    return next((node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == name), None)


def _has_package_fstring(function: ast.FunctionDef) -> bool:
    for node in ast.walk(function):
        if not isinstance(node, ast.JoinedStr):
            continue
        for value in ast.walk(node):
            if isinstance(value, ast.Name) and value.id == "package":
                return True
    return False


def lint_prompt_sources(root: Path = PLUGIN_ROOT.parent.parent) -> list[str]:
    """Return deterministic contract/source issues without reading runtime state."""
    issues: list[str] = []
    contract = load_prompt_contract(root / "plugins/cortex/prompt-contracts.json")
    profile_path = root / "plugins/cortex/profiles.json"
    try:
        profile_contract = json.loads(profile_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ["profiles.json is unreadable"]
    profiles = profile_contract.get("profiles")
    if not isinstance(profiles, list):
        return ["profiles.json has no profile list"]
    profiles_by_name = {str(item.get("name")): item for item in profiles if isinstance(item, dict)}
    if len(profiles_by_name) != len(profiles) or not profiles_by_name:
        issues.append("profiles.json profile names are not unique")
    agents_dir = root / "plugins/cortex/agents"
    seen_names: set[str] = set()
    required_markers = tuple(contract["role_delta_required_markers"])
    role_budget = int(contract["budgets"]["role_delta_target_bytes"])
    for path in sorted(agents_dir.glob("*.toml")):
        try:
            payload = tomllib.loads(path.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError):
            issues.append(f"{path.name}: invalid TOML")
            continue
        name = str(payload.get("name") or "")
        if not name or name in seen_names:
            issues.append(f"{path.name}: duplicate or missing profile name")
        seen_names.add(name)
        profile = profiles_by_name.get(name)
        if not isinstance(profile, dict):
            issues.append(f"{path.name}: not represented in profiles.json")
        elif (
            payload.get("description") != profile.get("description")
            or payload.get("sandbox_mode") != profile.get("sandbox")
        ):
            issues.append(f"{path.name}: description or sandbox differs from profiles.json")
        prompt = str(payload.get("developer_instructions") or "")
        missing_markers = [marker for marker in required_markers if marker not in prompt]
        if missing_markers:
            issues.append(f"{path.name}: missing role-delta markers {missing_markers}")
        if len(prompt.encode("utf-8")) > role_budget:
            issues.append(f"{path.name}: role delta exceeds {role_budget} bytes")
        for literal in contract["lint"]["forbidden_model_literals"]:
            if literal.lower() in prompt.lower():
                issues.append(f"{path.name}: prompt contains forbidden model literal {literal}")
    if seen_names != set(profiles_by_name):
        issues.append("agent TOML/profile contract sets differ")
    gate_briefings = profile_contract.get("gate_briefings")
    if not isinstance(gate_briefings, dict) or set(gate_briefings) != _EXPECTED_GATES:
        issues.append("profiles.json gate briefing map does not cover the canonical gates")
    else:
        for gate, briefing in gate_briefings.items():
            if not isinstance(briefing, dict) or set(briefing) != {"objective", "ownership", "acceptance", "verification"}:
                issues.append(f"gate briefing has an invalid shape: {gate}")
    shared_budgets = (profile_contract.get("shared_worker_contract") or {}).get("prompt_compaction_guidance")
    expected_budgets = {key: contract["budgets"][key] for key in (
        "bootstrap_target_bytes", "ordinary_briefing_target_bytes", "harvest_briefing_target_bytes",
    )}
    if (
        not isinstance(shared_budgets, dict)
        or {key: shared_budgets.get(key) for key in expected_budgets} != expected_budgets
        or shared_budgets.get("semantics") != contract.get("prompt_guidance_semantics")
    ):
        issues.append("profiles.json prompt budgets differ from prompt-contracts.json")
    skill_root = root / "plugins/cortex/skills"
    required_skills = set(contract["lint"]["required_skills"])
    section_requirements = contract["skill_section_requirements"]
    if set(section_requirements) != required_skills:
        issues.append("prompt contract skill requirements differ from lint skill set")
    for skill in sorted(required_skills):
        path = skill_root / skill / "SKILL.md"
        if not path.is_file():
            issues.append(f"missing required skill: {skill}")
            continue
        text = path.read_text(encoding="utf-8")
        if not text.startswith("---\n") or "name:" not in text.split("---\n", 2)[1]:
            issues.append(f"{skill}: missing YAML frontmatter")
        headings = [line.strip() for line in text.splitlines() if line.startswith("## ")]
        if len(headings) != len(set(headings)):
            issues.append(f"{skill}: duplicate level-2 headings")
        for required_heading in section_requirements.get(skill, []):
            if required_heading not in headings:
                issues.append(f"{skill}: missing required section {required_heading}")
    briefings_path = root / "plugins/cortex/scripts/cortex_runtime/briefings.py"
    try:
        tree = ast.parse(briefings_path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        issues.append("briefings.py is unreadable")
    else:
        v3 = _find_function(tree, "host_spawn_prompt")
        retired_v3 = _find_function(tree, "_pre_contract_v3_prompt_assembly")
        if retired_v3 is not None:
            issues.append("retired pre-contract v3 prompt assembly must not remain beside the canonical compiler")
        if v3 is None or not any(
            isinstance(node, ast.Name) and node.id == "compile_v3_briefing" for node in ast.walk(v3)
        ):
            issues.append("v3 briefing does not use the canonical compiler")
        elif _has_package_fstring(v3):
            issues.append("v3 briefing interpolates task data into normative prose")
    if set(contract["compiler"]["xml_boundaries"]) & {"all", "prompt"}:
        issues.append("XML must remain selective; whole-prompt XML is forbidden")
    return issues


__all__ = [
    "PROMPT_CONTRACT",
    "PROMPT_CONTRACT_DIGEST",
    "PROMPT_CONTRACT_PATH",
    "PromptSection",
    "assignment_json_block",
    "compile_prompt",
    "compile_v3_briefing",
    "lint_prompt_sources",
    "load_prompt_contract",
    "prompt_contract_digest",
]
