"""Bounded report-only consultant profile and routing policy regressions."""
import json
import runpy
from pathlib import Path

from cortex_package import PLUGIN

ROOT = Path(__file__).resolve().parents[1]
OBSERVER = runpy.run_path(str(ROOT / "scripts/cortex-desktop-dev"))


def test_consultant_is_the_opt_in_twenty_third_generated_profile():
    profiles = json.loads((PLUGIN / "profiles.json").read_text())["profiles"]
    consultant = next(item for item in profiles if item["name"] == "senior_consultant")
    assert len(profiles) == 23
    assert consultant["report_template"] == "general"
    assert (PLUGIN / "agents/senior-consultant.toml").is_file()
    skill = (PLUGIN / "skills/worker-senior-consultant/SKILL.md").read_text()
    for phrase in (
        "exact selected published report IDs",
            "at most\n   4,000 Unicode characters",
            "exact data request, and recommended profile",
        "never repeat an unchanged packet",
            "do not edit it",
    ):
        assert phrase in skill


def test_consultant_observer_allows_only_report_protocol_tools():
    check = OBSERVER["call_policy_flags"]
    for tool in (
        "mcp__cortex__read_report",
        "mcp__cortex__create_draft",
        "mcp__cortex__read_draft",
        "mcp__cortex__write_report",
    ):
        assert check(tool, "{}", "senior_consultant", "/tmp/project") == []
    assert "senior_consultant_report_catalogue_access" in check(
        "mcp__cortex__list_reports", "{}", "senior_consultant", "/tmp/project"
    )
    assert "senior_consultant_forbidden_project_access" in check(
        "exec_command", '{"cmd":"printf evidence"}', "senior_consultant", "/tmp/project"
    )
    assert "senior_consultant_forbidden_project_access" in check(
        "mcp__codebase_memory__search_graph", "{}", "senior_consultant", "/tmp/project"
    )
    assert "senior_consultant_forbidden_cortex_tool" in check(
        "mcp__cortex__set_governance", "{}", "senior_consultant", "/tmp/project"
    )


def test_consultation_model_classes_keep_sol_defaults_and_bound_astra_escalation():
    policy = OBSERVER["worker_model_policy"]
    assert policy("gpt-5.6-sol", "medium", "consultation") == []
    assert policy("gpt-5.6-sol", "low", "consultation-narrow") == []
    assert policy("gpt-5.6-sol", "high", "consultation-hard") == []
    assert policy("gpt-6-astra", "high", "consultation-deeper") == []
    assert policy("gpt-6-astra", "medium", "consultation") == ["worker_model_policy_violation"]
    assert policy("gpt-5.6-sol", "medium", "consultation-narrow") == ["worker_model_policy_violation"]


def test_consultant_assignment_metadata_is_native_and_does_not_change_main_model():
    payload = json.dumps({
        "task_name": "one-question-consultation",
        "message": "$cortex:worker-senior-consultant\nPolicy class: consultation\nUser-requested override: no",
        "model": "gpt-5.6-sol",
        "reasoning_effort": "medium",
        "fork_turns": "none",
    })
    metadata = OBSERVER["safe_call_metadata"]("spawn_agent", payload)
    assert metadata["assigned_profile"] == "senior_consultant"
    assert metadata["policy_class"] == "consultation"
    assert metadata["requested_model"] == "gpt-5.6-sol"
    assert OBSERVER["LIVE_MODEL"] == "gpt-5.6-luna"


def test_consultant_has_no_general_worker_execution_permissions():
    skill = (PLUGIN / "skills/worker-senior-consultant/SKILL.md").read_text()
    assert "Do not run commands, tests or project checks" in skill
    assert "You may investigate, implement" not in skill
    assert "Your only write is your own server-issued unpublished report draft" in skill


