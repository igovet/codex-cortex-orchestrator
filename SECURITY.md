# Security policy

## Scope

This repository contains the Cortex 1.14.12 Codex plugin. The V12 runtime is
explicitly opt-in, runs locally, and stores coordination state in a private,
project-isolated SQLite schema-v1 ledger. Cortex is a durable coordination
sidecar, not an authorization service or workflow engine. Canonical
coordination state remains in the private ledger; any Markdown file is a
derived, host-private human view rather than another source of authority.

## Supported security boundary

Cortex treats the following as authoritative:

- the exact resolved `project_root` supplied only to `open_task`, with its
  saved canonical `task_id` retained as evidence and compact `task_ref`
  anchoring later task-anchored public calls;
- project isolation derived from the SHA-256 digest of that resolved root;
- SQLite transactions, uniqueness, foreign keys, and schema-v1 metadata;
- stable task, delegation, report, assessment, initiative, closure, and
  user-decision IDs;
- strict public input schemas, enumerations, size limits, and reference checks;
- server-derived operation identities and normalized-payload digests for
  semantic report publication replay;
- private/internal immutable report chunks and finalized/aborted report assemblies, append-only
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

The complete semantic registry contains fourteen tools, but every MCP
connection receives an immutable audience projection. Coordinators receive
coordinator operations plus `read_task`; a signed worker-candidate or committed
worker receives only `read_task` and the three worker publication operations.
The worker spawn receives one compact closed native dispatch; it is neither the
full worker policy nor ledger authority. The mandatory first assignment read
supplies the full common policy, profile guidance, and task evidence. IDs are
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

For routing only, the host-injected `AGENTS.md` context already governs the
task. The coordinator then reads the project and feature indexes and
task-relevant pages selected from those indexes. The
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

The canonical project root does not attest Git-worktree capability. Workers
must establish that capability with a bounded, failure-normalizing probe before
any Git command. An unsupported or non-Git root is recorded as a clean observed
state and causes Git-dependent inspection to be skipped, not speculatively
executed with a nonzero failure.

For structural project-code discovery, every worker starts with the enabled
Codebase Memory MCP bound to the canonical project root. Missing, disabled, or
unusable Codebase Memory is an environment blocker, not a fallback condition.
One bounded ordinary-search fallback is permitted only after an actual graph
call proves that the indexed graph excludes the requested surface or is
insufficient; the worker records that rationale and scope. Silent or chained
fallback is a contract violation. The coordinator is denied operational access
to the shared Codebase Memory namespace.

## Public API boundary

The complete public catalog is defined in
[`public_contracts.py`](plugins/cortex/scripts/cortex_runtime/public_contracts.py)
and contains exactly:

1. `open_task`
2. `read_task`
3. `open_clarification`
4. `record_clarification`
5. `open_plan_review`
6. `record_plan_review`
7. `open_steering`
8. `record_steering`
9. `open_assignment`
10. `publish_plan`
11. `publish_result`
12. `publish_documentation`
13. `assess_governance`
14. `close_task`

Every tool has a closed input object. Runtime validation consumes the same
schema object advertised by `tools/list`. Unexpected properties, invalid
types, unsupported enumeration values, oversized fields or arrays, invalid
identifiers, and invalid continuation values are rejected before a service operation.

Only `open_task` accepts the exact resolved `project_root`; it is the sole
public project-root boundary, stores the canonical project association, and
returns preferred `task_ref` plus canonical `task_id`. The seven
task-anchored public tools require `task_ref` and resolve it fail-closed to the
saved canonical task. The 14-character reference contains only a 12-hex task
suffix; resolution scans private V12 shards and rejects zero or ambiguous
matches. Full `task_id` remains durable database evidence, not a public request
locator; every public operation is task-ref-only and private assignment/report
references never become caller-supplied locators.
Initiative operations use the resolved task only as a project locator, never
as authority. Current MCP call
metadata does not provide a guaranteed project-root binding, the plugin stdio
process `cwd="."` names the installed plugin directory rather than the target
project, and V12 has no lifecycle hook that binds a session root. The native
worker brief carries the saved root only for working-directory context.

