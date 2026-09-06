from storage_fixture import call_store, thread_for
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import sqlite3
import uuid

import pytest

from cortex_runtime.contracts import StoreError, TOOLS, validate
from cortex_runtime.store import Store


def key():return uuid.uuid4().hex

@pytest.fixture
def store(tmp_path):return Store(tmp_path/'private')


def create(store,tmp_path,**extra):
    return call_store(store,'create_task',dict(project_root=str(tmp_path),request='Original request',request_key=key(),**extra))['task_id']


def write(store,task,body='Free **Markdown** without a template.',**extra):
    request_key=extra.pop('request_key',key());kind=extra.pop('kind','report')
    draft=call_store(store,'create_draft',dict(task_id=task,template='pipeline' if kind=='pipeline' else 'general',request_key='draft-'+request_key))
    path=Path(draft['draft_path'])
    if path.exists():
        marker=path.read_bytes().split(b'\n\n',1)[0]+b'\n\n'; path.write_bytes(marker+body.encode())
    arguments=dict(task_id=task,title='Work',summary='A concise result.',author='worker',draft_id=draft['draft_id'],request_key=request_key)|extra
    return call_store(store,'write_report',arguments)


def test_exact_seven_tools():
    assert {t['name'] for t in TOOLS}=={'create_task','set_governance','create_draft','read_draft','write_report','list_reports','read_report'}
    assert len(TOOLS)==7
    with pytest.raises(StoreError,match='invalid_arguments'):
        validate('read_report',dict(limit=4001))
    with pytest.raises(StoreError,match='invalid_arguments'):
        validate('read_draft',dict(draft_id='d_'+'0'*12,limit=4001))


def test_pipeline_draft_rejects_unfilled_server_placeholder(store,tmp_path):
    task=create(store,tmp_path)
    draft=call_store(store,'create_draft',dict(task_id=task,template='pipeline',request_key=key()))
    path=Path(draft['draft_path'])
    assert '{{CURRENT_WORK_GRAPH}}' in draft['markdown']==path.read_text()
    assert draft['required_replacement_count']==6==len(draft['replaceable_markers'])
    assert draft['replaceable_markers'][0]=='{{CURRENT_OBJECTIVE_AND_STATUS}}'
    assert "Preserve its identity and first-line marker" in draft['edit_instruction']
    arguments=dict(task_id=task,title='Pipeline',summary='Current work is recorded.',
                   author='coordinator',draft_id=draft['draft_id'],request_key=key())
    with pytest.raises(StoreError) as rejected:
        call_store(store,'write_report',arguments)
    error=rejected.value
    assert error.args==('draft_guidance_remaining',)
    assert error.field=='draft_markdown'
    assert error.received.startswith('{{CURRENT_')
    assert path.is_file()
    body=path.read_text()
    for placeholder in [part.split('}}',1)[0]+'}}' for part in body.split('{{')[1:]]:
        body=body.replace('{{'+placeholder,'Complete current information.')
    path.write_text(body)
    assert call_store(store,'write_report',arguments)['report_id'].startswith('r_')
    assert not path.exists()


def test_short_ids_retry_collisions_without_overwriting(store,tmp_path,monkeypatch):
    import cortex_runtime.store as module
    first=create(store,tmp_path)
    original=call_store(store,'list_reports',dict(task_id=first))['reports'][0]['report_id']
    assert len(first)==len(original)==14
    generate=module.identifier
    seen={'t_':0,'r_':0}
    def collide_once(prefix):
        if prefix in seen:
            seen[prefix]+=1
            if seen[prefix]==1:return first if prefix=='t_' else original
        return generate(prefix)
    monkeypatch.setattr(module,'identifier',collide_once)
    second=create(store,tmp_path)
    report=call_store(store,'list_reports',dict(task_id=second))['reports'][0]['report_id']
    assert second!=first and report!=original
    assert seen=={'t_':2,'r_':2}
    assert call_store(store,'read_report',dict(task_id=first,report_id=original))['markdown']=='Original request'
    monkeypatch.setattr(module,'identifier',lambda prefix:original if prefix=='r_' else generate(prefix))
    with pytest.raises(StoreError,match='identifier_unavailable'):
        write(store,first)
    assert len(call_store(store,'list_reports',dict(task_id=first))['reports'])==1


