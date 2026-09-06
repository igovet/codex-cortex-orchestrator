"""Read only the current host thread's user input, never model-authored copies."""
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat
import uuid

from .contracts import StoreError

READ_BLOCK_BYTES = 64 * 1024
MAX_HOST_RECORD_BYTES = 4 * 1024 * 1024
MAX_CAPTURE_CHARACTERS = 1_000_000
MAX_CAPTURE_MESSAGES = 1_024


def _owned_file(path):
    info=path.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_uid!=os.getuid()
        or info.st_nlink!=1):
        raise ValueError('untrusted host file')
    return info


class NativeSource(str):
    """Text plus private provenance, never serialized into a tool response."""
    def __new__(cls,text,cursor,messages,completeness='complete',reason=None):
        value=super().__new__(cls,text)
        value.cursor=cursor;value.messages=messages
        value.completeness=completeness;value.reason=reason
        return value


def _regular_attachment(path):
    try:
        resolved=Path(path).expanduser().resolve(strict=True)
        info=resolved.stat()
        if (not stat.S_ISREG(info.st_mode) or info.st_uid!=os.getuid()
            or info.st_nlink!=1):
            return None
        return str(resolved)
    except (OSError,RuntimeError,ValueError):
        return None


def _attachment(part):
    """Retain a recovery locator, or an explicit gap, without copying content."""
    kind=part.get('type')
    if not isinstance(kind,str) or not kind or len(kind)>128:
        return dict(kind='unknown',available=False,recovery='unavailable')
    result=dict(kind=kind)
    name=part.get('name')
    if isinstance(name,str) and name and len(name)<=1_024:result['name']=name
    path=part.get('path')
    if isinstance(path,str) and path and len(path)<=16_384:
        available=_regular_attachment(path)
        if available is not None:
            result.update(available=True,path=available,recovery='read_file')
            return result
    for field in ('uri','resource','url'):
        resource=part.get(field)
        if isinstance(resource,str) and resource and len(resource)<=16_384:
            result.update(available=True,resource=resource,recovery='open_resource')
            return result
    image=part.get('image_url')
    if (isinstance(image,str) and len(image)<=16_384
        and image.startswith(('https://','http://','file://'))):
        result.update(available=True,resource=image,recovery='open_resource')
        return result
    result.update(available=False,recovery='unavailable')
    return result


def _message(entry,thread_id):
    if not isinstance(entry,dict) or entry.get('type')!='event_msg':return None
    payload=entry.get('payload',{})
    if (not isinstance(payload,dict) or payload.get('type')!='item_completed'
        or payload.get('thread_id')!=thread_id):return None
    item=payload.get('item',{})
    if not isinstance(item,dict) or item.get('type')!='UserMessage':return None
    content=item.get('content')
    if not isinstance(content,list):raise ValueError('invalid source content')
    parts=[];attachments=[]
    for part in content:
        if not isinstance(part,dict):raise ValueError('invalid source content')
        if part.get('type')=='text':
            text=part.get('text')
            if not isinstance(text,str):raise ValueError('invalid source text')
            parts.append(text)
            elements=part.get('text_elements',[])
            if not isinstance(elements,list):raise ValueError('invalid source elements')
            for value in elements:
                if not isinstance(value,dict):
                    attachments.append(dict(kind='unknown',available=False,recovery='unavailable'))
                elif value.get('type')!='text':attachments.append(_attachment(value))
        else:
            attachments.append(_attachment(part))
    text='\n'.join(parts)
    if not text and not attachments:raise ValueError('source unavailable')
    identity=item.get('id');turn=payload.get('turn_id')
    if not isinstance(identity,str) or not identity or len(identity)>1_024:
        raise ValueError('missing source identity')
    if not isinstance(turn,str) or not turn or len(turn)>1_024:
        raise ValueError('missing turn identity')
    return dict(id=identity,turn=turn,text=text,attachments=attachments)


def _cursor(source,info,offset):
    source.seek(max(0,offset-256))
    anchor=hashlib.sha256(source.read(min(offset,256))).hexdigest()
    return dict(device=info.st_dev,inode=info.st_ino,offset=offset,anchor=anchor)


