# Marketplace skills and native agent capacity

The supported route uses ordinary Codex progressive skill loading. The earlier
absolute ban on reading installed SKILL.md files was withdrawn on 2026-09-06 after
the user clarified that correct standard behavior, rather than a filesystem ban,
was their intent. Do not carry that superseded requirement into runtime assignments.

## Standard skill delivery

The [official skill guide](https://learn.chatgpt.com/docs/build-skills) describes
metadata-first discovery followed by loading the selected complete SKILL.md, with
supporting references loaded as needed. The [plugin packaging guide](https://developers.openai.com/plugins/build/plugins)
uses `.codex-plugin/plugin.json` with `"skills": "./skills/"`. Cortex retains that
layout, all 23 complete worker skills, including the report-only senior consultant,
companion skills and seven MCP operations.

A worker uses a complete body already injected by Codex or reads its exact advertised
SKILL.md path from the available-skills catalogue. Documented path aliases are expanded
from that catalogue. Needed declared Markdown references are valid. No installation
scan, guessed cache version, agent TOML read, server inspection, custom loader, setup
hook or personal registry is required. Read through the skill's end and retain actual
command receipts; a truncated page or role description is insufficient. Generated
worker skills retain a final completion marker and exact generated-byte checks because
a successful partial range read does not demonstrate that the whole file was loaded.

The [custom-agent guide](https://learn.chatgpt.com/docs/agent-configuration/subagents)
describes standalone TOML files in personal or project agent directories. Optional
TOML exports inside a plugin are not automatically selected native profiles. Cortex
uses its packaged worker skills and the host's advertised native delegation interface.

## Why automatic injection alone was insufficient

In the inspected official `rust-v0.153.0` source (unchanged in `rust-v0.153.4` for
these paths), V2 assignments arrive as `InterAgentCommunication`. `turn_user_input`
excludes that variant, and explicit skill injection consumes the filtered user input.
A skill token in a V2 assignment therefore does not prove automatic injection. This
is not a marketplace blocker when the advertised skill file can be read normally.

Source anchors: [V2 spawn](https://github.com/openai/codex/blob/rust-v0.153.0/codex-rs/core/src/tools/handlers/multi_agents_v2/spawn.rs),
[turn input and injection](https://github.com/openai/codex/blob/rust-v0.153.0/codex-rs/core/src/session/turn.rs),
[plugin manifest](https://github.com/openai/codex/blob/rust-v0.153.0/codex-rs/core-plugins/src/manifest.rs),
[native role loader](https://github.com/openai/codex/blob/rust-v0.153.0/codex-rs/agent-roles/src/loader.rs).

## Confirmed capacity behavior; unresolved host recovery

V2 automatically unloads eligible completed, errored or interrupted resident
contexts only when no active turn or pending mailbox remains. `pending_init` is
not eligible. `interrupt_agent` submits an interrupt; it is not a release operation.
The explicit `close_agent` operation is registered for V1, not V2.

A private real-host incident showed four contexts remaining `pending_init`, repeated
capacity rejection and unchanged interrupt receipts. This supports the capacity
diagnosis; the precise transition that stranded those contexts is not reproduced.
Do not claim an upstream allocator fix from a prompt change.

Source anchors: [residency eligibility](https://github.com/openai/codex/blob/rust-v0.153.0/codex-rs/core/src/agent/control/residency.rs),
[interrupt implementation](https://github.com/openai/codex/blob/rust-v0.153.0/codex-rs/core/src/agent/control.rs),
[interface-specific operations](https://github.com/openai/codex/blob/rust-v0.153.0/codex-rs/core/src/tools/spec_plan.rs).

Cortex now distinguishes assignment completion from capacity release, avoids
queue-only messages to completed workers, permits one diagnostic snapshot after
a capacity rejection, and requires an observed completion/release before retrying
spawn. Same-role continuation remains available when independently justified.
These changes prevent ineffective recovery loops; they do not free stranded V2
contexts or grant access to host state.

## Qualification boundary

Validate one unchanged installed marketplace candidate on actual CLI and then
Desktop: complete skill loading, Codebase Memory priority when available, report
identity, product outcome and independent verification when warranted. Desktop may
start only after CLI ACCEPT with a clean audit on that exact unchanged candidate and
payload. A failed, rejected, unavailable, or unverified CLI is ended and audited;
Desktop is not a diagnostic substitute. Review all calls, including unsuccessful
reads, truncated results and capacity handling. A product pass does not erase
protocol failures. Keep raw host evidence private.

For a current host that does not attest `functions-exec-pre-dispatch-v1`, the
bounded CLI fallback is `--bootstrap-route mcp-first`. It is an observer-gated,
not host-enforced, route: the child coordinator must directly complete public
`mcp__cortex__create_task` before any project-target host action. The observer
requires exact child-thread, project, task, original-request, and native-result
receipt bindings, and records `host_enforcement_state=unverified`. Pre-binding
shell, terminal, file, patch, nested wrapper, or private-path activity invalidates
the evidence. The sole observed-current-host static discovery exception is a complete
bounded literal read of an advertised coordinator `SKILL.md` or public `.mcp.json`/
`.codex-plugin/plugin.json` declaration in the isolated bundled release; directories,
globs, arbitrary cache content, runtime/state/registry paths, and mutation remain
invalid. No shell fallback or Desktop diagnostic substitute is permitted.
The exact value-free `text(ALL_TOOLS)` envelope is a distinct bounded public-catalogue
observation only with a complete receipt. It never authorizes filters, queries, paths,
content extraction, mutation, nested calls, or mixed wrappers.

The private-path boundary evaluates the manifest-bound active skill exception before
the general cache deny. It permits only one bounded literal `sed` read of the exact
active coordinator `skills/orchestrator/SKILL.md`, or a native-child worker's exact
registered `skills/worker-*/SKILL.md`; directories, globs, aliases, other profiles,
registries, runtime/state files, and every mutation remain denied. A complete result
receipt is distinct from a server event. Where the current host has no separate event,
observational evidence requires that result receipt plus one later non-replayed
task-thread publication correlation; it remains `host_enforcement_state=unverified`.

When the current host omits the authenticated worker assignment hint at that first
hook event, the boundary may admit one literal, manifest-bound leaf from the closed
registered worker `SKILL.md` set—not an arbitrary plugin/cache path. The retained
observer must later match that read's profile and assignment digest to the unique
closed native worker result; a cross-profile, absent, duplicate, or unmatched result
is BLOCK. Current-host `functions.exec` transports retain only the exact bounded
nested skill-read metadata necessary to classify that known coordinator read; a
generic wrapper or any omitted bounded field remains a pre-binding invalidator.
For the worker's exact registered leaf only, one literal `bash -lc` transport
envelope is accepted as transport rather than a second command. Its sole payload is
reparsed by the same closed `cat`/`sed`/`wc` read grammar; nested shells, extra
operations, directories, globs, adjacent cache files, and mutations remain denied.

In the current-host MCP-first observational mode, a verified closed native-worker
result plus its correlated coordinator delegation-evidence report may classify an
otherwise missing assignment receipt, separate worker skill-load receipt, or
worker-project-before-skill-receipt finding as a retained non-blocking diagnostic.
Likewise, a missing separate server event is diagnostic only after one complete public
MCP result and exactly one later same-thread non-replayed publication result. This is
post-observation evidence interpretation, not pre-dispatch authorization and not a
host-enforcement claim. Failed, truncated, replayed, duplicate, ambiguous, private,
registry/state, project-target, or mutating access—and failed binding, pending waits,
or incomplete audit—remain hard non-ACCEPT conditions.

For current-host interactive qualification, a capability the installed host cannot
emit is `not_applicable` or a retained diagnostic, never a hard result by itself:
bootstrap attestation, assignment-policy/skill-load receipt, separate server event,
and process self-exit are the bounded examples. A completed route still needs exactly
one direct public task receipt, one terminal native worker, one task-bound worker
publication, coordinator reconciliation of the exact artifact hash, complete and
non-truncated capture/calls/events/audit, and no pending/open work. A verified idle
composer followed by exact owned cleanup is the alternative terminal proof when a
self-exit marker is unavailable. All observed failures, malformed/mismatched/
ambiguous/replayed/truncated evidence, artifact/check failures, and unsafe
private/project mutation remain hard BLOCK conditions; the result is only
`observational_accept` with `host_enforcement_state=unverified`.

The same outcome rule treats a worker final that omits a redundant report ID as a
diagnostic only where exactly one task/parent/profile-correlated worker publication
and exact coordinator artifact reconciliation independently supply the identity.
One completed non-mutating public `read_report` `invalid_arguments` lookup is also
diagnostic only after that valid publication/reconciliation proof. These narrow
current-host exceptions do not admit failed writes, missing or ambiguous report
publication, mutation/private targets, artifact mismatch, replay, truncation, or
other malformed evidence.

For the current Desktop host only, `cortex-desktop-dev start` may record the
predeclared exact SHA-256 for the single expected `RESULT.md` artifact. A complete
observational Desktop outcome is one durable submitted root/direct public task
receipt, one attributable native child thread, one successful coordinator report
receipt, the exact regular artifact leaf below the isolated workdir, complete
non-truncated capture, and no open session or cell. Historical polling `pending`,
unavailable worker assignment/skill/native-final/report receipts, and a failed
side-effect-free worker `wc` or `truncate` check are retained diagnostics only when
that independently checked final state is complete. This does not authorize any
executed private/cache/project-outside-artifact access, mutation, duplicate task or
root, artifact mismatch, truncation, final open work, or a failed check without the
later exact artifact; those conditions remain BLOCK. The result remains
`observational_accept`, never host enforcement.

When the installed host omits a public task ID or native result body, the observer
uses only one immutable expected root, one parent-bound native worker/report, and
one exact non-replayed coordinator artifact reconciliation as opaque root/report
correlation. It never infers task identity: missing, duplicate, replayed, or
mismatched root, worker, report, or artifact links remain BLOCK. One or more
exact-equivalent denied-before-dispatch errors from the bounded registered worker
`SKILL.md` read are diagnostic only after that same full outcome proof. They require
the same worker/profile/manifest-bound static-read identity; differing, replayed,
private/project, mutating, dispatched, truncated, or ambiguous reads remain blocking
and none credits a skill receipt.

Within `current_host_mcp_first` only, the exact labels
`worker_assignment_policy_unverified` and `mcp_first_bootstrap_unverified`
record unavailable installed-host attestations. The observer always retains them as
printed `unsupported_by_current_host`, `not_applicable` diagnostics: they are never
integrity invalidators or hard blockers, and they grant neither evidence nor a host
enforcement claim. Supported outcome proof remains independently required; missing,
unsafe, ambiguous, replayed, truncated, pending, open, or incomplete supported
evidence remains fail-closed. Ordinary routes retain their existing policy handling.
The final current-host audit boundary reapplies exactly this two-label treatment after
all collector and classifier stages, preventing an earlier representation from
reintroducing either unsupported attestation as an invalidator. It does not suppress
any unrelated policy row or establish any outcome evidence.
Generic `pre_binding_host_action` is never a route-specific diagnostic: it lacks
strict actor/profile/path proof and can describe an unsafe dispatch. The one
coordinator setup exception is an observer-proven manifest-bound, bounded, read-only
literal `skills/orchestrator/SKILL.md` read carrying the active-skill marker; the
separate exact-equivalent registered-worker static-SKILL-read predicate can retain
complete read-only denied records. Any failed, replayed, ambiguous,
directory/glob/other-skill, private/project-target, dispatched, truncated, or
mutating bootstrap action remains a hard block.

The helper's exit status is derived from this final classification: it returns 0
only when `observational_accept` and valid evidence are present and every retained
finding is explicitly diagnostic/not-applicable. Diagnostics remain printed; a
hard failure, remaining policy row, pending/open state, mismatch, mutation,
truncation, ambiguity, replay, or missing outcome proof returns 1.

If worker Cortex MCP is unavailable, the current-host route may use only the closed
`cortex-native-worker-result-v1` final body. The observer requires one exact native
spawn, terminal wait, parent/task, registered profile, assignment digest, and status
match, then requires a coordinator-authored `Delegation evidence: native_worker_result`
publication. This is observational evidence with asserted worker artifact hashes and
`host_enforcement_state=unverified`, not worker authorship or host enforcement.

Native V2 capacity recovery from a stranded `pending_init` context remains a host
limitation. Standard automatic eviction of eligible completed contexts is distinct
from that failure. Never promise that a plugin prompt repairs host residency or
silently switch the user's native interface. Current named-candidate outcomes are
recorded in [release readiness](../release-readiness.md).

Encrypted assignment text is not readable policy evidence. The isolated observer
can nevertheless verify an executor's fixed Luna route from its exact native
activity path, parent edge, actual model/effort and successful complete skill-read
receipt. It retains the opaque-content marker and does not certify hidden scope
or reasoning. A missing child, incomplete/truncated skill read, inherited context
or mismatched route still fails this check; tool errors remain failures even when
the actor later recovers. This avoids equating ciphertext with a routing violation
while requiring positive evidence of the execution actually observed.

Callable deferred tool names do not expose their input contracts. Coordinators
and workers read the selected complete declaration when absent, reload it after
compaction, and construct requests with all required fields and optional defaults.
The shared instructions demonstrate both name discovery and selected-description
reading; printing names twice does not load an input contract. Result sizes and
remembered synonyms cannot redefine input fields or pagination bounds. The first
recovery skill read also preserves its complete command receipt. Publication keys
follow the writer's own declaration, independently of optional draft-retry keys.
See [focused verification](verification.md) for the consecutive real-host audit
results and rejected attempts.

## Worker app-message boundary

The worker protocol forbids app task messaging and requires progress, questions,
blockers and final delivery to remain on the native parent/subagent route. The
isolated observer classifies direct, quoted-bracket and simple static-alias worker
`send_message_to_thread` calls, including known `functions.exec` wrappers, as
forbidden orchestration outcomes, so CLI/Desktop qualification fails closed after
observing the actual route. This is bounded static inspection, not exhaustive
JavaScript evaluation. The installed Desktop provider is injected dynamically as the `codex_app` MCP
server. The candidate `[mcp_servers.codex_app] disabled_tools=["send_message_to_thread"]`
override fails bootstrap with `invalid transport`; the launcher therefore does
not claim a per-tool filter or shadow the provider. This remains an audit boundary
rather than host authorization: the plugin cannot hide or revoke an app connector
supplied globally by Codex.

## Lifecycle hooks in the format 11 candidate

The new payload includes a default hooks manifest and one local Python handler.
Use the installed host's documented event schemas and ordinary trust flow. The
[official hook reference](https://learn.chatgpt.com/docs/hooks) says subagent hooks
carry a parent session ID; only explicit agent/parent receipts establish actor
identity. Tool-event actor identity can remain unknown. `SessionStart(compact)` is
the recovery context channel, not PreCompact/PostCompact stdout.

Documented UserPromptSubmit has no unique typed-message identity; it records a
pending native capture signal instead of inventing a duplicate-free source ID.
A fresh active task is still created and initially sourced by MCP. Real CLI/Desktop
event coverage and timing are separate qualification evidence, reported in
[release readiness](../release-readiness.md). Historical no-hooks observations do
not establish support for this payload.
