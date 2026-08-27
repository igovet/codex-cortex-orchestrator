"""Host-private, derived Markdown views for the V12 SQLite ledger.

The module deliberately accepts a store instance rather than any caller paths.
Every target is derived from a canonical task's compact ``task_ref`` under the
store's own shard; the target project's ``project_root`` is never used as an
output location.
"""
from __future__ import annotations

import ctypes
import errno
import hashlib
import html
import json
import os
import re
import stat
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from cortex_runtime.v12_contract import PROJECTION_RENDERER_VERSION, task_ref


_MAX_RENDER_BYTES = 10 * 1024 * 1024


def _digest_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


_MARKDOWN_PUNCTUATION = re.compile(r"([\\`*_{}\[\]()#+.!|>~-])")


def _markdown_text(value: object) -> str:
    """Escape untrusted text so it remains readable, inert Markdown prose."""
    escaped = html.escape(str(value), quote=False)
    escaped = _MARKDOWN_PUNCTUATION.sub(r"\\\1", escaped)
    # A user-controlled newline must not be able to turn the remainder into a
    # new list item or heading.  Markdown's hard-break syntax keeps it prose.
    return escaped.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "  \n")


def _markdown_value(value: object, indent: str = "") -> list[str]:
    """Render structured ledger values as ordinary, readable Markdown lists.

    This intentionally does not serialize values as JSON.  Keys and all
    caller-authored strings are escaped before being placed in Markdown, so
    the projection remains a display-only view even when content contains
    Markdown or HTML-looking text.
    """
    if isinstance(value, Mapping):
        lines: list[str] = []
        for key, item in value.items():
            label = _markdown_text(key)
            if isinstance(item, (Mapping, Sequence)) and not isinstance(item, (str, bytes, bytearray)):
                lines.append(f"{indent}- **{label}**")
                lines.extend(_markdown_value(item, indent + "  "))
            else:
                lines.append(f"{indent}- **{label}:** {_markdown_text(item)}")
        return lines or [f"{indent}- *(empty)*"]
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        lines = []
        for item in value:
            if isinstance(item, (Mapping, Sequence)) and not isinstance(item, (str, bytes, bytearray)):
                lines.append(f"{indent}-")
                lines.extend(_markdown_value(item, indent + "  "))
            else:
                lines.append(f"{indent}- {_markdown_text(item)}")
        return lines or [f"{indent}- *(empty)*"]
    return [f"{indent}{_markdown_text(value)}"]


def _inert(value: object) -> str:
    """Render arbitrary ledger content as escaped, inert Markdown.

    The historical helper name is retained for callers, but its output is no
    longer an embedded JSON document or an HTML ``<pre>`` block.
    """
    return "\n".join(_markdown_value(value)) + "\n\n"


def _text(value: object) -> str:
    return _markdown_text(value or "")


def _regular(path: Path, *, required: bool = False) -> bool:
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        if required:
            raise OSError("projection target is missing")
        return False
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise OSError("projection target is unsafe")
    return True


def _directory(path: Path, *, root: Path) -> None:
    """Create/check every projection directory with no symlink traversal."""
    root = root.resolve(strict=True)
    try:
        relative = path.relative_to(root)
    except ValueError as exc:
        raise OSError("projection path escapes the shard") from exc
    info = os.lstat(root)
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise OSError("projection root is unsafe")
    current = root
    for part in relative.parts:
        current /= part
        try:
            current.mkdir(mode=0o700)
        except FileExistsError:
            pass
        info = os.lstat(current)
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise OSError("projection directory is unsafe")
        os.chmod(current, 0o700)


def _fsync_parent(path: Path) -> None:
    descriptor = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _safe_write(target: Path, body: bytes, *, expected_digest: str | None, root: Path) -> str:
    """Atomically replace an owned projection unless an external edit exists."""
    _directory(target.parent, root=root)
    exists = _regular(target)
    if exists:
        with target.open("rb") as stream:
            actual = _digest_bytes(stream.read(_MAX_RENDER_BYTES + 1))
        if expected_digest is None or actual != expected_digest:
            raise FileExistsError("projection conflict")
    descriptor = -1
    temporary: str | None = None
    try:
        descriptor, temporary = tempfile.mkstemp(prefix=".cortex-projection-", dir=target.parent)
        os.fchmod(descriptor, 0o600)
        written = 0
        view = memoryview(body)
        while written < len(body):
            written += os.write(descriptor, view[written:])
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        _regular(Path(temporary), required=True)
        os.replace(temporary, target)
        temporary = None
        _regular(target, required=True)
        os.chmod(target, 0o600)
        _fsync_parent(target)
        with target.open("rb") as stream:
            verified = stream.read(_MAX_RENDER_BYTES + 1)
        if len(verified) > _MAX_RENDER_BYTES or verified != body:
            raise OSError("projection readback failed")
        return _digest_bytes(verified)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary is not None:
            try:
                os.unlink(temporary)
            except OSError:
                pass


