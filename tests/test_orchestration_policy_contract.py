"""Focused semantic checks for conditional analysis and worker boundaries.

These tests intentionally assert policy outcomes rather than MCP request shapes.
The live advertised catalogue remains the only argument-contract authority.
"""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ORCHESTRATOR = ROOT / "plugins" / "cortex" / "skills" / "orchestrator" / "SKILL.md"
ADAPTIVE = ROOT / "plugins" / "cortex" / "skills" / "adaptive-pipeline" / "SKILL.md"
CONTROL = ROOT / "plugins" / "cortex" / "skills" / "cortex-control" / "SKILL.md"
PLANNER = ROOT / "plugins" / "cortex" / "agents" / "planner.toml"


def _text(path: Path) -> str:
    return " ".join(path.read_text(encoding="utf-8").split())


def test_pre_planner_analysis_is_conditional_luna_evidence_work():
    orchestrator = _text(ORCHESTRATOR)
    adaptive = _text(ADAPTIVE)

    for text in (orchestrator, adaptive):
        assert "Pre-planner analysis is" in text and "conditional" in text
        assert "read-only" in text
        assert "Luna" in text
        assert "entire currently advertised range" in text
        assert "highest available effort" in text
        assert "distinct" in text


def test_evidence_queue_has_slot_limited_concurrency_without_a_total_cap():
    orchestrator = _text(ORCHESTRATOR)
    adaptive = _text(ADAPTIVE)

    assert "There is no finite total cap on justified evidence assignments" in orchestrator
    assert "currently available native-agent slots" in orchestrator
    assert "dispatch the next queued assignment when a slot becomes free" in orchestrator
    assert "expected incremental value no longer justifies latency and cost" in orchestrator
    assert "Never fan out overlapping prompts merely to occupy slots" in orchestrator

    assert "Do not impose a finite total evidence-assignment cap" in adaptive
    assert "native slots currently available" in adaptive
    assert "dispatch the next non-overlapping ready assignment when a slot frees" in adaptive
    assert "incremental value no longer justifies latency and cost" in adaptive


def test_planner_remains_the_only_plan_owner_and_publishes_once():
    orchestrator = _text(ORCHESTRATOR)
    adaptive = _text(ADAPTIVE)
    planner = _text(PLANNER)

    assert "The planner remains the sole owner of the project solution plan" in orchestrator
    assert "Evidence workers never publish or revise the plan" in orchestrator
    assert "remains the sole owner of one terminal project plan" in adaptive
    assert "one terminal plan publication" in planner
    assert "never publish a supplementary result" in planner


def test_governance_is_root_coordinator_only_across_replanning_paths():
    orchestrator = _text(ORCHESTRATOR)
    adaptive = _text(ADAPTIVE)
    control = _text(CONTROL)
    planner = _text(PLANNER)

    assert "Only the root coordinator owns this operation" in orchestrator
    assert "No native worker or packaged profile may assess governance" in orchestrator
    assert "worker completion, repeated planning, or plan revision alone is not" in orchestrator.lower()
    assert "Governance assessment belongs only to the root coordinator" in adaptive
    assert "no native worker, profile, planner revision, replacement, or rework path" in adaptive
    assert "Every native worker and packaged profile is prohibited" in control
    assert "never read the assignment again or invoke coordinator-only governance" in planner


def test_first_read_recovery_and_terminal_transition_are_bounded():
    orchestrator = _text(ORCHESTRATOR)
    control = _text(CONTROL)

    for text in (orchestrator, control):
        assert "one materially corrected attempt" in text.lower()
        assert "unchanged malformed request" in text
        assert "guess" in text
        assert "before successful consumption" in text or "before consumption succeeds" in text
        assert "ambiguous transport" in text.lower()
        assert "immediately preceding otherwise-identical" in text
        assert "terminal" in text
        assert "partial or blocked publication" in text


def test_assignment_selection_uses_current_outcomes_and_one_bounded_recovery():
    orchestrator = _text(ORCHESTRATOR)

    assert "Immediately before every assignment, read current task state" in orchestrator
    assert "complete exact unique semantic outcome names" in orchestrator
    assert "Do not reuse a pre-steering snapshot" in orchestrator
    assert "at most one fresh-state read and one rebuilt assignment" in orchestrator
    assert "Never retry the unchanged request or reconstruct a retired outcome" in orchestrator
    assert "spawns exactly once and immediately" in orchestrator
    assert "never creates a duplicate worker" in orchestrator


def test_added_policy_does_not_embed_mcp_payload_contracts():
    orchestrator = ORCHESTRATOR.read_text(encoding="utf-8")
    start = orchestrator.index("Pre-planner analysis is conditional")
    end = orchestrator.index("Every assignment must be scoped", start)
    added_analysis_policy = orchestrator[start:end]

    forbidden_shape_tokens = (
        "reasoning_effort",
        "report_policy",
        "profile_name",
        "task_ref",
        "{\"",
        "required properties",
        "input schema",
    )
    for token in forbidden_shape_tokens:
        assert token not in added_analysis_policy
