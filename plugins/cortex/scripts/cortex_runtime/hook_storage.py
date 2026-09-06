"""Short, task-scoped hook transactions sharing the MCP storage services.

Only metadata is kept in SQLite. Prompt hooks record pending native-source
receipts, preserving authoritative message identity and redaction before Markdown
publication by the shared storage service. Importing this module performs no I/O.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .contracts import StoreError

HOOK_SCHEMA = '''
CREATE TABLE IF NOT EXISTS source_turns (
 task_id TEXT NOT NULL REFERENCES tasks(id), thread_id TEXT NOT NULL,
 turn_id TEXT NOT NULL, report_id TEXT NOT NULL REFERENCES reports(id), digest TEXT NOT NULL,
 PRIMARY KEY(thread_id,turn_id,report_id)
);
CREATE TABLE IF NOT EXISTS hook_pending_sources (
 task_id TEXT NOT NULL REFERENCES tasks(id), thread_id TEXT NOT NULL,
 turn_id TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY(thread_id,turn_id)
);
CREATE TABLE IF NOT EXISTS hook_events (
 task_id TEXT NOT NULL REFERENCES tasks(id), thread_id TEXT NOT NULL,
 event_key TEXT PRIMARY KEY, event_name TEXT NOT NULL, metadata TEXT NOT NULL,
 created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS hook_events_by_task ON hook_events(task_id,created_at);
CREATE TABLE IF NOT EXISTS hook_hints (
 task_id TEXT NOT NULL REFERENCES tasks(id), thread_id TEXT NOT NULL,
 event_name TEXT NOT NULL, state_key TEXT NOT NULL,
 PRIMARY KEY(thread_id,event_name)
);
CREATE TABLE IF NOT EXISTS hook_agent_bindings (
 agent_id TEXT PRIMARY KEY, parent_thread_id TEXT NOT NULL,
 task_id TEXT NOT NULL REFERENCES tasks(id), receipt TEXT NOT NULL UNIQUE,
 created_at TEXT NOT NULL
);
'''


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=True,
                                     separators=(",", ":")).encode()).hexdigest()


class HookStorage:
    def __init__(self, store):
        self.store = store

    def _context(self, db, session_id, cwd, agent_id=None):
        # A hook's parent session id alone never establishes a child's identity.
        parent = db.execute("SELECT * FROM thread_bindings WHERE thread_id=?", (session_id,)).fetchone()
        if parent is None:
            return None
        binding = db.execute("SELECT * FROM binding_receipts WHERE thread_id=?", (session_id,)).fetchone()
        if binding is None or binding["state"] != "cortex":
            return None
        actor = parent
        if agent_id is not None:
            actor = db.execute("SELECT * FROM thread_bindings WHERE thread_id=?", (agent_id,)).fetchone()
            hook_binding = db.execute("SELECT * FROM hook_agent_bindings WHERE agent_id=?", (agent_id,)).fetchone()
            if actor is None:
                if hook_binding is None or hook_binding["parent_thread_id"] != session_id or hook_binding["task_id"] != parent["task_id"]:
                    return None
                actor = dict(thread_id=agent_id, parent_thread_id=session_id, task_id=parent["task_id"])
                binding = dict(hook_binding)
            else:
                if actor["parent_thread_id"] != session_id or actor["task_id"] != parent["task_id"]:
                    return None
                if hook_binding is not None and (hook_binding["parent_thread_id"] != session_id or hook_binding["task_id"] != actor["task_id"]):
                    return None
                child_binding = db.execute("SELECT * FROM binding_receipts WHERE thread_id=?", (agent_id,)).fetchone()
                if child_binding is None or child_binding["state"] != "cortex":
                    return None
                binding = child_binding
        project = db.execute("SELECT project_root FROM tasks WHERE id=?", (actor["task_id"],)).fetchone()
        if project is None or Path(cwd).resolve() != Path(project[0]):
            return None
        return dict(task_id=actor["task_id"], thread_id=actor["thread_id"],
                    parent_thread_id=actor["parent_thread_id"], session_id=session_id,
                    agent_id=agent_id, project_root=project[0], receipt=binding["receipt"],
                    binding_origin="native_hook" if agent_id is not None and "state" not in dict(binding) else "native_mcp",
                    role="worker" if actor["parent_thread_id"] else "coordinator")

    def register_agent(self, session_id, cwd, agent_id):
        """Record the documented native lifecycle receipt, never infer a profile."""
        with self.store.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            parent = self._context(db, session_id, cwd)
            if parent is None or agent_id == session_id:
                return None
            prior = db.execute("SELECT * FROM hook_agent_bindings WHERE agent_id=?", (agent_id,)).fetchone()
            native = db.execute("SELECT * FROM thread_bindings WHERE thread_id=?", (agent_id,)).fetchone()
            for binding in (prior, native):
                if binding is not None and (binding["parent_thread_id"] != session_id or binding["task_id"] != parent["task_id"]):
                    raise StoreError("hook_binding_conflict")
            if prior is None:
                receipt = "hb_" + fingerprint([session_id, agent_id, parent["receipt"]])[:24]
                db.execute("INSERT INTO hook_agent_bindings VALUES (?,?,?,?,?)", (agent_id, session_id, parent["task_id"], receipt, self.store._now()))
            context = self._context(db, session_id, cwd, agent_id)
            db.commit()
            return context

    def context(self, session_id, cwd, agent_id=None):
        with self.store.connection() as db:
            return self._context(db, session_id, cwd, agent_id)

    def _current(self, db, context):
        current = self._context(db, context["session_id"], context["project_root"], context["agent_id"])
        if current is None or current["receipt"] != context["receipt"]:
            raise StoreError("hook_binding_changed")
        return current

    def snapshot(self, context):
        with self.store.connection() as db:
            self._current(db, context)
            task, thread = context["task_id"], context["thread_id"]
            pipeline = db.execute("""SELECT r.id,e.digest,e.sequence FROM reports r JOIN editions e ON e.report_id=r.id
                WHERE r.task_id=? AND r.kind='pipeline' ORDER BY e.sequence DESC LIMIT 1""", (task,)).fetchone()
            sources = db.execute("SELECT revision,report_id FROM source_revisions WHERE task_id=? ORDER BY revision DESC LIMIT 8", (task,)).fetchall()
            drafts = db.execute("SELECT id,kind FROM drafts WHERE task_id=? AND owner_thread_id=? AND published_report_id IS NULL ORDER BY created_at DESC LIMIT 9", (task, thread)).fetchall()
            published = db.execute("SELECT count(*) FROM drafts WHERE task_id=? AND owner_thread_id=? AND published_report_id IS NOT NULL", (task, thread)).fetchone()[0]
            changes = db.execute("SELECT COALESCE(MAX(sequence),0) FROM task_changes WHERE task_id=? AND kind!='hook'", (task,)).fetchone()[0]
            result = dict(source_revision=sources[0]["revision"] if sources else 0,
                          pipeline=dict(pipeline) if pipeline else None,
                          source_refs=[dict(row) for row in sources],
                          own_drafts=[dict(row) for row in drafts], published_count=published,
                          change_sequence=changes,
                          pending_source_turns=db.execute("SELECT COUNT(*) FROM hook_pending_sources WHERE task_id=?", (task,)).fetchone()[0])
            result["state_key"] = fingerprint(result)
            return result

    def claim_hint(self, context, event_name, state_key):
        with self.store.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            self._current(db, context)
            old = db.execute("SELECT state_key FROM hook_hints WHERE thread_id=? AND event_name=?", (context["thread_id"], event_name)).fetchone()
            if old is not None and old[0] == state_key:
                db.commit()
                return False
            db.execute("INSERT OR REPLACE INTO hook_hints VALUES (?,?,?,?)", (context["task_id"], context["thread_id"], event_name, state_key))
            db.commit()
            return True

    def record(self, context, event_name, event_key, metadata):
        with self.store.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            self._current(db, context)
            # A unified-exec call may expose a running receipt and a later exit
            # under one tool-use id. Preserve distinct observed result states;
            # only an exact metadata replay is deduplicated.
            key = fingerprint([context["receipt"], event_name, event_key, metadata])
            exists = db.execute("SELECT 1 FROM hook_events WHERE event_key=?", (key,)).fetchone()
            if exists:
                db.commit()
                return False
            db.execute("INSERT INTO hook_events VALUES (?,?,?,?,?,?)", (context["task_id"], context["thread_id"], key, event_name,
                       json.dumps(metadata, sort_keys=True), self.store._now()))
            self.store._change(db, context["task_id"], "hook", key)
            if metadata.get("changed_paths"):
                self.store._change(db, context["task_id"], "artifact", key)
            db.commit()
            return True

    def note_prompt(self, context, turn_id):
        """Record an unresolved turn signal, not a fabricated message identity.

        UserPromptSubmit documents no per-message id and may run before the
        native message is written. The MCP source reader archives native items
        later, with optional literal redaction before immutable publication.
        """
        if context["role"] != "coordinator":
            return False
        with self.store.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            self._current(db, context)
            prior = db.execute("SELECT 1 FROM hook_pending_sources WHERE thread_id=? AND turn_id=?",
                               (context["thread_id"], turn_id)).fetchone()
            if prior is None:
                db.execute("INSERT INTO hook_pending_sources VALUES (?,?,?,?)",
                           (context["task_id"], context["thread_id"], turn_id, self.store._now()))
                self.store._change(db, context["task_id"], "source_pending", None)
            db.commit()
            return True

    def protected_paths(self, context, paths):
        """Exact records only; a file mentioned inside patch content is never queried."""
        records = []
        with self.store.connection() as db:
            self._current(db, context)
            for path in paths:
                draft = db.execute("""SELECT d.path,d.owner_thread_id,d.published_report_id FROM drafts d
                    JOIN tasks t ON t.id=d.task_id WHERE t.project_root=? AND d.path=? AND d.published_report_id IS NULL""",
                                   (context["project_root"], path)).fetchone()
                if draft:
                    records.append(dict(path=path, kind="draft", published=bool(draft["published_report_id"]), owner_thread_id=draft["owner_thread_id"]))
                base = Path(context["project_root"]) / ".codex" / "cortex"
                if Path(path).parent.parent == base:
                    report = db.execute("""SELECT r.id FROM reports r JOIN tasks t ON t.id=r.task_id
                        WHERE t.project_root=? AND r.task_id=? AND r.filename=?""",
                                        (context["project_root"], Path(path).parent.name, Path(path).name)).fetchone()
                    if report:
                        records.append(dict(path=path, kind="report", published=True, owner_thread_id=None))
        return records
