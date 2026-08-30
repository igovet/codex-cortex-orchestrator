# Resume sixth-run forensic table

Sanitized development-only reconstruction; identifiers are represented only by
shortened hashes in the working notes and are not reproduced here.

| Phase | Session relation | Observed Cortex action | Interpretation |
|---|---|---|---|
| Initial live turn | One session, initial turn | Task opening executed successfully | Durable task anchor was created |
| Resume turn(s) | Same session identity with new turn identity in the hook stream | Read-only task reads observed; no new task opening | Transcript/state was resumed, not a new task |
| Post-resume first semantic action | Read-only task inspection occurred before later work | No post-resume task opening observed | A new opening was not required and did not execute |
| Transcript/pane | Historical assistant/tool material can be rendered on resume | Rendering alone is not a new tool call | Pane replay must not be counted as execution |
| Session metadata | `source=cli`, `parent_thread_id=null` | Root session metadata | Consistent with a CLI-created root session; null parent does not mean a new session on each resume |

## Decision

The evidence supports the same Codex session/task being resumed. A post-resume
`open_task` did not execute; the observed post-resume semantic action was a
read-only task inspection. Historical transcript material in the pane is replay,
not fresh MCP execution.

No launcher change is necessary for session identity. The acceptance harness
should distinguish newly emitted hook/MCP events from transcript rendering and
should treat `source=cli` plus null `parent_thread_id` as root-session metadata,
not as evidence that resume created a different task.
