import json
from pathlib import Path
import runpy
import sqlite3

import pytest

ROOT = Path(__file__).resolve().parents[1]
OBSERVER = runpy.run_path(str(ROOT / 'scripts/cortex-desktop-dev'))
EVAL = runpy.run_path(str(ROOT / 'scripts/cortex_eval.py'))


def row(tool, thread='worker', role='backend_dev', **fields):
    return dict(tool=tool, thread_id=thread, role=role, outcome='success') | fields


def published():
    return [row('mcp__cortex__write_report', parent_thread_id='parent', report_id='r_000000000001'),
            row('native_agent_result', parent_thread_id='parent', report_id='r_000000000001', timestamp='2')]


def flags(rows):
    return {r['violation'] for r in OBSERVER['call_policy_violations'](rows)}


def test_reassignment_requires_explicit_successful_parent_followup_after_final():
    followup = row('followup_task', 'parent', 'coordinator', target_thread_id='worker', timestamp='3')
    continuation = row('mcp__cortex__create_draft', template='implementation')
    assert not flags(published() + [followup, continuation])
    for change in ({'tool': 'send_message'}, {'outcome': 'error'}, {'thread_id': 'stranger'},
                   {'target_thread_id': 'other'}, {'requested_timestamp': '1'}):
        assert 'worker_tool_after_successful_write_report' in flags(published() + [{**followup, **change}, continuation])
    assert 'worker_tool_after_successful_write_report' in flags(published()[:1] + [followup, continuation])


def test_followup_consumed_once_and_new_report_required():
    rows = published() + [row('followup_task', 'parent', 'coordinator', target_thread_id='worker', timestamp='3'),
                          row('mcp__cortex__write_report', parent_thread_id='parent', report_id='r_000000000002'),
                          row('native_agent_result', parent_thread_id='parent', report_id='r_000000000001')]
    assert 'worker_final_with_unobserved_report' in flags(rows)
    assert 'worker_tool_after_successful_write_report' in flags(rows + [row('mcp__cortex__create_draft', template='implementation')])


def test_independent_worker_cannot_claim_another_workers_report():
    assert 'worker_final_with_unobserved_report' in flags(published() + [row('native_agent_result', 'other', report_id='r_000000000001')])


def test_handoff_allows_owned_predecessor_but_requires_latest_and_rejects_foreign_references():
    history=published()+[row('followup_task','parent','coordinator',target_thread_id='worker',timestamp='3'),
        row('mcp__cortex__write_report',parent_thread_id='parent',report_id='r_000000000002'),
        row('mcp__cortex__write_report','other',parent_thread_id='parent',report_id='r_000000000003')]
    final=row('native_agent_result',report_ids=['r_000000000001','r_000000000002'],timestamp='4')
    assert not flags(history+[final])
    for references in (['r_000000000001'],['r_000000000002','r_000000000003'],['r_000000000002','r_000000000099']):
        assert 'worker_final_with_unobserved_report' in flags(history+[{**final,'report_ids':references}])
    assert 'worker_final_without_report_id' in flags(history+[{**final,'report_ids':[]}])


def test_prior_verifier_context_is_not_independent_for_new_assignment():
    history=[{**r,'role':'build_verification'} for r in published()]
    history.append(row('followup_task','parent','coordinator',target_thread_id='worker',timestamp='3'))
    assert 'coordinator_reused_verification_worker' in flags(history)


def test_worker_message_allows_one_reply_to_that_worker_after_wait():
    wait=row('wait_agent','parent','coordinator')
    inbound=row('send_message','worker','backend_dev',parent_thread_id='parent',target_agent='/root')
    reply=row('send_message','parent','coordinator',target_thread_id='worker')
    violation='coordinator_unsolicited_message_after_wait'
    assert violation not in flags([wait,inbound,reply])
    assert violation in flags([wait,inbound,reply,reply])
    assert violation in flags([wait,inbound,{**reply,'target_thread_id':'other'}])
    assert violation in flags([wait,{**inbound,'outcome':'error'},reply])
    assert violation in flags([wait,{**inbound,'target_agent':'/root/other'},reply])


