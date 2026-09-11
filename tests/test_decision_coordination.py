import hashlib
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


def test_prior_verifier_context_can_extend_its_own_findings():
    history=[{**r,'role':'build_verification'} for r in published()]
    history.append(row('followup_task','parent','coordinator',target_thread_id='worker',timestamp='3'))
    assert 'coordinator_reused_verification_worker' not in flags(history)


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


@pytest.mark.parametrize('wait_outcome', ['pending', 'timeout'])
def test_pending_or_timeout_wait_requires_repeat_and_rejects_unsolicited_message(wait_outcome):
    wait = row('wait_agent', 'parent', 'coordinator', outcome=wait_outcome)
    status = row('list_agents', 'parent', 'coordinator')
    repeated = row('wait_agent', 'parent', 'coordinator', outcome='success')
    message = row('send_message', 'parent', 'coordinator', target_thread_id='worker')
    violation = 'coordinator_unsolicited_message_after_wait'
    assert flags([wait, status, repeated]) == set()
    assert violation in flags([wait, status, message])
    assert violation in flags([wait, status, repeated, message])


def test_reconciled_terminal_result_allows_intentional_followup():
    wait = row('wait_agent', 'parent', 'coordinator')
    history = [wait] + published()
    followup = row('followup_task', 'parent', 'coordinator', target_thread_id='worker', timestamp='3')
    assert 'coordinator_unsolicited_message_after_wait' not in flags(history + [followup])


def test_coordinator_can_read_needed_evidence_pages_and_user_sources():
    read = row('mcp__cortex__read_report', 'parent', 'coordinator', document_kind='report', page='start', requested_limit=4000)
    assert not flags([read])
    assert 'coordinator_report_continuation_read' not in flags([{**read, 'page': 'continuation'}])
    assert 'oversized_report_page' in flags([{**read, 'requested_limit': 4001}])
    assert 'coordinator_forbidden_tool' not in OBSERVER['call_policy_flags']('exec_command', '{"cmd":"sed -n 1,80p user-source.txt"}', 'coordinator', '/tmp/project')
    assert 'coordinator_forbidden_tool' in OBSERVER['call_policy_flags']('exec_command', '{"cmd":"python3 change.py"}', 'coordinator', '/tmp/project')


def test_routing_does_not_force_model_from_profile():
    assert not flags([row('spawn_agent', 'parent', 'coordinator', assigned_profile='architect',
                          model='gpt-5.6-luna', reasoning_effort='high',
                          requested_model='gpt-5.6-luna', requested_reasoning_effort='medium',
                          fork_turns='none')])


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
        assert 'forbidden_plugin_or_cache_access' in check(
            'exec_command',json.dumps({'cmd':'cat '+str(path)}),'coordinator',str(tmp_path))
        assert 'forbidden_plugin_or_cache_access' in check('exec_command',json.dumps({'cmd':'cat '+str(path)+'; ls '+str(cache)}),'general',str(tmp_path))
    for suffix in ('skills/worker-general/SKILL.md','agents/general.toml','profiles.json','scripts/cortex_server.py'):
        path='/home/test/.codex/plugins/cache/cortex/cortex/build/'+suffix
        assert 'forbidden_plugin_or_cache_access' in check('exec_command',json.dumps({'cmd':'cat '+path}),'general',str(tmp_path))
    assert 'forbidden_plugin_or_cache_access' in check('exec_command','{"cmd":"cat SKILL.md"}','general','/home/test/.codex/plugins/cache/cortex/cortex/build/skills/worker-general')
    assert 'forbidden_plugin_or_cache_access' not in check('exec_command','{"cmd":"cat solution.py"}','general',str(tmp_path))


