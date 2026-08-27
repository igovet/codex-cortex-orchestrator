# Plugin packaging and validation

<!-- GENERATED:START -->

## Purpose

Cortex 12.0.0 is packaged as a repository-local Codex plugin and distributed to
users through the GitHub Marketplace source documented in README. Manifest,
MCP server, advisory profiles, bundled skills, runtime, tests,
and release-facing documentation must describe the same V12 contract.

## Package files

- [plugin.json](../../../plugins/cortex/.codex-plugin/plugin.json) carries V12 version and UI metadata.
- [.mcp.json](../../../plugins/cortex/.mcp.json) launches the Python MCP server.
- [marketplace.json](../../../.agents/plugins/marketplace.json) defines the GitHub Marketplace entry.
- [cortex.py](../../../plugins/cortex/scripts/cortex.py) exposes the eleven-tool facade.
- [public_contracts.py](../../../plugins/cortex/scripts/cortex_runtime/public_contracts.py) defines the uniform catalog.
- [v12_contract.py](../../../plugins/cortex/scripts/cortex_runtime/v12_contract.py) defines bounded task/report constants and report-digest semantics.
- [v12_store.py](../../../plugins/cortex/scripts/cortex_runtime/v12_store.py) owns schema-v1 storage.
- [v12_projections.py](../../../plugins/cortex/scripts/cortex_runtime/v12_projections.py) owns host-private plan/report Markdown materialization.
- [v12_maintenance.py](../../../plugins/cortex/scripts/cortex_runtime/v12_maintenance.py) provides the task-anchored host-private operator CLI outside MCP.
- [worker_message.py](../../../plugins/cortex/scripts/cortex_runtime/worker_message.py) renders the attested native worker message.
- [profiles.json](../../../plugins/cortex/profiles.json) defines advisory roles and model recommendations.
- [orchestrator/SKILL.md](../../../plugins/cortex/skills/orchestrator/SKILL.md) defines the authoritative outcome-first coordinator contract.
- [cortex-control/SKILL.md](../../../plugins/cortex/skills/cortex-control/SKILL.md) defines the authoritative eleven-tool and nonblocking semantics.
- [coordinator-communication/SKILL.md](../../../plugins/cortex/skills/coordinator-communication/SKILL.md) defines the mandatory coordinator-to-user policy.
- [validate-cortex-marketplace.py](../../../scripts/validate-cortex-marketplace.py) validates repository package structure.
- [cortex_release_candidate.py](../../../scripts/cortex_release_candidate.py) builds the explicit source candidate and docs closure.
- [verify-cortex-release.py](../../../scripts/verify-cortex-release.py) validates source or committed candidates.
- [sync-cortex.sh](../../../scripts/sync-cortex.sh) supports repository-development/local-source synchronization and check/dry-run modes.

## Current package contract

The package exposes exactly eleven tools with identical coordinator and worker
schemas. Runtime validation uses the canonical registry's closed input schemas
and validates successful results against each advertised `outputSchema`. Success
returns canonical JSON as text plus `structuredContent` with `isError=false`.
Caller-correctable errors are bounded sanitized text-only `isError=true` results
with no `structuredContent`; server-state faults use sanitized JSON-RPC internal
errors. The server is a storage/integrity sidecar and contains no V11
control-plane route.

Only `create_task` accepts explicit `project_root`; it stores the canonical
project association and returns a compact `task_ref` for later task-anchored
calls. The durable `task_id` in results and ledger evidence is non-callable.
The seven task-anchored tools use `task_ref`: `inspect_task`,
`create_delegation`, `set_governance_mode`, `record_initiative`,
`inspect_governance`, `submit_governance_closure`, and
`record_user_decision`. The entity-derived tools use their compact emitted
refs: `read_delegation` uses `delegation_ref`, `submit_report` uses
`delegation_ref` (and continuation `report_ref`), and `read_reports` uses
`report_refs`; these tools do not accept `task_ref` or durable `*_id` values.
No host metadata, plugin `cwd`, or hook binds a project. `create_task` records
the exact original request and a concrete language tag beside the English
objective and four non-empty, meaningful result-contract lists; `context`
remains optional arbitrary JSON. Delegation `scope` is required non-empty text
defining the concise worker-ownership boundary, not an object, and detailed
execution belongs in `instructions`. Exact model and effort are required
together. Closure selects the existing subject with `subject_type` plus the
compact `subject_ref`; durable `subject_id` is evidence only.

