# Run3f native-dispatch mismatch

Sanitized read-only evidence from the isolated raw hook stream. No private
message, capability, identifier, or path value is included.

| Boundary | Server projection | Spawn input | Finding |
|---|---:|---:|---|
| Argument keys | `assignment_ref`, `dispatch_digest`, `native_arguments` | `fork_turns`, `message`, `task_name` (plus optional routing fields in some calls) | Host-facing shape was present |
| Message | Nonempty full rendered message; stable digest recorded | Nonempty but materially shorter message with a different digest | Spawn message was not byte-identical to server projection |
| Isolation marker | `fork_turns` set to isolated-history value | Same value | Isolation setting survived |
| Task name | Present | Present | Name survived |
| Dispatch digest | Present in server projection | Not present in spawn input | Correlation proof was discarded |
| Activation denial label | Spawn was denied before anchoring | Reported as `route_not_anchored` | Generic classifier masked handoff mismatch |

## Root cause

The coordinator/host mapping selected or reconstructed a shortened message rather
than passing `native_dispatch.native_arguments.message` byte-for-byte. It also
discarded the server-issued dispatch digest. Consequently the child handoff had
no verifiable correlation to the successful assignment and was correctly treated
as non-authoritative by the higher-level coordinator.

The `route_not_anchored` label was a secondary diagnostic defect: the activation
hook's denial classifier has no closed category for a collaboration spawn whose
dispatch projection is mismatched, so it fell through to the generic pre-anchor
label. The state itself did not establish that the assignment mutation had
failed.

## Required correction

The host adapter must pass the server-native projection unchanged, preserve the
dispatch digest in the child bootstrap lease, and classify a projection mismatch
as `dispatch_mismatch`, not `route_not_anchored`. A successful assignment must
not be replaced unless the server reports an ambiguous transport or explicit
stale/conflict condition.
