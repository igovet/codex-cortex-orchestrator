---
name: context-compaction
description: Internal Cortex recovery overlay. Load only for an explicitly activated Cortex task after compaction, reset, or bounded handoff.
---

# Durable Context Recovery

Preserve only the exact `task_ref`, current LLM intent, recorded user-visible decisions, semantic outcomes and acceptance conditions, verified project facts, material risks, changed paths, decisive checks, pending plan-review decisions, and neutral progress. Never retain secrets, credentials, personal data, private logs, raw worker streams, or internal ledger identity.

After compaction:

1. Accept the complete exact orchestrator/control repeat injected by the
   `SessionStart(source=compact)` host hook with `additionalContextLimit=0`, or repeat it through the
   standard host skill loader. Repeated loading remains permitted whenever
   context is lost. Never substitute `cat`, shell/filesystem inspection, an
   MCP resource, project copy, elevated execution, or a user approval question.
   Stop safely if exact host reload is unavailable.
2. Treat every model-visible read and every exact selector or live-schema value
   obtained before compaction as unavailable for constructing a later mutation,
   even when that read immediately preceded compaction. A coordinator's first
   post-compaction Cortex action is a fresh current-state read with the exact
   preserved `task_ref`. This scalar projection is not a complete outcome or
   assignment authority. Derive exact selectors from the fresh purpose-specific
   scope read and preserved point-edit details from the exact outcome read,
   never from the summary.
   Make this recovery read as one direct Cortex call. Do not place it or the
   later decision record inside programmatic tool calling, `exec`, or a batch;
   host hooks observe the outer tool boundary and cannot authorize nested
   operations individually.
3. If the state shows active or unfinished delegated work, read active
   continuations as the immediate next Cortex operation. Consume that view
   before queued steering or any scope, outcome, evidence, plan, assignment or
   timeline read. Then record queued direct changes in order; do not wait for
   superseded workers to publish. Use evidence only for the next actual decision.
4. A worker's first post-compaction Cortex action restarts its assignment view
   from the beginning on the same authenticated connection with its exact
   worker-scoped `task_ref`; this is the sole recovery exception to the normal
   terminal-read prohibition. Complete every returned page before further
   work or publication, and rebuild exact publication coverage only from the
   fresh server-owned reconciliation projection. Server receipts reconcile
   prior consumption without granting new authority.
   Make the recovery read and any later publication as separate direct Cortex
   calls, never as nested programmatic-tool or `exec` operations.
5. Continue bounded reads only as permitted by their live advertised contract.
6. Reconcile live native children through the complete unfiltered host
   projection. Signed current lifecycle evidence, not silence or remembered
   status, supports loss recovery. Confirm affected tasks have stopped before
   artifact reconciliation or overlapping mutation.
7. Reconstruct planning intent from current graph, decisions and evidence.
   Admission uses fresh server-derived readiness, artifact generations and
   exact node scope, never a remembered prose DAG. The backend does not choose
   future work or turn a waiting node into authorization to bypass prerequisites.

The coordinator remains coordination-only. Delegate missing project discovery, implementation, verification, Git state, manifests, caches, or documentation work. Do not recreate a task because context was compacted, and never reconstruct private identity from prose, paths, timestamps, suffixes, or earlier tool output.

If the exact `task_ref` was not preserved, disclose that durable task recovery is unavailable. Continue only from safe user-visible facts and ordinary host state; never invent an identifier or duplicate the task to simulate recovery.

Missing closure or partial evidence is advisory context. It does not itself prevent safe delegation or an honest final answer.
