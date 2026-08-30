from __future__ import annotations

import os
import tempfile
import threading
import unittest
from pathlib import Path
import sys

SCRIPTS = Path(__file__).resolve().parents[1] / "plugins" / "cortex" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from cortex_runtime.v12_store import V12Store, V12StoreError


class ClarificationBindingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.home = tempfile.TemporaryDirectory(prefix="cortex-v15-home-")
        self.root = tempfile.TemporaryDirectory(prefix="cortex-v15-project-")
        os.environ["CODEX_HOME"] = self.home.name
        self.store = V12Store(self.root.name)
        self.task, _ = self.store.create_task(
            objective="Binding test", user_request_original="Binding test", user_language="en",
            requirements=["r"], constraints=["c"], acceptance_criteria=["a"],
            verification_plan=["v"], context={}, idempotency_key="task",
        )
        self.task_id = self.task["task"]["task_id"]

    def tearDown(self) -> None:
        os.environ.pop("CODEX_HOME", None)
        self.root.cleanup(); self.home.cleanup()

    def test_issue_reissue_and_consume_replay_conflict(self) -> None:
        first = self.store.issue_clarification_binding(task_id=self.task_id, prompt="Choose theme", prompt_language="en")
        again = self.store.issue_clarification_binding(task_id=self.task_id, prompt="Choose theme", prompt_language="en")
        self.assertEqual(first["binding"]["clarification_binding"], again["binding"]["clarification_binding"])
        token = first["binding"]["clarification_binding"]
        result, _ = self.store.record_user_decision(task_id=self.task_id, subject_type="task", subject_id=self.task_id, decision_type="clarification", prompt="Choose theme", response_original="warm", user_language="en", clarification_binding=token, idempotency_key="decision-1")
        replay, replayed = self.store.record_user_decision(task_id=self.task_id, subject_type="task", subject_id=self.task_id, decision_type="clarification", prompt="Choose theme", response_original="warm", user_language="en", clarification_binding=token, idempotency_key="decision-2")
        self.assertTrue(replayed); self.assertEqual(result["decision"]["decision_id"], replay["decision"]["decision_id"])
        with self.assertRaisesRegex(V12StoreError, "already consumed"):
            self.store.record_user_decision(task_id=self.task_id, subject_type="task", subject_id=self.task_id, decision_type="clarification", prompt="Choose theme", response_original="dark", user_language="en", clarification_binding=token, idempotency_key="decision-3")

    def test_stale_revision_and_cross_project_fail_closed(self) -> None:
        binding = self.store.issue_clarification_binding(task_id=self.task_id, prompt="Choose theme", prompt_language="en")["binding"]["clarification_binding"]
        self.store.record_user_decision(task_id=self.task_id, subject_type="task", subject_id=self.task_id, decision_type="steer", prompt="steer", response_original="update", user_language="en", steering_delta={"add": [{"category": "requirement", "text": "new"}]}, idempotency_key="steer")
        with self.assertRaises(V12StoreError) as ctx:
            self.store.record_user_decision(task_id=self.task_id, subject_type="task", subject_id=self.task_id, decision_type="clarification", prompt="Choose theme", response_original="warm", user_language="en", clarification_binding=binding, idempotency_key="stale")
        self.assertIn(ctx.exception.code, {"clarification_binding_mismatch", "clarification_binding_stale"})

    def test_concurrent_consume_has_one_decision(self) -> None:
        token = self.store.issue_clarification_binding(task_id=self.task_id, prompt="Concurrent?", prompt_language="en")["binding"]["clarification_binding"]
        results: list[object] = []
        def consume(index: int) -> None:
            try:
                results.append(self.store.record_user_decision(task_id=self.task_id, subject_type="task", subject_id=self.task_id, decision_type="clarification", prompt="Concurrent?", response_original="yes", user_language="en", clarification_binding=token, idempotency_key=f"response-{index}"))
            except Exception as exc: results.append(exc)
        threads = [threading.Thread(target=consume, args=(index,)) for index in range(2)]
        for thread in threads: thread.start()
        for thread in threads: thread.join()
        self.assertEqual(len(results), 2)
        self.assertTrue(all(not isinstance(item, Exception) for item in results))
        self.assertEqual(len({item[0]["decision"]["decision_id"] for item in results}), 1)


if __name__ == "__main__":
    unittest.main()
