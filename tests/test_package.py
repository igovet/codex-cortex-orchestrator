from pathlib import Path
import json
import subprocess
import sys
import tomllib
import pytest

from cortex_package import PLUGIN, ROOT, payload_digest, validate
from generate_agent_profiles import (
    check as check_agent_profiles,
    expected_agent_references,
    expected_profiles,
    expected_worker_references,
)


def test_stamped_package_and_profiles():
    assert validate().startswith('1.15.7+codex.sha256.')
    assert len(list((PLUGIN/'agents').glob('*.toml')))==22
    payload=json.loads((PLUGIN/'runtime-payload.json').read_text())['files']
    assert all((PLUGIN/path).is_file() for path in payload)
    expected_runtime={
        Path(path).name for path in payload
        if Path(path).parent==Path('scripts/cortex_runtime') and path.endswith('.py')
    }
    assert {p.name for p in (PLUGIN/'scripts/cortex_runtime').glob('*.py')}==expected_runtime


def test_native_profiles_keep_roles_and_use_mcp_task_documents():
    check_agent_profiles()
    assert all(path.read_bytes() == body for path, body in expected_profiles().items())
    assert len(expected_agent_references())==3
    assert len(expected_worker_references())==66
    for path in (PLUGIN/'agents').glob('*.toml'):
        profile=tomllib.loads(path.read_text())
        instructions=profile['developer_instructions']
        headings={
            line[3:] for line in instructions.splitlines() if line.startswith('## ')
        }
        assert {
            'Role and responsibility',
            'Assignment contract',
            'Evidence and verification',
            'Report and handoff',
            'Report class selection',
            'Specialist workflow',
            'Quality criteria',
            'Recovery',
        } <= headings
        assert len(instructions) < 12_000
        assert 'page of at most\n4,000 characters' in instructions
        assert 'not a total context limit' in instructions
        assert 'references/report-publication.md' in instructions
        assert 'references/code-and-evidence.md' in instructions
        assert 'references/interactive-resources.md' in instructions
        assert 'first and only general catalogue query' not in instructions
        assert 'final `__`-delimited segment' not in instructions
        assert '.codex/plugins/' not in instructions
    control=(PLUGIN/'skills/cortex-control/SKILL.md').read_text()
    assert '[report example catalogue](references/index.md)' in control


def test_source_check_is_read_only():
    before=payload_digest(PLUGIN)
    result=subprocess.run([str(ROOT/'scripts/sync-cortex.sh'),'--check'],capture_output=True,text=True)
    assert result.returncode==0 and payload_digest(PLUGIN)==before


def test_desktop_helper_can_submit_one_literal_prompt_file():
    source=(ROOT/'scripts/cortex-desktop-dev').read_text()
    assert "add_argument('--prompt-file',type=Path)" in source
    assert "add_argument('--data-dir',type=Path)" not in source
    assert "codex://threads/new?" in source
    assert "urllib.parse.urlencode({'path':str(workdir),'prompt':prompt})" in source
    assert "prompt_supplied=prompt is not None" in source
    assert "store=str(store)" in source
    assert "CORTEX_DATA_DIR=str(" not in source
    assert "for key in ['profile','events']:" in source
    assert 'def configure_workspace_network():' in source
    assert "network_access = true" in source
    assert 'restore_workspace_network' in source
    assert "sub.add_parser('audit')" in source
    assert "sub.add_parser('calls')" in source
    assert 'def observed_tool_calls(state):' in source
    assert 'def nested_tool_invocations(source):' in source
    assert 'wrapper_argument_digest=digest' in source
    assert 'def safe_call_metadata(tool,arguments):' in source
    assert 'def open_command_sessions(rows):' in source
    assert 'def open_exec_cells(rows):' in source
    assert 'def call_policy_violations(rows):' in source
    assert 'def classify_host_failures(rows):' in source
    assert 'def tool_error_history(rows):' in source
    assert 'def orchestration_error_history(rows):' in source
    assert 'def orchestration_policy_violations(violations):' in source
    assert 'def native_agent_result_metadata(payload):' in source
    assert "def observed_outcome(raw,tool=None,arguments=''):" in source
    assert 'def mcp_receipt_metadata(item):' in source
    assert "argument_digest=digest" in source
    assert "outcome='truncated'" in source
    assert "host_failures=" in source
    assert "tool_error_history=error_history" in source
    assert "failures or orchestration_errors or orchestration_host_failures" in source
    assert "open_sessions=open_command_sessions(host_rows)" in source
    assert "open_cells=open_exec_cells(host_rows)" in source
    assert "policy_violations=call_policy_violations(host_rows)" in source
    assert "role=thread['agent_role'] or 'coordinator'" in source
    assert "row.get('outcome')!='success'" in source
    assert "Path(directory).glob('*.jsonl')" in source
    assert "sub.add_parser('send')" in source
    assert "owner.stdout.strip()==str(pid)" in source
    assert 'def desktop_thread_ids(workdir,started_at):' in source
    assert 'def wait_desktop_window(pid):' in source
    assert "[xdotool,'key','--window',window,'ctrl+Return']" in source
    assert "state['thread_id']=created.pop()" in source
    orchestrator=(PLUGIN/'skills/orchestrator/SKILL.md').read_text()
    companion=sum(
        len((PLUGIN/'skills'/name/'SKILL.md').read_text())
        for name in (
            'orchestrator',
            'coordinator-communication',
            'tool-discipline',
            'content-safety',
            'context-compaction',
        )
    )
    assert companion < 25_000
    assert '## Durable task and pipeline' in orchestrator
    assert '## Choose the smallest useful work graph' in orchestrator
    assert '## Model and effort' in orchestrator
    assert '## Recovery after compaction or restart' in orchestrator
    assert 'A 4,000-character limit is one page, never a total context limit.' in orchestrator
    assert 'Never reassign or\nduplicate work because of a wait timeout' in orchestrator
    assert 'Ordinary work defaults to `gpt-5.6-luna`' in orchestrator
    assert 'Sol is never an implementation route' in orchestrator
    assert 'Answer short questions directly' in orchestrator
    assert 'applicable artifact skill' in orchestrator
    assert 'non-code artifacts' in (PLUGIN/'agent-sources/worker-protocol.md').read_text()


