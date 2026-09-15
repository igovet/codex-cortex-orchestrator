# Security policy

Incident coordination preserves production safety, rollback and exact deployed
identity. Quality diagnostics remain advisory and grant no host permissions.
Coordinators may inspect project evidence; project mutation stays with its owner.
Discovery ownership and Codebase Memory priority are model guidance, not access
controls. Graph limitations permit bounded native fallback. Live helpers preserve
the isolated graph configuration without modifying stable settings.
Decision-model and English-only guidance never terminate workers or deny calls.
The observer retains a literal unary file predicate's exit 1 as `predicate_false`,
not a command crash. Separately, a complete failed read of one exact registered
skill or declared Markdown reference may remain a diagnostic only after independent
successful assigned-skill and final-outcome evidence establishes its safe resource
identity. Both interpretations preserve raw receipts and independent required-artifact,
completion, mutation and path-policy checks; unsafe, private/project, mutable,
ambiguous, replayed, truncated or unregistered access remains audit-critical.
See [incident outcomes](docs/project/incident-outcomes.md).
The observational collector recognizes only a bounded literal word filter over
public tool names/descriptions, not arbitrary JavaScript. Publication identity
comes from public result roots; nested advisory IDs cannot substitute for it,
and conflicting result copies remain ambiguous evidence.
Current lifecycle behavior supersedes the historical pre-dispatch enforcement
descriptions below: hooks never return a runtime veto. Proposed policy denials
are retained as diagnostics with `runtime_enforcement=none`, not prevented-action
receipts. Codex sandbox, permissions and approvals remain authoritative; storage
and audit still report invalid or incomplete evidence rather than invent success.
Terminal qualification also requires a successful exact native `wait_agent` recorded
after each corresponding native spawn before an idle-composer result is admissible.
When the host supplies wait target metadata, it must include the spawned worker;
when that metadata is unavailable, only a post-spawn owner-bound completed wait is
supported. This evidence rule is an audit boundary, not a host continuation
primitive: hooks remain unable to resume a stopped host turn.

## Stable release status

The current 1.16.0 source candidate is `1.16.0+codex.sha256.5445b95361d15a5e`
(payload SHA-256
`5445b95361d15a5e1a5465f6644d2627024f1f5233a57119befd5b3f06326a35`).
The final CLI/Desktop qualification (`r_648c4f40dcf9`) applies only to the
superseded `e4f332d43bf38024` payload, not to this changed candidate. Its package,
sync, full-suite, and real-host qualification remain required, and no efficacy,
score, promotion, delivery, or acceptance conclusion is claimed. The user-cancelled
Phase 2 outcome comparison remains non-blocking and non-scoring.

Cortex is a local private document store with advisory coordination guidance.
Native host/user permissions govern project edits, subagents, external actions
and credentials. Neither MCP nor hooks is an authorization service, an OS sandbox,
or protection against a malicious same-user process.

Host-supplied thread and parent receipts bind a task to its canonical project.
A lifecycle event's `session_id` may identify the parent of a subagent; it is never
silently treated as that child's identity. Explicit child identity and parent
linkage must agree with retained binding evidence. Missing or conflicting bindings
cannot broaden access. Author labels and artifact versions remain reported
metadata, not independently authenticated evidence.

For a host that exposes the typed `functions.exec` pre-dispatch contract, Cortex
accepts only an authenticated actor/role, explicit `pre_task` or `bound_task`
phase, trusted task/assignment relation, approved route, and a closed operand-free
nested operation envelope. Pre-task permits only typed Cortex task bootstrap;
shell, file, plugin/cache/registry, opaque, malformed, and mixed envelopes deny
atomically. The repository cannot itself make Codex invoke this seam, so the
shell-wrapper qualification launcher refuses fresh pre-task qualification without
a host-attested capability. The bounded current-host alternative is explicit
`--bootstrap-route mcp-first`: it accepts no caller receipt, file, environment,
shell, or inferred-local route as host authorization. It retains
`host_enforcement_state=unverified`, and requires the child coordinator's first
qualifying operation to be a direct public `mcp__cortex__create_task` with the exact
immutable launch-bound child root plus project, task, original-request, and
native-result receipt binding. Missing, competing, or replacement root identities
are unverified.
Pre-binding functions/terminal/file/patch/nested/private activity, a failed MCP
bootstrap, or any missing/ambiguous binding is an integrity invalidator with no shell
fallback; it is not host enforcement or a filesystem sandbox. A host execution
despite a deny receipt is an audit integrity invalidator.

Actor and role are one closed provenance pair in both phases:
`native_coordinator` must declare `coordinator` and `native_worker` must declare
`worker`. Membership in each set separately is insufficient; a cross-pair denies
before nested operation parsing or executor dispatch and retains no operand.

## P1 semantic capability and evidence contract

`plugins/cortex/policy/capability-manifest-v1.json` is a closed versioned policy
declaration, consumed by the repository runtime for bounded decisions and
evidence-admissibility checks. It does not mint a bearer token or claim installed
host enforcement. Canonical operations use a versioned semantic tuple; aliases,
encoded names, opaque descriptors, mixed resource scopes, unknown capability
versions, unverified profiles, and incomplete bindings deny fail-closed. The
build-verification profile permits only public report/task reads, runtime
execute/observe, and artifact inspection; it explicitly denies task/orchestrator
mutation, release promotion, and internal/cache/registry/plugin/debug/raw-state
classes.

Every protected receipt binds candidate payload, artifact revision, acceptance
boundary, check identity, task, assignment receipt, route, target, phase, policy
version, actor, profile/version, canonical capability, operation tuple, and resource
tuple. This full identity is the decision hash, so two capabilities in the same
qualification context cannot share a receipt. Denied, incomplete, stale, unknown,
truncated, replaced, or mismatched receipts are `unverified` and cannot be accepted
evidence. A host-attested receipt can describe host enforcement, but the
repository cannot create or locally verify that attestation: local helpers emit
only `policy_declared` or `policy_observed`, and any supplied `host_enforced`
label is unverified. Token authenticity and filesystem,
process, and egress isolation remain host/platform responsibilities.

