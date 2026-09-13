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
