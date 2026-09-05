---
name: tool-discipline
description: Prevent guessed, incomplete and replayed tool calls during explicitly selected Cortex coordinator or worker work.
---

# Tool-call discipline

## Scope and authority

Apply this skill to every tool call by the coordinator and native workers.
The current host-advertised tool declaration is the sole argument authority.
Skills, examples, previous calls and remembered schemas do not replace it.
This skill defines call discipline, not additional Cortex operations or server gates.
Both roles load required skills through the normal Codex procedure. The
coordinator keeps project evidence reading limited to previews and the pipeline.

## Before each call

1. Identify the concrete new information or state change required from this call.
   If an earlier result is still sufficient and relevant state has not changed, do
   not call a tool. Timeouts, quiet workers and a desire to reconfirm are not new needs.
2. Confirm the exact tool is available in this session and allowed in the current host mode.
   Distinguish built-in tools from MCP tools; absence from one catalogue proves nothing
   about the other. Use host tool discovery for deferred operations before declaring
   one unavailable. A worker must locate the task catalogue, bounded document reader,
   draft creator and common Markdown writer before project work. Never substitute direct database
   or final task-file access for those MCP operations.
3. Read its complete current description and input schema before first use, and reload
   the declaration after context loss or any indication that the catalogue changed.
4. Check that the operation matches the immediate intended action. A read cannot stand
   in for a write, a metadata listing cannot stand in for document evidence, and a saved
   report does not prove that its described verification happened.
5. Construct one complete request. Check required values, types, enumerations, nesting,
   allowed fields, identifiers, boundaries, size limits and conditional requirements.
   Omit optional properties when absent rather than guessing placeholders or nulls.
6. Obtain identifiers and cursors from the actual returned result for this task. Never
   invent them, confuse report references or alter an opaque continuation token.
7. Confirm actual dependencies and authorization. Do not issue dependent calls before
   earlier results exist, launch workers with incomplete assignments, or use tools to
   bypass native host permissions.
8. Invoke the checked tool. Use only supported orchestration/batching mechanisms; batch
   only genuinely independent calls and inspect every returned result.

Do not call a tool speculatively to discover its required arguments through errors.
Do not duplicate MCP request shapes or validation limits in skill prose or templates.
When the declaration is genuinely missing, discover it through supported host facilities.
If an essential fact remains unavailable, explain the limitation rather than inventing it.

## Code wrappers

Prefer a direct advertised tool invocation when the host exposes one. If the host
requires JavaScript or another code wrapper, keep it minimal: construct the request,
await the available tool and expose its result, including success/error state and a
command exit code when the nested result provides one. Returning only command stdout
does not establish success. The wrapper is executable code,
so validate its syntax before submission as well as the enclosed tool arguments.

- Check every property name, matching quote, colon, comma and closing delimiter.
- Validate command-language syntax before dispatch. A `python3 -c` one-liner must not
  place `class`, `def`, `for`, `if`, `try`, or `with` after a semicolon; use an
  equivalent simple expression or a valid multiline program.
- A Markdown body never belongs in a writer wrapper. First use the live draft creator,
  use its returned `markdown` as the exact initial contents, then update only its body
  with the built-in `apply_patch` file tool. Use one call with an independent exact hunk per returned marker.
  Do not call `read_draft` to repeat an unchanged
  creation result; reserve it for recovery or a genuinely needed later read. Never
  delete, replace or recreate the draft. A writer
  wrapper, if required,
  carries only the server-issued draft identifier and short metadata advertised by
  the live writer. A report body may appear only as exact `apply_patch` input. If the
  host exposes that file tool through a wrapper, pass one valid escaped string directly
  to `tools.apply_patch`; never use a JavaScript template literal, `String.raw`, arrays,
  chunks, heredocs, command substitution, shell interpolation, or a Cortex MCP request.
