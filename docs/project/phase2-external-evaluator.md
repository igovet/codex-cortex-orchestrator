# Phase 2 external evaluator preparation

> **User status (2026-09-11): CANCELLED.** The Phase 2 outcome comparison is
> cancelled, non-blocking, and non-scoring. Do not run this evaluator for the
> current release and do not claim an efficacy comparison, score, promotion, or
> quality conclusion. The material below is retained as an offline preparation
> reference and historical contract only.

`phase2_cli_runner.py` and `phase2_cli_auditor.py` are an evaluation-only,
offline preparation surface for `phase2-cli-v1`. They do not invoke Codex,
tmux, a provider, a listener, an oracle, a scorer, or a reveal operation.

The runner requires explicit baseline/candidate identities, a six-family
manifest, an external hash-locked dependency file, provider-route contract,
environment allowlist, model/effort contract, and timeout values. It then:

- validates the source as a canonical, user-owned Git checkout;
- archives the locked historical commit and current `HEAD` into new owner-private
  disposable directories, overlaying only the supplied current candidate payload;
- verifies the complete candidate and baseline payload digests and the exact
  stamped version embedded in each archived plugin manifest;
- creates private `.codex/cortex` parents while requiring the SQLite store to be
  absent;
- seals one common external live harness for both archived arms without changing
  either archived product tree; and
- seals each archived launcher's and observer's exact path, owner-private mode,
  and SHA-256 identity; and
- emits one owner-read-only JSON control record with symmetric neutral-envelope
  fingerprints.

The fixture manifest is read only for its sealed identity fields. Fixture trees,
prompts, reset scripts, and oracle scripts are not read or written by this
preparer. Reset is therefore recorded as an explicit sealed contract for the
later fixture owner, not as a live execution. Each family must retain the
canonical `oracle.py`/`reset.py` paths and command placeholders, a non-overwrite
reset policy, and the protected `USER-NOTE.txt` path; missing, malformed, or
remapped reset/oracle fields refuse preparation.

The auditor is intentionally a separate implementation. It re-hashes the
runner/auditor scripts, manifest, provider route, both payloads, checks both
embedded archive versions, private
workdirs, the common live adapter/harness, and neutral-envelope controls. It fails closed for identity drift,
manifest/hash-map mismatch, missing dependency/provider evidence, non-private or
symlinked snapshots, a present fresh store, asymmetric fingerprints, open or
duplicate receipts, or any live-activity marker.

The environment envelope is explicit rather than inferred from arm equality:
`--proxy-policy blocked` is required, the allowlist must contain unique variable
names, and ambient path/proxy/runtime controls are denied and recorded as
`absent`. Installed dependency trees, app hosts, and other unavailable
capabilities remain explicit unavailable/disabled values; they are never treated
as equivalent merely because both arms carry the same receipt.

## Offline invocation

Use a new absolute output directory for every preparation. All lock arguments
are required; there are no inferred equivalence values.

```bash
python3 scripts/phase2_cli_runner.py \
  --source-repo /absolute/source \
  --candidate-source /absolute/source \
  --manifest /absolute/fixture-manifest.json \
  --dependency-lock /absolute/provider-requirements.lock \
  --environment-allowlist /absolute/environment.allowlist \
  --provider-route offline://phase2-provider-v1 \
  --proxy-policy blocked \
  --output /absolute/new-private-output \
  --baseline-commit 17ace1ce2f7e3c5bb3dcf2b2b16424a16db7d7d9 \
  --baseline-version 1.15.6+codex.sha256.cc786ae2fbd04cf1 \
  --baseline-payload-sha256 cc786ae2fbd04cf1e9c29cfb34cf721de6ad6b8663f2d05f809baf2bee158698 \
  --candidate-version 1.15.9+codex.sha256.e4f332d43bf38024 \
  --candidate-payload-sha256 e4f332d43bf380248c5de142b835a62a888f8885ad6577677aa4f394804733d7 \
  --coordinator-model gpt-5.6-luna --coordinator-effort high \
  --worker-model gpt-5.6-luna --worker-effort medium \
  --cell-timeout-seconds 1800 --wait-timeout-ms 600000

python3 scripts/phase2_cli_auditor.py \
  /absolute/new-private-output/control-record.json
```

