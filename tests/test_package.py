from pathlib import Path
import json
import runpy
import shutil
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
    assert validate().startswith('1.15.9+codex.sha256.')
    assert len(list((PLUGIN/'agents').glob('*.toml')))==23
    payload=json.loads((PLUGIN/'runtime-payload.json').read_text())['files']
    assert all((PLUGIN/path).is_file() for path in payload)
    expected_runtime={
        Path(path).name for path in payload
        if Path(path).parent==Path('scripts/cortex_runtime') and path.endswith('.py')
    }
    assert {p.name for p in (PLUGIN/'scripts/cortex_runtime').glob('*.py')}==expected_runtime


def test_mcp_advertises_isolated_gateway_dependency_environment():
    mcp = json.loads((PLUGIN/'.mcp.json').read_text())['mcpServers']['cortex']
    assert set(mcp['env_vars']) == {'CORTEX_OBSERVATION_DIR', 'CORTEX_DEPENDENCY_DIR'}


@pytest.mark.parametrize('ambient', [None, '/tmp/ambient-stale-dependency'])
def test_desktop_environment_overwrites_dependency_path_after_preparation(tmp_path, monkeypatch, ambient):
    import os
    import runpy
    owner = tmp_path/'owner'
    dependency = owner/'.cortex-dev/.codex/cortex-deps'
    dependency.mkdir(parents=True, mode=0o700)
    dependency.chmod(0o700)
    monkeypatch.setattr(Path, 'home', lambda: owner)
    if ambient is None:
        monkeypatch.delenv('CORTEX_DEPENDENCY_DIR', raising=False)
    else:
        monkeypatch.setenv('CORTEX_DEPENDENCY_DIR', ambient)
    helper = runpy.run_path(str(ROOT/'scripts/cortex-desktop-dev'), run_name='observer')
    env = helper['environment'](tmp_path/'profile', tmp_path/'events')
    assert env['CORTEX_DEPENDENCY_DIR'] == str(dependency)
    assert env['CORTEX_DEPENDENCY_DIR'] != ambient
    assert env['PYTHONDONTWRITEBYTECODE'] == '1'
    assert dependency.stat().st_uid == os.getuid()
    assert dependency.stat().st_mode & 0o077 == 0


def test_desktop_environment_rejects_unsafe_dependency_directory(tmp_path, monkeypatch):
    import runpy
    owner = tmp_path/'owner'
    dependency = owner/'.cortex-dev/.codex/cortex-deps'
    dependency.mkdir(parents=True, mode=0o755)
    dependency.chmod(0o755)
    monkeypatch.setattr(Path, 'home', lambda: owner)
    helper = runpy.run_path(str(ROOT/'scripts/cortex-desktop-dev'), run_name='observer')
    with pytest.raises(RuntimeError, match='private owner-controlled'):
        helper['environment'](tmp_path/'profile', tmp_path/'events')


def test_native_profiles_keep_roles_and_use_mcp_task_documents():
    check_agent_profiles()
    assert all(path.read_bytes() == body for path, body in expected_profiles().items())
    assert len(expected_agent_references())==3
    assert len(expected_worker_references())==69
    for path in (PLUGIN/'agents').glob('*.toml'):
        profile=tomllib.loads(path.read_text())
        instructions=profile['developer_instructions']
        headings={
            line[3:] for line in instructions.splitlines() if line.startswith('## ')
        }
        if profile['name'] == 'senior_consultant':
            assert '## Access boundary' in instructions
            assert 'Do not run commands, tests or project checks' in instructions
            assert 'references/report-publication.md' in instructions
            assert 'You may investigate, implement' not in instructions
            continue
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
        assert 'Private Cortex evidence has a strict boundary:' in instructions
        assert 'mcp__cortex__read_report' in instructions
        assert 'Missing evidence is a stated gap/impact' in instructions
        assert 'first and only general catalogue query' not in instructions
        assert 'final `__`-delimited segment' not in instructions
        assert '.codex/plugins/' not in instructions
    control=(PLUGIN/'skills/cortex-control/SKILL.md').read_text()
    assert '[report example catalogue](references/index.md)' in control
    publication=(PLUGIN/'agent-sources/references/report-publication.md').read_text()
    assert 'crypto.randomUUID()' in publication
    assert 'pass a literal UUID in the tool arguments' in publication
    assert 'supply every required field, including on the initial call' in publication
    assert 'Never probe required\nfields with an empty argument object' in publication
    assert 'Omit `request_key` for' not in publication
    orchestrator=(PLUGIN/'skills/orchestrator/SKILL.md').read_text()
    assert '`request_key` must be a literal UUID or stable key' in orchestrator
    assert '`crypto.randomUUID()` or another runtime generator' in orchestrator
    assert 'After task creation, delegate every project-target mutation, read, hash or' in orchestrator
    assert '`functions.exec`, `exec_command` or terminals for project targets' in orchestrator
    assert 'Only user\nsources and the exact Cortex-issued pipeline draft remain coordinator-readable.' in orchestrator
    pipeline_publication=(PLUGIN/'skills/orchestrator/references/pipeline-publication.md').read_text()
    assert 'When a pipeline mutation schema requires `request_key`' in pipeline_publication
    assert 'ordered `replaceable_markers` list as authoritative' in pipeline_publication
    assert 'replace every exact\nlisted marker in place' in pipeline_publication
    assert 'coordinator publishes a current\n`pipeline` edition, while a worker publishes its own non-pipeline report' in pipeline_publication
    assert 'same draft and the original request key\nand metadata' in pipeline_publication
    assert 'draft or replay an acknowledged publication' in pipeline_publication


def test_coordinator_publication_guidance_closes_observed_draft_failure():
    orchestrator=(PLUGIN/'skills/orchestrator/SKILL.md').read_text()
    reference=(PLUGIN/'skills/orchestrator/references/pipeline-publication.md').read_text()
    assert 'follow its workflow.' in orchestrator
    assert reference.index('Before `write_report`') < reference.index('If `write_report` returns the deterministic')
    assert 'verify that no listed marker (or other exact template\nplaceholder) remains' in reference


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
    assert "disable-app-message-tool" not in source
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
    assert "classification['evidence_integrity_invalidators']" in source
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
    # The opt-in consultant and explicit native-worker tracking boundary add a
    # bounded coordinator packet while retaining the compact companion budget.
    assert companion < 27_500
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


def test_desktop_activation_keeps_strict_success_path_and_records_ownership(monkeypatch,tmp_path):
    import runpy
    import types
    helper=runpy.run_path(str(ROOT/'scripts/cortex-desktop-dev'),run_name='observer')
    calls=[]
    def fake_run(args,**kwargs):
        calls.append(args)
        if args[1]=='windowactivate':
            return types.SimpleNamespace(returncode=0,stdout='',stderr='')
        assert args[1]=='getwindowpid'
        return types.SimpleNamespace(returncode=0,stdout='123\n',stderr='')
    monkeypatch.setattr(helper['subprocess'],'run',fake_run)
    state_file=tmp_path/'session.json'
    state={}
    result=helper['activate_desktop_window']('/usr/bin/xdotool','456',123,state,state_file)
    assert result=={'method':'windowactivate','returncode':0}
    assert [call[1] for call in calls]==['windowactivate','getwindowpid']
    assert json.loads(state_file.read_text())['desktop_activation']==result


def test_desktop_activation_allows_only_exact_desktop_warning_focus_fallback(monkeypatch,tmp_path):
    import runpy
    import types
    helper=runpy.run_path(str(ROOT/'scripts/cortex-desktop-dev'),run_name='observer')
    calls=[]
    def fake_run(args,**kwargs):
        calls.append(args)
        command=args[1]
        if command=='windowactivate':
            return types.SimpleNamespace(returncode=1,stdout='',stderr='XGetWindowProperty[_NET_WM_DESKTOP] failed (code=1)')
        if command=='getwindowpid':
            return types.SimpleNamespace(returncode=0,stdout='123\n',stderr='')
        if command=='windowfocus':
            return types.SimpleNamespace(returncode=0,stdout='',stderr='')
        assert command=='getactivewindow'
        return types.SimpleNamespace(returncode=0,stdout='456\n',stderr='')
    monkeypatch.setattr(helper['subprocess'],'run',fake_run)
    state_file=tmp_path/'session.json'
    state={}
    result=helper['activate_desktop_window']('/usr/bin/xdotool','456',123,state,state_file)
    assert result['method']=='windowfocus_fallback'
    assert result['fallback']=='succeeded'
    assert [call[1] for call in calls]==[
        'windowactivate','getwindowpid','windowfocus','getactivewindow','getwindowpid']
    assert json.loads(state_file.read_text())['desktop_activation']['warning']==helper['DESKTOP_PROPERTY_WARNING']


