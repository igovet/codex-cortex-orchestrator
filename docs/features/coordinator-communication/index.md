# Coordinator communication

<!-- GENERATED:START -->

## Purpose and boundary

Cortex 1.15.6 ships a packaged coordinator-to-user communication policy. It
keeps user-facing commentary, questions, plan summaries, progress, decision
summaries, artifact summaries, and final answers clear without creating a
runtime loader, dispatcher, lifecycle hook, tool, or backend gate.

The policy leads with result, then user impact, then the next safe step. It uses
the latest meaningful user language, suppresses unchanged wait updates, hides
opaque identifiers and ledger jargon by default, reveals technical detail
progressively, and permits only safe optional contextual humor after the
material fact. Coordinator-to-worker messages, native worker transcripts,
worker-authored report narrative, decision normalization, ledger prose, and
human-view engineering narrative remain English. Original user evidence stays
unchanged in its designated task and decision fields.

For a Russian latest meaningful user message, coordinator-to-user communication
is Russian while preserving the same outcome-first, progressively detailed
shape. Russian is a user-facing localization rule only: worker, inter-worker,
ledger, and report content remains English and opaque internal identifiers stay
hidden by default.

The canonical source is the bundled
[coordinator-communication skill](../../../plugins/cortex/skills/coordinator-communication/SKILL.md).
It is integrated with the packaged orchestrator, control, and progress-accounting
guidance. It does not weaken verified-link publication, worker ownership,
ordinary approvals, or safety boundaries.

Decision-opening operations create durable holds but do not themselves render
their prompts in either CLI or Desktop. After a successful opening, the
coordinator's final answer must give the user a complete localized decision
packet: why the decision is needed, its concrete subject, the available safe
choices, and the material consequence or stopping condition of each. A user
must not need to expand a tool call or inspect worker output to understand what
is being approved.

Complete safe alternatives belong in an independently validated candidate
family: one review answer selects and approves one branch. Pre-plan steering
is reserved for a genuine choice needed before responsible alternatives can be
constructed. Direct user changes are recorded immediately, including while an
earlier question is pending, without requesting confirmation again. Internal
facts and recoverable failures are handled autonomously within scope.

Plan review additionally includes a decision-ready summary of scope, ordered
stages, intended changes, verification, stop/deploy conditions, and material
risks or unresolved items. A ready server-verified current-plan link is copied
byte-for-byte. If unavailable, repair the derived view before opening approval;
an inline summary cannot replace the verified relation. A bare “plan ready —
approve?” message is a contract violation.

Native workers never ask the user directly. They publish bounded evidence of
findings and limitations. The coordinator continues permitted correction or
planning; a partial report alone is not a user-question trigger. Only a genuine
decision receives a complete packet, never a context-free worker request.

A host-confirmed loss without publication is reconciled using current native
and artifact evidence before one lineage-linked replacement. The
coordinator does not replay the original assignment, ask the user to say
“continue”, or end the turn while that deterministic recovery remains available.

The mandatory final closure review follows presentation of current verified
results, documentation impact, risks and unrun checks. Only the direct current
close choice authorizes closure; revise keeps the same task. Prior approval
or a request to finish automatically cannot substitute for it.

## Deterministic verification

`scripts/cortex-prompt-lint.py`, the marketplace validator, and the targeted
release gate require the packaged skill, its front matter, policy markers, and
cross-skill integration. These are deterministic static contract checks, not
live-model, A/B, or model-quality evaluation.

<!-- GENERATED:END -->
