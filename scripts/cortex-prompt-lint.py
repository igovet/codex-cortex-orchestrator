#!/usr/bin/env python3
"""Lint the task_ref-only Cortex model instruction boundary."""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.dont_write_bytecode = True
os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins/cortex"
sys.path.insert(0, str(PLUGIN / "scripts"))


def main() -> int:
    from cortex_runtime.public_contracts import build_public_contracts

    issues: list[str] = []
    contracts = build_public_contracts()
    if len(contracts) != 14:
        issues.append("public catalogue must contain exactly 14 operations")
    private = {
        "assignment_ref", "continuation_ref", "binding_ref", "report_ref",
        "plan_ref", "decision_ref", "item_ref", "cursor", "digest", "handles",
    }
    for name, contract in contracts.items():
        schema = contract["inputSchema"]
        if schema.get("additionalProperties") is not False:
            issues.append(f"{name} input is not closed")
        properties = set(schema.get("properties", {}))
        exposed = properties & private
        if exposed:
            issues.append(f"{name} exposes private properties: {sorted(exposed)}")
        identifiers = {field for field in properties if field.endswith("_ref")}
        if identifiers - {"task_ref"}:
            issues.append(f"{name} exposes a non-task identifier")

    skill_paths = (
        PLUGIN / "skills/orchestrator/SKILL.md",
        PLUGIN / "skills/cortex-control/SKILL.md",
        PLUGIN / "skills/context-compaction/SKILL.md",
    )
    combined = "\n".join(path.read_text(encoding="utf-8") for path in skill_paths).lower()
    for forbidden in ("consume_assignment_evidence", "next_action", "suggested_", "post-anchor-engine.md"):
        if forbidden in combined:
            issues.append(f"model instructions contain removed protocol term {forbidden}")
    for required in (
        "dynamic dag", "stores only `task_ref`", "assignment view",
        "never an imperative workflow command", "no workflow or governance admission",
    ):
        if required not in combined:
            issues.append(f"model instructions omit required boundary: {required}")

    for issue in issues:
        print("contract-lint: " + issue)
    if issues:
        return 1
    print("contract-lint: passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
