from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REQUIRED_FEATURES = (
    "knowledge routing",
    "worker-only",
    "delegation",
    "model routing",
    "clarification",
    "approval",
    "steering",
    "evidence",
    "verification",
    "rework",
    "documentation",
    "governance",
    "adaptation",
    "compaction",
    "recovery",
    "progress",
    "content safety",
    "closure",
)


def test_activation_kernels_are_flattened_and_ordered() -> None:
    orchestrator = (ROOT / "plugins/cortex/skills/orchestrator/SKILL.md").read_text(encoding="utf-8").lower()
    control = (ROOT / "plugins/cortex/skills/cortex-control/SKILL.md").read_text(encoding="utf-8").lower()
    orchestrator_flat = " ".join(orchestrator.split())
    control_flat = " ".join(control.split())
    # The selected skill is complete on first load; it must not instruct the
    # coordinator to perform a second shell/file read of its companion text.
    assert "complete engine" in orchestrator
    assert "do not perform a second file or shell read" in " ".join(control.split())
    assert "post-anchor-engine.md" not in orchestrator
    assert "post-anchor-engine.md" not in control
    # The flattened coordinator states the boundary in its route invariant;
    # the companion states the same boundary in its pre-anchor protocol.  Do
    # not depend on the retired reference-loader wording or line counts.
    assert "first project execution action is the catalogued `open_task` operation" in orchestrator_flat
    assert "first project execution action is exactly one" in control_flat
    assert "before the anchor" in control


def test_preload_metadata_blocks_unanchored_user_questions() -> None:
    """The advertised skill summary governs the model before body loading."""
    orchestrator = (ROOT / "plugins/cortex/skills/orchestrator/SKILL.md").read_text(encoding="utf-8").lower()
    communication = (ROOT / "plugins/cortex/skills/coordinator-communication/SKILL.md").read_text(encoding="utf-8").lower()
    orchestrator_description = orchestrator.split("---", 2)[1]
    communication_description = communication.split("---", 2)[1]
    assert "read this skill completely before any task-specific commentary, question, plan, or result" in orchestrator_description
    assert "first project operation is open_task" in orchestrator_description
    assert "no user question may be rendered until the matching durable clarification hold succeeds" in orchestrator_description
    assert "never preview a pending question in commentary" in communication_description


def test_flattened_orchestrator_preserves_capability_inventory() -> None:
    text = (ROOT / "plugins/cortex/skills/orchestrator/SKILL.md").read_text(encoding="utf-8").lower()
    for feature in REQUIRED_FEATURES:
        assert feature in text, feature
    anchor = text.index("## route execution invariant")
    for marker in ("## plan and clarification holds", "## per-delegation model selection", "## closure confirmation and final answer"):
        assert anchor < text.index(marker)
    # The ledger capability is intentionally documented before the route
    # boundary; it remains part of the flattened inventory and is not a
    # second runtime reference.
    assert "## healthy dispatch and degraded ledger" in text


def test_post_anchor_references_preserve_feature_inventory() -> None:
    for name in ("orchestrator", "cortex-control"):
        text = (ROOT / "plugins/cortex/skills" / name / "references/post-anchor-engine.md").read_text(encoding="utf-8").lower()
        missing = [feature for feature in REQUIRED_FEATURES if feature not in text]
        assert not missing, f"{name} reference lost feature terms: {missing}"