def test_cli_helper_audits_all_thread_calls_with_shared_observer():
    source=(ROOT/'scripts/cortex-live-smoke').read_text()
    assert "add_argument('--data-dir',type=Path)" not in source
    assert "store=str(store)" in source
    assert "CORTEX_DATA_DIR='+" not in source
    assert 'def user_prompt_receipts(data,prompt):' in source
    assert 'prompt submission produced no exact user-turn receipt' in source
    assert "sub.add_parser('audit')" in source
    assert "s=sub.add_parser('calls');s.add_argument('--limit',type=int)" in source
    assert "runpy.run_path(str(ROOT/'scripts/cortex-desktop-dev')" in source
    assert "policy_violations=policy" in source
    assert "error_history=observer['tool_error_history'](host_rows)" in source
    assert "tool_error_history=error_history" in source
    assert "orchestration_errors=observer['orchestration_error_history'](host_rows)" in source
    assert "failures or orchestration_errors or orchestration_host_failures" in source
    assert "open_sessions=open_sessions" in source
    assert "open_cells=open_cells" in source
    assert "sandbox_workspace_write.network_access=true" in source


def test_live_helpers_derive_only_the_canonical_project_store(tmp_path):
    import runpy
    cli=runpy.run_path(str(ROOT/'scripts/cortex-live-smoke'),run_name='transport')
    desktop=runpy.run_path(str(ROOT/'scripts/cortex-desktop-dev'),run_name='observer')
    project=tmp_path.resolve(strict=True)
    expected=project/'.codex/cortex/cortex.sqlite3'
    assert cli['project_store'](project)==expected
    assert desktop['project_store'](project)==expected
    assert not expected.exists()
    with pytest.raises(RuntimeError,match='canonical project'):
        cli['project_store'](Path('relative-project'))


def test_live_helpers_remove_external_store_override_from_child_environments(monkeypatch,tmp_path):
    import runpy
    helper=runpy.run_path(str(ROOT/'scripts/cortex-desktop-dev'),run_name='observer')
    monkeypatch.setenv('CORTEX_DATA_DIR',str(tmp_path/'external'))
    env=helper['environment'](tmp_path/'profile',tmp_path/'events')
    assert 'CORTEX_DATA_DIR' not in env
    cli=(ROOT/'scripts/cortex-live-smoke').read_text()
    assert "'CODEX_THREAD_ID','CORTEX_DATA_DIR']" in cli
    assert "CORTEX_DATA_DIR='+" not in cli


def test_cli_smoke_resume_requires_same_existing_project_store_and_rejects_legacy_state(tmp_path):
    import runpy
    cli=runpy.run_path(str(ROOT/'scripts/cortex-live-smoke'),run_name='transport')
    project=tmp_path.resolve(strict=True)
    store=project/'.codex/cortex/cortex.sqlite3'
    store.parent.mkdir(parents=True,mode=0o700)
    store.write_bytes(b'SQLite format 3\0');store.chmod(0o600)
    retained={'workdir':str(project),'store':str(store)}
    assert cli['resumed_project_store'](project,retained)==store

    other=tmp_path/'other';other.mkdir()
    with pytest.raises(RuntimeError,match='workdir differs'):
        cli['resumed_project_store'](other,retained)
    external=tmp_path/'external.sqlite3';external.write_bytes(b'old');external.chmod(0o600)
    with pytest.raises(RuntimeError,match='store differs'):
        cli['resumed_project_store'](project,retained|{'store':str(external)})
    assert external.read_bytes()==b'old'
    with pytest.raises(RuntimeError,match='removed external data-directory'):
        cli['resumed_project_store'](project,{'workdir':str(project),'data':str(tmp_path/'old-data')})
    store.unlink()
    with pytest.raises(RuntimeError,match='unavailable'):
        cli['resumed_project_store'](project,retained)


def test_live_helper_stops_preserve_the_project_store(monkeypatch,tmp_path):
    import runpy
    project=tmp_path/'project';project.mkdir()
    store=project/'.codex/cortex/cortex.sqlite3'
    store.parent.mkdir(parents=True,mode=0o700)
    store.write_bytes(b'project database');store.chmod(0o600)

    cli=runpy.run_path(str(ROOT/'scripts/cortex-live-smoke'),run_name='transport')
    cli_globals=cli['main'].__globals__;cli_state=tmp_path/'cli-state'
    monkeypatch.setitem(cli_globals,'STATE',cli_state)
    cli_globals['private']()
    events=cli_state/'events';events.mkdir();(events/'event.jsonl').write_text('{}\n')
    (cli_state/'capture.txt').write_text('capture')
    (cli_state/'session.json').write_text(json.dumps({'workdir':str(project),'store':str(store)}))
    monkeypatch.setitem(cli_globals,'tmux',lambda *args,**kwargs: None)
    monkeypatch.setattr(sys,'argv',['cortex-live-smoke','stop'])
    cli['main']()
    assert store.read_bytes()==b'project database'
    assert json.loads((cli_state/'last.json').read_text())['store']==str(store)
    assert not (cli_state/'session.json').exists() and not events.exists()

    desktop=runpy.run_path(str(ROOT/'scripts/cortex-desktop-dev'),run_name='observer')
    desktop_globals=desktop['main'].__globals__;desktop_state=tmp_path/'desktop-state'
    desktop_state.mkdir(mode=0o700)
    config=tmp_path/'config.toml';config.write_text('modified')
    backup=desktop_state/'config.toml.before-live-test';backup.write_text('original');backup.chmod(0o600)
    profile=desktop_state/'profile';events=desktop_state/'events';profile.mkdir();events.mkdir()
    session={
        'pid':999999999,'start':'missing','profile':str(profile),'events':str(events),
        'store':str(store),'workdir':str(project),'isolated_config':str(config),
    }
    (desktop_state/'session.json').write_text(json.dumps(session))
    monkeypatch.setitem(desktop_globals,'STATE',desktop_state)
    monkeypatch.setitem(desktop_globals,'CONFIG_BACKUP',backup)
    monkeypatch.setitem(desktop_globals,'identity',lambda _: None)
    monkeypatch.setattr(sys,'argv',['cortex-desktop-dev','stop'])
    desktop['main']()
    assert store.read_bytes()==b'project database'
    assert config.read_text()=='original'
    assert not (desktop_state/'session.json').exists()


