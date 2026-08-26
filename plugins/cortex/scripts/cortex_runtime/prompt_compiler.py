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

from cortex_runtime import canonical_json


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
@dataclass(frozen=True)
class PromptSection:
    """A single canonical prompt section supplied by a compiler caller."""

    key: str
    body: str
    heading: str | None = None
    required: bool = False


def _contract_digest(contract: Mapping[str, Any]) -> str:
    return canonical_json.digest(contract)


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
    if sources.get("tool_schema_registry") != "active public MCP schema registry":
        raise RuntimeError("bundled Cortex tool-schema source is invalid")
    lint_contract = payload.get("lint")
    if not isinstance(lint_contract, dict) or not isinstance(lint_contract.get("source_ownership"), dict):
        raise RuntimeError("bundled Cortex prompt source ownership is invalid")
    required_source_owners = {
        "worker_tool_schema": "active public MCP schema registry",
        "worker_assignment_authorization": "one exact opaque authority carried by the native dispatch",
        "planner_shape": "semantic work breakdown published through the current worker completion tool",
        "attachment_preflight": "profiles.json.shared_worker_contract.attachment_preflight",
        "activation_context": "profiles.json.shared_worker_contract.activation_context",
    }
    if lint_contract["source_ownership"] != required_source_owners:
        raise RuntimeError("bundled Cortex prompt source ownership is invalid")
    v3 = payload.get("v3")
    if (
        not isinstance(v3, dict)
        or not isinstance(v3.get("title"), str)
        or not v3["title"].strip()
        or set(v3.get("required_sections") or []) != _V3_REQUIRED_KEYS
        or set(v3.get("conditional_sections") or []) != {"mode", "gate", "context"}
    ):
        raise RuntimeError("bundled Cortex v3 prompt contract is invalid")
    worker_completion_contract = payload.get("worker_completion_contract")
    if (
        not isinstance(worker_completion_contract, dict)
        or set(worker_completion_contract) != {
            "worker_authority", "worker_bootstrap_observation",
            "worker_question_pause", "coordinator_waiting",
            "briefing_terminal_authority", "coordinator_completion",
            "future_wave_adaptation",
        }
        or not all(isinstance(value, str) and value.strip() for value in worker_completion_contract.values())
    ):
        raise RuntimeError("bundled Cortex worker completion contract is invalid")
    budgets = payload.get("budgets")
    if not isinstance(budgets, dict) or not all(isinstance(value, int) and value > 0 for value in budgets.values()):
        raise RuntimeError("bundled Cortex prompt budgets are invalid")
    prompt_eval = payload.get("prompt_eval")
    if not isinstance(prompt_eval, dict):
        raise RuntimeError("bundled Cortex prompt-eval contract is invalid")
    if prompt_eval.get("model") != "gpt-5.6-luna" or prompt_eval.get("reasoning_effort") != "medium":
        raise RuntimeError("prompt evals must be pinned to Luna medium")
    if prompt_eval.get("allow_model_fallback") is not False or prompt_eval.get("offline_default") is not True:
        raise RuntimeError("prompt evals must be offline by default and fail closed without fallback")
    live_verification = prompt_eval.get("live_verification")
    if (
        not isinstance(live_verification, str)
        or not live_verification.strip()
        or "not a release gate" not in live_verification
    ):
        raise RuntimeError("prompt live-verification contract is invalid")
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


def _string_literals(tree: ast.AST) -> tuple[str, ...]:
    """Return only static prose that can become part of a compiled policy."""
    return tuple(
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    )


def _structured_prompt_form(prompt: str) -> str | None:
    """Identify a code-like payload field form in a role prompt.

    Role deltas may describe the work that belongs in a report, but must not
    restate a schema by spelling dotted fields, array fields, or JSON objects.
    Restrict this to inline-code spans so ordinary prose and required Markdown
    labels remain available to role authors.
    """
    for span in re.findall(r"`([^`\n]+)`", prompt):
        if re.search(r"\b[A-Za-z][A-Za-z0-9_]*\.[A-Za-z][A-Za-z0-9_]*\b", span):
            return span
        if re.search(r"\b[A-Za-z][A-Za-z0-9_]*\s*\[\s*\]", span):
            return span
        if re.search(r"\{[^{}\n]*\b[A-Za-z][A-Za-z0-9_]*\s*:", span):
            return span
    return None


