---
name: output-validation
description: Validate code changes, plans, or delegated results against explicit acceptance criteria and evidence. Use before integration, after implementation, or when a worker reports completion without sufficient proof.
---

# Output Validation

Validate the result, not the confidence of the worker.

1. Restate the acceptance criteria as observable checks.
2. Inspect the diff and relevant execution path for scope and unintended change.
3. Run the smallest relevant build, typecheck, lint, test, or reproduction command. Use `build_verification` or `qa_engineer` when it can run independently.
4. Check that reported files, commands, outputs, and behavior agree.
5. Classify each criterion as passed, failed, blocked, or unverified; state the evidence and the next owner for failures.

Do not convert missing evidence into a pass. Avoid style-only objections unless they conceal a behavioral, safety, or maintainability risk.
