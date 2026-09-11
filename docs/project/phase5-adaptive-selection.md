# Phase 5 adaptive selection (offline)

[`scripts/phase5_adaptive.py`](../../scripts/phase5_adaptive.py) implements the
Phase 5 decision-boundary contract as an offline, standard-library-only overlay.
It validates reviewed Phase 4 aggregates and produces a bounded
`phase5-evidence-bundle-v1`. Availability, stale/contradictory/contaminated
evidence, incomplete lifecycle/audit receipts, and low samples are explicit
exclusions; they never become quality scores or ranking signals.

The model supplies an advisory candidate proposal. The reducer does not choose
an executor, model, effort, acceptance result, or mandatory stage. Missing or
invalid proposals are normalized into a typed baseline fallback. Candidate
evidence is accepted only when the proposal names at least one evidence row and
every evidence row joins a selected report by report ID, source revision, and
artifact digest list; a missing reference or failed join falls back to baseline.
Unknown-reason lists and recommendation JSON are strictly bounded.
Explicit user routes are preserved. `make_decision_record` and `rollback_to_baseline` return
coordinator-owned records only; they do not write a pipeline or mutate runtime
state. Prompts, report bodies, commands, credentials, paths, and raw thread IDs
are rejected or excluded.

This is offline implementation evidence only. It does not activate adaptive
routing, establish quality improvement, or qualify CLI/Desktop behavior. Future
activation remains behind the coordinator-owned terminal Phase 3/4 evidence and
the required unchanged-payload CLI then Desktop qualification.

Focused verification:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q tests/test_phase5_adaptive.py
```
