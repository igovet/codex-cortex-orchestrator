# Plugin packaging and validation

<!-- GENERATED:START -->

## Purpose

Cortex 1.14.15 is packaged as a repository-local Codex plugin and distributed to
users through the GitHub Marketplace source documented in README. Manifest,
MCP server, advisory profiles, bundled skills, runtime, tests,
and release-facing documentation must describe the same V12 contract.

## Package files

- [plugin.json](../../../plugins/cortex/.codex-plugin/plugin.json) carries V12 version and UI metadata.
- [.mcp.json](../../../plugins/cortex/.mcp.json) launches the Python MCP server.
- [marketplace.json](../../../.agents/plugins/marketplace.json) defines the GitHub Marketplace entry.
- [cortex.py](../../../plugins/cortex/scripts/cortex.py) exposes the fourteen-tool semantic facade.
- [public_contracts.py](../../../plugins/cortex/scripts/cortex_runtime/public_contracts.py) defines the complete registry projected by runtime audience.
- [v12_contract.py](../../../plugins/cortex/scripts/cortex_runtime/v12_contract.py) defines bounded task/report constants and report-digest semantics.
- [v12_store.py](../../../plugins/cortex/scripts/cortex_runtime/v12_store.py) owns schema-v1 storage.
- [v12_projections.py](../../../plugins/cortex/scripts/cortex_runtime/v12_projections.py) owns host-private plan/report Markdown materialization.
- [v12_maintenance.py](../../../plugins/cortex/scripts/cortex_runtime/v12_maintenance.py) provides the task-anchored host-private operator CLI outside MCP.
- [worker_message.py](../../../plugins/cortex/scripts/cortex_runtime/worker_message.py) renders the attested native worker message.
- [profiles.json](../../../plugins/cortex/profiles.json) defines advisory roles and model recommendations.
- [orchestrator/SKILL.md](../../../plugins/cortex/skills/orchestrator/SKILL.md) defines the authoritative outcome-first coordinator contract.
- [cortex-control/SKILL.md](../../../plugins/cortex/skills/cortex-control/SKILL.md) defines the authoritative fourteen-tool semantic and nonblocking contract.
- [coordinator-communication/SKILL.md](../../../plugins/cortex/skills/coordinator-communication/SKILL.md) defines the mandatory coordinator-to-user policy.
- [validate-cortex-marketplace.py](../../../scripts/validate-cortex-marketplace.py) validates repository package structure.
- [cortex_release_candidate.py](../../../scripts/cortex_release_candidate.py) builds the explicit source candidate and docs closure.
- [verify-cortex-release.py](../../../scripts/verify-cortex-release.py) validates source or committed candidates.
- [sync-cortex.sh](../../../scripts/sync-cortex.sh) supports repository-development/local-source synchronization and check/dry-run modes.
- [cortex-dev](../../../scripts/cortex-dev) starts an interactive Codex session in the isolated persistent candidate runtime.
- [cortex-dev-reset](../../../scripts/cortex-dev-reset) removes only that exact candidate after explicit confirmation.

## Current package contract

The package exposes exactly fourteen semantic tools with identical coordinator and worker
schemas. `tools/list` advertises the canonical registry's closed input schemas and
complete descriptions in one response below 65,536 bytes. Optional MCP
`outputSchema` declarations are omitted from discovery. The complete family
result schemas remain private runtime contracts and are used to
validate every successful result before transport. Success returns canonical JSON as text plus
`structuredContent` with `isError=false`.
Every advertised tool description mechanically includes the exact required
input-property list derived from its closed `inputSchema`; the schema remains
the authoritative call contract. Caller-correctable errors are bounded
sanitized `isError=true` results with both text and a matching sanitized
`structuredContent.error`. Multiple missing required properties are returned
as one bounded `details.missing_fields` list and in the recovery action, so the
caller can correct the complete request at once. Server-state faults use
sanitized JSON-RPC internal errors. The server is a storage/integrity sidecar and contains no V11
control-plane route.

Every input schema also advertises the complete compact UTF-8 JSON aggregate
bound independently of per-field limits. Root aggregate diagnostics are
value-blind and bounded. Large successful structured results use a fixed text
notice rather than duplicating the same body across both MCP content channels,
keeping assignment authority and the full response below the physical frame.

