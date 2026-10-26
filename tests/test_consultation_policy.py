from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_consultation_is_conditionally_loaded_and_preserves_report_only_scope():
    root = ROOT / "plugins/cortex/skills/orchestrator"
    entry = (root / "SKILL.md").read_text()
    policy = (root / "references/senior-consultation.md").read_text()
    assert '(references/senior-consultation.md)' in entry
    assert 'concrete unresolved uncertainty' in policy
    assert 'selected published reports' in policy
    assert 'does not transfer' in policy
    assert 'repeat consultation only for changed evidence' in policy


def test_documented_overlay_has_negative_controls_and_no_runtime_gate():
    text = (ROOT / "docs/project/quality-evaluation.md").read_text()
    section = text.split("## Risk-triggered consultation overlay", 1)[1]
    assert "three transition families" in section
    assert "negative controls" in section
    assert "unavailable values\nremain null" in section
    assert "never a runtime gate, server decision or acceptance rule" in section


def test_advisory_lane_is_catalogue_optional_and_silent_when_unavailable():
    root = ROOT / "plugins/cortex/skills/orchestrator"
    entry = (root / "SKILL.md").read_text()
    policy = (root / "references/consultation-routing.md").read_text()
    assert '(references/consultation-routing.md)' in entry
    assert "only from the tools exposed in the current live host\ncatalogue" in policy
    assert "explicit\nmcp-rubber-duck provider/server identity" in policy
    assert "the tool name is exactly `ask_duck`" in policy
    assert "An unrelated\n`ask_duck`-like tool" in policy
    assert "does\nnot qualify" in policy
    assert "do not execute Advisory Lane logic at all" in policy
    assert "not a warning, blocker, degraded" in policy
    assert "pipeline mutation" in policy
    assert "Cortex behavior and" in policy


def test_advisory_lane_contract_is_non_owning_and_bounded():
    policy = (ROOT / "plugins/cortex/skills/orchestrator/references/consultation-routing.md").read_text()
    assert "not a pipeline stage, worker profile,\nruntime dependency" in policy
    assert "one bounded call with one compact decision packet" in policy
    assert "no councils, debates, multi-model\ncomparison, voting" in policy
    assert "inspect the live server/tool declaration" in policy
    assert "configured provider/model appropriate" in policy
    assert "Missing, malformed, or ambiguous provider/model metadata" in policy
    assert "package names, static configuration, cached assumptions" in policy
    assert "recursive worker route" in policy
    assert "never owns the incident or task" in policy
    assert "Escalate to a real bounded worker" in policy
    assert "generic host-equivalent name" in policy
