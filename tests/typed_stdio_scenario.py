"""Stateful source-MCP scenarios. Synthetic host leases are not live evidence."""
from plan_fixtures import ordinary_candidates
from contextlib import ExitStack
from pathlib import Path
import re

from jsonschema import validate
from test_command_receipts import _source_stdio_session, _write_host_worker_receipt
from test_publication_projection_repair import document_graph
from test_graph_ledger import observation
from test_typed_publication_transaction import baseline_content


def run_matrix(home, root):
    exercised = set()
    root = Path(root)
    root.mkdir(exist_ok=True)
    with ExitStack() as stack:
        coordinator = stack.enter_context(_source_stdio_session(str(home)))
        contracts = {tool["name"]: tool["inputSchema"] for tool in
                     coordinator.rpc("tools/list", {})["result"]["tools"]}

        def call(connection, name, **arguments):
            validate(arguments, contracts[name])
            reply = connection(name, arguments)
            assert not reply.get("error"), (name, reply.get("error"))
            result = reply["result"]
            assert not result.get("isError"), (name, result.get("structuredContent"))
            data = result["structuredContent"]
            assert not data.get("replayed"), name
            exercised.add(name)
            return data

        def pages(connection, name, **arguments):
            result = call(connection, name, **arguments)
            values = [result]
            while result.get("has_more"):
                result = call(connection, name, **arguments, **{"continue": True})
                values.append(result)
            return values

        def create(connection, name, request):
            project = root / name
            project.mkdir()
            return call(connection, "open_task", project_root=str(project), request_original=request,
                user_language="en", constraints=["Isolated source protocol fixture"],
                outcomes=[{"outcome": "Product", "acceptance": ["Product works"],
                           "constraints": [], "verification": []}])["task_ref"]

        def find_nodes(value):
            if isinstance(value, dict):
                if "terminal_publication_kind" in value and "nodes" in value:
                    return value["nodes"]
                for nested in value.values():
                    found = find_nodes(nested)
                    if found is not None:
                        return found
            elif isinstance(value, list):
                for nested in value:
                    found = find_nodes(nested)
                    if found is not None:
                        return found
            return None

        def dispatch(connection, task, *, nodes=None, bootstrap=None, responsibility="evidence", profile="general"):
            call(connection, "read_scope", task_ref=task, responsibility=responsibility)
            result = call(connection, "open_assignment", task_ref=task, profile_name=profile,
                model="gpt-5.6-luna", reasoning_effort="high",
                **({"nodes": nodes} if nodes is not None else {"bootstrap": bootstrap}))
            assert "model" not in result["native_dispatch"]
            worker_ref = re.search(r'"task_ref":"(t_[0-9a-f]{12}_[0-9a-f]{32})"',
                                   result["native_dispatch"]["message"]).group(1)
            identity = ("worker-" + worker_ref[-12:], "turn-" + worker_ref[-12:], "matrix-session")
            _write_host_worker_receipt(str(Path(home) / "plugins/data/cortex-cortex"), worker_ref,
                agent_id=identity[0], turn_id=identity[1], session_id=identity[2])
            worker = stack.enter_context(_source_stdio_session(str(home), host_identity=identity))
            consumed = pages(worker, "read_task", task_ref=worker_ref)
            assigned = find_nodes(consumed)
            assert assigned, "Consumed assignment must expose its typed nodes"
            return worker, worker_ref, assigned

        def publish(dispatched, *, kind="result", start="a", end="a"):
            worker, ref, nodes = dispatched
            content = baseline_content()
            content["artifact"] = observation(start * 64, end * 64)
            content["node_coverage"] = [{"node": node["key"], "coverage": [
                {**subject, "status": "complete", "verification": [
                    {"check_key": check["key"], "state": "executed", "summary": "Synthetic protocol fixture observation"}
                    for check in node["checks"]]}
                for subject in ([{"kind": "contribution", "name": name} for name in node["contributions"]]
                                + node["verifies"])]} for node in nodes]
            if kind == "documentation":
                content.pop("outcome")
                content.pop("changes")
                content.update(findings=[], recommendations=[])
            assert call(worker, "publish_" + kind, task_ref=ref, **content)["published"]

        task = create(coordinator, "execution", "Build the product and assess documentation. Show the plan for approval.")
        call(coordinator, "assess_governance", task_ref=task, mode="light", user_review_requested=True)
        publish(dispatch(coordinator, task, nodes=["baseline"]))
        planner, planner_ref, _ = dispatch(coordinator, task, bootstrap={"kind": "planning"},
                                           responsibility="planning", profile="planner")
        assert call(planner, "publish_plan", task_ref=planner_ref, status="completed", summary="Product plan",
                    scope="Product", candidates=ordinary_candidates(document_graph()), artifact=observation(), risks=[], unresolved=[])["published"]
        publish(dispatch(coordinator, task, nodes=["validate-candidate"]))
        pages(coordinator, "read_evidence", task_ref=task, report_policy="active_plan")
        packet = call(coordinator, "open_plan_review", task_ref=task, prompt="Review this product plan.", prompt_language="en")
        assert packet["data"]["human_view"]["markdown_link"].startswith("[Open plan revision](")
        call(coordinator, "record_plan_review", task_ref=task, outcome="approve",
             response_original="Approve this plan.", user_language="en")
        publish(dispatch(coordinator, task, nodes=["implementation"], responsibility="delivery"), end="b")
        publish(dispatch(coordinator, task, nodes=["documentation"], profile="technical_writer"),
                kind="documentation", start="b", end="b")
        call(coordinator, "read_state", task_ref=task)
        call(coordinator, "open_clarification", task_ref=task, purpose="closure_review", options=["revise", "close"],
             prompt="The product checks are complete. Review the result: revise or close?", prompt_language="en")
        call(coordinator, "record_clarification", task_ref=task, outcome="close",
             response_original="Close this checked task.", user_language="en")
        assert call(coordinator, "close_task", task_ref=task, verdict="ready")["state"] == "closed"

        branch_connection = stack.enter_context(_source_stdio_session(str(home)))
        branch = create(branch_connection, "branch", "Choose the product content language before planning.")
        call(branch_connection, "assess_governance", task_ref=branch, mode="light")
        call(branch_connection, "open_steering", task_ref=branch,
             prompt="Should the product content be English or Romanian?", prompt_language="en")
        call(branch_connection, "read_scope", task_ref=branch, responsibility="delivery")
        old = call(branch_connection, "read_outcome", task_ref=branch, outcome="Product")["data"]["outcome"]
        changed = call(branch_connection, "record_steering", task_ref=branch,
            response_original="Use Romanian.", user_language="en", retire=["Product"],
            add=[{**old, "acceptance": [*old["acceptance"], "Content is Romanian"]}])
        assert changed["effect"]["effective_revision"] == 2

        family_connection = stack.enter_context(_source_stdio_session(str(home)))
        family_task = create(family_connection, "alternatives", "Choose offline or online behavior from complete validated plans.")
        call(family_connection, "assess_governance", task_ref=family_task, mode="light")
        publish(dispatch(family_connection, family_task, nodes=["baseline"]))
        planner, planner_ref, _ = dispatch(family_connection, family_task, bootstrap={"kind": "planning"},
            responsibility="planning", profile="planner")
        candidates = [{"key": name, "consequences": ["Use " + name + " behavior"],
            "delta": {"retire": ["Product"], "add": [{"outcome": "Product", "acceptance": ["Works " + name],
                "constraints": [], "verification": []}]}, "graph": document_graph()} for name in ("offline", "online")]
        call(planner, "publish_plan", task_ref=planner_ref, status="completed", summary="Complete product alternatives",
            scope="Product", candidates=candidates, artifact=observation(), risks=[], unresolved=[])
        publish(dispatch(family_connection, family_task, nodes=["validate-candidate"]))
        choice = call(family_connection, "open_plan_review", task_ref=family_task,
            prompt="Choose offline or online and approve, revise or cancel.", prompt_language="en")
        assert [item["key"] for item in choice["data"]["alternatives"]] == ["offline", "online"]
        selected = call(family_connection, "record_plan_review", task_ref=family_task, outcome="approve",
            branch_key="offline", response_original="Select offline and approve it.", user_language="en")
        assert selected["effect"]["effective_revision"] == 2 and not selected["effect"]["reconciliation_required"]
        ready = call(family_connection, "read_scope", task_ref=family_task, responsibility="delivery")
        assert next(item["state"] for item in ready["data"]["nodes"] if item["node"] == "implementation") == "ready"

        recovering_connection = stack.enter_context(_source_stdio_session(str(home)))
        recovering = create(recovering_connection, "recovery", "Inspect the product and provide its inspection chronology.")
        call(recovering_connection, "assess_governance", task_ref=recovering, mode="minimal")
        dispatch(recovering_connection, recovering, nodes=["baseline"])
        restored = stack.enter_context(_source_stdio_session(str(home)))
        assert call(restored, "read_state", task_ref=recovering)["data"]["recovery_required"]
        pages(restored, "read_continuations", task_ref=recovering)
        pages(restored, "read_timeline", task_ref=recovering)
        assert exercised == set(contracts) and len(exercised) == 20
