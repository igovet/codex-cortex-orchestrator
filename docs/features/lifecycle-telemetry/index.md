# Lifecycle telemetry (retired)

<!-- GENERATED:START -->

This page is retained only as a historical link target for the V12 release
documentation closure. Lifecycle telemetry is not an active Cortex V12 feature.

Cortex 12.0.0 ships no native lifecycle hooks and no lifecycle hook script.
Host session, subagent start/stop, coordinator stop, wait output,
environment state, and compaction events are not ledger records, authorization,
completion evidence, or recovery inputs.

Native worker coordination belongs to the model and Codex host outside the
eleven-tool ledger. A missing report or worker stop does not activate a server
recovery route; the coordinator may disclose the evidence gap and create a
replacement delegation directly.

The active feature registry is [features/index.md](../index.md). See the
[orchestration ledger](../orchestration-ledger/index.md) and
[advisory governance](../advisory-governance/index.md) for the V12 contract.

<!-- GENERATED:END -->