Reports support `single`, `begin`, `append`, `finalize`, and `abort` under
bounded chunk, assembling, retained-content, and response limits. Plan reports
carry informational/required review policy and immutable digest identity.
`record_user_decision` appends coordinator-attributed original/English evidence
for an exact subject; plan/report decisions require that digest. Neither review
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

The installable package contains no lifecycle hooks and no lifecycle
hook script. Hook trust is not an installation step. Native subagent dispatch
remains outside the MCP server.

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

`./scripts/sync-cortex.sh` is for this repository's local source development
and final explicitly authorized checkout synchronization. It must not replace
the public GitHub Marketplace instructions. `--dry-run` and `--check` are
non-installing validation modes.

## Validation requirements

The release candidate must prove:

- manifest and Marketplace version 12.0.0 parity;
- exact eleven-tool registry/runtime parity;
- uniform participant catalog, closed input schemas, advertised successful
  `outputSchema` validation, and the distinct success/correctable-error/server
  fault transport shapes;
- explicit root only on `create_task`, `task_ref` on the seven task-anchored
  tools, `delegation_ref`/`report_ref`/`report_refs` on entity-derived tools,
  `subject_ref` and `initiative_ref` where applicable, arbitrary optional task
  context, bounded task contract/language fields, required
  non-empty textual delegation scope with object rejection, exact model/effort,
  and required closure subject fields;
- schema-v1 bootstrap/additive migration, project isolation, idempotency,
  concurrency, and ordered report reads;
- bounded incremental inspection with compact report references and bodies or
  whole chunks only through bounded `read_reports`;
- single/chunked report lifecycle, manifest digest and quota enforcement, plan
  review metadata, append-only digest-bound user decisions, safe resume, and
  task-scoped `report_chunk_appended` chronology/backfill behavior;
- host-private projection layout, owner-only modes, atomic writes, tamper
  conflict preservation, source-sequence/digest verification, and zero writes
  under `project_root`;
- private state modes, pre-open symlink/non-regular-file rejection, and
  oversized-frame drain/recovery;
- append-only assessments/revisions/closures and nonblocking initiative
  warnings/verdicts;
- self-contained bundled skill/profile lint covering coordinator-only,
  textual-scope, knowledge-contract, closed direct-read routing, worker-owned
  project-state verification, and conditional-documentation invariants without
  claiming model behavior;
- exact model/effort support, Luna override omission, and no server fallback;
- advisory profile parity across registry and TOML files;
- absence of lifecycle hooks and lifecycle hook code;
- packaged maintenance-module parity without changing the eleven-tool registry,
  including task/shard anchoring, confirmation strings, backup validation,
  offline restore, projection safety, canonical-data retention, and zero
  project/V11 writes;
- preservation of V11 database bytes;
- recursively valid public documentation links and documented commands;
- absence of secret-prone, runtime-state, symlink, bytecode, or nested
  Marketplace artifacts.

## Security-sensitive packaging rules

The MCP configuration invokes the packaged server directly through `python3`
and therefore requires Python 3.11+ in the Codex launch environment. Malformed
calls return bounded sanitized errors. Task/report content, secrets,
credentials, personal data, raw prompts, host identities, and private diagnostics
must not enter package logs, fixtures, docs, issues, or release evidence.

Package metadata must not claim waves, gates, plan authority, capability
handoff, host lifecycle binding, read receipts, profile enforcement, governance
promotion, closure authority, repair/rework waves, resource locks, or
server-owned recovery.

It also must not imply that the coordinator may inspect source or operate on
the target project, directly verify worker work, infer `project_root`, accept
an object delegation scope, use shell/search/graph for knowledge routing, check
project-local artifacts or state, independently reconstruct documentation
routing, or skip the report-grounded documentation-impact decision before
closure.

Package evidence must not treat a generated Markdown view as canonical, a
delegation view as worker input, a user decision as cryptographic attestation,
or a plan-review pause as a backend permission gate. All internal/durable
operational content is English; verbatim user language belongs only in labeled
original fields beside English normalizations, while coordinator summaries and
verified ready links follow the user's language.

## Verification

Run the commands in [verification.md](../../project/verification.md). The
release/protocol test is the black-box V12 contract proof; package validation,
the self-contained skill/profile lint, `git diff --check`, release-candidate
validation, and local source `sync-cortex.sh --dry-run` provide bounded
supporting evidence.

<!-- GENERATED:END -->
