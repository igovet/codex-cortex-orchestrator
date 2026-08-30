# Run3k worker identity and scope audit

Sanitized read-only audit of the implementation-worker bootstrap sequence.

| Boundary | Available metadata | Result |
|---|---|---|
| Spawn `PreToolUse`/`PostToolUse` | session, turn, cwd, transcript path, model, permission mode; no agent id on coordinator call | Host can bind the request to the coordinator turn, not yet to the child |
| `SubagentStart` | session, turn, agent id, agent type, cwd, transcript path, model, permission mode | Child identity becomes available at lifecycle start |
| Child MCP `PreToolUse`/`PostToolUse` | same child session/turn/agent hook metadata | Hook sees identity; MCP request itself contains only advertised operation arguments |
| MCP server identity | session nonce/candidate lease from process environment; no native agent id, task name, or host transcript metadata | Server cannot directly infer native agent identity |
| First consume attempt | Wrong property names (`anchor`, `bootstrap_token`) | Blocked before a server result; no assignment scope was established |
| Second consume attempt | Advertised `assignment_ref`, `bootstrap_capability` | Successful assignment-scoped consumption |

## Why the observed scopes differ

The first failed request was classified as coordinator scope because it never
reached a valid assignment-consuming operation and did not contain the
server-recognized assignment locator. The second request carried the exact
advertised locator and capability, so the MCP event was assignment-scoped.
This is an argument-shape transition, not a change in the underlying child
process identity.

## Architectural implication

Hooks receive native identity metadata, but the MCP server receives only its
process lease/nonce and tool arguments. The host must therefore write a binding
between child session/agent identity and the server-issued assignment
correlation at `SubagentStart`; the server cannot derive it from MCP transport
alone. The binding must be opaque, lease-bound, and read-only for ordinary
coordinator operations.