def test_consultant_draft_edits_require_an_observed_own_draft():
    metadata = OBSERVER["draft_call_metadata"]
    check = OBSERVER["call_policy_violations"]
    patch = "*** Begin Patch\n*** Update File: /tmp/project/.cortex/draft-reports/d_123456789abc.md\n@@\n-old\n+new\n*** End Patch"
    fields = metadata("apply_patch", patch)
    edit = {"thread_id":"consultant", "parent_thread_id":"parent", "role":"senior_consultant", "tool":"apply_patch", "outcome":"success", **fields}
    assert check([edit])
    create = {"thread_id":"consultant", "role":"senior_consultant", "tool":"mcp__cortex__create_draft", "draft_id":"d_123456789abc", "outcome":"success"}
    assert check([create, edit]) == []
    assert metadata("apply_patch", patch.replace("draft-reports", "pipeline-drafts"))["ordinary_draft_edit"] is False
    assert metadata("apply_patch", patch + "\n*** Update File: /tmp/project/app.py\n") == {}


def test_consultant_sol_exception_does_not_allow_sol_for_other_workers():
    check = OBSERVER["call_policy_violations"]
    row = {"thread_id":"parent", "role":"coordinator", "tool":"spawn_agent", "outcome":"success",
           "assigned_profile":"senior_consultant", "requested_model":"gpt-5.6-sol", "requested_reasoning_effort":"medium",
           "policy_class":"consultation", "model":"gpt-5.6-luna", "reasoning_effort":"high"}
    assert check([row]) == []
    assert check([{**row, "assigned_profile":"debugger"}])


def test_encrypted_assignment_is_unverified_without_claiming_the_wrong_model():
    fields = OBSERVER['safe_call_metadata']('spawn_agent', json.dumps({
        'message':'gAAAAA' + 'x'*100, 'model':'gpt-5.6-sol',
        'reasoning_effort':'medium', 'task_name':'senior_consultant'}))
    assert fields['assignment_content_unavailable'] is True
    rows = OBSERVER['call_policy_violations']([{'thread_id':'parent',
        'role':'coordinator','tool':'spawn_agent','outcome':'success',**fields}])
    assert [r['violation'] for r in rows] == ['worker_assignment_policy_unverified']
    assert OBSERVER['orchestration_policy_violations'](rows) == rows


def test_issued_draft_id_is_taken_only_from_a_successful_unique_receipt():
    receipt = {'type':'McpToolCall','server':'cortex','tool':'create_draft',
               'status':'completed','result':{'structuredContent':{'draft_id':'d_123456789abc'}}}
    extract = OBSERVER['mcp_receipt_metadata']
    assert extract(receipt)['draft_id'] == 'd_123456789abc'
    assert 'draft_id' not in extract({**receipt,'status':'failed'})
    assert 'draft_id' not in extract({**receipt,'result':[
        {'draft_id':'d_123456789abc'},{'draft_id':'d_abcdef123456'}]})


def test_opaque_default_consultant_route_requires_all_native_evidence():
    base = {'message':'gAAAAA'+'x'*100,'task_name':'senior_consultant',
            'model':'gpt-5.6-sol','reasoning_effort':'medium','fork_turns':'none'}
    def flags(args):
        fields = OBSERVER['safe_call_metadata']('spawn_agent',json.dumps(args))
        assert fields['assignment_content_unavailable'] is True
        return OBSERVER['call_policy_violations']([{'thread_id':'parent',
            'role':'coordinator','tool':'spawn_agent','outcome':'success',**fields}])
    assert flags(base) == []
    for change in ({'task_name':'debugger'}, {'fork_turns':'all'},
                   {'model':'gpt-6-astra'}, {'reasoning_effort':'high'}):
        assert 'worker_assignment_policy_unverified' in {
            row['violation'] for row in flags({**base,**change})}
    assert 'live_model_policy_violation' in {
        row['violation'] for row in flags({**base,'model':'gpt-5.6-terra'})}


