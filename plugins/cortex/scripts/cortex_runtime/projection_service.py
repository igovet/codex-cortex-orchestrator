"""SQLite-backed generic filesystem projection lifecycle.

Canonical bytes and export authorization live in the normalized SQLite
artifact catalog. This module only schedules and executes rebuildable
filesystem projections; it never treats an export as canonical state.
"""
from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from cortex_runtime.ledger_db import (
    ack_projection_job,
    claim_projection_job,
    claim_projection_jobs,
    enqueue_projection_job,
    fail_projection_job,
    get_artifact_for_export_path,
    get_artifact_metadata,
    get_projection_job,
    list_projection_jobs,
    read_artifact_content,
    register_artifact_export,
    retry_projection_job,
)
from cortex_runtime.projections import (
    ProjectionVerification,
    materialize_projection,
    remove_optional_projection,
    verify_projection,
)


def _task_dir(root: Path, task_id: str) -> Path:
    from cortex_runtime.ledger_db import artifact_path

    path = artifact_path(root, task_id)
    if path is None:
        raise ValueError("task artifact directory is unavailable")
    return Path(path)


def _authorized_artifact(root: Path, job: dict[str, Any]) -> dict[str, Any]:
    """Resolve a job through logical-artifact and export authorization."""
    task_id = str(job.get("task_id") or "")
    artifact_id = str(job.get("artifact_id") or "")
    export_path = str(job.get("export_path") or "")
    if not task_id or not artifact_id or not export_path:
        raise ValueError("projection job is missing task, artifact, or export path")
    metadata = get_artifact_metadata(root, task_id, artifact_id)
    exported = get_artifact_for_export_path(root, task_id, export_path)
    if metadata is None or exported is None or exported.get("artifact_ref") != metadata.get("artifact_ref"):
        raise ValueError("projection export is not authorized for its logical artifact")
    if str(metadata["digest_sha256"]) != str(job.get("expected_digest") or ""):
        raise ValueError("projection canonical artifact digest does not match job")
    return metadata


def enqueue(*, root: Path, task_id: str | None, artifact_id: str, projection_type: str,
            export_path: str, required: bool = False, projection_key: str | None = None) -> dict[str, Any]:
    """Authorize an export and add a filesystem-free projection request.

    Different paths for the same logical artifact receive independent outbox
    jobs and can be materialized or repaired separately.
    """
    normalized_task_id = str(task_id or "")
    metadata = get_artifact_metadata(root, normalized_task_id, artifact_id)
    if metadata is None:
        raise ValueError("projection artifact is unavailable")
    register_artifact_export(root, normalized_task_id, str(metadata["artifact_ref"]), export_path)
    job = enqueue_projection_job(
        root, task_id=normalized_task_id, artifact_id=str(metadata["artifact_ref"]),
        projection_type=projection_type, export_path=export_path,
        expected_digest=str(metadata["digest_sha256"]), required=required,
        projection_key=projection_key,
    )
    expected = {
        "task_id": normalized_task_id,
        "artifact_id": str(metadata["artifact_ref"]),
        "projection_type": projection_type,
        "export_path": export_path,
        "required": int(bool(required)),
        "expected_digest": str(metadata["digest_sha256"]),
    }
    if any(str(job.get(key)) != str(value) for key, value in expected.items()):
        raise ValueError("projection key already has conflicting identity")
    return job


def verify_job(root: Path, job: dict[str, Any]) -> ProjectionVerification:
    """Verify a materialized export against canonical SQLite bytes."""
    metadata = _authorized_artifact(root, job)
    return verify_projection(
        _task_dir(root, str(metadata["task_id"])), str(job["export_path"]),
        str(metadata["digest_sha256"]),
    )


def materialize_job(root: Path, job: dict[str, Any], *, materializer: Callable[..., Any] = materialize_projection,
                    worker_id: str = "projection-service") -> dict[str, Any]:
    """Materialize only an owned, leased job and durably acknowledge it.

    A pending row is claimed before any filesystem operation. A ready row is
    returned unchanged because materialization is idempotent; callers that need a
    physical integrity check use :func:`verify_job` or :func:`reconcile`.
    """
    key = str(job.get("projection_key") or "")
    current = get_projection_job(root, key)
    if current is None:
        raise ValueError("projection job is unavailable")
    if current.get("status") == "ready":
        return current
    if current.get("status") in {"pending", "failed"}:
        current = claim_projection_job(root, key, worker_id)
        if current is None:
            raise ValueError("projection job could not be claimed")
    if current.get("status") != "materializing" or current.get("lease_owner") != worker_id:
        raise ValueError("projection job is not owned by this materializer")
    expiry = current.get("lease_expires_at")
    if expiry:
        try:
            if datetime.fromisoformat(str(expiry)).astimezone(timezone.utc) <= datetime.now(timezone.utc):
                raise ValueError("projection job lease has expired")
        except ValueError:
            raise ValueError("projection job lease is invalid or expired")
    metadata = _authorized_artifact(root, current)
    content = read_artifact_content(root, str(metadata["task_id"]), str(metadata["artifact_ref"]))
    data = content.encode("utf-8") if isinstance(content, str) else bytes(content)
    digest = hashlib.sha256(data).hexdigest()
    if digest != str(current["expected_digest"]):
        raise ValueError("projection canonical bytes digest does not match job")
    materializer(_task_dir(root, str(metadata["task_id"])), str(current["export_path"]), data, digest)
    return ack_projection_job(root, key, expected_digest=digest,
                              materialized_digest=digest, lease_owner=worker_id)


