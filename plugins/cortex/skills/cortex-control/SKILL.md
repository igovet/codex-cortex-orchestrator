---
name: cortex-control
description: Cortex retention command and optional report-writing examples. Load only for an explicitly assigned clear command or when a worker is explicitly asked to consult a report example.
---

# Cortex control

## Loading boundary

Load this skill through the standard Codex skill mechanism only for one of the
declared purposes. Do not search for or inspect plugin, cache, installation, MCP
server, profile, or database files. Ordinary workers already receive their complete
self-contained Agent v2 profile and do not load this skill.

## Explicit retention maintenance

When the coordinator assigns the user's taskless `clear N days` command, run only
the bundled [retention command](../../scripts/cortex_clear.py). Obtain its current
CLI syntax with `--help`; do not read the script implementation. Supply the canonical
project, exact retention and protected native thread IDs established by the
coordinator. This route creates no task, governance choice, pipeline, or report.

The command removes matching old task records and project task directories. It also
removes only project draft files whose stored task relationship and SHA-256 make the
association unambiguous. Never remove the whole project `draft-reports` directory or
unknown drafts. Return only deletion and protection counts.

## Optional report examples

Use the [report example catalogue](references/index.md) only when the coordinator
explicitly asks for a report example. Read one matching example, then adapt it to the
assignment. Examples guide content and never replace the profile's requirements,
live writer schema, observable evidence, or honest unrun checks.

All worker reports remain complete English Markdown files first allocated by the
live draft creator, then filled at its returned project path and published through
the live writer using the returned short draft identifier. Keep that identifier in
the filename and Markdown marker. Do not pass a path or report body through MCP, JavaScript, JSON, arrays,
chunks, heredocs, command substitution, or shell interpolation. Do not delete or move
the draft yourself and never read the Cortex database or final task files directly.
