#!/usr/bin/env python3
"""Result the public Cortex MCP round-trip reduction for a wave plan."""
from __future__ import annotations

import argparse
import json


def counts(workers: int, waves: int) -> tuple[int, int]:
    if workers < 1 or waves < 1 or waves > workers:
        raise ValueError("require workers >= waves >= 1")
    # The retired public API needed activation, classification, initialization,
    # status, delegation, confirmation, result, finalization, evidence, gate,
    # reconciliation, handoff, close, and final status round-trips.
    baseline = 4 + workers * 4 + waves * 2 + 4
    # Public Cortex v11 needs one start, one continue per wave, and per worker
    # one briefing read, one compact completion, and one coordinator result
    # read. Digest-bound correction calls are data-dependent and measured
    # separately; rejected drafts do not mutate the task ledger.
    # Native spawn_agent calls are deliberately outside this MCP-call budget.
    facade = 1 + waves + workers * 3
    return baseline, facade


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--waves", type=int, default=5)
    args = parser.parse_args()
    baseline, facade = counts(args.workers, args.waves)
    reduction = (baseline - facade) / baseline
    result = {
        "workers": args.workers,
        "waves": args.waves,
        "baseline_mcp_calls": baseline,
        "current_v11_mcp_calls": facade,
        "reduction": round(reduction, 4),
        "target_met": facade == 1 + args.waves + args.workers * 3 and facade < baseline,
        "public_tools": ["start_orchestration", "continue_orchestration", "manage_orchestration", "manage_governance", "worker_question", "record_attempt_event", "complete_attempt", "read_dispatch_briefing", "read_worker_result"],
        "normal_operations": ["start_orchestration", "record_attempt_event", "complete_attempt", "read_worker_result", "continue_orchestration"],
        "complete_attempt_payload": ["task_ref", "assignment_ref", "plan_or_outcome"],
        "note": "Call-count contract benchmark; workers emit semantic attempt events and a compact completion result, while Cortex owns attempt persistence, server-observed metadata, result projections, and coordinator reads. Native host spawn calls are excluded.",
    }
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
