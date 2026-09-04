# Security policy

## Scope

Cortex 1.15.6 is an explicitly activated local coordination plugin. Its
project-isolated SQLite ledger is authority for orchestration state, not a
replacement for native Codex/user permissions. Markdown views and diagnostic
streams are derived observations, not authority. Unsupported storage and
publication formats are rejected without legacy conversion or fallback.

## Supported security boundary

The core enforces canonical project/task isolation, actor and connection
binding, closed schemas, transactional receipts, immutable graph and assignment
scope, unique ownership, dependency readiness, artifact generations and current
decision relations. The LLM owns semantic decomposition, risk classification,
profile/model/effort selection, scheduling among ready nodes and interpretation
of evidence. Neither component may invent unavailable host authority.

The coordinator delegates project inspection, edits, commands and verification.
Its direct documentation routing is restricted to known project/feature indexes
and relevant linked pages under the bundled skill's boundary. It has no general
shell, source-search or filesystem-inspection exception. Workers prefer Codebase
Memory for structural discovery; an unavailable or insufficient result permits
one bounded safe fallback, with the limitation recorded.

Every nontrivial candidate requires independent semantic graph validation.
Structural validation cannot prove semantic truth; the independent validator
must assess the actual requirements, artifact dependencies and contribution/
verification relationships. High-risk finding classifications receive the
plan-declared independent check. The backend never resolves disagreement by
guessing product meaning.

Native spawn, interruption, filesystem access, destructive changes, network,
credentials and deployment remain host/user actions. Plan approval cannot grant
permissions absent from those surfaces. Hooks observe and validate supported
host calls; they neither schedule nor asynchronously stop agents.

## Public API boundary

Host capability snapshots distinguish declarations, configuration and actual
observations from unverified capabilities. The injected CLI/Desktop adapter
contract does not grant a native execution API to the MCP backend. Snapshot
digests detect identity changes, not authenticity: observer provenance must be
verified separately. Export only sanitized schemas and capability facts into an
owner-controlled directory, never prompts, credentials or arbitrary host logs.
Passive preflight export is not native-host qualification.

The native-list observer accepts the bounded JSON-text envelope emitted by
CLI hooks as well as a decoded host object. Duplicate JSON keys and incomplete
or ambiguous projections are rejected. A tagged completed agent may establish
native quiescence, but its report text is discarded and cannot establish task
completion. A fresh complete list's interrupted status represents an aborted
turn and can establish quiescence. An unknown state remains present; an
interruption command's acknowledgement or previous-state reply is not proof.

Node ownership, not a caller recovery flag, determines the assignment transition.
A ready reconciliation node uses an ordinary claim; a selected unpublished
active owner requires fresh signed quiescence and complete scope before the
store may atomically revoke it and start reconciliation. Finite budgets and
current revision checks apply to that derived transition without exceptions.
Current state, continuation and scope projections share the same verified
native observation. Reads may show observed quiescence without persisting it;
assignment admission revalidates that evidence before committing ownership.
Native turn quiescence is not evidence that detached command processes have
exited. The separate background-mutator qualification gate remains open; do not
claim process-drain or filesystem isolation from an agent status alone.

The obligation-preserving redesign is in progress, not live-qualified. Its
source inbox primitives retain exact input privately, verify project/session-bound
signatures, preserve repeated submissions and enforce ordered one-shot consumption.
The registered root-input hook now supplies passive records for selected
execution routes; source tests cover that integration, not real-host delivery.
Capture, signing a fixture or coordinator-attributed prose is not evidence of
human authorship. Public bootstrap, source-origin qualification and independent
source review remain required before this inbox authorizes changes or closure.

Source bodies are confidential canonical input, not diagnostic telemetry. Never
copy them into progress/error output, report views or repository diagnostics.
General collection from unselected conversations is outside the plugin scope.

Incremental extraction drafts retain original requirement/criterion text,
append-only corrections and exact source ranges. They are bound to one consumed
task source and current obligation digest. A stale or foreign draft cannot be
edited. Structural coverage is not semantic review; no draft operation grants
execution or closure authority. Corrective receipts prevent delayed retries from
overwriting newer extraction. The independent review must bind both current
content and source-mapping digests before any future registry sealing.

The incremental verification journal stores command/output digests, not raw
commands or stdout. Its private receipt-ingestion boundary requires a trusted
adapter; it is not a model-callable route. A stdout-only hook observation cannot
be promoted to an observed successful process exit. Assignment consumption,
declared checks, current revision/generation and receipt signatures are checked
before a model explanation is attached. Late host observations remain historical
facts, not current verification. Reformatting a report cannot replace these
records. Native ingestion, public small-operation routing and independent
semantic acceptance remain unqualified; this journal alone proves no completion.

