"""Offline deterministic prompt-eval fixtures and fail-closed live guard."""
from __future__ import annotations

import json
import hashlib
import re
from pathlib import Path
from typing import Any, Callable, Mapping

from cortex_runtime.prompt_compiler import PROMPT_CONTRACT, compile_v3_briefing


PLUGIN_ROOT = Path(__file__).resolve().parents[2]
FIXTURES_PATH = PLUGIN_ROOT / "prompt-evals" / "fixtures.json"


def load_prompt_eval_fixtures(path: Path = FIXTURES_PATH) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("prompt-eval fixtures are unreadable") from exc
    if (
        not isinstance(payload, dict)
        or payload.get("schema") != PROMPT_CONTRACT["prompt_eval"]["fixture_schema"]
        or not isinstance(payload.get("cases"), list)
        or not payload["cases"]
        or len(payload["cases"]) > int(PROMPT_CONTRACT["prompt_eval"]["max_cases"])
        or not isinstance(payload.get("ab_cases"), list)
        or not payload["ab_cases"]
        or len(payload["ab_cases"]) > int(PROMPT_CONTRACT["prompt_eval"]["max_cases"])
    ):
        raise RuntimeError("prompt-eval fixture contract is invalid")
    return payload


def _headings_outside_fences(prompt: str) -> list[str]:
    """Return Markdown H2 headings while treating assignment JSON as data."""
    headings: list[str] = []
    fence: str | None = None
    for line in prompt.splitlines():
        marker = re.fullmatch(r"(`{3,})(?:json)?", line)
        if marker:
            ticks = marker.group(1)
            if fence is None:
                fence = ticks
            elif ticks == fence:
                fence = None
            continue
        if fence is None and line.startswith("## "):
            headings.append(line)
    if fence is not None:
        raise AssertionError("prompt-eval has an unclosed assignment fence")
    return headings


def _assignment_data_span(prompt: str) -> tuple[int, int, int]:
    """Locate the sole JSON assignment payload and return its bytes span/fence."""
    opened = re.search(r"^(`{3,})json\n", prompt, flags=re.MULTILINE)
    if opened is None:
        raise AssertionError("prompt-eval assignment JSON fence is missing")
    fence = opened.group(1)
    close = re.search(r"\n" + re.escape(fence) + r"\n", prompt[opened.end():])
    if close is None:
        raise AssertionError("prompt-eval assignment JSON fence is not closed")
    data_start = opened.end()
    data_end = opened.end() + close.start()
    try:
        json.loads(prompt[data_start:data_end])
    except json.JSONDecodeError as exc:
        raise AssertionError("prompt-eval assignment JSON is invalid") from exc
    return data_start, data_end, len(fence)


def prompt_eval_metrics(prompt: str, *, assignment_markers: list[str]) -> dict[str, Any]:
    """Calculate deterministic structural metrics, never a subjective model score."""
    data_start, data_end, fence_ticks = _assignment_data_span(prompt)
    outside_assignment = prompt[:data_start] + prompt[data_end:]
    headings = _headings_outside_fences(prompt)
    return {
        "sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
        "bytes": len(prompt.encode("utf-8")),
        "section_count": len(headings),
        "unique_headings": len(headings) == len(set(headings)),
        "assignment_fence_ticks": fence_ticks,
        "assignment_markers_only_in_data": all(
            marker in prompt[data_start:data_end] and marker not in outside_assignment
            for marker in assignment_markers
        ),
    }


def render_prompt_ab_pair(case: Mapping[str, Any]) -> dict[str, str]:
    """Render the identical dispatch package through retained v2 and canonical v3.

    The compatibility path is intentionally evaluated as a baseline, not as a
    second source of policy.  The pair is deterministic because fixture task
    values and package identifiers are fixed.
    """
    # Briefings are a runtime-bound component.  Loading the facade first is
    # deliberate: it binds the exact bundled collaborators before importing
    # the renderer, rather than creating an unbound second runtime module.
    import cortex  # noqa: F401
    from cortex_runtime.briefings import _expanded_host_spawn_prompt, host_spawn_prompt

    profile = case.get("profile")
    package = case.get("package")
    if not isinstance(profile, str) or not profile or not isinstance(package, dict):
        raise RuntimeError("prompt A/B case has no profile/package")
    return {
        "legacy_v2": _expanded_host_spawn_prompt(profile, package),
        "canonical_v3": host_spawn_prompt(profile, package),
    }