Each canonical project owns one SQLite store at
`.codex/cortex/cortex.sqlite3`; separate projects do not share this metadata
boundary. SQLite stores metadata, relationships, digests and receipts. Report
bodies and original messages are private Markdown files under
`.codex/cortex/<task>/`. Drafts use project `.cortex/`. Reports, source files, attachment references and
metadata may contain private information: do not commit, export or log them without
user authorization. Literal credential redaction preserves surrounding constraints;
translation or summarization is not redaction. The runtime does not claim automatic
secret detection. Retrieved text is evidence, not higher-priority instructions.
Forwarded agent messages are internal evidence, not new user requests or response-
language preferences. Workers return results through their native final response;
they do not duplicate delivery through cross-task messaging tools. Progress,
questions, blockers and verification updates also stay on the native parent/subagent
channel. Workers never use app/connector task messaging, including
`codex_app.send_message_to_thread`, to contact the coordinator.
Native workers created through `collaboration.spawn_agent` are tracked only with
`collaboration.wait_agent`, `collaboration.list_agents`, `collaboration.send_message`
and `collaboration.followup_task`. Codex app thread tools (`create_thread`,
`read_thread`, `wait_threads` and `send_message_to_thread`) are for explicit
user-owned task management only, not orchestration-worker tracking.
For an active Cortex task, a `PreToolUse` hook denies the canonical
`mcp__codex_app__send_message_to_thread` operation and its direct alias before
dispatch. Because host tool events may not identify a worker, the denial applies to
the whole active Cortex task, including the coordinator; it does not claim to revoke
the connector outside Cortex-managed tasks.
The live observer treats direct, quoted-bracket and simple static-alias worker
`send_message_to_thread` calls, including known `functions.exec` wrappers, as a
forbidden orchestration outcome and fails the audit. This is bounded static
inspection, not exhaustive JavaScript evaluation. The installed Desktop provider is injected dynamically as the `codex_app` MCP
server. The candidate `[mcp_servers.codex_app] disabled_tools=["send_message_to_thread"]`
override fails bootstrap with `invalid transport`, so the launcher does not claim
a host configuration filter or shadow the provider. The active-task hook denial
enforces the exact app-message calls before dispatch, while the observer retains
post-call evidence.
English-only worker reasoning and communication do not change exact quoted source
text or the requested product language; only coordinator replies follow the user's
response language.

## Worker model policy

The coordinator must state a worker's model, effort and policy class explicitly.
Luna (`gpt-5.6-luna`) is the default and priority route for ordinary work and all
fact gathering and exploration assignments. Prefer high effort, with xhigh/max
allowed for bounded implementation when the host supports them.
Terra (`gpt-5.6-terra`) handles genuinely complex implementation and is the
default code-review route at medium/high; complexity must be stated.
Sol (`gpt-5.6-sol`) handles consequential decisions at medium and difficult,
conflicting or high-risk decisions at high; narrow security analysis may use
medium/high. It is never an implementation route merely because a change is
security-related. Security implementation uses Luna or Terra. Reviews and
verifications follow the permitted model and effort routes without automatic
escalation from the inspected implementation. Explicit user-requested
model/effort overrides are preserved and recorded; coordinator-selected other models
or efforts are diagnostic policy deviations, never runtime prohibitions.
The `review` label selects Terra by default; a narrowly scoped verification may
use Luna only when review judgment is not needed. It never inherits the inspected
implementation model or effort.

The opt-in `senior_consultant` uses Sol medium for a standard or narrow question
and Sol high for a harder question. The
coordinator keeps its own model unchanged and must state the consultation policy
class in the native assignment.

This policy governs coordinator routing and audit evidence. Storage and lifecycle
hooks do not select agents, override user requests, or grant authorization. A host
that does not expose the actual worker model/effort leaves compliance unverified.

Consultant access is report-only. Its packet names one bounded question, current
requirements revision, exact published report IDs and artifact versions, attempts,
results, and facts versus hypotheses. It may read the current task pipeline when
needed and those selected reports only, using bounded pages. It may not inspect
source/project files or indexes, run commands/tests/network/environment checks,
edit project files or the coordinator pipeline, launch agents, accept tasks, or
change the plan. Its sole write is its own draft-backed Markdown report. The
observer flags non-report tools and unbounded report catalogue access. A thin
evidence result must request exact data and name the recommended executor profile;
consultation is advisory and never establishes implementation verification or
acceptance. Repeating a consultation requires new evidence, a clarified packet or
justified deeper escalation.

The separate consultant protocol receives complete attached instructions and
does not permit shell reads even for skill loading. Role restrictions are model
instructions, with post-run enforcement by the development audit, not a host
sandbox or revoked tool catalogue. The audit checks that patch targets are ordinary
draft IDs observed as issued to that consultant; pipeline and foreign draft edits
fail. Selected-report scope is instruction-enforced; the server independently
enforces task isolation and draft ownership. Native parent messages may request
missing evidence, but the consultant may not contact other agents.
The server also rejects worker attempts to create coordinator-owned `pipeline`
drafts before allocating a file or delivery receipt; ordinary worker report drafts
remain permitted.

The optional Advisory Lane has an intentionally strict capability boundary. It
may be considered only when the live host catalogue contains an explicit
provider/server identity naming `mcp-rubber-duck` together with the exact
`ask_duck` tool. An absent, unrelated, or identity-less entry is a silent no-op;
the policy does not infer availability from manifests, configuration, package
names, cached assumptions, or generic host-equivalent names. The lane has no
runtime bridge or dependency, does not add a profile or stage, and cannot route,
mutate, verify, or accept work. Its output is advisory context only, while
permissions, project ownership, worker evidence, and coordinator acceptance
remain unchanged. This repository does not claim that the MCP is installed or
that any provider produces useful advice.

The runtime helper enforces the same boundary: only a live entry named
`mcp__rubber_duck__ask_duck` with explicit Rubber Duck identity qualifies. The
required request field is `prompt`; provider, model, temperature, and images
are optional. Packet telemetry stores only a bounded hash/status/reason, and
explicit isolated launch opt-in does not copy ambient MCP configuration or
credentials.

When native assignment bodies are encrypted in host records, the audit reports
`worker_assignment_policy_unverified` unless the exact native name
`senior_consultant`, explicit Sol medium and no conversation inheritance prove
the standard consultation route. This narrow check verifies routing and isolation,
not the hidden packet. For ordinary isolated Luna workers, the audit instead
requires an exact native activity path and validated parent edge, a registered
executor profile, a single successful complete worker-skill receipt, actual
`gpt-5.6-luna` model and allowed effort, and `fork_turns=none`. When both
requested model and effort fields are unavailable because the assignment carries
the exact boolean `assignment_content_unavailable=true` marker, that complete
actual-route join may verify effective route compliance
and records bounded provenance; it never fills or infers the hidden request. If
either requested field is visible, strict requested/observed matching remains
required. Missing, partial, conflicting or duplicate evidence, mismatched routes,
and other opaque or disallowed model routes remain unverified. Assignment
prose and rationale are never claimed as inspected. All visible worker calls
still receive the full error, access, ownership and publication audit.
Declared reference reads remain valid instruction loading without creating a second
complete skill receipt. A quoted private-cache exclusion used as an `rg`/`grep`
pattern in a simple pipeline is recorded only as a static mention; direct private
operands, unquoted globs and ambiguous shell syntax remain denied.
Recovery context explicitly preserves complete command receipts before the first
skill read. Input requirements remain in live schemas; publication instructions
must not suggest omitting fields required by the selected operation. These are
model instructions and audit evidence, not a guarantee that every future model
turn follows the protocol.