The standard MCP `tools/list` response returns the complete unchanged
fourteen-tool catalogue in one page. A release fails validation if the final
JSON-RPC envelope exceeds 65,536 bytes, well below the 256 KiB physical JSONL
frame bound. The MCP companion is required at session startup and declares
`omit_tools_from: ["code_mode", "deferred"]`; Desktop must project the
complete catalogue as direct model tools before the first turn or fail
initialization explicitly. An
unavailable or truncated declaration is a fail-closed condition, and the host
must not infer or guess a mutation contract.

Only `open_task` accepts explicit `project_root`; it stores the canonical
project association and returns a compact `task_ref` for later task-anchored
calls. The durable `task_id` in results and ledger evidence is non-callable.
The task-anchored tools use `task_ref`: `read_task`, `open_assignment`,
`assess_governance`, `close_task`, the six narrow decision operations, and the
three worker-owned publication operations. Publications accept the worker-scoped
`task_ref`; private assignment and continuation identity is derived from the
connection after the worker has consumed its server-rendered assignment view;
there are no separate delegation/report-read or initiative inspection tools, and
no public call accepts durable `*_id` values, assignment refs, report refs,
initiative refs, cursors, or caller idempotency keys.
No host metadata, plugin `cwd`, or hook binds a project. `open_task` records
the exact original request and a concrete language tag beside the English
objective and four non-empty, meaningful result-contract lists; `context`
remains optional arbitrary JSON. Delegation `scope` is required non-empty text
defining the concise worker-ownership boundary, not an object, and detailed
execution belongs in `instructions`. Exact model and effort are required
together. `close_task` is task-scoped and accepts the exact task reference,
advisory verdict, and bounded evidence; private/internal subject and initiative
ledger identity is never a public argument.

The package keeps finalized-report evidence separate from advisory bookkeeping.
`read_task` exposes `execution_outcome` with `evidence_status`,
`finalized_report_count`, `completed_report_count`, `effective_revision`,
`coverage_status`, and `outcome`. The outcome derives deterministically from
current effective-contract coverage, not report arrival order or historical
claims, and makes no native-lifecycle claim. It also exposes
`advisory_closure` with the current record status. After sufficient evidence,
the coordinator selects `ready`, `ready_with_risks`, or `not_ready`, then
automatically attempts the advisory write and intended bounded inspection;
`ready_with_risks` never requires user confirmation. The closure result returns
`closure_confirmation` with `inspection_status`, `reason`, and `attempts`.
At most one same-idempotency retry is made for a verified transient persistence
or inspection failure. An `unconfirmed` advisory result is disclosed without
changing `execution_outcome` evidence.

The public publication operations accept one complete immutable plan, result,
or documentation evidence payload. Private/internal report assembly state,
chunking, and retained-content limits remain behind the facade. Plan reports
carry informational/required review policy and immutable digest identity.
Product-facing reports support the fixed `cortex/report/{progress,result,synthesis,plan}/v1`
schemas plus additive `cortex/report/{result,synthesis,plan}/v2` schemas. V2
retains structured effective-contract coverage, deviations, unresolved items,
risks, and verification. They may carry one optional unchanged `source_text`
value, with no language tag or translated/original duplicate. Storage-valid
legacy and semantic-invalid reports remain immutable evidence; only a
finalized, completed, semantic-valid canonical plan receives a ready approval
relation.
Planner-authored microtask fields are evidence for the model-owned DAG only;
they do not create backend jobs, scheduling gates, or worker-subtask To-Do
entries.
The matching narrow decision record operation appends coordinator-attributed exact original-language
evidence for an exact subject using one closed canonical field set: neutral
`prompt`, exact `response_original`, and `user_language`; retired `prompt_en`
and `response_en` are not accepted. Plan/report decisions
require the immutable subject digest; plan approval additionally requires the
matching ready-view handle, view digest, and source sequence copied from one
returned relation. Missing or mixed fields fail before mutation. Neither review
nor decision is a backend admission rule.

The bundled orchestration/control skills are the authoritative runtime model
contract. Agent TOML files are advisory role templates without model literals,
effort pins, or public-tool capabilities. `profiles.json` recommends `high` for
Luna, Terra, and Sol while the native transport supports all five effort values
for each model.

The bundled skills make the coordinator orchestration-only. All project
discovery, source/code/configuration access, domain analysis, edits, commands,
builds, tests, and verification are worker-owned. For routing only, the
coordinator must follow the bounded path defined in the orchestrator skill. That
skill alone owns the exact path list and six-part template; profiles consume
the compiled per-delegation contract and do not reroute. Coordinator reads use
only already-known exact paths through a non-shell direct reader; root/path
discovery, shell/search/graph access, and every project-local artifact or state
check are worker-owned. It also defines the
report-grounded conditional documentation stage before closure: material
impact gets documentation-sync plus a separate verifier; no impact gets an
explicit `documentation not required` rationale without an edit.

