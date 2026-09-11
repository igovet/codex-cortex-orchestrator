# Local lifecycle hooks

The default `hooks/hooks.json` invokes one packaged `python3 -B` handler. It shares
storage, native-source and file-validation services with MCP, uses no model calls,
and retains the seven-operation catalogue. Enable it through Codex's normal review
and trust flow. The [official event and coverage reference](https://learn.chatgpt.com/docs/hooks)
is the source of host payload contracts; source tests do not establish installed
CLI/Desktop support.

Hook storage follows the current native project binding and writes to that project's
`.codex/cortex/cortex.sqlite3`. It does not use a home-wide database or
`CORTEX_DATA_DIR`; concurrent hooks for one project share the store's serialization,
while hooks for different projects remain isolated.

The phase-1 completion, failure-diagnosis, and parallel-dispatch reminders are
optional model guidance. Hooks do not enforce those cues, select agents, create
workflow gates, score evaluation outcomes, or accept task results.

| Event | Local effect | Limit |
| --- | --- | --- |
| UserPromptSubmit | Marks new active-task input as pending for native source capture | The documented event has no unique native message ID; publication is deferred to an authoritative typed receipt, preserving optional redactions |
| SessionStart resume/compact | Supplies bounded recovery references, revisions and own unfinished drafts | No raw user text or report prose is injected as developer instructions |
| SubagentStart | Records explicit child/parent lifecycle binding and reminds the worker to preserve the complete initial skill-load command receipt, including output and the actual exit_code or running session_id | Never treats parent session_id as child or infers a selected profile |
| PreCompact/PostCompact | Records observed compaction boundary | Their stdout is not the recovery context channel |
| PostToolUse | Records selected statuses, exit/session receipts, truncation and change signals, including worker command-session cwd provenance | Tool events without actor identity require validated child/parent provenance or remain session-scoped |
| PreToolUse apply_patch | Checks actual parsed targets against registered Cortex files and the private-root boundary | Denies only established immutable-report/draft-integrity/ownership violations or a worker operand resolving inside `.codex/cortex/` |
| PreToolUse app-thread messaging | Denies `mcp__codex_app__send_message_to_thread` and its direct alias before dispatch for active Cortex tasks | Tool events may not identify a worker, so the exact denial is task-wide and does not revoke the connector outside Cortex |
| PreToolUse coordinator project access | Denies known command/file tools when the host explicitly identifies the bound coordinator; records the boundary receipt | Unproven session-scoped events remain compatible; validated child/parent provenance can establish worker scope without `agent_id` |
| PreToolUse worker private-root boundary | Rejects shell, file, and patch operands resolving inside the confirmed task-private `.codex/cortex/` root | Deny-only preflight; bounded `mcp__cortex__read_report` and ordinary external/workspace diagnostics remain available |
| SubagentStop/Stop | Diagnoses open drafts and missing saved publications | Advisory; reused assignment boundaries may be unavailable |
| Interrupt/SessionEnd | Commits a short observed boundary receipt | Does not keep a session alive |

Inactive Cortex does not archive ordinary conversations. The initial request is
saved by task creation. Explicit `normal` retains the existing archive and suspends
capture. Hook observations are distinct from model actions; they cannot replace a
host tool's actual result or the coordinator's interpretation.

Subagent events include explicit agent IDs. Tool events may expose only the parent's
session ID; those receipts retain unknown actor scope unless a matching child and
parent provenance is validated against the retained lifecycle binding. Running worker
command sessions retain their effective cwd, so a later `write_stdin` can recover the
same worker boundary even when its event omits `agent_id`. A later MCP binding must
agree with the retained lifecycle parent/task. Missing or conflicting evidence does
not authorize another thread's draft edits.

The confirmed `SubagentStart` context includes one concise reminder to return the
complete initial skill-load command result with its output and actual `exit_code` or
running `session_id`. It guides model-facing receipt handling; it does not establish
the command outcome itself.

The observed CLI Bash hook response is stdout only. JSON-looking output and printed
wrapper headings cannot establish exit status, a running command session or
truncation. Those hook receipts remain `unverified`; the native command observer
records the actual execution outcome separately. A successful MCP envelope also
does not establish an underlying shell command's exit status.

For `apply_patch`, the observed host envelope starts with `Exit code`, `Wall time`
and `Output` headers. Matching the retained result digests confirmed this format.
The adapter recognizes that exact envelope only for the patch tool; failed or
contradictory results never emit a completed file-change receipt.

Patch parsing considers add/update/delete/move targets only. Content mentioning a
protected path is not a mutation. Exact registered-file checks can protect another
task's immutable report in the same canonical project without traversing task bodies
or recovering unrelated tasks. The app-message denial uses the documented MCP tool
name and alias rather than parsing JavaScript. Worker shell/file tools are
preflighted only for direct or resolvable access to the task-private root; this is
a deny-only boundary, not broad sandboxing or a replacement for host permissions.

Recovery output is bounded to about 1,000 tokens and repeated unchanged hints are
suppressed. It links the pipeline and recent source references and lists bounded
own-draft metadata; the model retrieves required remaining pages. New source/artifact
signals do not automatically run tests or declare prior evidence invalid.

Hooks do not choose models, select specialists, assign work, grant authorization,
accept task results or force repeated continuation. Stop diagnostics cover open drafts
and known publications. Reused assignment stop semantics remain unknown when the host
does not provide a documented continuation boundary, so the hook cannot certify every
assignment of that worker. Errors are visible and non-blocking except an explicit
confirmed integrity denial. No failure is a successful capture receipt.

Private observation streams contain event types, safe identities/digests, statuses,
terminal receipts, actor/thread lineage and bounded path-policy provenance
(target class, access kind, decision and command/workdir digests), never raw
prompts, commands, private paths, patches or result bodies. Diagnostics may
include a bounded response shape: JSON types, approved key names,
capped lengths and unknown-key counts, without raw values or arbitrary key names.
The command-aware observer treats a quoted `rg`/`grep` pattern or glob mentioning
`.codex/cortex/` as a static search marker, not a cache target. Explicit path
operands and direct readers remain unauthorized; shell expansion, control syntax,
including unquoted filename globs (`*`, `?`, `[]`) or brace expansion (`{}`),
or otherwise ambiguous command shapes fail closed and retain only digests.
Ordinary handler performance target is p95 ≤100 ms including Python startup; total hook overhead target
is ≤5% of task time. See the measured/unverified distinction in
[release evidence](../../release-readiness.md) and the
[three-configuration pilot](../../project/quality-evaluation.md).
