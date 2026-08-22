#!/usr/bin/env python3
"""Run the deterministic Cortex Prompt Contract Architecture v3 linter."""
from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugins/cortex/scripts"))

from cortex_runtime.prompt_compiler import lint_prompt_sources  # noqa: E402


def main() -> int:
    issues = lint_prompt_sources(ROOT)
    if issues:
        for issue in issues:
            print("prompt-lint: " + issue)
        return 1
    print("prompt-lint: passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