The isolated Desktop launcher therefore instructs coordinators to put the child
`model`, `reasoning_effort`, and `fork_turns` on every `spawn_agent` call. The
observer records those requested fields and still requires matching observed
native child model/effort; omitted or conflicting values remain unverified or
violations and are never inferred from defaults.
Desktop audit views are rooted at the exact submitted coordinator. Explicit events
and sessions from another recent root are excluded from that task's evidence, while
the competing root itself remains an isolation failure. This prevents cross-task
attribution without allowing a shared-profile qualification to pass.

On the current Desktop host, an observational audit may use a predeclared exact
`RESULT.md` SHA-256 and the isolated workdir as the bounded product receipt. It does
not turn unavailable worker assignment/skill/final-report attestations into host
enforcement. Historical non-dispatch skill denials, polling snapshots, or failed
side-effect-free artifact checks remain visible diagnostics only after the final
artifact, unique root/worker/coordinator activity, complete capture, and no-open
state are independently proven. Any executed unsafe access, mutation outside the
artifact boundary, ambiguous root, mismatch, truncation, or final pending/open work
remains an audit failure.

The coordinator may inspect project evidence through read-only file tools or a
literal bounded read. Arbitrary command execution and project mutation stay with
the assigned owner. Internal/private storage boundaries remain in effect for reads.
Installed plugin/cache/candidate paths and agent registries are not generic
coordinator evidence sources. Before public task creation, the only permitted
current-host static discovery is a complete bounded read of an exact advertised
coordinator `SKILL.md` or the public `.mcp.json`/`.codex-plugin/plugin.json`
declaration from the isolated bundled release; directories, globs, arbitrary files,
runtime/state/registry paths, and mutations remain invalid.
The only catalogue form retained as pre-binding metadata is exact value-free
`text(ALL_TOOLS)` with its complete bounded receipt. It does not permit a query,
path, content extraction, mutation, nested member call, or mixed wrapper.
Where the host omits the early worker assignment hint, the protected boundary admits
only one literal manifest-bound leaf in the closed registered worker `SKILL.md` set.
The later native result must exactly bind that profile and assignment digest; this is
not a cache allowlist and a cross-profile, arbitrary leaf, duplicate, or unmatched
receipt stays blocking.
For worker MCP unavailability, `cortex-native-worker-result-v1` is model-supplied
observational evidence only. Its closed body must match exactly one observed native
spawn, terminal wait, parent/task, registered profile and assignment digest before a
coordinator-authored `Delegation evidence: native_worker_result` report is usable.
Its artifact hashes are asserted, not independently verified; the route remains
`host_enforcement_state=unverified` and any absent, duplicate, truncated, replayed,
or competing receipt invalidates qualification.
Only within that current-host observational route, a fully correlated closed native
worker result may retain unavailable assignment-policy evidence as a non-blocking
diagnostic, and a complete public MCP result plus one later same-thread
publication may retain a missing separate server event. These are post-observation
classifications, not an authorization bypass: the exact assigned worker-SKILL receipt
remains mandatory, and failed, malformed, truncated, replayed,
private/state/registry, project-target, mutating, or ambiguous operations remain hard
integrity invalidators and cannot become accepted evidence.
Where the installed host omits task/native-result bodies, one expected root, one
parent-bound native worker/report, and one exact non-replayed coordinator artifact
reconciliation are the only opaque observational binding. Missing, duplicate,
replayed, or mismatched links remain blocking. One or more exact-equivalent failures
while inspecting a declared skill/reference leaf may be diagnostic only after that
same full outcome proof and a separate successful mandatory worker-SKILL receipt.
They require the same worker/profile/manifest-bound read identity; differing,
replayed, private, project-target, mutating, dispatched, truncated, or ambiguous
reads remain blocking and never credit a skill receipt. The model chooses any
supported ordinary read-only means. If the installed host transports the read
through a wrapper, the observer requires one attributable operation and a completely
forwarded result; nested shells, aliases, extra operations, incomplete forwarding,
directories, globs, adjacent plugin/cache files, private paths, and writes fail closed.
If the installed interactive host cannot emit bootstrap attestation, assignment-policy
attestation, a separate server event, or a process self-exit marker, that absence
is `not_applicable`/diagnostic rather than a hard failure. It cannot substitute for
complete observed outcome evidence: exactly one direct task binding, one completed
native worker, one task-bound worker report, coordinator reconciliation of the exact
artifact hash, no pending/open work, and either owned `exit=0` or a verified idle
composer followed by exact owned cleanup. This remains observational only and never
sets `host_enforcement_state=host_enforced`.
A later final from the same parent-bound worker can be a sequential follow-up only
when each earlier final names a distinct, exactly-one observed worker publication;
each publication must follow its final and precede the next final, and the terminal
final still needs exact artifact reconciliation. A duplicate final, multiple workers,
replay, truncation, or an absent, competing, early, or delayed publication remains
BLOCK.
Generic `pre_binding_host_action` is never a current-host diagnostic because it
lacks strict actor/profile/path proof and can describe an unsafe dispatch. The sole
coordinator setup exception is an observer-proven manifest-bound, bounded, read-only
literal `skills/orchestrator/SKILL.md` read carrying the active-skill marker; the
separate declaration-bound read predicate can retain complete, safe read failures
only after the mandatory worker-SKILL receipt. Duplicate, replayed, ambiguous,
directory/glob/other-skill, private/project-target, dispatched, truncated, or
mutating bootstrap actions remain hard invalidators.
Compaction recovery repeats this boundary. Where the host supplies an explicit
coordinator identity, the active-task hook denies known command/file tools before
dispatch. When `agent_id` is absent, the hook establishes worker scope only from a
matching child-thread/parent-session provenance receipt validated against the
durable binding; otherwise the event remains a coordinator/system-compatible
task-scoped observation. Running worker command receipts retain their effective
cwd, so a later `write_stdin` continuation remains covered without overblocking
unproven session events.

Coordinator completion is also bounded by live ownership and evidence state: a
terminal final is forbidden while any assigned owner is active or a required
report/check is outstanding. A wait timeout is not completion and requires another
bounded wait for the same owner. Before acceptance/final delivery, assignments are
reconciled with native worker state and required evidence; failure or cancellation
must be explicit and recorded. This policy is model guidance, not a server-side
semantic acceptance gate.

Before ordinary acceptance, the coordinator also reviews verified changes for durable
project-knowledge or public-documentation impact. Missing inspection cannot support a
no-impact conclusion. Supported impact assigns an appropriate documentation owner to
load/apply `documentation-sync`, selects exact documentation ownership, and requires
updates plus proportionate verification; sufficient no-impact evidence needs only a
concise coordinator record. The coordinator does not read another role's `SKILL.md`
and, after the owner's published report, uses only Cortex report/public-evidence reads
to compare owner-cited documentation hashes and check results before acceptance. Any
additional project inspection, file read/hash or command verification is delegated to
an appropriate worker and returned in a task-bound report; the coordinator does not
rerun project checks itself. This remains advisory and does not activate the
explicit-only knowledge-harvest route or add a runtime continuation/gate.
Partial or inconclusive inspection instead requires bounded evidence gathering or
leaves impact unresolved with acceptance withheld until synchronization or sufficient
no-impact evidence exists.

