#!/usr/bin/env bash
# Install or verify the repo-local orchestration plugin without touching active state by default.
set -euo pipefail
export PYTHONDONTWRITEBYTECODE=1

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
# Preserve unrelated user configuration while enforcing Cortex's native worker
# prerequisites. A pre-existing non-Luna default is backed up before it is
# replaced; native Luna dispatches can then omit the `model` argument.
cortex_mcp_approval_override=""
global_subagent_model=""
global_subagent_model_state="missing"
multi_agent_v2_state="missing"
global_config_mode=""
global_config_backup_created="false"
original_global_subagent_model_state=""
original_global_config_mode=""
cortex_python=""
candidate_root=""
candidate_version=""

# `cortex-dev` sets these three values only after it has established the
# lexical, non-symlinked `$HOME/.cortex-dev/.codex` boundary.  Normal source
# sync remains usable for isolated test homes, but it must never attempt to
# repair a marketplace registration unless this explicit live-dev boundary is
# present and re-validated below.
isolated_reconcile_enabled="${CORTEX_ISOLATED_MARKETPLACE_RECONCILE:-}"
isolated_owner_home="${CORTEX_ISOLATED_DEV_OWNER_HOME:-}"
isolated_expected_codex_home="${CORTEX_ISOLATED_DEV_CODEX_HOME:-}"

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

resolve_cortex_python() {
  local requested resolved diagnostics
  if [[ ${CORTEX_PYTHON+x} == x ]]; then
    requested="${CORTEX_PYTHON}"
    if [[ "${requested}" != /* ]]; then
      echo "error: CORTEX_PYTHON must be an absolute executable path" >&2
      return 1
    fi
  else
    requested="python3"
  fi
  if [[ -z "${requested}" ]]; then
    echo "error: CORTEX_PYTHON must name an executable Python path" >&2
    return 1
  fi
  if [[ "${requested}" == */* ]]; then
    resolved="${requested}"
  else
    resolved="$(command -v -- "${requested}" 2>/dev/null || true)"
  fi
  if [[ -z "${resolved}" || ! -f "${resolved}" || ! -x "${resolved}" ]]; then
    echo "error: CORTEX_PYTHON=${requested} is not an executable file" >&2
    return 1
  fi
  if ! diagnostics="$("${resolved}" -B -c 'import sys
if sys.version_info < (3, 11):
    print(f"Python {sys.version.split()[0]} is too old; Python 3.11 or newer is required")
    raise SystemExit(1)
try:
    import tomllib
except ImportError:
    print("tomllib is unavailable")
    raise SystemExit(1)
print(sys.executable)' 2>&1)"; then
    [[ -n "${diagnostics}" ]] || diagnostics="runtime validation failed"
    echo "error: CORTEX_PYTHON=${requested} is incompatible: ${diagnostics}" >&2
    return 1
  fi
  cortex_python="${resolved}"
}

validate_roots() {
  local validated
  validated="$("${cortex_python}" -B - "${home_root}" "${codex_home}" "${script_dir}" <<'PY'
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[3])
from cortex_payload_manifest import RuntimePayloadError, validated_managed_directory

def validate(value, label, must_exist):
    try:
        return validated_managed_directory(Path(value), label, allow_missing=not must_exist)
    except RuntimePayloadError as exc:
        raise SystemExit(f"error: {exc}") from None

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

validate_isolated_reconcile_target() {
  [[ "${isolated_reconcile_enabled}" == "1" ]] || return 1
  [[ -n "${isolated_owner_home}" && -n "${isolated_expected_codex_home}" ]] || {
    echo "error: isolated marketplace reconciliation is missing its trusted target boundary" >&2
    return 1
  }
  "${cortex_python}" -B - "${home_root}" "${codex_home}" "${isolated_owner_home}" "${isolated_expected_codex_home}" "${script_dir}" <<'PY'
import sys
from pathlib import Path

sys.path.insert(0, sys.argv[5])
from cortex_payload_manifest import RuntimePayloadError, validated_managed_directory

home, codex_home, owner_home, expected_codex = (Path(value) for value in sys.argv[1:5])
try:
    home = validated_managed_directory(home, "isolated HOME")
    codex_home = validated_managed_directory(codex_home, "isolated CODEX_HOME")
    owner_home = validated_managed_directory(owner_home, "isolated owner HOME")
    expected_codex = validated_managed_directory(expected_codex, "isolated expected CODEX_HOME")
except RuntimePayloadError as exc:
    raise SystemExit(f"error: {exc}") from None

expected_home = owner_home / ".cortex-dev"
if home != expected_home or codex_home != expected_home / ".codex" or expected_codex != codex_home:
    raise SystemExit("error: marketplace reconciliation is allowed only for the exact isolated $HOME/.cortex-dev/.codex target")
PY
}

