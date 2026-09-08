---
name: tool-discipline
description: Prevent guessed, incomplete and replayed tool calls during explicitly selected Cortex coordinator or worker work.
---

# Tool discipline

Use this skill only during selected Cortex work.

Before a call, name the concrete fact, state change or acceptance condition it must
resolve. Reuse retained results while relevant state is unchanged. A timeout, quiet
worker or desire to reconfirm does not justify another call.

Use only tools available to this host and role. Read the complete live declaration
before first use and reload it after context loss; a name or recovery summary is
insufficient. Use minimal requests with declared defaults and observed identifiers.
Check supplied keys and values before dispatch. Do not copy argument contracts into
skills, infer synonymous fields, discover requirements by causing errors, or inspect
plugin internals instead of advertised operations.

Batch independent reads when useful and inspect every result. Keep dependent reads,
mutations, approvals and retries sequential. A wrapper must expose the full nested
result, including exit status or a running handle. Returning stdout alone is
unverified. If a wrapper yields an active cell or command session, continue with its
matching wait or input operation until a terminal receipt before dependent work.

For file changes, inspect the current target fragment and patch the exact observed
text. Re-read after user steering, another contributor's edit or a context mismatch.
Preserve unrelated work. A Cortex draft is edited only at its server-issued path.
Its exact patch may pass directly to the native patch tool or through a safe wrapper
that forwards it intact. Never construct draft content with executable interpolation,
shell substitution or evaluation, and never put its body in Cortex writer arguments.

Inspect success, error and truncation signals before making claims. For discovery,
use complete schemas already attached when available; otherwise select only needed
tool names and complete schemas. Open selected full declarations individually. A truncated result is
incomplete evidence: resolve the missing fact before relying on it. Observer-only
rendering truncation does not by itself prove the native call failed. Retain
acknowledged identifiers and never replay a mutation for reassurance. Correct one
deterministic error from the live declaration when the fix is unambiguous; otherwise
stop that route. Follow the tool's advertised retry contract for uncertain delivery.

Record failures and unrun checks without secrets, private report bodies or raw host
logs. Recover ownership and acknowledged references before resuming.
