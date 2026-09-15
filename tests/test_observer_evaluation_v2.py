import json
from datetime import datetime, timezone
from pathlib import Path
import runpy
import sqlite3
import tomllib

import pytest


ROOT=Path(__file__).resolve().parents[1]
OBSERVER=runpy.run_path(str(ROOT/'scripts/cortex-desktop-dev'))
EVAL=runpy.run_path(str(ROOT/'scripts/cortex_eval.py'))
CANDIDATE_VERSION = json.loads((ROOT / 'plugins/cortex/.codex-plugin/plugin.json').read_text())['version']


def trusted_cache(base):
    cache = base / CANDIDATE_VERSION
    manifest = cache / '.codex-plugin/plugin.json'
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_bytes((ROOT / 'plugins/cortex/.codex-plugin/plugin.json').read_bytes())
    return cache


def token_record(thread,response,stamp,**overrides):
    usage=dict(input_tokens=10,cached_input_tokens=4,cache_write_input_tokens=2,
               output_tokens=3,reasoning_output_tokens=1,total_tokens=13)
    usage.update(overrides)
    return dict(timestamp=stamp,type='token_usage_record',
                payload=dict(thread_id=thread,response_id=response,usage=usage))


def test_hook_actions_are_separate_from_model_and_mcp_events(tmp_path):
    (tmp_path/'server.jsonl').write_text(json.dumps(dict(time_ns=2,operation='create_task',outcome='success'))+'\n')
    private='do not expose this path'
    later=dict(timestamp_ns=20,event_kind='hook',hook_event='PostToolUse',outcome='success',
               receipt_digest='a'*12,result_digest='c'*12,command_session_id='command-7',
               parent_session_id='parent-2',binding_origin='native_hook',
               tool_name='Bash',response_shape=dict(json_type='string',string_length=9),
               changed_path_count=1,changed_paths_digest='b'*12,
               raw_output=private,arguments=private)
    earlier=dict(timestamp_ns=10,event_kind='hook',hook_event='PreToolUse',outcome='success',
                 command_session_id='command-3',parent_session_id='parent-1',
                 binding_origin='task_scope')
    (tmp_path/'hooks-12.jsonl').write_text(json.dumps(later)+'\n'+json.dumps(earlier)+'\n')
    assert [row['operation'] for row in OBSERVER['observed_events'](tmp_path)]==['create_task']
    rows=OBSERVER['observed_hook_events'](tmp_path)
    assert [row['timestamp_ns'] for row in rows]==[10,20]
    assert rows[1]=={key:value for key,value in later.items() if key not in {'raw_output','arguments'}}
    assert rows[1]['command_session_id']=='command-7'
    assert rows[1]['parent_session_id']=='parent-2'


def test_hook_failure_diagnostics_are_bounded_and_correlate_wrapper_truncation(tmp_path):
    hook=dict(timestamp_ns=200,event_kind='hook',hook_event='PostToolUse',outcome='error',
              task_id='t_'+'a'*12,thread_id='worker',parent_thread_id='parent',role='backend_dev',
              tool_name='exec_command',error_class='OSError',error_stage='post_tool_receipt',
              raw_error='private hook failure')
    (tmp_path/'hooks-13.jsonl').write_text(json.dumps(hook)+'\n')
    observed=OBSERVER['observed_hook_events'](tmp_path)
    assert observed == [{key:value for key,value in hook.items() if key!='raw_error'}]
    wrapper=dict(timestamp='2026-01-01T00:00:00Z',thread_id='worker',parent_thread_id='parent',
                 role='backend_dev',tool='exec_command',outcome='covered_by_nested',
                 wrapper_outcome='truncated',error_code='truncated_output')
    classified=OBSERVER['classify_audit_findings'](
        failures=[],hook_failures=observed,host_failures=[wrapper],policy_violations=[],
        open_sessions=[],open_cells=[])
    invalidators=classified['evidence_integrity_invalidators']
    assert any(item['source']=='hook_failure' and item['error_class']=='OSError'
               and item['error_stage']=='post_tool_receipt' for item in invalidators)
    assert any(item['source']=='host_failure'
               and item['reason']=='truncated_or_unverified_observation' for item in invalidators)


def test_pre_dispatch_denial_is_value_free_prevented_attempt_not_hook_failure(tmp_path):
    denial=dict(timestamp_ns=210,event_kind='hook',hook_event='PreToolUse',outcome='error',
                action_status='denied',task_id='t_'+'b'*12,thread_id='worker',parent_thread_id='parent',
                role='worker',binding_confidence='receipt',binding_origin='native_hook',
                authorization_outcome='PERMISSION_DENIED',
                authorization_capability='cortex.internal.*',authorization_reason='internal_operand',
                execution_status='denied_before_dispatch',
                diagnostic_codes=['pre_dispatch_authorization_denied','PERMISSION_DENIED'],
                raw_command='cat private/cache')
    (tmp_path/'hooks-denial.jsonl').write_text(json.dumps(denial)+'\n')
    observed=OBSERVER['observed_hook_events'](tmp_path)
    assert observed == [{key:value for key,value in denial.items() if key!='raw_command'}]
    classified=OBSERVER['classify_audit_findings'](
        failures=[],hook_failures=observed,host_failures=[],policy_violations=[],
        open_sessions=[],open_cells=[])
    assert classified['evidence_integrity_invalidators'] == []
    prevented=classified['quality_findings'][0]
    assert (prevented['source'], prevented['classification'], prevented['reason']) == (
        'hook_prevented_attempt', 'prevented_attempt_diagnostic',
        'authorization_denied_before_dispatch')
    assert prevented['authorization_outcome']=='PERMISSION_DENIED'
    assert prevented['execution_status']=='denied_before_dispatch'
    assert 'private/cache' not in json.dumps(prevented)


@pytest.mark.parametrize('execution_status', [None, 'unknown'])
def test_incomplete_pre_dispatch_denial_is_fail_closed_not_score_eligible(tmp_path, execution_status):
    denial=dict(timestamp_ns=211,event_kind='hook',hook_event='PreToolUse',outcome='error',
                action_status='denied',task_id='t_'+'c'*12,thread_id='worker',parent_thread_id='parent',
                role='worker',binding_confidence='receipt',binding_origin='native_mcp',
                authorization_outcome='PERMISSION_DENIED',
                authorization_capability='cortex.internal.*',authorization_reason='internal_operand',
                diagnostic_codes=['pre_dispatch_authorization_denied','PERMISSION_DENIED'])
    if execution_status is not None:
        denial['execution_status']=execution_status
    (tmp_path/'hooks-incomplete-denial.jsonl').write_text(json.dumps(denial)+'\n')
    classified=OBSERVER['classify_audit_findings'](
        failures=[],hook_failures=OBSERVER['observed_hook_events'](tmp_path),host_failures=[],
        policy_violations=[],open_sessions=[],open_cells=[])
    assert classified['evidence_valid'] is False
    assert classified['score_eligible'] is False
    invalidator=classified['evidence_integrity_invalidators'][0]
    assert (invalidator['source'], invalidator['reason']) == (
        'hook_prevented_attempt', 'pre_dispatch_denial_receipt_incomplete')


