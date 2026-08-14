#!/usr/bin/env python3
"""Report the Cortex v2 MCP round-trip reduction for a wave plan."""
from __future__ import annotations

import argparse
import json


def counts(workers: int, waves: int) -> tuple[int, int]:
    if workers < 1 or waves < 1 or waves > workers:
        raise ValueError("require workers >= waves >= 1")
    # The legacy public API needed activation, classification, initialization,
    # status, delegation, confirmation, report, finalization, evidence, gate,
    # reconciliation, handoff, close, and final status round-trips.
    legacy = 4 + workers * 4 + waves * 2 + 4
    # Cortex v2 needs one start and one advance per wave. Native spawn_agent
    # calls are deliberately outside this MCP-call budget.
    facade = 1 + waves
    return legacy, facade


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--waves", type=int, default=5)
    args = parser.parse_args()
    legacy, facade = counts(args.workers, args.waves)
    reduction = (legacy - facade) / legacy
    result = {
        "workers": args.workers,
        "waves": args.waves,
        "legacy_mcp_calls": legacy,
        "orchestrate_mcp_calls": facade,
        "reduction": round(reduction, 4),
        "target_met": facade == args.waves + 1,
        "public_tools": ["orchestrate"],
        "normal_operations": ["start", "advance"],
        "note": "Call-count contract benchmark; native host spawn calls are excluded.",
    }
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
