"""Explicit host-side retention maintenance; not an MCP operation."""
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import shutil

from .contracts import StoreError
from .store import file_digest, private_directory, sync_directory


def finish_deletions(store,db,task):
    """Replay committed deletion intents without deleting unrelated project drafts."""
    rows=db.execute("SELECT d.task_id,t.project_root FROM pending_deletions d JOIN tasks t ON t.id=d.task_id WHERE d.task_id=?",(task,)).fetchall()
    for row in rows:
        task=row["task_id"]
        for source in db.execute("SELECT path,published_digest FROM drafts WHERE task_id=?",(task,)).fetchall():
            try: candidate=store._source(db,task,source["path"],must_exist=False)
            except StoreError: candidate=None
            if candidate is not None:
                # Every server-created draft row is an unambiguous task link. Published
                # drafts additionally retain their accepted digest for tamper detection.
                if source["published_digest"] is not None and file_digest(candidate,private=False,validate_utf8=True)!=source["published_digest"]:
                    raise StoreError("draft_conflict","draft_id","retention candidate","bytes matching its accepted SHA-256")
                candidate.unlink(); sync_directory(candidate.parent)
        task_root=private_directory(Path(row["project_root"])/".codex/cortex")
        directory=task_root/task
        if directory.exists() or directory.is_symlink():
            info=directory.lstat()
            if directory.is_symlink() or not directory.is_dir() or info.st_uid!=os.getuid():
                raise StoreError("unsafe_storage","task_directory",str(directory),"owned project task directory")
            if not shutil.rmtree.avoids_symlink_attacks: raise StoreError("unsafe_storage")
            shutil.rmtree(directory); sync_directory(task_root)
        db.execute("DELETE FROM report_provenance WHERE edition_sequence IN (SELECT e.sequence FROM editions e JOIN reports r ON r.id=e.report_id WHERE r.task_id=?)",(task,))
        for table in ("source_turns","source_resets","hook_events","hook_hints","hook_agent_bindings","hook_pending_sources"):
            db.execute(f"DELETE FROM {table} WHERE task_id=?",(task,))
        db.execute("DELETE FROM source_revisions WHERE task_id=?",(task,))
        db.execute("DELETE FROM task_changes WHERE task_id=?",(task,))
        db.execute("DELETE FROM binding_receipts WHERE thread_id IN (SELECT thread_id FROM thread_bindings WHERE task_id=?)",(task,))
        db.execute("DELETE FROM pending_source_deletions WHERE task_id=?",(task,))
        db.execute("DELETE FROM draft_revisions WHERE draft_id IN (SELECT id FROM drafts WHERE task_id=?)",(task,))
        db.execute("DELETE FROM drafts WHERE task_id=?",(task,))
        db.execute("DELETE FROM governance WHERE task_id=?",(task,))
        db.execute("DELETE FROM source_messages WHERE task_id=?",(task,))
        db.execute("DELETE FROM source_cursors WHERE task_id=?",(task,))
        db.execute("DELETE FROM editions WHERE report_id IN (SELECT id FROM reports WHERE task_id=?)",(task,))
        db.execute("DELETE FROM reports WHERE task_id=?",(task,))
        db.execute("DELETE FROM deliveries WHERE scope=? OR (operation=? AND json_extract(response,?)=?)",(task,"create_task","$.task_id",task))
        db.execute("DELETE FROM thread_bindings WHERE task_id=?",(task,))
        db.execute("DELETE FROM pending_deletions WHERE task_id=?",(task,))
        db.execute("DELETE FROM tasks WHERE id=?",(task,))


def clear_tasks(store,project_root,days,keep_threads=(),now=None):
    if type(days) is not int or not 0<=days<=36500:
        raise StoreError("invalid_retention","days",days,"integer from 0 through 36500","Use clear N days with a nonnegative integer N.")
    project=Path(project_root)
    if not project.is_absolute() or not project.is_dir() or str(project.resolve())!=str(project_root):
        raise StoreError("invalid_project","project_root",str(project_root),"absolute existing canonical directory")
    cutoff=((now or datetime.now(timezone.utc))-timedelta(days=days)).isoformat(timespec="microseconds")
    with store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        rows=db.execute('''SELECT id,activity FROM (
            SELECT t.id,t.project_root,MAX(t.created_at,
                COALESCE((SELECT MAX(e.updated_at) FROM reports r JOIN editions e ON e.report_id=r.id WHERE r.task_id=t.id),t.created_at)) AS activity
            FROM tasks t) WHERE project_root=? AND activity<?''',(str(project),cutoff)).fetchall()
        keep={entry["task_id"] for thread in keep_threads for entry in db.execute("SELECT task_id FROM thread_bindings WHERE thread_id=?",(thread,))}
        selected=[row["id"] for row in rows if row["id"] not in keep]
        for task in selected: db.execute("INSERT OR IGNORE INTO pending_deletions VALUES (?)",(task,))
        db.commit()
        pending=[row[0] for row in db.execute("SELECT d.task_id FROM pending_deletions d JOIN tasks t ON t.id=d.task_id WHERE t.project_root=?",(str(project),))]
        for task in pending:
            db.execute("BEGIN IMMEDIATE"); finish_deletions(store,db,task); db.commit()
    return dict(deleted_tasks=len(selected),retention_days=days,
                skipped_protected=sum(row["id"] in keep for row in rows))