def test_brief_start_allowed_continuation_and_oversized_reads_rejected():
    read = row('mcp__cortex__read_report', 'parent', 'coordinator', document_kind='report', page='start', requested_limit=4000)
    assert not flags([read])
    assert 'coordinator_report_continuation_read' in flags([{**read, 'page': 'continuation'}])
    assert 'oversized_report_page' in flags([{**read, 'requested_limit': 4001}])
    assert 'coordinator_forbidden_tool' in OBSERVER['call_policy_flags']('exec_command', '{"cmd":"cat source.py"}', 'coordinator', '/tmp/project')


def test_routing_does_not_force_model_from_profile():
    assert not flags([row('spawn_agent', 'parent', 'coordinator', assigned_profile='architect', model='gpt-5.6-luna')])


def test_followup_metadata_never_contains_message():
    result = OBSERVER['safe_call_metadata']('followup_task', json.dumps({'target': '/root/worker', 'message': 'PRIVATE'}))
    assert result == {'target_agent': '/root/worker'}


def test_standard_skill_loading_does_not_allow_plugin_exploration(tmp_path,monkeypatch):
    check=OBSERVER['call_policy_flags']
    monkeypatch.setattr(Path,'home',lambda:tmp_path)
    cache=tmp_path/'.cortex-dev/.codex/plugins/cache/cortex/cortex/build'
    for suffix in ('skills/worker-general/SKILL.md','skills/context-compaction/SKILL.md',
                   'skills/cortex-control/references/index.md'):
        path=cache/suffix;path.parent.mkdir(parents=True,exist_ok=True);path.write_text('instructions')
        assert not check('exec_command',json.dumps({'cmd':'cat '+str(path)}),'coordinator',str(tmp_path))
        assert 'forbidden_plugin_or_cache_access' in check('exec_command',json.dumps({'cmd':'cat '+str(path)+'; ls '+str(cache)}),'general',str(tmp_path))
    for suffix in ('skills/worker-general/SKILL.md','agents/general.toml','profiles.json','scripts/cortex_server.py'):
        path='/home/test/.codex/plugins/cache/cortex/cortex/build/'+suffix
        assert 'forbidden_plugin_or_cache_access' in check('exec_command',json.dumps({'cmd':'cat '+path}),'general',str(tmp_path))
    assert 'forbidden_plugin_or_cache_access' in check('exec_command','{"cmd":"cat SKILL.md"}','general','/home/test/.codex/plugins/cache/cortex/cortex/build/skills/worker-general')
    assert 'forbidden_plugin_or_cache_access' not in check('exec_command','{"cmd":"cat solution.py"}','general',str(tmp_path))


def test_report_patch_mentions_are_not_plugin_access_but_patch_targets_are(tmp_path):
    check=OBSERVER['call_policy_flags']
    plugin='/home/test/.codex/plugins/cache/cortex/cortex/build/skills/worker-general/SKILL.md'
    patch='*** Begin Patch\n*** Update File: '+str(tmp_path/'report.md')+'\n@@\n-old\n+Loaded '+plugin+'\n*** End Patch'
    for arguments in (patch,'text(await tools.apply_patch('+json.dumps(patch)+'));'):
        assert 'forbidden_plugin_or_cache_access' not in check('apply_patch',arguments,'general',str(tmp_path))
    for header in ('Add File','Update File','Delete File','Move to'):
        bad='*** Begin Patch\n*** '+header+': '+plugin+'\n*** End Patch'
        assert 'forbidden_plugin_or_cache_access' in check('apply_patch',bad,'general',str(tmp_path))