The installable package contains the bounded activation guard and sanitized
lifecycle observer declared by its hook manifest. Installation requires review
and trust of only those declared callbacks. Hook processes store owner-private
digests and routing categories in `PLUGIN_DATA`; the MCP process resolves the
same exact package data directory from explicit `PLUGIN_DATA`/`CODEX_HOME` or,
when ordinary Desktop supplies neither, from its verified content-addressed
installed cache topology. Because Desktop initialize does not carry child
identity, every connection starts with a neutral complete
catalogue and foreign pending state cannot select its role. That catalogue
grants no authority. The process consumes only
the exact child-bound PreToolUse authorization on the first assignment read,
then advertises the worker projection through `tools/list_changed`; clients
that retain the neutral catalogue remain constrained by authoritative server
role checks. Native subagent dispatch remains outside
the MCP server, and hooks never replace server-side ledger authority.

The SQLite database remains schema v1 under the V12 project namespace, with
additive V12 migration history. Host-private Markdown views are disposable
structured projections for current/immutable plans and finalized reports only;
`v12_projections.py` writes no project file. Other task records remain
SQLite-only.
V11 databases are deliberately excluded from migration and remain untouched.

The task-ID-anchored `v12_maintenance` module is an operator/admin CLI, not an
MCP tool. It supports health, project-shard backup, checkpoint, optimize,
vacuum, strictly offline restore, derived-projection prune/regeneration, and
explicit backup retention. It accepts no root/export path, uses sanitized JSON,
and writes only inside the selected host-private V12 shard. Restore requires
`RESTORE`, exact task/shard confirmation, and `MCP_STOPPED`; package text must
never call it an online restore.

## Public installation versus source synchronization

End users add
`https://github.com/igovet/codex-cortex-orchestrator` at Git ref `main` as a
Marketplace source, then install `cortex@cortex` through Desktop or CLI.

The source published to that Marketplace already has a content-addressed
manifest version, `1.14.15+codex.sha256.<digest-prefix>`. The isolated development
builder uses the identical version rule. At MCP startup the packaged runtime
recomputes the normalized plugin-tree digest before `initialize`; therefore the
production and development paths differ only in installation environment, not
in provenance strength. A plain `1.14.15` manifest is source-mode only and the
Marketplace validator rejects it. The same gate enforces the host's 128-byte
`defaultPrompt` and three-second `SessionEnd` timeout limits.

`./scripts/cortex-dev` is the repository's interactive local source-development
entry point. It creates/reuses the exact persistent `$HOME/.cortex-dev`
candidate, isolates both `HOME` and `CODEX_HOME` (including plugin cache,
configuration, and V12 state), synchronizes this checkout there, and starts
ordinary Codex. It projects only the safe enabled production Codebase Memory
server definition into the candidate and runs that external MCP child with its
owning production HOME; production config and credentials remain unchanged.
Its paired `./scripts/cortex-dev-reset --confirm` helper is
explicit and path-guarded; it refuses stable, repository, broad, symlinked,
and non-regular targets. `./scripts/sync-cortex.sh` remains the explicitly
authorized local-source synchronization operation and must not replace the
public GitHub Marketplace instructions. `--dry-run` and `--check` are
non-installing validation modes.

The normal synchronization mode removes only disposable Python bytecode
(`__pycache__`, `.pyc`, and `.pyo`) beneath `plugins/cortex` before validation,
so a prior local interpreter run cannot make the workflow fail. It also refreshes
the marked model-routing table in `orchestrator/SKILL.md` from `profiles.json`.
Read-only `--dry-run` and `--check` never remove or rewrite source state and
continue to report any residue or catalog drift.

## Validation requirements

The release candidate must prove:

- content-addressed manifest/Marketplace parity with semantic base version
  1.14.15 and a suffix matching the complete normalized plugin payload;
- exact fourteen-tool registry/runtime parity;
- uniform participant catalog, closed advertised input schemas, compact public
  result projections with closed operation-specific handles, private
  successful-result schema validation, a complete catalogue below 65,536
  bytes, and the distinct success/correctable-error/server
  fault transport shapes;
- explicit root only on `open_task`, compact typed task, assignment, report,
  decision, and initiative references on their advertised consumers, arbitrary
  optional task context, bounded task contract/language fields, required
  non-empty textual delegation scope with object rejection, exact model/effort,
  and required closure subject fields;
