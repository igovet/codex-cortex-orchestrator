---
name: knowledge-harvest
description: Source-backed project knowledge maintenance. Load only after the user explicitly activates cortex:orchestrator with harvest or harvest-refresh; never infer this route from repository state.
---

# Knowledge Harvest

Apply this skill only to an explicitly selected harvest or harvest-refresh
route. Ordinary product work does not implicitly authorize a census or broad
documentation rewrite.

Build a durable functional map of the repository, not a task journal or recent
commit summary. Documentation should let a new engineer discover each in-scope
capability, its owning code, runtime behavior, and verification or operating
path.

Harvest workers load this complete skill through standard Codex skill loading,
including its feature-census guidance below. Its inventory, coverage, page-content
and validation rules apply before census/planning work. Exact advertised SKILL.md
and needed declared Markdown reference reads are allowed; plugin implementation
and TOML inspection are not. The coordinator receives concise report previews;
it does not open project indexes.

Every repository read, project analysis, documentation change, command and
completeness check is worker-owned. The coordinator reads previews, the current
pipeline and selected opening decision briefs, then chooses bounded assignments.
Graph/search, index routing and repository enumeration below are worker instructions.

Use the orchestrator's index-driven documentation routing instructions for every
harvest worker. A missing or unreadable project knowledge index justifies a
bounded census delegation and never coordinator source inspection. A worker's
evidence that supplied documentation is stale, conflicting, or incomplete
drives only task-relevant corrective ownership; it does not by itself widen the
harvest scope.

## Modes

- **Harvest:** Maintain a complete baseline. If no current source-backed
  coverage manifest establishes that the feature registry covers the
  repository, perform a full census. With a credible baseline, inspect changed
  and impacted surfaces and reconcile them against the complete inventory.
- **Harvest refresh:** Rebuild the inventory from current source without
  trusting prior coverage claims, audit every project and feature document, and
  run an independent post-write completeness review. Finish only when a second
  planning comparison proposes no factual changes or new feature page.

## Shared skills and reporting

Apply skill `cortex:tool-discipline` to calls. Draft creator guidance and the worker
profile supply the report requirements. Load a declared example only for an assigned
content question; do not load `cortex:cortex-control` merely for ordinary reporting.
After context loss, load skill `cortex:context-compaction` and follow its rereading procedure.

## Evidence topology

This topology is a recommendation, not backend phase ordering. The coordinator
may merge, omit, reorder, repeat, or add delegations when task evidence supports
that decision.

1. **Scope:** For a broad repository, create a read-only `planner` delegation
   to identify applications, services, packages, runtime processes, deployment
   surfaces, integrations, likely functional domains, and non-overlapping
   discovery ownership. Scope partitions evidence; it does not choose the
   solution.
2. **Domain census:** Create one `explorer` delegation per bounded domain,
   normally 2–8 in parallel for a large repository. Each inventories routes,
   commands, jobs, screens, handlers, policies, state, persistence,
   integrations, configuration, deployment paths, failure paths, and
   behavior-revealing tests. Give each the relevant finalized evidence reference
   when applicable.
3. **Architecture synthesis:** Create an `architect` delegation with the
   relevant census evidence references. It deduplicates features, defines stable
   boundaries, maps cross-domain flows and shared infrastructure, identifies
   ADR-worthy decisions, and proposes the documentation taxonomy.
4. **Planning:** When ownership, dependencies, acceptance, or verification
   would benefit, create a read-only `planner` delegation with the available
   synthesis reports. Its work breakdown remains advisory.
5. **Documentation:** Create one or more `technical_writer` delegations. Use
   one writer for a small repository. For a large repository, parallelize only
   across non-overlapping feature paths and give exactly one writer ownership
   of `docs/project/` plus `docs/features/index.md`. Writers verify
   consequential facts in current source or tests.
6. **Completeness review:** Create an independent `code_reviewer` delegation
   to compare the fresh source inventory with the coverage matrix and written
   pages. Unmapped surfaces, placeholders, thin pages, broken source links,
   undocumented failure paths, and unsupported coverage claims become
   actionable report findings and may justify corrective writer delegations.
7. **Verification:** Create a non-mutating `build_verification` delegation for
   links, paths, generated blocks, formatting, repository-native documentation
   checks, and the final coverage statement.

Independent delegations may run concurrently. A delegation that genuinely
needs earlier evidence receives the relevant finalized evidence references after
those publications
exist. The model owns every adaptation and completion decision.

## Relationship to the final documentation stage

A harvest or harvest-refresh is documentation-impacting by definition. Its
documentation delegations serve as the dedicated documentation-sync worker or
workers for `docs/project/`, `docs/features/`, and any affected public
documentation. After the final writing or corrective delegation, a separate
worker verifies documentation through the completeness-review and verification
evidence above. The coordinator decides readiness from the meaningful previews of those worker
checks and the current pipeline. It may read selected opening decision briefs, but never detailed report evidence, documentation
indexes or project files. Missing evidence requires another worker check, not
coordinator inspection.

