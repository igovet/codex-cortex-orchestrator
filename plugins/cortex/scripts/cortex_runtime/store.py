"""SQLite metadata with project-local, atomic Markdown publication."""
from __future__ import annotations

import codecs
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import stat
import uuid

from .contracts import StoreError, validate

APPLICATION_ID = 1129467218
SCHEMA = '''
CREATE TABLE IF NOT EXISTS tasks (
 id TEXT PRIMARY KEY, project_root TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS thread_bindings (
 thread_id TEXT PRIMARY KEY, parent_thread_id TEXT,
 task_id TEXT NOT NULL REFERENCES tasks(id)
);
CREATE TABLE IF NOT EXISTS reports (
 id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id),
 kind TEXT NOT NULL, filename TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS one_pipeline ON reports(task_id) WHERE kind='pipeline';
CREATE TABLE IF NOT EXISTS editions (
 sequence INTEGER PRIMARY KEY AUTOINCREMENT,
 report_id TEXT NOT NULL REFERENCES reports(id),
 title TEXT NOT NULL, summary TEXT NOT NULL, author TEXT NOT NULL,
 updated_at TEXT NOT NULL, digest TEXT NOT NULL, size_bytes INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS report_editions ON editions(report_id,sequence);
CREATE TABLE IF NOT EXISTS governance (
 id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id), mode TEXT NOT NULL,
 report_id TEXT NOT NULL REFERENCES reports(id)
);
CREATE TABLE IF NOT EXISTS deliveries (
 scope TEXT NOT NULL, operation TEXT NOT NULL, delivery_key TEXT NOT NULL,
 digest TEXT NOT NULL, content_digest TEXT NOT NULL, response TEXT NOT NULL,
 PRIMARY KEY(scope,operation,delivery_key)
);
CREATE TABLE IF NOT EXISTS drafts (
 id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id),
 owner_thread_id TEXT NOT NULL, kind TEXT NOT NULL, template TEXT NOT NULL,
 path TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL,
 device INTEGER NOT NULL, inode INTEGER NOT NULL,
 published_report_id TEXT REFERENCES reports(id), published_digest TEXT
);
CREATE TABLE IF NOT EXISTS pending_source_deletions (
 task_id TEXT NOT NULL REFERENCES tasks(id), draft_id TEXT NOT NULL REFERENCES drafts(id),
 path TEXT NOT NULL, digest TEXT NOT NULL, PRIMARY KEY(task_id,draft_id)
);
CREATE TABLE IF NOT EXISTS pending_deletions (
 task_id TEXT PRIMARY KEY REFERENCES tasks(id)
);
CREATE TRIGGER IF NOT EXISTS editions_no_update BEFORE UPDATE ON editions BEGIN
 SELECT RAISE(ABORT, 'immutable'); END;
CREATE TRIGGER IF NOT EXISTS editions_no_delete BEFORE DELETE ON editions
WHEN NOT EXISTS (
 SELECT 1 FROM pending_deletions d JOIN reports r ON r.task_id=d.task_id
 WHERE r.id=OLD.report_id
) BEGIN SELECT RAISE(ABORT, 'immutable'); END;
PRAGMA user_version=10;
'''

PIPELINE_PLACEHOLDERS = (
    b"{{CURRENT_OBJECTIVE_AND_STATUS}}",
    b"{{CURRENT_REQUIREMENTS_AND_CONSTRAINTS}}",
    b"{{CURRENT_WORK_GRAPH}}",
    b"{{CURRENT_ASSIGNMENTS_AND_ROUTING}}",
    b"{{CURRENT_EVIDENCE_AND_VERIFICATION}}",
    b"{{CURRENT_DECISIONS_QUESTIONS_AND_REMAINING_WORK}}",
)


def identifier(prefix):
    return prefix + uuid.uuid4().hex[:12]


def unique_identifier(db, prefix):
    table={"t_":"tasks","r_":"reports","d_":"drafts"}[prefix]
    for _ in range(32):
        value=identifier(prefix)
        if db.execute(f"SELECT 1 FROM {table} WHERE id=?",(value,)).fetchone() is None:
            return value
    raise StoreError("identifier_unavailable")


def encode(value):
    return json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(",",":"))


def digest(body):
    return hashlib.sha256(body).hexdigest()


def _base36(value):
    alphabet="0123456789abcdefghijklmnopqrstuvwxyz"; result=""
    while value:
        value,remainder=divmod(value,36); result=alphabet[remainder]+result
    return result


def private_directory(path):
    """Create a private storage directory without traversing symlinks."""
    path=Path(path).expanduser().absolute()
    if ".." in path.parts: raise StoreError("unsafe_storage","storage_path",str(path),"canonical private directory")
    current=Path(path.anchor)
    for part in path.parts[1:]:
        current/=part
        try: current.mkdir(mode=0o700)
        except FileExistsError: pass
        info=current.lstat()
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise StoreError("unsafe_storage","storage_path",str(current),"owned directory without symlinks")
    info=path.stat()
    if info.st_uid!=os.getuid() or stat.S_IMODE(info.st_mode)&0o077:
        raise StoreError("unsafe_storage","storage_path",str(path),"owner-only directory mode 0700")
    return path


