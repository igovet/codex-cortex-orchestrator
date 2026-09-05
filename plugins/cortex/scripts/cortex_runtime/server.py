"""Small newline-delimited JSON-RPC MCP server with value-free diagnostics."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time
import uuid

from .contracts import BY_NAME, MAX_REQUEST_BYTES, StoreError, TOOLS, ERROR_HELP, validate
from .host_source import original_request, pending_requests
from .store import Store, private_directory

PLUGIN = Path(__file__).resolve().parents[2]
VERSION = json.loads((PLUGIN / '.codex-plugin/plugin.json').read_text())['version']
CATALOGUE_DIGEST = hashlib.sha256(json.dumps(TOOLS, sort_keys=True).encode()).hexdigest()


def thread_context(meta):
    """Consume only host transport metadata, never model-authored arguments."""
    if not isinstance(meta,dict) or not isinstance(meta.get('x-codex-turn-metadata'),dict):
        raise StoreError('thread_metadata_missing')
    thread=meta.get('threadId')
    parent=meta['x-codex-turn-metadata'].get('parent_thread_id')
    for value in [thread]+([parent] if parent is not None else []):
        try:
            if not isinstance(value,str) or str(uuid.UUID(value))!=value:raise ValueError
        except (ValueError,AttributeError):raise StoreError('thread_metadata_invalid') from None
    if thread==parent:raise StoreError('thread_metadata_invalid')
    return thread,parent


def safe_observation_fields(operation,arguments,result=None):
    """Expose only bounded routing selectors needed to audit agent behavior."""
    arguments=arguments if isinstance(arguments,dict) else {}
    if operation=='create_task':
        return dict(original_request_sha256=(result or {}).get('original_request_sha256'))
    if operation=='read_report':
        result=result if isinstance(result,dict) else {}
        report_id=result.get('report_id') or arguments.get('report_id')
        return dict(document_kind=result.get('kind') or ('pipeline' if not report_id else 'unresolved'),
                    report_id=report_id,
                    requested_limit=arguments.get('limit',4000),
                    page='continuation' if arguments.get('cursor') else 'start')
    if operation=='list_reports':
        return dict(requested_limit=arguments.get('limit',25),
                    page='continuation' if arguments.get('cursor') else 'start')
    if operation=='read_draft':
        return dict(draft_id=arguments.get('draft_id'),page='continuation' if arguments.get('cursor') else 'start')
    if operation=='create_draft':return dict(template=arguments.get('template'))
    if operation=='write_report':
        result=result if isinstance(result,dict) else {}
        return dict(draft_id=arguments.get('draft_id'),report_id=result.get('report_id'),
                    summary_characters=len(arguments.get('summary','')))
    if operation=='set_governance':return dict(governance_mode=arguments.get('mode'))
    return {}


def observe(operation, outcome, replayed=False, meta=None, arguments=None, result=None):
    """Optional bounded passive observation; no payloads, hooks or workflow checks."""
    location = os.environ.get('CORTEX_OBSERVATION_DIR')
    if not location:
        return
    try:
        root = private_directory(location)
        path = root / f'{os.getpid()}.jsonl'
        row = dict(pid=os.getpid(), time_ns=time.time_ns(), operation=operation,
                   outcome=outcome, replayed=replayed, version=VERSION,
                   catalogue_digest=CATALOGUE_DIGEST)
        if operation == 'initialize':
            row['plugin_path'] = str(PLUGIN)
        elif operation in BY_NAME:
            try:
                thread,parent=thread_context(meta)
                row.update(thread_id=thread,parent_thread_id=parent,
                           context_source='MCP _meta',turn_metadata_type='object')
            except StoreError as exc:
                row['context_error']=str(exc)
            row.update(safe_observation_fields(operation,arguments,result))
        fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            if os.fstat(fd).st_size < 256_000:
                os.write(fd, (json.dumps(row)+'\n').encode())
        finally:
            os.close(fd)
    except (OSError, StoreError):
        pass  # Observation never governs execution or changes a committed write.


class ProtocolError(Exception):
    def __init__(self, code, message):
        self.code=code;self.message=message


class Server:
    def __init__(self, directory=None):
        self.directory = directory or os.environ.get('CORTEX_DATA_DIR') or str(Path(os.environ.get('CODEX_HOME', str(Path.home()/'.codex'))) / 'cortex')
        self.store = None
        self.request_source = original_request
        self.steering_source = pending_requests

    def dispatch(self, method, params):
        if method == 'initialize':
            if not isinstance(params,dict) or not isinstance(params.get('protocolVersion'),str) or not isinstance(params.get('capabilities'),dict) or not isinstance(params.get('clientInfo'),dict) or any(not isinstance(params['clientInfo'].get(k),str) for k in ('name','version')):
                raise ProtocolError(-32602,'Invalid initialize parameters. Supply protocolVersion (string), capabilities (object), and clientInfo with name and version strings.')
            observe('initialize','success')
            return dict(protocolVersion='2025-11-25', capabilities=dict(tools=dict(listChanged=False)), serverInfo=dict(name='cortex',version=VERSION), instructions='Cortex resolves the current task only from native MCP thread metadata, including registered parent inheritance; task and thread identifiers never appear in tool arguments or results. SQLite metadata lives under $CODEX_HOME/cortex, where CODEX_HOME is already the .codex directory. create_draft chooses an editable path under <project_root>/.cortex, binds the short draft identifier to the calling native thread, pre-fills English report or pipeline headings, and returns the complete initial Markdown; the same identifier appears in the filename and Markdown. Use that returned Markdown as the source of truth and update its body in place with native file tools; do not call read_draft immediately after creation. read_draft remains available for recovery or a genuinely needed later read of an existing draft. Give only draft_id and short metadata to write_report. Markdown report bodies never cross write_report and are streamed without an application size limit into task files under <project_root>/.codex/cortex/<task>. Report files are immutable, while the writer prepends each pipeline edition to that task pipeline.md. Catalogue, report and draft reads are cursor-bounded. Coordinators read previews, the current pipeline beginning, selected authored report opening decision briefs for consequential choices (never ordinary report continuation pages), and only their exact pipeline draft content returned by create_draft or recovered through read_draft. Workers read selected relevant report bodies, their own returned or recovered draft, and project evidence. Native workers load their complete assigned bundled skill through its advertised standard mechanism. They may read that exact skill file but never explore plugin internals. Original user source is captured by storage from the current native UserMessage receipt, not transcribed by the model. After compaction or restart both roles refresh their live catalogue and durable context before continuing. All seven tools remain available to both roles; governance is advisory and storage has no semantic role gate.')
        if method == 'ping':
            return {}
        if method == 'tools/list':
            return dict(tools=TOOLS)
        if method != 'tools/call':
            raise ProtocolError(-32601,'Unknown method. Use initialize, ping, tools/list or tools/call; discover tool names through tools/list.')
        if not isinstance(params, dict) or set(params)-{'name','arguments','_meta'} or not isinstance(params.get('name'), str) or ('arguments' in params and not isinstance(params['arguments'],dict)):
            observe('tools/call','invalid_arguments')
            raise ProtocolError(-32602,'Malformed tools/call parameters. Supply name as a string and arguments as an object; only name, arguments and _meta are allowed.')
        name = params['name']
        if name not in BY_NAME:
            observe('tools/call','unknown_tool')
            raise ProtocolError(-32602,'Unknown tool. Call tools/list and use an advertised name; do not reconstruct retired names.')
        try:
            validate(name,params.get('arguments',{}))
            thread,parent=thread_context(params.get('_meta'))
            if self.store is None:
                self.store = Store(self.directory)
            source=None
            if name=='create_task':
                if parent is not None:raise StoreError('child_creation')
                source=lambda:self.request_source(thread,params['arguments']['project_root'])
            steering=lambda project,cursor:self.steering_source(thread,project,cursor)
            data = self.store.call(name, params.get('arguments',{}),thread,parent,
                                   original_request=source,steering_source=steering if parent is None else None)
            observe(name,'success',data.get('replayed',False),params.get('_meta'),params.get('arguments'),data)
            return dict(content=[dict(type='text',text=json.dumps(data,ensure_ascii=False))], structuredContent=data, isError=False)
        except StoreError as exc:
            code = str(exc)
            failure = exc
        except Exception:
            code = 'storage_error'
            failure = StoreError(code)
        observe(name,code,meta=params.get('_meta'),arguments=params.get('arguments'))
        return self.error(failure)

    @staticmethod
    def error(failure):
        def public(value):
            if not isinstance(value,str): return value
            return re.sub(r'(?<![0-9a-f])t_[0-9a-f]{12}(?![0-9a-f])','<task>',value)
        code=str(failure)
        data=dict(error=code,message=ERROR_HELP.get(code,ERROR_HELP['storage_error']),
                  correction=public(failure.correction or ERROR_HELP.get(code,ERROR_HELP['storage_error'])))
        if failure.field:data['field']=failure.field
        if failure.received is not None:data['received']=public(failure.received)
        if failure.expected is not None:data['expected']=public(failure.expected)
        data['write_state']='uncertain' if code in ('storage_error','file_conflict','unsafe_storage') else 'rejected'
        return dict(content=[dict(type='text',text=json.dumps(data))],isError=True)


def reject_duplicates(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError
        result[key] = value
    return result


def main():
    os.umask(0o077)
    server = Server()
    while True:
        line = sys.stdin.buffer.readline(MAX_REQUEST_BYTES+1)
        if not line:
            break
        request_id = None
        try:
            if len(line)>MAX_REQUEST_BYTES:
                while line and not line.endswith(b'\n'):
                    line = sys.stdin.buffer.readline(MAX_REQUEST_BYTES+1)
                raise ValueError
            item = json.loads(line, object_pairs_hook=reject_duplicates)
            if not isinstance(item, dict) or item.get('jsonrpc')!='2.0' or not isinstance(item.get('method'),str):
                raise ProtocolError(-32600,'Invalid JSON-RPC request. Use an object with jsonrpc=2.0, a string method, and a string or integer id for requests.')
            request_id = item.get('id')
            if 'id' in item and (type(request_id) not in (str,int) or isinstance(request_id,str) and len(request_id)>128):
                request_id = None
                raise ProtocolError(-32600,'Request id must be a string of at most 128 characters or an integer; null and boolean ids are not valid MCP request ids.')
            if 'id' not in item:
                continue
            result = server.dispatch(item['method'],item.get('params',{}))
            reply = dict(jsonrpc='2.0',id=request_id,result=result)
        except ProtocolError as exc:
            reply = dict(jsonrpc='2.0',id=request_id,error=dict(code=exc.code,message=exc.message))
        except (ValueError, UnicodeError, RecursionError):
            reply = dict(jsonrpc='2.0',id=None,error=dict(code=-32700,message='Cannot parse the request. Send one valid UTF-8 JSON object per line without duplicate keys; keep each line within 2,000,000 bytes. Nothing in this frame was executed.'))
        except Exception:
            reply = dict(jsonrpc='2.0',id=request_id,error=dict(code=-32603,message='The server failed to complete this request. Do not change arguments to guess a repair. Check the local server; a write may be uncertain, so preserve and retry only its exact original request and delivery key.'))
        sys.stdout.write(json.dumps(reply,ensure_ascii=False)+'\n')
        sys.stdout.flush()
