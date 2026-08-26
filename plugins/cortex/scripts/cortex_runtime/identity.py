"""Stable identifiers and human-readable names for Cortex workers."""
from __future__ import annotations

import hashlib
import re


SAFE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,79}$")
NATIVE_AGENT_NAME_RE = re.compile(r"^[a-z0-9_]{1,80}$")


def safe_id(value: str) -> str:
    """Normalize a durable Cortex identifier without allowing path syntax."""
    if "/" in value or "\\" in value or value.strip() in {".", ".."}:
        raise ValueError("identifier must not contain path separators")
    candidate = re.sub(r"[^a-z0-9_-]+", "-", value.lower()).strip("-_")
    if not candidate or not SAFE_ID_RE.fullmatch(candidate):
        raise ValueError("identifier must contain only lowercase letters, numbers, hyphens, or underscores")
    return candidate


def worker_module_label(objective: object, gate: object) -> str:
    """Return a concise, non-sensitive module label for host-visible workers."""
    del gate  # A lifecycle gate is not a project module.
    ignored = {
        "app", "apps", "api", "docs", "features", "package", "packages", "plugin", "plugins",
        "script", "scripts", "service", "services", "src", "test", "tests", "the", "and", "for",
        "with", "from", "into", "this", "that", "work", "task", "cortex", "orchestrator",
        "harvest", "refresh", "review", "implement", "implementation", "documentation",
        "run", "source", "backed", "full", "knowledge", "small", "repository", "acceptance",
        "verification", "feature", "features", "create", "add", "fix", "update", "audit", "inspect",
        "check", "verify", "produce", "decision", "complete", "bounded", "requested", "outcome",
        "behavior", "explain", "explains", "user", "route", "use", "normal", "pipeline", "plan",
        "approval", "because", "command", "zero", "every", "scope", "perform", "post",
        "require", "required", "actual", "final", "continue", "until", "without",
        "not", "request", "project", "index", "exists", "mapped", "explicitly", "excluded",
        "remain", "remains", "validate", "links", "paths", "independently", "before", "closing",
        "canonical", "phase", "phases", "through", "independent", "close",
    }
    candidates: list[str] = []
    objective_text = str(objective or "")
    if not candidates:
        domain_candidates = [
            match.group(1)
            for match in re.finditer(
                r"\b([A-Za-z][A-Za-z0-9_-]*)\s+(?:feature|module|domain|component|service|flow|functionality|behavior|logic|workflow|scenario|capability)\b",
                objective_text,
                re.IGNORECASE,
            )
        ]
        candidates.extend(domain_candidates or re.findall(r"[A-Za-z][A-Za-z0-9]*", objective_text))
    normalized_candidates = {candidate.lower() for candidate in candidates}
    domain_aliases = (
        ({"auth", "authentication", "authenticate", "login", "logout"}, "Authentication"),
        ({"trade", "trades", "trading", "broker", "brokerage"}, "Trading"),
        ({"price", "prices", "pricing", "quote", "quotes"}, "Pricing"),
    )
    for aliases, label in domain_aliases:
        if normalized_candidates & aliases:
            return label
    if re.search(r"\bharvest(?:-refresh)?\b", objective_text, re.IGNORECASE):
        return "Repository"
    selected: list[str] = []
    for candidate in candidates:
        normalized = candidate.lower()
        if normalized in ignored or len(normalized) < 3 or normalized in {item.lower() for item in selected}:
            continue
        selected.append(normalized.title())
        if len(selected) == 2:
            break
    return " ".join(selected) if selected else "Repository"


def worker_display_name(profile: str, module: str) -> str:
    """Return a concise human-readable role and module label."""
    role = " ".join(part.title() for part in re.findall(r"[A-Za-z0-9]+", safe_id(profile)))
    compact_module = " ".join(re.findall(r"[A-Za-z0-9]+", str(module)))[:48] or "Worker"
    return f"{role} {compact_module}"


def native_worker_task_name(profile: str, task_id: str, attempt_id: str, module: str = "Worker") -> str:
    """Return an attempt-unique native task name within Codex's strict syntax."""
    profile_id = safe_id(profile)
    task_id = safe_id(task_id)
    attempt_id = safe_id(attempt_id)
    native_profile = profile_id.replace("-", "_")
    native_module = "_".join(re.findall(r"[a-z0-9]+", str(module).lower()))[:24].strip("_") or "worker"
    sequence = re.search(r"(\d{1,4})$", attempt_id)
    native_attempt = sequence.group(1).zfill(2) if sequence else attempt_id.replace("-", "_")[:12]
    identity_digest = hashlib.sha256(
        "\0".join((profile_id, task_id, attempt_id, native_module)).encode("utf-8")
    ).hexdigest()[:8]
    readable = f"{native_profile}_{native_module}_{native_attempt}_{identity_digest}"
    if len(readable) <= 80:
        candidate = readable
    else:
        digest = hashlib.sha256(readable.encode("utf-8")).hexdigest()[:16]
        attempt_fragment = native_attempt[:12].rstrip("_") or "attempt"
        profile_fragment = native_profile[:24].rstrip("_") or "worker"
        candidate = f"{profile_fragment}_{attempt_fragment}_{digest}"
    candidate = candidate[:80].rstrip("_")
    if not NATIVE_AGENT_NAME_RE.fullmatch(candidate):
        raise RuntimeError("native worker task name violated the host agent-name contract")
    return candidate