def test_desktop_call_outcome_classifies_mcp_errors_and_truncation():
    import runpy
    helper=runpy.run_path(str(ROOT/'scripts/cortex-desktop-dev'),run_name='cortex_desktop_dev')
    classify=helper['observed_outcome']
    outcome,code,_=classify([{'type':'input_text','text':'Script completed'},
                              {'type':'input_text','text':'{"isError":true,"error":"invalid_arguments"}'}])
    assert (outcome,code)==('error','invalid_arguments')
    assert classify([{'type':'input_text','text':'Warning: truncated output'}])[:2]==('truncated',None)
    assert classify([{'type':'input_text','text':'Script completed'}])[:2]==('success',None)
    assert classify([{'type':'input_text','text':'{"output":"ok"}'}],'exec_command')[:2]==('unverified',None)
    assert classify([{'type':'input_text','text':'{"exit_code":0,"output":"ok"}'}],'exec_command')[:2]==('success',None)
    assert classify([{'type':'input_text','text':'{"exit_code":2,"output":"bad"}'}],'exec_command')[:2]==('error','command_exit_2')
    assert classify([{'type':'input_text','text':'{"exit_code":0,"output":"fatal: not a git repository\\n"}'}],'exec_command')[:2]==('error','command_output_error')
    assert classify([{'type':'input_text','text':'{"exit_code":0,"output":"npm error code EAI_AGAIN\\n"}'}],'exec_command')[:2]==('error','command_output_error')
    assert classify([{'type':'input_text','text':'{"exit_code":0}{"exit_code":2}'}],'functions.exec')[:2]==('error','command_exit_2')
    flags=helper['call_policy_flags']
    assert flags(
        'apply_patch',
        'await tools.apply_patch(String.raw`*** Update File: /tmp/project/.cortex/draft.md`)',
        'planner','/tmp/project',
    )==[]
    assert flags(
        'apply_patch',
        'await tools.apply_patch("*** Update File: /tmp/project/.cortex/draft.md\\n+Use `code`")',
        'planner','/tmp/project',
    )==[]
    assert 'forbidden_cortex_draft_deletion' in flags(
        'apply_patch','*** Delete File: /tmp/project/.cortex/draft.md','planner','/tmp/project'
    )
    history=helper['tool_error_history']([
        {'timestamp':'1','thread_id':'w','role':'planner','tool':'mcp__cortex__create_draft',
         'outcome':'error','error_code':'invalid_arguments','argument_digest':'bad'},
        {'timestamp':'2','thread_id':'w','role':'planner','tool':'mcp__cortex__create_draft',
         'outcome':'success','argument_digest':'good'},
    ])
    assert history==[{'timestamp':'1','thread_id':'w','role':'planner',
                     'tool':'mcp__cortex__create_draft','error_code':'invalid_arguments',
                     'argument_digest':'bad'}]
    mixed_errors=[
        {'timestamp':'1','thread_id':'w','role':'planner','tool':'mcp__cortex__create_draft',
         'outcome':'error','error_code':'invalid_arguments','argument_digest':'bad'},
        {'timestamp':'2','thread_id':'w','role':'frontend_dev','tool':'js',
         'outcome':'error','error_code':'browser_detached','argument_digest':'browser'},
    ]
    assert len(helper['tool_error_history'](mixed_errors))==2
    assert [row['tool'] for row in helper['orchestration_error_history'](mixed_errors)]==[
        'mcp__cortex__create_draft'
    ]
    native_result=helper['native_agent_result_metadata']
    assert native_result({'type':'agent_message','author':'/root/plan','content':[{
        'type':'input_text','text':'Message Type: FINAL_ANSWER\nPayload:\nr_123456789abc ready'
    }]})=={'agent_path':'/root/plan','outcome':'success','report_id':'r_123456789abc'}
    assert native_result({'type':'agent_message','author':'/root/plan','content':[{
        'type':'input_text','text':"Message Type: FINAL_ANSWER\nPayload:\nAgent errored: Selected model is at capacity.\nThis agent's turn failed."
    }]})=={'agent_path':'/root/plan','outcome':'error','error_code':'agent_model_capacity'}
    stopped_history=helper['tool_error_history']([
        {'timestamp':'1','thread_id':'worker','tool':'exec_command','outcome':'running',
         'session_ids':[42],'intent_digest':'serve'},
        {'timestamp':'2','thread_id':'worker','tool':'write_stdin','outcome':'stopped',
         'requested_session_ids':[42],'intent_digest':'serve'},
        {'timestamp':'3','thread_id':'worker','tool':'command_execution','outcome':'error',
         'argument_digest':'execution','intent_digest':'serve','error_code':'command_exit_130'},
    ])
    assert stopped_history==[]
    receipt=helper['mcp_receipt_metadata']({
        'type':'McpToolCall','server':'cortex','tool':'create_draft','status':'failed',
        'result':{'isError':True,'content':[{'type':'text','text':'{"error":"invalid_arguments"}'}]},
    })
    assert receipt['tool']=='mcp__cortex__create_draft'
    assert receipt['host_status']=='failed'
    assert (receipt['host_receipt_outcome'],receipt['host_error_code'])==('error','invalid_arguments')
    command_outcome=helper['command_execution_outcome']
    assert command_outcome('completed',0,'fatal: quoted source text')==('success',None)
    assert command_outcome('completed',0,'npm error code appears in a diff')==('success',None)
    assert command_outcome('completed',1,'')==('error','command_exit_1')
    assert command_outcome('completed',None,'curl: (7) refused')==('error','command_output_error')
    unavailable=helper['mcp_receipt_metadata']({
        'type':'McpToolCall','server':'cua_repl','tool':'js','status':'failed',
        'result':{'isError':True,'content':[{'type':'text','text':'Capability is not available: visibility'}]},
    })
    assert unavailable['host_error_code']=='capability_unavailable'
    assert classify('Script running with cell ID 12','functions.exec')[:2]==('running',None)
    identifiers=helper['requested_identifiers']
    assert identifiers('{"session_id":47281}','session_id',numeric=True)==[47281]
    assert identifiers("{cell_id:'cell_12'}",'cell_id')==['cell_12']
    assert helper['safe_call_metadata']('spawn_agent',json.dumps({
        'task_name':'frontend','model':'gpt-5.6-terra',
        'reasoning_effort':'high','fork_turns':'none','message':'private assignment',
    }))=={
        'task_name':'frontend','requested_model':'gpt-5.6-terra',
        'requested_reasoning_effort':'high','fork_turns':'none',
    }
    assert classify([{'type':'input_text','text':'{"exit_code":130,"output":"^C"}'}],
                    'write_stdin','chars:"\\u0003"')[:2]==('stopped',None)
    assert classify([{'type':'input_text','text':'{"exit_code":1,"output":"^C"}'}],
                    'write_stdin','chars:"\\u0003"')[:2]==('stopped',None)
    invocations=helper['nested_tool_invocations']('text(await tools.exec_command({cmd:"ok"})); tools.mcp__cortex__read_report({limit:4000})')
    assert [name for name,_,_ in invocations]==['exec_command','mcp__cortex__read_report']
    intent=helper['command_intent_digest']
    assert intent('{cmd:"npm install && npm run build"}')==intent('{cmd:"npm run build"}')
    assert intent('{"cmd":"npm install && npm run build"}')==intent('{"cmd":"npm run build"}')
    assert intent('{cmd:"python3 -m http.server 8000"}')==intent('{cmd:"python3 -m http.server 8765 --bind 127.0.0.1"}')
    raw_intent=helper['raw_command_intent_digest']
    assert raw_intent('curl --fail http://localhost:8000/styles.css')==raw_intent('curl http://127.0.0.1:8765/styles.css')
    sessions=helper['open_command_sessions']([
        {'tool':'exec_command','outcome':'running','session_ids':[42]},
        {'tool':'write_stdin','outcome':'stopped','requested_session_ids':[42]},
        {'tool':'exec_command','outcome':'running','session_ids':[43]},
    ])
    assert sessions==[43]
    sessions=helper['open_command_sessions']([
        {'tool':'exec_command','outcome':'running','session_ids':[42]},
        {'tool':'write_stdin','outcome':'error','requested_session_ids':[42]},
    ])
    assert sessions==[]
    cells=helper['open_exec_cells']([
        {'tool':'functions.exec','outcome':'running','cell_ids':['12']},
        {'tool':'wait','outcome':'success','requested_cell_ids':['12']},
        {'tool':'functions.exec','outcome':'running','cell_ids':['13']},
    ])
    assert cells==['13']
    policy=helper['call_policy_violations']([
        {'timestamp':'1','thread_id':'worker','role':'technical_writer','tool':'exec_command','outcome':'success','policy_flags':['forbidden_plugin_or_cache_access']},
        {'timestamp':'2','thread_id':'worker','role':'technical_writer','tool':'mcp__cortex__write_report','outcome':'success'},
        {'timestamp':'3','thread_id':'worker','role':'technical_writer','tool':'send_message_to_thread','outcome':'error'},
    ])
    assert [item['violation'] for item in policy]==[
        'forbidden_plugin_or_cache_access','worker_tool_after_successful_write_report'
    ]
    duplicate=helper['call_policy_violations']([
        {'thread_id':'worker','role':'explorer','tool':'tool_catalogue_search','outcome':'success'},
        {'thread_id':'worker','role':'explorer','tool':'mcp__cortex__list_reports','outcome':'success'},
        {'thread_id':'worker','role':'explorer','tool':'mcp__cortex__read_report','outcome':'success','document_kind':'pipeline','page':'start'},
        {'thread_id':'worker','role':'explorer','tool':'mcp__cortex__read_report','outcome':'success','document_kind':'pipeline','page':'start'},
    ])
    assert duplicate==[]
    legacy_advisory=helper['call_policy_violations']([{
        'thread_id':'worker','role':'explorer','tool':'js','outcome':'success',
        'policy_flags':['browser_before_local_url_ready','batched_browser_mutations'],
    }])
    assert legacy_advisory==[]
    active_draft=helper['call_policy_violations']([
        {'thread_id':'worker','role':'qa_engineer','tool':'exec_command','outcome':'running','session_ids':[17]},
        {'thread_id':'worker','role':'qa_engineer','tool':'mcp__cortex__create_draft','outcome':'success'},
        {'thread_id':'worker','role':'qa_engineer','tool':'write_stdin','outcome':'stopped','requested_session_ids':[17]},
    ])
    assert any(item['violation']=='worker_draft_created_with_open_command_session'
               for item in active_draft)
    active_cell=helper['call_policy_violations']([
        {'thread_id':'worker','role':'frontend_dev','tool':'functions.exec','outcome':'running','cell_ids':['12']},
        {'thread_id':'worker','role':'frontend_dev','tool':'exec_command','outcome':'success'},
        {'thread_id':'worker','role':'frontend_dev','tool':'wait','outcome':'success','requested_cell_ids':['12']},
    ])
    assert any(item['violation']=='tool_called_before_exec_cell_terminal'
               for item in active_cell)
    missing_event=helper['call_policy_violations']([
        {'thread_id':'worker','role':'qa_engineer','tool':'mcp__cortex__create_draft',
         'outcome':'error','error_code':'invalid_arguments','server_observed':False,
         'host_receipt_observed':True},
    ])
    assert missing_event[0]['violation']=='cortex_call_missing_server_event'
    missing_receipt=helper['call_policy_violations']([
        {'thread_id':'worker','role':'qa_engineer','tool':'mcp__cortex__create_draft',
         'argument_digest':'call','outcome':'error','server_observed':True},
    ])
    assert missing_receipt[0]['violation']=='mcp_call_missing_host_receipt'
    corrected_mcp_error=helper['call_policy_violations']([
        {'thread_id':'worker','role':'planner','tool':'mcp__cortex__create_draft',
         'outcome':'error','error_code':'invalid_arguments','server_observed':True,
         'host_receipt_observed':True},
        {'thread_id':'worker','role':'planner','tool':'mcp__cortex__create_draft',
         'outcome':'success','server_observed':True,'host_receipt_observed':True,
         'template':'planning'},
    ])
    assert [item['violation'] for item in corrected_mcp_error]==['mcp_tool_error_observed']
    flags=helper['call_policy_flags']('exec_command','{cmd:"ps -eo pid,args"}','build_verification','/tmp/project')
    assert flags==['forbidden_global_process_probe']
    flags=helper['call_policy_flags']('exec_command','{cmd:"npm run build",max_output_tokens:4000}','frontend_dev','/tmp/project')
    assert flags==[]
    flags=helper['call_policy_flags']('exec_command','{cmd:"npm run build",max_output_tokens:16000}','frontend_dev','/tmp/project')
    assert flags==[]
    flags=helper['call_policy_flags']('exec_command','{cmd:"python3 -c \\\"print(open(\\\'.git/HEAD\\\').read())\\\""}','explorer','/tmp/project')
    assert flags==['unguarded_optional_git_marker_read']
    browser_meta=helper['browser_call_metadata']
    assert browser_meta('{"code":"await cua.getTab(\\"123\\", {browser: \\"1\\"})"}')=={
        'browser_action':'attach_tab'
    }
    assert browser_meta('{"code":"await cua.createBrowserTab(\\"1\\", \\"http://127.0.0.1:5173/\\")"}')=={
        'browser_action':'create_tab','browser_origin':'http://localhost:5173'
    }
    assert helper['local_http_origin']('curl http://127.0.0.1:5173/path')=='http://localhost:5173'
    assert helper['browser_mutation_count']('await tab.click(1); await tab.setValue(2, "x")')==2
    assert helper['draft_call_metadata'](
        'apply_patch','*** Update File: /tmp/project/.cortex/draft-reports/d_1.md'
    )=={'cortex_draft_edit':True}
    assert helper['draft_call_metadata'](
        'apply_patch','*** Update File: .cortex/draft-reports/d_2.md'
    )=={'cortex_draft_edit':True}
    browser_policy=helper['call_policy_violations']([
        {'thread_id':'worker','role':'build_verification','tool':'command_execution','outcome':'success','command_family':'curl','local_http_origin':'http://localhost:5173'},
        {'thread_id':'worker','role':'build_verification','tool':'js','outcome':'success','browser_action':'attach_tab'},
        {'thread_id':'worker','role':'build_verification','tool':'js','outcome':'error','browser_action':'create_tab','browser_origin':'http://localhost:5173','browser_mutations':2},
        {'thread_id':'worker','role':'build_verification','tool':'js','outcome':'success','browser_action':'create_tab','browser_origin':'http://localhost:8000'},
    ])
    assert browser_policy==[]
    assert helper['orchestration_policy_violations'](browser_policy)==[]
    premature=helper['call_policy_violations']([
        {'thread_id':'worker','role':'qa_engineer','tool':'js','outcome':'success','browser_action':'inventory'}
    ])
    assert premature==[]
    coordinator_policy=helper['call_policy_violations']([
        {'thread_id':'root','role':'coordinator','tool':'spawn_agent','outcome':'success'},
        {'thread_id':'root','role':'coordinator','tool':'wait_agent','outcome':'success'},
        {'thread_id':'root','role':'coordinator','tool':'list_agents','outcome':'success'},
        {'thread_id':'root','role':'coordinator','tool':'send_message','outcome':'success'},
    ])
    assert [item['violation'] for item in coordinator_policy]==[
        'coordinator_status_probe_after_wait','coordinator_unsolicited_message_after_wait'
    ]
    duplicate_owner=helper['call_policy_violations']([
        {'thread_id':'root','role':'coordinator','tool':'spawn_agent','outcome':'success',
         'agent_type':'frontend_dev','model':'gpt-5.6-luna'},
        {'thread_id':'root','role':'coordinator','tool':'wait_agent','outcome':'success'},
        {'thread_id':'root','role':'coordinator','tool':'spawn_agent','outcome':'success',
         'agent_type':'frontend_dev'},
    ])
    assert duplicate_owner==[]  # The same profile does not prove resource overlap.
    released_owner=helper['call_policy_violations']([
        {'thread_id':'root','role':'coordinator','tool':'spawn_agent','outcome':'success',
         'agent_type':'frontend_dev'},
        {'thread_id':'worker','parent_thread_id':'root','role':'frontend_dev',
         'tool':'mcp__cortex__write_report','outcome':'success'},
        {'thread_id':'root','role':'coordinator','tool':'spawn_agent','outcome':'success',
         'agent_type':'frontend_dev'},
    ])
    assert not any(item['violation']=='coordinator_duplicate_active_mutation_owner'
                   for item in released_owner)
    model_route=helper['call_policy_violations']([
        {'thread_id':'root','role':'coordinator','tool':'spawn_agent','outcome':'success',
         'agent_type':'planner','model':'gpt-5.6-luna'},
    ])
    assert model_route==[]  # Model selection is evidence-based, not a profile gate.
    preview_policy=helper['call_policy_violations']([
        {'thread_id':'root','role':'coordinator','tool':'mcp__cortex__write_report',
         'outcome':'success','summary_characters':141},
    ])
    assert preview_policy==[]
    template_policy=helper['call_policy_violations']([
        {'thread_id':'planner','role':'planner','tool':'mcp__cortex__create_draft',
         'outcome':'success','template':'implementation'},
    ])
    assert template_policy==[]
    lifecycle_policy=helper['call_policy_violations']([
        {'thread_id':'worker','parent_thread_id':'root','role':'planner',
         'tool':'native_agent_result','outcome':'success'},
        {'thread_id':'worker2','parent_thread_id':'root','role':'explorer',
         'tool':'native_agent_result','outcome':'success','report_id':'r_123456789abc'},
    ])
    assert [item['violation'] for item in lifecycle_policy]==[
        'worker_final_without_report_id','worker_final_with_unobserved_report'
    ]
    assert helper['orchestration_policy_violations'](lifecycle_policy)==lifecycle_policy
    unresolved,resolved=helper['classify_host_failures']([
        {'thread_id':'w','tool':'exec_command','argument_digest':'same','outcome':'error'},
        {'thread_id':'w','tool':'exec_command','argument_digest':'other','outcome':'error'},
        {'thread_id':'w','tool':'exec_command','argument_digest':'same','outcome':'success'},
    ])
    assert [row['argument_digest'] for row in unresolved]==['other']
    assert [row['argument_digest'] for row in resolved]==['same']
    unresolved,resolved=helper['classify_host_failures']([
        {'thread_id':'w','tool':'exec_command','argument_digest':'combined','intent_digest':'build','outcome':'error'},
        {'thread_id':'w','tool':'exec_command','argument_digest':'single','intent_digest':'build','outcome':'success'},
    ])
    assert unresolved==[] and resolved[0]['argument_digest']=='combined'
    unresolved,resolved=helper['classify_host_failures']([
        {'thread_id':'w','tool':'exec_command','argument_digest':'blocked','intent_digest':'serve','outcome':'error'},
        {'thread_id':'w','tool':'exec_command','argument_digest':'allowed','intent_digest':'serve','outcome':'running','session_ids':[77]},
        {'thread_id':'w','tool':'write_stdin','argument_digest':'stop','outcome':'success','requested_session_ids':[77]},
    ])
    assert unresolved==[] and resolved[0]['argument_digest']=='blocked'
    unresolved,resolved=helper['classify_host_failures']([
        {'thread_id':'w','tool':'wait','argument_digest':'cell-6','intent_digest':'serve','outcome':'error'},
        {'thread_id':'w','tool':'exec_command','argument_digest':'allowed','intent_digest':'serve','outcome':'running','session_ids':[78]},
        {'thread_id':'w','tool':'write_stdin','argument_digest':'stop','outcome':'stopped','requested_session_ids':[78]},
    ])
    assert unresolved==[] and resolved[0]['argument_digest']=='cell-6'
    unresolved,resolved=helper['classify_host_failures']([
        {'thread_id':'audit','parent_thread_id':'root','tool':'exec_command','argument_digest':'blocked','intent_digest':'serve','outcome':'error'},
        {'thread_id':'verify','parent_thread_id':'root','tool':'exec_command','argument_digest':'allowed','intent_digest':'serve','outcome':'success'},
    ])
    assert unresolved==[] and resolved[0]['thread_id']=='audit'
    unresolved,resolved=helper['classify_host_failures']([
        {'thread_id':'w','tool':'mcp__cortex__create_draft','argument_digest':'bad',
         'outcome':'error','error_code':'invalid_arguments'},
        {'thread_id':'w','tool':'mcp__cortex__create_draft','argument_digest':'good',
         'outcome':'success'},
    ])
    assert unresolved==[]
    assert resolved[0]['resolution']=='later_same_tool_success'
    unresolved,resolved=helper['classify_host_failures']([
        {'thread_id':'w','tool':'functions.exec','argument_digest':'large-read',
         'outcome':'covered_by_nested','wrapper_outcome':'truncated'},
        {'thread_id':'w','tool':'exec_command','argument_digest':'large-sed',
         'intent_digest':'read-source','outcome':'covered_by_command_execution',
         'wrapper_outcome':'truncated'},
        {'thread_id':'w','tool':'command_execution','argument_digest':'large-sed-host',
         'intent_digest':'read-source','command_family':'sed','outcome':'success'},
    ])
    assert [(row['tool'],row['effective_outcome']) for row in unresolved]==[
        ('functions.exec','truncated')
    ]
    assert [(row['tool'],row['effective_outcome']) for row in resolved]==[
        ('exec_command','truncated')
    ]
    unresolved,resolved=helper['classify_host_failures']([
        {'thread_id':'w','tool':'functions.exec','argument_digest':'poll-wrapper',
         'result_digest':'failed-poll','outcome':'covered_by_nested',
         'wrapper_outcome':'error'},
        {'thread_id':'w','tool':'write_stdin','argument_digest':'poll',
         'result_digest':'failed-poll','intent_digest':'build','outcome':'error'},
        {'thread_id':'w','tool':'exec_command','argument_digest':'retry',
         'intent_digest':'build','outcome':'success'},
    ])
    assert unresolved==[]
    assert {row['tool'] for row in resolved}=={'functions.exec','write_stdin'}


