"""All packaged profiles consume typed scope and publish once; not model/live work."""
import pytest

from cortex_runtime.domain_api import publish_result
from cortex_runtime.v12_contract import task_ref as public_task_ref
from cortex_runtime.worker_message import packaged_profile_names, assignment_worker_policy
from test_domain_public_api_contract import PROVENANCE
from test_node_assignment_receipts import node_case
from test_typed_public_api import dispatch_and_consume
from test_typed_publication_transaction import baseline_content


def test_profile_inventory_is_the_complete_packaged_matrix():
    assert len(packaged_profile_names()) == 22
    assert len(set(packaged_profile_names())) == 22


@pytest.mark.parametrize("profile", packaged_profile_names())
def test_each_profile_consumes_and_publishes_assigned_kind_on_first_call(node_case, monkeypatch, profile):
    store, args = node_case
    task = public_task_ref(args["task_id"])
    monkeypatch.setattr("cortex_runtime.domain_api._worker_capability_provenance", lambda: PROVENANCE)
    policy = assignment_worker_policy(profile)
    assert policy["profile_name"] == profile
    assert "Packaged profiles supply expertise" in policy["common_policy"]
    assert "terminal publication kind" in policy["common_policy"]
    worker_ref, worker = dispatch_and_consume(task, nodes=["baseline"], profile=profile)
    observed = store._read(lambda c: c.execute("SELECT a.terminal_kind,d.profile_name FROM execution_assignments a JOIN delegations d ON d.delegation_id=a.assignment_id WHERE a.assignment_id=?", (worker["assignment_id"],)).fetchone())
    assert tuple(observed) == ("result", profile)
    result = publish_result(task_ref=worker_ref, _connection_context=worker, **baseline_content())
    assert result["published"] and not result["replayed"]
    assert store._read(lambda c: c.execute("SELECT COUNT(*) FROM reports").fetchone()[0]) == 1
    assert store._read(lambda c: c.execute("SELECT COUNT(*) FROM execution_publications").fetchone()[0]) == 1