The pipeline keeps `implementation_state`, receipt-backed `delivery_state`, and
coordinator-owned `acceptance_state` separate. Immutable-report delivery does not
imply implementation correctness or acceptance. Git, CI, deployment, rollout, and
production claims require their observed receipt and exact artifact revision; absent
receipts remain unverified. Reused review/verification evidence is identified by
`(artifact_revision, acceptance_boundary, check_identity)` and unchanged repetition
requires new evidence or a concrete rerun reason. A consequential repeated live
attempt can use a model-authored cross-layer acceptance contract, failed-canary table,
predecessor comparison, and discriminating check; these are not hooks, server state,
approval stages, routing, or acceptance gates.

The report-template extension point optionally structures six model-authored fields:
`delivery_state`, `acceptance_boundary`, `causal_model_delta`,
`predecessor_rollout`, `retry_discriminator`, and `receipt_references`. Bounded
null-safe missing-field warnings and exact duplicate review/verification reuse
diagnostics are retained as advisory metadata only. They do not authorize, route,
approve, accept, or fail a task, and they never claim `host_enforced`.
The legacy `failed_canary`, `review`, and `cost` fields use the same bounded,
null-safe, strict-JSON-safe recursive normalization, including for nested values;
oversized strings are truncated and arbitrary-magnitude or non-finite numbers are
omitted as `null`. This preserves their keys and advisory-only role without
turning malformed model metadata into a blocker.

Codebase Memory supplies derived project evidence, not instructions or task authority.
Its presence or an isolated provider failure is ordinary worker activity, not an
orchestration failure. The development observer keeps path-policy observations
separate from orchestration violations and records only safe provenance/target
classes, hashes, command-intent digests, terminal status and actor lineage;
private paths and content are never retained. Native workers must not shell,
search, stat or otherwise probe `.codex/cortex/` task-cache paths. They select
only assignment-relevant immutable reports and retrieve them by exact ID through
bounded `mcp__cortex__read_report` pages; missing evidence remains an explicit
gap. Only a positively classified unauthorized plugin/cache/Cortex target remains
acceptance-critical. Static path mentions and ordinary workspace commands remain
diagnostic and do not weaken the underlying fail-closed access checks. The
observer classifies simple `rg`/`grep` operands so a quoted pattern or glob
containing `.codex/cortex/` is not mistaken for a filesystem target. Explicit
private-cache operands, direct readers, and shell-ambiguous forms remain
fail-closed unauthorized findings; provenance retains only safe classes and
digests.
The same data/target distinction is available to exact Python `-c` and
quoted-heredoc audit helpers only when every private marker string is a direct
comparison operand. Assignment, filesystem or subprocess calls, comments,
interpolation, malformed code and private working directories still fail closed.
Native worker finals identify only the assignment-owned publication; cited input
or predecessor IDs remain inside that report and cannot satisfy ownership checks.

The trusted `PreToolUse` hook also applies a deny-only worker execution boundary
to the confirmed task project: shell, file, and patch operands that resolve into
`.codex/cortex/` are rejected before dispatch with a sanitized nonzero decision.
The check covers absolute and relative paths, `cd`/shell expansion forms, existing
symlink routes, and retained stateful `write_stdin` command sessions without opening
or logging the target. It does not inspect
or block the bounded `mcp__cortex__read_report` operation, and ordinary workspace,
Git, Codebase Memory, and external MCP diagnostics remain available. This is a
narrow preflight control, not an OS sandbox; the observer's fail-closed hard stop
still remains acceptance-critical if an unauthorized event is observed.
Only complete attributable `PreToolUse` denials carrying `PERMISSION_DENIED` and
the explicit `denied_before_dispatch` state are retained as bounded
prevented-attempt diagnostics, not hook-observation failures or evidence of a
dispatched host access. Missing or unknown semantic status fails the audit closed.
They cannot establish acceptance. An actually dispatched unauthorized target remains
a fail-closed audit finding.
Workers match its index to the exact canonical workspace and check relevant coverage;
a similarly named project or ready status alone does not establish that match.
Initial local indexing is scoped to the authorized workspace and does not authorize
changing ignore rules, indexing other projects or changing stable MCP settings.

## Optional evaluation and guidance boundaries

The `phase01-v1` evaluation contract is an offline, operator-reviewed protocol
outside Cortex runtime semantics. Blind score packets omit arm and payload labels;
raw prompts, expected answers, protected content, traces, and native identities
remain private controls. Token fields, wall time, and observable counts are kept
separate, and unavailable observations are explicit `null` values rather than
inferred zeros. Protocol, audit, invariant, identity, and protected-fixture
failures stop a trial and cannot be converted into a quality score or acceptance.

The phase-1 completion, failure-diagnosis, and parallel-dispatch cues are optional
model guidance only. They do not grant permissions, create approval gates, impose
workflow stages, or let the server/hooks select agents or accept results.

## Storage integrity and recovery

Publication validates task/thread ownership, generated identifiers, canonical regular
files, permissions, UTF-8 and the original draft device/inode. It streams the complete
file, flushes, atomically renames and commits metadata before removing the draft.
Immutable reports cannot be updated through MCP. Pipeline editions preserve older
bytes below the newest edition. Exact delivery retries retain their receipts;
changed arguments or content conflict. Unfilled template markers are rejected,
which does not establish semantic completeness.
Coordinator publication guidance separately requires an exact check of the
server-returned `replaceable_markers` list, keeps coordinator pipeline editions
distinct from worker reports, and preserves the same draft/request identity for a
deterministic `draft_guidance_remaining` correction retry. It does not add a
server-side approval or acceptance decision.

Draft creation without an explicit key allocates a fresh server delivery identity.
It does not reinterpret an existing conflicting key as permission to overwrite or
create replacement evidence. Repeated unkeyed creation creates separate drafts;
uncertain creation can be recovered from the native owner's unfinished catalogue.
Workers provide the required draft `template` argument explicitly (`general` for an
ordinary report); an empty draft-creation request is rejected before publication.
Explicit keys, publication immutability and task/thread ownership checks remain intact.
Coordinator calls that require a key use a literal UUID or stable literal value in
the tool arguments. They do not evaluate `crypto.randomUUID()` inside a wrapper,
because an unavailable host global can fail before the server records the call and
leave incomplete audit evidence.

Recovery and retention are scoped to the relevant task. A corrupt adjacent pipeline
does not block unrelated archives. Checked file identity and page-offset caches
avoid re-reading unchanged files; identity changes trigger integrity validation.
These checks do not establish protection against a malicious local filesystem race.

