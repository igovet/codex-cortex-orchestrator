"""Host-private, derived Markdown views for the V12 SQLite ledger.

The module deliberately accepts a store instance rather than any caller paths.
Every target is derived from a canonical task's compact ``task_ref`` under the
store's own shard; the target project's ``project_root`` is never used as an
output location.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from cortex_runtime.v12_contract import PROJECTION_RENDERER_VERSION, task_ref
from cortex_runtime.report_presenters import render_report


_MAX_RENDER_BYTES = 10 * 1024 * 1024
_MAX_RENDER_FILES = 512
_MAX_RENDER_TOTAL_BYTES = 32 * 1024 * 1024


def _digest_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _markdown_link(relative: str, path: str) -> str:
    """Format an exact copyable link only after the path has been verified."""
    label = "current plan" if relative == "plans/current.md" else "plan revision" if relative.startswith("plans/revisions/") else "report"
    return f"[Open {label}]({path})"


def _compact_view_link(store: Any, relative: str, body: bytes, digest: str) -> str:
    """Materialize a short content-addressed alias for reliable user links."""
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        raise OSError("projection digest is invalid")
    kind = "plan" if relative.startswith("plans/") else "report"
    target = store._codex_home / "cortex" / "views" / f"{kind}-{digest.removeprefix('sha256:')}.md"
    observed = _safe_write(target, body, expected_digest=digest, root=store._codex_home)
    if observed != digest:
        raise OSError("compact projection readback failed")
    return _markdown_link(relative, str(target))


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
    # Keep the store's lexical root. macOS intentionally exposes temporary
    # storage through ``/var -> /private/var``; resolving only ``root`` while
    # leaving ``path`` lexical makes an otherwise contained child appear to
    # escape. Descendants are still checked component-by-component with
    # ``lstat``, so a symlink inside the managed projection tree remains
    # fail-closed without rejecting the host's system-level alias.
    root = Path(root)
    path = Path(path)
    if not root.is_absolute() or not path.is_absolute():
        raise OSError("projection path must be absolute")
    try:
        relative = path.relative_to(root)
    except ValueError as exc:
        raise OSError("projection path escapes the shard") from exc
    if any(part in {".", ".."} for part in relative.parts):
        raise OSError("projection path escapes the shard")
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


def _task_directory(store: Any, task_ref_value: str) -> Path:
    """Use only the current compact task locator, without migrating old paths."""
    directory = store.root / "tasks" / task_ref_value
    _directory(directory, root=store.root)
    return directory


def _view_metadata(store: Any, task_id: str, relative: str, *, require_fresh: bool = True) -> dict[str, Any]:
    try:
        task_ref_value = _projection_task_ref(store, task_id)
        fragment = _task_relative(task_ref_value, relative).relative_to(Path("tasks") / task_ref_value)
        path = _task_directory(store, task_ref_value) / fragment
    except FileExistsError:
        return {"status": "conflict", "path": None}
    except OSError:
        return {"status": "unavailable", "path": None}
    def read(connection: Any) -> dict[str, Any]:
        row = connection.execute("SELECT source_sequence,renderer_version,content_digest,status FROM projection_files WHERE task_id=? AND relative_path=?", (task_id, relative)).fetchone()
        latest = connection.execute("SELECT COALESCE(MAX(sequence), 0) FROM timeline WHERE task_id=?", (task_id,)).fetchone()[0]
        if row is None:
            return {"status": "stale", "path": None}
        source_sequence, renderer_version, digest, status = int(row[0]), str(row[1]), str(row[2]), str(row[3])
        if renderer_version != PROJECTION_RENDERER_VERSION or status != "ready" or (require_fresh and source_sequence < int(latest)):
            return {"status": "stale", "path": None}
        try:
            _regular(path, required=True)
            with path.open("rb") as stream:
                body = stream.read(_MAX_RENDER_BYTES + 1)
                actual = _digest_bytes(body)
            if actual != digest:
                return {"status": "conflict", "path": None}
            markdown_link = _compact_view_link(store, relative, body, digest)
        except FileExistsError:
            return {"status": "conflict", "path": None}
        except OSError:
            return {"status": "unavailable", "path": None}
        verified_path = str(path)
        return {
            "status": "ready",
            "path": verified_path,
            "markdown_link": markdown_link,
            "source_sequence": source_sequence,
            "content_digest": digest,
        }
    try:
        return store._read(read)
    except Exception:
        return {"status": "unavailable", "path": None}


def human_view(store: Any, task_id: str, relative: str, *, require_fresh: bool = True) -> dict[str, Any]:
    """Return only user-facing report/plan links; other ledger data is SQLite-only."""
    candidate = Path(relative)
    allowed = (
        candidate == Path("plans/current.md")
        or (candidate.parent == Path("reports") and candidate.suffix == ".md")
        or (candidate.parent == Path("plans/revisions") and candidate.suffix == ".md")
    )
    if not allowed:
        return {"status": "disabled", "path": None}
    return _view_metadata(store, task_id, relative, require_fresh=require_fresh)


def _task_data(store: Any, task_id: str) -> tuple[dict[str, Any], list[dict[str, Any]], int]:
    """Read only canonical inputs used by human-report rendering."""
    def read(connection: Any) -> tuple[dict[str, Any], list[dict[str, Any]], int]:
        task = store._task(connection, task_id)
        reports = [store._report(connection, row[0], task_id=task_id) for row in connection.execute(
            "SELECT report_id FROM reports WHERE task_id=? ORDER BY created_sequence", (task_id,)).fetchall()]
        sequence = int(connection.execute(
            "SELECT COALESCE(MAX(sequence), ?) FROM timeline WHERE task_id=?",
            (task["created_sequence"], task_id)).fetchone()[0])
        return task, reports, sequence
    return store._read(read)


def _render_report(store: Any, report: Mapping[str, Any]) -> bytes:
    def read(connection: Any) -> list[dict[str, Any]]:
        return store._report_chunks(connection, str(report["report_id"]))
    chunks = store._read(read)
    if len(chunks) != 1 or chunks[0].get("section") != "body":
        raise ValueError("current terminal report requires one immutable body")
    payload = chunks[0]["content"]
    body = render_report(
        report_type=report.get("report_type"),
        content=payload,
        report=report,
    )
    return body.encode("utf-8")


def _render_files(store: Any, task_id: str) -> tuple[dict[str, bytes], int, str]:
    task, reports, sequence = _task_data(store, task_id)
    files: dict[str, bytes] = {}
    # The SQLite ledger is canonical.  Materialize only documents that a
    # coordinator can actually publish to the user: immutable reports and
    # plan views.  All task, delegation, decision, governance, handoff, index,
    # and timeline evidence remains queryable through bounded MCP reads only.
    latest_plan: Mapping[str, Any] | None = None
    for report in reports:
        if report["assembly_state"] != "finalized":
            continue
        report_path = f"reports/{report['report_id']}.md"
        files[report_path] = _render_report(store, report)
        if report["report_type"] == "plan":
            files[f"plans/revisions/{report['report_id']}.md"] = files[report_path]
            if report["assembly_state"] == "finalized":
                latest_plan = report
    if latest_plan is not None:
        files["plans/current.md"] = files[f"plans/revisions/{latest_plan['report_id']}.md"]
    return files, sequence, str(task["task_ref"])


def materialize_task(store: Any, task_id: str) -> dict[str, Any]:
    """Best-effort materialize one task; canonical rows are never rolled back."""
    try:
        files, source_sequence, task_ref_value = _render_files(store, task_id)
        # Preflight the complete batch before resolving a target or writing a
        # temporary file.  Per-file validation in _safe_write remains a
        # defense in depth, but an admitted batch must never partially
        # materialize merely because its aggregate output is unsafe.
        if len(files) > _MAX_RENDER_FILES:
            raise OSError("projection file count exceeds the aggregate limit")
        total_bytes = sum(len(body) for body in files.values())
        if total_bytes > _MAX_RENDER_TOTAL_BYTES or any(len(body) > _MAX_RENDER_BYTES for body in files.values()):
            raise OSError("projection output exceeds the aggregate limit")
        task_directory = _task_directory(store, task_ref_value)
        ordered = sorted(files)
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
