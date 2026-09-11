#!/usr/bin/env python3
"""Regenerate manifest.json from the frozen prompt/tree/scripts."""

import json
from pathlib import Path

from manifest_tools import ROOT, build_manifest


if __name__ == "__main__":
    (ROOT / "manifest.json").write_text(
        json.dumps(build_manifest(), indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"manifest": "manifest.json", "outcome": "written", "exit_code": 0}, sort_keys=True))
