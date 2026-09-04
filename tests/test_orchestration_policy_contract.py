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


def test_evidence_parallelism_preserves_finite_expansion_and_mutation_bounds():
    for path in (ORCHESTRATOR, ADAPTIVE):
        text = _text(path).lower()
        assert "finite" in text and "budgets" in text
        assert "parallel" in text and "readers" in text
        assert "one" in text and "artifact-mutating assignment" in text
        assert "one-worker limit" in text or "one worker" in text
        assert "no finite total cap" not in text
        assert "do not impose a finite total evidence-assignment cap" not in text


def test_dynamic_dag_dispatches_only_dependency_ready_nodes():
    for path in (ORCHESTRATOR, ADAPTIVE):
        text = _text(path).lower()
        assert "readiness is a dependency" in text
        assert "terminal, acceptable evidence" in text or "prerequisites become acceptable" in text
        assert "implementation-dependent" in text or "implementation -> dependent audits" in text
        assert "sealed generation" in text or "sealed artifact" in text
    planner = _text(PLANNER)
    assert "every stage names the predecessor evidence and artifacts it requires" in planner
    assert "scope availability and a free worker slot are not readiness evidence" in planner.lower()


def test_planner_remains_the_only_plan_owner_and_publishes_once():
    for path in (ORCHESTRATOR, ADAPTIVE):
        text = _text(path)
        assert "planner remains the sole owner" in text.lower()
        assert "independent" in text.lower() and "validation" in text.lower()
    planner = _text(PLANNER)
    assert "one terminal publication fixed by the consumed assignment" in planner
    assert "A planning node publishes a plan" in planner
    assert "validation node publishes its assigned result even when this profile is used" in planner
    assert "Never publish a supplementary report" in planner


def test_expected_missing_paths_are_observed_without_failed_commands():
    orchestrator = _text(ORCHESTRATOR)
    planner = _text(PLANNER)

    for text in (orchestrator, planner):
        assert "existence-aware" in text
        assert "absence" in text
        assert "possibly missing path" in text
    assert "successful evidence rather than a failed command" in orchestrator
    assert "never turn expected absence into a nonzero command failure" in planner


def test_non_git_project_is_detected_without_a_failed_git_command():
    orchestrator = _text(ORCHESTRATOR)
    planner = _text(PLANNER)

    for text in (orchestrator, planner):
        assert "before" in text.lower() and "Git command" in text
        assert "failure-normalizing" in text
        assert "exits cleanly" in text
        assert "non-Git" in text
        assert "successful" in text
        assert "skip" in text
        assert "nonzero command failure" in text


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


def test_assignment_selection_uses_current_typed_nodes_not_removed_outcome_modes():
    orchestrator = _text(ORCHESTRATOR).lower()
    assert "immediately before every assignment" in orchestrator
    assert "same connection" in orchestrator
    assert "exact observed ready nodes" in orchestrator
    assert "backend owns exact node grouping" in orchestrator
    assert "replayed or ambiguous dispatch never creates another worker" in orchestrator
    for obsolete in ("terminal_rework=", "omit outcome selection", "that responsibility's returned names"):
        assert obsolete not in orchestrator


def test_assignment_scope_has_one_typed_authority_not_a_prose_partition():
    for path in (ORCHESTRATOR, ADAPTIVE):
        text = _text(path).lower()
        assert "exact" in text and "graph nodes" in text
        assert "prose" in text and "complete assignment" in text
        assert "terminal" in text
        assert "omitted selection grants the complete current responsibility scope" not in text
    assert "profile name never grants outcome ownership" in _text(ORCHESTRATOR).lower()


