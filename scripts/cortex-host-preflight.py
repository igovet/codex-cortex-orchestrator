#!/usr/bin/env python3
"""Report the host prerequisites needed to expose the Cortex MCP server."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import selectors
import shutil
import stat
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


sys.dont_write_bytecode = True
os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PLUGIN_ROOT = ROOT / "plugins/cortex"
MAX_CACHE_VERSION_HINTS = 8
MAX_COMMAND_OUTPUT_BYTES = 128 * 1024
COMMAND_TIMEOUT_SECONDS = 15
CORTEX_PLUGIN_ID = "cortex@cortex"
EXPECTED_BASE_VERSION = "1.13.2"
EXPECTED_MCP = {"mcpServers": {"cortex": {"command": "python3", "args": ["./scripts/cortex.py"], "cwd": "."}}}
RETIRED_PLUGIN_PATHS = {
    Path("hooks"),
    Path("scripts/cortex_hook.py"),
    Path("scripts/cortex-launcher"),
    Path("scripts/cortex_runtime/core"),
    Path("scripts/cortex_runtime/record_report"),
}
PYTHON_PROBE = """
import json
import sys

if sys.version_info < (3, 11):
    print(f"Python {sys.version.split()[0]} is too old; Python 3.11 or newer is required")
    raise SystemExit(1)
try:
    import tomllib
except ImportError:
    print("tomllib is unavailable")
    raise SystemExit(1)
print(json.dumps({"executable": sys.executable, "version": ".".join(map(str, sys.version_info[:3]))}))
"""
TOML_PROBE = """
import json
import sys
import tomllib
from pathlib import Path

