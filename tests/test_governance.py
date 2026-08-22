"""Focused acceptance coverage for the v10 governance ledger and resolver."""
from __future__ import annotations

import concurrent.futures
import tempfile
import unittest
import json
from pathlib import Path
from unittest import mock

from tests.cortex_test_support import HostPrivateControlStoreTestMixin

import sys

sys.path.insert(0, str(Path(__file__).parents[1] / "plugins/cortex/scripts"))

import cortex
from cortex_runtime import attempt_protocol, briefings, governance, ledger_db


class GovernanceAcceptanceTests(HostPrivateControlStoreTestMixin, unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.set_up_host_private_control_store()
        # This direct ledger-db fixture is intentionally outside the workspace
        # mapping: public runtime calls in this suite must use the private host
        # mapping rather than mutating an incidental project location.
        self.root = Path(self.temp.name) / "governance-ledger"
        ledger_db.ensure_database(self.root)

    def tearDown(self) -> None:
        self.tear_down_host_private_control_store()
        self.temp.cleanup()

    def add_task(self, task_id: str) -> None:
        definition = {
            "schema": "cortex/v3",
            "task_id": task_id,
            "objective": "governance fixture",
            "created_at": "2026-01-01T00:00:00+00:00",
        }
        state = {
            "schema": "cortex/v3",
            "task_id": task_id,
            "task_number": int(task_id.rsplit("-", 1)[-1]),
            "status": "active",
            "revision": 1,
            "updated_at": "2026-01-01T00:00:00+00:00",
        }
        ledger_db.create_task(self.root, definition, state, f"tasks/{task_id}")

    def no_risk_assessment(self) -> dict[str, bool]:
        return {key: False for key in governance.OFF_ASSESSMENT_KEYS}

    def test_mode_resolution_obeys_floor_and_stated_triggers(self) -> None:
        self.assertEqual(
            governance.classify_governance(complexity="C1", objective="author a plain document")["effective_mode"],
            "minimal",
        )
        self.assertEqual(
            governance.classify_governance(complexity="C2", requested_mode="required")["effective_mode"],
            "full",
        )
        triggered = governance.classify_governance(complexity="C1", objective="rotate an API key")
        self.assertEqual(triggered["effective_mode"], "full")
        self.assertTrue(any(item["trigger"] == "credentials" for item in triggered["trigger_evidence"]))
        numeric_metadata = governance.classify_governance(
            complexity="C1",
            task={"repository_count": 4, "related_task_count": 5},
        )
        self.assertEqual(numeric_metadata["effective_mode"], "minimal")
        explicit_multi_scope = governance.classify_governance(
            complexity="C1",
            task={"multiple_repositories": True},
        )
        self.assertEqual(explicit_multi_scope["effective_mode"], "full")
        with self.assertRaisesRegex(governance.GovernanceError, "governance_mode=off"):
            governance.classify_governance(complexity="C2", requested_mode="off")
        with self.assertRaisesRegex(governance.GovernanceError, "complete boolean"):
            governance.classify_governance(
                complexity="C1",
                requested_mode="off",
                objective="Perform a routine local maintenance adjustment.",
            )
        off = governance.classify_governance(
            complexity="C1",
            requested_mode="off",
            objective="Perform a routine local maintenance adjustment.",
            task={"risk_triggers": self.no_risk_assessment()},
        )
        self.assertEqual(off["effective_mode"], "minimal")
        self.assertEqual(off["policy_snapshot"]["off_assessment"], self.no_risk_assessment())
        custom_policy = governance.classify_governance(
            complexity="C1",
            requested_mode="off",
            objective="Perform a routine local maintenance adjustment.",
            task={"risk_triggers": self.no_risk_assessment()},
            policy={"schema": "custom-policy/v1"},
        )
        self.assertEqual(custom_policy["policy_snapshot"]["off_assessment"], self.no_risk_assessment())

    def test_unicode_oversized_approval_basis_is_rejected_before_governance_mutation(self) -> None:
        """A lifecycle basis is durable content, not an unbounded side channel."""
        self.add_task("task-1")
        # Each string stays below the per-string guard; only the canonical
        # UTF-8 JSON aggregate exceeds the record/lifecycle budget.
        oversized = ["🙂" * 16_000] * 5
        with self.assertRaises(governance.GovernanceError) as raised:
            governance.create_record(
                self.root,
                record_type="decision",
                task_id="task-1",
                content={"decision": "bounded"},
                approval_basis={"unicode": oversized},
            )
        self.assertEqual(raised.exception.code, "content_size_exceeded")
        with ledger_db._connection(self.root) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM governance_records").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM governance_record_lifecycle").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM artifact_blobs").fetchone()[0], 0)

    def test_c3_floor_and_explicit_risk_triggers_cannot_be_lowered(self) -> None:
        self.assertEqual(
            governance.classify_governance(complexity="C3", requested_mode="auto")["effective_mode"],
            "full",
        )
        with self.assertRaisesRegex(governance.GovernanceError, "governance_mode=off"):
            governance.classify_governance(complexity="C3", requested_mode="off")
        self.assertEqual(
            governance.classify_governance(
                complexity="C2", task={"risk_triggers": ["destructive"]}
            )["effective_mode"],
            "full",
        )

    def test_multilingual_destructive_migration_public_and_external_triggers_are_hard_floors(self) -> None:
        objectives = (
            "Удалить данные без возможности восстановления",
            "Выполнить миграцию схемы базы данных",
            "Опубликовать изменение публичного API",
            "Развернуть изменение вне рабочего пространства",
        )
        for objective in objectives:
            with self.subTest(objective=objective):
                resolved = governance.classify_governance(
                    complexity="C1",
                    objective=objective,
                    requested_mode="auto",
                )
                self.assertEqual(resolved["effective_mode"], "full")
                with self.assertRaisesRegex(governance.GovernanceError, "governance_mode=off"):
                    governance.classify_governance(
                        complexity="C1",
                        objective=objective,
                        requested_mode="off",
                    )

    def test_full_mode_adds_only_server_owned_governance_review_waves(self) -> None:
        task = {
            "governance": {"effective_mode": "full"},
            "objective": "governed change",
        }
        ordinary = [{"wave_id": "wave-01", "delegations": [{"gate": "implementation", "agent": "general"}]}]
        waves = cortex._append_governance_waves(ordinary, task)
        self.assertEqual([wave["delegations"][0]["gate"] for wave in waves], ["governance_activation", "implementation", "governance_close"])
        self.assertEqual([wave["delegations"][0]["agent"] for wave in waves if wave["delegations"][0]["gate"].startswith("governance_")], ["code_reviewer", "code_reviewer"])
        self.assertEqual(len(waves) - len(ordinary), 2)
        with_close = cortex._append_governance_waves(
            ordinary + [{"wave_id": "wave-close", "delegations": [{"gate": "close", "agent": "build_verification"}]}],
            task,
        )
        self.assertEqual(
            [wave["delegations"][0]["gate"] for wave in with_close],
            ["governance_activation", "implementation", "governance_close", "close"],
        )
        reordered = cortex._append_governance_waves(
            [
                {"wave_id": "ordinary", "delegations": [{"gate": "implementation", "agent": "general"}]},
                {"wave_id": "activation", "delegations": [{"gate": "governance_activation", "agent": "code_reviewer"}]},
                {"wave_id": "close", "delegations": [{"gate": "close", "agent": "build_verification"}]},
                {"wave_id": "governance-close", "delegations": [{"gate": "governance_close", "agent": "code_reviewer"}]},
            ],
            task,
        )
        self.assertEqual(
            [wave["delegations"][0]["gate"] for wave in reordered],
            ["governance_activation", "implementation", "governance_close", "close"],
        )
        with self.assertRaisesRegex(ValueError, "server-owned"):
            cortex._append_governance_waves(
                [{"wave_id": "bad", "delegations": [{"gate": "governance_activation", "agent": "general"}]}],
                task,
            )

    def test_governance_pipeline_transport_projection_keeps_full_lifecycle_edges(self) -> None:
        """A defensive projection must not falsely hide the final close edge."""
        pipeline = [
            "governance_activation",
            *[f"historical_gate_{index:02d}" for index in range(20)],
            "governance_close",
            "close",
        ]
        projected, metadata = briefings._compact_governance_pipeline(pipeline)
        self.assertEqual(projected[0], "governance_activation")
        self.assertEqual(projected[-2:], ["governance_close", "close"])
        self.assertEqual(metadata, {
            "schema": "cortex/governance-pipeline-projection/v1",
            "total_gates": len(pipeline),
            "selected_gates": len(projected),
            "omitted_middle_gates": len(pipeline) - len(projected),
            "truncated": True,
            "full_pipeline_source": "task_contract",
        })

    def test_activation_briefing_reviews_governance_boundary_not_future_delivery(self) -> None:
        project = Path(self.temp.name) / "activation-briefing"
        project.mkdir()
        (project / "README.md").write_text("# activation fixture\n", encoding="utf-8")
        started = cortex.start_orchestration({
            "project_root": str(project),
            "task": {
                "user_request": "Create result.txt as a governed high-impact release fixture.",
                "complexity": "C3",
                "acceptance_criteria": ["result.txt contains the governed fixture result."],
                "verification": ["Read result.txt after implementation."],
                "plan_approval": "auto",
            },
            "waves": [
                {"workers": [{"phase": "implementation"}]},
                {"workers": [{"phase": "documentation"}]},
                {"workers": [{"phase": "close"}]},
            ],
        })
        self.assertTrue(started["ok"], started)
        activation = started["dispatches"][0]
        self.assertEqual(activation["phase"], "governance_activation")
        briefing = Path(activation["briefing_path"]).read_text(encoding="utf-8")
        assignment = json.loads(briefing.split("```json\n", 1)[1].split("\n```", 1)[0])
        governance_context = assignment["governance_context"]
        self.assertEqual(governance_context["requested_mode"], "auto")
        self.assertEqual(governance_context["effective_mode"], "full")
        self.assertIn("complexity:C3", governance_context["reasons"])
        self.assertEqual(governance_context["autonomous_scope_ref"], "governance-scope-autonomous")
        self.assertEqual(governance_context["policy_snapshot"]["required_floor"], "full")
        self.assertRegex(governance_context["policy_snapshot_digest"], r"^[0-9a-f]{64}$")
        self.assertEqual(
            governance_context["current_pipeline"],
            ["governance_activation", "implementation", "documentation", "governance_close", "close"],
        )
        self.assertNotIn("task_acceptance_criteria", assignment)
        self.assertNotIn("task_verification", assignment)
        self.assertIn("MUST NOT be reported as findings", briefing)
        self.assertIn("Fail or request rework only for a defect in those activation inputs", briefing)
        self.assertIn("ATTEMPT_COMPLETED attempt_result_ref=<generated id>", briefing)
        self.assertNotIn("ATTEMPT_COMPLETED result_ref=", briefing)
        route = [
            "Q: ask=>QUESTION_RECORDED question_ref=<exact ref>",
            "Answer=>followup_task same child",
            "poll same ref/attempt first",
            "Answered=>record_attempt_event",
            "rerun, complete_attempt",
            "ATTEMPT_COMPLETED attempt_result_ref=<generated id>",
        ]
        positions = [briefing.index(marker) for marker in route]
        self.assertEqual(positions, sorted(positions))
        self.assertIn("Pending=>QUESTION_RECORDED", briefing)
        self.assertIn("No OTHER_TERMINAL/freeform/replacement", briefing)

    def test_actual_governance_activation_briefing_preserves_question_resume_route(self) -> None:
        project = Path(self.temp.name) / "question-route"
        project.mkdir()
        started = cortex.start_orchestration({
            "project_root": str(project),
            "task": {
                "user_request": "Create result.txt as a governed high-impact release fixture.",
                "complexity": "C3",
                "acceptance_criteria": ["result.txt contains the governed fixture result."],
                "verification": ["Read result.txt after implementation."],
                "plan_approval": "auto",
            },
            "waves": [
                {"workers": [{"phase": "implementation"}]},
                {"workers": [{"phase": "documentation"}]},
                {"workers": [{"phase": "close"}]},
            ],
        })
        self.assertTrue(started["ok"], started)
        activation = started["dispatches"][0]
        self.assertEqual(activation["phase"], "governance_activation")
        briefing = Path(activation["briefing_path"]).read_text(encoding="utf-8")
        self.assertLessEqual(len(briefing.encode("utf-8")), 14_500)
        route = [
            "Q: ask=>QUESTION_RECORDED question_ref=<exact ref>",
            "Answer=>followup_task same child",
            "poll same ref/attempt first",
            "Answered=>record_attempt_event",
            "rerun, complete_attempt",
            "ATTEMPT_COMPLETED attempt_result_ref=<generated id>",
        ]
        positions = [briefing.index(marker) for marker in route]
        self.assertEqual(positions, sorted(positions))
        self.assertIn("Pending=>QUESTION_RECORDED", briefing)
        self.assertIn("No OTHER_TERMINAL/freeform/replacement", briefing)

    def test_minimal_and_light_modes_preserve_the_existing_pipeline(self) -> None:
        ordinary = [{"wave_id": "wave-01", "delegations": [{"gate": "implementation", "agent": "general"}]}]
        for mode in ("minimal", "light"):
            with self.subTest(mode=mode):
                self.assertEqual(
                    cortex._append_governance_waves(ordinary, {"governance": {"effective_mode": mode}}),
                    ordinary,
                )

    def test_public_governance_requires_server_capability_and_activation_identity(self) -> None:
        project = str(Path(self.temp.name))
        base = {
            "project_root": project,
            "action": "inspect",
            "initiative_ref": "initiative-capability",
            "principal": "caller-principal",
            "thread_id": "caller-thread",
        }
        missing = cortex.manage_governance(base)
        self.assertFalse(missing["ok"])
        self.assertEqual(missing["code"], "coordinator_capability_required")
        activation = {
            "principal": "server-principal",
            "thread_id": "server-thread",
            "task_id": "task-capability",
        }
        with (
            mock.patch.object(cortex, "require_activation", return_value=activation),
            mock.patch.object(cortex, "_coordinator_capability_matches", return_value=True),
            mock.patch.object(
                cortex,
                "_coordinator_capability_claims_for_task",
                return_value={"kind": "project_admin", "generation": 1, "allowed_actions": ["*"]},
            ),
            mock.patch.object(
                cortex,
                "_coordinator_identity_for_capability",
                return_value=(activation["task_id"], activation["principal"], activation["thread_id"]),
            ),
            mock.patch.object(cortex, "manage_governance_service", return_value={}) as service,
        ):
            spoofed = cortex.manage_governance(
                {
                    **base,
                    "principal": "caller-principal",
                    "thread_id": "caller-thread",
                    "coordinator_capability": "a" * 64,
                }
            )
            self.assertFalse(spoofed["ok"])
            self.assertEqual(spoofed["code"], "coordinator_authorization_required")
            accepted = cortex.manage_governance(
                {
                    **base,
                    "principal": activation["principal"],
                    "thread_id": activation["thread_id"],
                    "coordinator_capability": "a" * 64,
                }
            )
            self.assertTrue(accepted["ok"], accepted)
            service.assert_called_once()
            self.assertEqual(service.call_args.kwargs["actor_role"], "coordinator")
            self.assertEqual(accepted["authorization"]["principal"], activation["principal"])
            cap_only = cortex.manage_governance(
                {
                    key: value
                    for key, value in {
                        **base,
                        "principal": None,
                        "thread_id": None,
                        "coordinator_capability": "a" * 64,
                    }.items()
                    if value is not None
                }
            )
            self.assertTrue(cap_only["ok"], cap_only)
            self.assertEqual(service.call_count, 2)

    def test_public_start_returns_capability_for_capability_only_governance_call(self) -> None:
        project = Path(self.temp.name) / "capability-project"
        project.mkdir()
        governance.create_initiative(
            cortex.ledger_root({"project_root": str(project)}),
            initiative_ref="initiative-capability-call",
            title="Capability call",
            goal="Exercise capability-only coordinator authorization",
            owner="coordinator",
        )
        started = cortex.start_orchestration(
            {
                "project_root": str(project),
                "task": {
                    "user_request": "Create a local plain-text note.",
                    "complexity": "C1",
                    "governance_mode": "off",
                    "initiative_ref": "initiative-capability-call",
                    "risk_triggers": self.no_risk_assessment(),
                    "acceptance_criteria": ["The note contract is preserved."],
                    "verification": ["Verify the local note result."],
                },
                "waves": [{"workers": [{"phase": "discover"}]}],
            }
        )
        self.assertTrue(started["ok"], started)
        capability = (started.get("authorization") or {}).get("coordinator_capability")
        self.assertRegex(str(capability), r"^[0-9a-f]{64}$")
        registry = cortex._operation_registry(cortex.ledger_root({"project_root": str(project)}))
        serialized_registry = json.dumps(registry, sort_keys=True)
        self.assertNotIn(str(capability), serialized_registry)
        self.assertNotIn('"coordinator_capability"', serialized_registry)
        self.assertIn('"coordinator_capability_digest"', serialized_registry)
        managed = cortex.manage_governance(
            {
                "project_root": str(project),
                "action": "inspect",
                "initiative_ref": "initiative-capability-call",
                "coordinator_capability": capability,
            }
        )
        self.assertTrue(managed["ok"], managed)
        self.assertEqual(
            managed["authorization"]["actor"],
            "coordinator",
        )
        replayed = cortex.start_orchestration(
            {
                "project_root": str(project),
                "task": {
                    "user_request": "Create a local plain-text note.",
                    "complexity": "C1",
                    "governance_mode": "off",
                    "initiative_ref": "initiative-capability-call",
                    "risk_triggers": self.no_risk_assessment(),
                    "acceptance_criteria": ["The note contract is preserved."],
                    "verification": ["Verify the local note result."],
                },
                "waves": [{"workers": [{"phase": "discover"}]}],
            }
        )
        self.assertTrue(replayed["ok"], replayed)
        self.assertNotIn("authorization", replayed)

    def test_public_start_enforces_governance_classification_and_review_waves(self) -> None:
        def start(project: Path, request: str, complexity: str, mode: str) -> dict[str, object]:
            project.mkdir(parents=True, exist_ok=True)
            return cortex.start_orchestration(
                {
                    "project_root": str(project),
                    "task": {
                        "user_request": request,
                        "complexity": complexity,
                        "governance_mode": mode,
                        "acceptance_criteria": ["The fixture contract is preserved."],
                        "verification": ["Run the fixture contract check."],
                    },
                }
            )

        c2_off = start(Path(self.temp.name) / "c2-off", "Write a plain plan.", "C2", "off")
        self.assertFalse(c2_off["ok"])
        self.assertIn("governance_mode=off", c2_off["diagnostics"][0]["message"])
        c3 = start(Path(self.temp.name) / "c3", "Review a high-impact change.", "C3", "auto")
        self.assertTrue(c3["ok"], c3)
        self.assertEqual(c3["effective_mode"], "full")
        self.assertEqual(c3["governance"]["effective_mode"], "full")
        self.assertEqual(
            [wave["workers"][0]["phase"] for wave in c3["pipeline"]["waves"] if wave["workers"]],
            ["governance_activation", "scope", "discover", "architecture", "plan", "implementation", "qa", "review", "documentation", "governance_close", "close"],
        )
        triggered = start(Path(self.temp.name) / "triggered", "Rotate an API key.", "C1", "auto")
        self.assertTrue(triggered["ok"], triggered)
        self.assertEqual(triggered["effective_mode"], "full")
        required = start(Path(self.temp.name) / "required", "Write a plain note.", "C1", "required")
        self.assertTrue(required["ok"], required)
        self.assertEqual(required["effective_mode"], "full")

    def test_public_auto_governance_waves_keep_integer_relative_steps(self) -> None:
        project = Path(self.temp.name) / "auto-governance-relative-steps"
        project.mkdir()
        started = cortex.start_orchestration(
            {
                "project_root": str(project),
                "task": {
                    "user_request": "Validate a high-impact cross-system release fixture.",
                    "complexity": "C3",
                    "acceptance_criteria": ["The governed fixture completes."],
                    "verification": ["Verify the governed lifecycle."],
                    "plan_approval": "auto",
                },
                "waves": [
                    {"workers": [{"phase": "implementation"}]},
                    {"workers": [{"phase": "documentation"}]},
                    {"workers": [{"phase": "close"}]},
                ],
            }
        )
        self.assertTrue(started["ok"], started)
        self.assertEqual(started["requested_mode"], "auto")
        self.assertEqual(started["effective_mode"], "full")
        self.assertEqual(started["step"], 1)
        self.assertEqual(
            [wave["wave"] for wave in started["pipeline"]["waves"]],
            [1, 2, 3, 4, 5],
        )
        self.assertEqual(
            [wave["workers"][0]["phase"] for wave in started["pipeline"]["waves"]],
            [
                "governance_activation",
                "implementation",
                "documentation",
                "governance_close",
                "close",
            ],
        )

    def test_initiative_hierarchy_dependencies_and_replay_are_safe(self) -> None:
        root = governance.create_initiative(
            self.root,
            initiative_ref="initiative-root",
            title="Root",
            goal="Coordinate work",
            owner="coordinator",
        )
        child = governance.create_initiative(
            self.root,
            initiative_ref="initiative-child",
            parent_ref=root["initiative_ref"],
            title="Child",
            goal="Deliver work",
            owner="coordinator",
        )
        self.assertEqual(governance.create_initiative(
            self.root,
            initiative_ref="initiative-child",
            parent_ref=root["initiative_ref"],
            title="Child",
            goal="Deliver work",
            owner="coordinator",
        )["revision"], child["revision"])
        with self.assertRaisesRegex(governance.GovernanceError, "different initiative"):
            governance.create_initiative(
                self.root,
                initiative_ref="initiative-child",
                parent_ref=root["initiative_ref"],
                title="Child",
                goal="Deliver work",
                owner="coordinator",
                risk="critical",
            )
        leaf = governance.create_initiative(
            self.root,
            initiative_ref="initiative-leaf",
            parent_ref=child["initiative_ref"],
            title="Leaf",
            goal="Finish work",
            owner="coordinator",
        )
        with self.assertRaisesRegex(governance.GovernanceError, "three levels"):
            governance.create_initiative(
                self.root,
                initiative_ref="initiative-too-deep",
                parent_ref=leaf["initiative_ref"],
                title="Too deep",
                goal="Reject",
                owner="coordinator",
            )
        governance.add_dependency(
            self.root,
            source_type="initiative",
            source_ref=root["initiative_ref"],
            target_type="initiative",
            target_ref=child["initiative_ref"],
        )
        with self.assertRaisesRegex(governance.GovernanceError, "cycle"):
            governance.add_dependency(
                self.root,
                source_type="initiative",
                source_ref=child["initiative_ref"],
                target_type="initiative",
                target_ref=root["initiative_ref"],
            )

    def test_task_dependency_cycles_are_rejected(self) -> None:
        self.add_task("task-201")
        self.add_task("task-202")
        governance.add_dependency(
            self.root,
            source_type="task",
            source_ref="task-201",
            target_type="task",
            target_ref="task-202",
        )
        with self.assertRaisesRegex(governance.GovernanceError, "cycle"):
            governance.add_dependency(
                self.root,
                source_type="task",
                source_ref="task-202",
                target_type="task",
                target_ref="task-201",
            )

    def test_idempotent_task_link_replay_survives_revision_bump(self) -> None:
        initiative = governance.create_initiative(
            self.root,
            initiative_ref="initiative-link-replay",
            title="Link replay",
            goal="Keep retries safe",
            owner="coordinator",
        )
        self.add_task("task-99")
        link = governance.link_task(
            self.root,
            initiative_ref=initiative["initiative_ref"],
            task_id="task-99",
            relationship="deliverable",
            deliverable="artifact",
            expected_revision=initiative["revision"],
        )
        governance.link_task(
            self.root,
            initiative_ref=initiative["initiative_ref"],
            task_id="task-99",
            relationship="milestone",
            milestone="second link",
        )
        replay = governance.link_task(
            self.root,
            initiative_ref=initiative["initiative_ref"],
            task_id="task-99",
            relationship="deliverable",
            deliverable="artifact",
            expected_revision=link["expected_revision"],
        )
        self.assertEqual(replay["initiative_ref"], initiative["initiative_ref"])
        self.assertEqual(replay["relationship"], "deliverable")

    def test_records_are_revised_append_only_and_snapshots_require_policy(self) -> None:
        initiative = governance.create_initiative(
            self.root,
            initiative_ref="initiative-records",
            title="Records",
            goal="Exercise records",
            owner="coordinator",
        )
        decision = governance.create_record(
            self.root,
            record_type="decision",
            content={"choice": "A"},
            initiative_ref=initiative["initiative_ref"],
        )
        revised = governance.revise_record(self.root, record_ref=decision["record_ref"], content={"choice": "B"})
        self.assertEqual(revised["revision"], 2)
        self.assertEqual(governance.list_records(self.root, initiative_ref=initiative["initiative_ref"], active_only=True)[0]["record_ref"], revised["record_ref"])
        pending_policy = governance.create_record(
            self.root,
            record_type="policy",
            content={"name": "pending"},
            initiative_ref=initiative["initiative_ref"],
        )
        self.assertNotIn(pending_policy["record_ref"], {
            item["record_ref"] for item in governance.active_snapshot(self.root, initiative_ref=initiative["initiative_ref"])["records"]
        })
        with self.assertRaisesRegex(governance.GovernanceError, "approved policy"):
            governance.create_record(
                self.root,
                record_type="risk",
                content={"sensitive": True, "fingerprint": "sensitive-risk"},
                initiative_ref=initiative["initiative_ref"],
            )
        policy = governance.create_record(
            self.root,
            record_type="policy",
            content={"name": "approved"},
            initiative_ref=initiative["initiative_ref"],
            status="approved",
        )
        link = governance.link_record(
            self.root,
            record_ref=policy["record_ref"],
            relationship="initiative",
            initiative_ref=initiative["initiative_ref"],
        )
        replay = governance.link_record(
            self.root,
            record_ref=policy["record_ref"],
            relationship="initiative",
            initiative_ref=initiative["initiative_ref"],
            link_ref=link["link_ref"],
        )
        self.assertEqual(replay["link_ref"], link["link_ref"])
        snapshot = governance.active_snapshot(self.root, initiative_ref=initiative["initiative_ref"])
        self.assertIn(policy["record_ref"], {item["record_ref"] for item in snapshot["records"]})

        expired = governance.create_record(
            self.root,
            record_type="learning",
            content={"lesson": "old"},
            initiative_ref=initiative["initiative_ref"],
            expires_at="2000-01-01T00:00:00+00:00",
        )
        history = governance.list_records(self.root, initiative_ref=initiative["initiative_ref"], active_only=False)
        snapshot = governance.active_snapshot(self.root, initiative_ref=initiative["initiative_ref"])
        self.assertIn(expired["record_ref"], {item["record_ref"] for item in history})
        self.assertNotIn(expired["record_ref"], {item["record_ref"] for item in snapshot["records"]})

    def test_sensitive_records_require_exact_type_policy_and_bounded_content(self) -> None:
        initiative = governance.create_initiative(
            self.root,
            initiative_ref="initiative-sensitive-policy",
            title="Sensitive policy",
            goal="Keep record policy scoped",
            owner="coordinator",
        )
        with self.assertRaisesRegex(governance.GovernanceError, "sensitive governance records"):
            governance.create_record(
                self.root,
                record_type="risk",
                content={"sensitive": True, "summary": "credential exposure"},
                initiative_ref=initiative["initiative_ref"],
            )
        generic = governance.create_record(
            self.root,
            record_type="policy",
            content={"name": "generic", "retention_days": 30, "allowed_roles": ["coordinator"]},
            initiative_ref=initiative["initiative_ref"],
            status="approved",
        )
        self.assertEqual(generic["status"], "approved")
        with self.assertRaisesRegex(governance.GovernanceError, "sensitive governance records"):
            governance.create_record(
                self.root,
                record_type="risk",
                content={"sensitive": True, "summary": "credential exposure"},
                initiative_ref=initiative["initiative_ref"],
            )
        exact = governance.create_record(
            self.root,
            record_type="policy",
            content={
                "record_types": ["risk"],
                "retention_days": 30,
                "allowed_roles": ["coordinator"],
            },
            initiative_ref=initiative["initiative_ref"],
            status="approved",
        )
        self.assertEqual(exact["status"], "approved")
        accepted = governance.create_record(
            self.root,
            record_type="risk",
            content={"sensitive": True, "summary": "credential exposure"},
            initiative_ref=initiative["initiative_ref"],
        )
        self.assertEqual(accepted["record_type"], "risk")
        self.assertIsNotNone(accepted["expires_at"])
        with self.assertRaisesRegex(governance.GovernanceError, "not allowed by policy"):
            governance.create_record(
                self.root,
                record_type="risk",
                content={"sensitive": True, "summary": "credential exposure"},
                initiative_ref=initiative["initiative_ref"],
                created_by="worker",
                actor_role="worker",
            )
        with self.assertRaisesRegex(governance.GovernanceError, "within policy retention"):
            governance.create_record(
                self.root,
                record_type="risk",
                content={"sensitive": True, "summary": "credential exposure"},
                initiative_ref=initiative["initiative_ref"],
                expires_at="2099-01-01T00:00:00+00:00",
            )
        field_scoped = governance.create_initiative(
            self.root,
            initiative_ref="initiative-sensitive-fields",
            title="Sensitive fields",
            goal="Enforce the allowed sensitive record shape",
            owner="coordinator",
        )
        governance.create_record(
            self.root,
            record_type="policy",
            content={
                "record_types": ["risk"],
                "retention_days": 7,
                "allowed_roles": ["coordinator"],
                "allowed_fields": ["sensitive", "summary"],
            },
            initiative_ref=field_scoped["initiative_ref"],
            status="approved",
        )
        with self.assertRaisesRegex(governance.GovernanceError, "fields not allowed"):
            governance.create_record(
                self.root,
                record_type="risk",
                content={"sensitive": True, "summary": "bounded", "raw_value": "must not persist"},
                initiative_ref=field_scoped["initiative_ref"],
            )
        with self.assertRaisesRegex(governance.GovernanceError, "too many array items"):
            governance.create_record(
                self.root,
                record_type="learning",
                content={"items": ["bounded"] * 1025},
                initiative_ref=initiative["initiative_ref"],
            )
        nested: object = "leaf"
        for _ in range(governance.MAX_GOVERNANCE_CONTENT_DEPTH + 2):
            nested = {"next": nested}
        with self.assertRaisesRegex(governance.GovernanceError, "nesting exceeds"):
            governance.create_record(
                self.root,
                record_type="learning",
                content=nested,
                initiative_ref=initiative["initiative_ref"],
            )

    def test_close_evidence_cannot_reuse_or_import_cross_scope_artifacts(self) -> None:
        self.add_task("task-204")
        source = governance.create_initiative(
            self.root,
            initiative_ref="initiative-source",
            title="Source",
            goal="Own evidence",
            owner="owner",
        )
        target = governance.create_initiative(
            self.root,
            initiative_ref="initiative-target",
            title="Target",
            goal="Reject foreign evidence",
            owner="owner",
        )
        governance.link_task(
            self.root,
            initiative_ref=source["initiative_ref"],
            task_id="task-204",
            relationship="deliverable",
        )
        body = {
            "initiative_ref": source["initiative_ref"],
            "obligation": "oracle_evidence",
            "observed": True,
        }
        artifact = ledger_db.put_artifact(
            self.root,
            "task-204",
            kind="evidence",
            title="foreign-close-evidence.json",
            mime_type="application/json",
            content=json.dumps(body, sort_keys=True),
            immutable=True,
        )
        governance.transition_initiative(self.root, initiative_ref=target["initiative_ref"], status="active")
        governance.transition_initiative(self.root, initiative_ref=target["initiative_ref"], status="completed")
        proof = {
            "artifact_ref": artifact["artifact_ref"],
            "digest": artifact["digest_sha256"],
            "scope_ref": target["initiative_ref"],
        }
        evidence = {key: dict(proof) for key in governance._CLOSE_EVIDENCE_KEYS}
        evidence["independent_review"].update(
            {"reviewer_identity": "reviewer", "reviewer_role": "code_reviewer", "independent": True}
        )
        with self.assertRaisesRegex(governance.GovernanceError, "close requires"):
            governance.transition_initiative(
                self.root,
                initiative_ref=target["initiative_ref"],
                status="closed",
                evidence=evidence,
            )

    def test_initiative_close_requires_scoped_oracle_and_independent_review_evidence(self) -> None:
        self.add_task("task-203")
        initiative = governance.create_initiative(
            self.root,
            initiative_ref="initiative-close-evidence",
            title="Close evidence",
            goal="Require every full-governance close proof",
            owner="owner",
        )
        governance.link_task(
            self.root,
            initiative_ref=initiative["initiative_ref"],
            task_id="task-203",
            relationship="deliverable",
        )
        governance.transition_initiative(self.root, initiative_ref=initiative["initiative_ref"], status="active")
        loaded = ledger_db.load_task(self.root, "task-203")
        assert loaded is not None
        completed_state = loaded[1]
        completed_state["status"] = "completed"
        ledger_db.update_task_state(self.root, completed_state)
        governance.transition_initiative(self.root, initiative_ref=initiative["initiative_ref"], status="completed")
        current_initiative = governance.inspect_initiative(self.root, initiative["initiative_ref"])
        state = completed_state
        state["attempts"] = [{
            "attempt_id": "governance-close-1",
            "gate": "governance_close",
            "agent": "code_reviewer",
            "dispatch_ref": "dispatch-governance-close-1",
            "briefing_digest": "briefing-governance-close-1",
            "briefing_artifact_ref": "artifact-governance-close-1",
            "status": "passed",
            "invalidated": False,
            "result_baseline_ref": "baseline-close-review",
            "result_baseline_digest": "a" * 64,
        }]
        ledger_db.update_task_state(self.root, state)
        attempt_protocol.acknowledge_briefing(
            self.root,
            task_id="task-203",
            attempt_id="governance-close-1",
            dispatch_ref="dispatch-governance-close-1",
            digest="briefing-governance-close-1",
        )
        attempt_protocol.record_verification_observation(
            self.root,
            task_id="task-203",
            attempt_id="governance-close-1",
            payload={"command": "python -m unittest tests.test_governance", "exit_code": 0},
        )
        completed_result = attempt_protocol.complete_attempt(
            self.root,
            task_id="task-203",
            attempt_id="governance-close-1",
            status="completed",
            summary="Independent governance close review completed.",
            workspace_observation={
                "baseline_ref": "baseline-close-review",
                "baseline_digest_sha256": "a" * 64,
                "current_digest_sha256": "b" * 64,
                "complete": True,
                "safe_to_attribute": True,
                "changed_files": [],
            },
        )["result"]
        attempt_protocol.finalize_attempt(
            self.root, task_id="task-203", attempt_id="governance-close-1",
        )
        state["attempts"][0]["attempt_result_ref"] = completed_result["result_ref"]
        ledger_db.update_task_state(self.root, state)
        artifact_by_key = {}
        for key in governance._CLOSE_EVIDENCE_KEYS:
            body = {
                "initiative_ref": initiative["initiative_ref"],
                "obligation": key,
                "observed": True,
            }
            if key == "independent_review":
                body.update({
                    "task_id": "task-203",
                    "attempt_id": "governance-close-1",
                    "attempt_result_ref": completed_result["result_ref"],
                    "reviewer_identity": "reviewer-1",
                    "reviewed_initiative_revision": current_initiative["revision"],
                    "reviewed_task_revisions": {"task-203": 1},
                    "reviewed_artifact_digests": {
                        artifact["artifact_ref"]: artifact["digest_sha256"]
                        for artifact in artifact_by_key.values()
                    },
                })
            artifact_by_key[key] = ledger_db.put_artifact(
                self.root,
                "task-203",
                kind="evidence",
                title=f"close evidence/{key}",
                mime_type="application/json",
                content=json.dumps(body, sort_keys=True),
                immutable=True,
            )
        with self.assertRaisesRegex(governance.GovernanceError, "close requires"):
            governance.transition_initiative(
                self.root,
                initiative_ref=initiative["initiative_ref"],
                status="closed",
                evidence={},
            )

        def proof(key: str) -> dict[str, str]:
            artifact = artifact_by_key[key]
            return {
                "artifact_ref": artifact["artifact_ref"],
                "digest": artifact["digest_sha256"],
                "scope_ref": initiative["initiative_ref"],
            }

        evidence = {key: proof(key) for key in governance._CLOSE_EVIDENCE_KEYS}
        evidence["independent_review"].update(
            {"reviewer_identity": "reviewer-1"}
        )
        with self.assertRaisesRegex(governance.GovernanceError, "close requires"):
            governance.transition_initiative(
                self.root,
                initiative_ref=initiative["initiative_ref"],
                status="closed",
                evidence=evidence,
            )
        ledger_db.put_worker_session(self.root, {
            "task_id": "task-203",
            "attempt_id": "governance-close-1",
            "host_agent_id": "reviewer-1",
            "host_task_name": "code_reviewer_repository_1",
            "host_tool": "spawn_agent",
            "status": "completed",
        })
        closed = governance.transition_initiative(
            self.root,
            initiative_ref=initiative["initiative_ref"],
            status="closed",
            evidence=evidence,
        )
        self.assertEqual(closed["status"], "closed")

    def test_pending_policy_revision_does_not_supersede_approved_policy(self) -> None:
        initiative = governance.create_initiative(
            self.root,
            initiative_ref="initiative-policy-approval-order",
            title="Policy approval ordering",
            goal="Keep the approved policy active until replacement approval",
            owner="coordinator",
        )
        approved = governance.create_record(
            self.root,
            record_type="policy",
            content={"name": "current"},
            initiative_ref=initiative["initiative_ref"],
            status="approved",
        )
        pending = governance.revise_record(
            self.root,
            record_ref=approved["record_ref"],
            content={"name": "proposed replacement"},
            created_by="worker",
            actor_role="worker",
        )
        history = governance.list_records(
            self.root,
            initiative_ref=initiative["initiative_ref"],
            record_type="policy",
            active_only=False,
        )
        status_by_ref = {item["record_ref"]: item["status"] for item in history}
        self.assertEqual(status_by_ref[approved["record_ref"]], "approved")
        self.assertEqual(status_by_ref[pending["record_ref"]], "pending")
        replacement = governance.revise_record(
            self.root,
            record_ref=pending["record_ref"],
            content={"name": "proposed replacement"},
            status="approved",
            approval_basis={"actor_role": "coordinator"},
            actor_role="coordinator",
        )
        active_refs = {
            item["record_ref"] for item in governance.active_snapshot(
                self.root,
                initiative_ref=initiative["initiative_ref"],
            )["records"]
        }
        self.assertIn(replacement["record_ref"], active_refs)
        self.assertNotIn(approved["record_ref"], active_refs)
        self.assertNotIn(pending["record_ref"], active_refs)

    def test_promotion_reads_canonical_findings_and_requires_coordinator(self) -> None:
        initiative = governance.create_initiative(
            self.root,
            initiative_ref="initiative-promotion",
            title="Promotion",
            goal="Exercise promotion",
            owner="coordinator",
        )
        for number in range(1, 4):
            task_id = f"task-{number}"
            self.add_task(task_id)
            ledger_db.upsert_task_finding(
                self.root,
                task_id,
                {
                    "fingerprint": "repeat-risk",
                    "severity": "P2",
                    "status": "open",
                    "blocking": False,
                    "summary": "Repeated risk",
                },
            )
        result = governance.evaluate_promotion(
            self.root,
            fingerprint="repeat-risk",
            initiative_ref=initiative["initiative_ref"],
        )
        self.assertTrue(result["proposal_created"])
        with self.assertRaisesRegex(governance.GovernanceError, "coordinator"):
            governance.approve_promotion(self.root, proposal_ref=result["proposal"]["record_ref"], actor_role="worker")
        # Keep the canonical policy beyond the public history page.  Promotion
        # replay must use its deterministic record_ref, not list_records()
        # pagination, or a busy governance ledger makes a valid retry look
        # corrupt after the first response is lost.
        for index in range(256):
            governance.create_record(
                self.root,
                record_type="policy",
                content={"padding": index},
                initiative_ref=initiative["initiative_ref"],
                created_by="coordinator",
                status="approved",
                approval_basis={"actor_role": "coordinator", "padding": index},
                record_ref=f"record-policy-padding-{index:03d}",
                actor_role="coordinator",
            )
        approved = governance.approve_promotion(
            self.root,
            proposal_ref=result["proposal"]["record_ref"],
            actor_role="coordinator",
        )
        self.assertEqual(approved["policy"]["status"], "approved")
        replay = governance.approve_promotion(
            self.root,
            proposal_ref=result["proposal"]["record_ref"],
            actor_role="coordinator",
        )
        self.assertEqual(replay["policy"]["record_ref"], approved["policy"]["record_ref"])

    def test_governance_obligations_require_server_bound_immutable_evidence(self) -> None:
        """Typed metadata alone must never satisfy activation/close proof."""
        scope = "governance-scope-autonomous"
        kinds = (
            "acceptance_oracle_evidence",
            "risk_register",
            "falsification_strategy",
            "independent_governance_review",
            "retrospective",
            "verification_evidence",
            "audit_receipt",
        )
        metadata_only = [
            {
                "evidence_id": f"evidence-{index}",
                "digest": "caller-supplied-digest",
                "governance_scope_ref": scope,
                "governance_obligations": [kind],
                "kind": kind,
                "attempt_result_ref": "attempt-result-1",
                "server_observation": "server-observation-1",
                "verified_execution": True,
                "exit_code": 0,
                "reviewer_identity": "reviewer-1" if kind == "independent_governance_review" else None,
                "reviewer_role": "code_reviewer" if kind == "independent_governance_review" else None,
                "independent_reviewer": True if kind == "independent_governance_review" else None,
            }
            for index, kind in enumerate(kinds, 1)
        ]
        state = {
            "task_id": "task-300",
            "governance": {"effective_mode": "full", "autonomous_scope_ref": scope},
            "evidence": metadata_only,
        }
        with self.assertRaisesRegex(ValueError, "typed governance obligation evidence"):
            cortex.validate_governance_obligation_evidence(state, "governance_close")

        self.add_task("task-300")
        bound: list[dict[str, object]] = []
        for index, kind in enumerate(kinds, 1):
            evidence_id = f"evidence-{index}"
            body = {
                "task_id": "task-300",
                "gate": "governance_close",
                "attempt_id": f"attempt-{index}",
                "evidence_id": evidence_id,
                "kind": kind,
            }
            artifact = ledger_db.put_artifact(
                self.root,
                "task-300",
                kind="evidence",
                title=f"evidence/{evidence_id}.json",
                mime_type="application/json",
                content=json.dumps(body, sort_keys=True),
                immutable=True,
            )
            bound.append({
                **body,
                "digest": "receipt-digest",
                "governance_scope_ref": scope,
                "governance_obligations": [kind],
                "attempt_result_ref": "attempt-result-1",
                "server_observation": "server-observation-1",
                "verified_execution": True,
                "exit_code": 0,
                "reviewer_identity": "reviewer-1" if kind == "independent_governance_review" else None,
                "reviewer_role": "code_reviewer" if kind == "independent_governance_review" else None,
                "independent_reviewer": True if kind == "independent_governance_review" else None,
                "artifact_ref": artifact["artifact_ref"],
                "artifact_digest": artifact["digest_sha256"],
                "artifact_immutable": True,
                "artifact_verified": True,
            })
        cortex.validate_governance_obligation_evidence(
            {**state, "evidence": bound},
            "governance_close",
            artifact_root=self.root,
        )


if __name__ == "__main__":
    unittest.main()
