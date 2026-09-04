from __future__ import annotations

import json
import re
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "plugins/cortex/scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from cortex import PUBLIC_TOOLS
from cortex_runtime.domain_api import (
    _ASSIGNMENT_STRING_FRAGMENT_BYTES,
    _assignment_fragments,
    _encoded_bytes,
    _resolve_task_context,
    assess_governance,
    open_assignment,
    open_steering,
    open_task,
    publish_result,
    read_evidence,
    read_outcome,
    read_scope,
    read_state,
    read_task,
    record_steering,
)
from cortex_runtime.mcp_api import (
    MAX_PHYSICAL_JSONL_FRAME_BYTES,
    _SchemaError,
    _success_tool_result,
    _tool_error_result,
    _validate_schema,
    _validation_failure,
)
from cortex_runtime.v12_contract import MCP_OPERATION_MAX_BYTES
from cortex_runtime.v12_service import V12ServiceError


PROVENANCE = {
    name: "sha256:" + "a" * 64
    for name in ("build_digest", "candidate_digest", "source_digest", "catalogue_digest")
}
WORKER_REF = re.compile(r'"task_ref":"(t_[0-9a-f]{12}_[0-9a-f]{32})"')


class RuntimeContractRemediationTests(unittest.TestCase):
    def _task(self, root: str, outcomes: list[dict]) -> dict:
        task = open_task(
            project_root=root,
            request_original="Exercise the bounded runtime contract.",
            user_language="en",
            outcomes=outcomes,
            constraints=["Keep the test deterministic."],
        )
        assess_governance(
            task_ref=task["task_ref"], mode="minimal",
            rationale="Focused runtime contract fixture.",
        )
        return task


    @staticmethod
    def _semantic_outcome(name: str) -> dict:
        return {
            "outcome": name,
            "acceptance": [f"{name} is accepted."],
            "constraints": [],
            "verification": [f"Verify {name}"],
        }


    def test_independent_steering_additions_and_atomic_replacement(self) -> None:
        original = self._semantic_outcome("Original outcome.")
        first = self._semantic_outcome("Independent outcome A.")
        second = self._semantic_outcome("Independent outcome B.")
        replacement = self._semantic_outcome("Replacement outcome.")
        with tempfile.TemporaryDirectory() as root, patch(
            "cortex_runtime.domain_api._worker_capability_provenance",
            return_value=PROVENANCE,
        ):
            task = self._task(root, [original])
            open_steering(
                task_ref=task["task_ref"], prompt="Add both outcomes?",
                prompt_language="en",
            )
            record_steering(
                task_ref=task["task_ref"], response_original="Add both.",
                user_language="en", add=[first, second], retire=[],
            )
            state = read_scope(task_ref=task["task_ref"], responsibility="evidence")
            names = [item["outcome"] for item in state["data"]["outcomes"]]
            self.assertEqual(names, [original["outcome"], first["outcome"], second["outcome"]])

            open_steering(
                task_ref=task["task_ref"], prompt="Replace the original?",
                prompt_language="en",
            )
            record_steering(
                task_ref=task["task_ref"], response_original="Replace it.",
                user_language="en", add=[replacement], retire=[original["outcome"]],
            )
            state = read_scope(task_ref=task["task_ref"], responsibility="evidence")
            names = [item["outcome"] for item in state["data"]["outcomes"]]
            self.assertEqual(names, [replacement["outcome"], first["outcome"], second["outcome"]])
            replaced = read_outcome(task_ref=task["task_ref"], outcome=replacement["outcome"])["data"]["outcome"]
            self.assertEqual(replaced["acceptance"], replacement["acceptance"])
            self.assertEqual(replaced["constraints"], replacement["constraints"])
            self.assertEqual(replaced["verification"], replacement["verification"])
            self.assertNotIn(original["acceptance"][0], replaced["acceptance"])
            with self.assertRaises(V12ServiceError) as stale:
                read_outcome(task_ref=task["task_ref"], outcome=original["outcome"])
            self.assertEqual(stale.exception.code, "outcome_item_not_found")
            context = {}
            scope = read_scope(task_ref=task["task_ref"], responsibility="evidence", _connection_context=context)
            ready = [node for node in scope["data"]["nodes"] if node["state"] == "ready"]
            self.assertEqual(len(ready), 1)
            opened = open_assignment(task_ref=task["task_ref"], nodes=[ready[0]["node"]],
                profile_name="explorer", model="gpt-5.6-luna", reasoning_effort="high", _connection_context=context)
            self.assertFalse(opened["replayed"])

    def test_same_name_steering_replacement_does_not_merge_retired_contract(self) -> None:
        original = {
            "outcome": "Public helper.",
            "acceptance": ["reset removes state."],
            "constraints": ["reset is thread-safe."],
            "verification": ["reset tests pass."],
        }
        replacement = {
            "outcome": "Public helper.",
            "acceptance": ["contains observes state."],
            "constraints": ["contains is read-only."],
            "verification": ["contains tests pass."],
        }
        with tempfile.TemporaryDirectory() as root:
            task = self._task(root, [original])
            open_steering(
                task_ref=task["task_ref"], prompt="Replace reset with contains?",
                prompt_language="en",
            )
            record_steering(
                task_ref=task["task_ref"], response_original="Replace it.",
                user_language="en", add=[replacement], retire=[original["outcome"]],
            )
            state = read_scope(task_ref=task["task_ref"], responsibility="evidence")
            current = [read_outcome(task_ref=task["task_ref"], outcome=item["outcome"])["data"]["outcome"] for item in state["data"]["outcomes"]]
            self.assertEqual(len(current), 1)
            self.assertEqual(current[0]["outcome"], replacement["outcome"])
            self.assertEqual(current[0]["acceptance"], replacement["acceptance"])
            self.assertEqual(current[0]["constraints"], replacement["constraints"])
            self.assertEqual(current[0]["verification"], replacement["verification"])

    def test_exact_record_steering_replays_after_contract_revision(self) -> None:
        """Compaction may repeat a successful decision after its binding was consumed."""
        original = self._semantic_outcome("Original outcome.")
        added = self._semantic_outcome("Outcome added before compaction.")
        with tempfile.TemporaryDirectory() as root:
            task = self._task(root, [original])
            open_steering(
                task_ref=task["task_ref"], prompt="Add the outcome?",
                prompt_language="en",
            )
            arguments = {
                "task_ref": task["task_ref"],
                "response_original": "Add it.", "user_language": "en",
                "add": [added], "retire": [],
            }
            first = record_steering(**arguments)
            replay = record_steering(**arguments)
            self.assertFalse(first["replayed"])
            self.assertTrue(replay["replayed"])
            state = read_scope(task_ref=task["task_ref"], responsibility="evidence")
            names = [item["outcome"] for item in state["data"]["outcomes"]]
            self.assertEqual(names.count(added["outcome"]), 1)

            changed = dict(arguments)
            changed["response_original"] = "A different response."
            with self.assertRaises(V12ServiceError) as stale:
                record_steering(**changed)
            self.assertEqual(stale.exception.code, "clarification_binding_stale")

    def test_invalid_public_steering_outcome_reports_exact_path(self) -> None:
        original = self._semantic_outcome("Original outcome.")
        with tempfile.TemporaryDirectory() as root:
            task = self._task(root, [original])
            open_steering(
                task_ref=task["task_ref"], prompt="Add a malformed outcome?",
                prompt_language="en",
            )
            malformed = {
                "outcome": "Incomplete.", "acceptance": [], "constraints": [],
            }
            with self.assertRaises(V12ServiceError) as rejected:
                record_steering(
                    task_ref=task["task_ref"], response_original="Add it.",
                    user_language="en", add=[malformed], retire=[],
                )
            self.assertEqual(rejected.exception.details.get("path"), "$.add[0]")
            self.assertEqual(
                rejected.exception.details.get("expected"),
                "complete_outcome_object",
            )

    def test_post_compaction_steering_uses_fresh_exact_current_outcome(self) -> None:
        current = self._semantic_outcome(
            "Build, install on the connected USB-debug phone, and verify it."
        )
        remembered = self._semantic_outcome(
            "Build, install it on the USB-debug phone, and verify it."
        )
        replacement = self._semantic_outcome(
            "Build, install on the connected USB-debug phone, and verify the replacement."
        )
        with tempfile.TemporaryDirectory() as root:
            task = self._task(root, [current])
            open_steering(
                task_ref=task["task_ref"], prompt="Replace the outcome?",
                prompt_language="en",
            )
            with self.assertRaises(V12ServiceError) as stale:
                record_steering(
                    task_ref=task["task_ref"], response_original="Replace it.",
                    user_language="en", add=[replacement], retire=[remembered["outcome"]],
                )
            self.assertEqual(stale.exception.code, "outcome_item_not_found")
            self.assertEqual(stale.exception.details.get("path"), "$.retire[0]")

            fresh = read_state(task_ref=task["task_ref"])
            self.assertEqual(fresh["data"]["effective_revision"], 1)
            exact = read_outcome(task_ref=task["task_ref"], outcome=current["outcome"])["data"]["outcome"]
            recorded = record_steering(
                task_ref=task["task_ref"], response_original="Replace it.",
                user_language="en", add=[replacement], retire=[exact["outcome"]],
            )
            self.assertFalse(recorded["replayed"])
            after = read_state(task_ref=task["task_ref"])
            self.assertEqual(after["data"]["effective_revision"], 2)
            self.assertEqual(
                read_outcome(task_ref=task["task_ref"], outcome=replacement["outcome"])["data"]["outcome"], replacement,
            )

    def test_exact_scope_replay_does_not_collapse_distinct_assignments(self) -> None:
        import test_domain_public_api_contract as support
        fixture = support.DomainPublicApiContractTests()
        outcomes = [self._semantic_outcome("Outcome A."), self._semantic_outcome("Outcome B.")]
        with tempfile.TemporaryDirectory() as root, patch(
            "cortex_runtime.domain_api._worker_capability_provenance", return_value=PROVENANCE,
        ):
            task = self._task(root, outcomes)
            fixture._prepared_plan(task["task_ref"], outcomes)
            ref, worker = fixture._consume_dispatch(fixture._assignment(task["task_ref"], outcomes[0], "validator"))
            fixture._publish_result(ref, outcomes[0], worker)
            dispatched = []
            contexts = []
            for index in range(2):
                context = {}
                current = read_scope(task_ref=task["task_ref"], responsibility="delivery", _connection_context=context)
                key = f"inspect-{index}"
                self.assertTrue(any(node["node"] == key and node["state"] == "ready" for node in current["data"]["nodes"]))
                arguments = dict(task_ref=task["task_ref"], nodes=[key], profile_name="explorer",
                    model="gpt-5.6-luna", reasoning_effort="high", _connection_context=context)
                result = open_assignment(**arguments)
                self.assertFalse(result["replayed"])
                dispatched.append(result)
                contexts.append(arguments)
            self.assertNotEqual(dispatched[0]["native_dispatch"], dispatched[1]["native_dispatch"])
            # Deliberate ambiguous-transport reconciliation fixture; never a
            # qualification policy that retries a confirmed successful spawn.
            for arguments, expected in zip(contexts, dispatched):
                repeated = open_assignment(**arguments)
                self.assertTrue(repeated["replayed"])
                self.assertEqual(repeated["native_dispatch"], expected["native_dispatch"])

    def test_parallel_owner_conflict_has_safe_scope_diagnostics(self) -> None:
        from threading import Barrier
        with tempfile.TemporaryDirectory() as root, patch(
            "cortex_runtime.domain_api._worker_capability_provenance", return_value=PROVENANCE,
        ):
            task = self._task(root, [self._semantic_outcome("Exclusively owned baseline.")])
            barrier = Barrier(2)
            def attempt(index):
                context = {}
                current = read_scope(task_ref=task["task_ref"], responsibility="evidence", _connection_context=context)
                ready = [node for node in current["data"]["nodes"] if node["state"] == "ready"]
                self.assertEqual(len(ready), 1)
                barrier.wait(timeout=10)
                try:
                    return "success", open_assignment(task_ref=task["task_ref"], nodes=[ready[0]["node"]],
                        profile_name="explorer", model="gpt-5.6-luna", reasoning_effort="high" if index else "medium", _connection_context=context)
                except V12ServiceError as error:
                    return "error", error
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(attempt, range(2)))
            self.assertEqual(sum(state == "success" for state, _ in results), 1)
            failure = next(value for state, value in results if state == "error")
            self.assertEqual(failure.code, "command_conflict")
            store, _, _, _ = _resolve_task_context(task["task_ref"])
            self.assertEqual(store._read(lambda c: c.execute("SELECT COUNT(*) FROM execution_assignments").fetchone()[0]), 1)
            self.assertNotIn("dispatch", repr(failure.details))

    def _recovery_setup(self, root: str, *, grouped: bool = False):
        import test_domain_public_api_contract as support
        fixture = support.DomainPublicApiContractTests()
        outcomes = [self._semantic_outcome(f"Recover surface {index}.") for index in range(2)]
        task = self._task(root, outcomes)["task_ref"]
        fixture._prepared_plan(task, outcomes)
        validator, worker = fixture._consume_dispatch(fixture._assignment(task, outcomes[0], "validator"))
        fixture._publish_result(validator, outcomes[0], worker)
        groups = [["inspect-0", "inspect-1"]] if grouped else [["inspect-0"], ["inspect-1"]]
        owners = []
        for keys in groups:
            context = {}
            read_scope(task_ref=task, responsibility="delivery", _connection_context=context)
            dispatch = open_assignment(task_ref=task, nodes=keys, profile_name="explorer",
                model="gpt-5.6-luna", reasoning_effort="high", _connection_context=context)
            ref, worker = fixture._consume_dispatch(dispatch)
            owners.append((ref, worker, keys))
        store, _, _, _ = _resolve_task_context(task)
        return fixture, task, store, outcomes, owners

    def _native_observation(self, root, task, *, children=()):
        from cortex_runtime import native_observation as native
        plugin_data = Path(root) / "native-observation"
        self.assertTrue(native.bind_task(plugin_data,
            task_digest=native.digest(task), session_digest=native.digest("coordinator")))
        self.assertTrue(native.record_projection(plugin_data,
            task_digest=native.digest(task), session_digest=native.digest("coordinator"),
            revision=1, barrier_epoch=0,
            response={"agents": [{"agent_name": "/root", "agent_status": "running"}, *children]},
            arguments={}))
        return {"_native_plugin_data": plugin_data}

    def _reconcile_loss(self, task, store, context, keys):
        import test_domain_public_api_contract as support
        from cortex_runtime import graph_ledger
        from test_typed_publication_transaction import baseline_content
        read_scope(task_ref=task, responsibility="delivery", _connection_context=context)
        dispatch = open_assignment(task_ref=task, nodes=keys, profile_name="explorer",
            model="gpt-5.6-luna", reasoning_effort="high", _connection_context=context)
        self.assertFalse(dispatch["replayed"])
        ref, worker = support.DomainPublicApiContractTests._consume_dispatch(dispatch)
        scope = store._read(lambda c: graph_ledger.assignment_scope(c, worker["assignment_id"]))
        self.assertTrue(scope["artifact"]["reconciliation"])
        self.assertEqual(len(scope["nodes"]), 1)
        self.assertEqual(scope["nodes"][0]["execution_mode"], "read_only")
        content = baseline_content()
        node = scope["nodes"][0]
        content["node_coverage"] = [{"node": node["key"], "coverage": [{
            **subject, "status": "complete", "verification": [{
                "check_key": check["key"], "state": "executed",
                "summary": "Current baseline reconciled without attributing unpublished work.",
            } for check in node["checks"]],
        } for subject in node["verifies"]]}]
        content["artifact"]["baseline_changes"] = content["artifact"]["changes"]
        self.assertTrue(publish_result(task_ref=ref, _connection_context=worker, **content)["published"])
        return scope

    def test_lost_owner_replacement_records_terminal_evidence_and_lineage(self) -> None:
        with tempfile.TemporaryDirectory() as root, patch(
            "cortex_runtime.domain_api._worker_capability_provenance", return_value=PROVENANCE,
        ):
            fixture, task, store, outcomes, owners = self._recovery_setup(root, grouped=True)
            ref, worker, keys = owners[0]
            coordinator = {}
            read_scope(task_ref=task, responsibility="delivery", _connection_context=coordinator)
            with self.assertRaises(V12ServiceError) as unsigned:
                open_assignment(task_ref=task, nodes=keys, profile_name="explorer",
                    model="gpt-5.6-luna", reasoning_effort="high", _connection_context=coordinator)
            self.assertEqual(unsigned.exception.code, "assignment_not_ready")
            original = worker["assignment_id"]
            name = store._read(lambda c: c.execute(
                "SELECT protected_task_name FROM execution_assignments WHERE assignment_id=?", (original,),
            ).fetchone()[0])
            coordinator = self._native_observation(root, task,
                children=[{"agent_name": "/root/" + name, "agent_status": "running"}])
            read_scope(task_ref=task, responsibility="delivery", _connection_context=coordinator)
            with self.assertRaises(V12ServiceError) as present:
                open_assignment(task_ref=task, nodes=keys, profile_name="explorer",
                    model="gpt-5.6-luna", reasoning_effort="high", _connection_context=coordinator)
            self.assertEqual(present.exception.details["reason"], "native_worker_present")
            coordinator = self._native_observation(root, task)
            self._reconcile_loss(task, store, coordinator, keys)
            # Confirmed native loss revokes identity capability; unlike a
            # still-bound steering race, this is not a valid stale publisher.
            with self.assertRaises(V12ServiceError) as lost_capability:
                fixture._publish_result(ref, outcomes[0], worker)
            self.assertEqual(lost_capability.exception.code, "assignment_stale")
            read_scope(task_ref=task, responsibility="delivery", _connection_context=coordinator)
            replacement = open_assignment(task_ref=task, nodes=keys, profile_name="explorer",
                model="gpt-5.6-luna", reasoning_effort="high", _connection_context=coordinator)
            _, successor = fixture._consume_dispatch(replacement)
            parent = store._read(lambda c: c.execute(
                "SELECT parent_delegation_id FROM delegations WHERE delegation_id=?", (successor["assignment_id"],),
            ).fetchone()[0])
            self.assertEqual(parent, original)
            self.assertEqual(store._read(lambda c: c.execute(
                "SELECT COUNT(*) FROM execution_publications WHERE assignment_id=?", (original,),
            ).fetchone()[0]), 0)

    def test_loss_recovery_ignores_unrelated_broad_report_authors(self) -> None:
        with tempfile.TemporaryDirectory() as root, patch(
            "cortex_runtime.domain_api._worker_capability_provenance", return_value=PROVENANCE,
        ):
            fixture, task, store, outcomes, owners = self._recovery_setup(root)
            sibling_ref, sibling, _ = owners[0]
            fixture._publish_result(sibling_ref, outcomes[0], sibling)
            original_ref, original, keys = owners[1]
            before = store._read(lambda c: c.execute("SELECT COUNT(*) FROM execution_publications").fetchone()[0])
            coordinator = self._native_observation(root, task)
            self._reconcile_loss(task, store, coordinator, keys)
            read_scope(task_ref=task, responsibility="delivery", _connection_context=coordinator)
            dispatch = open_assignment(task_ref=task, nodes=keys, profile_name="explorer",
                model="gpt-5.6-luna", reasoning_effort="high", _connection_context=coordinator)
            replacement_ref, replacement = fixture._consume_dispatch(dispatch)
            self.assertNotEqual(replacement_ref, original_ref)
            parent = store._read(lambda c: c.execute(
                "SELECT parent_delegation_id FROM delegations WHERE delegation_id=?", (replacement["assignment_id"],),
            ).fetchone()[0])
            self.assertEqual(parent, original["assignment_id"])
            self.assertNotEqual(parent, sibling["assignment_id"])
            self.assertEqual(store._read(lambda c: c.execute("SELECT COUNT(*) FROM execution_publications").fetchone()[0]), before + 1)

    def test_loss_recovery_requires_exact_complete_grouped_node_scope(self) -> None:
        with tempfile.TemporaryDirectory() as root, patch(
            "cortex_runtime.domain_api._worker_capability_provenance", return_value=PROVENANCE,
        ):
            fixture, task, store, outcomes, owners = self._recovery_setup(root, grouped=True)
            coordinator = self._native_observation(root, task)
            read_scope(task_ref=task, responsibility="delivery", _connection_context=coordinator)
            with self.assertRaises(V12ServiceError) as omitted:
                open_assignment(task_ref=task, profile_name="explorer",
                    model="gpt-5.6-luna", reasoning_effort="high", _connection_context=coordinator)
            self.assertEqual(omitted.exception.code, "invalid_argument")
            scope = self._reconcile_loss(task, store, coordinator, owners[0][2])
            subjects = scope["nodes"][0]["verifies"]
            self.assertEqual({item["name"] for item in subjects}, {item["outcome"] for item in outcomes})

    def test_loss_recovery_never_guesses_among_multiple_native_owners(self) -> None:
        with tempfile.TemporaryDirectory() as root, patch(
            "cortex_runtime.domain_api._worker_capability_provenance", return_value=PROVENANCE,
        ):
            fixture, task, store, outcomes, owners = self._recovery_setup(root)
            coordinator = self._native_observation(root, task)
            read_scope(task_ref=task, responsibility="delivery", _connection_context=coordinator)
            before = store._read(lambda c: c.execute("SELECT COUNT(*) FROM execution_assignments").fetchone()[0])
            with self.assertRaises(V12ServiceError) as omitted:
                open_assignment(task_ref=task, profile_name="explorer",
                    model="gpt-5.6-luna", reasoning_effort="high", _connection_context=coordinator)
            self.assertEqual(omitted.exception.code, "invalid_argument")
            self.assertEqual(store._read(lambda c: c.execute("SELECT COUNT(*) FROM execution_assignments").fetchone()[0]), before)
            self._reconcile_loss(task, store, coordinator, [key for _, _, keys in owners for key in keys])
            self.assertEqual(store._read(lambda c: c.execute("SELECT COUNT(*) FROM execution_assignments WHERE state='lost'").fetchone()[0]), 2)

    def test_terminal_assignment_receipts_do_not_authorize_a_fresh_context(self) -> None:
        import test_domain_public_api_contract as support
        fixture = support.DomainPublicApiContractTests()
        with tempfile.TemporaryDirectory() as root, patch(
            "cortex_runtime.domain_api._worker_capability_provenance", return_value=PROVENANCE,
        ):
            outcome = self._semantic_outcome("Produce predecessor evidence.")
            task = self._task(root, [outcome])
            baseline, worker = fixture._consume_dispatch(fixture._assignment(task["task_ref"], outcome, "baseline"))
            fixture._publish_result(baseline, outcome, worker)
            coordinator = {}
            read_scope(task_ref=task["task_ref"], responsibility="evidence", _connection_context=coordinator)
            dispatch = open_assignment(task_ref=task["task_ref"], profile_name="explorer",
                model="gpt-5.6-luna", reasoning_effort="high",
                bootstrap={"kind": "discovery", "question": "Inspect the established baseline."},
                _connection_context=coordinator)
            consumer_ref = WORKER_REF.search(dispatch["native_dispatch"]["message"]).group(1)
            context = {}
            first = read_task(task_ref=consumer_ref, _connection_context=context)
            self.assertFalse(first["has_more"])
            store, _, assignment_id, _ = _resolve_task_context(consumer_ref)
            def receipts():
                return store._read(lambda c: [
                    tuple(row) for row in c.execute(
                        "SELECT receipt_id,created_sequence FROM report_consumption_receipts "
                        "WHERE consumer_delegation_id=? ORDER BY receipt_id", (assignment_id,),
                    ).fetchall()
                ])
            before = receipts()
            self.assertEqual(len(before), 1)
            self.assertIn("Recovered worker result.", repr(first))
            with self.assertRaises(V12ServiceError) as copied:
                read_task(task_ref=consumer_ref, _connection_context={})
            self.assertEqual(copied.exception.code, "connection_lost")
            self.assertEqual(receipts(), before)

    def _large_reports(self, root, *, count, details):
        import test_domain_public_api_contract as support
        from cortex_runtime import graph_ledger
        from test_typed_publication_transaction import baseline_content
        fixture = support.DomainPublicApiContractTests()
        outcome = self._semantic_outcome("Plan from bounded independent evidence.")
        task = self._task(root, [outcome])["task_ref"]
        baseline, worker = fixture._consume_dispatch(fixture._assignment(task, outcome, "baseline"))
        fixture._publish_result(baseline, outcome, worker)
        store, _, _, _ = _resolve_task_context(task)
        for index in range(count):
            coordinator = {}
            read_scope(task_ref=task, responsibility="evidence", _connection_context=coordinator)
            dispatch = open_assignment(task_ref=task, profile_name="explorer", model="gpt-5.6-luna",
                reasoning_effort="high", bootstrap={"kind": "discovery", "question": f"Inspect distinct surface {index}."},
                _connection_context=coordinator)
            ref, worker = fixture._consume_dispatch(dispatch)
            node = store._read(lambda c: graph_ledger.assignment_scope(c, worker["assignment_id"])["nodes"][0])
            content = baseline_content()
            content["summary"] = f"Evidence {index}: " + "s" * 1700
            content["documentation_impact"] = "Documentation observation: " + "d" * 1700
            content["outcome"] = "Observed result: " + "o" * 1700
            content["risks"] = [f"Fixture risk {index}/{item}: " + "r" * 1800 for item in range(details)]
            content["node_coverage"] = [{"node": node["key"], "coverage": [{
                **subject, "status": "complete", "verification": [{
                    "check_key": check["key"], "state": "executed", "summary": "Bounded independent observation.",
                } for check in node["checks"]],
            } for subject in node["verifies"]]}]
            _validate_schema(PUBLIC_TOOLS["publish_result"]["inputSchema"], {"task_ref": ref, **content})
            self.assertTrue(publish_result(task_ref=ref, _connection_context=worker, **content)["published"])
        coordinator = {}
        read_scope(task_ref=task, responsibility="planning", _connection_context=coordinator)
        dispatch = open_assignment(task_ref=task, profile_name="planner", model="gpt-5.6-terra",
            reasoning_effort="high", bootstrap={"kind": "planning"}, _connection_context=coordinator)
        return WORKER_REF.search(dispatch["native_dispatch"]["message"]).group(1), store

    def test_large_assignment_evidence_uses_server_owned_continuation(self) -> None:
        with tempfile.TemporaryDirectory() as root, patch(
            "cortex_runtime.domain_api._worker_capability_provenance", return_value=PROVENANCE,
        ):
            consumer, store = self._large_reports(root, count=3, details=24)
            context = {}
            first = read_task(task_ref=consumer, _connection_context=context)
            self.assertTrue(first["has_more"])
            def receipts():
                return store._read(lambda c: [
                    tuple(row) for row in c.execute(
                        "SELECT receipt_id,created_sequence FROM assignment_page_receipts ORDER BY receipt_id"
                    ).fetchall()
                ])
            before = receipts()
            repeated = read_task(task_ref=consumer, _connection_context=context)
            self.assertEqual(repeated["data"], first["data"])
            self.assertEqual(receipts(), before)
            pages = [repeated]
            while pages[-1]["has_more"]:
                pages.append(read_task(task_ref=consumer, continue_=True, _connection_context=context))
            self.assertGreater(len(pages), 1)
            self.assertTrue(context["assignment_complete"])
            rendered = repr([page["data"] for page in pages])
            for index in range(3):
                self.assertIn(f"Evidence {index}:", rendered)
            with self.assertRaises(V12ServiceError) as exhausted:
                read_task(task_ref=consumer, continue_=True, _connection_context=context)
            self.assertEqual(exhausted.exception.code, "report_cursor_invalid")

    def test_semantic_steering_revokes_assignment_between_read_pages(self) -> None:
        with tempfile.TemporaryDirectory() as root, patch(
            "cortex_runtime.domain_api._worker_capability_provenance",
            return_value=PROVENANCE,
        ):
            task, selected, worker_ref = self._large_authority(root)
            context: dict = {}
            first = read_task(task_ref=worker_ref, _connection_context=context)
            self.assertTrue(first["has_more"])
            added = self._semantic_outcome("New current-revision outcome.")
            record_steering(
                task_ref=task["task_ref"], response_original="Also implement the new outcome.",
                user_language="en", add=[added], retire=[],
            )
            # The static superseded success belongs to publication, not to
            # an unconsumed assignment: steering revokes paging authority.
            with self.assertRaises(V12ServiceError) as stale_page:
                read_task(task_ref=worker_ref, continue_=True, _connection_context=context)
            self.assertEqual(stale_page.exception.code, "assignment_stale")
            with self.assertRaises(V12ServiceError) as stale_restart:
                read_task(task_ref=worker_ref, _connection_context={})
            self.assertEqual(stale_restart.exception.code, "assignment_stale")
            store, _, assignment_id, _ = _resolve_task_context(worker_ref)
            self.assertEqual(store._read(lambda connection: connection.execute(
                "SELECT COUNT(*) FROM execution_publications WHERE assignment_id=?",
                (assignment_id,),
            ).fetchone()[0]), 0)
            # Supersession does not prove native quiescence. The coordinator
            # cannot start a replacement while the old child may still write.
            coordinator: dict = {}
            current = read_scope(
                task_ref=task["task_ref"], responsibility="evidence",
                _connection_context=coordinator,
            )
            self.assertFalse(any(node["state"] == "ready" for node in current["data"]["nodes"]))
            self.assertEqual(
                read_outcome(task_ref=task["task_ref"], outcome=added["outcome"])["data"]["outcome"],
                added,
            )

    def test_multi_report_assignment_uses_response_limit_not_storage_value_limit(self) -> None:
        with tempfile.TemporaryDirectory() as root, patch(
            "cortex_runtime.domain_api._worker_capability_provenance", return_value=PROVENANCE,
        ):
            consumer, store = self._large_reports(root, count=4, details=8)
            context = {}
            pages = [read_task(task_ref=consumer, _connection_context=context)]
            while pages[-1]["has_more"]:
                pages.append(read_task(task_ref=consumer, continue_=True, _connection_context=context))
            evidence = [page["data"]["evidence"] for page in pages]
            self.assertEqual(len(evidence[0]["reports"]), 5)  # baseline plus four distinct analyses
            self.assertGreater(sum(len(_encoded_bytes(item)) for item in evidence), MCP_OPERATION_MAX_BYTES)
            for index in range(4):
                self.assertIn(f"Evidence {index}:", repr(evidence))
            for page in pages:
                rendered = _success_tool_result(page)
                self.assertLess(len(_encoded_bytes(rendered)), MAX_PHYSICAL_JSONL_FRAME_BYTES)
                self.assertEqual(json.loads(rendered["content"][-1]["text"]), rendered["structuredContent"])

    def test_large_assignment_is_self_contained_in_first_text_response(self) -> None:
        long_outcome = {
            "outcome": "Exact terminal publication outcome.",
            "acceptance": [f"Acceptance {index}: " + "a" * 500 for index in range(40)],
            "constraints": [f"Constraint {index}: " + "c" * 240 for index in range(20)],
            "verification": [f"Verification {index}: " + "v" * 300 for index in range(20)],
        }
        with tempfile.TemporaryDirectory() as root, patch(
            "cortex_runtime.domain_api._worker_capability_provenance",
            return_value=PROVENANCE,
        ):
            task = self._task(root, [long_outcome])
            import test_domain_public_api_contract as support
            assignment = support.DomainPublicApiContractTests()._assignment(task["task_ref"], long_outcome, "baseline")
            worker_ref = WORKER_REF.search(assignment["native_dispatch"]["message"]).group(1)
            read = read_task(
                task_ref=worker_ref,
                _connection_context={},
            )
            reconciliation = read["data"]["assignment"]
            self.assertEqual(
                reconciliation["terminal_publication_kind"], "result",
            )
            self.assertEqual(
                reconciliation["nodes"][0]["verifies"],
                [{"kind": "outcome", "name": long_outcome["outcome"]}],
            )
            rendered = _success_tool_result(read)
            compact = json.loads(rendered["content"][0]["text"])
            self.assertEqual(
                compact,
                {
                    "task_ref": worker_ref,
                    "assignment": reconciliation,
                    "has_more": False,
                },
            )
            self.assertEqual(
                json.loads(rendered["content"][-1]["text"]),
                rendered["structuredContent"],
            )
            self.assertEqual(
                rendered["structuredContent"]["data"]
                ["assignment"]["nodes"],
                reconciliation["nodes"],
            )

    def test_nested_evidence_view_link_leads_the_text_response(self) -> None:
        link = "[Open plan revision](/private/cortex/tasks/t_example/plans/revisions/plan.md)"
        rendered = _success_tool_result({
            "task_ref": "t_0123456789ab",
            "data": {
                "reports": [],
                "human_view": {
                    "kind": "plan",
                    "status": "ready",
                    "markdown_link": link,
                },
                "human_views": [{
                    "kind": "plan",
                    "status": "ready",
                    "markdown_link": link,
                }],
            },
            "has_more": False,
        })

        self.assertEqual(rendered["content"][0]["text"], link)
        self.assertEqual(rendered["content"].count({"type": "text", "text": link}), 1)

    def _large_authority(self, root: str) -> tuple[dict, dict, str]:
        """Grow real bounded contracts, never inject historical oversized rows."""
        outcomes = [
            {
                **self._semantic_outcome(f"Large authority requirement {index}."),
                "acceptance": [
                    f"Criterion {item}: " + "bounded source evidence " * 75
                    for item in range(24)
                ],
            }
            for index in range(4)
        ]
        task = self._task(root, outcomes[:1])
        for outcome in outcomes[1:]:
            record_steering(
                task_ref=task["task_ref"],
                response_original=f"Also implement {outcome['outcome']}",
                user_language="en", add=[outcome], retire=[],
            )
        coordinator: dict = {}
        current = read_scope(task_ref=task["task_ref"], responsibility="evidence", _connection_context=coordinator)
        ready = [node for node in current["data"]["nodes"] if node["state"] == "ready"]
        self.assertEqual(len(ready), 1)
        assignment = open_assignment(
            task_ref=task["task_ref"], nodes=[ready[0]["node"]],
            profile_name="explorer", model="gpt-5.6-luna", reasoning_effort="high",
            _connection_context=coordinator,
        )
        worker_ref = WORKER_REF.search(assignment["native_dispatch"]["message"]).group(1)
        return task, ready[0], worker_ref

    def test_large_authority_is_paginated_and_restarts_exactly(self) -> None:
        from test_typed_publication_transaction import baseline_content

        with tempfile.TemporaryDirectory() as root, patch(
            "cortex_runtime.domain_api._worker_capability_provenance",
            return_value=PROVENANCE,
        ):
            task, selected, worker_ref = self._large_authority(root)
            store, _task_id, assignment_id, _coordinator_ref = _resolve_task_context(worker_ref)

            def receipts():
                return store._read(lambda connection: [
                    tuple(row) for row in connection.execute(
                        "SELECT receipt_id,created_sequence,page_digest FROM assignment_page_receipts "
                        "WHERE assignment_id=? ORDER BY private_position", (assignment_id,),
                    ).fetchall()
                ])

            context: dict = {}
            first = read_task(task_ref=worker_ref, _connection_context=context)
            self.assertTrue(first["has_more"])
            self.assertEqual(first["data"]["assignment_page"]["phase"], "authority")
            self.assertNotIn("next_cursor", repr(first))
            before = receipts()
            restarted = read_task(task_ref=worker_ref, _connection_context=context)
            self.assertEqual(restarted["data"], first["data"])
            self.assertEqual(receipts(), before)
            pages = [restarted]
            while pages[-1]["has_more"]:
                pages.append(read_task(
                    task_ref=worker_ref, continue_=True, _connection_context=context,
                ))
            self.assertGreater(len(pages), 1)
            self.assertTrue(context["assignment_complete"])
            self.assertEqual(context["actor"], "worker")
            rendered = repr([page["data"] for page in pages])
            for index in range(4):
                self.assertIn(f"Large authority requirement {index}.", rendered)
            self.assertIn("Criterion 23:", rendered)

            terminal_receipts = receipts()
            recovered = [read_task(task_ref=worker_ref, _connection_context=context)]
            self.assertTrue(recovered[0]["has_more"])
            self.assertFalse(context["assignment_complete"])
            while recovered[-1]["has_more"]:
                recovered.append(read_task(
                    task_ref=worker_ref, continue_=True, _connection_context=context,
                ))
            self.assertTrue(context["assignment_complete"])
            self.assertEqual([page["data"] for page in recovered], [page["data"] for page in pages])
            self.assertEqual(receipts(), terminal_receipts)
            content = baseline_content()
            content["node_coverage"] = [{
                "node": selected["node"],
                "coverage": [{
                    **subject, "status": "complete",
                    "verification": [{
                        "check_key": "reconciliation", "state": "executed",
                        "summary": "Stable baseline observed after complete authority recovery.",
                    }],
                } for subject in selected["verifies"]],
            }]
            published = publish_result(
                task_ref=worker_ref, _connection_context=context, **content,
            )
            self.assertTrue(published["published"])
            self.assertFalse(published["replayed"])
            with self.assertRaises(V12ServiceError) as copied:
                read_task(task_ref=worker_ref, _connection_context={})
            self.assertEqual(copied.exception.code, "connection_lost")

    def test_assignment_fragment_boundaries_and_multibyte_integrity(self) -> None:
        path = ["value"]
        low, high = 0, _ASSIGNMENT_STRING_FRAGMENT_BYTES
        while low < high:
            middle = (low + high + 1) // 2
            candidate = {"path": path, "value": "a" * middle}
            if len(_encoded_bytes(candidate)) <= _ASSIGNMENT_STRING_FRAGMENT_BYTES:
                low = middle
            else:
                high = middle - 1
        boundary = _assignment_fragments({"value": "a" * low})
        over = _assignment_fragments({"value": "a" * (low + 1)})
        self.assertEqual(len(boundary), 1)
        self.assertIn("value", boundary[0])
        self.assertNotIn("value", over[0])
        self.assertEqual(over[0]["string_state"], "complete")
        self.assertEqual(
            "".join(item.get("value", item.get("text", "")) for item in over),
            "a" * (low + 1),
        )
        multibyte = "🙂é界" * 20_000
        parts = _assignment_fragments({"value": multibyte})
        self.assertEqual(
            "".join(item.get("value", item.get("text", "")) for item in parts),
            multibyte,
        )
        self.assertTrue(all(len(item.get("text", "").encode("utf-8")) <= _ASSIGNMENT_STRING_FRAGMENT_BYTES for item in parts))

    def test_aggregate_byte_diagnostic_and_large_success_framing(self) -> None:
        contract = PUBLIC_TOOLS["publish_result"]
        schema = contract["inputSchema"]
        output_schema = contract["outputSchema"]
        self.assertIn("replayed", output_schema["required"])
        self.assertIn(
            "All three states end worker activity",
            output_schema["properties"]["state"]["description"],
        )
        self.assertEqual(schema["maxBytes"], MCP_OPERATION_MAX_BYTES)
        from test_typed_publication_transaction import baseline_content
        arguments = {"task_ref": "t_" + "a" * 12 + "_" + "b" * 32, **baseline_content()}

        def encoded_bytes(value: dict) -> int:
            return len(json.dumps(
                value, ensure_ascii=False, separators=(",", ":"), allow_nan=False,
            ).encode("utf-8"))

        # Fill separate schema-valid fields rather than bypassing the 2048
        # character limit with one oversized summary.
        from copy import deepcopy
        at_limit = deepcopy(arguments)
        at_limit["risks"] = ["r"]
        while MCP_OPERATION_MAX_BYTES - encoded_bytes(at_limit) > 2047:
            at_limit["risks"].insert(0, str(len(at_limit["risks"])) + "x" * 1998)
        at_limit["risks"][-1] += "x" * (MCP_OPERATION_MAX_BYTES - encoded_bytes(at_limit))
        self.assertEqual(encoded_bytes(at_limit), MCP_OPERATION_MAX_BYTES)
        below_limit = deepcopy(at_limit)
        below_limit["risks"][-1] = below_limit["risks"][-1][:-1]
        self.assertEqual(encoded_bytes(below_limit), MCP_OPERATION_MAX_BYTES - 1)
        _validate_schema(schema, below_limit)
        _validate_schema(schema, at_limit)

        above = deepcopy(at_limit)
        above["risks"][-1] += "x"
        with self.assertRaises(_SchemaError) as caught:
            _validate_schema(schema, above)
        error = caught.exception
        self.assertEqual(error.path, "$")
        self.assertEqual(error.actual_bytes, MCP_OPERATION_MAX_BYTES + 1)
        self.assertEqual(error.max_bytes, MCP_OPERATION_MAX_BYTES)
        failure = _validation_failure(
            error, tool_name="publish_result", arguments=above,
            input_schema=schema,
        )
        self.assertEqual(failure["details"]["path"], "$")
        self.assertNotIn("field", failure["details"])
        self.assertEqual(failure["details"]["actual_bytes"], MCP_OPERATION_MAX_BYTES + 1)
        self.assertEqual(failure["details"]["max_bytes"], MCP_OPERATION_MAX_BYTES)
        self.assertEqual(failure["details"]["sections"][0]["section"], "risks")
        self.assertIn("exactly one", failure["action"])
        rendered_error = _tool_error_result(failure, mutation="publish_result")
        text = rendered_error["content"][0]["text"]
        self.assertNotIn("Field:", text)
        self.assertNotIn("Handle rule:", text)

        unicode_heavy = deepcopy(arguments)
        unicode_heavy["risks"] = [str(index) + "🔒" * 1024 for index in range(16)]
        self.assertLess(sum(map(len, unicode_heavy["risks"])), MCP_OPERATION_MAX_BYTES)
        with self.assertRaises(_SchemaError) as unicode_error:
            _validate_schema(schema, unicode_heavy)
        self.assertEqual(
            unicode_error.exception.actual_bytes,
            encoded_bytes(unicode_heavy),
        )
        self.assertGreater(
            unicode_error.exception.actual_bytes,
            MCP_OPERATION_MAX_BYTES,
        )

        large = _success_tool_result({"data": "z" * 130_000})
        self.assertEqual(
            large["content"][-1]["text"],
            "Complete Cortex result is available in structuredContent.",
        )
        self.assertEqual(len(large["structuredContent"]["data"]), 130_000)
        self.assertLess(
            len(json.dumps(large, separators=(",", ":")).encode("utf-8")),
            MAX_PHYSICAL_JSONL_FRAME_BYTES,
        )

        large_assignment = _success_tool_result({
            "task_ref": "t_123456789abc_" + "1" * 32,
            "data": {
                "assignment": {
                    "terminal_publication_kind": "result",
                    "nodes": [{"key": "baseline", "verifies": [{"kind": "outcome", "name": "Exact large outcome."}]}],
                    "artifact": arguments["artifact"],
                },
                "large_body": "z" * 130_000,
            },
            "has_more": False,
        })
        compact_assignment = json.loads(large_assignment["content"][0]["text"])
        self.assertEqual(
            compact_assignment["assignment"]
            ["nodes"][0]["verifies"],
            [{"kind": "outcome", "name": "Exact large outcome."}],
        )
        self.assertEqual(
            large_assignment["content"][-1]["text"],
            "Complete Cortex result is available in structuredContent.",
        )

    def test_governance_mode_is_structurally_required(self) -> None:
        schema = PUBLIC_TOOLS["assess_governance"]["inputSchema"]
        with self.assertRaises(_SchemaError) as caught:
            _validate_schema(schema, {"task_ref": "t_" + "a" * 12})
        self.assertEqual(caught.exception.path, "$.mode")
        self.assertEqual(caught.exception.missing_fields, ("mode",))


if __name__ == "__main__":
    unittest.main()