def test_sync_cannot_install_without_isolated_entrypoint():
    import os
    env=dict(os.environ);env.pop('CORTEX_DEV_OWNER_HOME',None)
    result=subprocess.run([str(ROOT/'scripts/sync-cortex.sh')],capture_output=True,text=True,env=env)
    assert result.returncode!=0


def test_markdown_local_links():
    import re
    paths=[ROOT/'README.md',ROOT/'SECURITY.md',ROOT/'PRIVACY.md',*list((ROOT/'docs').rglob('*.md')),*list((PLUGIN/'skills').rglob('*.md')),*list((PLUGIN/'agents').glob('*.toml'))]
    for path in paths:
        for link in re.findall(r'\]\(([^)]+)\)',path.read_text()):
            if '://' in link or link.startswith('#'):continue
            assert (path.parent/link.split('#')[0]).exists(),(path,link)


def test_coordinator_cannot_drop_required_checks_on_environment_failure():
    text=(PLUGIN/'skills/orchestrator/SKILL.md').read_text()
    assert 'Preserve required checks until the user changes scope.' in text
    assert 'Match completion evidence to the user\'s outcome.' in text
    assert 'exact pipeline draft edit' in ' '.join(text.split())
    assert 'One worker owns each shared or coupled mutation surface.' in text
    assert 'unavailable attachment as an explicit gap' in text


