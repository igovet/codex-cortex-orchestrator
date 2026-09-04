# Typed orchestration ledger

<!-- GENERATED:START -->

## Purpose and ownership

Cortex stores the execution graph and guards integrity while the LLM chooses
intent, decomposition, specialists and ready-node scheduling. Project
inspection, edits and verification belong to native workers. The coordinator
has only the bundled skill's bounded known-document routing exception.

The [implementation contract](../../project/typed-orchestration-integrity.md)
defines the invariants and qualification ladder. The ledger is not a prose DAG,
an initiative-status scheduler or a source of native permissions.

## Runtime surfaces

| Surface | Responsibility |
| --- | --- |
| [public_contracts.py](../../../plugins/cortex/scripts/cortex_runtime/public_contracts.py) | Twenty static operation schemas and authoritative call descriptions |
| [domain_api.py](../../../plugins/cortex/scripts/cortex_runtime/domain_api.py) | Audience-specific public adaptation and bounded projections |
| [domain_kernel.py](../../../plugins/cortex/scripts/cortex_runtime/domain_kernel.py) | Atomic semantic decisions and command receipts |
| [execution_graph.py](../../../plugins/cortex/scripts/cortex_runtime/execution_graph.py) | Structural DAG, capabilities, ownership and coverage validation |
| [graph_ledger.py](../../../plugins/cortex/scripts/cortex_runtime/graph_ledger.py) | Transactional graph lifecycle, readiness and generation relations |
| [candidate_family.py](../../../plugins/cortex/scripts/cortex_runtime/candidate_family.py) | Complete alternative validation and one-branch activation |
| [remediation.py](../../../plugins/cortex/scripts/cortex_runtime/remediation.py) | Bounded deterministic repair/regression expansion |
| [v12_store.py](../../../plugins/cortex/scripts/cortex_runtime/v12_store.py) | Current-only project-sharded SQLite state |
| [worker_message.py](../../../plugins/cortex/scripts/cortex_runtime/worker_message.py) | Exact native bootstrap and immutable assignment evidence |
| [native_observation.py](../../../plugins/cortex/scripts/cortex_runtime/native_observation.py) | Verified bounded native lifecycle observations |
| [artifact_fingerprint.py](../../../plugins/cortex/scripts/cortex_runtime/artifact_fingerprint.py) | Worker-executed Git/path-manifest procedures |

## Fresh execution and reads

Fresh task opening stores complete semantic outcomes and their acceptance,
constraints and expected checks. Governance assessment precedes the first
assignment. A worker-owned baseline establishes observed project state before
artifact-dependent discovery or planning.

Coordinator reads are purpose-specific: current scalar state, node scope,
one complete point-edit outcome, finalized evidence, active continuations or
explicit history. Recovery starts with state and consumes required continuations
before task progress; timeline never substitutes for recovery.

A fresh native worker consumes its exact server-rendered assignment first.
The terminal read supplies full scope, profile policy, predecessor evidence,
expected publication and artifact procedure. A copied reference or fresh
connection cannot inherit consumed publication authority. Compaction recovery
on the existing bound connection grants no new capability.

Worker evidence exposes exactly one typed assignment authority. Complete scoped
outcome requirements remain separate contextual evidence, not another list of
work or publication selectors. The legacy assigned/planning-item views and
serialized duplicate node instructions are absent. Artifact procedure, execution
mode, expected terminal kind and node-local checks stay together in that single
assignment; verification prose in the product contract cannot replace its keys.

## Graph and admission

Minimal work uses a generated complete-contract graph. Nontrivial plans publish
complete candidates which undergo structural and independent semantic
validation. Candidate publication alone never authorizes delivery.
Decision-bearing alternatives remain inactive until one current user answer
selects and approves a validated branch atomically.

Nodes declare dependencies, capabilities, execution modes, checks and completion
policies. Contribution producers are unique; outcome completion may combine
several contributions. Independent auditors may verify the same outcome, but a
producer cannot self-attest independence.

Scope exposes ready/waiting nodes and unmet prerequisites. Assignment selection
uses exact current node keys, and admission rechecks them in its write
transaction. The server derives scope rather than accepting a second prose
copy. Concurrent claims cannot acquire the same node.

Host slots do not determine semantic readiness. One active project mutator
excludes other mutators and artifact-dependent readers. Multiple ready readers
may audit the same sealed generation in parallel, while artifact-independent
work remains separately eligible.

## Artifacts and publications

Workers execute the declared fingerprint procedure; the core only stores and
compares observations. Read-only evidence requires matching start/end and target
generation. Mutation seals a successor generation and leaves older dependent
evidence historical. A snapshot conflict creates no report or terminal slot
and raises reconciliation.

Node purpose fixes terminal kind: planning publishes a plan, documentation
publishes its assessment, and every other node publishes a result. Profile and
edit count cannot change that choice. Exactly one cross-kind terminal slot
exists per assignment.

Observed node coverage is canonical. Every mandatory check must have successful
executed evidence for completion; failed, incomplete and unrun facts remain
explicit. Plans contain expectations rather than observed claims. Artifact
commitments identify what was checked without duplicating that narrative.
Every publication explicitly includes artifact observations; only
artifact-independent results use null. Missing observations are never silently
interpreted as artifact independence.

Validated finite policies generate in-contract repair, classification review,
regression and strategy nodes. Successful independent regression restores
capabilities without rewriting original failed reports. Identical prose/model
changes are not progress; exhaustion cannot spawn endless replacements or
satisfy dependants.

## Steering and native recovery

Direct user changes commit immediately, including while a plan question or old
worker is pending. The revision revokes old authority and returns protected
native task names for coordinator interruption. A racing stale publication
returns superseded without evidence, slot consumption or retry permission.

Reconciliation waits for complete supported native quiescence evidence and then
observes the actual project state. Old worker writes are not presumed reverted.
New planning/execution cannot cross that barrier. Timeout, silence or a stop
hook alone cannot establish loss. Verified loss uses immutable predecessor
lineage and finite recovery budgets; unknown native state does not authorize
duplicate workers.

## Decisions, transactions and views

Decision questions bind exact current evidence and user answers remain original
source text. Changed review boundaries invalidate old unanswered packets without
blocking permitted current work. In-contract correction does not create another
scope decision. Mandatory post-result closure review remains separate from plan
approval and is necessary, but not sufficient, for a successful closure verdict.

Every command atomically records transition, event and server-derived receipt.
Only an ambiguous response permits identical reconciliation. Changed input
conflicts; an explicit non-replayed success never permits a duplicate spawn.
Unsupported old storage/publication formats are rejected without migration or
generic compatibility rendering.

Plan/report Markdown is a derived private view. Readback and digest verification
precede a ready link. A post-commit view failure preserves the report, and exact
reconciliation repairs only the view. The coordinator copies verified links
byte-for-byte rather than reconstructing private identities or paths.

Native completion cannot be inferred from a report count or terminal pane alone.
Final verification and documentation bind the latest sealed generation; closure
cannot attest continuous filesystem immutability after the last observation.

## Verification

Local operation/profile matrices, DAG races, artifact transitions, semantic
steering, family selection, recovery, bounded remediation, projection repair and
closure tests are distinct from native live qualification. Conditional tools
are exercised only with real preconditions, never for out-of-state coverage.

See [verification](../../project/verification.md),
[governance and decisions](../advisory-governance/index.md),
[human-readable views](../human-readable-task-views/index.md) and the
[Completion checklist](../../project/typed-orchestration-integrity.md#11-completion-checklist).

<!-- GENERATED:END -->