The runner's successful exit is preparation evidence only. The auditor's
successful exit is a control-integrity result, not live qualification or an
outcome score. `codex`, `tmux`, app hosts, Codebase Memory, installed dependency
trees, terminal receipts, and audit receipts may be explicitly unavailable in
this offline phase; each such state is retained with a reason and cannot be
treated as success or zero cost. Malformed nested control-record sections return
a structured `fail-closed` JSON result and nonzero auditor exit; they do not
escape as a traceback.

## Symmetric live compatibility preflight

The archived baseline and candidate transports are not the live entry point:
their historical parsers expose different option sets. The preparation instead
copies one trusted-digest harness outside both arm trees and binds it to each
selected arm through the source evaluator adapter. The auditor and adapter reject
a digest merely rewritten in the mutable JSON, and require the exact canonical
`OUTPUT/arms/baseline` and `OUTPUT/arms/candidate` roots. Run both preflights after the independent
auditor and before allocating a live run ID or submitting any task:

```bash
python3 /absolute/source/scripts/phase2_cli_adapter.py \
  --control-record /absolute/new-private-output/control-record.json \
  --arm baseline preflight
python3 /absolute/source/scripts/phase2_cli_adapter.py \
  --control-record /absolute/new-private-output/control-record.json \
  --arm candidate preflight
```

Each command must exit 0 and report the identical
`phase2-common-live-harness-v1` mechanism. A missing capability, changed hash,
arm-root mismatch, embedded-version mismatch, or model/effort mismatch refuses
the run. The adapter independently recomputes the selected archive's full
normalized payload digest and reads its exact embedded version on preflight and
again before every transport subcommand. A successful preflight therefore cannot
authorize a later cell after archive rebuild or mutation drift.

For each selected arm, invoke every transport subcommand through that adapter.
The start command remains frozen and fresh-store isolation is mandatory:

```bash
python3 /absolute/source/scripts/phase2_cli_adapter.py \
  --control-record /absolute/new-private-output/control-record.json \
  --arm baseline start --workdir /absolute/fresh-arm-workdir \
  --evaluation-fresh-store --model gpt-5.6-luna --effort high
```

Replace only `--arm baseline` with `--arm candidate` for the candidate cell.
The adapter rejects resume, omission of fresh-store isolation, or any other
model/effort value before dispatch. The common harness then performs its atomic,
owner-private absent-store commit and runs the selected arm's unchanged
`scripts/cortex-dev` through explicit `/bin/bash`, because archived regular files
are deliberately non-executable mode `0600`. The adapter verifies the fixed path,
owner, regular-file type, mode, and sealed digest before constructing that argv;
the path is never interpolated as shell text. It never invokes either archived
arm's incompatible smoke parser.

Git-root readiness, exact shared-session absence, isolated observation state,
model/effort routing, and available launcher configuration are validated before
the atomic project hierarchy commit. The live start also performs a no-workload
tmux launch probe before that commit: it creates the exact isolated session with
the same sealed `new-session` argv used by the real launch except for a bounded
`/bin/sleep` command, reads server/session/pane provenance from one exact-target
`list-panes` format, applies the required session option, and removes the session
by its returned ID. Creation, format, configuration, permissions, identity, or
cleanup failure therefore leaves the project hierarchy absent. The post-commit
launch repeats the same argv with `/bin/bash` and uses the same provenance query;
it does not rely on `display-message` interpreting an exact-session prefix as a
pane target. Both probe and actual `set-option` calls target the returned session
ID, and pipe/dispatch calls target the returned pane ID. The identity is re-read
around configuration and before dispatch; server/session/pane recreation is
refused, and cleanup never falls back to the mutable session name. Before the rename, the harness durably writes
independent control- and workdir-keyed `phase2-cli-launch-transaction-v1`
`prepared` journals outside the project and places the matching `committing`
marker in the staged hierarchy. A crash or write failure after rename therefore
leaves at least the pre-commit journal and atomic marker as refusal evidence even
when every later receipt path is unwritable. Best-effort
`phase2-cli-post-commit-launch-failure-v1` receipts add diagnostics but are not the
reuse guard. Any created session is stopped only by its returned tmux session ID.
A failed or incomplete transaction requires a new control and new disposable
workdir; no `begin-cell` or send is authorized.

The harness also supplies the exact fresh `<workdir>/.codex/cortex` directory as
`CORTEX_DATA_DIR` to both launchers after removing any ambient value. This is a
sealed compatibility input for the format-10 baseline, which predates native
project-store resolution; the format-11 candidate independently resolves the same
directory from validated thread/project state and ignores the variable. The
database remains absent until the selected runtime handles `create_task`. Invalid,
redirected, non-private, or already populated project-store layouts fail closed.

