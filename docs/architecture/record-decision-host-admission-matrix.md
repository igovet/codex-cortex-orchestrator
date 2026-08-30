# `record_decision` Host-Admission Differential

Development-only investigation note. This file is not a runtime contract and
must not be referenced by installed skills, prompts, or product documentation.

## Scope and evidence

The comparison below is generated from the current source catalogue returned by
`cortex_runtime.public_contracts.build_public_contracts()` (13 model-visible
Cortex tools). The measurements use compact UTF-8 JSON serialization and count
the complete advertised `inputSchema` and `outputSchema`, including nested
descriptions. They are diagnostic measurements, not wire limits.

No `$ref`, `format`, nullable, or empty `oneOf`/`anyOf`/`allOf` construct is
advertised in the current catalogue. Every input object is closed at each
nested object boundary (`additionalProperties: false`). Output schemas are
mostly compact-handle envelopes: they contain required fields plus optional
result fields and, in several nested result objects, `additionalProperties:
true` for server-defined maps.

## Catalogue differential

| Tool | Input bytes | Input nodes / properties / required | Input enums / arrays | Output bytes | Output nodes / properties / required | Distinguishing input features |
|---|---:|---:|---:|---:|---:|---|
| `open_task` | 3,288 | 17 / 10 / 9 | 0 / 3 | 7,526 | 36 / 29 / 2 | Largest task request; nested arrays and 65,536-byte text limits |
| `read_task` | 1,066 | 4 / 2 / 1 | 0 / 0 | 7,823 | 39 / 31 / 2 | Minimal read request; integer cursor with `minimum: 0` |
| `open_clarification` | 1,986 | 6 / 4 / 3 | 0 / 0 | 14,218 | 68 / 58 / 23 | Largest decision-binding output; prompt and language fields; exact binding branch |
| `open_plan_review` | 1,671 | 6 / 4 / 4 | 0 / 0 | 13,854 | 68 / 58 / 23 | Plan ref pattern and decision-binding output |
| `open_steering` | 1,495 | 6 / 4 / 3 | 0 / 0 | 13,722 | 68 / 58 / 23 | Optional assignment relation; decision-binding output |
| `record_decision` | **3,969** | **19 / 13 / 7** | **2 / 2** | **14,120** | **69 / 59 / 24** | Flat closed request plus nested `response`; two mode/status enums; two arrays; 35-byte binding/task IDs; 65,536-byte response text |
| `open_assignment` | 3,995 | 15 / 10 / 7 | 1 / 2 | 3,420 | 15 / 10 / 9 | Comparable request size; mission object, arrays, profile enum |
| `consume_assignment_evidence` | 1,525 | 4 / 2 / 1 | 0 / 0 | 7,843 | 37 / 30 / 3 | Minimal request; assignment ref and 2,048-byte cursor |
| `publish_plan` | 6,663 | 36 / 22 / 14 | 3 / 9 | 4,533 | 23 / 18 / 17 | Largest input; publication evidence arrays and multiple enums |
| `publish_result` | 5,243 | 27 / 17 / 11 | 3 / 6 | 7,527 | 36 / 29 / 2 | Publication evidence; arrays and status enums |
| `publish_documentation` | 4,479 | 23 / 14 / 7 | 2 / 6 | 7,541 | 36 / 29 / 2 | Publication evidence; arrays and status enums |
| `assess_governance` | 2,223 | 7 / 4 / 2 | 1 / 1 | 7,414 | 36 / 29 / 2 | Governance mode enum and risk-factor array |
| `close_task` | 1,860 | 9 / 5 / 2 | 1 / 2 | 7,459 | 36 / 29 / 2 | Verdict enum; two bounded arrays |

## Unique and threshold-sensitive `record_decision` features

| Feature | Current shape | Admission risk | Smallest isolating variant |
|---|---|---|---|
| Closed top-level object | `additionalProperties: false` | Host-added fields are rejected before semantic handling | Exact required-only object; then add one unknown key |
| Closed nested response | `additionalProperties: false` | Reconstructed or UI-normalized response keys fail | Exact response object; then add one unknown key |
| Required count | 7 top-level required fields | Omission is schema failure, unlike output envelopes with only 2–3 required fields | Remove one required field at a time |
| Enum constraints | 2 enum nodes (decision kind/status) | Wrong spelling/case or stale enum fails | Replace one enum value with an adjacent semantic value |
| Arrays | 2 array nodes, including bounded `maxItems` | Host flattening, null, or over-limit list fails | Empty array, one item, then `maxItems + 1` |
| Exact identifiers | task/binding references have exact length/pattern constraints | Truncation, ellipsis, reconstructed IDs fail | Byte-exact server handle, one-character mutation, shortened handle |
| Response text | bounded string up to 65,536 characters | Character/UTF-8 boundary can differ from byte admission | ASCII at limit, UTF-8 at same character count, one over limit |
| Optional branch properties | Optional response/decision-context fields exist, but no union/ref branch is advertised | Sending fields from another lifecycle branch is rejected by closed objects | Required-only branch, each optional field alone, two incompatible fields |
| Output schema size | 14,120 bytes; 24 required fields in the decision output | Large output can expose host/tool-catalogue size handling, but does not alter input validation | Keep input fixed; compare response with and without optional output fields |

`record_decision` is not uniquely using `$ref`, `format`, nullable values, or
empty schema branches. Its meaningful difference is the combination of a larger
closed request, nested response object, exact opaque handles, enums, bounded
arrays, and a large decision-binding output. Therefore a host admission failure
must first be localized to the advertised input object and transport envelope;
removing or compressing `outputSchema` would not explain a field validation
failure such as `prompt_en` or an opaque binding mismatch.

## Deterministic reduction sequence

Run these variants through the same real stdio MCP transport and the exact
advertised catalogue used by the live candidate. Do not call a Python façade
directly and do not put argument hints in prompts or skills.

1. Capture the exact `tools/list` schema and compute its digest.
2. Submit the server-rendered, required-only valid `record_decision` request
   with byte-exact handles from the immediately preceding open operation.
3. Repeat the identical request only after an intentionally ambiguous transport
   interruption; classify the result as idempotent reconciliation, not replay.
4. Add one optional response property at a time, preserving all required fields.
5. Add one unknown top-level property, then one unknown nested property; both
   must fail at schema admission with the named location.
6. Mutate one dimension at a time: handle length, handle character, enum value,
   array cardinality, response text length, and UTF-8 byte boundary.
7. Keep input constant while comparing the normal advertised output schema to a
   locally instrumented output-size variant. If input behavior changes, the
   host's catalogue/transport admission is coupling unrelated schemas.
8. Repeat steps 2–7 through the real live-dev tmux session and inspect both the
   coordinator pane and bounded worker event stream. Any hidden validation
   error or unexplained successful mutation replay fails the experiment.

The expected architectural result is that server-issued handles are copied
byte-for-byte, the host forwards the closed object without adding fields, and
`record_decision` has one semantic admission path independent of output-schema
rendering. Any deviation indicates a host adapter/catalogue mismatch rather than
a need to teach the model MCP parameter names.