def test_original_and_governance_recoverable(store,tmp_path):
    task=create(store,tmp_path)
    result=call_store(store,'set_governance',dict(task_id=task,mode='full',rationale='Substantial change.',request_key=key()))
    reports=call_store(store,'list_reports',dict(task_id=task))['reports']
    assert [r['author'] for r in reports]==['coordinator','user request']
    assert call_store(store,'read_report',dict(task_id=task,report_id=reports[1]['report_id']))['markdown']=='Original request'
    assert 'Substantial change.' in call_store(store,'read_report',dict(task_id=task,report_id=result['report_id']))['markdown']
    # Neither absent nor full governance prevents writing.
    assert write(store,task)['report_id']


def test_governance_changes_are_advisory_isolated_and_durable(store,tmp_path):
    task=create(store,tmp_path)
    other=create(store,tmp_path)
    pipeline=write(store,task,'Before governance',kind='pipeline')['report_id']
    recorded=[]
    for mode in ('minimal','full','light','minimal'):
        rationale=f'Observed risk warrants {mode}; no approval or scheduling is implied.'
        result=call_store(store,'set_governance',dict(task_id=task,mode=mode,rationale=rationale,request_key=key()))
        recorded.append((result['report_id'],mode+'\n\n'+rationale))
        assert write(store,task,mode,kind='pipeline')['report_id']==pipeline
        # Workers can still publish, list and read in every advisory mode.
        worker=write(store,task,'Evidence only, not a completion decision.')['report_id']
        assert call_store(store,'read_report',dict(task_id=task,report_id=worker))['markdown'].endswith('Evidence only, not a completion decision.')
    restarted=Store(store.directory)
    reports=call_store(restarted,'list_reports',dict(task_id=task))['reports']
    governance=[r['report_id'] for r in reports if r['title'].startswith('Governance: ')]
    assert governance==[rid for rid,_ in reversed(recorded)]
    for rid,body in recorded:
        assert call_store(restarted,'read_report',dict(task_id=task,report_id=rid))['markdown']==body
        with pytest.raises(StoreError,match='not_found'):
            call_store(restarted,'read_report',dict(task_id=other,report_id=rid))
    assert len(call_store(restarted,'list_reports',dict(task_id=other))['reports'])==1
    assert len(reports)==10  # Request, one pipeline, four choices and four worker reports.


def test_invalid_governance_has_no_partial_effect(store,tmp_path):
    task=create(store,tmp_path)
    before=call_store(store,'list_reports',dict(task_id=task))
    for extra in (dict(mode='automatic'),dict(rationale=''),dict(approval=True)):
        args=dict(task_id=task,mode='light',rationale='Moderate risk.',request_key=key())|extra
        with pytest.raises(StoreError,match='invalid_arguments'):
            call_store(store,'set_governance',args)
        assert call_store(store,'list_reports',dict(task_id=task))==before
    with store.connection() as db:
        assert db.execute('SELECT COUNT(*) FROM governance').fetchone()[0]==0


def test_roundtrip_verbatim_and_no_semantic_parser(store,tmp_path):
    task=create(store,tmp_path)
    for body in ['No headings','- [x] complete\nnot actually tested','{"status":"done"}', '# Свободный отчёт\n\n😀\r\n\tend\n']:
        result=write(store,task,body)
        assert call_store(store,'read_report',dict(task_id=task,report_id=result['report_id']))['markdown'].endswith(body)


def test_replay_all_writes_and_conflicts(store,tmp_path):
    args=dict(project_root=str(tmp_path),request='Original',request_key=key())
    first=call_store(store,'create_task',args);again=call_store(store,'create_task',args)
    assert again==first|dict(replayed=True)
    task=first['task_id']
    pipeline=call_store(store,'create_draft',dict(task_id=task,template='pipeline',request_key='replay-draft'))
    pipeline_path=Path(pipeline['draft_path']);marker=pipeline_path.read_text().split('\n\n',1)[0]+'\n\n';pipeline_path.write_text(marker+'Work')
    for operation,arguments in [
        ('set_governance',dict(task_id=task,mode='minimal',rationale='Bounded',request_key=key())),
        ('write_report',dict(task_id=task,title='Pipeline',summary='v1',author='coordinator',draft_id=pipeline['draft_id'],request_key=key()))]:
        result=call_store(store,operation,arguments)
        assert call_store(store,operation,arguments)==result|dict(replayed=True)
        changed=arguments|({'rationale':'changed'} if operation=='set_governance' else {'title':'changed'})
        with pytest.raises(StoreError,match='delivery_conflict'):call_store(store,operation,changed)
    assert call_store(store,'create_task',args|dict(request='Changed'))==first|dict(replayed=True)
    assert len(call_store(store,'list_reports',dict(task_id=task))['reports'])==3


