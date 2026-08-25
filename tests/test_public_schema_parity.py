"""Focused parity checks for the public Cortex MCP contracts.

These tests intentionally use only the stdlib unittest runner.  The runtime
normalizers are the executable contract; the public schema must not reject a
value that those normalizers explicitly document and accept.
"""

from __future__ import annotations

import copy
import inspect
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "plugins/cortex/scripts"))

import cortex


class PublicSchemaParityTests(unittest.TestCase):
    def test_simple_public_forms_match_runtime_reference_and_repair_facets(self) -> None:
        """Keep the six non-management forms aligned with executable preflight."""
        task_ref_pattern = r"^task-[0-9a-f]{12}$"
        for operation in ("continue_orchestration", "read_worker_result"):
            self.assertEqual(
                cortex.PUBLIC_SCHEMA_REGISTRY[operation]["properties"]["task_ref"]["pattern"],
                task_ref_pattern,
                operation,
            )

        briefing_cursor = cortex.PUBLIC_SCHEMA_REGISTRY["read_dispatch_briefing"]["properties"]["cursor"]
        self.assertEqual(briefing_cursor["minLength"], 1)

        submission = cortex.PUBLIC_SCHEMA_REGISTRY["complete_attempt"]
        patches = submission["properties"]["patches"]
        self.assertEqual(patches["minItems"], 1)
        self.assertEqual(
            patches["items"]["properties"]["op"]["enum"],
            ["add", "replace", "remove"],
        )

    def test_start_harvest_branch_uses_runtime_token_boundaries(self) -> None:
        task_schema = cortex.PUBLIC_SCHEMA_REGISTRY["start_orchestration"]["properties"]["task"]
        harvest_branch = task_schema["anyOf"][1]
        pattern = harvest_branch["properties"]["user_request"]["pattern"]

        for value in ("harvest", "HARVEST-REFRESH", "please harvest this repository"):
            with self.subTest(value=value):
                self.assertIsNotNone(re.search(pattern, value))
                self.assertTrue(cortex._is_knowledge_harvest_task({"user_request": value}))
        for value in ("reharvest", "harvesting", "harvested"):
            with self.subTest(value=value):
                self.assertIsNone(re.search(pattern, value))
                self.assertFalse(cortex._is_knowledge_harvest_task({"user_request": value}))

    def test_result_and_completion_unions_remain_closed_against_cross_branch_fields(self) -> None:
        result_schema = cortex.PUBLIC_SCHEMA_REGISTRY["read_worker_result"]
        coordinator, worker = result_schema["oneOf"]
        self.assertEqual(coordinator["required"], ["coordinator_ref", "step"])
        self.assertEqual(worker["required"], ["assignment_ref", "attempt_result_ref"])
        self.assertEqual(
            coordinator["not"]["anyOf"],
            [{"required": ["assignment_ref"]}, {"required": ["attempt_result_ref"]}],
        )
        self.assertEqual(
            worker["not"]["anyOf"],
            [{"required": ["coordinator_ref"]}, {"required": ["step"]}],
        )

        completion = cortex.PUBLIC_SCHEMA_REGISTRY["complete_attempt"]
        branches = completion["oneOf"]
        self.assertEqual(len(branches), 3)
        self.assertEqual(branches[0]["required"], ["task_ref", "assignment_ref", "plan"])
        self.assertEqual(branches[1]["required"], ["task_ref", "assignment_ref", "outcome"])
        self.assertEqual(
            branches[2]["required"],
            ["task_ref", "assignment_ref", "repair_capsule", "base_payload_digest", "patches"],
        )
        self.assertTrue(all("not" in branch for branch in branches))

    def test_worker_forms_require_explicit_assignment_capability(self) -> None:
        for operation in ("worker_question", "record_attempt_event", "complete_attempt", "read_dispatch_briefing"):
            schema = cortex.PUBLIC_SCHEMA_REGISTRY[operation]
            self.assertIn("task_ref", schema["required"], operation)
            self.assertIn("assignment_ref", schema["required"], operation)
            self.assertNotIn("profile", schema.get("properties", {}), operation)
        management = cortex.PUBLIC_SCHEMA_REGISTRY["manage_orchestration"]
        self.assertTrue(all("future_waves" not in branch["properties"] for branch in management["oneOf"]))

    def test_worker_question_poll_schema_matches_the_exact_scalar_resume_contract(self) -> None:
        """The Desktop resume call must be accepted by the advertised MCP shape."""
        schema = cortex.PUBLIC_SCHEMA_REGISTRY["worker_question"]
        self.assertEqual(schema["properties"]["action"]["enum"], ["ask", "poll", "ask_batch", "poll_batch"])
        self.assertIn("task_ref", schema["required"])
        self.assertIn("assignment_ref", schema["required"])
        self.assertEqual(schema["properties"]["question_ref"]["pattern"], "^question-[0-9]+$")
        branches = schema["allOf"][0]["oneOf"]
        self.assertEqual(
            [branch["properties"]["action"]["const"] for branch in branches],
            ["ask", "poll", "ask_batch", "poll_batch"],
        )
        poll = branches[1]
        self.assertEqual(
            poll["required"],
            ["task_ref", "assignment_ref", "action", "question_ref"],
        )
        self.assertEqual(set(poll["properties"]), {"task_ref", "assignment_ref", "action", "question_ref"})
        self.assertFalse(poll["additionalProperties"])
        self.assertIn("literal string 'poll'", schema["description"])
        self.assertEqual(schema["properties"]["question_type"]["enum"], ["single_select", "multi_select", "text"])
        self.assertNotIn("context", schema["properties"])
        self.assertNotIn("multiple", schema["properties"])
        option = schema["properties"]["options"]["items"]
        self.assertIn("option_id", option["required"])
        self.assertIn("Stable option identifier", option["properties"]["option_id"]["description"])

    def test_worker_scope_schema_matches_non_broad_runtime_validator(self) -> None:
        item_schema = (
            cortex.PUBLIC_SCHEMA_REGISTRY["start_orchestration"]["properties"]["waves"]
            ["items"]["properties"]["workers"]["items"]["properties"]["allowed_paths"]["items"]
        )
        self.assertEqual(item_schema["format"], "project-relative-path")
        self.assertRegex("docs/current file.md", item_schema["pattern"])
        for unsafe in ("", " /absolute", "/absolute", "../escape", "folder\\escape", "*", ".", "trailing ", "bad\x00path"):
            with self.subTest(unsafe=unsafe):
                self.assertNotRegex(unsafe, item_schema["pattern"])
        allowed_paths_schema = (
            cortex.PUBLIC_SCHEMA_REGISTRY["start_orchestration"]["properties"]["waves"]
            ["items"]["properties"]["workers"]["items"]["properties"]["allowed_paths"]
        )
        self.assertEqual(allowed_paths_schema["minItems"], 1)

        microtask_item = (
            cortex.PUBLIC_SCHEMA_REGISTRY["complete_attempt"]["properties"]["plan"]
            ["properties"]["work_packages"]["items"]["properties"]["microtasks"]
            ["items"]["properties"]["allowed_paths"]["items"]
        )
        self.assertEqual(microtask_item, {"type": "string", "minLength": 1, "maxLength": 512})

    def test_start_preflight_keys_and_field_cards_are_generated_schema_views(self) -> None:
        start, task, waves, wave, worker = cortex._v11_start_public_schema_forms()
        canonical = cortex.PUBLIC_SCHEMA_REGISTRY["start_orchestration"]
        self.assertIs(start, canonical)
        self.assertIs(task, canonical["properties"]["task"])
        self.assertIs(waves, canonical["properties"]["waves"])
        self.assertIs(wave, waves["items"])
        self.assertIs(worker, wave["properties"]["workers"]["items"])

        allowed_schema = worker["properties"]["allowed_paths"]
        item_schema = allowed_schema["items"]
        diagnostics = cortex._v11_start_wave_preflight([{"phase": "implementation", "workers": [{
            "allowed_paths": [],
            "obsolete_worker_field": True,
        }, {
            "allowed_paths": ["../unsafe"],
        }], "obsolete_wave_field": True}])
        by_pointer = {item["json_pointer"]: item for item in diagnostics}
        self.assertEqual(
            by_pointer["/waves/0/workers/0/allowed_paths"]["field_schema"],
            cortex._v11_schema_field_card(allowed_schema, "type", "minItems"),
        )
        self.assertEqual(
            by_pointer["/waves/0/workers/1/allowed_paths/0"]["field_schema"],
            cortex._v11_schema_field_card(item_schema, "type", "minLength", "pattern", "format"),
        )
        self.assertEqual(
            by_pointer["/waves/0/workers/0/obsolete_worker_field"]["field_schema"]["properties"],
            sorted(worker["properties"]),
        )
        self.assertEqual(
            by_pointer["/waves/0/obsolete_wave_field"]["field_schema"]["properties"],
            sorted(wave["properties"]),
        )

        task_probe = {
            "user_request": "Reject only unknown fields.",
            "acceptance_criteria": ["The request is unchanged."],
            "verification": ["Inspect exact pointers."],
            "obsolete_task_field": {"nested": True},
        }
        task_before = copy.deepcopy(task_probe)
        task_diagnostics = cortex._v11_start_task_preflight(task_probe)
        self.assertEqual(task_probe, task_before)
        self.assertEqual(
            [item["json_pointer"] for item in task_diagnostics],
            ["/task/obsolete_task_field"],
        )
        top_probe = {"project_root": "/project", "unexpected/top": True}
        top_before = copy.deepcopy(top_probe)
        top_diagnostics = cortex._v11_collect_fields(
            top_probe,
            set(start["properties"]),
            operation="start_orchestration",
            public_schema=start,
        )
        self.assertEqual(top_probe, top_before)
        self.assertEqual(
            [item["json_pointer"] for item in top_diagnostics],
            ["/unexpected~1top"],
        )

    def test_start_preflight_acceptance_and_cards_follow_the_live_public_registry(self) -> None:
        _start, task, _waves, wave, worker = cortex._v11_start_public_schema_forms()
        task_properties = task["properties"]
        wave_properties = wave["properties"]
        worker_properties = worker["properties"]
        allowed_paths = worker_properties["allowed_paths"]
        sentinel_task = "schema_derived_task_text"
        sentinel_wave = "schema_derived_wave"
        sentinel_worker = "schema_derived_worker"
        previous_min_items = allowed_paths.get("minItems")
        task_properties[sentinel_task] = {"type": "array", "minItems": 2, "items": {"type": "string", "minLength": 3}}
        wave_properties[sentinel_wave] = {"type": "string"}
        worker_properties[sentinel_worker] = {"type": "boolean"}
        allowed_paths["minItems"] = 2
        try:
            task_diagnostics = cortex._v11_start_task_preflight({
                "user_request": "Schema-derived preflight.",
                "acceptance_criteria": ["The schema remains authoritative."],
                "verification": ["Run focused tests."],
                sentinel_task: 7,
            })
            task_by_pointer = {item["json_pointer"]: item for item in task_diagnostics}
            self.assertEqual(
                task_by_pointer[f"/task/{sentinel_task}"]["field_schema"],
                {"type": "array", "minItems": 2},
            )
            minimum_pointers = {
                item["json_pointer"] for item in cortex._v11_start_task_preflight({
                    "user_request": "Schema-derived preflight.",
                    "acceptance_criteria": ["The schema remains authoritative."],
                    "verification": ["Run focused tests."],
                    sentinel_task: ["valid text"],
                })
            }
            self.assertIn(f"/task/{sentinel_task}", minimum_pointers)

            wave_diagnostics = cortex._v11_start_wave_preflight([{
                "phase": "implementation",
                sentinel_wave: "accepted",
                "workers": [{sentinel_worker: True, "allowed_paths": []}],
            }])
            wave_by_pointer = {item["json_pointer"]: item for item in wave_diagnostics}
            self.assertNotIn(f"/waves/0/{sentinel_wave}", wave_by_pointer)
            self.assertNotIn(f"/waves/0/workers/0/{sentinel_worker}", wave_by_pointer)
            self.assertEqual(
                wave_by_pointer["/waves/0/workers/0/allowed_paths"]["field_schema"],
                {"type": "array", "minItems": 2},
            )
        finally:
            task_properties.pop(sentinel_task, None)
            wave_properties.pop(sentinel_wave, None)
            worker_properties.pop(sentinel_worker, None)
            if previous_min_items is None:
                allowed_paths.pop("minItems", None)
            else:
                allowed_paths["minItems"] = previous_min_items

        task_preflight_source = inspect.getsource(cortex._v11_start_task_preflight)
        self.assertNotIn('(\"requirements\", \"constraints\"', task_preflight_source)
        self.assertNotIn('(\"acceptance_criteria\", \"verification\")', task_preflight_source)

    def test_task_scope_text_arrays_advertise_non_empty_items(self) -> None:
        task_properties = cortex.PUBLIC_SCHEMA_REGISTRY["start_orchestration"]["properties"]["task"]["properties"]
        for field in ("requirements", "constraints", "scope", "allowed_paths", "pause_conditions"):
            self.assertEqual(task_properties[field]["items"], {"type": "string", "minLength": 1}, field)

    def test_runtime_accepts_only_canonical_profile_and_phase_forms(self) -> None:
        result = cortex._v11_compact_waves(
            [{"phase": "qa", "workers": [{"profile": "qa_engineer"}]}],
            {"user_request": "canonical parity", "complexity": "C1"},
        )
        self.assertEqual(result[0]["delegations"][0]["agent"], "qa_engineer")
        for phase, profile in (("verification", "qa_engineer"), ("discover", "discovery"), ("plan", "planner_agent")):
            with self.subTest(phase=phase, profile=profile), self.assertRaises(ValueError):
                cortex._v11_compact_waves(
                    [{"phase": phase, "workers": [{"profile": profile}]}],
                    {"user_request": "canonical parity", "complexity": "C1"},
                )

    def test_start_wave_schema_exposes_one_phase_and_closed_worker_profile_pairs(self) -> None:
        wave = cortex.PUBLIC_SCHEMA_REGISTRY["start_orchestration"]["properties"]["waves"]["items"]
        worker = wave["properties"]["workers"]["items"]
        self.assertEqual(wave["required"], ["phase", "workers"])
        self.assertNotIn("phase", worker["properties"])
        self.assertNotIn("enum", worker["properties"]["profile"])
        branches = {
            branch["properties"]["phase"]["const"]: branch
            for branch in wave["oneOf"]
        }
        def profiles(phase: str) -> list[str]:
            return branches[phase]["properties"]["workers"]["items"]["properties"]["profile"]["enum"]
        self.assertIn("general", profiles("implementation"))
        self.assertIn("qa_engineer", profiles("qa"))
        self.assertNotIn("general", profiles("qa"))
        self.assertEqual(
            cortex._v11_start_wave_preflight([
                {"phase": "implementation", "workers": [{"profile": "general"}]},
            ]),
            [],
        )
        inherited = cortex._v11_compact_waves([
            {"phase": "implementation", "workers": [
                {"profile": "general", "objective": "First owner."},
                {"profile": "general", "objective": "Second owner."},
            ]},
            {"phase": "qa", "workers": [{"profile": "qa_engineer"}]},
        ], {"user_request": "wave inheritance", "complexity": "C1"})
        self.assertEqual(
            [item["gate"] for wave_item in inherited for item in wave_item["delegations"]],
            ["implementation", "implementation", "qa"],
        )

        diagnostics = cortex._v11_start_wave_preflight([
            {"phase": "qa", "workers": [{"profile": "general"}]},
        ])
        self.assertEqual(len(diagnostics), 1)
        self.assertEqual(diagnostics[0]["json_pointer"], "/waves/0/workers/0/profile")
        self.assertEqual(
            diagnostics[0]["field_schema"],
            {"type": "string", "enum": ["build_verification", "qa_engineer"]},
        )

        legacy = cortex._v11_start_wave_preflight([
            {"phase": "implementation", "workers": [{"phase": "implementation", "profile": "general"}]},
        ])
        self.assertEqual(len(legacy), 1)
        self.assertEqual(legacy[0]["json_pointer"], "/waves/0/workers/0/phase")
        self.assertFalse(legacy[0].get("state_mutated", False))

    def test_management_validation_receipt_uses_the_complete_public_schema(self) -> None:
        receipt = cortex._validation_contract("manage_orchestration", [{
            "json_pointer": "/intent", "path": "intent",
        }])
        advertised = cortex.PUBLIC_SCHEMA_REGISTRY["manage_orchestration"]
        self.assertEqual(receipt["request_schema"], {"tool": "manage_orchestration", **advertised})
        branches = {
            item["properties"]["intent"]["const"]: item
            for item in advertised["oneOf"]
        }
        self.assertIn("finalize_bootstrap_failure", branches)
        finalizer = branches["finalize_bootstrap_failure"]
        self.assertEqual(finalizer["properties"]["payload"]["properties"]["reason_code"], {"const": "bootstrap_missing_identity"})

    def test_management_schema_is_the_tools_list_runtime_authority(self) -> None:
        schema = cortex.PUBLIC_SCHEMA_REGISTRY["manage_orchestration"]
        self.assertIs(cortex.MANAGE_ORCHESTRATION_SCHEMA, schema)
        self.assertIs(cortex.TOOLS["manage_orchestration"][1], schema)
        self.assertIs(cortex.PUBLIC_TOOLS["manage_orchestration"][1], schema)
        schema_intents = {
            branch["properties"]["intent"]["const"]
            for branch in schema["oneOf"]
        }
        runtime_source = inspect.getsource(cortex._manage_orchestration_impl)
        self.assertIn('management_schema.get("oneOf", [])', runtime_source)
        self.assertEqual(schema_intents, {
            "inspect", "recover_inspect", "recover_blocked", "resume", "deactivate",
            "lane", "resource", "question", "plan_approval", "follow_up", "steer",
            "artifacts", "finalize_bootstrap_failure", "finalize_worker_failure",
        })

    def test_management_discriminated_union_valid_matrix(self) -> None:
        task_ref = "task-aaaaaaaaaaaa"
        coordinator_ref = "b" * 64
        envelope = {"task_ref": task_ref, "coordinator_ref": coordinator_ref}
        valid = [
            {**envelope, "intent": "inspect"},
            {**envelope, "intent": "recover_inspect"},
            {**envelope, "intent": "recover_blocked", "reason": "Retry the server-derived corrective route."},
            {**envelope, "intent": "resume"},
            {**envelope, "intent": "deactivate", "reason": "The user ended Cortex."},
            *[
                {**envelope, "intent": "lane", "payload": payload}
                for payload in (
                    {"command": "create", "lane_id": "lane-a"},
                    {"command": "inspect", "lane_id": "lane-a"},
                    {"command": "claim", "lane_id": "lane-a", "expires_at": "2030-01-01T00:00:00+00:00"},
                    {"command": "release", "lane_id": "lane-a"},
                    {"command": "retire", "lane_id": "lane-a", "clean": True},
                    {"command": "bind_task", "lane_id": "lane-a"},
                    {"command": "materialize", "lane_id": "lane-a", "confirm": True},
                    {"command": "reconcile", "lane_id": "lane-a"},
                    {"command": "claim_resource", "lane_id": "lane-a", "path": "src", "owner": "worker", "expires_at": "2030-01-01T00:00:00+00:00"},
                    {"command": "release_resource", "lane_id": "lane-a", "path": "src", "owner": "worker"},
                )
            ],
            *[
                {**envelope, "intent": "resource", "payload": payload}
                for payload in (
                    {"command": "claim", "path": "src", "owner": "worker"},
                    {"command": "release", "path": "src", "owner": "worker"},
                    {"command": "acquire_lock", "path": "src", "owner": "worker"},
                    {"command": "release_lock", "path": "src", "owner": "worker"},
                )
            ],
            *[
                {**envelope, "intent": "question", "payload": payload}
                for payload in (
                    {"question_ref": "question-one"},
                    {"question_ref": "question-one", "localized_question": "Вопрос?"},
                    {"question_ref": "question-one", "answer": {"option_ids": ["safe"]}},
                    {"question_ref": "question-one", "answer": "Да", "answer_en": "Yes"},
                    {"question_ref": "batch-one", "answers": {"first": "safe"}},
                    {"question_ref": "batch-one", "canonical_answers": {"first": "Safe"}},
                )
            ],
            *[
                {**envelope, "intent": "plan_approval", "payload": payload}
                for payload in (
                    {"decision": "prompt"},
                    {"decision": "prompt", "localized_prompt": "Проверить?", "localized_title": "План", "localized_approve": "Да", "localized_cancel": "Нет", "localized_custom_label": "Изменения"},
                    {"decision": "approve_with_recommendations", "request_id": "plan-1"},
                    {"decision": "approve_without_recommendations", "request_id": "plan-1"},
                    {"decision": "cancel", "request_id": "plan-1"},
                    {"decision": "revise", "request_id": "plan-1", "feedback": "Split the rollout."},
                )
            ],
            {**envelope, "intent": "follow_up", "payload": {
                "user_request": "Apply the corrective change.",
                "acceptance_criteria": ["The correction is complete."],
                "verification": ["Run the focused regression."],
            }},
            {**envelope, "intent": "steer", "payload": {"user_message": "Keep the public API stable."}},
            *[
                {**envelope, "intent": "artifacts", "payload": payload}
                for payload in (
                    {"action": "list"},
                    {"action": "metadata", "artifact_ref": "artifact-one"},
                    {"action": "read", "artifact_ref": "artifact-one", "max_bytes": 4096},
                )
            ],
            {**envelope, "intent": "finalize_bootstrap_failure", "payload": {"dispatch_ref": "dispatch-" + "a" * 24, "reason_code": "bootstrap_missing_identity"}},
            {**envelope, "intent": "finalize_worker_failure", "payload": {"dispatch_ref": "dispatch-" + "a" * 24, "reason_code": "worker_nonretryable_terminal"}},
        ]
        for index, request in enumerate(valid):
            with self.subTest(index=index, intent=request["intent"], payload=request.get("payload")):
                self.assertEqual(cortex._manage_orchestration_input_diagnostics(request), [])

    def test_management_union_rejects_missing_fields_and_cross_branch_contamination(self) -> None:
        envelope = {"task_ref": "task-aaaaaaaaaaaa", "coordinator_ref": "b" * 64}
        invalid = [
            ({**envelope, "intent": "inspect", "payload": {}}, "/payload"),
            ({**envelope, "intent": "lane", "payload": {"command": "claim", "lane_id": "lane-a", "path": "src"}}, "/payload/path"),
            ({**envelope, "intent": "resource", "payload": {"command": "claim", "path": "src"}}, "/payload/owner"),
            ({**envelope, "intent": "question", "payload": {"question_ref": "question-one", "decision": "prompt"}}, "/payload/decision"),
            ({**envelope, "intent": "plan_approval", "payload": {"decision": "revise", "request_id": "plan-1"}}, "/payload/feedback"),
            ({**envelope, "intent": "artifacts", "payload": {"action": "metadata", "artifact_ref": "artifact-one", "max_bytes": 5}}, "/payload/max_bytes"),
            ({**envelope, "intent": "finalize_worker_failure", "reason": "caller prose", "payload": {"dispatch_ref": "dispatch-" + "a" * 24, "reason_code": "worker_nonretryable_terminal"}}, "/reason"),
        ]
        for request, pointer in invalid:
            with self.subTest(intent=request["intent"], pointer=pointer):
                diagnostics = cortex._manage_orchestration_input_diagnostics(request)
                self.assertIn(pointer, {item["json_pointer"] for item in diagnostics})

    def test_management_union_requires_explicit_capability_and_rejects_ambient_identity(self) -> None:
        request = {
            "intent": "question",
            "task_ref": "task-aaaaaaaaaaaa",
            "project_root": "/guessed/project",
            "payload": {"question_ref": "question-one", "principal": "guessed"},
        }
        pointers = {
            item["json_pointer"]
            for item in cortex._manage_orchestration_input_diagnostics(request)
        }
        self.assertTrue({"/coordinator_ref", "/project_root", "/payload/principal"}.issubset(pointers))

    def test_coordinator_preflights_aggregate_schema_leaves_before_authorization(self) -> None:
        management = cortex._manage_orchestration_input_diagnostics({
            "intent": "finalize_bootstrap_failure", "task_ref": "", "coordinator_ref": "bad",
            "project_root": "/forbidden", "payload": {"dispatch_ref": "bad", "reason_code": "bad", "extra": True},
        })
        self.assertEqual(
            {item["json_pointer"] for item in management},
            {"/task_ref", "/coordinator_ref", "/project_root", "/payload/dispatch_ref", "/payload/reason_code", "/payload/extra"},
        )
        governance = cortex._manage_governance_input_diagnostics({
            "action": "add_dependency", "task_ref": "bad", "coordinator_ref": "bad",
            "source_type": "not-a-type", "target_type": 3, "corrective": "false", "limit": 0,
        })
        pointers = {item["json_pointer"] for item in governance}
        self.assertTrue({"/source_type", "/target_type", "/corrective", "/limit", "/source_ref", "/target_ref", "/dependency_type"}.issubset(pointers))


if __name__ == "__main__":
    unittest.main()
