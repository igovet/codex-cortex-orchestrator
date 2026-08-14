#!/usr/bin/env bash
# Install or verify the repo-local orchestration plugin without touching active state by default.
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
project_dir="$(cd -- "${script_dir}/.." && pwd)"
plugin_name="cortex"
marketplace_name="cortex"
plugin_source="${project_dir}/plugins/${plugin_name}"
marketplace_root="${project_dir}"
marketplace_manifest="${marketplace_root}/.agents/plugins/marketplace.json"
home_root="${HOME:?HOME is required}"
codex_home="${CODEX_HOME:-${home_root}/.codex}"
legacy_profile="${codex_home}/agents/orchestrator.toml"
legacy_plugin_name="codex-""orchestration-""control"
legacy_cache="${codex_home}/plugins/cache/personal/${legacy_plugin_name}"
legacy_marketplace="${home_root}/.agents/plugins/marketplace.json"
# Only the exact retired profile distributed by this project is eligible for automatic removal.
legacy_profile_sha256="6b74fa45aa5e2312aca5472a17b39a638bdba7a74da7c36ce9a2fa9db925c367"
mode="install"
# Preserve only an explicit user override; never introduce a default during install.
cortex_mcp_approval_override=""

usage() {
  cat <<'EOF'
Usage: scripts/sync-cortex.sh [--check|--dry-run]

Install or update cortex from this repository's local
marketplace. --check is read-only and detects same-version content drift.
--dry-run reports planned changes without writing. Set HOME and CODEX_HOME to
temporary directories for an isolated validation run.
EOF
}

case "${1:-}" in
  "") ;;
  --check) mode="check" ;;
  --dry-run) mode="dry-run" ;;
  -h|--help) usage; exit 0 ;;
  *) echo "error: unknown argument: $1" >&2; usage >&2; exit 2 ;;
esac

run() {
  if [[ "${mode}" == "dry-run" ]]; then
    printf 'would run:'; printf ' %q' "$@"; printf '\n'
  else
    "$@"
  fi
}

validate_roots() {
  local validated
  validated="$(python3 - "${home_root}" "${codex_home}" <<'PY'
import os, stat, sys
from pathlib import Path

def validate(value, label, must_exist):
    path = Path(value).expanduser().absolute()
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        try:
            info = current.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode):
            raise SystemExit(f"error: {label} must not traverse symlinks: {current}")
    if must_exist and not path.is_dir():
        raise SystemExit(f"error: {label} must be an existing directory: {path}")
    if path.exists() and not path.is_dir():
        raise SystemExit(f"error: {label} must be a directory: {path}")
    return path

print(validate(sys.argv[1], "HOME", True))
print(validate(sys.argv[2], "CODEX_HOME", False))
PY
)" || return 1
  home_root="$(printf '%s\n' "${validated}" | sed -n '1p')"
  codex_home="$(printf '%s\n' "${validated}" | sed -n '2p')"
  legacy_profile="${codex_home}/agents/orchestrator.toml"
  legacy_cache="${codex_home}/plugins/cache/personal/${legacy_plugin_name}"
  legacy_marketplace="${home_root}/.agents/plugins/marketplace.json"
}

validate_cleanup_target() {
  local root="$1" relative="$2" target="$3"
  python3 - "${root}" "${relative}" "${target}" <<'PY'
import stat, sys
from pathlib import Path
root, relative, target = Path(sys.argv[1]).absolute(), Path(sys.argv[2]), Path(sys.argv[3]).absolute()
expected = root / relative
if target != expected or target.parent == root.parent:
    raise SystemExit(f"error: cleanup target is not the exact expected path: {target}")
target.relative_to(root)
current = Path(target.anchor)
for part in target.parts[1:]:
    current /= part
    try:
        info = current.lstat()
    except FileNotFoundError:
        continue
    if stat.S_ISLNK(info.st_mode):
        raise SystemExit(f"error: cleanup target must not traverse symlinks: {current}")
PY
}

prepare_backup_directory() {
  local backup_dir="$1"
  validate_cleanup_target "${codex_home}" "backups/${plugin_name}-upgrade" "${backup_dir}" || return 1
  run mkdir -p -- "${backup_dir}"
  run chmod 700 -- "${backup_dir}"
}

harden_backup_slot() {
  run chmod -R go-rwx -- "$1"
}

