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

| Event | Local effect | Limit |
| --- | --- | --- |
| UserPromptSubmit | Marks new active-task input as pending for native source capture | The documented event has no unique native message ID; publication is deferred to an authoritative typed receipt, preserving optional redactions |
| SessionStart resume/compact | Supplies bounded recovery references, revisions and own unfinished drafts | No raw user text or report prose is injected as developer instructions |
| SubagentStart | Records explicit child/parent lifecycle binding and reminds the worker to preserve the complete initial skill-load command receipt, including output and the actual exit_code or running session_id | Never treats parent session_id as child or infers a selected profile |
| PreCompact/PostCompact | Records observed compaction boundary | Their stdout is not the recovery context channel |
| PostToolUse | Records selected statuses, exit/session receipts, truncation and change signals | Partial tool coverage; unknown actor remains session-scoped |
| PreToolUse apply_patch | Checks actual parsed targets against registered Cortex files | Denies only established immutable-report/draft-integrity/ownership violations |
| SubagentStop/Stop | Diagnoses open drafts and missing saved publications | Advisory; reused assignment boundaries may be unavailable |
| Interrupt/SessionEnd | Commits a short observed boundary receipt | Does not keep a session alive |

Inactive Cortex does not archive ordinary conversations. The initial request is
saved by task creation. Explicit `normal` retains the existing archive and suspends
capture. Hook observations are distinct from model actions; they cannot replace a
host tool's actual result or the coordinator's interpretation.

Subagent events include explicit agent IDs. Tool events may expose only the parent's
session ID, so those receipts retain unknown actor scope unless identity is separately
confirmed. A later MCP binding must agree with the retained lifecycle parent/task.
Missing or conflicting evidence does not authorize another thread's draft edits.

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
or recovering unrelated tasks. Shell commands and unobserved editing routes are not
covered by this hook.

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

Private observation streams contain event types, safe identities/digests, statuses
and timing, never raw prompts, commands, patches or result bodies. Diagnostics may
include a bounded response shape: JSON types, approved key names,
capped lengths and unknown-key counts, without raw values or arbitrary key names.
Ordinary handler performance target is p95 ≤100 ms including Python startup; total hook overhead target
is ≤5% of task time. See the measured/unverified distinction in
[release evidence](../../release-readiness.md) and the
[three-configuration pilot](../../project/quality-evaluation.md).
