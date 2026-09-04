"""Passive launch recording is source evidence, not native qualification."""
import importlib.util
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]


def module():
    sys.path.insert(0, str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location("qualification_fixture", ROOT / "scripts/cortex_host_qualification.py")
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


@pytest.mark.parametrize("host", ["cli", "desktop"])
def test_snapshot_bound_to_receipt_without_secret_config(tmp_path, monkeypatch, host):
    helper = module()
    monkeypatch.setattr(helper, "cli_version", lambda: "fixture-cli-version")
    home = tmp_path / ".cortex-dev/.codex"
    home.mkdir(parents=True)
    (home / "config.toml").write_text('[agents]\ndefault_subagent_model="gpt-5.6-luna"\nmax_threads=5\n[mcp_servers.secret]\ntoken="fixture-must-not-export"\n')
    candidate = tmp_path / "candidate"
    (candidate / "hooks").mkdir(parents=True)
    (candidate / "hooks/hooks.json").write_text(json.dumps({"hooks": {"UserPromptSubmit": [], "SessionStart": []}}))
    monkeypatch.setattr(helper, "read_verified_receipt", lambda **kwargs: {
        "candidate_digest": "verified-fixture-digest", "candidate_path": str(candidate)})
    first = helper.capture_launch(tmp_path, host)
    second = helper.capture_launch(tmp_path, host)
    assert first != second
    text = first.read_text()
    assert "fixture-must-not-export" not in text
    value = json.loads(text)["snapshot"]
    assert value["identity"]["payload_digest"] == "verified-fixture-digest"
    assert value["identity"]["host"] == host
    assert value["identity"]["app_version"] == ("fixture-cli-version" if host == "cli" else "unverified")
    assert value["tools"] == []
    capabilities = dict(value["capabilities"])
    assert capabilities["models.default"]["state"] == "configured"
    assert capabilities["capacity.configured"]["state"] == "configured"
    assert capabilities["capacity.available"]["state"] == "unverified"
    assert capabilities["input.direct_user_origin"]["state"] == "unverified"
    assert capabilities["hooks.events"]["state"] == "declared"
    assert json.loads(capabilities["hooks.events"]["value_json"]) == ["SessionStart", "UserPromptSubmit"]
    assert capabilities["hooks.pre_tool_mcp"]["state"] == "unverified"


def test_failed_receipt_prevents_capture(tmp_path, monkeypatch):
    helper = module()
    def reject(**kwargs):
        raise ValueError("candidate mismatch")
    monkeypatch.setattr(helper, "read_verified_receipt", reject)
    with pytest.raises(ValueError, match="mismatch"):
        helper.capture_launch(tmp_path, "cli")
    assert not list(tmp_path.rglob("capabilities.json"))


def test_launchers_capture_after_candidate_preparation_before_native_start():
    cli = (ROOT / "scripts/cortex-dev").read_text()
    desktop = (ROOT / "scripts/cortex-desktop-dev").read_text()
    assert cli.index("cortex_host_qualification.py") < cli.index('exec codex "$@"')
    assert cli.index("if [[ \"${prepare_only}\" == \"1\" ]]") < cli.index("cortex_host_qualification.py")
    assert desktop.index('capture_launch(owner_home, "desktop")') < desktop.index("process = subprocess.Popen(")