marketplace_registration_state() {
  local marketplace_json="$1"
  CORTEX_MARKETPLACE_LIST_JSON="${marketplace_json}" "${cortex_python}" -B - "${marketplace_root}" "${marketplace_name}" <<'PY'
import json
import os
import sys

expected_root, expected_name = sys.argv[1:]
try:
    payload = json.loads(os.environ.pop("CORTEX_MARKETPLACE_LIST_JSON"))
except json.JSONDecodeError as exc:
    raise SystemExit(f"error: unable to parse isolated marketplace list: {exc}") from None
rows = payload.get("marketplaces") if isinstance(payload, dict) else payload
if not isinstance(rows, list):
    raise SystemExit("error: isolated marketplace list has no marketplace array")
matches = [row for row in rows if isinstance(row, dict) and row.get("name") == expected_name]
if not matches:
    print("missing")
    raise SystemExit(0)
if len(matches) != 1:
    raise SystemExit("error: isolated marketplace list contains duplicate Cortex registrations")
root = matches[0].get("root")
if not isinstance(root, str) or not root:
    raise SystemExit("error: isolated Cortex marketplace registration has no local root")
# Do not resolve the returned path: a symlinked managed ancestor is not an
# equivalent candidate source.  The freshly staged candidate was already
# validated lexically, so equality is deliberately exact.
print("same" if root == expected_root else "different")
PY
}

reconcile_isolated_marketplace() {
  # This is the only path that repairs a stale marketplace. It is intentionally
  # unavailable to ordinary sync, check, and dry-run workflows so it cannot
  # mutate a stable profile merely because an environment variable was set.
  if [[ "${isolated_reconcile_enabled}" != "1" ]]; then
    run codex plugin marketplace add "${marketplace_root}" --json >/dev/null
    return
  fi
  validate_isolated_reconcile_target || return 1
  if [[ "${mode}" == "check" ]]; then
    echo "error: isolated marketplace reconciliation requires install mode" >&2
    return 1
  fi
  if [[ "${mode}" == "dry-run" ]]; then
    echo "would reconcile isolated Cortex marketplace at ${marketplace_root}"
    return
  fi
  local listed state
  listed="$(codex plugin marketplace list --json)" || {
    echo "error: cannot inspect the isolated Cortex marketplace registration" >&2
    return 1
  }
  state="$(marketplace_registration_state "${listed}")" || return 1
  case "${state}" in
    same)
      echo "isolated Cortex marketplace source is current"
      ;;
    missing)
      run codex plugin marketplace add "${marketplace_root}" --json >/dev/null || return 1
      echo "registered isolated Cortex marketplace candidate"
      ;;
    different)
      # The named CLI removal changes only the one Cortex registration.  It
      # does not rewrite unrelated isolated marketplaces and never consults
      # the user's stable Codex home.
      run codex plugin marketplace remove "${marketplace_name}" --json >/dev/null || return 1
      run codex plugin marketplace add "${marketplace_root}" --json >/dev/null || return 1
      echo "replaced stale isolated Cortex marketplace candidate"
      ;;
    *)
      echo "error: unexpected isolated marketplace registration state" >&2
      return 1
      ;;
  esac
}