Storage format 11 has no automatic compatibility reader. The separate offline
10→11 conversion requires stopped access and a backup; it changes metadata without
rewriting Markdown. A legacy shared v11 store can be split into one fresh
project-local store with `cortex_split.py`; the split requires stopped access and a
new verified backup and never copies or rewrites Markdown. Back up each project's
SQLite file and task directories together. Retention is irreversible without that
backup. It deletes only the selected project's eligible tasks and known owned
drafts, preserving unrelated files and shared directories. The coordinator supplies
active-task exclusions; storage cannot infer whether an agent is still working.
For the sealed Phase 2 baseline compatibility path only, the live harness removes
ambient `CORTEX_DATA_DIR` and sets it to the already validated, empty
`<workdir>/.codex/cortex` directory. Missing, redirected, permissive, or populated
layouts are rejected before launch. Current format-11 storage still derives that
same path from validated native project state and ignores the compatibility input.
The outer payload digest intentionally normalizes the generated manifest version,
so archive acceptance separately requires the exact stamped version from the inner
plugin manifest. The preparer and independent auditor check both; the adapter
repeats both checks before preflight and every cell command, preventing a rewritten
or rebuilt archive from inheriting an earlier authorization.
The fresh-store launcher validates Git-root and other rejection-prone inputs before
committing the project hierarchy. It creates, validates, configures, and exactly
removes a no-workload tmux session before that commit. The probe and real launch
share one sealed `new-session` argument builder (only the bounded command differs),
and one exact-target `list-panes` query binds server, session, and pane provenance;
an empty or malformed format result fails before project mutation. Configuration
targets only the returned session ID, pipe/dispatch targets only the returned pane
ID, and repeated identity checks refuse server/session/pane substitution before
mutation or cleanup. It first durably journals the control and workdir
outside the project, then includes the same committing marker in the atomically
renamed hierarchy. An interrupted or unwritable post-commit receipt path therefore
cannot erase the reuse guard. Post-commit diagnostics and the unusable marker are
redundant best-effort records; any pre-existing incomplete journal or marker fails
closed. Cleanup targets only the tmux session ID created by that attempt, preventing
same-control resubmission and name-based cleanup of a replacement session. Recovery
requires a new control and new disposable workdir.
The sealed Phase 2 adapter also owns evidence-command arguments and output names.
It verifies a separately copied common observer by exact path and digest, so an
archived arm cannot remove current usage or policy evidence. The sealed observer
bundle also contains `profiles.json` and every dynamically valid `skills/` resource;
runner, auditor, and adapter agree on the canonical file set, per-file hashes, and
trusted aggregate. Missing, extra, tampered, symlinked, relocated, or non-private
dependencies fail before live transport. Token observation is
root-task scoped and content-free.
The shared observer separates evidence admissibility from product quality without
hiding raw failures. Missing, truncated, unbound, assignment-unverified, or
contaminating observations fail closed and cannot be scored. Only a complete,
attributable rejected access or tool failure can remain a scoreable quality
finding; an absent content-delivery receipt is an integrity-invalid unknown.
A one-time invalid-cell cleanup is restricted to
the explicitly anchored failed control/cell, preserves host and policy findings,
emits `score_eligible: false`, and requires stable ownership and inactivity before
the exact session stop; it is not a collection or scoring fallback.
Command observations retain the raw native status and exit code. Only exit 1 from
a structurally simple `rg`/`grep` search with empty stderr and no selected output
is labeled semantic `no_match`; the compound form is accepted only as exact
`pwd && SEARCH` with stdout equal to the bound working directory. This label
removes expected search absence from quality gates without masking exit 2+,
stderr, ambiguous shell syntax, or real host failures. Later command-family
success corrects only an exit-127 external-availability failure.
Its injected 64-hex control marker is the sole mode selector. A normal interactive
CLI smoke session keeps its visual trust confirmation, exact empty-composer check,
one literal paste, one Enter, and ordinary before/after input receipt without
requiring Phase 2 state. Phase 2-only commands reject outside sealed mode, while a
sealed invocation with missing control/session state rejects before transport.
An ordinary session with an existing first-submission timestamp likewise rejects
`send` before provenance/state mutation, composer or receipt inspection, paste, or
Enter; resume observes the existing task and never replays its original request.
It writes each bounded artifact by atomic replacement and emits the bundle receipt
last, only after successful status, capture, events, calls, usage, and audit schema
checks. `stop` requires and revalidates that control- and arm-bound receipt plus all
artifact digests; incomplete or tampered capture cannot authorize removal of the
live observation streams. Ordinary `stop` additionally re-queries the live tmux
server/session/pane/PID/start-tick identity before sealing and again before kill;
restarted, replaced, and reused identities refuse cleanup. Ordinary binding files
use exclusive creation and 0600 ownership, so a concurrent creator cannot be
overwritten.
Before submission, `begin-cell` issues an owner-private one-time nonce bound to the
frozen cell ID, control, arm, canonical workdir/store, and current session receipt.
The fresh start captures that receipt exactly once only after tmux returns the
owned session, pane, and pane PID, and seals those fields in a control-scoped
`phase2-cli-session-binding-v1` file. Begin-cell rejects incomplete start state or
receipt rotation. Its authorization path is scoped by the control digest and that
receipt, preventing stale UID-global and cross-control authorization inheritance
while preserving refusal for an unconsumed authorization in the current session.
The adapter requires a control/session-bound trust transition before begin-cell:
sealed `start` waits for a stable latest trust-or-composer state, seals intent
before at most one Enter, and returns only after proving the exact empty composer.
It never seals `not-required` from absence or an early placeholder. The separate
accept command only verifies the existing receipt. Historical placeholders cannot
hide active unsent text. It permits one submission, joins the observed coordinator thread during
collection, and binds the manifest digest back into that authorization. Stop checks
the live session and consumes the authorization only after successful cleanup, so
a copied same-arm bundle from an earlier cell and repeated submission/stop attempts
fail closed. `issued` is durably replaced by `submitting` before the transport is
called. A successful transport then records the exact request digest, root thread
ID, native user-turn source/line hashes, finite submission timestamp, and
nonce-bound receipt in one atomic authorization update. The native user turn is
accepted only from the single rollout file held open by the exact owned Codex
descendant after tmux, pane-PID, and process-start revalidation; cwd/time matches
without that linkage fail closed. It reads the descriptor-linked inode and requires
an identical final descriptor/process/session snapshot immediately before receipt
acceptance, rejecting added, removed, retargeted, or ambiguous rollout descriptors.
Bounded `resolve-send`
reconciliation can recover a delayed receipt but never emits input; duplicate or
ambiguous matches are rejected;
failed or uncertain transport leaves `submitting` non-resubmittable. Collection and
stop reject missing, malformed, inconsistent, or transport-mismatched submission
evidence.
Normal stop, pre-submit abort, and dead-pane recovery all dispatch the harness's
exact-target cleanup boundary. Its explicit arguments must match the sealed control,
session receipt, tmux server PID, session name/ID/creation time, pane ID/PID, and
pane process start identity. The helper revalidates those values immediately before
any interrupt and targets the pane/session by sealed IDs, then refuses server or
session replacement, rename/recreation, pane substitution, and pane-PID reuse.
Cleanup failure leaves the normal cell authorization unconsumed.
For a live unchanged trust prompt with no request receipt, or a stable empty
composer with two authoritative zero-user-turn observations after attempted
transport, a separate pre-submit abort gate requires the exact immutable
control/session binding, two identical
owned pane/process/activity generations, no task or worker evidence, and empty
open-exec state. It writes an authorized one-time receipt before exactly one
exact-target interrupting stop and consumes it only after success. Submitted, foreign, raced,
or uncertain sessions cannot use or retry this route; orphan recovery remains
limited to its dead-pane contract.
Collection additionally binds the canonical evidence-directory path and its
device/inode, owner, and `0700` mode into both authorization and manifest. That
identity is rechecked before stop, rejecting exact byte copies, renames, symlinks,
and non-canonical path aliases rather than authenticating bundle content alone.