def _complete_end(source,size):
    """Return the byte after the last complete JSONL record without reading a tail."""
    position=size
    while position:
        start=max(0,position-READ_BLOCK_BYTES)
        source.seek(start);block=source.read(position-start)
        newline=block.rfind(b'\n')
        if newline>=0:return start+newline+1
        position=start
    return 0


def _reverse_lines(source,end):
    """Yield complete lines newest-first while holding at most one bounded record."""
    position=end;fragment=b'';oversized=False;first=True
    while position:
        start=max(0,position-READ_BLOCK_BYTES)
        source.seek(start);block=source.read(position-start)
        separators=[index for index,value in enumerate(block) if value==10]
        if not separators:
            if not oversized:
                if len(block)+len(fragment)>MAX_HOST_RECORD_BYTES:
                    fragment=b'';oversized=True
                else:fragment=block+fragment
            position=start;continue
        right=block[separators[-1]+1:]
        if oversized:
            if _top_level_type(right) in (None,'event_msg','session_meta'):
                raise ValueError('oversized source record')
        else:
            line=right+fragment
            if not (first and not line) and line:yield start+separators[-1]+1,line
        first=False
        for index in range(len(separators)-1,0,-1):
            value=block[separators[index-1]+1:separators[index]]
            if len(value)>MAX_HOST_RECORD_BYTES:
                if _top_level_type(value) in (None,'event_msg','session_meta'):
                    raise ValueError('oversized source record')
            elif value:yield start+separators[index-1]+1,value
        fragment=block[:separators[0]]
        oversized=len(fragment)>MAX_HOST_RECORD_BYTES
        position=start
    if oversized:
        if _top_level_type(fragment) in (None,'event_msg','session_meta'):
            raise ValueError('oversized source record')
    elif fragment:yield 0,fragment


def _top_level_type(raw):
    """Extract a top-level JSON string type from a bounded record prefix."""
    depth=0;index=0;length=len(raw)
    while index<length:
        value=raw[index]
        if value==34:
            begin=index;index+=1;escaped=False
            while index<length:
                current=raw[index]
                if escaped:escaped=False
                elif current==92:escaped=True
                elif current==34:break
                index+=1
            if index>=length:return None
            if depth==1:
                after=index+1
                while after<length and raw[after] in b' \t\r\n':after+=1
                if after<length and raw[after]==58:
                    try:key=json.loads(raw[begin:index+1])
                    except (UnicodeError,ValueError):return None
                    if key=='type':
                        after+=1
                        while after<length and raw[after] in b' \t\r\n':after+=1
                        if after>=length or raw[after]!=34:return None
                        finish=after+1;escaped=False
                        while finish<length:
                            current=raw[finish]
                            if escaped:escaped=False
                            elif current==92:escaped=True
                            elif current==34:
                                try:return json.loads(raw[after:finish+1])
                                except (UnicodeError,ValueError):return None
                            finish+=1
                        return None
        elif value in (123,91):depth+=1
        elif value in (125,93):depth-=1
        index+=1
    return None


def _entry(raw):
    if len(raw)>MAX_HOST_RECORD_BYTES:raise ValueError('oversized host record')
    value=json.loads(raw)
    if (not isinstance(value,dict) or not isinstance(value.get('type'),str)
        or not isinstance(value.get('payload'),dict)):
        raise ValueError('invalid host record')
    return value


def _session_owner(source,thread_id,project_root=None):
    source.seek(0);raw=source.readline(MAX_HOST_RECORD_BYTES+1)
    if len(raw)>MAX_HOST_RECORD_BYTES or not raw.endswith(b'\n'):
        raise ValueError('invalid session metadata')
    entry=_entry(raw[:-1]);payload=entry['payload']
    if entry['type']!='session_meta' or payload.get('id')!=thread_id:
        raise ValueError('session owner mismatch')
    if project_root is not None:
        cwd=payload.get('cwd')
        location=Path(cwd) if isinstance(cwd,str) else None
        if (location is None or not location.is_absolute() or str(location)!=project_root
            or str(location.resolve(strict=True))!=project_root):
            raise ValueError('session project mismatch')


