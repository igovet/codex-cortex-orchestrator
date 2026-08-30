# Knowledge-route contract

<!-- GENERATED:START -->

## Purpose

`harvest` and `harvest-refresh` are explicit Cortex routes for source-backed
repository knowledge maintenance. They update durable navigation under
`docs/project/` and `docs/features/` without becoming a prerequisite for
ordinary orchestration.

## Key files and dependencies

- [knowledge-harvest/SKILL.md](../../../plugins/cortex/skills/knowledge-harvest/SKILL.md) defines the V12 route overlay.
- [documentation-sync/SKILL.md](../../../plugins/cortex/skills/documentation-sync/SKILL.md) governs verified documentation updates after material work.
- [feature-census.md](../../../plugins/cortex/skills/knowledge-harvest/references/feature-census.md) defines the bounded feature inventory.
- [profiles.json](../../../plugins/cortex/profiles.json) supplies advisory explorer/planner/architect/writer roles.
- [test_marketplace_release_gate.py](../../../tests/test_marketplace_release_gate.py) validates the release-facing package and docs closure.

## Behavior

The user must explicitly select `$cortex:orchestrator harvest` or
`harvest-refresh`; repository state never activates the route automatically.
`help` is read-only and `normal` leaves the active route.

The coordinator defines the knowledge outcome, identifies useful independent
domains, delegates exploration and synthesis, and passes relevant report IDs.
Workers—not the coordinator—inspect source and executable configuration. For
project-code discovery, Codebase Memory is the mandatory first route: workers
bind it to the exact canonical root and collect graph evidence before local
search. Only demonstrated unavailable, excluded, or insufficient graph
evidence permits one bounded ordinary-repository fallback, whose rationale and
scope must be recorded; silent or repeated fallback is not permitted. Workers
then edit documentation and verify the resulting document tree. Each
delegation uses a required concise textual ownership `scope`; its
detailed procedure belongs in `instructions`.

The root coordinator has one bounded project-read exception for knowledge
routing. The host-injected `AGENTS.md` context already governs the current
task; the coordinator does not reread a global or project-root `AGENTS.md`.
It then reads `docs/project/index.md` and `docs/features/index.md`, then only the
task-relevant pages those indexes select. It does not scan arbitrary
documentation, follow unrelated links, inspect source/code/configuration,
perform the underlying domain analysis, edit documents, or run link, command,
release, or rendering checks.

The exception is a closed direct-read allowlist. Each coordinator read names an
already-known exact path and uses a non-shell direct file reader. Shell or
command execution, `rg`, `find`, globs, Codebase Memory or another graph,
source/repository search, browser inspection, directory listing, and
candidate-path probes are not routing operations. If the exact root or an
applicable path is unknown, or the host has no non-shell direct reader, a native
worker discovers or retrieves it and reports the bounded evidence. This also
applies to nested `AGENTS.md` applicability.

The bundled `orchestrator` skill is the single authority for the exact path
list and reusable knowledge-contract template. After bounded routing, the
coordinator should include exact selected project-root-relative paths and why
they apply, applicable requirements, verification evidence, ownership limits,
known documentation state, and any bounded further discovery in ordinary
delegation `instructions` and the native worker brief. This is rich advisory
worker guidance, not a literal format requirement.

The production V12 service preserves ordinary non-empty `instructions`; it
does not parse or reject advisory heading, section, Markdown, ordering, path,
or language structure. This remains one `instructions` string, not a second
public MCP object or tool.

This contract is semantic delegation content, not another MCP request object.
Profiles consume it and do not repeat the route, index literals, or template.
They never independently redo routing. If an index is missing or unreadable,
the coordinator creates a bounded discovery-worker delegation; it does not
inspect source itself and does not infer the explicit `harvest` route. If a
worker reports stale, conflicting, or incomplete documentation, the
coordinator assesses its evidence and task impact before choosing a revised
contract, bounded discovery, documentation sync, or disclosed risk. Drift does
not automatically invalidate unrelated work or trigger harvest.

Root discovery and every project-local state or artifact check remain
worker-owned. This includes Git, manifests, caches, worktrees, file or directory
existence/absence or unchanged-state, and project-local `.codex`. Read-only,
pre-plan, report-recovery, or explicit-user-request framing does not widen the
coordinator exception.

The coordinator writes the compiled contract and all worker-facing operational
instructions in English. A non-English user request is retained verbatim only
in the task's labeled original field and separately normalized into the English
objective/decision fields used for delegation. Coordinator-to-user summaries
remain in the user's latest meaningful language. This separates trusted,
bounded worker instructions from unquoted user-authored content without
rejecting non-English paths, proper nouns, code, or task-required product text.

Knowledge work follows the same V12 ledger contract as every other task. It has
no required wave ordering, planner gate, receipt-gated lifecycle, lifecycle
hook, profile capability, or backend completion rule. Worker handoff receipts
are delivery evidence only. Missing baseline documentation cannot block a
feature task or final answer.

Documentation must preserve user-authored content outside generated sections
when the route contract requires it, keep facts grounded in current source, and
exclude secrets, raw reports, prompts, tokens, personal data, and private
diagnostics. Source, schemas, tests, and executable configuration remain
authoritative.

## Conditional final documentation stage

After worker-reported project verification, the coordinator assesses
documentation impact from verified reports only. Material changes to behavior,
architecture, interfaces, commands, verification guidance, conventions, or
feature ownership require a delegated documentation-sync worker to update the
affected harvest knowledge under `docs/project/` and `docs/features/`. The
coordinator then delegates a separate documentation verifier to check source
grounding, links, commands, Mermaid, scope, and preservation of user-authored
content.

If reports show no material impact, the coordinator records an explicit,
report-grounded `documentation not required` rationale and creates no
meaningless edit. Only after the material or no-impact branch is resolved does
the coordinator record advisory closure and synthesize the final answer.
Missing update or verification evidence may trigger rework, replacement, or
explicit risk disclosure; it never activates a backend lifecycle gate.

## Verification

Run the self-contained skill/profile lint, release/protocol test, package
validation, link scan, and documentation review listed in
[verification.md](../../project/verification.md).
Check that knowledge-route guidance contains no V11 wave/gate/capability or hook
dependency, that the bounded coordinator route and worker-only project action
remain distinct, that shell/search/graph routing and coordinator project-state
checks are rejected, that profiles consume rather than reconstruct the supplied
contract, that both conditional documentation branches are covered, and that
missing knowledge state remains nonblocking.

<!-- GENERATED:END -->