path = Path(sys.argv[1])
payload = tomllib.loads(path.read_text(encoding="utf-8"))
print(json.dumps(payload, separators=(",", ":")))
"""


def safe_cache_version(version: str) -> bool:
    """Return whether *version* can be used as one cache directory name."""
    if not version or version in {".", ".."} or "\x00" in version:
        return False
    separators = {os.sep}
    if os.altsep:
        separators.add(os.altsep)
    return not any(separator in version for separator in separators)


def check(name: str, ok: bool, detail: str, remediation: str | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {"name": name, "status": "PASS" if ok else "FAIL", "detail": detail}
    if remediation and not ok:
        result["remediation"] = remediation
    return result


def bounded_command(
    argv: list[str],
    *,
    input_text: str | None = None,
    timeout: float = COMMAND_TIMEOUT_SECONDS,
    max_bytes: int = MAX_COMMAND_OUTPUT_BYTES,
) -> tuple[int | None, str, str]:
    """Run one host command with bounded stdout and a hard timeout.

    Stderr is intentionally discarded: host tools can print arbitrary paths or
    credentials there, while the preflight needs only a bounded pass/fail
    diagnostic.
    """
    try:
        process = subprocess.Popen(
            argv,
            stdin=subprocess.PIPE if input_text is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
    except OSError as exc:
        return None, "", f"could not start command: {exc}"

    if process.stdin is not None and input_text is not None:
        try:
            process.stdin.write(input_text.encode("utf-8"))
            process.stdin.close()
        except OSError:
            try:
                process.kill()
            except OSError:
                pass
            process.wait()
            return None, "", "command input failed"

    output = bytearray()
    selector = selectors.DefaultSelector()
    assert process.stdout is not None
    selector.register(process.stdout, selectors.EVENT_READ)
    deadline = time.monotonic() + timeout
    exceeded = False
    timed_out = False
    try:
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                timed_out = True
                break
            events = selector.select(min(0.25, remaining))
            if not events:
                continue
            for key, _ in events:
                try:
                    chunk = os.read(key.fd, min(8192, max_bytes + 1 - len(output)))
                except OSError:
                    chunk = b""
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                output.extend(chunk)
                if len(output) > max_bytes:
                    exceeded = True
                    break
            if exceeded:
                break
    finally:
        selector.close()

    if exceeded or timed_out:
        try:
            process.kill()
        except OSError:
            pass
        process.wait()
        if exceeded:
            return None, "", f"command output exceeded the {max_bytes}-byte limit"
        return None, "", f"command timed out after {int(timeout)} seconds"
    returncode = process.wait()
    return returncode, output.decode("utf-8", errors="replace"), ""


def load_toml(path: Path, interpreter: Path | None) -> tuple[dict[str, Any] | None, str | None]:
    """Parse one regular config file through the validated host interpreter."""
    if interpreter is None:
        return None, "the selected Cortex Python runtime is unavailable"
    returncode, stdout, failure = bounded_command([str(interpreter), "-B", "-c", TOML_PROBE, str(path)])
    if failure:
        return None, failure
    if returncode != 0:
        return None, "configuration is invalid TOML or unreadable"
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError:
        return None, "configuration parser returned invalid JSON"
    if not isinstance(payload, dict):
        return None, "configuration root is not a TOML table"
    return payload, None


def regular_executable(path: Path) -> bool:
    return path.is_file() and os.access(path, os.X_OK)


def regular_non_symlink_file(path: Path) -> bool:
    """Return whether *path* is a regular file without following symlinks."""
    try:
        return stat.S_ISREG(path.lstat().st_mode)
    except OSError:
        return False


def package_residue(root: Path) -> str | None:
    """Return a non-sensitive reason when a plugin tree retains retired payload."""
    try:
        for path in root.rglob("*"):
            relative = path.relative_to(root)
            if path.is_symlink():
                return "contains a symlinked payload"
            if any(part == "__pycache__" for part in relative.parts) or path.suffix in {".pyc", ".pyo"}:
                return "contains Python bytecode residue"
            if relative in RETIRED_PLUGIN_PATHS or any(retired in relative.parents for retired in RETIRED_PLUGIN_PATHS):
                return "contains retired V11 hook/control-plane residue"
    except OSError:
        return "contains an unreadable payload"
    return None


def package_digest(root: Path) -> str | None:
    """Return a stable digest for comparable plugin files, or ``None`` on read failure."""
    try:
        if package_residue(root) is not None:
            return None
        files = []
        for path in sorted(root.rglob("*")):
            # A symlinked payload can point outside the cache while its target
            # content is omitted from the digest. Treat that as unverifiable
            # instead of allowing a false same-version match.
            if path.is_symlink():
                return None
            if path.is_file():
                files.append(path)
        digest = hashlib.sha256()
        for path in files:
            digest.update(path.relative_to(root).as_posix().encode("utf-8"))
            digest.update(b"\0")
            digest.update(path.read_bytes())
        return digest.hexdigest()
    except OSError:
        return None


def symlink_ancestor(path: Path) -> Path | None:
    """Return the first existing symlink component in *path*, if any."""
    absolute = path.absolute()
    current = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        current /= part
        try:
            mode = current.lstat().st_mode
        except FileNotFoundError:
            continue
        except OSError:
            return current
        if stat.S_ISLNK(mode):
            return current
    return None


def first_symlinked_path(paths: list[Path]) -> Path | None:
    """Return the first symlinked component in any checked contract path."""
    for path in paths:
        symlink = symlink_ancestor(path)
        if symlink:
            return symlink
    return None


def resolve_python() -> tuple[dict[str, Any], Path | None]:
    requested = "python3"
    resolved = Path(shutil.which(requested) or "")
    if not resolved or not regular_executable(resolved):
        return (
            check(
                "cortex_python",
                False,
                "python3 is not an executable file on PATH",
                "Install Python 3.11+ and make python3 available to the Codex host process.",
            ),
            None,
        )
    try:
        probe = subprocess.run(
            [str(resolved), "-B", "-c", PYTHON_PROBE],
            capture_output=True,
            check=False,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return (
            check(
                "cortex_python",
                False,
                f"could not execute {requested}: {exc}",
                "Verify that the selected Python executable is runnable by the Codex host user.",
            ),
            None,
        )
    if probe.returncode != 0:
        detail = (probe.stdout or probe.stderr).strip().splitlines()
        message = detail[-1] if detail else "runtime validation failed"
        return (
            check(
                "cortex_python",
                False,
                f"{requested} is incompatible: {message}",
                "Use Python 3.11+ with tomllib as the python3 command available to Codex.",
            ),
            None,
        )
    try:
        observed = json.loads(probe.stdout)
    except json.JSONDecodeError:
        observed = {}
    version = observed.get("version", "unknown")
    return (
        check(
            "cortex_python",
            True,
            f"{requested} resolved to {resolved} (Python {version} with tomllib)",
        ),
        resolved,
    )


def inspect_codex() -> dict[str, Any]:
    path = shutil.which("codex")
    if not path or not regular_executable(Path(path)):
        return check(
            "codex_cli",
            False,
            "codex executable is not available on PATH",
            "Install the Codex CLI for this SSH user and include it in PATH.",
        )
    try:
        probe = subprocess.run(
            [path, "--version"],
            capture_output=True,
            check=False,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return check(
            "codex_cli",
            False,
            f"codex executable at {path} could not run --version: {exc}",
            "Install or repair the Codex CLI for this SSH user, then rerun the preflight.",
        )
    if probe.returncode != 0:
        return check(
            "codex_cli",
            False,
            f"codex executable at {path} failed --version (exit code {probe.returncode})",
            "Install or repair the Codex CLI for this SSH user, then rerun the preflight.",
        )
    version_lines = [
        line.strip()
        for stream in (probe.stdout, probe.stderr)
        for line in stream.splitlines()
        if line.strip()
    ]
    version = version_lines[0] if version_lines else "version unavailable"
    # Keep a hostile or unexpectedly verbose executable from flooding the
    # machine-readable diagnostic while retaining the useful version proof.
    if len(version) > 200:
        version = version[:197] + "..."
    return check("codex_cli", True, f"codex executable resolved at {path} ({version})")


def inspect_plugin(plugin_root: Path) -> tuple[dict[str, Any], str | None]:
    symlink = symlink_ancestor(plugin_root)
    if symlink:
        return (
            check(
                "plugin_root",
                False,
                f"plugin root traverses a symlinked path component: {symlink}",
                "Use a regular plugin checkout or cache path without symlinked ancestors.",
            ),
            None,
        )
    if not plugin_root.is_dir():
        return (
            check(
                "plugin_root",
                False,
                f"plugin root is missing or not a regular directory: {plugin_root}",
                "Install the Cortex plugin for the same Codex user or pass --plugin-root to its cache directory.",
            ),
            None,
        )
    residue = package_residue(plugin_root)
    if residue is not None:
        return (
            check(
                "plugin_root",
                False,
                f"plugin source {residue}",
                "Remove generated bytecode and retired V11 payload before packaging Cortex.",
            ),
            None,
        )
    manifest_path = plugin_root / ".codex-plugin/plugin.json"
    mcp_path = plugin_root / ".mcp.json"
    entrypoint = plugin_root / "scripts/cortex.py"
    symlink = first_symlinked_path([manifest_path, mcp_path, entrypoint])
    if symlink:
        return (
            check(
                "plugin_root",
                False,
                f"plugin contract traverses a symlinked path component: {symlink}",
                "Use a regular plugin checkout or cache path without symlinked contract directories.",
            ),
            None,
        )
    if not regular_non_symlink_file(manifest_path) or not regular_non_symlink_file(mcp_path):
        return (
            check(
                "plugin_root",
                False,
                "plugin metadata is missing or not a regular non-symlink file",
                "Reinstall Cortex from a trusted checkout.",
            ),
            None,
        )
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        mcp = json.loads(mcp_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return check("plugin_root", False, f"plugin metadata is unreadable: {exc}", "Reinstall Cortex from a trusted checkout."), None
    version = manifest.get("version")
    if not isinstance(version, str) or not version:
        return check("plugin_root", False, "plugin manifest has no version", "Reinstall Cortex from a trusted checkout."), None
    if not safe_cache_version(version) or version.split("+", 1)[0] != EXPECTED_BASE_VERSION:
        return (
            check(
                "plugin_root",
                False,
                "plugin manifest version is not a safe cache directory name",
                "Reinstall Cortex from a trusted checkout.",
            ),
            None,
        )
    if mcp != EXPECTED_MCP:
        return check("plugin_root", False, "MCP manifest does not route directly to the V12 Python entrypoint", "Reinstall the same-version Cortex plugin."), version
    if not regular_non_symlink_file(entrypoint):
        return check("plugin_root", False, "bundled Cortex MCP entrypoint is missing or not a regular file", "Restore the installed Cortex plugin contents."), version
    return check("plugin_root", True, f"Cortex {version} has a valid direct Python MCP entrypoint"), version


def inspect_cache(codex_home: Path, version: str | None, source_digest: str | None) -> dict[str, Any]:
    symlink = symlink_ancestor(codex_home)
    if symlink:
        return check(
            "codex_home",
            False,
            f"CODEX_HOME traverses a symlinked path component: {symlink}",
            "Use a regular CODEX_HOME path without symlinked ancestors for this SSH user.",
        )
    if not codex_home.is_dir():
        return check(
            "codex_home",
            False,
            f"CODEX_HOME is missing or not a regular directory: {codex_home}",
            "Use the same writable CODEX_HOME for the SSH user's Codex installation.",
        )
    if not version:
        return check("codex_home", False, "cannot inspect the plugin cache without a valid plugin version", "Fix the plugin_root check first.")
    installed = codex_home / "plugins/cache/cortex/cortex" / version
    symlink = symlink_ancestor(installed)
    if symlink:
        return check(
            "codex_home",
            False,
            f"cached Cortex path traverses a symlinked path component: {symlink}",
            "Use a regular CODEX_HOME cache path without symlinked ancestors for this SSH user.",
        )
    if not installed.is_dir():
        cache_root = codex_home / "plugins/cache/cortex/cortex"
        try:
            cached_versions = sorted(
                child.name
                for child in cache_root.iterdir()
                if not child.is_symlink() and child.is_dir()
            )
            if len(cached_versions) > MAX_CACHE_VERSION_HINTS:
                cached_versions = cached_versions[:MAX_CACHE_VERSION_HINTS] + ["..."]
        except OSError:
            cached_versions = []
        detail = f"Cortex {version} is not installed in {codex_home}"
        if cached_versions:
            detail += f"; cached version(s) found: {', '.join(cached_versions)}"
        return check(
            "codex_home",
            False,
            detail,
            "Install or update cortex@cortex for this same Codex user, then start a new thread.",
        )
    residue = package_residue(installed)
    if residue is not None:
        return check(
            "codex_home",
            False,
            f"cached Cortex {version} {residue}",
            "Update Cortex through ./scripts/sync-cortex.sh to replace the cache with the clean staged plugin tree.",
        )
    manifest_path = installed / ".codex-plugin/plugin.json"
    mcp_path = installed / ".mcp.json"
    entrypoint = installed / "scripts/cortex.py"
    symlink = first_symlinked_path([manifest_path, mcp_path, entrypoint])
    if symlink:
        return check(
            "codex_home",
            False,
            f"cached Cortex contract traverses a symlinked path component: {symlink}",
            "Use a regular CODEX_HOME cache path without symlinked contract directories.",
        )
    if not regular_non_symlink_file(manifest_path) or not regular_non_symlink_file(mcp_path):
        return check(
            "codex_home",
            False,
            f"cached Cortex {version} metadata is missing or not a regular non-symlink file",
            "Reinstall or update cortex@cortex for this same Codex user, then start a new thread.",
        )
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        mcp = json.loads(mcp_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return check(
            "codex_home",
            False,
            f"cached Cortex {version} metadata is unreadable: {exc}",
            "Reinstall or update cortex@cortex for this same Codex user, then start a new thread.",
        )
    if manifest.get("version") != version:
        return check(
            "codex_home",
            False,
            f"cached Cortex manifest version does not match the selected {version} plugin",
            "Reinstall or update cortex@cortex for this same Codex user, then start a new thread.",
        )
    if mcp != EXPECTED_MCP or not regular_non_symlink_file(entrypoint):
        return check(
            "codex_home",
            False,
            f"cached Cortex {version} has an invalid direct MCP manifest or entrypoint",
            "Reinstall or update cortex@cortex for this same Codex user, then start a new thread.",
        )
    if source_digest is not None:
        cached_digest = package_digest(installed)
        if cached_digest is None:
            return check(
                "codex_home",
                False,
                f"cached Cortex {version} content is unreadable or contains symlinked files; cannot verify it against the checked plugin root",
                "Reinstall or update cortex@cortex for this same Codex user, then start a new thread.",
            )
        if cached_digest != source_digest:
            return check(
                "codex_home",
                False,
                f"cached Cortex {version} content differs from the checked plugin root",
                "Reinstall or update cortex@cortex for this same Codex user, then start a new thread.",
            )
    return check("codex_home", True, f"Cortex {version} is present and valid in the Codex plugin cache")


def inspect_registration(codex_path: Path | None, version: str | None) -> dict[str, Any]:
    """Require one enabled same-user cortex@cortex registration at *version*."""
    if not version:
        return check(
            "cortex_registration",
            False,
            "cannot inspect Cortex registration without a valid plugin version",
            "Fix the plugin_root check before validating same-user registration.",
        )
    if codex_path is None or not regular_executable(codex_path):
        return check(
            "cortex_registration",
            False,
            "codex plugin registration cannot be queried because the Codex CLI is unavailable",
            "Install the Codex CLI for this same user, then rerun the preflight.",
        )
    returncode, stdout, failure = bounded_command([str(codex_path), "plugin", "list", "--json"])
    if failure:
        return check("cortex_registration", False, f"Codex plugin list is unavailable: {failure}", "Rerun the preflight as the user who starts Codex.")
    if returncode != 0:
        return check("cortex_registration", False, "Codex plugin list failed for this user", "Repair the Codex CLI or plugin registration, then rerun the preflight.")
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError:
        return check("cortex_registration", False, "Codex plugin list returned malformed JSON", "Repair the Codex CLI registration state, then rerun the preflight.")
    rows = payload.get("installed", payload) if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        return check("cortex_registration", False, "Codex plugin list has no installed-plugin table", "Repair the Codex CLI registration state, then rerun the preflight.")
    cortex_rows = [row for row in rows if isinstance(row, dict) and row.get("pluginId") == CORTEX_PLUGIN_ID]
    if not cortex_rows:
        return check("cortex_registration", False, "same-user cortex@cortex registration is missing", "Install or update cortex@cortex for this same Codex user, then rerun the preflight.")
    if len(cortex_rows) != 1:
        return check("cortex_registration", False, "same-user cortex@cortex registration is duplicated", "Remove duplicate Cortex registrations and keep one enabled matching version.")
    row = cortex_rows[0]
    if row.get("installed") is not True:
        return check("cortex_registration", False, "same-user cortex@cortex registration is not installed", "Install cortex@cortex for this same Codex user, then rerun the preflight.")
    if row.get("enabled") is not True:
        return check("cortex_registration", False, "same-user cortex@cortex registration is disabled", "Enable cortex@cortex for this same Codex user, then rerun the preflight.")
    if row.get("version") != version:
        return check("cortex_registration", False, f"same-user cortex@cortex version does not match the checked {version} plugin", "Update cortex@cortex for this same Codex user, then rerun the preflight.")
    if row.get("name") != "cortex":
        return check("cortex_registration", False, "same-user Cortex registration has an unexpected plugin name", "Repair the Cortex plugin registration, then rerun the preflight.")
    return check("cortex_registration", True, f"same-user cortex@cortex registration is enabled at version {version}")


def inspect_codebase_memory_config(payload: dict[str, Any]) -> dict[str, Any]:
    """Require a usable, enabled, non-secret stdio Codebase Memory server.

    This deliberately validates only safe availability metadata.  It never
    reads or reports environment values, headers, URLs, or other credentials.
    """
    servers = payload.get("mcp_servers")
    server = servers.get("codebase_memory") if isinstance(servers, dict) else None
    if not isinstance(server, dict):
        return check(
            "codebase_memory_mcp",
            False,
            "top-level mcp_servers.codebase_memory is missing",
            "Configure an enabled local Codebase Memory MCP server for this Codex user, then rerun the preflight.",
        )
    if server.get("enabled", True) is not True:
        return check(
            "codebase_memory_mcp",
            False,
            "top-level mcp_servers.codebase_memory is disabled",
            "Enable mcp_servers.codebase_memory for this Codex user, then rerun the preflight.",
        )
    command = server.get("command")
    if not isinstance(command, str) or not command.strip() or "\n" in command or "\r" in command:
        return check(
            "codebase_memory_mcp",
            False,
            "mcp_servers.codebase_memory has no usable local command",
            "Configure a non-empty single-line command for the Codebase Memory MCP server, then rerun the preflight.",
        )
    args = server.get("args", [])
    if not isinstance(args, list) or any(not isinstance(item, str) or "\n" in item or "\r" in item for item in args):
        return check(
            "codebase_memory_mcp",
            False,
            "mcp_servers.codebase_memory has malformed command arguments",
            "Repair the Codebase Memory MCP command arguments, then rerun the preflight.",
        )
    if any(key in server for key in ("url", "http_url", "headers", "authorization", "env", "environment")):
        return check(
            "codebase_memory_mcp",
            False,
            "mcp_servers.codebase_memory uses an unsupported credential-bearing or remote form",
            "Configure Codebase Memory as a local stdio MCP server without inline credentials, then rerun the preflight.",
        )
    return check("codebase_memory_mcp", True, "enabled local Codebase Memory MCP command is configured")


def inspect_mcp_config(codex_home: Path, interpreter: Path | None) -> dict[str, Any]:
    """Require the same user's Codex config to permit Cortex and native V2."""
    symlink = symlink_ancestor(codex_home)
    if symlink:
        return check(
            "cortex_mcp_config",
            False,
            f"Cortex MCP configuration traverses a symlinked path component: {symlink}",
            "Use a regular CODEX_HOME path without symlinked ancestors for this SSH user.",
        )
    config_path = codex_home / "config.toml"
    if not regular_non_symlink_file(config_path):
        return check(
            "cortex_mcp_config",
            False,
            "same-user Codex configuration is missing or not a regular non-symlink file",
            "Run the approved Cortex installer as this Codex user, then rerun the preflight.",
        )
    payload, failure = load_toml(config_path, interpreter)
    if failure or payload is None:
        return check(
            "cortex_mcp_config",
            False,
            f"same-user Codex configuration is unreadable: {failure or 'unknown parser failure'}",
            "Repair the same-user Codex configuration, then rerun the preflight.",
        )
    memory_result = inspect_codebase_memory_config(payload)
    if memory_result["status"] != "PASS":
        return memory_result
    plugins = payload.get("plugins")
    registration = plugins.get(CORTEX_PLUGIN_ID) if isinstance(plugins, dict) else None
    if not isinstance(registration, dict):
        return check(
            "cortex_mcp_config",
            False,
            "same-user Codex configuration has no cortex@cortex registration table",
            "Run the approved Cortex installer as this Codex user, then rerun the preflight.",
        )
    if registration.get("enabled") is not True:
        return check(
            "cortex_mcp_config",
            False,
            "same-user cortex@cortex configuration is disabled",
            "Enable cortex@cortex for this same Codex user, then rerun the preflight.",
        )
    servers = registration.get("mcp_servers")
    cortex_server = servers.get("cortex") if isinstance(servers, dict) else None
    if not isinstance(cortex_server, dict):
        return check(
            "cortex_mcp_config",
            False,
            "same-user Codex configuration has no Cortex MCP server table",
            "Run the approved Cortex installer as this Codex user, then rerun the preflight.",
        )
    approval = cortex_server.get("default_tools_approval_mode")
    if approval != "approve":
        observed = approval if isinstance(approval, str) and len(approval) <= 32 else "missing"
        return check(
            "cortex_mcp_config",
            False,
            f"Cortex MCP default_tools_approval_mode must be approve (found {observed})",
            "Run the approved Cortex installer to set Cortex MCP approval to approve, then rerun the preflight.",
        )
    features = payload.get("features")
    if not isinstance(features, dict) or features.get("multi_agent_v2") is not True:
        return check(
            "cortex_mcp_config",
            False,
            "features.multi_agent_v2 must be true for native Cortex dispatch",
            "Run the approved Cortex installer to enable multi_agent_v2, then start a new thread.",
        )
    agents = payload.get("agents")
    default_model = agents.get("default_subagent_model") if isinstance(agents, dict) else None
    if default_model != "gpt-5.6-luna":
        observed = default_model if isinstance(default_model, str) and len(default_model) <= 64 else "missing"
        return check(
            "cortex_mcp_config",
            False,
            f"agents.default_subagent_model must be gpt-5.6-luna (found {observed})",
            "Run the approved Cortex installer to back up and replace the default, then start a new thread.",
        )
    return check(
        "cortex_mcp_config",
        True,
        "same-user Cortex MCP approval, multi_agent_v2, and Luna default configuration are valid",
    )


