"""Resolve one project-local store from native identity, independently of messages."""
from __future__ import annotations

from collections import OrderedDict
from contextlib import closing
import os
from pathlib import Path
import sqlite3
import uuid

from .contracts import StoreError
from .host_source import _owned_file, _validate_index


def canonical_project(value):
    try:
        project = Path(value)
        if (not project.is_absolute() or not project.is_dir()
                or str(project.resolve(strict=True)) != str(project)):
            raise ValueError
        return str(project)
    except (OSError, RuntimeError, TypeError, ValueError):
        raise StoreError("invalid_project") from None


def project_store_directory(project_root):
    """Compute the only supported location; neither create nor search directories."""
    return Path(canonical_project(project_root)) / ".codex" / "cortex"


def native_project(thread_id, parent_thread_id=None, *, check_parent=True):
    """Read exact thread rows from the supported host index, never a rollout body.

    A newly spawned child can precede its own index row. Its native MCP parent
    then supplies the project, and the store still requires the parent's binding.
    When both rows exist their projects must agree.
    """
    try:
        identities = [thread_id] + ([parent_thread_id] if parent_thread_id else [])
        if any(not isinstance(value, str) or str(uuid.UUID(value)) != value
               for value in identities) or thread_id == parent_thread_id:
            raise ValueError
        home = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))).resolve(strict=True)
        database = home / "state_5.sqlite"
        _owned_file(database)
        with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=2)) as db:
            _validate_index(db)
            rows = db.execute("SELECT id,cwd FROM threads WHERE id=? OR id=?",
                              (thread_id, parent_thread_id)).fetchall()
            if parent_thread_id is not None and parent_thread_id not in {row[0] for row in rows}:
                raise ValueError
            if check_parent and db.execute("SELECT 1 FROM sqlite_schema WHERE type='table' AND name='thread_spawn_edges'").fetchone():
                columns={row[1]:str(row[2]).upper() for row in db.execute("PRAGMA table_info('thread_spawn_edges')")}
                if any(columns.get(name)!='TEXT' for name in ('parent_thread_id','child_thread_id')):
                    raise ValueError
                edges=db.execute('SELECT parent_thread_id FROM thread_spawn_edges WHERE child_thread_id=?',(thread_id,)).fetchall()
                if edges and (len(edges)!=1 or edges[0][0]!=parent_thread_id):
                    raise StoreError('project_context_conflict')
        if not rows:
            raise ValueError
        projects = {canonical_project(row[1]) for row in rows}
        if len(projects) != 1:
            raise StoreError("project_context_conflict")
        return projects.pop()
    except StoreError as error:
        if str(error) == "project_context_conflict":
            raise
        raise StoreError("project_context_unavailable") from None
    except (OSError, RuntimeError, TypeError, ValueError, sqlite3.Error):
        raise StoreError("project_context_unavailable") from None


class ProjectResolver:
    """Retain bounded verified routes, without silently following a changed cwd."""
    def __init__(self, reader=None):
        self.reader = reader or native_project
        self.routes = OrderedDict()

    def resolve(self, thread_id, parent_thread_id=None):
        previous = self.routes.get(thread_id)
        if previous is not None and previous[1] != parent_thread_id:
            raise StoreError("thread_conflict")
        try:
            project = canonical_project(self.reader(thread_id, parent_thread_id))
        except StoreError as error:
            if str(error) != "project_context_unavailable" or previous is None:
                raise
            # The native source/index may disappear after a verified binding.
            # A fresh process still needs the native index to locate its archive.
            project = canonical_project(previous[0])
        parent = self.routes.get(parent_thread_id)
        if ((previous is not None and previous[0] != project)
                or (parent is not None and parent[0] != project)):
            raise StoreError("project_context_conflict")
        return project

    def remember(self, thread_id, parent_thread_id, project):
        """Retain only a route whose task binding the store actually accepted."""
        self.routes[thread_id] = (project, parent_thread_id)
        self.routes.move_to_end(thread_id)
        while len(self.routes) > 256:
            self.routes.popitem(last=False)