- schema-v1 bootstrap/additive migration, project isolation, idempotency,
  concurrency, and ordered report reads;
- same-connection worker publication on a persistent source stdio process,
  copied-locator rejection on a second initialized process, monotonic
  coordinator/worker connection roles, sanitized host audience receipts, and
  explicit blocked/aborted successor lineage for confirmed worker loss;
- bounded task inspection and evidence consumption through the advertised
  continuation handles;
- assembled report lifecycle, manifest digest and aggregate quota enforcement, plan
  review metadata, append-only digest-bound user decisions, safe resume, and
  task-scoped `report_chunk_appended` chronology/backfill behavior;
- host-private projection layout, owner-only modes, atomic writes, tamper
  conflict preservation, source-sequence/digest verification, and zero writes
  under `project_root`;
- private state modes, pre-open symlink/non-regular-file rejection, and
  oversized-frame drain/recovery;
- QA/verification adaptation that assigns failed source, candidate, dependency,
  CI, provenance, and harness checks to bounded rework and independently reruns
  the failed and affected gates;
- first-attempt profile admission: the live assignment schema classifies
  packaged owner, review, and planning profiles; light/full owner work requires
  approved planner evidence, while test-only QA correction remains non-owning;
- append-only assessments/revisions/closures and nonblocking initiative
  warnings/verdicts;
- self-contained bundled skill/profile lint covering coordinator-only,
  textual-scope, knowledge-contract, closed direct-read routing, worker-owned
  project-state verification, and conditional-documentation invariants without
  claiming model behavior;
- exact model/effort support, Luna override omission, and no server fallback;
- advisory profile parity across registry and TOML files;
- exact inclusion of the activation guard and sanitized lifecycle observer,
  with every callback using the same `python3 -B` runtime contract as the MCP
  server and exact native `Agent` matchers for pre/post tool observation;
- packaged maintenance-module parity without changing the fourteen-tool registry,
  including task/shard anchoring, confirmation strings, backup validation,
  offline restore, projection safety, canonical-data retention, and zero
  project/V11 writes;
- preservation of V11 database bytes;
- recursively valid public documentation links and documented commands;
- absence of secret-prone, runtime-state, symlink, bytecode, or nested
  Marketplace artifacts.
- source inclusion and source-mode execution of `tests/test_public_mcp_first_call_conformance.py`,
  which exercises a first valid call for every advertised public tool using
  exact emitted handles; and
- source inclusion and documented use of `scripts/cortex-live-smoke` as the
  owned-PTY verifier when the required controlling-terminal preflight cannot
  be supplied by the driver.

## Security-sensitive packaging rules

The MCP configuration invokes the packaged server directly through `python3`
and therefore requires Python 3.11+ in the Codex launch environment. Malformed
calls return bounded sanitized errors. Task/report content, secrets,
credentials, personal data, raw prompts, host identities, and private diagnostics
must not enter package logs, fixtures, docs, issues, or release evidence.

Package metadata must not claim waves, server-selected scheduling gates, plan
authority, bearer capability handoff, profile enforcement, governance
promotion, closure authority, repair/rework waves, resource locks, or
server-owned recovery. Assignment-page receipts are ledger evidence only. The
separate digest-only host audience receipt binds one supported native child to
one MCP connection but is not portable worker identity, task completion,
report evidence, or proof of physical worktree/workspace isolation.

It also must not imply that the coordinator may inspect source or operate on
the target project, directly verify worker work, infer `project_root`, accept
an object delegation scope, use shell/search/graph for knowledge routing, check
project-local artifacts or state, independently reconstruct documentation
routing, or skip the report-grounded documentation-impact decision before
closure.

Package evidence must not treat a generated Markdown view as canonical, a
delegation view as worker input, a user decision as cryptographic attestation,
or a plan-review pause as a backend permission gate. All worker-authored
internal/durable operational content is English. Existing task contracts retain
verbatim user language in labeled original fields; decision records retain the
exact `response_original` without English duplicate fields. Canonical
product-facing report and handoff payloads
carry any needed user-authored source material once in optional unchanged
`source_text`, without language tags or translated/original pairs. Coordinator
summaries and verified ready links follow the user's language.

## Verification

Run the commands in [verification.md](../../project/verification.md). The
release/protocol test is the black-box V12 contract proof; package validation,
the self-contained skill/profile lint, `git diff --check`, release-candidate
validation, and local source `sync-cortex.sh --dry-run` provide bounded
supporting evidence.

<!-- GENERATED:END -->