Before `open_task`, the coordinator must read every available user-supplied
attachment or pasted specification and normalize all decision-bearing details
into the semantic outcomes, acceptance criteria, constraints, and verification
contract. Exact limits, identifiers, named handlers/fields, states, negative
requirements, external boundaries, edge cases, and verification expectations
must not be replaced by a summary such as “strict” or “as specified”.
`open_task` records one versioned task/result contract: English-normalized
`objective` for internal coordination; exact arbitrary-Unicode
`user_request_original`; `user_language`; `task_contract_version`; bounded
English `requirements`, `constraints`, and `acceptance_criteria`; an outcome
contract that links each acceptance criterion to its requirement; an empty
independent verification plan that is never derived by copying acceptance; and
optional bounded JSON `context`. Original user wording
is preserved and never replaced by the normalization. The result contract is
not a backend execution plan or permission boundary. Optional `context` never
supplies or overrides the root. `open_assignment.scope` is a required
non-empty text string of at most 65,536 characters: it is the concise boundary
of worker ownership, detailed execution belongs in `instructions`, and an
object-shaped scope is invalid. Delegation `model` and `reasoning_effort` are
required together and retained exactly. `profile_name` is an exact packaged
enum distinct from the bounded human-readable `role`; it selects expertise, not
ownership. The public mission's explicit `responsibility` selects planning,
delivery ownership, or non-owning evidence. Delivery/evidence use exact current
semantic outcome selectors; planning scope is server-derived as the complete
current effective contract and cannot be narrowed or reconstructed by the
caller. Private exact `item_refs` bind the scope that must be reconciled.
The public state projection also emits canonical responsibility-specific
assignment selectors. Delivery can use only `delivery_outcomes`; terminal-owned
outcomes are separated with `terminal_rework=steering_revision_required` and
cannot become corrective delivery scope until an explicit steering revision
creates new unowned outcomes. Evidence remains selectable without acquiring
delivery ownership. Renderer proof must be loaded. The returned
closed native dispatch preserves the compact bootstrap and exact selected
model/effort for one matching host spawn. This semantic delegation
receipt proves packaged profile and semantic dispatch data, not host lifecycle.
Host-side one-shot dispatch correlation is isolated below a digest-named
coordinator-session directory. A mode-0600 atomic active index contains only
pending, delivery-pending, or worker-bound receipts and is updated under the
same session lock as each lifecycle transition. Completed and foreign-session
history, timestamps, and content-hash filename order are never routing
authority; malformed or ambiguous active state fails closed for that session.
Settled diagnostic history is independently capped at 64 receipts per session
and cleanup is never a precondition for routing correctness.
Normal spawning consumes that receipt directly. A successful non-terminal
assignment page keeps the already claimed authorization on that same bound
child and persistent MCP connection until the terminal page; it never creates
or claims a second authorization. Assignment continuations never independently
attest lifecycle and cannot transfer worker authority to another connection.
Native commentary alone is never durable progression: a lost child requires
explicit blocked/aborted evidence and an atomically linked successor, while a
completed child requires a finalized report. `close_task` records a task-scoped
advisory closure from durable evidence and does not gate safe work or a truthful
user-facing answer.