def test_desktop_observation_is_scoped_to_submitted_task_tree(tmp_path,monkeypatch):
    monkeypatch.setattr(Path,'home',lambda:tmp_path)
    codex=tmp_path/'.cortex-dev/.codex';codex.mkdir(parents=True)
    events=tmp_path/'events';events.mkdir()
    stamp='2026-09-10T18:17:00Z'
    usage=dict(input_tokens=10,cached_input_tokens=0,cache_write_input_tokens=0,
               output_tokens=2,reasoning_output_tokens=1,total_tokens=12)
    def rollout(path,thread,parent=None):
        source=[]
        if parent:
            source.append(dict(timestamp=stamp,type='session_meta',payload=dict(
                source=dict(subagent=dict(thread_spawn=dict(parent_thread_id=parent))))))
        source.extend([
            dict(timestamp=stamp,type='response_item',payload=dict(
                type='function_call',call_id='call-'+thread,name='list_agents',arguments='{}')),
            dict(timestamp=stamp,type='response_item',payload=dict(
                type='function_call_output',call_id='call-'+thread,output='ok')),
            dict(timestamp=stamp,type='token_usage_record',payload=dict(
                thread_id=thread,response_id='response-'+thread,usage=usage)),
        ])
        path.write_text('\n'.join(json.dumps(row) for row in source)+'\n')
    paths={name:tmp_path/f'{name}.jsonl' for name in ('root-a','child-a','root-b','child-b')}
    rollout(paths['root-a'],'root-a');rollout(paths['child-a'],'child-a','root-a')
    rollout(paths['root-b'],'root-b');rollout(paths['child-b'],'child-b','root-b')
    created=1789064200
    with sqlite3.connect(codex/'state_5.sqlite') as db:
        db.execute('CREATE TABLE threads(id TEXT,rollout_path TEXT,agent_role TEXT,model TEXT,reasoning_effort TEXT,created_at INTEGER,cwd TEXT)')
        db.execute('CREATE TABLE thread_spawn_edges(parent_thread_id TEXT,child_thread_id TEXT)')
        db.executemany('INSERT INTO threads VALUES (?,?,?,?,?,?,?)',[
            ('root-a',str(paths['root-a']),None,'gpt-5.6-luna','high',created,'/fixture'),
            ('child-a',str(paths['child-a']),'qa_engineer','gpt-5.6-luna','medium',created,'/fixture'),
            ('root-b',str(paths['root-b']),None,'gpt-5.6-luna','high',created,'/fixture'),
            ('child-b',str(paths['child-b']),'qa_engineer','gpt-5.6-luna','medium',created,'/fixture'),
        ])
        db.executemany('INSERT INTO thread_spawn_edges VALUES (?,?)',[
            ('root-a','child-a'),('root-b','child-b')])
    (events/'server.jsonl').write_text('\n'.join(json.dumps(row) for row in [
        dict(time_ns=1,operation='create_task',outcome='success',thread_id='root-a'),
        dict(time_ns=2,operation='create_task',outcome='success',thread_id='root-b'),
        dict(time_ns=3,operation='initialize',outcome='success'),
    ])+'\n')
    (events/'hooks-test.jsonl').write_text('\n'.join(json.dumps(row) for row in [
        dict(time_ns=1,event_kind='hook',hook_event='Stop',outcome='success',parent_session_id='root-a'),
        dict(time_ns=2,event_kind='hook',hook_event='Stop',outcome='success',parent_session_id='root-b'),
    ])+'\n')
    state=dict(workdir='/fixture',started_at=created,thread_created_since=created,
               trial_started_at=created,first_submission_at=created,
               thread_id='root-a',events=str(events))
    assert OBSERVER['desktop_task_thread_ids'](state)=={'root-a','child-a'}
    assert OBSERVER['desktop_task_inventory'](state)==(
        {'root-a','child-a'},['root-b'],[])
    calls=OBSERVER['observed_tool_calls'](state)
    assert {row.get('thread_id') for row in calls if row.get('thread_id')}=={'root-a','child-a'}
    scoped=OBSERVER['desktop_task_thread_ids'](state)
    assert [row.get('thread_id') for row in OBSERVER['observed_events'](events,scoped)]==[
        'root-a',None]
    assert [row['parent_session_id'] for row in OBSERVER['observed_hook_events'](events,scoped)]==[
        'root-a']
    usage_result=OBSERVER['participant_token_usage'](state)
    assert {row['thread_id'] for row in usage_result['participants']}=={'root-a','child-a'}
    assert not any(row['terminal'] for row in usage_result['participants'])
    def lifecycle(thread, kind, turn):
        with paths[thread].open('a') as target:
            target.write(json.dumps({'type':'event_msg','payload':{
                'type':kind,'turn_id':turn,'duration_ms':1000}})+'\n')
    lifecycle('root-a','task_started','root-turn')
    lifecycle('child-a','task_started','child-turn')
    lifecycle('root-a','task_complete','root-turn')
    participants=OBSERVER['participant_token_usage'](state)['participants']
    quality={'evidence_valid':True,'score_eligible':True,
             'evidence_integrity_invalidators':[],'quality_findings':[]}
    accept=OBSERVER['desktop_final_acceptance']
    assert not accept({'status':'complete'},quality,participants)
    lifecycle('child-a','task_complete','child-turn')
    participants=OBSERVER['participant_token_usage'](state)['participants']
    assert accept({'status':'complete'},quality,participants)
    # Desktop prefixes a child rollout with copied parent context. Its pending
    # parent turn is not work owned by the child; explicit native settings resume
    # the child's own lifecycle. A genuine later child turn must still block.
    with paths['child-a'].open('a') as target:
        for row in [
            {'type':'session_meta','payload':{'id':'root-a'}},
            {'type':'event_msg','payload':{'type':'task_started','turn_id':'inherited-parent'}},
            {'type':'event_msg','payload':{'type':'thread_settings_applied','thread_id':'child-a'}},
        ]: target.write(json.dumps(row)+'\n')
    assert accept({'status':'complete'},quality,
                  OBSERVER['participant_token_usage'](state)['participants'])
    lifecycle('child-a','task_started','own-followup')
    assert not accept({'status':'complete'},quality,
                      OBSERVER['participant_token_usage'](state)['participants'])
    lifecycle('child-a','task_complete','own-followup')
    assert not accept({'status':'complete'},dict(quality,quality_findings=[
        {'classification':'product_quality_outcome'}]),participants)
    lifecycle('root-a','task_started','followup')
    assert not accept({'status':'complete'},quality,
                      OBSERVER['participant_token_usage'](state)['participants'])


def test_task_scope_rejects_rows_with_any_conflicting_native_identity():
    check=OBSERVER['event_in_task_scope'];scope={'root','child'}
    assert check({'thread_id':'child','parent_thread_id':'root','task_id':'t_cortex'},scope)
    assert not check({'thread_id':'foreign','parent_thread_id':'root'},scope)
    assert not check({'task_id':'foreign','parent_session_id':'root'},scope)
    assert not check({'thread_id':'child','parent_session_id':'foreign'},scope)
    assert check({'operation':'initialize'},scope)


