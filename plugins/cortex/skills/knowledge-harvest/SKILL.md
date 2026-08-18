---
name: knowledge-harvest
description: Build or refresh exhaustive repository knowledge documentation from verified source, tests, configuration, process definitions, and integrations. Use for Cortex harvest, harvest-refresh, repository onboarding, incomplete feature registries, large restructuring, or stale docs/project and docs/features trees.
---

# Knowledge Harvest

Build a durable functional map of the repository, not a task journal or a
summary of recent commits. The documentation must let a new engineer discover
every in-scope capability, its owning code, its runtime behavior, and how to
verify or operate it.

Before planning or dispatching a harvest, read
[feature-census.md](references/feature-census.md) completely. Its inventory,
coverage, page-content, and validation contracts are mandatory.

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

1. **Scope:** Dispatch the read-only Planner Scope gate to enumerate top-level
   applications, services, packages, runtime processes, deployment surfaces,
   integrations, and likely functional domains. It publishes a discovery brief,
   relevant context files, and up to eight non-overlapping discovery domains;
   it must not design the solution or close material intent questions.
2. **Domain census:** Replace the discovery wave when necessary with one
   read-only `explorer` per bounded domain, normally 2–8 in parallel for a
   large repository. Each explorer exhaustively inventories its assigned
   domain and traces feature-bearing surfaces through entry points, workflows,
   state, persistence, configuration, integrations, failure paths, and tests.
   Give each explorer the scoping report through `depends_on: ["scope"]`.
3. **Architecture synthesis:** Dispatch `architect` with the scoping,
   discovery, and all domain handoffs. It deduplicates features, defines stable
   feature boundaries, maps cross-domain flows and shared infrastructure,
   identifies ADR-worthy decisions, and emits the canonical documentation
   taxonomy.
4. **Plan:** Dispatch the final read-only Planner after discovery and
   architecture. It consumes all predecessor reports and publishes the
   decision-complete `planning` artifact with ownership, dependencies, and
   complete acceptance and verification criteria.
5. **Documentation:** Dispatch one or more `technical_writer` workers. Use one
   writer for a small repository. For a large repository, parallelize only
   across non-overlapping `docs/features/<domain-or-feature>/` paths and assign
   exactly one writer to `docs/project/` plus `docs/features/index.md`. Every
   writer depends on the architecture and final-plan phases and verifies
   consequential facts in current source or tests instead of copying reports
   blindly.
6. **Completeness review:** Dispatch `code_reviewer` after documentation to
   independently compare the fresh source inventory with the coverage matrix
   and written pages. Any unmapped surface, placeholder, thin page, broken
   source link, undocumented failure path, or unsupported coverage claim
   fails review and triggers bounded documentation rework.
7. **Close:** Dispatch `build_verification` to check links, paths, generated
   blocks, formatting, repository-native documentation checks, and the final
   coverage statement without editing files.

The final Planner is deliberately separate from early scope: scope partitions
evidence, while plan resolves the implementation/documentation decision. The
strict seven-field `cortex/report/v1` remains unchanged; Scope may add only the
top-level `scoping` sibling and Plan may add only `planning`.

<!-- The canonical order above is authoritative. -->
<!-- Historical detailed wording below is intentionally omitted. -->
<!--
1. **Plan:** Dispatch `planner` to enumerate top-level applications, services,
   packages, runtime processes, deployment surfaces, integrations, and likely
   functional domains. The plan must define domain partitions, ownership,
   serialization boundaries, a coverage matrix, and explicit completeness
   criteria. A vague “inspect relevant files” plan fails this gate.
2. **Domain census:** Replace the discovery wave when necessary with one
   read-only `explorer` per bounded domain, normally 2–8 in parallel for a
   large repository. Each explorer exhaustively inventories its assigned
   domain and traces feature-bearing surfaces through entry points, workflows,
   state, persistence, configuration, integrations, failure paths, and tests.
   Give each explorer the planner report through `depends_on: ["plan"]`.
3. **Architecture synthesis:** Dispatch `architect` with the planning and all
   discovery handoffs. It deduplicates features, defines stable feature
   boundaries, maps cross-domain flows and shared infrastructure, identifies
   ADR-worthy decisions, and emits the canonical documentation taxonomy.
4. **Documentation:** Dispatch one or more `technical_writer` workers. Use one
   writer for a small repository. For a large repository, parallelize only
   across non-overlapping `docs/features/<domain-or-feature>/` paths and assign
   exactly one writer to `docs/project/` plus `docs/features/index.md`. Every
   writer depends on the architecture phase and verifies consequential facts
   in current source or tests instead of copying reports blindly.
5. **Completeness review:** Dispatch `code_reviewer` after documentation to
   independently compare the fresh source inventory with the coverage matrix
   and written pages. Any unmapped surface, placeholder, thin page, broken
   source link, undocumented failure path, or unsupported coverage claim
   fails review and triggers a bounded documentation rework.
6. **Close:** Dispatch `build_verification` to check links, paths, generated
   blocks, formatting, repository-native documentation checks, and the final
   coverage statement without editing files.

-->
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

## Evidence and preservation

Source, tests, executable configuration, schemas/migrations, and deployment
definitions outrank generated documentation. Codebase Memory may accelerate
architecture and trace discovery, but filesystem inventory and current source
must confirm consequential and completeness claims.

Use `<!-- GENERATED:START -->` and `<!-- GENERATED:END -->` for refreshable
facts. Preserve text outside generated blocks and do not overwrite a manual
ADR, gotcha, or feature explanation without evidence and explicit scope. Never
expose secrets, source dumps, private operational values, or personal data.

Every Cortex worker builds the strict seven-field report from
`get_report_template`, repeats `validate_report_draft` until valid, then records
the exact unchanged payload once through `record_report` with its validation
digest, acknowledges all supplied predecessor handoffs, and identifies
its inventory counts, mapped surfaces, exclusions, unknowns, evidence, and
coverage gaps. It must also review only the exact immutable briefing
issued by its compact dispatch bootstrap, verify the supplied SHA-256, and add
the exact `Dispatch briefing reviewed: <sha256>` evidence marker. That briefing
is the sole direct filesystem read allowed below `.codex/cortex`; workers never
list or inspect ledger state, baselines, delegation JSON, another briefing, or
report artifacts. If the host file reader alone cannot open that exact file,
the worker calls `read_dispatch_briefing` with the complete identity and
digest tuple from its bootstrap; an incomplete bounded response may continue
only with its returned cursor. Failure of that scoped read is a blocker, not
permission to browse or substitute another artifact. The coordinator reads each report before advancing and
uses `depends_on` when a later worker needs only selected phase handoffs.
A successor worker reads each supplied handoff through `read_worker_report`
with the exact project root, task ref, attempt id, profile, and report ref from
its generated briefing. It may not read an unlisted report or treat that
scoped evidence read as permission to call coordinator lifecycle operations.