Do not use a task-level `documentation not required` outcome for a completed
harvest, because documentation is the requested artifact. Public documents
that the worker reports as unaffected should remain unchanged rather than
receiving meaningless edits.

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

Every completed harvest contains all five listed `docs/project/` files. The
project index links the other four. Every non-excluded coverage row links a
canonical `docs/features/<feature>/index.md` entry point; a flat
`docs/features/<feature>.md` page alone is not canonical.

`docs/features/index.md` is the coverage manifest. It records census scope,
source categories, discovered features and pages, unmapped surfaces,
exclusions, known unknowns, and coverage status. “Complete” means every
in-scope feature-bearing surface is mapped or explicitly excluded with
evidence, not a guessed percentage of files.

Feature pages are behavior-complete rather than token summaries. Split a large
feature into focused workflow, state/data, interface, configuration, operation,
or verification pages while keeping `index.md` as its canonical entry point.

## Delegation reports

Each worker receives its concrete assignment and mandatory requirements
directly. It lists the report catalogue, selects only relevant materials, and
reads only needed pages before acting. It publishes a free-form Markdown report
through the same writer as every other profile. The report states completed
work and every incomplete or failed check truthfully. The live advertised
contract alone defines tool arguments. No specific report headings are required.

Report IDs are durable evidence references, not proof that work is complete.
A successor reads only the reports it needs. A partial or failed report can
support replacement, narrower research,
parallel progress elsewhere, or an honest residual-risk statement.

A material user decision is reported clearly to the coordinator, who presents
it through the normal user interaction boundary. Internal storage and worker coordination conditions alone are not user questions.
For a genuine question, load skill `cortex:coordinator-communication` through
Codex and follow it: detailed chat context and
answer alternatives and their consequences as ordinary chat text.

## Evidence and preservation

Source, tests, executable configuration, schemas and migrations, and deployment
definitions outrank generated documentation. Codebase Memory is the preferred
worker route for structural project-code discovery when available: bind it to
the exact canonical project root and use its graph evidence before local search.
If it is unavailable, denied, timed out, erroneous, unusable, or insufficient,
record that concrete limitation and use exactly one bounded repository-native
enumeration or file/text-search fallback. Check index coverage for every cited
graph path and bounded scope; confirm consequential claims in current source.
A fallback must remain limited to the requested scope; never silently fall back
or chain multiple fallback searches.

Run the repository's deterministic knowledge-census validator when available.
Its result is advisory evidence for the model and verification worker, never a
backend gate, authorization decision, or substitute for source confirmation.

Use `<!-- GENERATED:START -->` and `<!-- GENERATED:END -->` for refreshable
facts. Preserve text outside generated blocks and do not overwrite manual
decisions, gotchas, or feature explanations without evidence and explicit
scope. Never expose secrets, source dumps, private operational values, or
personal data.

Incomplete reports or unresolved dependencies may affect the model's readiness recommendation, but never prevent
useful delegation, evidence access, corrective work, or a truthful final answer.

<!-- BEGIN HOST-ATTACHED FEATURE CENSUS -->

# Feature census and completeness contract

## Contents

1. Inventory surface
2. Feature boundary rules
3. Coverage matrix
4. Required feature documentation
5. Mode-specific validation
6. Failure conditions

## Inventory surface

Build the inventory independently of existing docs. Inspect every applicable
category, recording counts and representative authoritative paths:

- deployable applications and services;
- packages, bounded domains, and shared runtime libraries;
- API routes, controllers, handlers, RPC/GraphQL endpoints, and public SDKs;
- user-facing screens, navigation flows, commands, and administrative tools;
- background workers, schedulers, queues, consumers, pollers, reconcilers, and
  maintenance jobs;
- domain policies, use cases, state machines, orchestration flows, and feature
  flags;
- database schemas, migrations, repositories, caches, files, and external
  state stores;
- third-party adapters, webhooks, exchanges, payment/auth/model providers, and
  other integration boundaries;
- configuration groups, environment-controlled behavior, safety limits, and
  compatibility switches;
- deployment manifests, process definitions, health checks, bootstrap paths,
  observability, and operational recovery;
- tests and fixtures that reveal supported behavior, invariants, negative
  paths, or hidden feature ownership.

Use repository-native file enumeration as a completeness backstop. Codebase
Memory is the preferred worker route for project-code discovery when available:
bind it to the exact canonical project root and use graph search, path tracing,
then exact snippets before local search. If it is unavailable, denied, timed
out, erroneous, unusable, or insufficient, record that concrete limitation and
use exactly one bounded file/text-search fallback. Check graph coverage for
every cited graph path and bounded scope, then confirm consequential claims in
current source. Do not silently fall back or chain additional local searches.
Graph evidence helps explain relationships but never replaces source-tree
enumeration. Generated, vendored, build, cache, and dependency directories may
be excluded once with a recorded reason. Tests are evidence and coverage
signals, not separate product features unless the repository ships a testing
product or framework.

