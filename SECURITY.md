# Security policy

## Scope

This repository contains the Cortex 12.1.0 Codex plugin. The V12 runtime is
explicitly opt-in, runs locally, and stores coordination state in a private,
project-isolated SQLite schema-v1 ledger. Cortex is a durable coordination
sidecar, not an authorization service or workflow engine. Canonical
coordination state remains in the private ledger; any Markdown file is a
derived, host-private human view rather than another source of authority.

## Supported security boundary

Cortex treats the following as authoritative:

- the exact resolved `project_root` supplied only to `create_task`, with its
  saved canonical `task_id` retained as evidence and compact `task_ref`
  anchoring later task-anchored public calls;
- project isolation derived from the SHA-256 digest of that resolved root;
- SQLite transactions, uniqueness, foreign keys, and schema-v1 metadata;
- stable task, delegation, report, assessment, initiative, closure, and
  user-decision IDs;
- strict public input schemas, enumerations, size limits, and reference checks;
- operation-scoped idempotency keys and normalized-payload digests;
- immutable report chunks and finalized/aborted report assemblies, append-only
  governance assessments, user decisions, and closures, and append-only
  initiative revisions;
- ordinary Codex/user approval for destructive, external, privileged, or
  materially scope-expanding actions.

The backend does not decide whether the model may take its next safe meaningful
step. Governance mode, initiative status, dependency warnings, report status,
closure verdict, stored user decisions, and missing closure are evidence, not
backend gates. Required plan review and a genuine user decision are owned by
the coordinator in ordinary chat: a stored decision records the evidence but
does not authenticate the user, grant authority, or authorize a later action.

Coordinators and workers receive the same exact eleven-tool catalog. There is no
audience filtering, opaque coordinator or worker capability, host epoch, native
child binding or lifecycle attestation, profile capability matrix, or session
or environment identity. The worker brief does carry renderer/profile proof and
a stateless native-dispatch projection; neither is host authority. IDs are
references, not bearer credentials.

Native subagent creation, waiting, permissions, filesystem edits, shell
commands, browser actions, and external application calls remain host/user
operations outside the Cortex ledger. The native dispatch projection also has
no worktree/workspace selector; physical concurrent-writer isolation is an
unconfirmed host capability and is not implemented or claimed by Cortex.
Approval to use the local Cortex MCP server does not authorize any of those
actions.

The root coordinator is an orchestration-only control plane. It may define the
outcome and acceptance criteria, select or revise governance, use the ledger,
create and coordinate workers, consume their native report handoffs, decide rework or
replacement, record advisory closure, and synthesize the user answer. It must
never inspect or search source/code/configuration, create or edit
target-project files, run project commands, builds, tests, browser checks, or
direct verification, or perform substantive task/domain work. Every
project-facing action and every substantive analysis belongs to a delegated
worker. Missing evidence requires another worker delegation, not direct
coordinator access.

For routing only, the coordinator must read applicable `AGENTS.md`, the project
and feature indexes, and task-relevant pages selected from those indexes. The
bundled orchestrator skill is the sole authority for this bounded route and its
six-part per-delegation knowledge contract. Profiles only consume the supplied
contract; they cannot widen the route. Arbitrary documentation scanning,
unrelated-link traversal, and interpreting project state from source remain
worker-owned.

This exception is a closed direct-read allowlist, not filesystem authority.
Each coordinator read uses a non-shell direct reader with one already-known
exact path. Shell/commands, `rg`, `find`, globs, Codebase Memory or other graph
search, source/repository search, directory listing, and candidate probes are
forbidden routing mechanisms. Unknown roots or paths and unavailable direct
reads require a native discovery/retrieval worker.

Project-root discovery and all project-local state or artifact checks are
worker-owned, including Git, manifests, caches, worktrees, existence/absence or
unchanged-state, and project-local `.codex`. The boundary does not change for a
read-only check, plan preparation, report recovery, or a user request addressed
to the coordinator.

## Public API boundary

The complete public catalog is defined in
[`public_contracts.py`](plugins/cortex/scripts/cortex_runtime/public_contracts.py)
and contains exactly:

1. `create_task`
2. `inspect_task`
3. `create_delegation`
4. `read_delegation`
5. `submit_report`
6. `read_reports`
7. `set_governance_mode`
8. `record_initiative`
9. `inspect_governance`
10. `submit_governance_closure`
11. `record_user_decision`