validate_cleanup_target() {
  local root="$1" relative="$2" target="$3"
  "${cortex_python}" -B - "${root}" "${relative}" "${target}" <<'PY'
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

clean_plugin_bytecode() {
  # Python bytecode is disposable source-tree state. Remove only exact
  # __pycache__ directories and .pyc/.pyo files beneath the packaged plugin,
  # refusing symlinks so cleanup cannot escape the source tree.
  if [[ "${mode}" == "check" || "${mode}" == "dry-run" ]]; then
    return 0
  fi
  "${cortex_python}" -B - "${plugin_source}" <<'PY'
import os
import shutil
import stat
import sys
from pathlib import Path

root = Path(sys.argv[1]).absolute()
if root.is_symlink() or not root.is_dir():
    raise SystemExit(f"error: plugin source must be a regular directory: {root}")
for base, directories, files in os.walk(root, topdown=True, followlinks=False):
    current = Path(base)
    for name in [*directories, *files]:
        path = current / name
        if path.is_symlink():
            raise SystemExit(f"error: refusing bytecode cleanup through symlink: {path.relative_to(root)}")
    retained = []
    for name in directories:
        path = current / name
        if name == "__pycache__":
            if not stat.S_ISDIR(path.lstat().st_mode):
                raise SystemExit(f"error: bytecode state is not a directory: {path.relative_to(root)}")
            print(f"removed Python bytecode state: {path.relative_to(root)}")
            shutil.rmtree(path)
        else:
            retained.append(name)
    directories[:] = retained
    for name in files:
        path = current / name
        if path.suffix in {".pyc", ".pyo"}:
            if not stat.S_ISREG(path.lstat().st_mode):
                raise SystemExit(f"error: bytecode state is not a regular file: {path.relative_to(root)}")
            print(f"removed Python bytecode: {path.relative_to(root)}")
            path.unlink()
PY
}

sync_model_routing_catalog() {
  if [[ "${mode}" == "install" ]]; then
    "${cortex_python}" -B "${script_dir}/render_cortex_tool_catalog.py" --root "${project_dir}" --write
  else
    "${cortex_python}" -B "${script_dir}/render_cortex_tool_catalog.py" --root "${project_dir}" --check
  fi
}

prepare_candidate() {
  [[ "${mode}" == "install" ]] || return 0
  local staging_root="${codex_home}/.cortex-candidates"
  local temporary
  if ! "${cortex_python}" -B - "${staging_root}" "${script_dir}" <<'PY'
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[2])
from cortex_payload_manifest import RuntimePayloadError, ensure_managed_directory
try:
    ensure_managed_directory(Path(sys.argv[1]), "Cortex candidate staging root")
except RuntimePayloadError as exc:
    raise SystemExit(f"error: {exc}") from None
PY
  then
    return 1
  fi
  chmod 700 "${staging_root}"
  temporary="$(mktemp -d "${staging_root}/.candidate.XXXXXX")"
  if ! "${cortex_python}" -B - "${project_dir}" "${temporary}" <<'PY'
import sys
from pathlib import Path
root, destination = Path(sys.argv[1]), Path(sys.argv[2])
sys.path.insert(0, str(root / "scripts"))
from cortex_release_candidate import build_source_candidate, validate_candidate_tree  # noqa: E402
manifest = build_source_candidate(root, destination)
validate_candidate_tree(destination, manifest)
print(destination / "plugins/cortex/.codex-plugin/plugin.json")
PY
  then
    rm -rf -- "${temporary}"
    return 1
  fi
  candidate_version="$(${cortex_python} -B - "${temporary}/plugins/cortex/.codex-plugin/plugin.json" <<'PY'
import json, sys
print(json.load(open(sys.argv[1], encoding="utf-8"))["version"])
PY
)"
  [[ "${candidate_version}" =~ ^1\.14\.14\+codex\.sha256\.[0-9a-f]{16}$ ]] || {
    rm -rf -- "${temporary}"
    echo "error: staged candidate has invalid content-addressed version" >&2
    return 1
  }
  candidate_root="${staging_root}/${candidate_version}"
  if ! "${cortex_python}" -B - "${candidate_root}" "${script_dir}" <<'PY'
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[2])
from cortex_payload_manifest import RuntimePayloadError, validated_managed_directory
try:
    validated_managed_directory(Path(sys.argv[1]), "Cortex candidate version root", allow_missing=True)
except RuntimePayloadError as exc:
    raise SystemExit(f"error: {exc}") from None
PY
  then
    rm -rf -- "${temporary}"
    return 1
  fi
  if [[ -e "${candidate_root}" ]]; then
    if ! "${cortex_python}" -B - "${project_dir}" "${candidate_root}" "${temporary}" <<'PY'
import sys
from pathlib import Path
root, installed = Path(sys.argv[1]), Path(sys.argv[2])
sys.path.insert(0, str(root / "scripts"))
from cortex_release_candidate import plugin_tree_digest, source_candidate_manifest  # noqa: E402
manifest = source_candidate_manifest(root)
if plugin_tree_digest(installed / "plugins/cortex", manifest) != plugin_tree_digest(Path(sys.argv[3]) / "plugins/cortex", manifest):
    raise SystemExit("candidate identity collision or tampered immutable staging path")
