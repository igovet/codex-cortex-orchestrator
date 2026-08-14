# Conventions

<!-- GENERATED:START -->
- The plugin’s machine-readable contracts live under [plugins/cortex](../../plugins/cortex/), including the manifest, MCP configuration, profile registry, and hook configuration.
- The supported profile names and shared worker-report fields are declared in [profiles.json](../../plugins/cortex/profiles.json); profile files must match that registry.
- `cortex/report/v1` payloads contain exactly `summary`, `findings`, `questions`, `changed_files`, `tests`, `evidence`, `uncertainty`, and `next_action`; the server sanitizes report data before persistence. See [cortex.py](../../plugins/cortex/scripts/cortex.py).
- Report persistence is bounded: one report is at most 64 KiB; an attempt can publish 32 reports; a task can publish 256 reports totaling at most 1 MiB; and an attempt can receive at most 256 explicit context grants. Stranded-report recovery allocates a new record and never overwrites an authoritative record.
- Ledger, report-bus, journal, and lifecycle-telemetry paths reject symlink traversal and require regular-file targets before reads or writes. Report JSON is authoritative; `reconcile_report_bus` repairs derived indexes, receipts, and Markdown after an interrupted multi-file publication.
- Observational metrics accept only nonnegative integer token/elapsed fields and finite nonnegative costs. Their retained tail is bounded to 1,000 events or 512 KiB and increments `telemetry_dropped` when old entries are evicted.
- The public MCP surface exposes exactly one coordinator tool: `orchestrate`. Every call supplies an exact absolute `project_root`; one MCP process may independently serve multiple project roots. `orchestrate(start)` performs activation, classification, initialization, full-wave planning, and first-wave preparation. `orchestrate(advance)` accepts a completed wave and prepares the next one. Native `spawn_agent` and user-authorized `create_thread` remain host calls, not public MCP tools.
- Mutating facade calls use durable `operations/<submission_id>.json` request-digest receipts: an identical retry replays the recorded result and a changed payload for the same submission id conflicts. Expected facade validation failures return `ok: false` and are not exception-log events. Profiles are preloaded and validated when the MCP server starts.
- Refreshable documentation facts are bounded by `<!-- GENERATED:START -->` and `<!-- GENERATED:END -->`; text outside those markers is preserved by knowledge-refresh work.
- Repository validation and smoke scripts use `python3`; shell scripts use Bash with strict mode. See [validate-cortex-marketplace.py](../../scripts/validate-cortex-marketplace.py) and [sync-cortex.sh](../../scripts/sync-cortex.sh).
<!-- GENERATED:END -->