def test_opaque_executor_route_requires_linked_child_and_complete_skill():
    request={'message':'gAAAAA'+'x'*100,'task_name':'author_spec',
             'model':'gpt-5.6-luna','reasoning_effort':'high','fork_turns':'none'}
    row={'thread_id':'parent','role':'coordinator','tool':'spawn_agent','outcome':'success',
         **OBSERVER['safe_call_metadata']('spawn_agent',json.dumps(request))}
    paths={'/root/author_spec':'child'};edges={'child':'parent'}
    details={'child':{'agent_role':'technical_writer','model':'gpt-5.6-luna','reasoning_effort':'high'}}
    read={'thread_id':'child','tool':'exec_command','outcome':'covered_by_command_execution',
          'wrapper_outcome':'success','worker_skill_complete':True}
    evidence=OBSERVER['native_spawn_route_metadata']
    check=OBSERVER['call_policy_violations']
    assert check([row])  # Ciphertext alone remains insufficient.
    observed=evidence(row,paths,edges,details,[read])
    assert observed['spawned_thread_id']=='child'
    assert observed['worker_route_evidence']=='native_child_and_complete_skill'
    assert check([{**row,**observed}])==[]
    assert evidence(row,paths,{'child':'unrelated'},details,[read])=={}
    assert evidence(row,{'/root/another_name':'child'},edges,details,[read])=={}
    assert evidence(row,paths,edges,details,[])=={}
    assert evidence(row,paths,edges,details,[{**read,'wrapper_outcome':'truncated'}])=={}
    for change in ({'observed_worker_model':'gpt-5.6-terra'},
                   {'observed_worker_effort':'medium'},
                   {'observed_worker_profile':'senior_consultant'},
                   {'fork_turns':'all'}, {'requested_model':'gpt-5.6-sol'}):
        assert 'worker_assignment_policy_unverified' in {
            flag['violation'] for flag in check([{**row,**observed,**change}])}


def test_opaque_executor_route_fallback_accepts_only_complete_actual_route():
    request={'message':'gAAAAA'+'x'*100,'task_name':'author_spec',
             'fork_turns':'none'}
    row={'thread_id':'parent','role':'coordinator','tool':'spawn_agent','outcome':'success',
         **OBSERVER['safe_call_metadata']('spawn_agent',json.dumps(request))}
    paths={'/root/author_spec':'child'};edges={'child':'parent'}
    details={'child':{'agent_role':'technical_writer','model':'gpt-5.6-luna','reasoning_effort':'medium'}}
    read={'thread_id':'child','tool':'exec_command','outcome':'covered_by_command_execution',
          'wrapper_outcome':'success','worker_skill_complete':True}
    evidence=OBSERVER['native_spawn_route_metadata']
    observed=evidence(row,paths,edges,details,[read])
    assert observed['route_policy_provenance']=='requested_route_unavailable_actual_route_verified'
    assert OBSERVER['call_policy_violations']([{**row,**observed}])==[]

    negatives=(
        ({**observed,'spawned_thread_id':None},),
        ({**observed,'native_child_path_verified':False},),
        ({**observed,'validated_parent_edge':False},),
        ({**observed,'worker_skill_receipt':'partial'},),
        ({**observed,'observed_worker_profile':'senior_consultant'},),
        ({**observed,'observed_worker_model':'gpt-5.6-terra'},),
        ({**observed,'observed_worker_effort':'low'},),
        ({**observed,'fork_turns':'all'},),
        ({**observed,'worker_route_evidence':'ambiguous'},),
    )
    for (change,) in negatives:
        assert 'worker_assignment_policy_unverified' in {
            item['violation'] for item in OBSERVER['call_policy_violations']([{**row,**change}])}

    assert 'worker_assignment_policy_unverified' in {
        item['violation'] for item in OBSERVER['call_policy_violations'](
            [{**row,**observed,'requested_model':'gpt-5.6-luna'}])}
    duplicate_reads=[read,dict(read)]
    assert evidence(row,paths,edges,details,duplicate_reads)=={}
    assert evidence(row,{'/root/author_spec':['child','other']},edges,details,[read])=={}
    assert evidence(row,paths,{'child':['parent','other']},details,[read])=={}


