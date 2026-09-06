from storage_fixture import call_store, thread_for
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest
import cortex_clear
from cortex_runtime.cleanup import clear_tasks
from cortex_runtime.contracts import StoreError
from cortex_runtime.store import Store


def test_retention_deletes_artifacts_and_receipts_but_keeps_recent_active_foreign(tmp_path):
    store=Store(tmp_path/'private');project=tmp_path/'project';project.mkdir();foreign=tmp_path/'foreign';foreign.mkdir()
    now=datetime.now(timezone.utc)
    def create(path,label,days):
        time=(now-timedelta(days=days)).isoformat(timespec='microseconds')
        with patch.object(Store,'_now',return_value=time):
            return call_store(store,'create_task',dict(project_root=str(path),request=label,request_key=label))['task_id']
    old=create(project,'old',10);recent=create(project,'recent',2);active=create(project,'active',20);other=create(foreign,'other',30);updated=create(project,'updated',15);drafted=create(project,'drafted',15)
    def report(task,path,name,body,key):
        created=call_store(store,'create_draft',dict(task_id=task,template='general',request_key='draft-'+key))
        draft=Path(created['draft_path']);marker=draft.read_text().split('\n\n',1)[0]+'\n\n';draft.write_text(marker+body)
        return call_store(store,'write_report',dict(task_id=task,title='Evidence',summary='Result',author='worker',draft_id=created['draft_id'],request_key=key)),draft
    with patch.object(Store,'_now',return_value=(now-timedelta(days=10)).isoformat(timespec='microseconds')):
        _,old_source=report(old,project,'old-report.md','accepted report','old-report')
    with store.connection() as db:
        row=db.execute('SELECT id,path FROM drafts WHERE task_id=? ORDER BY created_at DESC LIMIT 1',(old,)).fetchone()
    old_source=Path(row['path']); old_source.write_text(f"Cortex draft ID: `{row['id']}`\n\naccepted report")
    report(drafted,project,'current.md','recent activity','recent-draft')
    report(updated,project,'updated.md','Done','new')
    result=clear_tasks(store,str(project),7,[thread_for(store,active)],now)
    assert result['deleted_tasks']==1 and result['skipped_protected']==1
    assert not (project/'.codex/cortex'/old).exists() and not old_source.exists()
    for task in [recent,active,other,updated,drafted]:assert call_store(store,'list_reports',dict(task_id=task))['reports']
    with pytest.raises(StoreError,match='task_not_bound'):call_store(store,'list_reports',dict(task_id=old))
    with store.connection() as db:
        assert not db.execute('SELECT 1 FROM deliveries WHERE delivery_key=?',('old',)).fetchone()
        assert not db.execute('SELECT 1 FROM drafts WHERE task_id=?',(old,)).fetchone()
        assert db.execute('PRAGMA foreign_key_check').fetchall()==[]
    assert clear_tasks(store,str(project),7,[thread_for(store,active)],now)['deleted_tasks']==0


def test_committed_deletion_recovers_after_restart(tmp_path):
    store=Store(tmp_path/'private')
    task=call_store(store,'create_task',dict(project_root=str(tmp_path),request='Old',request_key='old'))['task_id']
    with store.connection() as db:db.execute('INSERT INTO pending_deletions VALUES (?)',(task,))
    restarted=Store(store.directory)
    with pytest.raises(StoreError,match='task_not_bound'):call_store(restarted,'list_reports',dict(task_id=task))
    assert not (tmp_path/'.codex/cortex'/task).exists()


def test_retention_command_uses_only_selected_project_store(tmp_path, monkeypatch, capsys):
    import json
    project=tmp_path/'project';project.mkdir()
    foreign=tmp_path/'foreign';foreign.mkdir()
    local=Store(project/'.codex/cortex',project_root=str(project))
    global_store=Store(tmp_path/'legacy-global')
    old=(datetime.now(timezone.utc)-timedelta(days=10)).isoformat(timespec='microseconds')
    with patch.object(Store,'_now',return_value=old):
        local_task=call_store(local,'create_task',dict(project_root=str(project),request='Local',request_key='local'))['task_id']
        foreign_task=call_store(global_store,'create_task',dict(project_root=str(foreign),request='Foreign',request_key='foreign'))['task_id']
    before=global_store.path.read_bytes()
    monkeypatch.setenv('CORTEX_DATA_DIR',str(global_store.directory))
    monkeypatch.setenv('CODEX_HOME',str(tmp_path/'missing-host-index'))
    monkeypatch.setattr('sys.argv',['cortex_clear.py','--project-root',str(project),'--days','7'])
    cortex_clear.main()
    assert json.loads(capsys.readouterr().out)['deleted_tasks']==1
    assert not (project/'.codex/cortex'/local_task).exists()
    assert (foreign/'.codex/cortex'/foreign_task).exists()
    assert global_store.path.read_bytes()==before


def test_retention_command_inactive_project_has_no_storage_side_effects(tmp_path, monkeypatch, capsys):
    import json
    monkeypatch.setattr('sys.argv',['cortex_clear.py','--project-root',str(tmp_path),'--days','7'])
    cortex_clear.main()
    assert json.loads(capsys.readouterr().out)['deleted_tasks']==0
    assert not (tmp_path/'.codex').exists()
    monkeypatch.setattr('sys.argv',['cortex_clear.py','--project-root',str(tmp_path),'--days','-1'])
    with pytest.raises(SystemExit,match='retention cleanup failed'):
        cortex_clear.main()
    assert not (tmp_path/'.codex').exists()
