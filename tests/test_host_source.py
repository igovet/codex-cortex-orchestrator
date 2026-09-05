import hashlib
import json
from pathlib import Path
import sqlite3
import uuid

import pytest

from cortex_runtime.contracts import StoreError
from cortex_runtime.host_source import original_request, request_from_rollout
from cortex_runtime.server import Server


def native_turn(thread,text,turn='current'):
    return [dict(type='event_msg',payload=dict(type='task_started',turn_id=turn)),
            dict(type='event_msg',payload=dict(type='item_completed',thread_id=thread,turn_id=turn,
                 item=dict(type='UserMessage',id=turn+'-message',content=[dict(type='text',text=text)])))]


def host(tmp_path,monkeypatch,text):
    home=tmp_path/'host';(home/'sessions').mkdir(parents=True)
    project=tmp_path/'project';project.mkdir()
    thread=str(uuid.uuid4());path=home/'sessions'/'current.jsonl'
    path.write_text('\n'.join(json.dumps(x) for x in native_turn(thread,text))+'\n')
    with sqlite3.connect(home/'state_5.sqlite') as db:
        db.execute('CREATE TABLE threads(id TEXT PRIMARY KEY,cwd TEXT,rollout_path TEXT)')
        db.execute('INSERT INTO threads VALUES (?,?,?)',(thread,str(project),str(path)))
    monkeypatch.setenv('CODEX_HOME',str(home))
    return home,project,thread,path


def call(server,thread,root,**extra):
    return server.dispatch('tools/call',dict(name='create_task',
        arguments=dict(project_root=str(root),request_key='once',**extra),
        _meta={'threadId':thread,'x-codex-turn-metadata':{}}))


def test_native_source_preserves_conditions_without_model_copy(tmp_path,monkeypatch):
    text='Не изменяй INPUT.\n\n  command --flag  value\nТочный invoice_id и `test_reconcile.py`.'
    home,project,thread,path=host(tmp_path,monkeypatch,'$cortex:orchestrator '+text)
    with path.open('a') as f:
        f.write(json.dumps(dict(type='response_item',payload=dict(type='message',role='user',content=[dict(type='input_text',text='<skill>Not user source</skill>')])) )+'\n')
    assert original_request(thread,str(project))==text
    server=Server(tmp_path/'store');result=call(server,thread,project)
    assert not result['isError']
    data=result['structuredContent'];assert data['original_request_sha256']==hashlib.sha256(text.encode()).hexdigest()
    report=server.store.call('read_report',dict(report_id=data['original_report_id']),thread)
    assert report['markdown']==text
    # A retry reuses the accepted receipt even when native input is no longer available.
    path.unlink()
    assert call(server,thread,project)['structuredContent']==data|dict(replayed=True)
    assert call(server,thread,project,request='Model omission')['isError']


def test_source_uses_latest_turn_and_exact_thread(tmp_path,monkeypatch):
    _,project,thread,path=host(tmp_path,monkeypatch,'Earlier ordinary chat')
    entries=native_turn(thread,'Current request\nPreserve this condition.',turn='later')
    entries+=native_turn('unrelated','Not ours',turn='later')[-1:]
    entries.append(dict(type='event_msg',payload=dict(type='item_completed',thread_id=thread,turn_id='old',item=dict(type='UserMessage',id='old-message',content=[dict(type='text',text='Old turn')]))))
    with path.open('a') as f:f.write('\n'.join(json.dumps(x) for x in entries)+'\n')
    assert original_request(thread,str(project))=='Current request\nPreserve this condition.'
    with pytest.raises(StoreError,match='host_request_unavailable'):original_request(str(uuid.uuid4()),str(project))
    with pytest.raises(StoreError,match='host_request_unavailable'):original_request(thread,str(tmp_path))


def test_host_source_rejects_symlink_and_outside_rollout(tmp_path,monkeypatch):
    home,project,thread,path=host(tmp_path,monkeypatch,'Private task')
    outside=tmp_path/'outside.jsonl';path.rename(outside);path.symlink_to(outside)
    with pytest.raises(StoreError,match='host_request_unavailable'):original_request(thread,str(project))
    with sqlite3.connect(home/'state_5.sqlite') as db:db.execute('UPDATE threads SET rollout_path=?',(str(outside),))
    with pytest.raises(StoreError,match='host_request_unavailable'):original_request(thread,str(project))
    server=Server(tmp_path/'store');result=call(server,thread,project)
    assert result['isError'] and 'Private task' not in json.dumps(result) and str(outside) not in json.dumps(result)
    with server.store.connection() as db:assert db.execute('SELECT count(*) FROM tasks').fetchone()[0]==0


