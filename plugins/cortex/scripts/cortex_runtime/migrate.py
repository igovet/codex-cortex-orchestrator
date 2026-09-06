"""Explicit offline metadata-only format 10 to 11 migration."""
from __future__ import annotations

import argparse
import fcntl
import json
import os
from pathlib import Path
import sqlite3
import uuid

from .contracts import StoreError
from .hook_storage import HOOK_SCHEMA
from .store import APPLICATION_ID, SCHEMA, private_directory, regular, sync_directory


def migrate(directory, backup, *, access_stopped=False):
    """Require a fresh verified backup and exclusive access; never open Markdown."""
    if not access_stopped: raise StoreError("migration_requires_stopped_access")
    directory=private_directory(directory); source=directory/"cortex.sqlite3"
    regular(source)
    backup=Path(backup).expanduser().absolute()
    if backup.exists() or backup.is_symlink(): raise StoreError("migration_backup_exists")
    if backup.parent.resolve()!=backup.parent or not backup.parent.is_dir(): raise StoreError("unsafe_storage")
    lock=directory/".access.lock"
    lock_fd=os.open(lock,os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
    db=None; target=None
    try:
        regular(lock)
        try: fcntl.flock(lock_fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError: raise StoreError("storage_busy") from None
        db=sqlite3.connect(source,timeout=0,isolation_level=None)
        if db.execute("PRAGMA application_id").fetchone()[0]!=APPLICATION_ID or db.execute("PRAGMA user_version").fetchone()[0]!=10:
            raise StoreError("unsupported_storage")
        db.execute("PRAGMA locking_mode=EXCLUSIVE")
        db.execute("BEGIN EXCLUSIVE"); db.commit()
        fd=os.open(backup,os.O_CREAT|os.O_EXCL|os.O_RDWR|os.O_NOFOLLOW,0o600);os.close(fd)
        target=sqlite3.connect(backup)
        db.backup(target)
        if target.execute("PRAGMA integrity_check").fetchone()[0]!="ok":raise StoreError("migration_backup_invalid")
        target.close();target=None
        with backup.open("rb") as stream:os.fsync(stream.fileno())
        sync_directory(backup.parent)
        # Existing v10 tables and immutable Markdown are untouched. Add metadata,
        # initialize known source order and mark older authored provenance unknown (0).
        db.executescript("BEGIN EXCLUSIVE;\n"+SCHEMA+HOOK_SCHEMA)
        db.execute("PRAGMA foreign_keys=ON")
        rows=db.execute("SELECT thread_id FROM thread_bindings").fetchall()
        for (thread,) in rows:
            db.execute("INSERT INTO binding_receipts VALUES (?,?,?,strftime('%Y-%m-%dT%H:%M:%fZ','now'))",(thread,"b_"+uuid.uuid4().hex,"cortex"))
        for (task,) in db.execute("SELECT id FROM tasks").fetchall():
            messages=db.execute("""SELECT s.thread_id,s.message_id,s.report_id FROM source_messages s
                WHERE task_id=? ORDER BY (SELECT MIN(sequence) FROM editions WHERE report_id=s.report_id),s.message_id""",(task,)).fetchall()
            for revision,(thread,message,report) in enumerate(messages,1):
                db.execute("INSERT INTO source_revisions VALUES (?,?,?,?,?,?)",(task,revision,thread,message,report,"[]"))
        db.execute("INSERT INTO report_provenance SELECT sequence,0,'[]' FROM editions")
        db.execute("INSERT INTO draft_revisions SELECT id,0 FROM drafts")
        for scope,operation,key,response in db.execute("SELECT scope,operation,delivery_key,response FROM deliveries WHERE operation='write_report'").fetchall():
            receipt=json.loads(response)
            receipt.update(source_revision=0,artifacts=[])
            db.execute("UPDATE deliveries SET response=? WHERE scope=? AND operation=? AND delivery_key=?",(json.dumps(receipt,ensure_ascii=False,sort_keys=True,separators=(",",":")),scope,operation,key))
        if db.execute("PRAGMA foreign_key_check").fetchone() is not None:raise StoreError("migration_integrity_failed")
        db.commit()
        db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        return dict(from_version=10,to_version=11,backup=str(backup),markdown_rewritten=False)
    except BaseException:
        if db is not None:db.rollback()
        raise
    finally:
        if target is not None:target.close()
        if db is not None:db.close()
        fcntl.flock(lock_fd,fcntl.LOCK_UN);os.close(lock_fd)


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--storage-dir",required=True)
    parser.add_argument("--backup",required=True,help="New owner-private SQLite backup file; never overwritten.")
    parser.add_argument("--access-stopped",action="store_true",help="Confirm all CLI/Desktop/MCP/hook access has been stopped; back up project task directories before running.")
    args=parser.parse_args(argv)
    try:
        result=migrate(args.storage_dir,args.backup,access_stopped=args.access_stopped)
    except (StoreError,OSError,sqlite3.Error) as error:
        parser.exit(1,f"Migration failed: {type(error).__name__}: {error}\n")
    print(f"Metadata migrated 10→11; SQLite backup: {result['backup']}; Markdown unchanged.")
    return 0
