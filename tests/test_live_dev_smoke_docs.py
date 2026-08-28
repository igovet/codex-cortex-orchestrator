from pathlib import Path


def test_live_dev_smoke_guidance_has_tui_capture_fallback() -> None:
    repository = Path(__file__).resolve().parents[1]
    guidance = (repository / "AGENTS.md").read_text(encoding="utf-8")

    assert "output-only capture fallback" in guidance
    assert "pipe-pane" in guidance
    assert "configured Cortex MCP default_tools_approval_mode=approve" in guidance
    assert "tail -c 20000" in guidance
    assert "The coordinator owns the three phases" in guidance
    assert "launch and return" in guidance
    assert "later action (do not execute" in guidance
    assert "give it to the coordinator/LLM for interpretation" in guidance
    assert "launcher-side" in guidance
    assert "not a shell pipe into Codex" in guidance
    assert "rm -f -- \"${capture_path}\"" in guidance
    assert 'capture-pane -p -t "=$session_name:0.0"' in guidance
    assert "Unchanged output is not completion" in guidance
    assert "coordinator/LLM" in guidance
    assert "current user's default" in guidance
    assert "tmux_cmd=(tmux -f /dev/null)" in guidance
    assert "tmux ls" in guidance
    assert "do not silently switch to an independent" in guidance
    assert "Do not kill the user's default tmux server" in guidance
    assert '"${tmux_cmd[@]}" kill-server' not in guidance
    assert "env -u TMUX" not in guidance
    assert "-L \"$socket_name\"" not in guidance


def test_cortex_dev_still_starts_ordinary_codex() -> None:
    repository = Path(__file__).resolve().parents[1]
    launcher = (repository / "scripts" / "cortex-dev").read_text(encoding="utf-8")
    root_launcher = (repository / "cortex-dev").read_text(encoding="utf-8")

    assert 'exec codex "$@"' in launcher
    assert "codex exec" not in launcher
    assert 'exec "${script_dir}/scripts/cortex-dev" "$@"' in root_launcher
