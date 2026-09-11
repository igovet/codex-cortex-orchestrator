from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _policy_text():
    text = (ROOT / "plugins/cortex/skills/orchestrator/SKILL.md").read_text()
    return text.split("### Optional consequential-transition consultation", 1)[1].split(
        "\nUse one suitable worker", 1
    )[0]


def test_consultation_requires_both_triggers_and_remains_optional():
    policy = _policy_text()
    assert "both a\nconsequence trigger and an uncertainty trigger" in policy
    assert "may run concurrently when safe" in policy
    assert "never blocks by rule" in policy
    assert "Otherwise proceed without consulting" in policy
    assert "never transfers planning, steering, evidence interpretation, acceptance" in policy
    assert "must consult" not in policy
    assert "consultation gate" not in policy


def test_consultation_policy_covers_exclusions_packet_record_and_evaluation():
    policy = _policy_text()
    for phrase in (
        "routine, reversible or deterministic work",
        "unchanged packets",
        "active incident recovery",
        "one compact packet",
        "never copied report bodies or private data",
        "whether advice changed",
        "Evaluate quality and overhead against the unchanged baseline",
    ):
        assert phrase in policy


def test_documented_overlay_has_negative_controls_and_no_runtime_gate():
    text = (ROOT / "docs/project/quality-evaluation.md").read_text()
    section = text.split("## Risk-triggered consultation overlay", 1)[1]
    assert "three transition families" in section
    assert "negative controls" in section
    assert "unavailable values\nremain null" in section
    assert "never a runtime gate, server decision or acceptance rule" in section