def test_shared_worker_protocol_routes_rare_interactive_procedure():
    protocol=(PLUGIN/'agent-sources/worker-protocol.md').read_text()
    reference=(PLUGIN/'agent-sources/references/interactive-resources.md').read_text()
    assert '[interactive resources](references/interactive-resources.md)' in protocol
    assert 'Never inspect\nglobal processes, ports, tabs or devices' in reference
    assert 'select, create or reuse only owned resources' in reference
    assert 'Stop or close only sessions created by this assignment.' in reference
    assert 'Accessibility element numbers belong only to the snapshot' not in protocol


def test_native_instruction_boundaries_cover_observed_live_failures():
    coordinator=(PLUGIN/'skills/orchestrator/SKILL.md').read_text()
    communication=(PLUGIN/'skills/coordinator-communication/SKILL.md').read_text()
    discipline=(PLUGIN/'skills/tool-discipline/SKILL.md').read_text()
    publication=(PLUGIN/'agent-sources/references/report-publication.md').read_text()
    assert "language of the user's latest own prose" in coordinator
    assert "user's latest own prose" in communication
    assert 'Reuse retained results while relevant state is unchanged.' in discipline
    assert 'Re-read after user steering' in discipline
    assert 'A wrapper must expose the full nested' in discipline
    assert 'never replay a mutation for reassurance' in discipline
    assert 'Use the draft reader only after compaction, restart' in publication
    assert 'Correct deterministic edit or argument errors once' in publication
    assert 'successful publication is the final tool action' in publication
    assert len(discipline) < 4_000


