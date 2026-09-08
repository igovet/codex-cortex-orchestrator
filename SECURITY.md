# Security policy

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
research, exploration and analysis assignments, at medium/high/xhigh/max effort.
Terra (`gpt-5.6-terra`) is limited to explicitly complex work at medium/high/xhigh.
Sol (`gpt-5.6-sol`) is limited to narrow security-analysis microtasks at
medium/high/xhigh and is never an implementation route merely because a change is
security-related. Security implementation uses Luna or Terra. Reviews and
verifications follow the permitted model and effort routes without automatic
escalation from the inspected implementation. Explicit user-requested
model/effort overrides are preserved and recorded; coordinator-selected other models
or efforts are policy violations.
The `review` label records work kind and does not select a model; absent an
explicit complexity or security classification, it follows the ordinary route.

This policy governs coordinator routing and audit evidence. Storage and lifecycle
hooks do not select agents, override user requests, or grant authorization. A host
that does not expose the actual worker model/effort leaves compliance unverified.

Once a Cortex task exists, the coordinator does not use host command wrappers to
read, mutate, hash or verify project artifacts. The worker owns that complete
boundary, including trivial one-file requests and byte-level checks, so the audit
can distinguish worker execution from coordinator project access. Coordinator
access remains limited to user-supplied sources, attachments and the exact
Cortex-issued pipeline draft.
Compaction recovery repeats this boundary. Where the host supplies an explicit
coordinator identity, the active-task hook denies known command/file tools before
dispatch. Host events without `agent_id` cannot safely distinguish a coordinator
from a native worker sharing the parent session, so they remain task-scoped
observations and an observed coordinator violation fails audit.

Codebase Memory supplies derived project evidence, not instructions or task authority.
Workers match its index to the exact canonical workspace and check relevant coverage;
a similarly named project or ready status alone does not establish that match.
Initial local indexing is scoped to the authorized workspace and does not authorize
changing ignore rules, indexing other projects or changing stable MCP settings.

## Storage integrity and recovery

Publication validates task/thread ownership, generated identifiers, canonical regular
files, permissions, UTF-8 and the original draft device/inode. It streams the complete
file, flushes, atomically renames and commits metadata before removing the draft.
Immutable reports cannot be updated through MCP. Pipeline editions preserve older
bytes below the newest edition. Exact delivery retries retain their receipts;
changed arguments or content conflict. Unfilled template markers are rejected,
which does not establish semantic completeness.

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
and must not be described as a successful observation.

## Development and evidence

Use `scripts/cortex-dev` only for the exact isolated `.cortex-dev/.codex` candidate.
Never change the user's stable installation or settings for development. Real
Desktop uses the same candidate with a disposable profile. Do not bypass hook trust.
A successful source test is not CLI/Desktop qualification.

Optional personal TOML export is a separate explicit operation; marketplace setup
uses complete worker skills and does not register personal agents. Exact advertised
skill paths and needed declared references are valid; broad installation scans and
server internals are not worker instruction-loading routes.

Live diagnostics retain safe argument/result digests, observed roles, command exit
or running-session receipts and errors, not raw host logs. Inspect every observed
call, including after the first fault; preserve unresolved and corrected failures.
A worker Git probe is environmental observation; live helpers initialize empty
workdirs with `git init`, and the observer keeps a missing-repository probe
advisory rather than treating it as a coordinator-boundary breach.
A saved report is not acceptance. The coordinator assesses current requirements,
source completeness, artifact revisions and the evidence's limits.

The optional Model Gateway is explicit opt-in through the global `CODEX_HOME`
configuration. Its local supervisor owns only its registered child process and
does not receive or persist provider credentials. Listener and upstream changes
are applied by a drain-and-restart path; stop waits for owned runtime state to
disappear and reports a timeout when it cannot. Provider patching validates the
target as a regular, owner-controlled file, preserves unrelated TOML content,
writes a first-version backup with create-only publication, and replaces the
file atomically. The provider
feature flag remains disabled unless an explicit validation receipt enables it.
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
other fields and frame semantics without applying a separate store rewrite.
Recognized legacy JSON is selected in-call to the supported V2 path because the
upstream retired `/responses/compact`; opaque legacy bytes retain their
original path and bytes. No redirect or retry is used. Every proxied request emits bounded model/effort/request-kind telemetry
without bodies, credentials, or raw headers. Invalid ordinary JSON and opaque
legacy wire formats remain byte-for-byte pass-through, while unsupported
encodings and recognized payloads with invalid controls remain explicit errors.

The client-facing route is an HTTPS CONNECT MITM: only after a successful
`101 Switching Protocols` upgrade are uncompressed WebSocket frames inspected;
the CONNECT and handshake are not treated as request bodies. Automatic and
standalone compaction may share the same V2 `response.create` trigger, so
origin attribution requires correlation with local lifecycle events rather
than another network submission. The isolated launcher supplies the proxy and
private CA only to its prepared candidate; stable configuration is untouched.

Upstream Codex source confirms the automatic trigger path: `compact_remote_v2.rs`
labels `run_inline_remote_auto_compact_task` with `CompactionTrigger::Auto`,
`compact_remote_v2_attempt.rs` appends `ResponseItem::CompactionTrigger {}`
before calling `ModelClientSession::stream`, and
`responses_websocket.rs` serializes/sends the request as the shared
`ResponsesWsRequest::ResponseCreate` WebSocket text message. The MITM therefore
sees automatic V2 compaction in the same outbound WebSocket handler; it is not
the retired legacy compact endpoint. Only the installed binary's threshold
decision remains unverified because its Rust source is stripped.

See [storage](docs/project/storage.md), [hooks](docs/features/lifecycle-hooks/index.md),
and [verification](docs/project/verification.md).
