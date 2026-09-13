from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _policy_text():
    text = (ROOT / "plugins/cortex/skills/orchestrator/SKILL.md").read_text()
    return text.split("### Optional consequential-transition consultation", 1)[1].split(
        "\nUse one suitable worker", 1
    )[0]


def test_consultation_requires_both_triggers_and_remains_optional():
    policy = _policy_text()
    assert "both consequence and uncertainty\napply; otherwise proceed" in policy
    assert "concurrent when safe" in policy
    assert "never a block" in policy
    assert "transfer of planning/acceptance" in policy
    assert "must consult" not in policy
    assert "consultation gate" not in policy


def test_consultation_policy_covers_exclusions_packet_record_and_evaluation():
    policy = _policy_text()
    for phrase in (
        "routine work, ordinary tests",
        "unchanged packets",
        "active incidents",
        "Record a compact\npacket",
        "decision change, quality and overhead",
    ):
        assert phrase in policy


def test_documented_overlay_has_negative_controls_and_no_runtime_gate():
    text = (ROOT / "docs/project/quality-evaluation.md").read_text()
    section = text.split("## Risk-triggered consultation overlay", 1)[1]
    assert "three transition families" in section
    assert "negative controls" in section
    assert "unavailable values\nremain null" in section
    assert "never a runtime gate, server decision or acceptance rule" in section
