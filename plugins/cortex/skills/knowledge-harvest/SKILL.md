---
name: knowledge-harvest
description: Internal Cortex knowledge-route overlay. Load only after the user explicitly activates cortex:orchestrator with harvest or harvest-refresh; never infer this route from repository state.
---

# Knowledge Harvest

Build a durable functional map of the repository, not a task journal or a
summary of recent commits. The documentation must let a new engineer discover
every in-scope capability, its owning code, its runtime behavior, and how to
verify or operate it.

Before planning or dispatching a harvest, read
[feature-census.md](references/feature-census.md) completely. Its inventory,
coverage, page-content, and validation contracts are authoritative evidence
guidance; gaps are routed to corrective workers rather than becoming a Cortex
stop.

## Modes

- **Harvest:** Maintain a complete baseline. If no current coverage manifest
  proves that the existing feature registry covers the repository, perform a
  full census before considering incremental changes. With a valid baseline,
  scan changed and impacted surfaces, then reconcile them against the complete
  existing inventory. Never reduce the task to commits since the last docs
  change when the baseline itself is incomplete.
- **Harvest refresh:** Ignore prior coverage claims during discovery, rebuild
  the inventory from source, audit every project and feature document, and run
  an independent post-write completeness pass. A refresh is complete only when
  the second pass finds no unmapped in-scope surfaces and proposes no factual
  documentation changes.

## Required pipeline

This is the canonical evidence pipeline shape. It is advisory guidance for
the orchestrator: the chosen route may reorder, merge, omit, or add phases
when verified evidence or the user's task contract requires it.

Use this sequence as the default evidence route. The orchestrator owns the
chosen pipeline and may reorder, merge, omit, or add corrective phases when
verified evidence or the user's task contract calls for it; no Planner phase
is mandatory merely because this route is a harvest.

1. **Scope recommendation:** Dispatch a read-only Planner Scope worker when a
   broad inventory will improve the route. It can enumerate top-level
   applications, services, packages, runtime processes, deployment surfaces,
   integrations, and likely functional domains. It publishes a discovery brief,
   relevant context files, and all validated non-overlapping discovery domains;
   it does not design the solution or close material intent questions.
2. **Domain census:** When domain evidence is more useful than a scope pass,
   use one
   read-only `explorer` per bounded domain, normally 2–8 in parallel for a
   large repository. Each explorer exhaustively inventories its assigned
   domain and traces feature-bearing surfaces through entry points, workflows,
   state, persistence, configuration, integrations, failure paths, and tests.
   Give each explorer the available scope conclusion as predecessor evidence
   when that projection exists.
3. **Architecture synthesis:** Dispatch `architect` with the scope,
   discovery, and all domain handoffs. It deduplicates features, defines stable
   feature boundaries, maps cross-domain flows and shared infrastructure,
   identifies ADR-worthy decisions, and emits the canonical documentation
   taxonomy.
4. **Planning recommendation:** If a planning artifact will clarify ownership,
   dependencies, acceptance, or verification, the orchestrator may dispatch a
   read-only Planner after the available discovery and architecture handoffs.
   The Planner's `planning` artifact is advisory input; the orchestrator may
   continue without it or choose a different route.
5. **Documentation:** Dispatch one or more `technical_writer` workers. Use one
   writer for a small repository. For a large repository, parallelize only
   across non-overlapping `docs/features/<domain-or-feature>/` paths and assign
   exactly one writer to `docs/project/` plus `docs/features/index.md`. Every
   writer uses the available architecture and planning handoffs and verifies
   consequential facts in current source or tests instead of copying worker
   prose blindly.
6. **Completeness review:** Dispatch `code_reviewer` after documentation to
   independently compare the fresh source inventory with the coverage matrix
   and written pages. Any unmapped surface, placeholder, thin page, broken
   source link, undocumented failure path, or unsupported coverage claim
   becomes a durable review finding and triggers corrective documentation work
   until the gap is resolved or the user must decide a task scope/acceptance
   issue.
7. **Close:** Dispatch `build_verification` to check links, paths, generated
   blocks, formatting, repository-native documentation checks, and the final
   coverage statement without editing files.

Planning is deliberately separate from early scope when the orchestrator
selects both: scope partitions evidence, while planning can inform the
implementation/documentation decision. Every worker publishes one semantic
conclusion through `submit_attempt`; the tool registry owns its call contract.
Canonical results and handoff projections are server-derived outputs.