@pytest.mark.parametrize('stderr', [
    'BadWindow (invalid Window parameter)',
    'XGetWindowProperty[_NET_ACTIVE_WINDOW] failed (code=1)',
    'prefix: XGetWindowProperty[_NET_WM_DESKTOP] failed (code=1)',
    'XGetWindowProperty[_NET_WM_DESKTOP] failed (code=1) suffix',
])
def test_desktop_activation_fails_closed_for_other_x11_errors(monkeypatch,tmp_path,stderr):
    import runpy
    import types
    helper=runpy.run_path(str(ROOT/'scripts/cortex-desktop-dev'),run_name='observer')
    calls=[]
    def fake_run(args,**kwargs):
        calls.append(args)
        return types.SimpleNamespace(returncode=1,stdout='',stderr=stderr)
    monkeypatch.setattr(helper['subprocess'],'run',fake_run)
    state_file=tmp_path/'session.json'
    state={}
    with pytest.raises(RuntimeError,match='activation failed'):
        helper['activate_desktop_window']('/usr/bin/xdotool','456',123,state,state_file)
    assert [call[1] for call in calls]==['windowactivate']
    activation=json.loads(state_file.read_text())['desktop_activation']
    assert activation['method']=='windowactivate'
    assert 'fallback' not in activation


def test_desktop_activation_fails_closed_when_focus_fails(monkeypatch,tmp_path):
    import runpy
    import types
    helper=runpy.run_path(str(ROOT/'scripts/cortex-desktop-dev'),run_name='observer')
    calls=[]
    def fake_run(args,**kwargs):
        calls.append(args)
        command=args[1]
        if command=='windowactivate':
            return types.SimpleNamespace(returncode=1,stdout='',stderr=helper['DESKTOP_PROPERTY_WARNING'])
        if command=='getwindowpid':
            return types.SimpleNamespace(returncode=0,stdout='123\n',stderr='')
        assert command=='windowfocus'
        return types.SimpleNamespace(returncode=1,stdout='',stderr='focus failed')
    monkeypatch.setattr(helper['subprocess'],'run',fake_run)
    state_file=tmp_path/'session.json'
    state={}
    with pytest.raises(RuntimeError,match='focus fallback failed'):
        helper['activate_desktop_window']('/usr/bin/xdotool','456',123,state,state_file)
    assert [call[1] for call in calls]==['windowactivate','getwindowpid','windowfocus']
    assert json.loads(state_file.read_text())['desktop_activation']['fallback']=='failed_focus'


def test_desktop_activation_fails_closed_when_active_window_mismatches(monkeypatch,tmp_path):
    import runpy
    import types
    helper=runpy.run_path(str(ROOT/'scripts/cortex-desktop-dev'),run_name='observer')
    calls=[]
    def fake_run(args,**kwargs):
        calls.append(args)
        command=args[1]
        if command=='windowactivate':
            return types.SimpleNamespace(returncode=1,stdout='',stderr=helper['DESKTOP_PROPERTY_WARNING'])
        if command=='getwindowpid':
            return types.SimpleNamespace(returncode=0,stdout='123\n',stderr='')
        if command=='windowfocus':
            return types.SimpleNamespace(returncode=0,stdout='',stderr='')
        assert command=='getactivewindow'
        return types.SimpleNamespace(returncode=0,stdout='999\n',stderr='')
    monkeypatch.setattr(helper['subprocess'],'run',fake_run)
    state_file=tmp_path/'session.json'
    state={}
    with pytest.raises(RuntimeError,match='did not make the owned Desktop window active'):
        helper['activate_desktop_window']('/usr/bin/xdotool','456',123,state,state_file)
    assert [call[1] for call in calls]==[
        'windowactivate','getwindowpid','windowfocus','getactivewindow']
    assert json.loads(state_file.read_text())['desktop_activation']['fallback']=='failed_active_window'


def test_desktop_activation_fails_closed_on_focus_ownership_change(monkeypatch,tmp_path):
    import runpy
    import types
    helper=runpy.run_path(str(ROOT/'scripts/cortex-desktop-dev'),run_name='observer')
    calls=[]
    pids=iter(['123\n','999\n'])
    def fake_run(args,**kwargs):
        calls.append(args)
        command=args[1]
        if command=='windowactivate':
            return types.SimpleNamespace(returncode=1,stdout='',stderr=helper['DESKTOP_PROPERTY_WARNING'])
        if command=='getwindowpid':
            return types.SimpleNamespace(returncode=0,stdout=next(pids),stderr='')
        if command=='windowfocus':
            return types.SimpleNamespace(returncode=0,stdout='',stderr='')
        assert command=='getactivewindow'
        return types.SimpleNamespace(returncode=0,stdout='456\n',stderr='')
    monkeypatch.setattr(helper['subprocess'],'run',fake_run)
    state_file=tmp_path/'session.json'
    state={}
    with pytest.raises(RuntimeError,match='ownership changed'):
        helper['activate_desktop_window']('/usr/bin/xdotool','456',123,state,state_file)
    assert [call[1] for call in calls]==[
        'windowactivate','getwindowpid','windowfocus','getactivewindow','getwindowpid']
    assert json.loads(state_file.read_text())['desktop_activation']['fallback']=='failed_ownership'


def test_cli_helper_audits_all_thread_calls_with_shared_observer():
    source=(ROOT/'scripts/cortex-live-smoke').read_text()
    assert "add_argument('--data-dir',type=Path)" not in source
    assert "add_argument('--evaluation-fresh-store',action='store_true'" in source
    assert 'prepare_evaluation_fresh_store(workdir)' in source
    assert "not args.resume_last" in source
    assert "store=str(store)" in source
    assert "'CORTEX_DATA_DIR='+str(evaluation_storage_directory(evaluation_workdir))" in source
    assert 'def user_prompt_receipts(data,prompt):' in source
    assert 'prompt submission produced no exact user-turn receipt' in source
    assert "sub.add_parser('audit')" in source
    assert "s=sub.add_parser('calls');s.add_argument('--limit',type=int)" in source
    assert "runpy.run_path(str(ROOT/'scripts/cortex-desktop-dev')" in source
    assert "policy_violations=policy" in source
    assert "error_history=observer['tool_error_history'](host_rows)" in source
    assert "tool_error_history=error_history" in source
    assert "orchestration_errors=observer['orchestration_error_history'](host_rows)" in source
    assert "classification['evidence_integrity_invalidators']" in source
    assert "open_sessions=open_sessions" in source
    assert "open_cells=open_cells" in source
    assert "sandbox_workspace_write.network_access=true" in source
    assert "desktop_editor_source_sha256']" in source


def test_observer_attributes_compound_command_failure_to_shell_named_executable():
    observer = runpy.run_path(str(ROOT / "scripts/cortex-desktop-dev"), run_name="observer_failure_family")
    assert observer["failed_command_family"](
        "ok\n/bin/bash: line 1: python: command not found\n"
    ) == "python"
    assert observer["raw_command_family"]("python3 -B -m py_compile x.py") == "python"
    assert observer["failed_command_family"]("nl: missing operand\n") is None
    assert observer["verified_compound_failure_family"](
        "pwd && rg --files -g 'AGENTS.md' .",
        "/tmp/work\n", "file:///tmp/work",
    ) == "rg"
    assert observer["verified_compound_failure_family"](
        "pwd && rg --files && wc -l", "/tmp/work\n", "file:///tmp/work",
    ) is None
    no_match = observer["semantic_no_match_family"]
    assert no_match("rg --files -g 'AGENTS.md' .", 1, "", "", "/tmp/work") == "rg"
    assert no_match("grep needle file", 1, "", "", "/tmp/work") == "grep"
    assert no_match(
        "pwd && rg --files -g 'AGENTS.md' .", 1,
        "/tmp/work\n", "", "file:///tmp/work",
    ) == "rg"
    assert no_match("pwd && rg x . && wc -l", 1, "/tmp/work\n", "", "/tmp/work") is None
    assert no_match("rg x .", 2, "", "", "/tmp/work") is None
    assert no_match("rg x .", 1, "", "permission denied", "/tmp/work") is None
    assert no_match("rg x . || true", 1, "", "", "/tmp/work") is None
    assert no_match("pwd && rg x .", 1, "/other\n", "", "/tmp/work") is None
    unresolved, resolved = observer["classify_host_failures"]([
        {"thread_id": "w", "tool": "command_execution", "argument_digest": "failed", "outcome": "error",
         "error_code": "command_exit_1", "command_family": "rg",
         "semantic_result": "no_match", "semantic_nonfailure": True},
        {"thread_id": "w", "tool": "command_execution", "argument_digest": "real", "outcome": "error",
         "error_code": "command_exit_2", "command_family": "rg"},
    ])
    assert [row["argument_digest"] for row in unresolved] == ["real"] and resolved == []
    assert observer["tool_error_history"]([
        {"thread_id": "w", "tool": "functions.exec", "argument_digest": "wrapper", "outcome": "error",
         "error_code": "command_exit_1", "exit_code": 1,
         "semantic_result": "no_match", "semantic_nonfailure": True},
        {"thread_id": "w", "tool": "command_execution", "argument_digest": "no-match", "outcome": "error",
         "error_code": "command_exit_1", "exit_code": 1,
         "semantic_result": "no_match", "semantic_nonfailure": True},
        {"thread_id": "w", "tool": "command_execution", "argument_digest": "real", "outcome": "error",
         "error_code": "command_exit_2", "exit_code": 2},
    ]) == [{"thread_id": "w", "tool": "command_execution",
            "error_code": "command_exit_2", "argument_digest": "real"}]