def test_python_pathlib_skill_read_is_static_and_role_safe(tmp_path,monkeypatch):
    check=OBSERVER['call_policy_flags']
    monkeypatch.setattr(Path,'home',lambda:tmp_path)
    cache=tmp_path/'.cortex-dev/.codex/plugins/cache/cortex/cortex/v7'
    skill=cache/'skills/orchestrator/SKILL.md'
    worker_skill=cache/'skills/worker-general/SKILL.md'
    for path in (skill,worker_skill):
        path.parent.mkdir(parents=True,exist_ok=True);path.write_text('instructions')

    def command(path, binding='p', tag='PY'):
        return (f"python3 - <<'{tag}'\nfrom pathlib import Path\n"
                f"{binding}=Path({str(path)!r})\nprint({binding}.read_text())\n{tag}")

    arguments=json.dumps({'cmd':command(skill)})
    assert OBSERVER['skill_instruction_read']('exec_command',arguments)
    assert 'forbidden_plugin_or_cache_access' not in check('exec_command',arguments,'coordinator','/fixture')
    assert 'coordinator_forbidden_tool' not in check('exec_command',arguments,'coordinator','/fixture')
    commented=command(skill).replace('from pathlib import Path\n', '# $(touch /tmp/example)\nfrom pathlib import Path\n')
    assert OBSERVER['skill_instruction_read']('exec_command',json.dumps({'cmd':commented}))
    unquoted=commented.replace("<<'PY'", '<<PY')
    unquoted_args=json.dumps({'cmd':unquoted})
    assert not OBSERVER['skill_instruction_read']('exec_command',unquoted_args)
    assert 'forbidden_plugin_or_cache_access' in check('exec_command',unquoted_args,'general','/fixture')
    assert OBSERVER['skill_instruction_read']('exec_command',json.dumps({'cmd':command(skill).replace('python3 - ', 'python3 -B - ')}))
    worker_arguments=json.dumps({'cmd':command(worker_skill,'skill_path','END')})
    assert OBSERVER['worker_skill_read']('exec_command',worker_arguments)
    assert 'forbidden_plugin_or_cache_access' in check(
        'exec_command',worker_arguments,'coordinator','/fixture')
    reference=skill.parent/'references/pipeline-publication.md'
    reference.parent.mkdir();reference.write_text('reference')
    reference_arguments=json.dumps({'cmd':command(reference,'reference_path','REF')})
    assert OBSERVER['skill_instruction_read']('exec_command',reference_arguments)
    assert 'forbidden_plugin_or_cache_access' in check(
        'exec_command',reference_arguments,'coordinator','/fixture')
    assert 'forbidden_plugin_or_cache_access' not in check(
        'exec_command',worker_arguments,'general','/fixture')

    negatives=(
        command(skill).replace('from pathlib import Path', 'import pathlib').replace('Path(', 'pathlib.Path('),
        command(skill).replace("Path(", "Path(__import__('os').environ['SKILL'])"),
        command(skill).replace("print(p.read_text())", "p.write_text('changed')"),
        command(tmp_path/'project.txt'),
        command(cache/'skills/worker-general/../agents/general.toml'),
        command(skill).replace("print(p.read_text())", "print(p.read_text()); __import__('os').system('true')"),
    )
    for value in negatives:
        args=json.dumps({'cmd':value})
        assert not OBSERVER['skill_instruction_read']('exec_command',args)
    assert 'coordinator_forbidden_tool' in check(
        'exec_command',json.dumps({'cmd':command(tmp_path/'project.txt')}),'coordinator','/fixture')
    assert 'forbidden_plugin_or_cache_access' in check(
        'exec_command',json.dumps({'cmd':negatives[1]}),'general','/fixture')


