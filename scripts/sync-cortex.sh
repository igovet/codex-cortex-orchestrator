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
mode="install"
# Preserve explicit user configuration, and install the Luna default when the
# global subagent setting is absent. The native spawn_agent request can then
# omit `model` and let Codex resolve this configured default.
cortex_mcp_approval_override=""
global_subagent_model=""
global_subagent_model_state="missing"
global_config_mode=""
global_config_backup_created="false"
original_global_subagent_model_state=""
original_global_config_mode=""

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
}

validate_global_config_path() {
  local config_path="${codex_home}/config.toml"
  [[ -e "${config_path}" || -L "${config_path}" ]] || return 0
  [[ -f "${config_path}" && ! -L "${config_path}" ]] || {
    echo "error: refusing to inspect non-regular Codex config: ${config_path}" >&2
    return 1
  }
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

validate_sources() {
  [[ -f "${plugin_source}/.codex-plugin/plugin.json" ]] || { echo "error: plugin manifest is missing" >&2; return 1; }
  [[ -f "${plugin_source}/.mcp.json" ]] || { echo "error: MCP manifest is missing" >&2; return 1; }
  [[ -f "${plugin_source}/hooks/hooks.json" ]] || { echo "error: hooks manifest is missing" >&2; return 1; }
  [[ -f "${script_dir}/sync-cortex-hook-trust.py" && ! -L "${script_dir}/sync-cortex-hook-trust.py" ]] || { echo "error: hook trust synchronizer is missing or symlinked" >&2; return 1; }
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
if module.SERVER_VERSION != version or base_version != "6.4.0":
    raise SystemExit("plugin/server version must match the 6.4.0 release manifest")
PY
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
  validate_global_config_path || return 1
  [[ -e "${config_path}" || -L "${config_path}" ]] || return 0
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

capture_global_subagent_model() {
  local config_path="${codex_home}/config.toml"
  global_subagent_model=""
  global_subagent_model_state="missing"
  validate_global_config_path || return 1
  [[ -e "${config_path}" || -L "${config_path}" ]] || return 0
  global_subagent_model_state="$(python3 - "${config_path}" <<'PY'
import sys
import tomllib
from pathlib import Path

path = Path(sys.argv[1])
try:
    payload = tomllib.loads(path.read_text(encoding="utf-8"))
except (OSError, UnicodeError, tomllib.TOMLDecodeError) as exc:
    raise SystemExit(f"error: cannot parse Codex config for agents.default_subagent_model: {exc}")
try:
    value = payload["agents"]["default_subagent_model"]
except (KeyError, TypeError):
    print("missing")
    raise SystemExit(0)
if not isinstance(value, str) or not value.strip():
    raise SystemExit("error: agents.default_subagent_model must be a non-empty string")
print(value.strip())
PY
)" || return 1
  if [[ "${global_subagent_model_state}" != "missing" ]]; then
    global_subagent_model="${global_subagent_model_state}"
  fi
  if [[ -f "${config_path}" && ! -L "${config_path}" ]]; then
    global_config_mode="$(python3 - "${config_path}" <<'PY'
import stat, sys
from pathlib import Path
print(format(stat.S_IMODE(Path(sys.argv[1]).stat().st_mode), "o"))
PY
)" || return 1
  fi
}

backup_global_config_for_update() {
  local config_path="${codex_home}/config.toml" backup_dir backup_slot
  [[ -f "${config_path}" && ! -L "${config_path}" ]] || return 0
  [[ "${mode}" != "dry-run" && "${mode}" != "check" ]] || return 0
  [[ "${global_config_backup_created}" != "true" ]] || return 0
  backup_dir="${codex_home}/backups/${plugin_name}-upgrade"
  prepare_backup_directory "${backup_dir}" || return 1
  backup_slot="$(mktemp -d "${backup_dir}/codex-config-$(date -u +%Y%m%dT%H%M%SZ).XXXXXX")"
  run cp -a -- "${config_path}" "${backup_slot}/config.toml"
  harden_backup_slot "${backup_slot}"
  global_config_backup_created="true"
}