def test_baseline_identity_and_graph_disabled_launcher_omit_invalid_transport():
    import runpy
    import cortex_eval

    assert cortex_eval.BASELINE == 'cc786ae2fbd04cf1e9c29cfb34cf721de6ad6b8663f2d05f809baf2bee158698'
    workloads = json.loads((ROOT/'tests/fixtures/phase01_eval/workloads-v1.json').read_text())
    assert workloads['payload_identities']['baseline'] == cortex_eval.BASELINE

    cli = runpy.run_path(str(ROOT/'scripts/cortex-live-smoke'), run_name='transport')
    command = cli['launch_command'](Path('/tmp/private-events'), False, 'gpt-5.6-luna',
                                    'high', False, False)
    overrides = [value for index, value in enumerate(command)
                 if index and command[index - 1] == '-c']
    assert 'mcp_servers.codebase_memory.enabled=false' not in overrides
    assert 'mcp_servers.codebase_memory.enabled=true' not in overrides
    assert 'mcp_servers.node_repl.enabled=false' not in overrides
    assert 'sandbox_workspace_write.network_access=true' in overrides


def test_graph_enabled_launcher_retains_explicit_complete_server_override():
    import runpy
    cli = runpy.run_path(str(ROOT/'scripts/cortex-live-smoke'), run_name='transport')
    command = cli['launch_command'](Path('/tmp/private-events'), False, 'gpt-5.6-luna',
                                    'high', False, True)
    overrides = [value for index, value in enumerate(command)
                 if index and command[index - 1] == '-c']
    assert 'mcp_servers.codebase_memory.enabled=false' not in overrides
    assert 'mcp_servers.codebase_memory.enabled=true' in overrides
    assert 'mcp_servers.node_repl.enabled=false' not in overrides


def test_graph_disabled_launcher_disables_existing_complete_server(monkeypatch, tmp_path):
    import runpy

    owner = tmp_path/'owner'
    config = owner/'.cortex-dev/.codex/config.toml'
    config.parent.mkdir(parents=True, mode=0o700)
    config.write_text('[mcp_servers.codebase_memory]\n'
                      'enabled = true\n'
                      'command = "/usr/local/bin/codebase-memory-mcp"\n')
    config.chmod(0o600)
    config.parent.chmod(0o700)
    owner.chmod(0o700)
    monkeypatch.setattr(Path, 'home', lambda: owner)
    cli = runpy.run_path(str(ROOT/'scripts/cortex-live-smoke'), run_name='transport')
    configured = cli['isolated_codebase_memory_configured']()
    assert configured is True
    command = cli['launch_command'](Path('/tmp/private-events'), False,
                                    'gpt-5.6-luna', 'high', False, False,
                                    configured)
    overrides = [value for index, value in enumerate(command)
                 if index and command[index - 1] == '-c']
    assert 'mcp_servers.codebase_memory.enabled=false' in overrides
    config.unlink()
    assert cli['isolated_codebase_memory_configured']() is False
    command = cli['launch_command'](Path('/tmp/private-events'), False,
                                    'gpt-5.6-luna', 'high', False, False)
    overrides = [value for index, value in enumerate(command)
                 if index and command[index - 1] == '-c']
    assert 'mcp_servers.codebase_memory.enabled=false' not in overrides


def test_desktop_graph_disabled_config_disables_only_complete_server():
    import runpy
    import tomllib

    desktop = runpy.run_path(str(ROOT/'scripts/cortex-desktop-dev'), run_name='observer')
    config = '[mcp_servers.codebase_memory]\nenabled = true\ncommand = "/bin/codebase-memory-mcp"\n'
    parsed = tomllib.loads(desktop['live_test_config'](config))
    assert parsed['mcp_servers']['codebase_memory']['enabled'] is False

    absent = tomllib.loads(desktop['live_test_config']('model = "x"\n'))
    assert 'mcp_servers' not in absent

    incomplete = '[mcp_servers.codebase_memory]\nenabled = true\n'
    parsed = tomllib.loads(desktop['live_test_config'](incomplete))
    assert parsed['mcp_servers']['codebase_memory']['enabled'] is True


def test_isolated_launchers_force_bytecode_suppression_over_ambient_value(monkeypatch, tmp_path):
    import runpy
    monkeypatch.setenv('PYTHONDONTWRITEBYTECODE', '0')
    cli = runpy.run_path(str(ROOT/'scripts/cortex-live-smoke'), run_name='transport')
    command = cli['launch_command'](tmp_path/'events', False, 'gpt-5.6-luna', 'high', False, False)
    assert [part for part in command if part.startswith('PYTHONDONTWRITEBYTECODE=')] == [
        'PYTHONDONTWRITEBYTECODE=1'
    ]
    owner = tmp_path/'owner'
    dependency = owner/'.cortex-dev/.codex/cortex-deps'
    dependency.mkdir(parents=True, mode=0o700)
    dependency.chmod(0o700)
    monkeypatch.setattr(Path, 'home', lambda: owner)
    desktop = runpy.run_path(str(ROOT/'scripts/cortex-desktop-dev'), run_name='observer')
    env = desktop['environment'](tmp_path/'profile', tmp_path/'desktop-events')
    assert env['PYTHONDONTWRITEBYTECODE'] == '1'


def test_worker_safety_and_post_wait_rules_are_payload_guidance():
    worker = (PLUGIN/'agent-sources/worker-protocol.md').read_text()
    orchestrator = (PLUGIN/'skills/orchestrator/SKILL.md').read_text()
    assert 'PYTHONDONTWRITEBYTECODE=1' in worker
    for rule in ('rm -rf', 'find ... -delete', 'git clean', 'reset/checkout', 'recursive cleanup'):
        assert rule in worker
    assert 'Checks `PYTHONDONTWRITEBYTECODE=1`' in worker
    assert 'native final names only the assignment-owned report ID' in worker
    assert 'Put every other report ID only in the saved report' in worker
    assert 'A wait timeout is only no new evidence, never\ncompletion' in orchestrator
    assert 'pending is equivalent' in orchestrator
    assert '`send_message`/`followup_task`\nafter a wait alone' in orchestrator
    assert 'inbound same-owner reply' in orchestrator
    assert 'follow-up after terminal result/report reconciliation' in orchestrator


def test_coordinator_native_worker_tracking_is_distinct_from_app_task_management():
    orchestrator = (PLUGIN/'skills/orchestrator/SKILL.md').read_text()
    payload = json.loads((PLUGIN/'runtime-payload.json').read_text())['files']
    rule = (
        'Native subagents spawned through `collaboration.spawn_agent` are tracked only with\n'
        'the native collaboration controls: `collaboration.wait_agent`,\n'
        '`collaboration.list_agents`, `collaboration.send_message` and\n'
        '`collaboration.followup_task`.'
    )
    assert rule in orchestrator
    assert '`create_thread`, `read_thread`, `wait_threads` or `send_message_to_thread`' in orchestrator
    assert 'reserved for explicit user-owned\ntask management, not worker coordination.' in orchestrator
    assert 'scripts/cortex_runtime/hooks.py' in payload
    assert (PLUGIN/'skills/orchestrator/SKILL.md').is_file()
    package_version = json.loads((PLUGIN/'.codex-plugin/plugin.json').read_text())['version']
    assert package_version.startswith('1.15.9+codex.sha256.')
    assert payload_digest(PLUGIN).startswith(package_version.rsplit('.', 1)[-1])
    for path, body in expected_profiles().items():
        if path.stem == 'senior-consultant':
            continue
        assert 'Never discover,\ncall or request approval for `codex_app.send_message_to_thread`' in body.decode()


def test_baseline_launcher_has_no_partial_codebase_memory_transport_literal():
    source = (ROOT/'scripts/cortex-live-smoke').read_text()
    assert "mcp_servers.codebase_memory.enabled='+('true' if graph_enabled else 'false')" not in source
    assert "mcp_servers.codebase_memory.enabled=false" not in source
    assert "mcp_servers.node_repl.enabled=false" not in source


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


def test_live_helpers_remove_ambient_external_store_override_from_child_environments(monkeypatch,tmp_path):
    import runpy
    home=tmp_path/'home'
    dependency=home/'.cortex-dev/.codex/cortex-deps'
    dependency.mkdir(parents=True,mode=0o700)
    monkeypatch.setenv('HOME',str(home))
    helper=runpy.run_path(str(ROOT/'scripts/cortex-desktop-dev'),run_name='observer')
    monkeypatch.setenv('CORTEX_DATA_DIR',str(tmp_path/'external'))
    env=helper['environment'](tmp_path/'profile',tmp_path/'events')
    assert 'CORTEX_DATA_DIR' not in env
    cli=runpy.run_path(str(ROOT/'scripts/cortex-live-smoke'),run_name='transport')
    project=tmp_path/'project';project.mkdir()
    (project/'.codex/cortex').mkdir(parents=True,mode=0o700)
    (project/'.codex').chmod(0o700)
    command=cli['launch_command'](tmp_path/'events',False,'gpt-5.6-luna','high',
                                  False,False,False,project.resolve())
    assignments=[part for part in command if part.startswith('CORTEX_DATA_DIR=')]
    assert assignments == ['CORTEX_DATA_DIR='+str(project.resolve()/'.codex/cortex')]
    assert str(tmp_path/'external') not in command