Worker publication authority is established only after terminal consumption of
the exact server-owned assignment view on one signed, host-bound MCP
connection. Current Codex Desktop supplies no trustworthy connection-specific
identity in MCP initialize parameters or environment: root and child processes
are indistinguishable at that boundary. Cortex therefore never selects an
initial audience from a global fresh worker record. Every unattributed
connection starts with a neutral complete catalogue and an uncommitted role;
the catalogue grants no call authority, and an unrelated pending/candidate
receipt cannot select a new root audience.
`SubagentStart` creates a one-shot digest-only attestation bound to the exact
child agent/session/assignment, and `PreToolUse` authorizes the exact first
assignment read without rewriting it. Only that otherwise-uncommitted
connection may atomically claim the authorization. The server validates the
read against its worker-candidate schema, fixes the assignment view, and
commits worker role only after successful terminal assignment consumption.
It then emits the standard `notifications/tools/list_changed` notification;
a supporting client refresh receives only `read_task` and the three worker
publication tools. A client that retains the neutral catalogue remains
fail-closed because committed server role checks reject every coordinator-only
worker call without mutation. A confirmed coordinator can never pivot. Pre-consumption
catalogue hiding cannot be authenticated until the host supplies identity at
initialize; server authorization and the activation hook remain authoritative
during that unavoidable discovery window.
Hook processes address the owner-only package data root through `PLUGIN_DATA`.
An installed MCP process uses an explicit `PLUGIN_DATA` or `CODEX_HOME` when
available; ordinary Desktop may forward neither, so the process otherwise
derives the same fixed data root from the already verified content-addressed
cache topology. Source mode has no installed topology and must supply its
isolated data root explicitly. A missing GUI environment variable therefore
cannot silently disable the host-attested worker claim.
Coordinator and worker roles are
monotonic per connection. A fresh process,
reconnect, copied worker locator, report reference, bare assignment reference,
or durable continuation cannot rehydrate or transfer consumed publication
authority. Context compaction does not create another bootstrap path: the
already-bound worker connection may reread only its same immutable assignment,
with exact page-receipt reconciliation and publication gated until that read is
terminal again. This read-only recovery grants no new identity or authority;
every fresh or copied connection remains rejected. After a compact lifecycle
event, the activation guard rejects coordinator mutations until a fresh
current state read succeeds and rejects worker publications until the same
bound connection completes its terminal assignment reread. The guard also
rejects any Cortex invocation hidden inside programmatic `exec`, for both
coordinator and worker routes, because host hooks cannot authorize or observe
those nested calls individually. Every Cortex operation must therefore remain
one separate direct model-visible call. Steering also has server-side,
same-connection admission: its successful
opening invalidates earlier state-read evidence and its record consumes one
later state read. The host audience receipt
is owner-only and digest-only: it carries
no task/worker locator, native message, assignment body, or bearer secret.
Unconsumed, consumed-on-another-connection, foreign, stale, partial, or
mismatched relations all fail closed without a report mutation or role change.
Worker failures are explicit: `assignment_not_consumed`, `wrong_connection`,
`connection_lost`, `assignment_stale`, and `publication_conflict` each carry a
bounded action specific to that state.

Confirmed loss uses an explicit successor workflow, never authority recovery.
The coordinator must record a `blocked` or `aborted` reason with non-empty
evidence. Cortex derives one unique current predecessor from the exact selected
outcomes and atomically stores immutable loss evidence, stales the old worker
lease, creates the successor, and links the lineage. Broad report inputs remain
evidence and never override that exact recovery predecessor. Timeout, lease
expiry, silence, reconnect, repeated waits, slow progress, or missing lifecycle
telemetry is not loss evidence and never justifies interrupting an active child.

Closure review is distinct from ordinary clarification. After the current
ordinary clarification is opened, its matching record omits outcome because
the pending binding already identifies an answer; supplying `revise` or
`close` against that binding is rejected without mutation. After the current
result is presented, exactly two localized choices are offered: revise the
same task or close it. Revision preserves the same `task_ref`; any later
assignment, report, or decision makes an earlier close choice stale. The
public `close_task` boundary atomically requires the current consumed close
choice and rejects missing, reused, or stale choices. Internal advisory storage
may remain policy-neutral; this public rule protects the close boundary without
turning closure into a work scheduler.

The narrow decision operations are the durable record of ordinary-chat
decisions. `record_clarification`, `record_plan_review`, and
`record_steering` each consume only the matching server-issued binding and
accept their operation-specific task-scoped fields. Binding, subject, digest,
and decision identity are derived privately from the open operation; callers do
not supply subject refs, report refs, delegation refs, or digests. The exact
original-language response and `user_language` are retained. Missing, legacy,
or mixed fields are rejected before the service mutation. The record may
supersede a prior decision on the same subject, but it never replaces original
wording or acts as a bearer approval token.
The mutation response is compact and never repeats the private
`response_original` value.

