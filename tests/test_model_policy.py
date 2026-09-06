"""Worker model routing policy regressions."""
import json
import runpy
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OBSERVER = runpy.run_path(str(ROOT / "scripts/cortex-desktop-dev"))


def assignment_message(policy_class, *, implementation_model=None,
                       implementation_effort=None, override=False):
    lines = [
        "$cortex:worker-backend-dev Bounded policy test assignment.",
        f"Policy class: {policy_class}",
        f"User-requested override: {'yes' if override else 'no'}",
    ]
    if implementation_model is not None:
        lines.append(f"Review implementation model: {implementation_model}")
    if implementation_effort is not None:
        lines.append(f"Review implementation effort: {implementation_effort}")
    return "\n".join(lines)


def spawn_payload(model, effort, policy_class, *, implementation_model=None,
                  implementation_effort=None, override=False):
    """Build only the documented native spawn_agent arguments."""
    payload = {
        "task_name": "policy-test",
        "message": assignment_message(
            policy_class,
            implementation_model=implementation_model,
            implementation_effort=implementation_effort,
            override=override,
        ),
        "model": model,
        "reasoning_effort": effort,
        "fork_turns": "none",
    }
    return json.dumps(payload)


def spawn_metadata(payload):
    return OBSERVER["safe_call_metadata"]("spawn_agent", payload)


def spawn_violations(payload, **extra):
    row = dict(
        thread_id="coordinator",
        role="coordinator",
        tool="spawn_agent",
        outcome="success",
    )
    row.update(spawn_metadata(payload))
    row.update(extra)
    return {item["violation"] for item in OBSERVER["call_policy_violations"]([row])}


def test_ordinary_research_and_analysis_are_luna_only():
    policy = OBSERVER["worker_model_policy"]
    assert policy("gpt-5.6-luna", "max", "research") == []
    assert policy("gpt-5.6-luna", "high", "exploration") == []
    assert policy("gpt-5.6-terra", "high", "analysis") == ["worker_model_policy_violation"]
    assert policy("gpt-5.6-sol", "high", "analysis") == ["worker_model_policy_violation"]


def test_complex_terra_and_sol_microtask_effort_allowlists():
    policy = OBSERVER["worker_model_policy"]
    assert policy("gpt-5.6-terra", "xhigh", "complex") == []
    assert policy("gpt-5.6-luna", "high", "complex") == ["worker_model_policy_violation"]
    assert policy("gpt-5.6-sol", "xhigh", "security-analysis-microtask") == []
    assert policy("gpt-5.6-sol", "max", "security-analysis-microtask") == ["worker_model_policy_violation"]


def test_security_implementation_never_routes_to_sol():
    policy = OBSERVER["worker_model_policy"]
    assert policy("gpt-5.6-luna", "high", "security-implementation") == []
    assert policy("gpt-5.6-terra", "high", "security-implementation") == []
    assert policy("gpt-5.6-sol", "high", "security-implementation") == ["worker_model_policy_violation"]


def test_reviews_are_stronger_than_the_implementation():
    policy = OBSERVER["worker_model_policy"]
    assert policy("gpt-5.6-terra", "medium", "review",
                  implementation_model="gpt-5.6-luna",
                  implementation_effort="high") == []
    assert policy("gpt-5.6-terra", "xhigh", "review",
                  implementation_model="gpt-5.6-terra",
                  implementation_effort="high") == []
    assert policy("gpt-5.6-terra", "high", "review",
                  implementation_model="gpt-5.6-terra",
                  implementation_effort="high") == ["worker_model_policy_violation"]
    assert policy("gpt-5.6-terra", "max", "review",
                  implementation_model="gpt-5.6-terra",
                  implementation_effort="max") == []


def test_explicit_user_override_is_preserved():
    payload = spawn_payload("gpt-5.6-sol", "ultra", "ordinary", override=True)
    assert set(json.loads(payload)) == {
        "task_name", "message", "model", "reasoning_effort", "fork_turns",
    }
    metadata = spawn_metadata(payload)
    assert metadata["explicit_user_override"] is True
    assert "worker_model_policy_violation" not in spawn_violations(payload)


def test_spawn_policy_audit_uses_supported_message_labels():
    complex_payload = spawn_payload("gpt-5.6-terra", "xhigh", "complex")
    complex_metadata = spawn_metadata(complex_payload)
    assert complex_metadata["policy_class"] == "complex"
    assert "worker_model_policy_violation" not in spawn_violations(complex_payload)

    sol_payload = spawn_payload("gpt-5.6-sol", "high", "security-analysis-microtask")
    assert "worker_model_policy_violation" not in spawn_violations(sol_payload)

    review_luna = spawn_payload(
        "gpt-5.6-terra", "medium", "review",
        implementation_model="gpt-5.6-luna", implementation_effort="high",
    )
    assert "worker_model_policy_violation" not in spawn_violations(review_luna)

    review_terra = spawn_payload(
        "gpt-5.6-terra", "xhigh", "review",
        implementation_model="gpt-5.6-terra", implementation_effort="high",
    )
    assert "worker_model_policy_violation" not in spawn_violations(review_terra)


def test_review_without_implementation_effort_is_rejected():
    payload = spawn_payload(
        "gpt-5.6-terra", "medium", "review",
        implementation_model="gpt-5.6-luna",
    )
    metadata = spawn_metadata(payload)
    assert "implementation_effort" not in metadata
    assert "worker_model_policy_violation" in spawn_violations(payload)


def test_review_with_invalid_implementation_effort_is_rejected():
    payload = spawn_payload(
        "gpt-5.6-terra", "medium", "review",
        implementation_model="gpt-5.6-luna",
        implementation_effort="ultra",
    )
    metadata = spawn_metadata(payload)
    assert metadata["implementation_effort"] == "ultra"
    assert "worker_model_policy_violation" in spawn_violations(payload)


def test_unsupported_top_level_policy_fields_are_ignored():
    payload = spawn_payload(
        "gpt-5.6-luna", "high", "ordinary",
    )
    value = json.loads(payload)
    value.update({
        "task_class": "security-implementation",
        "implementation_model": "gpt-5.6-terra",
        "implementation_effort": "max",
        "explicit_user_override": True,
        "user_requested_model": "gpt-5.6-sol",
        "agent_type": "security_auditor",
    })
    payload = json.dumps(value)
    metadata = spawn_metadata(payload)
    assert "task_class" not in metadata
    assert "implementation_model" not in metadata
    assert "implementation_effort" not in metadata
    assert metadata["explicit_user_override"] is False
    assert "user_requested_model" not in metadata
    assert "agent_type" not in metadata
    assert metadata["policy_class"] == "ordinary"