The coordinator owns domain partitioning and may change the future pipeline
when verified evidence exposes additional domains, shared ownership, or an
unsafe overlap. Same-wave workers do not depend on one another; put dependent
work in a later wave.

## Output structure

Maintain these canonical documents:

```text
docs/project/index.md
docs/project/conventions.md
docs/project/verification.md
docs/project/decisions.md
docs/project/gotchas.md
docs/features/index.md
docs/features/<feature>/index.md
docs/features/<feature>/<focused-detail>.md
```

Every completed harvest must contain all five `docs/project/` files above,
even when one records only a verified absence or an evidence boundary; the
project index must link the other four. Every non-excluded coverage row must
link a canonical `docs/features/<feature>/index.md` entry point. A flat
`docs/features/<feature>.md` page may be migrated into that directory but
cannot satisfy the final structural gate by itself.

`docs/features/index.md` is the coverage manifest. It must record the census
scope, source categories checked, every discovered feature and its page,
unmapped surfaces, justified exclusions, known unknowns, and coverage status.
“Complete” means every in-scope feature-bearing surface is mapped or explicitly
excluded with evidence; it never means a guessed percentage of files.

Feature pages must be behavior-complete, not token summaries. Split large
features into focused pages for workflows, state/data, interfaces,
configuration, operations, or verification while keeping `index.md` as the
canonical entry point.

## Worker result shape

Harvest workers use the same explicit worker authority as every Cortex worker:
preserve the exact opaque value from the native dispatch on every worker-side
Cortex call. Never infer, replace, or reconstruct it from a
session, environment, hook, path, process, task record, or parent message.
Server-derived lifecycle and evidence facts are never worker-authored input.

`submit_attempt` uses only the active public tool contract. A Planner publishes
its work breakdown as the semantic conclusion, not through a second planning
transport. When validation returns a repair route, retry only as its public
repair contract permits; there is no replacement worker. Scope publishes its
discovery brief, context paths, and domain boundaries as semantic evidence.

Repair material is opaque. Copy it directly from structured tool output; never
decode, reconstruct, or manually transcribe it. A successful terminal response
from `submit_attempt` ends every worker Cortex call and requires the advertised
terminal final message, with no later worker event or result read. A
nonretryable
response likewise ends all task-scoped calls and is reported neutrally without
capability values.

## Evidence and preservation

Source, tests, executable configuration, schemas/migrations, and deployment
definitions outrank generated documentation. Codebase Memory may accelerate
architecture and trace discovery, but filesystem inventory and current source
must confirm consequential and completeness claims.

Use `<!-- GENERATED:START -->` and `<!-- GENERATED:END -->` for refreshable
facts. Preserve text outside generated blocks and do not overwrite a manual
ADR, gotcha, or feature explanation without evidence and explicit scope. Never
expose secrets, source dumps, private operational values, or personal data.

Every Cortex worker uses only the public worker operation surface. Each call
preserves exact native worker authority; predecessor access is limited only by
the active schema and the server-issued continuation. The worker may checkpoint
semantic facts during execution; critical findings, decision evidence, blockers,
or observed checks should not wait for the final message.

The worker first reads its dispatch briefing and follows only the returned
server continuation until completion. A success exposes only the briefing content and its
server-issued continuation; server receipt recording remains internal. After
success, do not reconstruct its path, shell-read the briefing again, or locally
hash it. If a first operation reports that trusted native spawn observation is
still pending, retry only that same operation with bounded backoff until a
finite deadline. Make no project access, switch no operation, and never spawn a
replacement. An exact successful retry automatically clears the transient
observer failure. At the deadline, or for any other missing or rejected authority,
follow only public fail-closed recovery and return the neutral limitation to the
coordinator; do not inspect a ledger, ask a hook, query an environment, or
substitute an artifact. Same-worker recovery is allowed only when the server
returns it.

A successor reads only each explicitly granted predecessor. Workers do not
emit lifecycle identity, workspace observations, receipts, or evidence markers
as authoritative data. Follow only public diagnostics and retry on the same
attempt when the response explicitly authorizes deterministic correction. A successor may not read an ungranted result
or treat a scoped evidence read as permission to call coordinator lifecycle
operations.