def test_native_wait_is_advisory_and_durable_publication_recovers_empty_wait():
    orchestrator = _text(ORCHESTRATOR)
    control = _text(CONTROL)

    for text in (orchestrator, control):
        assert "Native wait output is advisory host coordination" in text
        assert "timeout or empty wait" in text
        assert "without polling the ledger" in text
        assert "finalized worker publication is authoritative durable completion evidence" in text
        assert "without another wait for that child" in text
        assert "lifecycle stop" in text.lower()
        assert "loss/recovery" in text
        assert "worker-liveness poll" in text
        assert "suppress" in text and "durable evidence" in text

    assert "immediate next Cortex operation" in orchestrator
    assert "priority over queued user steering" in orchestrator
    assert "never substitute the historical timeline" in orchestrator
    assert "immediate next Cortex operation" in control
    assert "before queued user steering" in control
    assert "Never use the historical timeline as a continuation lookup" in control
    assert "product branch" in orchestrator
    assert "plan-review" in orchestrator
    assert "direct user change" in control
    assert "do not open either question type" in control
    assert "terminal worker stop without publication" in control
    assert "never repeats the original assignment opening" in control
    assert "asks the user to say “continue”" in control


def test_orchestrator_has_one_canonical_routing_state_machine_without_payload_shapes():
    orchestrator = _text(ORCHESTRATOR)
    control = _text(CONTROL)
    start = orchestrator.index("## Routing state machine")
    end = orchestrator.index("## Delegation", start)
    routing = orchestrator[start:end]

    assert orchestrator.count("## Routing state machine") == 1
    assert "Observed coordinator event" in routing
    assert "read_continuations" in routing
    assert "Call any Cortex read merely to poll liveness" in routing
    assert "timeline as the second recovery operation" in routing
    assert "record that exact message as steering" in routing
    assert "record that exact message as steering" in routing
    assert "canonical coordinator router" in control

    worker_start = control.index("## Worker routing state machine")
    worker_end = control.index("## Anchor boundary", worker_start)
    worker_routing = control[worker_start:worker_end]
    assert control.count("## Worker routing state machine") == 1
    assert "Observed worker event" in worker_routing
    assert "read_task" in worker_routing
    assert "exactly one matching terminal publication" in worker_routing
    assert "read-only evidence as documentation" in worker_routing

    for payload_token in (
        '"task_ref"',
        '"continue"',
        '"report_policy"',
        '"outcomes"',
        "inputSchema",
        "additionalProperties",
    ):
        assert payload_token not in routing
        assert payload_token not in worker_routing


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


def test_no_conflicting_legacy_steering_review_or_unbounded_recovery_rules():
    removed = (
        "queue the exact message until the assignment publishes",
        "let that bounded assignment reach its terminal publication before recording steering",
        "after the revised planner publishes, the coordinator must open and record the fresh plan review",
        "a revised plan requires a fresh review",
        "terminal_rework=",
        "no finite total cap",
        "same-handle follow up",
        "copy its server-issued approval relation and exact report/view digest",
    )
    for path in (ORCHESTRATOR, CONTROL, ADAPTIVE):
        text = _text(path).lower()
        for obsolete in removed:
            assert obsolete not in text
        assert "pre-plan" in text
        assert "quiescence" in text or "quiescent" in text or "signed current observation" in text
        assert "closure" in text and "fresh" in text
def test_supporting_skills_preserve_typed_recovery_and_finite_evidence_policy():
    skills = ROOT / "plugins/cortex/skills"
    communication = (skills / "coordinator-communication/SKILL.md").read_text()
    recovery = (skills / "context-compaction/SKILL.md").read_text()
    validation = (skills / "output-validation/SKILL.md").read_text()
    combined = " ".join((communication + recovery + validation).split()).lower()
    for forbidden in (
        "exact returned retry handle", "stable finding key",
        "copy any complete semantic outcome used by a pending decision exactly from that fresh result",
        "only for the plan-review packet", "never becomes a backend permission barrier",
        "optional unchanged optional unchanged",
    ):
        assert forbidden not in combined
    assert "immediate next cortex operation" in recovery.lower()
    assert "complete unfiltered host" in recovery.lower()
    assert "genuine pre-plan steering" in communication.lower()
    assert "finite exhaustion" in communication.lower()
    assert "verification lives in that coverage matrix" in validation.lower()
    assert "failed source" in validation.lower()
    assert "only close permits" in validation.lower()