`start` owns the bounded machine trust/composer transition before it returns. It
waits for the latest active state to become either the exact trust prompt or the
exact empty composer. A possible composer must remain exact across three samples
and two seconds before `not-required` is sealed, preventing an early loading
placeholder from outrunning a delayed trust prompt. When trust is active, the
harness seals `enter-requested`, emits at most one named Enter, then requires the
exact empty composer and seals `accepted`. Session/pane/process replacement,
timeout, stale scrollback, unrelated composer text, or a second transport fails
closed. Success returns `ready-for-begin`.

The separate command is now only an idempotent receipt/composer verification:

```bash
python3 /absolute/source/scripts/phase2_cli_adapter.py \
  --control-record /absolute/new-private-output/control-record.json \
  --arm baseline accept-trust
```

It emits no input and requires the already sealed start receipt plus the current
exact empty composer. The latest rendered `›` input row must be the exact
placeholder; a stale placeholder above active unsent text is rejected.
This stricter branch is selected only by the valid control marker injected by the
sealed adapter. Direct ordinary `cortex-live-smoke send` retains its separate visual
trust/empty-composer, one-literal, one-Enter, and before/after input-receipt lifecycle
without Phase 2 identities. Missing identities in sealed mode still reject before
any transport; state fields alone never select Phase 2 mode.
Ordinary send is single-use as well: any existing first-submission timestamp rejects
before provenance mutation, composer/receipt inspection, paste, Enter, or state
write. Resume observes and continues the existing task without resending its request.
Then, before `send`, issue the unique frozen cell identity:

```bash
python3 /absolute/source/scripts/phase2_cli_adapter.py \
  --control-record /absolute/new-private-output/control-record.json \
  --arm baseline begin-cell --cell-id cell-001
```

`start` is the only transition that may create the session binding. After tmux
returns the exact owned session, pane, and pane PID, the harness computes the
session receipt once, writes the owner-private
`phase2-cli-session-binding-v1` receipt, and marks the session `started`. The
adapter rejects `begin-cell` before that completed transition and rejects any
later rotation of the receipt or its bound pane fields. The required order is
therefore exactly `start` (ready-for-begin), `begin-cell`, `send`; `begin-cell` is not a
pre-launch operation.

The adapter records a new one-time nonce with the current canonical workdir,
project store, session timestamps, session receipt, control hash, and arm in the
private live state. Its filename is scoped by both the control-record digest and
the immutable session receipt, so a fresh control/session cannot inherit a
UID-global or earlier-control authorization. An unconsumed authorization for the
current control/session still refuses replacement. Exactly one subsequent adapter `send` consumes the issued
submission state; another submission is refused. A new authorization cannot be
issued while the current one remains unconsumed. The authorization uses
`phase2-cli-cell-authorization-v2`: `issued` is atomically persisted as `submitting`
before external send, so a failed or uncertain send cannot be retried. After the
harness observes the exact root-thread native user-turn receipt, it seals the
prompt digest, thread ID, source format, source/line hashes, and bounded timing,
then replaces the initial null timestamp, and the adapter atomically records
`submitted`, the exact request digest, the finite submission timestamp, and their
nonce-bound receipt. A receipt is authoritative only when its
rollout file is the single session file held open by the exact owned Codex process
after tmux session/pane, pane-PID, and process-start revalidation; cwd/time filtering
alone never links a native turn to the cell. The reader opens the descriptor-linked
inode and then requires the complete rollout-descriptor, Codex-process, and tmux
identity snapshot to be unchanged immediately before accepting a receipt. Added,
removed, retargeted, or ambiguous rollout descriptors reject the cell.
A later
`resolve-send --prompt-file PATH` may recover a delayed accepted receipt without
emitting input; missing, duplicate, ambiguous, or mismatched receipts remain
refused. Collection and stop remain
unavailable from `submitting`; submitted-or-later validation recomputes the receipt
and requires the authorization to match the exact transport state.

If launch reaches the unchanged trust prompt, or an attempted send is proved to
have left an unchanged empty composer with no native user turn, and no cell request
was submitted,
the distinct `abort-pre-submit --abort-dir PATH` command is the only authorized
live cleanup route. It requires the exact control-scoped session binding, either a
null transport request or the matching prompt digest with two authoritative
zero-user-turn observations, a null submission timestamp/receipt, no
task/worker/foreign calls or events, empty open-session/open-cell audit state, and
two byte-identical status, process, rendered-screen, capture, calls, events, and
audit snapshots. The owned live Codex process and exact trust prompt or empty
composer must remain present. The
adapter persists an `authorized` `phase2-cli-pre-submit-abort-v1` receipt before
one exact-target interrupting stop, then marks it `consumed` only after success. Any
failed or uncertain stop is non-retryable. This route is not orphan recovery and
never accepts a submitted task.