PY
    then
      rm -rf -- "${temporary}"
      return 1
    fi
    rm -rf -- "${temporary}"
  else
    mv -- "${temporary}" "${candidate_root}"
  fi
  marketplace_root="${candidate_root}"
  marketplace_manifest="${marketplace_root}/.agents/plugins/marketplace.json"
  echo "staged Cortex candidate: ${candidate_version}"
  echo "marketplace validation passed: stamped candidate"
}

prepare_backup_directory() {
  local backup_dir="$1"
  validate_cleanup_target "${codex_home}" "backups/${plugin_name}-upgrade" "${backup_dir}" || return 1
  run mkdir -p -- "${backup_dir}"
  run chmod 700 "${backup_dir}"
}

harden_backup_slot() {
  run chmod -R go-rwx "$1"
}

validate_sources() {
  [[ -f "${plugin_source}/.codex-plugin/plugin.json" ]] || { echo "error: plugin manifest is missing" >&2; return 1; }
  [[ -f "${plugin_source}/.mcp.json" ]] || { echo "error: MCP manifest is missing" >&2; return 1; }
  [[ -f "${marketplace_manifest}" && ! -L "${marketplace_manifest}" ]] || { echo "error: root marketplace manifest is missing or symlinked" >&2; return 1; }
  # The checkout manifest is the source template, so its generated
  # content-address suffix may legitimately be stale after a working-tree
  # edit.  Install mode rebuilds and validates an immutable stamped candidate
  # in prepare_candidate; validating the unstamped checkout here would reject
  # the very refresh that is meant to reconcile it.  Read-only modes retain
  # the strict marketplace check and therefore continue to report drift.
  if [[ "${mode}" != "install" ]]; then
    "${cortex_python}" -B "${script_dir}/validate-cortex-marketplace.py"
  fi
  "${cortex_python}" -B "${script_dir}/verify-cortex-release.py" --mode source
  "${cortex_python}" -B - "${plugin_source}/.codex-plugin/plugin.json" "${plugin_source}/scripts/cortex.py" <<'PY'
import importlib.util, json, sys
from pathlib import Path
manifest, server = sys.argv[1:]
version = json.load(open(manifest, encoding="utf-8"))["version"]
sys.path.insert(0, str(Path(server).parent))
spec = importlib.util.spec_from_file_location("cortex_sync_check", server)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
base_version = version.split("+", 1)[0]
if module.SERVER_VERSION != base_version or base_version != "1.14.14":
    raise SystemExit("plugin/server semantic version must match the 1.14.14 release manifest")
PY
}

plugin_version() {
  if [[ -n "${candidate_version}" ]]; then
    printf '%s\n' "${candidate_version}"
    return
  fi
  "${cortex_python}" -B - "${plugin_source}/.codex-plugin/plugin.json" <<'PY'
import json, sys
print(json.load(open(sys.argv[1], encoding="utf-8"))["version"])
PY
}

reject_cache_collision() {
  local installed="${codex_home}/plugins/cache/${marketplace_name}/${plugin_name}/$(plugin_version)"
  "${cortex_python}" -B - "${project_dir}" "${installed}" "${script_dir}" <<'PY'
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[3])
from cortex_payload_manifest import RuntimePayloadError, validated_managed_directory
try:
    root = validated_managed_directory(Path(sys.argv[1]), "repository root")
    installed = validated_managed_directory(Path(sys.argv[2]), "installed candidate version root", allow_missing=True)
except RuntimePayloadError as exc:
    raise SystemExit(f"error: {exc}") from None
if not installed.exists():
    raise SystemExit(0)
sys.path.insert(0, str(root / "scripts"))
from cortex_release_candidate import plugin_tree_digest, source_candidate_manifest
manifest = source_candidate_manifest(root)
if not installed.is_dir() or plugin_tree_digest(installed, manifest) != manifest.plugin_digest(root):
    raise SystemExit("error: immutable cache identity collision or tampered candidate; refusing overwrite")
PY
}

