# Plugin packaging and validation

<!-- GENERATED:START -->

## Purpose and installable boundary

Cortex 1.15.6 is a Codex plugin with one complete installable payload below
plugins/cortex. Root scripts, tests, documents and AGENTS.md support development;
they are not installed runtime authority. End-user installation follows
[README](../../../README.md), while repository live development uses only the
isolated candidate.

| Source | Responsibility |
| --- | --- |
| [plugin.json](../../../plugins/cortex/.codex-plugin/plugin.json) | Semantic version, content stamp and UI metadata |
| [.mcp.json](../../../plugins/cortex/.mcp.json) | Direct Python MCP launch and host exposure |
| [marketplace.json](../../../.agents/plugins/marketplace.json) | Repository marketplace entry |
| [cortex.py](../../../plugins/cortex/scripts/cortex.py) | Public server facade |
| [public_contracts.py](../../../plugins/cortex/scripts/cortex_runtime/public_contracts.py) | Twenty authoritative tool contracts |
| [typed_publications.py](../../../plugins/cortex/scripts/cortex_runtime/typed_publications.py) | Shared publication schemas and validation |
| [execution_graph.py](../../../plugins/cortex/scripts/cortex_runtime/execution_graph.py) | Typed graph invariants |
| [graph_ledger.py](../../../plugins/cortex/scripts/cortex_runtime/graph_ledger.py) | Transactional graph/assignment/artifact state |
| [v12_store.py](../../../plugins/cortex/scripts/cortex_runtime/v12_store.py) | Current schema v2 storage and receipts |
| [v12_projections.py](../../../plugins/cortex/scripts/cortex_runtime/v12_projections.py) | Verified private Markdown projections |
| [v12_maintenance.py](../../../plugins/cortex/scripts/cortex_runtime/v12_maintenance.py) | Explicit non-MCP operator maintenance |
| [worker_message.py](../../../plugins/cortex/scripts/cortex_runtime/worker_message.py) | Exact native bootstrap and packaged profile policy |
| [profiles.json](../../../plugins/cortex/profiles.json) | All 22 profiles and model recommendations |
| [Orchestrator skill](../../../plugins/cortex/skills/orchestrator/SKILL.md) | Model-owned intent, ready-work selection and user communication |
| [Control skill](../../../plugins/cortex/skills/cortex-control/SKILL.md) | Worker/coordinator protocol policy |
| [Communication skill](../../../plugins/cortex/skills/coordinator-communication/SKILL.md) | Localized decision-ready user presentation |

## Catalogue and publication contract

The initial neutral catalogue contains all twenty tools in one response.
Closed input schemas and descriptions are authoritative; skills and profiles
must not duplicate call shapes. Required-property descriptions are generated
from the same schema. Catalogue size and reserve are tested so no tool or field
is silently truncated.

Successful result schemas remain runtime validation contracts even when not
included in discovery. Bounded structural diagnostics do not echo task/report
values. The physical frame and compact argument limits are independent.
Large structured responses preserve complete assignment authority without
duplicating an oversized body into text.

Only task opening supplies the canonical project root. Later public operations
use exact task/worker references, not caller-created IDs, cursors or replay keys.
Assignments select ready graph nodes or genuinely available bootstrap work;
there is no textual-scope API alongside the graph.

Workers consume one immutable assignment with complete scoped product context,
expected checks, artifact procedure and fixed terminal kind. Planning,
documentation and ordinary result publications are distinct and share one
cross-kind terminal slot. Observed node coverage is canonical verification;
plans contain expected checks, not fabricated observations.

Required evidence is not defaulted away. Unknown legacy envelopes, progress
publication and chunk-assembly continuation are unsupported. Conditional
artifact metadata requires the actual declared assignment condition.

## Integrity core and native host

The model chooses intent, decomposition, profile/model/effort, ready work and
evidence interpretation. The core deterministically guards prerequisites,
artifact generations, ownership, revisions, terminal kind and current user
decisions. It does not choose the next specialist or schedule a native agent.

The package includes the declared activation/lifecycle hooks. Desktop MCP
initialization may lack trusted child identity, so initial discovery is neutral.
Exact supported host correlation and first assignment consumption establish the
worker role; a retained initial catalogue cannot bypass per-call actor checks.

Native spawn remains outside MCP. Forward the exact new dispatch once and
immediately. Hooks do not asynchronously terminate workers, grant filesystem
permissions or manufacture completion. Native quiescence must be observed
before reconciliation and loss recovery.

