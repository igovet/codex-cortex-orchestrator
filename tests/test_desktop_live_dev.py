from __future__ import annotations

import importlib.machinery
import importlib.util
from pathlib import Path
import stat


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "cortex-desktop-dev"


def module():
    loader = importlib.machinery.SourceFileLoader("cortex_desktop_dev", str(SCRIPT))
    spec = importlib.util.spec_from_loader("cortex_desktop_dev", loader)
    assert spec and spec.loader
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def test_desktop_launcher_is_executable_and_uses_the_shared_candidate() -> None:
    mode = SCRIPT.stat().st_mode
    assert stat.S_ISREG(mode)
    assert mode & stat.S_IXUSR
    source = SCRIPT.read_text(encoding="utf-8")
    for required in (
        '"--prepare-only"',
        '"CODEX_HOME"',
        '"CODEX_ELECTRON_USER_DATA_PATH"',
        'start_new_session=True',
        '"CORTEX_CANDIDATE_RECEIPT"',
    ):
        assert required in source


def test_desktop_model_projection_only_copies_safe_scalars(tmp_path: Path) -> None:
    driver = module()
    owner = tmp_path / "owner"
    codex_home = tmp_path / "candidate" / ".codex"
    (owner / ".codex").mkdir(parents=True)
    codex_home.mkdir(parents=True)
    (owner / ".codex" / "config.toml").write_text(
        'model = "gpt-5.6-sol"\n'
        'model_reasoning_effort = "xhigh"\n'
        'personality = "pragmatic"\n'
        '[mcp_servers.secret]\ncommand = "must-not-copy"\n',
        encoding="utf-8",
    )
    (codex_home / "config.toml").write_text(
        '[features]\nmulti_agent_v2 = true\n', encoding="utf-8"
    )

    driver._project_desktop_model_config(
        owner,
        codex_home,
        model="gpt-5.6-luna",
        reasoning_effort="high",
    )

    rendered = (codex_home / "config.toml").read_text(encoding="utf-8")
    assert 'model = "gpt-5.6-luna"' in rendered
    assert 'model_reasoning_effort = "high"' in rendered
    assert 'personality = "pragmatic"' in rendered
    assert "must-not-copy" not in rendered
    assert "multi_agent_v2 = true" in rendered
    assert stat.S_IMODE((codex_home / "config.toml").stat().st_mode) == 0o600


def test_docs_require_consecutive_cli_desktop_parity_on_one_stamp() -> None:
    docs = "\n".join(
        (ROOT / path).read_text(encoding="utf-8")
        for path in (
            "AGENTS.md",
            "README.md",
            "SECURITY.md",
            "docs/project/verification.md",
            "docs/release-readiness.md",
        )
    )
    assert "./scripts/cortex-desktop-dev" in docs
    assert "same cache-stamped payload" in docs or "same stamped payload" in docs
    assert "invalidates both" in docs