def test_desktop_inventory_surfaces_duplicate_and_cyclic_foreign_topology(tmp_path,monkeypatch):
    monkeypatch.setattr(Path,'home',lambda:tmp_path)
    codex=tmp_path/'.cortex-dev/.codex';codex.mkdir(parents=True)
    created=1789064200
    with sqlite3.connect(codex/'state_5.sqlite') as db:
        db.execute('CREATE TABLE threads(id TEXT,created_at INTEGER,cwd TEXT)')
        db.execute('CREATE TABLE thread_spawn_edges(parent_thread_id TEXT,child_thread_id TEXT)')
        db.executemany('INSERT INTO threads VALUES (?,?,?)',[
            ('root',created,'/fixture'),('child',created,'/fixture'),
            ('cycle-a',created,'/fixture'),('cycle-b',created,'/fixture')])
        db.executemany('INSERT INTO thread_spawn_edges VALUES (?,?)',[
            ('root','child'),('root','child'),
            ('cycle-a','cycle-b'),('cycle-b','cycle-a')])
    state=dict(workdir='/fixture',started_at=created,thread_id='root')
    scoped,foreign,invalid=OBSERVER['desktop_task_inventory'](state)
    assert scoped=={'root','child'}
    assert foreign==['cycle-a','cycle-b']
    assert 'duplicate_edge:child' in invalid
    assert {'parent_cycle:cycle-a','parent_cycle:cycle-b'} <= set(invalid)
    usage=OBSERVER['participant_token_usage'](state)
    assert usage['status']=='invalid_topology'
    assert usage['participants']==[]
    assert 'duplicate_edge:child' in usage['invalid_task_topology']


def test_desktop_inventory_surfaces_self_parent_and_malformed_recent_edge(tmp_path,monkeypatch):
    monkeypatch.setattr(Path,'home',lambda:tmp_path)
    codex=tmp_path/'.cortex-dev/.codex';codex.mkdir(parents=True)
    created=1789064200
    with sqlite3.connect(codex/'state_5.sqlite') as db:
        db.execute('CREATE TABLE threads(id TEXT,created_at INTEGER,cwd TEXT)')
        db.execute('CREATE TABLE thread_spawn_edges(parent_thread_id TEXT,child_thread_id TEXT)')
        db.executemany('INSERT INTO threads VALUES (?,?,?)',[
            ('root',created,'/fixture'),('foreign',created,'/fixture')])
        db.executemany('INSERT INTO thread_spawn_edges VALUES (?,?)',[
            ('foreign','foreign'),('root','missing-child')])
    state=dict(workdir='/fixture',started_at=created,thread_id='root')
    scoped,foreign,invalid=OBSERVER['desktop_task_inventory'](state)
    assert scoped=={'root'}
    assert foreign==['foreign']
    assert 'self_parent:foreign' in invalid
    assert 'parent_cycle:foreign' in invalid
    assert 'edge_child_outside_recent_inventory:missing-child' in invalid


def test_usage_fails_closed_on_conflicting_child_parents(tmp_path,monkeypatch):
    monkeypatch.setattr(Path,'home',lambda:tmp_path)
    codex=tmp_path/'.cortex-dev/.codex';codex.mkdir(parents=True)
    created=1789064200
    with sqlite3.connect(codex/'state_5.sqlite') as db:
        db.execute('CREATE TABLE threads(id TEXT,created_at INTEGER,cwd TEXT)')
        db.execute('CREATE TABLE thread_spawn_edges(parent_thread_id TEXT,child_thread_id TEXT)')
        db.executemany('INSERT INTO threads VALUES (?,?,?)',[
            ('root',created,'/fixture'),('foreign',created,'/fixture'),
            ('child',created,'/fixture')])
        db.executemany('INSERT INTO thread_spawn_edges VALUES (?,?)',[
            ('root','child'),('foreign','child')])
    state=dict(workdir='/fixture',started_at=created,thread_id='root')
    scoped,foreign,invalid=OBSERVER['desktop_task_inventory'](state)
    assert scoped=={'root'}
    assert foreign==['child','foreign']
    assert 'multiple_parents:child' in invalid
    usage=OBSERVER['participant_token_usage'](state)
    assert usage==dict(status='invalid_topology',wall_seconds=None,totals=None,
                       participants=[],foreign_task_roots=['child','foreign'],
                       invalid_task_topology=['multiple_parents:child'])


def test_original_request_receipt_accepts_native_user_message_event(tmp_path,monkeypatch):
    monkeypatch.setattr(Path,'home',lambda:tmp_path)
    codex=tmp_path/'.cortex-dev/.codex';codex.mkdir(parents=True)
    events=tmp_path/'events';events.mkdir()
    rollout=tmp_path/'root.jsonl'
    prompt='Make list:\n1. first'
    delivered='$cortex:orchestrator Make list:\n\n1. first'
    captured=OBSERVER['native_archived_request_digest'](delivered)
    def entry(stamp,payload,kind='response_item'):
        return json.dumps(dict(timestamp=stamp,type=kind,payload=payload))
    rollout.write_text('\n'.join([
        entry('2026-09-10T18:00:00.000Z',dict(type='item_completed',thread_id='root',
            item=dict(type='UserMessage',id='message-root',
                      content=[dict(type='text',text=delivered)])),'event_msg'),
        entry('2026-09-10T18:00:00.010Z',dict(type='custom_tool_call',call_id='create',
            name='functions.exec',input='await tools.mcp__cortex__create_task({project_root:"/fixture",request_key:"key"})')),
        entry('2026-09-10T18:00:00.050Z',dict(type='item_completed',item=dict(
            type='McpToolCall',server='cortex',tool='create_task',status='completed',
            arguments={'project_root':'/fixture','request_key':'key'},
            result={'structuredContent':{'original_request_sha256':captured}}))),
        entry('2026-09-10T18:00:00.100Z',dict(type='custom_tool_call_output',call_id='create',
            output='Script completed')),
    ])+'\n')
    with sqlite3.connect(codex/'state_5.sqlite') as db:
        db.execute('CREATE TABLE threads(id TEXT,rollout_path TEXT,agent_role TEXT,model TEXT,reasoning_effort TEXT,created_at INTEGER,cwd TEXT)')
        db.execute('CREATE TABLE thread_spawn_edges(parent_thread_id TEXT,child_thread_id TEXT)')
        db.execute('INSERT INTO threads VALUES (?,?,?,?,?,?,?)',
                   ('root',str(rollout),None,'gpt-5.6-luna','high',1789063200,'/fixture'))
    (events/'server.jsonl').write_text(json.dumps(dict(
        time_ns=1789063200040000000,operation='create_task',outcome='success',
        thread_id='root',parent_thread_id=None))+'\n')
    state=dict(workdir='/fixture',started_at=1789063200,thread_created_since=1789063200,
               thread_id='root',events=str(events),
               original_request_sha256=OBSERVER['original_request_digest'](prompt),
               desktop_editor_source_sha256=OBSERVER['original_request_digest'](
                   OBSERVER['desktop_editor_source'](prompt)))
    rows=OBSERVER['observed_tool_calls'](state)
    create=[row for row in rows if row.get('tool')=='mcp__cortex__create_task']
    assert len(create)==1 and create[0]['original_request_preserved'] is True
    assert 'coordinator_original_request_changed' not in {
        row['violation'] for row in OBSERVER['call_policy_violations'](rows)}