backup_file_and_remove() {
  local target="$1" label="$2" root="$3" relative="$4"
  [[ -e "${target}" || -L "${target}" ]] || return 0
  validate_cleanup_target "${root}" "${relative}" "${target}" || return 1
  [[ -f "${target}" && ! -L "${target}" ]] || { echo "error: refusing non-regular cleanup target: ${target}" >&2; return 1; }
  if [[ "${mode}" == "check" ]]; then
    echo "outdated legacy ${label}: ${target}" >&2
    return 1
  fi
  local backup_dir="${codex_home}/backups/${plugin_name}-upgrade" backup_slot
  prepare_backup_directory "${backup_dir}" || return 1
  if [[ "${mode}" == "dry-run" ]]; then
    backup_slot="${backup_dir}/${label}-DRY-RUN"
    printf 'would reserve backup slot: %s\n' "${backup_slot}"
  else
    backup_slot="$(mktemp -d "${backup_dir}/${label}-$(date -u +%Y%m%dT%H%M%SZ).XXXXXX")"
  fi
  run cp -a -- "${target}" "${backup_slot}/${label}"
  harden_backup_slot "${backup_slot}"
  run rm -- "${target}"
  echo "backed up and removed legacy ${label}: ${target} (${backup_slot})"
}

remove_authenticated_legacy_cache() {
  [[ -e "${legacy_cache}" || -L "${legacy_cache}" ]] || return 0
  validate_cleanup_target "${codex_home}" "plugins/cache/personal/${legacy_plugin_name}" "${legacy_cache}" || return 1
  python3 - "${legacy_cache}" <<'PY' || return 1
import json, re, stat, sys
from pathlib import Path
root = Path(sys.argv[1]).absolute()
if root.is_symlink() or not root.is_dir():
    raise SystemExit(f"error: refusing unexpected retired cache path: {root}")
for path in root.rglob("*"):
    if path.is_symlink():
        raise SystemExit(f"error: retired cache contains a symlink: {path}")
versions = [path for path in root.iterdir() if path.is_dir()]
if len(versions) != 1:
    raise SystemExit("error: retired cache must contain exactly one known version")
version_root = versions[0]
manifest_path = version_root / ".codex-plugin" / "plugin.json"
server_path = version_root / "scripts" / "orchestration_control.py"
try:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    server = server_path.read_text(encoding="utf-8")
except (OSError, json.JSONDecodeError) as exc:
    raise SystemExit(f"error: retired cache manifest is unreadable: {exc}")
expected_name = "codex-" + "orchestration-" + "control"
expected_version = "4." + "4.0"
match = re.search(r'^SERVER_VERSION = "([^"]+)"$', server, re.MULTILINE)
if version_root.name != expected_version or manifest.get("name") != expected_name or manifest.get("version") != expected_version or not match or match.group(1) != expected_version:
    raise SystemExit("error: refusing unauthenticated retired plugin cache")
PY
  if [[ "${mode}" == "check" ]]; then
    echo "outdated legacy personal-plugin-cache: ${legacy_cache}" >&2
    return 1
  fi
  local backup_dir="${codex_home}/backups/${plugin_name}-upgrade" backup_slot
  prepare_backup_directory "${backup_dir}" || return 1
  if [[ "${mode}" == "dry-run" ]]; then
    backup_slot="${backup_dir}/personal-plugin-cache-DRY-RUN"
    printf 'would reserve backup slot: %s\n' "${backup_slot}"
  else
    backup_slot="$(mktemp -d "${backup_dir}/personal-plugin-cache-$(date -u +%Y%m%dT%H%M%SZ).XXXXXX")"
  fi
  run cp -a -- "${legacy_cache}" "${backup_slot}/personal-plugin-cache"
  harden_backup_slot "${backup_slot}"
  if [[ "${mode}" == "dry-run" ]]; then
    echo "would remove authenticated retired plugin cache: ${legacy_cache}"
    return 0
  fi
  python3 - "${legacy_cache}" <<'PY'
import os, stat, sys
from pathlib import Path
root = Path(sys.argv[1]).absolute()
for directory, names, files in os.walk(root, topdown=False, followlinks=False):
    base = Path(directory)
    if base.is_symlink():
        raise SystemExit(f"error: refusing symlink during retired cache removal: {base}")
    for name in files:
        path = base / name
        if path.is_symlink() or not stat.S_ISREG(path.lstat().st_mode):
            raise SystemExit(f"error: refusing non-regular retired cache entry: {path}")
        path.unlink()
    for name in names:
        path = base / name
        if path.is_symlink() or not path.is_dir():
            raise SystemExit(f"error: refusing unexpected retired cache directory: {path}")
        path.rmdir()
root.rmdir()
PY
  echo "backed up and removed authenticated retired plugin cache: ${legacy_cache} (${backup_slot})"
}

