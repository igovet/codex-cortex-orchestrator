"""Focused contract checks for the deterministic source-mode MCP smoke."""
from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "scripts" / "cortex-cold-boot-smoke.py"
SPEC = importlib.util.spec_from_file_location("cortex_cold_boot_smoke", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
SMOKE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SMOKE)


def test_cold_boot_fixture_uses_only_canonical_phases() -> None:
    phases = [
        str(worker["phase"])
        for wave in SMOKE.waves()
        for worker in wave["workers"]
    ]

    assert phases == ["discover"]
    assert SMOKE.cortex.DATABASE_SCHEMA_VERSION == 17