def test_all_tools_catalogue_label_is_exact_and_keeps_direct_create_provenance(tmp_path, monkeypatch):
    """Exercise the live-shaped wrapper/output/receipt/event correlation."""
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    codex=tmp_path/'.cortex-dev/.codex';codex.mkdir(parents=True)
    rollout=tmp_path/'coordinator.jsonl'; events=tmp_path/'events';events.mkdir()
    def entry(stamp, payload):
        return json.dumps(dict(timestamp=stamp,type='response_item',payload=payload))
    rollout.write_text('\n'.join([
        entry('2026-09-10T18:00:00.010Z',dict(type='custom_tool_call',call_id='catalogue',
            name='functions.exec',input='text(ALL_TOOLS)')),
        entry('2026-09-10T18:00:00.020Z',dict(type='custom_tool_call_output',call_id='catalogue',
            output='Script completed')),
        entry('2026-09-10T18:00:00.030Z',dict(type='custom_tool_call',call_id='create',
            name='functions.exec',input='await tools.mcp__cortex__create_task({project_root:"/fixture",request_key:"key"})')),
        entry('2026-09-10T18:00:00.040Z',dict(type='item_completed',item=dict(
            type='McpToolCall',server='cortex',tool='create_task',status='completed',
            arguments={'project_root':'/fixture','request_key':'key'},
            result={'structuredContent':{'task_id':'t_child'}}))),
        entry('2026-09-10T18:00:00.050Z',dict(type='custom_tool_call_output',call_id='create',
            output='Script completed')),
    ])+'\n')
    with sqlite3.connect(codex/'state_5.sqlite') as db:
        db.execute('CREATE TABLE threads(id TEXT,rollout_path TEXT,agent_role TEXT,model TEXT,reasoning_effort TEXT,created_at INTEGER,cwd TEXT)')
        db.execute('CREATE TABLE thread_spawn_edges(parent_thread_id TEXT,child_thread_id TEXT)')
        db.execute('INSERT INTO threads VALUES (?,?,?,?,?,?,?)',
                   ('root',str(rollout),None,'gpt-5.6-luna','high',1789063200,'/fixture'))
    (events/'server.jsonl').write_text(json.dumps(dict(
        time_ns=1789063200045000000,operation='create_task',outcome='success',
        thread_id='root',parent_thread_id=None,task_id='t_child'))+'\n')
    rows=OBSERVER['observed_tool_calls'](dict(workdir='/fixture',started_at=1789063200,
                                              thread_created_since=1789063200,events=str(events)))
    catalogue=[row for row in rows if row.get('tool')=='tool_catalogue_search']
    creates=[row for row in rows if row.get('tool')=='mcp__cortex__create_task']
    assert len(catalogue)==1 and catalogue[0]['catalogue_discovery'] is True
    assert catalogue[0]['host_receipt_observed'] is True
    assert len(creates)==1 and creates[0]['direct_public_mcp'] is True
    assert creates[0]['server_observed'] is True and creates[0]['canonical_call_key']


def test_worker_static_bracket_and_alias_app_calls_are_observed(tmp_path,monkeypatch):
    codex=tmp_path/'.cortex-dev/.codex';codex.mkdir(parents=True)
    rollout=tmp_path/'worker.jsonl'
    def entry(at,payload):
        return json.dumps(dict(timestamp=datetime.fromtimestamp(at,timezone.utc).isoformat(),
                               type='response_item',payload=payload))
    source='const send = tools["mcp__codex_app__send_message_to_thread"]; await send({threadId:"x"});'
    rollout.write_text('\n'.join([
        entry(110,dict(type='custom_tool_call',call_id='c',name='functions.exec',input=source)),
        entry(111,dict(type='custom_tool_call_output',call_id='c',output='Script completed')),
    ])+'\n')
    with sqlite3.connect(codex/'state_5.sqlite') as db:
        db.execute('CREATE TABLE threads(id TEXT,rollout_path TEXT,agent_role TEXT,model TEXT,reasoning_effort TEXT,created_at INTEGER,cwd TEXT)')
        db.execute('CREATE TABLE thread_spawn_edges(parent_thread_id TEXT,child_thread_id TEXT)')
        db.executemany('INSERT INTO threads VALUES (?,?,?,?,?,?,?)',[
            ('root',str(rollout),None,'gpt-5.6-luna','high',100,'/fixture'),
            ('child',str(rollout),'technical_writer','gpt-5.6-luna','medium',101,'/fixture'),
        ])
        db.execute('INSERT INTO thread_spawn_edges VALUES (?,?)',('root','child'))
    monkeypatch.setattr(Path,'home',lambda:tmp_path)
    rows=OBSERVER['observed_tool_calls'](dict(workdir='/fixture',started_at=100,
                                              thread_created_since=100,events=str(tmp_path/'events')))
    worker=[row for row in rows if row.get('thread_id')=='child'
            and row.get('tool')=='mcp__codex_app__send_message_to_thread']
    assert len(worker)==1
    assert 'forbidden_worker_app_thread_message' in worker[0]['policy_flags']
    violations=OBSERVER['call_policy_violations'](rows)
    assert any(row['thread_id']=='child' and
               row['violation']=='forbidden_worker_app_thread_message'
               for row in violations)


def test_all_participant_usage_counts_responses_once_and_cache_separately(tmp_path,monkeypatch):
    monkeypatch.setattr(Path,'home',lambda:tmp_path)
    codex=tmp_path/'.cortex-dev/.codex';codex.mkdir(parents=True)
    coordinator=tmp_path/'coordinator.jsonl';worker=tmp_path/'worker.jsonl'
    coordinator.write_text('\n'.join(json.dumps(row) for row in [
        dict(timestamp='2026-09-06T00:00:00Z',type='session_meta',payload=dict(source='cli')),
        token_record('parent','one','2026-09-06T00:00:05Z'),
        token_record('parent','one','2026-09-06T00:00:05Z'),
        token_record('parent','two','2026-09-06T00:00:08Z',input_tokens=20,cached_input_tokens=8,total_tokens=23),
        dict(timestamp='2026-09-06T00:00:09Z',type='event_msg',payload=dict(type='task_complete',turn_id='turn',duration_ms=7500))]))
    worker.write_text('\n'.join(json.dumps(row) for row in [
        dict(timestamp='2026-09-06T00:00:01Z',type='session_meta',payload=dict(
            source=dict(subagent=dict(thread_spawn=dict(parent_thread_id='parent'))))),
        token_record('child','three','2026-09-06T00:00:10Z')])+'\n')
    db=sqlite3.connect(codex/'state_5.sqlite')
    db.execute('CREATE TABLE threads(id TEXT,rollout_path TEXT,agent_role TEXT,model TEXT,reasoning_effort TEXT,created_at INTEGER,cwd TEXT)')
    db.executemany('INSERT INTO threads VALUES (?,?,?,?,?,?,?)',[
        ('parent',str(coordinator),None,'gpt-6-astra','high',1788652800,'/fixture'),
        ('child',str(worker),None,'gpt-5.6-luna','high',1788652801,'/fixture')])
    db.commit();db.close()
    result=OBSERVER['participant_token_usage'](dict(workdir='/fixture',started_at=1788652800,
        thread_created_since=1788652800,trial_started_at=1788652800,first_submission_at=1788652800))
    assert result['status']=='complete' and result['wall_seconds']==7.5
    assert result['wall_source']=='coordinator_task_lifecycle'
    assert result['totals']['input_tokens']==40
    assert result['totals']['cached_input_tokens']==16
    assert result['totals']['cache_write_input_tokens']==6
    assert [row['role'] for row in result['participants']]==['coordinator','worker']
    assert [row['responses'] for row in result['participants']]==[2,1]