## Source capture and hooks

The original request comes from the explicitly validated current native thread and
project, never model-authored replacement text. The reader searches the current turn
with bounded memory rather than a fixed tail. It rejects foreign sessions, symlinks
and unrecognized index formats. Distinct native messages retain separate identities,
even when text matches. Attachment metadata records recovery routes and explicit gaps;
it does not imply that an attachment was copied or verified.

Follow-up capture runs only for an already active Cortex task. `normal` suspends
capture without deleting the archive. With an established project route, a
host-source failure leaves saved reports accessible with explicit capture
completeness. A fresh process still requires the validated native index to locate
the archive; it never scans or guesses a global store. Unavailable sources never become
invented requirements. Source and report provenance help the coordinator decide
whether earlier evidence is still usable.

`UserPromptSubmit` has no unique native message identifier, so the hook records only
a pending follow-up until an authoritative typed receipt permits publication. Stop
diagnostics are advisory; when the host does not expose a reused worker assignment's
boundary, they cannot prove that assignment was stopped or completed.

Bundled lifecycle hooks use one local Python handler, no model calls, and the host's
ordinary review/trust mechanism. Recovery context contains bounded safe references,
not raw user text elevated into developer instructions. Unchanged reminders are
suppressed. Tool observations retain compact result statuses and change signals,
not raw commands, prompts, patches, credentials or tool-output bodies. Hook actions
are distinguishable from model actions and do not constitute complete tool coverage.
Private shape diagnostics retain only approved key names, JSON types and bounded
counts or lengths. Bash stdout cannot supply execution receipts: JSON-looking text
and printed wrapper headings remain unverified, with native command outcomes
observed separately.

Only confirmed registered-file integrity violations may deny an `apply_patch`:
mutating a published report, deleting/moving an owned draft, or a proven ownership
conflict. The handler parses actual patch targets; a path mentioned in text is not
such a target. Unknown actor identity cannot establish an ownership violation.
Hooks do not grant permission, rewrite tool results, accept work, assign agents or
force repeated continuation. Stop diagnostics remain advisory. Failures are visible
and must not be described as a successful observation. The runtime's narrow
fail-open diagnostic boundary applies only to passive observation, logging,
telemetry, and diagnostic serialization. It records at most a bounded error
class/code, marks the diagnostic evidence `unverified`, and continues the caller
when possible, including storage or permission errors in passive telemetry. It never
covers authorization, private-path/provenance, binding, replay/truncation,
publication, cleanup, audit, or a protected/uncertain action.

The offline live auditor requires native participant completion and rejects real
check failures even when an output artifact already matches. This acceptance
boundary never sends a runtime denial or terminates an executing worker.

## Development and evidence

Use `scripts/cortex-dev` only for the exact isolated `.cortex-dev/.codex` candidate.
Never change the user's stable installation or settings for development. Real
Desktop uses the same candidate with a disposable profile. Do not bypass hook trust.
A successful source test is not CLI/Desktop qualification.

Live parity is strictly CLI-first: Desktop may start only after a CLI **ACCEPT**
with a clean audit on the same unchanged candidate and payload. A failed, rejected,
unavailable, or unverified CLI attempt is ended, fully audited, and stopped before
Desktop; Desktop cannot be used as a diagnostic substitute. This ordering is
qualification procedure, not server-side acceptance state.

The Desktop submission helper treats X11 activation as an ownership boundary. Only
the exact `_NET_WM_DESKTOP` property warning may reach its single focus fallback;
the launched PID is checked before and after focus, and `getactivewindow` must
identify that same window before input is emitted. Other xdotool failures and any
identity mismatch fail closed. The warning and fallback result remain in the
owner-only session state, while composer preparation and the one-new-task receipt
gate remain mandatory. Current Desktop exposes no supported composer-element
locator, so the helper preserves the `codex://threads/new` URI's prepared composer
focus and never treats a guessed window coordinate as an input authorization. Before
its one plain `Return`, the helper requires the owned window to remain focused across
two observations after a three-second hydration interval. One monotonic 60-second
deadline covers both owned-window lookup and focus retries, so a missing window cannot
restart a fresh lookup budget. This bounded readiness check may delay or fail a
submission; it never substitutes for the exact one-new-task receipt.
If the helper's `start` or `send` boundary raises, its public output contains
only fixed `operation`, `category`, `reason`, and `recovery` values. Exception
text, private paths, commands, subprocess output, and logs remain suppressed.

Routine worker checks are permission-safe by guidance: isolated CLI and Desktop
helpers force `PYTHONDONTWRITEBYTECODE=1` and suppress only a complete inherited
optional Codebase Memory entry in the disposable profile, while workers avoid recursive cache
deletion, `git clean`, reset/checkout, and other broad cleanup for ordinary residue.
Pre-existing residue is left untouched and reported with the exact blocker and
required authority when it prevents a check. This does not broaden host permissions
or turn worker instructions into an approval mechanism.

Before any project action, a worker needs an attributable complete attached or exact
assigned worker-skill receipt; a coordinator read never establishes worker route
provenance. From its first action, workers do not probe installed plugin/cache/
candidate/agent-registry paths. The observer allows only that exact approved
instruction read and quoted static exclusions, while direct or ambiguous operands
remain auditable unauthorized-access findings. PostToolUse failure receipts expose
only bounded error class/stage and retained task attribution, never raw commands,
output, or exception text; they remain acceptance-critical. Use one bounded command
per wrapper with a complete exit/result receipt. A worker final names exactly its own
current published report ID; predecessor and foreign report IDs stay in the immutable
report body. Truncated wrappers remain incomplete evidence even when nested commands
later show success.