def summarize_mcp(checks: list[dict[str, Any]]) -> dict[str, Any]:
    """Explain whether the checked host can expose Cortex MCP/orchestration."""
    blocking_checks = [item["name"] for item in checks if item["status"] != "PASS"]
    if blocking_checks:
        names = ", ".join(blocking_checks)
        return {
            "status": "BLOCKED",
            "blocking_checks": blocking_checks,
            "detail": f"Cortex MCP registration and orchestration are blocked by failed host checks: {names}",
        }
    return {
        "status": "READY",
        "blocking_checks": [],
        "detail": "Cortex MCP host prerequisites, same-user registration, approval configuration, and Luna default configuration passed; start a new Codex thread after installation or update.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plugin-root", type=Path, default=DEFAULT_PLUGIN_ROOT, help="source or installed Cortex plugin directory")
    parser.add_argument("--json", action="store_true", dest="as_json", help="emit machine-readable JSON")
    args = parser.parse_args()

    python_result, cortex_python = resolve_python()
    codex_result = inspect_codex()
    codex_path_value = shutil.which("codex")
    codex_path = Path(codex_path_value) if codex_path_value and regular_executable(Path(codex_path_value)) else None
    plugin_result, version = inspect_plugin(args.plugin_root)
    source_digest = package_digest(args.plugin_root) if plugin_result["status"] == "PASS" else None
    if plugin_result["status"] == "PASS" and source_digest is None:
        plugin_result = check(
            "plugin_root",
            False,
            "plugin content is unreadable; cannot compare it with the same-version cache",
            "Use a readable Cortex checkout and rerun the preflight.",
        )
        version = None
    codex_home_value = os.environ.get("CODEX_HOME") or str(Path(os.environ.get("HOME", "~")) / ".codex")
    codex_home = Path(codex_home_value).expanduser()
    cache_result = inspect_cache(codex_home, version, source_digest)
    registration_result = inspect_registration(codex_path, version)
    config_result = inspect_mcp_config(codex_home, cortex_python)
    checks = [codex_result, python_result, plugin_result, cache_result, registration_result, config_result]
    payload = {
        "ok": all(item["status"] == "PASS" for item in checks),
        "checks": checks,
        "mcp": summarize_mcp(checks),
    }
    if args.as_json:
        print(json.dumps(payload, sort_keys=True))
    else:
        print("Cortex host preflight: " + ("PASS" if payload["ok"] else "FAIL"))
        for item in checks:
            print(f"{item['status']:<4} {item['name']}: {item['detail']}")
            if "remediation" in item:
                print(f"     remediation: {item['remediation']}")
        print(f"{payload['mcp']['status']:<7} mcp: {payload['mcp']['detail']}")
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