def complete_usage():
    tokens=dict(input_tokens=100,cached_input_tokens=60,cache_write_input_tokens=5,
                output_tokens=20,reasoning_output_tokens=4,total_tokens=120)
    return dict(status='complete',wall_seconds=9.0,totals=tokens,
                participants=[dict(thread_id='private',role='coordinator',model='gpt-6-astra',
                    reasoning_effort='high',responses=1,tokens=tokens)])


def observations(**updates):
    value=dict(usage=complete_usage(),wall_seconds=9.0,protocol_pass=True,
               claimed_complete=True,lost_requirements=0,recovery_success=None,
               payload_sha256='a'*16,host='cli',coordinator_model='gpt-6-astra',
               coordinator_effort='high',steering_observed=True,resume_observed=True)
    value.update(updates);return value


def test_pilot_is_fixed_to_three_configs_four_cases_and_keeps_unknowns_null(tmp_path):
    listing=EVAL['pilot_compare']([])
    assert len(listing['runs'])==12
    assert all(run['status']=='unrun' for run in listing['runs'])
    assert all(run['tokens'] is None and run['wall_seconds'] is None for run in listing['runs'])
    for summary in listing['configurations'].values():
        assert summary['median_total_tokens'] is None
        assert summary['correctness'] is None

    trial=tmp_path/'trial'
    EVAL['pilot_prepare']('stable-unique',trial,'baseline')
    (trial/'project/solution.py').write_text('def solve(values):\n    return list(dict.fromkeys(values))\n')
    result=EVAL['pilot_record'](trial,observations())
    assert result['status']=='measured' and result['correctness'] is True
    assert result['false_completion'] is False
    assert result['tokens']['cached_input_tokens']==60
    assert 'thread_id' not in result['participant_tokens'][0]


def test_historical_fixture_gets_a_separate_pilot_overlay(tmp_path):
    trial=tmp_path/'trial'
    EVAL['prepare']('retry-dedup',trial,'baseline',1)
    original=(trial/'trial.json').read_bytes()
    overlay=EVAL['pilot_adopt'](trial,'baseline')
    assert (trial/'trial.json').read_bytes()==original
    assert overlay['suite']=='hooks-pilot-v1'
    assert json.loads((trial/'pilot-trial.json').read_text())==overlay


def test_pilot_does_not_invent_missing_usage_or_recovery(tmp_path):
    trial=tmp_path/'trial'
    EVAL['pilot_prepare']('resume-pagination',trial,'full_hooks')
    missing=observations(usage=dict(status='unavailable',wall_seconds=None,totals=None,participants=[]),
                         wall_seconds=None,recovery_success=None,resume_observed=False)
    result=EVAL['pilot_record'](trial,missing)
    assert result['status']=='incomplete'
    assert result['correctness'] is None and result['false_completion'] is None
    assert result['tokens'] is None and result['recovery_success'] is None


def test_wrapper_truncation_and_running_receipt_remain_distinct():
    truncated=OBSERVER['observed_outcome']('Warning: truncated output','exec_command')
    running=OBSERVER['observed_outcome']('{"session_id":42,"output":"started"}','exec_command')
    assert truncated[0]=='truncated'
    assert running[0]=='running'
    assert truncated[2]!=running[2]


def test_mcp_event_joins_unique_nearby_receipt_by_operation_identity():
    receipt=dict(thread_id='worker',tool='mcp__cortex__write_report',outcome='success',
                 server_observed=False,host_receipt_observed=True,
                 host_receipt_outcome='success',
                 host_receipt_timestamp='2026-09-06T00:43:36.243Z',
                 draft_id='d_83b87f53d614')
    event=dict(outcome='success',draft_id='d_83b87f53d614',report_id='r_6b6185b46456')
    matched=OBSERVER['event_call_candidate'](
        [receipt],1788655416243491179,event.get('report_id'),event)
    assert matched is receipt


def test_observed_tool_calls_correlates_read_report_event_to_original_wrapper(tmp_path, monkeypatch):
    """Exercise the complete rollout/receipt/event join that exposed the canary defect."""
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    codex=tmp_path/'.cortex-dev/.codex';codex.mkdir(parents=True)
    rollout=tmp_path/'coordinator.jsonl'
    report='r_0123456789ab'
    def entry(stamp, payload):
        return json.dumps(dict(timestamp=stamp, type='response_item', payload=payload))
    rollout.write_text('\n'.join([
        entry('2026-09-06T00:00:00.100Z', dict(
            type='custom_tool_call', call_id='call-1',
            input=f'tools.mcp__cortex__read_report({{report_id:"{report}"}})')),
        entry('2026-09-06T00:00:00.101Z', dict(
            type='custom_tool_call_output', call_id='call-1', output='Script completed')),
        entry('2026-09-06T00:00:00.102Z', dict(
            type='item_completed', item=dict(
                type='McpToolCall', server='cortex', tool='read_report', status='completed',
                arguments={'report_id':report},
                result={'structuredContent': {'report_id':report, 'kind':'report'}}))),
    ])+'\n')
    with sqlite3.connect(codex/'state_5.sqlite') as db:
        db.execute('CREATE TABLE threads(id TEXT,rollout_path TEXT,agent_role TEXT,model TEXT,reasoning_effort TEXT,created_at INTEGER,cwd TEXT)')
        db.execute('CREATE TABLE thread_spawn_edges(parent_thread_id TEXT,child_thread_id TEXT)')
        db.execute('INSERT INTO threads VALUES (?,?,?,?,?,?,?)',
                   ('parent',str(rollout),None,'gpt-5.6-luna','high',1788652800,'/fixture'))
    events=tmp_path/'events';events.mkdir()
    (events/'server.jsonl').write_text(json.dumps(dict(
        time_ns=1788652800102000000, operation='read_report', outcome='success',
        thread_id='parent', parent_thread_id=None, report_id=report,
        document_kind='report', page='start', requested_limit=4000))+'\n')
    rows=OBSERVER['observed_tool_calls'](dict(workdir='/fixture',started_at=1788652800,
                                              thread_created_since=1788652800,
                                              events=str(events)))
    reads=[row for row in rows if row.get('tool')=='mcp__cortex__read_report']
    assert len(reads)==1 and reads[0]['server_observed'] is True
    assert OBSERVER['call_policy_violations'](rows)==[]


def test_mcp_event_correlation_fails_closed_for_ambiguous_or_mismatched_identity():
    base=dict(thread_id='worker', tool='mcp__cortex__read_report', outcome='success',
              server_observed=False, host_receipt_observed=True,
              host_receipt_outcome='success',
              host_receipt_timestamp='2026-09-06T00:43:36.243Z',
              requested_report_id='r_0123456789ab')
    event=dict(outcome='success', report_id='r_0123456789ab')
    assert OBSERVER['event_call_candidate']([dict(base),dict(base)],
        1788655416243491179,event['report_id'],event) is None
    mismatched=dict(base, requested_report_id='r_deadbeefdead')
    assert OBSERVER['event_call_candidate']([mismatched],
        1788655416243491179,event['report_id'],event) is None