def test_bounded_source_never_falls_back_to_prior_turn(tmp_path,monkeypatch):
    _,project,thread,path=host(tmp_path,monkeypatch,'Earlier input')
    with path.open('a') as f:
        f.write(json.dumps(dict(type='event_msg',payload=dict(type='task_started',turn_id='new')))+'\n')
    with pytest.raises(StoreError,match='host_request_unavailable'):original_request(thread,str(project))
    monkeypatch.setattr('cortex_runtime.host_source.MAX_TAIL_BYTES',64)
    with pytest.raises(ValueError):request_from_rollout(path,thread)


def test_literal_credential_redaction_preserves_surrounding_requirements(tmp_path,monkeypatch):
    source='Token=PRIVATE_CREDENTIAL_VALUE\nНе изменяй INPUT.\nKeep  two spaces.'
    _,project,thread,_=host(tmp_path,monkeypatch,source)
    server=Server(tmp_path/'store')
    bad=call(server,thread,project,redact_values=['absent credential'])
    assert bad['isError'] and 'PRIVATE_CREDENTIAL_VALUE' not in json.dumps(bad)
    result=call(server,thread,project,redact_values=['PRIVATE_CREDENTIAL_VALUE'])
    assert not result['isError']
    report=server.store.call('read_report',dict(report_id=result['structuredContent']['original_report_id']),thread)
    assert report['markdown']==source.replace('PRIVATE_CREDENTIAL_VALUE','[REDACTED]')
    assert call(server,thread,project,redact_values=[])['isError']
    for value in [['x','x'],[{'secret':'DO_NOT_ECHO'}],[''],[1]]:
        bad=call(server,thread,project,redact_values=value)
        assert bad['isError'] and 'DO_NOT_ECHO' not in json.dumps(bad)


def append_turn(path,thread,text,turn):
    with path.open('a') as f:
        f.write('\n'.join(json.dumps(x) for x in native_turn(thread,text,turn))+'\n')


def operation(server,thread,name='list_reports',**arguments):
    return server.dispatch('tools/call',dict(name=name,arguments=arguments,
        _meta={'threadId':thread,'x-codex-turn-metadata':{}}))


def source_bodies(server,thread):
    with server.store.connection() as db:
        ids=[r[0] for r in db.execute("SELECT report_id FROM source_messages WHERE thread_id=? ORDER BY rowid",(thread,))]
    return [server.store.call('read_report',dict(report_id=r),thread)['markdown'] for r in ids]


def test_every_steering_is_exact_ordered_and_survives_restart(tmp_path,monkeypatch):
    _,project,thread,path=host(tmp_path,monkeypatch,'Original scope')
    server=Server(tmp_path/'store');assert not call(server,thread,project)['isError']
    first='  Не изменяй INPUT.\n\nВызов: command  --flag\nКлюч invoice_id.\n'
    second='Отмени только --flag. Остальные ограничения сохраняются.\nНе отправляй ничего.'
    append_turn(path,thread,first,'one');append_turn(path,thread,second,'two')
    result=operation(server,thread);assert not result['isError']
    assert len(result['structuredContent']['reports'])==3
    assert source_bodies(server,thread)==['Original scope',first,second]
    # Repeated native receipt and repeated tool call do not duplicate a report.
    append_turn(path,thread,second,'two')
    assert not operation(server,thread)['isError']
    restarted=Server(tmp_path/'store')
    assert not operation(restarted,thread)['isError']
    append_turn(path,thread,'После перезапуска: dry-run не создаёт каталог.','three')
    assert not operation(restarted,thread)['isError']
    assert source_bodies(restarted,thread)==['Original scope',first,second,'После перезапуска: dry-run не создаёт каталог.']


def test_failed_operation_rolls_back_source_and_cursor(tmp_path,monkeypatch):
    _,project,thread,path=host(tmp_path,monkeypatch,'Original')
    server=Server(tmp_path/'store');assert not call(server,thread,project)['isError']
    append_turn(path,thread,'Preserve all validation before filtering.','one')
    # Automatic pipeline lookup fails: no partial source publication is committed.
    assert operation(server,thread,'read_report')['isError']
    assert source_bodies(server,thread)==['Original']
    assert not operation(server,thread)['isError']
    assert source_bodies(server,thread)==['Original','Preserve all validation before filtering.']
    with server.store.connection() as db:
        assert db.execute('PRAGMA foreign_key_check').fetchall()==[]
        task=db.execute('SELECT id FROM tasks').fetchone()[0]
        assert len(list((project/'.codex/cortex'/task).glob('*.md')))==2