After a pending or timed-out native wait, the coordinator repeats a bounded wait for
the same owner and may use internal status/evidence observations. Unsolicited
`send_message` or `followup_task` remains an audit violation; permitted transitions
are an inbound same-owner reply, direct user steering/clarification, or an intentional
follow-up after terminal result/report reconciliation. The server and hooks do not
enforce these semantic transitions or accept results.

Optional personal TOML export is a separate explicit operation; marketplace setup
uses complete worker skills and does not register personal agents. Exact advertised
skill paths and needed declared references are valid; broad installation scans and
server internals are not worker instruction-loading routes.

Live diagnostics retain safe argument/result digests, observed roles, command exit
or running-session receipts and errors, not raw host logs. Inspect every observed
call, including after the first fault; preserve unresolved and corrected failures.
The coordinator may read only the exact advertised orchestrator `SKILL.md` and its
directly declared Markdown reference leaves from the isolated cache; other skills,
adjacent files, and unlinked references are unauthorized. Live helpers
validate that the supplied workdir is already a Git repository root and never
initialize it or write identity settings. A missing-repository worker probe remains
advisory rather than a coordinator-boundary breach.
A saved report is not acceptance. The coordinator assesses current requirements,
source completeness, artifact revisions and the evidence's limits.

Unchanged waits are silent: a timeout, pending worker, elapsed time, or unchanged
poll does not justify a user-facing status update. Only a state/evidence delta,
genuine blocker, required user decision, or material next-action change is communicated.
Offline advisory signals for rollout, review, recheck, delivery-error, and wait are
bounded/null-safe planning evidence only; they never select models, rank workers,
gate actions, or establish acceptance.

Phase 2 orphan cleanup is not a fallback around the evidence-bundle stop gate.
The separate recovery command requires the exact owner-private prior control,
cell, arm, and session receipts; only an unsubmitted `issued`/`submitting` or
explicitly invalidated lifecycle may qualify. Every live pane is refused. A dead
retained pane requires matching terminal output, no active coordinator/worker/task/
exec/wait state, and an unchanged session receipt plus identical final status at
the check/stop boundary. One explicit nonce is consumed into an external cleanup
receipt. Submitted, foreign, receipt-less, repeated, raced, and mismatched sessions
remain untouched.

When tmux itself is absent, `gc-absent-runtime` can retire otherwise blocking
Phase 2 state without signaling a process. It requires an explicit one-time nonce,
exact known control/session/authorization/binding provenance, terminal retained
calls/audit state with both open-state fields explicitly present as empty lists,
exactly one saved-session file, whole-server absence, and absence or start-identity
mismatch for every sealed PID. The complete proof and target digests are repeated
immediately before every rename and final consumption, including prepared recovery.
Exact files are atomically archived rather than deleted, with a durable
prepared/consumed receipt supporting idempotent replay and bounded partial-failure
recovery. Any live server, matching PID, submitted active work, missing audit field,
duplicate saved state, unknown schema,
foreign binding, noncanonical or extra state-root entry, or changed byte refuses
the operation. Exact canonical filename joins cover control/workdir transactions,
session bindings, authorizations, saved state, and invalidation receipts; copied
same-control bytes under a backup, temp, alternate-case, suffix, or unknown filename
remain foreign state.
The selected binding and saved session must agree on the exact canonical
`<state-root>/events` path. That path may be absent after runtime loss; when present,
GC requires a nonsymlinked owner-owned mode-0700 directory with stable device/inode
identity and no children. No event row is cleanup evidence: opaque, valid-looking,
multiple, hidden, nested, linked, or otherwise present content refuses every replay,
rename, and consumption proof.
Pre-binding post-commit failures use a separate typed proof. Both failed
transaction journals and both unusable failure receipts must match the exact
control, canonical workdir digest, transaction ID, schemas, terminal statuses, and
`session_cleanup: stopped`; project marker copies must match and the database must
still be absent. A starting session may contain no tmux identity, authorization,
request, submission, or receipt. Only an empty capture and one sealed fresh-store
launcher row qualify. Seven exact entries are kind/digest sealed, atomically
archived, and fully revalidated before every rename and receipt consumption;
missing, mixed, successful, active, extra, or raced state fails closed.
Consumed cleanup pairs are validated as inert history and never select the cleanup
branch for later state. Current canonical markers select the generation kind;
supplying an exact consumed nonce requests only idempotent replay. Concurrent live
bound and failed-launch markers are ambiguous and refuse before mutation.

Normal Phase 2 evidence collection may recognize either the exact owned idle live
bash after one current successful exit marker and no descendants, or an exact
owned idle Codex composer without an exit marker. Composer acceptance requires a
stable owned process/task tree, terminal coordinator and workers, final report
call/event receipts joined by exact report and draft identity, no active tool/exec/
wait/session state, explicitly completed wait receipts, and the recognizable model,
effort, and workdir-bound prompt. Status, process, capture, event, call, and audit
snapshots are repeated byte-for-byte. Stale/spoofed UI, changing progress, active
sampling, foreign tasks, ownership drift, PID races, and check/stop races are
refused. Bundle-gated stop repeats the proof; orphan recovery continues to require
`dead=1`, recaptures and hashes unchanged owned process/status/exit-marker evidence
immediately before stop, and rejects every live pane.
Both call and event must independently carry well-formed, nonempty typed
`r_[0-9a-f]{12}` report and `d_[0-9a-f]{12}` draft receipts before the ownership
comparison; missing, empty, malformed, wrong-type, or merely equal null values fail closed.

