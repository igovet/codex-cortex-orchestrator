"""Behavioral regressions for task isolation, metadata recovery and offline upgrade."""
import hashlib
import json
from pathlib import Path
import sqlite3

import pytest

from cortex_runtime.contracts import StoreError
from cortex_runtime.host_source import NativeSource
from cortex_runtime.store import Store
from cortex_runtime.migrate import migrate
from test_markdown_store import create, write, key
from storage_fixture import call_store, thread_for


def test_corrupt_neighbor_never_blocks_restart_or_other_task(tmp_path):
    store=Store(tmp_path/'private')
    damaged=create(store,tmp_path); write(store,damaged,'Committed pipeline',kind='pipeline')
    good=create(store,tmp_path)
    (tmp_path/'.codex/cortex'/damaged/'pipeline.md').write_text('Corrupted')
    restarted=Store(tmp_path/'private')
    assert call_store(restarted,'list_reports',dict(task_id=good))['reports']
    with pytest.raises(StoreError,match='file_conflict'):
        call_store(restarted,'list_reports',dict(task_id=damaged))


def test_archive_reads_survive_capture_failure(tmp_path):
    store=Store(tmp_path/'private'); task=create(store,tmp_path)
    report=call_store(store,'list_reports',dict(task_id=task))['reports'][0]['report_id']
    def unavailable(*_):raise StoreError('host_request_unavailable')
    result=store.call('read_report',dict(report_id=report),thread_for(store,task),steering_source=unavailable)
    assert result['markdown']=='Original request'
    assert result['source_capture']==dict(status='unavailable',reason='host_request_unavailable',revision=1,pending_turns=0)


def test_checked_pages_skip_unchanged_full_reads_and_detect_replacement(tmp_path,monkeypatch):
    import cortex_runtime.store as module
    store=Store(tmp_path/'private');task=create(store,tmp_path)
    report=write(store,task,'αβ🙂'*60000)['report_id']
    original=module.file_blocks; scans=[]
    def tracked(path,*args,**kwargs):
        scans.append(str(path));yield from original(path,*args,**kwargs)
    monkeypatch.setattr(module,'file_blocks',tracked)
    first=call_store(store,'read_report',dict(task_id=task,report_id=report))
    count=len(scans)
    second=call_store(store,'read_report',dict(task_id=task,cursor=first['next_cursor']))
    assert len(scans)==count and len(second['markdown'])==4000
    path=tmp_path/'.codex/cortex'/task/(report+'.md')
    path.write_text('Altered')
    with pytest.raises(StoreError,match='file_conflict'):
        call_store(store,'read_report',dict(task_id=task,report_id=report))


def test_own_drafts_bindings_revision_and_artifact_metadata(tmp_path):
    store=Store(tmp_path/'private');task=create(store,tmp_path);thread=thread_for(store,task)
    mine=store.call('create_draft',dict(template='general',request_key=key()),thread)
    child=store.call('create_draft',dict(template='general',request_key=key()),'worker',thread)
    listing=store.call('list_reports',{},thread)
    assert [draft['draft_id'] for draft in listing['own_drafts']]==[mine['draft_id']]
    assert listing['binding']['receipt']!=child['binding']['receipt']
    result=write(store,task,source_revision=1,artifacts=[dict(reference='src/app.py',version='commit:abc')])
    read=store.call('read_report',dict(report_id=result['report_id']),thread)
    assert read['source_revision']==1 and read['artifacts']==result['artifacts']
    normal=store.call('set_governance',dict(mode='minimal',rationale='User requested normal mode.',state='normal',request_key=key()),thread)
    assert normal['binding']['state']=='normal'
    def forbidden(*_):raise AssertionError('normal state must not capture')
    assert store.call('list_reports',{},thread,steering_source=forbidden)['source_capture']['status']=='not_attempted'


def test_deferred_hooks_preserve_distinct_native_messages(tmp_path):
    from cortex_runtime.hook_storage import HookStorage
    store=Store(tmp_path/'private');task=create(store,tmp_path);thread=thread_for(store,task)
    hooks=HookStorage(store);context=hooks.context(thread,str(tmp_path))
    hooks.note_prompt(context,'turn-1')
    def native(*_):
        return NativeSource('Same exact text',cursor={'test':1},messages=[
            dict(id='native-1',turn='turn-1',text='Same exact text',attachments=[]),
            dict(id='native-2',turn='turn-1',text='Same exact text',attachments=[])])
    first=store.call('list_reports',{},thread,steering_source=native)
    assert first['source_capture']['revision']==3
    with store.connection() as db:
        assert not db.execute('SELECT 1 FROM hook_pending_sources').fetchall()
    assert store.call('list_reports',{},thread,steering_source=native)['source_capture']['revision']==3


