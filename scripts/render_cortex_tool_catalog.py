#!/usr/bin/env python3
"""Render the Cortex control skill's audience-filtered public tool catalog."""
from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SKILL_PATH = ROOT / "plugins/cortex/skills/cortex-control/SKILL.md"
BEGIN_MARKER = "<!-- BEGIN GENERATED CORTEX TOOL CATALOG -->"
END_MARKER = "<!-- END GENERATED CORTEX TOOL CATALOG -->"
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

    return build_public_contracts(agents=agents)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT, help="repository tree")
    parser.add_argument("--check", action="store_true", help="fail if the catalog is stale")
    return parser.parse_args()


def main() -> int:
    arguments = parse_args()
    root = arguments.root.resolve(strict=False)
    skill_path = root / SKILL_PATH.relative_to(ROOT)
    current = skill_path.read_text(encoding="utf-8")
    expected = expected_skill_text(current, _load_contracts(root))
    if arguments.check:
        if current != expected:
            print(f"tool catalog is stale: {skill_path}", file=sys.stderr)
            return 1
        print(f"tool catalog is current: {skill_path}")
        return 0
    skill_path.write_text(expected, encoding="utf-8")
    print(f"rendered tool catalog: {skill_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