def _task_relative(task_ref_value: str, relative: str) -> Path:
    # All fragments below are compiler-owned constant labels or validated
    # generated IDs; retain a defensive path traversal check nonetheless.
    value = Path(relative)
    if value.is_absolute() or ".." in value.parts:
        raise ValueError("unsafe projection relative path")
    return Path("tasks") / task_ref_value / value


def _projection_task_ref(store: Any, task_id: str) -> str:
    """Resolve the one compact directory name from canonical SQLite evidence."""
    expected = task_ref(task_id)
    if expected is None:
        raise OSError("projection task identifier is invalid")

    def read(connection: Any) -> str:
        observed = store._task(connection, task_id).get("task_ref")
        if observed != expected:
            raise OSError("projection task reference is invalid")
        return expected

    return store._read(read)


def _rename_directory_noreplace(source: Path, destination: Path) -> None:
    """Atomically move a legacy directory without ever replacing a destination."""
    if os.name != "posix":
        raise OSError("atomic no-replace directory migration is unavailable")
    try:
        renameat2 = ctypes.CDLL(None, use_errno=True).renameat2
    except AttributeError as exc:
        raise OSError("atomic no-replace directory migration is unavailable") from exc
    renameat2.argtypes = (ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint)
    renameat2.restype = ctypes.c_int
    if renameat2(-100, os.fsencode(source), -100, os.fsencode(destination), 1) != 0:
        error = ctypes.get_errno()
        if error == errno.EEXIST:
            raise FileExistsError("compact projection directory already exists")
        raise OSError(error, "atomic no-replace directory migration failed", str(source), str(destination))


def _migrate_legacy_task_directory(store: Any, task_id: str, task_ref_value: str) -> Path:
    """Move only the exact legacy task directory to its compact V12 locator.

    The projection tables deliberately retain canonical task IDs plus page-relative
    paths, so a successful same-shard rename preserves all digest/sequence metadata.
    A pre-existing compact directory is a conflict, never a merge or cleanup target.
    """
    tasks_root = store.root / "tasks"
    _directory(tasks_root, root=store.root)
    legacy = tasks_root / task_id
    compact = tasks_root / task_ref_value
    try:
        legacy_info = os.lstat(legacy)
    except FileNotFoundError:
        legacy_info = None
    if legacy_info is None:
        if compact.exists():
            _directory(compact, root=store.root)
        return compact
    if stat.S_ISLNK(legacy_info.st_mode) or not stat.S_ISDIR(legacy_info.st_mode):
        raise OSError("legacy projection directory is unsafe")
    try:
        compact_info = os.lstat(compact)
    except FileNotFoundError:
        compact_info = None
    if compact_info is not None:
        raise FileExistsError("compact projection directory already exists")
    _rename_directory_noreplace(legacy, compact)
    _directory(compact, root=store.root)
    return compact


def _view_metadata(store: Any, task_id: str, relative: str) -> dict[str, Any]:
    try:
        task_ref_value = _projection_task_ref(store, task_id)
        fragment = _task_relative(task_ref_value, relative).relative_to(Path("tasks") / task_ref_value)
        path = _migrate_legacy_task_directory(store, task_id, task_ref_value) / fragment
    except FileExistsError:
        return {"status": "conflict", "path": None}
    except OSError:
        return {"status": "unavailable", "path": None}
    def read(connection: Any) -> dict[str, Any]:
        row = connection.execute("SELECT source_sequence,content_digest,status FROM projection_files WHERE task_id=? AND relative_path=?", (task_id, relative)).fetchone()
        latest = connection.execute("SELECT COALESCE(MAX(sequence), 0) FROM timeline WHERE task_id=?", (task_id,)).fetchone()[0]
        if row is None:
            return {"status": "stale", "path": None}
        source_sequence, digest, status = int(row[0]), str(row[1]), str(row[2])
        if status != "ready" or source_sequence < int(latest):
            return {"status": "stale", "path": None}
        try:
            _regular(path, required=True)
            with path.open("rb") as stream:
                actual = _digest_bytes(stream.read(_MAX_RENDER_BYTES + 1))
            if actual != digest:
                return {"status": "conflict", "path": None}
        except FileExistsError:
            return {"status": "conflict", "path": None}
        except OSError:
            return {"status": "unavailable", "path": None}
        return {"status": "ready", "path": str(path), "source_sequence": source_sequence, "content_digest": digest}
    try:
        return store._read(read)
    except Exception:
        return {"status": "unavailable", "path": None}