def project_draft_directory(project_root,kind="report"):
    """Create the source directory for one file-backed Markdown write."""
    project=Path(project_root)
    if not project.is_absolute() or not project.is_dir() or str(project.resolve())!=str(project):
        raise StoreError("invalid_project","project_root",str(project_root),"absolute existing canonical directory")
    current=project
    leaf={"report":"draft-reports","pipeline":"pipeline-drafts"}.get(kind)
    if leaf is None: raise StoreError("invalid_draft_path","kind",kind,"report or pipeline")
    for part in (".cortex",leaf):
        current/=part
        try: current.mkdir(mode=0o700)
        except FileExistsError: pass
        info=current.lstat()
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode) or info.st_uid!=os.getuid():
            raise StoreError("unsafe_storage","draft_path",str(current),"owned directory without symlinks")
        os.chmod(current,0o700,follow_symlinks=False)
    return current


def project_task_directory(project_root):
    """Create the MCP-owned published-document root below project .codex."""
    project=Path(project_root)
    if not project.is_absolute() or not project.is_dir() or str(project.resolve())!=str(project):
        raise StoreError("invalid_project","project_root",str(project_root),"absolute existing canonical directory")
    current=project
    for part in (".codex","cortex"):
        current/=part
        try: current.mkdir(mode=0o700)
        except FileExistsError: pass
        info=current.lstat()
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode) or info.st_uid!=os.getuid():
            raise StoreError("unsafe_storage","storage_path",str(current),"owned directory without symlinks")
        os.chmod(current,0o700,follow_symlinks=False)
    return current


def draft_template(template, draft_id):
    """Load one packaged draft template and add its durable short identifier."""
    root=Path(__file__).resolve().parents[2]/"report-templates"
    path=(root/(template+".md")).resolve()
    if path.parent!=root.resolve() or not path.is_file():
        raise StoreError("storage_error","template",template,"packaged draft template")
    body=path.read_bytes()
    try: body.decode("utf-8")
    except UnicodeDecodeError:
        raise StoreError("storage_error","template",template,"valid UTF-8 packaged draft template") from None
    return f"Cortex draft ID: `{draft_id}`\n\n".encode()+body


def regular(path, private=True):
    info=Path(path).lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.getuid() or info.st_nlink!=1:
        raise StoreError("unsafe_storage","path",str(path),"owned regular file with one link")
    if private and stat.S_IMODE(info.st_mode)&0o077:
        raise StoreError("unsafe_storage","path",str(path),"owner-only regular file mode 0600")
    return info


def file_blocks(path, private=True):
    path=Path(path); before=regular(path,private)
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
    with os.fdopen(fd,"rb") as stream:
        info=os.fstat(stream.fileno())
        signature=(info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns,info.st_ctime_ns)
        expected=(before.st_dev,before.st_ino,before.st_size,before.st_mtime_ns,before.st_ctime_ns)
        if signature!=expected: raise StoreError("file_conflict","path",str(path),"unchanged regular file")
        while block:=stream.read(64*1024): yield block
        info=os.fstat(stream.fileno())
        after=(info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns,info.st_ctime_ns)
        if after!=signature: raise StoreError("file_conflict","path",str(path),"unchanged regular file")


def file_digest(path, private=True, validate_utf8=False,error_field="path",error_value=None):
    hashed=hashlib.sha256(); decoder=codecs.getincrementaldecoder("utf-8")() if validate_utf8 else None
    try:
        for block in file_blocks(path,private):
            hashed.update(block)
            if decoder: decoder.decode(block)
        if decoder: decoder.decode(b"",final=True)
    except UnicodeDecodeError:
        raise StoreError("invalid_utf8",error_field,error_value if error_value is not None else str(path),"complete valid UTF-8 Markdown file") from None
    return hashed.hexdigest()


