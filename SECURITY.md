# Security policy

## Scope

This repository contains the Cortex 10.0.5 Codex plugin. The runtime is
opt-in, runs locally, and stores orchestration state in a host-private SQLite
v15 ledger. The supported public contract is the fresh v10 protocol only.

## Supported security boundary

Cortex treats the following as authoritative:

- server-issued opaque task, dispatch, and attempt identities;
- immutable dispatch briefings scoped to the exact task and attempt;
- SQLite AttemptEvent rows and one canonical AttemptResult per attempt;
- server-observed timestamps, native identity, changed files, checks,
  workspace state, and verification observations;
- target-specific ContextCompiler and HandoffCompiler projections;
- attempt_result_ref, context_result_refs, and predecessor_result_refs for
  cross-stage result links.

Generated JSON, Markdown, journals, plans, and indexes are views. Their text,
presence, links, or filenames cannot authorize a read, gate, resume, handoff,
or completion.

The public operation set is exactly nine. Coordinator calls are
start_orchestration, continue_orchestration, manage_orchestration,
manage_governance, and read_worker_result. Worker calls are worker_question,
record_attempt_event, complete_attempt, read_dispatch_briefing, and
read_worker_result.

## Data handling

Do not place credentials, access tokens, personal data, private task content,
or raw host/model responses in prompts, issues, commits, generated views, or
logs. The runtime bounds event count, event bytes, context size, and diagnostic
output. It rejects symlink and non-regular targets at capability boundaries.

The worker contributes semantic facts only. The server derives identity,
timestamps, task revision, profile, phase, changed paths, executed checks,
workspace observations, and verification metadata. Missing observations remain
explicit and cannot be converted into a successful verification claim.

Read observations are idempotent and scoped to the exact task, attempt,
dispatch, result identity, and digest. A read from one task cannot satisfy
another task's requirement.

## State and filesystem safety

The default state root is a private, host-owned directory under
~/.codex/cortex/projects/p-<sha256>/. An explicit
CORTEX_HOST_STATE_DIR override must be private, outside the workspace, and
owned by the current user. Broad roots such as /, /home, /tmp, or a repository
root are not valid state roots.

SQLite is the atomic boundary. WAL/SHM files, advisory locks, and projection
jobs are coordination machinery, not application evidence. Filesystem views are
regular, private, digest-checked, and rebuildable. A failed view or serializer
after WORK_COMPLETED retries finalization on the same attempt; it never
creates a second worker for the same work.

Backups and pruning must be SQLite-aware. A tombstone is committed before
removing a view. Never use a broad recursive deletion command for Cortex state,
and never copy a database between users or unrelated projects.

## Lifecycle and hooks

The six hooks bind native sessions and workers to the exact server-issued
dispatch. They do not infer a task by scanning directories or accept prose as
proof. A stopped child before WORK_COMPLETED is recovered by its exact
attempt; a canonical WORK_COMPLETED result continues through FINALIZING and
COMPLETED.
An active dispatch without a finalized canonical result cannot authorize a
gate, handoff, terminal completion, or coordinator stop. A host child binding
is recovery metadata, not a proof that work completed.

Review hook commands before trust. They must invoke the installed cache's
bundled scripts/cortex-launcher and scripts/cortex_hook.py, and the content
hashes must match the selected plugin. Start a new Codex task after
installation or update.

## Prompt and plugin integrity

Prompt Contract v3 is the sole stable prompt path. Dispatch-controlled values
are fenced assignment data; static policy remains in the bundled skill and
profile sources. Prompt lint, deterministic prompt evaluation, marketplace
validation, and source-mode package checks are required before release. The
package manifest is version 10.0.5 and the ledger schema is v15.

## Vulnerability reporting

Please do not include credentials, personal data, private task contents, or
raw diagnostic logs in a public issue. If a confidential reporting channel is
listed for the published repository, use it. Otherwise open a minimal public
issue containing only a safe description and a way to request a private
conversation.

When reporting, include the affected version, operating system, reproduction
steps that contain no secrets, expected behavior, observed behavior, and
whether the issue affects the plugin package, MCP server, lifecycle hook, or
host-state boundary. Do not attach a database, prompt transcript, cache, or
worker output.

Maintainers should acknowledge a report, reproduce it in an isolated project,
assign severity, coordinate remediation, and publish a new version with
verified package hashes. Keep the test case deterministic and remove personal
or operational data before committing it.

## Release safety checklist

Before publishing 10.0.5 or a later release:

1. Run the focused protocol, lifecycle, context, handoff, governance, and
   packaging tests.
2. Run the full source test suite and record exact counts.
3. Run prompt lint/evaluation and marketplace validation.
4. Run git diff --check, inspect links and commands, and verify manifest
   version, public operation count, and schema v15.
5. Verify that no private data, credentials, temporary state, or generated
   cache files enter the package.
6. State any unavailable host-installation or live-model checks explicitly.

<!-- END SECURITY POLICY -->
