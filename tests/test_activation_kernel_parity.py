from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_activation_kernels_define_task_ref_only_llm_owned_orchestration() -> None:
    orchestrator = (ROOT / "plugins/cortex/skills/orchestrator/SKILL.md").read_text(encoding="utf-8").lower()
    control = (ROOT / "plugins/cortex/skills/cortex-control/SKILL.md").read_text(encoding="utf-8").lower()
    combined = orchestrator + control
    assert "first execution operation is `open_task`" in orchestrator
    assert "stores only `task_ref`" in orchestrator
    assert "dynamic dag" in combined
    assert "never schedules" in combined
    assert "no workflow or governance admission" in combined
    assert "assignment view" in combined
    assert "flat, closed, and operation-specific" in orchestrator
    assert "before the first assignment" in orchestrator
    assert "advisory governance assessment" in orchestrator
    assert "authentication, authorization, security, privacy" in orchestrator
    assert "light or full governance requires a planner-owned immutable plan" in orchestrator
    assert "backend rejects a delivery assignment" in orchestrator
    assert "assessment must exist before the first assignment" in orchestrator
    assert "record that response before creating plan-dependent delivery assignments" in orchestrator
    assert "never infer approval from the original implementation request" in orchestrator
    assert "exact numeric limits" in orchestrator
    assert '"from the attachment" never substitutes' in orchestrator
    assert "source-to-contract coverage check" in orchestrator
    assert "exact value and meaning" in orchestrator
    assert "silently omit it" in orchestrator
    assert "complete effective contract" in orchestrator
    planner = (ROOT / "plugins/cortex/agents/planner.toml").read_text(encoding="utf-8").lower()
    assert "requirement-coverage reconciliation" in planner
    assert "paraphrased-with-loss" in planner
    assert "do not silently fill it" in planner
    for forbidden in ("consume_assignment_evidence", "next_action", "suggested_", "post-anchor-engine.md"):
        assert forbidden not in combined


def test_preload_metadata_blocks_unanchored_user_questions() -> None:
    orchestrator = (ROOT / "plugins/cortex/skills/orchestrator/SKILL.md").read_text(encoding="utf-8").lower()
    description = orchestrator.split("---", 2)[1]
    assert "read this skill completely before task-specific commentary, questions, plans, or results" in description
    assert "first project operation is open_task" in description
    assert "no user question may be rendered before" in orchestrator