Expected failures return bounded error codes and structural details only.
Errors must not contain task objectives, delegation instructions, report
content, governance notes, personal data, credentials, raw exception state,
database rows, or private filesystem content. Unknown runtime failures collapse
to a sanitized ledger or validation error.

For example, an unsupported publication field returns bounded structural
diagnostics naming the field and expected advertised value without echoing the
supplied value. Any schema validation failure leaves the ledger unchanged.

Aggregate encoded-size diagnostics are likewise value-blind. They may expose
the root path, bounded numeric actual/maximum byte counts, and sizes of known
advertised top-level sections, but never caller text, arbitrary keys, task
content, filesystem paths, handles, revisions, digests, or private ownership
identity. A root failure never invents a named field. Publication correction is
limited to one materially changed complete request on the same worker
connection; unchanged, incomplete, still-oversize, second, and ambiguous
attempts fail closed before durable mutation.

The stdio transport bounds one JSON frame at 256 KiB. An oversized frame is
fully drained and returns a sanitized parse error so the next valid
`ping`/`tools/list` request can succeed; it must not desynchronize the server.
The complete fourteen-tool catalogue is additionally constrained to 65,536
bytes. It advertises the authoritative closed input contracts while keeping
optional successful-result schemas inside the runtime validation boundary, so
bounded host discovery receives every complete operation without pagination or
truncation. Cortex never splits or truncates a definition to fit a frame.

## Data handling

Do not place secrets, credentials, access tokens, private keys, personal data,
raw diagnostic logs, or unnecessarily sensitive operational details in:

- task objectives or context;
- delegation scope or instructions;
- worker reports or report handoffs;
- governance rationales, risk factors, initiatives, closures, or follow-ups;
- prompts, fixtures, tests, documentation, issues, commits, or generated views.

The isolated live-dev MCP observation journal is an owner-only bounded
diagnostic surface, not durable ledger evidence. It may retain only safe
operation/outcome metadata, an optional registry-safe failure fault, build
identity, and one-way anchor fingerprints. Its internal outcome vocabulary is
not a public MCP error-code namespace.
After a successful physical MCP initialization reply, one registration-only
`server_ready` observation may additionally retain the verified build identity
and a count plus one-way digest of the advertised catalogue. It must not retain
tool names, definitions, request content, server paths, or host diagnostics.
It must never retain raw references, request arguments, responses, prompts,
reports, native task names, host messages, continuation capabilities, project
paths, secrets, personal data, or raw exceptions. Symlinks, non-private modes,
and oversized/corrupt journal state are observation failures. They must not
change a successful canonical MCP mutation into a failure or trigger a retry;
the live verifier records the resulting observation limitation. The runtime
opens the isolated `CODEX_HOME` root and all journal descendants with a
no-follow descriptor chain; it rejects a symlink, wrong owner, or wrong mode
at any such ancestor and never creates a missing arbitrary `CODEX_HOME` root.

Use English for every native worker commentary/update, inter-worker message,
final response, tool-authored durable string, objective, requirement,
instruction, worker-authored report narrative, and governance record. Decision
records instead use neutral `prompt`, exact `response_original`, and
`user_language`; retired `prompt_en` and `response_en` fields are rejected. Acceptance covers complete child threads, not only
final messages or database rows. Canonical product-facing reports and handoffs
may carry one optional unchanged `source_text` value as inert source material,
without a language tag or translated/original duplicate. Existing task and
decision contracts preserve exact arbitrary-Unicode user wording in their
designated `user_request_original` or `response_original` fields with
`user_language`; never overwrite or silently translate that source text.

Reports may contain material engineering evidence, so private ledger references
must never be copied into public requests or treated as capabilities. The three
worker-owned publication operations accept one complete terminal plan, result,
or documentation outcome for the worker's task-scoped assignment. Their public
receipt contains only `task_ref`, `state`, and `replayed`; private report IDs,
digests, and assembly state remain server-owned. A plan must declare
`informational` or coordinator-owned `required` review policy without creating a
backend gate.

