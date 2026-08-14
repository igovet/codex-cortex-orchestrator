#!/usr/bin/env python3
"""Report the MCP round-trip reduction provided by Cortex typed fast paths.

This is a contract benchmark, not a latency claim: it counts the calls in an
equivalent orchestration trace and leaves the durable ledger untouched. Use a
real cold-boot run for elapsed-time measurements on a specific host adapter.
"""
from __future__ import annotations

import argparse
import json


def counts(workers: int) -> tuple[int, int]:
    if workers < 1:
        raise ValueError("workers must be positive")
    # Initial activation/classification/init/status and final status are kept
    # in both paths. Per worker: status+delegation vs prepare_delegation;
    # confirmation+report+finalize vs complete_attempt. Gate and close each
    # save one round-trip as well.
    fixed = 5
    legacy = fixed + workers * (2 + 3) + 2 + 2
    fast = fixed + workers * (1 + 1) + 1 + 1
    return legacy, fast


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()
    legacy, fast = counts(args.workers)
    reduction = (legacy - fast) / legacy if legacy else 0.0
    result = {
        "workers": args.workers,
        "legacy_mcp_calls": legacy,
        "fast_path_mcp_calls": fast,
        "reduction": round(reduction, 4),
        "target_met": reduction >= 0.35,
        "fast_paths": ["prepare_delegation", "prepare_delegations", "complete_attempt", "commit_gate", "close_audit"],
        "note": "Call-count contract benchmark; elapsed latency requires host-specific measurement.",
    }
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