def human_view(store: Any, task_id: str, relative: str) -> dict[str, Any]:
    """Return a verified current absolute path only; never expose stale paths."""
    return _view_metadata(store, task_id, relative)


def _task_data(store: Any, task_id: str) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], int]:
    def read(connection: Any) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], int]:
        task = store._task(connection, task_id)
        delegations = [store._delegation(connection, row[0], task_id=task_id) for row in connection.execute("SELECT delegation_id FROM delegations WHERE task_id=? ORDER BY created_sequence", (task_id,)).fetchall()]
        reports = [store._report(connection, row[0], task_id=task_id) for row in connection.execute("SELECT report_id FROM reports WHERE task_id=? ORDER BY created_sequence", (task_id,)).fetchall()]
        decisions = [store._decision(connection, row[0], task_id=task_id) for row in connection.execute("SELECT decision_id FROM user_decisions WHERE task_id=? ORDER BY created_sequence", (task_id,)).fetchall()]
        timeline = []
        for row in connection.execute("SELECT sequence,occurred_at,event_type,entity_type,entity_id,payload_json FROM timeline WHERE task_id=? ORDER BY sequence", (task_id,)).fetchall():
            timeline.append({"sequence": int(row[0]), "occurred_at": str(row[1]), "event_type": str(row[2]), "entity_type": str(row[3]), "entity_id": str(row[4]), "payload": json.loads(str(row[5]))})
        initiatives = [store._initiative(connection, initiative_id) for initiative_id in store._task_initiative_ids(connection, task_id)]
        initiative_ids = [str(item["initiative_id"]) for item in initiatives]
        closures = []
        closure_rows = connection.execute(
            "SELECT closure_id FROM governance_closures WHERE project_hash=? AND "
            "((subject_type='task' AND subject_id=?) OR "
            "(subject_type='initiative' AND subject_id IN (" + ",".join("?" for _ in initiative_ids) + "))) "
            "ORDER BY created_sequence,closure_id",
            [store.project_hash, task_id, *initiative_ids],
        ).fetchall() if initiative_ids else connection.execute(
            "SELECT closure_id FROM governance_closures WHERE project_hash=? AND subject_type='task' AND subject_id=? ORDER BY created_sequence,closure_id",
            (store.project_hash, task_id),
        ).fetchall()
        closures = [store._closure(connection, str(row[0])) for row in closure_rows]
        receipts = []
        for row in connection.execute("SELECT receipt_id,consumer_delegation_id,reader_kind,report_id,observed_content_digest,sections_json,input_cursor,output_cursor,chunk_indexes_json,returned_content_bytes,has_more,created_at,created_sequence FROM report_consumption_receipts WHERE task_id=? ORDER BY created_sequence,receipt_id", (task_id,)).fetchall():
            receipts.append({"receipt_id": int(row[0]), "consumer_delegation_id": row[1], "reader_kind": str(row[2]), "report_id": str(row[3]), "observed_content_digest": str(row[4]), "sections": json.loads(str(row[5])), "input_cursor": row[6], "output_cursor": row[7], "chunk_indexes": json.loads(str(row[8])), "returned_content_bytes": int(row[9]), "has_more": bool(row[10]), "created_at": str(row[11]), "created_sequence": int(row[12])})
        sequence = int(timeline[-1]["sequence"]) if timeline else int(task["created_sequence"])
        return task, delegations, reports, decisions, timeline, initiatives, closures, receipts, sequence
    return store._read(read)


def _render_report(store: Any, report: Mapping[str, Any]) -> bytes:
    def read(connection: Any) -> list[dict[str, Any]]:
        return store._report_chunks(connection, str(report["report_id"]))
    chunks = store._read(read)
    state = str(report["assembly_state"]).upper()
    title = "ABORTED — NOT FINAL EVIDENCE" if report["assembly_state"] == "aborted" else state
    lines = [f"# Report: {_text(report['report_id'])}", "", f"**State:** {title}", "", "## Metadata", "", _inert(store._compact_report(report)), "## Content", ""]
    for chunk in chunks:
        lines.extend((f"### {_text(chunk['section'])} (chunk {chunk['chunk_index']})", "", _inert(chunk["content"])))
    return ("\n".join(lines)).encode("utf-8")