def test_offline_migration_needs_new_backup_and_never_changes_markdown(tmp_path):
    store=Store(tmp_path/'private');task=create(store,tmp_path)
    write(store,task,request_key='accepted-v10')
    with store.connection() as db:
        draft_id=db.execute("SELECT id FROM drafts WHERE published_report_id IS NOT NULL").fetchone()[0]
        delivery=db.execute("SELECT response FROM deliveries WHERE operation='write_report'").fetchone()[0]
        legacy=json.loads(delivery);legacy.pop('source_revision');legacy.pop('artifacts')
        db.execute("UPDATE deliveries SET response=? WHERE operation='write_report'",(json.dumps(legacy),))
    path=tmp_path/'.codex/cortex'/task
    before={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in path.iterdir()}
    # Build an authentic format-10 metadata layout by removing only v11 additions.
    with store.connection() as db:
        for table in ('binding_receipts','source_revisions','report_provenance','draft_revisions','task_changes','source_turns','hook_events','hook_hints','hook_agent_bindings','hook_pending_sources','source_resets'):
            db.execute('DROP TABLE '+table)
        db.execute('PRAGMA user_version=10')
    with pytest.raises(StoreError,match='unsupported_storage'):Store(store.directory)
    backup=tmp_path/'backup.sqlite3'
    with pytest.raises(StoreError,match='migration_requires_stopped_access'):migrate(store.directory,backup)
    result=migrate(store.directory,backup,access_stopped=True)
    assert result['to_version']==11 and backup.is_file()
    assert sqlite3.connect(backup).execute('PRAGMA user_version').fetchone()[0]==10
    assert before=={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in path.iterdir()}
    upgraded=Store(store.directory)
    assert call_store(upgraded,'list_reports',dict(task_id=task))['reports']
    replay=call_store(upgraded,'write_report',dict(task_id=task,title='Work',summary='A concise result.',author='worker',draft_id=draft_id,request_key='accepted-v10'))
    assert replay['replayed'] and replay['source_revision']==0 and replay['artifacts']==[]


def test_normal_interval_is_not_archived_and_current_reactivation_is_preserved(tmp_path):
    store=Store(tmp_path/'private');task=create(store,tmp_path);thread=thread_for(store,task)
    def current(message_id,turn,text):
        def read(project,cursor):
            assert cursor is None
            return NativeSource(text,{'offset':message_id},[dict(id=message_id,turn=turn,text=text,attachments=[])])
        return read
    normal_args=dict(mode='minimal',rationale='Pause Cortex.',state='normal',request_key=key())
    store.call('set_governance',normal_args,thread,steering_source=current('a','turn-a','Use normal mode'))
    def ordinary(*_):raise AssertionError('normal task must not capture ordinary messages')
    store.call('list_reports',{},thread,steering_source=ordinary)
    resume_args=dict(mode='light',rationale='Resume Cortex.',state='cortex',request_key=key())
    resumed=store.call('set_governance',resume_args,thread,steering_source=current('c','turn-c','Resume and add requirement C'))
    assert resumed['source_capture']['status']=='partial'
    bodies=[store.call('read_report',dict(report_id=r['report_id']),thread)['markdown'] for r in store.call('list_reports',{},thread)['reports']]
    assert 'Resume and add requirement C' in bodies
    # Replaying the old pause receipt does not reapply its historical state.
    replay=store.call('set_governance',normal_args,thread)
    assert replay['replayed'] and replay['binding']['state']=='cortex'


def test_unavailable_resume_boundary_remains_pending_until_authoritative_source(tmp_path):
    store=Store(tmp_path/'private');task=create(store,tmp_path);thread=thread_for(store,task)
    source=NativeSource('Pause',{'offset':1},[dict(id='a',turn='turn-a',text='Pause',attachments=[])])
    store.call('set_governance',dict(mode='minimal',rationale='Pause.',state='normal',request_key=key()),thread,steering_source=lambda *_:source)
    def unavailable(*_):raise StoreError('host_request_unavailable')
    result=store.call('set_governance',dict(mode='minimal',rationale='Resume.',state='cortex',request_key=key()),thread,steering_source=unavailable)
    assert result['source_capture']['reason']=='resume_boundary_unavailable'
    with store.connection() as db:assert db.execute('SELECT 1 FROM source_resets').fetchone()
    def restored(project,cursor):
        assert cursor is None
        return NativeSource('Current',{'offset':3},[dict(id='c',turn='turn-c',text='Current',attachments=[])])
    assert store.call('list_reports',{},thread,steering_source=restored)['source_capture']['revision']==2


