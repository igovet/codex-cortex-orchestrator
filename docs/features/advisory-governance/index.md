# Governance and decision integrity

<!-- GENERATED:START -->

## Purpose

The model selects proportional risk and verification depth. The deterministic
core binds that selection to assignment admission and current user decisions;
it does not choose workers, perform semantic planning or grant native permissions.
The authoritative design and remaining qualification are recorded in
[typed orchestration integrity](../../project/typed-orchestration-integrity.md).

## Ownership

- [public_contracts.py](../../../plugins/cortex/scripts/cortex_runtime/public_contracts.py)
  owns the live call contracts.
- [domain_kernel.py](../../../plugins/cortex/scripts/cortex_runtime/domain_kernel.py)
  commits decision bindings, answers, revisions and receipts atomically.
- [graph_ledger.py](../../../plugins/cortex/scripts/cortex_runtime/graph_ledger.py)
  derives current review/readiness and binds evidence.
- [candidate_family.py](../../../plugins/cortex/scripts/cortex_runtime/candidate_family.py)
  validates complete alternatives and activates only the selected branch.
- [v12_store.py](../../../plugins/cortex/scripts/cortex_runtime/v12_store.py)
  stores immutable decisions, assessments and closure relations.
- The bundled orchestrator and control skills own model policy, not argument
  definitions or an alternative transaction route.

## Assessment and review

Fresh task opening precedes one explicit assessment, and that assessment
precedes every first assignment. Minimal and light complete plans may proceed
informationally. Full/material-high-risk work, explicit user-requested review
and genuine product/authority choices require a complete decision-ready packet.
Complexity alone is not high risk and cannot manufacture another approval.

A nontrivial plan requires independent semantic graph validation. Readiness
then respects its exact approval relation. The backend checks dependencies and
ownership even when all available native slots are empty.

Approval fulfills the current decision boundary, not a perpetual obligation
to approve every replan. Direct user steering authorizes the stated change;
its successor plan needs review only for new material risk/authority, a genuine
branch, credentials or explicit renewed review. Reassessment is evidence-backed
and cannot churn identical normalized evidence.

Unanswered review authority includes candidate identity, independent validation,
artifact/barrier state and assessment epoch. A changed boundary invalidates the
old packet. It cannot consume approval, but it also cannot block current
replanning or a directly authored change. All decisions remain historical.

## Validated alternatives

Where complete alternatives can be constructed responsibly, the planner
publishes a bounded candidate family. Each alternative has a complete proposed
contract and graph. Structural and independent semantic validation cover all
alternatives before review; none is installed as executable work merely because
it is first in the list.

One direct answer selects and approves exactly one branch. Selection, contract
revision, graph binding and approval commit together. The selected graph
references the original validation rather than copying facts or fabricating
another publication. Changed artifact evidence, unknown keys or failed writes
cannot partially consume this decision.

A genuine pre-plan steering question is appropriate only when the answer is
needed to construct responsible alternatives. No-op changes are rejected.
Already explicit user changes are recorded immediately without asking again.

## In-contract correction and closure

Validated finite remediation templates permit bounded fixes and independent
regression without another scope question. Scope-, authority- and risk-changing
findings cannot silently use ordinary correction authority. Non-progress and
exhaustion remain explicit, truthful incomplete evidence.

Before every closure attempt, present current verified results, documentation
impact, residual risks and unrun checks. Open the mandatory final closure review
and record the direct revise-or-close answer. Only current close permits the
attempt. Graph completeness, artifact consistency and review freshness still
apply. Earlier plan approval and advance instructions to close automatically
are insufficient.

Final verification and read-only documentation assessment cover the same latest
sealed generation. Documentation edits, if required, precede final verification.
A truthful not-ready closure cannot turn incomplete evidence into success.
A result can be explained honestly even when closure storage is unavailable.

## Verification

Focused regressions cover initial and renewed review, informational replans,
atomic candidate selection, changed packets, in-flight direct steering,
receipt replay/conflict, rollback and mandatory closure. See
[test_replan_review_lineage.py](../../../tests/test_replan_review_lineage.py),
[test_candidate_family_public.py](../../../tests/test_candidate_family_public.py),
[test_domain_kernel_receipts.py](../../../tests/test_domain_kernel_receipts.py)
and [test_typed_closure.py](../../../tests/test_typed_closure.py).

Source tests are not native live evidence. The
[Completion checklist](../../project/typed-orchestration-integrity.md#11-completion-checklist)
records separate full CLI and unchanged-payload Desktop gates.

<!-- GENERATED:END -->