Luna is the configured default, and its native override is omitted. Terra is
for genuinely complex planning/architecture; Sol is rare and high-risk-security
specific. They use exact explicit overrides. Every effort is explicit and no
higher than max. No server-owned escalation, ad-hoc profile fallback or
unsupported native agent-type argument is allowed.

## Current storage and human views

Fresh project state is schema v2. Unsupported schema shapes are rejected
without migration, directory adoption or legacy sentinels. Initiative tables,
services and locators are removed. Old installations remain untouched.

Canonical commands commit their transition, event and derived receipt
atomically. Exact ambiguous-response reconciliation does not create another
report or spawn. Steering immediately invalidates old authority; successor work
waits for native quiescence and observed artifact reconciliation.

Only plans and finalized reports have user-facing Markdown. Links require
contained regular-file, source and digest/readback verification. Unsafe paths
and external edits are not overwritten. A transient projection I/O failure can
be repaired once within the original publication from its durable report;
persistent failure stays explicit and cannot erase canonical evidence.

Maintenance remains an explicit task-anchored operator CLI outside the twenty
tools. Backup covers the project shard; restore is strictly offline. Pruning
cannot delete canonical task evidence or write ledger data into the project.

## Candidate identity and development

The installable manifest uses 1.15.6+codex.sha256.<digest-prefix>. Startup
recomputes the normalized payload digest before initialization. An invented,
missing or stale installed stamp is rejected. Explicit source mode is a
different evidence class and is not an installed release.

For this hardening task, preserve the semantic version and refresh only the
content-addressed suffix through the supported isolated entry point:

```bash
./scripts/cortex-dev --prepare-only
```

The helper creates/reuses the exact HOME/.cortex-dev candidate and its isolated
Codex configuration/cache/state. It never updates the stable plugin as a test.
The candidate's optional Codebase Memory projection copies only its supported
safe settings, not arbitrary credential/environment tables.

Ordinary interactive CLI runs use scripts/cortex-live-smoke; actual Desktop
uses scripts/cortex-desktop-dev with a disposable profile. The final pair must
fully complete on one unchanged payload. Any payload edit invalidates previous
host qualification.

Read-only sync preview/check modes do not install or rewrite source state.
Normal synchronization is a development operation invoked only through the
isolated launcher for live work, not a replacement end-user installation flow.

## Validation requirements

Release/source validation must prove:

- exact allowlisted payload, current content stamp and marketplace parity;
- complete twenty-tool schemas, catalogue reserve and safe diagnostic bounds;
- all 22 packaged profiles and default Luna/explicit effort transport;
- worker bootstrap, exact native correlation and actor/project isolation;
- typed readiness, acceptable dependencies and compatible parallel ownership;
- current-only schema, atomic receipts and one terminal publication;
- candidate validation, current plan/closure binding and real semantic steering;
- artifact conflict, quiescence, recovery and finite remediation;
- verified views and bounded post-commit repair without duplicate reports;
- private modes, no symlink adoption and no raw sensitive data in diagnostics;
- packaged non-MCP maintenance with offline restore and bounded retention;
- self-contained skills without removed initiative/progress/textual-scope routes;
- current public documentation, links, commands and release metadata.

Source regression is not a native live pass. After local levels and the short
CLI succeed, the full scenario must exercise all tools and profiles with real
prerequisites, multiple messages, several steering revisions, same-task recovery
and post-result closure. Actual Desktop must finish, not merely launch.

Use the commands and binary criteria in
[Verification](../../project/verification.md) and the authoritative
[qualification ladder](../../project/typed-orchestration-integrity.md#10-qualification-ladder).
Maintain the Completion checklist with exact observed results and unrun gates.

## Security-sensitive packaging rules

The package and hooks use the host-resolved Python 3.11+ runtime. They must share
the same launch contract without source-tree bytecode drift. No package
metadata, skill or profile may imply native permission from a plan decision,
make the coordinator a project operator or treat Markdown as ledger authority.

Keep credentials, personal data, raw worker reports, prompts and private logs
out of package artifacts and release evidence. Worker engineering prose is
English; exact user-source fields retain their language. Coordinator summaries
and decision packets are localized, with fresh server-verified links.

See [Security policy](../../../SECURITY.md) and
[Storage classification](../../project/storage-classification.md).

<!-- GENERATED:END -->