def materialize(root: Path, projection_key: str, *, worker_id: str = "projection-service",
                materializer: Callable[..., Any] = materialize_projection) -> dict[str, Any]:
    """Convenience entry point for a durable projection key."""
    job = get_projection_job(root, projection_key)
    if job is None:
        raise ValueError("projection job is unavailable")
    return materialize_job(root, job, worker_id=worker_id, materializer=materializer)


def list_pending(root: Path, *, task_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    """List durable pending projection jobs without touching the filesystem."""
    return list_projection_jobs(root, task_id=task_id, status="pending", limit=limit)


def list_failed(root: Path, *, task_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    """List durable failed projection jobs without touching the filesystem."""
    return list_projection_jobs(root, task_id=task_id, status="failed", limit=limit)


def retry(root: Path, projection_key: str) -> dict[str, Any]:
    """Move a failed or abandoned job back to the outbox pending state."""
    return retry_projection_job(root, projection_key)


def remove_optional(root: Path, job: dict[str, Any]) -> dict[str, Any]:
    """Remove only a safe optional export; its canonical row remains intact."""
    current = get_projection_job(root, str(job.get("projection_key") or ""))
    if current is None:
        raise ValueError("projection job is unavailable")
    if bool(current.get("required")):
        raise ValueError("required projection cannot be removed through optional cleanup")
    metadata = _authorized_artifact(root, current)
    removed = remove_optional_projection(
        _task_dir(root, str(metadata["task_id"])), str(current["export_path"]),
    )
    return {
        "projection_key": str(current["projection_key"]), "artifact_ref": str(metadata["artifact_ref"]),
        "export_path": str(current["export_path"]), "removed": removed.removed,
    }


def _repair_job(root: Path, current: dict[str, Any]) -> dict[str, Any]:
    """Create a distinct outbox attempt for a missing/altered ready export."""
    return enqueue(
        root=root, task_id=str(current["task_id"]), artifact_id=str(current["artifact_id"]),
        projection_type=str(current["projection_type"]), export_path=str(current["export_path"]),
        required=bool(current["required"]),
        # A ready acknowledgement remains immutable historical evidence. A new
        # job records a repair attempt instead of rewriting that outcome.
        projection_key=f"repair-{uuid.uuid4().hex}",
    )


def repair(root: Path, job: dict[str, Any], *, worker_id: str = "projection-repair",
           materializer: Callable[..., Any] = materialize_projection) -> dict[str, Any]:
    """Restore a missing export from canonical bytes through a new outbox job."""
    current = get_projection_job(root, str(job.get("projection_key") or ""))
    if current is None:
        raise ValueError("projection job is unavailable")
    if verify_job(root, current).valid:
        return current
    if current.get("status") == "pending":
        candidate = current
    elif current.get("status") == "failed":
        candidate = retry_projection_job(root, str(current["projection_key"]))
    elif current.get("status") == "materializing":
        raise ValueError("projection job is already owned by another materializer")
    else:
        candidate = _repair_job(root, current)
    try:
        return materialize_job(root, candidate, worker_id=worker_id, materializer=materializer)
    except Exception as exc:
        try:
            fail_projection_job(root, str(candidate["projection_key"]), str(exc))
        except ValueError:
            pass
        raise


def reconcile(root: Path, *, worker_id: str = "projection-reconciler", limit: int = 100,
              materializer: Callable[..., Any] = materialize_projection) -> list[dict[str, Any]]:
    """Process pending/failed jobs, then restore missing ready exports.

    Existing/tampered files are never overwritten: the latter become durable
    failed repair attempts. A deleted optional export is rebuilt from its
    authorized logical artifact without inventing another direct writer.
    """
    result: list[dict[str, Any]] = []
    for job in claim_projection_jobs(root, worker_id, limit=limit):
        try:
            result.append(materialize_job(root, job, materializer=materializer, worker_id=worker_id))
        except Exception as exc:
            result.append(fail_projection_job(root, str(job["projection_key"]), str(exc)))
    remaining = max(0, limit - len(result))
    if not remaining:
        return result
    for ready in list_projection_jobs(root, status="ready", limit=remaining):
        try:
            if not verify_job(root, ready).valid:
                result.append(repair(root, ready, worker_id=worker_id, materializer=materializer))
        except Exception as exc:
            # Preserve the original ready acknowledgement. Any created repair
            # job remains the durable retry target.
            result.append({"projection_key": ready["projection_key"], "status": "failed", "last_error": str(exc)[:2000]})
    return result


__all__ = [
    "enqueue", "materialize", "materialize_job", "verify_job", "repair", "reconcile",
    "list_pending", "list_failed", "retry", "remove_optional", "list_projection_jobs",
    "retry_projection_job",
]
