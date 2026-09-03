from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_activation_kernels_define_task_ref_only_llm_owned_orchestration() -> None:
    orchestrator = (ROOT / "plugins/cortex/skills/orchestrator/SKILL.md").read_text(encoding="utf-8").lower()
    control = (ROOT / "plugins/cortex/skills/cortex-control/SKILL.md").read_text(encoding="utf-8").lower()
    combined = orchestrator + control
    assert "first execution operation is `open_task`" in orchestrator
    assert "exactly one complete direct cortex call" in orchestrator
    assert "never place task opening inside programmatic tool calling" in orchestrator
    assert "invoke every cortex operation as its own direct tool call" in control
    assert "this restriction does not apply to non-cortex tools" in control
    assert "stores only `task_ref`" in orchestrator
    assert "dynamic dag" in combined
    assert "never schedules" in combined
    assert "no workflow or governance admission" in combined
    assert "assignment view" in combined
    assert "flat, closed, and operation-specific" in orchestrator
    assert "before the first assignment" in orchestrator
    assert "advisory governance assessment" in orchestrator
    assert "authentication, authorization, security, privacy" in orchestrator
    assert "use governance depth to choose proportional discovery" in orchestrator
    assert "language-independent review boundary" in orchestrator
    assert "planner cannot self-attest or downgrade" in orchestrator
    assert "light/full work, incomplete evidence" in orchestrator
    assert "continue authoritatively derived informational plans immediately" in orchestrator
    assert "assessment must exist before the first assignment" in orchestrator
    assert "record that response before creating plan-dependent delivery assignments" in orchestrator
    assert "never infer approval from the original implementation request" in orchestrator
    assert "exact numeric limits" in orchestrator
    assert '"from the attachment" never substitutes' in orchestrator
    assert "source-to-contract coverage check" in orchestrator
    assert "exact value and meaning" in orchestrator
    assert "silently omit it" in orchestrator
    assert "complete effective contract" in orchestrator
    assert "never interrupt or cancel a child merely because bounded waits repeated" in orchestrator
    assert "never open steering merely to re-authorize unfinished work" in orchestrator
    assert "not by itself a scope change" in orchestrator
    planner = (ROOT / "plugins/cortex/agents/planner.toml").read_text(encoding="utf-8").lower()
    assert "requirement-coverage reconciliation" in planner
    assert "paraphrased-with-loss" in planner
    assert "do not silently fill it" in planner
    for forbidden in ("consume_assignment_evidence", "next_action", "suggested_", "post-anchor-engine.md"):
        assert forbidden not in combined


def test_preload_metadata_blocks_unanchored_user_questions() -> None:
    orchestrator = (ROOT / "plugins/cortex/skills/orchestrator/SKILL.md").read_text(encoding="utf-8").lower()
    description = orchestrator.split("---", 2)[1]
    assert "read this skill completely before task-specific output" in description
    assert "after compaction or reset, accept its complete exact repeat" in description
    assert "without requesting user approval" in description
    assert "first task-specific output or action must be open_task" in description
    assert "no activation acknowledgement, commentary, question, plan, or result before its success" in description
    assert "no user question may be rendered before" in orchestrator


def test_compaction_reload_is_available_without_an_approval_prompt() -> None:
    orchestrator = (ROOT / "plugins/cortex/skills/orchestrator/SKILL.md").read_text(encoding="utf-8").lower()
    recovery = (ROOT / "plugins/cortex/skills/context-compaction/SKILL.md").read_text(encoding="utf-8").lower()
    normalized_recovery = " ".join(recovery.split())
    combined = orchestrator + recovery
    for required in (
        "host skill loader",
        "sessionstart(source=compact)",
        "repeated loading remains permitted",
        "user approval question",
        "stop safely if exact host reload is unavailable",
    ):
        assert required in combined
    assert "`cat`" in combined
    assert "additionalcontextlimit=0" in combined
    for required in (
        "obtained before compaction as unavailable",
        "first post-compaction cortex action is a fresh current-state read",
        "never from the summary",
        "worker's first post-compaction cortex action restarts its assignment view",
        "sole recovery exception",
        "fresh server-owned reconciliation projection",
        "without granting new authority",
    ):
        assert required in normalized_recovery
    assert "a state result obtained before compaction is never current input" in orchestrator
