"""Focused v11 hook-trust synchronization contracts."""
from __future__ import annotations

import importlib.util
import json
import shutil
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "scripts" / "sync-cortex-hook-trust.py"
SPEC = importlib.util.spec_from_file_location("sync_cortex_hook_trust", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
SYNC = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = SYNC
SPEC.loader.exec_module(SYNC)


class SyncCortexHookTrustTests(unittest.TestCase):
    def installed_fixture(self, base: Path) -> Path:
        installed = base / "cache" / "cortex" / "cortex" / "11.0.1+codex.test"
        (installed / "hooks").mkdir(parents=True)
        (installed / "scripts").mkdir()
        source_manifest = ROOT / "plugins" / "cortex" / "hooks" / "hooks.json"
        shutil.copyfile(source_manifest, installed / "hooks" / "hooks.json")
        (installed / "scripts" / "cortex_hook.py").write_text("# fixture hook\n", encoding="utf-8")
        return installed

    def hook_result(self, installed: Path, cwd: Path) -> tuple[dict, dict[str, str]]:
        hashes = {
            key: f"sha256:{index:064x}"
            for index, key in enumerate(sorted(SYNC._expected_hook_keys(installed)), start=1)
        }
        hooks = [
            {
                "key": key,
                "pluginId": SYNC.PLUGIN_ID,
                "enabled": True,
                "currentHash": digest,
                "sourcePath": str(installed / "hooks" / "hooks.json"),
                "command": str(installed / "scripts" / "cortex_hook.py"),
            }
            for key, digest in hashes.items()
        ]
        return {"data": [{"cwd": str(cwd), "hooks": hooks, "errors": []}]}, hashes

    def trust_config(self, hashes: dict[str, str], *, stale: bool = False) -> str:
        lines = [
            'unrelated_setting = "preserve"\n',
            '\n',
            '[hooks.state."other-plugin:hooks/hooks.json:post_tool_use:0:0"]\n',
            'trusted_hash = "sha256:' + 'f' * 64 + '"\n',
            '\n',
        ]
        for key, digest in sorted(hashes.items()):
            lines.extend([
                f'[hooks.state.{json.dumps(key)}]\n',
                f'trusted_hash = "{digest}"\n',
                '\n',
            ])
        if stale:
            for event in ("pre_tool_use", "user_prompt_submit"):
                key = f"{SYNC.PLUGIN_ID}:hooks/hooks.json:{event}:0:0"
                lines.extend([
                    f'[hooks.state.{json.dumps(key)}]\n',
                    'trusted_hash = "sha256:' + 'e' * 64 + '"\n',
                    '\n',
                ])
        return "".join(lines)

    def test_exact_v11_manifest_set_and_hook_rows_are_required(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            installed = self.installed_fixture(base)
            cwd = base / "workspace"
            result, expected = self.hook_result(installed, cwd)
            observed = SYNC._hook_hashes_from_result(result, cwd, installed)
            self.assertEqual(observed, expected)
            self.assertEqual(
                set(SYNC._expected_hook_keys(installed)),
                {
                    f"{SYNC.PLUGIN_ID}:hooks/hooks.json:{event}:0:0"
                    for event in ("post_tool_use", "session_start", "subagent_start", "subagent_stop", "stop")
                },
            )

            extra = json.loads((installed / "hooks/hooks.json").read_text(encoding="utf-8"))
            extra["hooks"]["UserPromptSubmit"] = []
            (installed / "hooks/hooks.json").write_text(json.dumps(extra), encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "exactly the v11 lifecycle events"):
                SYNC._expected_hook_keys(installed)

    def test_source_and_installed_manifests_are_byte_identical(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            installed = self.installed_fixture(Path(directory))
            source = ROOT / "plugins" / "cortex" / "hooks" / "hooks.json"
            self.assertEqual(source.read_bytes(), (installed / "hooks/hooks.json").read_bytes())

    def test_update_prunes_only_stale_cortex_trust_and_preserves_unrelated_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            installed = self.installed_fixture(base)
            result, hashes = self.hook_result(installed, base / "workspace")
            observed = SYNC._hook_hashes_from_result(result, base / "workspace", installed)
            config = base / "config.toml"
            config.write_text(self.trust_config(observed, stale=True), encoding="utf-8")

            SYNC.synchronize(config, hashes, check=False)
            parsed = tomllib.loads(config.read_text(encoding="utf-8"))
            state = parsed["hooks"]["state"]
            cortex_state = {key: value for key, value in state.items() if key.startswith(f"{SYNC.PLUGIN_ID}:")}
            self.assertEqual(set(cortex_state), set(hashes))
            self.assertEqual(state["other-plugin:hooks/hooks.json:post_tool_use:0:0"]["trusted_hash"], "sha256:" + "f" * 64)
            self.assertEqual(parsed["unrelated_setting"], "preserve")
            SYNC.synchronize(config, hashes, check=True)

    def test_check_rejects_extra_stale_cortex_trust_without_mutating_config(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            installed = self.installed_fixture(base)
            result, hashes = self.hook_result(installed, base / "workspace")
            observed = SYNC._hook_hashes_from_result(result, base / "workspace", installed)
            config = base / "config.toml"
            config.write_text(self.trust_config(observed, stale=True), encoding="utf-8")
            before = config.read_bytes()
            with self.assertRaisesRegex(RuntimeError, "stale="):
                SYNC.synchronize(config, hashes, check=True)
            self.assertEqual(config.read_bytes(), before)

    def test_helper_has_no_legacy_hook_or_bypass_marker(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")
        for marker in ("pre_tool_use", "PreToolUse", "user_prompt_submit", "CORTEX_WORKER_BINDING_JSON", "dangerously-bypass-approvals-and-sandbox"):
            self.assertNotIn(marker, source)


if __name__ == "__main__":
    unittest.main()