def test_python_inline_pathlib_skill_read_is_static_and_role_safe(tmp_path,monkeypatch):
    check=OBSERVER['call_policy_flags']
    monkeypatch.setattr(Path,'home',lambda:tmp_path)
    skill=(tmp_path/'.cortex-dev/.codex/plugins/cache/cortex/cortex'
           /'1.15.6+codex.sha256.fixture/skills/orchestrator/SKILL.md')
    skill.parent.mkdir(parents=True);skill.write_text('instructions')

    def command(path,flag=''):
        program=f'from pathlib import Path; print(Path("{path}").read_text())'
        return f"python3{flag} -c '{program}'"

    for value in (command(skill),command(skill,' -B')):
        arguments=json.dumps({'cmd':value})
        assert OBSERVER['python_skill_read_paths'](value)==[skill]
        assert OBSERVER['skill_instruction_read']('exec_command',arguments)
        flags=check('exec_command',arguments,'coordinator','/fixture')
        assert 'forbidden_plugin_or_cache_access' not in flags
        assert 'coordinator_forbidden_tool' not in flags

    valid=command(skill)
    raw_wrapper=('const result = await tools.exec_command({cmd:'+json.dumps(valid)
                 +',workdir:"/fixture",yield_time_ms:10000}); text(result);')
    assert OBSERVER['shell_command_text'](raw_wrapper)==valid
    assert OBSERVER['skill_instruction_read']('exec_command',raw_wrapper)
    assert check('exec_command',raw_wrapper,'coordinator','/fixture')==[]
    negatives=(
        valid.replace("-c '", '-c "').removesuffix("'")+'"',
        valid.replace(".read_text())'", ".read_text()); Path(\"/tmp/changed\").touch()'"),
        valid.replace('Path("'+str(skill)+'")', 'Path(__import__("os").environ["SKILL"])'),
        valid.replace('.read_text()', '.write_text("changed")'),
        valid+'; touch /tmp/changed',
        valid.replace('print(Path(', 'print(open("/tmp/other").read() + Path('),
        valid.replace('print(Path(', 'p=Path(').replace(').read_text())', '); print(p.read_text())'),
    )
    for value in negatives:
        arguments=json.dumps({'cmd':value})
        assert OBSERVER['python_skill_read_paths'](value)==[]
        assert not OBSERVER['skill_instruction_read']('exec_command',arguments)
        assert 'coordinator_forbidden_tool' in check(
            'exec_command',arguments,'coordinator','/fixture')


def test_native_archived_request_digest_preserves_verified_editor_bytes():
    digest=OBSERVER['original_request_digest']
    allowed=OBSERVER['delivered_request_digests']
    source='Total events: 2\n1. Keep total_events exactly.\n'
    delivered='$cortex:orchestrator '+source.replace('_',r'\_')
    state=dict(original_request_sha256=digest(source),
               desktop_editor_source_sha256=digest(OBSERVER['desktop_editor_source'](source)))
    raw_hash=hashlib.sha256(source.replace('_',r'\_').encode()).hexdigest()
    accepted=allowed(state,delivered)
    assert raw_hash in accepted
    assert OBSERVER['native_archived_request_digest'](delivered)==raw_hash
    changed=delivered.replace('exactly','differently')
    assert allowed(state,changed)=={digest(source)}
    assert hashlib.sha256(b'arbitrary').hexdigest() not in accepted
    assert raw_hash not in allowed(dict(original_request_sha256=digest(source)),delivered)


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
    failed_external={**call,'host_receipt_outcome':'error'}
    assert 'mcp_tool_error_observed' not in flags([failed_external])
    assert OBSERVER['orchestration_policy_violations'](
        OBSERVER['call_policy_violations']([failed_external]))==[]


def test_genuine_cortex_mcp_error_remains_acceptance_critical():
    failed=row('mcp__cortex__create_draft',role='planner',outcome='error',
               host_receipt_observed=True,host_receipt_outcome='error',
               server_observed=True)
    violations=OBSERVER['call_policy_violations']([failed])
    assert 'mcp_tool_error_observed' in flags([failed])
    assert OBSERVER['orchestration_policy_violations'](violations)


def test_namespaced_cortex_receipt_findings_survive_full_audit_promotion():
    missing_receipt=row('create_draft',role='planner',outcome='error',
                        tool_namespace='mcp__cortex',argument_digest='missing',
                        server_observed=True)
    external_failure=row('list_projects',role='explorer',outcome='error',
                         tool_namespace='mcp__codebase_memory',argument_digest='external',
                         host_receipt_observed=True,host_receipt_outcome='error')
    violations=OBSERVER['call_policy_violations']([missing_receipt,external_failure])
    namespaced=[row for row in violations if row['thread_id']=='worker']
    assert namespaced
    assert all(item['tool']=='mcp__cortex__create_draft' for item in namespaced)
    assert all(item['tool_namespace']=='mcp__cortex' for item in namespaced)
    assert {item['violation'] for item in namespaced} >= {
        'mcp_tool_error_observed','mcp_call_missing_host_receipt'}
    promoted=OBSERVER['orchestration_policy_violations'](violations)
    assert promoted
    assert not any(item['thread_id']=='worker' and
                   item['tool_namespace']=='mcp__codebase_memory'
                   for item in promoted)


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
    failed=row('spawn_agent','parent','coordinator',outcome='error',error_code='agent_limit_reached',
               model='gpt-5.6-luna', reasoning_effort='high',
               requested_model='gpt-5.6-luna', requested_reasoning_effort='medium',
               fork_turns='none')
    snapshot=row('list_agents','parent','coordinator')
    stopped=row('interrupt_agent','parent','coordinator',outcome='success')
    assert not flags([failed,snapshot])
    assert 'coordinator_repeated_capacity_snapshot' in flags([failed,snapshot,snapshot])
    assert 'coordinator_spawn_retry_without_capacity_change' in flags([failed,stopped,failed])
    complete=row('native_agent_result',parent_thread_id='parent',report_id='r_000000000001')
    assert 'coordinator_spawn_retry_without_capacity_change' not in flags([failed,complete,failed])
    closed=row('close_agent','parent','coordinator')
    assert 'coordinator_spawn_retry_without_capacity_change' not in flags([failed,closed,failed])


