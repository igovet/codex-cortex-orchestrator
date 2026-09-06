# Orchestration review against primary guidance — 2026-09-06

This review separates observed Cortex failures, host behavior and design hypotheses.
The table records historical failures and the current resolution of each one. The
user requested a shorter decision cycle; the 12 completed comparisons remain
historical measurements, not evidence for a newly edited payload.

## Confirmed problems and changes

| Problem | Evidence | Change and limit |
| --- | --- | --- |
| Treating skill metadata as an attached specialist profile | Actual CLI/ Desktop workers lacked the full body; the absolute file-read ban prevented normal loading | Use complete attached instructions or the exact advertised SKILL.md path. Keep TOML and implementation inspection forbidden. |
| Mistyped report references and guessed corrections | A shortened delegated reference was followed by a guessed, nonexistent reference in a real trial | Compare full retained IDs before handoff/read; correct only from the owner/catalogue. Server rejects malformed or unknown IDs; no alias or guessing route. |
| Extra specialists without additional evidence | The documentation routing rule required a separate writer even when the implementation assignment could finish its README | Let one bounded owner implement, test and update related docs; another agent needs a concrete unresolved question or risk. |
| Acceptance at the wrong boundary | A reported operational incident was declared fixed from internal checks while the requested external behavior was still missing | Require evidence at the user's observable boundary; clearly distinguish internal checks from unverified integration behavior. |
| Native lifecycle mistaken for available capacity | A real V2 incident retained pending-init contexts despite repeated interrupt/spawn calls | Preserve lifecycle state, avoid queue-only messages to completed workers, use advertised release operations when available, and retry only after an observed capacity change. Stranded host residents are not repaired by prompts. |
| Wrong retrieval workspace | A later real CLI worker selected a similarly named previous fixture index | Compare full normalized roots in code before graph dispatch; retain the matched entry and include evidence scope in the opening brief. |
| Premature cheap-model default | README required global Luna, and omission of an override was incorrectly equated with Luna | Preserve host preferences, establish a capable quality baseline, and qualify cheaper models against the same scope; remove the stale model-name allowlist. |
| A successful range mistaken for EOF | Actual workers repeatedly read only 240 lines of longer skills | Generated skills preserve a final marker and exact generated-byte checks; complete loading is required, while the shared core is now compact. |
| Discovery before instructions were loaded | Both roles in an actual CLI batched a long skill read with a broad catalogue search, truncating its result | Require the complete applicable skill body before relying on it. Already attached live schemas need no catalogue bootstrap, and no fixed first-call order or batching rule is imposed. |
| A coordinator reply classified as unsolicited | An active worker sent a question after the coordinator waited, then received its reply | Track one reply opportunity for that exact worker; unrelated or repeated messages remain flagged. |
| Report prose mistaken for file access | A report recorded the advertised skill path it had loaded | Classify patch access from edit headers, not mentioned paths in report content; actual plugin edit targets remain forbidden. |
| Successful MCP labeled failed after consumer exception | A successful project listing was followed by store(undefined), which failed in JavaScript | Respect the native MCP receipt when assigning the MCP-error flag; keep the consumer failure visible and require its explanation. |
| Overbroad audit attribution | One failed Git command in a batch marked unrelated Python commands as Git violations | Classify each nested command from its own arguments; keep the actual failed command and complete wrapper outcome visible. |

## Primary guidance and application

[OpenAI's skill guide](https://learn.chatgpt.com/docs/build-skills) describes progressive
loading; the [plugin packaging guide](https://developers.openai.com/plugins/build/plugins)
provides the standard distributable skills component. [Custom agents](https://learn.chatgpt.com/docs/agent-configuration/subagents)
are a separate configuration mechanism. These mechanisms must not be conflated.

[Anthropic's effective-agent guidance](https://www.anthropic.com/engineering/building-effective-agents)
recommends simple composable designs, increasing complexity only for demonstrated
value, and using environmental feedback. Cortex keeps one adaptive pipeline and
seven storage operations; it does not add a fixed review board, mandatory specialist
sequence or server-controlled workflow.

[Anthropic's multi-agent research account](https://www.anthropic.com/engineering/multi-agent-research-system)
explains the importance of explicit assignment boundaries, effort proportional to
complexity, selective tool use and outcome-based evaluation. It also notes that
coupled coding work is less naturally parallel than broad research. Cortex therefore
keeps one mutation owner for dependent contracts, uses parallel workers for independent
questions, and requires extra verification to add an independent observation rather
than repeat the implementer's assumptions. Small representative trials guide debugging;
they do not establish a general intelligence gain.

[OpenAI's practical agent guide](https://openai.com/business/guides-and-resources/a-practical-guide-to-building-ai-agents/)
recommends establishing a capable-model baseline before optimizing cost with smaller
models. Its [GPT-5.6 builder guide](https://openai.com/index/builders-guide-to-gpt-5-6/)
also emphasizes measuring the complete model-and-agent setup. Cortex removes the
unconditional cheap-model assumption; capability examples are provisional, and stronger
models do not excuse missing facts, tool limitations or unchecked workspace identity.

## Retained improvements and unresolved claims

Decision briefs, explicit alternatives and discriminating checks, same-role targeted
continuation, and difficulty-based model escalation remain useful design hypotheses.
The completed small comparison did not prove the requested significant gain. No model
ranking, fixed token saving or general quality improvement is claimed from source tests.

Codebase Memory remains first for code discovery when available, with exact workspace
matching, coverage checks and current-source confirmation. A missing, stale or unsuitable
index permits a scoped fallback, not a search of unrelated projects. Instruction loading
and non-code documentation edits do not require graph searches.

Bounded independent discovery may precede the first pipeline edition. Useful durable
requirements, decisions, assignments and ownership state must exist before dependency,
shared-resource or acceptance decisions; this is an outcome requirement rather than a
mandatory initial publication stage.

Real CLI/Desktop qualification must use one unchanged marketplace candidate and inspect
both outcomes and full call evidence. See [host compatibility](host-compatibility.md),
[comparison results](quality-evaluation.md) and [release readiness](../release-readiness.md).

The historical loading-boundary candidate passed the bounded ordinary CLI/Desktop
scenario on both hosts, with complete instruction receipts and no truncated tool
discovery. That result supports avoiding truncation; it does not establish mandatory
bootstrap choreography, replace the original comparison or prove the requested
significant gain. See the current release record.