def _latest_turn_boundary(source,end):
    for offset,raw in _reverse_lines(source,end):
        entry=_entry(raw);payload=entry['payload']
        if entry['type']=='event_msg' and payload.get('type')=='task_started':
            turn=payload.get('turn_id')
            if not isinstance(turn,str) or not turn or len(turn)>1_024:
                raise ValueError('invalid turn boundary')
            return offset,turn
    raise ValueError('turn boundary unavailable')


def _append(messages,seen,message,total):
    prior=seen.get(message['id'])
    if prior is not None:
        if prior!=message:raise ValueError('conflicting source identity')
        return total
    if len(messages)>=MAX_CAPTURE_MESSAGES:raise ValueError('oversized input batch')
    total+=len(message['text'])
    if total>MAX_CAPTURE_CHARACTERS:raise ValueError('oversized input batch')
    seen[message['id']]=message;messages.append(message)
    return total


def _forward_messages(source,start,end,thread_id,turn=None):
    source.seek(start);messages=[];seen={};total=0
    while source.tell()<end:
        remaining=end-source.tell()
        raw=source.readline(min(MAX_HOST_RECORD_BYTES+1,remaining))
        if not raw.endswith(b'\n'):
            kind=_top_level_type(raw)
            while source.tell()<end:
                block=source.readline(min(READ_BLOCK_BYTES,end-source.tell()))
                if block.endswith(b'\n'):break
            else:raise ValueError('incomplete host record')
            if kind in (None,'event_msg','session_meta'):
                raise ValueError('oversized source record')
            continue
        entry=_entry(raw[:-1]);message=_message(entry,thread_id)
        if message is not None and (turn is None or message['turn']==turn):
            total=_append(messages,seen,message,total)
    return messages


def request_from_rollout(path,thread_id,project_root=None):
    """Find the latest native turn boundary with fixed-size reverse reads."""
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
    try:
        info=os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid!=os.getuid()
            or info.st_nlink!=1):raise ValueError('untrusted host file')
        with os.fdopen(fd,'rb',closefd=False) as source:
            _session_owner(source,thread_id,project_root)
            end=_complete_end(source,info.st_size)
            start,turn=_latest_turn_boundary(source,end)
            messages=_forward_messages(source,start,end,thread_id,turn)
            cursor=_cursor(source,info,end)
    finally:os.close(fd)
    if not messages:raise ValueError('source unavailable')
    token='$cortex:orchestrator';first=messages[0]['text'];archive=first
    if first.startswith(token) and (len(first)==len(token) or first[len(token)].isspace()):
        archive=first[len(token):]
        if archive.startswith('\r\n'):archive=archive[2:]
        elif archive and archive[0].isspace():archive=archive[1:]
    messages[0]['archive_text']=archive
    text='\n\n'.join(message.get('archive_text',message['text']) for message in messages)
    if not text and not any(message['attachments'] for message in messages):
        raise ValueError('source unavailable')
    completeness='partial' if any(not item['available'] for message in messages
                                  for item in message['attachments']) else 'complete'
    return NativeSource(text,cursor,messages,completeness)


def _validate_index(connection):
    connection.execute('PRAGMA query_only=ON')
    tables={row[0] for row in connection.execute(
        "SELECT name FROM sqlite_schema WHERE type='table'")}
    if not {'_sqlx_migrations','threads'}<=tables:raise ValueError('invalid host index')
    columns={row[1]:(str(row[2]).upper(),row[3],row[5])
             for row in connection.execute("PRAGMA table_info('threads')")}
    required={'id':('TEXT',0,1),'rollout_path':('TEXT',1,0),'cwd':('TEXT',1,0)}
    if any(columns.get(name)!=definition for name,definition in required.items()):
        raise ValueError('invalid host index')
    migration_columns={row[1]:str(row[2]).upper()
                       for row in connection.execute("PRAGMA table_info('_sqlx_migrations')")}
    if migration_columns.get('version')!='BIGINT' or migration_columns.get('success')!='BOOLEAN':
        raise ValueError('invalid host index')
    count,failed=connection.execute(
        'SELECT count(*),sum(CASE WHEN success=1 THEN 0 ELSE 1 END) FROM _sqlx_migrations').fetchone()
    if not count or failed:raise ValueError('invalid host index')