def test_database_open_failure_releases_migration_lock(tmp_path,monkeypatch):
    import fcntl
    import os
    import cortex_runtime.store as module
    store=Store(tmp_path/'private')
    def failed(*_args,**_kwargs):raise sqlite3.DatabaseError('invalid database')
    monkeypatch.setattr(module.sqlite3,'connect',failed)
    with pytest.raises(sqlite3.DatabaseError):
        with store.connection():pass
    lock=os.open(store.directory/'.access.lock',os.O_RDWR)
    try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    finally:os.close(lock)


def test_recovery_restores_invalid_utf8_pipeline_and_removes_unindexed_first_edition(tmp_path):
    store=Store(tmp_path/'private');task=create(store,tmp_path)
    write(store,task,'Committed',kind='pipeline')
    pipeline=tmp_path/'.codex/cortex'/task/'pipeline.md'
    saved=pipeline.read_bytes();backup=pipeline.parent/'.backup_pipeline_test.md'
    backup.write_bytes(saved);backup.chmod(0o600);pipeline.write_bytes(b'\xff')
    call_store(Store(store.directory),'list_reports',dict(task_id=task))
    assert pipeline.read_bytes()==saved and not backup.exists()
    other=create(store,tmp_path);orphan=tmp_path/'.codex/cortex'/other/'pipeline.md'
    orphan.write_text('Uncommitted first edition');orphan.chmod(0o600)
    call_store(Store(store.directory),'list_reports',dict(task_id=other))
    assert not orphan.exists()


def test_pending_deletion_commits_metadata_before_rejecting_task(tmp_path):
    store=Store(tmp_path/'private');task=create(store,tmp_path)
    with store.connection() as db:db.execute('INSERT INTO pending_deletions VALUES (?)',(task,))
    with pytest.raises(StoreError,match='task_not_bound'):call_store(store,'list_reports',dict(task_id=task))
    with store.connection() as db:
        assert not db.execute('SELECT 1 FROM tasks WHERE id=?',(task,)).fetchone()
        assert not db.execute('SELECT 1 FROM pending_deletions WHERE task_id=?',(task,)).fetchone()


def test_bounded_changes_expose_only_compact_hook_receipts_after_restart(tmp_path):
    from cortex_runtime.hook_storage import HookStorage
    from cortex_runtime.contracts import OUTPUTS
    import jsonschema
    store=Store(tmp_path/'private');task=create(store,tmp_path);thread=thread_for(store,task)
    hooks=HookStorage(store);context=hooks.context(thread,str(tmp_path))
    paths=[str(tmp_path/('artifact-'+str(index))) for index in range(20)]
    hooks.record(context,'PostToolUse','command-running',dict(
        tool_name='exec_command',command_session_id='42',status='running',truncated=True,
        changed_paths=paths,actor_scope='actor',actor_thread_id=thread,parent_session_id=thread,
        binding_origin='native_mcp',output='PRIVATE RAW OUTPUT',command='SECRET COMMAND',prompt='SECRET SOURCE'))
    hooks.record(context,'PostToolUse','command-exit',dict(
        tool_name='write_stdin',command_session_id='42',status='exited',exit_code=0,
        actor_scope='session',actor_thread_id=None,parent_session_id=thread,binding_origin='native_mcp'))
    restarted=Store(store.directory)
    result=call_store(restarted,'list_reports',dict(task_id=task,limit=100))
    jsonschema.validate(result,OUTPUTS['list_reports'])
    observations=[change['observation'] for change in result['changes'] if change['observation']]
    running=next(item for item in observations if item['status']=='running')
    exited=next(item for item in observations if item['status']=='exited')
    assert running['source']=='hook' and running['actor_thread_id']==thread
    assert running['command_session_id']=='42' and running['truncated'] is True and running['exit_code'] is None
    assert running['changed_paths']==paths[:16] and running['changed_paths_total']==20
    assert running['changed_paths_complete'] is False
    assert exited['exit_code']==0 and exited['actor_scope']=='session' and exited['actor_thread_id'] is None
    assert 'PRIVATE RAW OUTPUT' not in json.dumps(result) and 'SECRET' not in json.dumps(result)
    page=call_store(restarted,'list_reports',dict(task_id=task,limit=1))
    assert len(page['changes'])==1 and page['changes_next'] is not None
    next_page=call_store(restarted,'list_reports',dict(task_id=task,limit=1,changes_after=page['changes_next']))
    assert next_page['changes'][0]['sequence']>page['changes'][0]['sequence']


