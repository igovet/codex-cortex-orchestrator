import json
import os
from pathlib import Path
import subprocess
import sys
import sqlite3
import copy
import pytest

META={'threadId':'00000000-0000-4000-8000-000000000001','x-codex-turn-metadata':{}}

ROOT=Path(__file__).resolve().parents[1]


def exchange(tmp_path,messages):
    messages=copy.deepcopy(messages)
    home=tmp_path/'host';(home/'sessions').mkdir(parents=True,exist_ok=True)
    db=sqlite3.connect(home/'state_5.sqlite')
    db.execute('CREATE TABLE IF NOT EXISTS _sqlx_migrations(version BIGINT PRIMARY KEY, description TEXT NOT NULL, installed_on TIMESTAMP NOT NULL, success BOOLEAN NOT NULL, checksum BLOB NOT NULL, execution_time BIGINT NOT NULL)')
    db.execute("INSERT OR IGNORE INTO _sqlx_migrations VALUES (1,'fixture','now',1,X'00',1)")
    db.execute('CREATE TABLE IF NOT EXISTS threads(id TEXT PRIMARY KEY,cwd TEXT NOT NULL,rollout_path TEXT NOT NULL)')
    for m in messages:
        if m.get('method')=='tools/call' and m['params'].get('name')=='create_task' and 'request' in m['params'].get('arguments',{}):
            args=m['params']['arguments'];source=args.pop('request');thread=m['params'].get('_meta',META)['threadId'];path=home/'sessions'/(thread+'.jsonl')
            path.write_text('\n'.join(json.dumps(x) for x in [
                dict(type='session_meta',payload=dict(id=thread,cwd=args['project_root'])),
                dict(type='event_msg',payload=dict(type='task_started',turn_id='turn')),
                dict(type='event_msg',payload=dict(type='item_completed',thread_id=thread,turn_id='turn',item=dict(type='UserMessage',id='original-message',content=[dict(type='text',text=source)]))),
            ])+'\n')
            db.execute('INSERT OR REPLACE INTO threads VALUES (?,?,?)',(thread,args['project_root'],str(path)))
    db.commit();db.close()
    for m in messages:
        if m.get('method')=='tools/call':m['params'].setdefault('_meta',META)
    result=subprocess.run([sys.executable,'-B',str(ROOT/'plugins/cortex/scripts/cortex.py')],input='\n'.join(json.dumps(m) for m in messages)+'\n',text=True,capture_output=True,env=os.environ|{'CODEX_HOME':str(home)},timeout=30)
    assert result.returncode==0 and result.stderr==''
    return [json.loads(line) for line in result.stdout.splitlines()]


def test_wire_catalogue_and_private_errors(tmp_path):
    replies=exchange(tmp_path,[
        dict(jsonrpc='2.0',id=1,method='initialize',params=dict(protocolVersion='2025-11-25',capabilities={},clientInfo=dict(name='test',version='1'))),
        dict(jsonrpc='2.0',method='notifications/initialized'),
        dict(jsonrpc='2.0',id=2,method='tools/list'),
        dict(jsonrpc='2.0',id=3,method='tools/call',params=dict(name='write_report',arguments={'secret':'PRIVATE_SENTINEL'})),
        dict(jsonrpc='2.0',id=4,method='tools/call',params=dict(name='removed_tool',arguments={})),
    ])
    assert len(replies)==4 and len(replies[1]['result']['tools'])==7
    assert replies[2]['result']['isError'] and 'PRIVATE_SENTINEL' not in json.dumps(replies)
    assert replies[3]['error']['code']==-32602


def test_process_restart_preserves_task_and_files(tmp_path):
    message=dict(jsonrpc='2.0',id=1,method='tools/call',params=dict(name='create_task',arguments=dict(project_root=str(tmp_path),request='Build a greeting.',request_key='process-test')))
    first=exchange(tmp_path,[message])[0]['result']['structuredContent']
    second=exchange(tmp_path,[message])[0]['result']['structuredContent']
    assert second==first|dict(replayed=True)
    reply=exchange(tmp_path,[dict(jsonrpc='2.0',id=2,method='tools/call',params=dict(name='read_report',arguments=dict(report_id=first['original_report_id'])))])[0]
    assert reply['result']['structuredContent']['markdown']=='Build a greeting.'


