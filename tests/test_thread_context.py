"""Public MCP context contract: host metadata, no model-authored task selector."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timedelta,timezone
import json
from pathlib import Path
import uuid

from cortex_runtime.server import Server
from cortex_runtime.store import Store
from cortex_runtime.cleanup import clear_tasks


def tid():return str(uuid.uuid4())

def meta(thread,parent=None):
    return {'threadId':thread,'x-codex-turn-metadata':({'parent_thread_id':parent} if parent else {})}

def call(server,name,args,thread,parent=None):
    return server.dispatch('tools/call',dict(name=name,arguments=args,_meta=meta(thread,parent)))

def ok(result):
    assert not result['isError'],result
    assert 'task_id' not in json.dumps(result)
    return result['structuredContent']

def error(result,code):
    assert result['isError']
    data=json.loads(result['content'][0]['text']);assert data['error']==code,data
    assert data['correction'] and data['write_state']=='rejected'

def create(server,root,thread,key='create'):
    return ok(call(server,'create_task',dict(project_root=str(root),request='Build a page.',request_key=key),thread))

def write(server,project,thread,parent=None,**extra):
    kind=extra.pop('kind','report'); request_key=tid()
    created=ok(call(server,'create_draft',dict(template='pipeline' if kind=='pipeline' else 'general',request_key='draft-'+request_key),thread,parent))
    path=Path(created['draft_path']);marker=path.read_text().split('\n\n',1)[0]+'\n\n'
    path.write_text(marker+('# Newest\n\nCurrent work.' if kind=='pipeline' else 'Complete evidence.'))
    arguments=dict(title='Evidence',summary='Verified result.',author='worker',draft_id=created['draft_id'],request_key=request_key)|extra
    return ok(call(server,'write_report',arguments,thread,parent))


def test_root_child_nested_restart_and_automatic_pipeline(tmp_path):
    server=Server(tmp_path/'store');root,child,nested=tid(),tid(),tid()
    create(server,tmp_path,root)
    error(call(server,'read_report',{},root),'pipeline_missing')
    pipeline=write(server,tmp_path,root,kind='pipeline')
    assert ok(call(server,'list_reports',{},child,root))['reports'][0]['report_id']==pipeline['report_id']
    assert '# Newest' in ok(call(server,'read_report',{},child,root))['markdown']
    worker=write(server,tmp_path,nested,child)
    restarted=Server(tmp_path/'store')
    assert ok(call(restarted,'read_report',{'report_id':worker['report_id']},nested,child))['markdown'].endswith('Complete evidence.')
    assert ok(call(restarted,'read_report',{},root))['report_id']==pipeline['report_id']
    with Store(tmp_path/'store').connection() as db:
        assert db.execute('SELECT COUNT(DISTINCT task_id) FROM thread_bindings').fetchone()[0]==1
        assert db.execute('SELECT COUNT(*) FROM thread_bindings').fetchone()[0]==3


def test_missing_invalid_unknown_and_conflicting_context_cannot_select_latest(tmp_path):
    server=Server(tmp_path/'store');root,child,other=tid(),tid(),tid()
    create(server,tmp_path,root)
    for transport,code in [(None,'thread_metadata_missing'),({},'thread_metadata_missing'),
            ({'threadId':root,'x-codex-turn-metadata':'{}'},'thread_metadata_missing'),
            (meta('PRIVATE_SENTINEL'),'thread_metadata_invalid'),(meta(root,root),'thread_metadata_invalid')]:
        result=server.dispatch('tools/call',dict(name='list_reports',arguments={},_meta=transport))
        error(result,code);assert 'PRIVATE_SENTINEL' not in json.dumps(result)
    error(call(server,'list_reports',{},other),'task_not_bound')
    error(call(server,'list_reports',{},child,other),'parent_not_bound')
    ok(call(server,'list_reports',{},child,root))
    error(call(server,'list_reports',{},child,other),'thread_conflict')
    error(call(server,'list_reports',{},child),'thread_conflict')
    error(call(server,'create_task',dict(project_root=str(tmp_path),request='No',request_key='child'),child,root),'child_creation')


def test_no_task_or_thread_selectors_and_no_cross_task_report_reads(tmp_path):
    server=Server(tmp_path/'store');one,two=tid(),tid()
    original=create(server,tmp_path,one);create(server,tmp_path,two)
    for tool in server.dispatch('tools/list',{})['tools']:
        for schema in ('inputSchema','outputSchema'):
            assert not {'task_id','task_ref','thread_id','threadId'} & set(tool[schema]['properties'])
    for field in ('task_id','task_ref','thread_id','threadId'):
        error(call(server,'list_reports',{field:'PRIVATE_SENTINEL'},one),'invalid_arguments')
    error(call(server,'read_report',dict(report_id=original['original_report_id']),two),'not_found')
    listing=ok(call(server,'list_reports',dict(limit=1),one))
    assert all('task_id' not in row for row in listing['reports'])


def test_draft_is_bound_to_creator_thread_and_many_coordinator_drafts_are_distinct(tmp_path):
    server=Server(tmp_path/'store');root,first,second=tid(),tid(),tid()
    create(server,tmp_path,root)
    one=ok(call(server,'create_draft',dict(template='planning',request_key='one'),root))
    two=ok(call(server,'create_draft',dict(template='pipeline',request_key='two'),root))
    assert one['draft_id']!=two['draft_id']
    child=ok(call(server,'create_draft',dict(template='investigation',request_key='child'),first,root))
    assert ok(call(server,'read_draft',dict(draft_id=child['draft_id']),first,root))['draft_id']==child['draft_id']
    error(call(server,'read_draft',dict(draft_id=child['draft_id']),second,root),'draft_not_owned')
    child_path=Path(child['draft_path']);marker=child_path.read_text().split('\n\n',1)[0]+'\n\n';child_path.write_text(marker+'Child evidence')
    ok(call(server,'list_reports',{},second,root))
    denied=call(server,'write_report',dict(title='Wrong owner',summary='Must fail.',author='worker',draft_id=child['draft_id'],request_key='wrong'),second,root)
    error(denied,'draft_not_owned')
    accepted=ok(call(server,'write_report',dict(title='Child evidence',summary='Accepted by its owner.',author='worker',draft_id=child['draft_id'],request_key='right'),first,root))
    assert accepted['report_id']


def test_internal_task_identifier_is_redacted_from_tool_errors(tmp_path):
    import os

    server=Server(tmp_path/'store'); thread=tid()
    original=create(server,tmp_path,thread)
    with server.store.connection() as db:
        task=db.execute('SELECT task_id FROM thread_bindings WHERE thread_id=?',(thread,)).fetchone()[0]
        filename=db.execute('SELECT filename FROM reports WHERE id=?',(original['original_report_id'],)).fetchone()[0]
    report=tmp_path/'.codex/cortex'/task/filename
    os.chmod(report,0o644)
    result=call(server,'read_report',dict(report_id=original['original_report_id']),thread)
    body=result['content'][0]['text']
    assert result['isError'] and task not in body and '<task>' in body


def test_creation_receipts_are_thread_scoped_and_atomic(tmp_path):
    server=Server(tmp_path/'store');one,two=tid(),tid()
    args=dict(project_root=str(tmp_path),request='First',request_key='same')
    first=ok(call(server,'create_task',args,one))
    assert ok(call(server,'create_task',args,one))==first|dict(replayed=True)
    error(call(server,'create_task',args|dict(request='Changed'),one),'delivery_conflict')
    error(call(server,'create_task',args|dict(request_key='second'),one),'task_already_bound')
    assert ok(call(server,'create_task',args,two))['original_report_id']!=first['original_report_id']
    three=tid()
    def attempt(key):return call(Server(tmp_path/'store'),'create_task',args|dict(request_key=key),three)
    with ThreadPoolExecutor(2) as pool:results=list(pool.map(attempt,['a','b']))
    assert sum(not r['isError'] for r in results)==1
    error(next(r for r in results if r['isError']),'task_already_bound')


def test_clear_removes_all_bindings_receipts_and_protects_native_threads(tmp_path):
    server=Server(tmp_path/'store');one,two,child=tid(),tid(),tid()
    create(server,tmp_path,one);create(server,tmp_path,two)
    ok(call(server,'list_reports',{},child,one))
    store=Store(tmp_path/'store')
    with store.connection() as db:
        tasks={row['thread_id']:row['task_id'] for row in db.execute('SELECT * FROM thread_bindings')}
    result=clear_tasks(store,str(tmp_path),0,[child],datetime.now(timezone.utc)+timedelta(days=1))
    assert result['deleted_tasks']==1 and result['skipped_protected']==1
    assert not (tmp_path/'.codex/cortex'/tasks[two]).exists()
    error(call(server,'list_reports',{},two),'task_not_bound')
    ok(call(server,'list_reports',{},one))
    with store.connection() as db:
        assert db.execute('SELECT COUNT(*) FROM thread_bindings').fetchone()[0]==2
        assert db.execute('SELECT 1 FROM deliveries WHERE scope=?',('new-task:'+two,)).fetchone() is None
        assert db.execute('PRAGMA foreign_key_check').fetchall()==[]