def test_isolation_and_foreign_continuation(store,tmp_path):
    first=create(store,tmp_path);second=create(store,tmp_path)
    rid=write(store,first)['report_id']
    assert len(call_store(store,'list_reports',dict(task_id=second))['reports'])==1
    with pytest.raises(StoreError,match='not_found'):call_store(store,'read_report',dict(task_id=second,report_id=rid))
    cursor=call_store(store,'list_reports',dict(task_id=first,limit=1))['next_cursor']
    assert cursor.startswith('cl.') and len(cursor)<40 and first not in cursor
    with pytest.raises(StoreError,match='invalid_cursor'):call_store(store,'list_reports',dict(task_id=second,cursor=cursor))
    read_cursor=call_store(store,'read_report',dict(task_id=first,report_id=rid,limit=1))['next_cursor']
    assert read_cursor.startswith('cr.') and len(read_cursor)<55 and first not in read_cursor
    with pytest.raises(StoreError,match='invalid_cursor'):call_store(store,'read_report',dict(task_id=second,cursor=read_cursor))
    parts=read_cursor.split('.');parts[3]=('0' if parts[3][0]!='0' else '1')+parts[3][1:]
    with pytest.raises(StoreError,match='invalid_cursor'):
        call_store(store,'read_report',dict(task_id=first,report_id=rid,cursor='.'.join(parts)))
    # Same delivery key is independent across tasks.
    same=key();assert write(store,first,request_key=same)['report_id']!=write(store,second,request_key=same)['report_id']


def test_big_unicode_read_restart_and_stable_catalogue(store,tmp_path):
    task=create(store,tmp_path);body='😀З\n'*50000
    rid=write(store,task,body)['report_id']
    page=call_store(store,'list_reports',dict(task_id=task,limit=1))
    write(store,task,'later')
    store=Store(store.directory)
    next_page=call_store(store,'list_reports',dict(task_id=task,limit=1,cursor=page['next_cursor']))
    assert next_page['next_cursor'] is None and next_page['reports'][0]['title']=='Original user request'
    cursor=None;pieces=[]
    while True:
        result=call_store(store,'read_report',(dict(task_id=task,report_id=rid,limit=3999) if cursor is None else dict(task_id=task,cursor=cursor,limit=3999)))
        pieces.append(result['markdown']);cursor=result['next_cursor']
        if cursor is None:break
    complete=''.join(pieces)
    assert complete.endswith(body) and 'Cortex draft ID:' in complete[:80]
    assert len(call_store(store,'list_reports',dict(task_id=task))['reports'])==3


def test_concurrent_delivery_once_and_distinct_writes(store,tmp_path):
    task=create(store,tmp_path);delivery=key()
    draft=call_store(store,'create_draft',dict(task_id=task,template='general',request_key='draft-'+delivery))
    path=Path(draft['draft_path']);marker=path.read_text().split('\n\n',1)[0]+'\n\n';path.write_text(marker+'same report')
    arguments=dict(task_id=task,title='Work',summary='A concise result.',author='worker',draft_id=draft['draft_id'],request_key=delivery)
    def same(_):return call_store(Store(store.directory),'write_report',arguments)
    with ThreadPoolExecutor(max_workers=8) as pool:results=list(pool.map(same,range(16)))
    assert len({r['report_id'] for r in results})==1
    assert sum(not r['replayed'] for r in results)==1
    with ThreadPoolExecutor(max_workers=8) as pool:results=list(pool.map(lambda _:write(Store(store.directory),task),range(16)))
    assert len({r['report_id'] for r in results})==16
    assert len(call_store(store,'list_reports',dict(task_id=task))['reports'])==18


