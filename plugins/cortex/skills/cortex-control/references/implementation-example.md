# Implementation report example

Use this as a content guide, not a required structure. Replace placeholders with
observed task facts; omit irrelevant sections, add useful ones and change their
order when helpful. Never copy example placeholders or imply checks were run.
Every profile uses the common Markdown writer; this file defines no tool arguments.

## Example Markdown

```markdown
# Implemented: <observable change>

## Result

<Before/after behavior and the acceptance conditions addressed.>

## Changes

| Path | Change and purpose |
| --- | --- |
| <Project path> | <What changed and why> |

## Verification

| Check | Working directory | Result |
| --- | --- | --- |
| <Exact relevant command or inspection> | <Directory> | <Observed outcome and exit code, or not run with reason> |

<Cover important negative cases, boundaries and regression evidence proportionally.>

## Documentation and compatibility impact

<Affected docs, interfaces or operational behavior; state any needed follow-up.>

## Remaining work and risks

<Failures, unverified behavior, scope limits and recommended next owner.>
```