validate_sources() {
  [[ -f "${plugin_source}/.codex-plugin/plugin.json" ]] || { echo "error: plugin manifest is missing" >&2; return 1; }
  [[ -f "${plugin_source}/.mcp.json" ]] || { echo "error: MCP manifest is missing" >&2; return 1; }
  [[ -f "${plugin_source}/hooks/hooks.json" ]] || { echo "error: hooks manifest is missing" >&2; return 1; }
  [[ -f "${marketplace_manifest}" && ! -L "${marketplace_manifest}" ]] || { echo "error: root marketplace manifest is missing or symlinked" >&2; return 1; }
  python3 "${script_dir}/validate-cortex-marketplace.py"
  python3 - "${plugin_source}/.codex-plugin/plugin.json" "${plugin_source}/scripts/cortex.py" <<'PY'
import importlib.util, json, sys
manifest, server = sys.argv[1:]
version = json.load(open(manifest, encoding="utf-8"))["version"]
spec = importlib.util.spec_from_file_location("cortex_sync_check", server)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
base_version = version.split("+", 1)[0]
if module.SERVER_VERSION != version or base_version != "1.0.3":
    raise SystemExit("plugin/server version must match the 1.0.3 release manifest")
PY
}

remove_legacy_profile() {
  [[ -e "${legacy_profile}" || -L "${legacy_profile}" ]] || return 0
  validate_cleanup_target "${codex_home}" "agents/orchestrator.toml" "${legacy_profile}" || return 1
  if [[ -L "${legacy_profile}" || ! -f "${legacy_profile}" ]]; then
    echo "error: refusing unexpected legacy profile path: ${legacy_profile}" >&2; return 1
  fi
  if [[ "$(sha256sum -- "${legacy_profile}" | awk '{print $1}')" != "${legacy_profile_sha256}" ]]; then
    echo "error: refusing to remove modified legacy profile: ${legacy_profile}" >&2; return 1
  fi
  backup_file_and_remove "${legacy_profile}" "orchestrator-profile" "${codex_home}" "agents/orchestrator.toml"
}

remove_legacy_marketplace_entry() {
  [[ -e "${legacy_marketplace}" || -L "${legacy_marketplace}" ]] || return 0
  validate_cleanup_target "${home_root}" ".agents/plugins/marketplace.json" "${legacy_marketplace}" || return 1
  [[ -f "${legacy_marketplace}" && ! -L "${legacy_marketplace}" ]] || { echo "error: refusing unexpected retired marketplace path: ${legacy_marketplace}" >&2; return 1; }
  if ! python3 - "${legacy_marketplace}" <<'PY'
import json, sys
try:
    data = json.load(open(sys.argv[1], encoding="utf-8"))
except (OSError, json.JSONDecodeError):
    raise SystemExit(1)
legacy = "codex-" + "orchestration-" + "control"
raise SystemExit(0 if any(item.get("name") == legacy and item.get("source", {}).get("source") == "local" and item.get("source", {}).get("path") == "./plugins/" + legacy for item in data.get("plugins", [])) else 1)
PY
  then return 0; fi
  if [[ "${mode}" == "check" ]]; then echo "outdated legacy marketplace entry: ${legacy_marketplace}" >&2; return 1; fi
  local backup_dir="${codex_home}/backups/${plugin_name}-upgrade" backup_slot
  prepare_backup_directory "${backup_dir}" || return 1
  if [[ "${mode}" == "dry-run" ]]; then
    backup_slot="${backup_dir}/personal-marketplace-DRY-RUN"
    printf 'would reserve backup slot: %s\n' "${backup_slot}"
  else
    backup_slot="$(mktemp -d "${backup_dir}/personal-marketplace-$(date -u +%Y%m%dT%H%M%SZ).XXXXXX")"
  fi
  run cp -a -- "${legacy_marketplace}" "${backup_slot}/personal-marketplace.json"
  harden_backup_slot "${backup_slot}"
  if [[ "${mode}" == "dry-run" ]]; then echo "would remove exact legacy marketplace entry from ${legacy_marketplace}"; return 0; fi
  python3 - "${legacy_marketplace}" <<'PY'
import json, os, sys, tempfile
path = sys.argv[1]
with open(path, encoding="utf-8") as stream: data = json.load(stream)
legacy = "codex-" + "orchestration-" + "control"
data["plugins"] = [item for item in data.get("plugins", []) if not (item.get("name") == legacy and item.get("source", {}).get("source") == "local" and item.get("source", {}).get("path") == "./plugins/" + legacy)]
fd, temporary = tempfile.mkstemp(prefix=".marketplace.", dir=os.path.dirname(path))
with os.fdopen(fd, "w", encoding="utf-8") as stream:
    json.dump(data, stream, ensure_ascii=False, indent=2); stream.write("\n")
os.replace(temporary, path)
PY
  echo "backed up and removed exact legacy marketplace entry: ${legacy_marketplace}"
}

