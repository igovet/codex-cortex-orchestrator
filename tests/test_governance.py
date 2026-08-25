"""Focused acceptance coverage for the v11 governance ledger and resolver."""
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
from cortex_runtime import attempt_protocol, governance, ledger_db


class GovernanceAcceptanceTests(HostPrivateControlStoreTestMixin, unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.set_up_host_private_control_store()
        # This direct ledger-db fixture is intentionally outside the workspace
        # mapping: public runtime calls in this suite must use the private host
        # mapping rather than mutating an incidental project location.
        self.root = Path(self.temp.name) / "governance-ledger"
        ledger_db.ensure_database(self.root)
        ledger_db._governance_lifecycle_hmac_key(self.root, create=True)

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
            governance.classify_governance(complexity="C1", objective="author a plain document")["chosen_mode"],
            "minimal",
        )
        self.assertEqual(
            governance.classify_governance(complexity="C2", requested_mode="required")["chosen_mode"],
            "full",
        )
        triggered = governance.classify_governance(complexity="C1", objective="rotate an API key")
        self.assertEqual(triggered["chosen_mode"], "full")
        self.assertTrue(any(item["trigger"] == "credentials" for item in triggered["trigger_evidence"]))
        numeric_metadata = governance.classify_governance(
            complexity="C1",
            task={"repository_count": 4, "related_task_count": 5},
        )
        self.assertEqual(numeric_metadata["chosen_mode"], "minimal")
        explicit_multi_scope = governance.classify_governance(
            complexity="C1",
            task={"multiple_repositories": True},
        )
        self.assertEqual(explicit_multi_scope["chosen_mode"], "full")
        c2_off = governance.classify_governance(complexity="C2", requested_mode="off")
        self.assertEqual(c2_off["chosen_mode"], "minimal")
        self.assertTrue(c2_off["policy_advisory"])
        incomplete_off = governance.classify_governance(
            complexity="C1",
            requested_mode="off",
            objective="Perform a routine local maintenance adjustment.",
        )
        self.assertTrue(incomplete_off["policy_advisory"])
        off = governance.classify_governance(
            complexity="C1",
            requested_mode="off",
            objective="Perform a routine local maintenance adjustment.",
            task={"risk_triggers": self.no_risk_assessment()},
        )
        self.assertEqual(off["chosen_mode"], "minimal")
        self.assertEqual(off["policy_snapshot"]["off_assessment"], self.no_risk_assessment())
        custom_policy = governance.classify_governance(
            complexity="C1",
            requested_mode="off",
            objective="Perform a routine local maintenance adjustment.",
            task={"risk_triggers": self.no_risk_assessment()},
            policy={"schema": "custom-policy/v1"},
        )
        self.assertEqual(custom_policy["policy_snapshot"]["off_assessment"], self.no_risk_assessment())

    def test_unicode_oversized_approval_basis_is_stored_without_a_content_quota(self) -> None:
        """Approval basis is canonical durable content, not a size-gated side channel."""
        self.add_task("task-1")
        oversized = ["🙂" * 16_000] * 5
        record = governance.create_record(
            self.root,
            record_type="decision",
            task_id="task-1",
            content={"decision": "lossless"},
            approval_basis={"unicode": oversized},
        )
        self.assertEqual(record["approval_basis_json"], {"unicode": oversized})
        with ledger_db._connection(self.root) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM governance_records").fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM governance_record_lifecycle").fetchone()[0], 1)
            self.assertGreater(connection.execute("SELECT byte_size FROM artifact_blobs ORDER BY rowid DESC LIMIT 1").fetchone()[0], 1)

    def test_c3_floor_and_explicit_risk_triggers_cannot_be_lowered(self) -> None:
        self.assertEqual(
            governance.classify_governance(complexity="C3", requested_mode="auto")["chosen_mode"],
            "full",
        )
        c3_off = governance.classify_governance(complexity="C3", requested_mode="off")
        self.assertEqual(c3_off["chosen_mode"], "minimal")
        self.assertTrue(c3_off["policy_advisory"])
        self.assertEqual(
            governance.classify_governance(
                complexity="C2", task={"risk_triggers": ["destructive"]}
            )["chosen_mode"],
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
                self.assertEqual(resolved["chosen_mode"], "full")
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
        self.assertEqual([wave["delegations"][0]["gate"] for wave in waves], ["implementation"])
        self.assertEqual(len(waves), len(ordinary))
        with_close = cortex._append_governance_waves(
            ordinary + [{"wave_id": "wave-close", "delegations": [{"gate": "close", "agent": "build_verification"}]}],
            task,
        )
        self.assertEqual(
            [wave["delegations"][0]["gate"] for wave in with_close],
            ["implementation", "close"],
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
            ["implementation", "governance_activation", "close", "governance_close"],
        )
        self.assertEqual(
            cortex._append_governance_waves(
                [{"wave_id": "bad", "delegations": [{"gate": "governance_activation", "agent": "general"}]}],
                task,
            )[0]["delegations"][0]["agent"],
            "general",
        )

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
        self.assertEqual(activation["call"], "spawn_agent")
        ledger = cortex.ledger_root({"project_root": str(project)})
        task_dir = next((ledger / "tasks").iterdir())
        state = cortex.load_task_state_for_artifact(task_dir)
        attempt = next(item for item in state["attempts"] if item.get("gate") == "implementation")
        briefing = (task_dir / str(attempt["briefing_file"])).read_text(encoding="utf-8")
        self.assertIn("Prompt volume targets are advisory worker guidance only", briefing)
        briefing_route = [
            "Ask worker_question only for an explicit requirement",
            "Record material evidence before completion",
            "complete_attempt ok=true terminal=true ends all task-scoped calls",
            "Return exactly ATTEMPT_COMPLETED",
        ]
        positions = [briefing.index(marker) for marker in briefing_route]
        self.assertEqual(positions, sorted(positions))

        # The full static resume sequence now lives once in the installed
        # profile contract instead of being repeated in every worker briefing.
        profiles = json.loads((Path(cortex.__file__).parents[1] / "profiles.json").read_text(encoding="utf-8"))
        question_route = profiles["shared_worker_contract"]["question_resume_contract"]
        route = [
            "worker_question(action=ask)", "QUESTION_RECORDED", "followup_task",
            "worker_question({action:'poll'", "record_attempt_event", "complete_attempt",
            "pending poll returns QUESTION_RECORDED", "no OTHER_TERMINAL",
        ]
        route_positions = [question_route.index(marker) for marker in route]
        self.assertEqual(route_positions, sorted(route_positions))

    def test_minimal_and_light_modes_preserve_the_existing_pipeline(self) -> None:
        ordinary = [{"wave_id": "wave-01", "delegations": [{"gate": "implementation", "agent": "general"}]}]
        for mode in ("minimal", "light"):
            with self.subTest(mode=mode):
                self.assertEqual(
                    cortex._append_governance_waves(ordinary, {"governance": {"effective_mode": mode}}),
                    ordinary,
                )

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

        def durable(project: Path) -> tuple[dict[str, object], dict[str, object]]:
            ledger = cortex.ledger_root({"project_root": str(project)})
            task_dir = next((ledger / "tasks").iterdir())
            state = cortex.load_task_state_for_artifact(task_dir)
            return state, cortex._load_orchestrate_plan(task_dir, state)

        c2_off = start(Path(self.temp.name) / "c2-off", "Write a plain plan.", "C2", "off")
        self.assertTrue(c2_off["ok"], c2_off)
        c2_state, _ = durable(Path(self.temp.name) / "c2-off")
        self.assertTrue(c2_state["governance"]["policy_advisory"])
        self.assertEqual(c2_state["governance"]["chosen_mode"], "minimal")
        c3_path = Path(self.temp.name) / "c3"
        c3 = start(c3_path, "Review a high-impact change.", "C3", "auto")
        self.assertTrue(c3["ok"], c3)
        c3_state, c3_plan = durable(c3_path)
        self.assertEqual(c3_state["governance"]["chosen_mode"], "full")
        self.assertEqual(
            [wave["delegations"][0]["gate"] for wave in c3_plan["waves"] if wave["delegations"]],
            ["scope", "discover", "architecture", "plan", "implementation", "qa", "review", "documentation", "close"],
        )
        triggered_path = Path(self.temp.name) / "triggered"
        triggered = start(triggered_path, "Rotate an API key.", "C1", "auto")
        self.assertTrue(triggered["ok"], triggered)
        self.assertEqual(durable(triggered_path)[0]["governance"]["chosen_mode"], "full")
        required_path = Path(self.temp.name) / "required"
        required = start(required_path, "Write a plain note.", "C1", "required")
        self.assertTrue(required["ok"], required)
        self.assertEqual(durable(required_path)[0]["governance"]["chosen_mode"], "full")

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
        self.assertEqual(started["step"], 1)
        ledger = cortex.ledger_root({"project_root": str(project)})
        task_dir = next((ledger / "tasks").iterdir())
        state = cortex.load_task_state_for_artifact(task_dir)
        plan = cortex._load_orchestrate_plan(task_dir, state)
        self.assertEqual(state["governance"]["requested_mode"], "auto")
        self.assertEqual(state["governance"]["chosen_mode"], "full")
        self.assertEqual(
            [wave["wave_id"] for wave in plan["waves"]],
            ["wave-01", "wave-02", "wave-03"],
        )
        self.assertEqual(
            [wave["delegations"][0]["gate"] for wave in plan["waves"]],
            [
                "implementation",
                "documentation",
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
        large_record = governance.create_record(
            self.root,
            record_type="learning",
            content={"items": ["lossless"] * 1025},
            initiative_ref=initiative["initiative_ref"],
        )
        self.assertEqual(len(large_record["content_json"]["items"]), 1025)
        nested: object = "leaf"
        for _ in range(40):
            nested = {"next": nested}
        nested_record = governance.create_record(
            self.root,
            record_type="learning",
            content=nested,
            initiative_ref=initiative["initiative_ref"],
        )
        self.assertEqual(nested_record["content_json"], nested)

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
        # A cross-scope artifact is an objective integrity violation and
        # remains fail-closed even though omitted governance evidence is only
        # an advisory.
        with self.assertRaisesRegex(governance.GovernanceError, "not linked to the initiative"):
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
        missing = governance.transition_initiative(
                self.root,
                initiative_ref=initiative["initiative_ref"],
                status="closed",
                evidence={},
            )
        self.assertFalse(missing["applied"])
        self.assertTrue(any(item["code"] == "close_evidence_required" for item in missing["advisories"]))

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
        with ledger_db.connection(self.root) as connection:
            self.assertIsNone(connection.execute(
                "SELECT 1 FROM worker_sessions WHERE task_id=? AND attempt_id=?",
                ("task-203", "governance-close-1"),
            ).fetchone())
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