Archived observers expose two legitimate schemas. The baseline returns MCP and
hook rows from one `observed_events` stream, while the current observer provides a
separate callable `observed_hook_events`. The common harness losslessly partitions
the former and validates the latter. A missing required reader, a non-callable
declared reader, malformed rows, or overlapping split streams emits a typed
`phase2-observer-evidence-schema` `NO-GO` result and exits nonzero; missing evidence
is never normalized to an empty success. In particular, the combined stream must
be nonempty and every row must be identifiable as a hook action or as an operation
event before legacy partitioning is accepted.

Current controls also copy and hash-lock one common observer beside the harness.
They also seal its complete runtime dependency closure: `plugins/cortex/profiles.json`
and every regular file under `plugins/cortex/skills/`, each at its canonical relative
path with an owner-only mode and SHA-256, plus a trusted aggregate manifest digest.
The runner copies that exact set; the independent auditor and adapter enumerate and
verify it before any live command, rejecting missing, extra, tampered, symlinked,
relocated, or non-private files. Both arms therefore use the same callable,
content-free participant usage and
current call-policy classification while their archived launchers and payloads
remain unchanged. Usage is scoped to exactly one observed coordinator root; zero
or multiple roots fail closed, and unavailable usage is never a quality score.
That observer retains raw command exit receipts but labels only exact exit-1,
empty-stderr `rg`/`grep` searches with no selected output as semantic `no_match`.
For a compound command it requires exactly `pwd && SEARCH` and stdout equal to the
bound working directory. Expected no-match results leave the quality gate; exit
2+, stderr, extra shell syntax, and other command failures remain visible and
blocking. Later family success can correct only a proven exit-127 availability
failure.

The `cleanup-invalid-cell` recovery is intentionally limited to the recorded
cell-001 observer-capability incident. It requires the exact control digest,
submitted user-turn receipt, original incomplete-collection receipt, stable double
captures, idle exact composer, terminal worker/task topology, and no open exec or
wait. Its receipt says `quality_status: not_produced` and `score_eligible: false`;
policy and host failures remain preserved.

## Sealed per-cell evidence collection

After the cell reaches a proven terminal workload state, collect all required
transient evidence with one adapter command. Normal collection can prove either
the exact idle interactive Codex composer after a completed task, or the retained
bash pane after ordinary Codex exits. Do not build a shell loop or pass observer
action flags yourself:

```bash
python3 /absolute/source/scripts/phase2_cli_adapter.py \
  --control-record /absolute/new-private-output/control-record.json \
  --arm baseline collect-evidence \
  --evidence-dir /absolute/new-private-evidence/cell-001
```

The evidence directory must be absent. The adapter owns the authoritative action
schemas and fixed bounds: `status`, ownership-bound `terminal`, `capture --lines
500`, `events --lines 500`, `calls --limit 10000`, `usage`, `audit`, and final
rechecks of status, terminal, capture, events, calls, and audit. It atomically writes `status.txt`, `terminal.json`,
`capture.txt`, `events.jsonl`, `calls.jsonl`, `usage.json`, `audit.json`,
`status-final.txt`, `terminal-final.json`, `capture-final.txt`,
`events-final.jsonl`, `calls-final.jsonl`, and `audit-final.json`, then writes
`evidence-bundle.json` only after every action exits successfully and its nonempty
output matches the required schema. A failed action leaves a named
`collection-failure.json` and the completed artifacts for diagnosis, but no sealed
bundle.

The common observer emits `cortex-evidence-quality-classification-v1` before a
bundle can be admitted. `evidence_valid` and `score_eligible` are false for any
missing, truncated, ambiguous, or unbound observation; unverified worker
assignment; failed observation hook; open/foreign topology; or protected/arm-
specific content contamination. Forbidden access with no authoritative delivery
status is `unknown_due_to_incomplete_evidence`, not a score. By contrast, a
fully observed attributable tool failure, coordinator misuse, or forbidden
request rejected before content delivery is retained in `quality_findings` and
may feed the frozen F/D/R rubric when every integrity check passes. All original
failure and policy arrays remain in the audit; classification neither deletes
history nor converts unknown evidence into a quality result. CLI and Desktop use
the same classifier for both arms.