Every tool has a closed input object. Runtime validation consumes the same
schema object advertised by `tools/list`. Unexpected properties, invalid
types, unsupported enumeration values, oversized fields or arrays, invalid
identifiers, and invalid cursors are rejected before a service operation.

Only `create_task` accepts the exact resolved `project_root`; it is the sole
public project-root boundary, stores the canonical project association, and
returns preferred `task_ref` plus canonical `task_id`. The seven
task-anchored public tools require `task_ref` and resolve it fail-closed to the
saved canonical task. The 14-character reference contains only a 12-hex task
suffix; resolution scans private V12 shards and rejects zero or ambiguous
matches. Full `task_id` remains durable database evidence, not a public request
locator; entity-derived public calls resolve only from their exact emitted
delegation or report refs instead.
Initiative operations use the resolved task only as a project locator, never
as authority. Current MCP call
metadata does not provide a guaranteed project-root binding, the plugin stdio
process `cwd="."` names the installed plugin directory rather than the target
project, and V12 has no lifecycle hook that binds a session root. The native
worker brief carries the saved root only for working-directory context.

`create_task` records one versioned task/result contract: English-normalized
`objective` for internal coordination; exact arbitrary-Unicode
`user_request_original`; `user_language`; `task_contract_version`; bounded
English `requirements`, `constraints`, `acceptance_criteria`, and
`verification_plan`; and optional bounded JSON `context`. Original user wording
is preserved and never replaced by the normalization. The result contract is
not a backend execution plan or permission boundary. Optional `context` never
supplies or overrides the root. `create_delegation.scope` is a required
non-empty text string of at most 65,536 characters: it is the concise boundary
of worker ownership, detailed execution belongs in `instructions`, and an
object-shaped scope is invalid. Delegation `model` and `reasoning_effort` are
required together and retained exactly. `profile_name` is an exact packaged
enum distinct from the bounded human-readable `role`, and its renderer proof
must be loaded. The returned native arguments preserve the exact rendered
message and selection for one matching host spawn. Normal spawning consumes
that receipt directly; recovery uses `inspect_task` continuation data only
after host reconciliation. Continuations never attest lifecycle, and native
commentary alone is never durable progression: a recovered child needs a
finalized report, explicit blocked/partial handoff, or parent-linked
replacement. `submit_governance_closure` requires both `subject_type` and the
matching compact task or initiative `subject_ref`; it records advisory evidence
and does not gate safe work or a truthful user-facing answer.

`record_user_decision` is the durable record of an ordinary-chat decision. It
requires one canonical field set: `task_ref`, subject type/ref/digest, decision
type, English prompt context, exact `response_original`, English `response_en`,
and `user_language`. A plan decision binds only that completed, finalized plan
revision. An `approve` decision additionally requires the complete exact ready
approval-view relation returned together by Cortex: the report ref/digest,
approval handle, view digest, and view source sequence. Missing, legacy, or
mixed fields are rejected before the service mutation. The record may supersede
a prior decision on the same subject, but it never replaces original wording or
acts as a bearer approval token.
The mutation response is compact: it may include at most a 512-character
English response excerpt and never returns `response_original`.

Expected failures return bounded error codes and structural details only.
Errors must not contain task objectives, delegation instructions, report
content, governance notes, personal data, credentials, raw exception state,
database rows, or private filesystem content. Unknown runtime failures collapse
to a sanitized ledger or validation error.

The stdio transport bounds one JSON frame at 256 KiB. An oversized frame is
fully drained and returns a sanitized parse error so the next valid
`ping`/`tools/list` request can succeed; it must not desynchronize the server.

## Data handling

Do not place secrets, credentials, access tokens, private keys, personal data,
raw diagnostic logs, or unnecessarily sensitive operational details in:

- task objectives or context;
- delegation scope or instructions;
- worker reports or report handoffs;
- governance rationales, risk factors, initiatives, closures, or follow-ups;
- prompts, fixtures, tests, documentation, issues, commits, or generated views.