def test_cli_evaluation_storage_directory_fails_closed_for_invalid_layouts(tmp_path):
    import runpy
    cli=runpy.run_path(str(ROOT/'scripts/cortex-live-smoke'),run_name='transport')
    project=tmp_path/'project';project.mkdir()
    with pytest.raises(RuntimeError,match='parent is unavailable'):
        cli['evaluation_storage_directory'](project.resolve())
    outside=tmp_path/'outside';outside.mkdir(mode=0o700)
    (project/'.codex').symlink_to(outside,target_is_directory=True)
    with pytest.raises(RuntimeError,match='private directory'):
        cli['evaluation_storage_directory'](project.resolve())
    (project/'.codex').unlink();(project/'.codex/cortex').mkdir(parents=True,mode=0o700)
    (project/'.codex').chmod(0o755)
    with pytest.raises(RuntimeError,match='owner-private'):
        cli['evaluation_storage_directory'](project.resolve())
    (project/'.codex').chmod(0o700)
    store=project/'.codex/cortex/cortex.sqlite3';store.write_bytes(b'occupied');store.chmod(0o600)
    with pytest.raises(RuntimeError,match='target already exists'):
        cli['evaluation_storage_directory'](project.resolve())


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


def test_cli_evaluation_fresh_store_creates_private_parents_and_redacted_provenance(tmp_path, monkeypatch):
    import hashlib
    import runpy
    owner = tmp_path/'owner'
    candidate = owner/'.cortex-dev/.codex/cortex-candidate.json'
    candidate.parent.mkdir(parents=True, mode=0o700)
    candidate.parent.chmod(0o700)
    candidate.write_text(json.dumps({'digest': hashlib.sha256(b'candidate').hexdigest()}))
    candidate.chmod(0o600)
    monkeypatch.setattr(Path, 'home', lambda: owner)
    cli = runpy.run_path(str(ROOT/'scripts/cortex-live-smoke'), run_name='transport')
    project = (tmp_path/'project').resolve()
    project.mkdir()
    receipt = cli['prepare_evaluation_fresh_store'](project)
    assert receipt['host_class'] == 'cli'
    assert receipt['project_relative_store'] == '.codex/cortex/cortex.sqlite3'
    assert receipt['existed_before'] is False
    assert receipt['candidate_digest'] == hashlib.sha256(b'candidate').hexdigest()
    assert not (project/'.codex/cortex/cortex.sqlite3').exists()
    assert (project/'.codex').stat().st_mode & 0o077 == 0
    assert (project/'.codex/cortex').stat().st_mode & 0o077 == 0
    events = tmp_path/'events'; events.mkdir(mode=0o700)
    cli['_write_evaluation_provenance'](events, receipt)
    rendered = next(events.glob('*.jsonl')).read_text()
    assert str(project) not in rendered
    assert str(owner) not in rendered
    assert 'prompt' not in rendered
    assert 'arm' not in rendered
    assert 'mapping' not in rendered
    assert 'raw' not in rendered


@pytest.mark.parametrize('candidate_value', [None, {'digest': 'not-a-digest'}])
def test_cli_evaluation_fresh_store_candidate_rejection_leaves_parents_absent(
        tmp_path, monkeypatch, candidate_value):
    import runpy
    owner = tmp_path/'owner'
    candidate = owner/'.cortex-dev/.codex/cortex-candidate.json'
    candidate.parent.mkdir(parents=True, mode=0o700)
    candidate.parent.chmod(0o700)
    if candidate_value is not None:
        candidate.write_text(json.dumps(candidate_value))
        candidate.chmod(0o600)
    monkeypatch.setattr(Path, 'home', lambda: owner)
    cli = runpy.run_path(str(ROOT/'scripts/cortex-live-smoke'), run_name='transport')
    project = (tmp_path/'project').resolve(); project.mkdir()
    with pytest.raises(RuntimeError, match='candidate identity'):
        cli['prepare_evaluation_fresh_store'](project)
    assert not (project/'.codex').exists()
    assert not (project/'.codex/cortex').exists()


@pytest.mark.parametrize('kind', ['file', 'directory', 'symlink'])
def test_cli_evaluation_fresh_store_rejects_existing_target_without_mutation(tmp_path, kind):
    import runpy
    project = (tmp_path/'project').resolve(); project.mkdir()
    store = project/'.codex/cortex/cortex.sqlite3'; store.parent.mkdir(parents=True, mode=0o700)
    if kind == 'file': store.write_bytes(b'keep-me'); store.chmod(0o600)
    elif kind == 'directory': store.mkdir(mode=0o700)
    else: store.symlink_to(tmp_path/'missing-store')
    cli = runpy.run_path(str(ROOT/'scripts/cortex-live-smoke'), run_name='transport')
    with pytest.raises(RuntimeError, match='evaluation fresh-store'):
        cli['prepare_evaluation_fresh_store'](project)
    if kind == 'file': assert store.read_bytes() == b'keep-me'
    elif kind == 'directory': assert store.is_dir()
    else: assert store.is_symlink()


def test_cli_evaluation_fresh_store_rejects_escape_and_nonprivate_parent(tmp_path):
    import runpy
    cli = runpy.run_path(str(ROOT/'scripts/cortex-live-smoke'), run_name='transport')
    project = (tmp_path/'project').resolve(); project.mkdir()
    outside = tmp_path/'outside'; outside.mkdir(mode=0o700)
    (project/'.codex').symlink_to(outside, target_is_directory=True)
    with pytest.raises(RuntimeError, match='escapes workdir'):
        cli['prepare_evaluation_fresh_store'](project)
    (project/'.codex').unlink()
    (project/'.codex').mkdir(mode=0o755)
    (project/'.codex').chmod(0o755)
    with pytest.raises(RuntimeError, match='owner-private'):
        cli['prepare_evaluation_fresh_store'](project)


@pytest.mark.parametrize('machine', ['s390x', ''])
def test_cli_evaluation_fresh_store_unknown_architecture_fails_closed(tmp_path, monkeypatch, machine):
    import runpy
    owner = tmp_path/'owner'; candidate = owner/'.cortex-dev/.codex/cortex-candidate.json'
    candidate.parent.mkdir(parents=True, mode=0o700)
    candidate.write_text(json.dumps({'digest': 'a' * 64})); candidate.chmod(0o600)
    monkeypatch.setattr(Path, 'home', lambda: owner)
    cli = runpy.run_path(str(ROOT/'scripts/cortex-live-smoke'), run_name='transport')
    monkeypatch.setattr(cli['platform'], 'machine', lambda: machine)
    project = tmp_path/'project'; project.mkdir()
    with pytest.raises(RuntimeError, match='architecture is unsupported'):
        cli['prepare_evaluation_fresh_store'](project)
    assert not (project/'.codex').exists()


def test_cli_evaluation_fresh_store_noreplace_preserves_existing_winner_and_cleans_stage(tmp_path):
    import runpy
    cli = runpy.run_path(str(ROOT/'scripts/cortex-live-smoke'), run_name='transport')
    stage = tmp_path/'stage'; target = tmp_path/'target'
    stage.mkdir(mode=0o700); (stage/'marker').write_text('loser')
    target.mkdir(mode=0o700); (target/'marker').write_text('winner')
    with pytest.raises(RuntimeError, match='target appeared'):
        cli['_linux_noreplace'](stage, target)
    assert (target/'marker').read_text() == 'winner'
    assert (stage/'marker').read_text() == 'loser'


def test_cli_evaluation_fresh_store_loser_retains_substituted_stage_and_project_parent_absent(
        tmp_path, monkeypatch):
    import hashlib
    import runpy
    owner = tmp_path/'owner'; candidate = owner/'.cortex-dev/.codex/cortex-candidate.json'
    candidate.parent.mkdir(parents=True, mode=0o700)
    candidate.write_text(json.dumps({'digest': hashlib.sha256(b'candidate').hexdigest()}))
    candidate.chmod(0o600); candidate.parent.chmod(0o700); owner.chmod(0o700)
    monkeypatch.setattr(Path, 'home', lambda: owner)
    cli = runpy.run_path(str(ROOT/'scripts/cortex-live-smoke'), run_name='transport')
    project = (tmp_path/'project').resolve(); project.mkdir()
    observed = {}

    def lose(stage, target):
        observed['stage'] = stage
        replacement = stage.with_name(stage.name + '-replacement')
        stage.rename(replacement)
        stage.mkdir(mode=0o700)
        (stage/'attacker-data').write_text('keep')
        raise RuntimeError('evaluation fresh-store target appeared during commit')

    monkeypatch.setitem(cli['prepare_evaluation_fresh_store'].__globals__, '_linux_noreplace', lose)
    with pytest.raises(RuntimeError, match='target appeared'):
        cli['prepare_evaluation_fresh_store'](project)
    assert not (project/'.codex').exists()
    assert (observed['stage']/'attacker-data').read_text() == 'keep'