def test_internal_status_observation_after_wait_is_not_thread_messaging():
    wait=row('wait_agent','parent','coordinator')
    status=row('list_agents','parent','coordinator')
    assert flags([wait,status])==set()
    worker_message=row('send_message_to_thread','worker','general',parent_thread_id='parent')
    assert 'forbidden_worker_app_thread_message' in flags([wait,status,worker_message])


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


def test_profiles_route_graph_details_to_generated_reference():
    canonical=(ROOT/'plugins/cortex/agent-sources/references/code-and-evidence.md').read_bytes()
    for path in (ROOT / 'plugins/cortex/skills').glob('worker-*/SKILL.md'):
        text = path.read_text()
        if path.parent.name == 'worker-senior-consultant':
            assert 'Do not inspect source files' in text
            assert '[code and evidence discovery]' not in text
            continue
        assert '[code and evidence discovery](references/code-and-evidence.md)' in text
        assert (path.parent/'references/code-and-evidence.md').read_bytes()==canonical
        assert 'Match a code index to the canonical assignment workspace' in canonical.decode()
        assert 'Confirm consequential graph findings against current source' in canonical.decode()
        assert '4,000 characters' in canonical.decode()
        assert '## Codebase Memory: graph first, source grounded' not in text


def test_generated_skill_loading_boundary_matches_actual_file_end():
    sources=ROOT/'plugins/cortex/agent-sources/references'
    for path in (ROOT/'plugins/cortex/skills').glob('worker-*/SKILL.md'):
        text=path.read_text()
        lines=text.splitlines()
        assert lines[-1]=='<!-- END OF COMPLETE CORTEX WORKER SKILL -->'
        assert lines.count(lines[-1])==1
        assert not any(line.startswith('This complete skill has ') for line in lines)
        assert {p.name for p in (path.parent/'references').glob('*.md')}=={
            'code-and-evidence.md',
            'interactive-resources.md',
            'report-publication.md',
        }
        for reference in (path.parent/'references').glob('*.md'):
            assert reference.read_bytes()==(sources/reference.name).read_bytes()
        assert '## Report class selection' in text
        if path.parent.name == 'worker-senior-consultant':
            assert 'Use the ordinary `general` report class.' in text
        else:
            assert 'Changing report class does not require a new worker.' in text
        assert 'fresh correctly matched worker' not in text
    publication=(sources/'report-publication.md').read_text()
    interaction=(sources/'interactive-resources.md').read_text()
    assert 'An inert JavaScript string or `String.raw` template' in publication
    assert 'executable interpolation, command\nsubstitution or shell evaluation' in publication
    assert 'Call grouping and tab count follow the tool\'s guarantees' in interaction
    assert 'Create one fresh tab' not in interaction
    assert 'Perform state changes separately' not in interaction


def test_observer_keeps_ordinary_worker_path_metadata_outside_orchestration_gate():
    helper=OBSERVER
    ordinary=helper['path_policy_metadata'](
        'exec_command',json.dumps({'cmd':'ls -la /tmp/project'}),'general','/tmp/project')
    assert ordinary['path_target_class']=='worker_workspace_or_external'
    assert ordinary['path_access_kind']=='ordinary_tooling'
    assert ordinary['path_policy_decision']=='diagnostic_only'
    row=dict(thread_id='worker',role='general',tool='exec_command',outcome='success',**ordinary,
             policy_flags=['forbidden_plugin_or_cache_access'])
    violations=helper['call_policy_violations']([row])
    assert helper['worker_policy_violations'](violations)
    assert helper['orchestration_policy_violations'](violations)==[]


