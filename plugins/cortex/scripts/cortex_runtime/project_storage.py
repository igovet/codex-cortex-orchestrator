"""Resolve one project-local store from native identity, independently of messages."""
from __future__ import annotations

from collections import OrderedDict
from contextlib import closing
import fcntl
import os
from pathlib import Path
import re
import sqlite3
import stat
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
    return Path(canonical_project(project_root)) / ".cortex"


def _private_existing_directory(path, error="unsafe_storage"):
    """Validate one existing owner-private directory without following links."""
    path = Path(path)
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    if (stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode)
            or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077):
        raise StoreError(error)
    return path


def _private_tree(path):
    """Validate an opaque published-task tree before an atomic directory move."""
    for root, directories, files in os.walk(path, topdown=True, followlinks=False):
        root_info = Path(root).lstat()
        if (stat.S_ISLNK(root_info.st_mode) or root_info.st_uid != os.getuid()
                or stat.S_IMODE(root_info.st_mode) & 0o077):
            raise StoreError("unsafe_storage")
        for name in directories + files:
            info = (Path(root) / name).lstat()
            if stat.S_ISLNK(info.st_mode) or info.st_uid != os.getuid():
                raise StoreError("unsafe_storage")
            if stat.S_ISDIR(info.st_mode):
                if stat.S_IMODE(info.st_mode) & 0o077:
                    raise StoreError("unsafe_storage")
            elif (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                  or stat.S_IMODE(info.st_mode) & 0o077):
                raise StoreError("unsafe_storage")


def migrate_legacy_project_storage(project_root):
    """Move recognized legacy project storage once; never read it as a fallback."""
    project = Path(canonical_project(project_root))
    source = project / ".codex" / "cortex"
    if _private_existing_directory(source) is None:
        return False
    from .store import private_directory, regular, sync_directory
    database_names = (
        "cortex.sqlite3", "cortex.sqlite3-wal", "cortex.sqlite3-shm",
        "cortex.sqlite3-journal",
    )
    source_database = [source / name for name in database_names
                       if (source / name).exists() or (source / name).is_symlink()]
    source_tasks = sorted(path for path in source.iterdir()
                          if re.fullmatch(r"t_[0-9a-f]{12}", path.name))
    if not source_database and not source_tasks:
        return False
    destination = private_directory(project / ".cortex")
    source_lock_path = source / ".access.lock"
    destination_lock_path = destination / ".access.lock"
    source_lock = os.open(source_lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    destination_lock = os.open(destination_lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    moved = False
    try:
        regular(source_lock_path); regular(destination_lock_path)
        try:
            fcntl.flock(source_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(destination_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (BlockingIOError, OSError):
            raise StoreError("storage_busy") from None
        destination_database = [destination / name for name in database_names
                                if (destination / name).exists() or (destination / name).is_symlink()]
        for path in source_database:
            regular(path)
        for path in destination_database:
            regular(path)
        for path in source_tasks:
            if not path.is_dir() or path.is_symlink():
                raise StoreError("unsafe_storage")
            _private_tree(path)
        conflicts = destination_database if source_database else []
        conflicts += [destination / path.name for path in source_tasks
                      if (destination / path.name).exists() or (destination / path.name).is_symlink()]
        if conflicts:
            raise StoreError("legacy_storage_conflict")
        for path in source_database:
            os.replace(path, destination / path.name)
            moved = True
        for path in source_tasks:
            os.replace(path, destination / path.name)
            moved = True
        if moved:
            sync_directory(source); sync_directory(destination)
        return moved
    finally:
        os.close(destination_lock)
        os.close(source_lock)


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