The complete public catalog is defined in
[`public_contracts.py`](plugins/cortex/scripts/cortex_runtime/public_contracts.py)
and contains exactly:

1. `open_task`
2. `read_task`
3. `read_state`
4. `read_scope`
5. `read_outcome`
6. `read_continuations`
7. `read_evidence`
8. `read_timeline`
9. `open_clarification`
10. `record_clarification`
11. `open_plan_review`
12. `record_plan_review`
13. `open_steering`
14. `record_steering`
15. `open_assignment`
16. `publish_plan`
17. `publish_result`
18. `publish_documentation`
19. `assess_governance`
20. `close_task`

Every operation derives arguments solely from its live advertised closed schema.
Skills, worker instructions and ordinary test workloads must not duplicate
request shapes or field tutorials. Invalid types, extra fields, unsupported
values and oversized requests are rejected before mutation.

The canonical project root enters through task opening. Subsequent public calls
use server-issued, audience-bound task references; internal row IDs, receipt
keys, report identities and digests are not caller-invented authority.
Independent additions and complete point replacements preserve requirement
provenance without merging unspecified fields or fabricating verification.

Assignments select current graph nodes, not reconstructed prose scopes or
ambiguous outcome ownership. The server renders complete immutable node scope,
predecessor evidence and expected terminal kind. Concurrent claims cannot own
the same node. Read-only auditors can run in parallel on a sealed generation;
the project-wide mutation barrier admits at most one mutator and excludes
overlapping artifact-dependent readers.

Each worker must consume its exact assignment before project work. Planning
nodes publish plans, documentation nodes publish documentation assessments, and
all other nodes publish results. Purpose, not specialist profile or edit count,
determines terminal kind. One cross-kind publication slot prevents contradictory
terminal reports. Coverage is the sole public observed-verification narrative;
plans carry expectations, not claimed observations.

Desktop initialization may lack trustworthy child identity. An unattributed
connection therefore receives a neutral complete catalogue, which grants no
execution authority. Exact host-correlated first assignment consumption binds
the worker role and capability. A retained initial catalogue cannot bypass
server-side role checks; no dynamic catalogue refresh is required for safety.
A coordinator cannot pivot into a worker. Copied references, reconnects and
sibling sessions cannot transfer consumed publication authority.

Installed hooks use the protected plugin data root. When Desktop omits data-root
environment variables, the verified installed cache topology supplies the same
root. Source mode requires an explicit isolated root. Private dispatch
correlation is session-bound; pending assignments are never matched by newest
timestamp, launch order, a foreign session or an arbitrary worker record.

Every Cortex invocation is a separate direct model-visible call. Programmatic
nesting is not an authorization route because host hooks cannot individually
validate hidden calls. A successful non-replayed dispatch permits exactly one
unchanged native spawn. Ambiguous host results require observation-based
reconciliation, not blind duplication.

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
The complete twenty-tool catalogue is additionally constrained to 65,536
bytes. It advertises the authoritative closed input contracts while keeping
optional successful-result schemas inside the runtime validation boundary, so
bounded host discovery receives every complete operation without pagination or
truncation. The bundled MCP is required at host session startup and excluded
from both programmatic code mode and deferred discovery. Direct model calls are
its only valid host surface, so a Desktop turn cannot proceed with the selected
skill but without the direct Cortex catalogue. Cortex never splits or truncates
a definition to fit a frame.

## Data handling

Never place secrets, credentials, private keys, personal data, raw diagnostics
or unnecessarily sensitive operational details in task context, reports,
assignments, decisions, fixtures, documentation, generated views or commits.
Use sanitized structural evidence and bounded summaries instead.

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

Worker communication and durable engineering narrative use English.
User decisions preserve the exact response and its language in the designated
source fields; the coordinator's presentation follows the user's language.
Do not silently translate original user evidence or introduce duplicate
translated/original fields.

Private error logs are same-user sensitive data. Inspect only a relevant
bounded tail, extract sanitized correlation facts, and never paste raw records
into chat, prompts, issues, tests or external systems. Diagnostic failures must
not turn an already committed canonical mutation into a retry requirement.

## State and filesystem safety

Each canonical project root maps to its own SHA-256-named SQLite shard below
the private Cortex runtime namespace. Directories are owner-only, and database,
WAL and SHM files are restricted to their owner. The runtime rejects symlinks,
non-regular database files, unsupported schema layouts and inconsistent project
metadata. The current schema is created directly; older databases and task-view
directories are not migrated, adopted or silently rewritten.

State includes revisioned contracts, immutable plans and candidate families,
graph nodes, assignments, terminal publications, artifact generations,
governance assessments, decision bindings, events and command receipts.
Do not edit databases or their side files manually, copy them between roots or
construct identifiers from other projects.

