"""Integration contract for the Phase C DomainKernel/receipt adapter."""
from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugins/cortex/scripts"))

from cortex_runtime.domain_kernel import CommandContext, DomainKernel
from cortex_runtime.v12_store import V12Store


class DomainKernelReceiptIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="cortex-kernel-")
        self.root = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    @staticmethod
    def payload(value: int = 1) -> dict[str, object]:
        return {"project_root": "/tmp/project", "value": value}

    def kernel(self) -> DomainKernel:
        return DomainKernel(V12Store(self.root))

    def test_replay_conflict_failed_admission_and_build_identity(self) -> None:
        kernel = self.kernel()
        first = kernel.execute_command(
            "open_task", self.payload(), aggregate_type="task", aggregate_id="t1",
            logical_slot=lambda op, payload: "task/t1/create", mutate=lambda conn: {"accepted": True},
            context=CommandContext("open_task", build_id="sha256:verified"),
        )
        self.assertTrue(first.ok)
        self.assertFalse(first.replayed)
        replay = kernel.run_command(
            "open_task", self.payload(), aggregate_type="task", aggregate_id="t1",
            logical_slot="task/t1/create", mutate=lambda conn: self.fail("replay mutated"),
        )
        self.assertTrue(replay.ok)
        self.assertTrue(replay.replayed)
        self.assertEqual(replay.value, first.value)
        self.assertEqual(kernel.receipt_store.lookup_command_receipt("task/t1/create")["build_id"], "sha256:verified")  # type: ignore[union-attr]
        conflict = kernel.execute_command(
            "open_task", self.payload(2), aggregate_type="task", aggregate_id="t1",
            logical_slot="task/t1/create", mutate=lambda conn: {"bad": True},
        )
        self.assertFalse(conflict.ok)
        self.assertEqual(conflict.error.code, "command_conflict")  # type: ignore[union-attr]
        failed = kernel.execute_command(
            "open_clarification", {}, aggregate_type="task", aggregate_id="t2",
            logical_slot="task/t2/decision", mutate=lambda conn: {"bad": True},
        )
        self.assertFalse(failed.ok)
        self.assertIsNone(kernel.receipt_store.lookup_command_receipt("task/t2/decision"))  # type: ignore[union-attr]

    def test_concurrent_identical_calls_have_one_mutation(self) -> None:
        calls = 0
        lock = threading.Lock()
        outputs = []

        def mutate(connection):
            nonlocal calls
            with lock:
                calls += 1
            return {"accepted": True}

        def call() -> None:
            outputs.append(DomainKernel(V12Store(self.root)).execute_command(
                "open_task", self.payload(), aggregate_type="task", aggregate_id="t3",
                logical_slot="task/t3/create", mutate=mutate,
            ))

        threads = [threading.Thread(target=call) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(calls, 1)
        self.assertEqual(len(outputs), 4)
        self.assertEqual(sum(not item.replayed for item in outputs), 1)
        self.assertTrue(all(item.ok for item in outputs))

    def test_lost_response_reconciles_and_projects_are_project_scoped(self) -> None:
        kernel = self.kernel()
        result = kernel.execute_command(
            "open_task", self.payload(), aggregate_type="task", aggregate_id="t4",
            logical_slot="task/t4/create", mutate=lambda conn: {"accepted": True},
        )
        self.assertTrue(result.ok)
        # A caller that lost the response can reconcile from the durable receipt.
        receipt = kernel.receipt_store.lookup_command_receipt("task/t4/create")  # type: ignore[union-attr]
        self.assertEqual(receipt["status"], "completed")  # type: ignore[index]
        other_root = self.root / "other-project"
        other_root.mkdir()
        other = V12Store(other_root)
        other_result = DomainKernel(other).execute_command(
            "open_task", self.payload(), aggregate_type="task", aggregate_id="t4",
            logical_slot="task/t4/create", mutate=lambda conn: {"accepted": "other"},
        )
        self.assertTrue(other_result.ok)
        self.assertNotEqual(result.value, other_result.value)

    def test_queries_are_receipt_free(self) -> None:
        kernel = self.kernel()
        result = kernel.execute_query("read_task", {"task_ref": "task-1"}, query=lambda payload: {"state": "open"})
        self.assertTrue(result.ok)
        self.assertIsNone(kernel.receipt_store.lookup_command_receipt("task-1/read"))  # type: ignore[union-attr]


if __name__ == "__main__":
    unittest.main()
