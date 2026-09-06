import json
from pathlib import Path
import runpy
import sqlite3
import tomllib


ROOT=Path(__file__).resolve().parents[1]
OBSERVER=runpy.run_path(str(ROOT/'scripts/cortex-desktop-dev'))
EVAL=runpy.run_path(str(ROOT/'scripts/cortex_eval.py'))


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
    assert ('mcp__create_draft','mcp_tool_error_observed') in flags
    assert ('mcp__create_draft','mcp_call_missing_host_receipt') in flags
    assert ('mcp__cortex__create_draft','cortex_call_missing_server_event') in flags


def test_paired_write_report_observation_does_not_look_like_post_publication_work():
    paired=dict(thread_id='worker',role='worker',tool='mcp__cortex__write_report',
                outcome='success',server_observed=True,host_receipt_observed=True,
                host_receipt_outcome='success',report_id='r_6b6185b46456',
                draft_id='d_83b87f53d614')
    flags={row['violation'] for row in OBSERVER['call_policy_violations']([paired])}
    assert 'cortex_call_missing_server_event' not in flags
    assert 'worker_tool_after_successful_write_report' not in flags


def test_skill_read_accepts_bounded_readonly_batches_and_rejects_shell_escape(tmp_path,monkeypatch):
    monkeypatch.setattr(Path,'home',lambda:tmp_path)
    skill=tmp_path/'.cortex-dev/.codex/plugins/cache/cortex/cortex/version/skills/worker-general/SKILL.md'
    skill.parent.mkdir(parents=True);skill.write_text('instructions')
    reference=tmp_path/'.cortex-dev/.codex/plugins/cache/cortex/cortex/version/skills/worker-backend-dev/references/report-publication.md'
    reference.parent.mkdir(parents=True);reference.write_text('instructions')
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


def test_mixed_skill_read_and_project_discovery_has_scoped_cache_policy(tmp_path,monkeypatch):
    monkeypatch.setattr(Path,'home',lambda:tmp_path)
    skill=tmp_path/'.cortex-dev/.codex/plugins/cache/cortex/cortex/version/skills/worker-general/SKILL.md'
    skill.parent.mkdir(parents=True);skill.write_text('instructions')
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
