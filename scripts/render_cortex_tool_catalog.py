#!/usr/bin/env python3
"""Render canonical Cortex registry data into bounded generated skill regions."""
from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SKILL_PATH = ROOT / "plugins/cortex/skills/cortex-control/SKILL.md"
ORCHESTRATOR_SKILL_PATH = ROOT / "plugins/cortex/skills/orchestrator/SKILL.md"
BEGIN_MARKER = "<!-- BEGIN GENERATED CORTEX TOOL CATALOG -->"
END_MARKER = "<!-- END GENERATED CORTEX TOOL CATALOG -->"
MODEL_BEGIN_MARKER = "<!-- BEGIN GENERATED CORTEX MODEL ROUTING -->"
MODEL_END_MARKER = "<!-- END GENERATED CORTEX MODEL ROUTING -->"
CAPABILITY_BEGIN_MARKER = "<!-- BEGIN GENERATED CORTEX PROFILE CAPABILITIES -->"
CAPABILITY_END_MARKER = "<!-- END GENERATED CORTEX PROFILE CAPABILITIES -->"
AUDIENCES = (("coordinator", "Coordinator tools"), ("worker", "Worker tools"))


def render_tool_catalog(contracts: Mapping[str, Mapping[str, Any]]) -> str:
    """Render registry order and audience ownership without reproducing schemas."""
    lines: list[str] = []
    seen: set[str] = set()
    for audience, heading in AUDIENCES:
        if lines:
            lines.append("")
        lines.extend((
            f"### {heading}",
            "",
            "| Tool | When to use |",
            "| --- | --- |",
        ))
        for name, contract in contracts.items():
            if contract.get("audience") != audience:
                continue
            if not isinstance(name, str) or not name or name in seen:
                raise ValueError("canonical public tool names must be unique non-empty strings")
            seen.add(name)
            lines.append(f"| `{name}` | Use to {name.replace('_', ' ')}. |")
    unsupported = [
        name for name, contract in contracts.items()
        if contract.get("audience") not in {audience for audience, _ in AUDIENCES}
    ]
    if unsupported:
        raise ValueError("canonical public tools contain an unsupported audience")
    if seen != set(contracts):
        raise ValueError("generated public tool catalog is incomplete")
    return "\n".join(lines)


def replace_generated_catalog(skill_text: str, catalog: str) -> str:
    """Replace the one bounded generated region and preserve all authored prose."""
    if skill_text.count(BEGIN_MARKER) != 1 or skill_text.count(END_MARKER) != 1:
        raise ValueError("cortex-control must contain exactly one tool-catalog marker pair")
    prefix, remainder = skill_text.split(BEGIN_MARKER, 1)
    _, suffix = remainder.split(END_MARKER, 1)
    return f"{prefix}{BEGIN_MARKER}\n{catalog}\n{END_MARKER}{suffix}"


def expected_skill_text(
    skill_text: str,
    contracts: Mapping[str, Mapping[str, Any]],
) -> str:
    return replace_generated_catalog(skill_text, render_tool_catalog(contracts))


def render_model_routing(model_routing: Mapping[str, Any]) -> str:
    """Render the one canonical per-worker selection policy without API schemas."""
    selection = model_routing.get("selection_policy")
    if not isinstance(selection, Mapping):
        raise ValueError("model routing has no canonical selection policy")
    governance_scope = selection.get("governance_scope")
    principle = selection.get("principle")
    routes = selection.get("routes")
    if (
        not isinstance(governance_scope, str)
        or not governance_scope.strip()
        or not isinstance(principle, str)
        or not principle.strip()
        or not isinstance(routes, list)
        or not routes
    ):
        raise ValueError("canonical model selection policy is incomplete")
    capabilities = model_routing.get("model_capabilities")
    if not isinstance(capabilities, list) or not capabilities:
        raise ValueError("model routing capability registry is invalid")
    supported_models = [
        item.get("model") for item in capabilities if isinstance(item, Mapping)
    ]
    supported_efforts = {
        effort
        for item in capabilities if isinstance(item, Mapping)
        for effort in item.get("reasoning_efforts") or []
        if isinstance(effort, str)
    }
    lines = [
        governance_scope.strip(),
        "",
        principle.strip(),
        "",
        "| Exact model | Recommended effort | Recommend for |",
        "| --- | --- | --- |",
    ]
    rendered_models: list[str] = []
    for route in routes:
        if not isinstance(route, Mapping):
            raise ValueError("model selection route must be an object")
        model = route.get("model")
        effort = route.get("recommended_effort")
        choose_for = route.get("choose_for")
        if (
            not isinstance(model, str)
            or model not in supported_models
            or model in rendered_models
            or not isinstance(effort, str)
            or effort not in supported_efforts
            or not isinstance(choose_for, str)
            or not choose_for.strip()
        ):
            raise ValueError("model selection route is invalid")
        rendered_models.append(model)
        lines.append(
            f"| `{model}` | `{effort}` | {choose_for.strip()} |"
        )
    if rendered_models != supported_models:
        raise ValueError("model selection routes must cover supported models in canonical order")
    return "\n".join(lines)