def _render_files(store: Any, task_id: str) -> tuple[dict[str, bytes], int, str]:
    task, delegations, reports, decisions, timeline, initiatives, closures, receipts, sequence = _task_data(store, task_id)
    files: dict[str, bytes] = {}
    governance_gate = store._read(lambda connection: store._governance_gate(connection, task_id))
    if isinstance(governance_gate, dict):
        governance_gate = dict(governance_gate) | {
            "documentation_impact_required_before_closure": governance_gate.get("mode") in {"light", "full"},
        }
    files["governance-gate.md"] = ("# Governance gate\n\n" + _inert(governance_gate)).encode("utf-8")
    files["task.md"] = ("# Task\n\n## English objective\n\n" + _text(task["objective"]) + "\n\n## Closure state\n\n" + _inert({key: task[key] for key in ("closure_state", "task_closure_ref", "task_closure_verdict", "task_closure_sequence")}) + "## User-authored original source\n\n" + _inert(task["user_request_original"]) + "## Task contract\n\n" + _inert({key: task[key] for key in ("task_contract_version", "user_language", "requirements", "constraints", "acceptance_criteria", "verification_plan")}) + "## Context\n\n" + _inert(task["context"])).encode("utf-8")
    for delegation in delegations:
        files[f"delegations/{delegation['delegation_id']}.md"] = ("# Delegation\n\n" + _inert({key: delegation[key] for key in ("delegation_id", "native_task_name", "objective", "role", "scope", "instructions", "input_report_ids", "input_decision_ids", "model", "reasoning_effort")})).encode("utf-8")
    files["handoffs/report-consumption-receipts.md"] = ("# Report consumption receipts\n\n" + _inert(receipts)).encode("utf-8")
    for initiative in initiatives:
        files[f"initiatives/{initiative['initiative_id']}.md"] = ("# Initiative\n\n" + _inert(initiative)).encode("utf-8")
    for closure in closures:
        files[f"closures/{closure['closure_id']}.md"] = ("# Governance closure\n\n" + _inert(closure)).encode("utf-8")
    latest_plan: Mapping[str, Any] | None = None
    for report in reports:
        report_path = f"reports/{report['report_id']}.md"
        files[report_path] = _render_report(store, report)
        if report["report_type"] == "plan":
            files[f"plans/revisions/{report['report_id']}.md"] = files[report_path]
            if report["assembly_state"] == "finalized":
                latest_plan = report
    if latest_plan is not None:
        files["plans/current.md"] = files[f"plans/revisions/{latest_plan['report_id']}.md"]
    for decision in decisions:
        files[f"decisions/{decision['decision_id']}.md"] = ("# User decision\n\n## Binding\n\n" + _inert({key: decision[key] for key in ("decision_id", "subject_type", "subject_id", "subject_digest", "decision_type", "attribution", "user_language", "supersedes_decision_id")}) + "## Coordinator prompt (English)\n\n" + _inert(decision["prompt_en"]) + "## User-authored original source\n\n" + _inert(decision["response_original"]) + "## English normalization\n\n" + _inert(decision["response_en"])).encode("utf-8")
    # Timeline pages are deterministic bounded sequence ranges, not ordinal
    # filenames.  A range remains meaningful when SQLite sequence values have
    # gaps, and lets a reader select an evidence window without depending on
    # a previous materialization's page count.  Deliberately do not delete or
    # read historic ``timeline/0001.md``-style residue from earlier preview
    # builds: derived files may have been locally altered and are not ledger
    # authority.
    page_index: list[dict[str, Any]] = []
    for offset in range(0, len(timeline), 100):
        page = timeline[offset:offset + 100]
        first_sequence, last_sequence = int(page[0]["sequence"]), int(page[-1]["sequence"])
        relative = f"timeline/pages/{first_sequence}-{last_sequence}.md"
        page_index.append({
            "first_sequence": first_sequence,
            "last_sequence": last_sequence,
            "events": len(page),
            "path": f"pages/{first_sequence}-{last_sequence}.md",
        })
        files[relative] = (
            f"# Timeline events {first_sequence}–{last_sequence}\n\n" + _inert(page)
        ).encode("utf-8")
    files["timeline/index.md"] = (
        "# Timeline\n\n" + _inert({
            "page_size": 100,
            "pages": page_index,
            "latest_sequence": sequence,
        })
    ).encode("utf-8")
    index = {"task_id": task_id, "task_ref": task["task_ref"], "latest_sequence": sequence, "closure_state": task["closure_state"], "task_closure_ref": task["task_closure_ref"], "task_closure_verdict": task["task_closure_verdict"], "task": "task.md", "governance_gate": "governance-gate.md", "report_receipts": "handoffs/report-consumption-receipts.md", "current_plan": None if latest_plan is None else "plans/current.md", "delegations": [f"delegations/{item['delegation_id']}.md" for item in delegations], "reports": [f"reports/{item['report_id']}.md" for item in reports], "decisions": [f"decisions/{item['decision_id']}.md" for item in decisions], "timeline": "timeline/index.md", "initiatives": [f"initiatives/{item['initiative_id']}.md" for item in initiatives], "closures": [f"closures/{item['closure_id']}.md" for item in closures]}
    # Index is deliberately written last by materialize_task.
    files["index.md"] = ("# Cortex task human view\n\n" + _inert(index)).encode("utf-8")
    return files, sequence, str(task["task_ref"])


