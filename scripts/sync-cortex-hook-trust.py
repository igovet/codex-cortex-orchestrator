#!/usr/bin/env python3
"""Trust only the exact installed Cortex hook hashes during an explicit sync."""
from __future__ import annotations

import argparse
import json
import os
import re
import selectors
import stat
import subprocess
import tempfile
import time
import tomllib
from pathlib import Path


PLUGIN_ID = "cortex@cortex"
EXPECTED_KEYS = {
    f"{PLUGIN_ID}:hooks/hooks.json:{name}:0:0"
    for name in (
        "pre_tool_use",
        "post_tool_use",
        "session_start",
        "subagent_start",
        "subagent_stop",
        "stop",
    )
}
HASH_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


def request(process: subprocess.Popen[str], request_id: int, method: str, params: dict) -> dict:
    assert process.stdin is not None and process.stdout is not None
    process.stdin.write(json.dumps({"id": request_id, "method": method, "params": params}) + "\n")
    process.stdin.flush()
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)
    deadline = time.monotonic() + 15
    try:
        while time.monotonic() < deadline:
            remaining = max(0.01, deadline - time.monotonic())
            if not selector.select(remaining):
                continue
            line = process.stdout.readline()
            if not line:
                break
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if payload.get("id") != request_id:
                continue
            if "error" in payload:
                raise RuntimeError(f"Codex app-server {method} failed")
            result = payload.get("result")
            if not isinstance(result, dict):
                raise RuntimeError(f"Codex app-server {method} returned no result")
            return result
    finally:
        selector.close()
    raise RuntimeError(f"Codex app-server {method} timed out")


def installed_hook_hashes(codex: str, cwd: Path, installed_root: Path) -> dict[str, str]:
    process = subprocess.Popen(
        [codex, "app-server", "--stdio"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        bufsize=1,
    )
    try:
        request(process, 1, "initialize", {
            "clientInfo": {"name": "cortex-hook-sync", "version": "1"},
            "capabilities": {"experimentalApi": True},
        })
        result = request(process, 2, "hooks/list", {"cwds": [str(cwd)]})
    finally:
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=3)

    rows = result.get("data")
    if not isinstance(rows, list):
        raise RuntimeError("Codex hooks/list returned no workspace data")
    workspace = next((row for row in rows if isinstance(row, dict) and row.get("cwd") == str(cwd)), None)
    if workspace is None or workspace.get("errors"):
        raise RuntimeError("Codex hooks/list did not load the Cortex workspace hooks")
    hashes: dict[str, str] = {}
    expected_source = (installed_root / "hooks" / "hooks.json").absolute()
    expected_script = (installed_root / "scripts" / "cortex_hook.py").absolute()
    for hook in workspace.get("hooks", []):
        if not isinstance(hook, dict) or hook.get("pluginId") != PLUGIN_ID:
            continue
        key = str(hook.get("key") or "")
        digest = str(hook.get("currentHash") or "")
        source = Path(str(hook.get("sourcePath") or "")).absolute()
        command = str(hook.get("command") or "")
        if key not in EXPECTED_KEYS:
            raise RuntimeError(f"unexpected Cortex hook key: {key}")
        if source != expected_source or str(expected_script) not in command:
            raise RuntimeError(f"Cortex hook source does not match the installed cache: {key}")
        if hook.get("enabled") is not True or not HASH_RE.fullmatch(digest):
            raise RuntimeError(f"Cortex hook is disabled or has an invalid hash: {key}")
        hashes[key] = digest
    if set(hashes) != EXPECTED_KEYS:
        missing = sorted(EXPECTED_KEYS - set(hashes))
        raise RuntimeError("installed Cortex hook set is incomplete: " + ", ".join(missing))
    return hashes


def update_table(text: str, table: str, digest: str) -> str:
    lines = text.splitlines(keepends=True)
    headers = [index for index, line in enumerate(lines) if line.strip() == table]
    if len(headers) > 1:
        raise RuntimeError(f"duplicate Codex hook trust table: {table}")
    if not headers:
        if text and not text.endswith(("\n", "\r")):
            text += "\n"
        if text and not text.endswith("\n\n"):
            text += "\n"
        return text + f'{table}\ntrusted_hash = "{digest}"\n'
    start = headers[0] + 1
    end = start
    header_re = re.compile(r"^\s*\[(?!\[).+\]\s*(?:#.*)?$")
    while end < len(lines) and not header_re.match(lines[end]):
        end += 1
    keys = [index for index in range(start, end) if re.match(r"^\s*trusted_hash\s*=", lines[index])]
    if len(keys) > 1:
        raise RuntimeError(f"duplicate trusted_hash in {table}")
    if not keys:
        lines.insert(start, f'trusted_hash = "{digest}"\n')
    else:
        newline = "\r\n" if lines[keys[0]].endswith("\r\n") else "\n"
        lines[keys[0]] = f'trusted_hash = "{digest}"{newline}'
    return "".join(lines)


def synchronize(config: Path, hashes: dict[str, str], check: bool) -> None:
    if config.is_symlink() or not config.is_file():
        raise RuntimeError(f"Codex config must be a regular file: {config}")
    original = config.read_text(encoding="utf-8")
    try:
        parsed = tomllib.loads(original)
    except tomllib.TOMLDecodeError as exc:
        raise RuntimeError(f"Codex config is invalid: {exc}") from exc
    current = parsed.get("hooks", {}).get("state", {})
    stale = [key for key, digest in hashes.items() if (current.get(key) or {}).get("trusted_hash") != digest]
    if check:
        if stale:
            raise RuntimeError("Cortex lifecycle hooks are not trusted at their installed hashes: " + ", ".join(sorted(stale)))
        return
    text = original
    for key in sorted(hashes):
        text = update_table(text, f'[hooks.state.{json.dumps(key)}]', hashes[key])
    tomllib.loads(text)
    if text == original:
        return
    mode = stat.S_IMODE(config.stat().st_mode)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{config.name}.", dir=str(config.parent))
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, config)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--codex", required=True)
    parser.add_argument("--cwd", type=Path, required=True)
    parser.add_argument("--installed-root", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    hashes = installed_hook_hashes(args.codex, args.cwd.absolute(), args.installed_root.absolute())
    synchronize(args.config.absolute(), hashes, args.check)
    print(f"ok      Cortex lifecycle hook trust ({len(hashes)} content hashes)")


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        raise SystemExit(f"error: {exc}") from None