def sync_directory(path):
    fd=os.open(path,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try: os.fsync(fd)
    finally: os.close(fd)


class Store:
    def __init__(self,directory):
        self.directory=private_directory(directory)
        self.path=self.directory/"cortex.sqlite3"
        fd=os.open(self.path,os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600); os.close(fd)
        regular(self.path)
        with self.connection() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("BEGIN IMMEDIATE")
            app=db.execute("PRAGMA application_id").fetchone()[0]
            version=db.execute("PRAGMA user_version").fetchone()[0]
            tables=db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            if app!=APPLICATION_ID and (app!=0 or tables or version!=0): raise StoreError("unsupported_storage")
            if app==APPLICATION_ID and version!=10: raise StoreError("unsupported_storage")
            db.commit()
            db.executescript("BEGIN IMMEDIATE;\n"+SCHEMA+f"PRAGMA application_id={APPLICATION_ID};\nCOMMIT;")
            db.execute("BEGIN IMMEDIATE"); self._recover(db); db.commit()

    @contextmanager
    def connection(self):
        for suffix in ("","-wal","-shm"):
            try: regular(Path(str(self.path)+suffix))
            except FileNotFoundError:
                if not suffix: raise
        db=sqlite3.connect(self.path,timeout=15,isolation_level=None)
        db.row_factory=sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON"); db.execute("PRAGMA synchronous=FULL")
        try: yield db
        finally: db.close()

    def call(self,operation,args,thread_id,parent_thread_id=None):
        validate(operation,args)
        return self._transaction(operation,args,(thread_id,parent_thread_id))

    def _transaction(self,operation,args,context):
        rollbacks=[]; staged=[]
        try:
            with self.connection() as db:
                db.execute("BEGIN IMMEDIATE"); self._recover(db); db.commit()
                db.execute("BEGIN IMMEDIATE")
                try:
                    bound=dict(args); thread,parent=context
                    if operation=="create_task":
                        if parent is not None: raise StoreError("child_creation")
                        existing=db.execute("SELECT * FROM thread_bindings WHERE thread_id=?",(thread,)).fetchone()
                        if existing is not None:
                            if existing["parent_thread_id"] is not None: raise StoreError("thread_conflict")
                            receipt=db.execute("SELECT 1 FROM deliveries WHERE scope=? AND operation=? AND delivery_key=?",("new-task:"+thread,operation,args["request_key"])).fetchone()
                            if receipt is None: raise StoreError("task_already_bound")
                    else:
                        bound["task_id"]=self._resolve_thread(db,thread,parent)
                    if operation=="read_report" and "report_id" not in bound:
                        if "cursor" in bound:
                            decoded=self._decode(bound["cursor"])
                            if not isinstance(decoded,list) or len(decoded)!=5 or decoded[0]!="read" or decoded[1]!=self._cursor_binding(bound["task_id"]) or not isinstance(decoded[2],str):
                                raise StoreError("invalid_cursor","cursor",bound["cursor"],"cursor for this task and document")
                            bound["report_id"]=decoded[2]
                        else:
                            row=db.execute("SELECT id FROM reports WHERE task_id=? AND kind='pipeline'",(bound["task_id"],)).fetchone()
                            if row is None: raise StoreError("pipeline_missing")
                            bound["report_id"]=row["id"]
                    result=self._call(db,operation,bound,context[0],rollbacks,staged)
                    if operation=="create_task":
                        task=result.pop("task_id")
                        db.execute("INSERT OR IGNORE INTO thread_bindings VALUES (?,NULL,?)",(thread,task))
                    db.commit()
                except BaseException:
                    db.rollback(); self._rollback_files(rollbacks)
                    for path in staged: path.unlink(missing_ok=True)
                    raise
                self._finalize_files(rollbacks)
                db.execute("BEGIN IMMEDIATE"); self._finish_source_deletions(db); db.commit()
            return result
        except StoreError: raise
        except (sqlite3.Error,OSError,ValueError,UnicodeError):
            raise StoreError("storage_error") from None

    @staticmethod
    def _resolve_thread(db,thread,parent):
        existing=db.execute("SELECT * FROM thread_bindings WHERE thread_id=?",(thread,)).fetchone()
        if existing is not None:
            if existing["parent_thread_id"]!=parent: raise StoreError("thread_conflict")
            return existing["task_id"]
        if parent is None: raise StoreError("task_not_bound")
        ancestor=db.execute("SELECT task_id FROM thread_bindings WHERE thread_id=?",(parent,)).fetchone()
        if ancestor is None: raise StoreError("parent_not_bound")
        db.execute("INSERT INTO thread_bindings VALUES (?,?,?)",(thread,parent,ancestor["task_id"]))
        return ancestor["task_id"]

    def _task_directory(self,db,task):
        row=db.execute("SELECT project_root FROM tasks WHERE id=?",(task,)).fetchone()
        if row is None: raise StoreError("task_not_bound")
        cortex_root=project_task_directory(row["project_root"])
        return private_directory(cortex_root/task)

    def _reports_directory(self,db,task):
        return self._task_directory(db,task)

    def _location(self,db,report):
        row=db.execute("SELECT task_id,kind,filename FROM reports WHERE id=?",(report,)).fetchone()
        if row is None: raise StoreError("not_found","report_id",report,"report in the current task")
        return self._task_directory(db,row["task_id"])/row["filename"]

    def _source(self,db,task,value,must_exist=True,kind=None,error_field="draft_path",error_value=None):
        shown=error_value if error_value is not None else value
        project=db.execute("SELECT project_root FROM tasks WHERE id=?",(task,)).fetchone()[0]
        roots={entry:project_draft_directory(project,entry).resolve() for entry in ("report","pipeline")}
        allowed=[roots[kind]] if kind in roots else list(roots.values())
        raw=Path(value).expanduser()
        if not raw.is_absolute():
            raise StoreError("invalid_draft_path",error_field,shown,"absolute .md path inside the matching project draft directory")
        try:
            info=raw.lstat()
        except FileNotFoundError:
            if must_exist: raise StoreError("draft_missing",error_field,shown,"existing server-created regular .md file") from None
            return None
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode) or info.st_uid!=os.getuid() or info.st_nlink!=1:
            raise StoreError("invalid_draft_path",error_field,shown,"owned regular non-symlink .md file with one link")
        resolved=raw.resolve(strict=True)
        if resolved.parent not in allowed or resolved.suffix.lower()!=".md":
            expected=str(allowed[0]) if len(allowed)==1 else "one advertised project draft directory"
            raise StoreError("invalid_draft_path",error_field,shown,".md file directly inside "+expected)
        os.chmod(resolved,0o600,follow_symlinks=False)
        return resolved

    def _draft(self,db,task,thread,draft_id,published_ok=False):
        row=db.execute("SELECT * FROM drafts WHERE id=?",(draft_id,)).fetchone()
        if row is None:
            raise StoreError("draft_not_found","draft_id",draft_id,"draft_id returned by create_draft to this thread")
        if row["task_id"]!=task or row["owner_thread_id"]!=thread:
            raise StoreError("draft_not_owned","draft_id",draft_id,"draft_id returned by create_draft to this exact native thread")
        if row["published_report_id"] is not None and not published_ok:
            raise StoreError("draft_published","draft_id",draft_id,"new unpublished draft_id")
        return row

    def _draft_source(self,db,task,thread,draft_id):
        draft=self._draft(db,task,thread,draft_id)
        source=self._source(db,task,draft["path"],kind=draft["kind"],error_field="draft_id",error_value=draft_id)
        identity=source.lstat()
        if identity.st_dev!=draft["device"] or identity.st_ino!=draft["inode"]:
            raise StoreError(
                "draft_replaced","draft_id",draft["id"],
                "same server-created file edited in place",
                "Create a new draft and update its body without deleting, replacing, renaming, or recreating the file.",
            )
        return draft,source

    def _recover(self,db):
        self._recover_task_files(db)
        self._finish_source_deletions(db)
        from .cleanup import finish_deletions
        finish_deletions(self,db)

    def _recover_task_files(self,db):
        for row in db.execute("SELECT id FROM tasks").fetchall():
            task=row["id"]; task_dir=self._task_directory(db,task)
            indexed={r["filename"] for r in db.execute("SELECT filename FROM reports WHERE task_id=? AND kind='report'",(task,))}
            for path in task_dir.iterdir():
                if path.name.startswith(".pending_") or (path.suffix==".md" and path.name not in indexed):
                    if path.name!="pipeline.md" and not path.name.startswith(".backup_pipeline_"):
                        regular(path); path.unlink(); sync_directory(task_dir)
            pipeline=db.execute("SELECT id,filename FROM reports WHERE task_id=? AND kind='pipeline'",(task,)).fetchone()
            backups=list(task_dir.glob(".backup_pipeline_*.md"))
            if pipeline:
                latest=db.execute("SELECT digest FROM editions WHERE report_id=? ORDER BY sequence DESC LIMIT 1",(pipeline["id"],)).fetchone()[0]
                target=task_dir/pipeline["filename"]
                if not target.exists() or file_digest(target)!=latest:
                    match=next((p for p in backups if file_digest(p)==latest),None)
                    if match is None: raise StoreError("file_conflict","pipeline",str(target),"bytes matching committed SHA-256")
                    os.replace(match,target); sync_directory(task_dir)
                for backup in backups: backup.unlink(missing_ok=True)
            else:
                for backup in backups: regular(backup); backup.unlink()
            for temp in task_dir.glob(".pending_*"): regular(temp); temp.unlink()

    def _finish_source_deletions(self,db):
        for row in db.execute("SELECT * FROM pending_source_deletions").fetchall():
            source=self._source(db,row["task_id"],row["path"],must_exist=False,error_field="draft_id",error_value=row["draft_id"])
            if source is not None:
                if file_digest(source,private=False,validate_utf8=True)!=row["digest"]:
                    raise StoreError("draft_conflict","draft_id",row["draft_id"],"original accepted SHA-256 "+row["digest"])
                source.unlink(); sync_directory(source.parent)
            db.execute("DELETE FROM pending_source_deletions WHERE task_id=? AND draft_id=?",(row["task_id"],row["draft_id"]))

    @staticmethod
    def _rollback_files(items):
        for target,backup in reversed(items):
            target.unlink(missing_ok=True)
            if backup is not None and backup.exists(): os.replace(backup,target)
            sync_directory(target.parent)

    @staticmethod
    def _finalize_files(items):
        for target,backup in items:
            if backup is not None: backup.unlink(missing_ok=True)
            sync_directory(target.parent)

    def _call(self,db,operation,args,creation_thread,rollbacks,staged):
        task=args.get("task_id")
        if task and db.execute("SELECT 1 FROM tasks WHERE id=?",(task,)).fetchone() is None: raise StoreError("task_not_bound")
        if operation=="list_reports": return self._list(db,args)
        if operation=="read_report": return self._read(db,args)
        if operation=="read_draft": return self._read_draft(db,args,creation_thread)
        scope=task or "new-task:"+creation_thread
        arg_hash=digest(encode(args).encode())
        previous=db.execute("SELECT digest,content_digest,response FROM deliveries WHERE scope=? AND operation=? AND delivery_key=?",(scope,operation,args["request_key"])).fetchone()
        if previous:
            if previous["digest"]!=arg_hash: raise StoreError("delivery_conflict","request_key",args["request_key"],"unused key or exact prior arguments")
            if operation=="write_report":
                draft=self._draft(db,task,creation_thread,args["draft_id"],published_ok=True)
                source=self._source(db,task,draft["path"],must_exist=False,kind=draft["kind"],error_field="draft_id",error_value=args["draft_id"])
                if source is not None:
                    if file_digest(source,private=False,validate_utf8=True,error_field="draft_id",error_value=args["draft_id"])!=previous["content_digest"]:
                        raise StoreError("draft_conflict","draft_id",args["draft_id"],"bytes matching accepted SHA-256 "+previous["content_digest"])
                    db.execute("INSERT OR IGNORE INTO pending_source_deletions VALUES (?,?,?,?)",(task,args["draft_id"],str(source),previous["content_digest"]))
            return dict(json.loads(previous["response"]),replayed=True)
        content_hash=""
        if operation=="create_task":
            project=Path(args["project_root"])
            if not project.is_absolute() or not project.is_dir() or str(project.resolve())!=args["project_root"]:
                raise StoreError("invalid_project","project_root",args["project_root"],"absolute existing canonical directory")
            project_draft_directory(project,"report"); project_draft_directory(project,"pipeline")
            task=unique_identifier(db,"t_"); db.execute("INSERT INTO tasks VALUES (?,?,?)",(task,str(project),self._now()))
            rid,content_hash,size=self._publish_text(db,task,"Original user request","Original task context.","user request",args["request"],"report",rollbacks,staged)
            result=dict(task_id=task,original_report_id=rid)
        elif operation=="set_governance":
            gid=identifier("g_")
            rid,content_hash,size=self._publish_text(db,task,"Governance: "+args["mode"],"Advisory choice and rationale.","coordinator",args["mode"]+"\n\n"+args["rationale"],"report",rollbacks,staged)
            db.execute("INSERT INTO governance VALUES (?,?,?,?)",(gid,task,args["mode"],rid))
            result=dict(governance_id=gid,report_id=rid)
        elif operation=="create_draft":
            draft_id=unique_identifier(db,"d_")
            kind="pipeline" if args["template"]=="pipeline" else "report"
            directory=project_draft_directory(db.execute("SELECT project_root FROM tasks WHERE id=?",(task,)).fetchone()[0],kind)
            path=directory/(draft_id+".md")
            body=draft_template(args["template"],draft_id)
            fd=os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o600)
            staged.append(path)
            with os.fdopen(fd,"wb") as output:
                output.write(body); output.flush(); os.fsync(output.fileno())
            sync_directory(directory)
            identity=path.lstat()
            markdown=body.decode("utf-8")
            replaceable_markers=[line for line in markdown.splitlines()
                                 if (line.startswith("<!--") and line.endswith("-->")
                                     or line.encode("utf-8") in PIPELINE_PLACEHOLDERS)]
            db.execute("INSERT INTO drafts(id,task_id,owner_thread_id,kind,template,path,created_at,device,inode) VALUES (?,?,?,?,?,?,?,?,?)",
                       (draft_id,task,creation_thread,kind,args["template"],str(path),self._now(),identity.st_dev,identity.st_ino))
            result=dict(draft_id=draft_id,draft_path=str(path),kind=kind,
                        template=args["template"],
                        required_first_line=f"Cortex draft ID: `{draft_id}`",
                        edit_instruction="Edit the existing file in place through the built-in apply_patch file tool. Use one call and one independent hunk per marker. Pass the patch as that file tool's exact string input; never use a JavaScript template literal or String.raw. The exact old marker line starts with the patch removal prefix '-' and its replacement with '+'.",
                        replaceable_markers=replaceable_markers,
                        required_replacement_count=len(replaceable_markers),
                        markdown=markdown,total_characters=len(markdown),
                        sha256=digest(body))
            content_hash=digest(body)
        else:
            draft,source=self._draft_source(db,task,creation_thread,args["draft_id"])
            rid,stored_hash,size,content_hash=self._publish_source(db,task,args,draft,source,rollbacks,staged)
            result=dict(report_id=rid,summary=args["summary"],size_bytes=size,sha256=stored_hash)
        db.execute("INSERT INTO deliveries VALUES (?,?,?,?,?,?)",(scope,operation,args["request_key"],arg_hash,content_hash,encode(result)))
        return dict(result,replayed=False)

    @staticmethod
    def _now(): return datetime.now(timezone.utc).isoformat(timespec="microseconds")

    def _publish_text(self,db,task,title,summary,author,markdown,kind,rollbacks,staged):
        return self._publish_blocks(db,task,title,summary,author,(markdown.encode("utf-8"),),kind,rollbacks,staged)

    def _publish_source(self,db,task,args,draft,source,rollbacks,staged):
        kind=draft["kind"]
        existing=db.execute("SELECT id FROM reports WHERE task_id=? AND kind='pipeline'",(task,)).fetchone() if kind=="pipeline" else None
        now=self._now(); previous=None
        if existing:
            rid=existing["id"]; target=self._location(db,rid)
            previous=db.execute("SELECT digest FROM editions WHERE report_id=? ORDER BY sequence DESC LIMIT 1",(rid,)).fetchone()[0]
        else:
            rid=unique_identifier(db,"r_"); target=self._task_directory(db,task)/("pipeline.md" if kind=="pipeline" else rid+".md")
        directory=target.parent; temporary=directory/(identifier(".pending_")+".tmp")
        staged.append(temporary); stored=hashlib.sha256(); content=hashlib.sha256(); size=0
        marker=f"Cortex draft ID: `{draft['id']}`\n\n".encode()
        observed_prefix=bytearray()
        template_lines=draft_template(draft["template"],draft["id"]).splitlines()
        placeholders=tuple(line for line in template_lines
                           if line in PIPELINE_PLACEHOLDERS
                           or line.startswith(b"<!--") and line.endswith(b"-->"))
        guidance_placeholder=None
        placeholder_tail=b""
        placeholder_window=max(map(len,placeholders),default=1)-1
        decoder=codecs.getincrementaldecoder("utf-8")()
        fd=os.open(temporary,os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o600)
        try:
            with os.fdopen(fd,"wb") as output:
                try:
                    for block in file_blocks(source,private=False):
                        if len(observed_prefix)<len(marker):
                            observed_prefix.extend(block[:len(marker)-len(observed_prefix)])
                        decoder.decode(block); output.write(block); content.update(block); stored.update(block); size+=len(block)
                        if placeholders and guidance_placeholder is None:
                            candidate=placeholder_tail+block
                            guidance_placeholder=next((token for token in placeholders if token in candidate),None)
                            placeholder_tail=candidate[-placeholder_window:]
                    decoder.decode(b"",final=True)
                except UnicodeDecodeError:
                    raise StoreError("invalid_utf8","draft_id",draft["id"],"complete valid UTF-8 Markdown file") from None
                if bytes(observed_prefix)!=marker:
                    raise StoreError("draft_marker_missing","draft_id",draft["id"],"unchanged first-line marker "+marker.decode().strip())
                if guidance_placeholder is not None:
                    token=guidance_placeholder.decode()
                    raise StoreError("draft_guidance_remaining","draft_markdown",token,
                                     "every exact template placeholder replaced with complete Markdown",
                                     "Replace the exact received placeholder in the existing draft and retry the same write.")
                if existing:
                    separator=b"\n\n---\n\n"; output.write(separator); stored.update(separator); size+=len(separator)
                    old=hashlib.sha256()
                    for block in file_blocks(target): old.update(block); output.write(block); stored.update(block); size+=len(block)
                    if old.hexdigest()!=previous: raise StoreError("file_conflict","pipeline",str(target),"committed SHA-256 "+previous)
                output.flush(); os.fsync(output.fileno())
            sync_directory(directory)
            backup=None
            if existing:
                backup=directory/(identifier(".backup_pipeline_")+".md")
                with open(backup,"xb") as copy:
                    for block in file_blocks(target): copy.write(block)
                    copy.flush(); os.fsync(copy.fileno())
                os.chmod(backup,0o600); sync_directory(directory)
            os.replace(temporary,target); staged.remove(temporary)
            rollbacks.append((target,backup)); sync_directory(directory)
        except BaseException:
            temporary.unlink(missing_ok=True); raise
        if not existing: db.execute("INSERT INTO reports VALUES (?,?,?,?,?)",(rid,task,kind,target.name,now))
        db.execute("INSERT INTO editions(report_id,title,summary,author,updated_at,digest,size_bytes) VALUES (?,?,?,?,?,?,?)",(rid,args["title"],args["summary"],args["author"],now,stored.hexdigest(),size))
        db.execute("UPDATE drafts SET published_report_id=?,published_digest=? WHERE id=?",
                   (rid,content.hexdigest(),draft["id"]))
        db.execute("INSERT INTO pending_source_deletions VALUES (?,?,?,?)",
                   (task,draft["id"],str(source),content.hexdigest()))
        return rid,stored.hexdigest(),size,content.hexdigest()

    def _publish_blocks(self,db,task,title,summary,author,blocks,kind,rollbacks,staged):
        existing=db.execute("SELECT id FROM reports WHERE task_id=? AND kind='pipeline'",(task,)).fetchone() if kind=="pipeline" else None
        now=self._now(); previous=None
        if existing:
            rid=existing["id"]; target=self._location(db,rid)
            previous=db.execute("SELECT digest FROM editions WHERE report_id=? ORDER BY sequence DESC LIMIT 1",(rid,)).fetchone()[0]
        else:
            rid=unique_identifier(db,"r_"); filename="pipeline.md" if kind=="pipeline" else rid+".md"
            target=self._task_directory(db,task)/filename
            db.execute("INSERT INTO reports VALUES (?,?,?,?,?)",(rid,task,kind,filename,now))
        temporary=target.parent/(identifier(".pending_")+".tmp"); staged.append(temporary)
        hashed=hashlib.sha256(); size=0
        fd=os.open(temporary,os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o600)
        try:
            with os.fdopen(fd,"wb") as output:
                for block in blocks: output.write(block); hashed.update(block); size+=len(block)
                if existing:
                    separator=b"\n\n---\n\n"; output.write(separator); hashed.update(separator); size+=len(separator)
                    old=hashlib.sha256()
                    for block in file_blocks(target): old.update(block); output.write(block); hashed.update(block); size+=len(block)
                    if old.hexdigest()!=previous: raise StoreError("file_conflict","pipeline",str(target),"committed SHA-256 "+previous)
                output.flush(); os.fsync(output.fileno())
            sync_directory(target.parent)
            backup=None
            if existing:
                backup=target.parent/(identifier(".backup_pipeline_")+".md")
                with open(backup,"xb") as copy:
                    for block in file_blocks(target): copy.write(block)
                    copy.flush(); os.fsync(copy.fileno())
                os.chmod(backup,0o600); sync_directory(target.parent)
            os.replace(temporary,target); staged.remove(temporary)
            rollbacks.append((target,backup)); sync_directory(target.parent)
        except BaseException:
            temporary.unlink(missing_ok=True); raise
        db.execute("INSERT INTO editions(report_id,title,summary,author,updated_at,digest,size_bytes) VALUES (?,?,?,?,?,?,?)",(rid,title,summary,author,now,hashed.hexdigest(),size))
        return rid,hashed.hexdigest(),size

    @staticmethod
    def _metadata(row):
        return dict(report_id=row["id"],title=row["title"],summary=row["summary"],author=row["author"],created_at=row["created_at"],updated_at=row["updated_at"],kind=row["kind"],size_bytes=row["size_bytes"],sha256=row["digest"])

    @staticmethod
    def _cursor(value):
        tag=value[0]
        number=lambda item: ("0" if item==0 else _base36(item))
        if tag=="list": return ".".join(("cl",value[1],number(value[2]),number(value[3])))
        if tag=="read": return ".".join(("cr",value[1],value[2][2:],value[3][:12],number(value[4])))
        if tag=="draft": return ".".join(("cd",value[1],value[2][2:],value[3][:12],number(value[4])))
        raise StoreError("invalid_cursor","cursor_kind",tag,"list, read, or draft")

    @staticmethod
    def _cursor_binding(task):
        return hashlib.sha256(("cortex-cursor\0"+task).encode()).hexdigest()[:12]

    @staticmethod
    def _decode(value):
        try:
            parts=value.split(".")
            if len(parts)==4 and parts[0]=="cl":
                return ["list",parts[1],int(parts[2],36),int(parts[3],36)]
            if len(parts)==5 and parts[0] in {"cr","cd"}:
                prefix="r_" if parts[0]=="cr" else "d_"
                return ["read" if parts[0]=="cr" else "draft",parts[1],prefix+parts[2],parts[3],int(parts[4],36)]
            raise ValueError
        except (AttributeError,ValueError,TypeError):
            raise StoreError("invalid_cursor","cursor",value,"exact compact server-issued cursor") from None

    def _list(self,db,args):
        task=args["task_id"]
        binding=self._cursor_binding(task)
        ceiling=db.execute("SELECT COALESCE(MAX(e.sequence),0) FROM editions e JOIN reports r ON r.id=e.report_id WHERE r.task_id=?",(task,)).fetchone()[0]
        before=ceiling+1
        if "cursor" in args:
            decoded=self._decode(args["cursor"])
            if not isinstance(decoded,list) or len(decoded)!=4: raise StoreError("invalid_cursor","cursor",args["cursor"],"catalogue cursor for this task")
            tag,bound,before,high=decoded
            if tag!="list" or bound!=binding or type(before) is not int or type(high) is not int or not 0<before<=high+1<=ceiling+1: raise StoreError("invalid_cursor","cursor",args["cursor"],"catalogue cursor for this task")
            ceiling=high
        rows=db.execute('''SELECT r.*,e.* FROM reports r JOIN editions e ON e.report_id=r.id
            WHERE r.task_id=? AND e.sequence=(SELECT MAX(v.sequence) FROM editions v WHERE v.report_id=r.id AND v.sequence<=?)
            AND e.sequence<? ORDER BY e.sequence DESC LIMIT ?''',(task,ceiling,before,args.get("limit",25)+1)).fetchall()
        page=rows[:args.get("limit",25)]
        cursor=self._cursor(["list",binding,page[-1]["sequence"],ceiling]) if len(rows)>len(page) else None
        return dict(reports=[self._metadata(row) for row in page],next_cursor=cursor)

    def _read_draft(self,db,args,thread):
        draft,source=self._draft_source(db,args["task_id"],thread,args["draft_id"])
        start=0; expected=None
        if "cursor" in args:
            decoded=self._decode(args["cursor"])
            if not isinstance(decoded,list) or len(decoded)!=5:
                raise StoreError("invalid_cursor","cursor",args["cursor"],"cursor for this draft")
            tag,task,draft_id,expected,start=decoded
            if tag!="draft" or task!=self._cursor_binding(args["task_id"]) or draft_id!=draft["id"] or type(start) is not int or start<0:
                raise StoreError("invalid_cursor","cursor",args["cursor"],"cursor for this draft")
        decoder=codecs.getincrementaldecoder("utf-8")(); hashed=hashlib.sha256(); total=0; pieces=[]; end=start+args.get("limit",4000)
        try:
            for block in file_blocks(source,private=False):
                hashed.update(block); text=decoder.decode(block)
                left=max(0,start-total); right=min(len(text),end-total)
                if right>left:pieces.append(text[left:right])
                total+=len(text)
            tail=decoder.decode(b"",final=True)
        except UnicodeDecodeError:
            raise StoreError("invalid_utf8","draft_id",draft["id"],"complete valid UTF-8 Markdown file") from None
        if tail:
            left=max(0,start-total); right=min(len(tail),end-total)
            if right>left:pieces.append(tail[left:right])
            total+=len(tail)
        current=hashed.hexdigest()
        if expected is not None and expected!=current[:12]:
            raise StoreError("draft_cursor_stale","cursor",args["cursor"],"cursor for the current draft digest")
        if start>total:raise StoreError("invalid_cursor","cursor",args.get("cursor"),"position within the draft")
        cursor=self._cursor(["draft",self._cursor_binding(args["task_id"]),draft["id"],current,end]) if end<total else None
        return dict(draft_id=draft["id"],draft_path=str(source),kind=draft["kind"],template=draft["template"],
                    markdown="".join(pieces),total_characters=total,sha256=current,next_cursor=cursor)

    def _read(self,db,args):
        row=db.execute("SELECT r.*,e.* FROM reports r JOIN editions e ON e.report_id=r.id WHERE r.task_id=? AND r.id=? ORDER BY e.sequence DESC LIMIT 1",(args["task_id"],args["report_id"])).fetchone()
        if row is None: raise StoreError("not_found","report_id",args["report_id"],"report in the current task")
        start=0
        if "cursor" in args:
            decoded=self._decode(args["cursor"])
            if not isinstance(decoded,list) or len(decoded)!=5: raise StoreError("invalid_cursor","cursor",args["cursor"],"read cursor for this document")
            tag,task,rid,expected,start=decoded
            if tag!="read" or task!=self._cursor_binding(args["task_id"]) or rid!=row["id"] or type(start) is not int or start<0: raise StoreError("invalid_cursor","cursor",args["cursor"],"read cursor for this document")
            if expected!=row["digest"][:12]:
                if row["kind"]=="pipeline":
                    raise StoreError("cursor_stale","cursor",args["cursor"],"cursor for current pipeline digest")
                raise StoreError("invalid_cursor","cursor",args["cursor"],"exact cursor for this immutable report")
        decoder=codecs.getincrementaldecoder("utf-8")(); hashed=hashlib.sha256(); total=0; pieces=[]; end=start+args.get("limit",4000)
        try:
            for block in file_blocks(self._location(db,row["id"])):
                hashed.update(block); text=decoder.decode(block)
                left=max(0,start-total); right=min(len(text),end-total)
                if right>left: pieces.append(text[left:right])
                total+=len(text)
            tail=decoder.decode(b"",final=True)
        except UnicodeDecodeError: raise StoreError("file_conflict","report_id",row["id"],"valid UTF-8 matching committed digest") from None
        if tail:
            left=max(0,start-total); right=min(len(tail),end-total)
            if right>left: pieces.append(tail[left:right])
            total+=len(tail)
        if hashed.hexdigest()!=row["digest"]: raise StoreError("file_conflict","report_id",row["id"],"committed SHA-256 "+row["digest"])
        if start>total: raise StoreError("invalid_cursor","cursor",args.get("cursor"),"position within the document")
        cursor=self._cursor(["read",self._cursor_binding(args["task_id"]),row["id"],row["digest"],end]) if end<total else None
        return dict(self._metadata(row),markdown="".join(pieces),total_characters=total,next_cursor=cursor)
