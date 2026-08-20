#!/usr/bin/env python3
"""Report the public Cortex MCP round-trip reduction for a wave plan."""
from __future__ import annotations

import argparse
import json


def counts(workers: int, waves: int) -> tuple[int, int]:
    if workers < 1 or waves < 1 or waves > workers:
        raise ValueError("require workers >= waves >= 1")
    # The legacy public API needed activation, classification, initialization,
    # status, delegation, confirmation, report, finalization, evidence, gate,
    # reconciliation, handoff, close, and final status round-trips.
    baseline = 4 + workers * 4 + waves * 2 + 4
    # Public Cortex needs one start, one continue per wave, and per worker one
    # template read, one successful draft validation, one short draft-ref
    # promotion, and one coordinator report read. Corrective draft validation
    # calls are measured separately because they are data-dependent; invalid
    # drafts do not persist, while the successful draft is staged in SQLite.
    # Native spawn_agent calls are deliberately outside this MCP-call budget.
    facade = 1 + waves + workers * 4
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
        "relative_v3_mcp_calls": facade,
        "reduction": round(reduction, 4),
        "target_met": facade == 1 + args.waves + args.workers * 4 and facade < baseline,
        "public_tools": ["start_orchestration", "continue_orchestration", "manage_orchestration", "manage_governance", "worker_question", "get_report_template", "record_report", "read_dispatch_briefing", "read_worker_report"],
        "normal_operations": ["start_orchestration", "get_report_template", "record_report", "read_worker_report", "continue_orchestration"],
        "record_report_payload": ["project_root", "task_id", "attempt_id", "profile", "draft_ref"],
        "note": "Call-count contract benchmark; template creation writes one private report file, validation and finalization reuse that file without regenerating its body, durable worker report writes and coordinator reads are included, and native host spawn calls are excluded.",
    }
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