Private/internal canonical report storage supports historical v1 schemas for
all four report types and the
additive v2 result, synthesis, and plan schemas. V2 adds structured coverage
for assigned effective-contract items plus deviations, unresolved items, risks,
and verification. The immutable original task contract is represented as a
revisioned effective contract with stable `o_` item references. Delegations may
own, contribute to, or produce evidence for an item; each active item has no
more than one owner. Aggregate coverage reports missing, partial, unverified,
stale, or contradictory evidence. A user steering decision revises only the
named active items; it does not rewrite unrelated evidence. This evidence and
the linked conformance projection guide model reasoning but never become a
backend authorization or lifecycle gate.

Required-review plans are revision-bound evidence. The server exposes a plan as
active, opens its review, and admits light/full delivery only when the plan's
planning-assignment snapshot matches the current effective-contract revision.
A material steering revision therefore invalidates reuse of every earlier plan
approval without deleting or rewriting its audit history.

The public aggregate projection binds each row to the exact semantic outcome
and supplies explicit `ownership` plus `delivery_assignability`. `assignable`
means an ordinary delivery owner may claim the currently unowned outcome;
`loss_recovery_only` identifies a nonterminal current owner and does not itself
prove loss; `not_assignable_terminal_owner` preserves finalized ownership and
cannot be reassigned. These values are a safe routing projection, not an
authorization grant: the transactional admission check remains authoritative
and rejects mixed, stale, retired, duplicate, or otherwise conflicting scope
without mutation.

The current V3 specialist envelope is admitted before terminal finalization.
It requires an exact one-to-one disposition for every independent outcome in
the immutable assignment scope, observable evidence, and residual
risks/deviations/unresolved items. Acceptance, verification, constraints,
steer additions, and source fragments remain linked metadata instead of
separate coverage obligations.
worker bootstrap provides a server-owned ordered reconciliation template and
count/reference receipt, but deliberately supplies no status or verification
claim. The worker must preserve and complete that row set before its first
publication attempt, so completeness enforcement cannot fabricate evidence.
The exact template is also the first model-visible `TextContent` block for an
assignment result and remains present when the larger serialized result falls
back to `structuredContent`. Publication resolves the complete ordered row set
in one validation pass, so a rejected outcome identifies its actual array
position instead of resetting every diagnostic to index zero.
Each steer addition targets an active outcome and produces a source-grounded
replacement revision; it cannot create an unlinked parallel item. Compatible
repeated rows for one item are losslessly coalesced only when their
status agrees, preserving all unique verification facts. Conflicting repeated
statuses, missing items, and foreign items are rejected before the terminal
publication slot is consumed.
Result and synthesis evidence also require a documentation-impact decision. A
planner receives the exact full current independent-outcome catalogue in its
semantic brief and maps each outcome exactly once with its linked criteria and
provenance. Ordered plan stages also identify an owner, earlier dependencies,
work, and verification. Predictable structural or mapping failures leave the
same report assembling and consume no terminal result slot; V1/V2 history stays
immutable and readable.

Ordinary task reads create no receipt or lifecycle fact. `read_task` accepts
only its advertised task-scoped view (`state`, `assignment`, or `evidence`),
with `continue=true` for the immediately preceding bounded read. The server
retains the continuation privately; callers do not supply report refs,
private assignment/publication references, private cursors, or caller replay keys. Native
handoffs are routing context, not semantic authority. Private/internal report
assembly and ledger continuation state remain inaccessible through the public
facade.

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
path-bearing `open_task` open automatically upgrades only the exact released
pre-human-view V12 shape through the complete ordered additive history ending
at `v12-effective-outcome-coverage` in one transaction; it preserves legacy
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
- immutable `user_decisions` with neutral `prompt`, exact `response_original`,
  and `user_language`; retired `prompt_en` and `response_en` fields are rejected;
- the ordered `timeline`;
- operation-scoped `idempotency` records;
- bounded projection job and projection-file metadata; and
- minimal schema and project metadata.

