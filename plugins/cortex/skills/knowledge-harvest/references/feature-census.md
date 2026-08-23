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

Use repository-native file enumeration as a completeness backstop. Use graph,
architecture, symbol, and trace tools to understand relationships, not as a
replacement for enumerating the source tree. Generated, vendored, build,
cache, and dependency directories may be excluded once with a recorded reason.
Tests are evidence and coverage signals, not separate product features unless
the repository ships a testing product or framework.

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
| Status | `covered`, `documented`, `verified`, or `excluded` in a final accepted manifest; `partial` or `unknown` is in-progress only and blocks the gate |

Also record totals for inventoried feature-bearing surfaces, mapped surfaces,
documented features, partial/unknown items, unmapped items, and exclusions.
The coverage gate passes only with zero unexplained unmapped surfaces. Partial
or unknown items remain visible and prevent a claim of complete behavioral
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
- documentation claims were copied from AttemptResult projections without source/test checks;
- refresh lacks an independent post-write census and no-change second pass.
