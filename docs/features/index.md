# Features

<!-- GENERATED:START -->

This registry covers the active, source-backed Cortex V12 feature areas.
Across every feature, the root coordinator is orchestration-only; workers own
all project actions, substantive analysis, changes, and verification. Its only
project-read exception is the bounded orchestrator-owned knowledge route used
to compile per-delegation requirements; profiles consume that supplied contract
and do not independently reroute. Coordinator routing reads are non-shell direct
reads of already-known exact allowed paths. Unknown roots or paths and every
project-local artifact/state check remain worker-owned.

- [Orchestration ledger](orchestration-ledger/index.md)
- [Advisory governance and project initiatives](advisory-governance/index.md)
- [Human-readable task views](human-readable-task-views/index.md)
- [Operator maintenance](operator-maintenance/index.md)
- [Plugin packaging and validation](plugin-packaging/index.md)
- [Coordinator communication](coordinator-communication/index.md)
- [Knowledge-route contract](knowledge-route-contract/index.md)

Lifecycle telemetry is not an active V12 feature. The package ships no native
lifecycle hooks, and host start/stop observations are not coordination
authority.

<!-- GENERATED:END -->
