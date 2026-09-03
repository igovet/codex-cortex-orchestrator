from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "plugins" / "cortex" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from cortex import PUBLIC_TOOLS
from cortex_runtime.mcp_api import (
    _SchemaError,
    _validation_failure,
    _validate_schema,
    _worker_candidate_read_schema,
)


EXPECTED_TOOLS = (
    "open_task", "read_task", "read_state", "read_scope", "read_outcome",
    "read_continuations", "read_evidence", "read_timeline",
    "open_clarification", "record_clarification",
    "open_plan_review", "record_plan_review", "open_steering", "record_steering",
    "open_assignment", "publish_plan", "publish_result", "publish_documentation",
    "assess_governance", "close_task",
)

# Keep this table deliberately about the public contract, rather than handler
# implementation details.  It catches omissions in any one of the twenty
# advertised operations while allowing genuinely optional fields to evolve.
EXPECTED_REQUIRED = {
    "open_task": {"outcomes", "project_root", "request_original", "user_language", "constraints"},
    "read_task": {"task_ref"},
    "read_state": {"task_ref"},
    "read_scope": {"task_ref", "responsibility"},
    "read_outcome": {"task_ref", "outcome"},
    "read_continuations": {"task_ref"},
    "read_evidence": {"task_ref", "report_policy"},
    "read_timeline": {"task_ref"},
    "open_clarification": {"task_ref", "prompt", "prompt_language"},
    "record_clarification": {"task_ref", "response_original", "user_language"},
    "open_plan_review": {"task_ref", "prompt", "prompt_language"},
    "record_plan_review": {"task_ref", "response_original", "user_language", "outcome"},
    "open_steering": {"task_ref", "prompt", "prompt_language"},
    "record_steering": {"task_ref", "response_original", "user_language", "add", "retire"},
    "open_assignment": {
        "task_ref", "role", "profile_name", "model", "reasoning_effort", "responsibility",
        "goal", "scope", "instructions", "report_policy",
    },
    "publish_plan": {
        "task_ref", "summary", "scope", "stages", "verification_facts",
        "outcome_coverage", "risks", "unresolved", "status",
    },
    "publish_result": {
        "task_ref", "summary", "outcome", "changes", "verification_facts", "outcome_coverage",
        "documentation_impact", "risks", "unresolved", "status",
    },
    "publish_documentation": {
        "task_ref", "summary", "findings", "recommendations",
        "outcome_coverage", "documentation_impact", "risks", "unresolved", "status",
    },
    "assess_governance": {"task_ref", "mode"},
    "close_task": {"task_ref", "verdict"},
}
FORBIDDEN = {
    "assignment_ref", "continuation_ref", "binding_ref", "report_ref", "report_refs",
    "plan_ref", "decision_ref", "item_ref", "cursor", "handles", "digest",
    "idempotency_key", "after_sequence",
}


def property_names(value):
    if isinstance(value, dict):
        properties = value.get("properties")
        if isinstance(properties, dict):
            yield from properties
        for item in value.values():
            yield from property_names(item)
    elif isinstance(value, list):
        for item in value:
            yield from property_names(item)


