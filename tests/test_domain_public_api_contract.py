from __future__ import annotations

from plan_fixtures import ordinary_candidates
import hashlib
import re
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from cortex import PUBLIC_TOOLS, SERVER_VERSION
from cortex_runtime.domain_api import (
    assess_governance, close_task, open_assignment, open_clarification,
    open_plan_review, open_steering, open_task, publish_documentation,
    publish_plan, publish_result, read_continuations, read_evidence, read_outcome,
    read_scope, read_state, read_task, read_timeline, record_clarification,
    record_plan_review, record_steering,
)
from cortex_runtime.v12_service import V12ServiceError
from cortex_runtime.v12_store import V12Store


PROVENANCE = {name: "sha256:" + "a" * 64 for name in ("build_digest", "candidate_digest", "source_digest", "catalogue_digest")}


class DomainPublicApiContractTests(unittest.TestCase):
    def assert_no_nested_transport_markers(self, value: object) -> None:
        if isinstance(value, dict):
            self.assertNotIn("has_more", value)
            self.assertNotIn("task_ref", value)
            for key in value:
                self.assertNotIn("cursor", str(key).lower())
            for item in value.values():
                self.assert_no_nested_transport_markers(item)
        elif isinstance(value, list):
            for item in value:
                self.assert_no_nested_transport_markers(item)

    def _task(self, root: str, outcomes: list[dict] | None = None) -> tuple[dict, list[dict]]:
        outcomes = outcomes or [{"outcome": "Build the artifact.", "acceptance": ["The artifact works."], "constraints": [], "verification": []}]
        task = open_task(project_root=root, request_original="Build it.", user_language="en", outcomes=outcomes, constraints=["Keep public identity minimal."])
        assess_governance(task_ref=task["task_ref"], mode="minimal", rationale="Bounded test fixture.")
        return task, outcomes

    def _assignment(self, task_ref: str, outcome: dict, role: str) -> dict:
        context = {}
        scope = read_scope(task_ref=task_ref, responsibility="evidence", _connection_context=context)
        ready = [node for node in scope["data"]["nodes"] if node["state"] == "ready"]
        self.assertEqual(len(ready), 1)
        return open_assignment(task_ref=task_ref, profile_name="explorer", model="gpt-5.6-luna",
                               reasoning_effort="high", nodes=[ready[0]["node"]], _connection_context=context)

    def _publish_result(self, worker_ref: str, outcome: dict, context: dict) -> dict:
        from cortex_runtime.domain_api import _resolve_task_context
        from cortex_runtime import graph_ledger
        from test_graph_ledger import observation
        store, _, assignment_id, _ = _resolve_task_context(worker_ref)
        nodes = store._read(lambda c: graph_ledger.assignment_scope(c, assignment_id)["nodes"])
        return publish_result(
            task_ref=worker_ref,
            summary="Recovered worker result.",
            outcome="The assigned result is complete.",
            changes=[],
            node_coverage=[{"node": node["key"], "coverage": [{**subject, "status": "complete",
                "verification": [{"check_key": check["key"], "state": "executed", "summary": "Focused recovery check passed."}
                                 for check in node["checks"]]}
                for subject in ([{"kind": "contribution", "name": name} for name in node["contributions"]] + node["verifies"])]} for node in nodes],
            artifact=observation(),
            documentation_impact="Recovery behavior is covered by repository documentation.",
            risks=[], unresolved=[], status="completed",
            _connection_context=context,
        )

    @staticmethod
    def _consume_dispatch(dispatch):
        ref = re.search(r'"task_ref":"(t_[0-9a-f]{12}_[0-9a-f]{32})"', dispatch["native_dispatch"]["message"]).group(1)
        context = {}
        page = read_task(task_ref=ref, _connection_context=context)
        while page["has_more"]:
            page = read_task(task_ref=ref, continue_=True, _connection_context=context)
        return ref, context

    def _prepared_plan(self, task_ref, outcomes, *, risks=None, unresolved=None):
        """Publish one structurally valid, not yet independently validated plan."""
        from test_execution_graph_integrity import graph, node
        from test_graph_ledger import observation
        consume = self._consume_dispatch
        baseline, context = consume(self._assignment(task_ref, outcomes[0], "baseline"))
        self._publish_result(baseline, outcomes[0], context)
        coordinator = {}
        read_scope(task_ref=task_ref, responsibility="planning", _connection_context=coordinator)
        planner = open_assignment(task_ref=task_ref, profile_name="planner", model="gpt-5.6-luna",
            reasoning_effort="high", bootstrap={"kind": "planning"}, _connection_context=coordinator)
        worker_ref, context = consume(planner)
        value = graph()
        value["nodes"] = []
        value["outcomes"] = []
        for index, outcome in enumerate(outcomes):
            item = node(f"inspect-{index}", contribution=f"evidence-{index}", provides=[f"inspected-{index}"])
            item.update(execution_mode="read_only", mutation_domains=[])
            value["nodes"].append(item)
            value["outcomes"].append({"outcome": outcome["outcome"], "all_of": [f"evidence-{index}"]})
        publish_plan(task_ref=worker_ref, _connection_context=context, status="partial" if unresolved else "completed", summary="Independent inspections",
            scope="Inspect each assigned surface", candidates=ordinary_candidates(value), artifact=observation(), risks=risks or [], unresolved=unresolved or [])
        return worker_ref, context

    def _parallel_assignments(self, task_ref: str, outcomes: list[dict]) -> list[dict]:
        """Prepare actual independent read-only contribution nodes before fanout."""
        self._prepared_plan(task_ref, outcomes)
        consume = self._consume_dispatch
        validator, context = consume(self._assignment(task_ref, outcomes[0], "validation"))
        self._publish_result(validator, outcomes[0], context)
        def dispatch(index):
            local = {}
            read_scope(task_ref=task_ref, responsibility="delivery", _connection_context=local)
            return open_assignment(task_ref=task_ref, profile_name="explorer", model="gpt-5.6-luna",
                reasoning_effort="high", nodes=[f"inspect-{index}"], _connection_context=local)
        with ThreadPoolExecutor(max_workers=len(outcomes)) as pool:
            return list(pool.map(dispatch, range(len(outcomes))))

    def _approved_repair_flow(self, *, audit):
        from copy import deepcopy
        from cortex_runtime import graph_ledger
        from test_execution_graph_integrity import node
        from test_graph_ledger import observation
        from test_typed_publication_transaction import baseline_content
        with tempfile.TemporaryDirectory() as root, patch("cortex_runtime.domain_api._worker_capability_provenance", return_value=PROVENANCE):
            task, outcomes = self._task(root)
            ref = task["task_ref"]
            assess_governance(task_ref=ref, mode="full", rationale="Approved material-risk fixture with bounded repair policy.")
            original = publish_plan
            def plan_with_audit(**kwargs):
                value = deepcopy(kwargs["candidates"][0]["graph"])
                value["nodes"][0].update(execution_mode="mutating", mutation_domains=["src"])
                if audit:
                    value["nodes"][0]["provides"].append("artifact")
                    value["nodes"][0]["remediation"]["restores"].append("artifact")
                    check = node("verify", audit=True, requires=["artifact"], provides=["verified"],
                        dependencies=[("inspect-0", ["artifact"])])
                    check["verifies"] = [{"kind": "outcome", "name": outcomes[0]["outcome"]}]
                    value["nodes"].append(check)
                return original(**{**kwargs, "candidates": ordinary_candidates(value)})
            with patch(__name__ + ".publish_plan", side_effect=plan_with_audit):
                self._prepared_plan(ref, outcomes)
            validator, context = self._consume_dispatch(self._assignment(ref, outcomes[0], "validation"))
            self._publish_result(validator, outcomes[0], context)
            open_plan_review(task_ref=ref, prompt="Review the plan and bounded repair policy.", prompt_language="en")
            record_plan_review(task_ref=ref, outcome="approve", response_original="Approve this plan and its bounded repairs.", user_language="en")
            store = V12Store(Path(root))
            def run(key, responsibility, *, failed=False, start="a" * 64, end="a" * 64):
                coordinator = {}
                read_scope(task_ref=ref, responsibility=responsibility, _connection_context=coordinator)
                assignment = open_assignment(task_ref=ref, nodes=[key], profile_name="general", model="gpt-5.6-luna",
                    reasoning_effort="high", _connection_context=coordinator)
                worker, context = self._consume_dispatch(assignment)
                definition = store._read(lambda c: graph_ledger.assignment_scope(c, context["assignment_id"])["nodes"][0])
                content = baseline_content()
                subjects = [{"kind": "contribution", "name": name} for name in definition["contributions"]] + definition["verifies"]
                content["node_coverage"] = [{"node": key, "coverage": [{**subject, "status": "failed" if failed else "complete",
                    "verification": [{"check_key": check["key"], "state": "failed" if failed else "executed", "summary": "Observed bounded source check.",
                        **({"classification": "defect_within_contract"} if failed else {})} for check in definition["checks"]]} for subject in subjects]}]
                content["status"] = "failed" if failed else "completed"
                content["artifact"] = observation(start, end)
                result = publish_result(task_ref=worker, _connection_context=context, **content)
                self.assertTrue(result["published"])
                self.assertFalse(result["replayed"])
                return context["assignment_id"]
            if audit:
                run("inspect-0", "delivery")
            source = run("verify" if audit else "inspect-0", "evidence" if audit else "delivery", failed=True)
            immutable = store._read(lambda c: c.execute("SELECT payload_json FROM execution_publications WHERE assignment_id=?", (source,)).fetchone()[0])
            repairs = read_scope(task_ref=ref, responsibility="delivery")["data"]["nodes"]
            repair = next(item["node"] for item in repairs if item["state"] == "ready" and item["node"].startswith("repair-"))
            run(repair, "delivery", end="b" * 64)
            regressions = read_scope(task_ref=ref, responsibility="evidence")["data"]["nodes"]
            regression = next(item["node"] for item in regressions if item["state"] == "ready" and item["node"].startswith("regression-"))
            run(regression, "evidence", start="b" * 64, end="b" * 64)
            self.assertEqual(read_state(task_ref=ref)["data"]["effective_revision"], 1)
            self.assertEqual(read_state(task_ref=ref)["data"]["coverage_status_counts"], {"complete": 1})
            self.assertEqual(store._read(lambda c: c.execute("SELECT payload_json FROM execution_publications WHERE assignment_id=?", (source,)).fetchone()[0]), immutable)
            self.assertEqual(store._read(lambda c: c.execute("SELECT COUNT(*) FROM user_decisions").fetchone()[0]), 1)

    def test_flat_task_open_and_state_read_expose_only_task_ref_identity(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            task, outcomes = self._task(root)
            self.assertEqual(set(task), {"task_ref", "replayed"})
            scope = read_scope(task_ref=task["task_ref"], responsibility="delivery")
            self.assertEqual([item["outcome"] for item in scope["data"]["outcomes"]], [item["outcome"] for item in outcomes])
            exact = [read_outcome(task_ref=task["task_ref"], outcome=item["outcome"])["data"]["outcome"] for item in outcomes]
            self.assertEqual(exact, outcomes)
            rendered = repr((scope, exact))
            for name in ("item_ref", "report_ref", "decision_ref", "digest", "cursor", "handles"):
                self.assertNotIn(name, rendered)

    def test_explicit_user_change_records_directly_without_redundant_steering_question(self) -> None:
        original = {
            "outcome": "Place the marker at the old position.",
            "acceptance": ["The old position is used."],
            "constraints": [],
            "verification": ["Inspect the marker."],
        }
        replacement = {
            "outcome": "Place the marker slightly below the knee.",
            "acceptance": ["The marker is below the knee on both models."],
            "constraints": ["Do not rewrite stored marker data."],
            "verification": ["Inspect both models."],
        }
        with tempfile.TemporaryDirectory() as root:
            task, _ = self._task(root, [original])
            context: dict = {}
            read_scope(
                task_ref=task["task_ref"], responsibility="delivery",
                _connection_context=context,
            )
            arguments = {
                "task_ref": task["task_ref"],
                "response_original": "Move it slightly below the knee on both models.",
                "user_language": "en", "add": [replacement],
                "retire": [original["outcome"]],
                "_connection_context": context,
            }
            first = record_steering(**arguments)
            replay = record_steering(**arguments)
            self.assertEqual(first["state"], "steering_recorded")
            self.assertFalse(first["replayed"])
            self.assertTrue(replay["replayed"])
            current = read_outcome(
                task_ref=task["task_ref"], outcome=replacement["outcome"],
            )
            self.assertEqual(current["data"]["outcome"], replacement)
            follow_up = {
                "outcome": "Keep the existing marker data unchanged.",
                "acceptance": ["Stored rows remain byte-for-byte unchanged."],
                "constraints": [],
                "verification": ["Compare stored rows before and after."],
            }
            second = record_steering(
                task_ref=task["task_ref"],
                response_original="Also preserve every stored marker row.",
                user_language="en", add=[follow_up], retire=[],
                _connection_context=context,
            )
            self.assertFalse(second["replayed"])
            self.assertEqual(
                read_outcome(
                    task_ref=task["task_ref"], outcome=follow_up["outcome"],
                )["data"]["outcome"],
                follow_up,
            )

    def test_empty_steering_delta_is_rejected_without_a_revision(self) -> None:
        outcome = {
            "outcome": "Keep the existing behavior.",
            "acceptance": ["The current behavior remains unchanged."],
            "constraints": [],
            "verification": ["Observe the effective revision."],
        }
        with tempfile.TemporaryDirectory() as root:
            task, _ = self._task(root, [outcome])
            with self.assertRaises(V12ServiceError) as rejected:
                record_steering(
                    task_ref=task["task_ref"],
                    response_original="No change.",
                    user_language="en",
                    add=[],
                    retire=[],
                )
            self.assertEqual(rejected.exception.code, "invalid_argument")
            self.assertEqual(rejected.exception.details.get("reason"), "semantic_noop")
            self.assertEqual(
                read_state(task_ref=task["task_ref"])["data"]["effective_revision"],
                1,
            )

    def test_direct_change_after_consumed_steering_binding_opens_fresh_binding(self) -> None:
        """A later user change must not replay the previous steering answer."""
        original = {
            "outcome": "Keep the original behavior.",
            "acceptance": ["The original behavior remains available."],
            "constraints": [],
            "verification": ["Run the original check."],
        }
        first = {
            "outcome": "Add the first requested behavior.",
            "acceptance": ["The first behavior is available."],
            "constraints": [],
            "verification": ["Run the first check."],
        }
        second = {
            "outcome": "Add the second requested behavior.",
            "acceptance": ["The second behavior is available."],
            "constraints": [],
            "verification": ["Run the second check."],
        }
        with tempfile.TemporaryDirectory() as root:
            task, _ = self._task(root, [original])
            open_steering(
                task_ref=task["task_ref"], prompt="Add the first behavior?",
                prompt_language="en",
            )
            first_record = record_steering(
                task_ref=task["task_ref"], response_original="Add the first behavior.",
                user_language="en", add=[first], retire=[],
            )
            self.assertFalse(first_record["replayed"])

            # No new open_steering call is made: this is a direct user-authored
            # change following the already-consumed first binding.
            second_record = record_steering(
                task_ref=task["task_ref"], response_original="Also add the second behavior.",
                user_language="en", add=[second], retire=[],
            )
            self.assertFalse(second_record["replayed"])
            names = [
                item["outcome"]
                for item in read_scope(
                    task_ref=task["task_ref"], responsibility="delivery",
                )["data"]["outcomes"]
            ]
            self.assertEqual(names, [original["outcome"], first["outcome"], second["outcome"]])

    def test_state_is_one_shot_and_timeline_is_newest_first_with_one_marker(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            task, _outcomes = self._task(root)
            for index in range(20):
                record_steering(
                    task_ref=task["task_ref"], response_original=f"Add independent requirement {index}.",
                    user_language="en", retire=[], add=[{
                        "outcome": f"Independent requirement {index}", "acceptance": [],
                        "constraints": [], "verification": [],
                    }],
                )

            context: dict = {}
            state = read_state(task_ref=task["task_ref"], _connection_context=context)
            self.assertNotIn("has_more", state)
            self.assertNotIn("timeline", state["data"])
            self.assertEqual(set(state["data"]), {
                "effective_revision", "coverage_status", "outcome_count",
                "coverage_status_counts", "node_state_counts", "unfinished_assignment_count",
                "recovery_required", "reconciliation_required", "reconciliation_epoch",
                "finalized_report_count", "completed_report_count",
                "artifact_generation_present", "admissible_operations",
                "closure_record_status", "closure_verdict",
            })
            self.assertNotIn("Build it.", repr(state))
            self.assertNotIn("Build the artifact.", repr(state))
            self.assertEqual(context["steering_state_read_task_ref"], task["task_ref"])

            page = read_timeline(task_ref=task["task_ref"], _connection_context=context)
            self.assertTrue(page["has_more"])
            self.assert_no_nested_transport_markers(page["data"])
            self.assertNotIn("next_sequence", page["data"])

            timeline = list(page["data"]["timeline"])
            while page["has_more"]:
                page = read_timeline(
                    task_ref=task["task_ref"], continue_=True,
                    _connection_context=context,
                )
                self.assert_no_nested_transport_markers(page["data"])
                self.assertNotIn("next_sequence", page["data"])
                timeline.extend(page["data"]["timeline"])

            self.assertEqual(
                [item["sequence"] for item in timeline],
                sorted({item["sequence"] for item in timeline}, reverse=True),
            )
            with self.assertRaises(V12ServiceError) as rejected:
                read_timeline(
                    task_ref=task["task_ref"], continue_=True,
                    _connection_context=context,
                )
            self.assertEqual(rejected.exception.code, "report_cursor_invalid")

    def test_assignment_read_has_only_the_top_level_pagination_marker(self) -> None:
        with tempfile.TemporaryDirectory() as root, patch(
            "cortex_runtime.domain_api._worker_capability_provenance",
            return_value=PROVENANCE,
        ):
            task, outcomes = self._task(root)
            assignment = self._assignment(task["task_ref"], outcomes[0], "marker audit")
            worker_ref = re.search(
                r'"task_ref":"(t_[0-9a-f]{12}_[0-9a-f]{32})"',
                assignment["native_dispatch"]["message"],
            ).group(1)
            page = read_task(
                task_ref=worker_ref,
                _connection_context={},
            )
            self.assertFalse(page["has_more"])
            self.assert_no_nested_transport_markers(page["data"])
            self.assertEqual(repr(page).count("task_ref"), 1)

    def test_worker_assignment_contains_only_its_selected_outcome(self) -> None:
        outcomes = [
            {"outcome": "Inspect the selected surface.", "acceptance": ["Selected evidence exists."], "constraints": [], "verification": []},
            {"outcome": "Keep the unrelated surface private.", "acceptance": ["It is not sent to this worker."], "constraints": [], "verification": []},
        ]
        with tempfile.TemporaryDirectory() as root, patch(
            "cortex_runtime.domain_api._worker_capability_provenance",
            return_value=PROVENANCE,
        ):
            task, _ = self._task(root, outcomes)
            assignment = self._parallel_assignments(task["task_ref"], outcomes)[0]
            worker_ref = re.search(
                r'"task_ref":"(t_[0-9a-f]{12}_[0-9a-f]{32})"',
                assignment["native_dispatch"]["message"],
            ).group(1)
            page = read_task(task_ref=worker_ref, _connection_context={})
            assigned = page["data"]["contract_context"]["outcomes"]
            self.assertEqual(assigned, [outcomes[0]])
            self.assertNotIn(outcomes[1]["outcome"], repr(page))

    def test_state_binds_exact_outcomes_to_post_steering_delivery_assignability(self):
        outcomes = [{"outcome": name, "acceptance": [], "constraints": [], "verification": []} for name in ("API", "Documentation")]
        with tempfile.TemporaryDirectory() as root, patch("cortex_runtime.domain_api._worker_capability_provenance", return_value=PROVENANCE):
            task, _ = self._task(root, outcomes)
            ref = task["task_ref"]
            assignments = self._parallel_assignments(ref, outcomes)
            workers = [self._consume_dispatch(assignment) for assignment in assignments]
            before = read_state(task_ref=ref)["data"]["finalized_report_count"]
            added = {"outcome": "Export", "acceptance": ["The export works."], "constraints": [], "verification": []}
            record_steering(task_ref=ref, response_original="Also implement Export.", user_language="en", add=[added], retire=[])
            state = read_state(task_ref=ref)["data"]
            self.assertEqual(state["coverage_status_counts"], {"unverified": 3})
            self.assertTrue(state["reconciliation_required"])
            scope = read_scope(task_ref=ref, responsibility="delivery")
            self.assertEqual([item["outcome"] for item in scope["data"]["outcomes"]], ["API", "Documentation", "Export"])
            self.assertTrue(all(item["state"] != "ready" for item in scope["data"]["nodes"]))
            for (worker, context), outcome in zip(workers, outcomes):
                result = self._publish_result(worker, outcome, context)
                self.assertEqual(result["state"], "superseded")
            self.assertEqual(read_state(task_ref=ref)["data"]["finalized_report_count"], before)

    def test_completed_node_cannot_be_claimed_again_without_a_new_authorized_route(self):
        with tempfile.TemporaryDirectory() as root, patch("cortex_runtime.domain_api._worker_capability_provenance", return_value=PROVENANCE):
            task, outcomes = self._task(root)
            ref = task["task_ref"]
            assigned = self._parallel_assignments(ref, outcomes)[0]
            worker, context = self._consume_dispatch(assigned)
            self._publish_result(worker, outcomes[0], context)
            coordinator = {}
            scope = read_scope(task_ref=ref, responsibility="delivery", _connection_context=coordinator)
            self.assertEqual(scope["data"]["nodes"][0]["state"], "complete")
            with self.assertRaises(V12ServiceError):
                open_assignment(task_ref=ref, nodes=["inspect-0"], profile_name="general",
                    model="gpt-5.6-luna", reasoning_effort="high", _connection_context=coordinator)
            refined = {**outcomes[0], "acceptance": [*outcomes[0]["acceptance"], "Also satisfy the new user requirement."]}
            record_steering(task_ref=ref, response_original="Also satisfy the new user requirement.", user_language="en",
                add=[refined], retire=[outcomes[0]["outcome"]], _connection_context=coordinator)
            self.assertEqual(read_outcome(task_ref=ref, outcome=refined["outcome"])["data"]["outcome"], refined)
            self.assertEqual(read_state(task_ref=ref)["data"]["effective_revision"], 2)

    def test_assignment_requires_governance_assessment(self) -> None:
        outcome = {"outcome": "Inspect the artifact.", "acceptance": ["Evidence is returned."], "constraints": [], "verification": []}
        with tempfile.TemporaryDirectory() as root, patch("cortex_runtime.domain_api._worker_capability_provenance", return_value=PROVENANCE):
            task = open_task(
                project_root=root, request_original="Inspect it.", user_language="en",
                outcomes=[outcome], constraints=["Read-only inspection."],
            )
            with self.assertRaisesRegex(V12ServiceError, "governance assessment is required"):
                self._assignment(task["task_ref"], outcome, "pre-governance")

    def test_worker_scoped_task_ref_cannot_assess_governance(self) -> None:
        outcome = {"outcome": "Inspect the artifact.", "acceptance": ["Evidence is returned."], "constraints": [], "verification": []}
        with tempfile.TemporaryDirectory() as root, patch(
            "cortex_runtime.domain_api._worker_capability_provenance", return_value=PROVENANCE,
        ):
            task, _ = self._task(root, [outcome])
            assignment = self._assignment(task["task_ref"], outcome, "planner boundary")
            worker_ref = re.search(
                r'"task_ref":"(t_[0-9a-f]{12}_[0-9a-f]{32})"',
                assignment["native_dispatch"]["message"],
            ).group(1)

            # Even a schema-complete call cannot turn a native worker into a
            # coordinator or append governance state through its scoped ref.
            with self.assertRaises(V12ServiceError) as rejected:
                assess_governance(
                    task_ref=worker_ref,
                    mode="full",
                    rationale="A worker must not own this lifecycle decision.",
                )
            self.assertEqual(rejected.exception.code, "wrong_connection")

    def test_full_governance_delivery_requires_exact_current_plan_approval(self):
        outcome = {"outcome": "Deliver the secured change.", "acceptance": ["The secured flow works."], "constraints": [], "verification": []}
        with tempfile.TemporaryDirectory() as root, patch("cortex_runtime.domain_api._worker_capability_provenance", return_value=PROVENANCE):
            task, _ = self._task(root, [outcome])
            ref = task["task_ref"]
            assess_governance(task_ref=ref, mode="full", rationale="Authentication-sensitive delivery.")
            self._prepared_plan(ref, [outcome])
            context = {}
            read_scope(task_ref=ref, responsibility="delivery", _connection_context=context)
            with self.assertRaises(V12ServiceError):
                open_assignment(task_ref=ref, nodes=["inspect-0"], profile_name="general",
                    model="gpt-5.6-luna", reasoning_effort="high", _connection_context=context)
            validator, worker = self._consume_dispatch(self._assignment(ref, outcome, "validator"))
            self._publish_result(validator, outcome, worker)
            scope = read_scope(task_ref=ref, responsibility="delivery", _connection_context=context)
            self.assertEqual(scope["data"]["nodes"][0]["state"], "waiting")
            open_plan_review(task_ref=ref, prompt="Approve the secured plan?", prompt_language="en")
            record_plan_review(task_ref=ref, outcome="approve", response_original="Approve the secured plan.", user_language="en")
            read_scope(task_ref=ref, responsibility="delivery", _connection_context=context)
            approved = open_assignment(task_ref=ref, nodes=["inspect-0"], profile_name="general",
                model="gpt-5.6-luna", reasoning_effort="high", _connection_context=context)
            self.assertIn("native_dispatch", approved)

    def test_ordinary_light_governance_bounded_plan_is_informational(self):
        with tempfile.TemporaryDirectory() as root, patch("cortex_runtime.domain_api._worker_capability_provenance", return_value=PROVENANCE):
            task, outcomes = self._task(root)
            ref = task["task_ref"]
            assess_governance(task_ref=ref, mode="light", rationale="Complex but fully specified local work.", risk_factors=[])
            self._prepared_plan(ref, outcomes)
            evidence = read_evidence(task_ref=ref, report_policy="active_plan")
            self.assertEqual(evidence["data"]["reports"][0]["review_policy"], "informational")
            validator, context = self._consume_dispatch(self._assignment(ref, outcomes[0], "validator"))
            self._publish_result(validator, outcomes[0], context)
            coordinator = {}
            scope = read_scope(task_ref=ref, responsibility="delivery", _connection_context=coordinator)
            self.assertEqual(scope["data"]["nodes"][0]["state"], "ready")
            delivered = open_assignment(task_ref=ref, nodes=["inspect-0"], profile_name="general",
                model="gpt-5.6-luna", reasoning_effort="high", _connection_context=coordinator)
            self.assertIn("native_dispatch", delivered)

    def test_adaptive_plan_review_requires_every_material_plan_class(self):
        # Risk is explicit evidence, never inferred from language-specific
        # keywords or a removed self-attested nonmateriality structure.
        cases = [
            ("recorded_risk", ["Production impact requires review."], [], False),
            ("plan_risk", [], ["Rollback behavior is uncertain."], False),
            ("russian_authority", [], ["Нужно разрешение на изменение внешней системы."], False),
            ("spanish_risk", [], ["Riesgo operativo desconocido."], False),
            ("japanese_branch", [], ["実装分岐の承認が必要です。"], False),
            ("explicit_request", [], [], True),
        ]
        for label, factors, risks, requested in cases:
            with self.subTest(label=label), tempfile.TemporaryDirectory() as root, patch(
                "cortex_runtime.domain_api._worker_capability_provenance", return_value=PROVENANCE,
            ):
                task, outcomes = self._task(root)
                ref = task["task_ref"]
                assess_governance(task_ref=ref, mode="light" if requested else "full", rationale="Explicit material-risk classification; language does not determine policy.",
                    risk_factors=factors, user_review_requested=requested)
                self._prepared_plan(ref, outcomes, risks=risks)
                evidence = read_evidence(task_ref=ref, report_policy="active_plan")
                self.assertEqual(evidence["data"]["reports"][0]["review_policy"], "required")
                with self.assertRaises(V12ServiceError):
                    open_plan_review(task_ref=ref, prompt="Review before validation?", prompt_language="en")
                validator, context = self._consume_dispatch(self._assignment(ref, outcomes[0], "validator"))
                self._publish_result(validator, outcomes[0], context)
                review = open_plan_review(task_ref=ref, prompt="Review the validated risk and plan?", prompt_language="en")
                self.assertEqual(review["data"]["human_view"]["kind"], "plan")

    def test_material_steering_invalidates_old_plan_and_approval(self):
        with tempfile.TemporaryDirectory() as root, patch("cortex_runtime.domain_api._worker_capability_provenance", return_value=PROVENANCE):
            task, outcomes = self._task(root)
            ref = task["task_ref"]
            assess_governance(task_ref=ref, mode="full", rationale="Material-risk fixture.")
            self._prepared_plan(ref, outcomes)
            worker, context = self._consume_dispatch(self._assignment(ref, outcomes[0], "validation"))
            self._publish_result(worker, outcomes[0], context)
            open_plan_review(task_ref=ref, prompt="Approve current plan?", prompt_language="en")
            record_plan_review(task_ref=ref, outcome="approve", response_original="Approve it.", user_language="en")
            coordinator = {}
            read_scope(task_ref=ref, responsibility="delivery", _connection_context=coordinator)
            replacement = {"outcome": "Revised Product", "acceptance": ["Use the revised behavior."], "constraints": [], "verification": []}
            record_steering(task_ref=ref, response_original="Use Revised Product instead.", user_language="en",
                add=[replacement], retire=[outcomes[0]["outcome"]], _connection_context=coordinator)
            with self.assertRaises(V12ServiceError):
                open_plan_review(task_ref=ref, prompt="Reuse old plan?", prompt_language="en")
            with self.assertRaises(V12ServiceError) as stale:
                open_assignment(task_ref=ref, nodes=["inspect-0"], profile_name="general", model="gpt-5.6-luna",
                    reasoning_effort="high", _connection_context=coordinator)
            self.assertEqual(stale.exception.code, "assignment_stale")
            historical = read_evidence(task_ref=ref, report_policy="all_finalized", _connection_context={})
            self.assertEqual(sum(report["report_type"] == "plan" for report in historical["data"]["reports"]), 1)
            self.assertEqual(read_state(task_ref=ref)["data"]["coverage_status_counts"], {"unverified": 1})

    def test_current_approved_multi_outcome_plan_admits_fullstack_delivery(self):
        outcomes = [{"outcome": name, "acceptance": [], "constraints": [], "verification": []} for name in ("API", "Tests", "README")]
        with tempfile.TemporaryDirectory() as root, patch("cortex_runtime.domain_api._worker_capability_provenance", return_value=PROVENANCE):
            task, _ = self._task(root, outcomes)
            ref = task["task_ref"]
            assess_governance(task_ref=ref, mode="light", user_review_requested=True)
            self._prepared_plan(ref, outcomes)
            worker, context = self._consume_dispatch(self._assignment(ref, outcomes[0], "validation"))
            self._publish_result(worker, outcomes[0], context)
            open_plan_review(task_ref=ref, prompt="Review all three outcomes.", prompt_language="en")
            record_plan_review(task_ref=ref, outcome="approve", response_original="Approve the three-outcome plan.", user_language="en")
            coordinator = {}
            scope = read_scope(task_ref=ref, responsibility="delivery", _connection_context=coordinator)
            keys = [item["node"] for item in scope["data"]["nodes"] if item["state"] == "ready"]
            self.assertEqual(len(keys), 3)
            dispatched = open_assignment(task_ref=ref, nodes=keys, profile_name="fullstack_dev",
                model="gpt-5.6-terra", reasoning_effort="high", _connection_context=coordinator)
            self.assertFalse(dispatched["replayed"])
            worker, context = self._consume_dispatch(dispatched)
            self._publish_result(worker, outcomes[0], context)
            self.assertEqual(read_state(task_ref=ref)["data"]["coverage_status_counts"], {"complete": 3})

    def test_approved_plan_allows_bounded_rework_without_repeated_steering(self):
        self._approved_repair_flow(audit=False)

    def test_approved_plan_reworks_when_independent_verification_finds_a_defect(self):
        self._approved_repair_flow(audit=True)

    def test_open_assignment_returns_only_native_dispatch_and_replay_state(self) -> None:
        with tempfile.TemporaryDirectory() as root, patch("cortex_runtime.domain_api._worker_capability_provenance", return_value=PROVENANCE):
            task, outcomes = self._task(root)
            result = self._assignment(task["task_ref"], outcomes[0], "audit")
            self.assertEqual(set(result), {"native_dispatch", "replayed"})
            self.assertNotIn("model", result["native_dispatch"])
            self.assertEqual(result["native_dispatch"]["reasoning_effort"], "high")
            self.assertEqual(
                list(result["native_dispatch"]),
                ["fork_turns", "task_name", "reasoning_effort", "message"],
            )
            self.assertNotIn("assignment_ref", repr(result))
            self.assertNotIn("continuation_ref", repr(result))
            self.assertRegex(result["native_dispatch"]["message"], r'"task_ref":"t_[0-9a-f]{12}_[0-9a-f]{32}"')
            message = result["native_dispatch"]["message"]
            self.assertNotIn("Build the artifact.", message)
            self.assertNotIn("Verify audit.", message)
            self.assertNotIn("Read-only bounded scope.", message)
            self.assertNotIn("Codebase Memory as the mandatory first evidence route", message)
            self.assertLess(len(message.encode("utf-8")), 1_024)
            worker_ref = re.search(r'"task_ref":"(t_[0-9a-f]{12}_[0-9a-f]{32})"', message).group(1)
            assignment = read_task(task_ref=worker_ref, _connection_context={})
            context = assignment["data"]["assignment_context"]
            self.assertIn("Codebase Memory as the preferred first evidence route when it is available", context["common_policy"])
            self.assertIn("Its absence alone\n  is not a blocked publication cause", context["common_policy"])
            self.assertEqual(context["profile_name"], "explorer")
            self.assertTrue(context["profile_instructions"])

    def test_parallel_workers_bind_distinct_assignments_even_when_read_in_reverse_order(self) -> None:
        outcomes = [
            {"outcome": "Audit A.", "acceptance": ["A verified."], "constraints": [], "verification": []},
            {"outcome": "Audit B.", "acceptance": ["B verified."], "constraints": [], "verification": []},
        ]
        with tempfile.TemporaryDirectory() as root, patch("cortex_runtime.domain_api._worker_capability_provenance", return_value=PROVENANCE):
            task, _ = self._task(root, outcomes)
            assignments = self._parallel_assignments(task["task_ref"], outcomes)
            worker_refs = [re.search(r'"task_ref":"(t_[0-9a-f]{12}_[0-9a-f]{32})"', item["native_dispatch"]["message"]).group(1) for item in assignments]
            self.assertEqual(len(set(worker_refs)), 2)
            contexts = [{}, {}]
            second = read_task(task_ref=worker_refs[1], _connection_context=contexts[1])
            first = read_task(task_ref=worker_refs[0], _connection_context=contexts[0])
            self.assertNotEqual(contexts[0]["assignment_id"], contexts[1]["assignment_id"])
            self.assertIn("Audit A.", repr(first))
            self.assertNotIn("Audit B.", repr(first["data"]["contract_context"]))
            self.assertIn("Audit B.", repr(second))
            self.assertNotIn("Audit A.", repr(second["data"]["contract_context"]))

    def test_terminal_assignment_reconciles_only_on_its_bound_connection(self) -> None:
        with tempfile.TemporaryDirectory() as root, patch("cortex_runtime.domain_api._worker_capability_provenance", return_value=PROVENANCE):
            task, outcomes = self._task(root)
            assignment = self._assignment(task["task_ref"], outcomes[0], "restart")
            worker_ref = re.search(r'"task_ref":"(t_[0-9a-f]{12}_[0-9a-f]{32})"', assignment["native_dispatch"]["message"]).group(1)
            context: dict = {}
            first = read_task(task_ref=worker_ref, _connection_context=context)
            self.assertIn("contract_context", first["data"])
            with self.assertRaises(V12ServiceError) as fresh:
                read_task(task_ref=worker_ref, _connection_context={})
            self.assertEqual(fresh.exception.code, "connection_lost")
            before = read_state(task_ref=task["task_ref"], )
            reconciled = read_task(
                task_ref=worker_ref,
                _connection_context=context,
            )
            after = read_state(task_ref=task["task_ref"], )
            self.assertEqual(reconciled["data"], first["data"])
            self.assertFalse(reconciled["has_more"])
            self.assertTrue(context["assignment_complete"])
            self.assertEqual(context["assignment_id"], context["bootstrap_assignment_id"])
            self.assertEqual(after["data"], before["data"])

    def test_fresh_connection_cannot_recover_consumed_worker_publication(self) -> None:
        with tempfile.TemporaryDirectory() as root, patch(
            "cortex_runtime.domain_api._worker_capability_provenance",
            return_value=PROVENANCE,
        ):
            task, outcomes = self._task(root)
            assignment = self._assignment(task["task_ref"], outcomes[0], "reconnect")
            worker_ref = re.search(
                r'"task_ref":"(t_[0-9a-f]{12}_[0-9a-f]{32})"',
                assignment["native_dispatch"]["message"],
            ).group(1)

            with self.assertRaises(V12ServiceError) as unconsumed:
                self._publish_result(worker_ref, outcomes[0], {})
            self.assertEqual(unconsumed.exception.code, "assignment_not_consumed")

            original_context: dict = {}
            read_task(
                task_ref=worker_ref,
                _connection_context=original_context,
            )
            fresh_context: dict = {}
            with self.assertRaises(V12ServiceError) as copied_publication:
                self._publish_result(worker_ref, outcomes[0], fresh_context)
            self.assertEqual(copied_publication.exception.code, "assignment_not_consumed")
            with self.assertRaises(V12ServiceError) as copied_read:
                read_task(
                    task_ref=worker_ref,
                    _connection_context=fresh_context,
                )
            self.assertEqual(copied_read.exception.code, "connection_lost")

            store = V12Store(Path(root))
            with store._connection() as connection:
                self.assertEqual(
                    connection.execute(
                        "SELECT COUNT(*) FROM report_operations"
                    ).fetchone()[0],
                    0,
                )
            published = self._publish_result(
                worker_ref, outcomes[0], original_context,
            )
            self.assertEqual(published["state"], "published")
            self.assertFalse(published["replayed"])

            evidence = read_evidence(
                task_ref=task["task_ref"],
                report_policy="all_finalized", _connection_context={},
            )
            self.assertEqual(len(evidence["data"]["reports"]), 1)
            self.assertIn("Recovered worker result.", repr(evidence["data"]))
            with store._connection() as connection:
                capability = connection.execute(
                    "SELECT state,continuation_ref FROM worker_capabilities",
                ).fetchall()
                operations = connection.execute(
                    "SELECT COUNT(*) FROM report_operations",
                ).fetchone()[0]
            self.assertEqual(len(capability), 1)
            self.assertEqual(capability[0]["state"], "consumed")
            self.assertEqual(capability[0]["continuation_ref"], original_context["continuation_ref"])
            self.assertEqual(operations, 1)

    def test_reconnect_rejects_partial_different_foreign_and_malformed_bindings(self) -> None:
        outcomes = [
            {"outcome": "Recover A.", "acceptance": ["A is isolated."], "constraints": [], "verification": []},
            {"outcome": "Recover B.", "acceptance": ["B is isolated."], "constraints": [], "verification": []},
        ]
        with tempfile.TemporaryDirectory() as root, patch(
            "cortex_runtime.domain_api._worker_capability_provenance",
            return_value=PROVENANCE,
        ):
            task, _ = self._task(root, outcomes)
            assignments = self._parallel_assignments(task["task_ref"], outcomes)
            worker_refs = [
                re.search(
                    r'"task_ref":"(t_[0-9a-f]{12}_[0-9a-f]{32})"',
                    assignment["native_dispatch"]["message"],
                ).group(1)
                for assignment in assignments
            ]
            bound_contexts = [{}, {}]
            for worker_ref, context in zip(worker_refs, bound_contexts):
                read_task(
                    task_ref=worker_ref,
                    _connection_context=context,
                )

            partial = {"actor": "worker"}
            store = V12Store(Path(root))
            before_operations = store._read(lambda c: c.execute("SELECT COUNT(*) FROM report_operations").fetchone()[0])
            with self.assertRaises(V12ServiceError) as partial_error:
                self._publish_result(worker_refs[0], outcomes[0], partial)
            self.assertEqual(partial_error.exception.code, "assignment_not_consumed")
            self.assertEqual(partial, {"actor": "worker"})

            different = dict(bound_contexts[1])
            with self.assertRaises(V12ServiceError) as different_error:
                self._publish_result(worker_refs[0], outcomes[0], different)
            self.assertEqual(different_error.exception.code, "wrong_connection")
            self.assertEqual(different, bound_contexts[1])

            foreign_task, _ = self._task(tempfile.mkdtemp(dir=root), [{
                "outcome": "Foreign task.", "acceptance": ["Remain isolated."],
                "constraints": [], "verification": [],
            }])
            foreign_ref = foreign_task["task_ref"] + "_" + worker_refs[0].rsplit("_", 1)[1]
            for rejected_ref in (foreign_ref, worker_refs[0][:-1] + "z"):
                with self.subTest(task_ref=rejected_ref), self.assertRaises(V12ServiceError):
                    self._publish_result(rejected_ref, outcomes[0], {})

            store = V12Store(Path(root))
            with store._connection() as connection:
                self.assertEqual(
                    connection.execute("SELECT COUNT(*) FROM report_operations").fetchone()[0],
                    before_operations,
                )

    def test_reconnect_rejects_provenance_dispatch_and_durable_state_drift(self) -> None:
        def fixture(root: str, label: str) -> tuple[dict, dict, str]:
            task, outcomes = self._task(root)
            assignment = self._assignment(task["task_ref"], outcomes[0], label)
            worker_ref = re.search(
                r'"task_ref":"(t_[0-9a-f]{12}_[0-9a-f]{32})"',
                assignment["native_dispatch"]["message"],
            ).group(1)
            read_task(task_ref=worker_ref, _connection_context={})
            return task, outcomes[0], worker_ref

        with patch(
            "cortex_runtime.domain_api._worker_capability_provenance",
            return_value=PROVENANCE,
        ):
            with tempfile.TemporaryDirectory() as root:
                _task, outcome, worker_ref = fixture(root, "provenance drift")
                changed = dict(PROVENANCE, source_digest="sha256:" + "b" * 64)
                with patch(
                    "cortex_runtime.domain_api._worker_capability_provenance",
                    return_value=changed,
                ), self.assertRaises(V12ServiceError) as rejected:
                    self._publish_result(worker_ref, outcome, {})
                self.assertEqual(rejected.exception.code, "assignment_not_consumed")

            with tempfile.TemporaryDirectory() as root:
                _task, outcome, worker_ref = fixture(root, "dispatch drift")
                store = V12Store(Path(root))
                with store._connection() as connection:
                    connection.execute(
                        "UPDATE worker_capabilities SET dispatch_digest=?",
                        ("sha256:" + "c" * 64,),
                    )
                with self.assertRaises(V12ServiceError) as rejected:
                    self._publish_result(worker_ref, outcome, {})
                self.assertEqual(rejected.exception.code, "assignment_not_consumed")

            with tempfile.TemporaryDirectory() as root:
                _task, outcome, worker_ref = fixture(root, "durable stale")
                store = V12Store(Path(root))
                with store._connection() as connection:
                    connection.execute("UPDATE worker_capabilities SET state='stale'")
                with self.assertRaises(V12ServiceError) as rejected:
                    self._publish_result(worker_ref, outcome, {})
                self.assertEqual(rejected.exception.code, "assignment_not_consumed")

    def test_steering_revokes_consumed_assignment_and_requires_fresh_current_assignment(self) -> None:
        with tempfile.TemporaryDirectory() as root, patch(
            "cortex_runtime.domain_api._worker_capability_provenance",
            return_value=PROVENANCE,
        ):
            task, outcomes = self._task(root)
            assignment = self._assignment(task["task_ref"], outcomes[0], "steered reconnect")
            worker_ref = re.search(
                r'"task_ref":"(t_[0-9a-f]{12}_[0-9a-f]{32})"',
                assignment["native_dispatch"]["message"],
            ).group(1)
            worker_context: dict = {}
            original = read_task(
                task_ref=worker_ref,
                _connection_context=worker_context,
            )
            self.assertEqual(original["data"]["contract_context"]["revision"], 1)

            refined = dict(outcomes[0]) | {"acceptance": ["A newer task revision works."]}
            open_steering(
                task_ref=task["task_ref"], prompt="Apply the newer task revision?",
                prompt_language="en",
            )
            record_steering(
                task_ref=task["task_ref"], response_original="Apply it.",
                user_language="en", add=[refined], retire=[outcomes[0]["outcome"]],
            )
            current = read_state(task_ref=task["task_ref"])
            self.assertEqual(current["data"]["effective_revision"], 2)

            stale_publication = self._publish_result(worker_ref, outcomes[0], worker_context)
            self.assertEqual(stale_publication, {
                "task_ref": worker_ref, "state": "superseded", "published": False, "replayed": False,
            })
            evidence = read_evidence(
                task_ref=task["task_ref"],
                report_policy="all_finalized", _connection_context={},
            )
            self.assertEqual(evidence["data"]["reports"], [])

            scope = read_scope(task_ref=task["task_ref"], responsibility="evidence")
            self.assertTrue(all(item["state"] != "ready" for item in scope["data"]["nodes"]))
            self.assertEqual(read_outcome(task_ref=task["task_ref"], outcome=refined["outcome"])["data"]["outcome"], refined)
            self.assertTrue(current["data"]["reconciliation_required"])

    def test_stale_publication_checks_lifecycle_before_current_coverage_names(self) -> None:
        original = {
            "outcome": "Implement the original contract.",
            "acceptance": ["The original contract works."],
            "constraints": [], "verification": ["Run the original check."],
        }
        replacement = {
            "outcome": "Implement the revised contract.",
            "acceptance": ["The revised contract works."],
            "constraints": [], "verification": ["Run the revised check."],
        }
        with tempfile.TemporaryDirectory() as root, patch(
            "cortex_runtime.domain_api._worker_capability_provenance",
            return_value=PROVENANCE,
        ):
            task, _ = self._task(root, [original])
            assignment = self._assignment(
                task["task_ref"], original, "stale coverage worker",
            )
            worker_ref = re.search(
                r'"task_ref":"(t_[0-9a-f]{12}_[0-9a-f]{32})"',
                assignment["native_dispatch"]["message"],
            ).group(1)
            worker_context: dict = {}
            read_task(task_ref=worker_ref, _connection_context=worker_context)

            open_steering(
                task_ref=task["task_ref"],
                prompt="Apply the revised contract?",
                prompt_language="en",
            )
            record_steering(
                task_ref=task["task_ref"],
                response_original="Apply the revised contract.",
                user_language="en", add=[replacement],
                retire=[original["outcome"]],
            )

            # The worker submits a name from the newer contract, which is not
            # present in its immutable revision-one assignment. Lifecycle
            # freshness must win over coverage-name matching.
            stale = self._publish_result(worker_ref, replacement, worker_context)
            self.assertEqual(stale, {
                "task_ref": worker_ref, "state": "superseded", "published": False, "replayed": False,
            })
            self.assertEqual(
                read_evidence(
                    task_ref=task["task_ref"], report_policy="all_finalized",
                    _connection_context={},
                )["data"]["reports"],
                [],
            )

    def test_recovery_state_binds_continuations_but_ordinary_state_read_does_not(self) -> None:
        with tempfile.TemporaryDirectory() as root, patch(
            "cortex_runtime.domain_api._worker_capability_provenance", return_value=PROVENANCE,
        ):
            task, outcomes = self._task(root)
            self._assignment(task["task_ref"], outcomes[0], "recovery projection")
            ordinary = {"_role": "coordinator"}
            state = read_state(task_ref=task["task_ref"], _connection_context=ordinary)
            self.assertGreater(state["data"]["unfinished_assignment_count"], 0)
            self.assertFalse(state["data"]["recovery_required"])
            self.assertNotIn("_required_next_operation", ordinary)
            recovered = {"_role": "unknown"}
            state = read_state(task_ref=task["task_ref"], _connection_context=recovered)
            self.assertEqual(state["data"]["admissible_operations"], ["read_continuations"])
            self.assertEqual(recovered["_required_next_operation"], ("read_continuations", task["task_ref"]))
            result = read_continuations(task_ref=task["task_ref"], _connection_context=recovered)
            self.assertFalse(result["has_more"])
            self.assertEqual(len(result["data"]["continuations"]), 1)
            self.assertNotIn("_required_next_operation", recovered)

    def test_add_only_steering_preserves_source_and_requires_native_reconciliation(self):
        with tempfile.TemporaryDirectory() as root, patch("cortex_runtime.domain_api._worker_capability_provenance", return_value=PROVENANCE):
            task, outcomes = self._task(root)
            ref = task["task_ref"]
            worker, context = self._consume_dispatch(self._assignment(ref, outcomes[0], "baseline"))
            added = {"outcome": "Added behavior", "acceptance": ["The addition works."], "constraints": [], "verification": []}
            effect = record_steering(task_ref=ref, response_original="Also implement Added behavior.", user_language="en", add=[added], retire=[])
            self.assertTrue(effect["effect"]["reconciliation_required"])
            result = self._publish_result(worker, outcomes[0], context)
            self.assertEqual(result["state"], "superseded")
            scope = read_scope(task_ref=ref, responsibility="evidence")
            self.assertTrue(all(node["state"] != "ready" for node in scope["data"]["nodes"]))
            for expected in [*outcomes, added]:
                self.assertEqual(read_outcome(task_ref=ref, outcome=expected["outcome"])["data"]["outcome"], expected)
            self.assertEqual(read_state(task_ref=ref)["data"]["finalized_report_count"], 0)

    def test_add_retire_and_replace_steering_each_revoke_pre_revision_publication(self) -> None:
        for mode in ("add", "retire", "replace"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as root, patch(
                "cortex_runtime.domain_api._worker_capability_provenance",
                return_value=PROVENANCE,
            ):
                original = {
                    "outcome": f"Implement the {mode} baseline.",
                    "acceptance": ["The baseline works."],
                    "constraints": [], "verification": ["Run the baseline check."],
                }
                remaining = {"outcome": "Retain the independent product requirement.",
                             "acceptance": [], "constraints": [], "verification": []}
                task, _ = self._task(root, [original, remaining] if mode == "retire" else [original])
                assignment = self._assignment(task["task_ref"], original, f"{mode} worker")
                worker_ref = re.search(
                    r'"task_ref":"(t_[0-9a-f]{12}_[0-9a-f]{32})"',
                    assignment["native_dispatch"]["message"],
                ).group(1)
                worker_context: dict = {}
                read_task(task_ref=worker_ref, _connection_context=worker_context)

                if mode == "add":
                    additions = [{
                        "outcome": "Implement the added requirement.",
                        "acceptance": ["The addition works."],
                        "constraints": [], "verification": ["Run the addition check."],
                    }]
                    retired = []
                elif mode == "retire":
                    additions = []
                    retired = [original["outcome"]]
                else:
                    additions = [dict(original) | {
                        "acceptance": ["The replacement requirement works."],
                    }]
                    retired = [original["outcome"]]
                open_steering(
                    task_ref=task["task_ref"], prompt=f"Apply {mode} steering?",
                    prompt_language="en",
                )
                record_steering(
                    task_ref=task["task_ref"], response_original=f"Apply {mode}.",
                    user_language="en", add=additions, retire=retired,
                )
                stale = self._publish_result(worker_ref, original, worker_context)
                self.assertEqual(stale, {"task_ref": worker_ref, "state": "superseded",
                                        "published": False, "replayed": False})
                self.assertEqual(
                    read_evidence(
                        task_ref=task["task_ref"], report_policy="all_finalized",
                        _connection_context={},
                    )["data"]["reports"],
                    [],
                )

    def test_assignment_dispatch_fails_if_steering_races_scope_selection(self) -> None:
        original = {
            "outcome": "Implement revision one.", "acceptance": ["Revision one works."],
            "constraints": [], "verification": [],
        }
        added = {
            "outcome": "Implement revision two.", "acceptance": ["Revision two works."],
            "constraints": [], "verification": [],
        }
        with tempfile.TemporaryDirectory() as root, patch(
            "cortex_runtime.domain_api._worker_capability_provenance", return_value=PROVENANCE,
        ):
            task, _ = self._task(root, [original])

            context = {}
            read_scope(task_ref=task["task_ref"], responsibility="evidence", _connection_context=context)
            record_steering(task_ref=task["task_ref"], response_original="Add revision two.",
                user_language="en", add=[added], retire=[], _connection_context={})
            with self.assertRaises(V12ServiceError) as stale:
                open_assignment(
                    task_ref=task["task_ref"], profile_name="general", nodes=["baseline"],
                    model="gpt-5.6-luna", reasoning_effort="high", _connection_context=context,
                )
            self.assertEqual(stale.exception.code, "assignment_stale")
            self.assertEqual(read_continuations(task_ref=task["task_ref"])["data"]["continuations"], [])

    def test_assignment_read_preserves_exact_source_limits_and_negative_requirements(self) -> None:
        """A worker must receive the normalized contract, not an attachment shorthand."""
        outcome = {
            "outcome": "Implement OTP verification for handler VerifyCode.",
            "acceptance": [
                "OTP is exactly 6 digits and expires after 10 minutes.",
                "Reject the request after 5 incorrect attempts.",
            ],
            "constraints": [
                "Resend cooldown is exactly 60 seconds.",
                "Never reveal whether an email address is registered.",
            ],
            "verification": [
                "Test expiry at 10:00 and rejection on attempt 6.",
                "Verify the unregistered-email response is indistinguishable.",
            ],
        }
        with tempfile.TemporaryDirectory() as root, patch("cortex_runtime.domain_api._worker_capability_provenance", return_value=PROVENANCE):
            task, _ = self._task(root, [outcome])
            assignment = self._assignment(task["task_ref"], outcome, "audit")
            worker_ref = re.search(r'"task_ref":"(t_[0-9a-f]{12}_[0-9a-f]{32})"', assignment["native_dispatch"]["message"]).group(1)
            read = read_task(task_ref=worker_ref, _connection_context={})
            item = read["data"]["contract_context"]["outcomes"][0]
            self.assertEqual(item["outcome"], outcome["outcome"])
            self.assertEqual(item["acceptance"], outcome["acceptance"])
            self.assertEqual(item["constraints"], outcome["constraints"])
            self.assertEqual(item["verification"], outcome["verification"])

    def test_parallel_workers_publish_to_their_exact_assignments_in_reverse_order(self) -> None:
        outcomes = [
            {"outcome": "Implement A.", "acceptance": ["A works."], "constraints": [], "verification": []},
            {"outcome": "Implement B.", "acceptance": ["B works."], "constraints": [], "verification": []},
        ]
        with tempfile.TemporaryDirectory() as root, patch("cortex_runtime.domain_api._worker_capability_provenance", return_value=PROVENANCE):
            task, _ = self._task(root, outcomes)
            assignments = self._parallel_assignments(task["task_ref"], outcomes)
            worker_refs = [re.search(r'"task_ref":"(t_[0-9a-f]{12}_[0-9a-f]{32})"', item["native_dispatch"]["message"]).group(1) for item in assignments]
            contexts = [{}, {}]
            read_task(task_ref=worker_refs[0], _connection_context=contexts[0])
            read_task(task_ref=worker_refs[1], _connection_context=contexts[1])

            def publish(index: int) -> dict:
                return self._publish_result(worker_refs[index], outcomes[index], contexts[index])

            second, first = publish(1), publish(0)
            self.assertEqual(second["task_ref"], worker_refs[1])
            self.assertEqual(first["task_ref"], worker_refs[0])
            self.assertNotEqual(contexts[0]["assignment_id"], contexts[1]["assignment_id"])

    def test_all_finalized_evidence_from_multiple_authors_keeps_assignment_public(self):
        from test_execution_graph_integrity import node
        from copy import deepcopy
        with tempfile.TemporaryDirectory() as root, patch("cortex_runtime.domain_api._worker_capability_provenance", return_value=PROVENANCE):
            outcomes = [{"outcome": name, "acceptance": [], "constraints": [], "verification": []} for name in ("Surface A", "Surface B")]
            task, _ = self._task(root, outcomes)
            original = publish_plan
            def include_consumer(**kwargs):
                value = deepcopy(kwargs["candidates"][0]["graph"])
                consumer = node("aggregate", audit=True, requires=["inspected-0", "inspected-1"], provides=["aggregate-checked"],
                    dependencies=[("inspect-0", ["inspected-0"]), ("inspect-1", ["inspected-1"])])
                consumer["kind"] = "discovery"
                consumer["verifies"] = [{"kind": "outcome", "name": item["outcome"]} for item in outcomes]
                value["nodes"].append(consumer)
                return original(**{**kwargs, "candidates": ordinary_candidates(value)})
            with patch(__name__ + ".publish_plan", side_effect=include_consumer):
                assignments = self._parallel_assignments(task["task_ref"], outcomes)
            for assignment, outcome in zip(assignments, outcomes):
                worker, context = self._consume_dispatch(assignment)
                self._publish_result(worker, outcome, context)
            evidence = read_evidence(task_ref=task["task_ref"], report_policy="all_finalized", _connection_context={})
            self.assertFalse(evidence["has_more"])
            views = evidence["data"]["human_views"]
            self.assertEqual(len(views), 5)
            self.assertEqual(sum(view["kind"] == "report" for view in views), 4)
            for view in views:
                self.assertEqual(view["status"], "ready")
                target = Path(view["markdown_link"].split("](", 1)[1][:-1])
                self.assertTrue(target.is_file())
                if view["kind"] == "report":
                    self.assertEqual(target.stem.removeprefix("report-"), hashlib.sha256(target.read_bytes()).hexdigest())
            assignment = self._assignment(task["task_ref"], outcomes[0], "aggregate")
            ref = re.search(r'"task_ref":"(t_[0-9a-f]{12}_[0-9a-f]{32})"', assignment["native_dispatch"]["message"]).group(1)
            context = {}
            page = read_task(task_ref=ref, _connection_context=context)
            self.assertFalse(page["has_more"])
            self.assertEqual(len(page["data"]["evidence"]["reports"]), 2)

    def test_open_assignment_does_not_accept_caller_authored_private_lineage(self):
        from cortex_runtime.mcp_api import _validate_schema, _validation_failure
        private_fields = ("input_report_refs", "input_decision_refs", "parent_assignment_ref")
        schema = PUBLIC_TOOLS["open_assignment"]["inputSchema"]
        valid = {"task_ref": "t_0123456789ab", "nodes": ["baseline"], "profile_name": "general",
                 "model": "gpt-5.6-luna", "reasoning_effort": "high"}
        for field in private_fields:
            with self.subTest(field=field):
                supplied = {**valid, field: "private-value-must-not-be-echoed"}
                with self.assertRaises(ValueError) as invalid:
                    _validate_schema(schema, supplied)
                failure = _validation_failure(invalid.exception, tool_name="open_assignment", arguments=supplied, input_schema=schema)
                self.assertNotIn(supplied[field], repr(failure))
                self.assertNotIn(field, schema["properties"])

    def test_partial_plan_for_server_derived_complete_scope_is_immediately_active_evidence(self):
        with tempfile.TemporaryDirectory() as root, patch("cortex_runtime.domain_api._worker_capability_provenance", return_value=PROVENANCE):
            task, outcomes = self._task(root)
            self._prepared_plan(task["task_ref"], outcomes, unresolved=["A required repository fact remains unknown."])
            evidence = read_evidence(task_ref=task["task_ref"], report_policy="active_plan")
            self.assertEqual(len(evidence["data"]["reports"]), 1)
            self.assertEqual(evidence["data"]["reports"][0]["status"], "partial")
            with self.assertRaises(V12ServiceError):
                open_plan_review(task_ref=task["task_ref"], prompt="Approve the unfinished plan?", prompt_language="en")
            scope = read_scope(task_ref=task["task_ref"], responsibility="delivery")
            self.assertTrue(all(item["state"] != "ready" for item in scope["data"]["nodes"]))

    def test_planning_assignment_cannot_publish_supplementary_result_after_plan(self):
        with tempfile.TemporaryDirectory() as root, patch("cortex_runtime.domain_api._worker_capability_provenance", return_value=PROVENANCE):
            task, outcomes = self._task(root)
            ref, context = self._prepared_plan(task["task_ref"], outcomes)
            before = read_state(task_ref=task["task_ref"])["data"]["finalized_report_count"]
            with self.assertRaises(V12ServiceError):
                self._publish_result(ref, outcomes[0], context)
            self.assertEqual(read_state(task_ref=task["task_ref"])["data"]["finalized_report_count"], before)

    def test_planning_publication_rejects_mismatched_graph_outcomes_atomically(self):
        with tempfile.TemporaryDirectory() as root, patch("cortex_runtime.domain_api._worker_capability_provenance", return_value=PROVENANCE):
            task, outcomes = self._task(root)
            original = publish_plan
            def wrong(**kwargs):
                from copy import deepcopy
                kwargs["candidates"][0]["graph"] = deepcopy(kwargs["candidates"][0]["graph"])
                kwargs["candidates"][0]["graph"]["outcomes"][0]["outcome"] = "Invented outcome"
                return original(**kwargs)
            with patch(__name__ + ".publish_plan", side_effect=wrong), self.assertRaises(V12ServiceError):
                self._prepared_plan(task["task_ref"], outcomes)
            store = V12Store(Path(root))
            self.assertEqual(store._read(lambda c: c.execute("SELECT COUNT(*) FROM reports WHERE report_type='plan'").fetchone()[0]), 0)
            self.assertEqual(store._read(lambda c: c.execute("SELECT COUNT(*) FROM execution_graphs WHERE graph_kind='candidate'").fetchone()[0]), 0)

    def test_unique_outcome_name_resolves_current_user_refined_revision(self) -> None:
        with tempfile.TemporaryDirectory() as root, patch("cortex_runtime.domain_api._worker_capability_provenance", return_value=PROVENANCE):
            task, outcomes = self._task(root)
            refined = dict(outcomes[0]) | {
                "acceptance": [
                    *outcomes[0]["acceptance"],
                    "The refined criterion also works.",
                ],
            }
            open_steering(task_ref=task["task_ref"], prompt="Apply the requested refinement?", prompt_language="en")
            record_steering(
                task_ref=task["task_ref"], response_original="Apply it.", user_language="en",
                add=[refined], retire=[outcomes[0]["outcome"]],
            )
            assignment = self._assignment(task["task_ref"], refined, "current baseline")
            worker_ref = re.search(r'"task_ref":"(t_[0-9a-f]{12}_[0-9a-f]{32})"', assignment["native_dispatch"]["message"]).group(1)
            current = read_task(task_ref=worker_ref, _connection_context={})
            self.assertIn("The refined criterion also works.", repr(current))
            self.assertIn("The artifact works.", repr(current["data"]["contract_context"]))

    def test_backend_facade_keeps_reads_and_decisions_neutral(self):
        with tempfile.TemporaryDirectory() as root:
            task, outcomes = self._task(root)
            ref = task["task_ref"]
            store = V12Store(Path(root))
            before = store._read(lambda c: c.execute("SELECT COUNT(*) FROM timeline").fetchone()[0])
            read_state(task_ref=ref)
            read_scope(task_ref=ref, responsibility="evidence")
            read_evidence(task_ref=ref, report_policy="all_finalized")
            read_timeline(task_ref=ref)
            self.assertEqual(store._read(lambda c: c.execute("SELECT COUNT(*) FROM timeline").fetchone()[0]), before)
            self.assertEqual(store._read(lambda c: c.execute("SELECT COUNT(*) FROM delegations").fetchone()[0]), 0)
            open_steering(task_ref=ref, prompt="Which export format must this product support?", prompt_language="en")
            added = {"outcome": "CSV export", "acceptance": ["CSV is supported."], "constraints": [], "verification": []}
            record_steering(task_ref=ref, response_original="Support CSV.", user_language="en", add=[added], retire=[])
            self.assertEqual(read_state(task_ref=ref)["data"]["effective_revision"], 2)
            self.assertEqual(store._read(lambda c: c.execute("SELECT COUNT(*) FROM delegations").fetchone()[0]), 0)

    def test_version_and_catalogue_remain_current(self) -> None:
        self.assertEqual(SERVER_VERSION, "1.15.6")
        self.assertEqual(len(PUBLIC_TOOLS), 20)


if __name__ == "__main__":
    unittest.main()
