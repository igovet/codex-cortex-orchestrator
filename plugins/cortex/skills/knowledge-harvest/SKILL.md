---
name: knowledge-harvest
description: Internal Cortex v12 knowledge-route overlay. Load only after the user explicitly activates cortex:orchestrator with harvest or harvest-refresh; never infer this route from repository state.
---

# Knowledge Harvest

Build a durable functional map of the repository, not a task journal or recent
commit summary. Documentation should let a new engineer discover each in-scope
capability, its owning code, runtime behavior, and verification or operating
path.

Before planning or delegating a harvest, read
[feature-census.md](references/feature-census.md) completely. Its inventory,
coverage, page-content, and validation rules are the evidence contract.

The coordinator uses this contract to create delegations, but never performs
the census, source or documentation inspection, technical synthesis, writing,
or verification itself. Every repository read, project analysis, documentation
change, command, and completeness check is worker-owned and returned by report
ID.

The orchestrator's closed knowledge-route exception still permits only
non-shell direct reads of already-known exact allowed paths. Graph/search and
repository-native enumeration described below are worker instructions, never
coordinator tools. Unknown roots or paths and every project-local artifact or
state check require a worker delegation.

Use the orchestrator's authoritative delegation knowledge contract for every
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
   behavior-revealing tests. Give each the scope report ID when relevant.
3. **Architecture synthesis:** Create an `architect` delegation with the
   relevant census report IDs. It deduplicates features, defines stable
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
needs earlier evidence receives the relevant report IDs after those reports
exist. The model owns every adaptation and completion decision.

## Relationship to the final documentation stage

A harvest or harvest-refresh is documentation-impacting by definition. Its
documentation delegations serve as the dedicated documentation-sync worker or
workers for `docs/project/`, `docs/features/`, and any affected public
documentation. After the final writing or corrective delegation, a separate
worker verifies documentation through the completeness-review and verification
evidence above. The coordinator decides readiness only from those report IDs;
apart from its bounded index-driven routing reads, it never inspects or edits
the documentation itself.

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

Each worker reads its complete delegation and any relevant input reports before
acting. It may publish progress reports when intermediate evidence will help
coordination, then publishes a result or synthesis report with a truthful
`partial`, `completed`, `blocked`, or `failed` status. The active tool
registry defines call fields.

Report IDs are durable evidence references, not proof that work is complete.
A successor reads only the reports it needs. A partial or failed report can
support replacement, narrower research,
parallel progress elsewhere, or an honest residual-risk statement.

A material user decision is reported clearly to the coordinator, who presents
it through the normal user interaction boundary. Internal ledger, dependency,
worker, or closure conditions are not user questions.

## Evidence and preservation

Source, tests, executable configuration, schemas and migrations, and deployment
definitions outrank generated documentation. When Codebase Memory is available
and project instructions select it, bind it to the exact canonical project root
and use graph search/trace/snippet tools first for structural discovery. Check
index coverage for every cited path and bounded scope; confirm consequential
claims in current source. Use repository-native enumeration as the completeness
backstop and at most one bounded text/file-search fallback when the graph is
unavailable, excludes the surface, or returns insufficient evidence. Record
the coverage limitation rather than treating graph absence as proof.

Run the repository's deterministic knowledge-census validator when available.
Its result is advisory evidence for the model and verification worker, never a
backend gate, authorization decision, or substitute for source confirmation.

Use `<!-- GENERATED:START -->` and `<!-- GENERATED:END -->` for refreshable
facts. Preserve text outside generated blocks and do not overwrite manual
decisions, gotchas, or feature explanations without evidence and explicit
scope. Never expose secrets, source dumps, private operational values, or
personal data.

Missing closure, incomplete reports, open initiative state, or unresolved
dependencies may affect the model's readiness recommendation, but never prevent
useful delegation, evidence access, corrective work, or a truthful final answer.
