from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "scripts/cortex-host-preflight.py"


class CortexHostPreflightTests(unittest.TestCase):
    def write_executable(self, path: Path, content: str) -> None:
        path.write_text(content, encoding="utf-8")
        path.chmod(path.stat().st_mode | stat.S_IXUSR)

    def make_plugin(self, root: Path) -> None:
        (root / ".codex-plugin").mkdir(parents=True)
        (root / "scripts").mkdir()
        (root / "hooks").mkdir()
        (root / ".codex-plugin/plugin.json").write_text(json.dumps({"version": "6.6.0"}), encoding="utf-8")
        (root / ".mcp.json").write_text(
            json.dumps({"mcpServers": {"cortex": {"command": "./scripts/cortex-launcher", "args": ["./scripts/cortex.py"], "cwd": "."}}}),
            encoding="utf-8",
        )
        self.write_executable(root / "scripts/cortex-launcher", "#!/bin/sh\nexit 0\n")
        (root / "scripts/cortex.py").write_text("# test MCP entrypoint\n", encoding="utf-8")
        (root / "scripts/cortex_hook.py").write_text("# test Cortex hook\n", encoding="utf-8")
        (root / "hooks/hooks.json").write_text("{}\n", encoding="utf-8")

    def run_preflight(self, environment: dict[str, str], *extra: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(SCRIPT), "--json", *extra],
            cwd=ROOT,
            env=environment,
            text=True,
            capture_output=True,
            check=False,
        )

    def make_aligned_host(
        self,
        base: Path,
        plugin: Path,
        *,
        install_cache: bool = True,
        install_codex: bool = True,
    ) -> tuple[dict[str, str], Path]:
        bin_dir = base / "bin"
        bin_dir.mkdir()
        if install_codex:
            installed_root = base / "home/.codex/plugins/cache/cortex/cortex/6.6.0"
            hashes = {
                "pre_tool_use": "sha256:" + "1" * 64,
                "post_tool_use": "sha256:" + "2" * 64,
                "session_start": "sha256:" + "3" * 64,
                "subagent_start": "sha256:" + "4" * 64,
                "subagent_stop": "sha256:" + "5" * 64,
                "stop": "sha256:" + "6" * 64,
            }
            hook_rows = []
            for name, digest in hashes.items():
                hook_rows.append(
                    {
                        "key": f"cortex@cortex:hooks/hooks.json:{name}:0:0",
                        "pluginId": "cortex@cortex",
                        "enabled": True,
                        "trustStatus": "trusted",
                        "currentHash": digest,
                        "sourcePath": str(installed_root / "hooks/hooks.json"),
                        "command": f"{installed_root / 'scripts/cortex-launcher'} {installed_root / 'scripts/cortex_hook.py'}",
                    }
                )
            fake_codex = (
                f"#!{sys.executable}\n"
                "import json, sys\n"
                "version = '6.6.0'\n"
                f"installed_root = {str(installed_root)!r}\n"
                f"workspace = {str(ROOT)!r}\n"
                f"hooks = {hook_rows!r}\n"
                "args = sys.argv[1:]\n"
                "if args == ['--version']:\n"
                "    print('codex 1.2.3')\n"
                "elif args == ['plugin', 'list', '--json']:\n"
                "    print(json.dumps({'installed': [{'pluginId': 'cortex@cortex', 'name': 'cortex', 'version': version, 'installed': True, 'enabled': True}]}))\n"
                "elif args == ['app-server', '--stdio']:\n"
                "    for line in sys.stdin:\n"
                "        request = json.loads(line)\n"
                "        if request.get('id') == 1:\n"
                "            print(json.dumps({'id': 1, 'result': {}}), flush=True)\n"
                "        elif request.get('id') == 2:\n"
                "            print(json.dumps({'id': 2, 'result': {'data': [{'cwd': workspace, 'hooks': hooks, 'errors': []}]}}), flush=True)\n"
                "            break\n"
                "else:\n"
                "    raise SystemExit(2)\n"
            )
            self.write_executable(bin_dir / "codex", fake_codex)
        home = base / "home"
        codex_home = home / ".codex"
        if install_cache:
            installed = codex_home / "plugins/cache/cortex/cortex/6.6.0"
            installed.parent.mkdir(parents=True)
            shutil.copytree(plugin, installed)
        else:
            codex_home.mkdir(parents=True)
        config = codex_home / "config.toml"
        config.parent.mkdir(parents=True, exist_ok=True)
        config_lines = [
            'approval_policy = { granular = { mcp_elicitations = true } }\n',
            '\n',
            '[plugins."cortex@cortex"]\n',
            'enabled = true\n',
            '\n',
            '[plugins."cortex@cortex".mcp_servers.cortex]\n',
            'default_tools_approval_mode = "approve"\n',
            '\n',
        ]
        for name, digit in (
            ("pre_tool_use", "1"),
            ("post_tool_use", "2"),
            ("session_start", "3"),
            ("subagent_start", "4"),
            ("subagent_stop", "5"),
            ("stop", "6"),
        ):
            config_lines.extend(
                [
                    f'[hooks.state."cortex@cortex:hooks/hooks.json:{name}:0:0"]\n',
                    f'trusted_hash = "sha256:{digit * 64}"\n',
                    '\n',
                ]
            )
        config.write_text("".join(config_lines), encoding="utf-8")
        environment = os.environ.copy()
        environment.update(
            {
                "PATH": str(bin_dir),
                "HOME": str(home),
                "CODEX_HOME": str(codex_home),
                "CORTEX_PYTHON": sys.executable,
            }
        )
        return environment, codex_home

    def test_diagnoses_missing_codex_and_incompatible_python_without_writes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            bin_dir = base / "bin"
            bin_dir.mkdir()
            fake_python = bin_dir / "python3"
            self.write_executable(fake_python, "#!/bin/sh\nprintf 'tomllib is unavailable\\n'\nexit 1\n")
            home = base / "home"
            codex_home = home / ".codex"
            codex_home.mkdir(parents=True)
            plugin = base / "plugin"
            self.make_plugin(plugin)
            sentinel = codex_home / "sentinel"
            sentinel.write_text("preserve", encoding="utf-8")
            environment = os.environ.copy()
            environment.update({"PATH": str(bin_dir), "HOME": str(home), "CODEX_HOME": str(codex_home)})
            completed = self.run_preflight(environment, "--plugin-root", str(plugin))
            self.assertEqual(completed.returncode, 1)
            payload = json.loads(completed.stdout)
            self.assertFalse(payload["ok"])
            checks = {item["name"]: item for item in payload["checks"]}
            self.assertEqual(checks["codex_cli"]["status"], "FAIL")
            self.assertIn("not available on PATH", checks["codex_cli"]["detail"])
            self.assertEqual(checks["cortex_python"]["status"], "FAIL")
            self.assertIn("tomllib is unavailable", checks["cortex_python"]["detail"])
            self.assertEqual(payload["mcp"]["status"], "BLOCKED")
            self.assertEqual(
                payload["mcp"]["blocking_checks"],
                ["codex_cli", "cortex_python", "codex_home", "cortex_registration", "cortex_mcp_config", "cortex_hook_trust"],
            )
            self.assertIn("MCP registration and orchestration are blocked", payload["mcp"]["detail"])
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "preserve")

    def test_diagnoses_python_version_before_missing_tomllib(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            bin_dir = base / "bin"
            bin_dir.mkdir()
            old_python = bin_dir / "python3"
            self.write_executable(
                old_python,
                f"#!{sys.executable}\n"
                "import sys\n"
                "sys.version_info = (3, 10, 12)\n"
                "sys.version = '3.10.12 (fake)'\n"
                "exec(sys.argv[2], {'__name__': '__main__'})\n",
            )
            home = base / "home"
            codex_home = home / ".codex"
            codex_home.mkdir(parents=True)
            plugin = base / "plugin"
            self.make_plugin(plugin)
            environment = os.environ.copy()
            environment.update({"PATH": str(bin_dir), "HOME": str(home), "CODEX_HOME": str(codex_home)})

            completed = self.run_preflight(environment, "--plugin-root", str(plugin))

            self.assertEqual(completed.returncode, 1)
            payload = json.loads(completed.stdout)
            checks = {item["name"]: item for item in payload["checks"]}
            self.assertIn("Python 3.10.12 is too old", checks["cortex_python"]["detail"])
            self.assertIn("Python 3.11 or newer is required", checks["cortex_python"]["detail"])
            self.assertNotIn("tomllib is unavailable", checks["cortex_python"]["detail"])

    def test_passes_when_codex_python_plugin_and_cache_align(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            plugin = base / "plugin"
            self.make_plugin(plugin)
            environment, _ = self.make_aligned_host(base, plugin)
            completed = self.run_preflight(environment, "--plugin-root", str(plugin))
            self.assertEqual(completed.returncode, 0, completed.stderr)
            payload = json.loads(completed.stdout)
            self.assertTrue(payload["ok"])
            self.assertEqual({item["status"] for item in payload["checks"]}, {"PASS"})
            self.assertIn("(codex 1.2.3)", payload["checks"][0]["detail"])
            self.assertIn(
                f"{sys.executable} resolved to {sys.executable}",
                payload["checks"][1]["detail"],
            )
            self.assertEqual(payload["mcp"], {
                "blocking_checks": [],
                "detail": "Cortex MCP host prerequisites, same-user registration, approval configuration, and hook trust passed; start a new Codex thread after installation or update.",
                "status": "READY",
            })

    def test_chat_question_flow_does_not_require_mcp_elicitations(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            plugin = base / "plugin"
            self.make_plugin(plugin)
            environment, codex_home = self.make_aligned_host(base, plugin)
            config_path = codex_home / "config.toml"
            config_path.write_text(
                config_path.read_text(encoding="utf-8").replace(
                    "mcp_elicitations = true", "mcp_elicitations = false"
                ),
                encoding="utf-8",
            )

            completed = self.run_preflight(environment, "--plugin-root", str(plugin))

            self.assertEqual(completed.returncode, 0)
            payload = json.loads(completed.stdout)
            checks = {item["name"]: item for item in payload["checks"]}
            self.assertEqual(checks["cortex_mcp_config"]["status"], "PASS")
            self.assertNotIn("cortex_mcp_config", payload["mcp"]["blocking_checks"])

    def test_diagnoses_missing_codex_independently_when_other_checks_pass(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            plugin = base / "plugin"
            self.make_plugin(plugin)
            environment, _ = self.make_aligned_host(base, plugin, install_codex=False)

            completed = self.run_preflight(environment, "--plugin-root", str(plugin))

            self.assertEqual(completed.returncode, 1)
            payload = json.loads(completed.stdout)
            self.assertFalse(payload["ok"])
            checks = {item["name"]: item for item in payload["checks"]}
            self.assertEqual(checks["codex_cli"]["status"], "FAIL")
            self.assertEqual(checks["cortex_python"]["status"], "PASS")
            self.assertEqual(checks["plugin_root"]["status"], "PASS")
            self.assertEqual(checks["codex_home"]["status"], "PASS")

    def test_diagnoses_non_runnable_codex_independently_when_other_checks_pass(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            plugin = base / "plugin"
            self.make_plugin(plugin)
            environment, _ = self.make_aligned_host(base, plugin)
            codex = Path(environment["PATH"].split(os.pathsep)[0]) / "codex"
            self.write_executable(codex, "#!/bin/sh\nexit 17\n")

            completed = self.run_preflight(environment, "--plugin-root", str(plugin))

            self.assertEqual(completed.returncode, 1)
            payload = json.loads(completed.stdout)
            self.assertFalse(payload["ok"])
            checks = {item["name"]: item for item in payload["checks"]}
            self.assertEqual(checks["codex_cli"]["status"], "FAIL")
            self.assertIn("failed --version", checks["codex_cli"]["detail"])
            self.assertIn("exit code 17", checks["codex_cli"]["detail"])
            self.assertEqual(checks["cortex_python"]["status"], "PASS")
            self.assertEqual(checks["plugin_root"]["status"], "PASS")
            self.assertEqual(checks["codex_home"]["status"], "PASS")

    def test_diagnoses_missing_cache_independently_when_other_checks_pass(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            plugin = base / "plugin"
            self.make_plugin(plugin)
            environment, _ = self.make_aligned_host(base, plugin, install_cache=False)

            completed = self.run_preflight(environment, "--plugin-root", str(plugin))

            self.assertEqual(completed.returncode, 1)
            payload = json.loads(completed.stdout)
            self.assertFalse(payload["ok"])
            checks = {item["name"]: item for item in payload["checks"]}
            self.assertEqual(checks["codex_cli"]["status"], "PASS")
            self.assertEqual(checks["cortex_python"]["status"], "PASS")
            self.assertEqual(checks["plugin_root"]["status"], "PASS")
            self.assertEqual(checks["codex_home"]["status"], "FAIL")
            self.assertEqual(payload["mcp"]["status"], "BLOCKED")
            self.assertEqual(payload["mcp"]["blocking_checks"], ["codex_home", "cortex_hook_trust"])
            self.assertIn("failed host checks: codex_home", payload["mcp"]["detail"])

    def test_rejects_same_version_cache_content_drift(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            plugin = base / "plugin"
            self.make_plugin(plugin)
            environment, codex_home = self.make_aligned_host(base, plugin)
            cached_entrypoint = codex_home / "plugins/cache/cortex/cortex/6.6.0/scripts/cortex.py"
            cached_entrypoint.write_text(cached_entrypoint.read_text(encoding="utf-8") + "\n# drift\n", encoding="utf-8")

            completed = self.run_preflight(environment, "--plugin-root", str(plugin))

            self.assertEqual(completed.returncode, 1)
            payload = json.loads(completed.stdout)
            checks = {item["name"]: item for item in payload["checks"]}
            self.assertEqual(checks["plugin_root"]["status"], "PASS")
            self.assertEqual(checks["codex_home"]["status"], "FAIL")
            self.assertIn("content differs from the checked plugin root", checks["codex_home"]["detail"])
            self.assertEqual(payload["mcp"]["status"], "BLOCKED")

    def test_rejects_symlinked_cache_payload_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            plugin = base / "plugin"
            self.make_plugin(plugin)
            payload = plugin / "assets/payload.txt"
            payload.parent.mkdir()
            payload.write_text("payload", encoding="utf-8")
            environment, codex_home = self.make_aligned_host(base, plugin)
            installed_payload = codex_home / "plugins/cache/cortex/cortex/6.6.0/assets/payload.txt"
            outside = base / "outside-payload.txt"
            outside.write_text("payload", encoding="utf-8")
            installed_payload.unlink()
            installed_payload.symlink_to(outside)

            completed = self.run_preflight(environment, "--plugin-root", str(plugin))

            self.assertEqual(completed.returncode, 1)
            result = json.loads(completed.stdout)
            checks = {item["name"]: item for item in result["checks"]}
            self.assertEqual(checks["plugin_root"]["status"], "PASS")
            self.assertEqual(checks["codex_home"]["status"], "FAIL")
            self.assertIn("contains symlinked files", checks["codex_home"]["detail"])
            self.assertEqual(result["mcp"]["status"], "BLOCKED")

    def test_identifies_stale_cached_version_when_expected_version_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            plugin = base / "plugin"
            self.make_plugin(plugin)
            (plugin / ".codex-plugin/plugin.json").write_text(json.dumps({"version": "6.6.1"}), encoding="utf-8")
            environment, codex_home = self.make_aligned_host(base, plugin, install_cache=False)
            stale = codex_home / "plugins/cache/cortex/cortex/6.6.0"
            stale.mkdir(parents=True)

            completed = self.run_preflight(environment, "--plugin-root", str(plugin))

            self.assertEqual(completed.returncode, 1)
            payload = json.loads(completed.stdout)
            checks = {item["name"]: item for item in payload["checks"]}
            self.assertEqual(checks["codex_home"]["status"], "FAIL")
            self.assertIn("Cortex 6.6.1 is not installed", checks["codex_home"]["detail"])
            self.assertIn("cached version(s) found: 6.6.0", checks["codex_home"]["detail"])

    def test_blocks_mcp_when_a_valid_cached_plugin_is_one_version_behind_source(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            source_plugin = base / "source-plugin"
            cached_plugin = base / "cached-plugin"
            self.make_plugin(source_plugin)
            self.make_plugin(cached_plugin)
            (source_plugin / ".codex-plugin/plugin.json").write_text(
                json.dumps({"version": "6.6.1"}), encoding="utf-8"
            )
            environment, codex_home = self.make_aligned_host(base, cached_plugin)

            completed = self.run_preflight(environment, "--plugin-root", str(source_plugin))

            self.assertEqual(completed.returncode, 1)
            payload = json.loads(completed.stdout)
            checks = {item["name"]: item for item in payload["checks"]}
            self.assertEqual(checks["codex_cli"]["status"], "PASS")
            self.assertEqual(checks["cortex_python"]["status"], "PASS")
            self.assertEqual(checks["plugin_root"]["status"], "PASS")
            self.assertEqual(checks["codex_home"]["status"], "FAIL")
            self.assertIn("Cortex 6.6.1 is not installed", checks["codex_home"]["detail"])
            self.assertIn("cached version(s) found: 6.6.0", checks["codex_home"]["detail"])
            self.assertEqual(payload["mcp"]["status"], "BLOCKED")
            self.assertEqual(payload["mcp"]["blocking_checks"], ["codex_home", "cortex_registration", "cortex_hook_trust"])
            self.assertTrue((codex_home / "plugins/cache/cortex/cortex/6.6.0/.mcp.json").is_file())

    def test_rejects_path_traversal_in_plugin_version_before_cache_inspection(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            plugin = base / "plugin"
            self.make_plugin(plugin)
            (plugin / ".codex-plugin/plugin.json").write_text(
                json.dumps({"version": "../../../../outside-cache"}),
                encoding="utf-8",
            )
            environment, codex_home = self.make_aligned_host(base, plugin, install_cache=False)
            outside = base / "outside-cache"
            outside.mkdir()
            (outside / "sentinel").write_text("preserve", encoding="utf-8")

            completed = self.run_preflight(environment, "--plugin-root", str(plugin))

            self.assertEqual(completed.returncode, 1)
            payload = json.loads(completed.stdout)
            checks = {item["name"]: item for item in payload["checks"]}
            self.assertEqual(checks["plugin_root"]["status"], "FAIL")
            self.assertIn("not a safe cache directory name", checks["plugin_root"]["detail"])
            self.assertEqual(checks["codex_home"]["status"], "FAIL")
            self.assertEqual((outside / "sentinel").read_text(encoding="utf-8"), "preserve")
            self.assertFalse((codex_home / "plugins/cache/cortex/outside-cache").exists())

    def test_rejects_cache_directory_without_plugin_contract(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            plugin = base / "plugin"
            self.make_plugin(plugin)
            environment, codex_home = self.make_aligned_host(base, plugin, install_cache=False)
            (codex_home / "plugins/cache/cortex/cortex/6.6.0").mkdir(parents=True)

            completed = self.run_preflight(environment, "--plugin-root", str(plugin))

            self.assertEqual(completed.returncode, 1)
            payload = json.loads(completed.stdout)
            self.assertFalse(payload["ok"])
            checks = {item["name"]: item for item in payload["checks"]}
            self.assertEqual(checks["plugin_root"]["status"], "PASS")
            self.assertEqual(checks["codex_home"]["status"], "FAIL")
            self.assertIn("metadata is missing or not a regular non-symlink file", checks["codex_home"]["detail"])

    def test_rejects_cache_without_mcp_entrypoint(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            plugin = base / "plugin"
            self.make_plugin(plugin)
            environment, codex_home = self.make_aligned_host(base, plugin)
            (codex_home / "plugins/cache/cortex/cortex/6.6.0/scripts/cortex.py").unlink()

            completed = self.run_preflight(environment, "--plugin-root", str(plugin))

            self.assertEqual(completed.returncode, 1)
            payload = json.loads(completed.stdout)
            checks = {item["name"]: item for item in payload["checks"]}
            self.assertEqual(checks["plugin_root"]["status"], "PASS")
            self.assertEqual(checks["codex_home"]["status"], "FAIL")
            self.assertIn("MCP entrypoint", checks["codex_home"]["detail"])

    def test_rejects_symlinked_cache_metadata_without_following_it(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            plugin = base / "plugin"
            self.make_plugin(plugin)
            environment, codex_home = self.make_aligned_host(base, plugin)
            installed = codex_home / "plugins/cache/cortex/cortex/6.6.0"
            outside = base / "outside-mcp.json"
            outside.write_text((installed / ".mcp.json").read_text(encoding="utf-8"), encoding="utf-8")
            (installed / ".mcp.json").unlink()
            (installed / ".mcp.json").symlink_to(outside)

            completed = self.run_preflight(environment, "--plugin-root", str(plugin))

            self.assertEqual(completed.returncode, 1)
            payload = json.loads(completed.stdout)
            checks = {item["name"]: item for item in payload["checks"]}
            self.assertEqual(checks["plugin_root"]["status"], "PASS")
            self.assertEqual(checks["codex_home"]["status"], "FAIL")
            self.assertIn("contract traverses a symlinked path component", checks["codex_home"]["detail"])

    def test_rejects_symlinked_cache_contract_directory_without_following_it(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            plugin = base / "plugin"
            self.make_plugin(plugin)
            environment, codex_home = self.make_aligned_host(base, plugin)
            installed = codex_home / "plugins/cache/cortex/cortex/6.6.0"
            metadata = installed / ".codex-plugin"
            outside = base / "outside-metadata"
            metadata.rename(outside)
            metadata.symlink_to(outside, target_is_directory=True)

            completed = self.run_preflight(environment, "--plugin-root", str(plugin))

            self.assertEqual(completed.returncode, 1)
            payload = json.loads(completed.stdout)
            checks = {item["name"]: item for item in payload["checks"]}
            self.assertEqual(checks["plugin_root"]["status"], "PASS")
            self.assertEqual(checks["codex_home"]["status"], "FAIL")
            self.assertIn("contract traverses a symlinked path component", checks["codex_home"]["detail"])

    def test_rejects_symlinked_codex_home_ancestry_without_following_it(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            plugin = base / "plugin"
            self.make_plugin(plugin)
            real_home = base / "real-home"
            real_codex_home = real_home / ".codex"
            environment, _ = self.make_aligned_host(base, plugin)
            link_parent = base / "linked-parent"
            link_parent.symlink_to(real_home, target_is_directory=True)
            environment["CODEX_HOME"] = str(link_parent / ".codex")
            sentinel = real_codex_home / "sentinel"
            real_codex_home.mkdir(parents=True)
            sentinel.write_text("preserve", encoding="utf-8")

            completed = self.run_preflight(environment, "--plugin-root", str(plugin))

            self.assertEqual(completed.returncode, 1)
            payload = json.loads(completed.stdout)
            checks = {item["name"]: item for item in payload["checks"]}
            self.assertEqual(checks["plugin_root"]["status"], "PASS")
            self.assertEqual(checks["codex_home"]["status"], "FAIL")
            self.assertIn("symlinked path component", checks["codex_home"]["detail"])
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "preserve")

    def test_rejects_symlinked_cache_ancestry_without_following_it(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            plugin = base / "plugin"
            self.make_plugin(plugin)
            environment, codex_home = self.make_aligned_host(base, plugin, install_cache=False)
            real_cache = base / "real-cache"
            real_cache.mkdir()
            (codex_home / "plugins").mkdir()
            (codex_home / "plugins/cache").symlink_to(real_cache, target_is_directory=True)

            completed = self.run_preflight(environment, "--plugin-root", str(plugin))

            self.assertEqual(completed.returncode, 1)
            payload = json.loads(completed.stdout)
            checks = {item["name"]: item for item in payload["checks"]}
            self.assertEqual(checks["plugin_root"]["status"], "PASS")
            self.assertEqual(checks["codex_home"]["status"], "FAIL")
            self.assertIn("cached Cortex path traverses a symlinked path component", checks["codex_home"]["detail"])

    def test_rejects_relative_explicit_python_without_path_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            plugin = base / "plugin"
            self.make_plugin(plugin)
            environment, _ = self.make_aligned_host(base, plugin)
            environment["CORTEX_PYTHON"] = "python3"

            completed = self.run_preflight(environment, "--plugin-root", str(plugin))

            self.assertEqual(completed.returncode, 1)
            payload = json.loads(completed.stdout)
            self.assertFalse(payload["ok"])
            checks = {item["name"]: item for item in payload["checks"]}
            self.assertEqual(checks["cortex_python"]["status"], "FAIL")
            self.assertIn("absolute executable path", checks["cortex_python"]["detail"])
            self.assertEqual(checks["codex_cli"]["status"], "PASS")
            self.assertEqual(checks["plugin_root"]["status"], "PASS")
            self.assertEqual(checks["codex_home"]["status"], "PASS")


if __name__ == "__main__":
    unittest.main()
