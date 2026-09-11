# Phase 4 privacy-safe telemetry

Phase 4 telemetry is an offline reducer, not a runtime policy. The companion
[`scripts/phase4_telemetry.py`](../../scripts/phase4_telemetry.py) accepts one
reviewed run/cell summary and returns a bounded `phase4-telemetry-v1` aggregate.
It is suitable for synthetic verification and later reviewed pilot input; it
does not launch hosts or select models, workers, or strategies.

## Aggregate contract

Each record contains opaque run, pair, and suite identifiers; phase and strategy
versions; host class; SHA-256 digests for payload/configuration/dependencies,
tool catalogue, task family, fixture, and oracle; lifecycle timing/status; and
participant buckets by role/model/effort. Participant thread identities are
rejected and never emitted. Dispatch, read, write, report, consultation, retry,
wait, and recovery counts are bounded non-negative integers.

Token totals preserve the six native dimensions (`input_tokens`,
`cached_input_tokens`, `cache_write_input_tokens`, `output_tokens`,
`reasoning_output_tokens`, and `total_tokens`). Response IDs are used only for
deduplication and are never retained in the aggregate. Missing participants or
responses produce `null` token totals and response counts rather than invented
zeros.

`availability` always has separate status/reason entries for Codebase Memory,
gateway/provider, dependencies, Git, and host capabilities. Unavailable,
unknown, and not-checked statuses are exclusions only: they cannot populate the
`quality` field or become a Phase 5 strategy input. Quality is optional and is
accepted only when explicitly sourced from an independent oracle or review.
Errors are category counts, never messages or stack traces.

Unknown fields, private-content fields, raw-content values, malformed digests,
unbounded labels, non-finite numeric values, and oversized records fail closed.
The JSONL sink re-validates every normalized record (including records already
carrying `schema_version`) and existing retained rows before writing; forged
schema-shaped mappings cannot bypass the no-raw-retention boundary. It uses
owner-only permissions and bounded record retention. Raw transcripts, prompts,
commands, reports, credentials, and host logs remain outside the repository.

## Offline verification boundary

The synthetic tests cover response deduplication, separate cached/uncached
dimensions, null-safe missing data, independent quality attribution, explicit
availability, private-field rejection, CLI/Desktop shape parity, conflicting
response detection, bounded record size, owner-only sink permissions, and
retention. Passing these checks is an implementation result only. It does not
establish telemetry validity on real runs or permit a live Phase 4 pilot before
the coordinator records the required transition evidence.
