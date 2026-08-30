# Host-boundary correlation audit

Development-only sanitized audit of the isolated hook stream.

## Observed hook facts

`PreToolUse` does receive collaboration calls. The observed normalized names
include `collaborationspawn_agent`, `collaborationinterrupt_agent`,
`collaborationlist_agents`, `collaborationsend_message`, and
`collaborationwait_agent`. Its payload contains `tool_name`, `tool_input`,
`tool_use_id`, session/turn/agent metadata, cwd, transcript path, model, and
permission mode.

`SubagentStart` contains session/turn/agent metadata, cwd, transcript path,
model, agent type, permission mode, and agent id. It contains no prompt,
context, rendered dispatch message, native dispatch digest, or tool input.
The observed `SubagentStop` sample had no payload fields beyond the event
classification.

## Enforcement design

At `PreToolUse` for `collaborationspawn_agent`, the coordinator should be
required to reference one server-issued native-dispatch correlation and the
host adapter should persist the dispatch digest with the resulting child
session. `SubagentStart` should then emit only the opaque digest correlation
and a closed delivery status. The activation hook can compare that correlation
to the pending server dispatch lease without seeing prompts or skill contents.

Replacement must remain prohibited after a successful non-ambiguous dispatch;
only an explicit ambiguous transport result or server-reported stale/conflict
state can release the lease.

The current hook can observe collaboration spawn/interrupt calls but cannot
authenticate their argument mapping to `SubagentStart`, because the lifecycle
event omits the relevant fields. This is an observability/authority gap, not a
reason to block ordinary coordinator reads after task anchoring.

## Documentation status

The repository provides hook registrations and payload handling but no local
authoritative schema defining prompt/context delivery on `SubagentStart`.
The available official OpenAI documentation search did not expose a detailed
Codex hook payload schema, so undocumented fields must not be assumed.
