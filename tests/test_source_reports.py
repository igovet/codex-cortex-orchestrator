"""Server-created drafts stream into project task Markdown without MCP bodies."""
import hashlib
import os
from pathlib import Path

import pytest

from cortex_runtime.contracts import StoreError
from cortex_runtime.store import Store
from storage_fixture import call_store


@pytest.fixture
def publication(tmp_path):
    project=tmp_path/'project'; project.mkdir()
    store=Store(tmp_path/'global-cortex')
    task=call_store(store,'create_task',dict(project_root=str(project),request='Large document',request_key='create'))['task_id']
    base=dict(task_id=task,title='Full evidence',summary='Complete report',author='worker')
    return store,task,base,project


def make_draft(store,task,template,body,key):
    draft=call_store(store,'create_draft',dict(task_id=task,template=template,request_key='create-'+key))
    path=Path(draft['draft_path'])
    assert draft['required_first_line']==f"Cortex draft ID: `{draft['draft_id']}`"
    marker=path.read_bytes().split(b'\n\n',1)[0]+b'\n\n'
    complete=marker+(body if isinstance(body,bytes) else body.encode())
    path.write_bytes(complete)
    return draft,path,complete


def publish(store,base,draft,key='report',**extra):
    return call_store(store,'write_report',base|dict(draft_id=draft['draft_id'],request_key=key)|extra)


def read_all(store,task,report_id):
    pieces=[]; cursor=None
    while True:
        args=dict(task_id=task,report_id=report_id,limit=4000)
        if cursor: args['cursor']=cursor
        page=call_store(store,'read_report',args)
        pieces.append(page['markdown']); cursor=page['next_cursor']
        if cursor is None: return ''.join(pieces)


def test_server_created_draft_contains_same_short_id_and_template(publication):
    store,task,_,project=publication
    draft=call_store(store,'create_draft',dict(task_id=task,template='planning',request_key='planning'))
    path=Path(draft['draft_path'])
    assert draft['draft_id'] in path.name
    assert f"Cortex draft ID: `{draft['draft_id']}`" in path.read_text()
    assert draft['markdown']==path.read_text()
    assert draft['template']=='planning'
    assert draft['total_characters']==len(draft['markdown'])
    assert draft['sha256']==hashlib.sha256(path.read_bytes()).hexdigest()
    assert '## Proposed work graph' in draft['markdown']
    assert path.parent==project/'.cortex/draft-reports'
    assert not (project/'.codex/cortex/draft-reports').exists()
    assert path.stat().st_mode&0o777==0o600


def test_existing_draft_can_be_recovered_with_bounded_cursor(publication):
    store,task,_,_=publication
    draft=call_store(store,'create_draft',dict(task_id=task,template='planning',request_key='read-planning'))
    pieces=[]; cursor=None
    while True:
        args=dict(task_id=task,draft_id=draft['draft_id'],limit=40)
        if cursor:args['cursor']=cursor
        page=call_store(store,'read_draft',args)
        pieces.append(page['markdown']);cursor=page['next_cursor']
        assert page['draft_path']==draft['draft_path'] and page['template']=='planning'
        if cursor is None:break
    text=''.join(pieces)
    assert text==Path(draft['draft_path']).read_text()
    assert text==draft['markdown']
    assert text.startswith(draft['required_first_line']+'\n\n')


def test_draft_cursor_expires_after_in_place_edit(publication):
    store,task,_,_=publication
    draft=call_store(store,'create_draft',dict(task_id=task,template='general',request_key='stale-draft'))
    first=call_store(store,'read_draft',dict(task_id=task,draft_id=draft['draft_id'],limit=10))
    assert first['next_cursor']
    path=Path(draft['draft_path']);path.write_text(path.read_text()+'changed')
    with pytest.raises(StoreError,match='draft_cursor_stale'):
        call_store(store,'read_draft',dict(task_id=task,draft_id=draft['draft_id'],cursor=first['next_cursor'],limit=10))


def test_one_thread_can_hold_multiple_distinct_drafts(publication):
    store,task,_,project=publication
    drafts=[call_store(store,'create_draft',dict(task_id=task,template=name,request_key=name))
            for name in ('general','planning','pipeline')]
    assert len({row['draft_id'] for row in drafts})==3
    assert all(Path(row['draft_path']).exists() for row in drafts)
    assert Path(drafts[-1]['draft_path']).parent==project/'.cortex/pipeline-drafts'


