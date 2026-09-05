"""Read only the current host thread's user input, never model-authored copies."""
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat

from .contracts import StoreError

MAX_TAIL_BYTES = 8 * 1024 * 1024
MAX_SOURCE_CHARACTERS = 250_000


def _owned_file(path):
    info=path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.getuid():
        raise ValueError('untrusted host file')


class NativeSource(str):
    """Text plus private provenance, never serialized into a tool response."""
    def __new__(cls,text,cursor,messages):
        value=super().__new__(cls,text)
        value.cursor=cursor;value.messages=messages
        return value


def _message(entry,thread_id):
    if not isinstance(entry,dict) or entry.get('type')!='event_msg':return None
    payload=entry.get('payload',{})
    if (payload.get('type')!='item_completed' or payload.get('thread_id')!=thread_id):return None
    item=payload.get('item',{})
    if item.get('type')!='UserMessage':return None
    parts=[p['text'] for p in item.get('content',[]) if isinstance(p,dict)
           and p.get('type')=='text' and isinstance(p.get('text'),str)]
    text='\n'.join(parts)
    if not text or len(text)>MAX_SOURCE_CHARACTERS:raise ValueError('source unavailable')
    if not isinstance(item.get('id'),str) or not item['id']:raise ValueError('missing source identity')
    return dict(id=item['id'],turn=payload.get('turn_id'),text=text)


def _cursor(source,info,offset):
    source.seek(max(0,offset-256))
    anchor=hashlib.sha256(source.read(min(offset,256))).hexdigest()
    return dict(device=info.st_dev,inode=info.st_ino,offset=offset,anchor=anchor)


def request_from_rollout(path,thread_id):
    """Only typed UserMessage receipts from the latest native turn are source."""
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
    try:
        info=os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.getuid():
            raise ValueError('untrusted host file')
        with os.fdopen(fd,'rb',closefd=False) as source:
            offset=max(0,info.st_size-MAX_TAIL_BYTES)
            source.seek(offset)
            if offset:source.readline()
            start=source.tell();tail=source.read(MAX_TAIL_BYTES)
            complete=tail.rfind(b'\n')+1
            cursor=_cursor(source,info,start+complete)
            tail=tail[:complete]
    finally:os.close(fd)
    active=None;messages=[];seen=set()
    for line in tail.splitlines():
        entry=json.loads(line)
        if not isinstance(entry,dict) or entry.get('type')!='event_msg':continue
        item=entry.get('payload',{})
        if item.get('type')=='task_started':
            active=item.get('turn_id');messages=[];seen=set()
        message=_message(entry,thread_id)
        if message and active and message['turn']==active and message['id'] not in seen:
            messages.append(message);seen.add(message['id'])
    text='\n\n'.join(m['text'] for m in messages).strip()
    token='$cortex:orchestrator'
    if text.startswith(token) and (len(text)==len(token) or text[len(token)].isspace()):
        text=text[len(token):].strip()
    if not text or len(text)>MAX_SOURCE_CHARACTERS:raise ValueError('source unavailable')
    return NativeSource(text,cursor,messages)


def _source_path(thread_id,project_root):
    """Fail closed if the host cannot provide a source scoped to this thread/project."""
    try:
        home=Path(os.environ.get('CODEX_HOME',str(Path.home()/'.codex'))).resolve(strict=True)
        database=home/'state_5.sqlite';_owned_file(database)
        connection=sqlite3.connect(database.as_uri()+'?mode=ro',uri=True,timeout=2)
        try:
            row=connection.execute('SELECT cwd,rollout_path FROM threads WHERE id=?',(thread_id,)).fetchone()
        finally:connection.close()
        if row is None or str(Path(row[0]).resolve(strict=True))!=project_root:
            raise ValueError('thread/project mismatch')
        path=Path(row[1]);_owned_file(path)
        resolved=path.resolve(strict=True)
        resolved.relative_to((home/'sessions').resolve(strict=True))
        return resolved
    except (OSError,ValueError,TypeError,sqlite3.Error):
        raise StoreError('host_request_unavailable') from None


def original_request(thread_id,project_root):
    try:return request_from_rollout(_source_path(thread_id,project_root),thread_id)
    except (OSError,ValueError,TypeError):raise StoreError('host_request_unavailable') from None


def pending_requests(thread_id,project_root,cursor):
    """Stream only appended complete records; a failed transaction keeps its cursor."""
    try:
        path=_source_path(thread_id,project_root)
        if cursor is None:return original_request(thread_id,project_root)
        fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
        try:
            info=os.fstat(fd)
            if (not stat.S_ISREG(info.st_mode) or info.st_uid!=os.getuid()
                or info.st_dev!=cursor['device'] or info.st_ino!=cursor['inode']
                or info.st_size<cursor['offset']):raise ValueError('changed source')
            with os.fdopen(fd,'rb',closefd=False) as source:
                if _cursor(source,info,cursor['offset'])!=cursor:raise ValueError('changed source')
                source.seek(cursor['offset']);offset=cursor['offset'];messages=[];total=0
                while source.tell()<info.st_size:
                    line=source.readline(min(MAX_TAIL_BYTES+1,info.st_size-source.tell()))
                    if len(line)>MAX_TAIL_BYTES:raise ValueError('oversized host record')
                    if not line.endswith(b'\n'):break
                    message=_message(json.loads(line),thread_id)
                    if message:
                        total+=len(message['text'])
                        if total>MAX_TAIL_BYTES:raise ValueError('oversized input batch')
                        messages.append(message)
                    offset=source.tell()
                return NativeSource('',_cursor(source,info,offset),messages)
        finally:os.close(fd)
    except (OSError,ValueError,TypeError,KeyError):
        raise StoreError('host_request_unavailable') from None