def test_cli_uncertain_submission_never_sends_again(monkeypatch,tmp_path):
    import runpy
    import sys
    import types
    helper=runpy.run_path(str(ROOT/'scripts/cortex-live-smoke'),run_name='cortex_transport_test')
    main=helper['main']
    namespace=main.__globals__
    sent=[]
    prompt=tmp_path/'prompt.txt';prompt.write_text('An ordinary task\n\n  Preserve indentation and  two spaces.\n')
    monkeypatch.setattr(sys,'argv',['cortex-live-smoke','send','--prompt-file',str(prompt)])
    monkeypatch.setitem(namespace,'state',lambda: {})
    monkeypatch.setitem(namespace,'save',lambda _: None)
    monkeypatch.setitem(namespace,'user_prompt_receipts',lambda *_: 0)
    monkeypatch.setitem(namespace,'tmux',lambda *args,**kwargs: sent.append(args))
    monkeypatch.setitem(namespace,'time',types.SimpleNamespace(sleep=lambda _: None,time=lambda: 123))
    import pytest
    with pytest.raises(RuntimeError,match='inspect the composer'):
        main()
    assert sum(call[0]=='paste-buffer' and '-p' in call for call in sent)==1
    assert next(call[-1] for call in sent if call[0]=='set-buffer')=='An ordinary task\n\n  Preserve indentation and  two spaces.'
    assert sum(call[-1]=='Enter' for call in sent)==1
    assert all('C-u' not in call for call in sent)


