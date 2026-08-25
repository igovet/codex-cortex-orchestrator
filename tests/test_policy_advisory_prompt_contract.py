"""Parity checks for advisory Cortex prompts and public tool descriptions."""

import json
import re
from pathlib import Path


ROOT = Path(__file__).parents[1]
SKILL_PATHS = (
    ROOT / "plugins/cortex/skills/adaptive-pipeline/SKILL.md",
    ROOT / "plugins/cortex/skills/cortex-control/SKILL.md",
    ROOT / "plugins/cortex/skills/orchestrator/SKILL.md",
)


def test_bundled_prompts_do_not_impose_planner_first_or_docs_blocker():
    text = "\n".join(path.read_text(encoding="utf-8") for path in SKILL_PATHS)
    forbidden = (
        r"planner-first",
        r"return a blocker",
        r"unbounded documentation rework until",
        r"must begin with one planner",
        r"replacement must be a singleton planner",
    )
    for pattern in forbidden:
        assert not re.search(pattern, text, re.IGNORECASE), pattern


def test_route_and_visible_output_corrections_are_machine_or_task_scoped():
    orchestrator = (ROOT / "plugins/cortex/skills/orchestrator/SKILL.md").read_text(encoding="utf-8")
    orchestrator_lower = " ".join(orchestrator.lower().split())
    assert "machine-readable route validation diagnostic" in orchestrator_lower
    assert 'path: "route"' in orchestrator_lower
    assert '["empty", "help", "harvest", "harvest-refresh", "normal"]' in orchestrator_lower
    assert "ask the user to choose" not in orchestrator_lower
    assert "ask the user to make an orchestration recovery decision" in orchestrator_lower
    assert "task question" in orchestrator_lower

    control = (ROOT / "plugins/cortex/skills/cortex-control/SKILL.md").read_text(encoding="utf-8")
    control_lower = " ".join(control.lower().split())
    assert "task-relevant question or explicit safety/authorization boundary" in control_lower
    assert "or a blocking error" not in control_lower


def test_knowledge_harvest_failures_are_findings_for_corrective_owners():
    paths = (
        ROOT / "plugins/cortex/skills/knowledge-harvest/SKILL.md",
        ROOT / "plugins/cortex/skills/knowledge-harvest/references/feature-census.md",
    )
    text = "\n".join(path.read_text(encoding="utf-8") for path in paths)
    assert "Corrective findings" in text
    assert "return a blocker" not in text.lower()
    assert "explicit non-retryable blocker is recorded" not in text.lower()


def test_knowledge_harvest_planner_route_is_advisory_and_orchestrator_owned():
    harvest = (ROOT / "plugins/cortex/skills/knowledge-harvest/SKILL.md").read_text(encoding="utf-8")
    normalized = " ".join(harvest.lower().split())
    assert "## required pipeline" in harvest.lower()
    assert "no planner phase is mandatory" in normalized
    assert "planner's `planning` artifact is advisory input" in normalized
    assert "the final planner" not in normalized
    assert "dispatch the final read-only planner" not in normalized


def test_profile_contract_marks_harvest_gaps_as_findings_not_policy_vetoes():
    profiles = json.loads((ROOT / "plugins/cortex/profiles.json").read_text(encoding="utf-8"))
    harvest = profiles["mode_overlays"]["harvest"]
    assert "blocking finding" not in harvest["code_reviewer"].lower()
    assert "concrete blockers" not in profiles["shared_worker_contract"]["worker_result_unresolved_semantics"].lower()


def test_public_schema_describes_chosen_recovery_and_caller_selected_pages():
    import sys

    sys.path.insert(0, str(ROOT / "plugins/cortex/scripts"))
    import cortex  # initialize the public schema registry
    from cortex_runtime import mcp_api

    descriptions = mcp_api.PUBLIC_TOOL_DESCRIPTIONS
    management = descriptions["manage_orchestration"].lower()
    worker_question = descriptions["worker_question"].lower()
    assert "planner recovery wave" not in management
    assert "must begin with a planner" not in management
    assert "questions may cover only task requirements" in worker_question

    schema = cortex.PUBLIC_SCHEMA_REGISTRY["read_dispatch_briefing"]
    max_bytes = schema["properties"]["max_bytes"]
    assert "caller-selected" in max_bytes["description"]
    assert "maximum" not in max_bytes


def test_internal_pipeline_classification_descriptions_keep_choice_with_orchestrator():
    import sys

    sys.path.insert(0, str(ROOT / "plugins/cortex/scripts"))
    import cortex

    classify_schema = cortex.TOOLS["classify_task"][1]
    reassess_schema = cortex.TOOLS["reassess_pipeline"][1]
    classify_description = classify_schema["properties"]["pipeline"]["description"].lower()
    reassess_description = reassess_schema["properties"]["pipeline"]["description"].lower()
    for description in (classify_description, reassess_description):
        assert "chosen pipeline remains authoritative" in description
        assert "documentation and close recommendations are advisory" in description
        assert "appends only documentation" not in description
        assert "documentation and close are enforced" not in description


def test_plan_approval_defaults_to_auto_and_requires_explicit_user_intent():
    import sys

    sys.path.insert(0, str(ROOT / "plugins/cortex/scripts"))
    import cortex  # initialize the public schema registry

    task_schema = cortex.PUBLIC_SCHEMA_REGISTRY["start_orchestration"]["properties"]["task"]
    approval = task_schema["properties"]["plan_approval"]
    description = approval["description"].lower()
    assert "defaults to auto for every complexity" in description
    assert "only when the user explicitly requested" in description
    assert "c2/c3" not in description
    control_skill = (ROOT / "plugins/cortex/skills/cortex-control/SKILL.md").read_text(encoding="utf-8").lower()
    assert "defaults to `auto` for" in control_skill
    assert "defaults to `required` for c2/c3" not in control_skill
    assert "governance, planner, risk, and review recommendations never" in control_skill
    assert "follow the returned corrective planner dispatch" not in control_skill
    assert "orchestrator's chosen pipeline" in control_skill
    assert "never forces a planner wave" in control_skill


def test_governance_briefing_routes_internal_evidence_gaps_to_correction():
    briefing = (ROOT / "plugins/cortex/scripts/cortex_runtime/briefings.py").read_text(encoding="utf-8")
    control = (ROOT / "plugins/cortex/skills/cortex-control/SKILL.md").read_text(encoding="utf-8")
    profiles = (ROOT / "plugins/cortex/profiles.json").read_text(encoding="utf-8")
    briefing_lower = briefing.lower()
    assert "evidence gap in findings/attemptevents" in briefing_lower
    assert "persist a worker question" in briefing_lower
    assert "complete with status=blocked" not in briefing_lower
    control_lower = " ".join(control.lower().split())
    assert "return `orchestrator_advice`" in control_lower
    assert "do not ask the user, create a durable question, or remain idle" in control_lower
    assert "status=blocked may use unresolved" not in profiles.lower()