Use English for every native worker commentary/update, inter-worker message,
final response, tool-authored durable string, objective, requirement,
instruction, worker-authored report narrative, governance record, and English
decision normalization. Acceptance covers complete child threads, not only
final messages or database rows. Canonical product-facing reports and handoffs
may carry one optional unchanged `source_text` value as inert source material,
without a language tag or translated/original duplicate. Existing task and
decision contracts preserve exact arbitrary-Unicode user wording in their
designated `user_request_original` or `response_original` fields with
`user_language`; never overwrite or silently translate that source text.

Reports may contain material engineering evidence, so exact emitted report refs should be
passed to later workers instead of copying full reports into prompts. A small
report may use `single`; a large report uses `begin`, sequential indexed
`append` calls with bounded lowercase section labels, then `finalize` with the
expected chunk count and whole-report digest, or `abort` with a sanitized
English reason. Chunks are immutable and ordered; finalized or aborted reports
cannot be appended, and a replacement explicitly supersedes rather than edits
its predecessor. The types are `progress`, `result`, `synthesis`, and `plan`;
a `plan` report can declare `informational` or coordinator-owned `required`
review policy without creating a backend gate.

Ordinary delegation/task inspection creates no receipt or lifecycle fact.
`read_reports` returns at most 20 unique known reports in the exact requested
order and is the only report body/chunk reader. A worker handoff read made with
`reader_kind="worker"` and its exact consuming delegation creates an immutable
page receipt (digest, chunk indexes, byte count, and cursor chain); a
coordinator-classified read does not substitute for that evidence. A
coordinator does not call it merely to summarize a completed worker report: the
worker must return a concise `Summary` and exact `Report ref`. Downstream
workers use `read_reports` when their declared work genuinely requires the
report body. It returns only complete JSON
chunks that fit its bounded integer `max_bytes` budget (at most 65,536 bytes), with a
selection-scoped cursor for resume; `max_bytes=0` returns metadata only and no
bodies. Inspection tools use
`after_sequence` plus `limit`, return compact
references and bounded timeline pages, and expose `next_sequence`/`has_more`.

Private tool-error logs are same-user sensitive data. Inspect only a bounded
tail when necessary, extract sanitized correlation metadata, and never paste
raw records into a chat, issue, prompt, commit, fixture, or external system.

## State and filesystem safety

Each exact resolved project root maps to a separate database:

```text
~/.codex/cortex/v12/projects/p-<sha256-of-resolved-project-root>/cortex.db
```

The V12 state, project-shard, task, and view directories are created or
reconciled to mode `0700`.
Before every SQLite open, the runtime rejects symlinks and non-regular database
paths. The database and adjacent WAL/SHM files are reconciled to `0600`. SQLite
identifies the database family with an application ID, verifies schema version
1, and checks the project metadata against the derived project hash. A normal,
path-bearing `create_task` open automatically upgrades only the exact released
pre-human-view V12 shape to the ordered `v12-initial` plus
`v12-schema-v1-human-views` history in one transaction; it preserves legacy
rows and fails closed for an unknown or future layout. No V11 database is ever
opened or used as migration input.

The schema stores:

- `tasks`;
- `delegations`;
- `reports`;
- `governance_assessments`;
- `initiatives` and append-only `initiative_revisions`;
- current `initiative_links` plus link history inside revisions;
- immutable `governance_closures`;
- immutable `user_decisions` with exact-original and English-normalized fields;
- the ordered `timeline`;
- operation-scoped `idempotency` records;
- bounded projection job and projection-file metadata; and
- minimal schema and project metadata.

Writes use transactions; concurrent first-use bootstrap is serialized and
normal concurrent mutations use SQLite's write reservation and WAL behavior.
Do not edit `cortex.db`, WAL/SHM files, or metadata directly. Do not copy a
database between project roots or synthesize IDs by scanning other project
directories.

Idempotency is uniform for mutations. A repeated operation/key with the same
normalized payload returns the original record. Reuse with a different payload
returns a non-mutating `idempotency_conflict`. Idempotency is a retry-safety
mechanism, not authentication.

Known task, delegation, report, parent, and initiative references must resolve
inside the current project ledger. Cross-project task/report links are rejected
without mutating the destination ledger. An unresolved dependency identifier
may be retained as a same-project initiative warning so the model can assess it;
that warning does not grant access to another project's data.