def _api_template_operation(policy: str, operations: Iterable[str]) -> str | None:
    """Return an operation whose prose contains an exact invocation template."""
    for operation in operations:
        if re.search(rf"\b{re.escape(operation)}\s*(?:\(|\{{|\[)", policy):
            return operation
    return None


def _schema_property_names(value: object) -> set[str]:
    """Derive every public argument identifier from the canonical tool registry."""
    names: set[str] = set()
    if isinstance(value, dict):
        properties = value.get("properties")
        if isinstance(properties, dict):
            names.update(str(name) for name in properties)
        for nested in value.values():
            names.update(_schema_property_names(nested))
    elif isinstance(value, list):
        for nested in value:
            names.update(_schema_property_names(nested))
    return names


def _all_strings(value: object) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for nested in value.values():
            yield from _all_strings(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from _all_strings(nested)


def _contains_schema_identifier(text: str, name: str) -> bool:
    """Detect a public property presented as schema syntax, not ordinary prose."""
    escaped = re.escape(name)
    patterns = (
        rf"`{escaped}`",
        rf"[\"']{escaped}[\"']\s*:",
        rf"(?<![A-Za-z0-9_]){escaped}\s*=",
        rf"\b(?:field|argument|property)\s+{escaped}\b",
    )
    if "_" in name:
        patterns += (rf"(?<![A-Za-z0-9_]){escaped}(?![A-Za-z0-9_])",)
    return any(re.search(pattern, text) for pattern in patterns)


def _tool_catalog_by_audience(text: str) -> dict[str, tuple[str, ...]] | None:
    """Read the generated Cortex control catalog without treating prose as schema."""
    begin = "<!-- BEGIN GENERATED CORTEX TOOL CATALOG -->"
    end = "<!-- END GENERATED CORTEX TOOL CATALOG -->"
    if text.count(begin) != 1 or text.count(end) != 1:
        return None
    body = text.split(begin, 1)[1].split(end, 1)[0]
    heading_audiences = {
        "### Coordinator tools": "coordinator",
        "### Worker tools": "worker",
    }
    rows: dict[str, list[str]] = {audience: [] for audience in heading_audiences.values()}
    current: str | None = None
    seen_headings: set[str] = set()
    for line in body.splitlines():
        stripped = line.strip()
        if stripped in heading_audiences:
            current = heading_audiences[stripped]
            if current in seen_headings:
                return None
            seen_headings.add(current)
            continue
        match = re.match(r"^\| `([^`]+)` \|", stripped)
        if match:
            if current is None:
                return None
            rows[current].append(match.group(1))
    if seen_headings != set(rows):
        return None
    return {audience: tuple(names) for audience, names in rows.items()}


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
    lint_policy = contract["lint"]
    shared_contract = profile_contract.get("shared_worker_contract")
    if not isinstance(shared_contract, dict):
        return ["profiles.json shared worker contract is unreadable"]
    coordinator_wait = " ".join(
        str(shared_contract.get("coordinator_wait_contract") or "").lower().split()
    )
    wait_action = (shared_contract.get("coordinator_action_semantics") or {}).get(
        "wait_for_wave",
    )
    if (
        not all(marker in coordinator_wait for marker in (
            "wait_agent cycles", "explicit 300-second timeout",
            "exact child identifier", "nonterminal",
            "call no cortex read or lifecycle tool", "read_worker_wave only after",
        ))
        or not all(status in coordinator_wait for status in (
            "interrupted", "completed", "errored", "shutdown", "notfound",
        ))
        or not isinstance(wait_action, dict)
        or wait_action.get("per_wait_timeout_seconds") != 300
        or wait_action.get("minimum_overall_wait_seconds") != 300
        or wait_action.get("native_default_wait_allowed") is not False
        or wait_action.get("repeat_after_early_wakeup") is not True
        or wait_action.get("timeout_authorizes_result_read") is not False
    ):
        issues.append("profiles.json coordinator native-wait contract is incomplete")
    worker_question_pause = str(shared_contract.get("worker_question_pause_contract") or "").lower()
    if not all(marker in worker_question_pause for marker in (
        "durably publishing", "complete unicode question", "same child is resumed",
        "new native turn", "real user answer",
    )):
        issues.append("profiles.json worker durable-question pause contract is incomplete")
    completion_contract = contract.get("worker_completion_contract")
    completion_policy = "\n".join(_all_strings(completion_contract)).lower()
    # Keep this a semantic contract check instead of requiring one exact prose
    # spelling.  The prompt must describe the host lifecycle boundary, but it
    # must not duplicate the MCP schema or request field names.  In particular,
    # equivalent wording such as "trusted observation of its native spawn" is
    # valid when it carries the same meaning as an explicit SubagentStart term.
    semantic_requirements = (
        ("native wait control", ("wait_agent cycles", "exact-child wait", "exact bound child")),
        ("bounded wait duration", ("explicit 300-second timeout",)),
        (
            "native child start observation",
            ("subagentstart", "trusted observation of its native spawn", "host binds the first authorized worker call"),
        ),
        ("native terminal observation", ("subagentstop", "terminal host authority")),
        (
            "canonical result and terminal stop gate",
            ("all canonical results and terminal stops", "canonical results and matching terminal stops"),
        ),
        ("same-child question resume", ("same child resumes",)),
        ("new native question turn", ("new native turn",)),
        ("real user answer", ("real user answer",)),
    )
    if any(not any(marker in completion_policy for marker in markers) for _, markers in semantic_requirements):
        issues.append("prompt contract lacks native wait, question pause, or host observation semantics")
    try:
        from cortex_runtime.public_contracts import build_public_contracts

        public_contracts = build_public_contracts(
            agents=profiles_by_name,
            operation_kinds=profile_contract.get("operation_kinds", {}),
            model_routing=profile_contract.get("model_routing", {}),
        )
    except (ImportError, TypeError, ValueError) as exc:
        return [f"canonical public tool registry is unreadable: {type(exc).__name__}"]
    answer_contract = public_contracts.get("answer_orchestration_question")
    answer_schema = (
        ((answer_contract or {}).get("inputSchema") or {}).get("properties", {}).get("answer")
    )
    answer_guidance = str((answer_schema or {}).get("description") or "").lower()
    if not all(marker in answer_guidance for marker in (
        "exact arbitrary-unicode", "response", "durable question",
    )):
        issues.append("canonical answer guidance is missing from the answer input schema")
    if _contains_schema_identifier(str((answer_contract or {}).get("description") or ""), "answer"):
        issues.append("canonical answer argument guidance leaked into the tool description")
    public_operations = tuple(public_contracts)
    public_argument_names: set[str] = set()
    for public_contract in public_contracts.values():
        public_argument_names.update(_schema_property_names(public_contract.get("inputSchema")))
    model_visible_sources: list[tuple[str, str]] = []
    model_visible_sources.extend(("profiles.json", value) for value in _all_strings(profile_contract))
    model_visible_sources.extend(("prompt-contracts.json", value) for value in _all_strings(contract))
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
        model_visible_sources.append((path.name, prompt))
        missing_markers = [marker for marker in required_markers if marker not in prompt]
        if missing_markers:
            issues.append(f"{path.name}: missing role-delta markers {missing_markers}")
        if len(prompt.encode("utf-8")) > role_budget:
            issues.append(f"{path.name}: role delta exceeds {role_budget} bytes")
        for literal in contract["lint"]["forbidden_model_literals"]:
            if literal.lower() in prompt.lower():
                issues.append(f"{path.name}: prompt contains forbidden model literal {literal}")
        structured_form = _structured_prompt_form(prompt)
        if structured_form is not None:
            issues.append(f"{path.name}: role prompt duplicates a structured payload form {structured_form!r}")
        for phrase in lint_policy.get("forbidden_role_protocol_phrases", []):
            if phrase.lower() in prompt.lower():
                issues.append(f"{path.name}: role prompt duplicates shared protocol phrase {phrase!r}")
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
    scanned_skill_paths: set[Path] = set()
    for skill in sorted(required_skills):
        path = skill_root / skill / "SKILL.md"
        if not path.is_file():
            issues.append(f"missing required skill: {skill}")
            continue
        text = path.read_text(encoding="utf-8")
        model_visible_sources.append((str(path.relative_to(root)), text))
        scanned_skill_paths.add(path)
        if not text.startswith("---\n") or "name:" not in text.split("---\n", 2)[1]:
            issues.append(f"{skill}: missing YAML frontmatter")
        headings = [line.strip() for line in text.splitlines() if line.startswith("## ")]
        if len(headings) != len(set(headings)):
            issues.append(f"{skill}: duplicate level-2 headings")
        for required_heading in section_requirements.get(skill, []):
            if required_heading not in headings:
                issues.append(f"{skill}: missing required section {required_heading}")
        lifecycle_text = " ".join(text.lower().split())
        if skill == "cortex-control":
            skill_semantics = (
                ("bounded native wait", ("300-second timeout",)),
                ("exact-child wait routing", ("exact child identifier returned by the host", "exact child identifiers returned by the preceding")),
                ("wave-read precondition", ("`read_worker_wave` is forbidden until",)),
                ("repeat wait control", ("require another wait", "immediately wait again")),
                ("arbitrary unicode question", ("arbitrary-unicode question",)),
                ("complete question presentation", ("present it completely",)),
                ("terminal completion gate", ("`subagentstop` plus a canonical terminal result is the completion gate",)),
                ("trusted native lifecycle observation", ("trusted same-user local observation",)),
                ("fail closed", ("fails closed",)),
            )
            if any(
                not any(marker in lifecycle_text for marker in markers)
                for _, markers in skill_semantics
            ):
                issues.append(f"{skill}: native wait or host-observation routing is incomplete")
        elif skill == "orchestrator":
            routing_requirements = (
                (
                    "authoritative lifecycle cross-reference",
                    ("the complete lifecycle is defined once in `../cortex-control/skill.md`",),
                ),
                (
                    "coordinator-only routing boundary",
                    ("this skill supplies only coordinator routing",),
                ),
                (
                    "active-registry routing",
                    ("start through the active registry", "follow the control skill's"),
                ),
                (
                    "schema and lifecycle non-duplication boundary",
                    ("do not reproduce call shapes, response fields, repair payloads, or alternate lifecycle prose here",),
                ),
            )
            if any(
                not all(marker in lifecycle_text for marker in markers)
                for _, markers in routing_requirements
            ):
                issues.append("orchestrator: cortex-control routing boundary is incomplete")
            duplicated_lifecycle_details = (
                "explicit 300-second timeout",
                "another generic wait",
                "request another generic wait",
                "complete unicode question",
                "terminal subagentstop",
                "`subagentstop` is the exact terminal host authority",
                "trusted local `subagentstart`/`subagentstop` events",
            )
            if any(marker in lifecycle_text for marker in duplicated_lifecycle_details):
                issues.append("orchestrator: duplicates lifecycle details owned by cortex-control")
        if skill == "cortex-control":
            catalog = _tool_catalog_by_audience(text)
            expected_catalog = {
                audience: tuple(
                    name for name, public_contract in public_contracts.items()
                    if public_contract.get("audience") == audience
                )
                for audience in ("coordinator", "worker")
            }
            if catalog != expected_catalog:
                issues.append(
                    "cortex-control: generated tool catalog differs from canonical audience order"
                )
    for path in sorted(skill_root.rglob("*.md")):
        if path not in scanned_skill_paths:
            model_visible_sources.append((str(path.relative_to(root)), path.read_text(encoding="utf-8")))
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
        if v3 is not None:
            policy = "\n".join(_string_literals(v3))
            model_visible_sources.append((str(briefings_path.relative_to(root)), policy))
            policy_lower = policy.lower()
            if not all(marker in policy_lower for marker in (
                "durably publishing", "durable-question marker", "same-child follow-up",
                "real user answer", "terminal native marker",
                "no later task-scoped call",
            )):
                issues.append("v3 briefing lacks question-pause or terminal marker semantics")
            api_template = _api_template_operation(policy, public_operations)
            if api_template is not None:
                issues.append(
                    f"generated briefing policy contains a public API invocation template: {api_template}"
                )
    fixtures_path = root / "plugins/cortex/prompt-evals/fixtures.json"
    try:
        fixture_payload = json.loads(fixtures_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        issues.append("prompt-evals/fixtures.json is unreadable")
    else:
        model_visible_sources.extend(
            (str(fixtures_path.relative_to(root)), value)
            for value in _all_strings(fixture_payload)
        )
    for source, text in model_visible_sources:
        invocation = _api_template_operation(text, public_operations)
        if invocation is not None:
            issues.append(
                f"{source}: model-visible text duplicates public invocation template {invocation!r}"
            )
        for argument_name in sorted(public_argument_names):
            if _contains_schema_identifier(text, argument_name):
                issues.append(
                    f"{source}: model-visible text duplicates public argument name {argument_name!r}"
                )
                break
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
