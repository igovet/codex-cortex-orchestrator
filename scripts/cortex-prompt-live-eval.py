#!/usr/bin/env python3
"""Explicitly run the real Luna-high canonical prompt evaluation."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugins/cortex/scripts"))

from cortex_runtime.prompt_live_eval import run_live_prompt_evals  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="opt in to real Codex calls; disabled by default")
    parser.add_argument("--model", default="gpt-5.6-luna")
    parser.add_argument("--reasoning-effort", default="high")
    args = parser.parse_args()
    try:
        results = run_live_prompt_evals(
            enabled=args.live, model=args.model, reasoning_effort=args.reasoning_effort,
        )
    except (AssertionError, RuntimeError, ValueError) as exc:
        print("prompt-live-eval: " + str(exc), file=sys.stderr)
        return 2
    print(json.dumps({"live": args.live, "results": results}, sort_keys=True))
    return 0 if all(item.get("status") in {"PASS", "SKIP"} for item in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
