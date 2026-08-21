#!/usr/bin/env python3
"""Run Cortex prompt-eval fixtures; live mode is intentionally fail-closed."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugins/cortex/scripts"))

from cortex_runtime.prompt_eval import run_prompt_ab_evals, run_prompt_evals  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="require an explicit Luna-high live evaluator")
    parser.add_argument("--model", default=None)
    parser.add_argument("--reasoning-effort", default=None)
    args = parser.parse_args()
    try:
        case_ids = run_prompt_evals(
            live=args.live,
            model=args.model,
            reasoning_effort=args.reasoning_effort,
        )
        ab_results = run_prompt_ab_evals()
    except (AssertionError, RuntimeError, ValueError) as exc:
        print("prompt-eval: " + str(exc), file=sys.stderr)
        return 2
    print("prompt-eval: passed " + ", ".join(case_ids))
    print("prompt-eval A/B: " + json.dumps(ab_results, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