def test_steering_redaction_and_delivery_replay(tmp_path,monkeypatch):
    _,project,thread,path=host(tmp_path,monkeypatch,'Original')
    server=Server(tmp_path/'store');assert not call(server,thread,project)['isError']
    args=dict(template='general',request_key='draft')
    first=operation(server,thread,'create_draft',**args)
    append_turn(path,thread,'Token=PRIVATE_VALUE\nKeep every other condition.','one')
    redacted=operation(server,thread,redact_values=['PRIVATE_VALUE'])
    assert not redacted['isError']
    assert source_bodies(server,thread)[-1]=='Token=[REDACTED]\nKeep every other condition.'
    append_turn(path,thread,'New requirement, even during a replay.','two')
    replay=operation(server,thread,'create_draft',**args)
    assert replay['structuredContent']==first['structuredContent']|dict(replayed=True)
    assert source_bodies(server,thread)[-1]=='New requirement, even during a replay.'


def test_pending_source_rejects_rewrite_and_conflicting_native_id(tmp_path,monkeypatch):
    _,project,thread,path=host(tmp_path,monkeypatch,'Original')
    server=Server(tmp_path/'store');assert not call(server,thread,project)['isError']
    append_turn(path,thread,'Changed content under accepted identity','current')
    result=operation(server,thread)
    assert result['isError'] and 'host_request_unavailable' in result['content'][0]['text']
    assert source_bodies(server,thread)==['Original']
    path.write_text('')
    assert operation(server,thread)['isError']


def test_partial_record_and_foreign_inputs_are_not_archived(tmp_path,monkeypatch):
    _,project,thread,path=host(tmp_path,monkeypatch,'Original')
    server=Server(tmp_path/'store');assert not call(server,thread,project)['isError']
    append_turn(path,'foreign-thread','Worker assignment, not user steering','foreign')
    text='Precise new condition'
    raw=json.dumps(native_turn(thread,text,'one')[-1])+'\n'
    with path.open('a') as f:f.write(raw[:40])
    assert not operation(server,thread)['isError']
    assert source_bodies(server,thread)==['Original']
    with path.open('a') as f:f.write(raw[40:])
    assert not operation(server,thread)['isError']
    assert source_bodies(server,thread)==['Original',text]


def test_workers_never_read_coordinator_source(tmp_path,monkeypatch):
    _,project,thread,path=host(tmp_path,monkeypatch,'Original')
    server=Server(tmp_path/'store');assert not call(server,thread,project)['isError']
    append_turn(path,thread,'Pending user restriction','one')
    server.steering_source=lambda *_:pytest.fail('Worker attempted user capture')
    result=server.dispatch('tools/call',dict(name='list_reports',arguments={},
        _meta={'threadId':str(uuid.uuid4()),'x-codex-turn-metadata':{'parent_thread_id':thread}}))
    assert not result['isError']
    assert source_bodies(server,thread)==['Original']


def test_steering_publication_failure_retries_without_loss(tmp_path,monkeypatch):
    _,project,thread,path=host(tmp_path,monkeypatch,'Original')
    server=Server(tmp_path/'store');assert not call(server,thread,project)['isError']
    append_turn(path,thread,'First condition','one');append_turn(path,thread,'Second condition','two')
    publish=server.store._publish_text;count=0
    def fail_second(*args,**kwargs):
        nonlocal count
        count+=1
        if count==2:raise OSError('simulated disk failure')
        return publish(*args,**kwargs)
    monkeypatch.setattr(server.store,'_publish_text',fail_second)
    assert operation(server,thread)['isError']
    assert source_bodies(server,thread)==['Original']
    monkeypatch.setattr(server.store,'_publish_text',publish)
    assert not operation(server,thread)['isError']
    assert source_bodies(server,thread)==['Original','First condition','Second condition']


def test_steering_limits_fail_without_truncation_and_retention_cleans_metadata(tmp_path,monkeypatch):
    from datetime import datetime,timedelta,timezone
    from cortex_runtime.cleanup import clear_tasks
    _,project,thread,path=host(tmp_path,monkeypatch,'Original')
    server=Server(tmp_path/'store');assert not call(server,thread,project)['isError']
    append_turn(path,thread,'A complete requirement that exceeds a test bound','one')
    monkeypatch.setattr('cortex_runtime.host_source.MAX_SOURCE_CHARACTERS',10)
    assert operation(server,thread)['isError']
    assert source_bodies(server,thread)==['Original']
    monkeypatch.setattr('cortex_runtime.host_source.MAX_SOURCE_CHARACTERS',250_000)
    assert not operation(server,thread)['isError']
    result=clear_tasks(server.store,str(project),0,now=datetime.now(timezone.utc)+timedelta(seconds=1))
    assert result['deleted_tasks']==1
    with server.store.connection() as db:
        for table in ('source_cursors','source_messages','reports','tasks'):
            assert db.execute('SELECT count(*) FROM '+table).fetchone()[0]==0
        assert db.execute('PRAGMA foreign_key_check').fetchall()==[]