def test_opaque_route_ignores_validated_reference_reads_when_joining_skill_receipt():
    request={'message':'gAAAAA'+'x'*100,'task_name':'author_spec','fork_turns':'none'}
    row={'thread_id':'parent','role':'coordinator','tool':'spawn_agent','outcome':'success',
         **OBSERVER['safe_call_metadata']('spawn_agent',json.dumps(request))}
    paths={'/root/author_spec':'child'};edges={'child':'parent'}
    details={'child':{'agent_role':'technical_writer','model':'gpt-5.6-luna',
                      'reasoning_effort':'medium'}}
    reads=[
        {'thread_id':'child','tool':'exec_command','outcome':'covered_by_command_execution',
         'wrapper_outcome':'success','worker_skill_complete':True},
        {'thread_id':'child','tool':'exec_command','outcome':'covered_by_command_execution',
         'wrapper_outcome':'success','skill_instruction_read':True},
    ]
    observed=OBSERVER['native_spawn_route_metadata'](row,paths,edges,details,reads)
    assert observed['worker_skill_receipt']=='complete_success'
    assert OBSERVER['call_policy_violations']([{**row,**observed}])==[]


def test_native_route_coalesces_started_completed_lifecycle_only():
    recorder=OBSERVER['record_agent_activity']
    evidence=OBSERVER['native_spawn_route_metadata']
    request={'message':'gAAAAA'+'x'*100,'task_name':'author_spec','fork_turns':'none'}
    row={'thread_id':'parent','role':'coordinator','tool':'spawn_agent','outcome':'success',
         **OBSERVER['safe_call_metadata']('spawn_agent',json.dumps(request))}
    edges={'child':'parent'}
    details={'child':{'agent_role':'technical_writer','model':'gpt-5.6-luna','reasoning_effort':'medium'}}
    read={'thread_id':'child','tool':'exec_command','outcome':'covered_by_command_execution',
          'wrapper_outcome':'success','worker_skill_complete':True}

    def route(kinds,child_ids=None):
        paths={};phases={};duplicates=set()
        child_ids=child_ids or ['child']*len(kinds)
        for kind,child in zip(kinds,child_ids):
            recorder({'type':'SubAgentActivity','kind':kind,'agent_path':'/root/author_spec',
                      'agent_thread_id':child},paths,phases,duplicates)
        return paths,duplicates

    paths,duplicates=route(['started','completed'])
    assert paths=={'/root/author_spec':'child'}
    assert duplicates==set()
    assert evidence(row,paths,edges,details,[read])['spawned_thread_id']=='child'

    for kinds,child_ids in ((['started','started'],None),
                            (['completed','completed'],None),
                            (['started','completed'],['child','other'])):
        paths,duplicates=route(kinds,child_ids)
        assert duplicates=={'/root/author_spec'}
        assert evidence(row,paths,edges,details,[read],
                        {'agent_paths':duplicates,'edges':set()})=={}


def test_marker_false_or_nonboolean_missing_route_fields_fail_closed():
    base={'thread_id':'parent','role':'coordinator','tool':'spawn_agent','outcome':'success',
          'task_name':'author_spec','fork_turns':'none',
          'observed_worker_profile':'technical_writer',
          'observed_worker_model':'gpt-5.6-luna','observed_worker_effort':'high',
          'spawned_thread_id':'child','native_child_path_verified':True,
          'validated_parent_edge':True,'worker_skill_receipt':'complete_success',
          'worker_route_evidence':'native_child_and_complete_skill'}
    check=OBSERVER['call_policy_violations']
    for marker in (False,None,'true',1):
        rows=check([{**base,'assignment_content_unavailable':marker}])
        assert 'worker_assignment_policy_unverified' in {row['violation'] for row in rows}

    valid=check([{**base,'assignment_content_unavailable':False,
                  'requested_model':'gpt-5.6-luna','requested_reasoning_effort':'high'}])
    assert 'worker_assignment_policy_unverified' not in {row['violation'] for row in valid}