def expected_orchestrator_skill_text(
    skill_text: str,
    model_routing: Mapping[str, Any],
) -> str:
    """Replace the orchestrator's generated routing region from profiles.json."""
    if skill_text.count(MODEL_BEGIN_MARKER) != 1 or skill_text.count(MODEL_END_MARKER) != 1:
        raise ValueError("orchestrator must contain exactly one model-routing marker pair")
    prefix, remainder = skill_text.split(MODEL_BEGIN_MARKER, 1)
    _, suffix = remainder.split(MODEL_END_MARKER, 1)
    rendered = render_model_routing(model_routing)
    return f"{prefix}{MODEL_BEGIN_MARKER}\n{rendered}\n{MODEL_END_MARKER}{suffix}"


def render_profile_capabilities(profile_contract: Mapping[str, Any]) -> str:
    """Render operation semantics and the profile capability matrix from one registry."""
    operation_kinds = profile_contract.get("operation_kinds")
    profiles = profile_contract.get("profiles")
    if (
        not isinstance(operation_kinds, Mapping)
        or list(operation_kinds) != ["inspect", "modify", "verify", "close"]
        or not isinstance(profiles, list)
        or not profiles
    ):
        raise ValueError("profile capability registry is invalid")
    lines = [
        "| Operation kind | Meaning |",
        "| --- | --- |",
    ]
    for name, meaning in operation_kinds.items():
        if not isinstance(meaning, str) or not meaning.strip():
            raise ValueError("operation-kind meaning is invalid")
        lines.append(f"| `{name}` | {meaning.strip()} |")
    lines.extend((
        "",
        "| Profile | Allowed operation kinds |",
        "| --- | --- |",
    ))
    seen: set[str] = set()
    for profile in profiles:
        if not isinstance(profile, Mapping):
            raise ValueError("profile capability entry is invalid")
        name = profile.get("name")
        capabilities = profile.get("operation_kinds")
        if (
            not isinstance(name, str)
            or not name
            or name in seen
            or not isinstance(capabilities, list)
            or not capabilities
            or any(capability not in operation_kinds for capability in capabilities)
        ):
            raise ValueError("profile capability entry is invalid")
        seen.add(name)
        lines.append(
            f"| `{name}` | {', '.join(f'`{capability}`' for capability in capabilities)} |"
        )
    return "\n".join(lines)


def expected_profile_capability_skill_text(
    skill_text: str,
    profile_contract: Mapping[str, Any],
) -> str:
    """Replace the generated capability region from profiles.json."""
    if skill_text.count(CAPABILITY_BEGIN_MARKER) != 1 or skill_text.count(CAPABILITY_END_MARKER) != 1:
        raise ValueError("orchestrator must contain exactly one capability marker pair")
    prefix, remainder = skill_text.split(CAPABILITY_BEGIN_MARKER, 1)
    _, suffix = remainder.split(CAPABILITY_END_MARKER, 1)
    rendered = render_profile_capabilities(profile_contract)
    return f"{prefix}{CAPABILITY_BEGIN_MARKER}\n{rendered}\n{CAPABILITY_END_MARKER}{suffix}"


def _load_contracts(root: Path) -> dict[str, dict[str, Any]]:
    plugin = root / "plugins/cortex"
    profiles = json.loads((plugin / "profiles.json").read_text(encoding="utf-8"))
    entries = profiles.get("profiles")
    if not isinstance(entries, list):
        raise ValueError("profiles.json has no profile list")
    agents = {
        str(item["name"]): item
        for item in entries
        if isinstance(item, dict) and isinstance(item.get("name"), str)
    }
    if len(agents) != len(entries) or not agents:
        raise ValueError("profiles.json profile names are invalid")
    runtime_path = str(plugin / "scripts")
    if runtime_path not in sys.path:
        sys.path.insert(0, runtime_path)
    from cortex_runtime.public_contracts import build_public_contracts

    return build_public_contracts(
        agents=agents,
        operation_kinds=profiles.get("operation_kinds", {}),
        model_routing=profiles.get("model_routing", {}),
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT, help="repository tree")
    parser.add_argument("--check", action="store_true", help="fail if the catalog is stale")
    return parser.parse_args()


def main() -> int:
    arguments = parse_args()
    root = arguments.root.resolve(strict=False)
    skill_path = root / SKILL_PATH.relative_to(ROOT)
    orchestrator_skill_path = root / ORCHESTRATOR_SKILL_PATH.relative_to(ROOT)
    current = skill_path.read_text(encoding="utf-8")
    profile_contract = json.loads(
        (root / "plugins/cortex/profiles.json").read_text(encoding="utf-8")
    )
    expected = expected_skill_text(current, _load_contracts(root))
    orchestrator_current = orchestrator_skill_path.read_text(encoding="utf-8")
    orchestrator_expected = expected_orchestrator_skill_text(
        orchestrator_current,
        profile_contract.get("model_routing") or {},
    )
    orchestrator_expected = expected_profile_capability_skill_text(
        orchestrator_expected,
        profile_contract,
    )
    if arguments.check:
        stale_paths = []
        if current != expected:
            stale_paths.append(skill_path)
        if orchestrator_current != orchestrator_expected:
            stale_paths.append(orchestrator_skill_path)
        if stale_paths:
            for stale_path in stale_paths:
                print(f"generated skill region is stale: {stale_path}", file=sys.stderr)
            return 1
        print(f"generated skill regions are current: {skill_path}, {orchestrator_skill_path}")
        return 0
    skill_path.write_text(expected, encoding="utf-8")
    orchestrator_skill_path.write_text(orchestrator_expected, encoding="utf-8")
    print(f"rendered skill regions: {skill_path}, {orchestrator_skill_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