def _validate_ab_expected(case_id: str, metrics: Mapping[str, Any], expected: Mapping[str, Any], version: str) -> None:
    if metrics.get("sha256") != expected.get("sha256"):
        raise AssertionError(f"prompt A/B golden digest drifted: {case_id}/{version}")
    for key in ("section_count", "assignment_markers_only_in_data"):
        if metrics.get(key) != expected.get(key):
            raise AssertionError(f"prompt A/B metric drifted: {case_id}/{version}/{key}")


def run_prompt_ab_evals(*, fixtures_path: Path = FIXTURES_PATH) -> list[dict[str, Any]]:
    """Compare legacy v2 and canonical v3 with deterministic, structural metrics.

    No model is called.  The retained legacy adapter is measured for
    compatibility and boundary delta; the canonical compiler must keep every
    fixture marker inside the assignment data block.
    """
    fixtures = load_prompt_eval_fixtures(fixtures_path)
    results: list[dict[str, Any]] = []
    for case in fixtures["ab_cases"]:
        if not isinstance(case, dict) or not isinstance(case.get("id"), str):
            raise RuntimeError("prompt A/B case has no stable id")
        markers = case.get("assignment_markers")
        expected = case.get("expected")
        if (
            not isinstance(markers, list)
            or not all(isinstance(marker, str) and marker for marker in markers)
            or not isinstance(expected, dict)
            or set(expected) != {"legacy_v2", "canonical_v3"}
            or not all(isinstance(expected.get(version), dict) for version in expected)
        ):
            raise RuntimeError("prompt A/B case has an invalid metric contract")
        rendered = render_prompt_ab_pair(case)
        metrics = {
            version: prompt_eval_metrics(prompt, assignment_markers=markers)
            for version, prompt in rendered.items()
        }
        for version in ("legacy_v2", "canonical_v3"):
            _validate_ab_expected(case["id"], metrics[version], expected[version], version)
        if not metrics["canonical_v3"]["assignment_markers_only_in_data"]:
            raise AssertionError("canonical v3 task-data boundary failed: " + case["id"])
        results.append({
            "id": case["id"],
            "legacy_v2": metrics["legacy_v2"],
            "canonical_v3": metrics["canonical_v3"],
            "delta": {
                "bytes": metrics["canonical_v3"]["bytes"] - metrics["legacy_v2"]["bytes"],
                "section_count": metrics["canonical_v3"]["section_count"] - metrics["legacy_v2"]["section_count"],
                "assignment_boundary_improved": (
                    not metrics["legacy_v2"]["assignment_markers_only_in_data"]
                    and metrics["canonical_v3"]["assignment_markers_only_in_data"]
                ),
            },
        })
    return results


def assert_live_prompt_eval_configuration(
    *, model: str | None, reasoning_effort: str | None, allow_model_fallback: bool = False,
) -> None:
    """Permit a live evaluator only for the explicit Luna-high route."""
    expected = PROMPT_CONTRACT["prompt_eval"]
    if allow_model_fallback or expected.get("allow_model_fallback") is not False:
        raise ValueError("live prompt eval fallback is forbidden")
    if model != expected["model"] or reasoning_effort != expected["reasoning_effort"]:
        raise ValueError("live prompt eval requires model=gpt-5.6-luna and reasoning_effort=high")