def test_report_over_16_mb_streams_verbatim_and_removes_project_draft(publication):
    store,task,base,project=publication
    unit='😀\r\nЗ`"\\\n'.encode(); body=unit*1_700_000
    assert len(body)>16*1024*1024
    draft,source,complete=make_draft(store,task,'implementation',body,'large')
    result=publish(store,base,draft)
    assert not source.exists() and result['size_bytes']==len(complete)
    assert result['sha256']==hashlib.sha256(complete).hexdigest()
    target=project/'.codex/cortex'/task/(result['report_id']+'.md')
    assert target.read_bytes()==complete
    assert read_all(Store(store.directory),task,result['report_id']).encode()==complete
    with store.connection() as db:
        dump='\n'.join(db.iterdump())
        assert unit.decode() not in dump
        row=db.execute('SELECT path,published_digest FROM drafts WHERE task_id=? AND id=?',(task,draft['draft_id'])).fetchone()
        assert row['path']==str(source) and row['published_digest']==hashlib.sha256(complete).hexdigest()


def test_pipeline_edition_larger_than_mcp_request_limit_is_file_backed(publication):
    store,task,base,project=publication
    body=('# Current pipeline\n'+'x'*3_000_000).encode()
    draft,source,complete=make_draft(store,task,'pipeline',body,'large-pipeline')
    args=base|dict(title='Large pipeline',summary='Large current state stored.',author='coordinator',draft_id=draft['draft_id'],request_key='large-pipeline')
    result=call_store(store,'write_report',args)
    assert not source.exists() and result['size_bytes']==len(complete)
    assert result['sha256']==hashlib.sha256(complete).hexdigest()
    assert (project/'.codex/cortex'/task/'pipeline.md').read_bytes()==complete
    assert call_store(Store(store.directory),'write_report',args)==result|dict(replayed=True)


def test_exact_retry_after_delete_and_conflicts(publication):
    store,task,base,_=publication
    draft,source,complete=make_draft(store,task,'general','first','same')
    args=base|dict(draft_id=draft['draft_id'],request_key='same')
    first=call_store(store,'write_report',args)
    assert not source.exists()
    assert call_store(Store(store.directory),'write_report',args)==first|dict(replayed=True)
    source.write_bytes(complete)
    assert call_store(store,'write_report',args)==first|dict(replayed=True)
    assert not source.exists()
    source.write_bytes(complete+b' changed')
    with pytest.raises(StoreError,match='draft_conflict'):
        call_store(store,'write_report',args)
    assert source.read_bytes()==complete+b' changed'
    with pytest.raises(StoreError,match='delivery_conflict'):
        call_store(store,'write_report',args|dict(summary='Changed metadata'))
    assert read_all(store,task,first['report_id']).encode()==complete


@pytest.mark.parametrize('case',('symlink','directory','missing'))
def test_tampered_server_draft_is_rejected_and_preserved(publication,tmp_path,case):
    store,task,base,_=publication
    draft,path,_=make_draft(store,task,'general','evidence',case)
    path.unlink()
    if case=='symlink':
        target=tmp_path/'target.md'; target.write_text('evidence'); path.symlink_to(target)
    elif case=='directory': path.mkdir()
    with pytest.raises(StoreError,match='invalid_draft_path|draft_missing'):
        publish(store,base,draft,key=case)
    assert path.exists() or path.is_symlink() or case=='missing'
    assert len(call_store(store,'list_reports',dict(task_id=task))['reports'])==1


def test_unknown_draft_id_is_rejected(publication):
    store,_,base,_=publication
    with pytest.raises(StoreError,match='draft_not_found'):
        publish(store,base,{'draft_id':'d_000000000000'})


def test_invalid_utf8_is_rejected_without_modifying_source(publication):
    store,task,base,_=publication
    draft,source,_=make_draft(store,task,'general',b'valid\n\xffbroken','invalid')
    before=source.read_bytes()
    with pytest.raises(StoreError,match='invalid_utf8'): publish(store,base,draft)
    assert source.read_bytes()==before


def test_removed_draft_id_marker_is_rejected(publication):
    store,task,base,_=publication
    draft,source,_=make_draft(store,task,'general','evidence','marker')
    source.write_text('evidence without the server marker')
    with pytest.raises(StoreError,match='draft_marker_missing'):
        publish(store,base,draft,key='marker')
    assert source.exists()