sync_cortex_hook_trust() {
  local expected_version installed_root config_path codex_binary
  local -a check_argument=()
  expected_version="$(plugin_version)"
  installed_root="${codex_home}/plugins/cache/${marketplace_name}/${plugin_name}/${expected_version}"
  config_path="${codex_home}/config.toml"
  codex_binary="$(command -v codex)"
  if [[ "${mode}" == "dry-run" ]]; then
    echo "would trust the five exact installed Cortex lifecycle hook content hashes"
    return 0
  fi
  if [[ "${mode}" == "check" ]]; then
    check_argument=(--check)
  fi
  python3 "${script_dir}/sync-cortex-hook-trust.py" \
    --codex "${codex_binary}" \
    --cwd "${project_dir}" \
    --installed-root "${installed_root}" \
    --config "${config_path}" \
    "${check_argument[@]}"
}

check_global_subagent_model() {
  capture_global_subagent_model || return 1
  if [[ "${global_subagent_model_state}" != "gpt-5.6-luna" ]]; then
    echo "outdated Codex global config: agents.default_subagent_model must be gpt-5.6-luna (found ${global_subagent_model_state})" >&2
    return 1
  fi
  echo "ok      agents.default_subagent_model=${global_subagent_model_state}"
}

ensure_global_subagent_model() {
  local config_path="${codex_home}/config.toml"
  local target_model="gpt-5.6-luna" previous_model
  capture_global_subagent_model || return 1
  previous_model="${original_global_subagent_model_state:-${global_subagent_model_state}}"
  if [[ "${previous_model}" == "${target_model}" ]]; then
    if [[ "${mode}" == "install" && -n "${original_global_config_mode}" ]]; then
      run chmod "${original_global_config_mode}" -- "${config_path}"
    fi
    echo "ok      agents.default_subagent_model=${target_model}"
    return 0
  fi
  if [[ "${mode}" == "check" ]]; then
    echo "outdated Codex global config: agents.default_subagent_model must be ${target_model} (found ${previous_model})" >&2
    return 1
  fi
  if [[ "${mode}" == "dry-run" ]]; then
    if [[ "${previous_model}" == "missing" ]]; then
      echo "would set agents.default_subagent_model=${target_model}"
    else
      echo "would back up config and replace agents.default_subagent_model=${previous_model} with ${target_model}"
    fi
    return 0
  fi
  if [[ "${previous_model}" != "missing" && "${global_config_backup_created}" != "true" ]]; then
    echo "error: refusing to replace agents.default_subagent_model without a private pre-install backup" >&2
    return 1
  fi
  python3 - "${config_path}" "${target_model}" "${original_global_config_mode:-${global_config_mode}}" <<'PY'
import os
import json
import re
import stat
import sys
import tempfile
import tomllib
from pathlib import Path

path = Path(sys.argv[1])
desired = sys.argv[2]
original_mode = int(sys.argv[3], 8) if len(sys.argv) > 3 and sys.argv[3] else None
encoded_desired = json.dumps(desired)
path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
if path.exists() and (path.is_symlink() or not path.is_file()):
    raise SystemExit(f"error: refusing to update non-regular Codex config: {path}")
original = path.read_text(encoding="utf-8") if path.exists() else ""
lines = original.splitlines(keepends=True)
header = "[agents]"
headers = [index for index, line in enumerate(lines) if line.strip() == header]
if len(headers) > 1:
    raise SystemExit("error: Codex config contains duplicate [agents] tables")
if not headers:
    text = original
    if text and not text.endswith(("\n", "\r")):
        text += "\n"
    if text and not text.endswith("\n\n"):
        text += "\n"
    text += f"{header}\ndefault_subagent_model = {encoded_desired}\n"
else:
    start = headers[0] + 1
    end = start
    table_header = re.compile(r"^\s*\[(?!\[).+\]\s*(?:#.*)?$")
    while end < len(lines) and not table_header.match(lines[end]):
        end += 1
    key_indexes = [
        index for index in range(start, end)
        if re.match(r"^\s*default_subagent_model\s*=", lines[index])
    ]
    if len(key_indexes) > 1:
        raise SystemExit("error: Codex config contains duplicate agents.default_subagent_model keys")
    if not key_indexes:
        lines.insert(start, f"default_subagent_model = {encoded_desired}\n")
        text = "".join(lines)
    else:
        index = key_indexes[0]
        line = lines[index]
        newline = "\r\n" if line.endswith("\r\n") else "\n" if line.endswith("\n") else ""
        body = line[:-len(newline)] if newline else line
        prefix = re.match(r"^(\s*default_subagent_model\s*=\s*)", body)
        if prefix is None:
            raise SystemExit("error: unable to locate agents.default_subagent_model key")
        comment = ""
        if "#" in body[prefix.end():]:
            comment = " #" + body[prefix.end():].split("#", 1)[1].lstrip()
        lines[index] = f"{prefix.group(1)}{encoded_desired}{comment}{newline}"
        text = "".join(lines)
try:
    parsed = tomllib.loads(text)
    observed = parsed["agents"]["default_subagent_model"]
except (KeyError, TypeError, OSError, UnicodeError, tomllib.TOMLDecodeError) as exc:
    raise SystemExit(f"error: updated Codex config is invalid: {exc}")
if observed != desired:
    raise SystemExit("error: updated agents.default_subagent_model was not retained")
mode = original_mode if original_mode is not None else (stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o600)
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
  if [[ "${previous_model}" == "missing" ]]; then
    echo "configured agents.default_subagent_model=${target_model}"
  else
    echo "backed up config and replaced agents.default_subagent_model=${previous_model} with ${target_model}"
  fi
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
  # A dry run is deliberately usable in minimal CI images: it validates the
  # source/config paths and reports every native Codex command without
  # executing one. Real installation and read-only installed-content checks
  # still require the CLI.
  if [[ "${mode}" != "dry-run" ]]; then
    command -v codex >/dev/null 2>&1 || { echo "error: codex CLI is required" >&2; return 1; }
  fi
  local version expected_version; version="$(installed_version)"; expected_version="$(plugin_version)"
  if [[ "${mode}" == "check" ]]; then
    [[ "${version}" == "${expected_version}" ]] || { echo "outdated ${plugin_name}@${marketplace_name}: expected ${expected_version}, found ${version:-missing}" >&2; return 1; }
    content_matches || { echo "outdated ${plugin_name}@${marketplace_name}: same-version content drift"; return 1; }
    check_global_subagent_model || return 1
    sync_cortex_hook_trust || return 1
    echo "ok      ${plugin_name}@${marketplace_name} (${expected_version}, content verified)"; return 0
  fi
  capture_cortex_mcp_approval_override || return 1
  capture_global_subagent_model || return 1
  original_global_subagent_model_state="${global_subagent_model_state}"
  original_global_config_mode="${global_config_mode}"
  backup_global_config_for_update || return 1
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
  ensure_global_subagent_model || return 1
  [[ "${mode}" == "dry-run" ]] || content_matches || { echo "error: installed plugin content differs from source" >&2; return 1; }
  sync_cortex_hook_trust || return 1
  echo "installed ${plugin_name}@${marketplace_name} from ${marketplace_root}"
}

validate_roots
validate_global_config_path
validate_sources
status=0
install_or_check || status=1
[[ "${status}" -eq 0 ]] || exit "${status}"
if [[ "${mode}" == "check" ]]; then
  echo "Cortex is up to date."
else
  echo "Cortex installed from this repository. Start a new Codex thread before dispatching agents so it loads the installed hook paths."
fi