content_matches() {
  local installed="${codex_home}/plugins/cache/${marketplace_name}/${plugin_name}/$(plugin_version)"
  "${cortex_python}" -B - "${project_dir}" "${installed}" "${script_dir}" <<'PY'
import sys
from pathlib import Path

sys.path.insert(0, sys.argv[3])
from cortex_payload_manifest import RuntimePayloadError, validated_managed_directory
try:
    root = validated_managed_directory(Path(sys.argv[1]), "repository root")
    installed = validated_managed_directory(Path(sys.argv[2]), "installed candidate version root")
except RuntimePayloadError as exc:
    raise SystemExit(f"error: {exc}") from None
sys.dont_write_bytecode = True
sys.path.insert(0, str(root / "scripts"))
from cortex_release_candidate import CandidateError, plugin_tree_digest, source_candidate_manifest

try:
    # Use the same canonical package closure and payload normalization that
    # stamps and validates candidates.  The former ad-hoc temporary-tree
    # comparison duplicated those rules and produced false content drift on
    # macOS even after the candidate had passed release validation.  This
    # remains fail-closed for missing, extra, changed, symlinked, non-regular,
    # bytecode, and retired payload paths; only the generated cache suffix is
    # normalized by the shared provenance contract.
    manifest = source_candidate_manifest(root)
    if plugin_tree_digest(installed, manifest) != manifest.plugin_digest(root):
        raise CandidateError("installed candidate payload digest differs from source")
except (CandidateError, OSError):
    raise SystemExit(1)
PY
}

write_isolated_candidate_receipt() {
  # The launcher never guesses an installed cache location.  Only the supported
  # isolated reconciliation path writes this receipt, after native installation
  # and exact source/candidate parity have both succeeded.
  [[ "${mode}" == "install" && "${isolated_reconcile_enabled}" == "1" ]] || return 0
  [[ -n "${candidate_version}" ]] || {
    echo "error: isolated candidate receipt requires a stamped candidate version" >&2
    return 1
  }
  validate_isolated_reconcile_target || return 1
  "${cortex_python}" -B "${script_dir}/cortex_candidate_receipt.py" write \
    --source-root "${project_dir}" \
    --owner-home "${isolated_owner_home}" \
    --isolated-home "${home_root}" \
    --isolated-codex-home "${codex_home}" \
    --candidate-version "${candidate_version}" >/dev/null || {
      echo "error: isolated candidate receipt was not committed; refusing to authorize Cortex launch" >&2
      return 1
    }
  echo "verified isolated Cortex candidate receipt"
}

capture_cortex_mcp_approval_override() {
  local config_path="${codex_home}/config.toml"
  cortex_mcp_approval_override=""
  validate_global_config_path || return 1
  [[ -e "${config_path}" || -L "${config_path}" ]] || return 0
  cortex_mcp_approval_override="$({
    "${cortex_python}" -B - "${config_path}" <<'PY'
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
  global_subagent_model_state="$("${cortex_python}" -B - "${config_path}" <<'PY'
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
    global_config_mode="$("${cortex_python}" -B - "${config_path}" <<'PY'
import stat, sys
from pathlib import Path
print(format(stat.S_IMODE(Path(sys.argv[1]).stat().st_mode), "o"))
PY
)" || return 1
  fi
}

capture_multi_agent_v2() {
  local config_path="${codex_home}/config.toml"
  multi_agent_v2_state="missing"
  validate_global_config_path || return 1
  [[ -e "${config_path}" || -L "${config_path}" ]] || return 0
  multi_agent_v2_state="$("${cortex_python}" -B - "${config_path}" <<'PY'
import sys
import tomllib
from pathlib import Path

path = Path(sys.argv[1])
try:
    payload = tomllib.loads(path.read_text(encoding="utf-8"))
except (OSError, UnicodeError, tomllib.TOMLDecodeError) as exc:
    raise SystemExit(f"error: cannot parse Codex config for features.multi_agent_v2: {exc}")
try:
    value = payload["features"]["multi_agent_v2"]
except (KeyError, TypeError):
    print("missing")
    raise SystemExit(0)
if not isinstance(value, bool):
    raise SystemExit("error: features.multi_agent_v2 must be a boolean")
print("true" if value else "false")
PY
)" || return 1
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