plugin_version() {
  python3 - "${plugin_source}/.codex-plugin/plugin.json" <<'PY'
import json, sys
print(json.load(open(sys.argv[1], encoding="utf-8"))["version"])
PY
}

content_matches() {
  local installed="${codex_home}/plugins/cache/${marketplace_name}/${plugin_name}/$(plugin_version)"
  [[ -d "${installed}" ]] || return 1
  python3 - "${plugin_source}" "${installed}" <<'PY'
import hashlib, pathlib, sys
def manifest(root):
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob("*") if p.is_file() and "__pycache__" not in p.parts and p.suffix not in {".pyc", ".pyo"}}
raise SystemExit(0 if manifest(pathlib.Path(sys.argv[1])) == manifest(pathlib.Path(sys.argv[2])) else 1)
PY
}

capture_cortex_mcp_approval_override() {
  local config_path="${codex_home}/config.toml"
  cortex_mcp_approval_override=""
  [[ -e "${config_path}" || -L "${config_path}" ]] || return 0
  [[ -f "${config_path}" && ! -L "${config_path}" ]] || {
    echo "error: refusing to inspect non-regular Codex config: ${config_path}" >&2
    return 1
  }
  cortex_mcp_approval_override="$({
    python3 - "${config_path}" <<'PY'
import sys
import tomllib
from pathlib import Path

path = Path(sys.argv[1])
try:
    payload = tomllib.loads(path.read_text(encoding="utf-8"))
except (OSError, UnicodeError, tomllib.TOMLDecodeError) as exc:
    raise SystemExit(f"error: cannot parse Codex config for Cortex approval override: {exc}")

try:
    value = payload["plugins"]["cortex@cortex"]["mcp_servers"]["cortex"]["default_tools_approval_mode"]
except (KeyError, TypeError):
    raise SystemExit(0)

allowed = {"auto", "prompt", "writes", "approve"}
if value not in allowed:
    raise SystemExit(
        "error: Cortex MCP default_tools_approval_mode must be one of "
        + ", ".join(sorted(allowed))
    )
print(value)
PY
  })" || return 1
}