def test_mcp_event_does_not_pair_same_template_twenty_milliseconds_late():
    receipt=dict(thread_id='worker',tool='mcp__cortex__create_draft',outcome='success',
                 server_observed=False,host_receipt_observed=True,
                 host_receipt_outcome='success',
                 host_receipt_timestamp='2026-09-06T00:43:12.050Z',
                 template='verification',timestamp='2026-09-06T00:43:12.048Z',
                 completed_timestamp='2026-09-06T00:43:12.049Z')
    event=dict(outcome='success',template='verification')
    event_time=1788655392050000000+20_000_000
    assert OBSERVER['event_call_candidate']([receipt],event_time,event=event) is None


def test_mcp_correlation_retains_typo_and_missing_server_receipt_failures():
    typo=dict(thread_id='worker',role='worker',tool='mcp__create_draft',outcome='error',
              argument_digest='typo',host_receipt_observed=False,
              host_receipt_outcome='error')
    missing_server=dict(thread_id='worker',role='worker',tool='mcp__cortex__create_draft',
                        outcome='success',server_observed=False,host_receipt_observed=True,
                        host_receipt_outcome='success',argument_digest='receipt',
                        template='verification')
    flags={(row['tool'],row['violation'])
           for row in OBSERVER['call_policy_violations']([typo,missing_server])}
    assert ('mcp__create_draft','mcp_tool_error_observed') not in flags
    assert ('mcp__create_draft','mcp_call_missing_host_receipt') not in flags
    assert ('mcp__cortex__create_draft','cortex_call_missing_server_event') in flags


def test_failed_cortex_mcp_call_remains_an_acceptance_critical_error():
    failed=dict(thread_id='worker',role='worker',tool='mcp__cortex__create_draft',
                outcome='error',server_observed=True,host_receipt_observed=True,
                host_receipt_outcome='error',argument_digest='cortex-error')
    violations=OBSERVER['call_policy_violations']([failed])
    assert ('mcp__cortex__create_draft','mcp_tool_error_observed') in {
        (row['tool'],row['violation']) for row in violations}
    assert OBSERVER['orchestration_policy_violations'](violations)


def test_paired_write_report_observation_does_not_look_like_post_publication_work():
    paired=dict(thread_id='worker',role='worker',tool='mcp__cortex__write_report',
                outcome='success',server_observed=True,host_receipt_observed=True,
                host_receipt_outcome='success',report_id='r_6b6185b46456',
                draft_id='d_83b87f53d614')
    flags={row['violation'] for row in OBSERVER['call_policy_violations']([paired])}
    assert 'cortex_call_missing_server_event' not in flags
    assert 'worker_tool_after_successful_write_report' not in flags


def test_write_report_result_receipt_extracts_exact_publication_identity():
    receipt=OBSERVER['mcp_receipt_metadata']({
        'type':'McpToolCall','server':'cortex','tool':'write_report','status':'completed',
        'result':{'structuredContent':{'report_id':'r_6b6185b46456'},'content':[]},
    })
    assert receipt['report_id']=='r_6b6185b46456'
    row={'thread_id':'worker','tool':receipt['tool'],'report_id':receipt['report_id']}
    row['canonical_call_key']=OBSERVER['canonical_mcp_call_key'](row)
    event={'thread_id':'worker','operation':'write_report','report_id':'r_6b6185b46456'}
    assert OBSERVER['event_call_candidate']([row],0,event=event) is row


def test_pipeline_editions_have_distinct_call_keys_and_sha256_prefix_is_normalized():
    base={'thread_id':'parent','tool':'mcp__cortex__write_report','report_id':'r_0123456789ab'}
    first=dict(base,draft_id='d_0123456789ab')
    second=dict(base,draft_id='d_deadbeefdead')
    for row in (first,second):row['canonical_call_key']=OBSERVER['canonical_mcp_call_key'](row)
    assert first['canonical_call_key']!=second['canonical_call_key']
    event=dict(second,operation='write_report')
    assert OBSERVER['event_call_candidate']([first,second],0,event=event) is second
    receipt=OBSERVER['mcp_receipt_metadata']({
        'type':'McpToolCall','server':'cortex','tool':'read_report','status':'completed',
        'result':{'structuredContent':{'report_id':base['report_id'],
          'artifacts':[{'reference':'retry.py','version':'sha256:'+'a'*64}]}}})
    assert receipt['reported_artifact_bindings'][0][1]=='a'*64


def test_report_pages_match_exact_identity_and_disjoint_intervals():
    base=dict(thread_id='parent',tool='mcp__cortex__read_report',report_id='r_0123456789ab')
    key=OBSERVER['canonical_mcp_call_key'](base)
    first=dict(base,canonical_call_key=key,timestamp='2026-01-01T00:00:00Z',
               completed_timestamp='2026-01-01T00:00:01Z')
    second=dict(base,canonical_call_key=key,timestamp='2026-01-01T00:00:02Z',
                completed_timestamp='2026-01-01T00:00:03Z')
    event=dict(base,operation='read_report')
    when=1767225600*1_000_000_000+500_000_000
    choose=OBSERVER['event_call_candidate']
    assert choose([first,second],when,event=event) is first
    assert choose([first,dict(first)],when,event=event) is None
    assert choose([first,second],when+1_000_000_000,event=event) is None
    assert choose([first,second],when,event={**event,'report_id':'r_deadbeefdead'}) is None


def test_publication_identity_ignores_nested_governance_but_not_conflicting_roots():
    published={'report_id':'r_6b6185b46456',
               'governance':{'report_id':'r_0123456789ab','mode':'light'}}
    item={'type':'McpToolCall','server':'cortex','tool':'write_report','status':'completed',
          'result':{'structuredContent':published,
                    'content':[{'type':'text','text':json.dumps(published)}]}}
    receipt=OBSERVER['mcp_receipt_metadata'](item)
    assert receipt['report_id']==published['report_id']
    row=dict(receipt,thread_id='worker')
    row['canonical_call_key']=OBSERVER['canonical_mcp_call_key'](row)
    event={'thread_id':'worker','operation':'write_report','report_id':published['report_id']}
    assert OBSERVER['event_call_candidate']([row],0,event=event) is row
    item['result']['content'][0]['text']=json.dumps({'report_id':'r_deadbeefdead'})
    assert 'report_id' not in OBSERVER['mcp_receipt_metadata'](item)
    item['result']={'structuredContent':{'governance':published['governance']}}
    assert 'report_id' not in OBSERVER['mcp_receipt_metadata'](item)


def test_skill_read_accepts_bounded_readonly_batches_and_rejects_shell_escape(tmp_path,monkeypatch):
    monkeypatch.setattr(Path,'home',lambda:tmp_path)
    cache=trusted_cache(tmp_path/'.cortex-dev/.codex/plugins/cache/cortex/cortex')
    skill=cache/'skills/worker-general/SKILL.md'
    skill.parent.mkdir(parents=True)
    skill.write_bytes((ROOT/'plugins/cortex/skills/worker-general/SKILL.md').read_bytes())
    reference=cache/'skills/worker-backend-dev/references/report-publication.md'
    reference.parent.mkdir(parents=True)
    reference.write_bytes((ROOT/'plugins/cortex/skills/worker-backend-dev/references/report-publication.md').read_bytes())
    check=OBSERVER['skill_instruction_read']
    positive=("pwd && printf '%s\\n' '--- skill ---' && echo 'reading exact skill' && sed -n '1,240p' "
              +str(skill))
    assert check('exec_command',json.dumps({'cmd':positive}))
    assert check('exec_command',json.dumps({'cmd':f"pwd && sed -n '1,260p' {reference}"}))
    for command in (
        f"cat \"$SKILL_PATH\" && sed -n '1,240p' {skill}",
        f"sed -n '1,240p' {skill} > /tmp/skill-copy",
        f"find {skill.parents[3]} -name SKILL.md",
        f"cat {skill.parents[2]}/agents/general.toml && sed -n '1,240p' {skill}",
        f"sed -n '1,240p' {skill} && sed -n '1,120p' .codex/cortex/t_1/pipeline.md",
        f"sed -n '1,240p' {skill} && cat solution.py",
        f"sed -n '1,240p' {skill} && touch {tmp_path/'changed'}",
        f"printf '%s\\n' \"$(pwd)\" && sed -n '1,240p' {skill}",
    ):
        assert not check('exec_command',json.dumps({'cmd':command}))


