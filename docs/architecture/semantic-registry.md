# Semantic registry

`plugins/cortex/scripts/cortex_runtime/semantic_registry.py` is the single
ordered metadata source for the public Cortex semantic boundary. It currently
describes all fifteen operations exposed by the one MCP server. Each operation
records its command/query kind, feature capabilities, anchor, handler name,
typed capabilities produced and consumed, and safe domain-error vocabulary.
Commands also declare a logical-slot category for the Phase C receipt adapter;
queries deliberately declare no receipt metadata.

The registry is intentionally not a second JSON-schema implementation. During
the migration, `build_contracts()` obtains the closed schemas from the existing
contract builder and asserts exact name/order parity. `bind_handlers()` then
constructs the composition map in registry order and rejects missing or extra
handlers. This makes drift fail at catalogue construction while preserving the
current runtime behavior and all existing orchestration functions.

`producer_consumer_edges()` and `exported_metadata()` provide machine-readable
metadata for validators, architecture reports, and generated conformance tests.
The registry describes capabilities, not workflow decisions: DAG shape, worker
selection, model/effort selection, user questions, governance judgment,
rework, recovery, and final-answer choices remain coordinator-owned.

The next migration gate is to move each schema factory and role-specific
publication envelope behind this registry without changing the public behavior
until its black-box candidate tests pass.

## Decision families

The public Decision API is split into three typed families rather than one
overloaded discriminator operation:

| Family | Open | Record |
| --- | --- | --- |
| Clarification | `open_clarification` | `record_clarification` |
| Plan review | `open_plan_review` | `record_plan_review` |
| Steering | `open_steering` | `record_steering` |

Each open operation emits a scalar server binding consumed by only its matching
record operation. Family-specific schemas make cross-family calls invalid at
the MCP boundary while the shared aggregate preserves durable behavior.

`validate_receipt_metadata()` is a build gate: every command must have a
logical-slot category and every query must be receipt-free at this boundary.
