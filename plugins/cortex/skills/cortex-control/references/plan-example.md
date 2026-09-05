# Planning report example

Use this as a content guide, not a required structure. Replace placeholders with
observed task facts; omit irrelevant sections, add useful ones and change their
order when helpful. Never copy example placeholders or imply checks were run.
Every profile uses the common Markdown writer; this file defines no tool arguments.

## Example Markdown

```markdown
# Plan: <desired behavior>

## Objective and boundaries

<Requested outcome, exact requirements, constraints, included and excluded work.>

## Established facts and open decisions

<Relevant source-backed facts, assumptions and missing information. For a genuine
user choice, explain alternatives and consequences for the coordinator.>

## Proposed work

| Work | Owner | Prerequisite evidence | Expected result | Intended verification |
| --- | --- | --- | --- | --- |
| <Bounded research if needed> | <Relevant specialist> | <Existing facts> | <Resolved uncertainty> | <Source confirmation> |
| <Implementation> | <Relevant specialist> | <Settled design and required findings> | <Observable behavior> | <Acceptance and regression checks> |
| <Dependent review if useful> | <Independent specialist> | <Implementation exists> | <Evidence of defects or readiness> | <Risk-proportional checks> |

<Explain independent work that can run concurrently and edits that must not overlap.
Omit stages that do not help this task. No fixed lifecycle is required.>

## Requirement coverage

<Show where each assigned requirement, boundary case and exact constraint is handled.>

## Risks and stopping conditions

<Failure paths, permissions, external prerequisites and conditions that require
reassessment. State what can safely proceed and what depends on an answer.>

## Verification status

<Discovery checks actually executed, separately from future checks proposed above.
A plan is not proof that implementation or tests have succeeded.>
```