def test_markdown_code_and_quotes_survive_wire_storage_and_restart(tmp_path):
    markdown = '# Evidence\n`files` and "quotes" and apostrophes: it\'s literal.\n```js\nconst text = `${not_executed}`;\nconst path = "C:\\\\work";\n```\nРусский текст — 🧪\n'
    created=exchange(tmp_path,[dict(jsonrpc='2.0',id=1,method='tools/call',params=dict(name='create_task',arguments=dict(project_root=str(tmp_path),request='Preserve literal report text.',request_key='literal-task')))])
    assert not created[0]['result']['isError']
    created_draft=exchange(tmp_path,[dict(jsonrpc='2.0',id=2,method='tools/call',params=dict(name='create_draft',arguments=dict(template='general',request_key='literal-draft')))])
    draft_data=created_draft[0]['result']['structuredContent'];draft_path=Path(draft_data['draft_path'])
    marker=draft_path.read_bytes().split(b'\n\n',1)[0]+b'\n\n';draft_path.write_bytes(marker+markdown.encode())
    written=exchange(tmp_path,[dict(jsonrpc='2.0',id=3,method='tools/call',params=dict(name='write_report',arguments=dict(title='Literal text',summary='Code and quotes preserved.',author='reviewer',draft_id=draft_data['draft_id'],request_key='literal-report')))])
    assert not written[0]['result']['isError']
    report_id = written[0]['result']['structuredContent']['report_id']
    read = exchange(tmp_path, [dict(jsonrpc='2.0', id=3, method='tools/call', params=dict(name='read_report', arguments=dict(report_id=report_id)))])[0]['result']
    assert read['structuredContent']['markdown'] == marker.decode()+markdown
    assert json.loads(read['content'][0]['text']) == read['structuredContent']


def test_raw_invalid_and_oversize_frames(tmp_path):
    raw='{"jsonrpc":"2.0","id":1,"method":"ping","method":"PRIVATE"}\n'+'x'*2_000_001+'\n'+json.dumps(dict(jsonrpc='2.0',id=3,method='ping'))+'\n'
    result=subprocess.run([sys.executable,'-B',str(ROOT/'plugins/cortex/scripts/cortex.py')],input=raw,text=True,capture_output=True,timeout=30)
    rows=[json.loads(line) for line in result.stdout.splitlines()]
    assert len(rows)==3 and rows[-1]['result']=={}
    assert 'PRIVATE' not in result.stdout and result.stderr==''


def test_actionable_schema_errors_never_echo_private_values(tmp_path):
    from cortex_runtime.server import Server
    server=Server()
    cases=[
        ({},'required field(s)'),
        ({'task_id':'PRIVATE_SENTINEL','mode':'light','rationale':'PRIVATE_SENTINEL','request_key':'k'},'Remove unadvertised fields'),
        ({'mode':'wrong','rationale':'PRIVATE_SENTINEL','request_key':'k'},'minimal, light, full'),
        ({'mode':'light','rationale':123,'request_key':'k'},'JSON string'),
    ]
    for arguments,expected in cases:
        result=server.dispatch('tools/call',dict(name='set_governance',arguments=arguments))
        body=result['content'][0]['text']
        assert result['isError'] and expected in body and 'PRIVATE_SENTINEL' not in body
        assert json.loads(body)['write_state']=='rejected'


def test_preview_budget_has_headroom_and_exact_length_correction(tmp_path):
    from cortex_runtime.server import Server
    from cortex_runtime.contracts import BY_NAME
    schema=BY_NAME['write_report']['inputSchema']['properties']['summary']
    assert schema['maxLength']==320 and '100' in schema['description']
    server=Server()
    args=dict(title='Review',summary='x'*321,
              author='reviewer',draft_id='d_000000000000',request_key='preview-check')
    result=server.dispatch('tools/call',dict(name='write_report',arguments=args))
    error=json.loads(result['content'][0]['text'])
    assert result['isError'] and error['field']=='summary'
    assert '321' in error['received'] and '160' in error['correction']
    assert 'x'*321 not in result['content'][0]['text']


def test_draft_routes_are_schema_visible_and_errors_explain_the_exact_repair(tmp_path):
    from jsonschema import Draft202012Validator, ValidationError
    from cortex_runtime.server import Server
    from cortex_runtime.contracts import BY_NAME
    schema=BY_NAME['write_report']['inputSchema'];validator=Draft202012Validator(schema)
    base=dict(title='Evidence',summary='One useful sentence.',author='worker',draft_id='d_000000000000',request_key='route')
    validator.validate(base)
    with pytest.raises(ValidationError):validator.validate({key:value for key,value in base.items() if key!='draft_id'})
    server=Server()
    for arguments,field,repair in [
        (base|dict(markdown='report body'),'markdown','Remove unadvertised fields'),
        (base|dict(draft_id=''),'draft_id','server-issued identifier'),
    ]:
        result=server.dispatch('tools/call',dict(name='write_report',arguments=arguments))
        body=json.loads(result['content'][0]['text'])
        assert result['isError'] and body['field']==field and repair in body['correction']