def test_atomic_rollback_and_immutable_reports(store,tmp_path,monkeypatch):
    task=create(store,tmp_path);original=store._publish_source
    def fail(*args):original(*args);raise RuntimeError('private payload')
    monkeypatch.setattr(store,'_publish_source',fail)
    with pytest.raises(RuntimeError):write(store,task)
    assert len(call_store(store,'list_reports',dict(task_id=task))['reports'])==1
    with store.connection() as db:
        for statement in ['UPDATE editions SET summary="changed"','DELETE FROM editions']:
            with pytest.raises(sqlite3.IntegrityError):db.execute(statement)
        assert db.execute('PRAGMA integrity_check').fetchone()[0]=='ok'


@pytest.mark.parametrize('change',[{'limit':0},{'limit':True},{'limit':101},{'extra':'private'}, {'cursor':'bad cursor'}])
def test_invalid_inputs(store,tmp_path,change):
    task=create(store,tmp_path)
    with pytest.raises(StoreError):call_store(store,'list_reports',dict(task_id=task)|change)


def test_file_backed_pipeline_and_private_paths(store,tmp_path):
    task=create(store,tmp_path)
    body='x'*250001
    pipeline=write(store,task,body,kind='pipeline')
    assert (tmp_path/'.codex/cortex'/task/'pipeline.md').read_text().endswith(body)
    assert (store.path.stat().st_mode&0o777)==0o600
    assert (store.directory.stat().st_mode&0o777)==0o700
    link=tmp_path/'link';link.symlink_to(store.directory,target_is_directory=True)
    with pytest.raises(StoreError):Store(link)
    alien=tmp_path/'alien';alien.mkdir(mode=0o700)
    with sqlite3.connect(alien/'cortex.sqlite3') as db:db.execute('CREATE TABLE foreign_data (body TEXT)')
    os.chmod(alien/'cortex.sqlite3',0o600)
    with pytest.raises(StoreError,match='unsupported_storage'):Store(alien)


def test_steering_pipeline_editions_and_recovery(store,tmp_path):
    task=create(store,tmp_path)
    first=write(store,task,'1. Build greeting. Worker: developer. Check its text.',title='Pipeline 1',kind='pipeline')['report_id']
    worker=Store(store.directory)
    listing=call_store(worker,'list_reports',dict(task_id=task))
    assert first in [r['report_id'] for r in listing['reports']]
    assert '1. Build greeting.' in call_store(worker,'read_report',dict(task_id=task,report_id=first))['markdown']
    write(worker,task,'Greeting implemented and checked.',author='developer')
    second=write(store,task,'User added a farewell.\n1. Greeting completed.\n2. Add farewell; check text.',title='Pipeline 2',kind='pipeline')['report_id']
    write(worker,task,'Farewell implemented and checked.',author='developer')
    write(store,task,'Greeting and farewell delivered; both checked.',title='Final result')
    restarted=Store(store.directory)
    reports=call_store(restarted,'list_reports',dict(task_id=task))['reports']
    assert len(reports)==5 and first==second
    body=call_store(restarted,'read_report',dict(task_id=task,report_id=first))['markdown']
    assert 'User added a farewell.' in body and body.endswith('Check its text.')
    assert (tmp_path/'.codex/cortex'/task/'pipeline.md').is_file()


def test_pipeline_newest_first_cursor_and_snapshot(store,tmp_path):
    task=create(store,tmp_path)
    rid=write(store,task,'Old edition '+('x'*10000),kind='pipeline')['report_id']
    old=call_store(store,'read_report',dict(task_id=task,report_id=rid,limit=100))
    write(store,task,'ordinary report')
    catalogue=call_store(store,'list_reports',dict(task_id=task,limit=1))
    updated=write(store,task,'Fresh edition',kind='pipeline',title='Current pipeline')
    assert updated['report_id']==rid
    assert 'Fresh edition\n\n---\n\n' in call_store(store,'read_report',dict(task_id=task,report_id=rid))['markdown']
    with pytest.raises(StoreError,match='cursor_stale'):
        call_store(store,'read_report',dict(task_id=task,report_id=rid,cursor=old['next_cursor']))
    # The older snapshot still lists the pipeline exactly once in its earlier position.
    following=call_store(store,'list_reports',dict(task_id=task,cursor=catalogue['next_cursor']))
    assert [r['report_id'] for r in following['reports']].count(rid)==1
    assert following['reports'][0]['title']=='Work'
    assert call_store(store,'list_reports',dict(task_id=task))['reports'][0]['title']=='Current pipeline'


