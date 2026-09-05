"""Test-only fixture handles for file/index assertions; never an installed API.

All operations use the real thread-bound entry. Internal task IDs are read from
SQLite solely to address on-disk artifacts in storage tests.
"""
import uuid
from cortex_runtime.contracts import StoreError

_handles={}

def call_store(store,operation,args):
    args=dict(args)
    task=args.pop('task_id',None)
    if operation=='create_task':
        thread=str(uuid.uuid5(uuid.NAMESPACE_URL,str(store.path)+args['request_key']))
    else:
        thread=_handles.get((str(store.path),task))
        if thread is None:raise StoreError('not_found')
    result=store.call(operation,args,thread)
    if operation=='create_task':
        with store.connection() as db:
            task=db.execute('SELECT task_id FROM thread_bindings WHERE thread_id=?',(thread,)).fetchone()[0]
        _handles[str(store.path),task]=thread
        return dict(result,task_id=task)
    return result

def thread_for(store,task):return _handles[str(store.path),task]