def test_passive_context_observation_never_logs_other_metadata(tmp_path,monkeypatch):
    from cortex_runtime.server import observe
    folder=tmp_path/'observations'
    monkeypatch.setenv('CORTEX_OBSERVATION_DIR',str(folder))
    thread='00000000-0000-4000-8000-000000000001'
    parent='00000000-0000-4000-8000-000000000002'
    observe('list_reports','success',meta={'threadId':thread,
        'x-codex-turn-metadata':{'parent_thread_id':parent,'private':'PRIVATE_SENTINEL'},
        'extra':'PRIVATE_SENTINEL'},arguments={'cursor':'PRIVATE_SENTINEL'})
    observe('read_report','success',meta={'threadId':thread,
        'x-codex-turn-metadata':{'parent_thread_id':parent}},
        arguments={'report_id':'r_0123456789ab','cursor':'PRIVATE_SENTINEL'},
        result={'report_id':'r_0123456789ab','kind':'pipeline'})
    observe('list_reports','success')
    raw=next(folder.glob('*.jsonl')).read_text()
    rows=[json.loads(line) for line in raw.splitlines()]
    assert rows[0]['thread_id']==thread and rows[0]['parent_thread_id']==parent
    assert rows[0]['page']=='continuation'
    assert rows[1]['document_kind']=='pipeline' and rows[1]['report_id']=='r_0123456789ab'
    assert rows[1]['page']=='continuation'
    assert rows[2]['context_error']=='thread_metadata_missing'
    assert 'PRIVATE_SENTINEL' not in raw


def test_wire_schemas_match_every_success(tmp_path):
    from cortex_runtime.project_storage import ProjectResolver
    from jsonschema import Draft202012Validator
    from cortex_runtime.server import Server
    server=Server(project_resolver=ProjectResolver(lambda *_: str(tmp_path)))
    catalogue=server.dispatch('tools/list',{})['tools']
    by_name={t['name']:t for t in catalogue}
    for item in catalogue:
        Draft202012Validator.check_schema(item['inputSchema'])
        Draft202012Validator.check_schema(item['outputSchema'])
        assert item['title'] and item['description']
        assert all(rule['description'] for rule in item['inputSchema']['properties'].values())
    def call(name,**arguments):
        Draft202012Validator(by_name[name]['inputSchema']).validate(arguments)
        result=server.dispatch('tools/call',dict(name=name,arguments=arguments,_meta=META))
        assert not result['isError'],result
        data=result['structuredContent']
        assert json.loads(result['content'][0]['text'])==data
        Draft202012Validator(by_name[name]['outputSchema']).validate(data)
        return data
    from types import SimpleNamespace
    server.steering_source=lambda *_:SimpleNamespace(cursor={},messages=[])
    server.request_source=lambda *_:'Точный запрос'
    call('create_task',project_root=str(tmp_path),request_key='new')
    call('set_governance',mode='full',rationale='Risk review.',request_key='gov')
    base=dict(title='Report',summary='Evidence',author='worker')
    assert not by_name['create_draft']['annotations']['idempotentHint']
    draft=call('create_draft',template='general')
    path=Path(draft['draft_path']);marker=path.read_text().split('\n\n',1)[0]+'\n\n';path.write_text(marker+'Part one\nPart two')
    final=call('write_report',**base,draft_id=draft['draft_id'],request_key='report-1')
    call('list_reports')
    result=call('read_report',report_id=final['report_id'])
    assert result['markdown'].endswith('Part one\nPart two')
    pipeline=call('create_draft',template='pipeline',request_key='pipeline-draft')
    path=Path(pipeline['draft_path']);marker=path.read_text().split('\n\n',1)[0]+'\n\n';path.write_text(marker+'Current work')
    call('write_report',**base,draft_id=pipeline['draft_id'],request_key='pipeline')


def test_protocol_revision_and_error_categories(tmp_path):
    replies=exchange(tmp_path,[
        dict(jsonrpc='2.0',id=1,method='initialize',params=dict(protocolVersion='2025-11-25',capabilities={},clientInfo=dict(name='test',version='1'))),
        dict(jsonrpc='2.0',id=2,method='initialize',params=dict(protocolVersion='unsupported',capabilities={},clientInfo=dict(name='test',version='1'))),
        dict(jsonrpc='2.0',id=3,method='tools/call',params=dict(name='read_report',arguments=[])),
        dict(jsonrpc='2.0',id=4,method='unknown-method'),
        dict(jsonrpc='2.0',id=5,method='initialize',params={}),
    ])
    assert replies[0]['result']['protocolVersion']=='2025-11-25'
    assert replies[1]['result']['protocolVersion']=='2025-11-25'
    assert replies[2]['error']['code']==-32602 and 'arguments as an object' in replies[2]['error']['message']
    assert replies[3]['error']['code']==-32601
    assert replies[4]['error']['code']==-32602 and 'clientInfo' in replies[4]['error']['message']