The Model Gateway starts by default for Marketplace MCP through the global
`CODEX_HOME` configuration. Set `gateway.enabled = false` for explicit
storage-only mode. Its local supervisor owns its registered child process and
may also drain an older same-user Cortex gateway only after matching the exact
gateway argv and a real Cortex package manifest for the requested loopback
listener; it never kills an unrelated port owner. Listener and upstream changes
are applied by a drain-and-restart path; stop waits for owned runtime state to
disappear and reports a timeout when it cannot. Provider patching validates the
target as a regular, owner-controlled file, preserves unrelated TOML content,
writes a first-version backup with create-only publication, and replaces the
file atomically. Automatic provider setup follows verified readiness, targets
only the exact loopback listener, enables existing OpenAI authentication and
WebSocket support, and preserves model/effort preferences and unrelated feature
flags. It selects `context_management = false` and `remote_compaction_v2 = true`
so automatic compaction invokes remote summarization instead of a local context
reset. Both feature edits retain their prior values in the private journal;
later user overrides survive reconnect and restoration.
It rejects pre-existing custom provider/upstream configurations. Later user
route edits are preserved. A private journal tracks owned route and compaction feature fields;
disable/stop restores matching values rather than replacing the whole config
with an old backup. The running gateway observes removal or disable of the exact
Marketplace entry recorded at setup and restores before draining. An absent or
forcibly killed observer cannot provide uninstall cleanup; already loaded client
configuration still requires a reload.
Provider configuration follows the effective `CODEX_HOME` unless an explicit
override is supplied; it never falls back to a stable config from an isolated
launcher. Existing commented table headers remain singular and valid. Each
provider publication uses an owner-only update lock and a descriptor/content
compare-and-swap check, failing without replacement when an unrelated editor
changes the Codex file. The gateway configuration file and `cortex` control
directory reject symlinks, non-regular paths, other-user ownership, and
group/other permission bits; configured unsafe paths fail closed rather than
being treated as absent. Each
request's upstream cancellation is scoped to its own task; a shared client
session is not closed because another client disconnected. Request path/query,
body bytes, and header pairs are tunneled without filtering; the incoming
loopback `Host` authority is rewritten only on the upstream wire to the fixed
`chatgpt.com` authority because HTTP/1.1 virtual-host routing cannot use the
local listener authority. This transport-only adjustment prevents Cloudflare
virtual-host rejection; only the explicit compaction model/effort policy may
rewrite a request body. Runtime identity
requires the normalized stamped payload digest and exact Python entrypoint
arguments, not merely a gateway path appearing somewhere in argv. Reload
returns failure and the control command exits nonzero when the owned process
cannot be signaled or drained. The isolated gateway dependency targets are
cleaned and version-verified after installation; launcher recovery uses the
prepared Marketplace cache's packaged control entrypoint. Gateway
Host authority validation rejects empty, malformed, and non-loopback IPv6 forms;
only explicit loopback authorities are accepted.
The gateway does not enforce a request-count or connection-slot admission limit.
Its active-request counter is drain bookkeeping only, so a historical 32-request
count cannot yield a local capacity 503, `Retry-After`, or an admission queue.
The isolated candidate uses an unbounded outbound connector while retaining
transport, protocol, policy, body, timeout, and cancellation safeguards. Candidate
preparation uses the disposable listener (normally 18787) and does not inspect,
modify, or restart the stable 8787 runtime.
The packaged MCP manifest forwards the launcher-provided isolated dependency
directory; this is required for the cached MCP process to start its pinned
gateway child without inheriting an ambient dependency path.
The Desktop launcher derives the canonical prepared dependency directory,
rejects missing, symlinked, or permissive trees, removes inherited values, and
overwrites the child environment with the validated owner-only path.
The isolated launcher uses the complete Linux CPython 3.11/3.12
`requirements.lock` wheel hash set with pip `--require-hashes` and `--no-compile`.
Runtime health includes both the lock-manifest digest and installed dependency
byte digest, so an artifact substituted under an expected version cannot satisfy
the owned-process identity check. Disabling the explicit gateway drains the
positively owned child and waits for state removal; a reload transition stops
the child and an in-flight race is rejected at the proxy, so a disabled
configuration does not leave a healthy local proxy forwarding requests. A
draining health result is never accepted as readiness by the supervisor.
Compaction routing classifies direct V2 `compaction_trigger` requests as
`auto_compaction` and decodable legacy compact-path requests as
`manual_compaction`; the HTTP routes rewrite model, reasoning effort, and the
upstream-required `store: false` and `stream: true` values. The optional
WebSocket route recognizes only uncompressed direct `response.create`
compaction messages and rewrites only model and reasoning effort, preserving
other fields without applying a separate store rewrite. Each WebSocket leg owns
its handshake, masking and framing. Application headers, text/binary messages,
ping/pong and close signals are relayed. Upstream redirects are rejected before
the client can replay credentials, including during WebSocket handshakes.
Recognized legacy JSON is selected in-call to the supported V2 path because the
upstream retired `/responses/compact`; opaque legacy bytes retain their
original path and bytes. No redirect or retry is used. Every proxied request emits bounded model/effort/request-kind telemetry
without bodies, credentials, or raw headers. Invalid ordinary JSON and opaque
legacy wire formats remain byte-for-byte pass-through, while unsupported
encodings and recognized payloads with invalid controls remain explicit errors.
The outbound HTTP client suppresses only its implicit `Accept-Encoding` header;
an explicit caller value remains lossless, and automatic response decompression
is disabled so upstream response bytes are not silently rewritten.

Offline Phase 4 telemetry is reduced to bounded, owner-private aggregates by
[`scripts/phase4_telemetry.py`](scripts/phase4_telemetry.py). It rejects raw
prompts, commands, reports, model output, credentials, paths, and thread
identities; response IDs are used only for deduplication. Availability and
diagnostic categories remain separate from independently sourced quality, and
missing values remain null.

The offline Phase 5 overlay in
[`scripts/phase5_adaptive.py`](scripts/phase5_adaptive.py) accepts only those
bounded aggregates and keeps availability, completeness, and contradiction
flags outside quality. A model-supplied candidate remains advisory: the script
does not select agents, change model policy, accept work, or write a pipeline.
Missing or excluded evidence returns the static baseline, and explicit user
routes are preserved. Raw prompts, reports, commands, credentials, paths, and
thread identities are not accepted by the contract. Candidate evidence must
join every selected report by report ID, source revision, and artifact digest;
unknown-reason lists and recommendation JSON have explicit size bounds, while
malformed proposals become typed baseline fallbacks.

The client-facing route is the local Codex model provider, using the fixed
HTTP/SSE/WebSocket listener. No MITM listener, certificate or proxy environment
variables are created by this path. Only after a successful upstream upgrade
are WebSocket messages forwarded. Automatic and
standalone compaction may share the same V2 `response.create` trigger, so
origin attribution requires correlation with local lifecycle events rather
than another network submission. The isolated launcher exercises this same
provider setup only in its prepared candidate profile.

Upstream Codex source confirms the automatic trigger path: `compact_remote_v2.rs`
labels `run_inline_remote_auto_compact_task` with `CompactionTrigger::Auto`,
`compact_remote_v2_attempt.rs` appends `ResponseItem::CompactionTrigger {}`
before calling `ModelClientSession::stream`, and
`responses_websocket.rs` serializes/sends the request as the shared
`ResponsesWsRequest::ResponseCreate` WebSocket text message. The gateway therefore
sees automatic V2 compaction in the same outbound WebSocket handler; it is not
the retired legacy compact endpoint. Only the installed binary's threshold
decision remains unverified because its Rust source is stripped.

See [storage](docs/project/storage.md), [hooks](docs/features/lifecycle-hooks/index.md),
and [verification](docs/project/verification.md).
## Advisory depth is not authorization

Current governance metadata contains only a validated mode and public record IDs;
raw rationale is not injected into recovery instructions. An unavailable projection
does not stop a successful operation. Changing depth neither grants host permissions
nor accepts results, cancels workers or removes required independent checks.

## Linked coordinator instructions

Read-only coordinator references are limited to direct Markdown links from the
packaged orchestrator entrypoint, with matching installed/source bytes. They do
not qualify as the pre-binding entrypoint read, a worker receipt, or permission
to inspect arbitrary cache, registry or runtime state.