def test_normal_transition_retires_only_accepted_skipped_pending_signals(tmp_path,monkeypatch):
    from cortex_runtime.hook_storage import HookStorage
    store=Store(tmp_path/'private');task=create(store,tmp_path);thread=thread_for(store,task)
    hooks=HookStorage(store);context=hooks.context(thread,str(tmp_path))
    hooks.note_prompt(context,'turn-normal')
    other=create(store,tmp_path);other_thread=thread_for(store,other)
    hooks.note_prompt(hooks.context(other_thread,str(tmp_path)),'turn-other')
    def current(message_id,turn,text):
        return lambda *_:NativeSource(text,{'offset':message_id},[dict(id=message_id,turn=turn,text=text,attachments=[])])
    pause=dict(mode='minimal',rationale='Pause capture.',state='normal',request_key=key())
    paused=store.call('set_governance',pause,thread,steering_source=current('normal','turn-normal','Use normal mode'))
    assert paused['source_capture']['pending_turns']==0
    assert paused['source_capture']['status']=='partial'
    assert paused['source_capture']['reason']=='inactive_interval_started_pending_sources_retired'
    assert paused['source_capture']['revision']==1
    restarted=Store(store.directory)
    with restarted.connection() as db:
        assert not db.execute('SELECT 1 FROM hook_pending_sources WHERE thread_id=?',(thread,)).fetchone()
        assert db.execute('SELECT 1 FROM hook_pending_sources WHERE thread_id=?',(other_thread,)).fetchone()
    resumed=restarted.call('set_governance',dict(mode='minimal',rationale='Resume.',state='cortex',request_key=key()),thread,
        steering_source=current('resume','turn-resume','Resume Cortex'))
    assert resumed['source_capture']['revision']==2 and resumed['source_capture']['pending_turns']==0
    hooks=HookStorage(restarted);context=hooks.context(thread,str(tmp_path));hooks.note_prompt(context,'turn-new')
    replay=restarted.call('set_governance',pause,thread)
    assert replay['replayed'] and replay['binding']['state']=='cortex'
    assert replay['source_capture']['pending_turns']==1
    original_change=restarted._change
    def fail_gap(db,task_id,kind,reference=None):
        if kind=='source_gap':raise StoreError('storage_error')
        return original_change(db,task_id,kind,reference)
    monkeypatch.setattr(restarted,'_change',fail_gap)
    with pytest.raises(StoreError,match='storage_error'):
        restarted.call('set_governance',dict(pause,request_key=key()),thread,
            steering_source=current('new','turn-new','Pause again'))
    listing=restarted.call('list_reports',{},thread)
    assert listing['binding']['state']=='cortex' and listing['source_capture']['pending_turns']==1
    gaps=[change for change in listing['changes'] if change['kind']=='source_gap']
    assert len(gaps)==1 and gaps[0]['reference']=='inactive_interval_started_pending_sources_retired'


def test_accepted_write_replay_does_not_apply_old_redactions_to_new_source(tmp_path):
    store=Store(tmp_path/'private');task=create(store,tmp_path);thread=thread_for(store,task)
    draft=store.call('create_draft',dict(template='general',request_key=key()),thread)
    path=Path(draft['draft_path']);marker=path.read_text().split('\n\n',1)[0]+'\n\n'
    path.write_text(marker+'Checked result.')
    old=NativeSource('Use old-secret',{'offset':1},[dict(id='old',turn='turn-old',text='Use old-secret',attachments=[])])
    args=dict(title='Result',summary='Checked.',author='worker',draft_id=draft['draft_id'],redact_values=['old-secret'],request_key=key())
    accepted=store.call('write_report',args,thread,steering_source=lambda *_:old)
    calls=[]
    newer=NativeSource('Add a new requirement',{'offset':2},[dict(id='new',turn='turn-new',text='Add a new requirement',attachments=[])])
    def new_source(project,cursor):calls.append(cursor);return newer
    replay=store.call('write_report',args,thread,steering_source=new_source)
    assert replay['replayed'] and not calls
    stable=lambda value:{k:v for k,v in value.items() if k not in {'binding','source_capture','replayed'}}
    assert stable(replay)==stable(accepted)
    assert replay['source_capture']['status']=='not_attempted'
    assert replay['source_capture']['revision']==accepted['source_capture']['revision']
    refreshed=store.call('list_reports',{},thread,steering_source=new_source)
    assert len(calls)==1 and refreshed['source_capture']['revision']==accepted['source_capture']['revision']+1
    bodies=[store.call('read_report',dict(report_id=row['report_id']),thread)['markdown'] for row in refreshed['reports']]
    assert 'Use [REDACTED]' in bodies and 'Add a new requirement' in bodies