def test_resumed_cli_observes_existing_thread_without_replaying_old_calls(monkeypatch,tmp_path):
    import runpy
    import sqlite3
    from datetime import datetime,timezone
    monkeypatch.setenv('HOME',str(tmp_path))
    home=tmp_path/'.cortex-dev/.codex';home.mkdir(parents=True)
    rollout=tmp_path/'rollout.jsonl'
    def entry(at,payload):
        return json.dumps(dict(timestamp=datetime.fromtimestamp(at,timezone.utc).isoformat(),
                               type='response_item',payload=payload))
    rollout.write_text('\n'.join([
        entry(110,dict(type='custom_tool_call',call_id='old',name='functions.exec',input='text(1);')),
        entry(111,dict(type='custom_tool_call_output',call_id='old',output='Script completed')),
        entry(210,dict(type='message',role='user',content=[dict(type='input_text',text='Continue this task')])),
        entry(211,dict(type='custom_tool_call',call_id='new',name='functions.exec',input='text(2);')),
        entry(212,dict(type='custom_tool_call_output',call_id='new',output='Script completed')),
    ])+'\n')
    with sqlite3.connect(home/'state_5.sqlite') as db:
        db.execute('CREATE TABLE threads (id,rollout_path,agent_role,model,reasoning_effort,created_at,cwd)')
        db.execute('CREATE TABLE thread_spawn_edges (parent_thread_id,child_thread_id)')
        db.execute('INSERT INTO threads VALUES (?,?,?,?,?,?,?)',('root',str(rollout),None,'model','medium',100,'/project'))
    state=dict(workdir='/project',started_at=200,thread_created_since=100,resumed=True,events=str(tmp_path/'events'))
    cli=runpy.run_path(str(ROOT/'scripts/cortex-live-smoke'),run_name='transport')
    assert cli['user_prompt_receipts'](state,'Continue this task')==1
    assert cli['user_prompt_receipts'](state,'Continue  this task')==0
    assert cli['user_prompt_receipts'](state,'Continue\nthis task')==0
    desktop=runpy.run_path(str(ROOT/'scripts/cortex-desktop-dev'),run_name='observer')
    rows=desktop['observed_tool_calls'](state)
    assert len(rows)==1
    assert rows[0]['thread_id']=='root' and rows[0]['outcome']=='success'
    assert rows[0]['timestamp']==datetime.fromtimestamp(211,timezone.utc).isoformat()


def test_marketplace_skills_deliver_profiles_and_progressive_references():
    from generate_agent_profiles import (
        expected_agent_references,
        expected_skills,
        expected_worker_references,
    )
    skills=expected_skills()
    agent_references=expected_agent_references()
    references=expected_worker_references()
    assert len(skills)==22
    assert len(agent_references)==3
    assert len(references)==66
    assert all(path.read_bytes()==body for path,body in agent_references.items())
    assert all(path.read_bytes()==body for path,body in references.items())
    for path,body in skills.items():
        assert path.read_bytes()==body
        name=path.parent.name.removeprefix('worker-')
        profile=tomllib.loads((PLUGIN/'agents'/f'{name}.toml').read_text())
        skill_body=body.decode().split('---\n',2)[2].lstrip()
        profile_body=skill_body.removesuffix('\n<!-- END OF COMPLETE CORTEX WORKER SKILL -->\n')
        assert profile_body==profile['developer_instructions']
    prepare=(ROOT/'scripts/prepare_codex.py').read_text()
    assert 'cortex_setup.py' not in prepare


def test_marketplace_audit_extracts_only_known_role_from_assignment():
    import runpy
    helper=runpy.run_path(str(ROOT/'scripts/cortex-desktop-dev'),run_name='observer')
    extract=helper['assigned_worker_profile']
    assert extract('$cortex:worker-backend-dev Implement a bounded change.')=='backend_dev'
    assert extract('$cortex:worker-unknown secret') is None
    assert extract('$cortex:worker-backend-dev $cortex:worker-debugger') is None
    metadata=helper['safe_call_metadata']('spawn_agent',json.dumps({'message':'$cortex:worker-technical-writer Private content'}))
    assert metadata=={'assigned_profile':'technical_writer'}


def test_skill_instruction_exception_does_not_allow_cache_exploration(tmp_path,monkeypatch):
    import runpy
    helper=runpy.run_path(str(ROOT/'scripts/cortex-desktop-dev'),run_name='observer')
    monkeypatch.setattr(Path,'home',lambda:tmp_path)
    path=tmp_path/'.cortex-dev/.codex/plugins/cache/cortex/cortex/version/skills/worker-technical-writer/SKILL.md'
    path.parent.mkdir(parents=True);path.write_text('instructions')
    check=helper['worker_skill_read']
    assert check('exec_command',json.dumps({'cmd':f'cat {path}'}))
    assert check('exec_command','{cmd:'+json.dumps(f"sed -n '1,240p' {path}")+'}')
    assert not check('exec_command',json.dumps({'cmd':f'cat {path}; touch /tmp/unrelated'}))
    assert not check('exec_command',json.dumps({'cmd':f'cat {path.parent.parent.parent}/profiles.json'}))
    assert not check('apply_patch',json.dumps({'cmd':f'cat {path}'}))


def test_labelled_command_status_is_a_receipt_but_stdout_alone_is_not():
    import runpy
    check=runpy.run_path(str(ROOT/'scripts/cortex-desktop-dev'),run_name='observer')['observed_outcome']
    assert check([{'text':'file content'},{'text':'exit_status=0'}],'exec_command')[0]=='success'
    assert check([{'text':'file content'},{'text':'exit_code=0'}],'exec_command')[0]=='success'
    assert check([{'text':'file content'},{'text':'exit_status=2'}],'exec_command')[:2]==('error','command_exit_2')
    assert check([{'text':'file content with exit_status=0'}],'exec_command')[0]=='unverified'