Normal collection accepts the retained successful dead bash pane, the owned idle
live bash pane with empty tmux status, or the exact owned idle interactive Codex
composer. Bash requires the current capture's single successful exit marker and
no descendants. The composer path instead requires no exit marker, exactly one
owned Codex process tree, the recognizable idle prompt and exact model/effort/
workdir footer, one coordinator task tree, terminal results for every worker, and
the coordinator's exact final report/pipeline receipt joined by report and draft
identity in both calls and events. Each side independently requires nonempty,
well-formed string `r_[0-9a-f]{12}` and `d_[0-9a-f]{12}` receipts before equality;
missing, empty, malformed, wrong-type, or equal-null fields reject. Prior editions may share the owned pipeline ID,
but duplicate exact or foreign report events are refused. Every path requires no
active or pending coordinator, worker, task, tool, command session, exec cell, or
wait. A wait is terminal only with an explicit successful outcome and completion
timestamp; unknown or non-terminal call/status values fail closed. Repeated status,
process, capture, events, calls, and audit
snapshots must be byte-identical. Missing, duplicate, stale, spoofed, foreign,
active, changing-progress, ownership-raced, and disconnected evidence fails
closed. The capture is reset at start. Stop repeats all final snapshots against
the sealed bundle immediately before cleanup; use bundle-authorized
`stop --interrupt` when the accepted terminal state is the idle composer.

The completion bundle additionally records `cell_id`, the pre-submission nonce,
workdir/store, session receipt, exact request digest, submission timestamp,
submission receipt, and the unique coordinator thread observed in `calls`.
Collection updates the private authorization to the exact bundle digest. A
same-control/same-arm bundle from another cell therefore cannot authorize the
current session.
The adapter also freezes the evidence directory's canonical absolute path,
device, inode, owner, and `0700` mode before collection and verifies the identity
again after collection and immediately before stop. Copying or renaming the
directory, supplying a symlink, or using a non-canonical path alias cannot
authorize cleanup even when every artifact byte is unchanged.

Cleanup requires that exact complete bundle and revalidates its control-record,
arm, fixed arguments, file modes, hashes, and schemas:

```bash
python3 /absolute/source/scripts/phase2_cli_adapter.py \
  --control-record /absolute/new-private-output/control-record.json \
  --arm baseline stop \
  --evidence-dir /absolute/new-private-evidence/cell-001
```

Use `--arm candidate` consistently for a candidate cell. `stop --interrupt` is
available only after the same bundle gate. Missing, partial, failed, tampered, or
cross-arm evidence refuses cleanup so the transient streams remain available.
Successful cleanup consumes the nonce, so neither the same bundle nor the same
authorization can approve another stop.

The adapter never delegates Phase 2 cleanup to the harness's mutable name-targeted
stop route. Normal stop, pre-submit abort, and orphan recovery pass the sealed
control digest, session receipt, tmux server PID, session name/ID/creation time,
pane ID/PID, and pane process start identity to `stop-exact`. The harness compares
the arguments with the immutable state receipt, recomputes that receipt, and
revalidates the exact server/session/pane/PID identity at the cleanup boundary and
again immediately before an interrupt. It signals only the sealed pane ID and
kills only the sealed session ID. Substitution between adapter validation and
cleanup, PID reuse, and a renamed or recreated session therefore refuse without
interrupting a replacement or consuming the cell authorization.

### Fail-closed orphan recovery

`stop` remains evidence-bundle gated. A separate `recover-orphan` command exists
only for a stranded Phase 2 session whose exact prior control record, arm,
`cell-NNN`, session receipt, and owner-private live state still agree. It accepts
only `issued` or `submitting` cell authorization with no submission timestamp or
submission receipt, plus an explicit 64-hex one-time authorization nonce and the
declared state `failed-pre-submit` or `invalidated`. A submitted, collected,
consumed, active, foreign, receipt-less, or mismatched session is never eligible.

