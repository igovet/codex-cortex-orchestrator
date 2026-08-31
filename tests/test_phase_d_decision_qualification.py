"""Phase D black-box qualification for the binding-driven decision boundary.

Every semantic call in this module crosses the staged candidate's real stdio
MCP handler. The candidate process is started without checkout ``PYTHONPATH``
or source mode, and every follow-up reference is copied from the preceding
structured response. No test imports candidate runtime code or reconstructs
opaque references from display text.
"""
from __future__ import annotations

import json
import hashlib
import os
import re
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
from dataclasses import dataclass
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "plugins" / "cortex" / "scripts"))
from cortex_candidate_location import (  # noqa: E402
    CandidateLocation,
    from_release_root,
    from_verified_installed_receipt,
)
from cortex_candidate_receipt import read_verified_receipt  # noqa: E402
from cortex_release_candidate import build_source_candidate, plugin_tree_digest, source_candidate_manifest  # noqa: E402
from cortex import PUBLIC_TOOLS  # noqa: E402
from cortex_runtime.mcp_api import catalogue_identity  # noqa: E402
from cortex_runtime.observation_generation import create_session_intent, consume_intent  # noqa: E402


EXPECTED_TOOLS = (
    "open_task", "read_task", "open_clarification", "record_clarification", "open_plan_review", "record_plan_review", "open_steering", "record_steering",
    "open_assignment", "consume_assignment_evidence", "publish_plan",
    "publish_result", "publish_documentation", "assess_governance", "close_task",
)
RETIRED_TOOLS = {"open_decision", "record_user_decision"}


@dataclass(frozen=True)
class QualificationTarget:
    """The one canonical location consumed by every Phase D branch."""

    location: CandidateLocation
    build_id: str
    observation_codex_home: Path | None = None
    candidate_version: str | None = None