Writes use transactions; concurrent first-use bootstrap is serialized and
normal concurrent mutations use SQLite's write reservation and WAL behavior.
Do not edit `cortex.db`, WAL/SHM files, or metadata directly. Do not copy a
database between project roots or synthesize IDs by scanning other project
directories.

Every mutation requires a caller-generated idempotency key. A repeated
operation/key with the same
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

After task creation and before the first assignment, the coordinator must
append one evidence-backed governance assessment. The model owns mode selection, reassessment, initiative state, dependency
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

Every light or full delivery assignment requires a current finalized plan with
server-derived `review_policy=required`; the coordinator opens the matching review, presents
the verified plan, and records an explicit approval bound to that exact report
and digest before dispatching delivery work. The backend enforces this narrow
pre-dispatch relation while leaving planning/evidence assignments available.
Minimal informational plans are permitted only when no
material product, scope, external, destructive, security, privacy, or risk
decision remains. This admission invariant never schedules work or authorizes
external, destructive, or scope-expanding action.

Initiative status is limited to `proposed`, `active`, `paused`, `completed`,
`closed`, and `cancelled`. The status is informational. An existing initiative
is revised only for a material goal, dependency graph, risk, status, parent, or
cross-task change; ordinary delegation stage/rework, report, decision, and
notes churn remains in the task timeline. Parent, dependency, task, and report
links remain project-scoped. Missing or cyclic dependencies are returned as
warnings; they do not block a later material revision or closure.

Closure verdicts are `ready`, `ready_with_risks`, and `not_ready`. They are
model-requested recommendations. The ledger never upgrades a request and
normalizes an overstated verdict downward to current conformance, returning both
requested and recorded values without selecting the next stage. A `not_ready` task can receive another
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

Native dispatch serialization must preserve isolated history and the exact
selected effort. Luna omits the model override so the configured default is
used; Terra and Sol carry their exact model override. The backend validates
this transport boundary but never chooses or silently replaces a pair. Cortex
has no server-owned model escalation or recovery ladder.

## Lifecycle and hooks

Cortex V12 ships bounded activation and lifecycle-observation hooks. They
correlate the exact server-issued native dispatch, enforce bootstrap ordering,
and emit sanitized observations; they do not grant ledger authority or prove
completion. Hook output, hook trust, host metadata, `SubagentStop`, coordinator
stop, session resume, and compaction events are not completion evidence.

There is no mandatory server-owned spawn/wait/read/continue lifecycle. A native
wait is an ordinary model/host coordination action. Each successful durable
delegation returns one exact compact closed native dispatch, which Codex forwards
once to exactly one matching active host spawn and then awaits that worker's own
report. The ledger does not launch or bind native agents. An
ambiguous spawn is reconciled by exact host handle, never blindly duplicated; a
reportless result alone never authorizes replacement of the same delivery
scope. A successor requires explicit blocked/aborted reason and evidence.

## Bundled skill and plugin integrity

The installed `orchestrator` and `cortex-control` skills are the authoritative
runtime model contract. Delegation assignment data remains bounded task
context, while agent profiles provide advisory role guidance. Briefing
conciseness must never cause material user intent, risk, decisions, report
references, applicable project-knowledge requirements, or verification results
to be omitted.

The orchestrator alone owns the exact knowledge-route paths and six-part
contract template. The coordinator embeds the compiled contract in
`instructions`; the native brief is only compact bootstrap context. Profiles
consume the full supplied contract after the mandatory assignment read,
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
material/no-impact rationale. The coordinator confirms that finalized evidence
through the bounded `read_task` evidence view before task closure. Private
report identity may remain in ledger evidence but is not a callable public
locator. A self-asserted
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
stable Cortex runtime and its V12 state outside the candidate boundary. To make
the required worker MCP real in live-dev, the launcher projects only the safe
production Codebase Memory server settings and gives that external child its
owning production HOME; it rejects arbitrary env/header/URL credential forms
and never changes the production config. The paired
`./scripts/cortex-dev-reset --confirm` helper removes only that exact dedicated
candidate and refuses the active HOME, repository, broad paths, symlinks, and
non-regular entries. Direct `./scripts/sync-cortex.sh` use remains an explicitly
authorized local-source operation; source-mode checks do not prove an installed
cache or interactive host behavior.

