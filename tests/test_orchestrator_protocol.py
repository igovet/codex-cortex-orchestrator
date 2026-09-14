"""Guard the coordinator's fresh-activation bootstrap ordering contract."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_fresh_activation_binds_task_before_governance():
    text = (ROOT / "plugins/cortex/skills/orchestrator/SKILL.md").read_text()

    start = text.index("## Apply governance to decisions")
    end = text.index("\n## Durable decisions", start)
    section = text[start:end]

    assert "fresh activation" in section
    assert "call `create_task`" in section
    assert "successful task binding" in section
    assert "Never call\n`set_governance` before successful `create_task`/binding" in section
    assert section.index("`create_task`") < section.index("`set_governance`")
    assert "not a server gate or approval machine" in section


def test_harvest_remains_explicit_only_in_orchestrator_contract():
    text = (ROOT / "plugins/cortex/skills/orchestrator/SKILL.md").read_text()
    assert "Keep `cortex:knowledge-harvest` explicit-only" in text