def test_concurrent_report_events_match_retained_reference_not_wrapper_order():
    metadata=OBSERVER['report_read_metadata'];match=OBSERVER['event_call_candidate']
    a='r_000000000001';b='r_000000000002'
    assert metadata('mcp__cortex__read_report',json.dumps({'report_id':a}))=={'requested_report_id':a}
    assert metadata('mcp__cortex__read_report','{report_id: ref}',f'const ref = "{a}";')=={'requested_report_id':a}
    assert metadata('mcp__cortex__read_report','{report_id: ref}',f'let ref = "{a}";')=={}
    assert metadata('mcp__cortex__read_report','{report_id: ref}',f'const ref = "{a}"; const ref = "{b}";')=={}
    rows=[dict(timestamp='2026-09-05T00:00:00Z',completed_timestamp='2026-09-05T00:00:01Z',requested_report_id=r) for r in (a,b)]
    time_ns=1788566400500000000
    assert match(rows,time_ns,b) is rows[1]
    assert match(rows,time_ns,a) is rows[0]
    assert match(rows,time_ns,'r_000000000003') is None
    assert match(rows,time_ns) is None


def test_successful_mcp_receipt_does_not_hide_or_inherit_consumer_failure():
    wrapper=row('functions.exec',outcome='error')
    call=row('mcp__codebase_memory__list_projects',outcome='error',
             host_receipt_observed=True,host_receipt_outcome='success')
    assert 'mcp_tool_error_observed' not in flags([wrapper,call])
    unresolved,_=OBSERVER['classify_host_failures']([wrapper,call])
    assert len(unresolved)==2  # The consumer's failed processing remains visible.
    assert 'mcp_tool_error_observed' in flags([{**call,'host_receipt_outcome':'error'}])


def test_backend_execution_does_not_hide_a_missing_model_visible_receipt():
    call=row('exec_command','worker','general',outcome='covered_by_command_execution',
             skill_instruction_read=True,wrapper_outcome='unverified')
    violations=OBSERVER['call_policy_violations']([call])
    assert 'command_wrapper_missing_receipt' in {r['violation'] for r in violations}
    assert OBSERVER['orchestration_policy_violations'](violations)
    assert 'command_wrapper_missing_receipt' not in flags([{**call,'wrapper_outcome':'success'}])


def test_server_event_matches_executed_call_not_an_earlier_skipped_guard():
    from datetime import datetime
    skipped={'timestamp':'2026-09-06T00:00:00Z','completed_timestamp':'2026-09-06T00:00:01Z'}
    executed={'timestamp':'2026-09-06T00:00:07Z','completed_timestamp':'2026-09-06T00:00:08Z'}
    event=int(datetime.fromisoformat('2026-09-06T00:00:07.5+00:00').timestamp()*1_000_000_000)
    choose=OBSERVER['event_call_candidate']
    assert choose([skipped,executed],event) is executed
    assert choose([skipped],event) is None
    assert choose([executed,dict(executed)],event) is None


def test_capacity_failure_is_an_error_and_interrupt_is_not_release():
    assert OBSERVER['observed_outcome']('collab spawn failed: agent thread limit reached','spawn_agent')[:2]==('error','agent_limit_reached')
    failed=row('spawn_agent','parent','coordinator',outcome='error',error_code='agent_limit_reached')
    snapshot=row('list_agents','parent','coordinator')
    stopped=row('interrupt_agent','parent','coordinator',outcome='success')
    assert not flags([failed,snapshot])
    assert 'coordinator_repeated_capacity_snapshot' in flags([failed,snapshot,snapshot])
    assert 'coordinator_spawn_retry_without_capacity_change' in flags([failed,stopped,failed])
    complete=row('native_agent_result',parent_thread_id='parent',report_id='r_000000000001')
    assert 'coordinator_spawn_retry_without_capacity_change' not in flags([failed,complete,failed])
    closed=row('close_agent','parent','coordinator')
    assert 'coordinator_spawn_retry_without_capacity_change' not in flags([failed,closed,failed])