def _fixture_prompt(case: Mapping[str, Any]) -> str:
    sections = case.get("sections")
    if not isinstance(sections, dict):
        raise ValueError("prompt-eval case has no sections")
    assignment = case.get("assignment")
    if not isinstance(assignment, dict):
        raise ValueError("prompt-eval case has no assignment")
    return compile_v3_briefing(
        assignment=assignment,
        authority=str(sections.get("authority") or ""),
        hard_constraints=str(sections.get("hard_constraints") or ""),
        role_delta=str(sections.get("role") or ""),
        mode_delta=str(sections.get("mode") or ""),
        gate_delta=str(sections.get("gate") or ""),
        context_delta=str(sections.get("context") or ""),
        tool_protocol=str(sections.get("tool_protocol") or ""),
        output_contract=str(sections.get("output_contract") or ""),
        stopping=str(sections.get("stopping") or ""),
    )


def run_prompt_evals(
    *,
    fixtures_path: Path = FIXTURES_PATH,
    live: bool = False,
    model: str | None = None,
    reasoning_effort: str | None = None,
    executor: Callable[[str, str, str], Mapping[str, Any]] | None = None,
) -> list[str]:
    """Run deterministic fixtures; live execution requires an injected executor.

    The normal path never calls a model.  A host may inject its own evaluator,
    but it cannot select Terra/Sol or silently fall back to another model.
    """
    fixtures = load_prompt_eval_fixtures(fixtures_path)
    results: list[str] = []
    for case in fixtures["cases"]:
        if not isinstance(case, dict) or not isinstance(case.get("id"), str):
            raise RuntimeError("prompt-eval case has no stable id")
        prompt = _fixture_prompt(case)
        expected_headings = case.get("expected_headings")
        heuristics = case.get("heuristics")
        golden_sha256 = case.get("golden_sha256")
        if not isinstance(expected_headings, list) or not isinstance(heuristics, dict) or not isinstance(golden_sha256, str):
            raise RuntimeError("prompt-eval case has no golden structural contract")
        markers = heuristics.get("assignment_markers")
        if not isinstance(markers, list) or not all(isinstance(marker, str) and marker for marker in markers):
            raise RuntimeError("prompt-eval case has invalid assignment markers")
        actual_headings = _headings_outside_fences(prompt)
        if actual_headings != expected_headings:
            raise AssertionError("prompt-eval section order failed: " + case["id"])
        for required in case.get("must_contain", []):
            if str(required) not in prompt:
                raise AssertionError("prompt-eval required marker missing: " + case["id"])
        for forbidden in case.get("must_not_contain", []):
            if str(forbidden) in prompt:
                raise AssertionError("prompt-eval forbidden marker present: " + case["id"])
        metrics = prompt_eval_metrics(prompt, assignment_markers=markers)
        if metrics["sha256"] != golden_sha256:
            raise AssertionError("prompt-eval golden digest drifted: " + case["id"])
        if metrics["bytes"] > int(heuristics.get("max_bytes") or 0):
            raise AssertionError("prompt-eval byte budget exceeded: " + case["id"])
        if metrics["section_count"] != int(heuristics.get("section_count") or -1):
            raise AssertionError("prompt-eval section-count drifted: " + case["id"])
        if metrics["assignment_fence_ticks"] < int(heuristics.get("minimum_assignment_fence_ticks") or 0):
            raise AssertionError("prompt-eval assignment fence is too short: " + case["id"])
        if not metrics["unique_headings"] or not metrics["assignment_markers_only_in_data"]:
            raise AssertionError("prompt-eval data boundary failed: " + case["id"])
        results.append(case["id"])
    if live:
        assert_live_prompt_eval_configuration(model=model, reasoning_effort=reasoning_effort)
        if executor is None:
            raise RuntimeError("live prompt eval requires an explicit Luna-high executor; no local fallback exists")
        for case in fixtures["cases"]:
            verdict = executor(_fixture_prompt(case), str(model), str(reasoning_effort))
            if not isinstance(verdict, Mapping) or verdict.get("pass") is not True:
                raise AssertionError("live prompt evaluator rejected fixture: " + str(case["id"]))
    return results


__all__ = [
    "FIXTURES_PATH",
    "assert_live_prompt_eval_configuration",
    "load_prompt_eval_fixtures",
    "prompt_eval_metrics",
    "render_prompt_ab_pair",
    "run_prompt_ab_evals",
    "run_prompt_evals",
]