- Do not combine discovery, unrelated calculations and mutations in one wrapper.
- A syntax error before dispatch is a failed native call, not an MCP rejection.
  Correct that syntax once, preserve the intended request and inspect the result.
  Do not claim delivery or invent a successful receipt for an unexecuted call.
- When the wrapper yields `Script running with cell ID ...`, call the built-in
  `wait` tool with that exact cell before any other tool call. Continue waiting until
  the cell returns a terminal result. Do not pass a cell ID to `write_stdin`, infer
  completion from files or another command, or begin dependent work first.
- When a local server returns a running session, verify readiness with one bounded
  command that retries the exact loopback URL internally until success or a deadline.
  A one-shot request during startup is not evidence and must not be used as a probe.
- Browser accessibility element numbers are snapshot-local. After a browser action
  changes DOM-backed state, request one complete non-diffed accessibility tree before
  the next element-number mutation and use only the newly returned number. Do not
  reuse a number from an older tree or deliberately call a stale element to discover
  its replacement.
- For a local web application owned by the assignment, prefer advertised Playwright
  role, label, text or test-id locators for control mutations. Use accessibility
  snapshots for semantic evidence and results; do not guess numeric element handles
  when a stable accessible locator can express the intended control.

## File patches

Before submitting a patch, check every affected path for duplicate operations.
Use one update operation for an existing file, including a complete content
replacement. Never delete and add the same path in one patch. Keep the existing
file intact until a valid replacement is ready; an empty or deleted intermediate
file is not a completed replacement.

Immediately before every project-file update, reread the exact current target
fragment. For a newly created Cortex draft, the complete `markdown` returned by
`create_draft` is that current read; do not add another tool call. Use separate exact
replacement hunks for its returned guidance comments instead of one large context
replacement. A later read is mandatory after user steering, another contributor's
work, or any earlier patch to that path. Build the patch from observed current text,
not remembered or generated pre-change text. A failed context match requires a fresh
read before one corrected attempt; never replay the stale patch.

## After each result

- Inspect the actual success/error result before dependent work or claims of completion.
- Retain successful identifiers and enough context to continue without replaying a write.
- Begin document and catalogue reads with their advertised default page size.
  Do not request a larger page merely to force an entire report into context.
  Follow the cursor only for needed text and stop once enough evidence is present.
- If the pipeline changed and a read cursor expired, restart its read from the current beginning.
- Keep the outcome separate from claims in retrieved Markdown; assess those claims as evidence.

## Errors and retries

| Observed outcome | Required action |
| --- | --- |
| Acknowledged success | Continue using the returned identifiers; never repeat a mutation merely to confirm it |
| Deterministic argument rejection | Re-read the live declaration, identify the concrete mismatch and make at most one materially corrected attempt when unambiguous |
| Still invalid or missing essential facts | Stop that failing route, explain the limitation and use a safe alternative if available; do not loop or guess |
| Genuinely uncertain delivery | Follow that tool's advertised retry contract exactly |
| Host permission denial or unavailable tool | Respect the boundary; do not substitute another mechanism to bypass it |
| Storage conflict or integrity error | Preserve evidence and diagnose within scope; do not rewrite documents or weaken checks to force success |

When an authorized verification command is blocked only by the sandbox, use the
host's advertised permission-escalation path when available. Inspect its decision
and respect any denial. Do not replace this with a mechanism that bypasses host
permissions, or silently omit the required verification. If that supported path
is unavailable, report the exact missing capability to the coordinator.

A timeout alone does not prove that a mutation failed or a worker stopped.
A new intended report is distinct from retrying an earlier delivery. Never relabel
an acknowledged duplicate mutation as a harmless retry to claim a clean live test.

## Evidence and recovery

Record tool failures, corrections and unrun checks honestly without private payloads.
After summarization, workers restore saved identifiers and load this skill together
with the relevant live declarations. Coordinators restore host-supplied rules,
catalogue previews and the current pipeline, loading required skills normally. The coordinator remains active
through native wait while unfinished delegated work runs; an error is not an excuse
to manufacture completion or a user question.
