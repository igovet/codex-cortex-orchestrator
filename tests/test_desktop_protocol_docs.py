"""Keep the documented Desktop qualification protocol executable and explicit."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_agents_desktop_protocol_binds_expected_result_before_plain_send():
    text = (ROOT / "AGENTS.md").read_text()

    start = text.index("Real Desktop uses `scripts/cortex-desktop-dev`")
    protocol = text[start:]

    assert "--prompt-file" in protocol
    assert "--expected-result-path README.md" in protocol
    assert "--expected-result-sha256 EXACT_RESULT_SHA256" in protocol
    assert "Before launch" in protocol
    assert "plain `send` with no `--prompt-file`" in protocol
    assert "final\nworker report" in protocol
    assert "report/public-evidence reconciliation" in protocol
    assert "audit exit code" in protocol
    assert "run plain `stop`" in protocol
    assert protocol.index("--expected-result-path") < protocol.index("plain `send`")
    assert protocol.index("report/public-evidence reconciliation") < protocol.index("running `audit`")
    assert protocol.index("audit exit code") < protocol.index("run plain `stop`")


def test_readme_desktop_example_binds_project_root_artifact_before_plain_send():
    text = (ROOT / "README.md").read_text()
    start = text.index("./scripts/cortex-desktop-dev start --workdir")
    end = text.index("./scripts/cortex-desktop-dev stop", start)
    protocol = text[start:end]

    assert "--expected-result-path README.md" in protocol
    assert "--expected-result-sha256 EXACT_RESULT_SHA256" in protocol
    assert protocol.count("--expected-result-path") == 1
    assert protocol.index("--expected-result-path README.md") < protocol.index("./scripts/cortex-desktop-dev send")
    assert protocol.index("--expected-result-sha256 EXACT_RESULT_SHA256") < protocol.index("./scripts/cortex-desktop-dev send")
    assert "send --prompt-file" not in protocol
    assert "./scripts/cortex-desktop-dev send\n" in protocol
