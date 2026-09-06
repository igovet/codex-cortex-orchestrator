#!/usr/bin/env python3
"""Delete this project's tasks older than a user-selected activity retention."""
import argparse
import json
import os
from cortex_runtime.cleanup import clear_tasks
from cortex_runtime.project_storage import canonical_project, project_store_directory
from cortex_runtime.store import Store


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--project-root',required=True)
    p.add_argument('--days',type=int,required=True)
    p.add_argument('--keep-thread',action='append',default=[],help='Protect tasks linked to known active native threads.')
    args=p.parse_args()
    os.umask(0o077)
    try:
        if not 0<=args.days<=36500:raise ValueError('invalid retention')
        threads=args.keep_thread+[value for name in ('CODEX_THREAD_ID','CODEX_SESSION_ID') if (value:=os.environ.get(name))]
        project=canonical_project(args.project_root)
        directory=project_store_directory(project)
        result=(clear_tasks(Store(directory,initialize=False,project_root=project),project,args.days,threads)
                if (directory/'cortex.sqlite3').exists()
                else dict(deleted_tasks=0,retention_days=args.days,skipped_protected=0))
        print(json.dumps(result))
    except Exception:
        raise SystemExit('Cortex retention cleanup failed; no private details are displayed.') from None


if __name__=='__main__':main()