class CandidateMcp:
    """Small line-oriented JSON-RPC client for one ordinary candidate process."""

    def __init__(self, server: Path, cwd: Path, build_id: str, *, state_root: Path | None = None, observation_available: bool = False, observation_codex_home: Path | None = None, candidate_version: str | None = None, prepare_observation: bool = True) -> None:
        self.server, self.cwd, self.build_id = server, cwd, build_id
        self.observation_available = observation_available
        self.observation_codex_home = observation_codex_home
        self.candidate_version = candidate_version or server.parent.name
        self.prepare_observation = prepare_observation
        self.state_root = state_root or cwd.parent
        self._next = 0
        self._io_lock = threading.Lock()
        self.server_info: dict[str, Any] = {}
        self._start()

    def _start(self) -> None:
        env = dict(os.environ)
        env.pop("PYTHONPATH", None)
        env.pop("CORTEX_SOURCE_MODE", None)
        # Candidate qualification must never resolve the ledger through the
        # coordinator's real profile.  All clients for one fixture share this
        # isolated pair so concurrent stdio calls exercise one candidate
        # database, while separate fixtures cannot touch one another.
        isolated_home = self.state_root / "home"
        isolated_codex_home = self.observation_codex_home or (self.state_root / "codex")
        isolated_home.mkdir(parents=True, exist_ok=True)
        isolated_codex_home.mkdir(parents=True, exist_ok=True)
        # The event journal accepts only the same owner-only isolated Codex
        # root that cortex-dev creates in real live-dev. Candidate qualification
        # must not weaken that filesystem boundary with a default 0755 fixture.
        os.chmod(isolated_home, 0o700)
        os.chmod(isolated_codex_home, 0o700)
        env["HOME"] = str(isolated_home)
        env["CODEX_HOME"] = str(isolated_codex_home)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        env["CORTEX_BUILD_ID"] = self.build_id
        env["CORTEX_SOURCE_DIGEST"] = self.build_id.removeprefix("sha256:")
        # The journal's default project anchor follows PWD; make the
        # subprocess environment agree with the explicit stdio cwd instead of
        # inheriting the qualification checkout's PWD.
        env["PWD"] = str(self.cwd)
        # Provision the same signed, runtime-owned observation lease that
        # cortex-live-smoke establishes before launching Codex.  This keeps
        # stdio qualification faithful to the production claim path and
        # prevents the journal from silently falling back to an unbound path.
        if self.observation_available and self.prepare_observation:
            identity = catalogue_identity(PUBLIC_TOOLS)
            nonce = os.urandom(32).hex()
            create_session_intent(code_home=isolated_codex_home, session_nonce=nonce)
            consume_intent(
                code_home=isolated_codex_home,
                package_root=self.server.parent.parent,
                build_id=self.build_id,
                candidate_version=self.candidate_version,
                catalogue_count=len(PUBLIC_TOOLS),
                catalogue_digest=str(identity["catalogue_digest"]),
                session_nonce=nonce,
            )
        self.process = subprocess.Popen(
            [sys.executable, "-B", str(self.server)], cwd=self.cwd, env=env,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, bufsize=1,
        )
        initialized = self.request("initialize", {
            "protocolVersion": "2025-06-18", "capabilities": {},
            "clientInfo": {"name": "phase-d-qualification", "version": "1"},
        })
        self.server_info = initialized["result"]["serverInfo"]
        assert self.server_info["parityVerified"] is True
        assert self.server_info["buildId"] == self.build_id
        self.notify("notifications/initialized", {})

    def _write(self, value: dict[str, Any]) -> None:
        assert self.process.stdin is not None
        self.process.stdin.write(json.dumps(value, ensure_ascii=False) + "\n")
        self.process.stdin.flush()

    def _read(self, request_id: int | str) -> dict[str, Any]:
        assert self.process.stdout is not None
        while True:
            line = self.process.stdout.readline()
            if not line:
                stderr = self.process.stderr.read() if self.process.stderr else ""
                raise AssertionError(f"candidate exited before response: {stderr}")
            response = json.loads(line)
            if response.get("id") == request_id:
                return response

    def request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        with self._io_lock:
            self._next += 1
            request_id = self._next
            self._write({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
            return self._read(request_id)

    def notify(self, method: str, params: dict[str, Any]) -> None:
        with self._io_lock:
            self._write({"jsonrpc": "2.0", "method": method, "params": params})

    def list_tools(self) -> list[dict[str, Any]]:
        return self.request("tools/list", {})["result"]["tools"]

    def call_raw(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        response = self.request("tools/call", {"name": name, "arguments": arguments})
        assert "error" not in response, response
        return response["result"]

    def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Return structured content; retain errors as explicit test values."""
        result = self.call_raw(name, arguments)
        structured = result.get("structuredContent", result)
        if result.get("isError"):
            return {"_tool_error": structured.get("error", structured), "_raw": result}
        assert isinstance(structured, dict), structured
        return structured

    @staticmethod
    def is_error(value: dict[str, Any]) -> bool:
        return "_tool_error" in value

    @staticmethod
    def error(value: dict[str, Any]) -> dict[str, Any]:
        assert CandidateMcp.is_error(value), value
        return value["_tool_error"]

    def close(self) -> None:
        if self.process.poll() is not None:
            return
        if self.process.stdin:
            self.process.stdin.close()
        self.process.wait(timeout=10)
        stderr = self.process.stderr.read() if self.process.stderr else ""
        assert self.process.returncode == 0, {
            "returncode": self.process.returncode,
            "stderr": stderr[-2000:],
        }
        # Release-root/source fixtures intentionally have no signed
        # observation lease.  The candidate_codex_home boundary reports this
        # nonblocking state; only receipt-selected installed runs require a
        # clean observation channel.
        unexpected = stderr.replace("Cortex MCP event observation=limited\n", "").strip()
        if self.observation_available:
            assert not unexpected, {"returncode": self.process.returncode, "stderr": stderr[-2000:]}
        else:
            assert not unexpected, {"returncode": self.process.returncode, "stderr": stderr[-2000:]}

    def restart(self) -> None:
        self.close()
        self._start()


@pytest.fixture()
def candidate():
    with tempfile.TemporaryDirectory(prefix="cortex-phase-d-") as directory:
        root = Path(directory)
        override = os.environ.get("CORTEX_PHASE_D_CANDIDATE_ROOT")
        observation_codex_home: Path | None = None
        candidate_version: str | None = None
        if override:
            owner = Path(os.environ.get("CORTEX_PHASE_D_ISOLATED_OWNER_HOME", Path.home())).absolute()
            receipt = read_verified_receipt(
                source_root=ROOT,
                owner_home=owner,
                isolated_home=owner / ".cortex-dev",
                isolated_codex_home=owner / ".cortex-dev/.codex",
            )
            manifest = source_candidate_manifest(ROOT).installable_plugin_manifest()
            location = from_verified_installed_receipt(receipt, requested_root=override)
            build_id = "sha256:" + plugin_tree_digest(location.plugin_root, manifest)
            observation_codex_home = Path(receipt["isolated_codex_home"]).absolute()
            candidate_version = str(receipt["candidate_version"])
        else:
            staged = root / "candidate"
            manifest = build_source_candidate(ROOT, staged)
            location = from_release_root(staged)
            build_id = "sha256:" + manifest.plugin_digest(staged)
        project = root / "project"
        project.mkdir()
        target = QualificationTarget(location=location, build_id=build_id, observation_codex_home=observation_codex_home, candidate_version=candidate_version)
        client = CandidateMcp(
            target.location.server_path, project, target.build_id,
            observation_available=target.location.kind == "installed",
            observation_codex_home=target.observation_codex_home,
            candidate_version=target.candidate_version,
        )
        try:
            yield client, project, target
        finally:
            client.close()


def _success(client: CandidateMcp, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    result = client.call(name, arguments)
    assert not client.is_error(result), (name, result)
    return result


def _expect_error(client: CandidateMcp, name: str, arguments: dict[str, Any], codes: set[str]) -> dict[str, Any]:
    result = client.call(name, arguments)
    assert client.is_error(result), (name, result)
    error = client.error(result)
    assert error["code"] in codes, error
    assert "Traceback" not in json.dumps(result, ensure_ascii=False)
    assert "private" not in json.dumps(result, ensure_ascii=False).lower()
    return error


def _decision_args(
    task_ref: str,
    binding_ref: str,
    response_original: str,
    user_language: str,
    *,
    outcome: str | None = None,
    steering_delta: dict[str, Any] | None = None,
    supersedes_decision_ref: str | None = None,
) -> dict[str, Any]:
    response: dict[str, Any] = {"response_original": response_original, "user_language": user_language}
    if outcome is not None:
        response["outcome"] = outcome
    if steering_delta is not None:
        response.update({"add": steering_delta.get("add", []), "retire_item_refs": steering_delta.get("retire_item_refs", [])})
    if supersedes_decision_ref is not None:
        response["supersedes_decision_ref"] = supersedes_decision_ref
    return {"task_ref": task_ref, "binding_ref": binding_ref, **response}


def _task(client: CandidateMcp, project: Path, *, suffix: str = "") -> str:
    result = _success(client, "open_task", {
        "task": {
            "project_root": str(project),
            "request_original": "Qualify decisions " + suffix, "user_language": "en",
            "outcomes": [{"requirement": "Preserve every decision capability.", "acceptance": ["The decision transaction is atomic."]}],
            "constraints": ["Do not create duplicate bindings."],
        },
    })
    return result["handles"]["task_ref"]


def _planner(client: CandidateMcp, task_ref: str) -> dict[str, Any]:
    state = _success(client, "read_task", {"task_ref": task_ref})
    item_refs = [item["item_ref"] for item in state["effective_contract"]["items"]]
    return _success(client, "open_assignment", {
        "task_ref": task_ref,
        "mission": {"role": "planner", "profile_name": "planner", "responsibility": "planning", "goal": "Prepare one immutable plan.", "constraints": "Planning only.", "instructions": "Map every contract item and submit one complete plan.", "item_refs": item_refs},
    })


def _plan_evidence(assignment: dict[str, Any]) -> dict[str, Any]:
    items = assignment.get("effective_contract", {}).get("planning_items", [])
    return {
        "schema": "cortex/report/plan/v3", "summary": "Complete qualification plan.",
        "scope": "Complete contract.",
        "stages": [{"owner": "planner", "work": ["Map every requirement."], "verification": ["Check every item."]}],
        "verification": ["Inspect every criterion."], "risks": [], "deviations": [], "unresolved": [],
        "verification_facts": [{"state": "not_run", "summary": "Planning does not execute project commands."}],
        "documentation_impact": "No documentation changed; no affected paths.",
        "contract_coverage": [{"item_ref": item["item_ref"], "status": "planned", "verification": ["Mapped this exact qualification item."]} for item in items],
    }


def _published_plan(client: CandidateMcp, project: Path) -> tuple[str, dict[str, Any], str]:
    task_ref = _task(client, project, suffix="plan")
    planner = _planner(client, task_ref)
    assignment_ref = planner["handles"]["assignment_ref"]
    bootstrap = _success(client, "consume_assignment_evidence", {
        "assignment_ref": assignment_ref,
    })
    planner["effective_contract"] = bootstrap.get("effective_contract", {})
    published = _success(client, "publish_plan", {
        "continuation_ref": bootstrap["handles"]["continuation_ref"],
        "assignment_ref": assignment_ref,
        "evidence": _plan_evidence(planner),
    })
    plan_ref = published["handles"]["report_ref"]
    return task_ref, published, plan_ref


def test_candidate_catalog_provenance_and_first_calls(candidate) -> None:
    client, project, target = candidate
    assert client.server_info["candidatePath"] == str(target.location.plugin_root)
    assert client.server_info["sourceDigest"] == target.build_id.removeprefix("sha256:")
    tools = client.list_tools()
    names = tuple(item["name"] for item in tools)
    assert names == EXPECTED_TOOLS
    assert not RETIRED_TOOLS.intersection(names)
    if target.location.kind == "installed":
        assert client.observation_codex_home is not None
        assert client.observation_codex_home == Path("/home/igovet/.cortex-dev/.codex")
        lease_root = client.observation_codex_home / ".cortex-mcp-observations"
        lease = json.loads((lease_root / "lease.json").read_text(encoding="ascii"))
        journal = lease_root / "generations" / lease["generation_id"] / "events.jsonl"
        ready = [json.loads(line) for line in journal.read_text(encoding="ascii").splitlines() if line.strip()]
        assert len(ready) == 1
        assert ready[0]["operation"] == "server_ready"
        assert ready[0]["kind"] == "registration"
        assert ready[0]["outcome"] == "success"
        assert ready[0]["build_id"] == target.build_id
        assert ready[0]["catalogue_count"] == len(EXPECTED_TOOLS)
        expected_digest = hashlib.sha256(json.dumps(
            tuple({"name": item["name"], "description": str(item["description"]),
                  "inputSchema": dict(item["inputSchema"]),
                  "outputSchema": dict(item["outputSchema"])} for item in tools),
            sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False,
        ).encode("ascii")).hexdigest()
        assert ready[0]["catalogue_digest"] == expected_digest
        assert all(key not in ready[0] for key in ("task_ref", "assignment_ref", "decision_ref"))
    for tool in tools:
        assert tool["inputSchema"]["type"] == "object"
        assert tool["inputSchema"]["additionalProperties"] is False
        assert tool["outputSchema"]["type"] == "object"
        handles = tool["outputSchema"]["properties"].get("handles")
        if handles is not None:
            assert handles["additionalProperties"] is False

    task_ref = _task(client, project)
    opened = _success(client, "open_clarification", {"task_ref": task_ref, "prompt": "First candidate call?", "prompt_language": "en"})
    assert opened["handles"]["binding_ref"].startswith("cb_")


def test_clarification_is_exactly_once_localized_and_replay_safe(candidate) -> None:
    client, project, _target = candidate
    task_ref = _task(client, project, suffix="clarification")
    prompt = "¿Qué color — голубой? 答案"
    response = "Ответ ✅ — do not translate or summarize"
    first = _success(client, "open_clarification", {"task_ref": task_ref, "prompt": prompt, "prompt_language": "ru"})
    binding = first["handles"]["binding_ref"]
    second = _success(client, "open_clarification", {"task_ref": task_ref, "prompt": prompt, "prompt_language": "ru"})
    assert second["handles"]["binding_ref"] == binding
    delta = {"add": [{"category": "requirement", "text": "Use the selected theme."}], "retire_item_refs": []}
    recorded = _success(client, "record_clarification", _decision_args(task_ref, binding, response, "ru"))
    decision_ref = recorded["handles"]["decision_ref"]
    assert recorded.get("replayed") is False
    assert recorded.get("decision", {}).get("response_original") == response
    replay = _success(client, "record_clarification", _decision_args(task_ref, binding, response, "ru"))
    assert replay["handles"]["decision_ref"] == decision_ref
    assert replay.get("replayed") is True
    conflict = _expect_error(client, "record_clarification", _decision_args(task_ref, binding, "Другой ответ", "ru"), {"command_conflict"})
    assert conflict["retryable"] is False
    inspected = _success(client, "read_task", {"task_ref": task_ref})
    decisions = inspected.get("decisions", [])
    # Canonical durable IDs are intentionally absent from public callable
    # handles; the read projection proves that exactly one decision exists.
    assert len(decisions) == 1
    assert len(inspected.get("delegations", [])) == 0


def test_clarification_allows_language_neutral_internal_prompt_with_russian_rendering_metadata(candidate) -> None:
    client, project, _target = candidate
    task_ref = _task(client, project, suffix="prompt-language")
    opened = _success(client, "open_clarification", {
        "task_ref": task_ref,
        "prompt": "Confirm the preferred visual direction.",
        "prompt_language": "ru",
    })
    assert opened["decision_context"]["prompt_language"] == "ru"
    assert opened["decision_context"]["prompt"] == "Confirm the preferred visual direction."
def test_pending_user_decision_atomically_blocks_task_phase_advance(candidate) -> None:
    """A chat answer cannot be bypassed by dispatch or governance mutations."""
    client, project, _target = candidate
    task_ref = _task(client, project, suffix="pending-phase-gate")
    prompt = "Which visual tone should be used?"
    opened = _success(client, "open_clarification", {
        "task_ref": task_ref,
        "prompt": prompt,
        "prompt_language": "en",
    })
    binding_ref = opened["handles"]["binding_ref"]
    state = _success(client, "read_task", {"task_ref": task_ref})
    item_refs = [item["item_ref"] for item in state["effective_contract"]["items"]]

    governance_error = _expect_error(client, "assess_governance", {
        "task_ref": task_ref,
        "mode": "light",
        "rationale": "Low-risk page.",
        "risk_factors": ["accessibility"],
    }, {"decision_pending"})
    assert "previously returned pending binding" in governance_error["action"]

    assignment_error = _expect_error(client, "open_assignment", {
        "task_ref": task_ref,
        "mission": {
            "role": "planner",
            "profile_name": "planner",
            "responsibility": "planning",
            "goal": "Prepare the implementation plan.",
            "constraints": "Planning only.",
            "instructions": "Map every current requirement before implementation.",
            "item_refs": item_refs,
        },
    }, {"decision_pending"})
    assert "previously returned pending binding" in assignment_error["action"]

    replay = _success(client, "open_clarification", {
        "task_ref": task_ref,
        "prompt": prompt,
        "prompt_language": "en",
    })
    assert replay["handles"]["binding_ref"] == binding_ref
    assert replay["replayed"] is True

    recorded = _success(client, "record_clarification", _decision_args(
        task_ref, binding_ref, "Use a calm and neutral tone.", "en",
    ))
    assert recorded["replayed"] is False
    assessment = _success(client, "assess_governance", {
        "task_ref": task_ref,
        "mode": "light",
        "rationale": "Low-risk page.",
        "risk_factors": ["accessibility"],
    })
    assert assessment["handles"]["task_ref"] == task_ref
    assignment = _planner(client, task_ref)
    assert assignment["handles"]["assignment_ref"].startswith("d_")


def test_concurrent_open_and_record_converge_on_one_binding_and_decision(candidate) -> None:
    client, project, target = candidate
    task_ref = _task(client, project, suffix="concurrency")
    clients = [CandidateMcp(target.location.server_path, project, target.build_id, observation_available=target.location.kind == "installed", observation_codex_home=target.observation_codex_home, candidate_version=target.candidate_version, prepare_observation=False) for _ in range(2)]
    try:
        opened: list[dict[str, Any]] = []
        lock = threading.Lock()

        open_failures: list[str] = []

        def open_one(worker: CandidateMcp) -> None:
            try:
                value = worker.call("open_clarification", {"task_ref": task_ref, "prompt": "Concurrent?", "prompt_language": "en"})
                if worker.is_error(value):
                    raise AssertionError(worker.error(value))
                with lock:
                    opened.append(value)
            except BaseException as exc:
                with lock:
                    open_failures.append(type(exc).__name__ + ": " + str(exc))

        threads = [threading.Thread(target=open_one, args=(worker,)) for worker in clients]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert open_failures == []
        assert len(opened) == 2
        bindings = {value["handles"]["binding_ref"] for value in opened}
        assert len(bindings) == 1
        binding = next(iter(bindings))
        recorded: list[dict[str, Any]] = []

        record_failures: list[str] = []

        def record_one(worker: CandidateMcp) -> None:
            try:
                value = worker.call("record_clarification", _decision_args(task_ref, binding, "Yes", "en"))
                with lock:
                    recorded.append(value)
            except BaseException as exc:
                with lock:
                    record_failures.append(type(exc).__name__ + ": " + str(exc))

        threads = [threading.Thread(target=record_one, args=(worker,)) for worker in clients]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert record_failures == []
        assert len(recorded) == 2
        assert all(not worker.is_error(value) for worker, value in zip(clients, recorded))
        assert len({value["handles"]["decision_ref"] for value in recorded}) == 1
        assert sorted(bool(value.get("replayed")) for value in recorded) == [False, True]
    finally:
        for worker in clients:
            worker.close()


def test_restart_and_lost_response_reconcile_without_new_binding(candidate) -> None:
    client, project, _target = candidate
    task_ref = _task(client, project, suffix="restart")
    opened = _success(client, "open_clarification", {"task_ref": task_ref, "prompt": "Restart safely?", "prompt_language": "en"})
    binding = opened["handles"]["binding_ref"]
    committed = _success(client, "record_clarification", _decision_args(task_ref, binding, "Yes", "en"))
    decision_ref = committed["handles"]["decision_ref"]
    client.restart()
    same = _success(client, "open_clarification", {"task_ref": task_ref, "prompt": "Restart safely?", "prompt_language": "en"})
    assert same["handles"]["binding_ref"] == binding
    replay = _success(client, "record_clarification", _decision_args(task_ref, binding, "Yes", "en"))
    assert replay["handles"]["decision_ref"] == decision_ref
    assert replay["replayed"] is True
    inspected = _success(client, "read_task", {"task_ref": task_ref})
    # The public projection deliberately keeps canonical durable IDs
    # non-callable.  Count the durable decision and use the compact reference
    # returned by the record receipt for all subsequent calls.
    assert len(inspected.get("decisions", [])) == 1


@pytest.mark.parametrize("outcome", ["approve", "request_revision", "cancel"])
def test_plan_review_outcomes_bind_one_immutable_plan(candidate, outcome: str) -> None:
    client, project, _target = candidate
    task_ref, published, plan_ref = _published_plan(client, project)
    approval_view = published.get("approval_view")
    assert isinstance(approval_view, dict)
    assert approval_view.get("status") == "ready"
    assert approval_view.get("report_content_digest") == published["report"]["content_digest"]
    opened = _success(client, "open_plan_review", {"task_ref": task_ref, "plan_ref": plan_ref, "prompt": "Review this exact plan.", "prompt_language": "en"})
    binding = opened["handles"]["binding_ref"]
    response = "I explicitly choose " + outcome
    recorded = _success(client, "record_plan_review", _decision_args(task_ref, binding, response, "en", outcome=outcome))
    assert recorded["handles"]["decision_ref"].startswith("u_")
    assert recorded["replayed"] is False
    reopened = _success(client, "open_plan_review", {"task_ref": task_ref, "plan_ref": plan_ref, "prompt": "Review this exact plan.", "prompt_language": "en"})
    assert reopened["handles"]["binding_ref"] == binding
    assert reopened["decision_context"]["consumed"] is True
    assert reopened["decision_context"]["decision_ref"] == recorded["handles"]["decision_ref"]
    replay = _success(client, "record_plan_review", _decision_args(task_ref, binding, response, "en", outcome=outcome))
    assert replay["handles"]["decision_ref"] == recorded["handles"]["decision_ref"]
    assert replay["replayed"] is True
    _expect_error(client, "record_plan_review", _decision_args(task_ref, binding, "Changed outcome", "en", outcome="approve" if outcome != "approve" else "cancel"), {"command_conflict"})


def test_plan_revision_relations_are_server_derived_through_real_stdio(candidate) -> None:
    client, project, _target = candidate
    task_ref, first_publication, first_plan_ref = _published_plan(client, project)
    first_assignment_ref = first_publication["approval_view"]["delegation_ref"]
    opened = _success(client, "open_plan_review", {
        "task_ref": task_ref,
        "plan_ref": first_plan_ref,
        "prompt": "Review the first immutable plan.",
        "prompt_language": "en",
    })
    revision = _success(client, "record_plan_review", _decision_args(
        task_ref, opened["handles"]["binding_ref"],
        "Add explicit print verification.", "en", outcome="request_revision",
    ))
    state = _success(client, "read_task", {"task_ref": task_ref})
    item_refs = [item["item_ref"] for item in state["effective_contract"]["items"]]
    replacement = _success(client, "open_assignment", {
        "task_ref": task_ref,
        "input_report_refs": [first_plan_ref],
        "input_decision_refs": [revision["handles"]["decision_ref"]],
        "mission": {
            "role": "revision planner",
            "profile_name": "planner",
            "responsibility": "planning",
            "goal": "Publish one revised immutable plan.",
            "constraints": "Planning only.",
            "instructions": "Consume the predecessor evidence and revise the plan.",
            "item_refs": item_refs,
        },
    })
    assert replacement["relations"] == {"parent_assignment_ref": first_assignment_ref}
    assignment_ref = replacement["handles"]["assignment_ref"]
    consumed = _success(client, "consume_assignment_evidence", {"assignment_ref": assignment_ref})
    consumed_body = consumed["evidence"]["reports"][0]["content"]
    assert consumed_body["stages"][0]["order"] == 1
    assert consumed_body["stages"][0]["dependencies"] == []
    second_publication = _success(client, "publish_plan", {
        "continuation_ref": consumed["handles"]["continuation_ref"],
        "assignment_ref": assignment_ref,
        # A server-returned immutable report body is a valid publication body.
        # This proves the advertised write/read/write contract through stdio.
        "evidence": consumed_body,
    })
    assert second_publication["report"]["supersedes_report_ref"] == first_plan_ref
    assert second_publication["handles"]["report_ref"] != first_plan_ref


def test_steering_creates_one_effective_revision_and_explicit_supersession(candidate) -> None:
    client, project, _target = candidate
    task_ref = _task(client, project, suffix="steering")
    initial_outcome_ref = _success(client, "read_task", {"task_ref": task_ref})["effective_contract"]["items"][0]["item_ref"]
    first_open = _success(client, "open_steering", {"task_ref": task_ref, "prompt": "May we add accessible colors?", "prompt_language": "en"})
    first_binding = first_open["handles"]["binding_ref"]
    first = _success(client, "record_steering", _decision_args(task_ref, first_binding, "Yes, add accessible colors.", "en", steering_delta={"add": [{"outcome_ref": initial_outcome_ref, "category": "requirement", "text": "Use accessible colors."}]}))
    first_decision = first["handles"]["decision_ref"]
    assert first["replayed"] is False
    assert first["decision"]["decision_ref"] == first_decision
    assert "decision_id" not in first["decision"]
    assert "supersedes_decision_id" not in first["decision"]
    after_first = _success(client, "read_task", {"task_ref": task_ref})
    assert after_first["effective_contract"]["revision"] == 2
    revised_outcome_ref = after_first["effective_contract"]["items"][0]["item_ref"]
    second_open = _success(client, "open_steering", {"task_ref": task_ref, "prompt": "May we refine accessible colors?", "prompt_language": "en"})
    second_binding = second_open["handles"]["binding_ref"]
    second_args = _decision_args(task_ref, second_binding, "Refine the accessibility requirement.", "en", steering_delta={"add": [{"outcome_ref": revised_outcome_ref, "category": "verification", "text": "Verify accessible contrast."}]}, supersedes_decision_ref=first_decision)
    second = _success(client, "record_steering", second_args)
    assert second["replayed"] is False
    assert second["decision"]["decision_ref"] == second["handles"]["decision_ref"]
    assert second["decision"]["relations"]["supersedes_decision_ref"] == first_decision
    assert "decision_id" not in second["decision"]
    assert "supersedes_decision_id" not in second["decision"]
    assert "supersedes_decision_id" not in second["decision"]["relations"]
    after_second = _success(client, "read_task", {"task_ref": task_ref})
    assert after_second["effective_contract"]["revision"] == 3
    replay = _success(client, "record_steering", second_args)
    assert replay["replayed"] is True
    assert _success(client, "read_task", {"task_ref": task_ref})["effective_contract"]["revision"] == 3


def test_stale_cross_project_wrong_family_and_malformed_calls_fail_safely(candidate) -> None:
    client, project, _target = candidate
    task_ref = _task(client, project, suffix="safety")
    opened = _success(client, "open_clarification", {"task_ref": task_ref, "prompt": "Will this become stale?", "prompt_language": "en"})
    binding = opened["handles"]["binding_ref"]
    outcome_ref = _success(client, "read_task", {"task_ref": task_ref})["effective_contract"]["items"][0]["item_ref"]
    steering = _success(client, "open_steering", {"task_ref": task_ref, "prompt": "Change the contract?", "prompt_language": "en"})
    _success(client, "record_steering", _decision_args(task_ref, steering["handles"]["binding_ref"], "Add a check.", "en", steering_delta={"add": [{"outcome_ref": outcome_ref, "category": "verification", "text": "Run the check."}]}))
    _expect_error(client, "record_clarification", _decision_args(task_ref, binding, "Too late", "en"), {"clarification_binding_stale"})
    _expect_error(client, "record_clarification", _decision_args(task_ref, binding, "Wrong family", "en"), {"clarification_binding_stale", "clarification_binding_mismatch"})
    _expect_error(client, "open_clarification", {"task_ref": task_ref, "prompt": "x", "prompt_language": "en", "unexpected": True}, {"validation_error"})

    with tempfile.TemporaryDirectory(prefix="cortex-phase-d-cross-project-") as other_directory:
        other = Path(other_directory)
        other_ref = _task(client, other, suffix="other")
        assert other_ref != task_ref
    _expect_error(client, "record_clarification", _decision_args(other_ref, binding, "Cross project", "en"), {"task_not_found"})


def test_governance_is_advisory_and_decision_does_not_schedule_or_approve(candidate) -> None:
    client, project, _target = candidate
    task_ref = _task(client, project, suffix="governance")
    before = _success(client, "read_task", {"task_ref": task_ref})
    assessment = _success(client, "assess_governance", {"task_ref": task_ref, "mode": "minimal"})
    assert assessment.get("handles", {}).get("initiative_ref") is None
    opened = _success(client, "open_clarification", {"task_ref": task_ref, "prompt": "Proceed?", "prompt_language": "en"})
    _success(client, "record_clarification", _decision_args(task_ref, opened["handles"]["binding_ref"], "Proceed.", "en"))
    after = _success(client, "read_task", {"task_ref": task_ref})
    assert len(after.get("delegations", [])) == len(before.get("delegations", [])) == 0
    assert not any(item.get("event_type") in {"delegation_created", "plan_approved"} for item in after.get("timeline", []))


def test_parallel_stdio_connections_keep_distinct_task_contexts(candidate) -> None:
    """Two live MCP processes must never share their connection task binding."""
    _fixture_client, project, target = candidate
    roots = [project.parent / "context-a", project.parent / "context-b"]
    states = [project.parent / "context-state-a", project.parent / "context-state-b"]
    for root in roots:
        root.mkdir()
    clients = [
        CandidateMcp(
            target.location.server_path, roots[index], target.build_id,
            state_root=states[index], observation_available=target.location.kind == "installed",
            observation_codex_home=target.observation_codex_home,
            candidate_version=target.candidate_version, prepare_observation=False,
        )
        for index in range(2)
    ]
    try:
        barrier = threading.Barrier(2)
        results: list[tuple[str, str] | None] = [None, None]

        def bind_and_read(index: int) -> None:
            barrier.wait(timeout=20)
            opened_ref = _task(clients[index], roots[index], suffix=f"context-{index}")
            # Omission is intentional: this must resolve only through the
            # current process's exact open_task binding.
            reread = _success(clients[index], "read_task", {})
            results[index] = (opened_ref, reread["handles"]["task_ref"])

        threads = [threading.Thread(target=bind_and_read, args=(index,)) for index in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)
        assert all(not thread.is_alive() for thread in threads)
        assert all(result is not None and result[0] == result[1] for result in results)
        assert results[0][0] != results[1][0]
    finally:
        for client in clients:
            client.close()


def test_candidate_eighty_simultaneous_pairs_are_receipt_exact_and_clean(candidate) -> None:
    """Stress the real candidate stdio boundary without importing its runtime.

    Each pair owns one isolated task and two independent candidate children.
    The pair opens and records concurrently, then exercises a changed-input
    conflict.  Assertions inspect only public responses and the bounded task
    projection; process return codes/stderr and filesystem sidecars are also
    checked so a hidden child crash cannot look like a semantic pass.
    """
    client, project, target = candidate
    pair_count = 80
    candidate_server = target.location.server_path
    pair_specs: list[tuple[int, Path, Path, str]] = []
    # Keep unrelated pairs off one SQLite shard.  Each pair still has two
    # independent candidate processes contending on its one task/database,
    # while all 80 pairs are submitted concurrently below.
    for index in range(pair_count):
        pair_project = project.parent / f"stress-project-{index}"
        pair_project.mkdir()
        pair_state = pair_project / ".candidate-state"
        pair_client = CandidateMcp(candidate_server, pair_project, target.build_id, state_root=pair_state, observation_available=target.location.kind == "installed", observation_codex_home=target.observation_codex_home, candidate_version=target.candidate_version, prepare_observation=False)
        try:
            pair_task = _task(pair_client, pair_project, suffix=f"stress-{index}")
        finally:
            pair_client.close()
        pair_specs.append((index, pair_project, pair_state, pair_task))

    def run_pair(index: int, pair_project: Path, pair_state: Path, task_ref: str) -> dict[str, Any]:
        left: CandidateMcp | None = None
        right: CandidateMcp | None = None
        try:
            left = CandidateMcp(candidate_server, pair_project, target.build_id, state_root=pair_state, observation_available=target.location.kind == "installed", observation_codex_home=target.observation_codex_home, candidate_version=target.candidate_version, prepare_observation=False)
            right = CandidateMcp(candidate_server, pair_project, target.build_id, state_root=pair_state, observation_available=target.location.kind == "installed", observation_codex_home=target.observation_codex_home, candidate_version=target.candidate_version, prepare_observation=False)
            clients = (left, right)
            barrier = threading.Barrier(2)
            opened: list[dict[str, Any]] = []
            open_failures: list[str] = []
            lock = threading.Lock()

            def open_one(worker: CandidateMcp) -> None:
                try:
                    barrier.wait(timeout=20)
                    value = worker.call("open_clarification", {
                        "task_ref": task_ref,
                        "prompt": f"Pair {index}: identical clarification?",
                        "prompt_language": "en",
                    })
                    if worker.is_error(value):
                        raise AssertionError(worker.error(value))
                    with lock:
                        opened.append(value)
                except BaseException as exc:
                    with lock:
                        open_failures.append(f"{type(exc).__name__}: {str(exc)[:500]}")

            threads = [threading.Thread(target=open_one, args=(worker,)) for worker in clients]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=30)
            assert all(not thread.is_alive() for thread in threads), "open child thread did not terminate"
            assert open_failures == [], open_failures
            assert len(opened) == 2
            bindings = {value["handles"]["binding_ref"] for value in opened}
            assert len(bindings) == 1, bindings
            binding = next(iter(bindings))

            recorded: list[dict[str, Any]] = []
            record_failures: list[str] = []
            record_barrier = threading.Barrier(2)

            def record_one(worker: CandidateMcp) -> None:
                try:
                    record_barrier.wait(timeout=20)
                    value = worker.call("record_clarification", _decision_args(
                        task_ref, binding, "One canonical answer.", "en",
                    ))
                    with lock:
                        recorded.append(value)
                except BaseException as exc:
                    with lock:
                        record_failures.append(f"{type(exc).__name__}: {str(exc)[:500]}")

            threads = [threading.Thread(target=record_one, args=(worker,)) for worker in clients]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=30)
            assert all(not thread.is_alive() for thread in threads), "record child thread did not terminate"
            assert record_failures == [], record_failures
            assert len(recorded) == 2
            assert all(not worker.is_error(value) for worker, value in zip(clients, recorded))
            decisions = {value["handles"]["decision_ref"] for value in recorded}
            assert len(decisions) == 1, decisions
            assert sorted(bool(value.get("replayed")) for value in recorded) == [False, True]

            conflict = left.call("record_clarification", _decision_args(
                task_ref, binding, "A changed answer must conflict.", "en",
            ))
            assert left.is_error(conflict), conflict
            assert left.error(conflict)["code"] == "command_conflict"

            projection = left.call("read_task", {"task_ref": task_ref})
            assert not left.is_error(projection), projection
            timeline = projection.get("timeline", [])
            assert sum(item.get("event_type") == "clarification_binding_issued" for item in timeline) == 1
            assert sum(item.get("event_type") == "user_decision_recorded" for item in timeline) == 1
            assert len(projection.get("decisions", [])) == 1
            return {"index": index, "binding": binding, "decision": next(iter(decisions))}
        finally:
            if left is not None:
                left.close()
            if right is not None:
                right.close()

    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=pair_count) as executor:
        futures = [executor.submit(run_pair, index, pair_project, pair_state, task_ref) for index, pair_project, pair_state, task_ref in pair_specs]
        results = [future.result() for future in futures]
    assert len(results) == pair_count
    sidecars = [path for _index, _pair_project, pair_state, _task_ref in pair_specs for path in pair_state.rglob("*") if path.is_file() and path.name.endswith(("-wal", "-shm"))]
    assert sidecars == [], [str(path) for path in sidecars]