## Feature boundary rules

A feature is a cohesive capability with recognizable developer, operator, or
user behavior and an owning runtime path. Prefer stable behavioral boundaries
over directory names. A feature may cross packages or services. Shared
infrastructure may have its own page when it exposes consequential contracts,
failure behavior, or operational ownership.

Do not create one page per generic helper, model, folder, or generated file.
Do not collapse a large service into one shallow “service overview” page when
it owns multiple workflows, state machines, commands, integrations, or safety
contracts. Split it into discoverable feature pages and link shared flows.

## Coverage matrix

The feature index must contain a source-backed matrix with at least:

It must also use these five literal Markdown section headings; prose mentions
do not substitute for them:

```text
## Coverage matrix
## Inventory totals
## Unmapped surfaces
## Exclusions
## Known unknowns
```

Use these seven labels verbatim as the coverage table headers, in this order;
additional columns may follow:

```text
| Feature | Runtime owner | Entry points | Source evidence | Documentation | Verification | Status |
```

| Field | Required meaning |
| --- | --- |
| Feature | Stable human-readable capability name |
| Runtime owner | Application/service/package/process that executes it |
| Entry points | Routes, commands, jobs, screens, events, or symbols |
| Source evidence | Key project-relative paths |
| Documentation | Canonical feature page(s) |
| Verification | Relevant tests or safe checks |
| Status | `covered`, `documented`, `verified`, or `excluded` in a final accepted manifest; `partial` or `unknown` remains in progress and prevents a complete-coverage claim |

Also record totals for inventoried feature-bearing surfaces, mapped surfaces,
documented features, partial/unknown items, unmapped items, and exclusions.
Coverage acceptance requires zero unexplained unmapped surfaces. Partial or
unknown items remain visible and prevent a claim of complete behavioral
coverage unless their boundary is explicitly out of scope.

Every non-excluded row must link its `Documentation` cell to a canonical page
below `docs/features/`. Every row must contain all seven required cells; prose
elsewhere in the file does not substitute for a missing header or cell.

## Required feature documentation

Each feature entry point must lead to documentation covering all applicable
sections:

1. purpose, actors, and externally observable behavior;
2. runtime owner, entry points, and key source map;
3. main workflows and alternate/negative paths;
4. state model, lifecycle, invariants, and concurrency/idempotency rules;
5. data ownership, persistence, schemas, cache behavior, and consistency;
6. APIs, events, commands, UI interactions, and integration contracts;
7. configuration, feature flags, defaults, limits, and precedence;
8. authorization, trust boundaries, sensitive-data handling, and safety rules;
9. failure modes, retries, fallback, recovery, rollback, and degradation;
10. observability, health, logs/metrics, deployment, and operational gotchas;
11. verification commands, tests, fixtures, and evidence limitations;
12. related features, decisions, known gaps, and unresolved questions.

Omit an inapplicable section only with a short reason. Avoid placeholder text,
generic prose that could describe another project, and unsupported production
claims. Cite paths and symbols densely enough that a maintainer can verify the
page without repeating repository-wide discovery.

## Mode-specific validation

### Harvest

1. Look for a current coverage matrix with evidence and zero unexplained gaps.
2. If absent, partial, stale, or contradicted, run a full baseline census.
3. If valid, detect source/config/test/deployment changes and their graph or
   caller impact, then update both affected pages and the global matrix.
4. Sample unchanged domains and reconcile top-level process/package inventory
   so an incremental run cannot preserve a silently incomplete baseline.

### Harvest refresh

1. Rebuild the complete inventory without trusting current feature pages.
2. Map the fresh inventory to the existing registry and audit every page.
3. Rewrite generated sections and add/split pages until the matrix has no
   unexplained gaps.
4. Run a separate read-only completeness review from the source inventory.
5. Repeat the documentation planning comparison. Pass only when it proposes no
   factual changes and no new feature page.

## Failure conditions

The completeness contract fails when required inventory, coverage, or
documentation evidence is missing, contradictory, or silently excluded.
The repository's deterministic census validator may report these conditions as
advisory verification evidence; it never authorizes work, blocks backend tools,
or replaces independent source confirmation and the model's readiness judgment.

## Corrective findings

Record a finding and dispatch the appropriate corrective owner instead of
claiming completion when:

- only recent commits were scanned without a validated complete baseline;
- a large repository received one broad explorer with no domain partitioning;
- the feature registry maps only top-level services while omitting their
  distinct workflows and capabilities;
- inventory or coverage counts are absent;
- any in-scope surface is unmapped, silently dropped, or marked with a vague
  exclusion;
- feature pages omit consequential behavior, state, configuration, failure,
  integration, operations, or verification details;
- documentation claims were copied from delegation reports without source/test checks;
- refresh lacks an independent post-write census and no-change second pass.

<!-- END HOST-ATTACHED FEATURE CENSUS -->