def test_bodies_only_in_real_files_and_report_tamper(store,tmp_path):
    task=create(store,tmp_path);body='private report body marker'
    rid=write(store,task,body)['report_id']
    file=tmp_path/'.codex/cortex'/task/(rid+'.md')
    assert file.read_text().endswith(body) and file.stat().st_mode&0o777==0o600
    with store.connection() as db:
        dump='\n'.join(db.iterdump())
    assert body not in dump
    file.write_text('external edit')
    with pytest.raises(StoreError,match='file_conflict'):call_store(store,'read_report',dict(task_id=task,report_id=rid))


def test_post_commit_file_failure_recovers_on_restart(store,tmp_path,monkeypatch):
    task=create(store,tmp_path);delivery=key()
    draft=call_store(store,'create_draft',dict(task_id=task,template='general',request_key='draft-'+delivery))
    path=Path(draft['draft_path']);marker=path.read_text().split('\n\n',1)[0]+'\n\n';path.write_text(marker+'Free **Markdown** without a template.')
    arguments=dict(task_id=task,title='Work',summary='A concise result.',author='worker',draft_id=draft['draft_id'],request_key=delivery)
    finish=store._finish_source_deletions
    calls=0
    def fail_after_commit(db,task):
        nonlocal calls
        calls+=1
        if calls==2:raise OSError('simulated power failure')
        return finish(db,task)
    monkeypatch.setattr(store,'_finish_source_deletions',fail_after_commit)
    with pytest.raises(StoreError,match='storage_error'):call_store(store,'write_report',arguments)
    restarted=Store(store.directory)
    result=call_store(restarted,'write_report',arguments)
    assert result['replayed'] is True
    assert call_store(restarted,'read_report',dict(task_id=task,report_id=result['report_id']))['markdown'].endswith('Free **Markdown** without a template.')
    assert len(call_store(restarted,'list_reports',dict(task_id=task))['reports'])==2


def test_restart_never_recreates_a_missing_project_root(tmp_path):
    import shutil

    project=tmp_path/'deleted-project'; project.mkdir()
    directory=tmp_path/'store'; store=Store(directory)
    task=call_store(store,'create_task',dict(
        project_root=str(project),request='Keep the project boundary.',request_key='create'))['task_id']
    shutil.rmtree(project)
    restarted=Store(directory)
    assert not project.exists()
    with pytest.raises(StoreError,match='invalid_project'):
        call_store(restarted,'list_reports',dict(task_id=task))
    assert not project.exists()


def test_pipeline_replay_does_not_duplicate_edition(store,tmp_path):
    task=create(store,tmp_path);delivery=key()
    first=write(store,task,'Edition one',kind='pipeline',request_key=delivery)
    assert write(store,task,'Edition one',kind='pipeline',request_key=delivery)['replayed']
    second=write(store,task,'Edition two',kind='pipeline')
    assert first['report_id']==second['report_id']
    body=call_store(store,'read_report',dict(task_id=task,report_id=first['report_id']))['markdown']
    assert 'Edition two\n\n---\n\n' in body and body.endswith('Edition one')


def test_previews_support_coordination_without_fetching_report_bodies(store,tmp_path):
    task=create(store,tmp_path)
    private='Detailed private evidence that is not a catalogue preview.'
    report=write(store,task,private,summary='Usage updated; five assertions passed; no blockers; full suite not run.')['report_id']
    pipeline=write(store,task,'Current task: documentation complete; five checks passed; no blockers.',kind='pipeline')['report_id']
    catalogue=call_store(store,'list_reports',dict(task_id=task))
    assert private not in json.dumps(catalogue)
    assert all('markdown' not in row for row in catalogue['reports'])
    assert next(r for r in catalogue['reports'] if r['report_id']==report)['summary'].endswith('full suite not run.')
    assert 'Current task: documentation complete' in call_store(store,'read_report',dict(task_id=task,report_id=pipeline))['markdown']
    # A worker can still read full evidence: no backend actor gate is introduced.
    assert call_store(Store(store.directory),'read_report',dict(task_id=task,report_id=report))['markdown'].endswith(private)
