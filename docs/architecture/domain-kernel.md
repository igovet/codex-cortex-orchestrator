# Domain Kernel foundation

`plugins/cortex/scripts/cortex_runtime/domain_kernel.py` defines the future
transaction boundary without cutting over existing handlers. It supplies
typed command/query contexts, aggregate-state and domain-error values, a
generic result envelope, coordinator-intent preflight, canonical request
normalization/digesting, slice-owned logical-slot injection, and the
`run_command_receipt` adapter.

The adapter is deliberately non-autonomous. It does not author a DAG,
select workers or models, ask clarification questions, approve plans, choose
governance depth, choose rework/recovery, or synthesize a final answer. Those
responsibilities remain with the coordinator and workers, as required by the
orchestration feature-parity contract. The Kernel owns the future boundary for
identity, typed scope, legal transitions, evidence completeness, publication
atomicity, logical slots, replay/conflict behavior, and closure readiness.

During this phase `domain_api.py` and the current store remain authoritative.
No feature is removed and no public operation is rerouted through the skeleton.
Commands use the store's single atomic lookup/mutate/receipt transaction. A
slice supplies aggregate identity, a logical-slot policy, and its transaction
mutation; the Kernel supplies the common admission and identity plumbing.
Queries execute without command receipts. A query that intentionally records a
read receipt must expose that as an explicit domain mutation in its own slice.

The build identity in `CommandContext` is copied into the durable receipt. The
launcher/provenance layer remains responsible for proving candidate parity;
the Kernel never invents or silently replaces that identity.

## Cutover rule

An operation may move behind `DomainKernel` only after its vertical slice has
black-box stdio coverage from an isolated candidate, durable-state assertions,
replay/conflict tests, and a focused real LLM-driven live-dev check. A green
unit test against a checkout import is not sufficient evidence.