Per-task Markdown projections live beside the canonical database under the
host-private V12 shard, never under `project_root`. A returned Markdown path is
publishable only when the active tool returns it `ready` after verifying
containment, regular-file type, current source sequence, and content digest.
The task directory is `tasks/<task_ref>/`, never a canonical full task ID;
full IDs remain in SQLite and the rendered evidence. Pair the server-provided
`markdown_link` from a verified ready view with a localized summary and its
effect or next step; copy that exact Markdown link byte-for-byte instead of
constructing a destination. Do not
publish guessed, constructed, backticked, code-block, line-broken, stale,
conflicted, unavailable, or bare paths; a projection failure leaves the
canonical ledger unchanged and must not block safe coordination or an honest
final answer. A released `tasks/<task-id>/` directory can move only through the
runtime's atomic no-replace migration; a destination conflict preserves both
directories and exposes no ready path.

Only current/immutable plan and finalized-report `.md` files are generated for
user-facing publication. They are readable Markdown documents with labeled
headings, normal lists, and paragraphs rather than raw nested field dumps.
The renderer owns the hierarchy. Ordinary caller-authored strings are treated
as data and sanitized context-sensitively so headings, lists, tables,
blockquotes, HTML, rules, and fences cannot inject Markdown structure; readable
punctuation is retained. Only explicitly typed blocks (such as a code block)
emit their intended formatting. An optional `cortex/report-view/v1` envelope is
interpreted only while rendering; malformed, unknown, or legacy content uses a
safe generic fallback and never changes report acceptance or persistence. It
does not place JSON objects, JSON arrays, script blocks, `<pre>` blocks, or opaque
serialized payloads in the view. Structured values remain in the canonical
SQLite database. Task, decision, delegation, initiative, closure, governance,
handoff, index, and timeline records are SQLite-only and have no user-facing
Markdown view.

## Operator maintenance boundary

`cortex_runtime.v12_maintenance` is a local administrator CLI, not a public MCP
tool or orchestration capability. It accepts one exact V12 `task_id`, derives
the project shard from the ID, and accepts no `project_root`, arbitrary database
path, export path, or V11 target. It performs owner/mode, regular-file,
no-symlink, database-family/schema/migration, project/task binding, integrity,
foreign-key, WAL, and synchronous-mode checks before sensitive work. Output is
bounded sanitized JSON.

`health` is read-only. Backup uses SQLite's online backup API but covers the
whole project shard, not only the anchor task, and creates a sealed owner-only
database/manifest bundle below that shard. Checkpoint, optimize, vacuum,
projection regeneration, and every other mutation require their exact uppercase
confirmation. Projection pruning and backup retention default to dry-run,
validate the entire selected set before mutation, and never remove canonical
ledger rows. Ready/conflicted/unmanaged/digest-mismatched views are not prune
targets. Backup retention accepts only the fixed sealed-bundle member set:
required `cortex.db` and `manifest.json`, with optional owner-private SQLite
WAL/SHM support files. It revalidates every member after confirmation and uses
no recursive traversal, glob, or caller-selected path.

Restore is **offline only**. The operator must stop all normal Cortex MCP access
to the shard and independently verify quiescence before invoking it. The command
requires `RESTORE`, the exact task ID, exact `p-<hash>` shard, sealed backup ID,
and `MCP_STOPPED`. The acknowledgement is not a shared lock and cannot make a
running service safe. Restore creates a fresh recovery backup first and attempts
rollback on failure, but an operator must never use it concurrently with the MCP
server or describe it as online. No maintenance action writes to `project_root`
or touches V11.

## Advisory governance boundary

The model owns mode selection, reassessment, initiative state, dependency
interpretation, verification depth, rework, risk acceptance, and closure. The
coordinator decides those orchestration questions from reports, while workers
perform every underlying project inspection and verification action. The
backend stores the model's statements and current projections.

Governance modes are `minimal`, `light`, and `full`. Assessment source is
`model` or `user_override`. An explicit user override is stored unchanged; the
backend does not promote, downgrade, or reject it. The latest user override
remains effective across later model assessments; those statements are new rows
that may preserve an evidence-backed warning or recommendation without
silently replacing the user's choice.

Initiative status is limited to `proposed`, `active`, `paused`, `completed`,
`closed`, and `cancelled`, but any transition among these values is accepted.
The status is informational. Parent, dependency, task, and report links remain
project-scoped. Missing or cyclic dependencies are returned as warnings; they
do not block a later status revision or closure.