Consumed worker evidence has one typed assignment authority. Complete scoped
product requirements are separate context, not a competing work or check list.
Legacy assigned/planning-item projections and serialized duplicate scope
instructions are not delivered to workers. Profile policy cannot expand that
assignment or change its terminal publication kind.

Every public mutation commits its state transition, event and normalized
server-derived receipt atomically. Identical ambiguous retries reconcile the
existing result; changed retries conflict without repeating the mutation.
Idempotency does not authenticate an actor or justify an unexplained replay.
A database transaction cannot roll back an already executed native filesystem
or agent side effect.

Worker fingerprints bind evidence to the declared Git or path-manifest
boundary. The backend compares worker observations but never claims to inspect
the filesystem itself. A read-only start/end mismatch returns a static snapshot
conflict without a report or terminal-slot consumption. A mutator seals a new
generation; dependent old-generation evidence remains historical.

Fingerprint manifests are worker-owned scratch in an owner-private system-temp
directory, separated by OS user, Codex-home namespace and project hash. They
contain hashed metadata, not file bodies. The worker creates the directory with
0700 and manifests with 0600, rejecting unsafe paths and mismatched contents.
Neither the ledger nor the project is granted extra write access to make this
procedure work: ordinary workspace-write includes system temporary storage.
Loss of scratch manifests is unavailable comparison evidence, never a match;
the current recovery/reconciliation policy applies. These scratch files are not
canonical reports or part of the durable ledger backup.

Steering immediately commits a revision and invalidates prior authority,
including pending questions. The coordinator then interrupts matching stale
native workers. A racing publication returns the static superseded result
without creating evidence or authorizing a retry. No new planning or execution
may bypass the reconciliation barrier: supported native observations must prove
quiescence, then a read-only worker observes and seals the actual baseline.
Stopped-worker writes are preserved as evidence, not silently undone.

On recovery, current state precedes any required terminal continuation read.
An unavailable or partial native-agent projection cannot prove loss.
Timeouts, silence and SubagentStop alone cannot authorize replacement.
Recovery and reconciliation use finite budgets and existing lineage.

Workers must terminate bounded child processes before final fingerprint and
publication; unowned background mutators are forbidden. This is not a claim of
an OS sandbox. Closure certifies the latest observed sealed generation, not
continuous immunity to external edits. Live qualification controls external
writers across final observation and closure.

Plans and finalized reports have host-private Markdown views. Rendering,
readback, containment, regular-file and digest checks precede a ready link.
The coordinator copies the returned complete Markdown link byte-for-byte.
A post-commit view failure preserves the canonical report. One transient I/O
failure can retry the same expected bytes once inside that request, without a
second report or external-edit override. Unsafe paths, digest conflicts and
permission failures remain errors. Exact reconciliation can repair only the
projection. Unknown report formats fail rather than falling
back to a legacy envelope. Authored Markdown is inert presentation and never
parsed back into authority.

## Operator maintenance boundary

`cortex_runtime.v12_maintenance` is a local administrator CLI, not a public MCP
tool or orchestration capability. It accepts one exact V12 `task_id`, derives
the project shard from the ID, and accepts no `project_root`, arbitrary database
path, export path, or V11 target. It performs owner/mode, regular-file,
no-symlink, database-family/current-schema, project/task binding, integrity,
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

## Governance and user decisions

Governance assessment precedes the first assignment. Minimal/light complete
plans may be informational; complexity alone does not require approval.
Material high risk, genuine authority/product decisions or explicitly requested
review require a complete verified plan packet and a direct current response.

Decision-bearing alternatives are independently validated before presentation.
One answer selects an exact semantic branch and approves its complete contract
delta and graph in one transaction. Unselected branches gain no authority.
A genuine pre-plan steering question is used only when alternatives cannot
responsibly be constructed before the answer. No-op steering is invalid.

An unanswered plan packet binds its candidate, validation, artifact/barrier
state and governance epoch. Changed evidence invalidates that authority without
blocking current replanning. Direct user changes may supersede a pending
question atomically. They do not automatically require repeated review; a new
material risk/authority boundary or explicit renewed-review request does.

In-contract remediation follows validated bounded templates. Independent
regression restores only verified capabilities and preserves original failed
reports. Scope/authority/risk-changing findings cannot silently authorize
ordinary repairs. Non-progress and exhaustion are explicit incomplete states,
not opportunities to fabricate success or ask unnecessary continuation questions.

Every closure attempt requires the verified result, documentation impact,
risks and unrun checks to be presented, followed by a fresh revise-or-close
question and the direct current close choice. Backend graph, evidence,
generation and freshness gates remain necessary. Incomplete work cannot be
closed, including after an explicit close answer. Report the unresolved work
without writing a closure; a risk-bearing verdict cannot waive mandatory checks.