def test_deleted_and_recreated_draft_is_rejected_even_with_exact_marker(publication):
    store,task,base,_=publication
    draft,source,complete=make_draft(store,task,'general','evidence','recreated')
    source.unlink()
    source.write_bytes(complete)
    with pytest.raises(StoreError,match='draft_replaced'):
        publish(store,base,draft,key='recreated')
    assert source.read_bytes()==complete


def test_failure_after_atomic_publish_rolls_back_task_file_and_keeps_draft(publication,monkeypatch):
    store,task,base,project=publication
    draft,source,_=make_draft(store,task,'general','complete evidence','rollback')
    original=store._publish_source
    def fail(*args):
        original(*args)
        raise RuntimeError('simulated database failure')
    monkeypatch.setattr(store,'_publish_source',fail)
    with pytest.raises(RuntimeError,match='database failure'): publish(store,base,draft)
    assert source.read_text().endswith('complete evidence')
    with store.connection() as db:
        assert db.execute("SELECT COUNT(*) FROM reports WHERE task_id=? AND kind='report'",(task,)).fetchone()[0]==1
    assert len(list((project/'.codex/cortex'/task).glob('r_*.md')))==1


def test_copy_and_rename_stay_inside_destination_filesystem(publication,monkeypatch):
    store,task,base,project=publication
    draft,_,complete=make_draft(store,task,'general','evidence','cross-filesystem')
    original=os.replace; calls=[]
    def observe(old,new):
        calls.append((Path(old),Path(new))); return original(old,new)
    monkeypatch.setattr('cortex_runtime.store.os.replace',observe)
    result=publish(store,base,draft)
    report_dir=project/'.codex/cortex'/task
    assert calls and all(old.parent==new.parent==report_dir for old,new in calls)
    assert (report_dir/(result['report_id']+'.md')).read_bytes()==complete


@pytest.mark.parametrize('template', ('general','planning','investigation','implementation','verification','documentation','synthesis','pipeline'))
def test_unfilled_template_cannot_be_published(publication, template):
    store,task,base,_=publication
    draft=call_store(store,'create_draft',dict(task_id=task,template=template,request_key=template))
    path=Path(draft['draft_path'])
    original=path.read_text()
    with pytest.raises(StoreError,match='draft_guidance_remaining'):
        publish(store,base,draft)
    assert path.read_text()==original
    for marker in draft['replaceable_markers']:
        original=original.replace(marker,'Verified evidence; no remaining work.')
    path.write_text(original)
    assert publish(store,base,draft)['replayed'] is False


def test_crash_before_pipeline_commit_restores_backup(publication):
    import multiprocessing
    store,task,base,project=publication
    first,_,old=make_draft(store,task,'pipeline','Previous committed edition','first')
    receipt=publish(store,base,first,'first')
    second,source,new=make_draft(store,task,'pipeline','New edition','second')
    def crash():
        original=store._publish_source
        def interrupt(*args):
            original(*args)
            os._exit(73)
        store._publish_source=interrupt
        publish(store,base,second,'second')
    process=multiprocessing.get_context('fork').Process(target=crash)
    process.start(); process.join(10)
    assert process.exitcode==73
    target=project/'.codex/cortex'/task/'pipeline.md'
    assert target.read_bytes().startswith(new)
    restarted=Store(store.directory)
    call_store(restarted,'list_reports',dict(task_id=task))
    assert target.read_bytes()==old and source.exists()
    assert not list(target.parent.glob('.backup_pipeline_*'))
    result=publish(restarted,base,second,'second')
    assert result['report_id']==receipt['report_id']
    assert target.read_bytes()==new+b'\n\n---\n\n'+old


def test_directory_sync_failure_after_pipeline_rename_rolls_back(publication,monkeypatch):
    import cortex_runtime.store as runtime
    store,task,base,project=publication
    first,_,old=make_draft(store,task,'pipeline','Committed edition','first')
    publish(store,base,first,'first')
    second,source,new=make_draft(store,task,'pipeline','Next edition','second')
    target=project/'.codex/cortex'/task/'pipeline.md'
    original=runtime.sync_directory
    failed=False
    def fail_once(path):
        nonlocal failed
        if not failed and Path(path)==target.parent and target.read_bytes().startswith(new):
            failed=True
            raise OSError('simulated directory sync failure')
        original(path)
    monkeypatch.setattr(runtime,'sync_directory',fail_once)
    with pytest.raises(StoreError,match='storage_error'):
        publish(store,base,second,'second')
    assert failed and target.read_bytes()==old and source.exists()
    assert publish(store,base,second,'second')['replayed'] is False