def materialize_task(store: Any, task_id: str) -> dict[str, Any]:
    """Best-effort materialize one task; canonical rows are never rolled back."""
    try:
        files, source_sequence, task_ref_value = _render_files(store, task_id)
        task_directory = _migrate_legacy_task_directory(store, task_id, task_ref_value)
        ordered = [item for item in sorted(files) if item != "index.md"] + ["index.md"]
        outcomes: dict[str, str] = {}
        for relative in ordered:
            target = task_directory / Path(relative)
            def prior(connection: Any, item: str = relative) -> str | None:
                row = connection.execute("SELECT content_digest FROM projection_files WHERE task_id=? AND relative_path=?", (task_id, item)).fetchone()
                return None if row is None else str(row[0])
            expected = store._read(prior)
            try:
                digest = _safe_write(target, files[relative], expected_digest=expected, root=store.root)
            except FileExistsError:
                outcomes[relative] = "conflict"
                continue
            def record(connection: Any, item: str = relative, value: str = digest) -> None:
                connection.execute("INSERT INTO projection_files(task_id,relative_path,source_sequence,renderer_version,content_digest,status,updated_at) VALUES (?, ?, ?, ?, ?, 'ready', ?) ON CONFLICT(task_id,relative_path) DO UPDATE SET source_sequence=excluded.source_sequence,renderer_version=excluded.renderer_version,content_digest=excluded.content_digest,status='ready',updated_at=excluded.updated_at", (task_id, item, source_sequence, PROJECTION_RENDERER_VERSION, value, __import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat()))
            store._write(record)
            outcomes[relative] = "ready"
        expected_pages = {relative for relative in files if relative.startswith("timeline/pages/")}
        if outcomes.get("timeline/index.md") == "ready" and all(outcomes.get(item) == "ready" for item in expected_pages):
            # Do not delete a historical host-private file implicitly: it may
            # have been externally altered.  Its old metadata must nevertheless
            # stop looking like a current timeline page, because the freshly
            # rendered index is the sole canonical page map.
            def retire_stale_pages(connection: Any) -> None:
                if expected_pages:
                    placeholders = ",".join("?" for _ in expected_pages)
                    connection.execute(
                        f"UPDATE projection_files SET status='stale' WHERE task_id=? AND relative_path LIKE 'timeline/%' AND relative_path <> 'timeline/index.md' AND relative_path NOT IN ({placeholders})",
                        [task_id, *sorted(expected_pages)],
                    )
                else:
                    connection.execute(
                        "UPDATE projection_files SET status='stale' WHERE task_id=? AND relative_path LIKE 'timeline/%' AND relative_path <> 'timeline/index.md'",
                        (task_id,),
                    )
            store._write(retire_stale_pages)
        return {"status": "ready" if all(value == "ready" for value in outcomes.values()) else "conflict", "files": outcomes}
    except FileExistsError:
        return {"status": "conflict", "files": {}}
    except Exception:
        return {"status": "unavailable", "files": {}}


def reconcile(store: Any, *, task_id: str | None = None, limit: int = 2) -> None:
    """Bounded opportunistic repair; failures remain advisory and sanitized."""
    if task_id is not None:
        materialize_task(store, task_id)
        return
    def read(connection: Any) -> list[str]:
        return [str(row[0]) for row in connection.execute("SELECT task_id FROM tasks ORDER BY updated_sequence DESC LIMIT ?", (max(1, min(limit, 8)),)).fetchall()]
    try:
        for item in store._read(read):
            materialize_task(store, item)
    except Exception:
        return