## Model and profile integrity

The coordinator selects a packaged profile as immutable assignment policy,
not a native agent type or authority grant. There is no degraded non-durable
profile fallback. The expected publication remains determined by node purpose.

Luna is the default for most work, with effort through max. Native dispatch
omits its model override and requires the host default to be configured as Luna.
Terra is reserved for genuinely complex planning/architecture. Sol is rare and
reserved for materially risky security work. Both use explicit native model
overrides. Every worker has explicit effort no higher than max; ultra is never
an orchestration route. The backend validates but never silently escalates or
substitutes the coordinator's choice.

## Lifecycle and hooks

Activation and lifecycle hooks enforce supported bootstrap/recovery ordering
and record bounded private correlation facts. Hook output, native commentary,
stop, resume and compaction events are not terminal publication evidence.
Fresh-worker authority comes from exact assignment consumption and connection
binding. Worker compaction rereads only that same immutable assignment through
its existing authority; it does not mint a replacement capability.

Host-confirmed native quiescence plus ledger evidence supports loss recovery.
The coordinator owns spawn, wait, list and interruption calls. Backend
readiness is distinct from host slot capacity; waiting for capacity cannot
justify duplicate assignment or premature audit.

## Bundled skill and plugin integrity

Installed skills, profiles, schemas, hooks and runtime below plugins/cortex are
the product contract. Repository development documents do not silently alter an
installed instance. The advertised schemas own call shapes; skills describe
policy and meaning. Immutable assignment evidence owns project work scope.

Documentation edits precede final verification when they mutate the artifact.
Final verification and read-only documentation-impact assessment bind the same
latest sealed generation. No-impact claims require actual worker evidence;
unavailable inspection is not proof of no impact.

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

Real Desktop live development uses `./scripts/cortex-desktop-dev`. It prepares
the same isolated candidate, launches the actual Desktop binary with a
disposable Electron profile, and does not write the stable Codex profile or
stable Cortex plugin. CLI/Desktop parity evidence is valid only for consecutive
real-host runs of one unchanged cache-stamped payload; a payload edit
invalidates both live results.

Production and isolated development installations share one fail-closed package
identity rule. Their plugin manifest carries
`1.15.6+codex.sha256.<digest-prefix>`, and the MCP process recomputes the complete
normalized plugin-tree digest before answering `initialize`. Plain `1.15.6` is
accepted only when source mode is explicitly enabled; an explicitly source-mode
checkout may also retain its last stamped suffix while edited, but reports
`parityVerified=false`. Installed and candidate runtimes remain strict, and a
plain or stale stamp is never a publishable Marketplace artifact. Release validation also enforces Desktop metadata limits,
including a 128-byte `defaultPrompt` and a maximum three-second `SessionEnd` hook
timeout, so host clamping or ignored metadata cannot conceal package drift.

Historical installations and databases remain untouched and are never a
fallback identity, migration input or recovery surface for the current runtime.

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

1. Verify the complete twenty-operation registry, closed schemas and
   server-enforced actor/connection isolation, including retained catalogues.
2. Verify fresh activation, governance-before-assignment, typed DAG readiness,
   unique contribution ownership, compatible parallel readers and mutation
   exclusion.
3. Verify exact native dispatch, first assignment consumption, purpose-bound
   terminal kind, one publication, default Luna routing and no ultra.
4. Verify current-only storage, atomic command receipts, rollback, changed-input
   conflict and ambiguous-response reconciliation without duplicate spawn.
5. Verify independent candidate validation, all alternatives, one-answer
   selection, changed review evidence and direct in-flight user steering.
6. Verify finite remediation, independent regression, stale publication,
   artifact conflict, native reconciliation and recovery ordering.
7. Verify protected filesystem boundaries, private sanitized diagnostics,
   verified Markdown links and post-commit projection repair.
8. Verify offline-only maintenance, exact confirmations and protected backup/
   restore targets; never mutate stable user state during development tests.
9. Run package validation, source regressions, git diff --check and read-only
   sync checks on a current cache-stamped payload.
10. Run the required ordinary interactive CLI qualification, then consecutive
    real Desktop qualification on the unchanged payload. Never substitute
    codex exec, synthetic leases or source MCP calls for native live evidence.
    Inspect every worker's first publication and actual model/effort route.
11. Require current result presentation and explicit closure review. State all
    unrun or failed gates; never treat partial Desktop execution as a pass.

See [verification.md](docs/project/verification.md),
[release-readiness.md](docs/release-readiness.md), and the authoritative
[Completion checklist](docs/project/typed-orchestration-integrity.md#11-completion-checklist).