def test_completed_worker_gets_followup_not_queue_only_message():
    message=row('send_message','parent','coordinator',target_thread_id='worker')
    assert 'coordinator_message_to_completed_worker' in flags(published()+[message])
    assert 'coordinator_message_to_completed_worker' not in flags([message])


def test_desktop_steering_receipts_are_typed_and_task_scoped(tmp_path,monkeypatch):
    monkeypatch.setenv('HOME',str(tmp_path))
    home=tmp_path/'.cortex-dev/.codex';home.mkdir(parents=True)
    rollout=home/'rollout.jsonl'
    db=sqlite3.connect(home/'state_5.sqlite')
    db.execute('CREATE TABLE threads(id TEXT,cwd TEXT,rollout_path TEXT)')
    db.execute('INSERT INTO threads VALUES (?,?,?)',('root','/fixture',str(rollout)));db.commit();db.close()
    def receipt(thread,text,kind='UserMessage'):
        return dict(type='event_msg',payload=dict(type='item_completed',thread_id=thread,
            item=dict(type=kind,id='message-'+thread,content=[dict(type='text',text=text)])))
    rollout.write_text('\n'.join(json.dumps(r) for r in [receipt('root','exact'),receipt('other','exact'),
        receipt('root','near'),receipt('root','exact','AgentMessage')]))
    assert OBSERVER['desktop_prompt_receipts']({'thread_id':'root','workdir':'/fixture'},'exact')=={'message-root'}
    rollout.write_text(json.dumps(receipt('root','exact\n')))
    assert OBSERVER['desktop_prompt_receipts']({'thread_id':'root','workdir':'/fixture'},'exact')=={'message-root'}
    for altered in ('еxact\n','exact\n\n',' exact\n'):
        rollout.write_text(json.dumps(receipt('root',altered)))
        assert OBSERVER['desktop_prompt_receipts']({'thread_id':'root','workdir':'/fixture'},'exact')==set()
    with pytest.raises(RuntimeError): OBSERVER['desktop_prompt_receipts']({'thread_id':'root','workdir':'/other'},'exact')


def test_resume_restores_only_completed_assignment_receipts():
    reduce=OBSERVER['completed_assignment_snapshots']
    history=[row('prior_publication',timestamp='1',parent_thread_id='parent',report_id='r_000000000001'),
             row('prior_final',timestamp='2',report_id='r_000000000001')]
    snapshot=reduce(history)
    assert len(snapshot)==1 and snapshot[0]['tool']=='native_assignment_snapshot'
    followup=row('followup_task','parent','coordinator',target_thread_id='worker',timestamp='3')
    assert not flags(snapshot+[followup,row('mcp__cortex__create_draft',template='implementation')])
    assert reduce(history[:1])==[]
    assert reduce(history+[row('prior_worker_activity',timestamp='3')])==[]
    assert reduce([history[0],{**history[1],'report_id':'r_000000000002'}])==[]
    second=history+[row('prior_worker_activity',timestamp='3'),
        row('prior_publication',timestamp='4',parent_thread_id='parent',report_id='r_000000000002'),
        row('prior_final',timestamp='5',report_ids=['r_000000000001','r_000000000002'])]
    retained=reduce(second)
    assert retained[0]['owned_report_ids']==['r_000000000001','r_000000000002']
    assert not flags(retained+[row('followup_task','parent','coordinator',target_thread_id='worker',timestamp='6'),
        row('mcp__cortex__write_report',parent_thread_id='parent',report_id='r_000000000003'),
        row('native_agent_result',report_ids=['r_000000000001','r_000000000003'])])
    assert reduce(second+[row('prior_final',timestamp='6',report_ids=['r_000000000002','r_000000000099'])])==[]