check_global_subagent_model() {
  capture_global_subagent_model || return 1
  reject_cache_collision || return 1
  if [[ "${global_subagent_model_state}" != "gpt-5.6-luna" ]]; then
    echo "outdated Codex global config: agents.default_subagent_model must be gpt-5.6-luna (found ${global_subagent_model_state})" >&2
    return 1
  fi
  echo "ok      agents.default_subagent_model=${global_subagent_model_state}"
}

check_multi_agent_v2() {
  capture_multi_agent_v2 || return 1
  if [[ "${multi_agent_v2_state}" != "true" ]]; then
    echo "outdated Codex global config: features.multi_agent_v2 must be true (found ${multi_agent_v2_state})" >&2
    return 1
  fi
  echo "ok      features.multi_agent_v2=true"
}

ensure_multi_agent_v2() {
  local config_path="${codex_home}/config.toml"
  capture_multi_agent_v2 || return 1
  if [[ "${multi_agent_v2_state}" == "true" ]]; then
    echo "ok      features.multi_agent_v2=true"
    return 0
  fi
  if [[ "${mode}" == "check" ]]; then
    echo "outdated Codex global config: features.multi_agent_v2 must be true (found ${multi_agent_v2_state})" >&2
    return 1
  fi
  if [[ "${mode}" == "dry-run" ]]; then
    echo "would set features.multi_agent_v2=true"
    return 0
  fi
  "${cortex_python}" -B - "${config_path}" <<'PY'
import os
import re
import stat
import sys
import tempfile
import tomllib
from pathlib import Path

path = Path(sys.argv[1])
if path.is_symlink() or (path.exists() and not path.is_file()):
    raise SystemExit(f"error: refusing to update non-regular Codex config: {path}")
original = path.read_text(encoding="utf-8") if path.exists() else ""
lines = original.splitlines(keepends=True)
header = "[features]"
headers = [index for index, line in enumerate(lines) if line.strip() == header]
if len(headers) > 1:
    raise SystemExit("error: Codex config contains duplicate [features] tables")
if not headers:
    text = original
    if text and not text.endswith(("\n", "\r")):
        text += "\n"
    if text and not text.endswith("\n\n"):
        text += "\n"
    text += f"{header}\nmulti_agent_v2 = true\n"
else:
    start = headers[0] + 1
    end = start
    table_header = re.compile(r"^\s*\[(?!\[).+\]\s*(?:#.*)?$")
    while end < len(lines) and not table_header.match(lines[end]):
        end += 1
    key_indexes = [
        index for index in range(start, end)
        if re.match(r"^\s*multi_agent_v2\s*=", lines[index])
    ]
    if len(key_indexes) > 1:
        raise SystemExit("error: Codex config contains duplicate features.multi_agent_v2 keys")
    if not key_indexes:
        lines.insert(start, "multi_agent_v2 = true\n")
    else:
        index = key_indexes[0]
        line = lines[index]
        newline = "\r\n" if line.endswith("\r\n") else "\n" if line.endswith("\n") else ""
        body = line[:-len(newline)] if newline else line
        prefix = re.match(r"^(\s*multi_agent_v2\s*=\s*)", body)
        if prefix is None:
            raise SystemExit("error: unable to locate features.multi_agent_v2 key")
        comment = ""
        if "#" in body[prefix.end():]:
            comment = " #" + body[prefix.end():].split("#", 1)[1].lstrip()
        lines[index] = f"{prefix.group(1)}true{comment}{newline}"
    text = "".join(lines)
try:
    parsed = tomllib.loads(text)
    observed = parsed["features"]["multi_agent_v2"]
except (KeyError, TypeError, UnicodeError, tomllib.TOMLDecodeError) as exc:
    raise SystemExit(f"error: updated Codex config is invalid: {exc}")
if observed is not True:
    raise SystemExit("error: updated features.multi_agent_v2 was not retained")
path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o600
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
  echo "configured features.multi_agent_v2=true"
}

