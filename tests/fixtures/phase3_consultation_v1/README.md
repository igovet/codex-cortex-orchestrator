# `phase3-consultation-v1` offline consultation-effect fixture

This directory freezes the Phase 3 consultation-effect protocol without
launching a host, selecting an agent, contacting a provider, or making a live
quality claim. It is an executable, disposable control surface for preparing
and auditing synthetic ledger rows.

The protocol compares an unchanged baseline with an optional consultation
candidate. It has three qualifying transition families, three paired repeats
per family, and two negative-control families with three repeats each:

| ID | Transition | Expected consultation |
| --- | --- | --- |
| T-01 | material consequential decision | qualifying |
| T-02 | contradictory or hypothesis-bound evidence | qualifying |
| T-03 | repeated failure or surprise replanning | qualifying |
| N-01 | routine/reversible work | negative control |
| N-02 | active incident recovery | negative control |

The arm mapping and randomization custody stay with the coordinator. Blinded
rows contain only opaque pair identifiers and condition-independent identities;
they do not contain `arm`, `condition`, or baseline/candidate mapping. The
scorer receives no diagnostics, token accounting, or wall time as quality input.

`manifest.json` freezes the case set, prompt/fixture/oracle/reset hashes,
payload and model envelope, randomization seed, scoring fields, stopping rules,
promotion screen, and null reasons. `workloads-v1.json` is the deterministic
case packet. `ledger_schema.json` defines the blinded row and score boundaries.
The model envelope is exact: the coordinator and all native workers use
`gpt-5.6-luna`, with coordinator `high` effort and worker `medium` or `high`
effort; inherited conversation and Astra are disabled. Candidate manifests are
validated as supplied, including empty or non-object candidates, and are never
silently replaced by the frozen manifest.

Reset is deterministic and refuses to overwrite an existing destination:

```bash
python3 tests/fixtures/phase3_consultation_v1/reset.py --destination /tmp/p3-trial
```

The independent oracle validates a synthetic ledger JSON file and exits zero
only when all 30 blinded rows (15 pairs) satisfy the frozen protocol:

```bash
python3 tests/fixtures/phase3_consultation_v1/oracle.py /tmp/p3-trial/ledger.json
```

Unavailable host/provider/gateway/Codebase Memory/dependency observations are
excluded cells with null quality/cost/latency fields and an explicit matching
reason. Every diagnostic dimension uses `available` or a frozen unavailable
reason; an unavailable component cannot be hidden by an aggregate `available`
value. Available rows must contain every cost dimension and wall latency rather
than silently omitting overhead observations. Unavailable reasons are strict
strings and are correlated with the row's unavailable diagnostics. These values
are never converted into consultation benefit, harm, a strategy rank, or an
acceptance decision. Promotion and stopping values are recommendations for the
coordinator, not runtime gates or mandatory stages.

The oracle rejects incomplete case/repeat coverage, reused pair join keys,
missing diagnostics, unknown availability states, scored `not_observed` cells,
inconsistent component availability, malformed scalar identifiers, and
identities whose frozen prompt, fixture, oracle, reset, or dependency digests do
not match the manifest and current files. Every cost dimension and latency must
be null for an unavailable cell and present for an available cell, and JSON
booleans are rejected where integer or numeric ledger values are required.
Malformed records and nested objects produce a structured failed receipt instead
of a traceback. The manifest validator also rejects malformed nested candidates
and mutations of the complete offline, coordinator-owned, blind-scoring,
randomization, scoring, stopping, and promotion policy.
