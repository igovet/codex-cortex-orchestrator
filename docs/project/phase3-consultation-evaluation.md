# Phase 3 consultation-effect evaluation

Phase 3 is frozen as an offline preparation protocol in
[`tests/fixtures/phase3_consultation_v1/`](../../tests/fixtures/phase3_consultation_v1/).
This artifact does not run a host, consult a model, select an executor, reveal
an arm, or establish a consultation-effect claim.

## Frozen design

The protocol compares an unchanged baseline with an optional consultation
candidate using five families: three qualifying transition families and two
negative controls. Each family has three paired repeats (15 pairs, 30 blinded
rows). Qualifying transitions are material consequential decisions,
contradictory or hypothesis-bound evidence, and repeated-failure or surprise
replanning. Routine/reversible work and active incident recovery are negative
controls. The same prompt, fixture, oracle, reset, host/model/dependency
envelope, and frozen payload-identity set are required across each repeat.

The frozen model envelope is exact: the coordinator uses `gpt-5.6-luna` with
high reasoning effort, workers may use only `gpt-5.6-luna` with medium or high
effort, inherited conversation is disabled, and Astra is prohibited. The
manifest validator rejects supplied empty/non-object candidates instead of
falling back to the current manifest.

The coordinator retains the randomization seed, arm mapping, reveal authority,
promotion decision, and acceptance ownership. Scorer input is blind to arm,
condition, payload mapping, diagnostics, token accounting, and wall time. The
model remains the owner of optional consultation and strategy decisions; no
server, hook, fixture, or oracle rule creates a mandatory stage or runtime gate.

## Measures and controls

Quality is scored separately for critical decision defects, requirement
coverage, discriminating-check use, false consultation activation, missed
qualifying transitions, protocol pass, acceptance regression, and protected
content preservation. Overhead is reported separately for consultation count,
all six token dimensions, wall time, report bytes, duplicate evidence, and
recovery delay. Missing observations remain null with a reason; host, provider,
gateway, dependency, Codebase Memory, and Git availability cannot become a
quality outcome, adaptive rank, acceptance result, or consultation benefit.

The offline oracle stops on identity/control asymmetry, arm leakage, missing
terminal or audit evidence, raw/private-content exposure, an unavailable
required receipt, or an external availability failure that prevents a
comparable cell. Its promotion screen is advisory and coordinator-owned:
fewer critical defects, no acceptance/protocol regression, at least 80%
specificity on negative controls, and median consultation count no greater
than one per qualifying transition. These criteria are not runtime gates.

The oracle also requires the exact 15 `(case_id, repeat)` cells and one opaque
`blind_pair_id` per cell, with exactly two rows in each pair. Every row must
carry all six diagnostics fields, each using `available` or a frozen null-reason
vocabulary. Any unavailable component makes the row unavailable; an aggregate
`available` value cannot override an unavailable provider, gateway, dependency,
Codebase Memory, or Git component. Unavailable rows carry an explicit matching
reason in `consultation.unavailable_reason`; `not_observed` is an unavailable cell
and therefore requires null quality, cost, and latency. Available rows must report
all six cost dimensions and wall latency; null overhead observations are rejected
instead of being treated as zero. Condition-independent task identities are bound
to the frozen prompt, workload fixture, oracle, reset, and dependency-envelope
digests. Payload identities remain coordinator-supplied opaque SHA-256 values.
`manifest_tools.py` independently rejects mutations to the complete frozen case,
dependency, randomization, blind-scoring, scorecard, stopping, negative-control,
promotion, and model-envelope policies, including runtime gates, automatic
acceptance, non-blind scoring, and live execution status. Every declared cost
dimension is null for unavailable cells, booleans are not accepted as integer or
numeric ledger values, and malformed rows or nested objects produce structured
failed receipts.

## Verification

Run the fixture contract checks from the repository root:

```bash
python3 -m pytest -q tests/test_phase3_consultation.py
```

Live CLI/Desktop scenarios, arm reveal, consultation scoring, and adaptive
activation are intentionally unrun. They remain behind the coordinator-owned
Desktop route evidence and a published terminal report for the immediately
preceding phase.