restore_cortex_mcp_approval_override() {
  [[ -n "${cortex_mcp_approval_override}" ]] || return 0
  local config_path="${codex_home}/config.toml"
  if [[ "${mode}" == "dry-run" ]]; then
    echo "would preserve Cortex MCP default_tools_approval_mode=${cortex_mcp_approval_override}"
    return 0
  fi
  [[ -f "${config_path}" && ! -L "${config_path}" ]] || {
    echo "error: Codex config disappeared or became non-regular during Cortex update: ${config_path}" >&2
    return 1
  }
  python3 - "${config_path}" "${cortex_mcp_approval_override}" <<'PY'
import os
import re
import stat
import sys
import tempfile
import tomllib
from pathlib import Path

path = Path(sys.argv[1])
value = sys.argv[2]
allowed = {"auto", "prompt", "writes", "approve"}
if value not in allowed:
    raise SystemExit(f"error: invalid captured Cortex MCP approval mode: {value}")
if path.is_symlink() or not path.is_file():
    raise SystemExit(f"error: refusing to update non-regular Codex config: {path}")

original = path.read_text(encoding="utf-8")
text = original
lines = text.splitlines(keepends=True)
header = '[plugins."cortex@cortex".mcp_servers.cortex]'
header_indexes = [index for index, line in enumerate(lines) if line.strip() == header]
if len(header_indexes) > 1:
    raise SystemExit("error: Codex config contains duplicate Cortex MCP approval tables")

if not header_indexes:
    if text and not text.endswith(("\n", "\r")):
        text += "\n"
    if text and not text.endswith("\n\n"):
        text += "\n"
    text += f'{header}\ndefault_tools_approval_mode = "{value}"\n'
else:
    start = header_indexes[0] + 1
    end = start
    table_header = re.compile(r"^\s*\[(?!\[).+\]\s*(?:#.*)?$")
    while end < len(lines) and not table_header.match(lines[end]):
        end += 1
    key_indexes = [
        index
        for index in range(start, end)
        if re.match(r"^\s*default_tools_approval_mode\s*=", lines[index])
    ]
    if len(key_indexes) > 1:
        raise SystemExit("error: Codex config contains duplicate Cortex MCP approval keys")
    if not key_indexes:
        lines.insert(start, f'default_tools_approval_mode = "{value}"\n')
        text = "".join(lines)
    else:
        index = key_indexes[0]
        line = lines[index]
        newline = "\r\n" if line.endswith("\r\n") else "\n" if line.endswith("\n") else ""
        body = line[:-len(newline)] if newline else line
        prefix_match = re.match(r"^(\s*default_tools_approval_mode\s*=\s*)", body)
        if prefix_match is None:
            raise SystemExit("error: unable to locate Cortex MCP approval key")
        comment = ""
        if "#" in body[prefix_match.end():]:
            comment = " #" + body[prefix_match.end():].split("#", 1)[1].lstrip()
        lines[index] = f'{prefix_match.group(1)}"{value}"{comment}{newline}'
        text = "".join(lines)

try:
    parsed = tomllib.loads(text)
    observed = parsed["plugins"]["cortex@cortex"]["mcp_servers"]["cortex"]["default_tools_approval_mode"]
except (KeyError, TypeError, OSError, UnicodeError, tomllib.TOMLDecodeError) as exc:
    raise SystemExit(f"error: restored Codex config is invalid: {exc}")
if observed != value:
    raise SystemExit("error: restored Cortex MCP approval override was not retained")
if text == original:
    raise SystemExit(0)

mode = stat.S_IMODE(path.stat().st_mode)
fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
try:
    with os.fdopen(fd, "w", encoding="utf-8", newline="") as stream:
        stream.write(text)
        stream.flush()
        os.fsync(stream.fileno())
    os.chmod(temporary, mode)
    os.replace(temporary, path)
except Exception:
    try:
        os.unlink(temporary)
    except FileNotFoundError:
        pass
    raise
PY
}

installed_version() {
  codex plugin list --json 2>/dev/null | python3 -c 'import json,sys; value=json.load(sys.stdin); rows=value.get("installed", value) if isinstance(value, dict) else value; print(next((row.get("version", "") for row in rows if row.get("pluginId") == "cortex@cortex"), ""))' 2>/dev/null || true
}

install_or_check() {
  command -v codex >/dev/null 2>&1 || { echo "error: codex CLI is required" >&2; return 1; }
  local version expected_version; version="$(installed_version)"; expected_version="$(plugin_version)"
  if [[ "${mode}" == "check" ]]; then
    [[ "${version}" == "${expected_version}" ]] || { echo "outdated ${plugin_name}@${marketplace_name}: expected ${expected_version}, found ${version:-missing}" >&2; return 1; }
    content_matches || { echo "outdated ${plugin_name}@${marketplace_name}: same-version content drift"; return 1; }
    echo "ok      ${plugin_name}@${marketplace_name} (${expected_version}, content verified)"; return 0
  fi
  capture_cortex_mcp_approval_override || return 1
  if ! run codex plugin marketplace add "${marketplace_root}" --json >/dev/null; then
    restore_cortex_mcp_approval_override || true
    return 1
  fi
  if [[ -n "${version}" ]] && ! run codex plugin remove "${plugin_name}@${marketplace_name}" --json >/dev/null; then
    restore_cortex_mcp_approval_override || true
    return 1
  fi
  if ! run codex plugin add "${plugin_name}@${marketplace_name}" --json >/dev/null; then
    restore_cortex_mcp_approval_override || true
    return 1
  fi
  restore_cortex_mcp_approval_override || return 1
  [[ "${mode}" == "dry-run" ]] || content_matches || { echo "error: installed plugin content differs from source" >&2; return 1; }
  echo "installed ${plugin_name}@${marketplace_name} from ${marketplace_root}"
}

validate_roots
validate_sources
status=0
remove_legacy_profile || status=1
remove_authenticated_legacy_cache || status=1
remove_legacy_marketplace_entry || status=1
install_or_check || status=1
[[ "${status}" -eq 0 ]] || exit "${status}"
[[ "${mode}" == "check" ]] && echo "Cortex is up to date." || echo "Cortex installed from this repository. Start a new Codex thread to pick up its skills and MCP tools."