Closure verdicts are `ready`, `ready_with_risks`, and `not_ready`. They are
model-authored recommendations. A `not_ready` task can receive another
delegation and another report immediately. An initiative can close while a
dependency remains unresolved if the residual risk is recorded. Missing
closure never prevents a user-facing answer.

## Model and profile integrity

Agent profiles are advisory prompt templates. They describe roles, workflows,
quality bars, and escalation conditions but contain no model literals, effort
pins, public-tool admission rules, or lifecycle authority.

The coordinator selects the exact packaged `profile_name` independently from
the human-readable `role` and verifies loaded proof plus digest. Free-form role
text is not profile proof. An unavailable fallback is limited to degraded
non-durable dispatch and requires a complete explicit role contract plus visible
disclosure.

The coordinator selects one exact model and effort independently for each
delegation. Supported models are `gpt-5.6-luna`, `gpt-5.6-terra`, and
`gpt-5.6-sol`; each supports `low`, `medium`, `high`, `xhigh`, and `max`.
Canonical recommendations use `high` for all three: Luna for default bounded
work, Terra for genuinely complex non-security work, and Sol for security work
and security-focused review.

Native dispatch serialization must preserve the exact selected effort and use
`fork_turns="none"`. Because Luna is the configured host default, a logical
Luna dispatch omits the native `model` argument. Terra and Sol pass their exact
model override. The backend validates this transport boundary but never chooses
or silently replaces a pair. Cortex has no server-owned model escalation or
recovery ladder.

## Lifecycle and hooks

Cortex V12 has no native lifecycle hooks and ships no lifecycle hook code. Hook
output, hook trust, host metadata, `SubagentStop`, coordinator stop,
session resume, and compaction events are not authorization or completion
evidence.

There is no mandatory server-owned spawn/wait/read/continue lifecycle. A native
wait is an ordinary model/host coordination action. Each successful durable
delegation returns one exact native-dispatch projection, which the coordinator
copies byte-for-byte into exactly one matching host spawn and then awaits that
worker's own report. The ledger does not launch or bind native agents. An
ambiguous spawn is reconciled by exact host handle, never blindly duplicated; a
reportless result may lead to an explicitly parent-linked replacement.

## Bundled skill and plugin integrity

The installed `orchestrator` and `cortex-control` skills are the authoritative
runtime model contract. Delegation assignment data remains bounded task
context, while agent profiles provide advisory role guidance. Briefing
conciseness must never cause material user intent, risk, decisions, report
references, applicable project-knowledge requirements, or verification results
to be omitted.

The orchestrator alone owns the exact knowledge-route paths and six-part
contract template. The coordinator embeds the compiled contract in
`instructions` and the native brief. Profiles consume that supplied contract,
do not repeat the routing definition, and inspect additional documentation only
when its further-discovery boundary explicitly authorizes the purpose, scope,
and stopping condition.

After worker-reported project verification, the coordinator assesses
documentation impact from worker reports. Material behavior, architecture,
interface, command, verification, convention, or feature-ownership changes
require a delegated documentation-sync update under `docs/project/` and
`docs/features/`, followed by a separate documentation-verifier worker. If
there is no material impact, the coordinator obtains a finalized worker-owned
report with an explicit English documentation-impact section and
material/no-impact rationale, links its exact `report_ref` in the final initiative,
and cites that compact ref plus its returned digest in closure evidence. A durable
`report_id` may remain in evidence, but is not a callable public locator. A self-asserted
`documentation_not_required` value is invalid. The coordinator does not
manufacture an edit and may use the bounded routing exception
to identify affected knowledge paths, but never edits or verifies those files;
the impact decision remains grounded in worker reports. This conditional stage
precedes advisory closure and the final answer. Missing documentation evidence
may cause model-owned rework, replacement, or explicit risk disclosure, never a
backend lifecycle gate.

The installable product lives below `plugins/cortex/`. Repository-root scripts,
tests, documents, and `AGENTS.md` are development support and cannot silently
change installed runtime behavior. End users install/update through the README's
GitHub Marketplace flow. Repository developers use `./scripts/cortex-dev` for
interactive development: it creates the exact persistent `$HOME/.cortex-dev`
candidate directory, sets `HOME` and `CODEX_HOME` inside that candidate, runs
the checkout synchronization there, and starts ordinary Codex. This keeps the
stable runtime and its V12 state outside the candidate boundary. The paired
`./scripts/cortex-dev-reset --confirm` helper removes only that exact dedicated
candidate and refuses the active HOME, repository, broad paths, symlinks, and
non-regular entries. Direct `./scripts/sync-cortex.sh` use remains an explicitly
authorized local-source operation; source-mode checks do not prove an installed
cache or interactive host behavior.

