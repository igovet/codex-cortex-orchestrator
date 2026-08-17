"""Focused coverage for release-fixture closure and lazy-artifact checks."""
from __future__ import annotations

import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
RUNTIME = ROOT / "plugins" / "cortex" / "scripts"
sys.path.insert(0, str(RUNTIME))

import cortex  # noqa: E402


def load_script(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / filename)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {filename}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


COLD_BOOT = load_script("cortex_cold_boot_smoke_fixture", "cortex-cold-boot-smoke.py")
LUNA_EVAL = load_script("cortex_luna_high_eval_fixture", "cortex-luna-high-eval.py")


class VerificationFixtureContractTests(unittest.TestCase):
    def test_fixture_closures_are_valid_and_have_no_open_blockers(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "project"
            project.mkdir()
            subprocess.run(["git", "init", "-q"], cwd=project, check=True)
            (project / "changed.txt").write_text("fixture\n", encoding="utf-8")

            for factory in (COLD_BOOT.passing_closure, LUNA_EVAL.passing_closure):
                closure = factory(project, "close")
                self.assertEqual(closure["decision"], "pass")
                self.assertEqual(closure["findings"], [])
                self.assertEqual(closure["verification"]["required_missing"], [])
                self.assertEqual(closure["workspace"]["untracked"], ["changed.txt"])
                self.assertEqual(cortex.sanitize_closure_payload(closure), closure)

    def test_deterministic_fixtures_complete_from_canonical_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            cold_base = base / "cold-boot"
            luna_base = base / "luna"
            cold_base.mkdir()
            luna_base.mkdir()
            cold_boot = COLD_BOOT.run(cold_base)
            luna = LUNA_EVAL.fixture_eval(luna_base)

        self.assertEqual(cold_boot["status"], "PASS")
        self.assertTrue(luna)
        self.assertTrue(all(item["outcome"] == "completed" for item in luna))


if __name__ == "__main__":
    unittest.main()
