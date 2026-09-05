# Verification and review report example

Use this as a content guide, not a required structure. Replace placeholders with
observed task facts; omit irrelevant sections, add useful ones and change their
order when helpful. Never copy example placeholders or imply checks were run.
Every profile uses the common Markdown writer; this file defines no tool arguments.

## Example Markdown

```markdown
# Assessment: <subject of review>

## Subject and scope

<The concrete result reviewed, relevant revision or paths and assigned acceptance checks.>

## Findings

| Severity or impact | Finding | Evidence | Suggested action |
| --- | --- | --- | --- |
| <Impact> | <Concrete defect or uncertainty> | <Reproduction/source/test> | <Bounded correction> |

<If no defects were found, state that within the reviewed scope; do not imply unlimited assurance.>

## Checks executed

| Check | Expected behavior | Observed result |
| --- | --- | --- |
| <Command/inspection and working directory> | <Acceptance condition> | <Actual evidence and exit result> |

## Unrun checks and limitations

<Missing environment, untested platforms, unavailable evidence or coverage limits.>

## Recommendation

<What the coordinator can conclude, what needs correction and whether another
independent check is useful. The report does not create a server acceptance gate.>
```
