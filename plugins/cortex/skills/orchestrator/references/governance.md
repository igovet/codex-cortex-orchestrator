## Apply governance to decisions

Restore the current governance from public responses before selecting work, on a
mode change and after context loss. Read its rationale when it affects a decision.
If unset, select and record a suitable depth with `set_governance`; preserve an
explicit user selection. Record the depth, rationale reference and concrete effect
on assignments/checks in the pipeline, not a decorative mode label.

- `minimal`: narrow low-risk work, the smallest sufficient specialist set and
  focused checks; one native executor is acceptable when independent acceptance is
  not required. The coordinator does not replace that executor for code discovery
  or project writes; a read-only question still needs its specialist owner.
- `light`: examine affected components and regression risks; use an independent
  reviewer/verifier for a nontrivial change, and research only a concrete uncertainty.
- `full`: cover the consequential end-to-end path, separate uncertain diagnosis
  from implementation, and require independent verification for production,
  security, migrations or substantial behavior changes, with integration and
  rollback evidence where applicable.

Risk and explicit requirements take precedence over a lower depth. Explain any
needed adjustment; no mode removes required safety checks. Full does not mean
extra agents, heavier models or repeated reviews automatically. Select each
assignment's model independently under worker routing. If governance is unavailable,
retain that diagnostic and continue from user requirements and observed risk.
Changing depth replans remaining work; it neither cancels owners nor invalidates
unchanged evidence automatically. Governance is model guidance, never a runtime gate.
