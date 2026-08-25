---
name: output-validation
description: Internal Cortex validation overlay. Load only for an explicitly activated Cortex task when a gate result must be checked against its acceptance and evidence contract.
---

# Output Validation

Validate the result, not the confidence of the worker.

1. Restate the acceptance criteria as observable checks.
2. Inspect the diff and relevant execution path for scope and unintended change.
3. Run the smallest relevant build, typecheck, lint, test, or reproduction command. Use `build_verification` or `qa_engineer` when it can run independently.
4. Check that result-linked files, commands, outputs, and behavior agree.
5. Classify each criterion as passed, failed, blocked, or unverified; state the evidence and the next owner for failures.

For an executed-check completion gate, publish each observed command, working
directory, integer exit code, and decisive evidence in the semantic conclusion.
Every passing check has exit code 0. A negative-path check uses an assertion
harness that observes the expected failure and exits 0. Never omit, disguise,
or relabel a nonzero result, and never use another passing check to balance it;
preserve the failure in the semantic conclusion for server-directed correction.

Do not convert missing evidence into a pass. Avoid style-only objections unless they conceal a behavioral, safety, or maintainability risk.