Production and isolated development installations share one fail-closed package
identity rule. Their plugin manifest carries
`1.14.12+codex.sha256.<digest-prefix>`, and the MCP process recomputes the complete
normalized plugin-tree digest before answering `initialize`. Plain `1.14.12` is
accepted only when source mode is explicitly enabled; an explicitly source-mode
checkout may also retain its last stamped suffix while edited, but reports
`parityVerified=false`. Installed and candidate runtimes remain strict, and a
plain or stale stamp is never a publishable Marketplace artifact. Release validation also enforces Desktop metadata limits,
including a 128-byte `defaultPrompt` and a maximum three-second `SessionEnd` hook
timeout, so host clamping or ignored metadata cannot conceal package drift.

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

1. Verify the manifest is V12, the complete registry has exactly fourteen
   tools, and coordinator/worker `tools/list` projections are disjoint except
   for `read_task`.
2. Verify the bundled skills make the root coordinator orchestration-only and
   delegate every source/code/config read, analysis, edit, command, test,
   verification, and conditional documentation update to workers, while
   keeping only the narrowly bounded orchestrator-owned knowledge route.
3. Verify only `open_task` accepts explicit `project_root`, every public
   operation is task-ref-only, the versioned task/result language fields preserve the
   original request beside English durable coordination fields, delegation
   `scope` is required non-empty text, object scope is rejected, exact packaged
   `profile_name` stays distinct from human `role` and mission
   `responsibility`, every assignment requires an explicit non-empty exact item
   scope that is reconciled one-to-one, model/effort are required
   together, closure accepts only its task-scoped verdict/evidence fields, and
   user decisions consume their matching server-issued bindings. For a plan
   approval, verify the exact `response_original` and `user_language` through
   that binding; malformed or mixed fields must fail without a mutation.
4. Run the self-contained skill/profile lint and isolated V12 release/protocol
   test.
5. Verify schema-v1 bootstrap, concurrent mutations, server-owned replay
   reconciliation, bounded task reads, cross-project
   rejection, governance and decision history, dependency warnings,
   host-private verified projection behavior, and V11 byte-for-byte
   preservation.
6. Confirm the packaged activation guard and lifecycle observer are present,
   every declared callback uses the shared host-resolved `python3 -B`
   contract without an absolute interpreter path, and `PreToolUse` plus
   `PostToolUse` bind the lifecycle observer only through the exact `^Agent$`
   matcher. Verify that hooks carry host lifecycle attestation only and never
   replace the server's independent identity, schema, isolation, or mutation
   checks.
7. Verify the packaged maintenance CLI remains outside the fourteen-tool semantic catalog,
   uses task/shard-derived host-private targets and exact confirmations,
   validates backups before retention/restore, requires offline `MCP_STOPPED`
   restore acknowledgement, preserves canonical data during projection/backup
   cleanup, and writes neither project nor V11 state.
8. Confirm one durable delegation maps to one exact returned host spawn; the
   healthy `open_assignment` response carries one compact closed native
   dispatch and replay state. Confirm that Codex forwards the projection to its
   active spawn operation without treating
   host argument names, lifecycle, model availability, or sandbox state as
   Cortex authority.
9. Run package validation, release-candidate validation, `git diff --check`, and
   `./scripts/sync-cortex.sh --dry-run`.
10. Treat ordinary interactive tmux `codex` smoke as separate installed-host
   evidence; verify that coordinator target-project reads were limited to the
   bounded knowledge route and it used no source/edit/command/test tool, never
   substitute `codex exec`, scan every child message for English-only content,
   verify worker-owned documentation-impact evidence plus exact initiative and
   closure links, exercise lossless multi-finding handoff and truthful verdict
   normalization, and never claim an unrun smoke.
11. State every unavailable release, host, or live-model check explicitly.

See [verification.md](docs/project/verification.md) and
[release-readiness.md](docs/release-readiness.md) for exact commands and
evidence expectations.