def test_skill_read_accepts_newline_reference_batches_but_rejects_mixed_or_quoted_paths(tmp_path,monkeypatch):
    monkeypatch.setattr(Path,'home',lambda:tmp_path)
    cache=trusted_cache(tmp_path/'.cortex-dev/.codex/plugins/cache/cortex/cortex')
    skill=cache/'skills/worker-general/SKILL.md'
    skill.parent.mkdir(parents=True)
    skill.write_bytes((ROOT/'plugins/cortex/skills/worker-general/SKILL.md').read_bytes())
    reference=cache/'skills/worker-backend-dev/references/report-publication.md'
    reference.parent.mkdir(parents=True)
    reference.write_bytes((ROOT/'plugins/cortex/skills/worker-backend-dev/references/report-publication.md').read_bytes())
    check=OBSERVER['skill_instruction_read']
    newline_batch=(f"sed -n '1,240p' {skill}\n"
                   f"sed -n '1,260p' {reference}")
    arguments=json.dumps({'cmd':newline_batch})
    assert check('exec_command',arguments)
    assert 'forbidden_plugin_or_cache_access' not in set(
        OBSERVER['call_policy_flags']('exec_command',arguments,'general','/fixture'))

    mixed=(f"sed -n '1,240p' {skill}\n"
           "cat .codex/cortex/t_1/pipeline.md")
    mixed_arguments=json.dumps({'cmd':mixed})
    assert not check('exec_command',mixed_arguments)
    assert 'forbidden_plugin_or_cache_access' in set(
        OBSERVER['call_policy_flags']('exec_command',mixed_arguments,'general','/fixture'))

    quoted_newline=json.dumps({'cmd':f"sed -n '1,240p' '{skill}\n{reference}'"})
    assert not check('exec_command',quoted_newline)
    assert 'forbidden_plugin_or_cache_access' in set(
        OBSERVER['call_policy_flags']('exec_command',quoted_newline,'general','/fixture'))


def test_mixed_skill_read_and_project_discovery_has_scoped_cache_policy(tmp_path,monkeypatch):
    monkeypatch.setattr(Path,'home',lambda:tmp_path)
    cache=trusted_cache(tmp_path/'.cortex-dev/.codex/plugins/cache/cortex/cortex')
    skill=cache/'skills/worker-general/SKILL.md'
    skill.parent.mkdir(parents=True)
    skill.write_bytes((ROOT/'plugins/cortex/skills/worker-general/SKILL.md').read_bytes())
    mixed=(f"sed -n '1,240p' {skill} && printf '%s\\n' '--- files ---' && "
           "rg --files -g '!*.pyc' . | sort")
    arguments=json.dumps({'cmd':mixed})
    assert not OBSERVER['skill_instruction_read']('exec_command',arguments)
    assert 'forbidden_plugin_or_cache_access' not in set(
        OBSERVER['call_policy_flags']('exec_command',arguments,'general','/fixture'))
    assert 'coordinator_forbidden_tool' in set(
        OBSERVER['call_policy_flags']('exec_command',arguments,'coordinator','/fixture'))
    assert 'forbidden_plugin_or_cache_access' not in set(
        OBSERVER['call_policy_flags']('exec_command',json.dumps({'cmd':f"echo '{skill}'"}),
                                     'general','/fixture'))
    semicolon_mixed=mixed.replace(' && ', '; ')
    assert not OBSERVER['skill_instruction_read']('exec_command',json.dumps({'cmd':semicolon_mixed}))
    assert 'forbidden_plugin_or_cache_access' not in set(
        OBSERVER['call_policy_flags']('exec_command',json.dumps({'cmd':semicolon_mixed}),
                                     'general','/fixture'))
    assert 'coordinator_forbidden_tool' in set(
        OBSERVER['call_policy_flags']('exec_command',json.dumps({'cmd':semicolon_mixed}),
                                     'coordinator','/fixture'))

    for command in (
        f"sed -n '1,240p' {skill} && cat {skill.parents[2]}/agents/general.toml",
        f"sed -n '1,240p' {skill}; cat {skill.parents[2]}/agents/general.toml",
        f"sed -n '1,240p' {skill} && cat .codex/cortex/t_1/pipeline.md",
        f"sed -n '1,240p' {skill} && python3 -c \"open('{skill}').read()\"",
        f"sed -n '1,240p' {skill} && printf '%s' \"$(cat '{skill}')\"",
        f"sed -n '1,240p' {skill} && {skill.parents[2]}/scripts/cortex.py",
    ):
        arguments=json.dumps({'cmd':command})
        assert 'forbidden_plugin_or_cache_access' in set(
            OBSERVER['call_policy_flags']('exec_command',arguments,'general','/fixture'))

    for command in ('cat .mcp.json', f"sed -n '1,240p' {skill} && cat .mcp.json"):
        assert 'forbidden_plugin_or_cache_access' in set(
            OBSERVER['call_policy_flags']('exec_command',json.dumps({'cmd':command}),
                                         'general',str(skill.parents[2])))


def test_live_config_layers_luna_policy_without_dropping_existing_instructions():
    source='developer_instructions = "keep me"\nmodel = "gpt-6-astra"\nmodel_reasoning_effort = "xhigh"\n\n[agents]\ndefault_subagent_model = "gpt-6-astra"\n'
    parsed=tomllib.loads(OBSERVER['live_test_config'](source))
    assert parsed['model']=='gpt-5.6-luna'
    assert parsed['model_reasoning_effort']=='high'
    assert parsed['agents']['default_subagent_model']=='gpt-5.6-luna'
    assert parsed['developer_instructions'].startswith('keep me\n\n')
    assert 'overrides any Cortex recommendation' in parsed['developer_instructions']