def test_cli_evaluation_fresh_store_precommit_failure_has_no_project_residue(tmp_path):
    import hashlib
    import runpy
    owner = tmp_path/'owner'; candidate = owner/'.cortex-dev/.codex/cortex-candidate.json'
    candidate.parent.mkdir(parents=True, mode=0o700)
    candidate.write_text(json.dumps({'digest': hashlib.sha256(b'candidate').hexdigest()}))
    candidate.chmod(0o600); candidate.parent.chmod(0o700); owner.chmod(0o700)
    project = (tmp_path/'project').resolve(); project.mkdir()
    old_home = Path.home
    try:
        Path.home = lambda: owner
        cli = runpy.run_path(str(ROOT/'scripts/cortex-live-smoke'), run_name='transport')
        with pytest.raises(RuntimeError, match='simulated precommit failure'):
            cli['prepare_evaluation_fresh_store'](
                project, lambda: (_ for _ in ()).throw(RuntimeError('simulated precommit failure')))
    finally:
        Path.home = old_home
    assert not (project/'.codex').exists()


def test_cli_start_fresh_store_rejects_before_git_or_launch_mutation(tmp_path, monkeypatch):
    import runpy
    from types import SimpleNamespace

    project = (tmp_path/'project').resolve(); project.mkdir()
    store = project/'.codex/cortex/cortex.sqlite3'; store.parent.mkdir(parents=True, mode=0o700)
    store.write_bytes(b'keep-me'); store.chmod(0o600)
    before = (store.read_bytes(), store.is_file(), store.is_symlink())

    cli = runpy.run_path(str(ROOT/'scripts/cortex-live-smoke'), run_name='transport')
    globals_ = cli['start'].__globals__
    monkeypatch.setitem(globals_, 'STATE', tmp_path/'cli-state')
    tmux_calls = []

    def tmux(*args, **kwargs):
        tmux_calls.append(args)
        if args[:2] == ('has-session', '-t'):
            return SimpleNamespace(returncode=1)
        raise AssertionError('fresh-store rejection must not launch tmux')

    monkeypatch.setitem(globals_, 'tmux', tmux)
    monkeypatch.setitem(globals_, 'ensure_git_workspace',
                        lambda workdir: (_ for _ in ()).throw(AssertionError('Git setup ran before rejection')))
    args = SimpleNamespace(workdir=project, resume_last=False, evaluation_fresh_store=True,
                           model=None, effort=None, codebase_memory=False, apps_enabled=False)

    with pytest.raises(RuntimeError, match='evaluation fresh-store target already exists'):
        cli['start'](args)

    assert (store.read_bytes(), store.is_file(), store.is_symlink()) == before
    assert not (project/'.git').exists()
    assert not (tmp_path/'cli-state').exists()
    assert tmux_calls == []


def test_cli_start_missing_git_is_precommit_and_leaves_no_project_residue(tmp_path, monkeypatch):
    import hashlib
    import runpy
    from types import SimpleNamespace

    owner = tmp_path/'owner'; candidate = owner/'.cortex-dev/.codex/cortex-candidate.json'
    candidate.parent.mkdir(parents=True, mode=0o700); candidate.parent.chmod(0o700)
    candidate.write_text(json.dumps({'digest': hashlib.sha256(b'candidate').hexdigest()}))
    candidate.chmod(0o600); owner.chmod(0o700)
    monkeypatch.setattr(Path, 'home', lambda: owner)
    project = (tmp_path/'project').resolve(); project.mkdir()
    cli = runpy.run_path(str(ROOT/'scripts/cortex-live-smoke'), run_name='transport')
    monkeypatch.setitem(cli['start'].__globals__, 'STATE', tmp_path/'cli-state')
    monkeypatch.setitem(cli['start'].__globals__, 'tmux',
                        lambda *args, **kwargs: SimpleNamespace(returncode=1, stdout=''))
    args = SimpleNamespace(workdir=project, resume_last=False, evaluation_fresh_store=True,
                           model=None, effort=None, codebase_memory=False, apps_enabled=False)

    with pytest.raises(RuntimeError, match='root of a Git repository'):
        cli['start'](args)
    assert not (project/'.codex').exists()
    assert not list((tmp_path/'cli-state').glob('phase2-*failure*.json'))


def test_cli_tmux_launch_preflight_rejects_bad_provenance_before_project_commit(tmp_path, monkeypatch):
    import hashlib
    import runpy
    from types import SimpleNamespace

    owner = tmp_path/'owner'; candidate = owner/'.cortex-dev/.codex/cortex-candidate.json'
    candidate.parent.mkdir(parents=True, mode=0o700); candidate.parent.chmod(0o700)
    candidate.write_text(json.dumps({'digest': hashlib.sha256(b'candidate').hexdigest()}))
    candidate.chmod(0o600); owner.chmod(0o700)
    monkeypatch.setattr(Path, 'home', lambda: owner)
    project = (tmp_path/'project').resolve(); project.mkdir()
    subprocess.run(['git', 'init', '-q', str(project)], check=True)
    cli = runpy.run_path(str(ROOT/'scripts/cortex-live-smoke'), run_name='transport')
    globals_ = cli['start'].__globals__; state_root = tmp_path/'cli-state'
    monkeypatch.setitem(globals_, 'STATE', state_root)
    monkeypatch.setitem(globals_, 'PHASE2_CONTROL_SHA256', 'a' * 64)
    calls = []

    def fake_tmux(*args, **kwargs):
        calls.append(args)
        if args[:2] == ('has-session', '-t'):
            return SimpleNamespace(returncode=1, stdout='', stderr='no server running')
        if args[0] == 'new-session':
            return SimpleNamespace(returncode=0, stdout='$9\n', stderr='')
        if args[0] == 'list-panes':
            # Reproduce run 10's empty session provenance while tmux exits 0.
            return SimpleNamespace(returncode=0, stdout='|||||\n', stderr='')
        return SimpleNamespace(returncode=0, stdout='', stderr='')

    monkeypatch.setitem(globals_, 'tmux', fake_tmux)
    args = SimpleNamespace(workdir=project, resume_last=False, evaluation_fresh_store=True,
                           model=None, effort=None, codebase_memory=False, apps_enabled=False)
    with pytest.raises(RuntimeError, match='tmux launch preflight failed before fresh-store commit'):
        cli['start'](args)
    assert not (project/'.codex').exists()
    assert not list(state_root.glob('phase2-*transaction*.json'))
    assert not list(state_root.glob('phase2-*failure*.json'))
    assert ('kill-session', '-t', '$9') not in calls
    assert not any(call[0] == 'display-message' for call in calls)
    assert calls[1][0:10] == (
        'new-session','-d','-P','-F','#{session_id}','-s','cortex-markdown-smoke',
        '-c',str(project),'/bin/sleep',
    )


@pytest.mark.skipif(shutil.which('tmux') is None, reason='real tmux is unavailable')
def test_cli_tmux_launch_preflight_real_no_workload(tmp_path):
    import runpy

    cli = runpy.run_path(str(ROOT/'scripts/cortex-live-smoke'), run_name='transport')
    absent = cli['tmux']('has-session','-t','=cortex-markdown-smoke',check=False)
    if absent.returncode == 0:
        pytest.skip('exact shared smoke session is already in use')
    project = tmp_path.resolve()
    cli['_preflight_tmux_launch'](project)
    after = cli['tmux']('has-session','-t','=cortex-markdown-smoke',check=False)
    assert after.returncode != 0


def test_cli_tmux_launch_preflight_refuses_session_recreation(tmp_path, monkeypatch):
    import runpy
    from types import SimpleNamespace

    cli = runpy.run_path(str(ROOT/'scripts/cortex-live-smoke'), run_name='transport')
    globals_ = cli['_preflight_tmux_launch'].__globals__
    rows = iter((
        '700|cortex-markdown-smoke|$7|701|%7|7007\n',
        '800|cortex-markdown-smoke|$7|801|%8|8008\n',
        '800|cortex-markdown-smoke|$7|801|%8|8008\n',
    ))
    calls = []
    def fake_tmux(*args, **kwargs):
        calls.append(args)
        if args[0] == 'new-session':
            return SimpleNamespace(returncode=0, stdout='$7\n', stderr='')
        if args[0] == 'list-panes':
            return SimpleNamespace(returncode=0, stdout=next(rows), stderr='')
        if args[0] == 'has-session':
            return SimpleNamespace(returncode=0, stdout='', stderr='')
        return SimpleNamespace(returncode=0, stdout='', stderr='')
    monkeypatch.setitem(globals_, 'tmux', fake_tmux)
    with pytest.raises(RuntimeError, match='refused a substituted session'):
        cli['_preflight_tmux_launch'](tmp_path.resolve())
    assert ('kill-session', '-t', '$7') not in calls
    assert all(call[2] == '=$7' for call in calls if call[0] == 'list-panes')


