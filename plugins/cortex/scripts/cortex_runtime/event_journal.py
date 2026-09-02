"""Sanitized, owner-only MCP call observations for live verification.

This journal is deliberately not ledger evidence and never participates in a
command result.  It lets an operator observe that a real isolated MCP process
did (or did not) see hidden worker calls without retaining caller text,
arguments, durable references, prompts, reports, or diagnostics.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import time
from collections.abc import Mapping
from typing import Any
from cortex_runtime.raw_diagnostic import append as _raw_diagnostic


JOURNAL_DIRECTORY = ".cortex-mcp-events"
MAX_BYTES = 65_536
MAX_EVENTS = 512
_CATALOGUE_DIGEST_LENGTH = 64


def _fingerprint(value: object) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    return hashlib.sha256(("cortex-event-anchor-v1:" + value).encode("utf-8")).hexdigest()[:20]


def _private_directory_descriptor(path: Path) -> int:
    """Open one existing isolated-runtime directory without link traversal."""
    details = path.lstat()
    if stat.S_ISLNK(details.st_mode) or not stat.S_ISDIR(details.st_mode):
        raise OSError("event journal directory is invalid")
    if details.st_uid != os.getuid() or stat.S_IMODE(details.st_mode) != 0o700:
        raise OSError("event journal directory is not owner-only")
    flags = os.O_RDONLY | os.O_DIRECTORY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags)
    try:
        observed = os.fstat(descriptor)
        if not stat.S_ISDIR(observed.st_mode) or observed.st_uid != os.getuid() or stat.S_IMODE(observed.st_mode) != 0o700:
            raise OSError("event journal directory changed while opening")
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _private_child_directory(parent_fd: int, name: str) -> int:
    """Create/open one owner-only child using the trusted parent descriptor."""
    if not name or name in {".", ".."} or "/" in name:
        raise OSError("event journal child path is invalid")
    try:
        os.mkdir(name, mode=0o700, dir_fd=parent_fd)
    except FileExistsError:
        pass
    flags = os.O_RDONLY | os.O_DIRECTORY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(name, flags, dir_fd=parent_fd)
    try:
        observed = os.fstat(descriptor)
        if not stat.S_ISDIR(observed.st_mode) or observed.st_uid != os.getuid() or stat.S_IMODE(observed.st_mode) != 0o700:
            raise OSError("event journal child directory is not owner-only")
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


class EventJournal:
    """Best-effort append-only bounded observation journal.

    A failed observation must never roll back or relabel a successful durable
    mutation.  ``limited`` therefore becomes visible to the live helper while
    every canonical MCP result keeps its original outcome.
    """

    def __init__(self, path: Path | None, *, build_id: str, code_home: Path | None = None, generation: Path | None = None) -> None:
        self.path = path
        self.build_id = build_id
        self.code_home = code_home
        self.generation = generation
        self.limited = path is None
        self._reported_limited = False

    @staticmethod
    def _raw_boundary(kind: str, payload: Mapping[str, Any]) -> None:
        """Tap every publication before sanitization through one non-recursive boundary."""
        _raw_diagnostic(kind=kind, payload=dict(payload))

    @classmethod
    def from_generation(cls, *, generation: Path, build_id: str, code_home: Path) -> "EventJournal":
        """Use a runtime-claimed observation generation, never host env."""
        return cls(generation / "events.jsonl", build_id=build_id, code_home=code_home, generation=generation)

    def _open(self) -> int:
        if self.path is None or self.code_home is None:
            raise OSError("event journal path is unavailable")
        if self.generation is not None:
            generation = self.generation
            expected_root = self.code_home / ".cortex-mcp-observations" / "generations"
            if self.path != generation / "events.jsonl" or generation.parent != expected_root:
                raise OSError("event journal generation is outside isolated runtime")
            root_fd = _private_directory_descriptor(self.code_home)
            os.close(root_fd)
            # Validate every managed ancestor explicitly. Checking only the
            # leaf would allow a substituted root/generations directory to be
            # hidden behind normal Path traversal.
            observation_root = expected_root.parent
            observation_fd = _private_directory_descriptor(observation_root)
            generations_fd = _private_directory_descriptor(expected_root)
            os.close(observation_fd)
            os.close(generations_fd)
            details = generation.lstat()
            if stat.S_ISLNK(details.st_mode) or not stat.S_ISDIR(details.st_mode) or details.st_uid != os.getuid() or stat.S_IMODE(details.st_mode) != 0o700:
                raise OSError("event journal generation is not owner-only")
            flags = os.O_RDWR | os.O_CREAT
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            descriptor = os.open(self.path, flags, 0o600)
            try:
                os.fchmod(descriptor, 0o600)
                observed = os.fstat(descriptor)
                if not stat.S_ISREG(observed.st_mode) or observed.st_uid != os.getuid() or stat.S_IMODE(observed.st_mode) != 0o600:
                    raise OSError("event journal is not owner-only")
                return descriptor
            except BaseException:
                os.close(descriptor)
                raise
        event_root = self.code_home / JOURNAL_DIRECTORY
        session_directory = self.path.parent
        if (
            not self.path.is_absolute()
            or self.path.name != "events.jsonl"
            or session_directory.parent != event_root
            or event_root.parent != self.code_home
        ):
            raise OSError("event journal path is outside the isolated runtime")
        # The CODEX_HOME root must already have been made private by the
        # isolated launcher.  Do not create it here: silently creating an
        # arbitrary environment-selected root could touch a stable profile.
        root_fd = _private_directory_descriptor(self.code_home)
        event_fd = session_fd = None
        descriptor: int | None = None
        flags = os.O_RDWR | os.O_CREAT
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            event_fd = _private_child_directory(root_fd, JOURNAL_DIRECTORY)
            session_fd = _private_child_directory(event_fd, session_directory.name)
            descriptor = os.open(self.path.name, flags, 0o600, dir_fd=session_fd)
            os.fchmod(descriptor, 0o600)
            observed = os.fstat(descriptor)
            if not stat.S_ISREG(observed.st_mode) or observed.st_uid != os.getuid() or stat.S_IMODE(observed.st_mode) != 0o600:
                raise OSError("event journal is not owner-only")
            return descriptor
        except BaseException:
            if descriptor is not None:
                os.close(descriptor)
            raise
        finally:
            if session_fd is not None:
                os.close(session_fd)
            if event_fd is not None:
                os.close(event_fd)
            os.close(root_fd)

    @staticmethod
    def _sequence(payload: bytes) -> int:
        latest = 0
        for line in payload.splitlines()[-MAX_EVENTS:]:
            try:
                value = json.loads(line)
            except (UnicodeError, json.JSONDecodeError):
                continue
            sequence = value.get("sequence") if isinstance(value, Mapping) else None
            if isinstance(sequence, int) and not isinstance(sequence, bool):
                latest = max(latest, sequence)
        return latest

    @staticmethod
    def _retained(payload: bytes) -> bytes:
        lines = payload.splitlines(keepends=True)
        if len(lines) > MAX_EVENTS:
            lines = lines[-MAX_EVENTS:]
        value = b"".join(lines)
        if len(value) <= MAX_BYTES:
            return value
        # Keep only complete JSONL records.  Every individual event is tiny;
        # if a corrupted oversized row exists, dropping it is safer than
        # parsing or retaining an uncontrolled payload.
        while lines and len(value) > MAX_BYTES:
            lines.pop(0)
            value = b"".join(lines)
        return value

    def emit(
        self,
        *,
        operation: str,
        kind: str,
        success: bool,
        fault: str | None,
        mutation: str | None,
        task_anchor: object = None,
        assignment_anchor: object = None,
        connection_role: object = None,
        publication_type: object = None,
        publication_status: object = None,
        dispatch_correlation_marker: object = None,
        validation_location: object = None,
        validation_field: object = None,
        validation_expected: object = None,
        corrective_action: object = None,
        activation_role: object = None,
        activation_operation_category: object = None,
        activation_phase: object = None,
        activation_reason_code: object = None,
    ) -> None:
        """Record one safe event.  Failures remain non-blocking observations."""
        self._raw_boundary("journal_emit", {"operation": operation, "kind": kind, "success": success, "fault": fault, "mutation": mutation, "task_anchor": task_anchor, "assignment_anchor": assignment_anchor})
        try:
            descriptor = self._open()
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX)
                with os.fdopen(os.dup(descriptor), "r+b", closefd=True) as file:
                    file.seek(0)
                    prior = file.read(MAX_BYTES + 1)
                    if len(prior) > MAX_BYTES:
                        # A trusted writer always bounds the file.  Treat a
                        # malformed/manual expansion as an observation limit.
                        raise OSError("event journal exceeded its hard bound")
                    event: dict[str, Any] = {
                        "sequence": self._sequence(prior) + 1,
                        "monotonic_ns": time.monotonic_ns(),
                        "operation": operation,
                        "kind": kind,
                        "outcome": "success" if success else "failure",
                        "build_id": self.build_id,
                    }
                    if mutation is not None:
                        event["mutation"] = mutation
                    if not success and isinstance(fault, str) and fault:
                        event["fault"] = fault
                    task = _fingerprint(task_anchor)
                    assignment = _fingerprint(assignment_anchor)
                    if connection_role == "worker" and assignment is None:
                        # Worker-scoped task references are public selectors,
                        # but the committed connection role is the audience
                        # proof. Attribute that digest to assignment scope so
                        # live verification can distinguish the worker's read
                        # and publication from coordinator queries.
                        assignment = _fingerprint(task_anchor)
                    if task is not None:
                        event["task"] = task
                    if assignment is not None:
                        event["assignment"] = assignment
                        event["scope"] = "assignment"
                    else:
                        event["scope"] = "coordinator"
                    if connection_role in {"coordinator", "worker"}:
                        event["role"] = connection_role
                    dispatch_correlation = _fingerprint(dispatch_correlation_marker)
                    if dispatch_correlation is not None:
                        event["dispatch_correlation"] = dispatch_correlation
                    if isinstance(publication_type, str) and publication_type:
                        event["publication_type"] = publication_type
                    if isinstance(publication_status, str) and publication_status:
                        event["publication_status"] = publication_status
                    # Host activation observations use a closed vocabulary;
                    # unlike MCP diagnostics they carry no native tool name,
                    # input, prompt, handle, or identifier.
                    if operation == "activation_hook" and kind == "pre_tool":
                        allowed_role = {"coordinator", "worker", "unattributed"}
                        allowed_category = {"project_local", "native_agent", "cortex_semantic", "local_tool", "unknown"}
                        allowed_phase = {"pre_anchor", "post_anchor", "worker_bootstrap", "worker_active"}
                        allowed_reason = {"route_not_anchored", "worker_bootstrap_required", "coordinator_worker_operation", "dispatch_mismatch", "turn_mismatch", "orphan_child", "unknown"}
                        if activation_role in allowed_role:
                            event["role"] = activation_role
                        if activation_operation_category in allowed_category:
                            event["operation_category"] = activation_operation_category
                        if activation_phase in allowed_phase:
                            event["phase"] = activation_phase
                        if activation_reason_code in allowed_reason:
                            event["reason_code"] = activation_reason_code
                    # Validation observations are intentionally structural.
                    # They carry no request value, parser text, prompt, or
                    # payload fragment, yet let the live verifier distinguish
                    # a malformed first publication from a server admission
                    # failure and avoid an unchanged retry.
                    if isinstance(validation_location, str) and re.fullmatch(r"^\$(?:\.[A-Za-z_][A-Za-z0-9_]{0,63}|\[[0-9]{1,4}\]){0,16}$", validation_location):
                        event["validation_location"] = validation_location
                    if isinstance(validation_field, str) and re.fullmatch(r"^[a-z][a-z0-9_]{0,63}$", validation_field):
                        event["validation_field"] = validation_field
                    if isinstance(validation_expected, str) and validation_expected in {"required_field", "no_extra_properties", "string", "integer", "object", "array", "permitted_value", "constant", "bounded_length", "bounded_range", "unique_items", "permitted_input_shape", "complete_evidence_envelope"}:
                        event["validation_expected"] = validation_expected
                    if isinstance(corrective_action, str) and corrective_action in {"review_advertised_schema", "correct_publication_evidence", "reuse_typed_handle"}:
                        event["corrective_action"] = corrective_action
                    encoded = json.dumps(event, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii") + b"\n"
                    retained = self._retained(prior + encoded)
                    file.seek(0)
                    file.write(retained)
                    file.truncate()
                    file.flush()
                    os.fsync(file.fileno())
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            finally:
                os.close(descriptor)
        except (OSError, ValueError, TypeError):
            self.limited = True
            if not self._reported_limited:
                # This is a safe observability limitation, not a tool error:
                # the canonical MCP result has already succeeded or failed on
                # its own merits and must not be retried because journaling is
                # unavailable.
                print("Cortex MCP event observation=limited", file=sys.stderr, flush=True)
                self._reported_limited = True

    def emit_server_ready(self, *, catalogue_count: int, catalogue_digest: str) -> None:
        """Record the one safe post-initialize observation for this process.

        The caller owns the once-per-process guard because it alone knows the
        actual MCP session state and physical initialize reply outcome.  This
        method deliberately accepts only fixed, non-caller-controlled
        catalogue metadata; it cannot become a second channel for server
        paths, tool definitions, prompts, or diagnostics.
        """
        self._raw_boundary("journal_writer_attempt", {"operation": "server_ready", "build_id": self.build_id})
        if (
            isinstance(catalogue_count, bool)
            or not isinstance(catalogue_count, int)
            or catalogue_count < 0
            or not isinstance(catalogue_digest, str)
            or len(catalogue_digest) != _CATALOGUE_DIGEST_LENGTH
            or any(character not in "0123456789abcdef" for character in catalogue_digest)
        ):
            self.limited = True
            if not self._reported_limited:
                print("Cortex MCP event observation=limited", file=sys.stderr, flush=True)
                self._reported_limited = True
            return
        try:
            descriptor = self._open()
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX)
                with os.fdopen(os.dup(descriptor), "r+b", closefd=True) as file:
                    file.seek(0)
                    prior = file.read(MAX_BYTES + 1)
                    if len(prior) > MAX_BYTES:
                        raise OSError("event journal exceeded its hard bound")
                    event = {
                        "sequence": self._sequence(prior) + 1,
                        "monotonic_ns": time.monotonic_ns(),
                        "operation": "server_ready",
                        "kind": "registration",
                        "outcome": "success",
                        "build_id": self.build_id,
                        "catalogue_count": catalogue_count,
                        "catalogue_digest": catalogue_digest,
                        "scope": "unattributed",
                    }
                    encoded = json.dumps(event, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii") + b"\n"
                    retained = self._retained(prior + encoded)
                    file.seek(0); file.write(retained); file.truncate(); file.flush(); os.fsync(file.fileno())
                self._raw_boundary("journal_record", {"operation": "server_ready", "outcome": "written"})
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            finally:
                os.close(descriptor)
        except (OSError, ValueError, TypeError):
            self.limited = True
            if not self._reported_limited:
                print("Cortex MCP event observation=limited", file=sys.stderr, flush=True)
                self._reported_limited = True

    def emit_lifecycle(
        self,
        *,
        event_kind: str,
        source: str,
        reason: str | None = None,
        session: object = None,
        turn: object = None,
        agent: object = None,
        parent: object = None,
        generation: object = None,
        correlation: object = None,
        dispatch_correlation_marker: object = None,
        status: str | None = None,
    ) -> None:
        """Append a host lifecycle observation without importing host state.

        Hook payloads contain native identifiers and sometimes paths.  This
        method intentionally accepts them only as opaque values and stores
        short one-way fingerprints.  It is observation-only: a journal fault
        is reported as ``limited`` and never changes a Codex lifecycle result.
        """
        self._raw_boundary("lifecycle_emit", {"event_kind": event_kind, "source": source, "session": session, "turn": turn, "agent": agent, "parent": parent, "generation": generation, "correlation": correlation, "status": status})
        allowed_kind = {"session_start", "session_end", "subagent_start", "subagent_stop", "pre_compact", "post_compact", "native_dispatch"}
        allowed_source = {"startup", "resume", "clear", "compact", "manual", "auto", "spawn", "follow_up", "acknowledged", "conflict", "unavailable", "ambiguous", "unknown"}
        if event_kind not in allowed_kind or source not in allowed_source:
            self.limited = True
            if not self._reported_limited:
                print("Cortex MCP event observation=limited", file=sys.stderr, flush=True)
                self._reported_limited = True
            return
        try:
            descriptor = self._open()
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX)
                with os.fdopen(os.dup(descriptor), "r+b", closefd=True) as file:
                    file.seek(0)
                    prior = file.read(MAX_BYTES + 1)
                    if len(prior) > MAX_BYTES:
                        raise OSError("event journal exceeded its hard bound")
                    event: dict[str, Any] = {
                        "sequence": self._sequence(prior) + 1,
                        "monotonic_ns": time.monotonic_ns(),
                        "operation": "host_lifecycle",
                        "kind": event_kind,
                        "source": source,
                        "outcome": "observed",
                        "build_id": self.build_id,
                    }
                    for key, value in (("session", session), ("turn", turn), ("agent", agent), ("parent", parent), ("generation", generation), ("correlation", correlation)):
                        fingerprint = _fingerprint(value)
                        if fingerprint is not None:
                            event[key] = fingerprint
                    dispatch = _fingerprint(dispatch_correlation_marker)
                    if dispatch is not None:
                        event["dispatch_correlation"] = dispatch
                    if isinstance(reason, str) and reason in allowed_source:
                        event["reason"] = reason
                    if isinstance(status, str) and status in allowed_source:
                        event["status"] = status
                    encoded = json.dumps(event, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii") + b"\n"
                    retained = self._retained(prior + encoded)
                    file.seek(0); file.write(retained); file.truncate(); file.flush(); os.fsync(file.fileno())
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            finally:
                os.close(descriptor)
        except (OSError, ValueError, TypeError):
            self.limited = True
            if not self._reported_limited:
                print("Cortex MCP event observation=limited", file=sys.stderr, flush=True)
                self._reported_limited = True
            return