def test_observer_classifies_genuine_unauthorized_cache_access_as_acceptance_critical(tmp_path):
    helper=OBSERVER
    cache='/home/test/.codex/plugins/cache/cortex/cortex/build/agents/general.toml'
    arguments=json.dumps({'cmd':'cat '+cache})
    metadata=helper['path_policy_metadata']('exec_command',arguments,'general',str(tmp_path))
    assert metadata['path_target_class']=='plugin_or_cache'
    assert metadata['path_access_kind']=='unauthorized_read_or_probe'
    assert metadata['path_policy_decision']=='unauthorized_access'
    row=dict(thread_id='worker',role='general',tool='exec_command',outcome='success',**metadata,
             policy_flags=['forbidden_plugin_or_cache_access'])
    violations=helper['call_policy_violations']([row])
    assert helper['orchestration_policy_violations'](violations)


def test_python_audit_comparison_marker_is_static_but_access_remains_forbidden(tmp_path):
    helper=OBSERVER
    candidate='/home/test/.cortex-dev/candidates/1.15.9+codex.sha256.0123456789abcdef'

    def metadata(command,workdir=tmp_path):
        return helper['path_policy_metadata'](
            'exec_command',json.dumps({'cmd':command}),'build_verification',str(workdir))

    comparison=("PYTHONDONTWRITEBYTECODE=1 python3 - <<'PY'\n"
                "import json\n"
                "from pathlib import Path\n"
                "audit=json.loads(Path('/tmp/preserved-audit.json').read_text())\n"
                f"assert audit['candidate']=={candidate!r}\n"
                "PY")
    allowed=metadata(comparison)
    assert allowed['path_target_class']=='approved_instruction_or_static_mention'
    assert allowed['path_access_kind']=='static_mention'
    assert allowed['path_policy_decision']=='allowed'
    assert helper['call_policy_flags'](
        'exec_command',json.dumps({'cmd':comparison}),'build_verification',str(tmp_path))==[]

    inline=f"python3 -c 'assert observed == \"{candidate}\"'"
    assert metadata(inline)['path_policy_decision']=='allowed'

    forbidden=(
        f"python3 - <<'PY'\nfrom pathlib import Path\n"
        f"print(Path({candidate!r}).read_text())\nPY",
        f"python3 - <<'PY'\npath={candidate!r}\nprint(open(path).read())\nPY",
        f"python3 - <<'PY'\nimport subprocess\nsubprocess.run(['cat',{candidate!r}])\nPY",
        f"python3 - <<'PY'\n# compare {candidate}\nprint('safe')\nPY",
    )
    for command in forbidden:
        denied=metadata(command)
        assert denied['path_target_class']=='plugin_or_cache'
        assert denied['path_access_kind']=='unauthorized_read_or_probe'
        assert denied['path_policy_decision']=='unauthorized_access'

    # AST coordinates are UTF-8 byte offsets. A non-ASCII direct comparison
    # must not mask a subsequent private marker in a comment.
    unicode_comment=(f"python3 -c 'assert \"{'é' * 120}{candidate}\" == \"x\" "
                     f"# {candidate}'")
    denied=metadata(unicode_comment)
    assert denied['path_target_class']=='plugin_or_cache'
    assert denied['path_access_kind']=='unauthorized_read_or_probe'
    assert denied['path_policy_decision']=='unauthorized_access'

    private_workdir=tmp_path/'.cortex-dev/candidates/private'
    assert metadata(inline,private_workdir)['path_policy_decision']=='unauthorized_access'