@pytest.mark.parametrize('failure', ['provenance', 'tmux-configuration'])
def test_cli_start_postcommit_failure_marks_store_unusable_consumes_control_and_stops_owned_session(
        tmp_path, monkeypatch, failure):
    import hashlib
    import runpy
    from types import SimpleNamespace

    owner = tmp_path/'owner'; candidate = owner/'.cortex-dev/.codex/cortex-candidate.json'
    candidate.parent.mkdir(parents=True, mode=0o700); candidate.parent.chmod(0o700)
    candidate.write_text(json.dumps({'digest': hashlib.sha256(b'candidate').hexdigest()}))
    candidate.chmod(0o600); owner.chmod(0o700)
    monkeypatch.setattr(Path, 'home', lambda: owner)
    project = (tmp_path/'project').resolve(); project.mkdir()
    subprocess.run(['git', 'init', '-q', str(project)], check=True)
    cli = runpy.run_path(str(ROOT/'scripts/cortex-live-smoke'), run_name='transport')
    globals_ = cli['start'].__globals__
    monkeypatch.setitem(globals_, 'STATE', tmp_path/'cli-state')
    monkeypatch.setitem(globals_, 'PHASE2_CONTROL_SHA256', 'b' * 64)
    tmux_calls = []
    set_option_calls = 0

    def fake_tmux(*args, **kwargs):
        nonlocal set_option_calls
        tmux_calls.append(args)
        if args[:2] == ('has-session', '-t'):
            return SimpleNamespace(returncode=1)
        if args[0] == 'new-session':
            return SimpleNamespace(returncode=0, stdout='$42\n')
        if args[0] == 'list-panes':
            return SimpleNamespace(
                returncode=0,
                stdout='900|cortex-markdown-smoke|$42|901|%42|4242\n',
            )
        if args[0] == 'set-option':
            set_option_calls += 1
            if failure == 'tmux-configuration' and set_option_calls == 2:
                raise RuntimeError('simulated tmux launch failure')
        return SimpleNamespace(returncode=0, stdout='')

    monkeypatch.setitem(globals_, 'tmux', fake_tmux)
    monkeypatch.setitem(globals_, '_proc_start_ticks', lambda _pid: 902)
    if failure == 'provenance':
        monkeypatch.setitem(globals_, '_write_evaluation_provenance',
                            lambda events, receipt: (_ for _ in ()).throw(OSError('artifact unavailable')))
    args = SimpleNamespace(workdir=project, resume_last=False, evaluation_fresh_store=True,
                           model=None, effort=None, codebase_memory=False, apps_enabled=False)
    with pytest.raises(cli['PostCommitLaunchFailure'], match='post_commit_launch_failure') as caught:
        cli['start'](args)
    message = str(caught.value)
    assert 'fresh-store commit accepted' in message
    assert 'canonical .codex is preserved' in message
    assert 'new disposable workdir' in message
    assert (project/'.codex').is_dir()
    assert not (project/'.codex/cortex/cortex.sqlite3').exists()
    marker = json.loads((project/'.codex/cortex/phase2-launch-failure.json').read_text())
    control_receipt = json.loads((tmp_path/'cli-state'/f"phase2-control-failure-{'b' * 64}.json").read_text())
    assert marker == control_receipt
    assert marker['schema_version'] == 'phase2-cli-post-commit-launch-failure-v1'
    assert marker['status'] == 'unusable'
    assert marker['failure_stage'] == failure
    expected_cleanup = 'not-created' if failure == 'provenance' else 'stopped'
    assert marker['session_cleanup'] == expected_cleanup
    if failure == 'tmux-configuration':
        assert ('kill-session', '-t', '$42') in tmux_calls
        assert ('kill-session', '-t', '=cortex-markdown-smoke') not in tmux_calls
    with pytest.raises(RuntimeError, match='marked unusable'):
        cli['prepare_evaluation_fresh_store'](project)
    clean = (tmp_path/'clean').resolve(); clean.mkdir()
    with pytest.raises(RuntimeError, match='control was consumed'):
        cli['start'](args.__class__(**{**args.__dict__, 'workdir': clean}))


def test_cli_start_normal_fresh_launch_uses_owned_session_identity(tmp_path, monkeypatch):
    import hashlib
    import runpy
    from types import SimpleNamespace

    owner = tmp_path/'owner'; candidate = owner/'.cortex-dev/.codex/cortex-candidate.json'
    candidate.parent.mkdir(parents=True, mode=0o700); candidate.parent.chmod(0o700)
    candidate.write_text(json.dumps({'digest': hashlib.sha256(b'candidate').hexdigest()}))
    candidate.chmod(0o600); owner.chmod(0o700)
    monkeypatch.setattr(Path, 'home', lambda: owner)
    project = (tmp_path/'project').resolve(); project.mkdir()
    subprocess.run(['git', 'init', '-q', str(project)], check=True)
    cli = runpy.run_path(str(ROOT/'scripts/cortex-live-smoke'), run_name='transport')
    globals_ = cli['start'].__globals__; monkeypatch.setitem(globals_, 'STATE', tmp_path/'cli-state')
    calls = []
    def fake_tmux(*args, **kwargs):
        calls.append(args)
        if args[:2] == ('has-session', '-t'):
            return SimpleNamespace(returncode=1, stdout='')
        if args[0] == 'new-session':
            return SimpleNamespace(returncode=0, stdout='$7\n')
        if args[0] == 'list-panes':
            return SimpleNamespace(
                returncode=0,
                stdout='700|cortex-markdown-smoke|$7|701|%7|7007\n',
            )
        return SimpleNamespace(returncode=0, stdout='')
    monkeypatch.setitem(globals_, 'tmux', fake_tmux)
    args = SimpleNamespace(workdir=project, resume_last=False, evaluation_fresh_store=True,
                           model=None, effort=None, codebase_memory=False, apps_enabled=False)
    cli['start'](args)
    assert any(call[0] == 'send-keys' for call in calls)
    creation_calls = [call for call in calls if call[0] == 'new-session']
    assert len(creation_calls) == 2
    assert creation_calls[0][:-2] == creation_calls[1][:-1]
    assert creation_calls[0][-2:] == ('/bin/sleep', '30')
    assert creation_calls[1][-1] == '/bin/bash'
    configuration_calls = [call for call in calls if call[0] == 'set-option']
    assert configuration_calls == [
        ('set-option', '-t', '$7', 'remain-on-exit', 'on'),
        ('set-option', '-t', '$7', 'remain-on-exit', 'on'),
    ]
    assert all(call[2] == '=$7' for call in calls if call[0] == 'list-panes')
    assert all(call[call.index('-t') + 1] == '%7'
               for call in calls if call[0] in {'pipe-pane', 'send-keys'})
    assert not any('cortex-markdown-smoke:0.0' in call for call in calls)
    assert not (project/'.codex/cortex/phase2-launch-failure.json').exists()
    marker = json.loads((project/'.codex/cortex/phase2-launch-transaction.json').read_text())
    assert marker['status'] == 'launched'
    next_workdir = (tmp_path/'next-workdir').resolve(); next_workdir.mkdir()
    globals_['_refuse_failed_phase2_launch'](next_workdir)


@pytest.mark.parametrize('failure_mode', ['first-committed-receipt', 'all-postcommit-receipts'])
def test_cli_postcommit_receipt_io_failure_remains_typed_and_refuses_control_and_workdir(
        tmp_path, monkeypatch, failure_mode):
    import hashlib
    import runpy
    from types import SimpleNamespace

    owner = tmp_path/'owner'; candidate = owner/'.cortex-dev/.codex/cortex-candidate.json'
    candidate.parent.mkdir(parents=True, mode=0o700); candidate.parent.chmod(0o700)
    candidate.write_text(json.dumps({'digest': hashlib.sha256(b'candidate').hexdigest()}))
    candidate.chmod(0o600); owner.chmod(0o700)
    monkeypatch.setattr(Path, 'home', lambda: owner)
    project = (tmp_path/'project').resolve(); project.mkdir()
    subprocess.run(['git', 'init', '-q', str(project)], check=True)
    cli = runpy.run_path(str(ROOT/'scripts/cortex-live-smoke'), run_name='transport')
    globals_ = cli['start'].__globals__; state_root = tmp_path/'cli-state'
    monkeypatch.setitem(globals_, 'STATE', state_root)
    monkeypatch.setitem(globals_, 'PHASE2_CONTROL_SHA256', 'c' * 64)
    tmux_calls = []
    def fake_tmux(*args, **kwargs):
        tmux_calls.append(args)
        if args[:2] == ('has-session', '-t'):
            return SimpleNamespace(returncode=1, stdout='')
        if args[0] == 'new-session':
            return SimpleNamespace(returncode=0, stdout='$51\n')
        if args[0] == 'list-panes':
            return SimpleNamespace(
                returncode=0,
                stdout='510|cortex-markdown-smoke|$51|511|%51|5151\n',
            )
        return SimpleNamespace(returncode=0, stdout='')
    monkeypatch.setitem(globals_, 'tmux', fake_tmux)
    original_atomic = globals_['_atomic_private_json']
    injected = {'done': False}
    def failing_atomic(path, value):
        status = value.get('status') if isinstance(value, dict) else None
        if failure_mode == 'first-committed-receipt' and status == 'committed' and not injected['done']:
            injected['done'] = True
            raise OSError('injected first post-commit receipt failure')
        if failure_mode == 'all-postcommit-receipts' and status in {'committed', 'failed', 'unusable'}:
            raise OSError('injected unavailable receipt path')
        return original_atomic(path, value)
    monkeypatch.setitem(globals_, '_atomic_private_json', failing_atomic)
    args = SimpleNamespace(workdir=project, resume_last=False, evaluation_fresh_store=True,
                           model=None, effort=None, codebase_memory=False, apps_enabled=False)

    with pytest.raises(cli['PostCommitLaunchFailure'], match='post_commit_launch_failure'):
        cli['start'](args)
    assert (project/'.codex/cortex/phase2-launch-transaction.json').is_file()
    assert any(call[0] == 'new-session' and call[-2:] == ('/bin/sleep', '30')
               for call in tmux_calls)
    assert not any(call[0] == 'new-session' and call[-1] == '/bin/bash'
                   for call in tmux_calls)
    other = tmp_path/'other'; other.mkdir()
    subprocess.run(['git', 'init', '-q', str(other)], check=True)
    with pytest.raises(RuntimeError, match='control (?:was consumed|has an incomplete prior launch transaction)'):
        cli['start'](args.__class__(**{**args.__dict__, 'workdir': other}))

    monkeypatch.setitem(globals_, 'PHASE2_CONTROL_SHA256', 'd' * 64)
    with pytest.raises(RuntimeError, match='workdir (?:was consumed|already has a launch transaction)'):
        globals_['_refuse_failed_phase2_launch'](project)