V12 state uses a new namespace. V11 databases are not opened, migrated,
deleted, or modified. V11 public tools and unfinished V11 tasks are incompatible
with V12. Historical V11 state is never an authorization source, fallback
identity, or recovery surface for a V12 task.

## Vulnerability reporting

Report suspected vulnerabilities privately to the repository owner through the
security contact mechanism configured on the repository hosting service. Do not
open a public issue for a vulnerability that could expose credentials, private
state, personal data, cross-project content, or a reproducible exploit before a
coordinated disclosure decision.

A useful report includes:

- affected Cortex version and Codex host version;
- operating system and Python version;
- the smallest sanitized reproduction;
- whether the issue affects package validation, MCP input validation,
  idempotency, project isolation, SQLite integrity, diagnostics, prompt
  boundaries, or external approval handling;
- the expected and observed behavior;
- confirmation that no real secrets or private reports are attached.

## Release safety checklist

1. Verify the manifest is V12, the public registry has exactly eleven tools, and
   coordinator and worker catalogs are identical.
2. Verify the bundled skills make the root coordinator orchestration-only and
   delegate every source/code/config read, analysis, edit, command, test,
   verification, and conditional documentation update to workers, while
   keeping only the narrowly bounded orchestrator-owned knowledge route.
3. Verify only `create_task` accepts explicit `project_root`, the seven
   task-anchored tools require `task_ref` while entity-derived calls omit a
   redundant anchor, the versioned task/result language fields preserve the
   original request beside English durable coordination fields, delegation
   `scope` is required non-empty text, object scope is rejected, exact packaged
   `profile_name` stays distinct from human `role`, model/effort are required
   together, closure requires `subject_type` plus matching `subject_ref`,
   and user decisions bind the correct subject and digest. For a plan approval,
   verify the complete canonical decision payload: exact `response_original`,
   separate English `response_en`, `prompt_en`, `user_language`, the finalized
   plan ref/digest, and the matching ready-view handle, view digest, and source
   sequence; malformed or mixed fields must fail without a mutation.
4. Run the self-contained skill/profile lint and isolated V12 release/protocol
   test.
5. Verify schema-v1 bootstrap, concurrent mutations, idempotency conflicts,
   chunk ordering/finalization/abort and bounded report reads, cross-project
   rejection, governance and decision history, dependency warnings,
   host-private verified projection behavior, and V11 byte-for-byte
   preservation.
6. Confirm lifecycle hook code and enabled hook declarations are absent.
7. Verify the packaged maintenance CLI remains outside the eleven-tool catalog,
   uses task/shard-derived host-private targets and exact confirmations,
   validates backups before retention/restore, requires offline `MCP_STOPPED`
   restore acknowledgement, preserves canonical data during projection/backup
   cleanup, and writes neither project nor V11 state.
8. Confirm one durable delegation maps to one exact returned host spawn; the
   healthy `create_delegation` response carries root-level `native_dispatch` and
   `renderer`, with the complete worker message only once in
   `native_dispatch.native_arguments.message`; `read_delegation` is recovery
   only. Confirm `fork_turns="none"` and effort are explicit, Luna omits the native model
   override, and Terra/Sol carry theirs.
9. Run package validation, release-candidate validation, `git diff --check`, and
   `./scripts/sync-cortex.sh --dry-run`.
10. Treat ordinary interactive tmux `codex` smoke as separate installed-host
   evidence; verify that coordinator target-project reads were limited to the
   bounded knowledge route and it used no source/edit/command/test tool, never
   substitute `codex exec`, scan every child message for English-only content,
   verify worker-owned documentation-impact evidence plus exact initiative and
   closure links, and never claim an unrun smoke.
11. State every unavailable release, host, or live-model check explicitly.

See [verification.md](docs/project/verification.md) and
[release-readiness.md](docs/release-readiness.md) for exact commands and
evidence expectations.