def test_skill_read_allows_only_an_exit_preserving_suffix(tmp_path,monkeypatch):
    import runpy
    helper=runpy.run_path(str(ROOT/'scripts/cortex-desktop-dev'),run_name='observer')
    monkeypatch.setattr(Path,'home',lambda:tmp_path)
    path=tmp_path/'.cortex-dev/.codex/plugins/cache/cortex/cortex/version/skills/worker-general/SKILL.md'
    path.parent.mkdir(parents=True);path.write_text('instructions')
    suffix='; rc=$?; printf \'\\n__EXIT_STATUS__=%s\\n\' "$rc"; exit "$rc"'
    check=helper['worker_skill_read']
    combined=f"wc -l {path} && sed -n '1,240p' {path}"
    assert check('exec_command',json.dumps({'cmd':combined}))
    assert check('exec_command',json.dumps({'cmd':f"sed -n '1,240p' {path}"+suffix}))
    assert check('exec_command',json.dumps({'cmd':f'wc -l {path}'+suffix}))
    assert check('exec_command',json.dumps({'cmd':f'cat {path}; s=$?; echo "__EXIT_STATUS__=$s"; exit $s'}))
    assert check('exec_command',json.dumps({'cmd':f'cat {path}'+suffix.replace('rc','s').removesuffix('; exit "$s"')}))
    assert not check('exec_command',json.dumps({'cmd':f'cat {path}; s=$?; echo "__EXIT_STATUS__=$other"'}))
    assert not check('exec_command',json.dumps({'cmd':f'cat {path}'+suffix+'; touch /tmp/unrelated'}))
    assert not check('exec_command',json.dumps({'cmd':f"find {path.parents[3]} -name SKILL.md"}))
    assert not check('exec_command',json.dumps({'cmd':f"wc -l {path} && sed -n '1,240p' {path.parent/'references/other.md'}"}))
    assert helper['observed_outcome']([{'text':'__EXEC_EXIT_CODE__=3'}],'exec_command')[:2]==('error','command_exit_3')
    assert helper['is_orchestration_call']({'role':'general','tool':'exec_command','skill_instruction_read':True})
    assert not helper['is_orchestration_call']({'role':'general','tool':'exec_command'})


def test_original_request_audit_rejects_translation_and_lost_formatting():
    import runpy
    h=runpy.run_path(str(ROOT/'scripts/cortex-desktop-dev'),run_name='observer')
    digest=h['original_request_digest']
    request='Сохрани исходные данные.\n\n  command --flag  value'
    assert digest('$cortex:orchestrator '+request)==digest(request+'\n')
    assert digest(request)!=digest(request.replace('  ',' '))
    assert digest(request)!=digest('Preserve the original data.')
    rows=[{'thread_id':'root','role':'coordinator','tool':'mcp__cortex__create_task','outcome':'success','original_request_preserved':False}]
    violations=h['orchestration_policy_violations'](h['call_policy_violations'](rows))
    assert [r['violation'] for r in violations]==['coordinator_original_request_changed']


def test_new_task_allows_bounded_discovery_before_pipeline_publication():
    import runpy
    check=runpy.run_path(str(ROOT/'scripts/cortex-desktop-dev'),run_name='observer')['call_policy_violations']
    def row(tool,**extra):return dict(thread_id='root',role='coordinator',tool=tool,outcome='success',**extra)
    begin=[row('mcp__cortex__create_task'),row('mcp__cortex__create_draft',template='pipeline')]
    spawn=row('spawn_agent',fork_turns='none',assigned_profile='general')
    assert check(begin+[spawn])==[]
    assert check(begin+[row('mcp__cortex__write_report'),spawn])==[]
    assert check([row('mcp__cortex__read_report',document_kind='pipeline'),spawn])==[]


def test_desktop_request_fidelity_requires_observed_editor_provenance():
    import runpy
    h=runpy.run_path(str(ROOT/'scripts/cortex-desktop-dev'),run_name='observer')
    digest=h['original_request_digest'];allowed=h['delivered_request_digests']
    source='Task requirements:\n1. Keep invoice_id and  two spaces.\n2. Do not translate.'
    delivered='$cortex:orchestrator '+source.replace(':\n',':\n\n').replace('_',r'\_')
    state=dict(original_request_sha256=digest(source),desktop_editor_source_sha256=digest(h['desktop_editor_source'](source)))
    assert digest(delivered.replace(r'\_','_')) in allowed(state,delivered)
    assert digest(delivered) in allowed(state,delivered)
    assert digest(delivered) not in allowed(dict(original_request_sha256=digest(source)),delivered)
    for changed in [delivered.replace('  ',' '),delivered+' extra',delivered.replace('invoice','customer')]:
        assert allowed(state,changed)=={digest(source)}


def test_coordinator_may_reply_once_to_unresolved_worker_handoff():
    import runpy
    check=runpy.run_path(str(ROOT/'scripts/cortex-desktop-dev'),run_name='observer')['call_policy_violations']
    wait=dict(thread_id='root',role='coordinator',tool='wait_agent',outcome='success')
    handoff=dict(thread_id='worker',parent_thread_id='root',role='general',tool='native_agent_result',outcome='success')
    reply=dict(thread_id='root',role='coordinator',tool='followup_task',outcome='success')
    flag='coordinator_unsolicited_message_after_wait'
    assert flag not in [x['violation'] for x in check([wait,handoff,reply])]
    assert [x['violation'] for x in check([wait,handoff,reply,reply])].count(flag)==1


def test_live_audit_user_steering_allows_delivery_after_wait():
    import runpy
    check=runpy.run_path(str(ROOT/'scripts/cortex-desktop-dev'),run_name='observer')['call_policy_violations']
    root=dict(thread_id='root',role='coordinator',outcome='success')
    rows=[root|dict(tool='wait_agent'),root|dict(tool='native_user_input'),
          root|dict(tool='send_message'),root|dict(tool='send_message')]
    assert not check(rows)
    flags=check(rows+[root|dict(tool='wait_agent'),root|dict(tool='send_message')])
    assert [r['violation'] for r in flags]==['coordinator_unsolicited_message_after_wait']