def _source_path(thread_id,project_root):
    """Resolve exactly one owned session from the active Codex index."""
    try:
        if not isinstance(thread_id,str) or str(uuid.UUID(thread_id))!=thread_id:
            raise ValueError('invalid thread')
        requested=Path(project_root)
        if not requested.is_absolute():raise ValueError('invalid project')
        project_root=str(requested.resolve(strict=True))
        if str(requested)!=project_root:raise ValueError('invalid project')
        home=Path(os.environ.get('CODEX_HOME',str(Path.home()/'.codex'))).resolve(strict=True)
        database=home/'state_5.sqlite';_owned_file(database)
        connection=sqlite3.connect(database.as_uri()+'?mode=ro',uri=True,timeout=2)
        try:
            _validate_index(connection)
            rows=connection.execute('SELECT cwd,rollout_path FROM threads WHERE id=?',(thread_id,)).fetchall()
        finally:connection.close()
        if len(rows)!=1:raise ValueError('thread/project mismatch')
        indexed=Path(rows[0][0]) if isinstance(rows[0][0],str) else None
        if (indexed is None or not indexed.is_absolute()
            or str(indexed)!=project_root or str(indexed.resolve(strict=True))!=project_root):
            raise ValueError('thread/project mismatch')
        raw=rows[0][1]
        if not isinstance(raw,str):raise ValueError('invalid rollout path')
        path=Path(raw)
        if not path.is_absolute() or path.suffix!='.jsonl':raise ValueError('invalid rollout path')
        _owned_file(path);resolved=path.resolve(strict=True)
        if path!=resolved:raise ValueError('invalid rollout path')
        resolved.relative_to((home/'sessions').resolve(strict=True))
        return resolved,project_root
    except (OSError,RuntimeError,ValueError,TypeError,sqlite3.Error):
        raise StoreError('host_request_unavailable') from None


def original_request(thread_id,project_root):
    try:
        path,project=_source_path(thread_id,project_root)
        return request_from_rollout(path,thread_id,project)
    except (OSError,RuntimeError,ValueError,TypeError):
        raise StoreError('host_request_unavailable') from None


def pending_requests(thread_id,project_root,cursor):
    """Stream appended complete records; a failed transaction keeps its cursor."""
    try:
        path,project=_source_path(thread_id,project_root)
        if cursor is None:return request_from_rollout(path,thread_id,project)
        if (not isinstance(cursor,dict) or set(cursor)!={'device','inode','offset','anchor'}
            or type(cursor['device']) is not int or type(cursor['inode']) is not int
            or type(cursor['offset']) is not int or cursor['offset']<0
            or not isinstance(cursor['anchor'],str) or len(cursor['anchor'])!=64):
            raise ValueError('invalid source cursor')
        fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
        try:
            info=os.fstat(fd)
            if (not stat.S_ISREG(info.st_mode) or info.st_uid!=os.getuid()
                or info.st_nlink!=1 or info.st_dev!=cursor['device']
                or info.st_ino!=cursor['inode'] or info.st_size<cursor['offset']):
                raise ValueError('changed source')
            with os.fdopen(fd,'rb',closefd=False) as source:
                _session_owner(source,thread_id,project)
                if _cursor(source,info,cursor['offset'])!=cursor:raise ValueError('changed source')
                end=_complete_end(source,info.st_size)
                if end<cursor['offset']:raise ValueError('changed source')
                messages=_forward_messages(source,cursor['offset'],end,thread_id)
                current=_cursor(source,info,end)
        finally:os.close(fd)
        completeness='partial' if any(not item['available'] for message in messages
                                      for item in message['attachments']) else 'complete'
        return NativeSource('',current,messages,completeness)
    except (OSError,RuntimeError,ValueError,TypeError,KeyError,json.JSONDecodeError):
        raise StoreError('host_request_unavailable') from None