ensure_global_subagent_model() {
  local config_path="${codex_home}/config.toml"
  local target_model="gpt-5.6-luna" previous_model
  capture_global_subagent_model || return 1
  previous_model="${original_global_subagent_model_state:-${global_subagent_model_state}}"
  if [[ "${previous_model}" == "${target_model}" ]]; then
    if [[ "${mode}" == "install" && -n "${original_global_config_mode}" ]]; then
      run chmod "${original_global_config_mode}" "${config_path}"
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
  "${cortex_python}" -B - "${config_path}" "${target_model}" "${original_global_config_mode:-${global_config_mode}}" <<'PY'
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
  "${cortex_python}" -B - "${config_path}" "${cortex_mcp_approval_override}" <<'PY'
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
path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
if path.is_symlink() or (path.exists() and not path.is_file()):
    raise SystemExit(f"error: refusing to update non-regular Codex config: {path}")

original = path.read_text(encoding="utf-8") if path.exists() else ""
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

mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o600
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

check_cortex_mcp_approval_mode() {
  capture_cortex_mcp_approval_override || return 1
  if [[ "${cortex_mcp_approval_override}" != "approve" ]]; then
    echo "outdated Codex global config: Cortex default_tools_approval_mode must be approve (found ${cortex_mcp_approval_override:-missing})" >&2
    return 1
  fi
  echo "ok      Cortex default_tools_approval_mode=approve"
}

ensure_cortex_mcp_approval_mode() {
  local config_path="${codex_home}/config.toml" captured="${cortex_mcp_approval_override}"
  if [[ "${mode}" == "dry-run" ]]; then
    echo "would set Cortex MCP default_tools_approval_mode=approve"
    return 0
  fi
  cortex_mcp_approval_override="approve"
  restore_cortex_mcp_approval_override || {
    cortex_mcp_approval_override="${captured}"
    return 1
  }
  cortex_mcp_approval_override="${captured}"
  echo "configured Cortex MCP default_tools_approval_mode=approve"
}

installed_version() {
  codex plugin list --json 2>/dev/null | "${cortex_python}" -B -c 'import json,sys; value=json.load(sys.stdin); rows=value.get("installed", value) if isinstance(value, dict) else value; print(next((row.get("version", "") for row in rows if row.get("pluginId") == "cortex@cortex"), ""))' 2>/dev/null || true
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
    check_cortex_mcp_approval_mode || return 1
    check_multi_agent_v2 || return 1
    check_global_subagent_model || return 1
    echo "ok      ${plugin_name}@${marketplace_name} (${expected_version}, content verified)"; return 0
  fi
  capture_cortex_mcp_approval_override || return 1
  capture_global_subagent_model || return 1
  original_global_subagent_model_state="${global_subagent_model_state}"
  original_global_config_mode="${global_config_mode}"
  backup_global_config_for_update || return 1
  if ! reconcile_isolated_marketplace; then
    restore_cortex_mcp_approval_override || true
    return 1
  fi
  # A matching content-addressed cache path is immutable: keep it and avoid
  # remove/reinstall churn.  Only an absent or different installed version
  # may go through the native replacement flow.
  if [[ "${version}" == "${expected_version}" ]]; then
    reject_cache_collision || {
      restore_cortex_mcp_approval_override || true
      return 1
    }
  else
    if [[ -n "${version}" ]] && ! run codex plugin remove "${plugin_name}@${marketplace_name}" --json >/dev/null; then
      restore_cortex_mcp_approval_override || true
      return 1
    fi
    if ! run codex plugin add "${plugin_name}@${marketplace_name}" --json >/dev/null; then
      restore_cortex_mcp_approval_override || true
      return 1
    fi
  fi
  ensure_global_subagent_model || return 1
  ensure_multi_agent_v2 || return 1
  ensure_cortex_mcp_approval_mode || return 1
  [[ "${mode}" == "dry-run" ]] || content_matches || { echo "error: installed plugin content differs from source" >&2; return 1; }
  write_isolated_candidate_receipt || return 1
  if [[ "${mode}" == "dry-run" ]]; then
    echo "would install ${plugin_name}@${marketplace_name} from ${marketplace_root}"
  else
    echo "installed ${plugin_name}@${marketplace_name} from ${marketplace_root}"
  fi
}

resolve_cortex_python
validate_roots
validate_global_config_path
clean_plugin_bytecode
sync_model_routing_catalog
validate_sources
prepare_candidate
status=0
install_or_check || status=1
[[ "${status}" -eq 0 ]] || exit "${status}"
if [[ "${mode}" == "check" ]]; then
  echo "Cortex is up to date."
elif [[ "${mode}" == "dry-run" ]]; then
  echo "Cortex dry run complete. No plugin or Codex configuration was changed."
else
  echo "Cortex installed from this repository. Start a new Codex thread before using the updated plugin."
fi