def test_worker_private_cortex_cache_is_blocked_but_bounded_report_read_is_allowed(tmp_path):
    helper=OBSERVER
    private_path=str(tmp_path/'.codex/cortex/t_1/pipeline.md')
    shell=json.dumps({'cmd':'sed -n 1,40p '+private_path})
    metadata=helper['path_policy_metadata']('exec_command',shell,'general',str(tmp_path))
    assert metadata['path_target_class']=='cortex_internal'
    assert metadata['path_access_kind']=='unauthorized_read_or_probe'
    assert metadata['path_policy_decision']=='unauthorized_access'
    assert 'path_command_intent_digest' in metadata
    assert private_path not in json.dumps(metadata)
    blocked=dict(thread_id='worker',role='general',tool='exec_command',outcome='success',
                 argument_digest='arg',result_digest='result',exit_code=0,truncated=False,
                 policy_flags=['forbidden_plugin_or_cache_access'],**metadata)
    violations=helper['call_policy_violations']([blocked])
    assert helper['orchestration_policy_violations'](violations)
    assert violations[0]['path_target_class']=='cortex_internal'
    assert violations[0]['argument_digest']=='arg'
    assert private_path not in json.dumps(violations)

    bounded=json.dumps({'report_id':'r_0123456789ab','limit':4000})
    assert helper['call_policy_flags']('mcp__cortex__read_report',bounded,'general',str(tmp_path))==[]


def test_external_worker_diagnostics_remain_outside_private_cache_gate(tmp_path):
    (tmp_path/'.git').mkdir()
    helper=OBSERVER
    assert helper['call_policy_flags'](
        'mcp__codebase_memory__search_graph',
        json.dumps({'query':'private module'}),'explorer',str(tmp_path))==[]
    assert helper['call_policy_flags'](
        'exec_command',json.dumps({'cmd':'git status --short'}),'explorer',str(tmp_path))==[]
    assert helper['call_policy_flags'](
        'exec_command',json.dumps({'cmd':'rg -n private .'}),'explorer',str(tmp_path))==[]


def test_rg_grep_path_policy_is_command_aware_end_to_end(tmp_path):
    helper=OBSERVER

    def audit(command):
        arguments=json.dumps({'cmd':command})
        metadata=helper['path_policy_metadata']('exec_command',arguments,'explorer',str(tmp_path))
        flags=helper['call_policy_flags']('exec_command',arguments,'explorer',str(tmp_path))
        row=dict(thread_id='worker',role='explorer',tool='exec_command',outcome='success',
                 policy_flags=flags,**metadata)
        violations=helper['call_policy_violations']([row])
        return metadata,flags,violations

    pattern,flags,violations=audit("rg -n '.codex/cortex/' scripts")
    assert pattern['path_target_class']=='approved_instruction_or_static_mention'
    assert pattern['path_access_kind']=='static_mention'
    assert pattern['path_policy_provenance']=='observer_literal_marker'
    assert pattern['path_policy_decision']=='allowed'
    assert flags==[]
    assert helper['orchestration_policy_violations'](violations)==[]

    for glob in ('*','?','[abc]','{private,public}'):
        literal,flags,violations=audit(f"rg -n .codex/cortex/{glob} scripts")
        assert literal['path_target_class']=='cortex_internal'
        assert literal['path_policy_decision']=='unauthorized_access'
        assert flags==['forbidden_plugin_or_cache_access']
        assert helper['orchestration_policy_violations'](violations)

    for glob in ('*','?','[abc]','{private,public}'):
        quoted,flags,violations=audit(f"rg -n '.codex/cortex/{glob}' scripts")
        assert quoted['path_target_class']=='approved_instruction_or_static_mention'
        assert quoted['path_access_kind']=='static_mention'
        assert quoted['path_policy_decision']=='allowed'
        assert flags==[]
        assert helper['orchestration_policy_violations'](violations)==[]

    # Double-quoted rg/grep patterns are equally static; only unquoted
    # expansion/glob operands are private-target findings.
    double_quoted,flags,violations=audit('rg --files -g "!**/.codex/cortex/**" | sed -n "1,120p"')
    assert double_quoted['path_target_class']=='approved_instruction_or_static_mention'
    assert double_quoted['path_access_kind']=='static_mention'
    assert double_quoted['path_policy_decision']=='allowed'
    assert flags==[]
    assert helper['orchestration_policy_violations'](violations)==[]

    target,flags,violations=audit("grep -R -n needle .codex/cortex/private")
    assert target['path_target_class']=='cortex_internal'
    assert target['path_access_kind']=='unauthorized_read_or_probe'
    assert target['path_policy_decision']=='unauthorized_access'
    assert flags==['forbidden_plugin_or_cache_access']
    assert helper['orchestration_policy_violations'](violations)


    ambiguous,flags,violations=audit('rg -n "$PATTERN .codex/cortex/" scripts')
    assert ambiguous['path_target_class']=='cortex_internal'
    assert ambiguous['path_policy_decision']=='unauthorized_access'
    assert flags==['forbidden_plugin_or_cache_access']
    assert helper['orchestration_policy_violations'](violations)

    direct,flags,violations=audit('cat .codex/cortex/private')
    assert direct['path_target_class']=='cortex_internal'
    assert direct['path_policy_decision']=='unauthorized_access'
    assert flags==['forbidden_plugin_or_cache_access']
    assert helper['orchestration_policy_violations'](violations)

    # A separated option value is a filesystem operand too; it must not be
    # mistaken for the search pattern or silently dropped from path analysis.
    for command in (
        'grep --exclude-from .codex/cortex/private needle scripts',
        'grep --exclude-from=.codex/cortex/private needle scripts',
    ):
        excluded_file,flags,violations=audit(command)
        assert excluded_file['path_target_class']=='cortex_internal'
        assert excluded_file['path_policy_decision']=='unauthorized_access'
        assert flags==['forbidden_plugin_or_cache_access']
        assert helper['orchestration_policy_violations'](violations)