Before cleanup the adapter creates a new canonical owner-private recovery
directory and captures `status`, the ownership-bound process snapshot, terminal `capture`, bounded `calls`, bounded
`events`, and `audit`. Every live pane is refused, including an apparently idle
`bash` or `codex` pane: eligibility requires the exact retained dead `bash` pane,
its numeric exit status, and a matching `Cortex live-dev exit=N` marker. Those
observations must also show no running coordinator, worker, task, exec cell,
command session, or wait. Immediately before cleanup the adapter recomputes the
unchanged session receipt and recaptures byte-identical status, process snapshot,
and exit-marker output immediately before stop, so a PID, marker, or status
check/stop race fails closed. Both generations are stored and hashed in the
recovery receipt. Historical diagnostic failures may make audit exit
nonzero, but they remain captured and do not imply active work. Only after those
checks does the adapter persist the one-time authorization and invoke the same
exact-target smoke-session stop. `orphan-recovery.json` records the consumed authorization and
hashes of all captured artifacts; an absent pane or repeated authorization is a
safe refusal and never targets another session.

```bash
python3 /absolute/source/scripts/phase2_cli_adapter.py \
  --control-record /absolute/prior-output/control-record.json \
  --arm baseline recover-orphan \
  --cell-id cell-001 \
  --session-receipt EXACT_64_HEX_SESSION_RECEIPT \
  --recovery-state invalidated \
  --authorization-nonce NEW_EXPLICIT_64_HEX_NONCE \
  --recovery-dir /absolute/new-private-recovery-directory
```

This route recognizes the current sealed binding. The earlier run-5 cleanup
receipt remains historical evidence, not authorization for another legacy
cleanup. The route does not kill the tmux server, touch the stable listener,
remove the project store, or create evaluation acceptance. After a consumed
receipt, start the next cell with a new disposable workdir and a freshly prepared
control.

### Absent-runtime stale-state GC

When the complete default tmux server has disappeared, normal stop and dead-pane
recovery cannot run. The separate command below performs no signaling and starts
no workload:

```bash
python3 /absolute/source/scripts/phase2_cli_adapter.py gc-absent-runtime \
  --state-dir /absolute/owner-private-live-state \
  --authorization-nonce NEW_EXPLICIT_64_HEX_NONCE
```

It joins exact known control transactions, session bindings, cell authorizations,
saved state, and consumed invalidation receipts. It refuses foreign or unknown
generations, simultaneous saved-session files, any live tmux server, a
matching/ambiguous sealed PID, missing or non-list audit open-state fields, and
retained calls or audit state showing an active task, tool, wait, cell, or session.
It repeats the full tmux/session/PID/start-tick/activity/inventory/hash proof
immediately before each exact archive rename and final consumption, including on
prepared recovery, and durably
advances one receipt from `prepared` to `consumed`. Reusing the same nonce returns
the consumed receipt; a partial archive resumes only if every source/archive byte
still has its sealed digest.
The state root itself is allowlisted: every selected control transaction, workdir
transaction, binding, authorization, saved state, and invalidation receipt must use
its exact digest-derived canonical filename and cardinality. Extra JSON, hidden or
temporary files, backups, alternate suffix/case/encoding, duplicate records, and
same-control bytes under an unrecognized name are foreign state and refuse before
mutation, including on prepared and consumed replay.
The selected binding and saved state must also identify exactly the canonical
`<state-root>/events` path. It may be absent after the runtime is gone. If present,
it must be a nonsymlinked owner-owned 0700 directory and completely empty; its
device/inode/path are sealed and rechecked before each mutation and consumption.
No event row is accepted or parsed as cleanup evidence. A file named `events`,
symlink, wrong owner/mode, opaque or valid-looking JSONL row, multiple/hidden child,
nested entry, or content race refuses.

A separate branch of the same command recognizes exactly one failed launch that
never reached session binding. It requires byte-identical control/workdir
`phase2-cli-launch-transaction-v1` records in `failed` state, byte-identical
control/workdir `phase2-cli-post-commit-launch-failure-v1` receipts in `unusable`
state with `session_cleanup: stopped`, matching canonical project markers, and an
absent project database. The `starting` saved session must have no tmux identity,
authorization, request, submission, or receipt. Only an empty capture and the one
typed fresh-store launcher provenance row qualify; any runtime event refuses. The
seven exact root entries are kind/digest sealed, atomically archived, and fully
revalidated before every rename and final receipt consumption. Missing, mixed,
successful, active, extra, malformed, or raced state fails closed.
Consumed receipt/archive pairs are validated inert history, not current-generation
dispatch signals. A later bound generation therefore follows normal absent-runtime
GC. Only supplying the consumed generation's exact nonce requests its idempotent
replay; simultaneous live failed-launch and bound-generation markers are ambiguous
and refuse.

The normal idle-composer and idle-live-bash detectors do not relax this orphan route: orphan
recovery continues to reject every `dead=0` pane.