def test_cli_launch_transaction_crash_windows_are_fail_closed(tmp_path, monkeypatch):
    import runpy

    cli = runpy.run_path(str(ROOT/'scripts/cortex-live-smoke'), run_name='transport')
    globals_ = cli['start'].__globals__; state_root = tmp_path/'cli-state'
    monkeypatch.setitem(globals_, 'STATE', state_root)
    monkeypatch.setitem(globals_, 'PHASE2_CONTROL_SHA256', 'e' * 64)
    state_root.mkdir(mode=0o700)
    before = (tmp_path/'before').resolve(); before.mkdir()
    before_transaction = globals_['_prepare_launch_transaction'](before)
    assert not (before/'.codex').exists()
    with pytest.raises(RuntimeError, match='incomplete prior launch transaction'):
        globals_['_refuse_failed_phase2_launch'](tmp_path/'different')

    monkeypatch.setitem(globals_, 'PHASE2_CONTROL_SHA256', 'f' * 64)
    after = (tmp_path/'after').resolve(); after.mkdir()
    transaction = None
    def prepared():
        nonlocal transaction
        transaction = globals_['_prepare_launch_transaction'](after)
        return transaction
    receipt = cli['prepare_evaluation_fresh_store'](after, prepared)
    assert receipt['run_id'] == transaction['record']['transaction_id']
    marker = json.loads((after/'.codex/cortex/phase2-launch-transaction.json').read_text())
    assert marker['status'] == 'committing'
    with pytest.raises(RuntimeError, match='incomplete prior launch transaction'):
        globals_['_refuse_failed_phase2_launch'](tmp_path/'another')


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
    (cli_state/'session.json').write_text(json.dumps({
        'workdir':str(project),'store':str(store),
        'tmux_session_id':'$1','tmux_pane_id':'%1',
    }))
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
    provenance=helper['safe_receipt_provenance']
    assert provenance([{'text':'{"exit_code":0,"output":"private content"}'}])==(0,False)
    assert provenance([{'text':'{"exit_code":0}{"exit_code":2}'}])==(None,False)
    assert provenance([{'text':'Warning: truncated output'}])==(None,True)
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
    for source in (
        'tools["mcp__codex_app__send_message_to_thread"]({threadId:"x"})',
        'const send = tools.mcp__codex_app__send_message_to_thread; await send({threadId:"x"})',
        'const send = tools["mcp__codex_app__send_message_to_thread"]; send({threadId:"x"})',
    ):
        assert [name for name,_,_ in helper['nested_tool_invocations'](source)] == [
            'mcp__codex_app__send_message_to_thread']
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
        'forbidden_plugin_or_cache_access','forbidden_worker_app_thread_message',
        'worker_tool_after_successful_write_report'
    ]
    app_message=helper['call_policy_violations']([
        {'thread_id':'worker','parent_thread_id':'root','role':'technical_writer',
         'tool':'send_message_to_thread','tool_namespace':'codex_app',
         'outcome':'success'},
    ])
    assert [item['violation'] for item in app_message]==[
        'forbidden_worker_app_thread_message'
    ]
    wrapped_app_message=helper['call_policy_violations']([
        {'thread_id':'worker','parent_thread_id':'root','role':'technical_writer',
         'tool':'mcp__codex_app__send_message_to_thread','outcome':'success',
         'nested':True},
    ])
    assert [item['violation'] for item in wrapped_app_message]==[
        'forbidden_worker_app_thread_message'
    ]
    assert helper['orchestration_policy_violations'](app_message)==app_message
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
        'apply_patch','*** Update File: /tmp/project/.cortex/draft-reports/d_123456789abc.md'
    )=={'cortex_draft_edit':True,'edited_draft_ids':['d_123456789abc'],'ordinary_draft_edit':True}
    assert helper['draft_call_metadata'](
        'apply_patch','*** Update File: .cortex/draft-reports/d_123456789abc.md'
    )=={'cortex_draft_edit':True,'edited_draft_ids':['d_123456789abc'],'ordinary_draft_edit':True}
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
        {'thread_id':'root','role':'coordinator','tool':'spawn_agent','outcome':'success',
         'model':'gpt-5.6-luna','reasoning_effort':'high',
         'requested_model':'gpt-5.6-luna','requested_reasoning_effort':'medium',
         'fork_turns':'none',
         'assigned_profile':'general'},
        {'thread_id':'root','role':'coordinator','tool':'wait_agent','outcome':'success'},
        {'thread_id':'root','role':'coordinator','tool':'list_agents','outcome':'success'},
        {'thread_id':'root','role':'coordinator','tool':'send_message','outcome':'success'},
    ])
    assert [item['violation'] for item in coordinator_policy]==[
        'coordinator_unsolicited_message_after_wait'
    ]
    assert helper['call_policy_flags'](
        'apply_patch','*** Update File: /tmp/project/.cortex/draft-reports/d_1.md',
        'coordinator','/tmp/project')==[]
    duplicate_owner=helper['call_policy_violations']([
        {'thread_id':'root','role':'coordinator','tool':'spawn_agent','outcome':'success',
         'agent_type':'frontend_dev','model':'gpt-5.6-luna',
         'requested_model':'gpt-5.6-luna','requested_reasoning_effort':'medium',
         'fork_turns':'none'},
        {'thread_id':'root','role':'coordinator','tool':'wait_agent','outcome':'success'},
        {'thread_id':'root','role':'coordinator','tool':'spawn_agent','outcome':'success',
         'agent_type':'frontend_dev','requested_model':'gpt-5.6-luna',
         'requested_reasoning_effort':'medium','fork_turns':'none'},
    ])
    assert duplicate_owner==[]  # The same profile does not prove resource overlap.
    released_owner=helper['call_policy_violations']([
        {'thread_id':'root','role':'coordinator','tool':'spawn_agent','outcome':'success',
         'agent_type':'frontend_dev','requested_model':'gpt-5.6-luna',
         'requested_reasoning_effort':'medium','fork_turns':'none'},
        {'thread_id':'worker','parent_thread_id':'root','role':'frontend_dev',
         'tool':'mcp__cortex__write_report','outcome':'success'},
        {'thread_id':'root','role':'coordinator','tool':'spawn_agent','outcome':'success',
         'agent_type':'frontend_dev','requested_model':'gpt-5.6-luna',
         'requested_reasoning_effort':'medium','fork_turns':'none'},
    ])
    assert not any(item['violation']=='coordinator_duplicate_active_mutation_owner'
                   for item in released_owner)
    model_route=helper['call_policy_violations']([
        {'thread_id':'root','role':'coordinator','tool':'spawn_agent','outcome':'success',
         'agent_type':'planner','model':'gpt-5.6-luna',
         'requested_model':'gpt-5.6-luna','requested_reasoning_effort':'medium',
         'fork_turns':'none'},
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
    assert unresolved==[]
    assert [(row['tool'],row['effective_outcome']) for row in resolved]==[
        ('exec_command','truncated')
    ]
    unresolved,resolved=helper['classify_host_failures']([
        {'thread_id':'w','tool':'functions.exec','argument_digest':'poll-wrapper',
         'result_digest':'failed-poll','outcome':'covered_by_nested',
         'wrapper_outcome':'error'},
        {'thread_id':'w','tool':'exec_command','argument_digest':'poll-command',
         'result_digest':'failed-poll','outcome':'covered_by_command_execution',
         'wrapper_outcome':'error'},
        {'thread_id':'w','tool':'write_stdin','argument_digest':'poll',
         'result_digest':'failed-poll','intent_digest':'build','outcome':'error'},
        {'thread_id':'w','tool':'exec_command','argument_digest':'retry',
         'intent_digest':'build','outcome':'success'},
    ])
    assert [row['tool'] for row in unresolved]==['exec_command']
    assert {row['tool'] for row in resolved}=={'write_stdin'}


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


def test_coordinator_cannot_finalize_active_or_unreconciled_work():
    text=(PLUGIN/'skills/orchestrator/SKILL.md').read_text()
    assert 'Never emit a terminal final while an assigned owner is active or a required' in text
    assert 'Interim updates are non-terminal.' in text
    assert 'Before acceptance/final, reconcile\nassignments with native worker state/evidence' in text
    assert 'A wait timeout is only no new evidence, never\ncompletion' in text
    assert 'repeat bounded native wait for the same owner.' in text
    assert 'Record terminal failure/cancellation before final.' in text
    assert 'Timeout or unavailable\nobservation is not failure/cancellation.' in text
    assert 'Never inspect installed plugin/cache/candidate paths or agent registries.' in text


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
    state_root=tmp_path/'state';state_root.mkdir()
    control='c'*64;receipt='d'*64
    monkeypatch.setattr(sys,'argv',['cortex-live-smoke','send','--prompt-file',str(prompt)])
    monkeypatch.setitem(namespace,'state',lambda: {
            'tmux_session_id':'$1','tmux_pane_id':'%1','workdir':str(tmp_path),
            'phase2_control_sha256':control,'session_receipt':receipt,
            'resumed':False,'first_submission_at':None,'started_at':100,
    })
    monkeypatch.setitem(namespace,'STATE',state_root)
    monkeypatch.setitem(namespace,'save',lambda _: None)
    monkeypatch.setitem(namespace,'user_prompt_receipts',lambda *_: 0)
    monkeypatch.setitem(namespace,'_require_trust_receipt',lambda *_: {'status':'accepted'})
    monkeypatch.setitem(namespace,'_require_empty_composer',lambda *_: 'empty')
    monkeypatch.setitem(namespace,'tmux',lambda *args,**kwargs: sent.append(args))
    monkeypatch.setitem(namespace,'time',types.SimpleNamespace(sleep=lambda _: None,time=lambda: 123))
    import pytest
    with pytest.raises(RuntimeError,match='inspect the composer'):
        main()
    assert sum(call[0]=='paste-buffer' and '-p' in call for call in sent)==1
    assert next(call[-1] for call in sent if call[0]=='set-buffer')=='An ordinary task\n\n  Preserve indentation and  two spaces.'
    assert sum(call[-1]=='Enter' for call in sent)==1
    assert all('C-u' not in call for call in sent)


def test_ordinary_cli_trust_composer_send_needs_no_phase2_identity(monkeypatch,tmp_path):
    import runpy
    import sys
    import types

    helper=runpy.run_path(str(ROOT/'scripts/cortex-live-smoke'),run_name='ordinary_transport_test')
    main=helper['main'];namespace=main.__globals__
    prompt=tmp_path/'prompt.txt';prompt.write_text('Ordinary exact task\n')
    data={
        'workdir':str(tmp_path),'started_at':100,'thread_created_since':100,
        'tmux_pane_id':'%7','resumed':False,'original_request_sha256':'a'*64,
        'first_submission_at':None,'lifecycle_status':'started',
    }
    sent=[];saved=[];receipts=iter((0,0,1))
    monkeypatch.setitem(namespace,'state',lambda:data)
    monkeypatch.setitem(namespace,'save',lambda value:saved.append(dict(value)))
    monkeypatch.setitem(namespace,'_require_empty_composer',lambda *_:'› Ask Codex to do anything')
    monkeypatch.setitem(namespace,'user_prompt_receipts',lambda *_:next(receipts))
    monkeypatch.setitem(namespace,'tmux',lambda *args,**kwargs: sent.append(args) or types.SimpleNamespace(returncode=0))
    monkeypatch.setitem(namespace,'time',types.SimpleNamespace(sleep=lambda _:None,time=lambda:123.0))

    monkeypatch.setattr(sys,'argv',['cortex-live-smoke','enter'])
    assert main() is None
    monkeypatch.setattr(sys,'argv',['cortex-live-smoke','send','--prompt-file',str(prompt)])
    assert main() is None
    assert 'phase2_control_sha256' not in data and 'session_receipt' not in data
    assert sum(call[0]=='paste-buffer' for call in sent)==1
    assert sum(call[-1]=='Enter' for call in sent)==2
    assert saved[-1]['first_submission_at']==123.0

    calls_after_first=list(sent);saves_after_first=list(saved)
    with pytest.raises(RuntimeError,match='already submitted; refusing another transport'):
        main()
    assert sent==calls_after_first and saved==saves_after_first
    assert data['first_submission_at']==123.0


def test_ordinary_resumed_cli_refuses_send_before_observation_or_state_change(monkeypatch,tmp_path):
    import runpy
    import sys

    helper=runpy.run_path(str(ROOT/'scripts/cortex-live-smoke'),run_name='ordinary_resume_transport_test')
    main=helper['main'];namespace=main.__globals__
    prompt=tmp_path/'prompt.txt';prompt.write_text('Do not replay this request\n')
    data={
        'workdir':str(tmp_path),'started_at':100,'thread_created_since':90,
        'tmux_pane_id':'%7','resumed':True,'original_request_sha256':'a'*64,
        'first_submission_at':77.0,'lifecycle_status':'started',
    }
    sent=[];saved=[]
    monkeypatch.setattr(sys,'argv',['cortex-live-smoke','send','--prompt-file',str(prompt)])
    monkeypatch.setitem(namespace,'state',lambda:data)
    monkeypatch.setitem(namespace,'save',lambda value:saved.append(dict(value)))
    monkeypatch.setitem(namespace,'_require_empty_composer',lambda *_:pytest.fail('repeat inspected composer'))
    monkeypatch.setitem(namespace,'user_prompt_receipts',lambda *_:pytest.fail('repeat inspected receipts'))
    monkeypatch.setitem(namespace,'tmux',lambda *args,**kwargs:sent.append(args))
    with pytest.raises(RuntimeError,match='already submitted; refusing another transport'):
        main()
    assert sent==[] and saved==[] and data['first_submission_at']==77.0


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
        json.dumps(dict(timestamp=datetime.fromtimestamp(100,timezone.utc).isoformat(),
                       type='session_meta',payload=dict(
            id='root',cwd='/project',parent_thread_id=None,
        ))),
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
    state=dict(workdir='/project',started_at=200,thread_created_since=100,resumed=True,
               events=str(tmp_path/'events'),phase2_control_sha256='c'*64,session_receipt='d'*64)
    cli=runpy.run_path(str(ROOT/'scripts/cortex-live-smoke'),run_name='transport')
    monkeypatch.setitem(cli['native_user_turn_receipts'].__globals__,
                        '_owned_native_rollout_sources',lambda _state: {
                            rollout.resolve(): dict(
                                owned_codex_pid=4321,owned_codex_start_ticks=987,
                                owned_rollout_descriptors=[dict(
                                    fd=9,device=rollout.stat().st_dev,inode=rollout.stat().st_ino,
                                )],
                            ),
                        })
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
    assert len(skills)==23
    assert len(agent_references)==3
    assert len(references)==69
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
    spawn=row('spawn_agent',fork_turns='none',assigned_profile='general',
              model='gpt-5.6-luna',reasoning_effort='high',
              requested_model='gpt-5.6-luna',requested_reasoning_effort='medium')
    assert check(begin+[spawn])==[]
    assert check(begin+[row('mcp__cortex__write_report'),spawn])==[]
    assert check([row('mcp__cortex__read_report',document_kind='pipeline'),spawn])==[]


def test_live_git_probe_is_advisory_and_launchers_require_existing_git_root(tmp_path):
    import runpy
    h=runpy.run_path(str(ROOT/'scripts/cortex-desktop-dev'),run_name='observer')
    row={'thread_id':'worker','role':'technical_writer','tool':'exec_command',
         'outcome':'error','policy_flags':['git_command_without_git_workspace']}
    assert h['call_policy_violations']([row])==[]
    marker=tmp_path/'user-file';marker.write_text('preserve me')
    with pytest.raises(RuntimeError,match='must already be the root of a Git repository'):
        h['ensure_git_workspace'](tmp_path)
    assert marker.read_text()=='preserve me'
    assert not (tmp_path/'.git').exists()
    subprocess.run(['git','init'],cwd=tmp_path,check=True,capture_output=True)
    h['ensure_git_workspace'](tmp_path)
    assert subprocess.run(['git','config','--local','--get','user.name'],cwd=tmp_path,
                          capture_output=True,text=True).returncode!=0

    cli=runpy.run_path(str(ROOT/'scripts/cortex-live-smoke'),run_name='observer')
    other=tmp_path/'nonempty';other.mkdir();(other/'user-file').write_text('preserve me')
    with pytest.raises(RuntimeError,match='must already be the root of a Git repository'):
        cli['ensure_git_workspace'](other)
    assert not (other/'.git').exists()


def test_write_report_description_matches_artifact_schema():
    source=(ROOT/'plugins/cortex/scripts/cortex_runtime/contracts.py').read_text()
    assert 'Metadata arrays use the advertised item schemas' in source
    assert 'Never put Markdown bodies or shell interpolation' in source
    assert 'writer metadata, arrays, chunks' not in source


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