class PublicMcpFirstCallConformanceTests(unittest.TestCase):
    def test_open_task_retains_the_complete_original_semantic_contract(self) -> None:
        """Direct-call enforcement must not depend on sacrificing or reordering fields."""
        contract = PUBLIC_TOOLS["open_task"]
        schema = contract["inputSchema"]
        self.assertEqual(next(iter(schema["properties"])), "outcomes")
        self.assertEqual(schema["required"][0], "outcomes")
        outcome = schema["properties"]["outcomes"]["items"]
        self.assertEqual(
            list(outcome["properties"]),
            ["outcome", "acceptance", "constraints", "verification"],
        )
        self.assertEqual(
            outcome["required"],
            ["outcome", "acceptance", "constraints", "verification"],
        )
        description = contract["description"].lower()
        self.assertIn("primary semantic contract", description)
        self.assertIn("exactly one direct mcp call", description)
        self.assertIn("never invoke open_task through programmatic tool calling", description)

    def test_open_task_project_root_cannot_be_described_as_an_output_directory(self) -> None:
        """The first-call contract separates existing host root from planned work."""
        description = PUBLIC_TOOLS["open_task"]["inputSchema"]["properties"]["project_root"]["description"].lower()
        for phrase in (
            "absolute existing canonical project directory",
            "supplied by the host or current workspace",
            "not a planned output",
            "never append or create path segments",
        ):
            self.assertIn(phrase, description)

    def test_report_policy_is_scoped_to_the_operation_that_supplies_scope(self) -> None:
        read_policy = PUBLIC_TOOLS["read_evidence"]["inputSchema"]["properties"]["report_policy"]
        assignment_policy = PUBLIC_TOOLS["open_assignment"]["inputSchema"]["properties"]["report_policy"]
        self.assertEqual(read_policy["enum"], ["none", "active_plan", "all_finalized"])
        self.assertEqual(
            assignment_policy["enum"],
            ["none", "active_plan", "latest_for_scope", "all_finalized"],
        )
        self.assertNotIn("latest_for_scope", read_policy["enum"])
        self.assertIn("no caller-supplied outcome scope", read_policy["description"])

    def test_every_public_input_contract_is_required_and_closed(self) -> None:
        """The advertised boundary, not a handler convenience, is the API."""
        self.assertEqual(set(PUBLIC_TOOLS), set(EXPECTED_REQUIRED))
        for name, expected in EXPECTED_REQUIRED.items():
            with self.subTest(tool=name):
                schema = PUBLIC_TOOLS[name]["inputSchema"]
                self.assertEqual(set(schema["required"]), expected)
                self.assertFalse(schema["additionalProperties"])
                for field in expected:
                    self.assertTrue(
                        schema["properties"][field]["description"].startswith("Required property."),
                        (name, field, schema["properties"][field].get("description")),
                    )

                def assert_closed_objects(value: object, path: str = "schema") -> None:
                    if isinstance(value, dict):
                        if value.get("type") == "object" and "properties" in value:
                            self.assertIs(value.get("additionalProperties"), False, (name, path))
                        for key, child in value.items():
                            assert_closed_objects(child, f"{path}.{key}")
                    elif isinstance(value, list):
                        for index, child in enumerate(value):
                            assert_closed_objects(child, f"{path}[{index}]")

                assert_closed_objects(schema)

    def test_public_descriptions_advertise_ownership_and_timing(self) -> None:
        """Descriptions prevent a worker from guessing lifecycle ownership."""
        semantic_tokens = {
            "open_task": ("coordinator-only", "first project execution"),
            "read_task": (
                "fresh worker", "calls it first", "assignment",
                "only operation", "never supply a view",
            ),
            "read_state": ("coordinator-only", "bounded status summary"),
            "read_scope": ("coordinator-only", "one selected responsibility"),
            "read_outcome": ("coordinator-only", "one current semantic outcome"),
            "read_continuations": ("coordinator-only", "continuation"),
            "read_evidence": ("coordinator-only", "finalized evidence read"),
            "read_timeline": ("coordinator-only", "newest-first"),
            "open_clarification": ("coordinator-only", "decision opening"),
            "record_clarification": ("coordinator-only", "direct user answer"),
            "open_plan_review": ("coordinator-only", "current finalized active plan"),
            "record_plan_review": ("coordinator-only", "direct user decision"),
            "open_steering": ("coordinator-only", "decision opening"),
            "record_steering": ("coordinator-only", "atomic", "direct user steering"),
            "open_assignment": (
                "coordinator-only", "exactly one", "private worker assignment",
                "never reads or consumes", "must never call open_assignment",
            ),
            "publish_plan": ("worker-only", "atomic", "complete"),
            "publish_result": ("worker-only", "atomic", "complete"),
            "publish_documentation": ("worker-only", "atomic", "complete"),
            "assess_governance": ("coordinator-only", "before the first worker", "explicit"),
            "close_task": (
                "coordinator-only", "ledger", "unresolved evidence",
                "post-result review", "readiness probe",
            ),
        }
        for name, tokens in semantic_tokens.items():
            with self.subTest(tool=name):
                description = PUBLIC_TOOLS[name]["description"].lower()
                for token in tokens:
                    self.assertIn(token, description)

    def test_decision_open_descriptions_require_separate_informed_user_presentation(self) -> None:
        """A durable hold is not itself a visible question in CLI or Desktop."""
        for name in ("open_clarification", "open_plan_review", "open_steering"):
            with self.subTest(tool=name):
                description = PUBLIC_TOOLS[name]["description"].lower()
                self.assertIn("does not display its prompt to the user", description)
                self.assertIn("after success", description)
                self.assertIn("final answer", description)

        clarification = PUBLIC_TOOLS["open_clarification"]["description"].lower()
        self.assertIn("established context", clarification)
        self.assertIn("safe choices", clarification)
        self.assertIn("material consequence of each", clarification)
        self.assertIn("omit the options property entirely", clarification)
        self.assertIn("never send an empty array", clarification)
        options_description = (
            PUBLIC_TOOLS["open_clarification"]["inputSchema"]
            ["properties"]["options"]["description"].lower()
        )
        self.assertIn("legal only for purpose=closure_review", options_description)
        self.assertIn("must be absent", options_description)
        self.assertIn("never pass an empty array", options_description)

        steering = PUBLIC_TOOLS["open_steering"]["description"].lower()
        self.assertIn("context-free worker question", steering)
        self.assertIn("material consequence of each", steering)
        self.assertIn("before or after plan review, resume, or compaction", steering)
        self.assertIn("open steering before presenting it", steering)
        self.assertIn("never ask for a second confirmation", steering)

        self.assertIn("possible answers leave every current outcome detail unchanged", clarification)
        self.assertIn("must use open_steering instead", clarification)

        assignment = PUBLIC_TOOLS["open_assignment"]["description"].lower()
        self.assertIn("previously unstated concrete behavior", assignment)
        self.assertIn("including planning", assignment)
        self.assertIn("cannot substitute for that contract revision", assignment)

        recorded_clarification = PUBLIC_TOOLS["record_clarification"]["description"].lower()
        self.assertIn("open steering next", recorded_clarification)
        self.assertIn("do not read assignment scope", recorded_clarification)

        state = PUBLIC_TOOLS["read_state"]["description"].lower()
        self.assertIn("not worker-liveness polling", state)
        self.assertIn("wait timed out or returned no completion", state)

        continuations = PUBLIC_TOOLS["read_continuations"]["description"].lower()
        self.assertIn("call this next", continuations)
        self.assertIn("do not substitute read_timeline", continuations)

        timeline = PUBLIC_TOOLS["read_timeline"]["description"].lower()
        self.assertIn("explicit chronology or audit need", timeline)
        self.assertIn("must not replace read_continuations", timeline)

        plan_review = PUBLIC_TOOLS["open_plan_review"]["description"].lower()
        self.assertIn("result returns data.human_view", plan_review)
        self.assertIn("copy that complete markdown_link byte-for-byte", plan_review)
        for token in (
            "decision-ready plan summary",
            "scope, ordered stages, intended changes, verification, stop conditions",
            "material risks or unresolved items",
            "server-provided verified plan link",
            "enough detail inline for an informed decision",
            "bare 'plan ready' question is invalid",
        ):
            self.assertIn(token, plan_review)

        read_description = PUBLIC_TOOLS["read_evidence"]["description"].lower()
        self.assertIn("literal square brackets", read_description)
        self.assertIn("bare absolute path", read_description)
        self.assertIn("omitted label is invalid", read_description)

        plan_review = PUBLIC_TOOLS["open_plan_review"]["description"].lower()
        self.assertIn("bare path is not a link and is invalid", plan_review)
        self.assertIn("coordinator-only finalized evidence read", read_description)
        self.assertIn("verified human-view links", read_description)
        self.assertIn("copy every relevant returned link byte-for-byte", read_description)

    def test_public_descriptions_publish_the_exact_required_set(self) -> None:
        """The callable description must expose the same required set as its schema.

        This is deliberately derived from ``inputSchema`` rather than keeping a
        second hand-written field list in the test.  A model should be able to
        validate a publication call before sending it, and the description must
        not silently drift when a contract gains or removes a required field.
        """
        for name, contract in PUBLIC_TOOLS.items():
            with self.subTest(tool=name):
                schema = contract["inputSchema"]
                required = ", ".join(schema.get("required", []))
                description = contract["description"]
                self.assertIn(
                    f"Required properties for this call: {required}.",
                    description,
                )
                self.assertIn(
                    "Before invoking, verify every required property is present",
                    description,
                )

    def test_publish_result_validation_reports_all_missing_required_fields(self) -> None:
        """One malformed publication must not force serial field-by-field retries."""
        schema = PUBLIC_TOOLS["publish_result"]["inputSchema"]
        try:
            _validate_schema(schema, {"task_ref": "t_0123456789ab_" + "a" * 32})
        except ValueError as error:
            missing = tuple(getattr(error, "missing_fields", ()))
            self.assertEqual(
                missing,
                tuple(field for field in schema["required"] if field != "task_ref"),
            )
            failure = _validation_failure(
                error,
                tool_name="publish_result",
                arguments={"task_ref": "t_0123456789ab_" + "a" * 32},
                input_schema=schema,
            )
            details = failure["details"]
            self.assertEqual(details["missing_fields"], list(missing))
        else:
            self.fail("publish_result accepted a payload missing its required properties")

    def test_representative_first_calls_cross_real_stdio_boundary(self) -> None:
        """Exercise catalogue discovery plus task-opening and governance calls in stdio."""
        script = Path(__file__).resolve().parents[1] / "plugins" / "cortex" / "scripts" / "cortex.py"
        with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as project:
            env = dict(os.environ, CODEX_HOME=home, CORTEX_SOURCE_MODE="1", PYTHONDONTWRITEBYTECODE="1")
            env.pop("PYTHONPATH", None)
            process = subprocess.Popen(
                [sys.executable, str(script)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, text=True, env=env,
            )
            try:
                assert process.stdin is not None and process.stdout is not None

                notifications: list[dict] = []

                def call(payload: dict) -> dict:
                    process.stdin.write(json.dumps(payload) + "\n")
                    process.stdin.flush()
                    while True:
                        line = process.stdout.readline()
                        self.assertTrue(line.strip(), "stdio server closed before a response")
                        response = json.loads(line)
                        if "id" not in response and response.get("method") == "notifications/tools/list_changed":
                            notifications.append(response)
                            continue
                        return response

                call({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "conformance", "version": "1"}}})
                process.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}}) + "\n")
                process.stdin.flush()
                catalogue = call({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
                names = [item["name"] for item in catalogue["result"]["tools"]]
                self.assertEqual(len(names), 20)
                by_name = {item["name"]: item for item in catalogue["result"]["tools"]}
                expected_catalogue = [{
                    "name": name,
                    "description": contract["description"],
                    "inputSchema": contract["inputSchema"],
                } for name, contract in PUBLIC_TOOLS.items()]
                self.assertEqual(catalogue["result"]["tools"], expected_catalogue)
                self.assertTrue(all("outputSchema" not in item for item in expected_catalogue))
                close_description = by_name["close_task"]["description"]
                self.assertIn("open_clarification", close_description)
                self.assertIn("record_clarification", close_description)
                self.assertIn("immediate final answer", close_description)
                self.assertIn("byte-for-byte", close_description)
                self.assertTrue({
                    "publish_plan", "publish_result", "publish_documentation",
                }.issubset(set(names)))
                opened = call({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "open_task", "arguments": {"project_root": project, "request_original": "Conformance", "user_language": "en", "outcomes": [{"outcome": "Check the contract.", "acceptance": ["The contract is durable."], "constraints": [], "verification": ["Read the created task."]}], "constraints": ["No additional constraints."]}}})
                self.assertNotIn("error", opened)
                self.assertFalse(opened["result"].get("isError"), opened)
                task_ref = opened["result"]["structuredContent"]["task_ref"]
                narrowed = call({"jsonrpc": "2.0", "id": 31, "method": "tools/list", "params": {}})
                narrowed_names = {item["name"] for item in narrowed["result"]["tools"]}
                self.assertTrue(narrowed_names.isdisjoint({
                    "publish_plan", "publish_result", "publish_documentation",
                }))
                self.assertEqual(len(notifications), 1)
                read = call({"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "read_state", "arguments": {"task_ref": task_ref}}})
                self.assertNotIn("error", read)
                unreviewed_close = call({"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {"name": "close_task", "arguments": {"task_ref": task_ref, "verdict": "ready"}}})
                self.assertTrue(unreviewed_close["result"]["isError"])
                self.assertEqual(
                    unreviewed_close["result"]["structuredContent"]["error"]["code"],
                    "closure_review_required",
                )
                incomplete_result = call({"jsonrpc": "2.0", "id": 6, "method": "tools/call", "params": {"name": "publish_result", "arguments": {"task_ref": task_ref}}})
                self.assertTrue(incomplete_result["result"]["isError"])
                publication_error = incomplete_result["result"]["structuredContent"]["error"]
                self.assertEqual(publication_error["code"], "wrong_connection")
                self.assertNotIn("details", publication_error)
                missing_mode = call({"jsonrpc": "2.0", "id": 7, "method": "tools/call", "params": {"name": "assess_governance", "arguments": {"task_ref": task_ref}}})
                self.assertTrue(missing_mode["result"]["isError"])
                self.assertEqual(missing_mode["result"]["structuredContent"]["error"]["code"], "validation_error")
                assessed = call({"jsonrpc": "2.0", "id": 8, "method": "tools/call", "params": {"name": "assess_governance", "arguments": {"mode": "light"}}})
                self.assertNotIn("error", assessed)
                self.assertFalse(assessed["result"].get("isError"), assessed)
                self.assertEqual(assessed["result"]["structuredContent"]["task_ref"], task_ref)
            finally:
                if process.stdin is not None:
                    process.stdin.close()
                process.wait(timeout=5)
                if process.stdout is not None:
                    process.stdout.close()
                if process.stderr is not None:
                    process.stderr.close()

    def test_worker_candidate_accepts_only_assignment_paging_fields(self) -> None:
        schema = _worker_candidate_read_schema(PUBLIC_TOOLS["read_task"])
        worker_ref = "t_0123456789ab_" + "a" * 32
        _validate_schema(schema, {"task_ref": worker_ref})
        _validate_schema(schema, {"task_ref": worker_ref, "continue": True})
        with self.assertRaises(_SchemaError):
            _validate_schema(schema, {
                "task_ref": worker_ref,
                "report_policy": "latest_for_scope",
            })

    def test_catalogue_is_flat_task_ref_only(self) -> None:
        self.assertEqual(tuple(PUBLIC_TOOLS), EXPECTED_TOOLS)
        for name, contract in PUBLIC_TOOLS.items():
            for surface in ("inputSchema", "outputSchema", "runtimeOutputSchema"):
                names = set(property_names(contract[surface]))
                self.assertFalse(names & FORBIDDEN, (name, surface, names & FORBIDDEN))
                identifier_like = {item for item in names if item.endswith(("_ref", "_refs", "_id", "_ids"))}
                self.assertLessEqual(identifier_like, {"task_ref"})

    def test_old_handle_shapes_fail_at_schema_boundary(self) -> None:
        worker = "t_0123456789ab_" + "a" * 32
        base = {
            "task_ref": worker, "summary": "Done.", "outcome": "Done.", "changes": [],
            "verification_facts": [{"state": "executed", "summary": "Focused check passed."}],
            "outcome_coverage": [{"outcome": "Build.", "status": "complete", "verification": ["Passed."]}],
            "documentation_impact": "No documentation impact.", "risks": [], "unresolved": [], "status": "completed",
        }
        _validate_schema(PUBLIC_TOOLS["publish_result"]["inputSchema"], base)
        for field in FORBIDDEN - {"digest"}:
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "unsupported property"):
                _validate_schema(PUBLIC_TOOLS["publish_result"]["inputSchema"], {**base, field: "old"})

    def test_publications_are_separate_flat_closed_contracts(self) -> None:
        for name in ("publish_plan", "publish_result", "publish_documentation"):
            schema = PUBLIC_TOOLS[name]["inputSchema"]
            self.assertFalse(schema["additionalProperties"])
            self.assertNotIn("evidence", schema["properties"])
            self.assertIn("outcome_coverage", schema["properties"])
        self.assertIn("stages", PUBLIC_TOOLS["publish_plan"]["inputSchema"]["properties"])
        self.assertIn("changes", PUBLIC_TOOLS["publish_result"]["inputSchema"]["properties"])
        self.assertIn("findings", PUBLIC_TOOLS["publish_documentation"]["inputSchema"]["properties"])
        documentation = PUBLIC_TOOLS["publish_documentation"]
        self.assertIn("verification_facts", documentation["inputSchema"]["properties"])
        self.assertNotIn("verification_facts", documentation["inputSchema"]["required"])
        self.assertIn("without loss", documentation["description"])

    def test_publication_terminal_discriminators_precede_long_evidence(self) -> None:
        """Required short fields remain visible before host-compacted arrays."""
        expected_prefixes = {
            "publish_plan": ["task_ref", "status", "summary", "scope"],
            "publish_result": ["task_ref", "status", "summary", "outcome", "documentation_impact"],
            "publish_documentation": ["task_ref", "status", "summary", "documentation_impact"],
        }
        for name, prefix in expected_prefixes.items():
            with self.subTest(tool=name):
                schema = PUBLIC_TOOLS[name]["inputSchema"]
                self.assertEqual(list(schema["properties"])[:len(prefix)], prefix)
                self.assertEqual(schema["required"][:len(prefix)], prefix)

    def test_publish_plan_advertises_required_empty_evidence_arrays(self) -> None:
        contract = PUBLIC_TOOLS["publish_plan"]
        schema = contract["inputSchema"]
        self.assertIn("unresolved", schema["required"])
        self.assertIn("risks", schema["required"])
        self.assertIn("must be present", schema["properties"]["unresolved"]["description"])
        self.assertIn("empty array", schema["properties"]["unresolved"]["description"])
        self.assertIn("must be present", schema["properties"]["risks"]["description"])
        self.assertIn("empty array", schema["properties"]["risks"]["description"])
        self.assertIn("empty", contract["description"])

    def test_assignment_and_task_opening_are_flat(self) -> None:
        self.assertNotIn("task", PUBLIC_TOOLS["open_task"]["inputSchema"]["properties"])
        self.assertNotIn("mission", PUBLIC_TOOLS["open_assignment"]["inputSchema"]["properties"])
        self.assertIn("outcomes", PUBLIC_TOOLS["open_assignment"]["inputSchema"]["properties"])
        self.assertNotIn("outcomes", PUBLIC_TOOLS["open_assignment"]["inputSchema"]["required"])
        self.assertEqual(
            PUBLIC_TOOLS["open_assignment"]["inputSchema"]["properties"]["outcomes"]["items"]["type"],
            "string",
        )
        for name in ("publish_plan", "publish_result", "publish_documentation"):
            coverage = PUBLIC_TOOLS[name]["inputSchema"]["properties"]["outcome_coverage"]["items"]
            self.assertEqual(coverage["properties"]["outcome"]["type"], "string")

    def test_governance_assessment_advertises_coordinator_owned_explicit_depth(self) -> None:
        contract = PUBLIC_TOOLS["assess_governance"]
        description = contract["description"].lower()
        mode_description = contract["inputSchema"]["properties"]["mode"]["description"].lower()

        # The catalogue description must tell a worker that this is not a
        # worker-owned lifecycle operation, while telling the coordinator
        # what semantic choice is required.  The actual call shape remains
        # solely in the advertised schema.
        self.assertIn("coordinator-only", description)
        self.assertIn("semantic ownership", description)
        self.assertIn("explicit", description)
        self.assertIn("depth", description)
        self.assertIn("explicit coordinator depth selection", mode_description)

    def test_assignment_advertises_one_complete_instruction_field(self) -> None:
        contract = PUBLIC_TOOLS["open_assignment"]
        description = contract["description"].lower()
        instruction_description = contract["inputSchema"]["properties"]["instructions"]["description"].lower()

        self.assertIn("sole task-specific instruction field", description)
        self.assertIn("never invent supplementary fields", description)
        self.assertIn("complete task-specific worker instructions", instruction_description)
        self.assertIn("sole instruction channel", instruction_description)
        self.assertFalse(contract["inputSchema"]["additionalProperties"])

    def test_serialized_catalogue_contains_no_removed_callable_names(self) -> None:
        serialized = json.dumps({name: value["inputSchema"] for name, value in PUBLIC_TOOLS.items()}, sort_keys=True)
        for name in FORBIDDEN:
            self.assertNotIn(f'"{name}"', serialized)


if __name__ == "__main__":
    unittest.main()
