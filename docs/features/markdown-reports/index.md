# Markdown coordination

Cortex keeps one model-authored `pipeline.md` per task. Each new edition is
prepended, retaining previous editions below. The current edition records active
requirements, cancelled conditions, decisions, assignments, resource owners,
unfinished actions and references to evidence. Original user messages are separate
immutable reports; the pipeline is a working interpretation, never their replacement.

The coordinator reads the original request, later clarifications, attachment
references and the evidence pages needed for a decision. A 4,000-character page
is a transport bound, not a limit on accessible context. Catalogue entries and
opening decision briefs help select relevant material. Workers receive a bounded
assignment and selected predecessor references; they do not routinely read every
report. After context loss, either role can retrieve the required source pages.

The coordinator records and restores the user's response language independently
of English internal reports and assignments. Progress updates, questions and final
answers follow the user's own prose or explicit response-language preference;
forwarded agent messages and recovery summaries do not change it. Requested product
language and exact source text remain intact. All worker reasoning and communication
use English from the first response, including skill-loading commentary and context
recovery; assignments state this before skill loading. Workers deliver a published report's
compact handoff once through their native final response, without a duplicate
cross-task message.

Reports record the source revision and checked artifact versions on which their
findings depend. A later message or observed file change signals possible stale
evidence. The coordinator decides what needs reconciliation or another check;
the storage service and hooks do not decide semantic coverage or acceptance.

Bounded independent discovery may precede the first pipeline edition. Before making
dependency, shared-resource or acceptance decisions, the coordinator records useful
durable requirements, decisions, assignments, ownership and open actions. This does
not impose a universal publication stage before delegation.

Each of the 22 specialists loads its complete marketplace worker skill through
native attachment or the exact advertised `SKILL.md` path. Needed declared
references are loaded on demand. One shared protocol and specialization fragments
generate all skills and optional TOML exports. No installation scan or personal
agent registry is needed. See [host compatibility](../../project/host-compatibility.md).

One worker may investigate, implement, verify and document a bounded result.
Additional specialists serve a concrete independent check or useful parallel
work. A completed context may receive an explicit continuation when appropriate;
a fresh verifier supplies independence when the risk warrants it. Timeout alone
does not end an assignment or transfer its files, browser, device or command
sessions to another worker.

The report class follows the assignment's observed outcome. A specialist's class is
a default and does not require reassignment when the same worker produces a suitable
different result.

The server creates a thread-bound draft below project `.cortex/`. The actor edits
that exact file in place and publishes its identifier with compact metadata.
Deletion, recreation or replacement invalidates its original device/inode
identity. Publication streams the complete UTF-8 file into the task directory,
flushes and atomically renames it, commits metadata, and removes the draft. An
exact retry returns its existing delivery receipt. An error before commit retains
the draft; recovery is scoped to the affected task. Report bodies remain real
Markdown under `.codex/cortex/<task>/`, with no application-wide body-size cap.
Their metadata is in the same project's `.codex/cortex/cortex.sqlite3`; a different
project has a separate store and does not contend for this database.

Native source capture uses the explicitly validated current Codex thread and
project. Identical text in distinct native messages remains distinct. Attachment
metadata preserves a usable path or resource and recovery method when available;
unavailable attachments remain gaps. Failure to fetch later host messages does
not close the existing archive: operation results expose capture completeness.

Optional, normally trusted [lifecycle hooks](../lifecycle-hooks/index.md) record
short local metadata and source updates only for an active Cortex task. They do
not select agents, approve actions, accept results or enforce continuation.
`normal` suspends Cortex capture while retaining existing documents.

See [storage](../../project/storage.md) for reliability and offline migration,
and [verification](../../project/verification.md) for source and real-host checks.