def test_pipeline_private_exclusion_is_not_a_private_read_but_private_target_is(tmp_path):
    helper=OBSERVER

    excluded="pwd && rg --files -g '!**/.codex/cortex/**' | sed -n '1,120p'"
    arguments=json.dumps({'cmd':excluded})
    metadata=helper['path_policy_metadata']('exec_command',arguments,'explorer',str(tmp_path))
    assert metadata['path_target_class']=='approved_instruction_or_static_mention'
    assert metadata['path_access_kind']=='static_mention'
    assert helper['call_policy_flags']('exec_command',arguments,'explorer',str(tmp_path))==[]

    private="cat .codex/cortex/private | sed -n '1,20p'"
    private_arguments=json.dumps({'cmd':private})
    private_metadata=helper['path_policy_metadata']('exec_command',private_arguments,
                                                     'explorer',str(tmp_path))
    assert private_metadata['path_target_class']=='cortex_internal'
    assert private_metadata['path_access_kind']=='unauthorized_read_or_probe'
    assert 'forbidden_plugin_or_cache_access' in helper['call_policy_flags'](
        'exec_command',private_arguments,'explorer',str(tmp_path))


def test_observer_retains_codebase_memory_and_git_failures_as_worker_diagnostics():
    helper=OBSERVER
    memory=dict(thread_id='worker',role='explorer',tool='mcp__codebase_memory__list_projects',
                outcome='success')
    git=dict(thread_id='worker',role='explorer',tool='command_execution',outcome='error',
             error_code='command_exit_128',command_family='git',exit_code=128)
    rows=[memory,git]
    assert helper['is_orchestration_call'](memory) is False
    assert helper['is_orchestration_call'](git) is False
    assert helper['orchestration_error_history'](rows)==[]
    unresolved,_=helper['classify_host_failures'](rows)
    assert unresolved==[git]


def test_observer_allows_narrow_skill_read_and_records_safe_path_provenance(tmp_path,monkeypatch):
    monkeypatch.setattr(Path,'home',lambda:tmp_path)
    skill=tmp_path/'.cortex-dev/.codex/plugins/cache/cortex/cortex/build/skills/worker-general/SKILL.md'
    skill.parent.mkdir(parents=True);skill.write_text('instructions')
    arguments=json.dumps({'cmd':'cat '+str(skill)})
    metadata=OBSERVER['path_policy_metadata']('exec_command',arguments,'general','/tmp/project')
    assert metadata['path_target_class']=='approved_instruction_or_static_mention'
    assert metadata['path_access_kind']=='approved_instruction_read'
    assert metadata['path_policy_decision']=='allowed'
    assert 'forbidden_plugin_or_cache_access' not in OBSERVER['call_policy_flags'](
        'exec_command',arguments,'general','/tmp/project')