def test_desktop_launcher_and_observer_require_explicit_spawn_route_fields():
    instructions=OBSERVER['LIVE_DEVELOPER_INSTRUCTIONS']
    assert 'Every spawn_agent call must explicitly include' in instructions
    assert 'model="gpt-5.6-luna"' in instructions
    assert 'reasoning_effort="medium" or "high"' in instructions
    assert 'fork_turns="none"' in instructions
    assert 'must not use functions.exec, exec_command, terminal, or another shell route' in instructions
    assert 'attributable complete' in instructions
    assert 'nested success never excuses truncation' in instructions
    assert 'ordinary read-only means selected by the model' in instructions
    assert "sed -n '1,$p' <literal-path>" not in instructions
    assert 'malformed EOF marker' not in instructions

    cli=runpy.run_path(str(ROOT/'scripts/cortex-live-smoke'),run_name='live_instruction_fixture')
    assert 'Each role may inspect its exact advertised SKILL.md first' in cli['LIVE_DEVELOPER_INSTRUCTIONS']
    assert 'does not permit directory scans or private plugin/cache/candidate/registry probing' in cli['LIVE_DEVELOPER_INSTRUCTIONS']
    assert 'ordinary read-only means selected by the model' in cli['LIVE_DEVELOPER_INSTRUCTIONS']
    assert "sed -n '1,$p' <literal-path>" not in cli['LIVE_DEVELOPER_INSTRUCTIONS']
    assert 'malformed EOF marker' not in cli['LIVE_DEVELOPER_INSTRUCTIONS']
    forbidden=OBSERVER['call_policy_flags'](
        'exec_command',json.dumps({'cmd':'ls /fixture/.cortex-dev/.codex/plugins/cache'}),
        'coordinator','/fixture')
    assert 'forbidden_plugin_or_cache_access' in forbidden
    static=OBSERVER['call_policy_flags'](
        'exec_command',json.dumps({'cmd':"rg -n 'plugins/cache' README.md"}),
        'coordinator','/fixture')
    assert 'forbidden_plugin_or_cache_access' not in static

    base={'message':'gAAAAA'+'x'*100,'task_name':'author_spec',
          'model':'gpt-5.6-luna','reasoning_effort':'high','fork_turns':'none'}
    fields=OBSERVER['safe_call_metadata']('spawn_agent',json.dumps(base))
    row={'thread_id':'parent','role':'coordinator','tool':'spawn_agent','outcome':'success',**fields,
         'worker_route_evidence':'native_child_and_complete_skill',
         'spawned_thread_id':'child','native_child_path_verified':True,
         'validated_parent_edge':True,'worker_skill_receipt':'complete_success',
         'observed_worker_profile':'technical_writer','observed_worker_model':'gpt-5.6-luna',
         'observed_worker_effort':'high'}
    assert OBSERVER['call_policy_violations']([row])==[]

    missing=dict(base);missing.pop('model')
    missing_fields=OBSERVER['safe_call_metadata']('spawn_agent',json.dumps(missing))
    missing_row=dict(row);missing_row.pop('requested_model',None);missing_row.update(missing_fields)
    assert 'worker_assignment_policy_unverified' in {
        item['violation'] for item in OBSERVER['call_policy_violations']([missing_row])}


def test_live_config_replaces_complete_multiline_value_and_preserves_other_bytes():
    source=('title = "unchanged\\\\path"\n'
            "developer_instructions = '''first line\nsecond \\ line\n'''\n"
            'model = "gpt-6-astra"\n'
            'model_reasoning_effort = "max"\n\n'
            '[unrelated]\nvalue = "keep\\\\n-literal"\n\n'
            '[agents]\ndefault_subagent_model = "gpt-6-astra"\n'
            'workers = 3\n')
    updated=OBSERVER['live_test_config'](source)
    parsed=tomllib.loads(updated)
    assert 'first line\nsecond \\ line' in parsed['developer_instructions']
    assert parsed['unrelated']=={'value':'keep\\n-literal'}
    assert parsed['title']=='unchanged\\path'
    assert '[unrelated]\nvalue = "keep\\\\n-literal"\n' in updated
    assert 'workers = 3\n' in updated
    assert updated.count('developer_instructions = ')==1


def test_live_audit_rejects_heavy_models_and_wrong_effort():
    policy=OBSERVER['call_policy_violations']
    violation='live_model_policy_violation'
    coordinator=dict(thread_id='root',role='coordinator',tool='native_user_input',outcome='success',
                     model='gpt-5.6-luna',reasoning_effort='high')
    assert violation not in {row['violation'] for row in policy([coordinator])}
    for change in ({'model':'gpt-6-astra'},{'reasoning_effort':'medium'}):
        assert violation in {row['violation'] for row in policy([{**coordinator,**change}])}
    accepted_spawn=dict(thread_id='root',role='coordinator',tool='spawn_agent',outcome='success',
                        model='gpt-5.6-luna',reasoning_effort='high',
                        requested_model='gpt-5.6-luna',requested_reasoning_effort='medium')
    assert violation not in {row['violation'] for row in policy([accepted_spawn])}
    spawn={**accepted_spawn,'requested_model':'gpt-6-astra'}
    assert violation in {row['violation'] for row in policy([spawn])}
    worker={**coordinator,'thread_id':'child','parent_thread_id':'root','role':'worker',
            'reasoning_effort':'low'}
    assert violation in {row['violation'] for row in policy([worker])}
def test_task_local_revision_and_exact_worker_receipt_are_fail_closed():
    revisions=OBSERVER['task_source_revision_violations']
    # Parent revision is provenance only; a newly-created child starts at one.
    assert revisions([
        {'thread_id':'parent','source_revision':5},
        {'thread_id':'child','source_revision':1},
        {'thread_id':'child','source_revision':1},
    ]) == []
    assert revisions([
        {'thread_id':'pipeline-parent','task_id':'parent-task','source_revision':9},
        {'thread_id':'fresh-root','task_id':'fresh-task','parent_thread_id':None,
         'source_revision':1},
        {'thread_id':'fresh-worker','task_id':'fresh-task','parent_thread_id':'fresh-root',
         'source_revision':1},
    ]) == []
    assert revisions([
        {'thread_id':'child','source_revision':2},
        {'thread_id':'child','source_revision':1},
    ]) == [{'index':1,'thread_id':'child',
            'violation':'task_source_revision_nonmonotonic'}]
    legal=OBSERVER['call_policy_violations']([
        {'thread_id':'parent','role':'coordinator','tool':'mcp__cortex__create_task',
         'outcome':'success','source_revision':5},
        {'thread_id':'child','parent_thread_id':'parent','role':'coordinator',
         'tool':'mcp__cortex__create_task','outcome':'success','source_revision':1},
    ])
    assert not [row for row in legal if row['violation'].startswith('task_source_revision')]
    illegal=OBSERVER['call_policy_violations']([
        {'thread_id':'child','role':'coordinator','tool':'mcp__cortex__create_task',
         'outcome':'success','source_revision':2},
        {'thread_id':'child','role':'coordinator','tool':'mcp__cortex__list_reports',
         'outcome':'success','source_revision':1},
    ])
    assert any(row['violation']=='task_source_revision_nonmonotonic' for row in illegal)
    task_identity_illegal=OBSERVER['call_policy_violations']([
        {'thread_id':'child-a','task_id':'child-task','role':'coordinator',
         'tool':'mcp__cortex__create_task','outcome':'success','source_revision':2},
        {'thread_id':'child-b','task_id':'child-task','role':'coordinator',
         'tool':'mcp__cortex__list_reports','outcome':'success','source_revision':1},
    ])
    assert any(row['violation']=='task_source_revision_nonmonotonic'
               and row['task_id']=='child-task' for row in task_identity_illegal)
    audit=OBSERVER['classify_audit_findings'](failures=[],hook_failures=[],host_failures=[],
        policy_violations=illegal,open_sessions=[],open_cells=[])
    assert audit['evidence_valid'] is False
    receipt=OBSERVER['worker_skill_assignment_receipt']
    assert receipt('explorer','explorer',True)=='complete_exact_assigned'
    assert receipt('explorer',None,True) is None
    assert receipt(None,None,True) is None
