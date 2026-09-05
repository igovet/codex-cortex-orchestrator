# Marketplace skills and native agent capacity

The supported route uses ordinary Codex progressive skill loading. The earlier
absolute ban on reading installed SKILL.md files was withdrawn on 2026-09-06 after
the user clarified that correct standard behavior, rather than a filesystem ban,
was their intent. Do not carry that superseded requirement into runtime assignments.

## Standard skill delivery

The [official skill guide](https://learn.chatgpt.com/docs/build-skills) describes
metadata-first discovery followed by loading the selected complete SKILL.md, with
supporting references loaded as needed. The [plugin packaging guide](https://developers.openai.com/plugins/build/plugins)
uses `.codex-plugin/plugin.json` with `"skills": "./skills/"`. Cortex retains that
layout, all 22 complete worker skills, companion skills and seven MCP operations.

A worker uses a complete body already injected by Codex or reads its exact advertised
SKILL.md path from the available-skills catalogue. Documented path aliases are expanded
from that catalogue. Needed declared Markdown references are valid. No installation
scan, guessed cache version, agent TOML read, server inspection, custom loader, setup
hook or personal registry is required. Read through the skill's end and retain actual
command receipts; a truncated page or role description is insufficient. Generated
worker skills declare their exact line count and final completion marker because
a successful partial range read does not demonstrate that the whole file was loaded.

The [custom-agent guide](https://learn.chatgpt.com/docs/agent-configuration/subagents)
describes standalone TOML files in personal or project agent directories. Optional
TOML exports inside a plugin are not automatically selected native profiles. Cortex
uses its packaged worker skills and the host's advertised native delegation interface.

## Why automatic injection alone was insufficient

In the inspected official `rust-v0.153.0` source (unchanged in `rust-v0.153.4` for
these paths), V2 assignments arrive as `InterAgentCommunication`. `turn_user_input`
excludes that variant, and explicit skill injection consumes the filtered user input.
A skill token in a V2 assignment therefore does not prove automatic injection. This
is not a marketplace blocker when the advertised skill file can be read normally.

Source anchors: [V2 spawn](https://github.com/openai/codex/blob/rust-v0.153.0/codex-rs/core/src/tools/handlers/multi_agents_v2/spawn.rs),
[turn input and injection](https://github.com/openai/codex/blob/rust-v0.153.0/codex-rs/core/src/session/turn.rs),
[plugin manifest](https://github.com/openai/codex/blob/rust-v0.153.0/codex-rs/core-plugins/src/manifest.rs),
[native role loader](https://github.com/openai/codex/blob/rust-v0.153.0/codex-rs/agent-roles/src/loader.rs).

## Confirmed capacity behavior; unresolved host recovery

V2 automatically unloads eligible completed, errored or interrupted resident
contexts only when no active turn or pending mailbox remains. `pending_init` is
not eligible. `interrupt_agent` submits an interrupt; it is not a release operation.
The explicit `close_agent` operation is registered for V1, not V2.

A private real-host incident showed four contexts remaining `pending_init`, repeated
capacity rejection and unchanged interrupt receipts. This supports the capacity
diagnosis; the precise transition that stranded those contexts is not reproduced.
Do not claim an upstream allocator fix from a prompt change.

Source anchors: [residency eligibility](https://github.com/openai/codex/blob/rust-v0.153.0/codex-rs/core/src/agent/control/residency.rs),
[interrupt implementation](https://github.com/openai/codex/blob/rust-v0.153.0/codex-rs/core/src/agent/control.rs),
[interface-specific operations](https://github.com/openai/codex/blob/rust-v0.153.0/codex-rs/core/src/tools/spec_plan.rs).

Cortex now distinguishes assignment completion from capacity release, avoids
queue-only messages to completed workers, permits one diagnostic snapshot after
a capacity rejection, and requires an observed completion/release before retrying
spawn. Same-role continuation remains available when independently justified.
These changes prevent ineffective recovery loops; they do not free stranded V2
contexts or grant access to host state.

## Qualification boundary

Validate one unchanged installed marketplace candidate on actual CLI and Desktop:
complete skill loading, Codebase Memory priority when available, report identity,
product outcome and independent verification when warranted. Review all calls,
including unsuccessful reads, truncated results and capacity handling. A product
pass does not erase protocol failures. Keep raw host evidence private.

Native V2 capacity recovery from a stranded `pending_init` context remains a host
limitation. Standard automatic eviction of eligible completed contexts is distinct
from that failure. Never promise that a plugin prompt repairs host residency or
silently switch the user's native interface. Current named-candidate outcomes are
recorded in [release readiness](../release-readiness.md).
