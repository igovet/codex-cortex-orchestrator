import sys
import subprocess
import runpy
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "plugins/cortex/scripts"))
from cortex_runtime.advisory_lane import (QUALIFIED_TOOL, consider_advisory_lane,
                                           consult, discover, packet_identity)


def entry(name=QUALIFIED_TOOL, **extra):
    return {"name": name, "server": "mcp-rubber-duck", "inputSchema": {
        "type": "object", "required": ["prompt"], "properties": {"prompt": {"type": "string"},
        "provider": {}, "model": {}, "temperature": {}, "images": {}}}, **extra}


def test_discovery_requires_qualified_identity_and_caches():
    assert discover([{"name": "ask_duck", "server": "mcp-rubber-duck"}]) is None
    assert discover([{"name": QUALIFIED_TOOL}]) is None
    cache = {}
    capability = discover([entry(provider="byteplus")], cache=cache)
    assert capability and capability.provider == "byteplus"
    assert discover([], cache=cache) is capability


def test_discovery_rejects_spoofed_or_ambiguous_identity_and_lazy_catalogue():
    assert discover([entry(server="not-mcp-rubber-duck")]) is None
    assert discover([entry(server="mcp-rubber-duck", mcp="other")]) is None
    seen = []
    def lazy():
        for i in range(1000000):
            seen.append(i)
            yield {"name": "unrelated"}
    assert discover(lazy()) is None
    assert len(seen) == 128


def test_discovery_requires_object_schema_and_required_prompt():
    bad = entry(inputSchema={"type": "object", "properties": {"prompt": {}}})
    assert discover([bad]) is None


def test_prompt_schema_type_enum_and_value_are_fail_closed_before_invocation():
    calls = []
    invoke = lambda *args, **kwargs: calls.append(args) or "bad"
    for prompt_schema in ({"type": "number"}, {"type": "array"},
                          {"type": "string", "enum": ["other"]}):
        schema = {"type": "object", "required": ["prompt"], "properties": {"prompt": prompt_schema}}
        cap = discover([entry(inputSchema=schema)])
        if cap is None:
            continue
        assert consult(cap, {"prompt": "q"}, invoke=invoke)["status"] == "skipped"
    valid = discover([entry(inputSchema={"type": "object", "required": ["prompt"],
        "properties": {"prompt": {"type": "string", "enum": ["q"]}}})])
    assert consult(valid, {"prompt": "q"}, invoke=invoke)["status"] == "consulted"
    assert consult(valid, {"prompt": "other"}, invoke=invoke)["status"] == "skipped"
    assert len(calls) == 1
    bad = entry(inputSchema={"type": "array", "required": ["prompt"], "properties": {"prompt": {}}})
    assert discover([bad]) is None


def test_consider_transitions_and_negative_controls():
    assert consider_advisory_lane("consequential", question="q")["decision"] == "consult"
    assert consider_advisory_lane("contradictory", question="q")["decision"] == "consult"
    assert consider_advisory_lane("routine")["decision"] == "skip"
    assert consider_advisory_lane("incident-recovery", question="q")["decision"] == "skip"


def test_optional_metadata_one_call_dedupe_and_failure_are_advisory():
    cap = discover([entry()])
    calls = []
    def invoke(name, payload, timeout):
        calls.append((name, payload, timeout))
        return "advice"
    seen = set()
    packet = {"prompt": "Should we compare these approaches?"}
    result = consult(cap, packet, invoke=invoke, seen=seen)
    assert result["status"] == "consulted" and calls[0][0] == QUALIFIED_TOOL
    assert "provider" not in calls[0][1]
    assert consult(cap, packet, invoke=invoke, seen=seen)["reason"] == "duplicate-packet"
    assert packet_identity(calls[0][1]) == result["packet_id"]

    def fail(*args, **kwargs):
        raise TimeoutError()
    assert consult(cap, {"prompt": "q"}, invoke=fail)["reason"] == "timeout"
    assert consult(None, {"prompt": "q"})["status"] == "unavailable"


def test_malformed_packet_does_not_invoke():
    assert consult(discover([entry()]), {"provider": "byteplus"})["reason"] == "malformed-packet"


def test_optional_fields_must_be_declared_and_bounded():
    cap = discover([entry()])
    assert consult(cap, {"prompt": "q", "images": "SECRET"})["reason"] == "undeclared-field"
    no_images = entry(inputSchema={"type": "object", "required": ["prompt"], "properties": {"prompt": {"type": "string"}}})
    assert consult(discover([no_images]), {"prompt": "q", "images": ["x"]}, invoke=lambda *a, **k: "bad")["reason"] == "undeclared-field"
    assert consult(cap, {"prompt": "q" * 6001})["reason"] == "oversized-prompt"


def test_declared_provider_and_model_enums_are_enforced():
    schema = {"type": "object", "required": ["prompt"], "properties": {
        "prompt": {"type": "string"},
        "provider": {"type": "string", "enum": ["byteplus"]},
        "model": {"type": "string", "enum": ["duck-v1"]},
    }}
    cap = discover([entry(inputSchema=schema)])
    calls = []
    invoke = lambda name, payload, timeout: calls.append(payload) or "advice"
    assert consult(cap, {"prompt": "q", "provider": "byteplus", "model": "duck-v1"}, invoke=invoke)["status"] == "consulted"
    assert consult(cap, {"prompt": "q", "provider": "evil"}, invoke=invoke)["status"] == "skipped"
    assert consult(cap, {"prompt": "q", "model": "other"}, invoke=invoke)["status"] == "skipped"
    assert len(calls) == 1


def test_cli_and_desktop_expose_equivalent_explicit_opt_in():
    root = Path(__file__).parents[1]
    cli = subprocess.run([str(root / "scripts/cortex-live-smoke"), "start", "--help"], text=True, capture_output=True)
    desktop = subprocess.run(["python3", str(root / "scripts/cortex-desktop-dev"), "start", "--help"], text=True, capture_output=True)
    assert cli.returncode == desktop.returncode == 0
    assert "--rubber-duck-opt-in" in cli.stdout
    assert "--rubber-duck-opt-in" in desktop.stdout
    smoke = runpy.run_path(str(root / "scripts/cortex-live-smoke"), run_name="advisory-test")
    ordinary = smoke["launch_command"](Path("/tmp/events"), False, None, None, False, False)
    opted = smoke["launch_command"](Path("/tmp/events"), False, None, None, False, False, rubber_duck_opt_in=True)
    assert "CORTEX_RUBBER_DUCK_OPT_IN=1" not in ordinary
    assert "CORTEX_RUBBER_DUCK_OPT_IN=1" in opted
