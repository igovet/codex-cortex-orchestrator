#!/usr/bin/env python3
"""Render and verify the uniform Cortex V12 tool and routing catalogues."""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any


sys.dont_write_bytecode = True
os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")

ROOT = Path(__file__).resolve().parents[1]
CONTROL_RELATIVE = Path("plugins/cortex/skills/cortex-control/SKILL.md")
ORCHESTRATOR_RELATIVE = Path("plugins/cortex/skills/orchestrator/SKILL.md")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT, help="repository tree")
    parser.add_argument("--check", action="store_true", help="fail when bundled catalogues drift from their registries")
    return parser.parse_args()


def section(markdown: str, heading: str) -> str:
    match = re.search(
        rf"(?ms)^##+\s+{re.escape(heading)}\s*$\n(.*?)(?=^##+\s+|\Z)",
        markdown,
    )
    if match is None:
        raise ValueError(f"missing Markdown section: {heading}")
    return match.group(1)


def load_contracts(root: Path) -> dict[str, dict[str, Any]]:
    runtime_path = str(root / "plugins/cortex/scripts")
    if runtime_path not in sys.path:
        sys.path.insert(0, runtime_path)
    from cortex_runtime.public_contracts import V12_TOOL_NAMES, build_public_contracts

    contracts = build_public_contracts()
    if tuple(contracts) != V12_TOOL_NAMES or not contracts:
        raise ValueError("runtime tool registry is not a non-empty canonical ordered catalogue")
    if any(not isinstance(name, str) or not name or not isinstance(value, Mapping) for name, value in contracts.items()):
        raise ValueError("runtime tool registry has an invalid entry")
    return contracts


def load_routing(root: Path) -> tuple[tuple[str, str, str], ...]:
    path = root / "plugins/cortex/profiles.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"profiles.json is unreadable: {exc}") from exc
    routing = payload.get("model_routing") if isinstance(payload, dict) else None
    recommendations = routing.get("recommendations") if isinstance(routing, Mapping) else None
    if not isinstance(recommendations, list) or not recommendations:
        raise ValueError("profiles.json has no model-routing recommendations")
    rows: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    for recommendation in recommendations:
        if not isinstance(recommendation, Mapping):
            raise ValueError("model-routing recommendation must be an object")
        model = recommendation.get("model")
        effort = recommendation.get("recommended_effort")
        choose_for = recommendation.get("choose_for")
        if (
            not isinstance(model, str)
            or not model
            or model in seen
            or not isinstance(effort, str)
            or not effort
            or not isinstance(choose_for, str)
            or not choose_for.strip()
        ):
            raise ValueError("model-routing recommendation is invalid")
        seen.add(model)
        rows.append((model, effort, choose_for.strip()))
    return tuple(rows)


def render_tool_catalog(contracts: Mapping[str, Mapping[str, Any]]) -> str:
    """Render the shared uniform catalogue without V11 audience projections."""
    lines = ["| Tool | Semantic purpose |", "| --- | --- |"]
    for name, contract in contracts.items():
        description = contract.get("description")
        if not isinstance(description, str) or not description.strip():
            raise ValueError(f"tool {name!r} has no semantic description")
        lines.append(f"| `{name}` | {description.strip()} |")
    return "\n".join(lines)


def render_model_routing(rows: tuple[tuple[str, str, str], ...]) -> str:
    lines = [
        "| Exact model | Recommended effort | Recommend for |",
        "| --- | --- | --- |",
    ]
    lines.extend(
        f"| `{model}` | `{effort}` | {choose_for} |"
        for model, effort, choose_for in rows
    )
    return "\n".join(lines)


def catalog_names(markdown: str) -> tuple[str, ...]:
    catalog = section(markdown, "Public semantic catalog")
    return tuple(re.findall(r"^\|\s*`([^`]+)`\s*\|", catalog, re.MULTILINE))


def routing_rows(markdown: str) -> tuple[tuple[str, str, str], ...]:
    routing = section(markdown, "Per-delegation model selection")
    return tuple(
        (model, effort, choose_for.strip())
        for model, effort, choose_for in re.findall(
            r"^\|\s*`([^`]+)`\s*\|\s*`([^`]+)`\s*\|\s*(.*?)\s*\|\s*$",
            routing,
            re.MULTILINE,
        )
    )


def verify(root: Path) -> list[str]:
    contracts = load_contracts(root)
    expected_tools = tuple(contracts)
    expected_routing = load_routing(root)
    control = (root / CONTROL_RELATIVE).read_text(encoding="utf-8")
    orchestrator = (root / ORCHESTRATOR_RELATIVE).read_text(encoding="utf-8")
    errors: list[str] = []
    if catalog_names(control) != expected_tools:
        errors.append("cortex-control public tool names differ from the uniform runtime catalogue")
    if routing_rows(orchestrator) != expected_routing:
        errors.append("orchestrator model/effort routing rows differ from profiles.json")
    return errors


def main() -> int:
    args = parse_args()
    root = args.root.resolve(strict=False)
    try:
        contracts = load_contracts(root)
        routing = load_routing(root)
        if args.check:
            errors = verify(root)
            if errors:
                for error in errors:
                    print(f"catalog validation failed: {error}", file=sys.stderr)
                return 1
            print("Cortex uniform tool catalog and model-routing table are current")
            return 0
        print("## Public semantic catalog")
        print()
        print(render_tool_catalog(contracts))
        print()
        print("## Per-delegation model selection")
        print()
        print(render_model_routing(routing))
        return 0
    except (ImportError, OSError, RuntimeError, ValueError) as exc:
        print(f"catalog validation failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
