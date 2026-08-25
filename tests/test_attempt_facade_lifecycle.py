"""Focused public-facade and native-stop regression tests.

These tests deliberately exercise the seams where a worker's semantic work is
already durable but server finalization is not. A failed materialization is
retryable finalization work, never evidence that the worker must be failed or replaced.
"""
from __future__ import annotations

from contextlib import ExitStack, nullcontext
import tempfile
import unittest
from pathlib import Path
from unittest import mock


SCRIPTS = Path(__file__).parents[1] / "plugins" / "cortex" / "scripts"
import sys

if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import cortex
from cortex_runtime import attempt_facade, attempt_protocol, context_handoff, ledger_db


class AttemptFacadeLifecycleTests(unittest.TestCase):
    """Protect the public lifecycle from finalization failure paths."""

    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory(prefix="cortex-attempt-facade-")
        self.project = Path(self._temporary.name) / "project"
        self.project.mkdir()
        # Do not inherit a process-level CORTEX_ROOT from another test run:
        # this contract owns its isolated SQLite ledger explicitly.
        self.root = self.project / ".attempt-facade-ledger"
        ledger_db.ensure_database(self.root)
        self.task_id = "facade-lifecycle-task"
        self.attempt_id = "implementation-01"
        self.attempt = {
            "attempt_id": self.attempt_id,
            "status": "running",
            "gate": "implementation",
            "profile": "backend_dev",
            "agent": "backend_dev",
            "dispatch_ref": "dispatch-implementation-01",
            "briefing_digest": "a" * 64,
            "briefing_artifact_ref": "artifact-briefing",
            "result_baseline_ref": "manifest-implementation-01",
            "result_baseline_digest": "b" * 64,
            "allowed_paths": ["."],
            "context_result_refs": [],
        }
        self.state = {
            "pipeline_contract_version": 2,
            "task_id": self.task_id,
            "task_number": 1,
            "principal": "coordinator",
            "thread_id": "coordinator",
            "revision": 3,
            "attempts": [self.attempt],
        }
        ledger_db.create_task(
            self.root,
            {
                "task_id": self.task_id,
                "task_number": 1,
                "project_root": str(self.project),
            },
            self.state,
            "tasks/0001-facade-lifecycle-task",
        )
        attempt_protocol.acknowledge_briefing(
            self.root,
            task_id=self.task_id,
            attempt_id=self.attempt_id,
            dispatch_ref="dispatch-implementation-01",
            digest="a" * 64,
        )
        cortex._governance_lifecycle_hmac_key(self.root, create=True)
        self.params = {
            "project_root": str(self.project),
            "task_id": self.task_id,
            "attempt_id": self.attempt_id,
            "profile": "backend_dev",
            "status": "completed",
            "summary": "Implemented the bounded server-owned completion transition.",
            "findings": [{"summary": "Semantic result was persisted before projection."}],
            "decisions_needed": [],
            "unresolved": [],
            "claims": [],
        }

    def tearDown(self) -> None:
        self._temporary.cleanup()

    def _facade_patches(self, generated_view, *, attempt=None, profile="backend_dev"):
        """Supply host facts while keeping AttemptResult persistence real."""
        observation = {
            "baseline_ref": "manifest-implementation-01",
            "baseline_digest_sha256": "b" * 64,
            "current_digest_sha256": "c" * 64,
            "complete": True,
            "safe_to_attribute": True,
            "changed_files": ["src/owned.py"],
        }
        patches = ExitStack()
        patches.enter_context(mock.patch.object(
            attempt_facade._runtime, "ledger_root", return_value=self.root,
        ))
        patches.enter_context(mock.patch.multiple(
            attempt_facade,
            _worker_context=mock.Mock(return_value=(
                self.project, self.project, self.state, attempt or self.attempt, profile,
            )),
            _receipt_guard=mock.Mock(return_value={}),
            _workspace_observation=mock.Mock(return_value=observation),
            _mark_attempt=mock.Mock(),
        ))
        patches.enter_context(mock.patch.object(
            attempt_protocol, "build_attempt_result_view", generated_view,
        ))
        return patches

    @staticmethod
    def _planner_plan() -> dict[str, object]:
        return {
            "overview": "Create and verify the exact requested file in the implementation wave.",
            "work_packages": [{
                "id": "file-change", "title": "File change", "objective": "Create and verify the target.",
                "allowed_paths": ["desktop-v11-multi-wave.txt"],
                "microtasks": [{
                    "id": "write-file", "title": "Write file", "objective": "Write exact bytes.",
                    "profile": "backend_dev", "allowed_paths": ["desktop-v11-multi-wave.txt"],
                    "acceptance_criteria": ["The file has the exact requested bytes."],
                    "verification": ["Compare bytes and final newline count."],
                }],
            }],
            "recommendation": "revise",
            "recommendation_actions": [],
            "risks": ["The implementation must preserve exactly one final newline."],
        }

    def _complete(self, params):
        """Submit semantic completion through explicit v11 assignment authority."""
        return attempt_facade._complete_attempt_impl({
            "task_ref": "task-000000000001",
            "assignment_ref": "assignment-v1-" + "a" * 64,
            "outcome": {
                "status": params["status"],
                "summary": params["summary"],
                "findings": params.get("findings", []),
                "decisions_needed": params.get("decisions_needed", []),
                "unresolved": params.get("unresolved", []),
                "claims": params.get("claims", []),
            },
        })

    def _public_complete(self, submission, generated_view=None):
        view = generated_view or mock.Mock(return_value={"projection_ref": "attempt-result-view-implementation-01"})
        with self._facade_patches(view):
            return attempt_facade.complete_attempt(submission)

    def test_private_repair_escrow_survives_reopen_reuses_handle_and_replays_success(self) -> None:
        refs = {
            "task_ref": "task-000000000001",
            "assignment_ref": "assignment-v1-" + "a" * 64,
        }
        rejected = {
            **refs,
            "outcome": {
                "status": "completed", "summary": "",
                "findings": [{"summary": "Preserve this valid finding."}],
            },
        }
        first = self._public_complete(rejected)
        repeated = self._public_complete(rejected)
        self.assertFalse(first["ok"])
        self.assertEqual(first["recovery"]["repair"], repeated["recovery"]["repair"])
        repair = first["recovery"]["repair"]
        self.assertEqual(
            len(repair["repair_capsule"]),
            attempt_facade.v11_submission.REPAIR_HANDLE_LENGTH,
        )
        self.assertFalse(first["recovery"]["state_mutated"])
        self.assertEqual(repair["patch_paths"], ["/summary"])
        self.assertEqual(first["recovery"]["kind"], "repair_patch_only")
        self.assertEqual(repair["diagnostics"][0]["repair_pointer"], "/summary")

        handle_id = repair["repair_capsule"].split(".")[1]
        ledger_db._forget_database_readiness(self.root)
        row = ledger_db.get_repair_escrow(
            self.root,
            handle_digest=attempt_facade.v11_submission.repair_handle_digest(handle_id),
        )
        self.assertIsNotNone(row)
        assert row is not None
        self.assertEqual(row["payload"]["findings"], [{"summary": "Preserve this valid finding."}])
        with ledger_db._connection(self.root) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM repair_escrow").fetchone()[0], 1)

        repaired_submission = {
            **refs,
            "repair_capsule": repair["repair_capsule"],
            "base_payload_digest": repair["base_payload_digest"],
            "patches": [{"op": "replace", "path": "/summary", "value": "Completed after exact repair."}],
        }
        completed = self._public_complete(repaired_submission)
        replayed = self._public_complete(repaired_submission)
        self.assertEqual(completed, replayed)
        self.assertEqual(set(completed), {"schema", "ok", "terminal"})
        self.assertTrue(completed["ok"])
        self.assertTrue(completed["terminal"])

    def test_live_planner_repair_stays_locked_across_four_bad_calls_and_recovers(self) -> None:
        refs = {
            "task_ref": "task-000000000001",
            "assignment_ref": "assignment-v1-" + "a" * 64,
        }
        planner_attempt = {
            **self.attempt,
            "attempt_id": self.attempt_id,
            "gate": "plan",
            "profile": "planner",
            "agent": "planner",
            "allowed_paths": ["desktop-v11-multi-wave.txt"],
        }
        view = mock.Mock(return_value={"projection_ref": "attempt-result-view-plan-01"})
        with self._facade_patches(view, attempt=planner_attempt, profile="planner"), mock.patch.object(
            attempt_facade._runtime, "materialize_planning_payload",
        ):
            wrong_branch = attempt_facade.complete_attempt({
                **refs,
                "outcome": {"status": "completed", "summary": "Wrong planner branch."},
            })
            self.assertFalse(wrong_branch["ok"])
            self.assertTrue(wrong_branch["recovery"]["retryable"])
            self.assertEqual(wrong_branch["error"]["diagnostics"][0]["json_pointer"], "/plan")

            rejected_plan = self._planner_plan()
            issued = attempt_facade.complete_attempt({**refs, "plan": rejected_plan})
            repair = issued["recovery"]["repair"]
            self.assertEqual(repair["patch_paths"], ["/recommendation_actions"])
            self.assertEqual(issued["recovery"]["kind"], "repair_patch_only")
            self.assertTrue(issued["recovery"]["retryable"])

            malformed_copy = attempt_facade.complete_attempt({
                **refs,
                "repair_capsule": repair["repair_capsule"][:-5],
                "base_payload_digest": repair["base_payload_digest"],
                "patches": [{
                    "op": "replace", "path": "/recommendation_actions", "value": [],
                }],
            })
            self.assertEqual(malformed_copy["recovery"]["repair"], repair)
            self.assertTrue(malformed_copy["recovery"]["retryable"])

            regenerated = self._planner_plan()
            regenerated["risks"] = [{"summary": "A newly introduced, schema-invalid risk."}]
            full_resubmit = attempt_facade.complete_attempt({**refs, "plan": regenerated})
            self.assertEqual(full_resubmit["recovery"]["repair"], repair)

            out_of_scope = attempt_facade.complete_attempt({
                **refs,
                "repair_capsule": repair["repair_capsule"],
                "base_payload_digest": repair["base_payload_digest"],
                "patches": [{"op": "replace", "path": "/risks", "value": ["changed"]}],
            })
            self.assertEqual(out_of_scope["recovery"]["repair"], repair)
            self.assertTrue(out_of_scope["recovery"]["retryable"])

            for label, patches in {
                "empty": [],
                "wrong_op": [{
                    "op": "copy", "path": "/recommendation_actions", "value": [],
                }],
                "wrong_value": [{
                    "op": "replace", "path": "/recommendation_actions", "value": ["not an action object"],
                }],
            }.items():
                with self.subTest(repair_retry=label):
                    retry = attempt_facade.complete_attempt({
                        **refs,
                        "repair_capsule": repair["repair_capsule"],
                        "base_payload_digest": repair["base_payload_digest"],
                        "patches": patches,
                    })
                    self.assertEqual(retry["recovery"]["repair"], repair)

            valid = {
                **refs,
                "repair_capsule": repair["repair_capsule"],
                "base_payload_digest": repair["base_payload_digest"],
                "patches": [{
                    "op": "replace",
                    "path": "/recommendation_actions",
                    "value": [{
                        "issue": "The exact-byte implementation must be explicit.",
                        "action": "Create the target with the requested bytes and one newline.",
                        "plan_refs": ["write-file"],
                        "verification": "Compare the complete byte sequence.",
                    }],
                }],
            }
            completed = attempt_facade.complete_attempt(valid)
            replayed = attempt_facade.complete_attempt(valid)

        self.assertEqual(completed, {"schema": "cortex/worker-completion/v11", "ok": True, "terminal": True})
        self.assertEqual(replayed, completed)
        with ledger_db._connection(self.root) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM repair_escrow").fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM attempt_results").fetchone()[0], 1)

    def test_multi_error_planner_repairs_from_public_diagnostics_without_schema_lookup(self) -> None:
        refs = {
            "task_ref": "task-000000000001",
            "assignment_ref": "assignment-v1-" + "a" * 64,
        }
        planner_attempt = {
            **self.attempt,
            "gate": "plan",
            "profile": "planner",
            "agent": "planner",
            "allowed_paths": ["desktop-v11-multi-wave.txt"],
        }
        invalid_plan = {
            "overview": "Create, implement, and review the exact requested artifact.",
            "work_packages": [
                {
                    "id": "plan", "title": "Plan", "objective": "Plan the work.",
                    "microtasks": [{
                        "id": "plan-one", "objective": "Define the implementation.",
                        "profile": "planner", "allowed_paths": ["desktop-v11-multi-wave.txt"],
                        "acceptance_criteria": ["The plan is complete."],
                        "dependencies": [],
                    }],
                },
                {
                    "id": "implement", "title": "Implement", "objective": "Create the artifact.",
                    "microtasks": [{
                        "id": "write-one", "objective": "Write exact bytes.",
                        "profile": "backend_dev", "allowed_paths": ["desktop-v11-multi-wave.txt"],
                        "acceptance_criteria": ["Exact bytes are present."],
                        "verification": ["Compare exact bytes."],
                        "dependencies": ["plan-one"],
                    }],
                },
            ],
            "requirement_coverage": [
                {"requirement": "Create the artifact.", "plan_refs": ["write-one"], "verification": "Compare bytes."},
                {"requirement": "Review the artifact.", "plan_refs": ["write-one"], "verification": "Review bytes."},
            ],
            "risks": [{"summary": "This must be a string."}],
        }

        def value_from(card):
            schema = card["field_schema"]
            if "const" in schema:
                return schema["const"]
            if schema.get("enum"):
                return schema["enum"][0]
            if schema.get("type") == "string":
                return "valid"
            if schema.get("type") == "array":
                count = max(1, int(schema.get("minItems", 0)))
                return [value_from({"field_schema": schema.get("items", {"type": "string"})}) for _ in range(count)]
            if schema.get("type") == "object":
                properties = schema.get("properties", {})
                return {
                    name: value_from({"field_schema": properties[name]})
                    for name in schema.get("required", [])
                    if name in properties
                }
            if schema.get("type") == "integer":
                return int(schema.get("minimum", 0))
            if schema.get("type") == "boolean":
                return True
            raise AssertionError(f"public diagnostic is not self-contained: {card!r}")

        with self._facade_patches(
            mock.Mock(return_value={"projection_ref": "attempt-result-view-plan-01"}),
            attempt=planner_attempt,
            profile="planner",
        ), mock.patch.object(attempt_facade._runtime, "materialize_planning_payload"):
            issued = attempt_facade.complete_attempt({**refs, "plan": invalid_plan})
            repair = issued["recovery"]["repair"]
            self.assertEqual(len(repair["diagnostics"]), 12)
            self.assertEqual(
                repair["patch_paths"],
                [card["repair_pointer"] for card in repair["diagnostics"]],
            )
            self.assertTrue(all(
                set(card) == {"code", "json_pointer", "repair_pointer", "message", "field_schema", "allowed_ops"}
                for card in repair["diagnostics"]
            ))
            self.assertTrue(all(card["allowed_ops"] for card in repair["diagnostics"]))
            patches = []
            for card in repair["diagnostics"]:
                if card["code"] == "validation_unknown":
                    patches.append({"op": "remove", "path": card["repair_pointer"]})
                else:
                    patches.append({
                        "op": "add" if card["code"] == "validation_required" else "replace",
                        "path": card["repair_pointer"],
                        "value": value_from(card),
                    })
            completed = attempt_facade.complete_attempt({
                **refs,
                "repair_capsule": repair["repair_capsule"],
                "base_payload_digest": repair["base_payload_digest"],
                "patches": patches,
            })

        self.assertEqual(
            completed,
            {"schema": "cortex/worker-completion/v11", "ok": True, "terminal": True},
        )

    def test_tampered_cross_pair_stale_and_out_of_scope_repair_never_write_canonical_result(self) -> None:
        refs = {
            "task_ref": "task-000000000001",
            "assignment_ref": "assignment-v1-" + "a" * 64,
        }
        rejected = {**refs, "outcome": {"status": "completed", "summary": ""}}
        repair = self._public_complete(rejected)["recovery"]["repair"]
        valid_patch = [{"op": "replace", "path": "/summary", "value": "fixed"}]
        token = repair["repair_capsule"]
        cases = {
            "tampered": {
                **refs, "repair_capsule": token[:-1] + ("0" if token[-1] != "0" else "1"),
                "base_payload_digest": repair["base_payload_digest"], "patches": valid_patch,
            },
            "cross_pair": {
                **refs, "assignment_ref": "assignment-v1-" + "b" * 64,
                "repair_capsule": token, "base_payload_digest": repair["base_payload_digest"], "patches": valid_patch,
            },
            "stale": {
                **refs, "repair_capsule": token, "base_payload_digest": "sha256:" + "0" * 64,
                "patches": valid_patch,
            },
            "out_of_scope": {
                **refs, "repair_capsule": token, "base_payload_digest": repair["base_payload_digest"],
                "patches": [{"op": "replace", "path": "/status", "value": "failed"}],
            },
        }
        for label, submission in cases.items():
            with self.subTest(label=label):
                response = self._public_complete(submission)
                self.assertFalse(response["ok"])
                if label == "out_of_scope":
                    self.assertEqual(response["recovery"]["repair"], repair)
                    self.assertTrue(response["recovery"]["retryable"])
                    self.assertFalse(response["recovery"]["state_mutated"])
                else:
                    self.assertFalse(response["recovery"]["retryable"])
                    self.assertFalse(response["recovery"]["state_mutated"])
                self.assertNotIn("task_ref", response)
                self.assertIsNone(attempt_protocol.get_attempt_result(
                    self.root, task_id=self.task_id, attempt_id=self.attempt_id,
                ))

    def test_every_private_escrow_field_is_reauthenticated_before_repair_use(self) -> None:
        refs = {
            "task_ref": "task-000000000001",
            "assignment_ref": "assignment-v1-" + "a" * 64,
        }
        repair = self._public_complete({
            **refs,
            "outcome": {"status": "completed", "summary": "", "findings": [{"summary": "keep"}]},
        })["recovery"]["repair"]
        handle_id = repair["repair_capsule"].split(".")[1]
        handle_digest = attempt_facade.v11_submission.repair_handle_digest(handle_id)
        submission = {
            **refs,
            "repair_capsule": repair["repair_capsule"],
            "base_payload_digest": repair["base_payload_digest"],
            "patches": [{"op": "replace", "path": "/summary", "value": "fixed"}],
        }
        immutable_trigger = (
            "CREATE TRIGGER repair_escrow_immutable_update BEFORE UPDATE ON repair_escrow "
            "FOR EACH ROW BEGIN SELECT RAISE(ABORT, 'repair escrow rows are immutable'); END"
        )
        with ledger_db._connection(self.root) as connection:
            original = dict(connection.execute(
                "SELECT * FROM repair_escrow WHERE handle_digest=?", (handle_digest,),
            ).fetchone())
        mutations = {
            "payload_json": '{"status":"completed","summary":"","findings":[]}',
            "diagnostics_json": '[{"json_pointer":"/outcome/status","repair_pointer":"/status"}]',
            "allowed_paths_json": '["/status"]',
            "task_ref_digest": "sha256:" + "1" * 64,
            "attempt_id": "implementation-02",
            "assignment_ref_digest": "sha256:" + "2" * 64,
            "kind": "plan",
            "base_payload_digest": "sha256:" + "3" * 64,
            "escrow_digest": "4" * 64,
        }
        for column, mutated in mutations.items():
            with self.subTest(column=column):
                with ledger_db._connection(self.root, write=True) as connection:
                    connection.execute("DROP TRIGGER repair_escrow_immutable_update")
                    connection.execute(
                        f"UPDATE repair_escrow SET {column}=? WHERE handle_digest=?",
                        (mutated, handle_digest),
                    )
                    connection.execute(immutable_trigger)
                response = self._public_complete(submission)
                self.assertFalse(response["ok"])
                self.assertFalse(response["recovery"]["retryable"])
                self.assertFalse(response["recovery"]["state_mutated"])
                self.assertIsNone(attempt_protocol.get_attempt_result(
                    self.root, task_id=self.task_id, attempt_id=self.attempt_id,
                ))
                with ledger_db._connection(self.root, write=True) as connection:
                    connection.execute("DROP TRIGGER repair_escrow_immutable_update")
                    connection.execute(
                        f"UPDATE repair_escrow SET {column}=? WHERE handle_digest=?",
                        (original[column], handle_digest),
                    )
                    connection.execute(immutable_trigger)

    def test_post_completion_worker_event_and_result_read_are_terminal_failures(self) -> None:
        refs = {
            "task_ref": "task-000000000001",
            "assignment_ref": "assignment-v1-" + "a" * 64,
        }
        terminal_attempt = {**self.attempt, "status": "completed"}
        with mock.patch.object(
            attempt_facade,
            "_worker_context",
            return_value=(self.project, self.project, self.state, terminal_attempt, "backend_dev"),
        ):
            event = attempt_facade.record_attempt_event({
                **refs, "event_type": "progress", "payload": {"summary": "too late"},
            })
        self.assertFalse(event["ok"])
        self.assertEqual(event["error"]["code"], "record_attempt_event_closed")
        self.assertFalse(event["recovery"]["retryable"])
        self.assertNotIn("task_ref", event)

        terminal_state = {**self.state, "attempts": [terminal_attempt]}
        with mock.patch.object(
            attempt_facade._runtime,
            "authorize_worker_assignment",
            return_value=(self.project, self.project, terminal_state, terminal_attempt, "backend_dev"),
        ):
            result = attempt_facade.read_worker_result({
                **refs, "attempt_result_ref": "attempt-result-own-terminal",
            })
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "read_worker_result_not_authorized")
        self.assertFalse(result["recovery"]["retryable"])
        self.assertNotIn("task_ref", result)

    def test_public_complete_attempt_keeps_semantic_result_through_projection_retry(self) -> None:
        """The second call finalizes the original result rather than a new worker."""
        failed_projection = mock.Mock(side_effect=RuntimeError("injected result-view failure"))
        with self._facade_patches(failed_projection):
            pending = self._complete(dict(self.params))

        self.assertFalse(pending["ok"])
        self.assertEqual(pending["outcome"], "finalization_pending")
        self.assertTrue(pending["retryable"])
        self.assertFalse(pending["worker_replacement_authorized"])
        stored = attempt_protocol.get_attempt_result(
            self.root, task_id=self.task_id, attempt_id=self.attempt_id,
        )
        self.assertIsNotNone(stored)
        assert stored is not None
        self.assertEqual(stored["status"], "completed")
        self.assertEqual(stored["summary"], self.params["summary"])
        self.assertEqual(stored["findings"], self.params["findings"])
        self.assertEqual(stored["changed_files"], ["src/owned.py"])
        self.assertEqual(stored["lifecycle_status"], attempt_protocol.LIFECYCLE_FINALIZING)
        self.assertEqual(
            [event["event_type"] for event in attempt_protocol.list_attempt_events(
                self.root, task_id=self.task_id, attempt_id=self.attempt_id,
            )],
            ["briefing_acknowledged", "work_completed", "finalizing", "finalization_failed"],
        )

        recorded_projection = mock.Mock(return_value={"projection_ref": "attempt-result-view-implementation-01"})
        with self._facade_patches(recorded_projection):
            completed = self._complete(dict(self.params))

        self.assertTrue(completed["ok"])
        self.assertEqual(completed, {
            "schema": cortex.PUBLIC_ORCHESTRATION_SCHEMA,
            "ok": True,
            "terminal": True,
        })
        final = attempt_protocol.get_attempt_result(
            self.root, task_id=self.task_id, attempt_id=self.attempt_id,
        )
        self.assertEqual(final["lifecycle_status"], attempt_protocol.LIFECYCLE_COMPLETED)
        self.assertEqual(final["result_ref"], stored["result_ref"])
        self.assertEqual(
            [event["event_type"] for event in attempt_protocol.list_attempt_events(
                self.root, task_id=self.task_id, attempt_id=self.attempt_id,
            )],
            ["briefing_acknowledged", "work_completed", "finalizing", "finalization_failed", "completed"],
        )
        self.assertEqual(recorded_projection.call_args.kwargs, {
            "task_id": self.task_id, "attempt_id": self.attempt_id,
        })

    def test_public_completed_attempt_preserves_successor_unresolved_handoff(self) -> None:
        """The facade stores scoped unresolved work without rewriting its status."""
        params = dict(self.params)
        params["unresolved"] = [{"summary": "Governance close must resolve the inherited risk."}]
        with self._facade_patches(mock.Mock(return_value={"projection_ref": "attempt-result-view-implementation-01"})):
            response = self._complete(params)

        self.assertTrue(response["ok"])
        stored = attempt_protocol.get_attempt_result(
            self.root, task_id=self.task_id, attempt_id=self.attempt_id,
        )
        self.assertIsNotNone(stored)
        assert stored is not None
        self.assertEqual(stored["status"], "completed")
        self.assertEqual(stored["unresolved"], params["unresolved"])
        self.assertEqual(stored["lifecycle_status"], attempt_protocol.LIFECYCLE_COMPLETED)

    def test_required_artifact_preflight_returns_exact_paths_without_writing_result(self) -> None:
        manifest = {
            "work_packages": [{"artifact_path": "artifacts/planning.json"}],
        }
        package_record = {
            "package": {
                "required_artifacts": [{
                    "path": "tests/generated_invariants.py",
                    "kind": "test_suite",
                    "owner_gate": "implementation",
                    "verification": "pytest -q tests/generated_invariants.py",
                }],
                "microtasks": [],
            },
        }
        with mock.patch.object(cortex, "current_planning_manifest", return_value=manifest), \
             mock.patch.object(cortex, "read_immutable_json_artifact", return_value=(package_record, {})):
            diagnostics = cortex.required_artifact_diagnostics(
                self.project,
                self.project,
                {"task_id": self.task_id},
                self.attempt,
            )

        self.assertEqual(len(diagnostics), 1)
        diagnostic = diagnostics[0]
        self.assertEqual(diagnostic["code"], "required_artifact_missing")
        self.assertEqual(diagnostic["json_pointer"], "/required_artifacts/0")
        self.assertEqual(diagnostic["received"]["path"], "tests/generated_invariants.py")
        self.assertIn("pytest -q tests/generated_invariants.py", diagnostic["fix"])
        self.assertIn("same implementation attempt", diagnostic["fix"])

    def test_terminal_attempt_result_reconciles_exact_worker_session(self) -> None:
        """Terminal canonical results cannot leave their native session live."""
        ledger_db.put_worker_session(self.root, {
            "task_id": self.task_id,
            "attempt_id": self.attempt_id,
            "host_agent_id": "native.Implementation:01",
            "host_task_name": "implementation-worker",
            "host_tool": "spawn_agent",
            "status": "running",
            "resumable": True,
        })
        result = {
            "result_ref": "attempt-result-terminal-session-01",
            "lifecycle_status": attempt_protocol.LIFECYCLE_COMPLETED,
            "work_completed_at": "2026-08-22T00:00:00+00:00",
            "completed_at": "2026-08-22T00:01:00+00:00",
        }
        with mock.patch.object(attempt_facade._runtime, "ledger_root", return_value=self.root), \
             mock.patch.object(attempt_facade._runtime, "state_lock", return_value=nullcontext()), \
             mock.patch.object(attempt_facade._runtime, "load_state", return_value=(self.project, self.project, self.state)), \
             mock.patch.object(attempt_facade._runtime, "save_state"):
            attempt_facade._mark_attempt(
                self.project,
                self.task_id,
                self.attempt_id,
                lifecycle_status="result_finalized",
                result=result,
            )

        sessions = ledger_db.list_worker_sessions(self.root, self.task_id)
        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0]["status"], "completed")
        self.assertEqual(sessions[0]["resumable"], 0)
        self.assertEqual(sessions[0]["terminated_at"], result["completed_at"])
        self.assertEqual(self.attempt["worker_session_terminal_status"], "completed")
        self.assertEqual(self.attempt["worker_session_reconciled_at"], result["completed_at"])

    def test_terminal_attempt_result_without_worker_session_fails_closed(self) -> None:
        """A result cannot fabricate a missing native identity during repair."""
        result = {
            "result_ref": "attempt-result-missing-session-01",
            "lifecycle_status": attempt_protocol.LIFECYCLE_BLOCKED,
            "work_completed_at": "2026-08-22T00:00:00+00:00",
        }
        with mock.patch.object(attempt_facade._runtime, "ledger_root", return_value=self.root), \
             mock.patch.object(attempt_facade._runtime, "state_lock", return_value=nullcontext()), \
             mock.patch.object(attempt_facade._runtime, "load_state", return_value=(self.project, self.project, self.state)), \
             mock.patch.object(attempt_facade._runtime, "save_state"):
            with self.assertRaisesRegex(ValueError, "no persisted worker session"):
                attempt_facade._mark_attempt(
                    self.project,
                    self.task_id,
                    self.attempt_id,
                    lifecycle_status="blocked",
                    result=result,
                    terminal_status="blocked",
                )

        self.assertNotIn("worker_session_reconciled_at", self.attempt)

    def test_terminal_result_live_session_is_an_invariant_violation(self) -> None:
        """Completion checks reject snapshots that still advertise live work."""
        attempt = {
            "attempt_id": self.attempt_id,
            "facade_managed": True,
            "status": "running",
            "attempt_result_ref": "attempt-result-live-session-01",
        }
        session = {
            "attempt_id": self.attempt_id,
            "status": "running",
            "resumable": 1,
            "terminated_at": None,
        }
        terminal = {
            "result_ref": attempt["attempt_result_ref"],
            "lifecycle_status": attempt_protocol.LIFECYCLE_COMPLETED,
        }
        with mock.patch.object(cortex, "load_task_definition", return_value={"task_id": self.task_id}), \
             mock.patch.object(cortex, "_task_document_root", return_value=self.root), \
             mock.patch.object(cortex, "db_list_worker_sessions", return_value=[session]), \
             mock.patch.object(cortex.attempt_protocol, "get_attempt_result", return_value=terminal):
            violations = cortex._terminal_facade_attempts_with_live_sessions(
                self.project, [attempt],
            )
        self.assertEqual(violations, [self.attempt_id])

    def test_context_handoff_keeps_finalization_pending_out_of_failure_recovery(self) -> None:
        """Compaction must not turn the hook's exact pending tag into a failed receipt."""
        state = {
            "pipeline_contract_version": 2,
            "task_id": "finalization-context-task",
            "status": "active",
            "current_gates": ["implementation"],
            "current_pipeline": ["implementation"],
            "parallel_groups": [["implementation"]],
            "completed_gates": [],
            "skipped_gates": [],
            "attempts": [{
                "attempt_id": "implementation-04",
                "status": "running",
                "gate": "implementation",
                "profile": "backend_dev",
                "dispatch_ref": "dispatch-implementation-04",
                "lifecycle_status": "finalizing",
                "attempt_result_ref": "attempt-result-finalization-04",
                "host_stopped_at": "2026-08-22T00:00:00+00:00",
                "host_stop_outcome": "work_completed_finalization_pending",
                "host_spawn": {"agent_id": "native.Finalization:04", "task_name": "implementation-finalization"},
            }],
        }
        task = {"user_request": "finalize one persisted attempt", "acceptance_criteria": [], "verification": []}
        plan = {"waves": [{
            "wave_id": "wave-01",
            "gates": ["implementation"],
            "attempt_ids": ["implementation-04"],
        }]}
        handoff = context_handoff._context_handoff(self.project, state, task, plan)

        stopped = handoff["stopped_workers"]
        self.assertEqual(len(stopped), 1)
        self.assertTrue(stopped[0]["finalization_pending"])
        self.assertIsNone(stopped[0]["failure_status"])
        self.assertIsNone(stopped[0]["failure_reason"])
        self.assertFalse(stopped[0]["resumable"])
        self.assertIn("retry complete_attempt on that exact persisted attempt only", handoff["next_action"])
        self.assertNotIn("submit exactly one failed continuation", handoff["next_action"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