def test_eval_fixture_is_separate_and_refuses_overwrite(tmp_path):
    trial = tmp_path / 'trial'
    EVAL['prepare']('retry-dedup', trial, 'baseline', 1)
    assert not (trial / 'project/prompt.txt').exists()
    assert not EVAL['grade'](trial)['functional_success']
    with pytest.raises(FileExistsError): EVAL['prepare']('retry-dedup', trial, 'baseline', 1)
    (trial / 'project/solution.py').write_text('def solve(events):\n    seen = {}\n    for key, value in events:\n        if key in seen and seen[key] != value: raise ValueError()\n        seen[key] = value\n    return sum(seen.values())\n')
    assert EVAL['grade'](trial)['functional_success']
    (trial / 'project/USER-NOTE.txt').write_text('changed')
    assert not EVAL['grade'](trial)['functional_success']


def test_eval_missing_baseline_never_claims_gain():
    assert all(r['status'] == 'unverified' for r in EVAL['compare']([]).values())
    assert len(EVAL['CASES']) == 12
    assert sum(c['split'] == 'holdout' for c in EVAL['CASES']) == 6


def test_eval_rejects_duplicate_trials():
    r = dict(configuration='baseline', case='retry-dedup', attempt=1)
    with pytest.raises(ValueError, match='duplicate'): EVAL['compare']([r, r])


def test_eval_complete_screen_keeps_measurement_uncertainty():
    records=[]
    for configuration in ('baseline','combined'):
        for c in EVAL['CASES']:
            for attempt in (1,2,3):
                records.append(dict(configuration=configuration,case=c['name'],attempt=attempt,
                    tokens=100,seconds=10,protocol_pass=True,claimed_complete=True,repeated_reads=0,
                    payload_sha256=EVAL['BASELINE'] if configuration=='baseline' else 'a'*64,
                    fixture_sha256=c['name'],host='cli',model_settings_sha256='b'*64,
                    functional_success=configuration!='baseline' or attempt!=1 or c['family']=='simple'))
    assert EVAL['compare'](records)['combined']['status']=='needs_replication'
    records[-1]['tokens']=None
    assert EVAL['compare'](records)['combined']['status']=='unverified'
    records[-1]['tokens']=0
    with pytest.raises(ValueError,match='measurement'): EVAL['compare'](records)


def test_missing_steering_receipt_cannot_count_as_success(tmp_path):
    trial=tmp_path/'trial'
    EVAL['prepare']('threshold-change',trial,'combined',1)
    (trial/'project/solution.py').write_text('def solve(values, threshold):\n    return [v for v in values if v >= threshold]\n')
    observations=dict(tokens=None,seconds=None,protocol_pass=True,claimed_complete=True,repeated_reads=None,
        payload_sha256='a'*64,host='cli',model_settings_sha256='b'*64,
        steering_observed=False,resume_observed=False)
    result=EVAL['record_trial'](trial,observations)
    assert result['functional_success'] is None and result['status']=='incomplete'
    with pytest.raises(FileExistsError): EVAL['record_trial'](trial,observations)


def test_profiles_share_concrete_graph_first_route():
    for path in (ROOT / 'plugins/cortex/skills').glob('worker-*/SKILL.md'):
        text = path.read_text()
        assert '## Codebase Memory: graph first, source grounded' in text
        for operation in ('list_projects', 'search_graph', 'trace_path', 'get_code_snippet', 'check_index_coverage'):
            assert operation in text
        assert 'not a similar name or another worktree' in text
        assert 'a first page is not an exhaustive result' in text
        assert 'Do not repeatedly rebuild a watched index' in text


def test_generated_skill_loading_boundary_matches_actual_file_end():
    import re
    for path in (ROOT/'plugins/cortex/skills').glob('worker-*/SKILL.md'):
        lines=path.read_text().splitlines()
        match=re.search(r'This complete skill has (\d+) lines\.',lines[5])
        assert match and int(match.group(1))==len(lines)
        assert lines[-1]=='<!-- END OF COMPLETE CORTEX WORKER SKILL -->'
        assert lines.count(lines[-1])==1
